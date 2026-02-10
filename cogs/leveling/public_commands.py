import discord
from discord import app_commands
from discord.ext import commands

from .service import LevelingService


class LevelingPublicCommands(commands.Cog):
    def __init__(self, bot: commands.Bot, service: LevelingService):
        self.bot = bot
        self.service = service

    @app_commands.command(name="level", description="Show your level and XP.")
    @app_commands.describe(user="Optional: select a user")
    async def level_cmd(self, interaction: discord.Interaction, user: discord.Member | None = None):
        if not self.service.enabled():
            await interaction.response.send_message("Leveling is disabled.", ephemeral=True)
            return
        if interaction.guild is None:
            await interaction.response.send_message("This command is only available in a server.", ephemeral=True)
            return

        target = user or interaction.user
        if not isinstance(target, discord.Member):
            await interaction.response.send_message("Invalid user.", ephemeral=True)
            return

        rank, xp, level = await self.service.get_rank(target.id)

        embed = discord.Embed(title="Level")
        embed.add_field(name="User", value=target.mention, inline=False)
        embed.add_field(name="Level", value=str(level), inline=True)
        embed.add_field(name="XP", value=str(xp), inline=True)
        embed.add_field(name="Rank", value=f"#{rank}", inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=False)

    @app_commands.command(name="rank", description="Show your rank based on XP.")
    @app_commands.describe(user="Optional: select a user")
    async def rank_cmd(self, interaction: discord.Interaction, user: discord.Member | None = None):
        await self.level_cmd(interaction, user)

    @app_commands.command(name="leaderboard", description="Show the top users by XP.")
    async def leaderboard_cmd(self, interaction: discord.Interaction):
        if not self.service.enabled():
            await interaction.response.send_message("Leveling is disabled.", ephemeral=True)
            return
        if interaction.guild is None:
            await interaction.response.send_message("This command is only available in a server.", ephemeral=True)
            return

        size = self.service.leaderboard_size()
        entries = await self.service.storage.all_entries()
        items = [(uid, v.get("xp", 0), v.get("level", 0)) for uid, v in entries.items()]
        items.sort(key=lambda x: (int(x[1]), int(x[0])), reverse=True)
        top = items[:size]

        lines = []
        for i, (uid, xp, lvl) in enumerate(top, start=1):
            member = interaction.guild.get_member(uid)
            name = member.mention if member else f"<@{uid}>"
            lines.append(f"**#{i}** {name} • Level **{int(lvl)}** • XP **{int(xp)}**")

        if not lines:
            lines = ["No data yet."]

        embed = discord.Embed(title="Leaderboard", description="\n".join(lines))
        await interaction.response.send_message(embed=embed, ephemeral=False)
