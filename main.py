

# ======== CONFIG / KEYS ========
import os
os.environ["PULSE_SINK"] = "VirtualMicSink"

import re
import threading
import queue
import readline

import requests
import sounddevice as sd
import numpy as np

from deepgram import DeepgramClient, LiveTranscriptionEvents, LiveOptions
from elevenlabs.client import ElevenLabs
from pathlib import Path
from elevenlabs import VoiceSettings
from dotenv import load_dotenv

# carrega .env da pasta do script

load_dotenv( )
# ======== ÁUDIO / LINGUAGEM ========
PCM_SR = 24000
PCM_CH = 1
SAMPLE_RATE = 24000
CHANNELS = 1
CHUNK_SECONDS = 1      # 200ms por chunk
FLUSH_GRACE = 2.0          # silêncio antes de flush do carry

VIRTUAL_SINK_NAME = os.getenv("VIRTUAL_SINK_NAME", "VirtualMicSink")

fromAudio = "pt-BR"
to_lang = "English"

# ======== CHAVES / MODELOS ========
DG_KEY = os.getenv("DG_KEY")
EL_KEY = os.getenv("EL_KEY")
REVIEW_ENABLED = True

VOICE = os.getenv("EL_VOICE_ID", "HDNjMGNzhjXlh3sYMYQI")
TTS_MODEL = os.getenv("EL_TTS_MODEL", "eleven_flash_v2_5")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
MODEL = os.getenv("OLLAMA_MODEL", "gemma2:2b")

print("[INIT] DeepgramClient...")
dg = DeepgramClient(api_key=DG_KEY)

print("[INIT] Criando conexão live...")
conn = dg.listen.live.v("1")

print("[INIT] ElevenLabs client...")
el = ElevenLabs(api_key=EL_KEY)

# ======== ESTADO ========
carry = ""
carry_lock = threading.Lock()

sent_q: "queue.Queue[str]" = queue.Queue()

flush_timer = None
flush_lock = threading.Lock()

tts_ev = threading.Event()   # pausa captura enquanto traduz/edita/fala
stop_ev = threading.Event()

http = requests.Session()

# ======== FLUSH TIMER ========
def cancel_flush_timer():
    global flush_timer
    with flush_lock:
        if flush_timer:
            flush_timer.cancel()
            flush_timer = None

def schedule_flush_after_grace():
    global flush_timer
    with flush_lock:
        if flush_timer:
            flush_timer.cancel()
        t = threading.Timer(FLUSH_GRACE, flush_carry_if_any)
        t.daemon = True
        flush_timer = t
        t.start()

# ======== TRADUÇÃO PT->EN (via Ollama) ========
def translate_pt_en(txt: str) -> str:
    if not txt or not txt.strip():
        return ""
    s = txt.strip()
    if len(s) < 4 and (" " not in s):
        return ""

    try:
        r = http.post(
            OLLAMA_URL,
            json={
                "model": MODEL,
                "prompt": (
                    f"Translate from {fromAudio} to {to_lang}: \"{txt}\". "
                    "Only respond with the translated string, no explanations, "
                    "no quotes, nothing else."
                ),
                "stream": False,
            },
            timeout=(3, 25),
        )
        r.raise_for_status()
        j = r.json()
        out = j.get("response", "").strip()
        return out
    except Exception as e:
        print("[TRAD][ERRO]", e)
        return ""

# ======== HELPER ÁUDIO ========
def _extract_bytes(chunk):
    if isinstance(chunk, (bytes, bytearray)):
        return bytes(chunk)
    a = getattr(chunk, "audio", None)
    if a is None and isinstance(chunk, dict):
        a = chunk.get("audio")
    return bytes(a) if isinstance(a, (bytes, bytearray)) else None

def _pulse_out_index(device_name: str = VIRTUAL_SINK_NAME) -> int | None:
    try:
        devs = sd.query_devices()
        prefer = None
        fallback = None

        for i, d in enumerate(devs):
            if d.get("max_output_channels", 0) <= 0:
                continue
            name = str(d.get("name", ""))
            if device_name.lower() in name.lower():
                prefer = i
            if "pulse" in name.lower():
                fallback = i

        if prefer is not None:
            return int(prefer)
        if fallback is not None:
            return int(fallback)
    except Exception as e:
        print("[AUDIO] erro ao buscar device:", e)

    return None

# ======== TTS  ========
def speak(t: str):
    t = (t or "").strip()
    if not t:
        return
    print('Speak: ', t)
    try:
        resp = el.text_to_speech.stream(
            text=t,
            voice_id=VOICE,
            model_id=TTS_MODEL,
            output_format=f"pcm_{PCM_SR}",
            voice_settings=VoiceSettings(
                stability=0.55,
                similarity_boost=0.9,
                style=0.2,
                use_speaker_boost=True,
                speed=0.8,
            ),
        )

        pcm_bytes = bytearray()
        for ch in resp:
            buf = _extract_bytes(ch)
            if buf:
                pcm_bytes.extend(buf)

        if len(pcm_bytes) < 4:
            return

        pcm = np.frombuffer(pcm_bytes, dtype=np.int16)
        max_val = int(np.max(np.abs(pcm)) or 1)
        target = 28000
        gain = target / max_val
        gain = min(gain, 3.0)
        pcm = np.clip(pcm * gain, -32768, 32767).astype(np.int16)
        pcm_bytes = pcm.tobytes()

        dev_idx = _pulse_out_index()

        bytes_per_frame = PCM_CH * 2
        frames_per_block = PCM_SR // 20     # ~50 ms
        block_bytes = frames_per_block * bytes_per_frame

        with sd.RawOutputStream(
            device=dev_idx,
            samplerate=PCM_SR,
            channels=PCM_CH,
            dtype="int16",
            blocksize=frames_per_block,
            latency="low",
        ) as out:

            mv = memoryview(pcm_bytes)
            for pos in range(0, len(mv), block_bytes):
                chunk = mv[pos:pos + block_bytes]
                out.write(chunk)

     
    except Exception as e:
        print("[TTS][ERRO]", e)
    print("\n" + "=" * 60)

