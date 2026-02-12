from .userinfo import UserInfo
import discord


async def setup(bot):
    gid = int(bot.cfg.get("guild_id", 0) or 0)
    guild = discord.Object(id=gid) if gid else None
    await bot.add_cog(UserInfo(bot), guild=guild)
