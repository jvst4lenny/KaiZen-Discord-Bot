from .nicknamefilter import NicknameFilter


async def setup(bot):
    await bot.add_cog(NicknameFilter(bot))
