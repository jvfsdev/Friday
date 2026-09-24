#!/usr/bin/env python3
"""Executor de tarefas de código no Mac, dentro da sessão do usuário.

Por que existir: o JARVIS mora no servidor e conecta por SSH, mas sessões
SSH no macOS não conseguem abrir o Keychain do login — e é lá que o Claude
Code guarda as credenciais. Rodando como LaunchAgent (sessão gráfica), este
executor tem acesso ao Keychain e também consegue abrir o editor na tela.

Protocolo, tudo em ~/.jarvis/jobs/ (o servidor fala por SSH):
  pending/<id>.json   o servidor deposita a tarefa
  running/<id>.json   progresso ao vivo (última ação, nº de ações, sessão)
  cancel/<id>         o servidor pede para parar
  done/<id>.json      o resultado, que o servidor lê e apaga

O agente trabalha na branch que já estiver aberta e deixa as alterações
SOLTAS, sem commit: é assim que o chefe revisa — o painel de mudanças do
VS Code mostra arquivo por arquivo, e ele commita, ajusta ou descarta.
Instalação: veja DEPLOY.md (seção do Mac).
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

BASE = Path.home() / ".jarvis" / "jobs"
PENDENTES, PRONTOS = BASE / "pending", BASE / "done"
RODANDO, CANCELAR = BASE / "running", BASE / "cancel"
ESTADOS = Path.home() / ".jarvis" / "estado"
TIMEOUT_AGENTE = 1800
# Resultado que o servidor nunca buscou (ele caiu, a rede caiu) não pode se
# acumular para sempre.
RETENCAO_PRONTOS = 2 * 24 * 3600

# Comandos que o agente pode rodar sozinho. Lista fechada de propósito:
# rodar a suíte antes de entregar melhora muito o que chega para revisão, mas
# execução arbitrária no Mac do chefe é outra conversa — e o gatilho pode vir
# de um email de terceiro (pipeline de reclamação).
TESTES_PERMITIDOS = [
    "Bash(pytest*)", "Bash(python -m pytest*)", "Bash(python3 -m pytest*)",
    "Bash(python -m unittest*)", "Bash(python3 -m unittest*)",
    "Bash(npm test*)", "Bash(npm run test*)", "Bash(yarn test*)", "Bash(pnpm test*)",
    "Bash(npx vitest*)", "Bash(npx jest*)", "Bash(go test*)", "Bash(cargo test*)",
    "Bash(make test*)", "Bash(mvn test*)", "Bash(./gradlew test*)",
]
# Só leitura: metade dos pedidos reais é "estuda o projeto" / "roda um git
# status", e sem isso o agente respondia às cegas. Nada que mude branch,
# índice ou histórico (git branch fica de fora: aceita -D).
GIT_LEITURA = [
    "Bash(git status*)", "Bash(git diff*)", "Bash(git log*)", "Bash(git show*)",
    "Bash(ls*)",
]

# Regras da casa, no prompt de sistema e não colado na tarefa: com --continue
# o agente entra na conversa que o chefe tiver aberto no Mac, e o texto da
# tarefa fica no histórico dele — melhor que fique só o pedido.
INSTRUCOES = """\
Esta tarefa chega pelo JARVIS, o assistente pessoal do dono deste Mac, que a
repassou do Telegram. Ele não está olhando o terminal agora.
- Pode ser uma pergunta ou análise: aí apenas responda, sem alterar arquivos.
- Se alterar código e o projeto tiver testes automatizados, rode-os antes de
  terminar; se algum falhar pelo que você mudou, corrija. Sem testes (ou sem
  o runner instalado), apenas diga isso.
- Nunca faça commit, push, stash nem troque de branch: as alterações ficam
  soltas na árvore para o dono revisar no editor.
- Sua resposta final vai para o celular dele: em português, direta, no
  máximo umas 15 linhas. Diga o que mudou e por quê, e o resultado dos testes.
