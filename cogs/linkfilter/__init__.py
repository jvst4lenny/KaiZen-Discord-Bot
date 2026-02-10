from .linkfilter import LinkFilter


async def setup(bot):
    await bot.add_cog(LinkFilter(bot))
