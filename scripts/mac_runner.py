#!/usr/bin/env python3
"""Executor de tarefas de código no Mac, dentro da sessão do usuário.

Por que existir: o JARVIS mora no servidor e conecta por SSH, mas sessões
SSH no macOS não conseguem abrir o Keychain do login — e é lá que o Claude
Code guarda as credenciais. Rodando como LaunchAgent (sessão gráfica), este
executor tem acesso ao Keychain e também consegue abrir o editor na tela.

O servidor deposita um JSON em ~/.jarvis/jobs/pending/ e lê o resultado em
~/.jarvis/jobs/done/. Instalação: veja DEPLOY.md (seção do Mac).

O agente trabalha na branch que já estiver aberta e deixa as alterações
SOLTAS, sem commit: é assim que o chefe revisa — o painel de mudanças do
VS Code mostra arquivo por arquivo, e ele commita, ajusta ou descarta.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

BASE = Path.home() / ".jarvis" / "jobs"
PENDENTES, PRONTOS = BASE / "pending", BASE / "done"
ESTADOS = Path.home() / ".jarvis" / "estado"
TIMEOUT_AGENTE = 1800

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

AGENTES = {
    "claude": [
        "claude", "-p", "{tarefa}", "--permission-mode", "acceptEdits",
        "--allowedTools", *TESTES_PERMITIDOS,
    ],
    # O agy só oferece tudo-ou-nada em permissões, então fica só com edição.
    "antigravity": ["agy", "-p", "{tarefa}", "--mode", "accept-edits"],
}

# Vai junto da tarefa: sem isso o agente entrega sem validar o que escreveu.
PEDIDO_DE_TESTE = (
    "\n\n[Se este projeto tiver testes automatizados, rode-os antes de terminar "
    "e me diga o resultado. Se algum falhar por causa do que você mudou, corrija. "
    "Se não houver testes ou o runner não estiver instalado, apenas diga isso.]"
)

# Continuar a conversa anterior daquela pasta — inclusive uma que o chefe tenha
# aberto no Mac. É de propósito: ele pediu para o JARVIS entrar na sessão dele,
# assim o agente já chega sabendo o que estava sendo feito ali.
CONTINUAR = {"claude": "--continue", "antigravity": "--continue"}

# Sinais de "não existe conversa anterior aqui" — primeira tarefa no projeto.
SEM_CONVERSA = ("no conversation", "nenhuma conversa", "no previous", "not found",
                "no sessions", "nao encontrada")

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


def executar(tarefa_spec: dict) -> dict:
    env = _ambiente()
    caminho = Path(tarefa_spec["path"]).expanduser()
    tarefa = tarefa_spec["tarefa"]
    preferidos = tarefa_spec.get("agentes") or ["claude", "antigravity"]

    if not (caminho / ".git").exists():
        return {"ok": False, "resultado": f"{caminho} não é um repositório git."}

    _, branch = _git(caminho, "rev-parse", "--abbrev-ref", "HEAD", env=env)
    branch = branch.strip()

    sujos = _sujos(caminho, env)
    if sujos and not _deixei_eu(caminho, sujos):
        return {"ok": False, "resultado": (
            "A árvore tem alterações que não são minhas — não vou mexer por cima do "
            "trabalho do chefe. Commita ou guarda antes:\n"
            + "\n".join(sujos)[:800])}

    antes = _retrato(caminho, env)

    def rodar(nome: str, modelo: list, continuar: bool):
        comando = [p.replace("{tarefa}", tarefa + PEDIDO_DE_TESTE) for p in modelo]
        if continuar and CONTINUAR.get(nome):
            comando.insert(1, CONTINUAR[nome])
        return subprocess.run(
            comando, cwd=caminho, capture_output=True, text=True,
            timeout=TIMEOUT_AGENTE, env=env, stdin=subprocess.DEVNULL,
        )

    continuar = not tarefa_spec.get("novaSessao")
    tentativas, usado, resposta, sessao = [], None, "", "nova"
    for nome in preferidos:
        modelo = AGENTES.get(nome)
        if not modelo or not shutil.which(modelo[0], path=env["PATH"]):
            tentativas.append(f"{nome}: não instalado")
            continue
        try:
            r = rodar(nome, modelo, continuar)
            saida_agente = (r.stdout + r.stderr).strip()
            # Primeira tarefa no projeto: não há conversa para continuar.
            if continuar and r.returncode != 0 and any(
                s in saida_agente.lower() for s in SEM_CONVERSA
            ):
                r = rodar(nome, modelo, False)
                saida_agente = (r.stdout + r.stderr).strip()
                sessao = "nova (não havia conversa anterior)"
            elif continuar:
                sessao = "continuando a conversa anterior do projeto"
        except subprocess.TimeoutExpired:
            tentativas.append(f"{nome}: estourou {TIMEOUT_AGENTE}s")
            continue
        if r.returncode == 0:
            usado, resposta = nome, saida_agente
            break
        tentativas.append(f"{nome}: código {r.returncode} — {saida_agente[:200]}")

    depois = _retrato(caminho, env)
    _anotar_estado(caminho, depois[0])

    if not usado:
        # Não desfazemos nada: um agente que morreu no meio pode ter deixado
        # edição pela metade, e jogar fora trabalho por conta própria é pior
        # do que avisar.
        recado = "Nenhum agente executou. " + "; ".join(tentativas)
        if depois != antes:
            recado += "\n(Sobrou alteração pela metade na árvore — dá uma olhada antes.)"
        return {"ok": False, "resultado": recado}

    if depois == antes:
        return {"ok": False, "resultado": f"O {usado} rodou mas não mudou arquivo nenhum.\n{resposta[:1200]}"}

    _, diffstat = _git(caminho, "--no-pager", "diff", "--stat", env=env)

    # abre para revisão na tela (aqui dentro da sessão gráfica isso funciona)
    for abrir in (["code", str(caminho)], ["open", str(caminho)]):
        if shutil.which(abrir[0], path=env["PATH"]):
            subprocess.run(abrir, env=env, capture_output=True)
            break

    return {
        "ok": True,
        "agente": usado,
        "sessao": sessao,
        "branch": branch,
        "diffstat": diffstat,
        "desfazer": "git reset --hard && git clean -fd",
        "resultado": resposta[:2000],
    }


def main():
    for pasta in (PENDENTES, PRONTOS):
        pasta.mkdir(parents=True, exist_ok=True)
    print(f"executor do JARVIS no ar, vigiando {PENDENTES}", flush=True)
    while True:
        for arquivo in sorted(PENDENTES.glob("*.json")):
            try:
                spec = json.loads(arquivo.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                arquivo.unlink(missing_ok=True)
                continue
            arquivo.unlink(missing_ok=True)
            print(f"executando {arquivo.stem}: {spec.get('tarefa', '')[:60]}", flush=True)
            try:
                resultado = executar(spec)
            except Exception as exc:
                resultado = {"ok": False, "resultado": f"{type(exc).__name__}: {exc}"}
            (PRONTOS / arquivo.name).write_text(
                json.dumps(resultado, ensure_ascii=False), encoding="utf-8"
            )
            print(f"concluído {arquivo.stem}: ok={resultado.get('ok')}", flush=True)
        time.sleep(2)


if __name__ == "__main__":
    main()
