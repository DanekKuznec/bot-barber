import asyncio
from datetime import datetime, timedelta

import pytest

from bot.catalog import TZ
from bot.db import Database, SlotTaken


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "test.sqlite3"))
    run(database.init())
    run(database.save_client(1, "Иван", "+7 (900) 111-22-33"))
    run(database.save_client(2, "Пётр"))
    return database


NOW = datetime(2026, 11, 10, 9, 0, tzinfo=TZ)
START = datetime(2026, 11, 12, 15, 0, tzinfo=TZ)


def test_double_booking_is_rejected(db):
    run(db.create_booking(1, "haircut", "artem", START, START + timedelta(hours=1), NOW))
    with pytest.raises(SlotTaken):
        run(db.create_booking(2, "beard", "artem", START + timedelta(minutes=30), START + timedelta(hours=1), NOW))
    # another master at the same time is fine
    run(db.create_booking(2, "beard", "ilya", START, START + timedelta(minutes=30), NOW))


def test_parallel_confirmations_only_one_wins(db):
    async def both():
        return await asyncio.gather(
            db.create_booking(1, "haircut", "artem", START, START + timedelta(hours=1), NOW),
            db.create_booking(2, "haircut", "artem", START, START + timedelta(hours=1), NOW),
            return_exceptions=True,
        )

    results = run(both())
    assert sum(isinstance(r, int) for r in results) == 1
    assert sum(isinstance(r, SlotTaken) for r in results) == 1


def test_cancelled_booking_frees_the_slot(db):
    booking_id = run(db.create_booking(1, "haircut", "artem", START, START + timedelta(hours=1), NOW))
    run(db.cancel(booking_id))
    assert run(db.busy_by_master(START.date())) == {}
    run(db.create_booking(2, "haircut", "artem", START, START + timedelta(hours=1), NOW))


def test_upcoming_and_phone_kept(db):
    run(db.create_booking(1, "haircut", "artem", START, START + timedelta(hours=1), NOW))
    items = run(db.upcoming_for_user(1, NOW))
    assert len(items) == 1
    assert items[0].client_phone == "+7 (900) 111-22-33"
    run(db.save_client(1, "Иван Петров"))  # name updated, phone kept
    assert run(db.client_phone(1)) == "+7 (900) 111-22-33"


def test_reminders_day_and_soon(db):
    booking_id = run(db.create_booking(1, "haircut", "artem", START, START + timedelta(hours=1), NOW))

    day_before = START - timedelta(hours=20)
    day, soon = run(db.due_reminders(day_before))
    assert [b.id for b in day] == [booking_id] and soon == []
    run(db.mark_reminded(booking_id, "day"))
    assert run(db.due_reminders(day_before)) == ([], [])

    two_hours = START - timedelta(minutes=90)
    day, soon = run(db.due_reminders(two_hours))
    assert day == [] and [b.id for b in soon] == [booking_id]


def test_no_day_reminder_for_last_minute_booking(db):
    late_now = START - timedelta(hours=5)
    run(db.create_booking(1, "haircut", "artem", START, START + timedelta(hours=1), late_now))
    day, _ = run(db.due_reminders(START - timedelta(hours=4)))
    assert day == []
