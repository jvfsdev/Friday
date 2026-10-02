"""Quem pode entrar no painel.

Três barreiras, nesta ordem:
1. Rede: só endereços que não são da internet pública (rede de casa,
   Tailscale, a própria máquina). O painel mexe nas chaves de todas as
   contas — ele não existe para quem está do lado de fora.
2. Senha: criada no primeiro acesso, guardada só como hash (scrypt).
3. Sessão: cookie assinado, HttpOnly, SameSite=Strict — outro site não
   consegue disparar um formulário daqui em nome de quem está logado.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
import time
from pathlib import Path

COOKIE = "guara_sessao"
DURACAO_SESSAO = 30 * 24 * 3600
TENTATIVAS_POR_MINUTO = 5


class Guarda:
    def __init__(self, arquivo: Path):
        self.arquivo = arquivo
        self._falhas: dict[str, list[float]] = {}

    # ---- armazenamento ----
    def _ler(self) -> dict:
        try:
            return json.loads(self.arquivo.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _gravar(self, dados: dict):
        self.arquivo.parent.mkdir(exist_ok=True)
        tmp = self.arquivo.with_suffix(".tmp")
        tmp.write_text(json.dumps(dados), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(self.arquivo)

    @property
    def tem_senha(self) -> bool:
        return "senha" in self._ler()

    def _segredo(self) -> bytes:
        dados = self._ler()
        if "segredo" not in dados:
            dados["segredo"] = secrets.token_hex(32)
            self._gravar(dados)
        return bytes.fromhex(dados["segredo"])

    # ---- senha ----
    @staticmethod
    def _hash(senha: str, sal: bytes) -> bytes:
        # n=2^14: ~0,2 s no notebook velho — caro para quem chuta, barato para quem entra.
        return hashlib.scrypt(senha.encode(), salt=sal, n=2 ** 14, r=8, p=1, dklen=32)

    def definir_senha(self, senha: str):
        sal = secrets.token_bytes(16)
        dados = self._ler()
        dados["senha"] = {"sal": sal.hex(), "hash": self._hash(senha, sal).hex()}
        # trocar a senha derruba todas as sessões abertas
        dados["segredo"] = secrets.token_hex(32)
        self._gravar(dados)

    def confere(self, senha: str, ip: str) -> bool:
        agora = time.time()
        recentes = [t for t in self._falhas.get(ip, []) if agora - t < 60]
        if len(recentes) >= TENTATIVAS_POR_MINUTO:
            self._falhas[ip] = recentes
            return False
        guardada = self._ler().get("senha")
        ok = bool(guardada) and hmac.compare_digest(
            self._hash(senha, bytes.fromhex(guardada["sal"])), bytes.fromhex(guardada["hash"]))
        if not ok:
            self._falhas[ip] = recentes + [agora]
        return ok

    def bloqueado(self, ip: str) -> bool:
        agora = time.time()
        return len([t for t in self._falhas.get(ip, []) if agora - t < 60]) >= TENTATIVAS_POR_MINUTO

    # ---- sessão ----
    def novo_cookie(self) -> str:
        expira = str(int(time.time()) + DURACAO_SESSAO)
        return f"{expira}.{hmac.new(self._segredo(), expira.encode(), 'sha256').hexdigest()}"

    def sessao_valida(self, valor: str | None) -> bool:
        if not valor or "." not in valor:
            return False
        expira, assinatura = valor.split(".", 1)
        esperado = hmac.new(self._segredo(), expira.encode(), "sha256").hexdigest()
        return hmac.compare_digest(assinatura, esperado) and expira.isdigit() and int(expira) > time.time()


def endereco_permitido(ip: str, encaminhado: str | None) -> bool:
    """Rede de casa, Tailscale ou a própria máquina — nunca a internet.

    Se alguém um dia publicar esta porta por um proxy, a requisição chega
    "de 127.0.0.1"; por isso o X-Forwarded-For também é conferido.
    """
    candidatos = [ip] + [p.strip() for p in (encaminhado or "").split(",") if p.strip()]
    for c in candidatos:
        try:
            if ipaddress.ip_address(c.split("%")[0]).is_global:
                return False
        except ValueError:
            return False
    return True
