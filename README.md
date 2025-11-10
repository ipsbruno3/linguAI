# 🌐 linguAI — Real‑time STT ↔️ Translation ↔️ TTS (Linux)

> **English + Português** — One workstation. Real‑time captions + translated voice for Google Meet/Zoom/Teams.
> Uses **Deepgram** (ASR), **Ollama** (local LLM translation), **ElevenLabs** (TTS) and **python‑soundcard** loopback.

---

## 🇺🇸 Overview (English)

**What it does**

* Captures the **meeting audio** (system output) via loopback and **transcribes** it with **Deepgram**.
* Sends the text to **Ollama** for **translation** (e.g., EN → PT‑BR) and prints the result live.
* In the other direction, it takes **your microphone (PT‑BR)** → **Deepgram** → **Ollama (PT→EN)** → **ElevenLabs TTS** and **plays** a natural English voice to a **virtual mic**, so participants hear you **in their language** with low latency.

**Why it’s useful**

* Works across Google Meet/Zoom/Teams without plugins.
* Round‑trip from ASR → LLM → TTS under a few hundred ms per sentence chunk when tuned.
* Designed for real meetings: stable loopback routing, buffering to avoid half‑sentences, and explicit “translation‑only” prompts to prevent echoing.

---

## 🇧🇷 Visão Geral (Português)

**O que ele faz**

* Captura o **áudio da reunião** (saída do sistema) via loopback e **transcreve** com **Deepgram**.
* Envia o texto para o **Ollama** para **tradução** (ex.: EN → PT‑BR) e mostra o resultado ao vivo.
* No sentido contrário, pega **seu microfone (PT‑BR)** → **Deepgram** → **Ollama (PT→EN)** → **ElevenLabs TTS** e **toca** uma voz em inglês em um **microfone virtual**, para que a outra pessoa te ouça **no idioma dela** com baixa latência.

**Por que é útil**

* Funciona em Google Meet/Zoom/Teams sem plugins.
* Latência baixa por trecho (ASR → LLM → TTS), adequada para conversa.
* Pensado para reuniões reais: roteamento estável, buffer de frases, prompt “apenas tradução”.

---

## ✨ Features

* 🎧 **Loopback robusto** com `python-soundcard` (PipeWire/PulseAudio).
* 🗣️ **ASR**: Deepgram **live** (`nova-3` por padrão).
* 🔁 **Tradução local**: **Ollama** com modelo `zongwei/gemma3-translator:1b` (personalizável).
* 🔊 **TTS**: **ElevenLabs** com voz clonada/personalizada.
* 🧠 **Buffer de frases**: agrega parciais e só envia ao LLM quando a sentença está “fechada” (ou por timeout), reduzindo erros.
* 🔐 **Segurança**: chaves via variáveis de ambiente (não commitar secrets).

---

## 🧱 Architecture / Fluxo

```
[Meeting Audio (system out)] --loopback--> [Deepgram STT EN]
    -> [Buffer sentence] -> [Ollama EN→PT] -> [Print PT captions]

[Your Mic (PT)] -> [Deepgram STT PT] -> [Ollama PT→EN] -> [ElevenLabs TTS EN]
    -> [Virtual Mic / Default Output] -> Remote hears you in English
```

---

## 🧩 Requirements / Requisitos

* **Ubuntu** + **PipeWire/PulseAudio**
* **Python 3.10+**
* `pip install`: `soundcard`, `numpy`, `requests`, `deepgram-sdk`, `elevenlabs`, (opcional: `sounddevice`)
* **Ollama** rodando local
* **Deepgram API key**
* **ElevenLabs API key** (para TTS)
* `pavucontrol` (útil p/ roteamento)

> **Importante**: **NÃO** deixe chaves no código. Use variáveis de ambiente e **revogue** quaisquer chaves que já tenham sido expostas.

---

## 🛠️ Setup

### 1) Instale dependências

```bash
sudo apt install pavucontrol
pip install soundcard numpy requests deepgram-sdk elevenlabs
```

### 2) Inicie o Ollama e baixe o modelo

```bash
ollama pull zongwei/gemma3-translator:1b
# ollama serve (se não iniciar automaticamente)
```

### 3) Exporte as chaves (não commit!)

```bash
export DEEPGRAM_API_KEY="coloque_sua_chave"
export ELEVEN_API_KEY="coloque_sua_chave"
export OLLAMA_URL="http://localhost:11434/api"    # use /chat ou /generate conforme função
export OLLAMA_MODEL="zongwei/gemma3-translator:1b"
```

### 4) (Recomendado) Sink virtual estável para Bluetooth

Bluetooth (A2DP) **não tem** loopback real. Crie um **sink virtual** e duplique para o headset:

```bash
pactl load-module module-null-sink sink_name=transcribe sink_properties=device.description=TranscribeSink
# descubra seu sink bluetooth
pactl list short sinks | grep -i bluez
# duplique TranscribeSink para o sink BT
pactl load-module module-loopback source=transcribe.monitor sink=<SEU_SINK_BLUETOOTH> latency_msec=5
```

No **pavucontrol → Reprodução**, direcione o navegador/player para **TranscribeSink**.
O script vai capturar de **TranscribeSink.monitor** (nível garantido) e você continua ouvindo no fone BT.

---

## ▶️ How to Run / Como usar

### A) **Captions EN→PT** (captura do áudio da reunião)

