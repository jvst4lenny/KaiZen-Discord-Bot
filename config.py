import json
import os


class Config:
    def __init__(self, path: str = "config.json"):
        self.path = path
        self.data = self._load()

    def _load(self) -> dict:
        if not os.path.exists(self.path):
            raise FileNotFoundError(self.path)
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        token = data.get("token")
        if not token:
            raise ValueError("missing token")
        return data

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def section(self, key: str) -> dict:
        val = self.data.get(key, {})
        return val if isinstance(val, dict) else {}
