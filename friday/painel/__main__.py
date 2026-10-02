"""Painel do Guará: configurar tudo pelo navegador, sem terminal.

    python -m friday.painel            # porta 8080 (ou GUARA_PAINEL_PORTA)

Roda como serviço separado do Guará de propósito: o Guará se recusa a subir
sem chave do Gemini e token do Telegram — e configurar isso é justamente o
trabalho do painel numa instalação nova.

Só responde à rede de casa e ao Tailscale; tem senha própria (criada no
primeiro acesso). Para testes, GUARA_RAIZ aponta para outra pasta.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
from pathlib import Path

from aiohttp import web

from ..config import ROOT
from . import arquivos, integracoes
from .contas import Contas, nome_ok
from .seguranca import COOKIE, DURACAO_SESSAO, Guarda, endereco_permitido
from .visual import e, pagina
from .voz import Calibracao

log = logging.getLogger("guara.painel")

RAIZ = Path(os.environ.get("GUARA_RAIZ") or ROOT)
ENV, CONFIG, ESTADO = RAIZ / ".env", RAIZ / "config.yaml", RAIZ / "state"
PORTA = int(os.environ.get("GUARA_PAINEL_PORTA", "8080"))
FONTES = Path(os.environ.get("FRIDAY_FONTES") or "/usr/share/fonts/opentype/inter")
LIVRES = {"/entrar", "/primeiro-acesso"}
REINICIAR = "precisa_reiniciar"


# ================================================================ guardas
@web.middleware
async def porteiro(request, handler):
    if not endereco_permitido(request.remote or "", request.headers.get("X-Forwarded-For")):
        return web.Response(status=403, text="O painel só abre na rede de casa ou pelo Tailscale.")
    if request.path.startswith("/fontes/"):
        return await handler(request)
    guarda: Guarda = request.app["guarda"]
    if not guarda.tem_senha:
        if request.path != "/primeiro-acesso":
            raise web.HTTPSeeOther("/primeiro-acesso")
        return await handler(request)
    if request.method == "POST":
        # SameSite=Strict já barra o cookie vindo de outro site; isto é a segunda tranca.
        origem = request.headers.get("Origin")
        if origem and origem.split("://", 1)[-1] != request.host:
            return web.Response(status=403, text="Origem não confere.")
    if request.path not in LIVRES and not guarda.sessao_valida(request.cookies.get(COOKIE)):
        raise web.HTTPSeeOther("/entrar")
    return await handler(request)


def responder(html: str) -> web.Response:
    return web.Response(text=html, content_type="text/html",
                        headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY",
                                 "Referrer-Policy": "same-origin"})


def precisa_reiniciar(app) -> bool:
    return app.get(REINICIAR, False)


def tela(request, titulo, corpo, **kw) -> web.Response:
    return responder(pagina(titulo, corpo, reiniciar=precisa_reiniciar(request.app), **kw))


# ================================================================ acesso
async def primeiro_acesso(request):
    guarda: Guarda = request.app["guarda"]
    if guarda.tem_senha:
        raise web.HTTPSeeOther("/")
    erro = ""
    if request.method == "POST":
        f = await request.post()
        s1, s2 = f.get("senha", ""), f.get("repete", "")
        if len(s1) < 8:
            erro = "A senha precisa de pelo menos 8 caracteres."
        elif s1 != s2:
            erro = "As duas senhas não batem."
        else:
            guarda.definir_senha(s1)
            raise _logar(guarda, "/")
    corpo = """<h1>Oi. Eu sou o Guará.</h1>
