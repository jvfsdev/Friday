"""Controle de outras máquinas (Mac, PC) via SSH e Wake-on-LAN."""

from __future__ import annotations

import socket

import asyncssh

from . import Tool, ToolContext, truncate
from .shell import TIMEOUT, is_dangerous


async def _run_on(ctx: ToolContext, machine: str, command: str) -> str:
    spec = ctx.config.machines.get(machine)
    if not spec:
        known = ", ".join(ctx.config.machines) or "nenhuma"
        return f"Máquina '{machine}' não configurada. Máquinas conhecidas: {known}."

    if is_dangerous(command):
        ok = await ctx.confirm(f"Executar em **{machine}** ({spec.host}):\n`{command}`")
        if not ok:
            return "Ação cancelada pelo chefe."

    try:
        async with asyncssh.connect(
            spec.host, username=spec.user, known_hosts=None, connect_timeout=15
        ) as conn:
            result = await conn.run(command, check=False, timeout=TIMEOUT)
    except (OSError, asyncssh.Error) as exc:
        return f"Falha ao conectar em {machine} ({spec.host}): {exc}"

    out = (result.stdout or "") + (result.stderr or "")
    status = "" if result.exit_status == 0 else f"\n(código de saída: {result.exit_status})"
    return (truncate(out) if out.strip() else "(sem saída)") + status


async def _wake(ctx: ToolContext, machine: str) -> str:
    spec = ctx.config.machines.get(machine)
    if not spec or not spec.mac_address:
        return f"Máquina '{machine}' não tem endereço MAC configurado para Wake-on-LAN."

    mac = bytes.fromhex(spec.mac_address.replace(":", "").replace("-", ""))
    packet = b"\xff" * 6 + mac * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, ("255.255.255.255", 9))
    return f"Pacote Wake-on-LAN enviado para {machine}. Ela deve ligar em instantes."


RUN_ON_TOOL = Tool(
    declaration={
        "name": "run_on_machine",
        "description": (
            "Executa um comando de shell em outra máquina do chefe via SSH "
            "(ex.: 'mac' ou 'pc'). No Mac, apps abrem com `open -a Nome`."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "machine": {"type": "STRING", "description": "Nome da máquina configurada."},
                "command": {"type": "STRING", "description": "Comando a executar nela."},
            },
            "required": ["machine", "command"],
        },
    },
    handler=_run_on,
)

WAKE_TOOL = Tool(
    declaration={
        "name": "wake_machine",
        "description": "Liga uma máquina desligada via Wake-on-LAN.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "machine": {"type": "STRING", "description": "Nome da máquina configurada."}
            },
            "required": ["machine"],
        },
    },
    handler=_wake,
)
