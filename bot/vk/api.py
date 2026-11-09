"""Thin VK API client: the five methods the bot needs plus Bots Long Poll."""

import json
import logging
import random
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

API_URL = "https://api.vk.com/method/"
API_VERSION = "5.199"


class VkApiError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(f"VK API error {code}: {message}")
        self.code = code


class VkApi:
    def __init__(self, token: str, session: aiohttp.ClientSession):
        self.token = token
        self.session = session

    async def call(self, method: str, **params: Any) -> Any:
        params = {k: v for k, v in params.items() if v is not None}
        params.update(access_token=self.token, v=API_VERSION)
        async with self.session.post(API_URL + method, data=params) as resp:
            body = await resp.json(content_type=None)
        if "error" in body:
            err = body["error"]
            raise VkApiError(err.get("error_code", 0), err.get("error_msg", "unknown"))
        return body["response"]

    async def send(self, peer_id: int, text: str, keyboard: str | None = None) -> int:
        """Returns conversation_message_id, which messages.edit needs."""
        response = await self.call(
            "messages.send",
            peer_ids=peer_id,
            random_id=random.getrandbits(31),
            message=text,
            keyboard=keyboard,
        )
        return response[0]["conversation_message_id"]

    async def edit(self, peer_id: int, cmid: int, text: str, keyboard: str | None = None) -> None:
        await self.call(
            "messages.edit",
            peer_id=peer_id,
            conversation_message_id=cmid,
            message=text,
            keyboard=keyboard,
        )

    async def answer_event(self, event_id: str, user_id: int, peer_id: int, snackbar: str | None = None) -> None:
        event_data = json.dumps({"type": "show_snackbar", "text": snackbar}, ensure_ascii=False) if snackbar else None
        await self.call(
            "messages.sendMessageEventAnswer",
            event_id=event_id,
            user_id=user_id,
            peer_id=peer_id,
            event_data=event_data,
        )

    async def user_name(self, user_id: int) -> str:
        users = await self.call("users.get", user_ids=user_id)
        return f"{users[0]['first_name']} {users[0]['last_name']}".strip() if users else str(user_id)

    async def listen(self, group_id: int) -> AsyncIterator[dict]:
        server = await self.call("groups.getLongPollServer", group_id=group_id)
        ts = server["ts"]
        while True:
            params = {"act": "a_check", "key": server["key"], "ts": ts, "wait": 25}
            try:
                async with self.session.get(
                    server["server"], params=params, timeout=aiohttp.ClientTimeout(total=35)
                ) as resp:
                    body = await resp.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError):
                log.warning("Long Poll: обрыв соединения, переподключаемся")
                continue

            failed = body.get("failed")
            if failed == 1:  # history is outdated, take the new ts
                ts = body["ts"]
                continue
            if failed in (2, 3):  # key expired
                server = await self.call("groups.getLongPollServer", group_id=group_id)
                ts = server["ts"]
                continue

            ts = body["ts"]
            for update in body.get("updates", []):
                yield update
