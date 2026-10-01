# Identidade visual do Guará

Editorial e chapada — cara de pôster e de revista, não de "tela de IA".
Vale para tudo que for visual: o rosto na tela da sala (`friday/face.py`),
vídeos, páginas, gráficos.

## O que evitar

Neon ciano com brilho, degradê, painel de vidro com glow, orbe de
partículas, estrelas, linhas de varredura, glitch, "SYSTEM ONLINE" em mono.
Tudo isso virou assinatura de coisa gerada por IA.

## Paleta

| Nome | Hex | Uso |
|---|---|---|
| Papel | `#EEEAE1` | fundo claro |
| Tinta | `#141414` | fundo escuro, texto |
| **Laranja** | `#FF5A1F` | a cor da marca — destaque, alerta, o ponto da marca |
| Cobalto | `#2B3BE0` | só para o que é novo / "em breve" |
| Cinza | `#8C877D` | texto secundário, rótulos |
| Verde / vermelho | `#1E9E57` / `#E5484D` | só com sentido: passou / falhou |

Cores chapadas. **Zero brilho, degradê ou sombra.**

## Forma

- Blocos sólidos; bordas de 3 px em tinta; cantos quase retos (4–8 px).
  Bolhas de chat podem ser redondas.
- Seções alternam fundos sólidos (papel / tinta / laranja).
- Transparência não existe: para "esmaecer", misture a cor com o fundo.

## Tipografia

- Grotesca pesadíssima e enorme, alinhada à esquerda, espaçamento apertado:
  **Inter Display** Black/ExtraBold (no servidor: `fonts-inter`); em vídeo
  no Mac, SF Pro serve.
- Texto corrido: Inter Bold/SemiBold.
- Mono (DejaVu Sans Mono / Menlo) só para rótulos pequenos em caixa alta.

## Nome

**Guará**, como o lobo-guará: o bicho mais laranja do Brasil — desconfiado,
meio desengonçado, independente, e muito mais esperto do que parece.
Escreve-se com acento; em identificadores técnicos, `guara`.

## Marca

A cabeça do lobo-guará em geometria chapada: orelhas enormes, rosto que
afina num focinho comprido, **um olho só** (a piscadela de quem acabou de
ser irônico). A forma está em `friday/face.py` (`CABECA`, `FOCINHO`).

| Fundo | Cabeça | Focinho | Olho |
|---|---|---|---|
| Papel (principal) | laranja | tinta | tinta |
| Tinta | papel | — | laranja |
| Laranja | tinta | — | papel |

Em fundo claro é o bicho; em fundo escuro ou laranja vira silhueta, e o
olho é o único ponto de cor. No rosto da sala, ele pisca a cada ~5 s.
O nome acompanha a marca em Inter Display Black: **Guará**.

## Movimento

- Texto entra subindo por dentro de uma máscara.
- Troca de estado/cena: o fundo novo varre a tela (de baixo para cima).
- Cortes secos na batida; leve overshoot no que surge.

## Voz do texto

Curta, humana, com uma pontinha de ironia: "Pode falar.", "Um instante.",
"Vai sobrar?", "Ele cuida do resto." Nada de "assistente pessoal
autônomo". A personalidade completa está em `GUARA.md`.
