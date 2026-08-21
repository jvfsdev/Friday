"""Manda arquivo para o chefe pelo Telegram — do servidor ou de outra máquina.

Ler um documento e resumir resolve metade dos casos; a outra metade é "me
manda esse arquivo", quando ele quer o original no celular.
"""

from __future__ import annotations

from pathlib import Path

from . import Tool, ToolContext

LIMITE_TELEGRAM = 45 * 1024 * 1024   # o Telegram recusa acima de ~50 MB


async def _enviar(ctx: ToolContext, path: str, machine: str = "", legenda: str = "") -> str:
    if not ctx.send_file:
        return "Não tenho por onde enviar arquivo nesta interface."

    nome = Path(path).name
    if machine:
        from .documents import _fetch_remote

        dados = await _fetch_remote(ctx, machine, path)
        if isinstance(dados, str):
            return dados          # já vem com a explicação do erro
        origem = f"{machine}:{path}"
    else:
        local = Path(path).expanduser()
        if not local.exists():
            return f"Arquivo não encontrado: {local}"
        dados = local.read_bytes()
        origem = str(local)

    if len(dados) > LIMITE_TELEGRAM:
        return (f"O arquivo tem {len(dados) // 1_000_000} MB e o Telegram só aceita até 50. "
                "Posso resumir o conteúdo, ou mandar por email como anexo, se preferir.")

    await ctx.send_file(nome, dados, legenda or f"📎 {nome}")
    return f"Arquivo '{nome}' enviado ao chefe ({len(dados) // 1024} KB, de {origem})."


TOOL = Tool(
    declaration={
        "name": "enviar_arquivo",
        "description": (
            "Manda um arquivo para o chefe no Telegram, do servidor ou de outra máquina "
            "(mac, pc). Use quando ele quiser o documento em si, não um resumo."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path": {"type": "STRING", "description": "Caminho do arquivo."},
                "machine": {"type": "STRING", "description": "Máquina remota; vazio = o próprio servidor."},
                "legenda": {"type": "STRING", "description": "Uma linha explicando o que é."},
            },
            "required": ["path"],
        },
    },
    handler=_enviar,
)
