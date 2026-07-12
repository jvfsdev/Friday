"""Auto-manutenção: a Friday atualiza o próprio código e se reinicia.

O reinício é um os._exit(0): no servidor o systemd (Restart=always) a ressuscita
em segundos; rodando manualmente, é preciso subir de novo na mão.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from . import Tool, ToolContext
from ..config import ROOT
from .shell import run_local

log = logging.getLogger("friday.maintenance")


async def _handler(ctx: ToolContext) -> str:
    ok = await ctx.confirm("Atualizar o JARVIS (git pull + dependências) e reiniciar?")
    if not ok:
        return "Atualização cancelada pelo chefe."

    remotes = await run_local(f"git -C {ROOT} remote")
    if "(sem saída)" in remotes:
        return (
            "Não há remoto git configurado neste repositório, então não tenho de onde "
            "me atualizar. Configure um (git remote add origin ...) primeiro."
        )

    out = await run_local(f"git -C {ROOT} pull --ff-only", timeout=120)
    if "código de saída" in out:
        return f"O git pull falhou, não vou reiniciar:\n{out}"
    if "Already up to date" in out or "Já está atualizado" in out:
        return "Já estou na versão mais recente, chefe. Nada a fazer."

    deps = await run_local(f"{sys.executable} -m pip install -q -e {ROOT}", timeout=300)
    version = await run_local(f"git -C {ROOT} log --oneline -1")

    if ctx.send:
        await ctx.send(f"✅ Atualizada para: {version}\nReiniciando agora — volto em segundos.")
    log.info("autoatualização concluída (%s) — reiniciando", version)
    # Sai depois de o aviso ter sido entregue; o systemd reinicia o serviço.
    asyncio.get_running_loop().call_later(1.0, os._exit, 0)
    return "Reiniciando…"


TOOL = Tool(
    declaration={
        "name": "update_self",
        "description": (
            "Atualiza o seu próprio código (git pull + dependências) e se reinicia. "
            "Use quando o chefe pedir para você se atualizar."
        ),
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    handler=_handler,
)
