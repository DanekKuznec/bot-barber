from datetime import datetime

from .catalog import TZ

_frozen: datetime | None = None


def now() -> datetime:
    if _frozen is not None:
        return _frozen
    return datetime.now(TZ).replace(second=0, microsecond=0)


def freeze(moment: datetime | None) -> None:
    """Test helper: pin the current time, or pass None to restore the real clock."""
    global _frozen
    _frozen = moment
