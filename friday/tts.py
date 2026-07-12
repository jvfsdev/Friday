"""A voz da Friday: síntese local com Piper (offline, leve, pt-BR).

O modelo de voz é baixado uma única vez para state/voices/.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import httpx

from .config import ROOT

log = logging.getLogger("friday.tts")

VOICE = "pt_BR-faber-medium"
VOICES_DIR = ROOT / "state" / "voices"
HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium"

_voice_cache = None


def available() -> bool:
    try:
        import piper  # noqa: F401
    except ImportError:
        return False
    return shutil.which("ffmpeg") is not None


def _download_model():
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    for suffix in (".onnx", ".onnx.json"):
        dest = VOICES_DIR / f"{VOICE}{suffix}"
        if dest.exists():
            continue
        url = f"{HF_BASE}/{VOICE}{suffix}"
        log.info("baixando voz %s…", dest.name)
        with httpx.stream("GET", url, follow_redirects=True, timeout=300) as resp:
            resp.raise_for_status()
            with dest.open("wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)


def _synthesize_sync(text: str) -> Path:
    global _voice_cache
    from piper import PiperVoice

    if _voice_cache is None:
        _download_model()
        _voice_cache = PiperVoice.load(str(VOICES_DIR / f"{VOICE}.onnx"))

    out = Path(tempfile.mkstemp(suffix=".wav", prefix="friday_voz_")[1])
    with wave.open(str(out), "wb") as wav_file:
        if hasattr(_voice_cache, "synthesize_wav"):  # API nova do piper
            _voice_cache.synthesize_wav(text, wav_file)
        else:
            _voice_cache.synthesize(text, wav_file)
    return out


async def synthesize_wav(text: str) -> Path:
    return await asyncio.to_thread(_synthesize_sync, text)


async def synthesize_ogg(text: str) -> Path:
    """Gera OGG/Opus — formato de mensagem de voz do Telegram."""
    wav = await synthesize_wav(text)
    ogg = wav.with_suffix(".ogg")
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
        "-c:a", "libopus", "-b:a", "32k", str(ogg),
    )
    await proc.wait()
    wav.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg falhou ao converter a voz")
    return ogg


async def play(path: Path):
    """Toca um arquivo de áudio localmente (CLI/alto-falantes)."""
    player = ["afplay", str(path)] if sys.platform == "darwin" else ["aplay", "-q", str(path)]
    if sys.platform != "darwin" and shutil.which("paplay"):
        player = ["paplay", str(path)]
    proc = await asyncio.create_subprocess_exec(
        *player, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    await proc.wait()
