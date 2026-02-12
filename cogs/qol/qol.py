import re
from datetime import datetime, timezone
import discord
from discord import app_commands
from discord.ext import commands


def _cfg(bot) -> dict:
    v = bot.cfg.get("qol", {})
    return v if isinstance(v, dict) else {}


def _enabled(bot) -> bool:
    return bool(_cfg(bot).get("enabled", True))


def _guild_only(bot) -> bool:
    return bool(_cfg(bot).get("guild_only", True))


def _subcfg(bot, key: str) -> dict:
    v = _cfg(bot).get(key, {})
    return v if isinstance(v, dict) else {}


def _sub_enabled(bot, key: str) -> bool:
    return bool(_subcfg(bot, key).get("enabled", True))


def _parse_color(s: str | None, fallback: str) -> discord.Color:
    raw = (s or "").strip()
    if not raw:
        raw = (fallback or "").strip()
    if not raw:
        return discord.Color.dark_grey()

    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", raw)
    if m:
        return discord.Color(int(m.group(1), 16))

    try:
        v = int(raw)
        v = max(0, min(0xFFFFFF, v))
        return discord.Color(v)
    except Exception:
        return discord.Color.dark_grey()


async def _resolve_message(interaction: discord.Interaction, message_id: int, channel: discord.TextChannel | None):
    if channel is None:
        if isinstance(interaction.channel, discord.TextChannel):
            channel = interaction.channel
        else:
            return None, None

    try:
        msg = await channel.fetch_message(message_id)
        return channel, msg
    except Exception:
        return channel, None


