import re
import discord
from discord.ext import commands


def _cfg(bot) -> dict:
    cfg = getattr(bot, "cfg", None)
    if cfg is None:
        return {}
    v = cfg.get("nickname_filter", {})
    return v if isinstance(v, dict) else {}


def _enabled(bot) -> bool:
    return bool(_cfg(bot).get("enabled", True))


def _guild_only(bot) -> bool:
    return bool(_cfg(bot).get("guild_only", True))


def _exempt_role_ids(bot) -> set[int]:
    v = _cfg(bot).get("exempt_role_ids", [])
    if not isinstance(v, list):
        return set()
    out = set()
    for x in v:
        try:
            out.add(int(x))
        except Exception:
            pass
    return out


def _admin_exempt(bot) -> bool:
    return bool(_cfg(bot).get("require_administrator_exempt", True))


def _words(bot) -> list[str]:
    v = _cfg(bot).get("disallowed_words", [])
    if not isinstance(v, list):
        return []
    out = []
    for s in v:
        if isinstance(s, str) and s.strip():
            out.append(s.strip().lower())
    return out


def _regex(bot) -> list[re.Pattern]:
    v = _cfg(bot).get("disallowed_regex", [])
    if not isinstance(v, list):
        return []
    out = []
    for s in v:
        if isinstance(s, str) and s.strip():
            try:
                out.append(re.compile(s, re.IGNORECASE))
            except Exception:
                pass
    return out


def _min_len(bot) -> int:
    try:
        return max(0, int(_cfg(bot).get("min_length", 2)))
    except Exception:
        return 2


def _max_len(bot) -> int:
    try:
        return max(1, int(_cfg(bot).get("max_length", 32)))
    except Exception:
        return 32


def _action(bot) -> dict:
    v = _cfg(bot).get("action", {})
    return v if isinstance(v, dict) else {}


def _reset_nick(bot) -> bool:
    return bool(_action(bot).get("reset_nickname", True))


def _dm_user(bot) -> bool:
    return bool(_action(bot).get("dm_user", False))


def _dm_msg(bot) -> str:
    s = _action(bot).get("dm_message", "")
    return str(s) if s is not None else ""


def _is_exempt(bot, member: discord.Member) -> bool:
    if _admin_exempt(bot) and member.guild_permissions.administrator:
        return True
    ex = _exempt_role_ids(bot)
    if ex and any(r.id in ex for r in member.roles):
        return True
    return False


def _violates(bot, nickname: str) -> bool:
    if nickname is None:
        return False
    name = nickname.strip()
    if not name:
        return False
    if len(name) < _min_len(bot):
        return True
    if len(name) > _max_len(bot):
        return True

    low = name.lower()
    for w in _words(bot):
        if w in low:
            return True

    for rx in _regex(bot):
        if rx.search(name):
            return True

    return False


class NicknameFilter(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _enforce(self, member: discord.Member):
        if not _enabled(self.bot):
            return
        if _guild_only(self.bot) and member.guild is None:
            return
        if _is_exempt(self.bot, member):
            return

        nick = member.nick if member.nick is not None else member.display_name
        if not _violates(self.bot, str(nick)):
            return

        if _reset_nick(self.bot):
            try:
                await member.edit(nick=None, reason="Nickname filter")
            except Exception:
                return

        if _dm_user(self.bot):
            msg = _dm_msg(self.bot)
            if msg:
                try:
                    await member.send(msg)
                except Exception:
                    pass

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self._enforce(member)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.nick == after.nick:
            return
        await self._enforce(after)
