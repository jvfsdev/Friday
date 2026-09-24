"""Delegação de tarefas de código para o Mac.

O servidor é velho demais para rodar agentes de código (os binários exigem
instruções de CPU pós-2010), então o JARVIS conecta no Mac por SSH e entrega
a tarefa ao Claude Code de lá, via o executor scripts/mac_runner.py.

Trilhos de segurança, sempre:
  * só projetos declarados em `code_projects:` no config;
  * nada de mexer por cima de trabalho do chefe — se a árvore tem alteração
    que não é nossa, para;
  * trabalha na branch que já está aberta lá e deixa tudo SOLTO, sem commit:
    é assim que ele revisa, no painel de mudanças do VS Code;
  * NUNCA commita, faz push, merge ou deploy — a última palavra é do chefe;
  * o editor abre no Mac com o resultado para revisão.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shlex
import time
import uuid

import asyncssh

from ..jobs import TrabalhoFalhou
from . import Tool, ToolContext, truncate

log = logging.getLogger("friday.coder")

# O executor no Mac para o agente em 30 min. O servidor espera um pouco mais
# que isso a partir de quando o trabalho COMEÇA (não de quando entrou na
# fila): se desistisse antes, o resultado ficava órfão lá no Mac.
TIMEOUT_AGENTE = 1800
FOLGA = 5 * 60
ESPERA_NA_FILA = 2 * 3600       # outros trabalhos na frente, no máximo isso
EXECUTOR_PARADO = 2 * 60        # na fila sem nada rodando = executor fora do ar
INTERVALO = 10
# Rede oscila (vide o DNS do servidor): perder o Mac por um tempo não pode
# virar "falhou" enquanto o agente segue trabalhando lá.
SEM_CONTATO_MAX = 5 * 60

FILA = "~/.jarvis/jobs"


class _Mac:
    """Uma conexão SSH reaproveitada durante o trabalho inteiro.

    Antes era uma conexão nova a cada consulta — centenas num trabalho longo,
    e qualquer uma que falhasse derrubava o acompanhamento.
    """

    def __init__(self, config, machine: str):
        spec = config.machines.get(machine)
        if not spec:
            raise TrabalhoFalhou(f"máquina '{machine}' não configurada")
        self.spec = spec
        self.nome = machine
        self.conn = None

    async def run(self, comando: str, entrada: str | None = None, timeout: int = 60) -> tuple[int, str]:
        for tentativa in (1, 2):
            try:
                if self.conn is None:
                    self.conn = await asyncssh.connect(
                        self.spec.host, username=self.spec.user,
                        known_hosts=None, connect_timeout=15,
                    )
                r = await self.conn.run(comando, check=False, timeout=timeout, input=entrada)
                return r.exit_status or 0, ((r.stdout or "") + (r.stderr or "")).strip()
            except (OSError, asyncssh.Error, asyncio.TimeoutError):
                self.fechar()
                if tentativa == 2:
                    raise
        raise AssertionError("inalcançável")

    def fechar(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None


def _consulta(mac_id: str) -> str:
    """Um comando só, que diz em que pé o trabalho está no Mac."""
    return (
        f"cd {FILA} && "
        f"if [ -s done/{mac_id}.json ]; then echo PRONTO; cat done/{mac_id}.json; "
        f"elif [ -f pending/{mac_id}.json ]; then echo NA_FILA; ls running | wc -l; "
        f"else echo RODANDO; cat running/{mac_id}.json 2>/dev/null; fi"
    )


async def _executar(config, projeto: str, spec: dict, tarefa: str,
                    nova_sessao: bool = False, mac_id: str | None = None) -> str:
    """Deposita a tarefa para o executor que roda na sessão gráfica do Mac.

    Não chamamos o agente direto por SSH porque o macOS não abre o Keychain
    do login em sessão não-interativa — e é lá que ficam as credenciais do
    Claude Code. O executor (LaunchAgent) tem esse acesso e ainda consegue
    abrir o editor na tela.

    Falha levanta exceção: o registro de trabalhos marca "falhou" e avisa
    com ⚠️, em vez de um ✅ em cima de "não deu certo".
    """
    mac = _Mac(config, spec["machine"])
    mac_id = mac_id or uuid.uuid4().hex[:10]
    payload = json.dumps(
        {"path": spec["path"], "tarefa": tarefa, "novaSessao": bool(nova_sessao)},
        ensure_ascii=False,
    )
    try:
        code, saida = await mac.run(
            f"mkdir -p {FILA}/pending {FILA}/done && cat > {FILA}/pending/{mac_id}.json",
            entrada=payload,
        )
        if code != 0:
            raise TrabalhoFalhou(f"não consegui enviar a tarefa para o {mac.nome}: {saida[:300]}")
        resultado = await _acompanhar(mac, mac_id)
        await mac.run(f"rm -f {FILA}/done/{mac_id}.json")
    finally:
        mac.fechar()

    if not resultado.get("ok"):
        raise TrabalhoFalhou(resultado.get("resultado") or "sem detalhes do Mac")
    return _relatorio(projeto, resultado)


async def _acompanhar(mac: _Mac, mac_id: str) -> dict:
    entrou = time.monotonic()
    comecou = None
    sem_contato_desde = None
    while True:
        await asyncio.sleep(INTERVALO)
        agora = time.monotonic()
        try:
            _, saida = await mac.run(_consulta(mac_id))
            sem_contato_desde = None
        except Exception as exc:
            sem_contato_desde = sem_contato_desde or agora
            log.warning("sem contato com o %s (%s) — seguindo", mac.nome, exc)
            if agora - sem_contato_desde > SEM_CONTATO_MAX:
                raise TrabalhoFalhou(
                    f"perdi o contato com o {mac.nome} há mais de {SEM_CONTATO_MAX // 60} min. "
                    "O agente pode ter terminado lá mesmo assim — o resultado fica em "
                    f"~/.jarvis/jobs/done/{mac_id}.json"
                ) from exc
            continue

        estado, _, resto = saida.partition("\n")
        if estado == "PRONTO":
            try:
                return json.loads(resto)
            except json.JSONDecodeError:
                raise TrabalhoFalhou(f"resposta ilegível do executor: {resto[:300]}")
        if estado == "NA_FILA":
            rodando = resto.strip() not in ("", "0")
            if not rodando and agora - entrou > EXECUTOR_PARADO:
                raise TrabalhoFalhou(
                    f"a tarefa está parada na fila do {mac.nome} sem nada rodando na frente — "
                    "o executor do JARVIS está no ar? (`launchctl list | grep jarvis` no Mac)"
                )
            if agora - entrou > ESPERA_NA_FILA:
                raise TrabalhoFalhou(f"a tarefa ficou {ESPERA_NA_FILA // 3600}h na fila do {mac.nome}")
            continue
        comecou = comecou or agora
        if agora - comecou > TIMEOUT_AGENTE + FOLGA:
            raise TrabalhoFalhou(
                f"o {mac.nome} não devolveu o resultado em {(TIMEOUT_AGENTE + FOLGA) // 60} min"
            )


def _minutos(segundos) -> str:
    segundos = int(segundos or 0)
    return f"{segundos // 60} min {segundos % 60:02d} s" if segundos >= 60 else f"{segundos} s"


def _relatorio(projeto: str, r: dict) -> str:
    ritmo = f"{r.get('acoes', 0)} ações em {_minutos(r.get('duracao_s'))}"
    sessao = r.get("sessao")
    retomar = f"\nPara seguir essa conversa no terminal: `claude --resume {sessao}`" if sessao else ""
    resposta = truncate(r.get("resultado", ""), 2500)

    if not r.get("mudou"):
        # Análise/pergunta: a resposta é o entregável.
        return f"{projeto} — sem alterar arquivos ({ritmo}).\n\n{resposta}{retomar}"
    return (
        f"{projeto} — branch {r.get('branch')}, alterações soltas, sem commit ({ritmo}).\n\n"
        f"{truncate(r.get('diffstat', ''), 1500)}\n\n"
        f"{resposta}\n\n"
        f"Abri o projeto no Mac: está tudo no painel de mudanças do editor. "
        f"Para jogar fora: `{r.get('desfazer', 'git reset --hard && git clean -fd')}`"
        f"{retomar}"
    )


async def _codar(ctx: ToolContext, projeto: str, tarefa: str, nova_sessao: bool = False) -> str:
    projetos = ctx.config.code_projects or {}
    spec = projetos.get(projeto)
    if not spec:
        conhecidos = ", ".join(projetos) or "nenhum"
        return f"Projeto '{projeto}' não está na lista permitida. Projetos: {conhecidos}."
    if not ctx.jobs:
        return "Registro de trabalhos indisponível."

    config = ctx.config
    mac_id = uuid.uuid4().hex[:10]
    job = ctx.jobs.submit(
        "codar", f"{projeto} — {tarefa[:80]}",
        lambda: _executar(config, projeto, spec, tarefa, nova_sessao, mac_id),
    )
    job.meta.update(machine=spec["machine"], mac_id=mac_id)
    return (
        f"Trabalho [{job.id}] iniciado no projeto {projeto}. Pode levar alguns minutos; "
        "eu aviso quando terminar. Não repita a tarefa."
    )


async def _progresso(config, job) -> str:
    """Última ação do agente, lida ao vivo no Mac."""
    try:
        mac = _Mac(config, job.meta["machine"])
        try:
            _, saida = await mac.run(_consulta(job.meta["mac_id"]), timeout=20)
        finally:
            mac.fechar()
    except Exception as exc:
        return f"(não consegui consultar o Mac agora: {type(exc).__name__})"
    estado, _, resto = saida.partition("\n")
    if estado == "NA_FILA":
        return "na fila do Mac, esperando outro trabalho terminar"
    if estado == "PRONTO":
        return "terminou no Mac; o resultado chega em instantes"
    try:
        p = json.loads(resto)
    except json.JSONDecodeError:
        return "começando no Mac"
    return f"{p.get('acoes', 0)} ações até agora; agora: {p.get('ultima', '?')}"


def _codar_rodando(ctx: ToolContext, job_id: str):
    job = ctx.jobs.get(job_id) if ctx.jobs else None
    if not job or job.kind != "codar" or not job.meta.get("mac_id"):
        return None
    return job


async def _status(ctx: ToolContext, job_id: str = "") -> str:
    if not ctx.jobs:
        return "Registro de trabalhos indisponível."
    if job_id:
        job = ctx.jobs.get(job_id)
        if not job:
            return f"Não achei o trabalho [{job_id}]."
        if job.status == "rodando" and _codar_rodando(ctx, job_id):
            return f"{job.resumo()}\n{await _progresso(ctx.config, job)}"
        return f"{job.resumo()}\n\n{job.result or '(ainda rodando)'}"

    linhas = [ctx.jobs.listar()]
    for job in ctx.jobs.jobs.values():
        if job.status == "rodando" and _codar_rodando(ctx, job.id):
            linhas.append(f"[{job.id}] {await _progresso(ctx.config, job)}")
    return "\n".join(linhas)


async def _cancelar(ctx: ToolContext, job_id: str) -> str:
    job = _codar_rodando(ctx, job_id)
    if not job:
        return f"Não há trabalho de código [{job_id}]."
    if job.status != "rodando":
        return f"O trabalho [{job_id}] já terminou ({job.status})."
    mac_id = shlex.quote(job.meta["mac_id"])
    # Ainda na fila: tira de lá e deixa a resposta pronta, para o
    # acompanhamento encerrar sozinho. Já rodando: pede ao executor para parar.
    cancelado = json.dumps({"ok": False, "resultado": "Cancelado antes de começar."})
    comando = (
        f"cd {FILA} && mkdir -p cancel && "
        f"if rm pending/{mac_id}.json 2>/dev/null; then "
        f"printf %s {shlex.quote(cancelado)} > done/{mac_id}.json; echo FILA; "
        f"else touch cancel/{mac_id}; echo RODANDO; fi"
    )
    mac = _Mac(ctx.config, job.meta["machine"])
    try:
        _, saida = await mac.run(comando, timeout=20)
    finally:
        mac.fechar()
    if saida.startswith("FILA"):
        return f"Tirei o trabalho [{job_id}] da fila; ele nem chegou a começar."
    return (f"Pedi para o agente parar o trabalho [{job_id}]. O que ele já tiver "
            "alterado fica na árvore para o chefe decidir — não desfaço nada sozinho.")


CODAR_TOOL = Tool(
    declaration={
        "name": "codar",
        "description": (
            "Delega ao Claude Code, no Mac do chefe, uma tarefa num projeto: mudar "
            "código OU estudar/analisar/responder sobre ele (aí não altera nada e "
            "volta a resposta). Continua a conversa anterior do projeto por padrão, "
            "então dá para pedir ajustes em cima do que foi feito. Trabalha na branch "
            "que já está aberta e deixa as alterações sem commit para o chefe revisar; "
            "nunca commita nem faz push. Demora minutos: avisa o chefe e NÃO fique "
            "esperando."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "projeto": {"type": "STRING", "description": "Nome do projeto na lista permitida."},
                "tarefa": {"type": "STRING", "description": "A tarefa, detalhada como você explicaria a um colega."},
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
        "description": (
            "Mostra os trabalhos de código em andamento ou recentes. Para um trabalho "
            "rodando, diz ao vivo o que o agente está fazendo agora no Mac."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "job_id": {"type": "STRING", "description": "Id do trabalho (vazio = lista todos)."}
            },
        },
    },
    handler=_status,
)

CANCEL_TOOL = Tool(
    declaration={
        "name": "code_cancel",
        "description": (
            "Para um trabalho de código em andamento (ou tira da fila). O que já "
            "tiver sido alterado fica na árvore; nada é desfeito."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "job_id": {"type": "STRING", "description": "Id do trabalho, como em code_status."}
            },
            "required": ["job_id"],
        },
    },
    handler=_cancelar,
)
