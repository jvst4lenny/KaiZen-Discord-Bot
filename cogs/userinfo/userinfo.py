import discord
from discord import app_commands
from discord.ext import commands


def _cfg(bot) -> dict:
    cfg = getattr(bot, "cfg", None)
    if cfg is None:
        return {}
    v = cfg.get("userinfo", {})
    return v if isinstance(v, dict) else {}


def _enabled(bot) -> bool:
    return bool(_cfg(bot).get("enabled", True))


def _guild_only(bot) -> bool:
    return bool(_cfg(bot).get("guild_only", True))


def _show_roles(bot) -> bool:
    return bool(_cfg(bot).get("show_roles", True))


def _max_roles(bot) -> int:
    try:
        return max(0, int(_cfg(bot).get("max_roles", 15)))
    except Exception:
        return 15


class UserInfo(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="userinfo", description="Show information about a user.")
    @app_commands.describe(user="Select a user")
    async def userinfo(self, interaction: discord.Interaction, user: discord.Member | None = None):
        if not _enabled(self.bot):
            return
        if _guild_only(self.bot) and interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        member = user or interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message("User not found.", ephemeral=True)
            return

        embed = discord.Embed(title=str(member), description=f"<@{member.id}>")
        embed.set_thumbnail(url=member.display_avatar.url)

        embed.add_field(name="User ID", value=str(member.id), inline=True)
        embed.add_field(name="Account Created", value=f"<t:{int(member.created_at.timestamp())}:F>", inline=True)

        if member.joined_at:
            embed.add_field(name="Joined Server", value=f"<t:{int(member.joined_at.timestamp())}:F>", inline=True)

        embed.add_field(name="Top Role", value=member.top_role.mention if member.top_role else "None", inline=True)

        if _show_roles(self.bot):
            roles = [r for r in member.roles if r and r.name != "@everyone"]
            roles = sorted(roles, key=lambda r: r.position, reverse=True)
            cap = _max_roles(self.bot)
            shown = roles[:cap]
            text = " ".join([r.mention for r in shown]) if shown else "None"
            if cap and len(roles) > cap:
                text = text + f" (+{len(roles) - cap} more)"
            embed.add_field(name="Roles", value=text, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)
