"""Escada de chaves × modelos do Gemini.

A cota gratuita é por projeto Google (= por chave de API) e por modelo.
O pool tenta na ordem: modelo melhor em todas as chaves, depois o próximo
modelo — e lembra por um tempo quais combinações esgotaram a cota diária,
para não desperdiçar chamadas em becos sem saída.

Cada tipo de falha desce a escada de um jeito: cota (429/dia) é da chave
NAQUELE modelo, então a mesma chave segue viva num degrau abaixo; sobrecarga
(503) é do modelo inteiro, e insistir com outra chave só cai na mesma fila.
Nenhuma das duas deve chegar ao chefe como erro enquanto houver degrau.
"""

from __future__ import annotations

import asyncio
import logging
import time

from google import genai
from google.genai import errors

log = logging.getLogger("friday.llm")

EXHAUST_TTL = 30 * 60  # re-testa uma combinação esgotada a cada 30 min
# Sobrecarga (503) é do MODELO, não da chave: o pico de demanda é no servidor
# do Google, então a mesma chave em outro modelo passa e outra chave no mesmo
# modelo bate na mesma fila. TTL curto porque esses picos são passageiros.
SOBRECARGA_TTL = 2 * 60


class GeminiPool:
    def __init__(self, config):
        self.keys: list[str] = config.gemini_api_keys
        self.models: list[str] = config.models
        self.clients = [genai.Client(api_key=key) for key in self.keys]
        self._exhausted: dict[tuple[int, str], float] = {}
        # (dia, chave, modelo) -> requisições bem-sucedidas (zera no restart)
        self.usage: dict[tuple[str, int, str], int] = {}
        # modelos que a API não reconhece (ID errado no config)
        self._unknown_models: set[str] = set()
        # modelos sobrecarregados agora (503) -> até quando pular
        self._sobrecarregados: dict[str, float] = {}

    def _available(self):
        now = time.time()
        for model in self.models:
            for i, client in enumerate(self.clients):
                # relido a cada volta: um 404 no meio descarta o modelo na hora,
                # sem gastar as outras chaves com um ID que não existe.
                if model in self._unknown_models:
                    break
                # 503: desce um degrau em vez de insistir com as outras chaves.
                if self._sobrecarregados.get(model, 0) > now:
                    break
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
                    if exc.code == 503:
                        # "high demand": a fila é do modelo. Insistir na mesma
                        # chave só demora, e trocar de chave cai na mesma fila —
                        # o próximo degrau da escada é que resolve.
                        self._sobrecarregados[model] = time.time() + SOBRECARGA_TTL
                        log.warning("modelo %s sobrecarregado (503) — descendo a escada", model)
                        break
                    if exc.code == 500 and attempt == 1:
                        # erro interno costuma ser sorte: uma segunda tentativa vale.
                        await asyncio.sleep(5)
                        continue
                    if exc.code == 500:
                        log.warning("erro interno persistente em %s — próxima combinação", model)
                        break
                    if exc.code == 404:
                        # ID de modelo que a API não conhece (typo no config.yaml):
                        # não adianta tentar em outra chave — pula para o próximo
                        # degrau em vez de derrubar a conversa inteira.
                        self._unknown_models.add(model)
                        log.error("modelo '%s' não existe para esta API — "
                                  "ignorando e usando o próximo da escada", model)
                        break
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
                if self._sobrecarregados.get(model, 0) > now:
                    status = "SOBRECARREGADO (503) por ora"
                elif self._exhausted.get((i, model), 0) > now:
                    status = "ESGOTADA por ora"
                else:
                    status = "disponível"
                lines.append(f"chave {i + 1} × {model}: {used} req hoje (desde o último restart) — {status}")
        return "\n".join(lines)
