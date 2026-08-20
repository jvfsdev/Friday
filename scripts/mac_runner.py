#!/usr/bin/env python3
"""Executor de tarefas de código no Mac, dentro da sessão do usuário.

Por que existir: o JARVIS mora no servidor e conecta por SSH, mas sessões
SSH no macOS não conseguem abrir o Keychain do login — e é lá que o Claude
Code guarda as credenciais. Rodando como LaunchAgent (sessão gráfica), este
executor tem acesso ao Keychain e também consegue abrir o editor na tela.

O servidor deposita um JSON em ~/.jarvis/jobs/pending/ e lê o resultado em
~/.jarvis/jobs/done/. Instalação: veja DEPLOY.md (seção do Mac).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

BASE = Path.home() / ".jarvis" / "jobs"
PENDENTES, PRONTOS = BASE / "pending", BASE / "done"
TIMEOUT_AGENTE = 1800

AGENTES = {
    "claude": ["claude", "-p", "{tarefa}", "--permission-mode", "acceptEdits"],
    "antigravity": ["agy", "-p", "{tarefa}", "--mode", "accept-edits"],
}

PATH_EXTRA = [str(Path.home() / ".local/bin"), "/opt/homebrew/bin", "/usr/local/bin"]


def _ambiente():
    env = dict(os.environ)
    env["PATH"] = ":".join(PATH_EXTRA + [env.get("PATH", "")])
    return env


def _git(path: Path, *args, env=None):
    r = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, env=env or _ambiente())
    return r.returncode, (r.stdout + r.stderr).strip()


def _slug(texto: str) -> str:
    limpo = re.sub(r"[^a-z0-9]+", "-", texto.lower())[:32].strip("-")
    return f"{limpo or 'tarefa'}-{time.strftime('%m%d-%H%M')}"


def executar(tarefa_spec: dict) -> dict:
    env = _ambiente()
    caminho = Path(tarefa_spec["path"]).expanduser()
    tarefa = tarefa_spec["tarefa"]
    preferidos = tarefa_spec.get("agentes") or ["claude", "antigravity"]

    if not (caminho / ".git").exists():
        return {"ok": False, "resultado": f"{caminho} não é um repositório git."}

    code, sujo = _git(caminho, "status", "--porcelain", env=env)
    if sujo.strip():
        return {"ok": False, "resultado": f"Repositório com alterações não commitadas:\n{sujo[:800]}"}

    branch = f"jarvis/{_slug(tarefa)}"
    code, saida = _git(caminho, "checkout", "-b", branch, env=env)
    if code != 0:
        return {"ok": False, "resultado": f"Falha ao criar o branch {branch}: {saida[:300]}"}
    _, base = _git(caminho, "rev-parse", "HEAD", env=env)

    tentativas, usado, resposta = [], None, ""
    for nome in preferidos:
        modelo = AGENTES.get(nome)
        if not modelo or not shutil.which(modelo[0], path=env["PATH"]):
            tentativas.append(f"{nome}: não instalado")
            continue
        comando = [p.replace("{tarefa}", tarefa) for p in modelo]
        try:
            r = subprocess.run(
                comando, cwd=caminho, capture_output=True, text=True,
                timeout=TIMEOUT_AGENTE, env=env, stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            tentativas.append(f"{nome}: estourou {TIMEOUT_AGENTE}s")
            continue
        saida_agente = (r.stdout + r.stderr).strip()
        if r.returncode == 0:
            usado, resposta = nome, saida_agente
            break
        tentativas.append(f"{nome}: código {r.returncode} — {saida_agente[:200]}")

    if not usado:
        _git(caminho, "checkout", "-", env=env)
        _git(caminho, "branch", "-D", branch, env=env)
        return {"ok": False, "resultado": "Nenhum agente executou. " + "; ".join(tentativas)}

    _git(caminho, "add", "-A", env=env)
    _git(caminho, "commit", "-m", f"JARVIS: {tarefa[:120]}", "--no-verify", env=env)
    _, diffstat = _git(caminho, "--no-pager", "diff", "--stat", f"{base.strip()}..HEAD", env=env)

    if not diffstat.strip():
        _git(caminho, "checkout", "-", env=env)
        _git(caminho, "branch", "-D", branch, env=env)
        return {"ok": False, "resultado": f"O {usado} rodou mas não mudou arquivo nenhum.\n{resposta[:1200]}"}

    # abre para revisão na tela (aqui dentro da sessão gráfica isso funciona)
    for abrir in (["code", str(caminho)], ["open", str(caminho)]):
        if shutil.which(abrir[0], path=env["PATH"]):
            subprocess.run(abrir, env=env, capture_output=True)
            break

    return {
        "ok": True,
        "agente": usado,
        "branch": branch,
        "diffstat": diffstat,
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
