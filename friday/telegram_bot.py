"""Interface principal: bot do Telegram, restrito ao dono."""

from __future__ import annotations

import asyncio
import logging
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .brain import Brain
from .config import Config

log = logging.getLogger("friday.telegram")

CONFIRM_TIMEOUT = 120
MAX_MESSAGE = 4000


class TelegramInterface:
    def __init__(self, config: Config):
        self.config = config
        self.brain: Brain | None = None  # injetado depois (dependência circular com confirm)
        self.app = (
            Application.builder()
            .token(config.telegram_bot_token)
            .job_queue(None)  # usamos nosso próprio scheduler (FridayScheduler)
            # Sem isso os updates são processados em fila e o clique em
            # Confirmar/Cancelar fica preso atrás da própria espera pela confirmação.
            .concurrent_updates(True)
            .connect_timeout(30)
            .read_timeout(30)
            .write_timeout(30)
            .build()
        )
        self._pending: dict[str, asyncio.Future[bool]] = {}

        owner = filters.User(user_id=config.telegram_user_id)
        self.app.add_handler(CommandHandler("start", self._cmd_start, filters=owner))
        self.app.add_handler(CommandHandler("reset", self._cmd_reset, filters=owner))
        self.app.add_handler(MessageHandler(owner & filters.TEXT & ~filters.COMMAND, self._on_message))
        self.app.add_handler(MessageHandler(owner & (filters.VOICE | filters.AUDIO), self._on_voice))
        self.app.add_handler(MessageHandler(owner & filters.PHOTO, self._on_photo))
        self.app.add_handler(CallbackQueryHandler(self._on_button))

    # ---- envio proativo (rotinas/lembretes) ----

    async def send(self, text: str):
        for chunk in _split(text):
            await self.app.bot.send_message(chat_id=self.config.telegram_user_id, text=chunk)

    # ---- confirmação de ações perigosas ----

    async def confirm(self, description: str) -> bool:
        token = uuid.uuid4().hex[:12]
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending[token] = future
        keyboard = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("✅ Confirmar", callback_data=f"ok:{token}"),
                InlineKeyboardButton("❌ Cancelar", callback_data=f"no:{token}"),
            ]]
        )
        await self.app.bot.send_message(
            chat_id=self.config.telegram_user_id,
            text=f"⚠️ Preciso da sua confirmação, chefe:\n\n{description}",
            reply_markup=keyboard,
        )
        try:
            return await asyncio.wait_for(future, timeout=CONFIRM_TIMEOUT)
        except asyncio.TimeoutError:
            return False
        finally:
            self._pending.pop(token, None)

    async def _on_button(self, update: Update, _: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        if update.effective_user.id != self.config.telegram_user_id:
            return
        action, _, token = (query.data or "").partition(":")
        future = self._pending.get(token)
        if future and not future.done():
            future.set_result(action == "ok")
            verdict = "Confirmado ✅" if action == "ok" else "Cancelado ❌"
            await query.edit_message_text(f"{query.message.text}\n\n{verdict}")

    # ---- conversa ----

    async def _cmd_start(self, update: Update, _: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Online, chefe. No que posso ajudar?")

    async def _cmd_reset(self, update: Update, _: ContextTypes.DEFAULT_TYPE):
        self.brain.reset()
        await update.message.reply_text("Conversa zerada.")

    async def _on_message(self, update: Update, _: ContextTypes.DEFAULT_TYPE):
        await self._respond(update, update.message.text)

    async def _on_voice(self, update: Update, _: ContextTypes.DEFAULT_TYPE):
        voice = update.message.voice or update.message.audio
        file = await voice.get_file()
        data = await file.download_as_bytearray()
        mime = getattr(voice, "mime_type", None) or "audio/ogg"
        text = update.message.caption or "[Mensagem de voz do chefe — ouça e atenda ao que ele pedir.]"
        await self._respond(update, text, media=[(bytes(data), mime)])

    async def _on_photo(self, update: Update, _: ContextTypes.DEFAULT_TYPE):
        photo = update.message.photo[-1]  # maior resolução disponível
        file = await photo.get_file()
        data = await file.download_as_bytearray()
        text = update.message.caption or "[Foto enviada pelo chefe — analise e comente o que for útil.]"
        await self._respond(update, text, media=[(bytes(data), "image/jpeg")])

    async def _respond(self, update: Update, text: str, media=None):
        chat_id = update.effective_chat.id
        typing = asyncio.create_task(self._keep_typing(chat_id))
        try:
            answer = await self.brain.ask(text, media=media)
        finally:
            typing.cancel()
        for chunk in _split(answer):
            await update.message.reply_text(chunk)

    async def _keep_typing(self, chat_id: int):
        try:
            while True:
                await self.app.bot.send_chat_action(chat_id, ChatAction.TYPING)
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass

    # ---- ciclo de vida (compartilha o event loop com o scheduler) ----

    async def run_forever(self):
        # Rede pode estar lenta/instável na subida (ex.: servidor acabou de ligar).
        for attempt in range(5):
            try:
                await self.app.initialize()
                break
            except TelegramError as exc:
                if attempt == 4:
                    raise
                log.warning("falha ao conectar no Telegram (%s), tentando de novo em 15s", exc)
                await asyncio.sleep(15)
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        log.info("bot do Telegram no ar")
        try:
            await asyncio.Event().wait()
        finally:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()


def _split(text: str) -> list[str]:
    text = text.strip() or "(sem resposta)"
    return [text[i : i + MAX_MESSAGE] for i in range(0, len(text), MAX_MESSAGE)]
