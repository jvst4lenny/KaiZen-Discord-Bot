import re
import discord
from discord.ext import commands


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


class LinkFilter(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cfg = getattr(bot, "cfg", None)
        self.log = getattr(bot, "log", None)
        self.url_re = re.compile(
            r"(?i)\b("
            r"(?:https?://|www\.)\S+"
            r"|discord\.gg/\S+"
            r"|discord\.com/invite/\S+"
            r"|discordapp\.com/invite/\S+"
            r")\b"
        )

    def _cfg(self) -> dict:
        if self.cfg is None:
            return {}
        v = self.cfg.get("link_filter", {})
        return v if isinstance(v, dict) else {}

    def _enabled(self) -> bool:
        return _to_bool(self._cfg().get("enabled", True), True)

    def _guild_only(self) -> bool:
        return _to_bool(self._cfg().get("guild_only", True), True)

    def _excluded_channels(self) -> set[int]:
        v = self._cfg().get("excluded_channel_ids", [])
        if not isinstance(v, list):
            return set()
        out = set()
        for x in v:
            i = _to_int(x, 0)
            if i > 0:
                out.add(i)
        return out

    def _bypass_roles(self) -> set[int]:
        v = self._cfg().get("bypass_role_ids", [])
        if not isinstance(v, list):
            return set()
        out = set()
        for x in v:
            i = _to_int(x, 0)
            if i > 0:
                out.add(i)
        return out

    def _allowed_domains(self) -> set[str]:
        v = self._cfg().get("allowed_domains", [])
        if not isinstance(v, list):
            return set()
        out = set()
        for x in v:
            s = str(x).strip().lower()
            if s:
                out.add(s)
        return out

    def _action_cfg(self) -> dict:
        v = self._cfg().get("action", {})
        return v if isinstance(v, dict) else {}

    def _delete_message(self) -> bool:
        return _to_bool(self._action_cfg().get("delete_message", True), True)

    def _warn_in_channel(self) -> bool:
        return _to_bool(self._action_cfg().get("warn_in_channel", True), True)

    def _warn_delete_after(self) -> int:
        return max(0, _to_int(self._action_cfg().get("warn_delete_after_seconds", 6), 6))

    def _warn_message(self) -> str:
        return str(self._action_cfg().get("warn_message", "{user} links are not allowed here."))

    def _has_bypass(self, member: discord.Member) -> bool:
        bypass = self._bypass_roles()
        if bypass and any(r.id in bypass for r in member.roles):
            return True
        return False

    def _extract_domains(self, text: str) -> set[str]:
        if not text:
            return set()
        hits = set()
        for m in re.finditer(r"(?i)\bhttps?://([a-z0-9\.\-]+)", text):
            host = (m.group(1) or "").lower()
            if host.startswith("www."):
                host = host[4:]
            if host:
                hits.add(host)
        for m in re.finditer(r"(?i)\bwww\.([a-z0-9\.\-]+)", text):
            host = (m.group(1) or "").lower()
            if host.startswith("www."):
                host = host[4:]
            if host:
                hits.add(host)
        return hits

    def _is_allowed_link(self, text: str) -> bool:
        allowed = self._allowed_domains()
        if not allowed:
            return False
        domains = self._extract_domains(text)
        if not domains:
            return False
        for d in domains:
            for a in allowed:
                if d == a or d.endswith("." + a):
                    return True
        return False

    def _contains_link(self, content: str) -> bool:
        if not content:
            return False
        return self.url_re.search(content) is not None

    @commands.Cog.listener("on_message")
    async def on_message(self, message: discord.Message):
        if not self._enabled():
            return
        if message.author.bot:
            return
        if self._guild_only() and message.guild is None:
            return
        if message.guild is None:
            return
        if message.channel and message.channel.id in self._excluded_channels():
            return

        member = message.author if isinstance(message.author, discord.Member) else None
        if member is None:
            try:
                member = await message.guild.fetch_member(message.author.id)
            except Exception:
                return

        if self._has_bypass(member):
            return

        text = message.content or ""
        has_link = self._contains_link(text)
        if not has_link and message.attachments:
            for a in message.attachments:
                if a.url and self._contains_link(a.url):
                    has_link = True
                    text = (text + " " + a.url).strip()
                    break

        if not has_link:
            return

        if self._is_allowed_link(text):
            return

        if self._delete_message():
            try:
                await message.delete()
            except Exception:
                return

        if self._warn_in_channel():
            txt = self._warn_message().replace("{user}", member.mention)
            try:
                warn = await message.channel.send(txt)
                d = self._warn_delete_after()
                if d > 0:
                    await warn.delete(delay=d)
            except Exception:
                return
