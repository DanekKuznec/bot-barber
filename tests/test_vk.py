import asyncio
import json
from datetime import date, datetime

import pytest

from bot import clock
from bot.catalog import TZ
from bot.db import Database
from bot.reminders import send_due
from bot.vk import keyboards as kb
from bot.vk.bot import VkBookingBot

CLIENT = 777
ADMIN = 900


class FakeVk:
    def __init__(self):
        self.sent: list[tuple[int, str, str | None]] = []
        self.edits: list[tuple[int, int, str, str | None]] = []
        self.answers = 0
        self._cmid = 0

    async def send(self, peer_id, text, keyboard=None):
        self._cmid += 1
        self.sent.append((peer_id, text, keyboard))
        return self._cmid

    async def edit(self, peer_id, cmid, text, keyboard=None):
        self.edits.append((peer_id, cmid, text, keyboard))

    async def answer_event(self, event_id, user_id, peer_id, snackbar=None):
        self.answers += 1

    async def user_name(self, user_id):
        return "Мария Иванова"

    def last_screen(self) -> tuple[str, list[dict]]:
        text, keyboard = (self.edits[-1][2], self.edits[-1][3]) if self.edits else (self.sent[-1][1], self.sent[-1][2])
        buttons = [b for row in json.loads(keyboard)["buttons"] for b in row] if keyboard else []
        return text, buttons


def payloads(buttons):
    return [json.loads(b["action"]["payload"]) for b in buttons]


class Client:
    def __init__(self, bot: VkBookingBot, user=CLIENT):
        self.bot, self.user, self._n = bot, user, 0

    async def say(self, text, payload=None):
        msg = {"peer_id": self.user, "from_id": self.user, "text": text}
        if payload:
            msg["payload"] = json.dumps(payload)
        await self.bot.handle({"type": "message_new", "object": {"message": msg}})

    async def press(self, payload):
        self._n += 1
        await self.bot.handle(
            {
                "type": "message_event",
                "object": {
                    "user_id": self.user,
                    "peer_id": self.user,
                    "event_id": f"e{self._n}",
                    "conversation_message_id": 1,
                    "payload": payload,
                },
            }
        )


@pytest.fixture
def env(tmp_path):
    clock.freeze(datetime(2026, 11, 16, 9, 0, tzinfo=TZ))  # Monday 09:00
    db = Database(str(tmp_path / "vk.sqlite3"))
    asyncio.run(db.init())
    api = FakeVk()
    bot = VkBookingBot(api, db, frozenset({ADMIN}))
    yield bot, api, db
    clock.freeze(None)


