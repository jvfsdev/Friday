"""Navegador de verdade (Playwright/Chromium) para sites que exigem JavaScript.

Dependência opcional e pesada: só é registrada se `playwright` estiver
instalado (pip install playwright && playwright install chromium).
"""

from __future__ import annotations

import importlib.util
import re

from . import Tool, ToolContext, ToolMedia, truncate

TIMEOUT_MS = 30000


def available() -> bool:
    return importlib.util.find_spec("playwright") is not None


async def _render(url: str, screenshot: bool):
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport={"width": 1280, "height": 900})
            await page.goto(url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
            await page.wait_for_timeout(2500)  # deixa o JS assentar
            if screenshot:
                return await page.screenshot(type="png", full_page=False)
            return await page.inner_text("body")
        finally:
            await browser.close()


async def _browse(ctx: ToolContext, url: str) -> str:
    try:
        text = await _render(url, screenshot=False)
    except Exception as exc:
        return f"Falha ao abrir {url}: {type(exc).__name__}: {exc}"
    return truncate(re.sub(r"\n{3,}", "\n\n", text))


async def _browse_screenshot(ctx: ToolContext, url: str) -> str | ToolMedia:
    try:
        png = await _render(url, screenshot=True)
    except Exception as exc:
        return f"Falha ao abrir {url}: {type(exc).__name__}: {exc}"
    return ToolMedia(
        note=f"Captura de tela de {url} anexada — analise-a visualmente.",
        data=png,
        mime="image/png",
    )


BROWSE_TOOL = Tool(
    declaration={
        "name": "browse",
        "description": (
            "Abre uma página num navegador de verdade (executa JavaScript) e retorna o "
            "texto renderizado. Use quando fetch_url não trouxer o conteúdo."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {"url": {"type": "STRING", "description": "URL completa."}},
            "required": ["url"],
        },
    },
    handler=_browse,
)

SCREENSHOT_TOOL = Tool(
    declaration={
        "name": "browse_screenshot",
        "description": "Abre uma página no navegador e tira uma captura de tela para você VER o site.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"url": {"type": "STRING", "description": "URL completa."}},
            "required": ["url"],
        },
    },
    handler=_browse_screenshot,
)
