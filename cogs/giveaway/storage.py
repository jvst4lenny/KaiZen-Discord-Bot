import asyncio
import json
import os
from typing import Any


class JsonStorage:
    def __init__(self, path: str, log=None):
        self.path = path
        self.log = log
        self._lock = asyncio.Lock()
        self._save_task: asyncio.Task | None = None
        self.data: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        if not os.path.exists(self.path):
            self.data = {}
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self.data = raw if isinstance(raw, dict) else {}
        except Exception:
            self.data = {}

    async def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        async with self._lock:
            payload = self.data
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def schedule_save(self, delay: float = 0.6) -> None:
        if self._save_task and not self._save_task.done():
            return

        async def runner():
            await asyncio.sleep(delay)
            try:
                await self.save()
            except Exception as e:
                if self.log:
                    self.log.exception(f"giveaway_storage_save_error | {e}")

        self._save_task = asyncio.create_task(runner())

    async def get(self, key: str) -> dict[str, Any] | None:
        async with self._lock:
            v = self.data.get(key)
            return dict(v) if isinstance(v, dict) else None

    async def set(self, key: str, value: dict[str, Any]) -> None:
        async with self._lock:
            self.data[key] = value
        self.schedule_save()

    async def delete(self, key: str) -> None:
        async with self._lock:
            if key in self.data:
                del self.data[key]
        self.schedule_save()

    async def all(self) -> dict[str, dict[str, Any]]:
        async with self._lock:
            raw = dict(self.data)
        out: dict[str, dict[str, Any]] = {}
        for k, v in raw.items():
            if isinstance(v, dict):
                out[str(k)] = dict(v)
        return out
