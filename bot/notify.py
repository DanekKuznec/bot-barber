import logging
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from .db import Booking
from .texts import booking_card

log = logging.getLogger(__name__)


def client_line(b: Booking) -> str:
    phone = escape(b.client_phone) if b.client_phone else "телефон не указан"
    return f"Клиент: {escape(b.client_name)}, {phone}"


async def notify_admins(bot: Bot, admin_ids: frozenset[int], title: str, booking: Booking) -> None:
    text = f"<b>{escape(title)}</b> · № {booking.id}\n\n{booking_card(booking)}\n{client_line(booking)}"
    for admin_id in admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except TelegramAPIError:
            # admin hasn't started the bot or blocked it; the booking itself is fine
            log.warning("Не удалось отправить уведомление администратору %s", admin_id)
