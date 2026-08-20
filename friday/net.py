"""Preferência por IPv4 em máquinas sem rota IPv6.

O servidor recebe respostas AAAA das APIs do Google, mas não tem rota IPv6:
cada conexão tenta o caminho v6 e falha (ou espera o timeout) antes de cair
para o v4. Reordenar o `getaddrinfo` conserta isso para todas as bibliotecas
do processo de uma vez — httpx, google-api-client, telegram, MCP.

Só age quando a rota IPv6 realmente não existe; em máquina com IPv6 saudável
não mexe em nada.
"""

from __future__ import annotations

import logging
import socket

log = logging.getLogger("friday.net")

_applied = False


def _has_ipv6_route() -> bool:
    """UDP connect não envia pacote: só consulta a tabela de rotas."""
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.5)
            sock.connect(("2001:4860:4860::8888", 53))
        return True
    except OSError:
        return False


def prefer_ipv4_if_needed() -> bool:
    """Põe os endereços IPv4 na frente quando a máquina não tem IPv6."""
    global _applied
    if _applied or _has_ipv6_route():
        return False

    original = socket.getaddrinfo

    def getaddrinfo_v4_first(host, port, family=0, type=0, proto=0, flags=0):
        resultados = original(host, port, family, type, proto, flags)
        # Ordena em vez de filtrar: um destino só-IPv6 continua alcançável.
        return sorted(resultados, key=lambda r: 0 if r[0] == socket.AF_INET else 1)

    socket.getaddrinfo = getaddrinfo_v4_first
    _applied = True
    log.info("sem rota IPv6 nesta máquina — priorizando IPv4 nas conexões")
    return True
