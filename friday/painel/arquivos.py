"""Leitura e gravação do .env e do config.yaml sem estragar o que já existe.

O .env é editado linha a linha: comentários, ordem e chaves que o painel
não conhece ficam intactos. Toda gravação é atômica, guarda a versão
anterior e mantém a permissão 600 — ali moram as chaves de tudo.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import yaml

_LINHA = re.compile(r"^\s*([A-Z0-9_]+)\s*=(.*)$")


class ValorInvalido(ValueError):
    pass


def ler_env(arquivo: Path) -> dict[str, str]:
    valores = {}
    try:
        linhas = arquivo.read_text(encoding="utf-8").splitlines()
    except OSError:
        return valores
    for linha in linhas:
        m = _LINHA.match(linha)
        if m:
            valores[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return valores


def gravar_env(arquivo: Path, mudancas: dict[str, str]):
    """Atualiza só as chaves pedidas; as demais linhas ficam como estavam."""
    for chave, valor in mudancas.items():
        # quebra de linha num valor viraria uma variável nova injetada
        if any(c in valor for c in "\r\n\0"):
            raise ValorInvalido(f"{chave}: o valor não pode ter quebra de linha")
    try:
        linhas = arquivo.read_text(encoding="utf-8").splitlines()
    except OSError:
        linhas = []
    pendentes = dict(mudancas)
    saida = []
    for linha in linhas:
        m = _LINHA.match(linha)
        if m and m.group(1) in pendentes:
            saida.append(f"{m.group(1)}={pendentes.pop(m.group(1))}")
        else:
            saida.append(linha)
    saida += [f"{k}={v}" for k, v in pendentes.items()]
    _gravar(arquivo, "\n".join(saida) + "\n")


def ler_config(arquivo: Path) -> dict:
    try:
        return yaml.safe_load(arquivo.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def gravar_config(arquivo: Path, mudar):
    """`mudar(dados)` altera o dicionário; o resto do config é preservado."""
    dados = ler_config(arquivo)
    mudar(dados)
    texto = yaml.safe_dump(dados, allow_unicode=True, sort_keys=False, width=100)
    yaml.safe_load(texto)  # nunca grava um YAML que não volta a abrir
    _gravar(arquivo, texto)


def _gravar(arquivo: Path, texto: str):
    if arquivo.exists():
        shutil.copy2(arquivo, arquivo.with_name(arquivo.name + ".anterior"))
        arquivo.with_name(arquivo.name + ".anterior").chmod(0o600)
    tmp = arquivo.with_name(arquivo.name + ".tmp")
    tmp.write_text(texto, encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(arquivo)


def mascarar(valor: str) -> str:
    if not valor:
        return ""
    return "••••" + valor[-4:] if len(valor) > 8 else "••••"