def test_keyboards_fit_vk_limits():
    kb.services()
    kb.masters({"artem", "ilya", "dima"})
    kb.days([date(2026, 11, 16 + i) for i in range(7)], date(2026, 11, 16))
    starts = [datetime(2026, 11, 16, 10, tzinfo=TZ).replace(hour=10 + i // 2, minute=30 * (i % 2)) for i in range(21)]
    for page in range(4):
        kb.times(starts, page)
    kb.my_bookings([(i, f"пн 16.11 1{i}:00") for i in range(12)])
    for s in kb.services(), kb.confirm():
        for row in json.loads(s)["buttons"]:
            for b in row:
                assert len(b["action"]["label"]) <= 40
                assert len(b["action"]["payload"]) <= 255


def test_full_booking_flow(env):
    bot, api, db = env
    client = Client(bot)

    async def scenario():
        await client.say("Начать", {"command": "start"})
        await client.say(kb.MENU_BOOK, {"cmd": "book"})
        await client.press({"a": "svc", "v": "haircut"})
        await client.press({"a": "mst", "v": "any"})
        await client.press({"a": "day", "v": "2026-11-16"})

        text, buttons = api.last_screen()
        assert "страница 1" in text
        assert {"a": "tm", "v": "1000"} in payloads(buttons)
        assert {"a": "pg", "v": 1} in payloads(buttons)

        await client.press({"a": "pg", "v": 1})
        assert {"a": "tm", "v": "1300"} in payloads(api.last_screen()[1])

        await client.press({"a": "tm", "v": "1300"})
        assert "Проверьте запись" in api.last_screen()[0]

        await client.press({"a": "ok"})
        assert "номер телефона" in api.sent[-1][1]

        await client.say("8 (900) 555 44 33")

    asyncio.run(scenario())

    to_client = [m for m in api.sent if m[0] == CLIENT]
    assert "вы записаны" in to_client[-1][1]
    assert "<b>" not in to_client[-1][1]
    to_admin = [m for m in api.sent if m[0] == ADMIN]
    assert "+7 (900) 555-44-33" in to_admin[-1][1] and "ВКонтакте" in to_admin[-1][1]
    assert api.answers == 6

    bookings = asyncio.run(db.for_day(date(2026, 11, 16)))
    assert len(bookings) == 1 and bookings[0].platform == "vk"


def test_shared_schedule_with_telegram(env):
    bot, api, db = env
    start = datetime(2026, 11, 17, 12, 0, tzinfo=TZ)
    asyncio.run(db.save_client(CLIENT, "Клиент из Telegram"))
    asyncio.run(db.create_booking(CLIENT, "haircut", "artem", start, start.replace(hour=13), clock.now(), "tg"))

    client = Client(bot)

    async def scenario():
        await client.say(kb.MENU_BOOK, {"cmd": "book"})
        await client.press({"a": "svc", "v": "haircut"})
        await client.press({"a": "mst", "v": "artem"})
        await client.press({"a": "day", "v": "2026-11-17"})
        await client.press({"a": "pg", "v": 0})

    asyncio.run(scenario())
    times = [p["v"] for p in payloads(api.last_screen()[1]) if p["a"] == "tm"]
    assert "1200" not in times and "1130" not in times

    # same numeric id on another platform must not leak into "my bookings"
    asyncio.run(client.say(kb.MENU_MY, {"cmd": "my"}))
    assert "Предстоящих записей нет" in api.sent[-1][1]


def test_cancel_and_reminder(env):
    bot, api, db = env
    client = Client(bot)

    async def book_then_cancel():
        await client.say(kb.MENU_BOOK, {"cmd": "book"})
        await client.press({"a": "svc", "v": "beard"})
        await client.press({"a": "mst", "v": "ilya"})
        await client.press({"a": "day", "v": "2026-11-18"})
        await client.press({"a": "tm", "v": "1100"})
        await client.press({"a": "ok"})
        await client.say("+79001112233")

    asyncio.run(book_then_cancel())
    booking = asyncio.run(db.upcoming_for_user(CLIENT, clock.now(), "vk"))[0]

    clock.freeze(datetime(2026, 11, 17, 12, 0, tzinfo=TZ))
    sent = []

    async def capture(user_id, html):
        await bot.send_reminder(user_id, html)
        sent.append(user_id)

    assert asyncio.run(send_due(db, "vk", capture)) == 1
    assert "завтра" in api.sent[-1][1] and "<b>" not in api.sent[-1][1]

    async def cancel():
        await client.say(kb.MENU_MY, {"cmd": "my"})
        await client.press({"a": "cnl", "v": booking.id})
        await client.press({"a": "cnl!", "v": booking.id})

    asyncio.run(cancel())
    assert asyncio.run(db.upcoming_for_user(CLIENT, clock.now(), "vk")) == []
    assert any("отменил" in m[1] for m in api.sent if m[0] == ADMIN)


def test_admin_schedule_command(env):
    bot, api, _db = env
    asyncio.run(Client(bot, ADMIN).say("/today"))
    assert api.sent[-1][1].startswith("Расписание")
    asyncio.run(Client(bot).say("/today"))
    assert not api.sent[-1][1].startswith("Расписание")


def test_late_cancel_still_works_by_button(env):
    bot, api, db = env
    start = datetime(2026, 11, 16, 9, 30, tzinfo=TZ)
    asyncio.run(db.save_client(CLIENT, "Мария", "+7 (900) 111-22-33", "vk"))
    booking_id = asyncio.run(
        db.create_booking(
            CLIENT, "beard", "artem", start, start.replace(hour=10), datetime(2026, 11, 15, 12, 0, tzinfo=TZ), "vk"
        )
    )
    client = Client(bot)

    async def cancel():
        await client.press({"a": "cnl", "v": booking_id})
        await client.press({"a": "cnl!", "v": booking_id})

    asyncio.run(cancel())
    assert asyncio.run(db.upcoming_for_user(CLIENT, clock.now(), "vk")) == []
    assert any("меньше чем за 2 часа" in m[1] for m in api.sent if m[0] == ADMIN)
