from datetime import date, datetime, timedelta
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from ..booking_core import (
    available_days,
    book,
    master_ids,
    masters_with_time,
    normalize_phone,
    slots_for,
    summary,
)
from ..catalog import MASTER_BY_ID, MASTERS, SERVICE_BY_ID, SHOP_PHONE, TZ, format_price
from ..clock import now
from ..config import Config
from ..db import Database
from ..notify import notify_admins
from ..texts import ASK_PHONE, booking_card, when

router = Router(name="booking")


class BookingFlow(StatesGroup):
    choosing = State()
    phone = State()


def _master_ids(data: dict) -> list[str]:
    return master_ids(data["master"])


async def _slots(db: Database, day: date, service_id: str, masters: list[str]) -> dict[datetime, list[str]]:
    return await slots_for(db, day, service_id, masters, now())


async def _available_days(db: Database, service_id: str, masters: list[str]) -> list[date]:
    return await available_days(db, service_id, masters, now())


_summary = summary


async def show_services(message: Message, edit: bool) -> None:
    text = "Выберите услугу:"
    if edit:
        await message.edit_text(text, reply_markup=kb.services())
    else:
        await message.answer(text, reply_markup=kb.services())


async def show_masters(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    available = await masters_with_time(db, data["service"], now())
    if not available:
        await message.edit_text(
            f"{_summary(data)}\n\nНа ближайшую неделю на эту услугу всё занято. "
            f"Позвоните нам, постараемся найти время: {SHOP_PHONE}",
            reply_markup=kb.services(),
        )
        return
    await message.edit_text(f"{_summary(data)}\n\nК кому записать?", reply_markup=kb.masters(available))


async def show_days(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    days = await _available_days(db, data["service"], _master_ids(data))
    if not days:
        await message.edit_text(
            f"{_summary(data)}\n\nУ этого мастера на неделю вперёд всё занято. Выберите другого:",
            reply_markup=kb.masters({m.id for m in MASTERS}),
        )
        return
    today = now().date()
    await message.edit_text(f"{_summary(data)}\n\nВыберите день:", reply_markup=kb.days(days, today))


async def show_times(message: Message, state: FSMContext, db: Database, note: str = "") -> None:
    data = await state.get_data()
    slots = await _slots(db, date.fromisoformat(data["day"]), data["service"], _master_ids(data))
    if not slots:
        await show_days(message, state, db)
        return
    prefix = f"{note}\n\n" if note else ""
    await message.edit_text(
        f"{prefix}{_summary(data)}\n\nВыберите время:",
        reply_markup=kb.times(list(slots)),
    )


@router.message(Command("book"))
@router.message(F.text == kb.BTN_BOOK)
async def start_booking(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(BookingFlow.choosing)
    await show_services(message, edit=False)


async def _session_ok(callback: CallbackQuery, state: FSMContext, *keys: str) -> bool:
    """After a restart the FSM data is gone; buttons from old messages restart the flow."""
    data = await state.get_data()
    if all(k in data for k in keys):
        return True
    await state.set_state(BookingFlow.choosing)
    await callback.message.edit_text(
        "Эта запись устарела, давайте начнём заново. Выберите услугу:", reply_markup=kb.services()
    )
    await callback.answer()
    return False


@router.callback_query(kb.ServiceCb.filter())
async def on_service(callback: CallbackQuery, callback_data: kb.ServiceCb, state: FSMContext, db: Database) -> None:
    await state.set_state(BookingFlow.choosing)
    await state.set_data({"service": callback_data.id})
    await show_masters(callback.message, state, db)
    await callback.answer()


@router.callback_query(kb.MasterCb.filter())
async def on_master(callback: CallbackQuery, callback_data: kb.MasterCb, state: FSMContext, db: Database) -> None:
    if not await _session_ok(callback, state, "service"):
        return
    await state.update_data(master=callback_data.id, day=None)
    await show_days(callback.message, state, db)
    await callback.answer()


@router.callback_query(kb.DayCb.filter())
async def on_day(callback: CallbackQuery, callback_data: kb.DayCb, state: FSMContext, db: Database) -> None:
    if not await _session_ok(callback, state, "service", "master"):
        return
    await state.update_data(day=callback_data.iso)
    await show_times(callback.message, state, db)
    await callback.answer()


@router.callback_query(kb.TimeCb.filter())
async def on_time(callback: CallbackQuery, callback_data: kb.TimeCb, state: FSMContext, db: Database) -> None:
    if not await _session_ok(callback, state, "service", "master", "day"):
        return
    data = await state.get_data()
    day = date.fromisoformat(data["day"])
    start = datetime(day.year, day.month, day.day, int(callback_data.hhmm[:2]), int(callback_data.hhmm[2:]), tzinfo=TZ)

    slots = await _slots(db, day, data["service"], _master_ids(data))
    free_masters = slots.get(start)
    if not free_masters:
        await show_times(callback.message, state, db, note="Это время уже заняли.")
        await callback.answer("Время уже заняли, выберите другое")
        return

    master_id = free_masters[0]
    await state.update_data(start=start.isoformat(), assigned=master_id)

    service = SERVICE_BY_ID[data["service"]]
    end = start + timedelta(minutes=service.minutes)
    await callback.message.edit_text(
        "<b>Проверьте запись</b>\n\n"
        f"{escape(service.title)}\n"
        f"{when(start)}–{end:%H:%M}\n"
        f"Мастер: {escape(MASTER_BY_ID[master_id].name)}\n"
        f"Стоимость: {format_price(service.price)}, оплата в салоне",
        reply_markup=kb.confirm(),
    )
    await callback.answer()


@router.callback_query(kb.NavCb.filter(F.to.in_({"services", "masters", "days", "times"})))
async def on_back(callback: CallbackQuery, callback_data: kb.NavCb, state: FSMContext, db: Database) -> None:
    needs = {
        "services": (),
        "masters": ("service",),
        "days": ("service", "master"),
        "times": ("service", "master", "day"),
    }[callback_data.to]
    if not await _session_ok(callback, state, *needs):
        return
    if callback_data.to == "services":
        await show_services(callback.message, edit=True)
    elif callback_data.to == "masters":
        await show_masters(callback.message, state, db)
    elif callback_data.to == "days":
        await show_days(callback.message, state, db)
    else:
        await show_times(callback.message, state, db)
    await callback.answer()


@router.callback_query(kb.NavCb.filter(F.to == "abort"))
async def on_abort(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Запись не оформлена. Нажмите «Записаться», когда будет удобно.")
    await callback.answer()


@router.callback_query(kb.NavCb.filter(F.to == "confirm"))
async def on_confirm(callback: CallbackQuery, state: FSMContext, db: Database, bot: Bot, config: Config) -> None:
    if not await _session_ok(callback, state, "service", "master", "day", "start", "assigned"):
        return
    user = callback.from_user
    await db.save_client(user.id, user.full_name)
    await callback.answer()

    if await db.client_phone(user.id):
        await callback.message.edit_reply_markup(reply_markup=None)
        await finalize(callback.message, state, db, bot, config, user.id)
        return

    await state.set_state(BookingFlow.phone)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(ASK_PHONE, reply_markup=kb.phone_request())


@router.message(BookingFlow.phone, F.text == kb.BTN_CANCEL_INPUT)
async def phone_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Запись не оформлена.", reply_markup=kb.main_menu())


@router.message(BookingFlow.phone, F.contact)
@router.message(BookingFlow.phone, F.text)
async def on_phone(message: Message, state: FSMContext, db: Database, bot: Bot, config: Config) -> None:
    raw = message.contact.phone_number if message.contact else message.text
    phone = normalize_phone(raw)
    if not phone:
        await message.answer(
            "Не получилось разобрать номер. Напишите его в виде +7 900 123-45-67 или нажмите «Отправить мой номер».",
            reply_markup=kb.phone_request(),
        )
        return
    await db.save_client(message.from_user.id, message.from_user.full_name, phone)
    await finalize(message, state, db, bot, config, message.from_user.id)


async def finalize(message: Message, state: FSMContext, db: Database, bot: Bot, config: Config, user_id: int) -> None:
    data = await state.get_data()
    service = SERVICE_BY_ID[data["service"]]
    start = datetime.fromisoformat(data["start"])
    booking_id = await book(
        db,
        platform="tg",
        user_id=user_id,
        service_id=service.id,
        master_choice=data["master"],
        assigned=data["assigned"],
        start=start,
        now=now(),
    )

    if booking_id is None:
        await state.set_state(BookingFlow.choosing)
        await message.answer("Пока вы подтверждали, это время заняли. Выберите другое:", reply_markup=kb.main_menu())
        slots = await _slots(db, start.date(), service.id, _master_ids(data))
        if slots:
            await message.answer(f"{_summary(data)}\n\nВыберите время:", reply_markup=kb.times(list(slots)))
        else:
            await message.answer(
                "На этот день свободного времени не осталось. Выберите услугу заново:", reply_markup=kb.services()
            )
        return

    await state.clear()
    booking = await db.get(booking_id)
    await message.answer(
        f"Готово, вы записаны.\n\n{booking_card(booking)}\n\n"
        "Напомним за день и за 2 часа до визита. Отменить можно в разделе «Мои записи».",
        reply_markup=kb.main_menu(),
    )
    await notify_admins(bot, config.admin_ids, "Новая запись", booking)
