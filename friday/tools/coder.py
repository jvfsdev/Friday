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

import logging
import re
import shlex
import time

import asyncssh

from . import Tool, ToolContext, truncate

log = logging.getLogger("friday.coder")

TIMEOUT_AGENTE = 1800  # 30 min
PATH_MAC = 'export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH";'

# {tarefa} recebe a tarefa já entre aspas
AGENTES_PADRAO = {
    "claude": "claude -p {tarefa} --permission-mode acceptEdits",
    "antigravity": "agy -p {tarefa} --mode accept-edits",
}


async def _ssh(config, machine: str, comando: str, timeout: int = 120) -> tuple[int, str]:
    spec = config.machines.get(machine)
    if not spec:
        raise RuntimeError(f"máquina '{machine}' não configurada")
    async with asyncssh.connect(
        spec.host, username=spec.user, known_hosts=None, connect_timeout=15
    ) as conn:
        r = await conn.run(comando, check=False, timeout=timeout)
        return r.exit_status or 0, ((r.stdout or "") + (r.stderr or "")).strip()


def _slug(texto: str) -> str:
    limpo = re.sub(r"[^a-z0-9]+", "-", texto.lower())[:32].strip("-")
    return f"{limpo or 'tarefa'}-{time.strftime('%m%d-%H%M')}"


def _agentes(config) -> dict:
    return {**AGENTES_PADRAO, **(config.code_agents or {})}


def _ordem(config, agente: str) -> list[str]:
    disponiveis = _agentes(config)
    if agente and agente != "auto":
        return [agente] if agente in disponiveis else []
    preferencia = config.code_agent_order or list(AGENTES_PADRAO)
    return [a for a in preferencia if a in disponiveis]


async def _executar(config, projeto: str, spec: dict, tarefa: str, agente: str) -> str:
    machine, path = spec["machine"], spec["path"]
    cd = f"cd {shlex.quote(path)} &&"

    code, saida = await _ssh(config, machine, f"{cd} git rev-parse --is-inside-work-tree")
    if code != 0:
        return f"'{path}' no {machine} não é um repositório git ({saida[:200]})."

    code, sujo = await _ssh(config, machine, f"{cd} git status --porcelain")
    if sujo.strip():
        return ("O repositório tem alterações não commitadas — não vou misturar meu "
                f"trabalho com o seu. Resolva e peça de novo:\n{truncate(sujo, 800)}")

    branch = f"jarvis/{_slug(tarefa)}"
    code, saida = await _ssh(config, machine, f"{cd} git checkout -b {shlex.quote(branch)}")
    if code != 0:
        return f"Não consegui criar o branch {branch}: {saida[:300]}"

    base, _ = await _ssh(config, machine, f"{cd} git rev-parse HEAD")
    tentativas = []
    for nome in _ordem(config, agente):
        template = _agentes(config)[nome]
        comando = template.replace("{tarefa}", shlex.quote(tarefa))
        log.info("projeto %s: rodando agente %s", projeto, nome)
        code, saida = await _ssh(
            config, machine, f"{PATH_MAC} {cd} {comando}", timeout=TIMEOUT_AGENTE
        )
        tentativas.append(f"{nome}: código {code}")
        if code == 0:
            usado, resposta = nome, saida
            break
        log.warning("agente %s falhou (%s) — tentando o próximo", nome, saida[:200])
    else:
        await _ssh(config, machine, f"{cd} git checkout - && git branch -D {shlex.quote(branch)}")
        return f"Nenhum agente conseguiu executar ({'; '.join(tentativas) or 'nenhum configurado'})."

    await _ssh(config, machine, f"{cd} git add -A")
    mensagem = f"JARVIS: {tarefa[:120]}"
    await _ssh(config, machine, f"{cd} git commit -m {shlex.quote(mensagem)} --no-verify")

    _, diffstat = await _ssh(config, machine, f"{cd} git --no-pager diff --stat {base.strip()}..HEAD")
    if not diffstat.strip():
        await _ssh(config, machine, f"{cd} git checkout - && git branch -D {shlex.quote(branch)}")
        return f"O agente {usado} rodou mas não mudou nenhum arquivo. Resposta dele:\n{truncate(resposta, 1500)}"

    # deixa aberto na tela do Mac para revisão
    await _ssh(config, machine, f"{PATH_MAC} (code {shlex.quote(path)} || open {shlex.quote(path)}) >/dev/null 2>&1")

    return (
        f"Pronto, usei o {usado} no projeto {projeto}.\n"
        f"Branch: {branch} (commit local, sem push)\n\n"
        f"{truncate(diffstat, 1500)}\n\n"
        f"Abri o projeto no Mac para você revisar. Resumo do agente:\n{truncate(resposta, 1200)}"
    )


async def _codar(ctx: ToolContext, projeto: str, tarefa: str, agente: str = "auto") -> str:
    projetos = ctx.config.code_projects or {}
    spec = projetos.get(projeto)
    if not spec:
        conhecidos = ", ".join(projetos) or "nenhum"
        return f"Projeto '{projeto}' não está na lista permitida. Projetos: {conhecidos}."
    if not ctx.jobs:
        return "Registro de trabalhos indisponível."

    config = ctx.config
    job = ctx.jobs.submit(
        "codar", f"{projeto} — {tarefa[:80]}",
        lambda: _executar(config, projeto, spec, tarefa, agente),
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
            "Trabalha sempre num branch novo e abre o editor para revisão; nunca faz "
            "push. Demora minutos: avisa o chefe e NÃO fique esperando."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "projeto": {"type": "STRING", "description": "Nome do projeto na lista permitida."},
                "tarefa": {"type": "STRING", "description": "A tarefa, detalhada como você explicaria a um colega."},
                "agente": {"type": "STRING", "description": "claude, antigravity ou auto (padrão)."},
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
