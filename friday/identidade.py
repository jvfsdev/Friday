"""Quais endereços de email são do próprio chefe.

Serve para não pedir confirmação quando ele manda algo para si mesmo — que é
o caso de "me manda isso por email". Os endereços são descobertos nas contas
já conectadas, não numa lista que alguém teria de manter à mão; extras
manuais entram por MEUS_EMAILS no .env.
"""

from __future__ import annotations

import logging
import os
import re

log = logging.getLogger("friday.identidade")

_cache: set[str] | None = None

ENDERECO = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def extrair(destinatario: str) -> str:
    """'João <joao@x.com>' -> 'joao@x.com' (minúsculo)."""
    achado = ENDERECO.search(destinatario or "")
    return achado.group(0).lower() if achado else ""


def _descobrir() -> set[str]:
    enderecos: set[str] = set()

    from .tools.google_workspace import _service, accounts

    for conta in accounts():
        try:
            perfil = _service("gmail", "v1", conta).users().getProfile(userId="me").execute()
            if perfil.get("emailAddress"):
                enderecos.add(perfil["emailAddress"].lower())
        except Exception:
            log.warning("não consegui descobrir o email da conta Google '%s'", conta)

    # Microsoft: o endereço já está no cache do token (campo username do MSAL).
    # Perguntar ao Graph /me exigiria o escopo User.Read, que não pedimos — e
    # não vale forçar o chefe a reautorizar só para saber o email dele.
    import json

    from .tools import outlook

    for conta in outlook.accounts():
        try:
            cache = json.loads(outlook.cache_file(conta).read_text(encoding="utf-8"))
            for dados in (cache.get("Account") or {}).values():
                if dados.get("username"):
                    enderecos.add(str(dados["username"]).lower())
        except Exception:
            log.warning("não consegui descobrir o email da conta Microsoft '%s'", conta)

    extras = os.getenv("MEUS_EMAILS", "")
    enderecos.update(e.strip().lower() for e in extras.split(",") if e.strip())
    return enderecos


def meus_enderecos(recarregar: bool = False) -> set[str]:
    global _cache
    if _cache is None or recarregar:
        _cache = _descobrir()
        log.info("endereços do chefe reconhecidos: %d", len(_cache))
    return _cache


def e_do_chefe(destinatario: str) -> bool:
    endereco = extrair(destinatario)
    if not endereco:
        return False
    if endereco in meus_enderecos():
        return True
    # gmail ignora pontos e trata +tag como a mesma caixa: joao.silva+nf@ = joaosilva@
    usuario, _, dominio = endereco.partition("@")
    if dominio in ("gmail.com", "googlemail.com"):
        base = usuario.split("+")[0].replace(".", "")
        for meu in meus_enderecos():
            m_usuario, _, m_dominio = meu.partition("@")
            if m_dominio in ("gmail.com", "googlemail.com") and \
                    m_usuario.split("+")[0].replace(".", "") == base:
                return True
    return False