# ======== INPUT COM TEXTO PRÉ-PREENCHIDO ========
def input_prefill(prompt: str, default: str) -> str:
    def hook():
        readline.insert_text(default)
        readline.redisplay()

    readline.set_startup_hook(hook)
    try:
        return input(prompt)
    finally:
        readline.set_startup_hook()

def review_en_text(src_pt: str, auto_en: str) -> str | None:
    print("\n" + "=" * 60)
    print(f"[Voice {fromAudio}]:", src_pt)
    print("-" * 60)

    try:
        # aqui o prompt é só "Edit: ", sem repetir [Final] duas vezes
        user = input_prefill("Edit: ", auto_en).strip()
    except EOFError:
        return auto_en

    return user

# ======== FILA ========
def enqueue_sentence(text: str):
    sent_q.put(text)

# ======== WORKER  ========
def consumer_worker():
    while not stop_ev.is_set():
        try:
            try:
                s = sent_q.get(timeout=0.1) 
            except queue.Empty:
                continue

            try:
                tts_ev.set()
                t = repr(s)
                n = repr(s)
                s = ""
                s = t
                if REVIEW_ENABLED == True:
                    s = review_en_text(t, n)
                en_auto = translate_pt_en(s)

                speak(en_auto)

            finally:
                sent_q.task_done()
                tts_ev.clear()  

        except Exception as e:
            print("[WORKER][ERRO]", repr(e))

worker_th = threading.Thread(target=consumer_worker, daemon=True)
worker_th.start()

# ======== SPLIT DE FRASES ========
SENT_SPLIT = re.compile(r'(?<=[.!?])\s+')

def push_text_and_split(text: str):
    global carry
    with carry_lock:
        carry = (carry + " " + text).strip() if carry else text.strip()
        if not carry:
            return

        parts = SENT_SPLIT.split(carry)
        if not parts:
            return

        complete = parts[:-1]
        rest = parts[-1]

        if rest and re.search(r'[.!?]\s*$', rest):
            complete.append(rest)
            rest = ""

        for s in complete:
            s = s.strip()
            if not s:
                continue
            if len(s) < 4 and (" " not in s):
                continue
            enqueue_sentence(s)

        carry = rest.strip()

def flush_carry_if_any():
    global carry
    with carry_lock:
        leftover = carry.strip()
        if leftover:
            if len(leftover) >= 4 or (" " in leftover):
                enqueue_sentence(leftover)
            carry = ""

# ======== CALLBACK DEEPGRAM ========
def on_transcript(connection, result, **kwargs):
    alt = result.channel.alternatives[0]
    txt = alt.transcript or ""
    if not txt:
        return

    is_final = getattr(result, "is_final", False)
    speech_final = getattr(result, "speech_final", False)

    if is_final:
        cancel_flush_timer()
        push_text_and_split(txt)

    if speech_final:
        schedule_flush_after_grace()

# ======== MAIN LOOP ========
def main():
    print("[MAIN] Registrando handler de transcrição...")
    conn.on(LiveTranscriptionEvents.Transcript, on_transcript)

    print("[MAIN] Iniciando live com Deepgram...")
    conn.start(
        LiveOptions(
            model="nova-3",
            language=fromAudio,
            smart_format=True,
            encoding="linear16",
            channels=CHANNELS,
            sample_rate=SAMPLE_RATE,
        )
    )

    try:
        print(sd.query_devices())
    except Exception:
        print("[AUDIO] sounddevice indisponível para listar devices (ok).")

    print(f"🎙 Fale em {fromAudio} (Ctrl+C para sair)")
    try:
        while True:
            frames = int(CHUNK_SECONDS * SAMPLE_RATE)
            data = sd.rec(
                frames,
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="float32",
            )
            sd.wait()
            audio = (data * 32767).astype(np.int16).tobytes()
            try:
                conn.send(audio)
            except Exception as e:
                print("[AUDIO][ERRO AO ENVIAR PARA DG]", e)
                break
    except KeyboardInterrupt:
        print("\n[MAIN] CTRL+C recebido, finalizando...")
    finally:
        try:
            cancel_flush_timer()
            flush_carry_if_any()
            print("[MAIN] Aguardando processamento das frases restantes...")
            sent_q.join()
        except Exception:
            pass
        print("[MAIN] Fechando conexão Deepgram...")
        try:
            conn.finish()
        except Exception:
            pass
        stop_ev.set()
        try:
            sent_q.put("")
        except Exception:
            pass
        worker_th.join(timeout=0.5)
        print("[MAIN] Fim.")

if __name__ == "__main__":
    main()
