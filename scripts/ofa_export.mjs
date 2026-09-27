#!/usr/bin/env node
// Exporta contas e lançamentos do openfinance-analyst em JSON, para o fluxo
// de caixa do JARVIS (friday/fluxo.py).
//
// Por que não pelas ferramentas MCP: nenhuma delas devolve os lançamentos
// crus, e a ponte corta respostas em 12 mil caracteres. Por que não mudar o
// projeto do colega: é dele. Este script só IMPORTA o código compilado dele
// (a mesma chave, o mesmo banco cifrado) e não escreve nada.
//
// Uso: node scripts/ofa_export.mjs <dist do openfinance-analyst> <de> <até>
// As credenciais PLUGGY_* vêm do ambiente, como para o servidor MCP.

import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

const [dist, de, ate] = process.argv.slice(2)
if (!dist || !de || !ate) {
  console.error('uso: ofa_export.mjs <dist> <de YYYY-MM-DD> <até YYYY-MM-DD>')
  process.exit(2)
}

const carregar = (rel) => import(pathToFileURL(join(dist, rel)).href)

try {
  const { loadConfig } = await carregar('config.js')
  const { getOrCreateKey } = await carregar('store/key.js')
  const { openDb } = await carregar('store/db.js')
  const { Repo } = await carregar('store/repo.js')

  const config = loadConfig()
  const repo = new Repo(openDb(config.dbPath, getOrCreateKey()))

  const contas = repo.listAccounts().map((a) => ({
    id: a.id,
    nome: a.name,
    tipo: a.kind,
    saldo: a.balance,
    limite: a.creditLimit,
    fechamento: a.closeDate,
    vencimento: a.dueDate,
  }))
  const transacoes = repo.queryTransactions({ from: de, to: ate }).map((t) => ({
    conta: t.accountId,
    data: t.date,
    valor: t.amount,
    descricao: t.description,
    categoria: t.category,
    parcela: t.installmentNumber,
    parcelas: t.installmentTotal,
    fatura: t.billForecastDate,
    status: t.status,
  }))
  process.stdout.write(JSON.stringify({ contas, transacoes }))
} catch (err) {
  // Só a mensagem: exceção de SDK pode carregar credencial.
  console.error(err instanceof Error ? err.message : 'falha ao ler o openfinance-analyst')
  process.exit(1)
}