<p class="sub">Primeiro, uma senha para este painel. Ele guarda as chaves de todas as suas contas.</p>
<form method="post"><label for="f-senha">Senha</label><input id="f-senha" type="password" name="senha" autocomplete="new-password" required>
<label for="f-repete">Repita</label><input id="f-repete" type="password" name="repete" autocomplete="new-password" required>
<div class="botoes"><button>Criar senha</button></div></form>"""
    return responder(pagina("Primeiro acesso", corpo, logado=False, aviso=erro, aviso_ruim=True))


def _logar(guarda: Guarda, destino: str) -> web.Response:
    r = web.HTTPSeeOther(destino)
    r.set_cookie(COOKIE, guarda.novo_cookie(), max_age=DURACAO_SESSAO, httponly=True, samesite="Strict")
    return r


async def entrar(request):
    guarda: Guarda = request.app["guarda"]
    erro = ""
    if request.method == "POST":
        ip = request.remote or "?"
        senha = (await request.post()).get("senha", "")
        if guarda.bloqueado(ip):
            erro = "Muitas tentativas. Espere um minuto."
        elif await asyncio.to_thread(guarda.confere, senha, ip):
            raise _logar(guarda, "/")
        else:
            erro = "Senha errada."
    corpo = """<h1>Entrar</h1><form method="post"><label for="f-senha">Senha do painel</label><input id="f-senha" type="password" name="senha" autocomplete="current-password" autofocus required>
