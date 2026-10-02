"""Voz na sala: o microfone e os alto-falantes do notebook são o corpo da Friday.

Pipeline local: wake word (openWakeWord, modelo "hey jarvis") → grava até
silêncio → áudio vai nativo ao Gemini → resposta falada pela Piper.

Ativação: bloco `voice: {enabled: true}` no config.yaml (só faz sentido no
servidor). Dependências: openwakeword, onnxruntime, sounddevice, numpy.

Estados publicados via on_state (idle/listening/thinking/speaking) — é o
gancho para o futuro "rosto" na tela.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
import wave

from . import face_state
from .brain import Brain
from .config import ROOT, Config

log = logging.getLogger("friday.voice")

RATE = 16000
CHUNK = 1280  # 80 ms — tamanho que o openWakeWord espera


BEEP_FILE = ROOT / "state" / "wake_beep.wav"

# Calibração passiva: toda vez que ele acorda — ou QUASE acorda — guarda a
# nota e os 2 s de áudio. É com isso que se ajusta o limiar e se retreina
# a palavra com a voz, o microfone e a sala de verdade (scripts/palavra/).
CALIBRACAO = ROOT / "state" / "calibracao"
NOTA_MINIMA = 0.25
PALAVRA_PADRAO = "modelos/guara.onnx"


def _relatorio(modelo) -> dict:
    """Os valores que o treino escolheu para este modelo, se houver."""
    import json

    try:
        return json.loads(modelo.with_suffix(".relatorio.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
GUARDAR_NO_MAXIMO = 300


def _ensure_beep():
    """Gera uma vez um bipe curto de confirmação (440->660Hz, 160ms)."""
    if BEEP_FILE.exists():
        return BEEP_FILE
    import math
    import struct
    import wave as wavelib

    taxa, dur = 16000, 0.16
    quadros = int(taxa * dur)
    dados = bytearray()
    for i in range(quadros):
        t = i / taxa
        freq = 440 + (660 - 440) * (i / quadros)
        env = min(1.0, i / (taxa * 0.01), (quadros - i) / (taxa * 0.04))
        dados += struct.pack("<h", int(9000 * env * math.sin(2 * math.pi * freq * t)))
    BEEP_FILE.parent.mkdir(exist_ok=True)
    with wavelib.open(str(BEEP_FILE), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(taxa)
        f.writeframes(bytes(dados))
    return BEEP_FILE


class VoiceLoop:
    def __init__(self, config: Config, brain: Brain, on_state=None):
        self.config = config
        self.brain = brain
        # Por padrão publica no rosto (state/face_state.json).
        self.on_state = on_state or face_state.publish
        s = config.voice_settings
        # Palavra de ativação: por padrão o "Ô, Guará" que vem no repositório.
        # Limiar e quadros seguidos vêm do relatório do treino que acompanha o
        # modelo (<modelo>.relatorio.json); o config só sobrescreve se quiser.
        self.wake_model = s.get("wake_model") or PALAVRA_PADRAO
        relatorio = _relatorio(ROOT / self.wake_model)
        self.wake_threshold = float(s.get("wake_threshold") or relatorio.get("limiar", 0.5))
        self.silence_seconds = float(s.get("silence_seconds", 0.9))
        self.max_utterance = float(s.get("max_utterance_seconds", 15))
        self.input_device = s.get("input_device")  # None = padrão do sistema
        # Palavra própria (treinada com scripts/palavra/): caminho do .onnx.
        # Sem ela, o modelo pronto "hey jarvis" do openWakeWord.
        # Quantos quadros de 80 ms seguidos acima do limiar para acordar. Ruído
        # e música dão picos de um quadro; a palavra dura vários. O treino
        # (scripts/palavra/treinar.py) diz qual valor usar junto com o limiar.
        self.wake_seguidos = max(1, int(s.get("wake_seguidos") or relatorio.get("seguidos", 1)))
        # Microfone de notebook capta baixo: a voz chega ~3x acima do ruído, e o
        # detector de voz humana (VAD) não acredita que aquilo é gente — zera a
        # nota. O ganho só amplifica o que vai para o detector; o áudio enviado
        # ao modelo de linguagem continua o original.
        self.ganho_mic = float(s.get("ganho_mic", 1))

    def _set(self, state: str, caption: str | None = None):
        log.info("estado de voz: %s", state)
        try:
            self.on_state(state, caption)
        except Exception:
            pass

    async def run(self):
        """Roda para sempre; blocos de áudio são processados numa thread."""
        try:
            import numpy as np
            import sounddevice as sd
            from openwakeword.model import Model as WakeModel
        except ImportError as exc:
            log.error("voz desativada: dependência faltando (%s). "
                      "pip install openwakeword onnxruntime sounddevice numpy", exc)
            return

        import openwakeword

        from pathlib import Path

        from .config import ROOT

        proprio = (ROOT / self.wake_model) if self.wake_model else None
        if proprio and proprio.exists():
            openwakeword.utils.download_models([])      # só os modelos de áudio de base
            # vad_threshold: o detector de voz humana do openWakeWord zera a nota
            # quando não há fala — corta os disparos com música e ruído, que
            # são a maior parte dos que um modelo treinado em casa ainda tem.
            wake = WakeModel(wakeword_models=[str(proprio)], inference_framework="onnx",
                             vad_threshold=float(self.config.voice_settings.get("wake_vad", 0.3)))
            chave, falada = proprio.stem, f"modelo próprio {proprio.name}"
        else:
            if proprio:
                log.warning("wake_model %s não existe — voltando para 'hey jarvis'", proprio)
            openwakeword.utils.download_models(["hey_jarvis"])
            wake = WakeModel(wakeword_models=["hey_jarvis"], inference_framework="onnx")
            chave, falada = "hey_jarvis", "hey jarvis"
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)

        def on_audio(indata, frames, t, status):
            data = bytes(indata)
            try:
                loop.call_soon_threadsafe(queue.put_nowait, data)
            except RuntimeError:
                pass

        stream = sd.RawInputStream(
            samplerate=RATE, blocksize=CHUNK, dtype="int16", channels=1,
            device=self.input_device, callback=on_audio,
        )
        from . import tts

        await tts.warmup()  # paga import e handshake antes da 1a conversa
        log.info("ouvindo a sala (palavra de ativação: %s, limiar %.2f, %d quadro(s) seguidos, ganho %gx)",
                 falada, self.wake_threshold, self.wake_seguidos, self.ganho_mic)
        piso_ruido = 0.0  # média móvel do RMS ambiente, medida em repouso
        from collections import deque

        anteriores: deque = deque(maxlen=int(2.5 * RATE / CHUNK))   # últimos 2,5 s
        ultimo_registro = 0.0
        corrida = 0
        with stream:
            self._set("idle")
            while True:
                chunk = await queue.get()
                dados = np.frombuffer(chunk, dtype=np.int16)
                rms = float(np.sqrt(np.mean(dados.astype(np.float64) ** 2)))
                piso_ruido = rms if piso_ruido == 0 else 0.97 * piso_ruido + 0.03 * rms
                if self.ganho_mic != 1:
                    dados = np.clip(dados.astype(np.float32) * self.ganho_mic, -32767, 32767).astype(np.int16)
                anteriores.append(dados)
                scores = wake.predict(dados)
                nota = float(scores.get(chave, 0))
                if nota >= NOTA_MINIMA and time.monotonic() - ultimo_registro > 2.0:
                    ultimo_registro = time.monotonic()
                    asyncio.get_running_loop().run_in_executor(
                        None, _registrar, np.concatenate(list(anteriores)), nota,
                        nota >= self.wake_threshold)
                corrida = corrida + 1 if nota >= self.wake_threshold else 0
                if corrida < self.wake_seguidos:
                    continue
                corrida = 0

                wake.reset()
                self._set("listening", "Ouvindo…")
                await self._beep()
                # fala = bem acima do ruído ambiente medido agora há pouco
                limiar = max(400.0, piso_ruido * 1.7)
                utterance = await self._record_utterance(queue, np, limiar)
                if utterance is None:
                    self._set("idle")
                    continue

                self._set("thinking", "Processando…")
                try:
                    answer = await self.brain.ask(
                        "[O chefe falou com você pelo microfone da sala — responda "
                        "curto, vai virar fala.]",
                        media=[(utterance, "audio/wav")],
                    )
                    self._set("speaking", answer)
                    await self._speak(answer)
                except Exception:
                    log.exception("interação por voz falhou")
                self._drain(queue)  # descarta o que o mic pegou da própria fala
                self._set("idle")

    async def _record_utterance(self, queue, np, limiar: float) -> bytes | None:
        frames: list[bytes] = []
        started = time.monotonic()
        last_sound = started
        while True:
            try:
                chunk = await asyncio.wait_for(queue.get(), timeout=2)
            except asyncio.TimeoutError:
                break
            frames.append(chunk)
            rms = float(np.sqrt(np.mean(np.frombuffer(chunk, np.int16).astype(np.float64) ** 2)))
            now = time.monotonic()
            if rms > limiar:  # há fala acima do ruído ambiente
                last_sound = now
            if now - last_sound > self.silence_seconds and now - started > 1.5:
                break
            if now - started > self.max_utterance:
                break
        if time.monotonic() - started < 1.0:
            return None  # acionamento falso, nada dito
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(RATE)
            f.writeframes(b"".join(frames))
        return buffer.getvalue()

    async def _speak(self, text: str):
        from . import tts

        if not tts.available():
            log.error("TTS indisponível — resposta de voz perdida: %s", text[:80])
            return
        # Streaming por frases: a primeira já toca enquanto as outras sintetizam.
        await tts.speak_streaming(text)

    async def _beep(self):
        """Confirma o wake word na hora — o chefe sabe que foi ouvido antes
        mesmo de o Gemini responder."""
        from . import tts

        try:
            await tts.play(_ensure_beep())
        except Exception:
            pass

    @staticmethod
    def _drain(queue):
        while not queue.empty():
            queue.get_nowait()


def _registrar(audio, nota: float, acordou: bool):
    """Guarda um quase-acordar/acordar para calibração (nunca derruba a voz)."""
    try:
        CALIBRACAO.mkdir(parents=True, exist_ok=True)
        nome = f"{time.strftime('%Y%m%d-%H%M%S')}_{nota:.2f}_{'acordou' if acordou else 'quase'}.wav"
        with wave.open(str(CALIBRACAO / nome), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(audio.tobytes())
        antigos = sorted(CALIBRACAO.glob("*.wav"))
        for velho in antigos[:-GUARDAR_NO_MAXIMO]:
            velho.unlink(missing_ok=True)
    except Exception:
        log.exception("não consegui guardar o áudio de calibração")
