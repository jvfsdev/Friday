"""Passo 2 de 3 — treina o detector da palavra de ativação.

Treina uma rede pequena em cima dos modelos de áudio do openWakeWord (que
já "sabem ouvir"): ela só aprende a reconhecer a SUA frase. O resultado é
um .onnx de ~200 KB que roda até no notebook velho.

Honestidade na medida: algumas vozes ficam de fora do treino inteiro, e é
nelas que a taxa de acerto é medida — senão o número mede decoreba. Os
disparos falsos são contados por hora, em áudio que o modelo nunca viu, e
cada palavra parecida ("pegadinha") é conferida uma a uma.

Uso:
    .venv-treino/bin/python scripts/palavra/treinar.py --nome guara
Saída: treino-dados/guara/guara.onnx + relatório. Depois: avaliar com a
sua própria voz (calibrar.py) e copiar para o servidor.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

RAIZ = Path(__file__).resolve().parents[2]
SR = 16000
JANELA = 32000          # 2 s de áudio = 16 quadros de embedding (o que o modelo enxerga)
QUADROS = 16
FORA_DO_TREINO = {"Rocko", "Flo", "pt-PT-RaquelNeural", "en-US-BrianMultilingualNeural"}
AUMENTOS_POR_POSITIVO = 6
REFRATARIO = 25         # quadros (2 s): um disparo longo conta uma vez só
# Quantos quadros seguidos acima do limiar para acordar. Ruído e música dão
# picos de um quadro; a palavra de verdade dura vários. O voice.py usa o mesmo.
SEGUIDOS = (1, 2, 3)

rng = np.random.default_rng(7)


# ------------------------------------------------------------------- áudio
def carregar(arq: Path) -> np.ndarray:
    x, sr = sf.read(arq, dtype="float32")
    return x if sr == SR else signal.resample_poly(x, SR, sr).astype("float32")


def sem_silencio(x: np.ndarray) -> np.ndarray:
    energia = np.abs(x) > 0.02 * (np.abs(x).max() + 1e-9)
    idx = np.flatnonzero(energia)
    return x[idx[0]: idx[-1] + 1] if len(idx) else x


def ruido(n: int, cor: str) -> np.ndarray:
    branco = rng.standard_normal(n).astype("float32")
    if cor == "branco":
        return branco
    espectro = np.fft.rfft(branco)
    f = np.maximum(np.fft.rfftfreq(n), 1 / n)
    espectro /= np.sqrt(f) if cor == "rosa" else f
    r = np.fft.irfft(espectro, n).astype("float32")
    return r / (np.abs(r).max() + 1e-9)


def eco(x: np.ndarray) -> np.ndarray:
    rt = rng.uniform(.08, .6)
    n = int(rt * SR)
    rir = rng.standard_normal(n) * np.exp(-6.9 * np.arange(n) / n)
    rir[0] = 1.0
    molhado = signal.fftconvolve(x, rir)[: len(x)]
    molhado /= np.abs(molhado).max() + 1e-9
    mix = rng.uniform(.15, .55)
    return ((1 - mix) * x / (np.abs(x).max() + 1e-9) + mix * molhado).astype("float32")


def bagunca(fala: np.ndarray, fundo: np.ndarray | None) -> np.ndarray:
    """Uma fala limpa vira uma fala "de sala": eco, ruído, mic ruim, volume."""
    x = fala
    if rng.random() < .5:                                       # velocidade (muda o tom junto)
        fator = rng.uniform(.88, 1.12)
        x = signal.resample(x, int(len(x) / fator)).astype("float32")
    if rng.random() < .6:
        x = eco(x)
    if rng.random() < .4:                                       # microfone de notebook
        b, a = signal.butter(4, rng.uniform(3000, 7000) / (SR / 2))
        x = signal.lfilter(b, a, x).astype("float32")
    x = x / (np.abs(x).max() + 1e-9)
    # posiciona: a fala TERMINA perto do fim da janela (é quando o detector decide)
    saida = np.zeros(JANELA, dtype="float32")
    fim = JANELA - int(rng.uniform(.05, .35) * SR)
    ini = max(0, fim - len(x))
    saida[ini:fim] = x[-(fim - ini):]
    # ruído de fundo a um SNR sorteado
    snr = rng.uniform(0, 25)
    if fundo is not None and rng.random() < .5:
        k = int(rng.integers(0, max(1, len(fundo) - JANELA)))
        n = fundo[k: k + JANELA]
        n = np.pad(n, (0, JANELA - len(n)))
    else:
        n = ruido(JANELA, rng.choice(["branco", "rosa", "marrom"]))
    pot_s = np.mean(saida[ini:fim] ** 2) + 1e-9
    pot_n = np.mean(n ** 2) + 1e-9
    saida = saida + n * np.sqrt(pot_s / (pot_n * 10 ** (snr / 10)))
    saida *= 10 ** (rng.uniform(-14, 0) / 20) / (np.abs(saida).max() + 1e-9)
    return saida


def int16(x: np.ndarray) -> np.ndarray:
    return (np.clip(x, -1, 1) * 32767).astype(np.int16)


# --------------------------------------------------------------- features
class Ouvido:
    def __init__(self):
        from openwakeword.utils import AudioFeatures, download_models
        download_models([])
        self.f = AudioFeatures(inference_framework="onnx")

    def janelas(self, clipes: np.ndarray) -> np.ndarray:
        """(N, 32000) int16 → (N, 16, 96): o que o detector vê no instante final."""
        e = self.f.embed_clips(clipes, batch_size=64)
        return e[:, -QUADROS:, :].astype("float32")

    def fluxo(self, audio: np.ndarray) -> np.ndarray:
        """Áudio longo → sequência de quadros de embedding (para medir disparos)."""
        partes = []
        passo = SR * 60
        for i in range(0, len(audio), passo):
            pedaco = int16(audio[max(0, i - 4000): i + passo])
            if len(pedaco) > 4000:
                partes.append(self.f.embed_clips(pedaco[None], batch_size=1)[0])
        return np.concatenate(partes).astype("float32")


def deslizantes(quadros: np.ndarray, passo: int) -> np.ndarray:
    idx = np.arange(0, len(quadros) - QUADROS, passo)
    return np.stack([quadros[i: i + QUADROS] for i in idx])


# ------------------------------------------------------------------ modelo
def construir():
    import torch.nn as nn

    return nn.Sequential(
        nn.Flatten(), nn.Dropout(.1), nn.Linear(QUADROS * 96, 96), nn.LayerNorm(96), nn.ReLU(), nn.Dropout(.3),
        nn.Linear(96, 96), nn.LayerNorm(96), nn.ReLU(), nn.Dropout(.3),
        nn.Linear(96, 1), nn.Sigmoid(),
    )


def treinar(X, y, peso, epocas=16):
    import torch

    disp = "mps" if torch.backends.mps.is_available() else "cpu"
    modelo = construir().to(disp)
    opt = torch.optim.AdamW(modelo.parameters(), lr=1e-3, weight_decay=2e-2)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, epocas)
    Xt, yt, wt = (torch.tensor(a, device=disp) for a in (X, y.astype("float32"), peso.astype("float32")))
    n = len(Xt)
    for ep in range(epocas):
        modelo.train()
        ordem = torch.randperm(n, device=disp)
        total = 0.0
        for i in range(0, n, 512):
            b = ordem[i: i + 512]
            p = modelo(Xt[b]).squeeze(1).clamp(1e-6, 1 - 1e-6)
            perda = -(wt[b] * (yt[b] * torch.log(p) + (1 - yt[b]) * torch.log(1 - p))).mean()
            opt.zero_grad()
            perda.backward()
            opt.step()
            total += perda.item() * len(b)
        sched.step()
        if ep % 10 == 9 or ep == epocas - 1:
            print(f"  época {ep + 1}/{epocas}: perda {total / n:.4f}", flush=True)
    return modelo.cpu().eval()


def pontuar(modelo, X: np.ndarray) -> np.ndarray:
    import torch

    with torch.no_grad():
        return np.concatenate([modelo(torch.tensor(X[i: i + 4096])).squeeze(1).numpy()
                               for i in range(0, len(X), 4096)]) if len(X) else np.array([])


def disparos(pontos: np.ndarray, limiar: float, seguidos: int = 1) -> int:
    n, i, corrida = 0, 0, 0
    while i < len(pontos):
        corrida = corrida + 1 if pontos[i] >= limiar else 0
        if corrida >= seguidos:
            n, corrida = n + 1, 0
            i += REFRATARIO
        else:
            i += 1
    return n


def acertou(notas_seq: np.ndarray, limiar: float, seguidos: int) -> float:
    """Fração de clipes em que a nota fica acima do limiar por N quadros seguidos."""
    ok = 0
    for seq in notas_seq:
        corrida = 0
        for v in seq:
            corrida = corrida + 1 if v >= limiar else 0
            if corrida >= seguidos:
                ok += 1
                break
    return ok / max(1, len(notas_seq))


# -------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nome", required=True)
    ap.add_argument("--max-fa-hora", type=float, default=0.5, help="disparos falsos aceitáveis por hora")
    ap.add_argument("--minhas", type=Path, help="pasta state/calibracao copiada do servidor, com rotulos.json "
                    "(marcados no painel): a sua voz, o seu microfone, a sua sala")
    a = ap.parse_args()
    pasta = RAIZ / "treino-dados" / a.nome
    t0 = time.time()
    voz = lambda p: p.name.split("__")[0]

    pos = sorted((pasta / "positivos").glob("*.wav"))
    neg = sorted((pasta / "negativos").glob("*.wav"))
    pos_tr = [p for p in pos if voz(p) not in FORA_DO_TREINO]
    pos_te = [p for p in pos if voz(p) in FORA_DO_TREINO]
    neg_tr = [p for p in neg if voz(p) not in FORA_DO_TREINO]
    neg_te = [p for p in neg if voz(p) in FORA_DO_TREINO]
    print(f"{len(pos)} positivas ({len(pos_te)} de vozes fora do treino), {len(neg)} negativas", flush=True)

    ouvido = Ouvido()
    # fala comum em português, em fluxo, para servir de fundo e de negativo
    fala_tr = np.concatenate([np.concatenate([carregar(p), np.zeros(int(SR * rng.uniform(.2, 1)), "float32")])
                              for p in neg_tr])

    print("preparando positivos (com bagunça de sala)…", flush=True)
    clipes = [int16(bagunca(sem_silencio(carregar(p)), fala_tr)) for p in pos_tr for _ in range(AUMENTOS_POR_POSITIVO)]
    Xp = ouvido.janelas(np.stack(clipes))

    print("preparando negativos…", flush=True)
    # 1) frases e pegadinhas, também bagunçadas (o detector vai ouvi-las na sala)
    clipes = [int16(bagunca(sem_silencio(carregar(p)), None)) for p in neg_tr for _ in range(3)]
    Xn_frases = ouvido.janelas(np.stack(clipes))
    pegadinha = np.array(["__peg__" in p.name for p in neg_tr for _ in range(3)])
    # 2) a fala corrida em português, janela a janela
    Xn_fluxo = deslizantes(ouvido.fluxo(fala_tr), 4)
    # 3) as ~11 h do openWakeWord (fala, música, ruído); o fim fica para a prova
    oww = np.load(RAIZ / "treino-dados" / "negativos_oww.npy", mmap_mode="r")
    corte = int(len(oww) * .85)
    Xn_oww = deslizantes(np.asarray(oww[:corte]), 3)

    # 4) a calibração de verdade, se houver: o que a pessoa marcou no painel
    Xn_minhas = np.zeros((0, QUADROS, 96), "float32")
    if a.minhas:
        rotulos = json.loads((a.minhas / "rotulos.json").read_text())
        eu = [a.minhas / f for f, r in rotulos.items() if r == "eu" and (a.minhas / f).exists()]
        nao = [a.minhas / f for f, r in rotulos.items() if r == "nao" and (a.minhas / f).exists()]
        # os trechos já terminam no instante em que ele acordou: é só pegar os últimos 2 s
        fim = lambda p: np.pad(carregar(p), (JANELA, 0))[-JANELA:]
        if eu:
            minhas = [int16(_leve(fim(p))) for p in eu for _ in range(10)]
            Xp = np.concatenate([Xp, ouvido.janelas(np.stack(minhas))])
        if nao:
            Xn_minhas = ouvido.janelas(np.stack([int16(fim(p)) for p in nao for _ in range(3)]))
        print(f"calibração: {len(eu)} chamadas suas e {len(nao)} sons que não eram você", flush=True)

    X = np.concatenate([Xp, Xn_frases, Xn_fluxo, Xn_oww, Xn_minhas])
    y = np.concatenate([np.ones(len(Xp)), np.zeros(len(Xn_frases) + len(Xn_fluxo) + len(Xn_oww) + len(Xn_minhas))])
    # pegadinhas pesam mais: é exatamente onde o detector não pode errar
    peso = np.concatenate([np.full(len(Xp), 1.0), np.where(pegadinha, 6.0, 2.0),
                           np.full(len(Xn_fluxo), 1.5), np.full(len(Xn_oww), 1.0), np.full(len(Xn_minhas), 6.0)])
    peso[: len(Xp)] *= (len(X) - len(Xp)) / len(Xp) * .5      # equilibra as classes
    print(f"treinando com {len(Xp)} positivos e {len(X) - len(Xp)} negativos…", flush=True)
    X = X.astype("float32")
    modelo = treinar(X, y, peso)

    # Rodadas em cima dos próprios erros: os negativos em que ele quase
    # acordou voltam com peso maior. É o que mais derruba disparo falso.
    for rodada in (1,):
        notas = pontuar(modelo, X[len(Xp):])
        dificeis = np.flatnonzero(notas > .2) + len(Xp)
        print(f"rodada {rodada}: {len(dificeis)} negativos difíceis (nota > 0,2)", flush=True)
        if not len(dificeis):
            break
        X = np.concatenate([X, np.repeat(X[dificeis], 3, axis=0)])
        y = np.concatenate([y, np.zeros(len(dificeis) * 3)])
        peso = np.concatenate([peso, np.full(len(dificeis) * 3, 2.0)])
        modelo = treinar(X, y, peso)

    # ----------------------------------------------------------- a prova
    print("avaliando em vozes e áudio que o modelo nunca viu…", flush=True)
    # cada positivo ganha 0,8 s depois da fala: é nesse trecho que o detector
    # decide, e a regra dos "quadros seguidos" precisa da sequência de notas
    def depois(x, sujo):
        cauda = (ruido(int(.8 * SR), "rosa") * .01) if sujo else np.zeros(int(.8 * SR), "float32")
        return int16(np.concatenate([x, cauda]))
    seq = lambda clipes: np.stack([pontuar(modelo, deslizantes(e, 1)) for e in ouvido.f.embed_clips(np.stack(clipes), batch_size=64)])
    n_limpos = seq([depois(_fim(sem_silencio(carregar(p))), False) for p in pos_te])
    n_sujos = seq([depois(bagunca(sem_silencio(carregar(p)), None), True) for p in pos_te for _ in range(3)])
    fala_te = np.concatenate([np.concatenate([carregar(p), np.zeros(int(SR * .5), "float32")])
                              for p in neg_te if "__peg__" not in p.name])
    pontos_fala = pontuar(modelo, deslizantes(ouvido.fluxo(fala_te), 1))
    pontos_oww = pontuar(modelo, deslizantes(np.asarray(oww[corte:]), 1))
    horas_fala = len(pontos_fala) * .08 / 3600
    horas_oww = len(pontos_oww) * .08 / 3600

    resultados = []
    for seguidos in SEGUIDOS:
        for limiar in np.round(np.arange(.3, .96, .05), 2):
            df, do = disparos(pontos_fala, limiar, seguidos), disparos(pontos_oww, limiar, seguidos)
            resultados.append({"seguidos": seguidos, "limiar": float(limiar),
                               "acerto_limpo": round(acertou(n_limpos, limiar, seguidos), 3),
                               "acerto_sala": round(acertou(n_sujos, limiar, seguidos), 3),
                               "falsos_por_hora": round((df + do) / (horas_fala + horas_oww), 2),
                               "falsos_portugues": df, "falsos_geral": do})
    bons = [r for r in resultados if r["falsos_por_hora"] <= a.max_fa_hora]
    escolhido = (max(bons, key=lambda r: (r["acerto_sala"], -r["falsos_por_hora"])) if bons
                 else min(resultados, key=lambda r: r["falsos_por_hora"] - r["acerto_sala"]))

    # pegadinhas: a maior nota sustentada (pelos quadros seguidos do escolhido)
    peg = {}
    k = escolhido["seguidos"]
    for p in neg_te:
        if "__peg__" in p.name:
            frase = p.name.split("__peg__")[1].removesuffix(".wav")
            notas = seq([depois(_fim(sem_silencio(carregar(p))), False)])[0]
            sustentada = max(min(notas[i: i + k]) for i in range(len(notas) - k + 1))
            peg[frase] = max(peg.get(frase, 0), float(sustentada))

    _exportar(modelo, pasta / f"{a.nome}.onnx")
    relatorio = {"limiar": escolhido["limiar"], "seguidos": escolhido["seguidos"], "escolhido": escolhido,
                 "curva": resultados, "pegadinhas_pior_nota": dict(sorted(peg.items(), key=lambda kv: -kv[1])),
                 "horas_de_prova": round(horas_fala + horas_oww, 2), "minutos": round((time.time() - t0) / 60, 1)}
    (pasta / "relatorio.json").write_text(json.dumps(relatorio, ensure_ascii=False, indent=2))
    print(f"prova: {horas_fala * 60:.0f} min de fala em português + {horas_oww:.1f} h de áudio geral")
    for r in resultados:
        if r["limiar"] in (.5, .6, .7, .8, .85, .9, .95):
            print(f"  {r['seguidos']} quadro(s), limiar {r['limiar']:.2f}: acerto {r['acerto_limpo']:.0%} limpo / "
                  f"{r['acerto_sala']:.0%} sala · {r['falsos_por_hora']}/h ({r['falsos_portugues']} pt, {r['falsos_geral']} geral)")
    print(f"escolhido: limiar {escolhido['limiar']}, {escolhido['seguidos']} quadro(s) seguidos · {relatorio['minutos']} min")
    print("pegadinhas (pior nota sustentada):")
    for f, sc in list(relatorio["pegadinhas_pior_nota"].items())[:12]:
        print(f"  {'⚠' if sc >= escolhido['limiar'] else ' '} {sc:.2f}  {f}")


def _leve(x: np.ndarray) -> np.ndarray:
    """Variação leve para gravação real (que já tem a sala embutida)."""
    x = x * 10 ** (rng.uniform(-8, 4) / 20)
    return x + ruido(len(x), "rosa") * rng.uniform(0, .01)


def _fim(x: np.ndarray) -> np.ndarray:
    """Fala limpa terminando 0,15 s antes do fim da janela."""
    saida = np.zeros(JANELA, dtype="float32")
    fim = JANELA - int(.15 * SR)
    x = x[-fim:]
    saida[fim - len(x): fim] = x / (np.abs(x).max() + 1e-9) * .5
    return saida


def _exportar(modelo, destino: Path):
    import torch

    torch.onnx.export(modelo, torch.zeros(1, QUADROS, 96), str(destino), input_names=["x"], output_names=["p"],
                      dynamic_axes={"x": {0: "n"}, "p": {0: "n"}}, opset_version=13, dynamo=False)
    print(f"modelo salvo em {destino} ({destino.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    random.seed(7)
    main()
