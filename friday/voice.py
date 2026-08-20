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
        self.wake_threshold = float(s.get("wake_threshold", 0.5))
        self.silence_seconds = float(s.get("silence_seconds", 0.9))
        self.max_utterance = float(s.get("max_utterance_seconds", 15))
        self.input_device = s.get("input_device")  # None = padrão do sistema

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

        openwakeword.utils.download_models(["hey_jarvis"])
        wake = WakeModel(wakeword_models=["hey_jarvis"], inference_framework="onnx")
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
        log.info("ouvindo a sala (wake word: hey jarvis)")
        piso_ruido = 0.0  # média móvel do RMS ambiente, medida em repouso
        with stream:
            self._set("idle")
            while True:
                chunk = await queue.get()
                dados = np.frombuffer(chunk, dtype=np.int16)
                rms = float(np.sqrt(np.mean(dados.astype(np.float64) ** 2)))
                piso_ruido = rms if piso_ruido == 0 else 0.97 * piso_ruido + 0.03 * rms
                scores = wake.predict(dados)
                if scores.get("hey_jarvis", 0) < self.wake_threshold:
                    continue

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
