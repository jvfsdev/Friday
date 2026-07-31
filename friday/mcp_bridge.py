"""Ponte MCP: pluga servidores Model Context Protocol como ferramentas do JARVIS.

Qualquer servidor MCP (ex.: openfinance-analyst) vira um bloco no config.yaml:

    mcp_servers:
      openfinance:
        command: node
        args: [/home/jarvis/openfinance-analyst/dist/index.js]
        env: {PLUGGY_CLIENT_ID: "...", PLUGGY_CLIENT_SECRET: "..."}

As ferramentas dele aparecem para o modelo prefixadas com o nome do servidor
(ex.: openfinance_spending_by_category). Requer: pip install -e ".[mcp]"
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import AsyncExitStack

from .tools import Tool, truncate

log = logging.getLogger("friday.mcp")

CALL_TIMEOUT = 180  # syncs de dados podem demorar

_GEMINI_TYPES = {
    "string": "STRING", "number": "NUMBER", "integer": "INTEGER",
    "boolean": "BOOLEAN", "array": "ARRAY", "object": "OBJECT",
}


def _clean_schema(schema) -> dict:
    """Converte JSON Schema (MCP) para o subconjunto que o Gemini aceita."""
    if not isinstance(schema, dict):
        return {"type": "STRING"}
    out: dict = {}
    tipo = schema.get("type")
    if isinstance(tipo, list):  # ["string", "null"] etc.
        tipo = next((t for t in tipo if t != "null"), "string")
    if tipo in _GEMINI_TYPES:
        out["type"] = _GEMINI_TYPES[tipo]
    if schema.get("description"):
        out["description"] = str(schema["description"])[:500]
    if "enum" in schema:
        out["enum"] = [str(v) for v in schema["enum"]]
        out.setdefault("type", "STRING")
    if out.get("type") == "OBJECT" or "properties" in schema:
        out["type"] = "OBJECT"
        props = {k: _clean_schema(v) for k, v in (schema.get("properties") or {}).items()}
        if props:
            out["properties"] = props
            required = [r for r in schema.get("required", []) if r in props]
            if required:
                out["required"] = required
    if out.get("type") == "ARRAY":
        out["items"] = _clean_schema(schema.get("items", {}))
    out.setdefault("type", "STRING")
    return out


class McpBridge:
    def __init__(self, config):
        self.config = config
        self.stack = AsyncExitStack()
        self._route: dict[str, tuple] = {}  # nome exposto -> (session, nome original)

    async def start(self) -> dict[str, Tool]:
        """Sobe os servidores configurados e devolve as ferramentas deles."""
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            log.error("mcp_servers configurado, mas o pacote 'mcp' não está "
                      "instalado — rode: pip install -e '.[mcp]'")
            return {}

        tools: dict[str, Tool] = {}
        for server, spec in (self.config.mcp_servers or {}).items():
            stack = AsyncExitStack()  # pilha própria: falhou, fecha na hora
            try:
                params = StdioServerParameters(
                    command=spec["command"],
                    args=[str(a) for a in (spec.get("args") or [])],
                    env={**os.environ, **{k: str(v) for k, v in (spec.get("env") or {}).items()}},
                    cwd=spec.get("cwd"),
                )
                read, write = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(ClientSession(read, write))
                await asyncio.wait_for(session.initialize(), timeout=45)
                listed = await asyncio.wait_for(session.list_tools(), timeout=30)
            except Exception as exc:
                log.error("servidor MCP '%s' não subiu (%s: %s) — seguindo sem ele",
                          server, type(exc).__name__, exc)
                try:
                    await stack.aclose()
                except Exception:
                    pass
                continue
            self.stack.push_async_callback(stack.aclose)

            nomes = []
            for t in listed.tools:
                exposto = f"{server}_{t.name}"
                self._route[exposto] = (session, t.name)
                parameters = _clean_schema(t.inputSchema or {})
                if parameters.get("type") != "OBJECT":
                    parameters = {"type": "OBJECT", "properties": {}}
                tools[exposto] = Tool(
                    declaration={
                        "name": exposto,
                        "description": (t.description or f"Ferramenta {t.name} do servidor {server}")[:900],
                        "parameters": parameters,
                    },
                    handler=self._make_handler(exposto),
                )
                nomes.append(t.name)
            log.info("MCP '%s' no ar com %d ferramentas: %s", server, len(nomes), ", ".join(nomes))
        return tools

    def _make_handler(self, exposto: str):
        async def handler(ctx, **args) -> str:
            session, original = self._route[exposto]
            result = await asyncio.wait_for(
                session.call_tool(original, args or {}), timeout=CALL_TIMEOUT
            )
            textos = [c.text for c in result.content if getattr(c, "text", None)]
            corpo = "\n".join(textos).strip() or "(sem retorno em texto)"
            if getattr(result, "isError", False):
                return f"O servidor MCP retornou erro: {truncate(corpo, 2000)}"
            return truncate(corpo, 12000)

        return handler

    async def stop(self):
        await self.stack.aclose()
