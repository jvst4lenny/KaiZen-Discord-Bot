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
                    self.log.exception(f"storage_save_error | {e}")

        self._save_task = asyncio.create_task(runner())

    async def get_entry(self, user_id: int) -> dict[str, Any]:
        key = str(user_id)
        async with self._lock:
            entry = self.data.get(key)
            if not isinstance(entry, dict):
                entry = {"xp": 0, "level": 0}
                self.data[key] = entry
            if "xp" not in entry:
                entry["xp"] = 0
            if "level" not in entry:
                entry["level"] = 0
            try:
                entry["xp"] = max(0, int(entry.get("xp", 0)))
            except Exception:
                entry["xp"] = 0
            try:
                entry["level"] = max(0, int(entry.get("level", 0)))
            except Exception:
                entry["level"] = 0
            return dict(entry)

    async def set_entry(self, user_id: int, xp: int, level: int) -> None:
        key = str(user_id)
        async with self._lock:
            self.data[key] = {"xp": max(0, int(xp)), "level": max(0, int(level))}
        self.schedule_save()

    async def all_entries(self) -> dict[int, dict[str, Any]]:
        async with self._lock:
            raw = dict(self.data)
        out: dict[int, dict[str, Any]] = {}
        for k, v in raw.items():
            try:
                uid = int(k)
            except Exception:
                continue
            if not isinstance(v, dict):
                continue
            xp = 0
            level = 0
            try:
                xp = max(0, int(v.get("xp", 0)))
            except Exception:
                xp = 0
            try:
                level = max(0, int(v.get("level", 0)))
            except Exception:
                level = 0
            out[uid] = {"xp": xp, "level": level}
        return out
