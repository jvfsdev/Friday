"""Fluxo de caixa para a frente: o saldo das contas dia a dia, até o horizonte.

A pergunta não é "quanto gastei" (isso o openfinance-analyst já responde),
é "quando o dinheiro do começo do mês chegar, cobre o que sai logo depois?".
O mês do chefe é apertado e concentrado: as entradas caem por volta do dia 5
e, no mesmo punhado de dias, saem as transferências fixas, as contas e a
fatura. A fatura é a variável que decide.

Toda a conta é feita aqui, em código. O modelo só explica o resultado —
número de projeção estimado pelo LLM sai errado com cara de certo.

Os dados vêm do banco do openfinance-analyst via scripts/ofa_export.mjs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import statistics
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import ROOT

log = logging.getLogger("friday.fluxo")

HORIZONTE_PADRAO = 45
HISTORICO_MESES = 6
# Recorrência: aparece em pelo menos tantos meses diferentes da janela...
MESES_MINIMOS = 3
# ...e continua viva: a última vez não pode ser mais antiga que isso.
INATIVA_APOS_DIAS = 45
# Entrada recorrente de verdade cai sempre perto do mesmo dia do mês; os R$ 20
# que um amigo manda de vez em quando aparecem em vários meses, mas em dias
# soltos. Só vale para ENTRADA: conta de luz paga cada mês num dia é despesa
# real, e errar para o lado pessimista é o lado seguro.
DISPERSAO_MAX_DIAS = 4
VALOR_MINIMO = 30.0
# Atraso aceito antes de concluir que "este mês não vem". Os dados chegam com
# alguns dias de defasagem, então ausência recente não é ausência.
TOLERANCIA_ATRASO = 6
# Dados mais velhos que isso tornam a projeção pouco confiável.
DADOS_VELHOS_DIAS = 5
RITMO_JANELA_DIAS = 60
# Saldo mínimo abaixo do qual vale um aviso, mesmo sem ficar negativo.
MARGEM_PADRAO = 100.0

# Dinheiro andando entre contas do próprio chefe não é entrada nem saída.
# O Nubank classifica o pagamento de fatura como "Transfers", por isso a
# descrição também conta. O pagamento em si entra na projeção pela fatura.
CATEGORIAS_INTERNAS = {"Same person transfer", "Transfer - Internal", "Credit card payment"}
_PREFIXOS = re.compile(
    r"^(pix\s+(enviado|recebido)|transfer[eê]ncia\s+(enviada|recebida)|"
    r"pagamento\s+efetuado|compra\s+no\s+d[eé]bito|ted|doc)\s*",
    re.IGNORECASE,
)


@dataclass
class Evento:
    data: date
    valor: float          # entrada positiva, saída negativa
    descricao: str
    estimado: bool = False
    saldo: float = 0.0    # saldo logo depois deste evento
    # Fatura aberta tem dois valores: o que já está nela (piso, certo) e o
    # que deve chegar no ritmo recente (estimativa). Os demais: iguais.
    valor_piso: float | None = None
    saldo_piso: float = 0.0


@dataclass
class Fatura:
    vencimento: date
    fechamento: date
    ate_agora: float
    projetada: float
    aberta: bool


@dataclass
class Projecao:
    hoje: date
    ate: date
    saldo_inicial: float
    eventos: list[Evento]
    faturas: list[Fatura]
    menor_saldo: float
    dia_menor_saldo: date
    # Mesmo cenário, mas com as faturas abertas só com o que já está nelas.
    # O backtest mostrou por quê: o piso é firme; o ritmo errou +2% num mês e
    # +28% no outro (compras grandes e avulsas inflam a média).
    menor_saldo_piso: float = 0.0
    dia_menor_piso: date | None = None
    avisos: list[str] = field(default_factory=list)
    dados_ate: dict[str, date] = field(default_factory=dict)

    @property
    def fica_negativo(self) -> bool:
        """No ritmo atual de gasto, falta dinheiro (pode faltar)."""
        return self.menor_saldo < 0

    @property
    def falta_certa(self) -> bool:
        """Falta mesmo sem nenhuma compra a mais (vai faltar)."""
        return self.menor_saldo_piso < 0


# ------------------------------------------------------------------ utilidades


def brl(valor: float) -> str:
    texto = f"{abs(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'-' if valor < 0 else ''}R$ {texto}"


def _dia(ano: int, mes: int, dia: int) -> date:
    return date(ano, mes, min(dia, monthrange(ano, mes)[1]))


def _somar_meses(d: date, n: int, dia: int | None = None) -> date:
    total = d.year * 12 + d.month - 1 + n
    return _dia(total // 12, total % 12 + 1, dia or d.day)


def _data(texto: str) -> date:
    return date.fromisoformat(texto[:10])


def _interna(tx: dict) -> bool:
    return (tx.get("categoria") in CATEGORIAS_INTERNAS
            or "pagamento de fatura" in (tx.get("descricao") or "").lower())


def _palavras(descricao: str) -> list[str]:
    texto = descricao or ""
    if "|" in texto:
        texto = texto.split("|", 1)[1]
    return re.findall(r"[A-Za-zÀ-ÿ]{2,}", _PREFIXOS.sub("", texto.strip()).upper())


def contraparte(descricao: str) -> str:
    """Quem está do outro lado: "Pix enviado FULANO DE TAL" e
    "Transferência enviada|Fulano" são a mesma pessoa, FULANO."""
    palavras = _palavras(descricao)
    return palavras[0] if palavras else ""


def _rotulo(descricoes: list[str]) -> str:
    """Nome legível para o celular: a forma mais completa que apareceu."""
    formas = [" ".join(_palavras(d)[:3]).title() for d in descricoes]
    return max(formas, key=len)[:28] if formas else "?"


def _grupos_por_valor(valores: list[tuple[date, float]]) -> list[list[tuple[date, float]]]:
    """Separa cobranças do mesmo lugar com valores de outra ordem de grandeza
    (a internet que cobra R$ 40 e R$ 100 são duas contas, não uma)."""
    ordenados = sorted(valores, key=lambda v: abs(v[1]))
    grupos: list[list[tuple[date, float]]] = []
    for item in ordenados:
        if grupos and abs(item[1]) <= abs(grupos[-1][-1][1]) * 1.5:
            grupos[-1].append(item)
        else:
            grupos.append([item])
    return grupos


# --------------------------------------------------------------- recorrências


@dataclass
class Recorrente:
    nome: str
    valor: float        # com sinal
    dia: int
    ultima: date
    meses: set[tuple[int, int]]


def recorrentes_em_conta(transacoes: list[dict], contas_banco: set[str], hoje: date) -> list[Recorrente]:
    inicio = _somar_meses(hoje.replace(day=1), -HISTORICO_MESES)
    por_chave: dict[tuple[str, int], list[tuple[date, float]]] = {}
    descricoes: dict[tuple[str, int], list[str]] = {}
    for tx in transacoes:
        if tx["conta"] not in contas_banco or _interna(tx):
            continue
        quando = _data(tx["data"])
        if not (inicio <= quando <= hoje) or not tx["valor"]:
            continue
        nome = contraparte(tx.get("descricao") or "")
        if not nome:
            continue
        sinal = 1 if tx["valor"] > 0 else -1
        por_chave.setdefault((nome, sinal), []).append((quando, float(tx["valor"])))
        descricoes.setdefault((nome, sinal), []).append(tx.get("descricao") or "")

    achados = []
    for chave, itens in por_chave.items():
        for grupo in _grupos_por_valor(itens):
            meses = {(d.year, d.month) for d, _ in grupo}
            ultima = max(d for d, _ in grupo)
            if len(meses) < MESES_MINIMOS or (hoje - ultima).days > INATIVA_APOS_DIAS:
                continue
            valor = statistics.median(v for _, v in grupo)
            dia = statistics.median(d.day for d, _ in grupo)
            dispersao = statistics.median(abs(d.day - dia) for d, _ in grupo)
            if abs(valor) < VALOR_MINIMO or (valor > 0 and dispersao > DISPERSAO_MAX_DIAS):
                continue
            achados.append(Recorrente(
                nome=_rotulo(descricoes[chave]),
                valor=round(valor, 2),
                dia=int(dia),
                ultima=ultima,
                meses=meses,
            ))
    return achados


def _ocorrencias(rec: Recorrente, hoje: date, ate: date, avisos: list[str]) -> list[Evento]:
    """Próximas datas de uma recorrência mensal dentro do horizonte."""
    este_mes = (hoje.year, hoje.month)
    if este_mes in rec.meses:
        proxima = _somar_meses(hoje, 1, rec.dia)
    else:
        prevista = _dia(hoje.year, hoje.month, rec.dia)
        if prevista >= hoje:
            proxima = prevista
        elif (hoje - prevista).days <= TOLERANCIA_ATRASO:
            # Atrasou — ou o dado ainda não chegou. Saída: assume que vem
            # (pessimista é o lado seguro). Entrada: não conta com ela.
            if rec.valor < 0:
                proxima = hoje
            else:
                avisos.append(f"{rec.nome} ({brl(rec.valor)}) costuma cair por volta do dia "
                              f"{rec.dia} e ainda não apareceu — não contei com ela.")
                proxima = _somar_meses(hoje, 1, rec.dia)
        else:
            proxima = _somar_meses(hoje, 1, rec.dia)

    eventos = []
    while proxima <= ate:
        eventos.append(Evento(proxima, rec.valor, rec.nome, estimado=True))
        proxima = _somar_meses(proxima, 1, rec.dia)
    return eventos


# --------------------------------------------------------------------- cartão


def _dia_fechamento(compras: list[dict], dia_vencimento: int, hoje: date) -> int:
    """A Pluggy não informa o fechamento; os dados sim. A primeira compra do
    mês anterior que já caiu na fatura seguinte é o dia depois do fechamento.

    Olhar a última compra de cada fatura engana: a fatura aberta ainda não
    fechou, e a última compra dela é só a mais recente.
    """
    dias = []
    for tx in compras:
        if not tx.get("fatura") or tx.get("parcelas") is not None:
            continue
        quando = _data(tx["data"])
        ano, mes = map(int, tx["fatura"].split("-"))
        anterior = _somar_meses(date(ano, mes, 1), -1)
        # compra do mês anterior que foi parar nesta fatura = depois do fechamento
        if (quando.year, quando.month) == (anterior.year, anterior.month) and \
                _dia(ano, mes, dia_vencimento) < hoje:
            dias.append((tx["fatura"], quando.day))
    primeiras: dict[str, int] = {}
    for fatura, dia in dias:
        primeiras[fatura] = min(dia, primeiras.get(fatura, 99))
    if len(primeiras) < 2:
        return max(1, dia_vencimento - 8)
    return max(1, int(statistics.median(primeiras.values())) - 1)


def faturas_do_cartao(cartao: dict, transacoes: list[dict], hoje: date, ate: date,
                      avisos: list[str]) -> list[Fatura]:
    if not cartao.get("vencimento"):
        avisos.append(f"O cartão {cartao['nome']} não informa vencimento — deixei a fatura de fora.")
        return []
    dia_venc = _data(cartao["vencimento"]).day
    txs = [t for t in transacoes if t["conta"] == cartao["id"]]
    compras = [t for t in txs if t["valor"] < 0 and t.get("categoria") != "Credit card payment"]
    dia_fech = _dia_fechamento(compras, dia_venc, hoje)

    def fechamento_de(venc: date) -> date:
        # fecha no mesmo mês do vencimento, ou no anterior se o dia for maior
        return _dia(venc.year, venc.month, dia_fech) if dia_fech < dia_venc \
            else _somar_meses(venc, -1, dia_fech)

    # Ritmo de gasto à vista (parcela futura já vem lançada; não conta duas vezes)
    janela = hoje - timedelta(days=RITMO_JANELA_DIAS)
    avulsas = sum(-t["valor"] for t in compras
                  if t.get("parcelas") is None and janela < _data(t["data"]) <= hoje)
    ritmo = avulsas / RITMO_JANELA_DIAS

    def marcado(mes: str) -> float:
        return round(-sum(t["valor"] for t in txs
                          if t.get("fatura") == mes and t.get("categoria") != "Credit card payment"), 2)

    venc = _dia(hoje.year, hoje.month, dia_venc)
    if venc <= hoje:
        venc = _somar_meses(venc, 1, dia_venc)

    faturas = []
    primeira = True
    while venc <= ate:
        mes = f"{venc.year:04d}-{venc.month:02d}"
        fech = fechamento_de(venc)
        aberta = hoje < fech
        ate_agora = marcado(mes)
        if primeira:
            # O saldo devedor do banco é a fonte confiável: há compras que
            # chegam sem a marcação da fatura e sumiriam da soma. Tira dele só
            # as parcelas de faturas seguintes.
            futuras = sum(-t["valor"] for t in txs
                          if (t.get("fatura") or "") > mes and t.get("parcelas") is not None)
            ate_agora = max(ate_agora, round(float(cartao.get("saldo") or 0) - futuras, 2))
            primeira = False
        # Quanto ainda vai entrar até fechar: do que falta do ciclo (ou do
        # ciclo inteiro, se a fatura nem abriu ainda) no ritmo recente.
        inicio_ciclo = _somar_meses(fech, -1, dia_fech)
        dias_restantes = (fech - max(hoje, inicio_ciclo)).days if aberta else 0
        projetada = round(ate_agora + ritmo * dias_restantes, 2)
        faturas.append(Fatura(venc, fech, ate_agora, projetada, aberta))
        venc = _somar_meses(venc, 1, dia_venc)
    return faturas


# ------------------------------------------------------------------- projeção


_VALOR_NA_AGENDA = re.compile(r"R\$\s*([\d.]+(?:,\d{1,2})?)", re.IGNORECASE)


def valor_na_agenda(titulo: str) -> float | None:
    """ "IPVA R$ 1.234,56" -> -1234.56; "receber R$ 300" -> 300."""
    achado = _VALOR_NA_AGENDA.search(titulo or "")
    if not achado:
        return None
    valor = float(achado.group(1).replace(".", "").replace(",", "."))
    return valor if re.search(r"receb|entrada|salário|salario", titulo, re.IGNORECASE) else -valor


def projetar(dados: dict, hoje: date, dias: int = HORIZONTE_PADRAO,
             agenda: list[tuple[date, str]] | None = None,
             margem: float = MARGEM_PADRAO) -> Projecao:
    ate = hoje + timedelta(days=dias)
    contas = dados["contas"]
    transacoes = dados["transacoes"]
    avisos: list[str] = []

    banco = [c for c in contas if c["tipo"] == "BANK"]
    cartoes = [c for c in contas if c["tipo"] == "CREDIT"]
    saldo = round(sum(float(c["saldo"] or 0) for c in banco), 2)

    dados_ate = {}
    for c in contas:
        datas = [_data(t["data"]) for t in transacoes if t["conta"] == c["id"] and _data(t["data"]) <= hoje]
        if datas:
            dados_ate[c["nome"]] = max(datas)

    eventos: list[Evento] = []
    for rec in recorrentes_em_conta(transacoes, {c["id"] for c in banco}, hoje):
        eventos += _ocorrencias(rec, hoje, ate, avisos)

    faturas: list[Fatura] = []
    for cartao in cartoes:
        for fatura in faturas_do_cartao(cartao, transacoes, hoje, ate, avisos):
            faturas.append(fatura)
            rotulo = f"fatura {cartao['nome']}"
            if fatura.aberta:
                rotulo += f" (até agora {brl(fatura.ate_agora)}, no ritmo)"
            eventos.append(Evento(fatura.vencimento, -fatura.projetada, rotulo,
                                  estimado=fatura.aberta, valor_piso=-fatura.ate_agora))
        ultimo = dados_ate.get(cartao["nome"])
        if ultimo and (hoje - ultimo).days > DADOS_VELHOS_DIAS:
            avisos.append(f"O cartão {cartao['nome']} tem lançamentos só até {ultimo:%d/%m} — "
                          "compras mais recentes ainda não entraram na fatura.")

    for quando, titulo in agenda or []:
        valor = valor_na_agenda(titulo)
        if valor is not None and hoje <= quando <= ate:
            eventos.append(Evento(quando, valor, f"agenda: {titulo}"))

    # No mesmo dia, entrada antes de saída: o banco não recusa um débito
    # porque o salário chegou uma hora depois.
    eventos.sort(key=lambda e: (e.data, e.valor < 0))
    inicial = saldo
    menor, dia_menor = saldo, hoje
    for e in eventos:
        saldo = round(saldo + e.valor, 2)
        e.saldo = saldo
        if saldo < menor:
            menor, dia_menor = saldo, e.data
    piso, menor_piso, dia_piso = inicial, inicial, hoje
    for e in eventos:
        piso = round(piso + (e.valor if e.valor_piso is None else e.valor_piso), 2)
        e.saldo_piso = piso
        if piso < menor_piso:
            menor_piso, dia_piso = piso, e.data

    if 0 <= menor < margem and eventos:
        avisos.append(f"O saldo chega a só {brl(menor)} em {dia_menor:%d/%m}.")

    return Projecao(
        hoje=hoje, ate=ate,
        saldo_inicial=round(sum(float(c["saldo"] or 0) for c in banco), 2),
        eventos=eventos, faturas=faturas,
        menor_saldo=menor, dia_menor_saldo=dia_menor,
        menor_saldo_piso=menor_piso, dia_menor_piso=dia_piso,
        avisos=avisos, dados_ate=dados_ate,
    )


def veredito(p: Projecao) -> str:
    """A conclusão em uma frase, separando o certo do provável."""
    if p.falta_certa:
        return (f"⚠️ VAI FALTAR: mesmo sem nenhuma compra a mais, o saldo chega a "
                f"{brl(p.menor_saldo_piso)} em {p.dia_menor_piso:%d/%m}. No ritmo atual de gasto, a "
                f"falta sobe para ~{brl(-p.menor_saldo)}.")
    if p.fica_negativo:
        # os dois cenários no MESMO dia crítico, para comparar maçã com maçã
        folga = [e.saldo_piso for e in p.eventos if e.data == p.dia_menor_saldo][-1]
        return (f"⚠️ PODE FALTAR em {p.dia_menor_saldo:%d/%m}: com o que já está na fatura, sobram só "
                f"{brl(folga)} nesse dia; no ritmo de gasto dos últimos {RITMO_JANELA_DIAS} dias, "
                f"faltam ~{brl(-p.menor_saldo)}. Cada real a mais no cartão até o fechamento sai "
                f"dessa folga.")
    return f"✅ Fecha: o menor saldo no período é {brl(p.menor_saldo)} em {p.dia_menor_saldo:%d/%m}."


def resumo(p: Projecao) -> str:
    linhas = [f"Fluxo de caixa de {p.hoje:%d/%m} a {p.ate:%d/%m} — saldo hoje nas contas: {brl(p.saldo_inicial)}."]
    if p.dados_ate:
        linhas.append("Dados até: " + ", ".join(f"{n[:18]} {d:%d/%m}" for n, d in p.dados_ate.items()) + ".")

    for f in p.faturas:
        if f.aberta:
            linhas.append(f"Fatura que vence {f.vencimento:%d/%m}: {brl(f.ate_agora)} até agora; fecha por volta "
                          f"de {f.fechamento:%d/%m} e, no ritmo dos últimos {RITMO_JANELA_DIAS} dias, deve "
                          f"chegar a ~{brl(f.projetada)}.")
        else:
            linhas.append(f"Fatura que vence {f.vencimento:%d/%m}: {brl(f.projetada)} (já fechada).")

    linhas.append("\nLinha do tempo (saldo depois de cada movimento):")
    for e in p.eventos:
        marca = "~" if e.estimado else ""
        linhas.append(f"  {e.data:%d/%m} {marca}{'+' if e.valor > 0 else ''}{brl(e.valor)} {e.descricao}"
                      f" → {brl(e.saldo)}")
    if not p.eventos:
        linhas.append("  (nenhum movimento previsto)")

    linhas.append("")
    linhas.append(veredito(p))
    for aviso in p.avisos:
        linhas.append(f"• {aviso}")
    linhas.append("(~ = estimado pelo histórico; valores de recorrência são a mediana dos últimos meses.)")
    return "\n".join(linhas)


# ---------------------------------------------------------------- integração


def _dist_do_openfinance(config) -> str | None:
    for spec in (config.mcp_servers or {}).values():
        for arg in spec.get("args") or []:
            if "openfinance-analyst" in str(arg) and str(arg).endswith("index.js"):
                return str(Path(arg).parent)
    return None


async def exportar(config, hoje: date) -> dict:
    dist = _dist_do_openfinance(config)
    if not dist:
        raise RuntimeError("o openfinance-analyst não está configurado em mcp_servers")
    de = _somar_meses(hoje.replace(day=1), -(HISTORICO_MESES + 1)).isoformat()
    ate = (hoje + timedelta(days=400)).isoformat()
    proc = await asyncio.create_subprocess_exec(
        "node", str(ROOT / "scripts" / "ofa_export.mjs"), dist, de, ate,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        saida, erro = await asyncio.wait_for(proc.communicate(), timeout=90)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("a leitura dos dados financeiros passou de 90 s")
    if proc.returncode != 0:
        raise RuntimeError(f"não consegui ler os dados financeiros: {erro.decode(errors='replace')[:300]}")
    return json.loads(saida)


async def _agenda(config, dias: int) -> list[tuple[date, str]]:
    """Compromissos com valor em reais no título, de todas as contas Google."""
    from .tools import google_workspace as gw

    fuso = ZoneInfo(config.timezone)

    def buscar(conta: str):
        agora = datetime.now(fuso)
        itens = gw._service("calendar", "v3", conta).events().list(
            calendarId="primary", timeMin=agora.isoformat(),
            timeMax=(agora + timedelta(days=dias)).isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=250, q="R$",
        ).execute().get("items", [])
        return [(_data(ev["start"].get("dateTime") or ev["start"].get("date")), ev.get("summary", ""))
                for ev in itens]

    achados = []
    for conta in gw.accounts():
        try:
            achados += await asyncio.to_thread(buscar, conta)
        except Exception as exc:
            log.warning("agenda da conta %s indisponível para o fluxo: %s", conta, exc)
    return achados


async def projecao_atual(config, sincronizar=None, dias: int = HORIZONTE_PADRAO,
                         margem: float = MARGEM_PADRAO) -> Projecao:
    """Sincroniza com o banco (se der), lê os dados e projeta."""
    if sincronizar:
        try:
            await sincronizar(None)
        except Exception as exc:
            # Dado de ontem ainda serve; a projeção avisa a idade dele.
            log.warning("sync do Open Finance falhou, seguindo com o que há: %s", exc)
    hoje = datetime.now(ZoneInfo(config.timezone)).date()
    dados = await exportar(config, hoje)
    return projetar(dados, hoje, dias, agenda=await _agenda(config, dias), margem=margem)
