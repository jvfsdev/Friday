"""O cérebro da Friday: conversa com o Gemini + loop de function calling."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from google.genai import errors, types

from . import memory
from .config import Config, PERSONA_FILE
from .llm import GeminiPool
from .tools import Tool, ToolContext, ToolMedia

log = logging.getLogger("friday.brain")

MAX_TOOL_ROUNDS = 12


class Brain:
    def __init__(self, config: Config, tools: dict[str, Tool], ctx: ToolContext):
        self.config = config
        self.tools = tools
        self.ctx = ctx
        self.llm = ctx.llm or GeminiPool(config)
        self.history: list[types.Content] = []
        self._lock = asyncio.Lock()  # uma conversa por vez (rotinas x chat)

    def reset(self):
        self.history.clear()

    def _system_prompt(self) -> str:
        from .tools import google_workspace, outlook

        persona = PERSONA_FILE.read_text(encoding="utf-8")
        now = datetime.now(ZoneInfo(self.config.timezone)).strftime("%A, %d/%m/%Y %H:%M")
        machines = ", ".join(self.config.machines) or "nenhuma configurada"
        google = ", ".join(google_workspace.accounts()) or "nenhuma"
        microsoft = ", ".join(outlook.accounts()) or "nenhuma"
        return (
            f"{persona}\n\n"
            f"# Contexto atual\n"
            f"- Agora: {now} ({self.config.timezone})\n"
            f"- Máquinas remotas disponíveis: {machines}\n"
            f"- Contas Google conectadas: {google}\n"
            f"- Contas Microsoft/Outlook conectadas: {microsoft}\n\n"
            f"# Memória\n{memory.load_memory()}"
        )

    async def ask(self, user_text: str, media: list[tuple[bytes, str]] | None = None) -> str:
        """Pergunta à Friday. `media`: pares (bytes, mime_type) — áudio, imagem etc."""
        parts = [types.Part(text=user_text)]
        for data, mime_type in media or []:
            parts.append(types.Part.from_bytes(data=bytes(data), mime_type=mime_type))
        async with self._lock:
            self.history.append(types.Content(role="user", parts=parts))
            try:
                answer = await self._run_loop()
            except errors.APIError as exc:
                log.error("Erro da API Gemini: %s", exc)
                self.history.pop()  # não deixa a conversa num estado quebrado
                if exc.code == 429:
                    return (
                        "Estourei os limites gratuitos do Gemini em todas as chaves e "
                        "modelos por agora, chefe. Tenta de novo mais tarde."
                    )
                return f"Deu erro na API do Gemini ({exc.code}): {exc.message}"
            except Exception as exc:
                log.exception("erro inesperado no cérebro")
                self.history.pop()
                return f"Algo deu errado do meu lado: {type(exc).__name__}: {exc}"
            self._trim_history()
            return answer

    async def _run_loop(self) -> str:
        declarations = [t.declaration for t in self.tools.values()]
        gen_config = types.GenerateContentConfig(
            system_instruction=self._system_prompt(),
            tools=[types.Tool(function_declarations=declarations)],
        )

        for _ in range(MAX_TOOL_ROUNDS):
            response = await self._generate(gen_config)
            content = response.candidates[0].content
            self.history.append(content)

            calls = response.function_calls or []
            if not calls:
                return response.text or "(sem resposta)"

            response_parts = []
            media_parts = []
            for call in calls:
                result = await self._execute(call.name, dict(call.args or {}))
                if isinstance(result, ToolMedia):
                    # A nota vai como resultado da função; os bytes entram como
                    # mídia na mesma mensagem, para o modelo "ver" o arquivo.
                    media_parts.append(
                        types.Part.from_bytes(data=result.data, mime_type=result.mime)
                    )
                    result = result.note
                response_parts.append(
                    types.Part.from_function_response(
                        name=call.name, response={"result": result}
                    )
                )
            self.history.append(
                types.Content(role="user", parts=response_parts + media_parts)
            )

        return "Rodei ferramentas demais numa tarefa só e parei por segurança. Reformula o pedido?"

    async def _generate(self, gen_config: types.GenerateContentConfig):
        # A escada de chaves × modelos (retries, cotas, fallback) mora no pool.
        return await self.llm.generate(self.history, gen_config)

    async def _execute(self, name: str, args: dict) -> str:
        tool = self.tools.get(name)
        if not tool:
            return f"Ferramenta desconhecida: {name}"
        log.info("ferramenta %s(%s)", name, args)
        try:
            return await tool.handler(self.ctx, **args)
        except Exception as exc:  # a ferramenta nunca deve derrubar o loop
            log.exception("ferramenta %s falhou", name)
            return f"A ferramenta {name} falhou: {type(exc).__name__}: {exc}"

    def _trim_history(self):
        # Janela deslizante: corta os turnos mais antigos, sempre começando em 'user'
        # com texto (nunca no meio de uma troca de function call).
        max_items = self.config.history_max_turns * 2
        if len(self.history) <= max_items:
            return
        cut = len(self.history) - max_items
        while cut < len(self.history):
            item = self.history[cut]
            if item.role == "user" and all(p.function_response is None for p in item.parts or []):
                break
            cut += 1
        del self.history[:cut]
