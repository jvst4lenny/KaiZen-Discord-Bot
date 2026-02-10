from .service import LevelingService
from .core import LevelingCore
from .public_commands import LevelingPublicCommands
from .admin_commands import LevelingAdminCommands


async def setup(bot):
    service = LevelingService(bot)
    setattr(bot, "leveling_service", service)
    await bot.add_cog(LevelingCore(bot, service))
    await bot.add_cog(LevelingPublicCommands(bot, service))
    await bot.add_cog(LevelingAdminCommands(bot, service))
