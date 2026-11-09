"""End-to-end Telegram dialogs. FakeSession records Bot API calls instead of sending them."""

import asyncio
from datetime import datetime

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Contact, Message, Update, User

from bot import clock
from bot import keyboards as kb
from bot.__main__ import build_dispatcher
from bot.catalog import TZ
from bot.config import Config
from bot.db import Database

CLIENT = User(id=500, is_bot=False, first_name="Олег")
ADMIN_ID = 900


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self._message_id = 1000

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if method.__returning__ is bool:
            return True
        self._message_id += 1
        return Message(
            message_id=self._message_id,
            date=datetime.now(TZ),
            chat=Chat(id=getattr(method, "chat_id", None) or CLIENT.id, type="private"),
            text=getattr(method, "text", None),
        )

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError

    async def close(self):
        pass


class Client:
    def __init__(self, dp, bot, user=CLIENT):
        self.dp, self.bot, self.user = dp, bot, user
        self._update_id = 0
        self.chat = Chat(id=user.id, type="private")

    async def _feed(self, **payload):
        self._update_id += 1
        await self.dp.feed_update(self.bot, Update(update_id=self._update_id, **payload))

    async def say(self, text=None, contact=None):
        msg = Message(
            message_id=self._update_id + 1,
            date=datetime.now(TZ),
            chat=self.chat,
            from_user=self.user,
            text=text,
            contact=contact,
        )
        await self._feed(message=msg)

    async def press(self, callback_data):
        msg = Message(message_id=1, date=datetime.now(TZ), chat=self.chat, from_user=self.user, text="…")
        cq = CallbackQuery(
            id=str(self._update_id), from_user=self.user, chat_instance="x", message=msg, data=callback_data.pack()
        )
        await self._feed(callback_query=cq)


def texts(session, kind=(SendMessage, EditMessageText)):
    return [c.text for c in session.calls if isinstance(c, kind)]


def buttons(call):
    markup = call.reply_markup
    return [b.callback_data for row in markup.inline_keyboard for b in row]


# aiogram routers can be attached only once per process, so the dispatcher is shared
# and each test swaps in its own database and FSM storage.
_CONFIG = Config("123:TEST", frozenset({ADMIN_ID}), ":unused:")
_DP = None


def _dispatcher(db):
    global _DP
    if _DP is None:
        _DP = build_dispatcher(_CONFIG, db)
    _DP["db"] = db
    _DP.fsm.storage = MemoryStorage()
    return _DP


@pytest.fixture
def env(tmp_path):
    # Monday, 16 Nov 2026, 09:00
    clock.freeze(datetime(2026, 11, 16, 9, 0, tzinfo=TZ))
    db = Database(str(tmp_path / "flow.sqlite3"))
    asyncio.run(db.init())
    session = FakeSession()
    bot = Bot(_CONFIG.bot_token, session=session)
    yield _dispatcher(db), bot, session, db
    clock.freeze(None)


def test_full_booking_flow(env):
    dp, bot, session, db = env
    client = Client(dp, bot)

    async def scenario():
        await client.say("/start")
        await client.say(kb.BTN_BOOK)
        await client.press(kb.ServiceCb(id="haircut"))
        await client.press(kb.MasterCb(id="artem"))

        day_call = [c for c in session.calls if isinstance(c, EditMessageText)][-1]
        assert kb.DayCb(iso="2026-11-16").pack() in buttons(day_call)

        await client.press(kb.DayCb(iso="2026-11-16"))
        times_call = [c for c in session.calls if isinstance(c, EditMessageText)][-1]
        assert kb.TimeCb(hhmm="1000").pack() in buttons(times_call)

        await client.press(kb.TimeCb(hhmm="1500"))
        assert "Проверьте запись" in texts(session)[-1]

        await client.press(kb.NavCb(to="confirm"))
        assert "номер телефона" in texts(session, SendMessage)[-1]

        await client.say(contact=Contact(phone_number="79001234567", first_name="Олег", user_id=CLIENT.id))

    asyncio.run(scenario())

    sent = [c for c in session.calls if isinstance(c, SendMessage)]
    assert "вы записаны" in sent[-2].text
    assert "15:00" in sent[-2].text
    assert sent[-1].chat_id == ADMIN_ID
    assert "+7 (900) 123-45-67" in sent[-1].text

    bookings = asyncio.run(db.for_day(datetime(2026, 11, 16).date()))
    assert len(bookings) == 1 and bookings[0].master_id == "artem"

    pressed = 5
    assert sum(isinstance(c, AnswerCallbackQuery) for c in session.calls) >= pressed


def test_taken_time_is_not_offered_and_cancel_works(env):
    dp, bot, session, db = env
    first = Client(dp, bot)
    second = Client(dp, bot, User(id=501, is_bot=False, first_name="Анна"))

    async def book(client, phone):
        await client.say(kb.BTN_BOOK)
        await client.press(kb.ServiceCb(id="haircut"))
        await client.press(kb.MasterCb(id="artem"))
        await client.press(kb.DayCb(iso="2026-11-17"))
        await client.press(kb.TimeCb(hhmm="1200"))
        await client.press(kb.NavCb(to="confirm"))
        await client.say(phone)

    asyncio.run(book(first, "8 900 111-22-33"))

    async def second_tries():
        await second.say(kb.BTN_BOOK)
        await second.press(kb.ServiceCb(id="haircut"))
        await second.press(kb.MasterCb(id="artem"))
        await second.press(kb.DayCb(iso="2026-11-17"))

    asyncio.run(second_tries())
    times_call = [c for c in session.calls if isinstance(c, EditMessageText)][-1]
    offered = buttons(times_call)
    assert kb.TimeCb(hhmm="1200").pack() not in offered
    assert kb.TimeCb(hhmm="1130").pack() not in offered  # 11:30–12:30 задело бы запись
    assert kb.TimeCb(hhmm="1300").pack() in offered

    booking = asyncio.run(db.upcoming_for_user(CLIENT.id, clock.now()))[0]

    async def cancel():
        await first.say(kb.BTN_MY)
        await first.press(kb.CancelCb(booking_id=booking.id))
        await first.press(kb.CancelCb(booking_id=booking.id, sure=True))

    asyncio.run(cancel())
    assert asyncio.run(db.upcoming_for_user(CLIENT.id, clock.now())) == []
    admin_msgs = [c.text for c in session.calls if isinstance(c, SendMessage) and c.chat_id == ADMIN_ID]
    assert any("отменил" in t for t in admin_msgs)


def test_admin_schedule_only_for_admin(env):
    dp, bot, session, _db = env
    admin = Client(dp, bot, User(id=ADMIN_ID, is_bot=False, first_name="Админ"))
    stranger = Client(dp, bot)

    asyncio.run(admin.say("/today"))
    assert "Расписание" in texts(session, SendMessage)[-1]

    asyncio.run(stranger.say("/today"))
    assert "Расписание" not in texts(session, SendMessage)[-1]


def test_bad_phone_asks_again(env):
    dp, bot, session, db = env
    client = Client(dp, bot)

    async def scenario():
        await client.say(kb.BTN_BOOK)
        await client.press(kb.ServiceCb(id="beard"))
        await client.press(kb.MasterCb(id="any"))
        await client.press(kb.DayCb(iso="2026-11-18"))
        await client.press(kb.TimeCb(hhmm="1100"))
        await client.press(kb.NavCb(to="confirm"))
        await client.say("позвоните мне")

    asyncio.run(scenario())
    assert "Не получилось разобрать номер" in texts(session, SendMessage)[-1]
    assert asyncio.run(db.for_day(datetime(2026, 11, 18).date())) == []
