"""A voz do JARVIS, com motores plugáveis.

Medido no servidor (AMD E-300, sem AVX/SSE4.2), por frase:
  edge   ~1,4s  neural, natural, precisa de internet
  espeak ~0,2s  robótica, offline, instantânea
  piper  ~13s   natural e offline, mas inviável nesse CPU (inferência
                neural sem instruções vetoriais); mantida para máquinas
                melhores e como último recurso

`auto` (padrão) usa edge e cai para espeak se a rede falhar — o chefe nunca
fica sem resposta falada.

Para a sala o que importa é `speak_streaming`: divide a resposta em frases e
começa a falar a primeira enquanto sintetiza as seguintes.
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
DEFAULT_EDGE_VOICE = "pt-BR-AntonioNeural"
VOICES_DIR = ROOT / "state" / "voices"
HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"

# Frases curtas demais viram cortes artificiais; longas demais atrasam o início.
MIN_CHUNK = 25
MAX_CHUNK = 240

_voice_cache: dict[str, object] = {}


def available() -> bool:
    """Há algum motor de voz utilizável nesta máquina?"""
    if shutil.which("espeak-ng") or shutil.which("espeak"):
        return True
    try:
        import piper  # noqa: F401
    except ImportError:
        return False
    return shutil.which("ffmpeg") is not None


def _settings() -> dict:
    from .config import load_config

    try:
        return load_config().voice_settings or {}
    except Exception:
        return {}


def backend() -> str:
    return _settings().get("tts_backend", "auto")


def _edge_voice() -> str:
    return _settings().get("edge_voice", DEFAULT_EDGE_VOICE)


async def _run(*args) -> int:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    return await proc.wait()


async def _espeak_wav(text: str) -> Path:
    binario = shutil.which("espeak-ng") or shutil.which("espeak")
    if not binario:
        raise RuntimeError("espeak-ng não instalado")
    out = Path(tempfile.mkstemp(suffix=".wav", prefix="jarvis_voz_")[1])
    velocidade = str(_settings().get("espeak_speed", 165))
    if await _run(binario, "-v", "pt-br", "-s", velocidade, "-w", str(out), text) != 0:
        out.unlink(missing_ok=True)
        raise RuntimeError("espeak falhou")
    return out


async def _edge_audio(text: str) -> Path:
    """Devolve o MP3 da nuvem sem converter — a conversão custava 1,1s no
    servidor antigo, quase o mesmo que a própria requisição de rede."""
    import edge_tts

    mp3 = Path(tempfile.mkstemp(suffix=".mp3", prefix="jarvis_voz_")[1])
    await edge_tts.Communicate(text, _edge_voice()).save(str(mp3))
    return mp3


async def warmup():
    """Paga o import e o primeiro handshake fora da conversa."""
    if backend() in ("auto", "edge"):
        try:
            audio = await _edge_audio("Pronto.")
            audio.unlink(missing_ok=True)
            log.info("voz da nuvem aquecida")
        except Exception as exc:
            log.warning("não consegui aquecer a voz da nuvem: %s", type(exc).__name__)


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


async def synthesize(text: str, voice: str | None = None) -> Path:
    """Sintetiza uma frase com o motor configurado (mp3 ou wav, conforme o
    motor) — use `play` para tocar sem se preocupar com o formato."""
    motor = backend()
    if voice:  # voz explícita = Piper (usada em comparações)
        return await asyncio.to_thread(_synthesize_sync, text, voice)

    if motor in ("auto", "edge"):
        try:
            return await _edge_audio(text)
        except Exception as exc:
            if motor == "edge":
                raise
            log.warning("voz da nuvem indisponível (%s) — usando espeak", type(exc).__name__)
            return await _espeak_wav(text)
    if motor == "espeak":
        return await _espeak_wav(text)
    return await asyncio.to_thread(_synthesize_sync, text, voice_id())


# compatibilidade com chamadas antigas
synthesize_wav = synthesize


async def synthesize_ogg(text: str, voice: str | None = None) -> Path:
    """Gera OGG/Opus — formato de mensagem de voz do Telegram."""
    wav = await synthesize(text, voice)
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
    """Toca um arquivo de áudio localmente, wav ou mp3."""
    if sys.platform == "darwin":
        player = ["afplay", str(path)]
    elif path.suffix == ".mp3":
        binario = shutil.which("mpg123") or shutil.which("ffplay")
        if not binario:
            raise RuntimeError("instale mpg123 para tocar áudio da nuvem")
        player = ([binario, "-q", str(path)] if binario.endswith("mpg123")
                  else [binario, "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)])
    elif shutil.which("paplay"):
        player = ["paplay", str(path)]
    else:
        player = ["aplay", "-q", str(path)]
    proc = await asyncio.create_subprocess_exec(
        *player, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    await proc.wait()


async def speak_streaming(text: str, voice: str | None = None, on_first_word=None):
    """Fala em pipeline: sintetiza a próxima frase enquanto toca a atual."""
    frases = split_sentences(text)
    if not frases:
        return

    fila: asyncio.Queue = asyncio.Queue(maxsize=2)

    async def produtor():
        for frase in frases:
            try:
                caminho = await synthesize(frase, voice)
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
