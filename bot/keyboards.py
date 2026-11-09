from datetime import date, datetime

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .catalog import MASTERS, SERVICES, format_duration, format_price
from .texts import day_button


class ServiceCb(CallbackData, prefix="svc"):
    id: str


class MasterCb(CallbackData, prefix="mst"):
    id: str  # id мастера или "any"


class DayCb(CallbackData, prefix="day"):
    iso: str


class TimeCb(CallbackData, prefix="tm"):
    hhmm: str  # "1530"


class NavCb(CallbackData, prefix="nav"):
    to: str  # services | masters | days | times | confirm | abort


class CancelCb(CallbackData, prefix="cnl"):
    booking_id: int
    sure: bool = False


BTN_BOOK = "Записаться"
BTN_MY = "Мои записи"
BTN_PRICES = "Услуги и цены"
BTN_CONTACTS = "Контакты"
BTN_CANCEL_INPUT = "Отменить запись"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_BOOK), KeyboardButton(text=BTN_MY)],
            [KeyboardButton(text=BTN_PRICES), KeyboardButton(text=BTN_CONTACTS)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )


def phone_request() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Отправить мой номер", request_contact=True)],
            [KeyboardButton(text=BTN_CANCEL_INPUT)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def services() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for s in SERVICES:
        kb.button(
            text=f"{s.title} · {format_duration(s.minutes)} · {format_price(s.price)}",
            callback_data=ServiceCb(id=s.id),
        )
    kb.adjust(1)
    return kb.as_markup()


def masters(available_ids: set[str]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Любой свободный мастер", callback_data=MasterCb(id="any"))
    for m in MASTERS:
        if m.id in available_ids:
            kb.button(text=f"{m.name} — {m.about}", callback_data=MasterCb(id=m.id))
    kb.button(text="← Другая услуга", callback_data=NavCb(to="services"))
    kb.adjust(1)
    return kb.as_markup()


def days(available: list[date], today: date) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for d in available:
        kb.button(text=day_button(d, today), callback_data=DayCb(iso=d.isoformat()))
    kb.adjust(3)
    kb.row(*InlineKeyboardBuilder().button(text="← Другой мастер", callback_data=NavCb(to="masters")).buttons)
    return kb.as_markup()


def times(starts: list[datetime]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for t in starts:
        kb.button(text=f"{t:%H:%M}", callback_data=TimeCb(hhmm=f"{t:%H%M}"))
    kb.adjust(4)
    kb.row(*InlineKeyboardBuilder().button(text="← Другой день", callback_data=NavCb(to="days")).buttons)
    return kb.as_markup()


def confirm() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Подтвердить запись", callback_data=NavCb(to="confirm"))
    kb.button(text="← Другое время", callback_data=NavCb(to="times"))
    kb.button(text="Отменить", callback_data=NavCb(to="abort"))
    kb.adjust(1, 2)
    return kb.as_markup()


def my_bookings(items: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for booking_id, label in items:
        kb.button(text=f"Отменить: {label}", callback_data=CancelCb(booking_id=booking_id))
    kb.adjust(1)
    return kb.as_markup()


def cancel_confirm(booking_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Да, отменить", callback_data=CancelCb(booking_id=booking_id, sure=True))
    kb.button(text="Нет, оставить", callback_data=NavCb(to="keep"))
    kb.adjust(2)
    return kb.as_markup()
