"""O rosto do JARVIS na tela da sala, desenhado nativamente com pygame.

Estética editorial (a mesma dos vídeos): cor chapada, tipografia enorme,
nada de brilho, degradê ou partícula. Cada estado tem o seu fundo, e a troca
entre eles é o fundo novo varrendo a tela de baixo para cima:

    repouso   papel  — relógio grande, clima, agenda, marca pulsando
    ouvindo   laranja — "Pode falar." (dá para ver do outro lado da sala)
    pensando  tinta  — "Um instante." e três blocos laranja
    falando   tinta  — a frase, grande, aparecendo no ritmo da fala
    noite     tinta  — só o relógio, quase apagado (quiet_hours)

Leve para o notebook velho: texto renderizado fica em cache, e em repouso a
tela redesenha poucas vezes por segundo.

Uso:
    python -m friday.face             # tela cheia (kiosk)
    python -m friday.face --window    # janela (testes)
    python -m friday.face --demo      # cicla os estados sozinho (preview)
    python -m friday.face --foto DIR  # salva um PNG de cada estado e sai

No servidor sem desktop: SDL_VIDEODRIVER=kmsdrm python -m friday.face
Fonte: Inter (apt install fonts-inter); sem ela, cai na DejaVu.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import face_state
from .config import load_config
from .notifier import Notifier

PAPEL = (238, 234, 225)
TINTA = (20, 20, 20)
LARANJA = (255, 90, 31)
CINZA = (140, 135, 125)
NOITE = (58, 56, 51)

FUNDO = {"idle": PAPEL, "listening": LARANJA, "thinking": TINTA, "speaking": TINTA, "night": TINTA}
VARRE = 0.3            # segundos da troca de fundo
ENTRA = 0.4            # segundos do texto subindo pela máscara
LETRAS_POR_SEG = 16    # ritmo em que a frase aparece enquanto ele fala

PASTAS_FONTE = [os.environ.get("FRIDAY_FONTES", ""), "/usr/share/fonts/opentype/inter"]
PESOS = {"black": "InterDisplay-Black.otf", "extra": "InterDisplay-ExtraBold.otf",
         "bold": "Inter-Bold.otf", "semi": "Inter-SemiBold.otf"}

DEMO_CICLO = [("idle", ""), ("listening", "Ouvindo…"), ("thinking", "Processando…"),
              ("speaking", "O tempo está firme: sem chuva até domingo. Quer que eu deixe a sala a 23 graus?")]
DEMO_HUD = {"clima": "24° · céu limpo", "agenda": "14:00 Reunião | 17:00 Dentista | 19:30 Treino",
            "monitores": "monitores ok"}

DIAS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
DIAS_CURTOS = ["SEG", "TER", "QUA", "QUI", "SEX", "SÁB", "DOM"]
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]


def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def prog(t, a, b):
    return clamp((t - a) / (b - a))


def e_out(k):
    return 1 - (1 - k) ** 4


def e_in_out(k):
    return 8 * k ** 4 if k < .5 else 1 - (-2 * k + 2) ** 4 / 2


def mistura(c1, c2, k):
    """Cor intermediária — é assim que se "esmaece" sem transparência."""
    return tuple(int(a + (b - a) * clamp(k)) for a, b in zip(c1, c2))


class Face:
    def __init__(self, window: bool, demo: bool, tamanho: tuple[int, int] | None = None):
        # O rosto não usa som — deixa o dispositivo de áudio livre para a voz.
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
        import pygame

        self.pg = pygame
        self.config = load_config()
        self.quiet = Notifier(self.config, None)
        self.demo = demo
        pygame.init()
        pygame.display.set_caption("JARVIS")
        if tamanho:
            self.screen = pygame.display.set_mode(tamanho)
        else:
            flags = 0 if window else pygame.FULLSCREEN
            self.screen = pygame.display.set_mode((1366, 768) if window else (0, 0), flags)
        pygame.mouse.set_visible(window)
        self.w, self.h = self.screen.get_size()
        self.m = int(self.h * 0.075)
        self.clock = pygame.time.Clock()
        self._fontes: dict = {}
        self._textos: dict = {}
        self.estado, self.anterior, self.t_troca = "idle", "idle", -10.0
        self._caption_vista = ""

    # ------------------------------------------------------------ fontes
    def fonte(self, peso: str, tam: int):
        chave = (peso, tam)
        if chave not in self._fontes:
            pg = self.pg
            arq = None
            if peso != "mono":
                for pasta in PASTAS_FONTE:
                    if pasta and (Path(pasta) / PESOS[peso]).exists():
                        arq = str(Path(pasta) / PESOS[peso])
                        break
            if arq:
                self._fontes[chave] = pg.font.Font(arq, tam)
            elif peso == "mono":
                self._fontes[chave] = pg.font.SysFont("dejavusansmono,menlo,monospace", tam, bold=True)
            else:
                self._fontes[chave] = pg.font.SysFont("dejavusans,helveticaneue,arial", tam, bold=True)
        return self._fontes[chave]

    def texto(self, peso: str, tam: int, s: str, cor) -> "pygame.Surface":
        chave = (peso, tam, s, cor)
        surf = self._textos.get(chave)
        if surf is None:
            if len(self._textos) > 300:
                self._textos.clear()
            surf = self._textos[chave] = self.fonte(peso, tam).render(s, True, cor)
        return surf

    def sobe(self, surf, x: int, y: int, k: float):
        """Texto entrando por máscara: o gesto tipográfico da identidade."""
        if k <= 0:
            return
        r = surf.get_rect(topleft=(x, y))
        self.screen.set_clip(r)
        self.screen.blit(surf, (x, y + int((1 - e_out(k)) * r.height)))
        self.screen.set_clip(None)

    def marca(self, cx: int, cy: int, r: int, t: float, fundo=TINTA, ponto=LARANJA):
        pg = self.pg
        pg.draw.circle(self.screen, fundo, (cx, cy), r)
        pulso = math.exp(-6 * (t % 2.0))
        pg.draw.circle(self.screen, ponto, (int(cx + r * .38), int(cy - r * .38)), max(2, int(r * (.2 + .06 * pulso))))

    def quebra(self, peso: str, tam: int, s: str, largura: int) -> list[str]:
        f, linhas, atual = self.fonte(peso, tam), [], ""
        for palavra in s.split():
            teste = f"{atual} {palavra}".strip()
            if f.size(teste)[0] > largura and atual:
                linhas.append(atual)
                atual = palavra
            else:
                atual = teste
        return linhas + ([atual] if atual else [])

    # ------------------------------------------------------------ estados
    def repouso(self, t: float, k: float, hud: dict):
        pg, w, h, m = self.pg, self.w, self.h, self.m
        agora = datetime.now(ZoneInfo(self.config.timezone))
        r = int(h * .036)
        self.marca(m + r, m + r, r, t)
        self.screen.blit(self.texto("black", int(h * .046), "JARVIS", TINTA), (m + 2 * r + 18, m + r - int(h * .03)))
        data = self.texto("mono", int(h * .026), f"{DIAS_CURTOS[agora.weekday()]} · {agora.day} {MESES[agora.month - 1][:3].upper()}", TINTA)
        self.screen.blit(data, data.get_rect(topright=(w - m, m + r - data.get_height() // 2)))

        relogio = self.texto("black", int(h * .33), agora.strftime("%H:%M"), TINTA)
        self.sobe(relogio, m - int(h * .012), int(h * .19), k)
        linha2 = hud.get("clima") or f"{DIAS[agora.weekday()]}, {agora.day} de {MESES[agora.month - 1]}"
        self.sobe(self.texto("bold", int(h * .056), linha2, TINTA), m, int(h * .6), prog(k, .2, 1))
        x0 = max(int(w * .62), m + relogio.get_width() + int(h * .06))

        # coluna da agenda (nunca por baixo do relógio)
        self.screen.blit(self.texto("mono", int(h * .024), "AGENDA", CINZA), (x0, int(h * .2)))
        itens = [i.strip() for i in (hud.get("agenda") or "").replace(";", "|").split("|") if i.strip()][:3]
        if not itens:
            itens = ["Nada marcado."]
        y = int(h * .26)
        for i, item in enumerate(itens):
            pg.draw.rect(self.screen, TINTA, (x0, y, w - m - x0, 3))
            linhas = self.quebra("bold", int(h * .042), item, w - m - x0)[:2]
            for j, l in enumerate(linhas):
                self.sobe(self.texto("bold", int(h * .042), l, TINTA if itens[0] != "Nada marcado." else CINZA),
                          x0, y + int(h * .025) + j * int(h * .05), prog(k, .15 + i * .12, .8 + i * .12))
            y += int(h * .05) * len(linhas) + int(h * .05)

        # rodapé: linha e status; monitor em alerta vira bloco laranja
        yb = h - m - int(h * .05)
        pg.draw.rect(self.screen, TINTA, (m, yb, w - 2 * m, 3))
        on = self.texto("mono", int(h * .024), "ONLINE", TINTA)
        pg.draw.circle(self.screen, TINTA, (m + 6, yb + int(h * .025) + on.get_height() // 2), 6)
        self.screen.blit(on, (m + 20, yb + int(h * .025)))
        mon = hud.get("monitores", "monitores ok")
        if mon.startswith("⚠"):
            s = self.texto("mono", int(h * .026), "ATENÇÃO · " + mon.lstrip("⚠ ").upper(), TINTA)
            caixa = s.get_rect(topright=(w - m, yb + int(h * .02))).inflate(28, 16)
            pg.draw.rect(self.screen, LARANJA, caixa, border_radius=4)
            self.screen.blit(s, s.get_rect(center=caixa.center))
        else:
            s = self.texto("mono", int(h * .024), "MONITORES OK", CINZA)
            self.screen.blit(s, s.get_rect(topright=(w - m, yb + int(h * .025))))

    def ouvindo(self, t: float, k: float):
        pg, w, h, m = self.pg, self.w, self.h, self.m
        r = int(h * .15)
        cx, cy = w - m - int(r * 1.8), h // 2
        for i in range(3):
            fase = (t * .9 + i / 3) % 1
            pg.draw.circle(self.screen, mistura(TINTA, LARANJA, fase), (cx, cy), int(r * (1 + fase * .9)), 5)
        self.marca(cx, cy, r, t, TINTA, PAPEL)
        tam = int(h * .19)
        self.sobe(self.texto("black", tam, "Pode", TINTA), m, int(h * .26), k)
        self.sobe(self.texto("black", tam, "falar.", TINTA), m, int(h * .26) + int(tam * 1.02), prog(k, .15, 1))
        self.screen.blit(self.texto("mono", int(h * .026), "OUVINDO", TINTA), (m, h - m - int(h * .03)))

    def pensando(self, t: float, k: float):
        pg, h, m = self.pg, self.h, self.m
        r = int(h * .036)
        self.marca(m + r, m + r, r, t, PAPEL, LARANJA)
        tam = int(h * .17)
        self.sobe(self.texto("black", tam, "Um instante.", PAPEL), m, int(h * .34), k)
        lado = int(h * .05)
        for i in range(3):
            pulo = max(0.0, math.sin((t * 5 - i * .7))) * lado * .8
            pg.draw.rect(self.screen, LARANJA, (m + i * int(lado * 1.6), int(h * .7) - int(pulo), lado, lado), border_radius=3)

    def falando(self, t: float, k: float, legenda: str, desde: float):
        pg, w, h, m = self.pg, self.w, self.h, self.m
        r = int(h * .036)
        self.marca(m + r, m + r, r, t, PAPEL, LARANJA)
        rot = self.texto("mono", int(h * .026), "FALANDO", CINZA)
        self.screen.blit(rot, rot.get_rect(topright=(w - m, m + r - rot.get_height() // 2)))
        frase = legenda.strip().strip("“”\"")
        # a maior fonte em que a frase cabe em até 4 linhas
        tam = int(h * .1)
        while tam > int(h * .05):
            linhas = self.quebra("extra", tam, frase, w - 2 * m)
            if len(linhas) <= 4:
                break
            tam -= int(h * .006)
        linhas = self.quebra("extra", tam, frase, w - 2 * m)[:5]
        mostrar = int((t - desde) * LETRAS_POR_SEG) + 1
        y = int(h * .2)
        for l in linhas:
            if mostrar <= 0:
                break
            self.screen.blit(self.texto("extra", tam, l[:mostrar], PAPEL), (m, y))
            mostrar -= len(l) + 1
            y += int(tam * 1.08)
        # equalizador chapado
        n, base = 34, h - m
        larg = (w - 2 * m) / n
        for i in range(n):
            alt = h * .015 + h * .09 * abs(math.sin(t * (3 + (i * 7) % 5) + i * 1.7)) * (.4 + .6 * abs(math.sin(i * .9)))
            pg.draw.rect(self.screen, LARANJA, (int(m + i * larg), int(base - alt), max(2, int(larg * .55)), int(alt)))

    def noite(self):
        agora = datetime.now(ZoneInfo(self.config.timezone))
        s = self.texto("black", int(self.h * .3), agora.strftime("%H:%M"), NOITE)
        self.screen.blit(s, s.get_rect(center=(self.w // 2, self.h // 2)))

    # ------------------------------------------------------------ quadro
    def cena(self, estado: str, t: float, k: float, dados: dict):
        self.screen.fill(FUNDO[estado])
        if estado == "idle":
            self.repouso(t, k, dados.get("hud", {}))
        elif estado == "listening":
            self.ouvindo(t, k)
        elif estado == "thinking":
            self.pensando(t, k)
        elif estado == "speaking":
            self.falando(t, k, dados.get("caption", ""), self.t_troca + VARRE)
        else:
            self.noite()

    def quadro(self, dados: dict, t: float):
        estado = dados.get("state", "idle")
        # Estado velho demais = processo que o publicou já era; volta ao repouso.
        if estado != "idle" and time.time() - dados.get("updated_at", 0) > 90 and not self.demo:
            estado = "idle"
        if estado == "idle" and self.quiet.is_quiet() and not self.demo:
            estado = "night"
        # uma frase nova enquanto fala também recomeça a animação
        if estado != self.estado or (estado == "speaking" and dados.get("caption") != self._caption_vista):
            if estado != self.estado:
                self.anterior = self.estado
            self.estado, self.t_troca = estado, t
            self._caption_vista = dados.get("caption", "")

        dt = t - self.t_troca
        mesmo_fundo = FUNDO[self.anterior] == FUNDO[self.estado]
        if dt < VARRE and not mesmo_fundo:
            self.cena(self.anterior, t, 1.0, dados)
            altura = int(self.h * e_in_out(dt / VARRE))
            self.pg.draw.rect(self.screen, FUNDO[self.estado], (0, self.h - altura, self.w, altura))
        else:
            atraso = 0 if mesmo_fundo else VARRE
            self.cena(self.estado, t, prog(dt, atraso, atraso + ENTRA), dados)
        # animando? quadros cheios; repouso parado? poucos, poupa o Bobcat
        return 30 if (dt < VARRE + ENTRA + .2 or self.estado in ("listening", "thinking", "speaking")) else 8

    def run(self):
        pg = self.pg
        demo_i, demo_t = 0, time.monotonic()
        while True:
            for event in pg.event.get():
                if event.type == pg.QUIT:
                    return
                if event.type == pg.KEYDOWN and event.key in (pg.K_ESCAPE, pg.K_q):
                    return
            if self.demo and time.monotonic() - demo_t > 3.5:
                demo_i = (demo_i + 1) % len(DEMO_CICLO)
                face_state.publish(*DEMO_CICLO[demo_i])
                demo_t = time.monotonic()
            dados = face_state.read()
            if self.demo:
                dados["hud"] = {**DEMO_HUD, **dados.get("hud", {})}
            fps = self.quadro(dados, time.monotonic())
            pg.display.flip()
            self.clock.tick(fps)

    def fotos(self, pasta: str):
        """Um PNG de cada estado, já com a animação de entrada concluída."""
        Path(pasta).mkdir(parents=True, exist_ok=True)
        hud = dict(DEMO_HUD)
        for estado, legenda in [("idle", ""), ("listening", ""), ("thinking", ""),
                                ("speaking", DEMO_CICLO[3][1]), ("night", "")]:
            self.estado, self.anterior, self.t_troca = estado, estado, 0.0
            self._caption_vista = legenda
            self.cena(estado, 12.0, 1.0, {"caption": legenda, "hud": hud})
            self.pg.image.save(self.screen, str(Path(pasta) / f"rosto-{estado}.png"))
        hud["monitores"] = "⚠ fluxo-de-caixa"
        self.cena("idle", 12.0, 1.0, {"hud": hud})
        self.pg.image.save(self.screen, str(Path(pasta) / "rosto-idle-alerta.png"))


def main():
    parser = argparse.ArgumentParser(prog="friday.face")
    parser.add_argument("--window", action="store_true", help="janela em vez de tela cheia")
    parser.add_argument("--demo", action="store_true", help="cicla os estados (preview)")
    parser.add_argument("--foto", metavar="PASTA", help="salva um PNG de cada estado e sai")
    parser.add_argument("--frames", type=int, default=0, help="sai após N frames (teste)")
    args = parser.parse_args()

    try:
        import pygame  # noqa: F401
    except ImportError:
        sys.exit("pygame não instalado. Rode: pip install -e '.[face]'")

    if args.foto:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        Face(window=True, demo=True, tamanho=(1366, 768)).fotos(args.foto)
        return
    face = Face(window=args.window, demo=args.demo)
    if args.frames:
        for _ in range(args.frames):
            face.quadro(face_state.read(), time.monotonic())
            face.pg.display.flip()
            face.clock.tick(30)
        return
    face.run()


if __name__ == "__main__":
    main()
