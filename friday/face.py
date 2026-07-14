"""O rosto do JARVIS: orbe animado renderizado nativamente com pygame.

Leve o bastante para um notebook velho (~50 MB, sem navegador): desenha o
orbe estilo reator, legendas e um HUD discreto nos cantos, lendo os estados
publicados pelo processo principal em state/face_state.json.

Uso:
    python -m friday.face             # tela cheia (kiosk)
    python -m friday.face --window    # janela (testes)
    python -m friday.face --demo      # cicla os estados sozinho (preview)

No servidor sem desktop: SDL_VIDEODRIVER=kmsdrm python -m friday.face
No horário de silêncio (quiet_hours) a tela vira só um relógio fraco.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import face_state
from .config import load_config
from .notifier import Notifier

FUNDO = (6, 13, 20)
CIANO = (90, 200, 230)
CIANO_FRACO = (77, 122, 138)
CIANO_MEDIO = (127, 212, 232)

DEMO_CICLO = [("idle", "Às ordens, senhor."), ("listening", "Ouvindo…"),
              ("thinking", "Processando…"),
              ("speaking", "“O tempo está firme; sem chuva até domingo, senhor.”")]


def _dim(cor, fator):
    return tuple(int(c * fator) for c in cor)


class Face:
    def __init__(self, window: bool, demo: bool):
        import os

        # O rosto não usa som — deixa o dispositivo de áudio livre para a voz.
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
        import pygame

        self.pg = pygame
        self.config = load_config()
        self.quiet = Notifier(self.config, None)
        self.demo = demo
        pygame.init()
        pygame.display.set_caption("JARVIS")
        flags = 0 if window else pygame.FULLSCREEN
        size = (960, 540) if window else (0, 0)
        self.screen = pygame.display.set_mode(size, flags)
        pygame.mouse.set_visible(window)
        self.w, self.h = self.screen.get_size()
        base = max(16, self.h // 34)
        mono = "menlo,monaco,dejavusansmono,liberationmono,monospace"
        self.font_hud = pygame.font.SysFont(mono, base)
        self.font_legenda = pygame.font.SysFont(mono, int(base * 1.15))
        self.font_relogio_noturno = pygame.font.SysFont(mono, base * 4)
        self.clock = pygame.time.Clock()

    # ---- desenho dos estados ----

    def _orbe(self, estado: str, t: float):
        pg = self.pg
        cx, cy = self.w // 2, int(self.h * 0.44)
        raio = int(self.h * 0.16)

        if estado == "speaking":
            n, gap = 5, max(6, raio // 8)
            larg = max(4, raio // 11)
            total = n * larg + (n - 1) * gap
            for i in range(n):
                fase = math.sin(t * (2 * math.pi) / (0.5 + i * 0.07))
                alt = int(raio * (0.35 + 0.65 * (0.5 + 0.5 * fase)) * (0.5 + 0.5 * math.sin(i * 1.3 + 1)))
                alt = max(int(raio * 0.15), alt)
                x = cx - total // 2 + i * (larg + gap)
                rect = pg.Rect(x, cy - alt, larg, alt * 2)
                pg.draw.rect(self.screen, CIANO, rect, border_radius=larg // 2)
            pg.draw.circle(self.screen, _dim(CIANO, 0.35), (cx, cy), raio, 2)
            return

        pg.draw.circle(self.screen, _dim(CIANO, 0.35), (cx, cy), raio, 2)

        if estado == "listening":
            fase = (t % 1.1) / 1.1
            r = int(raio * (0.9 + 0.6 * fase))
            pg.draw.circle(self.screen, _dim(CIANO, 0.9 * (1 - fase)), (cx, cy), r, 2)
            nucleo = int(raio * 0.45 * (1 + 0.18 * math.sin(t * 2 * math.pi / 1.2)))
            pg.draw.circle(self.screen, CIANO, (cx, cy), nucleo)
        elif estado == "thinking":
            seg, r = 12, int(raio * 0.72)
            ang0 = t * 2 * math.pi / 1.6
            for i in range(seg):
                if i % 2:
                    continue
                a1 = ang0 + i * 2 * math.pi / seg
                a2 = a1 + 1.2 * math.pi / seg
                pontos = [(cx + r * math.cos(a), cy + r * math.sin(a))
                          for a in (a1, (a1 + a2) / 2, a2)]
                pg.draw.lines(self.screen, CIANO_MEDIO, False, pontos, 3)
            pg.draw.circle(self.screen, _dim(CIANO, 0.8), (cx, cy), int(raio * 0.3))
        else:  # idle
            nucleo = int(raio * 0.42 * (1 + 0.12 * math.sin(t * 2 * math.pi / 3.5)))
            pg.draw.circle(self.screen, _dim(CIANO, 0.9), (cx, cy), nucleo)
            pg.draw.circle(self.screen, _dim(CIANO, 0.55), (cx, cy), int(raio * 0.72), 2)

    def _texto(self, fonte, texto, cor, pos, ancora):
        surf = fonte.render(texto, True, cor)
        rect = surf.get_rect(**{ancora: pos})
        self.screen.blit(surf, rect)

    DIAS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
    MESES = ["jan", "fev", "mar", "abr", "mai", "jun",
             "jul", "ago", "set", "out", "nov", "dez"]

    def _hud(self, dados: dict):
        agora = datetime.now(ZoneInfo(self.config.timezone))
        relogio = (f"{agora:%H:%M} · {self.DIAS[agora.weekday()]}, "
                   f"{agora.day} {self.MESES[agora.month - 1]}")
        m = int(self.h * 0.035)
        self._texto(self.font_hud, relogio, CIANO_MEDIO, (m, m), "topleft")
        if dados.get("clima"):
            self._texto(self.font_hud, dados["clima"], CIANO_MEDIO, (self.w - m, m), "topright")
        if dados.get("agenda"):
            self._texto(self.font_hud, dados["agenda"], CIANO_FRACO, (m, self.h - m), "bottomleft")
        monitores = dados.get("monitores", "monitores ok")
        self._texto(self.font_hud, monitores, CIANO_FRACO, (self.w - m, self.h - m), "bottomright")

    def _legenda(self, texto: str):
        if not texto:
            return
        max_chars = max(30, self.w // (self.font_legenda.size("x")[0] + 1) - 8)
        if len(texto) > max_chars:
            texto = texto[: max_chars - 1] + "…"
        self._texto(self.font_legenda, texto, CIANO_MEDIO,
                    (self.w // 2, int(self.h * 0.68)), "center")

    def _modo_noturno(self):
        agora = datetime.now(ZoneInfo(self.config.timezone))
        self._texto(self.font_relogio_noturno, agora.strftime("%H:%M"),
                    _dim(CIANO, 0.25), (self.w // 2, self.h // 2), "center")

    # ---- loop principal ----

    def run(self):
        pg = self.pg
        demo_i, demo_t = 0, time.monotonic()
        while True:
            for event in pg.event.get():
                if event.type == pg.QUIT:
                    return
                if event.type == pg.KEYDOWN and event.key in (pg.K_ESCAPE, pg.K_q):
                    return

            if self.demo and time.monotonic() - demo_t > 3:
                demo_i = (demo_i + 1) % len(DEMO_CICLO)
                face_state.publish(*DEMO_CICLO[demo_i])
                demo_t = time.monotonic()

            dados = face_state.read()
            estado = dados.get("state", "idle")
            t = time.monotonic()

            self.screen.fill(FUNDO)
            if self.quiet.is_quiet() and estado == "idle" and not self.demo:
                self._modo_noturno()
            else:
                self._orbe(estado, t)
                self._legenda(dados.get("caption", ""))
                self._hud(dados.get("hud", {}))
            pg.display.flip()
            self.clock.tick(30)


def main():
    parser = argparse.ArgumentParser(prog="friday.face")
    parser.add_argument("--window", action="store_true", help="janela em vez de tela cheia")
    parser.add_argument("--demo", action="store_true", help="cicla os estados (preview)")
    parser.add_argument("--frames", type=int, default=0, help="sai após N frames (teste)")
    args = parser.parse_args()

    try:
        import pygame  # noqa: F401
    except ImportError:
        sys.exit("pygame não instalado. Rode: pip install -e '.[face]'")

    face = Face(window=args.window, demo=args.demo)
    if args.frames:
        for _ in range(args.frames):
            face.screen.fill(FUNDO)
            face._orbe("idle", time.monotonic())
            face._hud({})
            face.pg.display.flip()
            face.clock.tick(30)
        return
    face.run()


if __name__ == "__main__":
    main()