"""

PATH_EXTRA = [str(Path.home() / ".local/bin"), "/opt/homebrew/bin", "/usr/local/bin"]


def _ambiente():
    env = dict(os.environ)
    env["PATH"] = ":".join(PATH_EXTRA + [env.get("PATH", "")])
    return env


def _git(path: Path, *args, env=None):
    r = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, env=env or _ambiente())
    return r.returncode, (r.stdout + r.stderr).strip()


def _arquivo_estado(caminho: Path) -> Path:
    return ESTADOS / (hashlib.sha1(str(caminho).encode()).hexdigest()[:12] + ".json")


def _sujos(caminho: Path, env=None) -> list:
    """Arquivos que diferem do último commit: mexidos, novos ou apagados.

    De propósito por `--name-only` em vez de `status --porcelain`: o porcelain
    codifica o estado nas duas primeiras colunas, e um caminho por linha limpa
    não tem prefixo para errar ao fatiar.
    """
    code, mexidos = _git(caminho, "diff", "--name-only", "HEAD", env=env)
    if code != 0:                 # repositório ainda sem commit nenhum
        mexidos = ""
    _, novos = _git(caminho, "ls-files", "--others", "--exclude-standard", env=env)
    return sorted({l.strip() for l in (mexidos + "\n" + novos).splitlines() if l.strip()})


def _impressoes(raiz: Path, sujos: list) -> dict:
    """Assinatura do conteúdo de cada arquivo sujo — a prova de autoria."""
    marcas = {}
    for nome in sujos:
        try:
            marcas[nome] = hashlib.sha256((raiz / nome).read_bytes()).hexdigest()
        except OSError:
            marcas[nome] = "(apagado)"
    return marcas


def _deixei_eu(caminho: Path, sujos: list) -> bool:
    """A sujeira na árvore é do trabalho anterior do JARVIS, ou do chefe?

    Sem commit, a árvore fica suja depois de cada tarefa — e recusar por isso
    mataria o "agora ajusta aquilo" logo em seguida. Então guardamos o
    conteúdo exato do que deixamos: se cada arquivo sujo ainda está byte a
    byte como saiu da nossa mão, é nosso e dá para continuar por cima. Basta
    o chefe ter encostado num deles para pararmos.

    É conteúdo, e não commit: assim ele pode aprovar metade do trabalho e
    pedir ajuste no resto sem que a gente se recuse a mexer.
    """
    try:
        estado = json.loads(_arquivo_estado(caminho).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    deixado = estado.get("arquivos")
    if not isinstance(deixado, dict):
        return False
    return all(deixado.get(nome) == marca
               for nome, marca in _impressoes(caminho, sujos).items())


def _anotar_estado(caminho: Path, sujos: list):
    ESTADOS.mkdir(parents=True, exist_ok=True)
    _arquivo_estado(caminho).write_text(
        json.dumps({"arquivos": _impressoes(caminho, sujos)}), encoding="utf-8"
    )


def _retrato(caminho: Path, env) -> tuple:
    """Como a árvore está agora: (arquivos sujos, diff).

    A lista diz QUAIS arquivos mudaram; o diff diz O QUE mudou — sem ele, um
    agente que só reescreve um arquivo já sujo passaria por "não fez nada".
    O `add -N` faz o arquivo novo aparecer no diff e no painel de mudanças do
    VS Code, em vez de ficar escondido como "não rastreado".
    """
    _git(caminho, "add", "-N", ".", env=env)
    _, diff = _git(caminho, "--no-pager", "diff", env=env)
    return _sujos(caminho, env), diff


def _descrever(ferramenta: str, entrada: dict, raiz: Path) -> str:
    """Uma linha legível do que o agente está fazendo, para o code_status."""
    def rel(caminho: str) -> str:
        try:
            return str(Path(caminho).resolve().relative_to(raiz.resolve()))
        except (ValueError, OSError):
            return caminho

    if ferramenta in ("Edit", "MultiEdit", "Write", "NotebookEdit"):
        return f"editando {rel(entrada.get('file_path') or entrada.get('notebook_path', '?'))}"
    if ferramenta == "Read":
        return f"lendo {rel(entrada.get('file_path', '?'))}"
    if ferramenta == "Bash":
        return f"rodando `{(entrada.get('command') or '')[:80]}`"
    if ferramenta in ("Grep", "Glob"):
        return f"procurando {entrada.get('pattern', '')[:60]}"
    return ferramenta


def _rodar_claude(job_id: str, caminho: Path, tarefa: str, continuar: bool, env) -> dict:
    """Roda o Claude Code acompanhando o fluxo de eventos.

    O stream-json dá três coisas que o texto puro não dava: progresso ao vivo
    (para o chefe perguntar "como está?"), um veredito de erro confiável, e o
    id da sessão — que ele pode abrir no terminal para continuar dali.
    """
    comando = [
        "claude", "-p", tarefa,
        "--output-format", "stream-json", "--verbose",
        "--permission-mode", "acceptEdits",
        "--append-system-prompt", INSTRUCOES,
        "--allowedTools", *TESTES_PERMITIDOS, *GIT_LEITURA,
    ]
    if continuar:
        # Entra na conversa mais recente da pasta — inclusive a que o chefe
        # tiver aberto no Mac: foi pedido dele, o agente chega sabendo o
        # contexto. Sem conversa anterior, o Claude Code abre uma nova.
        comando.insert(1, "--continue")

    proc = subprocess.Popen(
        comando, cwd=caminho, env=env, text=True, bufsize=1,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=True,   # para poder matar o grupo inteiro no cancelamento
    )
    linhas: queue.Queue = queue.Queue()

    def ler():
        for linha in proc.stdout:
            linhas.put(linha)
        linhas.put(None)

    threading.Thread(target=ler, daemon=True).start()

    progresso = {"inicio": time.time(), "acoes": 0, "ultima": "começando", "sessao": ""}
    arquivo_progresso = RODANDO / f"{job_id}.json"
    pedido_cancelar = CANCELAR / job_id
    final, crus, motivo_parada = None, deque(maxlen=8), ""
    gravado_em, pendente = 0.0, False

    def gravar(forcar=False):
        # Limita a uma escrita por segundo, mas nunca perde a última: o que
        # ficou pendente sai na próxima volta do laço, chegue evento ou não —
        # senão "rodando pytest" nunca aparecia durante os 5 min da suíte.
        nonlocal gravado_em, pendente
        pendente = True
        if forcar or time.time() - gravado_em >= 1:
            arquivo_progresso.write_text(json.dumps(progresso, ensure_ascii=False), encoding="utf-8")
            gravado_em, pendente = time.time(), False

    gravar(forcar=True)
    while True:
        if pedido_cancelar.exists():
            motivo_parada = "cancelado"
        elif time.time() - progresso["inicio"] > TIMEOUT_AGENTE:
            motivo_parada = "tempo"
        if motivo_parada:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            break
        if pendente:
            gravar()
        try:
            linha = linhas.get(timeout=1)
        except queue.Empty:
            continue
        if linha is None:
            break
        try:
            evento = json.loads(linha)
        except json.JSONDecodeError:
            if linha.strip():
                crus.append(linha.strip())
            continue
        tipo = evento.get("type")
        if tipo == "system" and evento.get("subtype") == "init":
            progresso["sessao"] = evento.get("session_id", "")
            gravar(forcar=True)
        elif tipo == "assistant":
            for bloco in (evento.get("message") or {}).get("content") or []:
                if bloco.get("type") == "tool_use":
                    progresso["acoes"] += 1
                    progresso["ultima"] = _descrever(bloco.get("name", "?"), bloco.get("input") or {}, caminho)
                    gravar()
        elif tipo == "result":
            final = evento

    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
    arquivo_progresso.unlink(missing_ok=True)
    pedido_cancelar.unlink(missing_ok=True)

    return {
        "final": final, "motivo_parada": motivo_parada, "codigo": proc.returncode,
        "crus": list(crus), "sessao": progresso["sessao"], "acoes": progresso["acoes"],
        "duracao_s": int(time.time() - progresso["inicio"]),
    }


def executar(tarefa_spec: dict, job_id: str = "manual") -> dict:
    env = _ambiente()
    caminho = Path(tarefa_spec["path"]).expanduser()
    tarefa = tarefa_spec["tarefa"]

    if not (caminho / ".git").exists():
        return {"ok": False, "resultado": f"{caminho} não é um repositório git."}
    if not shutil.which("claude", path=env["PATH"]):
        return {"ok": False, "resultado": "O Claude Code não está instalado (ou fora do PATH) no Mac."}

    _, branch = _git(caminho, "rev-parse", "--abbrev-ref", "HEAD", env=env)
    branch = branch.strip()

    sujos = _sujos(caminho, env)
    if sujos and not _deixei_eu(caminho, sujos):
        return {"ok": False, "resultado": (
            "A árvore tem alterações que não são minhas — não vou mexer por cima do "
            "trabalho do chefe. Commita ou guarda antes:\n"
            + "\n".join(sujos)[:800])}

    antes = _retrato(caminho, env)
    r = _rodar_claude(job_id, caminho, tarefa, not tarefa_spec.get("novaSessao"), env)
    depois = _retrato(caminho, env)
    _anotar_estado(caminho, depois[0])
    mudou = depois != antes

    final = r["final"] or {}
    base = {
        "branch": branch, "sessao": r["sessao"], "acoes": r["acoes"],
        "duracao_s": r["duracao_s"], "mudou": mudou,
        "desfazer": "git reset --hard && git clean -fd",
    }
    # Nunca desfazemos nada sozinhos: um agente parado no meio pode ter
    # deixado edição pela metade, e jogar trabalho fora é pior que avisar.
    meio = "\n(Ficou alteração pela metade na árvore — dá uma olhada antes.)" if mudou else ""
    if r["motivo_parada"] == "cancelado":
        return {**base, "ok": False, "resultado": "Parei a pedido do chefe." + meio}
    if r["motivo_parada"] == "tempo":
        return {**base, "ok": False,
                "resultado": f"O agente passou de {TIMEOUT_AGENTE // 60} minutos e eu parei." + meio}
    if not final or final.get("is_error") or r["codigo"] not in (0, None):
        detalhe = final.get("result") or "\n".join(r["crus"]) or f"código {r['codigo']}"
        return {**base, "ok": False, "resultado": f"O Claude Code falhou: {detalhe[:800]}" + meio}

    diffstat = ""
    if mudou:
        _, diffstat = _git(caminho, "--no-pager", "diff", "--stat", env=env)
        # abre para revisão na tela (aqui dentro da sessão gráfica isso funciona)
        for abrir in (["code", str(caminho)], ["open", str(caminho)]):
            if shutil.which(abrir[0], path=env["PATH"]):
                subprocess.run(abrir, env=env, capture_output=True)
                break

    # Não mudar nada é resultado legítimo: "estuda o projeto" e "o que esse
    # módulo faz?" são pedidos de verdade, e a resposta é o próprio entregável.
    return {**base, "ok": True, "diffstat": diffstat,
            "turnos": final.get("num_turns"), "resultado": (final.get("result") or "")[:3000]}


def _faxina():
    agora = time.time()
    for velho in PRONTOS.glob("*.json"):
        if agora - velho.stat().st_mtime > RETENCAO_PRONTOS:
            velho.unlink(missing_ok=True)
    # Nada roda no boot do executor: progresso e cancelamentos que sobraram
    # são de uma execução que morreu junto com ele.
    for sobra in list(RODANDO.glob("*.json")) + list(CANCELAR.glob("*")):
        sobra.unlink(missing_ok=True)


def main():
    for pasta in (PENDENTES, PRONTOS, RODANDO, CANCELAR):
        pasta.mkdir(parents=True, exist_ok=True)
    _faxina()
    print(f"executor do JARVIS no ar, vigiando {PENDENTES}", flush=True)
    while True:
        for arquivo in sorted(PENDENTES.glob("*.json"), key=lambda a: a.stat().st_mtime):
            try:
                spec = json.loads(arquivo.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                arquivo.unlink(missing_ok=True)
                continue
            arquivo.unlink(missing_ok=True)
            print(f"{time.strftime('%F %T')} executando {arquivo.stem}: "
                  f"{spec.get('tarefa', '')[:60]}", flush=True)
            try:
                resultado = executar(spec, arquivo.stem)
            except Exception as exc:
                resultado = {"ok": False, "resultado": f"{type(exc).__name__}: {exc}"}
            (PRONTOS / arquivo.name).write_text(
                json.dumps(resultado, ensure_ascii=False), encoding="utf-8"
            )
            print(f"{time.strftime('%F %T')} concluído {arquivo.stem}: ok={resultado.get('ok')}", flush=True)
        time.sleep(2)


if __name__ == "__main__":
    main()
