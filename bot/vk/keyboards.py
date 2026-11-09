"""VK keyboards. Inline keyboards are limited to 10 buttons, 6 rows and 40-char labels."""

import json
from datetime import date, datetime

from ..catalog import MASTERS, SERVICES, format_price
from ..texts import day_button

LABEL_MAX = 40
TIMES_PER_PAGE = 6

MENU_BOOK = "Записаться"
MENU_MY = "Мои записи"
MENU_PRICES = "Услуги и цены"
MENU_CONTACTS = "Контакты"


def _payload(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _callback(label: str, payload: dict, color: str = "secondary") -> dict:
    return {"action": {"type": "callback", "label": label[:LABEL_MAX], "payload": _payload(payload)}, "color": color}


def _text(label: str, payload: dict, color: str = "secondary") -> dict:
    return {"action": {"type": "text", "label": label[:LABEL_MAX], "payload": _payload(payload)}, "color": color}


def _inline(rows: list[list[dict]]) -> str:
    rows = [r for r in rows if r]
    assert len(rows) <= 6 and sum(map(len, rows)) <= 10, "превышены ограничения inline-клавиатуры ВК"
    return json.dumps({"inline": True, "buttons": rows}, ensure_ascii=False)


def _chunks(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def main_menu() -> str:
    return json.dumps(
        {
            "one_time": False,
            "buttons": [
                [_text(MENU_BOOK, {"cmd": "book"}, "positive"), _text(MENU_MY, {"cmd": "my"})],
                [_text(MENU_PRICES, {"cmd": "prices"}), _text(MENU_CONTACTS, {"cmd": "contacts"})],
            ],
        },
        ensure_ascii=False,
    )


def services() -> str:
    return _inline([[_callback(f"{s.title} · {format_price(s.price)}", {"a": "svc", "v": s.id})] for s in SERVICES])


def masters(available: set[str]) -> str:
    rows = [[_callback("Любой свободный мастер", {"a": "mst", "v": "any"}, "primary")]]
    rows += [[_callback(f"{m.name} — {m.about}", {"a": "mst", "v": m.id})] for m in MASTERS if m.id in available]
    rows.append([_callback("← Другая услуга", {"a": "nav", "v": "services"})])
    return _inline(rows)


def days(available: list[date], today: date) -> str:
    buttons = [_callback(day_button(d, today), {"a": "day", "v": d.isoformat()}) for d in available]
    rows = _chunks(buttons, 3)
    rows.append([_callback("← Другой мастер", {"a": "nav", "v": "masters"})])
    return _inline(rows)


def times(starts: list[datetime], page: int) -> tuple[str, int, int]:
    """Returns (keyboard, clamped page, page count)."""
    pages = max(1, -(-len(starts) // TIMES_PER_PAGE))
    page = min(max(page, 0), pages - 1)
    chunk = starts[page * TIMES_PER_PAGE : (page + 1) * TIMES_PER_PAGE]

    rows = _chunks([_callback(f"{t:%H:%M}", {"a": "tm", "v": f"{t:%H%M}"}) for t in chunk], 3)
    paging = []
    if page > 0:
        paging.append(_callback("← Раньше", {"a": "pg", "v": page - 1}))
    if page < pages - 1:
        paging.append(_callback("Позже →", {"a": "pg", "v": page + 1}))
    rows.append(paging)
    rows.append([_callback("← Другой день", {"a": "nav", "v": "days"})])
    return _inline(rows), page, pages


def confirm() -> str:
    return _inline(
        [
            [_callback("Подтвердить запись", {"a": "ok"}, "positive")],
            [
                _callback("← Другое время", {"a": "nav", "v": "times"}),
                _callback("Отменить", {"a": "abort"}, "negative"),
            ],
        ]
    )


def my_bookings(items: list[tuple[int, str]]) -> str | None:
    if not items:
        return None
    buttons = [
        _callback(f"Отменить {label}", {"a": "cnl", "v": booking_id}, "negative") for booking_id, label in items[:10]
    ]
    return _inline(_chunks(buttons, 2))


def cancel_confirm(booking_id: int) -> str:
    return _inline(
        [
            [
                _callback("Да, отменить", {"a": "cnl!", "v": booking_id}, "negative"),
                _callback("Нет, оставить", {"a": "keep"}),
            ]
        ]
    )
