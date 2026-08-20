"""Servidor web interno do JARVIS.

Atende os webhooks da Twilio (ligações) e um endpoint genérico de entrada
para sistemas de suporte. Não fica exposto direto: quem publica é o
`tailscale funnel`, então nenhuma porta do roteador precisa ser aberta.
"""

from __future__ import annotations

import hmac
import logging

from aiohttp import web

log = logging.getLogger("friday.web")


class WebServer:
    def __init__(self, config, phone=None, on_intake=None):
        self.config = config
        self.phone = phone
        self.on_intake = on_intake      # async (payload: dict) -> None
        self.app = web.Application()
        self.app.add_routes([
            web.get("/health", self.health),
            web.post("/intake", self.intake),
            web.post("/twilio/voice", self.twilio_voice),
            web.post("/twilio/gather", self.twilio_gather),
        ])
        self._runner: web.AppRunner | None = None

    async def start(self):
        self._runner = web.AppRunner(self.app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self.config.web_port)
        await site.start()
        log.info("servidor web interno na porta %d (público: %s)",
                 self.config.web_port, self.config.public_url or "não configurado")

    async def stop(self):
        if self._runner:
            await self._runner.cleanup()

    # ---- rotas ----

    async def health(self, request):
        return web.json_response({"status": "ok", "jarvis": "online"})

    async def intake(self, request):
        """Entrada genérica para sistemas de suporte (token obrigatório)."""
        token = request.headers.get("X-Jarvis-Token") or request.query.get("token", "")
        if not self.config.intake_token or not hmac.compare_digest(token, self.config.intake_token):
            return web.json_response({"erro": "token inválido"}, status=401)
        try:
            payload = await request.json()
        except Exception:
            payload = {"texto": (await request.text())[:5000]}
        log.info("entrada recebida via /intake")
        if self.on_intake:
            await self.on_intake(payload)
        return web.json_response({"recebido": True})

    async def _validar_twilio(self, request) -> bool:
        """Confere a assinatura para ninguém falsificar webhook de ligação."""
        if not self.config.twilio_token:
            return False
        try:
            from twilio.request_validator import RequestValidator
        except ImportError:
            return False
        assinatura = request.headers.get("X-Twilio-Signature", "")
        url = f"{self.config.public_url}{request.path_qs}"
        form = dict(await request.post())
        return RequestValidator(self.config.twilio_token).validate(url, form, assinatura)

    async def twilio_voice(self, request):
        if not self.phone or not await self._validar_twilio(request):
            return web.Response(status=403, text="assinatura inválida")
        ligacao = request.query.get("ligacao", "")
        return web.Response(
            text=await self.phone.responder(ligacao, None), content_type="text/xml"
        )

    async def twilio_gather(self, request):
        if not self.phone or not await self._validar_twilio(request):
            return web.Response(status=403, text="assinatura inválida")
        ligacao = request.query.get("ligacao", "")
        form = await request.post()
        fala = (form.get("SpeechResult") or "").strip()
        return web.Response(
            text=await self.phone.responder(ligacao, fala or "(não falou nada)"),
            content_type="text/xml",
        )
