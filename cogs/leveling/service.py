import time
from dataclasses import dataclass

import discord
from discord.ext import commands

from .storage import JsonStorage


@dataclass(frozen=True)
class LevelDef:
    level: int
    xp_needed: int
    role_id: int | None
    active: bool


def _to_int(v, default=0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _to_bool(v, default=False) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "1", "yes", "y", "on"):
            return True
        if s in ("false", "0", "no", "n", "off"):
            return False
    return default


def _norm_get(d: dict, key: str):
    for k in d.keys():
        if str(k).lower() == key.lower():
            return d[k]
    return None


class LevelingService:
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cfg = getattr(bot, "cfg", None)
        self.log = getattr(bot, "log", None)

        path = str(self.cfg.get("leveling", {}).get("storage_path", "data/leveling.json")) if self.cfg else "data/leveling.json"
        self.storage = JsonStorage(path, log=self.log)

        self._last_xp_time: dict[int, float] = {}
        self._last_msg: dict[int, tuple[str, float]] = {}

    def config(self) -> dict:
        if self.cfg is None:
            return {}
        v = self.cfg.get("leveling", {})
        return v if isinstance(v, dict) else {}

    def enabled(self) -> bool:
        return _to_bool(self.config().get("enabled", True), True)

    def guild_only(self) -> bool:
        return _to_bool(self.config().get("guild_only", True), True)

    def xp_per_message(self) -> int:
        return max(0, _to_int(self.config().get("xp_per_message", 1), 1))

    def excluded_channels(self) -> set[int]:
        v = self.config().get("excluded_channel_ids", [])
        if not isinstance(v, list):
            return set()
        out = set()
        for x in v:
            i = _to_int(x, 0)
            if i > 0:
                out.add(i)
        return out

    def remove_old_level_roles(self) -> bool:
        return _to_bool(self.config().get("remove_old_level_roles", False), False)

    def announce_cfg(self) -> dict:
        v = self.config().get("announce", {})
        return v if isinstance(v, dict) else {}

    def announce_enabled(self) -> bool:
        return _to_bool(self.announce_cfg().get("enabled", False), False)

    def announce_channel_id(self) -> int:
        return _to_int(self.announce_cfg().get("channel_id", 0), 0)

    def announce_message(self) -> str:
        return str(self.announce_cfg().get("message", "%usermetion% you reached a new rank! You are now a %newrole%"))

    def leaderboard_size(self) -> int:
        v = self.config().get("leaderboard", {})
        if not isinstance(v, dict):
            return 10
        return max(1, min(50, _to_int(v.get("size", 10), 10)))

    def spam_cfg(self) -> dict:
        v = self.config().get("spam_protection", {})
        return v if isinstance(v, dict) else {}

    def cooldown(self) -> float:
        return float(max(0, _to_int(self.spam_cfg().get("cooldown_seconds", 10), 10)))

    def block_same(self) -> bool:
        return _to_bool(self.spam_cfg().get("block_same_message", True), True)

    def same_window(self) -> float:
        return float(max(0, _to_int(self.spam_cfg().get("same_message_window_seconds", 120), 120)))

    def levels(self) -> list[LevelDef]:
        raw = self.config().get("levels", [])
        if not isinstance(raw, list):
            return []
        out: list[LevelDef] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            level = _to_int(_norm_get(item, "level"), 0)
            xp_needed = _to_int(_norm_get(item, "xp_needed"), 0)
            role_val = _norm_get(item, "role")
            role_id = None
            rid = _to_int(role_val, 0)
            if rid > 0:
                role_id = rid
            active = _to_bool(_norm_get(item, "active"), True)
            if level > 0:
                out.append(LevelDef(level=level, xp_needed=max(0, xp_needed), role_id=role_id, active=active))
        out.sort(key=lambda x: (x.xp_needed, x.level))
        return out

    def active_levels(self) -> list[LevelDef]:
        return [x for x in self.levels() if x.active]

    def compute_level(self, xp: int) -> int:
        best = 0
        for lv in self.active_levels():
            if xp >= lv.xp_needed and lv.level > best:
                best = lv.level
        return best

    def level_def(self, level: int) -> LevelDef | None:
        for lv in self.active_levels():
            if lv.level == level:
                return lv
        return None

    def all_level_role_ids(self) -> set[int]:
        ids = set()
        for lv in self.active_levels():
            if lv.role_id:
                ids.add(lv.role_id)
        return ids

    def passes_spam(self, user_id: int, content: str) -> bool:
        now = time.time()
        cd = self.cooldown()
        last = self._last_xp_time.get(user_id, 0.0)
        if cd > 0 and (now - last) < cd:
            return False

        if self.block_same():
            window = self.same_window()
            prev = self._last_msg.get(user_id)
            if prev:
                prev_content, prev_time = prev
                if window <= 0 or (now - prev_time) <= window:
                    if (content or "").strip().lower() == (prev_content or "").strip().lower():
                        return False

        self._last_xp_time[user_id] = now
        self._last_msg[user_id] = (content or "", now)
        return True

    async def apply_roles_for_level(self, member: discord.Member, new_level: int, force_remove_all: bool = False) -> None:
        lv = self.level_def(new_level)
        add_role = None
        if lv and lv.role_id:
            add_role = member.guild.get_role(lv.role_id)

        remove_ids = set()
        if force_remove_all:
            remove_ids = self.all_level_role_ids()
        elif self.remove_old_level_roles():
            remove_ids = self.all_level_role_ids()
            if lv and lv.role_id:
                remove_ids.discard(lv.role_id)

        to_remove = []
        if remove_ids:
            for r in member.roles:
                if r.id in remove_ids:
                    to_remove.append(r)

        try:
            if to_remove:
                await member.remove_roles(*to_remove, reason=f"Leveling: role cleanup (level {new_level})")
        except Exception as e:
            if self.log:
                self.log.exception(f"leveling_role_remove_error | guild={member.guild.id} | user={member.id} | {e}")

        if add_role and add_role not in member.roles:
            try:
                await member.add_roles(add_role, reason=f"Leveling: level {new_level}")
            except Exception as e:
                if self.log:
                    self.log.exception(f"leveling_role_add_error | guild={member.guild.id} | user={member.id} | role={add_role.id} | {e}")

    async def announce_levelup(self, member: discord.Member, new_level: int) -> None:
        if not self.announce_enabled():
            return
        ch_id = self.announce_channel_id()
        if ch_id <= 0:
            return
        channel = member.guild.get_channel(ch_id)
        if channel is None:
            try:
                channel = await member.guild.fetch_channel(ch_id)
            except Exception:
                return
        if not hasattr(channel, "send"):
            return

        lv = self.level_def(new_level)
        newrole = f"Level {new_level}"
        if lv and lv.role_id:
            role = member.guild.get_role(lv.role_id)
            if role:
                newrole = role.mention

        msg = self.announce_message()
        text = msg
        text = text.replace("%usermetion%", member.mention)
        text = text.replace("%newrole%", newrole)
        text = text.replace("%level%", str(new_level))
        text = text.replace("{user}", member.mention)
        text = text.replace("{level}", str(new_level))
        text = text.replace("{newrole}", newrole)
        try:
            await channel.send(text)
        except Exception as e:
            if self.log:
                self.log.exception(f"leveling_announce_error | guild={member.guild.id} | channel={ch_id} | {e}")

    async def get_rank(self, user_id: int) -> tuple[int, int, int]:
        entries = await self.storage.all_entries()
        items = [(uid, v.get("xp", 0), v.get("level", 0)) for uid, v in entries.items()]
        items.sort(key=lambda x: (x[1], x[0]), reverse=True)

        for i, (uid, xp, level) in enumerate(items, start=1):
            if uid == user_id:
                return i, int(xp), int(level)

        entry = await self.storage.get_entry(user_id)
        xp = _to_int(entry.get("xp", 0), 0)
        level = _to_int(entry.get("level", 0), 0)
        computed = self.compute_level(xp)
        if computed != level:
            level = computed
            await self.storage.set_entry(user_id, xp, level)
        return len(items) + 1, xp, level

    async def set_xp(self, member: discord.Member, xp: int) -> tuple[int, int, int, int]:
        xp = max(0, int(xp))
        old = await self.storage.get_entry(member.id)
        old_xp = _to_int(old.get("xp", 0), 0)
        old_level = _to_int(old.get("level", 0), 0)
        new_level = self.compute_level(xp)
        await self.storage.set_entry(member.id, xp, new_level)
        await self.apply_roles_for_level(member, new_level)
        return old_xp, old_level, xp, new_level

    async def set_level(self, member: discord.Member, level: int) -> tuple[int, int, int, int]:
        level = max(0, int(level))
        lv = self.level_def(level)
        if lv is None:
            raise ValueError("unknown_level")
        old = await self.storage.get_entry(member.id)
        old_xp = _to_int(old.get("xp", 0), 0)
        old_level = _to_int(old.get("level", 0), 0)
        xp = int(lv.xp_needed)
        await self.storage.set_entry(member.id, xp, level)
        await self.apply_roles_for_level(member, level)
        return old_xp, old_level, xp, level

    async def reset_level(self, member: discord.Member) -> None:
        await self.storage.set_entry(member.id, 0, 0)
        await self.apply_roles_for_level(member, 0, force_remove_all=True)