<div class="botoes"><button>Entrar</button></div></form>"""
    return responder(pagina("Entrar", corpo, logado=False, aviso=erro, aviso_ruim=True))


async def sair(request):
    r = web.HTTPSeeOther("/entrar")
    r.del_cookie(COOKIE)
    raise r


# ================================================================ início
def _servico(nome: str, usuario=False) -> str:
    cmd = ["systemctl"] + (["--user"] if usuario else []) + ["is-active", nome]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip() or "?"
    except (OSError, subprocess.TimeoutExpired):
        return "?"


async def inicio(request):
    env = arquivos.ler_env(ENV)
    contas: Contas = request.app["contas"]
    estado = await asyncio.to_thread(_servico, "friday")
    no_ar = estado == "active"
    essenciais_ok = all(integracoes.configurada(i, env) for i in integracoes.INTEGRACOES if i.essencial)

    def linha(href, titulo, desc, ok, essencial=False):
        selo = ('<span class="selo ok">CONECTADO</span>' if ok else
                '<span class="selo falta">FALTA</span>' if essencial else '<span class="selo nao">CONFIGURAR</span>')
        return f'<a class="linha" href="{href}"><span class="x"><span class="t">{e(titulo)}</span><br><span class="d">{e(desc)}</span></span>{selo}</a>'

    linhas = [linha(f"/c/{i.id}", i.nome, i.resumo, integracoes.configurada(i, env), i.essencial)
              for i in integracoes.INTEGRACOES if i.essencial]
    g, m = contas.contas_google(), contas.contas_microsoft()
    linhas.append(linha("/google", "Contas Google", "Gmail, Agenda e Drive." + (f" {len(g)} conectada(s)." if g else ""), bool(g)))
    linhas.append(linha("/microsoft", "Contas Microsoft", "Outlook e Hotmail." + (f" {len(m)} conectada(s)." if m else ""), bool(m)))
    linhas += [linha(f"/c/{i.id}", i.nome, i.resumo, integracoes.configurada(i, env))
               for i in integracoes.INTEGRACOES if not i.essencial]
    linhas.append(linha("/voz", "Palavra de ativação", "Ajuste para a sua voz e a sua sala.", True))
    linhas.append(linha("/preferencias", "Preferências", "Fuso horário e horário de silêncio.", True))

    if no_ar:
        titulo, sub = "Tudo no ar.", "Ele está rodando. Aqui você liga e desliga o resto."
    elif not essenciais_ok:
        titulo, sub = "Vamos começar.", "Preencha os dois itens laranja. O resto pode ficar para depois."
    else:
        titulo, sub = "Parado.", f"O Guará não está rodando (estado: {estado}). Tente reiniciar."
    corpo = (f'<p class="rotulo">{"● online" if no_ar else "○ offline"}</p><h1>{titulo}</h1><p class="sub">{e(sub)}</p>'
             + "".join(linhas)
             + '<div class="botoes"><form method="post" action="/reiniciar"><button class="leve">Reiniciar o Guará</button></form></div>')
    return tela(request, "Início", corpo)


async def reiniciar(request):
    """Encerra o processo do Guará; o systemd (Restart=always) sobe de novo.
    Assim o painel não precisa de permissão de administrador."""
    try:
        pid = int(subprocess.run(["systemctl", "show", "-p", "MainPID", "--value", "friday"],
                                 capture_output=True, text=True, timeout=5).stdout.strip() or 0)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pid = 0
    if pid > 0:
        try:
            os.kill(pid, signal.SIGTERM)
        except (PermissionError, ProcessLookupError):
            pid = 0
    request.app[REINICIAR] = False
    msg = ("Reiniciando. Em uns 30 segundos ele volta." if pid else
           "Não achei o Guará rodando como serviço. Se ele estiver parado por falta de configuração, "
           "complete os itens laranja: o serviço tenta subir de novo sozinho a cada poucos segundos.")
    return responder(pagina("Reiniciando", f'<h1>Já volto.</h1><p class="sub">{e(msg)}</p>'
                            '<a class="botao" href="/">Voltar ao início</a>', atualizar=0))


# ================================================================ integrações
def _form_integracao(integ, env, aviso="", ruim=False) -> str:
    passos = "".join(f"<li>{p}</li>" for p in integ.passos)   # texto fixo do código, com links
    campos = []
    for c in integ.campos:
        atual = env.get(c.env, "")
        if c.multilinha:
            n = len([x for x in atual.split(",") if x.strip()])
            dica = f"{n} chave(s) salva(s). Escreva aqui para substituir todas." if n else ""
            campo = f'<textarea name="{c.env}" placeholder="{e(c.exemplo)}" spellcheck="false"></textarea>'
        elif c.secreto:
            dica = f"Salvo: {arquivos.mascarar(atual)}. Deixe vazio para manter." if atual else ""
            campo = f'<input type="password" name="{c.env}" placeholder="{e(c.exemplo)}" autocomplete="off">'
        else:
            dica = ""
            campo = f'<input type="text" name="{c.env}" value="{e(atual)}" placeholder="{e(c.exemplo)}" autocomplete="off">'
        ajuda = " ".join(x for x in (c.ajuda, dica) if x)
        campo = campo.replace(" name=", f' id="f-{c.env}" name=', 1)
        campos.append(f'<label for="f-{c.env}">{e(c.rotulo)}</label>{campo}' + (f'<p class="ajuda">{e(ajuda)}</p>' if ajuda else ""))
    extra = ('<button class="leve" formaction="/c/telegram/id">Descobrir meu ID</button>'
             if integ.id == "telegram" else "")
    return (f'<p class="rotulo">{"essencial" if integ.essencial else "opcional"}</p><h1>{e(integ.nome)}</h1>'
            f'<p class="sub">{e(integ.resumo)}</p><ol class="passos">{passos}</ol>'
            f'<form method="post">{"".join(campos)}<div class="botoes"><button>Salvar</button>'
            f'<button class="leve" formaction="/c/{integ.id}/testar">Testar</button>{extra}</div></form>')


async def _salvar_campos(integ, form) -> dict:
    mudancas = {}
    for c in integ.campos:
        valor = (form.get(c.env) or "").strip()
        if c.multilinha:
            valor = ",".join(x.strip() for x in valor.replace(",", "\n").splitlines() if x.strip())
        if valor or not c.secreto:      # secreto vazio = manter o atual
            mudancas[c.env] = valor
    if integ.id == "gemini" and "GEMINI_API_KEY" in mudancas and arquivos.ler_env(ENV).get("GEMINI_API_KEYS"):
        mudancas["GEMINI_API_KEYS"] = ""   # a variante antiga teria precedência e esconderia a nova
    return mudancas


async def integracao(request):
    integ = integracoes.POR_ID.get(request.match_info["id"])
    if not integ:
        raise web.HTTPNotFound()
    aviso, ruim = "", False
    if request.method == "POST":
        try:
            mudancas = await _salvar_campos(integ, await request.post())
            if mudancas:
                arquivos.gravar_env(ENV, mudancas)
                request.app[REINICIAR] = True
            aviso = "Salvo. Toque em Testar para conferir."
        except arquivos.ValorInvalido as exc:
            aviso, ruim = e(str(exc)), True
    return tela(request, integ.nome, _form_integracao(integ, arquivos.ler_env(ENV)), aviso=aviso, aviso_ruim=ruim)


async def testar(request):
    integ = integracoes.POR_ID.get(request.match_info["id"])
    if not integ:
        raise web.HTTPNotFound()
    # testa o que foi digitado agora por cima do que está salvo, sem gravar
    valores = arquivos.ler_env(ENV)
    valores.update({k: v for k, v in (await _salvar_campos(integ, await request.post())).items() if v})
    try:
        ok, msg = await integracoes.TESTES[integ.id](valores)
    except Exception as exc:
        ok, msg = False, f"Não consegui testar agora ({type(exc).__name__}). A internet está ok?"
    return tela(request, integ.nome, _form_integracao(integ, arquivos.ler_env(ENV)),
                aviso=("Funcionou! " if ok else "Não funcionou. ") + e(msg), aviso_ruim=not ok)


async def telegram_id(request):
    integ = integracoes.POR_ID["telegram"]
    form = await request.post()
    tok = (form.get("TELEGRAM_BOT_TOKEN") or "").strip() or arquivos.ler_env(ENV).get("TELEGRAM_BOT_TOKEN", "")
    if not tok:
        aviso, ruim = "Primeiro salve o token do bot.", True
    else:
        ok, msg, uid = await integracoes.descobrir_id(tok)
        aviso, ruim = e(msg), not ok
        if ok:
            arquivos.gravar_env(ENV, {"TELEGRAM_BOT_TOKEN": tok, "TELEGRAM_USER_ID": uid})
            request.app[REINICIAR] = True
            aviso += " Salvei como o seu ID."
    return tela(request, integ.nome, _form_integracao(integ, arquivos.ler_env(ENV)), aviso=aviso, aviso_ruim=ruim)


# ================================================================ Google
def _lista_contas(nomes, rota) -> str:
    if not nomes:
        return '<p class="vazio">Nenhuma conta conectada ainda.</p>'
    return "".join(
        f'<div class="linha"><span class="x"><span class="t">{e(n)}</span></span>'
        f'<form method="post" action="{rota}/remover"><input type="hidden" name="nome" value="{e(n)}">'
        f'<button class="leve">Desconectar</button></form></div>' for n in nomes)


async def google(request):
    contas: Contas = request.app["contas"]
    aviso, ruim = "", False
    if request.method == "POST":
        arquivo = (await request.post()).get("credencial")
        conteudo = arquivo.file.read(200_000) if hasattr(arquivo, "file") else b""
        erro = contas.salvar_credencial(conteudo)
        aviso, ruim = (erro, True) if erro else ("Credencial salva. Agora conecte suas contas.", False)
    if not contas.credencial_google.exists():
        corpo = """<p class="rotulo">uma vez só</p><h1>Contas Google</h1>
