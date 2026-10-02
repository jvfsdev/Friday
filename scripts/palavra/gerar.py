"""Passo 1 de 3 — gera as vozes para treinar uma palavra de ativação.

Ninguém precisa gravar centenas de vezes a própria voz: o script pede a
dezenas de vozes sintéticas (macOS + vozes neurais da Microsoft) que falem
a frase, com ritmos e tons diferentes. Diversidade de vozes é o que faz o
detector reconhecer quem ele nunca ouviu.

Gera também o "não é a palavra":
  * frases do dia a dia em português, para ele não acordar com conversa;
  * palavras PARECIDAS com a de ativação (as "pegadinhas"), que são onde os
    detectores mais erram.

Uso (no Mac, com o ambiente de treino):
    .venv-treino/bin/python scripts/palavra/gerar.py \\
        --frase "Ô, Guará" --variantes "Ei, Guará" "Oi, Guará" \\
        --parecidas "guaraná" "guarda" "agora" "jaguará"

Saída em treino-dados/<nome>/ (fora do git). Depois: treinar.py.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import re
import subprocess
import sys
import tempfile
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]

VOZES_MAC = ["Luciana", "Eddy", "Flo", "Grandma", "Grandpa", "Reed", "Rocko", "Sandy", "Shelley"]
VOZES_EDGE = [
    "pt-BR-ThalitaMultilingualNeural", "pt-BR-AntonioNeural", "pt-BR-FranciscaNeural",
    "pt-PT-DuarteNeural", "pt-PT-RaquelNeural",
    # multilíngues: falam português com sotaques variados — ótimo para generalizar
    "en-US-AndrewMultilingualNeural", "en-US-AvaMultilingualNeural", "en-US-BrianMultilingualNeural",
    "en-US-EmmaMultilingualNeural", "en-AU-WilliamMultilingualNeural", "fr-FR-VivienneMultilingualNeural",
    "fr-FR-RemyMultilingualNeural", "de-DE-SeraphinaMultilingualNeural", "de-DE-FlorianMultilingualNeural",
    "it-IT-GiuseppeMultilingualNeural",
]

# Conversa comum de casa: o que o detector vai ouvir 99,9% do tempo.
FRASES = """Bom dia, dormiu bem? | Que horas são agora? | Você viu minha chave em algum lugar?
O almoço vai ficar pronto em dez minutos | Desliga a televisão antes de sair
Amanhã eu tenho reunião cedo | Vamos pedir uma pizza hoje? | Cadê o carregador do celular?
Está fazendo muito calor aqui dentro | Fecha a janela que vai chover | O ônibus atrasou de novo
Preciso comprar pão e leite | Esse filme é muito bom, você já viu? | Liga para a sua mãe depois
O cachorro do vizinho latiu a noite toda | Vou tomar banho e já volto | Abre a porta para mim, por favor
Hoje o trânsito estava horrível | Quanto custou esse tênis? | Me passa o sal, por favor
A internet caiu de novo | Você lembrou de pagar a conta de luz? | Que saudade da praia
Estou morrendo de fome | Coloca uma música aí | O jogo começa às nove da noite
Eu acho que vou dormir mais cedo hoje | Arruma a sua cama antes de sair | Deixa que eu lavo a louça
O mercado fecha às dez | Sabe se vai chover amanhã? | Esse café está muito forte
Minha cabeça está doendo | Vamos ao cinema no sábado? | O gato derrubou o vaso da sala
Você terminou o trabalho da faculdade? | Que barulho foi esse? | Esqueci a senha do wi-fi
O médico pediu uns exames | Vou chegar um pouco atrasado | Olha que lindo esse pôr do sol
Ninguém merece segunda-feira | A geladeira está fazendo um barulho estranho | Bora fazer um churrasco?
Ele ganhou o campeonato ontem | Faltou energia no prédio inteiro | Põe o feijão de molho
Ah, para com isso | Opa, tudo certo? | Oi, tudo bem com você? | Ei, espera aí
Ô, cara, que demora | Olha só quem chegou | Agora sim, ficou bom | Guarda isso na gaveta
Clara, vem jantar | Laura, cadê você? | Ô, Mara, me ajuda aqui | Aguarda um minutinho
Já falei que não | Vai, rapidinho | Tá bom, tá bom | Nossa, que susto
Sabe aquele restaurante japonês? | Hoje é aniversário da minha avó | O carro está fazendo um barulho
""".replace("\n", "|").split("|")

# As pegadinhas dentro de frases de verdade: é assim que elas aparecem na sala.
# "{p}" vira cada palavra parecida passada em --parecidas.
CONTEXTOS = ["E {p}?", "{p} não", "Faz isso {p}", "{p} sim, ficou ótimo", "Vem cá {p}",
             "Ô, {p} vai", "Um {p} e outro depois", "Me vê um {p}, por favor", "É {p} ou nunca",
             "Ele falou {p} mesmo", "Olha o {p} ali", "Não, {p} não dá", "Pode ser {p}?",
             "Tá, {p} eu vou", "Fala {p}, rapidinho"]


def limpa(s: str) -> str:
    """Nome de arquivo a partir do texto. A pontuação final entra no nome:
    "Guará!", "Guará?" e "Guará" têm entonações diferentes e não podem
    virar o mesmo arquivo (o gerador pula o que já existe)."""
    fim = {"!": "-excl", "?": "-perg"}.get(s.strip()[-1:], "")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:40] + fim


def wav16k(origem: Path, destino: Path):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(origem), "-ac", "1", "-ar", "16000",
                    "-sample_fmt", "s16", str(destino)], check=True)


def falar_mac(voz: str, texto: str, ritmo: int, destino: Path):
    if destino.exists():
        return
    with tempfile.NamedTemporaryFile(suffix=".aiff") as tmp:
        subprocess.run(["say", "-v", voz, "-r", str(ritmo), "-o", tmp.name, texto], check=True)
        wav16k(Path(tmp.name), destino)


async def falar_edge(voz: str, texto: str, ritmo: int, tom: int, destino: Path, sem: asyncio.Semaphore):
    if destino.exists():
        return
    import edge_tts

    async with sem:
        for tentativa in range(4):
            try:
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                    caminho = Path(tmp.name)
                await edge_tts.Communicate(texto, voz, rate=f"{ritmo:+d}%", pitch=f"{tom:+d}Hz").save(str(caminho))
                await asyncio.to_thread(wav16k, caminho, destino)
                caminho.unlink(missing_ok=True)
                return
            except Exception as exc:
                if tentativa == 3:
                    print(f"  falhou {voz} «{texto}»: {exc}", file=sys.stderr)
                await asyncio.sleep(2 * (tentativa + 1))


async def gerar(tarefas_mac, tarefas_edge):
    with ThreadPoolExecutor(8) as pool:
        loop = asyncio.get_running_loop()
        fut_mac = [loop.run_in_executor(pool, falar_mac, *t) for t in tarefas_mac]
        sem = asyncio.Semaphore(10)
        total = len(tarefas_edge)
        feitas = 0
        async def uma(t):
            nonlocal feitas
            await falar_edge(*t, sem)
            feitas += 1
            if feitas % 200 == 0:
                print(f"  vozes neurais: {feitas}/{total}", flush=True)
        await asyncio.gather(*fut_mac, *(uma(t) for t in tarefas_edge))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--frase", required=True, help='a frase de ativação, ex.: "Ô, Guará"')
    ap.add_argument("--variantes", nargs="*", default=[], help="outras formas aceitas de chamar")
    ap.add_argument("--parecidas", nargs="*", default=[], help="palavras parecidas que NÃO devem acordar")
    ap.add_argument("--nome", help="pasta de saída (padrão: derivado da frase)")
    a = ap.parse_args()

    random.seed(7)
    pasta = RAIZ / "treino-dados" / (a.nome or limpa(a.frase))
    pos, neg = pasta / "positivos", pasta / "negativos"
    for d in (pos, neg):
        d.mkdir(parents=True, exist_ok=True)

    frases = [a.frase] + a.variantes
    # pontuação muda a entonação: chamado, pergunta, exclamação
    falas = sorted({f + p for f in frases for p in ("", "!", "?")})
    mac, edge = [], []
    for voz in VOZES_MAC:
        for f in falas:
            for ritmo in (150, 175, 200, 230):
                mac.append((voz, f, ritmo, pos / f"{voz}__{limpa(f)}__{ritmo}.wav"))
    for voz in VOZES_EDGE:
        for f in falas:
            for ritmo in (-20, -5, 10, 25):
                for tom in (-10, 0, 10):
                    edge.append((voz, f, ritmo, tom, pos / f"{voz}__{limpa(f)}__{ritmo}_{tom}.wav"))

    # pegadinhas: cada parecida sozinha e dentro de uma frase, em TODAS as vozes
    pegadinhas = []
    for p in a.parecidas:
        pegadinhas += [p, f"{p}!", f"Ô, {p}", f"Me dá um {p}", f"Cadê o {p}?"]
        pegadinhas += [c.format(p=p) for c in random.sample(CONTEXTOS, 6)]
    comuns = [f.strip() for f in FRASES if f.strip()]
    for voz in VOZES_MAC:
        for f in pegadinhas:
            mac.append((voz, f, 185, neg / f"{voz}__peg__{limpa(f)}.wav"))
        for f in comuns:
            mac.append((voz, f, random.choice((160, 190, 220)), neg / f"{voz}__{limpa(f)}.wav"))
    for voz in VOZES_EDGE:
        for f in pegadinhas:
            edge.append((voz, f, 0, 0, neg / f"{voz}__peg__{limpa(f)}.wav"))
        for f in comuns:
            edge.append((voz, f, random.choice((-10, 0, 10)), random.choice((-5, 0, 5)), neg / f"{voz}__{limpa(f)}.wav"))

    print(f"gerando {len(mac)} falas do macOS e {len(edge)} neurais em {pasta}", flush=True)
    asyncio.run(gerar(mac, edge))
    print(f"pronto: {len(list(pos.glob('*.wav')))} positivas, {len(list(neg.glob('*.wav')))} negativas")


if __name__ == "__main__":
    main()
