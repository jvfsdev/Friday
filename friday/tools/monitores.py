"""O JARVIS cria e remove seus próprios vigias, a pedido do chefe.

"me avisa quando chegar o email da bolsa" vira um monitor de Gmail que se
apaga sozinho depois de avisar. Sem editar config, sem reiniciar serviço.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..config import MonitorSpec
from . import Tool, ToolContext

# Campos obrigatórios de cada tipo — recusar cedo é melhor que criar um vigia
# quebrado que só falha daqui a cinco minutos, calado.
OBRIGATORIOS = {
    "gmail": ("query",),
    "drive_folder": ("folder_id",),
    "ha_state": ("entity", "to"),
    "ping": ("host",),
    "command": ("command",),
    "disk": (),
}


async def _criar(
    ctx: ToolContext, nome: str, tipo: str, prompt: str,
    intervalo_minutos: int = 5, urgencia: str = "normal", uma_vez: bool = False,
    expira_em_horas: int = 0, query: str = "", account: str = "", folder_id: str = "",
    entity: str = "", to: str = "", host: str = "", command: str = "",
    path: str = "", threshold_percent: int = 0,
) -> str:
    if not ctx.monitor:
        return "Não consigo mexer nos monitores agora."
    if tipo not in OBRIGATORIOS:
        return f"Tipo '{tipo}' não existe. Use: {', '.join(OBRIGATORIOS)}."

    params: dict = {k: v for k, v in {
        "query": query, "account": account, "folder_id": folder_id,
        "entity": entity, "to": to, "host": host, "command": command,
        "path": path, "threshold_percent": threshold_percent or None,
    }.items() if v}

    faltando = [c for c in OBRIGATORIOS[tipo] if c not in params]
    if faltando:
        return f"Faltou {', '.join(faltando)} para um monitor do tipo '{tipo}'."

    # Vigia que roda comando é o único que executa coisa no servidor sozinho,
    # em intervalo, sem ninguém olhando — esse pede aval explícito.
    if tipo == "command":
        from .shell import is_dangerous

        if is_dangerous(command) and ctx.confirm:
            if not await ctx.confirm(f"Criar um monitor que roda a cada "
                                     f"{intervalo_minutos} min:\n`{command}`"):
                return "Monitor não criado — o chefe cancelou."

    params.update({"prompt": prompt, "urgency": urgencia, "_nome": nome})
    if uma_vez:
        params["uma_vez"] = True
    if expira_em_horas:
        fim = datetime.now(ZoneInfo(ctx.config.timezone)) + timedelta(hours=int(expira_em_horas))
        params["expira_em"] = fim.isoformat(timespec="minutes")

    erro = ctx.monitor.adicionar(MonitorSpec(
        name=nome, check=tipo, interval_minutes=max(1, int(intervalo_minutos)), params=params,
    ))
    if erro:
        return f"Não deu para criar: {erro}"

    detalhes = [f"a cada {intervalo_minutos} min"]
    if uma_vez:
        detalhes.append("some depois de avisar")
    if expira_em_horas:
        detalhes.append(f"expira em {expira_em_horas}h")
    return f"Vigia '{nome}' criado ({', '.join(detalhes)}). Aviso você quando acontecer."


async def _listar(ctx: ToolContext) -> str:
    return ctx.monitor.listar() if ctx.monitor else "Monitores indisponíveis."


async def _remover(ctx: ToolContext, nome: str) -> str:
    if not ctx.monitor:
        return "Monitores indisponíveis."
    if ctx.monitor.remover(nome):
        return f"Parei de vigiar '{nome}'."
    return (f"Não achei um vigia chamado '{nome}' entre os que eu criei. "
            "Os do config.yaml só o chefe muda.")


CRIAR_TOOL = Tool(
    declaration={
        "name": "criar_monitor",
        "description": (
            "Cria um vigia novo quando o chefe pede para ser avisado de algo — email "
            "específico que ele espera, arquivo numa pasta do Drive, serviço que caiu, "
            "máquina que ligou, alguém chegando em casa. Passa a valer na hora, sem "
            "reiniciar nada. Use uma_vez=true quando ele espera UM acontecimento "
            "('me avisa quando chegar o boleto') e expira_em_horas quando a espera tem "
            "prazo — vigia esquecido virando ruído é o pior desfecho."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "nome": {"type": "STRING", "description": "Curto e descritivo: 'email-bolsa'."},
                "tipo": {"type": "STRING", "description": "gmail, drive_folder, ha_state, ping, command, disk"},
                "prompt": {"type": "STRING", "description": "O que você deve fazer/dizer quando disparar."},
                "intervalo_minutos": {"type": "INTEGER", "description": "De quanto em quanto tempo checar (padrão 5)."},
                "urgencia": {"type": "STRING", "description": "normal, high, critical ou decision."},
                "uma_vez": {"type": "BOOLEAN", "description": "Remove o vigia depois do primeiro aviso."},
                "expira_em_horas": {"type": "INTEGER", "description": "Some sozinho depois de N horas."},
                "query": {"type": "STRING", "description": "gmail: busca no formato do Gmail (from:, subject:, is:unread)."},
                "account": {"type": "STRING", "description": "gmail/drive_folder: conta Google."},
                "folder_id": {"type": "STRING", "description": "drive_folder: id da pasta."},
                "entity": {"type": "STRING", "description": "ha_state: entidade do Home Assistant."},
                "to": {"type": "STRING", "description": "ha_state: estado que dispara."},
                "host": {"type": "STRING", "description": "ping: IP ou nome da máquina."},
                "command": {"type": "STRING", "description": "command: comando cujo código de saída != 0 dispara."},
                "path": {"type": "STRING", "description": "disk: caminho a vigiar."},
                "threshold_percent": {"type": "INTEGER", "description": "disk: percentual que dispara."},
            },
            "required": ["nome", "tipo", "prompt"],
        },
    },
    handler=_criar,
)

LISTAR_TOOL = Tool(
    declaration={
        "name": "listar_monitores",
        "description": "Mostra tudo que você está vigiando agora, separando os fixos dos criados a pedido.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    handler=_listar,
)

REMOVER_TOOL = Tool(
    declaration={
        "name": "remover_monitor",
        "description": "Para de vigiar algo que você criou a pedido do chefe.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"nome": {"type": "STRING", "description": "Nome do vigia."}},
            "required": ["nome"],
        },
    },
    handler=_remover,
)
