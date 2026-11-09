"""Entry point: `python -m bot`. Starts every channel that has a token in .env."""

import asyncio
import contextlib
import logging

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from .config import Config, load_config
from .db import Database
from .handlers import admin, booking, common, my_bookings
from .reminders import Sender, reminder_loop
from .vk.api import VkApi
from .vk.bot import VkBookingBot

log = logging.getLogger("bot")


def build_dispatcher(config: Config, db: Database) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp["db"] = db
    dp["config"] = config

    dp.include_routers(
        common.router,
        my_bookings.router,
        admin.make_router(config.admin_ids),
        booking.router,
        common.fallback_router,  # must stay last
    )
    return dp


async def run_telegram(config: Config, db: Database, senders: dict[str, Sender]) -> None:
    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await bot.set_my_commands(
        [
            BotCommand(command="book", description="Записаться"),
            BotCommand(command="my", description="Мои записи"),
            BotCommand(command="prices", description="Услуги и цены"),
            BotCommand(command="contacts", description="Адрес и телефон"),
        ]
    )

    async def send(user_id: int, html_text: str) -> None:
        await bot.send_message(user_id, html_text)

    senders["tg"] = send
    try:
        await build_dispatcher(config, db).start_polling(bot, handle_signals=False)
    finally:
        await bot.session.close()


async def run_vk(config: Config, db: Database, senders: dict[str, Sender]) -> None:
    async with aiohttp.ClientSession() as session:
        vk = VkBookingBot(VkApi(config.vk_token, session), db, config.vk_admin_ids)
        senders["vk"] = vk.send_reminder
        await vk.run(config.vk_group_id)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()

    db = Database(config.db_path)
    await db.init()

    senders: dict[str, Sender] = {}
    channels = []
    if config.bot_token:
        channels.append(run_telegram(config, db, senders))
    if config.vk_token:
        channels.append(run_vk(config, db, senders))
    log.info(
        "Запускаем: %s",
        ", ".join(name for name, on in (("Telegram", config.bot_token), ("ВКонтакте", config.vk_token)) if on),
    )

    reminders = asyncio.create_task(reminder_loop(db, senders))
    try:
        await asyncio.gather(*channels)
    finally:
        reminders.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reminders


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