Arquivo: `playback_transcribe.py` (o que você colou)

1. Garanta que o app de reunião (ou navegador) está enviando áudio para **TranscribeSink** (ou para a saída que possua **monitor** real).
2. Rode:

   ```bash
   export LANGUAGE="en-US"            # idioma do que você ouve
   export DG_MODEL="nova-3"
   export SAMPLE_RATE=48000
   python playback_transcribe.py
   ```
3. Em **pavucontrol → Gravando**, o processo deve estar em **Monitor of TranscribeSink** (ou “Built-in Audio … monitor”).
4. As traduções **PT‑BR** aparecem no terminal.

> **Dica**: no seu código, evite `loopbacks[2]`. Faça seleção por nome/id do monitor (ex: “transcribe”/“built-in”) para não depender de índices variáveis.

### B) **Speak PT→EN (voz)** com TTS

**Mic PT to  EN TTS**)

1. Configure:

   ```bash
   export ELEVEN_API_KEY="..."   # sua chave
   export EL_VOICE_ID="..."      # id da sua voz
   export EL_TTS_MODEL="eleven_flash_v2_5"
   export SAMPLE_RATE=16000      # o script usa 16 kHz para TTS PCM; Deepgram aceita
   ```
2. Rode o script. Ele:

   * escuta **seu microfone** em PT‑BR,
   * transcreve (Deepgram),
   * traduz **PT→EN** (Ollama),
   * sintetiza voz **EN** (ElevenLabs) e toca no **backend pulse**.
3. Para mandar ao **microfone virtual** da videoconferência:

   * mantenha esse output roteado para o seu **VirtualMicSink**/**TranscribeSink**, ou
   * use ferramentas de loopback para que o app de reunião receba o áudio do script como **input**.

---

## ⚙️ Tuning / Ajustes

* **Buffer de frases**
  Use `BUFFER_TIMEOUT` (ex.: `0.6–1.0s`) e `BUFFER_MAX_CHARS` (ex.: `160`) para reduzir cortes no meio da frase.
* **Evite eco** no Ollama
  Use **/api/chat** com `system: "Return ONLY the translation"` ou um prompt com “### TRANSLATION:” e `temperature=0`.
* **48 kHz** no loopback
  `SAMPLE_RATE=48000` é estável para PipeWire/PulseAudio end‑to‑end.
* **Gate de silêncio**
  Descarte blocos com RMS muito baixo para não enviar vazios.

---

## 🧪 Quick sanity checks

* **Ollama up**:

  ```bash
  curl -s http://localhost:11434/api/generate \
    -d '{"model":"zongwei/gemma3-translator:1b","prompt":"Translate to Portuguese: Hello world","stream":false}'
  ```
* **Monitor com nível**: em **pavucontrol → Gravando**, a barra do seu processo **precisa** oscilar.
  Se ficar zerada, direcione o player para **TranscribeSink** ou troque a origem do processo para **Monitor of Built‑in/TranscribeSink**.

---

## 🐛 Troubleshooting

* **Só aparece “Translate this sentence to Portuguese: …” no terminal**
  O modelo ecoou a instrução ou houve erro e você imprimiu o *prompt/texto* em vez da resposta.
  → Use `/api/chat` com “ONLY the translation” **ou** o prompt “### TRANSLATION:”. Logue `r.status_code` e `r.text`.
* **Bluetooth sem áudio no monitor**
  Normal: A2DP não expõe monitor real. Use **TranscribeSink** + loopback para o sink BT.
* **Nada imprime em flush por pontuação**
  Lembre de dar `print(out, flush=True)` também no ramo que encerra por `.`/`!`/`?`.

---

## 🔐 Security

* **NUNCA** commite chaves (`DEEPGRAM_API_KEY`, `ELEVEN_API_KEY`).
* Revogue as que já foram expostas em snippets antigos.

---

## 🗺️ Roadmap

* 🔓 **Port 100% open‑source** (ASR + TTS locais) para latência ~**<50 ms** por trecho curto.
* 🎛️ Auto‑detecção de monitor ativo por RMS.
* 🧪 Testes automatizados de áudio (latência e dropouts).

---

## 📄 Example env (copy/paste)

```bash
# Common
export OLLAMA_URL="http://localhost:11434/api"
export OLLAMA_MODEL="zongwei/gemma3-translator:1b"

# Captions EN->PT (playback_transcribe.py)
export DEEPGRAM_API_KEY="..."
export LANGUAGE="en-US"
export DG_MODEL="nova-3"
export SAMPLE_RATE=48000
export BUFFER_TIMEOUT=0.8
export BUFFER_MAX_CHARS=160

# Mic PT->EN + TTS
export ELEVEN_API_KEY="..."
export EL_VOICE_ID="HDNjMGNzhjXlh3sYMYQI" #ipssbruno real voice
export EL_TTS_MODEL="eleven_flash_v2_5"
```

---

## 🧭 Notes from your code (applied)

* Usa `python-soundcard` para pegar **loopback** (evita a dependência do “monitor” do PortAudio).
* Faz **downmix** para **mono PCM16** antes de enviar à Deepgram.
* **Agrega** parciais em `printer_worker` para evitar tradução de **meias frases**.
* Para estabilidade com Bluetooth, preferir **TranscribeSink.monitor** (não indexar por `[2]`).


