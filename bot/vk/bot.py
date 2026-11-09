import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ..booking_core import (
    ANY_MASTER,
    available_days,
    book,
    master_ids,
    masters_with_time,
    normalize_phone,
    slots_for,
    summary,
)
from ..catalog import LATE_CANCEL_MIN, MASTER_BY_ID, SERVICE_BY_ID, SHOP_PHONE, TZ, format_price
from ..clock import now
from ..db import Booking, Database
from ..texts import (
    ASK_PHONE,
    CONTACTS,
    WEEKDAYS_SHORT,
    WELCOME,
    booking_card,
    plain,
    price_list,
    schedule,
    when,
)
from . import keyboards as kb
from .api import VkApi, VkApiError

log = logging.getLogger(__name__)

PLATFORM = "vk"
CHAT_PEER_START = 2_000_000_000  # peer_id бесед начинается отсюда; бот работает только в личке


@dataclass
class Session:
    data: dict = field(default_factory=dict)
    waiting_phone: bool = False


class VkBookingBot:
    def __init__(self, api: VkApi, db: Database, admin_ids: frozenset[int]):
        self.api = api
        self.db = db
        self.admin_ids = admin_ids
        self.sessions: dict[int, Session] = {}

    async def handle(self, update: dict) -> None:
        kind = update.get("type")
        obj = update.get("object", {})
        if kind == "message_new":
            await self._on_message(obj["message"])
        elif kind == "message_event":
            await self._on_button(obj)

    async def _on_message(self, msg: dict) -> None:
        peer, user = msg["peer_id"], msg["from_id"]
        if peer >= CHAT_PEER_START:
            return
        text = (msg.get("text") or "").strip()
        try:
            cmd = json.loads(msg.get("payload") or "{}")
        except json.JSONDecodeError:
            cmd = {}
        cmd = cmd.get("cmd") or cmd.get("command")

        session = self.sessions.setdefault(user, Session())

        # menu buttons always work and reset any dialog in progress
        if cmd == "start" or text.lower() in {"начать", "старт", "/start"}:
            await self._save_client(user)
            self.sessions[user] = Session()
            await self.api.send(peer, plain(WELCOME), kb.main_menu())
        elif cmd == "book" or text == kb.MENU_BOOK:
            self.sessions[user] = Session()
            await self.api.send(peer, "Выберите услугу:", kb.services())
        elif cmd == "my" or text == kb.MENU_MY:
            self.sessions[user] = Session()
            await self._send_my(peer, user)
        elif cmd == "prices" or text == kb.MENU_PRICES:
            await self.api.send(peer, plain(price_list()), kb.main_menu())
        elif cmd == "contacts" or text == kb.MENU_CONTACTS:
            await self.api.send(peer, plain(CONTACTS), kb.main_menu())
        elif user in self.admin_ids and text in {"/today", "/tomorrow"}:
            day = now().date() + timedelta(days=1 if text == "/tomorrow" else 0)
            await self.api.send(peer, plain(schedule(day, await self.db.for_day(day))))
        elif session.waiting_phone:
            await self._on_phone(peer, user, text)
        else:
            await self.api.send(peer, "Я понимаю только кнопки меню. Выберите действие внизу.", kb.main_menu())

    async def _on_button(self, event: dict) -> None:
        user, peer, cmid = event["user_id"], event["peer_id"], event["conversation_message_id"]
        payload = event.get("payload") or {}
        if isinstance(payload, str):
            payload = json.loads(payload)
        action, value = payload.get("a"), payload.get("v")
        session = self.sessions.setdefault(user, Session())
        data = session.data
        snackbar = None

        handlers = {
            "svc": self._pick_service,
            "mst": self._pick_master,
            "day": self._pick_day,
            "tm": self._pick_time,
            "pg": self._page,
            "nav": self._back,
        }
        try:
            if action in handlers:
                snackbar = await handlers[action](peer, user, cmid, data, value)
            elif action == "ok":
                snackbar = await self._confirm(peer, user, cmid, session)
            elif action == "abort":
                self.sessions[user] = Session()
                await self.api.edit(peer, cmid, "Запись не оформлена. Нажмите «Записаться», когда будет удобно.")
            elif action == "cnl":
                snackbar = await self._ask_cancel(peer, user, cmid, int(value))
            elif action == "cnl!":
                snackbar = await self._do_cancel(peer, user, cmid, int(value))
            elif action == "keep":
                text, keyboard = await self._my_screen(user)
                await self.api.edit(peer, cmid, text, keyboard)
                snackbar = "Запись осталась в силе"
        finally:
            # always answer the event, otherwise the button keeps spinning
            try:
                await self.api.answer_event(event["event_id"], user, peer, snackbar)
            except VkApiError:
                log.warning("Не удалось ответить на нажатие кнопки")

    def _stale(self, data: dict, *keys: str) -> bool:
        return not all(data.get(k) for k in keys)

    async def _restart(self, peer: int, cmid: int) -> str:
        await self.api.edit(peer, cmid, "Эта запись устарела, давайте начнём заново. Выберите услугу:", kb.services())
        return "Начнём заново"

    async def _screen_masters(self, data: dict) -> tuple[str, str]:
        available = await masters_with_time(self.db, data["service"], now())
        if not available:
            return (
                plain(summary(data)) + f"\n\nНа ближайшую неделю на эту услугу всё занято. Позвоните нам: {SHOP_PHONE}",
                kb.services(),
            )
        return plain(summary(data)) + "\n\nК кому записать?", kb.masters(available)

    async def _screen_days(self, data: dict) -> tuple[str, str]:
        days = await available_days(self.db, data["service"], master_ids(data["master"]), now())
        if not days:
            available = await masters_with_time(self.db, data["service"], now())
            return (
                plain(summary(data)) + "\n\nУ этого мастера на неделю вперёд всё занято. Выберите другого:",
                kb.masters(available),
            )
        return plain(summary(data)) + "\n\nВыберите день:", kb.days(days, now().date())

    async def _screen_times(self, data: dict, note: str = "") -> tuple[str, str]:
        slots = await slots_for(
            self.db, date.fromisoformat(data["day"]), data["service"], master_ids(data["master"]), now()
        )
        if not slots:
            return await self._screen_days(data)
        keyboard, page, pages = kb.times(list(slots), data.get("page", 0))
        data["page"] = page
        paging = f" (страница {page + 1} из {pages})" if pages > 1 else ""
        prefix = f"{note}\n\n" if note else ""
        return f"{prefix}{plain(summary(data))}\n\nВыберите время{paging}:", keyboard

    async def _pick_service(self, peer, user, cmid, data, value) -> None:
        data.clear()
        data["service"] = value
        await self.api.edit(peer, cmid, *await self._screen_masters(data))

    async def _pick_master(self, peer, user, cmid, data, value):
        if self._stale(data, "service"):
            return await self._restart(peer, cmid)
        data.update(master=value, day=None, page=0)
        await self.api.edit(peer, cmid, *await self._screen_days(data))

    async def _pick_day(self, peer, user, cmid, data, value):
        if self._stale(data, "service", "master"):
            return await self._restart(peer, cmid)
        data.update(day=value, page=0)
        await self.api.edit(peer, cmid, *await self._screen_times(data))

    async def _page(self, peer, user, cmid, data, value):
        if self._stale(data, "service", "master", "day"):
            return await self._restart(peer, cmid)
        data["page"] = int(value)
        await self.api.edit(peer, cmid, *await self._screen_times(data))

    async def _pick_time(self, peer, user, cmid, data, value):
        if self._stale(data, "service", "master", "day"):
            return await self._restart(peer, cmid)
        day = date.fromisoformat(data["day"])
        start = datetime(day.year, day.month, day.day, int(value[:2]), int(value[2:]), tzinfo=TZ)
        slots = await slots_for(self.db, day, data["service"], master_ids(data["master"]), now())
        if start not in slots:
            await self.api.edit(peer, cmid, *await self._screen_times(data, "Это время уже заняли."))
            return "Время уже заняли, выберите другое"

        data.update(start=start.isoformat(), assigned=slots[start][0])
        service = SERVICE_BY_ID[data["service"]]
        end = start + timedelta(minutes=service.minutes)
        await self.api.edit(
            peer,
            cmid,
            "Проверьте запись\n\n"
            f"{service.title}\n{when(start)}–{end:%H:%M}\n"
            f"Мастер: {MASTER_BY_ID[data['assigned']].name}\n"
            f"Стоимость: {format_price(service.price)}, оплата в салоне",
            kb.confirm(),
        )

    async def _back(self, peer, user, cmid, data, value):
        needs = {
            "services": (),
            "masters": ("service",),
            "days": ("service", "master"),
            "times": ("service", "master", "day"),
        }.get(value)
        if needs is None or self._stale(data, *needs):
            return await self._restart(peer, cmid)
        screen = {
            "services": lambda: _const(("Выберите услугу:", kb.services())),
            "masters": lambda: self._screen_masters(data),
            "days": lambda: self._screen_days(data),
            "times": lambda: self._screen_times(data),
        }[value]
        await self.api.edit(peer, cmid, *await screen())

    async def _save_client(self, user: int, phone: str | None = None) -> None:
        try:
            name = await self.api.user_name(user)
        except VkApiError:
            name = f"id{user}"
        await self.db.save_client(user, name, phone, PLATFORM)

    async def _confirm(self, peer: int, user: int, cmid: int, session: Session):
        if self._stale(session.data, "service", "master", "day", "start", "assigned"):
            return await self._restart(peer, cmid)
        await self._save_client(user)
        # drop the buttons so the booking can't be confirmed twice
        await self.api.edit(peer, cmid, "Запись подтверждена, оформляем…")
        if await self.db.client_phone(user, PLATFORM):
            await self._finalize(peer, user, session)
        else:
            session.waiting_phone = True
            await self.api.send(
                peer,
                ASK_PHONE.replace("Нажмите кнопку ниже или напишите номер сообщением.", "Напишите номер сообщением."),
            )
        return None

    async def _on_phone(self, peer: int, user: int, text: str) -> None:
        phone = normalize_phone(text)
        if not phone:
            await self.api.send(peer, "Не получилось разобрать номер. Напишите его в виде +7 900 123-45-67.")
            return
        await self._save_client(user, phone)
        await self._finalize(peer, user, self.sessions[user])

    async def _finalize(self, peer: int, user: int, session: Session) -> None:
        data = session.data
        booking_id = await book(
            self.db,
            platform=PLATFORM,
            user_id=user,
            service_id=data["service"],
            master_choice=data["master"],
            assigned=data["assigned"],
            start=datetime.fromisoformat(data["start"]),
            now=now(),
        )
        session.waiting_phone = False
        if booking_id is None:
            text, keyboard = await self._screen_times(data, "Пока вы подтверждали, это время заняли.")
            await self.api.send(peer, text, keyboard)
            return

        self.sessions[user] = Session()
        booking = await self.db.get(booking_id)
        await self.api.send(
            peer,
            f"Готово, вы записаны.\n\n{plain(booking_card(booking))}\n\n"
            "Напомним за день и за 2 часа до визита. Отменить можно в разделе «Мои записи».",
            kb.main_menu(),
        )
        await self._notify_admins("Новая запись", booking)

    async def _my_screen(self, user: int) -> tuple[str, str | None]:
        items = await self.db.upcoming_for_user(user, now(), PLATFORM)
        if not items:
            return "Предстоящих записей нет. Нажмите «Записаться», чтобы выбрать время.", None
        text = "Ваши записи\n\n" + "\n\n".join(plain(booking_card(b)) for b in items)
        labels = [(b.id, f"{WEEKDAYS_SHORT[b.starts_at.weekday()]} {b.starts_at:%d.%m %H:%M}") for b in items]
        return text, kb.my_bookings(labels)

    async def _send_my(self, peer: int, user: int) -> None:
        await self.api.send(peer, *await self._my_screen(user))

    async def _own_active(self, user: int, booking_id: int) -> tuple[Booking | None, str | None]:
        b = await self.db.get(booking_id)
        if not b or b.platform != PLATFORM or b.user_id != user or b.status != "active":
            return None, "Запись уже отменена или не найдена"
        if b.starts_at <= now():
            return None, "Эта запись уже началась или прошла"
        return b, None

    async def _ask_cancel(self, peer, user, cmid, booking_id):
        b, problem = await self._own_active(user, booking_id)
        if problem:
            return problem
        await self.api.edit(peer, cmid, f"Отменить эту запись?\n\n{plain(booking_card(b))}", kb.cancel_confirm(b.id))
        return None

    async def _do_cancel(self, peer, user, cmid, booking_id):
        b, problem = await self._own_active(user, booking_id)
        if problem:
            return problem
        await self.db.cancel(b.id)
        await self.api.edit(peer, cmid, f"Запись отменена.\n\n{plain(booking_card(b))}")
        late = b.starts_at - now() < timedelta(minutes=LATE_CANCEL_MIN)
        await self._notify_admins("Клиент отменил запись" + (" — меньше чем за 2 часа до визита" if late else ""), b)
        return "Запись отменена"

    async def _notify_admins(self, title: str, b: Booking) -> None:
        phone = b.client_phone or "телефон не указан"
        text = f"{title} · № {b.id} · ВКонтакте\n\n{plain(booking_card(b))}\nКлиент: {b.client_name}, {phone}"
        for admin_id in self.admin_ids:
            try:
                await self.api.send(admin_id, text)
            except VkApiError:
                log.warning("Не удалось отправить уведомление администратору ВК %s", admin_id)

    async def send_reminder(self, user_id: int, html_text: str) -> None:
        await self.api.send(user_id, plain(html_text))

    async def run(self, group_id: int) -> None:
        log.info("ВКонтакте: бот запущен для сообщества %s", group_id)
        async for update in self.api.listen(group_id):
            try:
                await self.handle(update)
            except Exception:
                log.exception("Ошибка при обработке события ВК")


async def _const(value):
    return value


__all__ = ["ANY_MASTER", "VkBookingBot"]
