from datetime import date, datetime

from bot.catalog import MASTER_BY_ID, TZ
from bot.slots import free_starts, overlaps, slots_by_master

ARTEM = MASTER_BY_ID["artem"]  # Mon-Fri
ILYA = MASTER_BY_ID["ilya"]  # Wed-Sun

MONDAY = date(2026, 11, 16)
EARLY = datetime(2026, 11, 15, 9, 0, tzinfo=TZ)  # day before, lead time irrelevant


def at(d: date, h: int, m: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, tzinfo=TZ)


def test_touching_intervals_do_not_overlap():
    assert not overlaps((at(MONDAY, 10), at(MONDAY, 11)), (at(MONDAY, 11), at(MONDAY, 12)))
    assert overlaps((at(MONDAY, 10), at(MONDAY, 11)), (at(MONDAY, 10, 30), at(MONDAY, 11, 30)))


def test_full_free_day_hour_service():
    starts = free_starts(MONDAY, 60, ARTEM, [], EARLY)
    assert starts[0] == at(MONDAY, 10)
    # last one-hour slot starts at 20:00 to finish by closing time
    assert starts[-1] == at(MONDAY, 20)
    assert len(starts) == 21  # 10:00 … 20:00 с шагом 30 минут


def test_day_off_has_no_slots():
    assert free_starts(MONDAY, 60, ILYA, [], EARLY) == []


def test_busy_interval_blocks_overlapping_starts():
    busy = [(at(MONDAY, 12), at(MONDAY, 13))]
    starts = free_starts(MONDAY, 60, ARTEM, busy, EARLY)
    assert at(MONDAY, 11, 30) not in starts  # 11:30–12:30 задевает запись
    assert at(MONDAY, 12) not in starts
    assert at(MONDAY, 12, 30) not in starts
    assert at(MONDAY, 11) in starts  # 11:00–12:00 заканчивается ровно к записи
    assert at(MONDAY, 13) in starts


def test_long_service_needs_whole_gap():
    # a 90-minute service doesn't fit into the 13:00-14:00 gap
    busy = [(at(MONDAY, 10), at(MONDAY, 13)), (at(MONDAY, 14), at(MONDAY, 21))]
    assert free_starts(MONDAY, 90, ARTEM, busy, EARLY) == []
    assert free_starts(MONDAY, 60, ARTEM, busy, EARLY) == [at(MONDAY, 13)]


def test_today_respects_lead_time():
    now = at(MONDAY, 14, 10)
    starts = free_starts(MONDAY, 30, ARTEM, [], now)
    assert starts[0] == at(MONDAY, 15, 30)


def test_any_master_merges_and_lists_who_is_free():
    wednesday = date(2026, 11, 18)  # both work
    busy = {"artem": [(at(wednesday, 10), at(wednesday, 12))]}
    slots = slots_by_master(wednesday, 60, [ARTEM, ILYA], busy, EARLY)
    assert slots[at(wednesday, 10)] == ["ilya"]
    assert slots[at(wednesday, 12)] == ["artem", "ilya"]
    assert list(slots) == sorted(slots)
