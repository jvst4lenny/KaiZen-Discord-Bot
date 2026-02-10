import discord
from discord import app_commands
from discord.ext import commands

from .service import LevelingService


class LevelingAdminCommands(commands.Cog):
    def __init__(self, bot: commands.Bot, service: LevelingService):
        self.bot = bot
        self.service = service

    def _admin_cfg(self) -> dict:
        v = self.service.config().get("admin", {})
        return v if isinstance(v, dict) else {}

    def _admin_role_ids(self) -> set[int]:
        v = self._admin_cfg().get("role_ids", [])
        if not isinstance(v, list):
            return set()
        out = set()
        for x in v:
            try:
                i = int(x)
            except Exception:
                continue
            if i > 0:
                out.add(i)
        return out

    def _require_admin(self) -> bool:
        v = self._admin_cfg().get("require_administrator", True)
        return True if isinstance(v, bool) and v else False

    def _require_manage_guild(self) -> bool:
        v = self._admin_cfg().get("require_manage_guild", False)
        return True if isinstance(v, bool) and v else False

    def _has_admin_role(self, member: discord.Member) -> bool:
        ids = self._admin_role_ids()
        if not ids:
            return False
        return any(r.id in ids for r in member.roles)

    def _is_allowed(self, member: discord.Member) -> bool:
        if self._has_admin_role(member):
            return True
        if self._require_admin() and member.guild_permissions.administrator:
            return True
        if self._require_manage_guild() and member.guild_permissions.manage_guild:
            return True
        return False

    async def _deny(self, interaction: discord.Interaction):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)

    @app_commands.command(name="getxp", description="Get the current XP of a user.")
    @app_commands.describe(user="Select a user")
    async def getxp_cmd(self, interaction: discord.Interaction, user: discord.Member):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command is only available in a server.", ephemeral=True)
            return
        if not self._is_allowed(interaction.user):
            await self._deny(interaction)
            return

        entry = await self.service.storage.get_entry(user.id)
        xp = int(entry.get("xp", 0))
        level = int(entry.get("level", 0))
        await interaction.response.send_message(f"{user.mention} has **{xp} XP** (Level **{level}**).", ephemeral=True)

    @app_commands.command(name="setxp", description="Set XP for a user.")
    @app_commands.describe(user="Select a user", xp="New XP value")
    async def setxp_cmd(self, interaction: discord.Interaction, user: discord.Member, xp: int):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command is only available in a server.", ephemeral=True)
            return
        if not self._is_allowed(interaction.user):
            await self._deny(interaction)
            return

        old_xp, old_level, new_xp, new_level = await self.service.set_xp(user, xp)
        await interaction.response.send_message(
            f"Updated {user.mention}: XP **{old_xp} → {new_xp}**, Level **{old_level} → {new_level}**.",
            ephemeral=True,
        )

    @app_commands.command(name="setlevel", description="Set level for a user (sets XP to the minimum required for that level).")
    @app_commands.describe(user="Select a user", level="New level")
    async def setlevel_cmd(self, interaction: discord.Interaction, user: discord.Member, level: int):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command is only available in a server.", ephemeral=True)
            return
        if not self._is_allowed(interaction.user):
            await self._deny(interaction)
            return

        try:
            old_xp, old_level, new_xp, new_level = await self.service.set_level(user, level)
        except ValueError:
            await interaction.response.send_message("Unknown level. Check your config leveling.levels.", ephemeral=True)
            return

        await interaction.response.send_message(
            f"Updated {user.mention}: XP **{old_xp} → {new_xp}**, Level **{old_level} → {new_level}**.",
            ephemeral=True,
        )

    @app_commands.command(name="resetlevel", description="Reset a user's level and XP to 0.")
    @app_commands.describe(user="Select a user")
    async def resetlevel_cmd(self, interaction: discord.Interaction, user: discord.Member):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command is only available in a server.", ephemeral=True)
            return
        if not self._is_allowed(interaction.user):
            await self._deny(interaction)
            return

        await self.service.reset_level(user)
        await interaction.response.send_message(f"Reset level for {user.mention}.", ephemeral=True)