class QoL(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="sendmessage", description="Send a plain text message to a channel.")
    @app_commands.describe(channel="Channel to send to (optional)", content="Message content")
    async def sendmessage(self, interaction: discord.Interaction, content: str, channel: discord.TextChannel | None = None):
        if not _enabled(self.bot) or not _sub_enabled(self.bot, "send_message"):
            return

        if _guild_only(self.bot) and interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        target = channel or (interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None)
        if target is None:
            await interaction.response.send_message("Invalid channel.", ephemeral=True)
            return

        me = interaction.guild.me
        if me is None:
            await interaction.response.send_message("Bot member not found.", ephemeral=True)
            return

        perms = target.permissions_for(me)
        if not perms.send_messages:
            await interaction.response.send_message("I need Send Messages permission in that channel.", ephemeral=True)
            return

        await interaction.response.send_message("Sent.", ephemeral=True)
        await target.send(content)

    @app_commands.command(name="sendembed", description="Send an embed message to a channel.")
    @app_commands.describe(
        channel="Channel to send to (optional)",
        title="Embed title (optional)",
        description="Embed description (optional)",
        color="Hex color like #ff0000 or integer (optional)",
        thumbnail_url="Thumbnail image url (optional)",
        image_url="Big image url (optional)"
    )
    async def sendembed(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        title: str | None = None,
        description: str | None = None,
        color: str | None = None,
        thumbnail_url: str | None = None,
        image_url: str | None = None
    ):
        if not _enabled(self.bot) or not _sub_enabled(self.bot, "send_embed"):
            return

        if _guild_only(self.bot) and interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        target = channel or (interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None)
        if target is None:
            await interaction.response.send_message("Invalid channel.", ephemeral=True)
            return

        me = interaction.guild.me
        if me is None:
            await interaction.response.send_message("Bot member not found.", ephemeral=True)
            return

        perms = target.permissions_for(me)
        if not perms.send_messages or not perms.embed_links:
            await interaction.response.send_message("I need Send Messages + Embed Links permission in that channel.", ephemeral=True)
            return

        now = datetime.now(timezone.utc)

        default_color = str(_subcfg(self.bot, "send_embed").get("default_color", "#2B2D31") or "#2B2D31")
        emb = discord.Embed(
            title=(title or "").strip() or None,
            description=(description or "").strip() or None,
            color=_parse_color(color, default_color),
            timestamp=now
        )

        prefix = str(_subcfg(self.bot, "send_embed").get("footer_prefix", "") or "").strip()
        if prefix:
            emb.set_footer(text=prefix)

        turl = (thumbnail_url or "").strip()
        if turl:
            emb.set_thumbnail(url=turl)

        iurl = (image_url or "").strip()
        if iurl:
            emb.set_image(url=iurl)

        await interaction.response.send_message("Sent.", ephemeral=True)
        await target.send(embed=emb)

    @app_commands.command(name="editmessage", description="Edit a message (plain content).")
    @app_commands.describe(message_id="Message ID", channel="Channel where the message is (optional)", content="New message content")
    async def editmessage(self, interaction: discord.Interaction, message_id: str, content: str, channel: discord.TextChannel | None = None):
        if not _enabled(self.bot) or not _sub_enabled(self.bot, "edit_message"):
            return

        if _guild_only(self.bot) and interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        mid = 0
        try:
            mid = int(message_id)
        except Exception:
            pass
        if mid <= 0:
            await interaction.response.send_message("Invalid message ID.", ephemeral=True)
            return

        target_channel, msg = await _resolve_message(interaction, mid, channel)
        if target_channel is None:
            await interaction.response.send_message("Invalid channel.", ephemeral=True)
            return
        if msg is None:
            await interaction.response.send_message("Message not found.", ephemeral=True)
            return

        if interaction.client.user is None or msg.author.id != interaction.client.user.id:
            await interaction.response.send_message("I can only edit messages sent by me.", ephemeral=True)
            return

        me = interaction.guild.me
        if me is None:
            await interaction.response.send_message("Bot member not found.", ephemeral=True)
            return

        perms = target_channel.permissions_for(me)
        if not perms.send_messages:
            await interaction.response.send_message("Missing Send Messages permission.", ephemeral=True)
            return

        try:
            await msg.edit(content=content)
        except Exception:
            await interaction.response.send_message("I couldn't edit that message.", ephemeral=True)
            return

        await interaction.response.send_message("Edited.", ephemeral=True)

    @app_commands.command(name="editembed", description="Edit an embed on a message (replaces the first embed).")
    @app_commands.describe(
        message_id="Message ID",
        channel="Channel where the message is (optional)",
        title="New embed title (optional)",
        description="New embed description (optional)",
        color="Hex color like #ff0000 or integer (optional)",
        thumbnail_url="Thumbnail image url (optional)",
        image_url="Big image url (optional)"
    )
    async def editembed(
        self,
        interaction: discord.Interaction,
        message_id: str,
        channel: discord.TextChannel | None = None,
        title: str | None = None,
        description: str | None = None,
        color: str | None = None,
        thumbnail_url: str | None = None,
        image_url: str | None = None
    ):
        if not _enabled(self.bot) or not _sub_enabled(self.bot, "edit_embed"):
            return

        if _guild_only(self.bot) and interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        mid = 0
        try:
            mid = int(message_id)
        except Exception:
            pass
        if mid <= 0:
            await interaction.response.send_message("Invalid message ID.", ephemeral=True)
            return

        target_channel, msg = await _resolve_message(interaction, mid, channel)
        if target_channel is None:
            await interaction.response.send_message("Invalid channel.", ephemeral=True)
            return
        if msg is None:
            await interaction.response.send_message("Message not found.", ephemeral=True)
            return

        if interaction.client.user is None or msg.author.id != interaction.client.user.id:
            await interaction.response.send_message("I can only edit messages sent by me.", ephemeral=True)
            return

        me = interaction.guild.me
        if me is None:
            await interaction.response.send_message("Bot member not found.", ephemeral=True)
            return

        perms = target_channel.permissions_for(me)
        if not perms.send_messages or not perms.embed_links:
            await interaction.response.send_message("I need Send Messages + Embed Links permission in that channel.", ephemeral=True)
            return

        now = datetime.now(timezone.utc)

        default_color = str(_subcfg(self.bot, "send_embed").get("default_color", "#2B2D31") or "#2B2D31")
        emb = discord.Embed(
            title=(title or "").strip() or None,
            description=(description or "").strip() or None,
            color=_parse_color(color, default_color),
            timestamp=now
        )

        prefix = str(_subcfg(self.bot, "send_embed").get("footer_prefix", "") or "").strip()
        if prefix:
            emb.set_footer(text=prefix)

        turl = (thumbnail_url or "").strip()
        if turl:
            emb.set_thumbnail(url=turl)

        iurl = (image_url or "").strip()
        if iurl:
            emb.set_image(url=iurl)

        try:
            await msg.edit(embed=emb)
        except Exception:
            await interaction.response.send_message("I couldn't edit that embed.", ephemeral=True)
            return

        await interaction.response.send_message("Edited.", ephemeral=True)

    @app_commands.command(name="deletemsg", description="Delete a bot message by message ID.")
    @app_commands.describe(message_id="Message ID", channel="Channel where the message is (optional)")
    async def deletemsg(self, interaction: discord.Interaction, message_id: str, channel: discord.TextChannel | None = None):
        if not _enabled(self.bot):
            return

        if _guild_only(self.bot) and interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        mid = 0
        try:
            mid = int(message_id)
        except Exception:
            pass
        if mid <= 0:
            await interaction.response.send_message("Invalid message ID.", ephemeral=True)
            return

        target_channel, msg = await _resolve_message(interaction, mid, channel)
        if target_channel is None:
            await interaction.response.send_message("Invalid channel.", ephemeral=True)
            return
        if msg is None:
            await interaction.response.send_message("Message not found.", ephemeral=True)
            return

        if interaction.client.user is None or msg.author.id != interaction.client.user.id:
            await interaction.response.send_message("I can only delete messages sent by me.", ephemeral=True)
            return

        me = interaction.guild.me
        if me is None:
            await interaction.response.send_message("Bot member not found.", ephemeral=True)
            return

        perms = target_channel.permissions_for(me)
        if not perms.manage_messages and not perms.send_messages:
            await interaction.response.send_message("Missing permissions in that channel.", ephemeral=True)
            return

        try:
            await msg.delete()
        except Exception:
            await interaction.response.send_message("I couldn't delete that message.", ephemeral=True)
            return

        await interaction.response.send_message("Deleted.", ephemeral=True)
