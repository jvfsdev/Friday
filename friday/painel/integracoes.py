"""O que o painel sabe configurar, e como testar se ficou certo.

Cada integração declara seus campos (todos variáveis do .env), um guia em
passos curtos para quem nunca viu aquilo, e um teste que fala com o serviço
de verdade — "salvou" não quer dizer "funciona".
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx


@dataclass
class Campo:
    env: str
    rotulo: str
    ajuda: str = ""
    secreto: bool = True
    multilinha: bool = False      # várias chaves, uma por linha (vira lista com vírgula)
    exemplo: str = ""


@dataclass
class Integracao:
    id: str
    nome: str
    resumo: str
    campos: list[Campo]
    passos: list[str]
    essencial: bool = False
    obrigatorios: list[str] = field(default_factory=list)


INTEGRACOES = [
    Integracao(
        "gemini", "Inteligência (Gemini)", "O cérebro. Sem ele, nada funciona.",
        [Campo("GEMINI_API_KEY", "Chaves de API", "Uma por linha. Cada chave de uma conta Google diferente "
               "soma mais cota gratuita.", multilinha=True, exemplo="AIza...")],
        ["Abra <a href='https://aistudio.google.com/apikey' target='_blank'>aistudio.google.com/apikey</a> "
         "e entre com sua conta Google.",
         "Clique em <b>Criar chave de API</b> e copie.",
         "Cole aqui e toque em <b>Testar</b>. É gratuito."],
        essencial=True, obrigatorios=["GEMINI_API_KEY"],
    ),
    Integracao(
        "telegram", "Telegram", "Onde vocês conversam.",
        [Campo("TELEGRAM_BOT_TOKEN", "Token do bot", exemplo="123456:ABC..."),
         Campo("TELEGRAM_USER_ID", "Seu ID", "Só esse ID consegue falar com o Guará. "
               "Não sabe? Salve o token e use o botão abaixo.", secreto=False, exemplo="123456789")],
        ["No Telegram, abra o <a href='https://t.me/BotFather' target='_blank'>@BotFather</a> e mande "
         "<code>/newbot</code>.",
         "Escolha um nome e um usuário terminado em <code>bot</code>. Ele te devolve um token: cole aqui.",
         "Para o seu ID: salve o token, mande qualquer mensagem para o seu bot e toque em "
         "<b>Descobrir meu ID</b>."],
        essencial=True, obrigatorios=["TELEGRAM_BOT_TOKEN", "TELEGRAM_USER_ID"],
    ),
    Integracao(
        "casa", "Casa (Home Assistant)", "Luzes, aparelhos, presença e clima.",
        [Campo("HA_URL", "Endereço", "Normalmente o próprio servidor.", secreto=False,
               exemplo="http://localhost:8123"),
         Campo("HA_TOKEN", "Token de acesso longo")],
        ["No Home Assistant, clique no seu nome (canto inferior esquerdo) → <b>Segurança</b>.",
         "Em <b>Tokens de acesso de longa duração</b>, crie um chamado <i>Guará</i> e copie.",
         "Cole aqui e teste."],
        obrigatorios=["HA_TOKEN"],
    ),
    Integracao(
        "notion", "Notion", "Listas de mercado, tarefas e anotações.",
        [Campo("NOTION_TOKEN", "Token da integração", exemplo="ntn_...")],
        ["Abra <a href='https://www.notion.so/my-integrations' target='_blank'>notion.so/my-integrations</a> "
         "e crie uma integração interna chamada <i>Guará</i>.",
         "Copie o token e cole aqui.",
         "Em cada página que ele pode usar: menu <b>•••</b> → <b>Conexões</b> → adicione o Guará."],
        obrigatorios=["NOTION_TOKEN"],
    ),
    Integracao(
        "financas", "Finanças (Open Finance)", "Saldos, fatura e o fluxo de caixa. Só leitura.",
        [Campo("PLUGGY_CLIENT_ID", "Client ID", secreto=False),
         Campo("PLUGGY_CLIENT_SECRET", "Client Secret"),
         Campo("PLUGGY_ITEM_IDS", "IDs das conexões", "Separados por vírgula.", secreto=False)],
        ["Conecte seus bancos em <a href='https://meu.pluggy.ai' target='_blank'>meu.pluggy.ai</a> "
         "(gratuito) e copie o <b>Item ID</b> de cada conexão.",
         "Em <a href='https://dashboard.pluggy.ai' target='_blank'>dashboard.pluggy.ai</a>, crie uma "
         "aplicação e copie Client ID e Client Secret.",
         "Cole tudo aqui e teste. O Guará nunca faz pagamentos: só lê."],
        obrigatorios=["PLUGGY_CLIENT_ID", "PLUGGY_CLIENT_SECRET"],
    ),
    Integracao(
        "telefone", "Ligações (Twilio)", "Ele te liga quando precisa de uma decisão. Pago.",
        [Campo("TWILIO_ACCOUNT_SID", "Account SID", secreto=False, exemplo="AC..."),
         Campo("TWILIO_AUTH_TOKEN", "Auth Token"),
         Campo("TWILIO_FROM_NUMBER", "Número da Twilio", secreto=False, exemplo="+55..."),
         Campo("USER_PHONE_NUMBER", "Seu celular", secreto=False, exemplo="+5511999999999")],
        ["Crie uma conta em <a href='https://www.twilio.com' target='_blank'>twilio.com</a> e compre um número.",
         "No console, copie Account SID e Auth Token.",
         "Verifique o seu celular na Twilio e preencha os números com +55 e DDD."],
        obrigatorios=["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", "USER_PHONE_NUMBER"],
    ),
]
POR_ID = {i.id: i for i in INTEGRACOES}


# ------------------------------------------------------------------ testes
# Cada teste devolve (ok, mensagem para gente).

async def _gemini(v: dict) -> tuple[bool, str]:
    chaves = [c.strip() for c in v.get("GEMINI_API_KEY", "").split(",") if c.strip()]
    if not chaves:
        return False, "Nenhuma chave preenchida."
    boas = 0
    async with httpx.AsyncClient(timeout=20) as c:
        for chave in chaves:
            r = await c.get("https://generativelanguage.googleapis.com/v1beta/models",
                            params={"key": chave, "pageSize": 1})
            boas += r.status_code == 200
    if boas == len(chaves):
        return True, f"{boas} chave(s) funcionando."
    if not boas:
        return False, "Nenhuma chave funcionou. Confira se copiou a chave inteira."
    return True, f"{boas} de {len(chaves)} chaves funcionam — confira as outras."


async def _telegram(v: dict) -> tuple[bool, str]:
    tok = v.get("TELEGRAM_BOT_TOKEN", "")
    if not tok:
        return False, "Falta o token."
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"https://api.telegram.org/bot{tok}/getMe")
    if r.status_code != 200:
        return False, "O Telegram não reconheceu esse token."
    bot = r.json()["result"]
    falta = "" if v.get("TELEGRAM_USER_ID") else " Agora falta o seu ID."
    return True, f"Bot @{bot['username']} encontrado.{falta}"


async def descobrir_id(tok: str) -> tuple[bool, str, str]:
    """Lê a última mensagem mandada ao bot e devolve (ok, msg, id)."""
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"https://api.telegram.org/bot{tok}/getUpdates", params={"limit": 20})
    if r.status_code == 409:
        return False, "O Guará já está usando esse bot — então o ID já deve estar configurado.", ""
    if r.status_code != 200:
        return False, "O Telegram não reconheceu o token.", ""
    msgs = [u["message"] for u in r.json().get("result", []) if "message" in u]
    if not msgs:
        return False, "Ainda não chegou mensagem. Mande um “oi” para o bot e tente de novo.", ""
    de = msgs[-1]["from"]
    return True, f"Achei: {de.get('first_name', '')} (@{de.get('username', '?')}).", str(de["id"])


async def _casa(v: dict) -> tuple[bool, str]:
    url = (v.get("HA_URL") or "http://localhost:8123").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{url}/api/", headers={"Authorization": f"Bearer {v.get('HA_TOKEN', '')}"})
    except httpx.HTTPError:
        return False, f"Não consegui chegar em {url}. O Home Assistant está ligado?"
    if r.status_code == 401:
        return False, "O Home Assistant recusou o token."
    return r.status_code == 200, "Conectado ao Home Assistant." if r.status_code == 200 else f"Resposta {r.status_code}."


async def _notion(v: dict) -> tuple[bool, str]:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get("https://api.notion.com/v1/users/me",
                        headers={"Authorization": f"Bearer {v.get('NOTION_TOKEN', '')}",
                                 "Notion-Version": "2022-06-28"})
    return (True, "Integração do Notion ok.") if r.status_code == 200 else (False, "O Notion recusou o token.")


async def _financas(v: dict) -> tuple[bool, str]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post("https://api.pluggy.ai/auth", json={
            "clientId": v.get("PLUGGY_CLIENT_ID", ""), "clientSecret": v.get("PLUGGY_CLIENT_SECRET", "")})
    if r.status_code != 200:
        return False, "A Pluggy recusou Client ID / Secret."
    itens = [i for i in v.get("PLUGGY_ITEM_IDS", "").split(",") if i.strip()]
    return True, f"Pluggy ok, {len(itens)} conexão(ões) declarada(s)."


async def _telefone(v: dict) -> tuple[bool, str]:
    sid = v.get("TWILIO_ACCOUNT_SID", "")
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json",
                        auth=(sid, v.get("TWILIO_AUTH_TOKEN", "")))
    return (True, "Conta Twilio ok.") if r.status_code == 200 else (False, "A Twilio recusou SID / token.")


TESTES = {"gemini": _gemini, "telegram": _telegram, "casa": _casa, "notion": _notion,
          "financas": _financas, "telefone": _telefone}


def configurada(integ: Integracao, env: dict) -> bool:
    return all(env.get(k) for k in integ.obrigatorios)
