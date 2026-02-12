import discord
from discord.ext import commands


def _cfg(bot) -> dict:
    cfg = getattr(bot, "cfg", None)
    if cfg is None:
        return {}
    v = cfg.get("join_leave", {})
    return v if isinstance(v, dict) else {}


def _enabled(bot) -> bool:
    return bool(_cfg(bot).get("enabled", True))


def _guild_only(bot) -> bool:
    return bool(_cfg(bot).get("guild_only", True))


def _auto_role_id(bot) -> int:
    try:
        return int(_cfg(bot).get("auto_role_id", 0) or 0)
    except Exception:
        return 0


def _section(bot, key: str) -> dict:
    v = _cfg(bot).get(key, {})
    return v if isinstance(v, dict) else {}


def _fmt(text: str, member: discord.Member) -> str:
    server = member.guild.name if member.guild else "Server"
    mc = member.guild.member_count if member.guild and member.guild.member_count else 0
    return (
        str(text)
        .replace("{user}", member.mention)
        .replace("{server}", server)
        .replace("{member_count}", str(mc))
    )


class JoinLeave(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _send_embed(self, channel_id: int, title: str, description: str, footer: str, member: discord.Member):
        if member.guild is None:
            return
        ch = member.guild.get_channel(channel_id)
        if ch is None:
            try:
                ch = await member.guild.fetch_channel(channel_id)
            except Exception:
                return
        if not isinstance(ch, discord.TextChannel):
            return

        embed = discord.Embed(title=_fmt(title, member), description=_fmt(description, member))
        embed.set_thumbnail(url=member.display_avatar.url)
        f = _fmt(footer, member).strip()
        if f:
            embed.set_footer(text=f)
        await ch.send(embed=embed)

    async def _auto_role(self, member: discord.Member):
        rid = _auto_role_id(self.bot)
        if rid <= 0:
            return
        role = member.guild.get_role(rid) if member.guild else None
        if role is None and member.guild:
            try:
                role = await member.guild.fetch_role(rid)
            except Exception:
                role = None
        if role:
            try:
                await member.add_roles(role, reason="Auto role")
            except Exception:
                pass

    async def _dm(self, member: discord.Member):
        dm = _section(self.bot, "dm")
        if not bool(dm.get("enabled", False)):
            return
        msg = str(dm.get("message", "") or "").strip()
        if not msg:
            return
        try:
            await member.send(_fmt(msg, member))
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if not _enabled(self.bot):
            return
        if _guild_only(self.bot) and member.guild is None:
            return

        await self._auto_role(member)
        await self._dm(member)

        w = _section(self.bot, "welcome")
        if not bool(w.get("enabled", True)):
            return
        cid = int(w.get("channel_id", 0) or 0)
        if cid <= 0:
            return

        title = str(w.get("title", "Welcome!") or "Welcome!")
        desc = str(w.get("description", "Welcome {user} to **{server}**!") or "")
        footer = str(w.get("footer", "Member #{member_count}") or "")
        try:
            await self._send_embed(cid, title, desc, footer, member)
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if not _enabled(self.bot):
            return
        if _guild_only(self.bot) and member.guild is None:
            return

        g = _section(self.bot, "goodbye")
        if not bool(g.get("enabled", False)):
            return
        cid = int(g.get("channel_id", 0) or 0)
        if cid <= 0:
            return

        title = str(g.get("title", "Goodbye!") or "Goodbye!")
        desc = str(g.get("description", "{user} left **{server}**.") or "")
        footer = str(g.get("footer", "Member #{member_count}") or "")
        try:
            await self._send_embed(cid, title, desc, footer, member)
        except Exception:
            pass
