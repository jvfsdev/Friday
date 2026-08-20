"""A voz do JARVIS: síntese local com Piper (offline, leve, pt-BR).

O modelo de voz é baixado uma única vez para state/voices/.

Para a sala, o caminho que importa é `speak_streaming`: ele divide a resposta
em frases e começa a falar a primeira enquanto ainda sintetiza as seguintes.
Sem isso, o chefe espera a síntese do texto inteiro antes de ouvir a primeira
palavra — o que num notebook de 2011 são vários segundos.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import httpx

from .config import ROOT

log = logging.getLogger("friday.tts")

DEFAULT_VOICE = "pt_BR-faber-medium"
VOICES_DIR = ROOT / "state" / "voices"
HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"

# Frases curtas demais viram cortes artificiais; longas demais atrasam o início.
MIN_CHUNK = 25
MAX_CHUNK = 240

_voice_cache: dict[str, object] = {}


def available() -> bool:
    try:
        import piper  # noqa: F401
    except ImportError:
        return False
    return shutil.which("ffmpeg") is not None


def voice_id() -> str:
    """Voz configurada em voice.piper_voice, ou o padrão."""
    from .config import load_config

    try:
        return load_config().voice_settings.get("piper_voice") or DEFAULT_VOICE
    except Exception:
        return DEFAULT_VOICE


def _voice_url(voice: str) -> str:
    """pt_BR-faber-medium -> .../pt/pt_BR/faber/medium/pt_BR-faber-medium"""
    lang, name, quality = voice.split("-", 2)
    familia = lang.split("_")[0]
    return f"{HF_BASE}/{familia}/{lang}/{name}/{quality}/{voice}"


def _download_model(voice: str):
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    base = _voice_url(voice)
    for suffix in (".onnx", ".onnx.json"):
        dest = VOICES_DIR / f"{voice}{suffix}"
        if dest.exists():
            continue
        log.info("baixando voz %s…", dest.name)
        with httpx.stream("GET", f"{base}{suffix}", follow_redirects=True, timeout=300) as resp:
            resp.raise_for_status()
            with dest.open("wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)


def _load(voice: str):
    from piper import PiperVoice

    if voice not in _voice_cache:
        _download_model(voice)
        _voice_cache[voice] = PiperVoice.load(str(VOICES_DIR / f"{voice}.onnx"))
    return _voice_cache[voice]


def _synthesize_sync(text: str, voice: str) -> Path:
    piper_voice = _load(voice)
    out = Path(tempfile.mkstemp(suffix=".wav", prefix="jarvis_voz_")[1])
    with wave.open(str(out), "wb") as wav_file:
        if hasattr(piper_voice, "synthesize_wav"):  # API nova do piper
            piper_voice.synthesize_wav(text, wav_file)
        else:
            piper_voice.synthesize(text, wav_file)
    return out


async def synthesize_wav(text: str, voice: str | None = None) -> Path:
    return await asyncio.to_thread(_synthesize_sync, text, voice or voice_id())


async def synthesize_ogg(text: str, voice: str | None = None) -> Path:
    """Gera OGG/Opus — formato de mensagem de voz do Telegram."""
    wav = await synthesize_wav(text, voice)
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


def split_sentences(text: str) -> list[str]:
    """Quebra em unidades faláveis, agrupando fragmentos curtos."""
    limpo = re.sub(r"[*_`#]+", "", text).strip()
    if not limpo:
        return []
    partes = re.split(r"(?<=[.!?…:;])\s+|\n+", limpo)
    frases: list[str] = []
    for parte in partes:
        parte = parte.strip()
        if not parte:
            continue
        if frases and len(frases[-1]) < MIN_CHUNK:
            frases[-1] = f"{frases[-1]} {parte}"
        else:
            frases.append(parte)
    # quebra as muito longas em vírgulas para não atrasar o começo
    final: list[str] = []
    for frase in frases:
        while len(frase) > MAX_CHUNK:
            corte = frase.rfind(",", 0, MAX_CHUNK)
            if corte < MIN_CHUNK:
                corte = MAX_CHUNK
            final.append(frase[:corte + 1].strip())
            frase = frase[corte + 1:].strip()
        if frase:
            final.append(frase)
    return final


async def play(path: Path):
    """Toca um arquivo de áudio localmente (CLI/alto-falantes)."""
    player = ["afplay", str(path)] if sys.platform == "darwin" else ["aplay", "-q", str(path)]
    if sys.platform != "darwin" and shutil.which("paplay"):
        player = ["paplay", str(path)]
    proc = await asyncio.create_subprocess_exec(
        *player, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    await proc.wait()


async def speak_streaming(text: str, voice: str | None = None, on_first_word=None):
    """Fala em pipeline: sintetiza a próxima frase enquanto toca a atual."""
    voice = voice or voice_id()
    frases = split_sentences(text)
    if not frases:
        return

    fila: asyncio.Queue = asyncio.Queue(maxsize=2)

    async def produtor():
        for frase in frases:
            try:
                caminho = await asyncio.to_thread(_synthesize_sync, frase, voice)
            except Exception:
                log.exception("falha ao sintetizar trecho")
                continue
            await fila.put(caminho)
        await fila.put(None)

    tarefa = asyncio.create_task(produtor())
    primeiro = True
    try:
        while True:
            caminho = await fila.get()
            if caminho is None:
                break
            if primeiro and on_first_word:
                primeiro = False
                try:
                    on_first_word()
                except Exception:
                    pass
            try:
                await play(caminho)
            finally:
                caminho.unlink(missing_ok=True)
    finally:
        tarefa.cancel()
