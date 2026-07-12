"""Executa comandos na máquina onde a Friday roda (o servidor)."""

from __future__ import annotations

import asyncio
import re

from . import Tool, ToolContext, truncate

# Padrões que exigem confirmação do chefe antes de executar.
DANGEROUS = re.compile(
    r"""(?x)
    \brm\s |
    \bsudo\b |
    \bmkfs |
    \bdd\s |
    \bshutdown\b | \breboot\b | \bpoweroff\b | \bhalt\b |
    \bkill(all)?\b |
    \bsystemctl\s+(stop|disable|mask|restart)\b |
    \b(apt|apt-get|dnf|yum|pacman)\s+(remove|purge|autoremove|-R)\b |
    \bchmod\s+-R | \bchown\s+-R |
    >\s*/dev/ |
    \bmv\s+/ |
    \btruncate\b |
    \buserdel\b | \bpasswd\b |
    \bcrontab\s+-r
    """
)

TIMEOUT = 90


def is_dangerous(command: str) -> bool:
    return bool(DANGEROUS.search(command))


async def run_local(command: str, timeout: int = TIMEOUT) -> str:
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return f"Comando excedeu o tempo limite de {timeout}s e foi encerrado."
    text = out.decode(errors="replace")
    status = "" if proc.returncode == 0 else f"\n(código de saída: {proc.returncode})"
    return truncate(text) + status if text.strip() else f"(sem saída){status}"


async def _handler(ctx: ToolContext, command: str) -> str:
    if is_dangerous(command):
        ok = await ctx.confirm(f"Executar no servidor:\n`{command}`")
        if not ok:
            return "Ação cancelada pelo chefe."
    return await run_local(command)


TOOL = Tool(
    declaration={
        "name": "run_command",
        "description": (
            "Executa um comando de shell no servidor onde você (JARVIS) está rodando. "
            "Use para consultar o sistema, gerenciar arquivos, ver processos etc."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "command": {"type": "STRING", "description": "Comando de shell a executar."}
            },
            "required": ["command"],
        },
    },
    handler=_handler,
)
