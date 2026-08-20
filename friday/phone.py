"""Ligações telefônicas com conversa livre (Twilio).

O JARVIS liga quando precisa de uma decisão do chefe. A conversa é por voz,
sem menu de dígitos: o Twilio transcreve a fala, o texto vai para uma sessão
dedicada do modelo e a resposta volta como fala.

Trava de segurança dentro da conversa livre: antes de executar qualquer ação
o JARVIS repete o que entendeu e espera um "sim" final. O modelo marca o
desfecho com [AUTORIZADO], [NEGADO] ou [FIM], que é o que o código obedece —
nunca a interpretação livre do texto.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

log = logging.getLogger("friday.phone")

INSTRUCOES = """Você é o JARVIS falando ao TELEFONE com o chefe. Regras:
- Frases curtas e naturais: isso vira voz, não texto.
- Comece explicando em uma ou duas frases por que ligou.
- Se ele autorizar uma ação, REPITA o que entendeu e peça a confirmação final.
- Só quando ele confirmar de verdade, responda começando com [AUTORIZADO].
- Se ele recusar, responda começando com [NEGADO].
- Quando a conversa acabar naturalmente, termine com [FIM] no final da fala.
- Nunca invente informação que não esteja no contexto abaixo.

Contexto desta ligação:
{contexto}"""

MAX_TURNOS = 12


@dataclass
class Ligacao:
    id: str
    contexto: str
    ao_autorizar: object = None      # callable async, executada se autorizado
    historico: list = field(default_factory=list)
    desfecho: str = ""               # autorizado | negado | fim
    transcricao: list = field(default_factory=list)


class PhoneService:
    def __init__(self, config, llm, notifier=None):
        self.config = config
        self.llm = llm
        self.notifier = notifier
        self.ligacoes: dict[str, Ligacao] = {}

    def disponivel(self) -> bool:
        return bool(
            self.config.twilio_sid and self.config.twilio_token
            and self.config.twilio_from and self.config.user_phone
            and self.config.public_url
        )

    # ---- disparo ----

    async def ligar(self, contexto: str, ao_autorizar=None) -> str:
        if not self.disponivel():
            raise RuntimeError(
                "telefone não configurado (faltam credenciais da Twilio ou public_url)"
            )
        import asyncio

        from twilio.rest import Client

        ligacao = Ligacao(id=uuid.uuid4().hex[:8], contexto=contexto, ao_autorizar=ao_autorizar)
        self.ligacoes[ligacao.id] = ligacao

        def criar():
            cliente = Client(self.config.twilio_sid, self.config.twilio_token)
            return cliente.calls.create(
                to=self.config.user_phone,
                from_=self.config.twilio_from,
                url=f"{self.config.public_url}/twilio/voice?ligacao={ligacao.id}",
                method="POST",
            )

        chamada = await asyncio.to_thread(criar)
        log.info("ligação %s iniciada (sid %s)", ligacao.id, chamada.sid)
        return ligacao.id

    # ---- conversa ----

    async def _falar_proximo(self, ligacao: Ligacao, fala_do_chefe: str | None) -> str:
        from google.genai import types

        if fala_do_chefe:
            ligacao.historico.append(types.Content(role="user", parts=[types.Part(text=fala_do_chefe)]))
            ligacao.transcricao.append(f"chefe: {fala_do_chefe}")
        else:
            ligacao.historico.append(
                types.Content(role="user", parts=[types.Part(text="[a ligação foi atendida]")])
            )

        resposta = await self.llm.generate(
            ligacao.historico,
            types.GenerateContentConfig(
                system_instruction=INSTRUCOES.format(contexto=ligacao.contexto)
            ),
        )
        texto = (resposta.text or "Desculpe, não consegui pensar numa resposta.").strip()
        ligacao.historico.append(types.Content(role="model", parts=[types.Part(text=texto)]))
        ligacao.transcricao.append(f"jarvis: {texto}")

        if texto.startswith("[AUTORIZADO]"):
            ligacao.desfecho = "autorizado"
        elif texto.startswith("[NEGADO]"):
            ligacao.desfecho = "negado"
        elif texto.rstrip().endswith("[FIM]"):
            ligacao.desfecho = "fim"
        return texto.replace("[AUTORIZADO]", "").replace("[NEGADO]", "").replace("[FIM]", "").strip()

    async def responder(self, ligacao_id: str, fala: str | None) -> str:
        """Devolve o TwiML da próxima fala."""
        ligacao = self.ligacoes.get(ligacao_id)
        if not ligacao:
            return self._twiml("Desculpe, essa ligação expirou.", encerrar=True)

        if len(ligacao.historico) > MAX_TURNOS * 2:
            return self._twiml("Vamos continuar pelo Telegram, chefe. Até já.", encerrar=True)

        try:
            texto = await self._falar_proximo(ligacao, fala)
        except Exception:
            log.exception("falha na conversa da ligação %s", ligacao_id)
            return self._twiml("Tive um problema aqui, chefe. Te mando pelo Telegram.", encerrar=True)

        if ligacao.desfecho:
            await self._encerrar(ligacao)
            return self._twiml(texto, encerrar=True)
        return self._twiml(texto, encerrar=False, ligacao_id=ligacao_id)

    async def _encerrar(self, ligacao: Ligacao):
        transcricao = "\n".join(ligacao.transcricao)
        log.info("ligação %s encerrada: %s", ligacao.id, ligacao.desfecho)
        if ligacao.desfecho == "autorizado" and ligacao.ao_autorizar:
            try:
                await ligacao.ao_autorizar()
            except Exception:
                log.exception("ação autorizada por telefone falhou")
        if self.notifier:
            try:
                await self.notifier(
                    f"📞 Ligação encerrada ({ligacao.desfecho}).\n\n{transcricao}"
                )
            except Exception:
                log.exception("não consegui registrar a ligação")

    # ---- TwiML ----

    def _twiml(self, texto: str, encerrar: bool, ligacao_id: str = "") -> str:
        voz = self.config.twilio_voice
        falar = f'<Say voice="{voz}" language="pt-BR">{escape(texto)}</Say>'
        if encerrar:
            return f'<?xml version="1.0" encoding="UTF-8"?><Response>{falar}<Hangup/></Response>'
        acao = f"{self.config.public_url}/twilio/gather?ligacao={ligacao_id}"
        return (
            '<?xml version="1.0" encoding="UTF-8"?><Response>'
            f'<Gather input="speech" language="pt-BR" speechTimeout="auto" '
            f'action="{escape(acao)}" method="POST">{falar}</Gather>'
            f'<Say voice="{voz}" language="pt-BR">Não ouvi resposta. Até logo.</Say><Hangup/>'
            "</Response>"
        )
