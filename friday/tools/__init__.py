"""Registro de ferramentas expostas ao Gemini via function calling."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import Config

MAX_OUTPUT = 6000


def truncate(text: str, limit: int = MAX_OUTPUT) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… (saída cortada em {limit} caracteres)"


@dataclass
class ToolContext:
    """Dependências que as ferramentas recebem da interface ativa."""

    config: "Config"
    # Pergunta ao usuário antes de ações perigosas; retorna True se autorizado.
    confirm: Callable[[str], Awaitable[bool]]
    # Envia mensagem direta ao chefe sem passar pelo modelo (avisos de sistema).
    send: Callable[[str], Awaitable[None]] | None = None
    # Pool de chaves/modelos Gemini compartilhado (escada de fallback).
    llm: Any = None
    # Envia áudio falado ao chefe (voice note no Telegram); None se não houver.
    send_voice: Callable[[str], Awaitable[None]] | None = None


@dataclass
class ToolMedia:
    """Retorno de ferramenta que carrega um arquivo para o Gemini VER
    (PDF, imagem...), além da nota em texto."""

    note: str
    data: bytes
    mime: str


@dataclass
class Tool:
    declaration: dict[str, Any]
    handler: Callable[..., Awaitable[str]]  # async (ctx, **args) -> str


def build_tools(config: "Config") -> dict[str, Tool]:
    """Monta as ferramentas disponíveis conforme a configuração."""
    from . import home, maintenance, remote, shell, web
    from .. import memory

    tools: dict[str, Tool] = {}

    def add(tool: Tool):
        tools[tool.declaration["name"]] = tool

    add(shell.TOOL)
    add(maintenance.TOOL)

    from . import documents, speak

    add(speak.TOOL)
    add(documents.TOOL)

    async def _usage(ctx: "ToolContext") -> str:
        if not ctx.llm:
            return "Pool de modelos indisponível."
        return ctx.llm.report()

    add(
        Tool(
            declaration={
                "name": "usage_report",
                "description": "Mostra o uso das cotas do Gemini hoje, por chave de API e modelo.",
                "parameters": {"type": "OBJECT", "properties": {}},
            },
            handler=_usage,
        )
    )
    add(web.SEARCH_TOOL)
    add(web.FETCH_TOOL)
    add(
        Tool(
            declaration={
                "name": "remember",
                "description": (
                    "Guarda um fato permanentemente na memória (preferências do chefe, "
                    "informações importantes, lembretes de longo prazo)."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "fact": {"type": "STRING", "description": "O fato a memorizar, em uma frase."}
                    },
                    "required": ["fact"],
                },
            },
            handler=lambda ctx, fact: _async_value(memory.remember(fact)),
        )
    )

    if config.machines:
        add(remote.RUN_ON_TOOL)
        if any(m.mac_address for m in config.machines.values()):
            add(remote.WAKE_TOOL)

    if config.ha_token:
        add(home.DEVICES_TOOL)
        add(home.CONTROL_TOOL)
        add(home.ANNOUNCE_TOOL)

    from . import browser, google_workspace, notion, outlook

    if browser.available():
        add(browser.BROWSE_TOOL)
        add(browser.SCREENSHOT_TOOL)

    if google_workspace.has_credentials():
        add(google_workspace.LIST_EMAILS_TOOL)
        add(google_workspace.READ_EMAIL_TOOL)
        add(google_workspace.SEND_EMAIL_TOOL)
        add(google_workspace.CALENDAR_TOOL)
        add(google_workspace.CREATE_EVENT_TOOL)

    if config.ms_client_id and outlook.has_credentials():
        add(outlook.LIST_TOOL)
        add(outlook.READ_TOOL)
        add(outlook.SEND_TOOL)

    if config.notion_token:
        add(notion.SEARCH_TOOL)
        add(notion.READ_PAGE_TOOL)
        add(notion.APPEND_TOOL)

    return tools


async def _async_value(value: str) -> str:
    return value
