"""Estados que o JARVIS liga e desliga a pedido do chefe (não perturbe etc.)."""

from __future__ import annotations

from .. import flags
from . import Tool, ToolContext

ESTADOS = {
    "nao_perturbe": "não liga nem manda alerta que fura o silêncio; só mensagem no Telegram",
}


async def _ligar(ctx: ToolContext, estado: str, minutos: int = 0, motivo: str = "") -> str:
    if estado not in ESTADOS:
        return f"Estado '{estado}' não existe. Disponíveis: {', '.join(ESTADOS)}."
    fim = flags.ativar(estado, int(minutos) or None, motivo, ctx.config.timezone)
    quando = f"até {fim[11:16]}" if fim else "até você mandar desligar"
    return f"Modo '{estado}' ligado {quando}." + (f" Motivo: {motivo}." if motivo else "")


async def _desligar(ctx: ToolContext, estado: str) -> str:
    if flags.desativar(estado):
        return f"Modo '{estado}' desligado. Volto a te alcançar normalmente."
    return f"O modo '{estado}' já estava desligado."


async def _situacao(ctx: ToolContext) -> str:
    ativos = flags.listar(ctx.config.timezone)
    if not ativos:
        return "Nenhum modo especial ligado — te alcanço por todos os canais."
    linhas = []
    for nome, dados in ativos.items():
        prazo = dados.get("ate")
        linhas.append(f"{nome}: até {prazo[11:16] if prazo else 'desligarem'}"
                      + (f" ({dados['motivo']})" if dados.get("motivo") else ""))
    return "\n".join(linhas)


LIGAR_TOOL = Tool(
    declaration={
        "name": "ligar_modo",
        "description": (
            "Liga um modo temporário quando o chefe avisa que vai ficar indisponível "
            "(reunião, encontro, cinema, dormindo). 'nao_perturbe': " + ESTADOS["nao_perturbe"] +
            ". Sempre estime uma duração pelo que ele disse — modo esquecido ligado é pior "
            "que modo nenhum."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "estado": {"type": "STRING", "description": "nao_perturbe"},
                "minutos": {"type": "INTEGER", "description": "Duração; 0 = até mandarem desligar."},
                "motivo": {"type": "STRING", "description": "Ex.: 'reunião com o cliente'."},
            },
            "required": ["estado"],
        },
    },
    handler=_ligar,
)

DESLIGAR_TOOL = Tool(
    declaration={
        "name": "desligar_modo",
        "description": "Desliga um modo temporário (ex.: quando o chefe diz que a reunião acabou).",
        "parameters": {
            "type": "OBJECT",
            "properties": {"estado": {"type": "STRING", "description": "nao_perturbe"}},
            "required": ["estado"],
        },
    },
    handler=_desligar,
)

SITUACAO_TOOL = Tool(
    declaration={
        "name": "modos_ativos",
        "description": "Mostra quais modos temporários estão ligados e até quando.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    handler=_situacao,
)
