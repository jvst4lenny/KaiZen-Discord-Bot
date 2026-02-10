import discord
from discord.ext import commands

from .service import LevelingService, _to_int


class LevelingCore(commands.Cog):
    def __init__(self, bot: commands.Bot, service: LevelingService):
        self.bot = bot
        self.service = service
        self.cfg = getattr(bot, "cfg", None)

    @commands.Cog.listener("on_message")
    async def on_message(self, message: discord.Message):
        if not self.service.enabled():
            return
        if message.author.bot:
            return
        if self.service.guild_only() and message.guild is None:
            return
        if message.guild is None:
            return
        if message.channel and message.channel.id in self.service.excluded_channels():
            return

        guild_id_cfg = 0
        if self.cfg is not None:
            guild_id_cfg = _to_int(self.cfg.get("guild_id", 0), 0)
        if guild_id_cfg and message.guild.id != guild_id_cfg:
            return

        member = message.author if isinstance(message.author, discord.Member) else None
        if member is None:
            try:
                member = await message.guild.fetch_member(message.author.id)
            except Exception:
                return

        if not self.service.passes_spam(member.id, message.content or ""):
            return

        gain = self.service.xp_per_message()
        if gain <= 0:
            return

        entry = await self.service.storage.get_entry(member.id)
        old_xp = _to_int(entry.get("xp", 0), 0)
        old_level = _to_int(entry.get("level", 0), 0)

        new_xp = old_xp + gain
        new_level = self.service.compute_level(new_xp)

        await self.service.storage.set_entry(member.id, new_xp, new_level)

        if new_level != old_level:
            await self.service.apply_roles_for_level(member, new_level)
            if new_level > old_level:
                await self.service.announce_levelup(member, new_level)
