import re
from datetime import date, datetime
from html import escape, unescape

from .catalog import (
    CLOSES_AT,
    MASTER_BY_ID,
    MASTERS,
    OPENS_AT,
    SERVICE_BY_ID,
    SERVICES,
    SHOP_ADDRESS,
    SHOP_NAME,
    SHOP_PHONE,
    format_duration,
    format_price,
)
from .db import Booking

WEEKDAYS_SHORT = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
WEEKDAYS_FULL = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
MONTHS_GEN = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def day_button(day: date, today: date) -> str:
    if day == today:
        return "Сегодня"
    if (day - today).days == 1:
        return "Завтра"
    return f"{WEEKDAYS_SHORT[day.weekday()]}, {day.day} {MONTHS_GEN[day.month - 1][:3]}"


def day_long(day: date) -> str:
    return f"{WEEKDAYS_FULL[day.weekday()]}, {day.day} {MONTHS_GEN[day.month - 1]}"


def when(dt: datetime) -> str:
    return f"{day_long(dt.date())}, {dt:%H:%M}"


def booking_card(b: Booking) -> str:
    service = SERVICE_BY_ID[b.service_id]
    master = MASTER_BY_ID[b.master_id]
    return (
        f"<b>{escape(service.title)}</b>\n"
        f"{when(b.starts_at)}–{b.ends_at:%H:%M}\n"
        f"Мастер: {escape(master.name)}\n"
        f"Стоимость: {format_price(service.price)}"
    )


WELCOME = (
    f"Здравствуйте! Это бот {SHOP_NAME}.\n\n"
    "Здесь можно записаться к мастеру за минуту, посмотреть свои записи и при необходимости отменить их."
)


def price_list() -> str:
    lines = ["<b>Услуги и цены</b>\n"]
    for s in SERVICES:
        lines.append(f"{escape(s.title)} — {format_price(s.price)}, {format_duration(s.minutes)}")
    lines.append("\n<b>Мастера</b>")
    for m in MASTERS:
        days = ", ".join(WEEKDAYS_SHORT[d] for d in sorted(m.workdays))
        lines.append(f"{escape(m.name)} — {escape(m.about)} ({days})")
    return "\n".join(lines)


CONTACTS = f"<b>{SHOP_NAME}</b>\n{SHOP_ADDRESS}\nКаждый день {OPENS_AT:%H:%M}–{CLOSES_AT:%H:%M}\n{SHOP_PHONE}"

ASK_PHONE = (
    "Остался номер телефона — по нему мастер свяжется, если что-то поменяется.\n"
    "Нажмите кнопку ниже или напишите номер сообщением."
)


PLATFORM_LABEL = {"tg": "Telegram", "vk": "ВКонтакте"}


def plain(html_text: str) -> str:
    """VK messages have no markup, so strip the <b> tags used for Telegram."""
    return unescape(re.sub(r"</?b>", "", html_text))


def schedule(day: date, bookings: list[Booking]) -> str:
    title = f"<b>Расписание: {day_long(day)}</b>"
    if not bookings:
        return f"{title}\n\nЗаписей нет."
    blocks = []
    for master in MASTERS:
        own = [b for b in bookings if b.master_id == master.id]
        if not own:
            continue
        lines = [f"<b>{escape(master.name)}</b>"]
        for b in own:
            phone = escape(b.client_phone) if b.client_phone else "без телефона"
            lines.append(
                f"{b.starts_at:%H:%M}–{b.ends_at:%H:%M} · {escape(SERVICE_BY_ID[b.service_id].title)} · "
                f"{escape(b.client_name)}, {phone} ({PLATFORM_LABEL[b.platform]})"
            )
        blocks.append("\n".join(lines))
    return f"{title}\nВсего записей: {len(bookings)}\n\n" + "\n\n".join(blocks)
