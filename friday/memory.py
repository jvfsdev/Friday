"""Memória persistente da Friday: arquivos markdown em memory/."""

from __future__ import annotations

from datetime import datetime

from .config import MEMORY_DIR

FACTS_FILE = MEMORY_DIR / "fatos.md"


def load_memory() -> str:
    """Concatena todos os .md da memória para entrar no system prompt."""
    parts = []
    for path in sorted(MEMORY_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8").strip()
        if text:
            parts.append(f"## {path.stem}\n{text}")
    if not parts:
        return "(memória vazia por enquanto)"
    return "\n\n".join(parts)


def remember(fact: str) -> str:
    MEMORY_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    with FACTS_FILE.open("a", encoding="utf-8") as f:
        f.write(f"- ({stamp}) {fact.strip()}\n")
    return "Anotado na memória."
