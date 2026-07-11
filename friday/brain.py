"""O cérebro da Friday: conversa com o Gemini + loop de function calling."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from google import genai
from google.genai import errors, types

from . import memory
from .config import Config, PERSONA_FILE
from .tools import Tool, ToolContext

log = logging.getLogger("friday.brain")

MAX_TOOL_ROUNDS = 12
MAX_RETRIES = 4


class Brain:
    def __init__(self, config: Config, tools: dict[str, Tool], ctx: ToolContext):
        self.config = config
        self.tools = tools
        self.ctx = ctx
        self.client = genai.Client(api_key=config.gemini_api_key)
        self.history: list[types.Content] = []
        self._lock = asyncio.Lock()  # uma conversa por vez (rotinas x chat)

    def reset(self):
        self.history.clear()

    def _system_prompt(self) -> str:
        persona = PERSONA_FILE.read_text(encoding="utf-8")
        now = datetime.now(ZoneInfo(self.config.timezone)).strftime("%A, %d/%m/%Y %H:%M")
        machines = ", ".join(self.config.machines) or "nenhuma configurada"
        return (
            f"{persona}\n\n"
            f"# Contexto atual\n"
            f"- Agora: {now} ({self.config.timezone})\n"
            f"- Máquinas remotas disponíveis: {machines}\n\n"
            f"# Memória\n{memory.load_memory()}"
        )

    async def ask(self, user_text: str) -> str:
        async with self._lock:
            self.history.append(
                types.Content(role="user", parts=[types.Part(text=user_text)])
            )
            try:
                answer = await self._run_loop()
            except errors.APIError as exc:
                log.error("Erro da API Gemini: %s", exc)
                self.history.pop()  # não deixa a conversa num estado quebrado
                if exc.code == 429:
                    return (
                        "Estourei o limite gratuito do Gemini por agora, chefe. "
                        "Tenta de novo em um minuto."
                    )
                return f"Deu erro na API do Gemini ({exc.code}): {exc.message}"
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
            for call in calls:
                result = await self._execute(call.name, dict(call.args or {}))
                response_parts.append(
                    types.Part.from_function_response(
                        name=call.name, response={"result": result}
                    )
                )
            self.history.append(types.Content(role="user", parts=response_parts))

        return "Rodei ferramentas demais numa tarefa só e parei por segurança. Reformula o pedido?"

    async def _generate(self, gen_config: types.GenerateContentConfig):
        delay = 10
        for attempt in range(MAX_RETRIES):
            try:
                return await self.client.aio.models.generate_content(
                    model=self.config.model,
                    contents=self.history,
                    config=gen_config,
                )
            except errors.APIError as exc:
                retriable = exc.code in (429, 500, 503)
                if not retriable or attempt == MAX_RETRIES - 1:
                    raise
                log.warning("Gemini %s — tentando de novo em %ss", exc.code, delay)
                await asyncio.sleep(delay)
                delay *= 2
        raise RuntimeError("unreachable")

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
