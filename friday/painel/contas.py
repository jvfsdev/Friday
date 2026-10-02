"""Conectar contas Google e Microsoft pelo navegador, sem terminal.

Google: o tipo de credencial que funciona para Gmail/Agenda/Drive num app
pessoal ("App para computador") só aceita voltar para `localhost`. Pelo
celular, esse endereço não abre — então a página pede para o usuário copiar
o endereço da aba que "não carregou" e colar aqui. O código de autorização
está nele. É o caminho oficial que sobrou depois que o Google aposentou o
"copie este código".

Microsoft: o login por código de dispositivo é feito para isso — mostra um
código, a pessoa digita em microsoft.com/devicelogin em qualquer aparelho, e
o painel espera em segundo plano.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("guara.painel")

RETORNO_GOOGLE = "http://localhost:8971/"
NOME_VALIDO = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")


def nome_ok(nome: str) -> bool:
    return bool(NOME_VALIDO.match(nome))


class Contas:
    def __init__(self, estado: Path):
        self.estado = estado
        self._google: dict[str, tuple[object, float]] = {}   # nome -> (flow, criado)
        self.microsoft: dict[str, LoginMicrosoft] = {}

    # ---------------------------------------------------------------- Google
    @property
    def credencial_google(self) -> Path:
        return self.estado / "google_credentials.json"

    def contas_google(self) -> list[str]:
        return sorted(p.stem.removeprefix("google_token_") for p in self.estado.glob("google_token_*.json"))

    def salvar_credencial(self, conteudo: bytes) -> str:
        try:
            dados = json.loads(conteudo)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return "Esse arquivo não é o JSON da credencial."
        if "installed" not in dados:
            if "web" in dados:
                return ("Essa credencial é do tipo “Aplicativo da Web”. Crie uma do tipo "
                        "“App para computador” e envie de novo.")
            return "Esse JSON não parece uma credencial OAuth do Google."
        self.estado.mkdir(exist_ok=True)
        self.credencial_google.write_bytes(conteudo)
        self.credencial_google.chmod(0o600)
        return ""

    def iniciar_google(self, nome: str) -> str:
        """Devolve a URL onde o usuário autoriza."""
        from google_auth_oauthlib.flow import Flow

        from ..tools.google_workspace import SCOPES

        flow = Flow.from_client_secrets_file(str(self.credencial_google), scopes=SCOPES,
                                             redirect_uri=RETORNO_GOOGLE)
        url, _ = flow.authorization_url(access_type="offline", prompt="consent")
        self._google = {n: v for n, v in self._google.items() if time.time() - v[1] < 1800}
        self._google[nome] = (flow, time.time())
        return url

    def finalizar_google(self, nome: str, colado: str) -> str:
        """Recebe o endereço colado (ou só o código). Devolve erro ou ''."""
        if nome not in self._google:
            return "Esse pedido expirou. Comece a conexão de novo."
        texto = colado.strip()
        codigo = parse_qs(urlparse(texto).query).get("code", [""])[0] if "code=" in texto else texto
        if not codigo:
            return "Não achei o código nesse endereço. Copie a barra de endereços inteira."
        flow, _ = self._google[nome]
        # o Google às vezes devolve escopos numa ordem/forma diferente da pedida
        os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
        try:
            flow.fetch_token(code=codigo)
        except Exception as exc:
            log.warning("google: falha ao trocar o código: %s", exc)
            return "O Google não aceitou esse código. Ele vale uma vez só e expira rápido — tente de novo."
        destino = self.estado / f"google_token_{nome}.json"
        destino.write_text(flow.credentials.to_json(), encoding="utf-8")
        destino.chmod(0o600)
        del self._google[nome]
        return ""

    def remover_google(self, nome: str):
        (self.estado / f"google_token_{nome}.json").unlink(missing_ok=True)

    # ------------------------------------------------------------- Microsoft
    def contas_microsoft(self) -> list[str]:
        return sorted(p.stem.removeprefix("ms_token_") for p in self.estado.glob("ms_token_*.json"))

    async def iniciar_microsoft(self, client_id: str, nome: str) -> "LoginMicrosoft":
        login = LoginMicrosoft(nome)
        self.microsoft[nome] = login
        await login.comecar(client_id, self.estado / f"ms_token_{nome}.json")
        return login

    def remover_microsoft(self, nome: str):
        (self.estado / f"ms_token_{nome}.json").unlink(missing_ok=True)


@dataclass
class LoginMicrosoft:
    nome: str
    codigo: str = ""
    link: str = ""
    situacao: str = "iniciando"      # iniciando | esperando | pronto | erro
    erro: str = ""
    _tarefa: object = field(default=None, repr=False)

    async def comecar(self, client_id: str, destino: Path):
        import msal

        from ..tools.outlook import AUTHORITY, SCOPES

        cache = msal.SerializableTokenCache()
        app = msal.PublicClientApplication(client_id, authority=AUTHORITY, token_cache=cache)
        fluxo = await asyncio.to_thread(app.initiate_device_flow, scopes=SCOPES)
        if "user_code" not in fluxo:
            self.situacao, self.erro = "erro", fluxo.get("error_description", "a Microsoft recusou o pedido")
            return
        self.codigo, self.link, self.situacao = fluxo["user_code"], fluxo["verification_uri"], "esperando"

        async def esperar():
            resultado = await asyncio.to_thread(app.acquire_token_by_device_flow, fluxo)
            if "access_token" in resultado:
                destino.write_text(cache.serialize(), encoding="utf-8")
                destino.chmod(0o600)
                self.situacao = "pronto"
            else:
                self.situacao, self.erro = "erro", resultado.get("error_description", "login não concluído")

        self._tarefa = asyncio.create_task(esperar())
