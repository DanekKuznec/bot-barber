"""SQLite storage.

Clients are keyed by (platform, user_id): Telegram and VK ids can collide.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import aiosqlite

from .catalog import TZ
from .slots import Interval

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    platform  TEXT NOT NULL CHECK (platform IN ('tg', 'vk')),
    user_id   INTEGER NOT NULL,
    name      TEXT NOT NULL,
    phone     TEXT,
    PRIMARY KEY (platform, user_id)
);

CREATE TABLE IF NOT EXISTS bookings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    platform      TEXT NOT NULL,
    user_id       INTEGER NOT NULL,
    service_id    TEXT NOT NULL,
    master_id     TEXT NOT NULL,
    starts_at     TEXT NOT NULL,
    ends_at       TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'cancelled')),
    created_at    TEXT NOT NULL,
    reminded_day  INTEGER NOT NULL DEFAULT 0,
    reminded_soon INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (platform, user_id) REFERENCES clients (platform, user_id)
);

CREATE INDEX IF NOT EXISTS bookings_master_time ON bookings (master_id, starts_at);
CREATE INDEX IF NOT EXISTS bookings_user ON bookings (platform, user_id, starts_at);
"""


@dataclass(frozen=True)
class Booking:
    id: int
    platform: str
    user_id: int
    client_name: str
    client_phone: str | None
    service_id: str
    master_id: str
    starts_at: datetime
    ends_at: datetime
    status: str
    created_at: datetime


class SlotTaken(Exception):
    pass


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(TZ)


def _iso(value: datetime) -> str:
    return value.astimezone(TZ).isoformat(timespec="minutes")


_SELECT = """
SELECT b.id, b.platform, b.user_id, c.name, c.phone, b.service_id, b.master_id,
       b.starts_at, b.ends_at, b.status, b.created_at
FROM bookings b JOIN clients c ON c.platform = b.platform AND c.user_id = b.user_id
"""


def _row(row) -> Booking:
    return Booking(
        id=row[0],
        platform=row[1],
        user_id=row[2],
        client_name=row[3],
        client_phone=row[4],
        service_id=row[5],
        master_id=row[6],
        starts_at=_dt(row[7]),
        ends_at=_dt(row[8]),
        status=row[9],
        created_at=_dt(row[10]),
    )


class Database:
    def __init__(self, path: str):
        self.path = path

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            await db.commit()

    async def save_client(self, user_id: int, name: str, phone: str | None = None, platform: str = "tg") -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO clients (platform, user_id, name, phone) VALUES (?, ?, ?, ?)
                   ON CONFLICT(platform, user_id) DO UPDATE SET
                     name = excluded.name,
                     phone = COALESCE(excluded.phone, clients.phone)""",
                (platform, user_id, name, phone),
            )
            await db.commit()

    async def client_phone(self, user_id: int, platform: str = "tg") -> str | None:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT phone FROM clients WHERE platform = ? AND user_id = ?", (platform, user_id))
            row = await cur.fetchone()
            return row[0] if row else None

    async def busy_by_master(self, day: date) -> dict[str, list[Interval]]:
        start = datetime.combine(day, datetime.min.time(), tzinfo=TZ)
        end = start + timedelta(days=1)
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                """SELECT master_id, starts_at, ends_at FROM bookings
                   WHERE status = 'active' AND starts_at < ? AND ends_at > ?""",
                (_iso(end), _iso(start)),
            )
            result: dict[str, list[Interval]] = {}
            async for master_id, s, e in cur:
                result.setdefault(master_id, []).append((_dt(s), _dt(e)))
            return result

    async def create_booking(
        self,
        user_id: int,
        service_id: str,
        master_id: str,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime,
        platform: str = "tg",
    ) -> int:
        async with aiosqlite.connect(self.path, isolation_level=None) as db:
            # IMMEDIATE takes the write lock up front, so two concurrent confirmations can't both pass the check
            await db.execute("BEGIN IMMEDIATE")
            try:
                cur = await db.execute(
                    """SELECT 1 FROM bookings
                       WHERE status = 'active' AND master_id = ?
                         AND starts_at < ? AND ends_at > ? LIMIT 1""",
                    (master_id, _iso(ends_at), _iso(starts_at)),
                )
                if await cur.fetchone():
                    raise SlotTaken
                cur = await db.execute(
                    """INSERT INTO bookings (platform, user_id, service_id, master_id, starts_at, ends_at, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (platform, user_id, service_id, master_id, _iso(starts_at), _iso(ends_at), _iso(now)),
                )
                await db.execute("COMMIT")
                return cur.lastrowid
            except BaseException:
                await db.execute("ROLLBACK")
                raise

    async def get(self, booking_id: int) -> Booking | None:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(_SELECT + " WHERE b.id = ?", (booking_id,))
            row = await cur.fetchone()
            return _row(row) if row else None

    async def upcoming_for_user(self, user_id: int, now: datetime, platform: str = "tg") -> list[Booking]:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                _SELECT
                + """ WHERE b.platform = ? AND b.user_id = ? AND b.status = 'active'
                    AND b.starts_at >= ? ORDER BY b.starts_at""",
                (platform, user_id, _iso(now)),
            )
            return [_row(r) for r in await cur.fetchall()]

    async def for_day(self, day: date) -> list[Booking]:
        start = datetime.combine(day, datetime.min.time(), tzinfo=TZ)
        end = start + timedelta(days=1)
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                _SELECT + " WHERE b.status = 'active' AND b.starts_at >= ? AND b.starts_at < ? ORDER BY b.starts_at",
                (_iso(start), _iso(end)),
            )
            return [_row(r) for r in await cur.fetchall()]

    async def cancel(self, booking_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (booking_id,))
            await db.commit()

    async def due_reminders(self, now: datetime, platform: str = "tg") -> tuple[list[Booking], list[Booking]]:
        # No day-before reminder for bookings made less than a day in advance.
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                _SELECT
                + """ WHERE b.platform = ? AND b.status = 'active' AND b.reminded_day = 0
                    AND b.starts_at <= ? AND b.starts_at > ?""",
                (platform, _iso(now + timedelta(hours=24)), _iso(now + timedelta(hours=3))),
            )
            day = [b for b in map(_row, await cur.fetchall()) if b.starts_at - b.created_at > timedelta(hours=24)]

            cur = await db.execute(
                _SELECT
                + """ WHERE b.platform = ? AND b.status = 'active' AND b.reminded_soon = 0
                    AND b.starts_at <= ? AND b.starts_at > ?""",
                (platform, _iso(now + timedelta(hours=2)), _iso(now)),
            )
            soon = [b for b in map(_row, await cur.fetchall()) if b.starts_at - b.created_at > timedelta(hours=2)]
            return day, soon

    async def mark_reminded(self, booking_id: int, kind: str) -> None:
        column = {"day": "reminded_day", "soon": "reminded_soon"}[kind]
        async with aiosqlite.connect(self.path) as db:
            await db.execute(f"UPDATE bookings SET {column} = 1 WHERE id = ?", (booking_id,))
            await db.commit()
