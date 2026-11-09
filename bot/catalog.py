"""Shop data and booking rules. Edit this file to adapt the bot to another salon."""

from dataclasses import dataclass
from datetime import time
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Moscow")

SHOP_NAME = "Барбершоп «Фейд»"
SHOP_ADDRESS = "ул. Садовая, 14, вход с торца"
SHOP_PHONE = "+7 (900) 000-14-14"

OPENS_AT = time(10, 0)
CLOSES_AT = time(21, 0)

SLOT_STEP_MIN = 30
MIN_LEAD_MIN = 60  # same-day bookings need at least an hour of notice
DAYS_AHEAD = 7
LATE_CANCEL_MIN = 120  # later cancellations still go through, admins just get flagged


@dataclass(frozen=True)
class Service:
    id: str
    title: str
    minutes: int
    price: int


@dataclass(frozen=True)
class Master:
    id: str
    name: str
    about: str
    workdays: frozenset[int]  # 0 = Monday


SERVICES: tuple[Service, ...] = (
    Service("haircut", "Мужская стрижка", 60, 1200),
    Service("clipper", "Стрижка машинкой", 30, 700),
    Service("combo", "Стрижка и борода", 90, 1800),
    Service("beard", "Оформление бороды", 30, 700),
    Service("kids", "Детская стрижка, до 12 лет", 45, 900),
)

MASTERS: tuple[Master, ...] = (
    Master("artem", "Артём", "фейды и короткие стрижки", frozenset({0, 1, 2, 3, 4})),
    Master("ilya", "Илья", "бороды и классика", frozenset({2, 3, 4, 5, 6})),
    Master("dima", "Дмитрий", "длинные стрижки и укладки", frozenset({0, 1, 4, 5, 6})),
)

SERVICE_BY_ID = {s.id: s for s in SERVICES}
MASTER_BY_ID = {m.id: m for m in MASTERS}


def format_price(rub: int) -> str:
    return f"{rub:,}".replace(",", " ") + " ₽"


def format_duration(minutes: int) -> str:
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f"{hours} ч {rest} мин"
    if hours:
        return f"{hours} ч"
    return f"{rest} мин"
