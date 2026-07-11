"""Pesquisa na web (Google Search grounding do Gemini) e leitura de páginas."""

from __future__ import annotations

import re
from html.parser import HTMLParser

import httpx
from google.genai import types

from . import Tool, ToolContext, truncate


async def _search(ctx: ToolContext, query: str) -> str:
    # O grounding com Google Search não pode ser combinado com function calling
    # na mesma requisição, então a busca é uma chamada separada ao Gemini —
    # pela mesma escada de chaves/modelos do cérebro.
    from ..llm import GeminiPool

    llm = ctx.llm or GeminiPool(ctx.config)
    response = await llm.generate(
        contents=(
            "Pesquise na web e responda em português, de forma factual e concisa, "
            f"citando datas quando relevante: {query}"
        ),
        gen_config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )
    return truncate(response.text or "(a busca não retornou texto)")


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head"}

    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self.chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth and data.strip():
            self.chunks.append(data.strip())


async def _fetch(ctx: ToolContext, url: str) -> str:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (Friday)"})
    content_type = resp.headers.get("content-type", "")
    if "html" not in content_type:
        return truncate(resp.text)
    parser = _TextExtractor()
    parser.feed(resp.text)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(parser.chunks))
    return truncate(text)


SEARCH_TOOL = Tool(
    declaration={
        "name": "web_search",
        "description": "Pesquisa informações atuais na web (notícias, clima, fatos, preços).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "O que pesquisar."}
            },
            "required": ["query"],
        },
    },
    handler=_search,
)

FETCH_TOOL = Tool(
    declaration={
        "name": "fetch_url",
        "description": "Baixa uma página web e retorna o texto dela.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "url": {"type": "STRING", "description": "URL completa da página."}
            },
            "required": ["url"],
        },
    },
    handler=_fetch,
)
