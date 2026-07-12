"""Ferramenta de fala: a Friday responde em áudio quando o chefe pede."""

from __future__ import annotations

from . import Tool, ToolContext
from .. import tts


async def _handler(ctx: ToolContext, text: str) -> str:
    if not tts.available():
        return "TTS indisponível nesta máquina (piper-tts ou ffmpeg faltando)."
    if ctx.send_voice:
        await ctx.send_voice(text)
        return "Áudio enviado ao chefe. Não repita o conteúdo por texto; apenas confirme brevemente."
    # Sem interface de voz (ex.: CLI): sintetiza e toca localmente.
    path = await tts.synthesize_wav(text)
    await tts.play(path)
    path.unlink(missing_ok=True)
    return "Áudio reproduzido nos alto-falantes."


TOOL = Tool(
    declaration={
        "name": "speak",
        "description": (
            "Fala um texto em voz alta (mensagem de voz no Telegram ou alto-falantes). "
            "Use APENAS quando o chefe pedir resposta em áudio."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "text": {"type": "STRING", "description": "O que falar, em português natural."}
            },
            "required": ["text"],
        },
    },
    handler=_handler,
)