<p class="sub">Antes da primeira conta, o Google exige uma “credencial” sua. É chato, mas é uma vez só.</p>
<ol class="passos">
<li>Abra o <a href="https://console.cloud.google.com/projectcreate" target="_blank">Google Cloud</a> e crie um projeto chamado <i>Guará</i>.</li>
<li>Em <a href="https://console.cloud.google.com/apis/library" target="_blank">APIs e serviços → Biblioteca</a>, ative <b>Gmail API</b>, <b>Google Calendar API</b> e <b>Google Drive API</b>.</li>
<li>Em <b>Tela de consentimento OAuth</b>: tipo <b>Externo</b>, e adicione cada e-mail seu como <b>usuário de teste</b>.</li>
<li>Em <b>Credenciais → Criar credenciais → ID do cliente OAuth</b>, escolha <b>App para computador</b>.</li>
<li>Baixe o JSON e envie aqui.</li></ol>
<form method="post" enctype="multipart/form-data"><input type="file" name="credencial" accept=".json,application/json" required>
<div class="botoes"><button>Enviar credencial</button></div></form>"""
        return tela(request, "Contas Google", corpo, aviso=aviso, aviso_ruim=ruim)
    corpo = (f'<h1>Contas Google</h1><p class="sub">Gmail, Agenda e Drive. Dá para conectar várias.</p>'
             f'{_lista_contas(contas.contas_google(), "/google")}<h2>Conectar outra</h2>'
             '<form method="post" action="/google/conectar"><label for="f-nome">Apelido da conta</label>'
             '<input type="text" id="f-nome" name="nome" placeholder="pessoal, trabalho, faculdade…" required>'
             '<p class="ajuda">Só letras minúsculas, números e hífen.</p>'
             '<div class="botoes"><button>Conectar</button></div></form>')
    return tela(request, "Contas Google", corpo, aviso=aviso, aviso_ruim=ruim)


async def google_conectar(request):
    contas: Contas = request.app["contas"]
    nome = ((await request.post()).get("nome") or "").strip().lower()
    if not nome_ok(nome):
        return tela(request, "Contas Google", '<a class="botao" href="/google">Voltar</a>',
                    aviso="Apelido inválido: use letras minúsculas, números e hífen.", aviso_ruim=True)
    try:
        url = await asyncio.to_thread(contas.iniciar_google, nome)
    except Exception as exc:
        log.warning("google: não iniciou: %s", exc)
        return tela(request, "Contas Google", '<a class="botao" href="/google">Voltar</a>',
                    aviso="A credencial enviada não funcionou. Envie de novo.", aviso_ruim=True)
    corpo = f"""<p class="rotulo">conta “{e(nome)}”</p><h1>Dois passos.</h1>
