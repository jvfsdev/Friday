"""Calibrar a palavra de ativação pelo painel.

O Guará guarda sozinho cada vez que acordou ou QUASE acordou (nota ≥ 0,25)
em state/calibracao/ — ver friday/voice.py. Aqui a pessoa ouve cada trecho
e diz se era ela chamando. Com isso:
  * o painel sugere o limiar que separa as duas coisas na SUA sala;
  * os trechos marcados viram dados de treino (scripts/palavra/treinar.py
    --minhas), para retreinar com a voz e o microfone de verdade.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

NOME = re.compile(r"^(\d{8}-\d{6})_(\d\.\d\d)_(acordou|quase)\.wav$")


class Calibracao:
    def __init__(self, pasta: Path):
        self.pasta = pasta
        self.rotulos_arq = pasta / "rotulos.json"

    def rotulos(self) -> dict[str, str]:
        try:
            return json.loads(self.rotulos_arq.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def rotular(self, arquivo: str, rotulo: str):
        if not NOME.match(arquivo) or rotulo not in ("eu", "nao", ""):
            return
        r = self.rotulos()
        if rotulo:
            r[arquivo] = rotulo
        else:
            r.pop(arquivo, None)
        self.pasta.mkdir(parents=True, exist_ok=True)
        self.rotulos_arq.write_text(json.dumps(r), encoding="utf-8")

    def trechos(self, limite: int = 40) -> list[dict]:
        r = self.rotulos()
        itens = []
        for arq in sorted(self.pasta.glob("*.wav"), reverse=True)[:limite]:
            m = NOME.match(arq.name)
            if m:
                d, h = m.group(1).split("-")
                itens.append({"arquivo": arq.name, "nota": float(m.group(2)), "acordou": m.group(3) == "acordou",
                              "quando": f"{d[6:8]}/{d[4:6]} {h[:2]}:{h[2:4]}", "rotulo": r.get(arq.name, "")})
        return itens

    def caminho(self, arquivo: str) -> Path | None:
        return self.pasta / arquivo if NOME.match(arquivo) and (self.pasta / arquivo).is_file() else None

    def sugerir(self, atual: float) -> dict | None:
        """O limiar que mais separa "era eu" de "não era", entre os marcados."""
        r = self.rotulos()
        notas = {a: float(NOME.match(a).group(2)) for a in r if NOME.match(a)}
        eu = sorted(notas[a] for a in notas if r[a] == "eu")
        nao = sorted(notas[a] for a in notas if r[a] == "nao")
        if len(eu) < 3:
            return None
        opcoes = []
        for limiar in [x / 100 for x in range(30, 96, 5)]:
            acertos = sum(n >= limiar for n in eu)
            falsos = sum(n >= limiar for n in nao)
            opcoes.append((acertos - 3 * falsos, limiar, acertos, falsos))   # um falso custa mais
        topo = max(o[0] for o in opcoes)
        empatados = [o for o in opcoes if o[0] == topo]
        # o do meio do empate: margem tanto para as chamadas fracas quanto para os falsos
        _, limiar, acertos, falsos = empatados[len(empatados) // 2]
        return {"limiar": limiar, "acertos": acertos, "chamadas": len(eu), "falsos": falsos,
                "nao_marcados": len(nao), "atual": atual}
