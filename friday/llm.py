"""Escada de chaves × modelos do Gemini.

A cota gratuita é por projeto Google (= por chave de API) e por modelo.
O pool tenta na ordem: modelo melhor em todas as chaves, depois o próximo
modelo — e lembra por um tempo quais combinações esgotaram a cota diária,
para não desperdiçar chamadas em becos sem saída.
"""

from __future__ import annotations

import asyncio
import logging
import time

from google import genai
from google.genai import errors

log = logging.getLogger("friday.llm")

EXHAUST_TTL = 30 * 60  # re-testa uma combinação esgotada a cada 30 min


class GeminiPool:
    def __init__(self, config):
        self.keys: list[str] = config.gemini_api_keys
        self.models: list[str] = config.models
        self.clients = [genai.Client(api_key=key) for key in self.keys]
        self._exhausted: dict[tuple[int, str], float] = {}
        # (dia, chave, modelo) -> requisições bem-sucedidas (zera no restart)
        self.usage: dict[tuple[str, int, str], int] = {}

    def _available(self):
        now = time.time()
        for model in self.models:
            for i, client in enumerate(self.clients):
                if self._exhausted.get((i, model), 0) > now:
                    continue
                yield i, client, model

    async def generate(self, contents, gen_config):
        last_exc: Exception | None = None
        for i, client, model in self._available():
            for attempt in (1, 2):
                try:
                    response = await client.aio.models.generate_content(
                        model=model, contents=contents, config=gen_config
                    )
                    day = time.strftime("%Y-%m-%d")
                    self.usage[(day, i, model)] = self.usage.get((day, i, model), 0) + 1
                    return response
                except errors.APIError as exc:
                    last_exc = exc
                    if exc.code == 429:
                        if "PerDay" in str(exc):
                            self._exhausted[(i, model)] = time.time() + EXHAUST_TTL
                            log.warning(
                                "cota diária esgotada: %s na chave %d/%d — descendo a escada",
                                model, i + 1, len(self.clients),
                            )
                            break  # próxima combinação
                        if attempt == 1:  # limite por minuto: respira e tenta 1x
                            await asyncio.sleep(10)
                            continue
                        break
                    if exc.code in (500, 503) and attempt == 1:
                        await asyncio.sleep(5)
                        continue
                    raise  # erro real (4xx de verdade), não adianta trocar de chave
        if last_exc:
            raise last_exc
        raise RuntimeError(
            "todas as combinações de chave/modelo estão com a cota esgotada no momento"
        )

    def report(self) -> str:
        day = time.strftime("%Y-%m-%d")
        now = time.time()
        lines = []
        for model in self.models:
            for i in range(len(self.keys)):
                used = self.usage.get((day, i, model), 0)
                status = "ESGOTADA por ora" if self._exhausted.get((i, model), 0) > now else "disponível"
                lines.append(f"chave {i + 1} × {model}: {used} req hoje (desde o último restart) — {status}")
        return "\n".join(lines)
