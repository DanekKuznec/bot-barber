from datetime import timedelta

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from ..catalog import LATE_CANCEL_MIN
from ..clock import now
from ..config import Config
from ..db import Database
from ..notify import notify_admins
from ..texts import WEEKDAYS_SHORT, booking_card

router = Router(name="my_bookings")


async def _render(user_id: int, db: Database) -> tuple[str, object]:
    items = await db.upcoming_for_user(user_id, now())
    if not items:
        return "Предстоящих записей нет. Нажмите «Записаться», чтобы выбрать время.", None
    text = "<b>Ваши записи</b>\n\n" + "\n\n".join(booking_card(b) for b in items)
    buttons = [(b.id, f"{WEEKDAYS_SHORT[b.starts_at.weekday()]} {b.starts_at:%d.%m, %H:%M}") for b in items]
    return text, kb.my_bookings(buttons)


@router.message(Command("my"))
@router.message(F.text == kb.BTN_MY)
async def my_bookings(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    text, markup = await _render(message.from_user.id, db)
    await message.answer(text, reply_markup=markup)


async def _own_active(callback: CallbackQuery, booking_id: int, db: Database):
    booking = await db.get(booking_id)
    if not booking or booking.user_id != callback.from_user.id or booking.status != "active":
        await callback.answer("Эта запись уже отменена или не найдена", show_alert=True)
        return None
    if booking.starts_at <= now():
        await callback.answer("Эта запись уже началась или прошла", show_alert=True)
        return None
    return booking


@router.callback_query(kb.CancelCb.filter(F.sure.is_(False)))
async def ask_cancel(callback: CallbackQuery, callback_data: kb.CancelCb, db: Database) -> None:
    booking = await _own_active(callback, callback_data.booking_id, db)
    if not booking:
        return
    await callback.message.edit_text(
        f"Отменить эту запись?\n\n{booking_card(booking)}",
        reply_markup=kb.cancel_confirm(booking.id),
    )
    await callback.answer()


@router.callback_query(kb.CancelCb.filter(F.sure.is_(True)))
async def do_cancel(
    callback: CallbackQuery, callback_data: kb.CancelCb, db: Database, bot: Bot, config: Config
) -> None:
    booking = await _own_active(callback, callback_data.booking_id, db)
    if not booking:
        return
    await db.cancel(booking.id)
    await callback.message.edit_text(f"Запись отменена.\n\n{booking_card(booking)}")
    await callback.answer("Запись отменена")
    late = booking.starts_at - now() < timedelta(minutes=LATE_CANCEL_MIN)
    title = "Клиент отменил запись" + (" — меньше чем за 2 часа до визита" if late else "")
    await notify_admins(bot, config.admin_ids, title, booking)


@router.callback_query(kb.NavCb.filter(F.to == "keep"))
async def keep(callback: CallbackQuery, db: Database) -> None:
    text, markup = await _render(callback.from_user.id, db)
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer("Запись осталась в силе")
