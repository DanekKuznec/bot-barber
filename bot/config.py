import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]
    db_path: str
    vk_token: str = ""
    vk_group_id: int = 0
    vk_admin_ids: frozenset[int] = frozenset()


def _ids(name: str) -> frozenset[int]:
    return frozenset(int(x) for x in os.environ.get(name, "").replace(" ", "").split(",") if x)


def load_config() -> Config:
    _load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    token = os.environ.get("BOT_TOKEN", "").strip()
    vk_token = os.environ.get("VK_TOKEN", "").strip()
    vk_group = os.environ.get("VK_GROUP_ID", "").strip()

    if not token and not vk_token:
        raise SystemExit(
            "Не задан ни BOT_TOKEN (Telegram), ни VK_TOKEN (ВКонтакте). "
            "Скопируйте .env.example в .env и заполните хотя бы один."
        )
    if vk_token and not vk_group.isdigit():
        raise SystemExit("Для ВКонтакте укажите VK_GROUP_ID — числовой id сообщества.")

    return Config(
        bot_token=token,
        admin_ids=_ids("ADMIN_IDS"),
        db_path=os.environ.get("DB_PATH", "bookings.sqlite3"),
        vk_token=vk_token,
        vk_group_id=int(vk_group or 0),
        vk_admin_ids=_ids("VK_ADMIN_IDS"),
    )
