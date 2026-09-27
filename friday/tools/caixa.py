"""Ferramenta de fluxo de caixa: "vai sobrar dinheiro esse mês?".

Só existe quando o openfinance-analyst está plugado (é dele que vêm os
dados). A conta é toda do friday/fluxo.py; o modelo só repassa.
"""

from __future__ import annotations

from .. import fluxo
from . import Tool, ToolContext


def registrar(tools: dict) -> None:
    """Adiciona a ferramenta se o Open Finance estiver no ar."""
    sync = tools.get("openfinance_sync")
    if not sync:
        return

    async def _fluxo(ctx: ToolContext, dias: int = fluxo.HORIZONTE_PADRAO) -> str:
        p = await fluxo.projecao_atual(ctx.config, sincronizar=sync.handler,
                                       dias=max(7, min(int(dias), 120)))
        return fluxo.resumo(p)

    tools["fluxo_de_caixa"] = Tool(
        declaration={
            "name": "fluxo_de_caixa",
            "description": (
                "Projeta o saldo das contas do chefe dia a dia daqui para a frente: "
                "entradas e saídas que se repetem todo mês, a fatura do cartão (o que "
                "já está nela e quanto deve chegar no ritmo atual) e compromissos da "
                "agenda com valor em R$. Responde 'vai sobrar dinheiro?', 'consigo "
                "pagar a fatura?', 'quanto preciso trazer para a conta e até quando?'. "
                "Os números são calculados em código: repita-os exatos, não recalcule. "
                "Os lançamentos vêm só com o nome de quem recebe/paga — não suponha o que "
                "são (não chame uma transferência a uma pessoa de 'aluguel')."
            ),
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "dias": {"type": "INTEGER", "description": "Horizonte em dias (padrão 45)."},
                },
            },
        },
        handler=_fluxo,
    )
