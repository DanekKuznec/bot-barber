from datetime import date, timedelta
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from ..catalog import MASTER_BY_ID
from ..clock import now
from ..db import Database
from ..texts import schedule


def make_router(admin_ids: frozenset[int]) -> Router:
    router = Router(name="admin")
    router.message.filter(F.from_user.id.in_(admin_ids))

    async def send_schedule(message: Message, db: Database, day: date) -> None:
        await message.answer(schedule(day, await db.for_day(day)))

    @router.message(Command("today"))
    async def today(message: Message, db: Database) -> None:
        await send_schedule(message, db, now().date())

    @router.message(Command("tomorrow"))
    async def tomorrow(message: Message, db: Database) -> None:
        await send_schedule(message, db, now().date() + timedelta(days=1))

    @router.message(Command("admin"))
    async def help_(message: Message) -> None:
        names = ", ".join(m.name for m in MASTER_BY_ID.values())
        await message.answer(
            "<b>Команды администратора</b>\n"
            "/today — записи на сегодня\n"
            "/tomorrow — записи на завтра\n\n"
            f"Уведомления о новых записях и отменах приходят сюда автоматически. Мастера: {escape(names)}."
        )

    return router
