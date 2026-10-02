<p align="center"><img src="docs/capa.png" alt="Guará — seu assistente pessoal. Sarcástico, mas avisa antes." width="100%"></p>

Um assistente pessoal que mora num computador da sua casa — um notebook
velho, um Raspberry Pi, um PC encostado. Você conversa com ele pelo
Telegram ou chama em voz alta na sala: **"Ô, Guará."**

Ele cuida da agenda, dos e-mails, da casa, do dinheiro e até do código.
E tem opinião sobre tudo isso.

> **Você:** Guará, posso pedir um iFood?
> **Guará:** Pode pedir. Sobram R$ 30 até o dia 16. Eu só vou ficar aqui,
> julgando em silêncio. Bem alto.

## O que ele faz

- **Conversa** pelo Telegram — texto, áudio ou foto — e responde em voz
  quando você pede. Só fala com você.
- **Escuta a sala.** Diga "Ô, Guará" e ele acorda, ouve e responde pelo
  alto-falante. A tela do computador vira o rosto dele.
- **E-mail, agenda e Drive** — várias contas Google e Outlook/Hotmail. Lê,
  resume, marca compromisso, escreve e-mail (pede confirmação antes de
  mandar para outra pessoa).
- **Casa**, pelo Home Assistant: luzes, aparelhos, clima — e sabe quando
  você sai e chega, pelo GPS do celular.
- **Dinheiro**, pelo Open Finance (só leitura): saldos, fatura e o fluxo de
  caixa para a frente — avisa *antes* de faltar.
- **Vigia** o que você pedir: "me avisa quando chegar o e-mail do banco",
  "avisa se o servidor cair". Lembretes, rotinas e um modo não perturbe.
- **Programa**: manda o [Claude Code](https://claude.com/claude-code) do
  seu Mac trabalhar num projeto e te avisa quando os testes passarem.
- **Te liga** (com [Twilio](https://www.twilio.com), pago) quando precisa
  de uma decisão sua — em breve.
- **Controla outras máquinas** por SSH e liga o PC pela rede.

## Instalação

Precisa de um computador com **Linux** que fique ligado (Debian, Ubuntu,
Raspberry Pi OS…) e **Python 3.11+**.

```bash
git clone https://github.com/jvfsdev/Guara.git ~/Guara
cd ~/Guara && ./scripts/instalar.sh
```

O instalador mostra um endereço — algo como `http://192.168.0.42:8080`.
Abra no celular, na mesma rede, e o resto é pelo **painel**: crie uma
senha e siga os guias. Só dois itens são obrigatórios, ambos de graça:

1. **Gemini** — o cérebro. Chave grátis em
   [aistudio.google.com/apikey](https://aistudio.google.com/apikey).
2. **Telegram** — crie um bot com o [@BotFather](https://t.me/BotFather);
   o painel descobre o seu ID sozinho.

Cada item tem um botão **Testar** que fala com o serviço de verdade. Depois
é só mandar um "oi" para o bot.

O painel só abre na rede de casa (ou pelo [Tailscale](https://tailscale.com),
de qualquer lugar) — é ali que ficam as chaves das suas contas.

### Opcionais

- **Voz na sala e rosto na tela** — microfone, alto-falante e a tela do
  próprio computador. Ver [DEPLOY.md](DEPLOY.md#9-voz-na-sala-mic-e-alto-falantes-do-notebook).
- **Contas Google e Microsoft, casa, finanças, ligações** — tudo pelo painel.
- **Código no Mac** — ver [DEPLOY.md](DEPLOY.md#12-código-delegado-ao-mac-claude-code).

## Outro nome?

O Guará já vem treinado para ouvir **"Ô, Guará"** (e "Ei, Guará",
"Oi, Guará"). Quer chamar de outro jeito? Dá para treinar a sua própria
palavra num Mac em uns 40 minutos, sem gravar centenas de vezes a sua voz:
[scripts/palavra/LEIAME.md](scripts/palavra/LEIAME.md).

E ele aprende com a sua sala: toda vez que acorda — ou quase —, guarda o
trecho, e no painel você marca "era eu" ou "não era".

## Bom saber

- **Privacidade.** O Guará roda na sua casa, mas o cérebro é o Gemini: as
  conversas passam pelo Google. No nível gratuito, o Google pode usar esses
  dados para treinar modelos — pense nisso antes de conectar o e-mail.
  Ativar a cobrança no projeto do Google desliga esse uso.
- **Cota grátis.** O Gemini gratuito tem limite por minuto e por dia. O
  Guará desce uma escada de modelos quando um esgota — e aceita várias
  chaves (cada uma de uma conta Google soma mais cota).
- **Ele pede confirmação** antes de qualquer coisa perigosa ou irreversível
  (apagar arquivo, comando de administrador, e-mail para terceiros).
- **Áudio da sala** fica só no computador: no máximo 300 trechos curtos,
  para calibrar a palavra de ativação.

## Por dentro

- [GUARA.md](GUARA.md) — a personalidade. Edite à vontade.
- [docs/identidade-visual.md](docs/identidade-visual.md) — cores, marca e tipografia.
- [DEPLOY.md](DEPLOY.md) — cada integração em detalhe, para quem prefere o terminal.
- `friday/` — o código (o pacote ainda se chama assim, do tempo em que ele era a Friday).

Feito com [Gemini](https://ai.google.dev),
[openWakeWord](https://github.com/dscripka/openWakeWord),
[Home Assistant](https://www.home-assistant.io) e o
[openfinance-analyst](https://github.com/meloluan/openfinance-analyst).

## Licença

[MIT](LICENSE) — use, modifique e distribua à vontade.
