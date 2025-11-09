# linguAI
DG_KEY = "="
EL_KEY = ""

import os, json, requests, sounddevice as sd, numpy as np, re, threading, queue
from deepgram import DeepgramClient, LiveTranscriptionEvents, LiveOptions
from elevenlabs.client import ElevenLabs
from elevenlabs import stream as el_stream



VOICE = "HDNjMGNzhjXlh3sYMYQI"
TTS_MODEL = "eleven_flash_v2_5"

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "zongwei/gemma3-translator:1b"  

SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_SECONDS = 1

print("[INIT] DeepgramClient...")
dg = DeepgramClient(api_key=DG_KEY)

print("[INIT] Criando conexão live...")
conn = dg.listen.live.v("1")

print("[INIT] ElevenLabs client...")
el = ElevenLabs(api_key=EL_KEY)

# ======== ESTADO ========
# 'carry' guarda pedaço de frase ainda sem pontuação final
carry = ""
carry_lock = threading.Lock()

# fila de frases completas PT -> consumidor sequencial traduz + fala
sent_q: "queue.Queue[str]" = queue.Queue()

# ======== HTTP session p/ reuso (Ollama mais rápido) ========
http = requests.Session()

# ======== TRADUÇÃO PT->EN ========
def translate_pt_en(txt: str) -> str:
    if not txt.strip():
        return ""
    try:
        # uso sem stream (mais simples), conexão reaproveitada pela Session
        r = http.post(
            OLLAMA_URL,
            json={
                "model": MODEL,
                "prompt": f"Translate English to Portugurese: {txt}",
                "stream": False,
                "speed": 1.5
            },
            timeout=(3, 25),  # connect, read
        )
        r.raise_for_status()
        j = r.json()
        out = j.get("response", "").strip()
        return out
    except Exception as e:
        print("[TRAD][ERRO]", e)
        return ""

# ======== FALA (EN) ========
def speak_en(t: str):
    if not t.strip():
        return
    try:
        audio_stream = el.text_to_speech.stream(text=t, voice_id=VOICE, model_id=TTS_MODEL)
        el_stream(audio_stream)  # bloqueia até terminar -> mantém ordem
    except Exception as e:
        print("[TTS][ERRO]", e)

# ======== WORKER: consome frases completas em ordem, traduz e fala ========
stop_ev = threading.Event()

def consumer_worker():
    while not stop_ev.is_set():
        try:
            s = sent_q.get(timeout=0.1)
        except queue.Empty:
            continue
        try:
            en = translate_pt_en(s)
            speak_en(en)
        finally:
            sent_q.task_done()

worker_th = threading.Thread(target=consumer_worker, daemon=True)
worker_th.start()

# ======== Regex de sentença: fecha com ., ! ou ? ========
SENT_SPLIT = re.compile(r'(?<=[.!?])\s+')

def push_text_and_split(text: str):
    """
    Junta texto ao 'carry', separa por sentenças (.,!,?).
    Tudo que formar sentença completa vai p/ fila; sobra (sem pontuação final) volta p/ 'carry'.
    """
    global carry
    with carry_lock:
        carry = (carry + " " + text).strip() if carry else text.strip()
        if not carry:
            return

        parts = SENT_SPLIT.split(carry)  # divide por delimitadores mantendo-os (via lookbehind)
        if not parts:
            return

        # as completas são todas menos a última, que pode estar incompleta
        complete = parts[:-1]
        rest = parts[-1]

        # se a última também termina com pontuação, ela é completa
        if rest and re.search(r'[.!?]\s*$', rest):
            complete.append(rest)
            rest = ""

        # enfileira completas
        for s in complete:
            s = s.strip()
            if s:
                sent_q.put(s)

        # atualiza carry com o resto (incompleto)
        carry = rest.strip()

def flush_carry_if_any():
    """No fim da fala, envia o que sobrou (sem pontuação)."""
    global carry
    with carry_lock:
        if carry:
            sent_q.put(carry.strip())
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
        # só usa finais, para evitar duplicação
        push_text_and_split(txt)
        # opcional: log de parcial consolidada
        # print(f"[DG][FINAL-SEG] {txt!r}")

    if speech_final:
        # fecha frase sobressalente (sem pontuação) e dispara worker
        flush_carry_if_any()
        # print("[DG] speech_final -> flush")

# ======== MAIN LOOP ========
def main():
    print("[MAIN] Registrando handler de transcrição...")
    conn.on(LiveTranscriptionEvents.Transcript, on_transcript)

    print("[MAIN] Iniciando live com Deepgram...")
    conn.start(
        LiveOptions(
            model="enhanced",
            language="en-US",
            smart_format=True,   # insere pontuação -> essencial p/ detecção de frase
            encoding="linear16",
            channels=CHANNELS,
            sample_rate=SAMPLE_RATE,
        )
    )
    print("[MAIN] Live iniciado.")

    print("[MAIN] Dispositivos de áudio disponíveis:")
    try:
        import sounddevice as sd
        print(sd.query_devices())
    except Exception:
        print("[AUDIO] sounddevice indisponível para listar devices (ok).")

    print("🎙 Fale em PT (Ctrl+C para sair)")
    try:
        import sounddevice as sd
        while True:
            frames = int(CHUNK_SECONDS * SAMPLE_RATE)
            data = sd.rec(frames, samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="float32")
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
        sent_q.put("")  # libera worker se estiver bloqueado
        worker_th.join(timeout=0.5)
        print("[MAIN] Fim.")

if __name__ == "__main__":
    main()
