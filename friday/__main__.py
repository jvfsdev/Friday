"""Ponto de entrada: `python -m friday` (Telegram) ou `python -m friday --cli`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .brain import Brain
from .config import load_config
from .monitor import FridayMonitor
from .net import prefer_ipv4_if_needed
from .scheduler import FridayScheduler
from .tools import ToolContext, build_tools

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)


async def run_cli(config):
    from rich.console import Console
    from rich.markdown import Markdown

    console = Console()

    async def confirm(description: str) -> bool:
        console.print(f"\n[bold yellow]⚠️  Confirmação necessária:[/] {description}")
        answer = await asyncio.to_thread(input, "Confirmar? [s/N] ")
        return answer.strip().lower() in ("s", "sim", "y", "yes")

    async def send(text: str):
        console.print(Markdown(text))

    from .llm import GeminiPool
    from .notifier import Notifier

    from .jobs import JobRegistry

    jobs = JobRegistry(notify=send)
    ctx = ToolContext(config=config, confirm=confirm, send=send, llm=GeminiPool(config), jobs=jobs)
    tools = build_tools(config)
    if config.mcp_servers:
        from .mcp_bridge import McpBridge

        tools.update(await McpBridge(config).start())
    brain = Brain(config, tools, ctx)

    notifier = Notifier(config, send)
    scheduler = FridayScheduler(config, brain, notifier.send)
    tools[scheduler.reminder_tool().declaration["name"]] = scheduler.reminder_tool()
    scheduler.start()
    notifier.attach(scheduler.scheduler)
    FridayMonitor(config, brain, notifier.send, scheduler.scheduler).start()

    console.print("[bold cyan]JARVIS[/] online. ('sair' para encerrar, '/reset' para zerar)\n")
    while True:
        try:
            user = (await asyncio.to_thread(input, "você> ")).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user:
            continue
        if user.lower() in ("sair", "exit", "quit"):
            break
        if user == "/reset":
            brain.reset()
            console.print("[dim]conversa zerada[/]")
            continue
        with console.status("[cyan]pensando…[/]"):
            answer = await brain.ask(user)
        console.print(Markdown(answer))
        console.print()


async def run_telegram(config):
    from .telegram_bot import TelegramInterface

    from .llm import GeminiPool
    from .notifier import Notifier

    from .jobs import JobRegistry

    interface = TelegramInterface(config)
    jobs = JobRegistry(notify=interface.send)
    ctx = ToolContext(
        config=config, confirm=interface.confirm, send=interface.send,
        llm=GeminiPool(config), send_voice=interface.send_voice, jobs=jobs,
    )
    tools = build_tools(config)
    if config.mcp_servers:
        from .mcp_bridge import McpBridge

        tools.update(await McpBridge(config).start())
    brain = Brain(config, tools, ctx)
    interface.brain = brain

    notifier = Notifier(config, interface.send)
    scheduler = FridayScheduler(config, brain, notifier.send)
    tools[scheduler.reminder_tool().declaration["name"]] = scheduler.reminder_tool()
    scheduler.start()
    notifier.attach(scheduler.scheduler)
    FridayMonitor(config, brain, notifier.send, scheduler.scheduler).start()

    if config.voice_enabled:
        from .voice import VoiceLoop

        asyncio.create_task(VoiceLoop(config, brain).run())

    await interface.run_forever()


def main():
    parser = argparse.ArgumentParser(prog="friday")
    parser.add_argument("--cli", action="store_true", help="chat no terminal (modo de teste)")
    args = parser.parse_args()

    prefer_ipv4_if_needed()
    config = load_config()
    if not config.gemini_api_key:
        sys.exit(
            "GEMINI_API_KEY não configurada.\n"
            "Crie sua chave gratuita em https://aistudio.google.com/apikey ,\n"
            "copie .env.example para .env e preencha."
        )
    if not args.cli and (not config.telegram_bot_token or not config.telegram_user_id):
        sys.exit(
            "TELEGRAM_BOT_TOKEN/TELEGRAM_USER_ID não configurados no .env.\n"
            "Crie o bot com o @BotFather e descubra seu ID com o @userinfobot,\n"
            "ou rode em modo terminal: python -m friday --cli"
        )

    try:
        asyncio.run(run_cli(config) if args.cli else run_telegram(config))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
