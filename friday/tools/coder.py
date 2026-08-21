"""Delegação de tarefas de código para o Mac.

O servidor é velho demais para rodar agentes de código (os binários exigem
instruções de CPU pós-2010), então o JARVIS conecta no Mac por SSH e usa o
agente que estiver lá — Claude Code ou Antigravity, mesma interface.

Trilhos de segurança, sempre:
  * só projetos declarados em `code_projects:` no config;
  * árvore limpa antes de começar (nada de misturar com trabalho em curso);
  * sempre num branch novo `jarvis/...`, nunca no principal;
  * commit local, NUNCA push/merge/deploy — a última palavra é do chefe;
  * o editor abre no Mac com o resultado para revisão.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

import asyncssh

from . import Tool, ToolContext, truncate

log = logging.getLogger("friday.coder")

TIMEOUT_AGENTE = 1800  # 30 min
# Só os NOMES: o comando de cada agente vive em scripts/mac_runner.py, que é
# quem roda de fato (com as permissões e o --continue). Declarar comando aqui
# seria configuração que não faz nada — e pior, que engana quem lê.
AGENTES_PADRAO = ("claude", "antigravity")


async def _ssh(config, machine: str, comando: str, timeout: int = 120,
               entrada: str | None = None) -> tuple[int, str]:
    spec = config.machines.get(machine)
    if not spec:
        raise RuntimeError(f"máquina '{machine}' não configurada")
    async with asyncssh.connect(
        spec.host, username=spec.user, known_hosts=None, connect_timeout=15
    ) as conn:
        r = await conn.run(comando, check=False, timeout=timeout, input=entrada)
        return r.exit_status or 0, ((r.stdout or "") + (r.stderr or "")).strip()


def _agentes(config) -> tuple:
    return AGENTES_PADRAO


def _ordem(config, agente: str) -> list[str]:
    disponiveis = _agentes(config)
    if agente and agente != "auto":
        return [agente] if agente in disponiveis else []
    preferencia = config.code_agent_order or list(AGENTES_PADRAO)
    return [a for a in preferencia if a in disponiveis]



async def _executar(config, projeto: str, spec: dict, tarefa: str, agente: str,
                    nova_sessao: bool = False) -> str:
    """Deposita a tarefa para o executor que roda na sessão gráfica do Mac.

    Não chamamos o agente direto por SSH porque o macOS não abre o Keychain
    do login em sessão não-interativa — e é lá que ficam as credenciais do
    Claude Code. O executor (LaunchAgent) tem esse acesso e ainda consegue
    abrir o editor na tela.
    """
    machine = spec["machine"]
    job_id = uuid.uuid4().hex[:10]
    payload = json.dumps(
        {
            "path": spec["path"],
            "tarefa": tarefa,
            "agentes": _ordem(config, agente),
            "novaSessao": bool(nova_sessao),
        },
        ensure_ascii=False,
    )

    pendente = f"~/.jarvis/jobs/pending/{job_id}.json"
    pronto = f"~/.jarvis/jobs/done/{job_id}.json"
    code, saida = await _ssh(
        config, machine,
        f"mkdir -p ~/.jarvis/jobs/pending ~/.jarvis/jobs/done && cat > {pendente}",
        entrada=payload,
    )
    if code != 0:
        return f"Não consegui enviar a tarefa para o {machine}: {saida[:300]}"

    limite = time.monotonic() + TIMEOUT_AGENTE
    avisou_executor = False
    while time.monotonic() < limite:
        await asyncio.sleep(5)
        code, conteudo = await _ssh(config, machine, f"cat {pronto} 2>/dev/null")
        if code == 0 and conteudo.strip():
            break
        if not avisou_executor and time.monotonic() > limite - TIMEOUT_AGENTE + 45:
            avisou_executor = True
            code_p, _ = await _ssh(config, machine, f"test -f {pendente}")
            if code_p == 0:
                log.warning("tarefa parada na fila do %s — executor rodando?", machine)
    else:
        return (f"A tarefa ficou {TIMEOUT_AGENTE // 60} minutos na fila do {machine} sem "
                "resposta. O executor do JARVIS está rodando lá? "
                "(`launchctl list | grep jarvis` no Mac)")

    await _ssh(config, machine, f"rm -f {pronto}")
    try:
        resultado = json.loads(conteudo)
    except json.JSONDecodeError:
        return f"Resposta ilegível do executor: {conteudo[:300]}"

    if not resultado.get("ok"):
        return f"Não deu certo: {resultado.get('resultado', 'sem detalhes')}"

    return (
        f"Pronto, usei o {resultado.get('agente')} no projeto {projeto} "
        f"({resultado.get('sessao', 'sessão nova')}).\n"
        f"Branch: {resultado.get('branch')} (commit local, sem push)\n\n"
        f"{truncate(resultado.get('diffstat', ''), 1500)}\n\n"
        f"Abri o projeto no Mac para você revisar. Resumo do agente:\n"
        f"{truncate(resultado.get('resultado', ''), 1200)}"
    )


async def _codar(ctx: ToolContext, projeto: str, tarefa: str, agente: str = "auto",
                 nova_sessao: bool = False) -> str:
    projetos = ctx.config.code_projects or {}
    spec = projetos.get(projeto)
    if not spec:
        conhecidos = ", ".join(projetos) or "nenhum"
        return f"Projeto '{projeto}' não está na lista permitida. Projetos: {conhecidos}."

    # Agente escrito errado não pode virar "tanto faz": o chefe pediu um.
    ordem = _ordem(ctx.config, agente)
    if not ordem:
        return (f"Agente '{agente}' não existe. Disponíveis: "
                f"{', '.join(_agentes(ctx.config))} — ou 'auto'.")
    if not ctx.jobs:
        return "Registro de trabalhos indisponível."

    config = ctx.config
    job = ctx.jobs.submit(
        "codar", f"{projeto} — {tarefa[:80]}",
        lambda: _executar(config, projeto, spec, tarefa, agente, nova_sessao),
    )
    return (
        f"Trabalho [{job.id}] iniciado no projeto {projeto}. Pode levar alguns minutos; "
        "eu aviso quando terminar. Não repita a tarefa."
    )


async def _status(ctx: ToolContext, job_id: str = "") -> str:
    if not ctx.jobs:
        return "Registro de trabalhos indisponível."
    if job_id:
        job = ctx.jobs.get(job_id)
        if not job:
            return f"Não achei o trabalho [{job_id}]."
        return f"{job.resumo()}\n\n{job.result or '(ainda rodando)'}"
    return ctx.jobs.listar()


CODAR_TOOL = Tool(
    declaration={
        "name": "codar",
        "description": (
            "Delega uma tarefa de programação ao agente de código no Mac do chefe. "
            "Continua a conversa anterior do projeto por padrão, então dá para pedir "
            "ajustes em cima do que foi feito antes. Trabalha sempre num branch novo e "
            "abre o editor para revisão; nunca faz push. Demora minutos: avisa o chefe "
            "e NÃO fique esperando."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "projeto": {"type": "STRING", "description": "Nome do projeto na lista permitida."},
                "tarefa": {"type": "STRING", "description": "A tarefa, detalhada como você explicaria a um colega."},
                "agente": {"type": "STRING", "description": "claude, antigravity ou auto (padrão)."},
                "nova_sessao": {
                    "type": "BOOLEAN",
                    "description": (
                        "Por padrão continua a conversa anterior naquele projeto (inclusive "
                        "a que o chefe tiver aberto no Mac). Use true quando a tarefa não "
                        "tiver relação com o que veio antes."
                    ),
                },
            },
            "required": ["projeto", "tarefa"],
        },
    },
    handler=_codar,
)

STATUS_TOOL = Tool(
    declaration={
        "name": "code_status",
        "description": "Mostra os trabalhos de código em andamento ou recentes.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "job_id": {"type": "STRING", "description": "Id do trabalho (vazio = lista todos)."}
            },
        },
    },
    handler=_status,
)
