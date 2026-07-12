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

from .brain import Brain
from .config import Config

log = logging.getLogger("friday.voice")

RATE = 16000
CHUNK = 1280  # 80 ms — tamanho que o openWakeWord espera


class VoiceLoop:
    def __init__(self, config: Config, brain: Brain, on_state=None):
        self.config = config
        self.brain = brain
        self.on_state = on_state or (lambda state: None)
        s = config.voice_settings
        self.wake_threshold = float(s.get("wake_threshold", 0.5))
        self.silence_seconds = float(s.get("silence_seconds", 1.2))
        self.max_utterance = float(s.get("max_utterance_seconds", 15))
        self.input_device = s.get("input_device")  # None = padrão do sistema

    def _set(self, state: str):
        log.info("estado de voz: %s", state)
        try:
            self.on_state(state)
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
        log.info("ouvindo a sala (wake word: hey jarvis)")
        with stream:
            self._set("idle")
            while True:
                chunk = await queue.get()
                scores = wake.predict(np.frombuffer(chunk, dtype=np.int16))
                if scores.get("hey_jarvis", 0) < self.wake_threshold:
                    continue

                wake.reset()
                self._set("listening")
                utterance = await self._record_utterance(queue, np)
                if utterance is None:
                    self._set("idle")
                    continue

                self._set("thinking")
                try:
                    answer = await self.brain.ask(
                        "[O chefe falou com você pelo microfone da sala — responda "
                        "curto, vai virar fala.]",
                        media=[(utterance, "audio/wav")],
                    )
                    self._set("speaking")
                    await self._speak(answer)
                except Exception:
                    log.exception("interação por voz falhou")
                self._drain(queue)  # descarta o que o mic pegou da própria fala
                self._set("idle")

    async def _record_utterance(self, queue, np) -> bytes | None:
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
            if rms > 300:  # há fala
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
        path = await tts.synthesize_wav(text)
        await tts.play(path)
        path.unlink(missing_ok=True)

    @staticmethod
    def _drain(queue):
        while not queue.empty():
            queue.get_nowait()