<ol class="passos"><li><a class="botao quente" href="{e(url)}" target="_blank" rel="noopener">1 · Autorizar no Google</a></li>
<li>Entre com a conta certa e autorize tudo. Se aparecer “o Google não verificou este app”, toque em <b>Avançado → continuar</b>: o app é o seu.</li>
<li>No fim, abre uma página que <b>não carrega</b> (endereço começando com <code>localhost</code>). É normal. <b>Copie o endereço inteiro</b> da barra e cole aqui:</li></ol>
<form method="post" action="/google/finalizar"><input type="hidden" name="nome" value="{e(nome)}">
<input type="text" name="endereco" placeholder="http://localhost:8971/?state=…&code=…" required autocomplete="off">
<div class="botoes"><button>2 · Concluir</button></div></form>"""
    return tela(request, "Contas Google", corpo)


async def google_finalizar(request):
    contas: Contas = request.app["contas"]
    f = await request.post()
    nome = (f.get("nome") or "").strip()
    erro = await asyncio.to_thread(contas.finalizar_google, nome, f.get("endereco") or "")
    if not erro:
        request.app[REINICIAR] = True
    return tela(request, "Contas Google", '<a class="botao" href="/google">Voltar</a>',
                aviso=e(erro) if erro else f"Conta “{e(nome)}” conectada!", aviso_ruim=bool(erro))


async def google_remover(request):
    nome = ((await request.post()).get("nome") or "").strip()
    if nome_ok(nome):
        request.app["contas"].remover_google(nome)
        request.app[REINICIAR] = True
    raise web.HTTPSeeOther("/google")


# ================================================================ Microsoft
async def microsoft(request):
    contas: Contas = request.app["contas"]
    env = arquivos.ler_env(ENV)
    aviso, ruim = "", False
    if request.method == "POST":
        cid = ((await request.post()).get("MS_CLIENT_ID") or "").strip()
        if cid:
            arquivos.gravar_env(ENV, {"MS_CLIENT_ID": cid})
            env["MS_CLIENT_ID"], aviso = cid, "Salvo."
    corpo = ('<h1>Contas Microsoft</h1><p class="sub">Outlook, Hotmail e Live.</p>'
             '<ol class="passos"><li>No <a href="https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps" target="_blank">portal do Azure</a>, '
             '<b>Novo registro</b>: nome <i>Guará</i>, tipo “contas em qualquer diretório e contas pessoais”.</li>'
             '<li>Em <b>Autenticação</b>, ative <b>Permitir fluxos de cliente público</b>.</li>'
             '<li>Copie o <b>ID do aplicativo (cliente)</b> e cole abaixo.</li></ol>'
             f'<form method="post"><label for="f-MS_CLIENT_ID">ID do aplicativo</label><input id="f-MS_CLIENT_ID" type="text" name="MS_CLIENT_ID" value="{e(env.get("MS_CLIENT_ID", ""))}">'
             '<div class="botoes"><button class="leve">Salvar ID</button></div></form>')
    if env.get("MS_CLIENT_ID"):
        corpo += (f'<h2>Contas</h2>{_lista_contas(contas.contas_microsoft(), "/microsoft")}'
                  '<form method="post" action="/microsoft/conectar"><label for="f-nome">Apelido da conta</label>'
                  '<input type="text" id="f-nome" name="nome" placeholder="hotmail" required>'
                  '<div class="botoes"><button>Conectar</button></div></form>')
    return tela(request, "Contas Microsoft", corpo, aviso=aviso, aviso_ruim=ruim)


async def microsoft_conectar(request):
    contas: Contas = request.app["contas"]
    nome = ((await request.post()).get("nome") or "").strip().lower()
    if not nome_ok(nome):
        return tela(request, "Contas Microsoft", '<a class="botao" href="/microsoft">Voltar</a>',
                    aviso="Apelido inválido.", aviso_ruim=True)
    await contas.iniciar_microsoft(arquivos.ler_env(ENV).get("MS_CLIENT_ID", ""), nome)
    raise web.HTTPSeeOther(f"/microsoft/espera?nome={nome}")


async def microsoft_espera(request):
    contas: Contas = request.app["contas"]
    login = contas.microsoft.get(request.query.get("nome", ""))
    if not login:
        raise web.HTTPSeeOther("/microsoft")
    if login.situacao == "pronto":
        request.app[REINICIAR] = True
        return tela(request, "Contas Microsoft", '<a class="botao" href="/microsoft">Voltar</a>',
                    aviso=f"Conta “{e(login.nome)}” conectada!")
    if login.situacao == "erro":
        return tela(request, "Contas Microsoft", '<a class="botao" href="/microsoft">Voltar</a>',
                    aviso=e(login.erro), aviso_ruim=True)
    corpo = (f'<p class="rotulo">conta “{e(login.nome)}”</p><h1>Digite este código</h1>'
             f'<p class="codigo">{e(login.codigo)}</p>'
             f'<p>em <a class="botao quente" href="{e(login.link)}" target="_blank" rel="noopener">{e(login.link)}</a></p>'
             '<p class="sub">Entre com a conta Microsoft. Esta página se atualiza sozinha.</p>')
    return tela(request, "Contas Microsoft", corpo, atualizar=4)


async def microsoft_remover(request):
    nome = ((await request.post()).get("nome") or "").strip()
    if nome_ok(nome):
        request.app["contas"].remover_microsoft(nome)
        request.app[REINICIAR] = True
    raise web.HTTPSeeOther("/microsoft")


# ================================================================ preferências
FUSOS = ["America/Sao_Paulo", "America/Manaus", "America/Belem", "America/Fortaleza", "America/Recife",
         "America/Cuiaba", "America/Porto_Velho", "America/Rio_Branco", "America/Noronha"]


async def preferencias(request):
    aviso, ruim = "", False
    if request.method == "POST":
        f = await request.post()
        fuso = f.get("fuso", "")
        ini, fim = (f.get("inicio") or "").strip(), (f.get("fim") or "").strip()
        valido = lambda h: not h or (len(h) == 5 and h[2] == ":" and h[:2].isdigit() and h[3:].isdigit()
                                     and int(h[:2]) < 24 and int(h[3:]) < 60)
        if fuso not in FUSOS or not (valido(ini) and valido(fim)) or bool(ini) != bool(fim):
            aviso, ruim = "Confira: horários no formato 23:00, os dois preenchidos ou os dois vazios.", True
        else:
            def mudar(d):
                d["timezone"] = fuso
                if ini:
                    d["quiet_hours"] = {"inicio": ini, "fim": fim}
                else:
                    d.pop("quiet_hours", None)
            arquivos.gravar_config(CONFIG, mudar)
            request.app[REINICIAR] = True
            aviso = "Salvo."
    cfg = arquivos.ler_config(CONFIG)
    q = cfg.get("quiet_hours") or {}
    atual = cfg.get("timezone", "America/Sao_Paulo")
    opcoes = "".join(f'<option {"selected" if f == atual else ""}>{f}</option>' for f in FUSOS)
    corpo = f"""<h1>Preferências</h1><form method="post"><label for="f-fuso">Fuso horário</label><select id="f-fuso" name="fuso" style="font:600 17px Txt,system-ui;padding:12px;border:3px solid #141414;border-radius:6px;width:100%">{opcoes}</select>
