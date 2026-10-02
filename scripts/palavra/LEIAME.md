# Treinar a sua própria palavra de ativação

O Guará já vem treinado para **"Ô, Guará"** (`modelos/guara.onnx`). Com
estes scripts você treina o **seu** nome — "Ei, Lupa", "Ô, Tonico", o que
quiser — sem gravar centenas de vezes a própria voz.

Precisa de um **Mac** (o treino usa as vozes do macOS e o chip para
acelerar) e de uns **40 minutos**, quase todos esperando. O resultado é um
arquivo de ~600 KB que roda até em notebook velho.

## 1. Preparar (uma vez)

```bash
python3.13 -m venv .venv-treino
.venv-treino/bin/pip install torch openwakeword onnxruntime onnx edge-tts numpy scipy soundfile
mkdir -p treino-dados
curl -L -o treino-dados/negativos_oww.npy \
  https://huggingface.co/datasets/davidscripka/openwakeword_features/resolve/main/validation_set_features.npy
```

O último arquivo (185 MB) tem ~11 horas de fala, música e ruído já
processados: é o que ensina o detector a **não** acordar com a TV.

## 2. Gerar as vozes

```bash
.venv-treino/bin/python scripts/palavra/gerar.py \
  --frase "Ô, Guará" --variantes "Ei, Guará" "Oi, Guará" \
  --parecidas "guaraná" "guarda" "agora" "aguarda" "jaguará" \
  --nome guara
```

- `--frase` e `--variantes`: as formas de chamar. Duas ou três sílabas no
  nome e um "Ô"/"Ei" na frente funcionam melhor que uma palavra solta.
- `--parecidas`: **a parte mais importante.** Palavras que soam como o nome
  e aparecem em conversa normal. Fale o nome em voz alta e pense no que
  rima, no que começa igual e no que se fala sem querer. Elas entram
  sozinhas e dentro de frases ("E agora?", "Me vê um guaraná").

São ~25 vozes (macOS + vozes neurais da Microsoft, inclusive multilíngues
que falam português com sotaques diferentes), com ritmos e tons variados.

## 3. Treinar

```bash
.venv-treino/bin/python scripts/palavra/treinar.py --nome guara
```

O treino "bagunça" as vozes para soarem como uma sala (eco, ruído,
microfone ruim), treina, e depois faz duas rodadas em cima dos próprios
erros. No fim mostra uma tabela como esta:

```
limiar 0.70: acerto 97% limpo / 92% sala · 0.4/h (1 em pt, 0 no geral)
```

- **acerto**: em vozes que o modelo **nunca ouviu** (4 ficam de fora).
- **/h**: disparos falsos por hora, em áudio que ele também nunca ouviu.
- E a lista das pegadinhas com a pior nota de cada uma.

O arquivo fica em `treino-dados/guara/guara.onnx`.

## 4. Usar

Copie para o servidor (`modelos/guara.onnx`) e, no `config.yaml`:

```yaml
voice:
  enabled: true
  wake_model: modelos/guara.onnx
  wake_threshold: 0.70        # o limiar escolhido pelo treino
```

## 5. Calibrar com a sua voz (o passo que faz diferença)

Voz sintética ensina o básico; a sua voz, o seu microfone e o eco da sua
sala, só gravação de verdade. O Guará cuida disso sozinho: toda vez que
acorda — ou **quase** acorda — guarda os 2 s de áudio (só no aparelho,
no máximo 300 trechos).

1. Chame ele umas 10 vezes, do jeito que você fala no dia a dia, de
   lugares diferentes da sala.
2. No painel, abra **Palavra de ativação**, ouça cada trecho e marque
   **Era eu** ou **Não era**. O painel já sugere o limiar ideal — um toque
   para usar.
3. Para retreinar com a sua voz, copie `state/calibracao/` do servidor e:
   ```bash
   .venv-treino/bin/python scripts/palavra/treinar.py --nome guara --minhas caminho/para/calibracao
   ```
