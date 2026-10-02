"""A cara do painel: a identidade editorial do Guará, em HTML puro.

Sem framework e sem JavaScript: o notebook velho serve isto sem esforço e
o celular abre na hora. Todo texto variável passa por `e()` (escape).
"""

from __future__ import annotations

from html import escape

from ..face import CABECA, FOCINHO


def e(texto) -> str:
    return escape(str(texto), quote=True)


def _pontos(forma, s=10, dy=0.26) -> str:
    return " ".join(f"{x * s + 10:.2f},{(y + dy) * s + 16:.2f}" for x, y in forma)


MARCA = (f'<svg class="marca" viewBox="0 0 20 30" aria-hidden="true">'
         f'<polygon points="{_pontos(CABECA)}" fill="#FF5A1F"/>'
         f'<polygon points="{_pontos(FOCINHO)}" fill="#141414"/>'
         f'<circle cx="{.33 * 10 + 10:.2f}" cy="{(.16 + .26 - .26) * 10 + 16:.2f}" r="1.4" fill="#141414"/></svg>')

CSS = """
@font-face{font-family:Disp;src:url(/fontes/InterDisplay-Black.otf);font-weight:900}
@font-face{font-family:Txt;src:url(/fontes/Inter-SemiBold.otf);font-weight:600}
@font-face{font-family:Txt;src:url(/fontes/Inter-Bold.otf);font-weight:800}
:root{--papel:#EEEAE1;--tinta:#141414;--laranja:#FF5A1F;--cinza:#8C877D;--verde:#1E9E57;--vermelho:#E5484D}
*{box-sizing:border-box}
body{margin:0;background:var(--papel);color:var(--tinta);font:600 17px/1.45 Txt,system-ui,-apple-system,sans-serif}
a{color:inherit}
.topo{display:flex;align-items:center;gap:12px;padding:20px 22px;border-bottom:3px solid var(--tinta)}
.topo a{text-decoration:none;display:flex;align-items:center;gap:10px}
.marca{width:26px;height:39px}
.nome{font:900 30px Disp,system-ui,sans-serif;letter-spacing:-1px}
.topo form{margin-left:auto}
main{max-width:720px;margin:0 auto;padding:26px 22px 80px}
h1{font:900 clamp(40px,9vw,64px)/1 Disp,system-ui,sans-serif;letter-spacing:-2px;margin:10px 0 8px}
h2{font:900 26px Disp,system-ui,sans-serif;letter-spacing:-.5px;margin:34px 0 10px}
.sub{color:var(--cinza);margin:0 0 26px}
.rotulo{font:800 12px/1 ui-monospace,Menlo,monospace;letter-spacing:2px;text-transform:uppercase;color:var(--cinza)}
.linha{display:flex;align-items:center;gap:14px;padding:16px 0;border-top:3px solid var(--tinta);text-decoration:none}
.linha:last-child{border-bottom:3px solid var(--tinta)}
.linha .t{font:800 21px Txt,system-ui,sans-serif}
.linha .d{color:var(--cinza);font-size:15px}
.linha .x{flex:1}
.selo{font:800 12px ui-monospace,Menlo,monospace;letter-spacing:1px;padding:6px 10px;border-radius:4px;white-space:nowrap}
.ok{background:var(--tinta);color:var(--papel)}
.nao{border:2px solid var(--tinta)}
.falta{background:var(--laranja);color:var(--tinta)}
ol.passos{padding-left:22px;margin:0 0 24px}ol.passos li{margin:6px 0}
label{display:block;margin:18px 0 6px;font-weight:800}
.ajuda{color:var(--cinza);font-size:14px;margin:4px 0 0}
input[type=text],input[type=password],textarea{width:100%;font:600 17px ui-monospace,Menlo,monospace;padding:12px;border:3px solid var(--tinta);border-radius:6px;background:#fff;color:var(--tinta)}
textarea{min-height:110px}
input:focus,textarea:focus{outline:3px solid var(--laranja);outline-offset:1px}
.botoes{display:flex;flex-wrap:wrap;gap:10px;margin-top:22px}
button,.botao{font:800 17px Txt,system-ui,sans-serif;padding:13px 20px;border-radius:6px;border:3px solid var(--tinta);background:var(--tinta);color:var(--papel);cursor:pointer;text-decoration:none;display:inline-block}
button.leve,.botao.leve{background:transparent;color:var(--tinta)}
button.quente{background:var(--laranja);border-color:var(--laranja);color:var(--tinta)}
.aviso{padding:14px 16px;border-radius:6px;margin:0 0 22px;font-weight:800}
.aviso.bom{background:var(--tinta);color:var(--papel)}
.aviso.ruim{background:var(--laranja);color:var(--tinta)}
.faixa{background:var(--laranja);padding:14px 22px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.faixa b{flex:1}
.codigo{font:900 46px Disp,ui-monospace,monospace;letter-spacing:4px;margin:10px 0}
code{background:#fff;padding:1px 6px;border-radius:4px;font-size:.9em}
.vazio{color:var(--cinza)}
"""


def pagina(titulo: str, corpo: str, *, logado: bool = True, aviso: str = "", aviso_ruim: bool = False,
           reiniciar: bool = False, atualizar: int = 0) -> str:
    refresh = f'<meta http-equiv="refresh" content="{atualizar}">' if atualizar else ""
    sair = ('<form method="post" action="/sair"><button class="leve">Sair</button></form>' if logado else "")
    faixa = ('<div class="faixa"><b>Mudou alguma coisa. Reinicie o Guará para valer.</b>'
             '<form method="post" action="/reiniciar"><button>Reiniciar agora</button></form></div>'
             if reiniciar else "")
    caixa = f'<div class="aviso {"ruim" if aviso_ruim else "bom"}">{aviso}</div>' if aviso else ""
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">{refresh}
<title>{e(titulo)} · Guará</title><style>{CSS}</style></head><body>
<header class="topo"><a href="/">{MARCA}<span class="nome">Guará</span></a>{sair}</header>
{faixa}<main>{caixa}{corpo}</main></body></html>"""