<h2>Horário de silêncio</h2><p class="ajuda">Nesse intervalo ele não manda mensagem comum — guarda para depois. Alertas críticos furam.</p>
<label for="f-inicio">Começa</label><input id="f-inicio" type="text" name="inicio" value="{e(q.get('inicio', ''))}" placeholder="23:00">
<label for="f-fim">Termina</label><input id="f-fim" type="text" name="fim" value="{e(q.get('fim', ''))}" placeholder="07:00">
<div class="botoes"><button>Salvar</button></div></form>"""
    return tela(request, "Preferências", corpo, aviso=aviso, aviso_ruim=ruim)


# ================================================================ palavra de ativação
async def voz(request):
    cal: Calibracao = request.app["calibracao"]
    cfg = arquivos.ler_config(CONFIG).get("voice") or {}
    atual = float(cfg.get("wake_threshold", 0.5))
    modelo = cfg.get("wake_model") or "hey jarvis (padrão)"
    trechos = cal.trechos()
    sug = cal.sugerir(atual)
    corpo = (f'<h1>Palavra de ativação</h1><p class="sub">Modelo: <b>{e(modelo)}</b> · limiar atual <b>{atual:.2f}</b>. '
             'Toda vez que ele acorda — ou quase — guarda os 2 s de áudio aqui, só neste aparelho. '
             'Ouça e diga se era você chamando: é assim que ele aprende a sua sala.</p>')
    if sug:
        corpo += (f'<div class="aviso bom">Pelas suas marcações, o melhor limiar é <b>{sug["limiar"]:.2f}</b>: '
                  f'acorda em {sug["acertos"]} de {sug["chamadas"]} chamadas suas e em {sug["falsos"]} '
                  f'de {sug["nao_marcados"]} sons que não eram você.'
                  f'<form method="post" action="/voz/limiar" style="margin-top:10px">'
                  f'<input type="hidden" name="limiar" value="{sug["limiar"]:.2f}">'
                  f'<button class="quente">Usar {sug["limiar"]:.2f}</button></form></div>')
    else:
        corpo += ('<div class="aviso ruim">Chame ele umas 10 vezes, do jeito que você fala no dia a dia, '
                  'e marque abaixo. Com 3 chamadas marcadas já dá para sugerir um limiar.</div>')
    if not trechos:
        corpo += '<p class="vazio">Nada gravado ainda.</p>'
    for t in trechos:
        selo = '<span class="selo ok">ACORDOU</span>' if t["acordou"] else '<span class="selo nao">QUASE</span>'
        botoes = "".join(
            f'<button name="rotulo" value="{v}" class="{"" if t["rotulo"] == v else "leve"}">{r}</button>'
            for v, r in (("eu", "Era eu"), ("nao", "Não era")))
        corpo += (f'<div class="linha" style="flex-wrap:wrap"><span class="x"><span class="t">{t["nota"]:.2f}</span> '
                  f'<span class="d">{e(t["quando"])}</span></span>{selo}'
                  f'<audio controls preload="none" src="/voz/audio/{e(t["arquivo"])}" style="width:100%"></audio>'
                  f'<form method="post" action="/voz/rotular" class="botoes" style="margin-top:6px">'
                  f'<input type="hidden" name="arquivo" value="{e(t["arquivo"])}">{botoes}</form></div>')
    return tela(request, "Palavra de ativação", corpo)


async def voz_rotular(request):
    f = await request.post()
    request.app["calibracao"].rotular(f.get("arquivo", ""), f.get("rotulo", ""))
    raise web.HTTPSeeOther("/voz")


async def voz_audio(request):
    arq = request.app["calibracao"].caminho(request.match_info["arquivo"])
    if not arq:
        raise web.HTTPNotFound()
    return web.FileResponse(arq, headers={"Content-Type": "audio/wav", "Cache-Control": "no-store"})


async def voz_limiar(request):
    try:
        limiar = round(float((await request.post()).get("limiar", "")), 2)
    except ValueError:
        raise web.HTTPSeeOther("/voz")
    if 0.1 <= limiar <= 0.99:
        arquivos.gravar_config(CONFIG, lambda d: d.setdefault("voice", {}).__setitem__("wake_threshold", limiar))
        request.app[REINICIAR] = True
    raise web.HTTPSeeOther("/voz")


# ================================================================ app
async def fonte(request):
    nome = request.match_info["nome"]
    arquivo = FONTES / nome
    if "/" in nome or not nome.endswith(".otf") or not arquivo.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(arquivo, headers={"Cache-Control": "public, max-age=604800"})


def criar_app() -> web.Application:
    app = web.Application(middlewares=[porteiro], client_max_size=300_000)
    app["guarda"] = Guarda(ESTADO / "painel.json")
    app["contas"] = Contas(ESTADO)
    app["calibracao"] = Calibracao(ESTADO / "calibracao")
    app.add_routes([
        web.route("*", "/primeiro-acesso", primeiro_acesso),
        web.route("*", "/entrar", entrar),
        web.post("/sair", sair),
        web.get("/", inicio),
        web.post("/reiniciar", reiniciar),
        web.route("*", "/c/{id}", integracao),
        web.post("/c/{id}/testar", testar),
        web.post("/c/telegram/id", telegram_id),
        web.route("*", "/google", google),
        web.post("/google/conectar", google_conectar),
        web.post("/google/finalizar", google_finalizar),
        web.post("/google/remover", google_remover),
        web.route("*", "/microsoft", microsoft),
        web.post("/microsoft/conectar", microsoft_conectar),
        web.get("/microsoft/espera", microsoft_espera),
        web.post("/microsoft/remover", microsoft_remover),
        web.route("*", "/preferencias", preferencias),
        web.get("/voz", voz),
        web.post("/voz/rotular", voz_rotular),
        web.get("/voz/audio/{arquivo}", voz_audio),
        web.post("/voz/limiar", voz_limiar),
        web.get("/fontes/{nome}", fonte),
    ])
    return app


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")
    log.info("painel do Guará na porta %d (raiz %s)", PORTA, RAIZ)
    web.run_app(criar_app(), host="0.0.0.0", port=PORTA, access_log=None, print=None)


if __name__ == "__main__":
    main()
