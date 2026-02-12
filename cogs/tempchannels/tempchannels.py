import asyncio
import discord
from discord import app_commands
from discord.ext import commands


def _cfg(bot) -> dict:
    v = bot.cfg.get("temp_channels", {})
    return v if isinstance(v, dict) else {}


def _enabled(bot) -> bool:
    return bool(_cfg(bot).get("enabled", True))


def _hub_ids(bot) -> set[int]:
    v = _cfg(bot).get("hub_channel_ids", [])
    if not isinstance(v, list):
        return set()
    out = set()
    for x in v:
        try:
            out.add(int(x))
        except Exception:
            pass
    return out


def _category_id(bot) -> int:
    try:
        return int(_cfg(bot).get("category_id", 0) or 0)
    except Exception:
        return 0


def _name_template(bot) -> str:
    t = _cfg(bot).get("name_template", "{user}'s channel")
    return str(t) if t is not None else "{user}'s channel"


def _user_limit_default(bot) -> int:
    try:
        return max(0, int(_cfg(bot).get("user_limit_default", 0)))
    except Exception:
        return 0


def _lock_default(bot) -> bool:
    return bool(_cfg(bot).get("lock_by_default", False))


def _delete_delay(bot) -> int:
    try:
        return max(0, int(_cfg(bot).get("delete_delay_seconds", 3)))
    except Exception:
        return 3


def _owner_overwrites(bot) -> dict:
    v = _cfg(bot).get("owner_overwrites", {})
    return v if isinstance(v, dict) else {}


def _bypass_role_ids(bot) -> set[int]:
    v = _cfg(bot).get("bypass_role_ids", [])
    if not isinstance(v, list):
        return set()
    out = set()
    for x in v:
        try:
            out.add(int(x))
        except Exception:
            pass
    return out


def _is_bypass(member: discord.Member, bypass: set[int]) -> bool:
    if member.guild_permissions.administrator:
        return True
    if bypass and any(r.id in bypass for r in member.roles):
        return True
    return False


def _fmt_name(template: str, member: discord.Member) -> str:
    name = template.replace("{user}", member.display_name).replace("{username}", member.name)
    name = name.strip()
    if not name:
        name = f"{member.display_name}'s channel"
    if len(name) > 100:
        name = name[:100]
    return name


class TempChannels(commands.GroupCog, group_name="voice", group_description="Manage your temporary voice channel."):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.log = getattr(bot, "log", None)
        self._temp_owner: dict[int, int] = {}
        self._delete_tasks: dict[int, asyncio.Task] = {}
        super().__init__()

    def _is_temp(self, channel_id: int) -> bool:
        return channel_id in self._temp_owner

    def _owner_id(self, channel_id: int) -> int:
        return int(self._temp_owner.get(channel_id, 0) or 0)

    async def _cancel_delete(self, channel_id: int):
        t = self._delete_tasks.pop(channel_id, None)
        if t and not t.done():
            t.cancel()

    async def _schedule_delete_if_empty(self, channel: discord.VoiceChannel):
        await self._cancel_delete(channel.id)

        async def runner():
            try:
                await asyncio.sleep(_delete_delay(self.bot))
                ch = channel.guild.get_channel(channel.id)
                if not isinstance(ch, discord.VoiceChannel):
                    return
                if len(ch.members) == 0 and self._is_temp(ch.id):
                    try:
                        await ch.delete(reason="Temp voice cleanup")
                    except Exception:
                        return
                    self._temp_owner.pop(ch.id, None)
            except asyncio.CancelledError:
                return
            except Exception:
                return

        self._delete_tasks[channel.id] = asyncio.create_task(runner())

    async def _create_temp(self, member: discord.Member, hub: discord.VoiceChannel):
        cat_id = _category_id(self.bot)
        category = None
        if cat_id > 0:
            c = member.guild.get_channel(cat_id)
            if isinstance(c, discord.CategoryChannel):
                category = c
        if category is None:
            category = hub.category

        template = _name_template(self.bot)
        name = _fmt_name(template, member)

        ow_cfg = _owner_overwrites(self.bot)
        owner_overwrites = discord.PermissionOverwrite(
            manage_channels=bool(ow_cfg.get("manage_channels", True)),
            move_members=bool(ow_cfg.get("move_members", True)),
            mute_members=bool(ow_cfg.get("mute_members", True)),
            deafen_members=bool(ow_cfg.get("deafen_members", True))
        )

        overwrites = {
            member.guild.default_role: discord.PermissionOverwrite(connect=True, view_channel=True),
            member: owner_overwrites
        }

        ch = await member.guild.create_voice_channel(
            name=name,
            category=category,
            user_limit=_user_limit_default(self.bot),
            overwrites=overwrites,
            reason="Temp voice created"
        )

        self._temp_owner[ch.id] = member.id

        if _lock_default(self.bot):
            try:
                await ch.set_permissions(member.guild.default_role, connect=False, view_channel=True, reason="Temp voice lock default")
            except Exception:
                pass

        try:
            await member.move_to(ch, reason="Move to temp voice")
        except Exception:
            pass

        if self.log:
            self.log.info(f"temp_voice_created | channel_id={ch.id} | owner_id={member.id} | hub_id={hub.id}")

    async def _get_owner_channel(self, member: discord.Member) -> discord.VoiceChannel | None:
        vs = member.voice
        if not vs or not isinstance(vs.channel, discord.VoiceChannel):
            return None
        if self._owner_id(vs.channel.id) == member.id:
            return vs.channel
        return None

    async def _require_owner(self, interaction: discord.Interaction) -> discord.VoiceChannel | None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return None
        ch = await self._get_owner_channel(interaction.user)
        if ch is None:
            await interaction.response.send_message("You are not in your temp voice channel.", ephemeral=True)
            return None
        return ch

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if not _enabled(self.bot):
            return
        if member.guild is None:
            return

        hubs = _hub_ids(self.bot)

        if after.channel and isinstance(after.channel, discord.VoiceChannel):
            if after.channel.id in hubs and (before.channel is None or before.channel.id != after.channel.id):
                try:
                    await self._create_temp(member, after.channel)
                except Exception:
                    return

        if before.channel and isinstance(before.channel, discord.VoiceChannel):
            if self._is_temp(before.channel.id) and len(before.channel.members) == 0:
                await self._schedule_delete_if_empty(before.channel)

        if after.channel and isinstance(after.channel, discord.VoiceChannel):
            if self._is_temp(after.channel.id):
                await self._cancel_delete(after.channel.id)

    @app_commands.command(name="name", description="Rename your temp voice channel.")
    @app_commands.describe(name="New channel name")
    async def name(self, interaction: discord.Interaction, name: str):
        ch = await self._require_owner(interaction)
        if ch is None:
            return
        new_name = (name or "").strip()
        if not new_name:
            await interaction.response.send_message("Name cannot be empty.", ephemeral=True)
            return
        if len(new_name) > 100:
            new_name = new_name[:100]
        try:
            await ch.edit(name=new_name, reason="Temp voice rename")
        except Exception:
            await interaction.response.send_message("I can't rename this channel.", ephemeral=True)
            return
        await interaction.response.send_message("Channel renamed.", ephemeral=True)

    @app_commands.command(name="limit", description="Set user limit for your temp voice channel.")
    @app_commands.describe(limit="0 = unlimited")
    async def limit(self, interaction: discord.Interaction, limit: int):
        ch = await self._require_owner(interaction)
        if ch is None:
            return
        lim = max(0, int(limit))
        try:
            await ch.edit(user_limit=lim, reason="Temp voice limit")
        except Exception:
            await interaction.response.send_message("I can't change the user limit.", ephemeral=True)
            return
        await interaction.response.send_message("User limit updated.", ephemeral=True)

    @app_commands.command(name="lock", description="Lock your temp voice channel (no one can join).")
    async def lock(self, interaction: discord.Interaction):
        ch = await self._require_owner(interaction)
        if ch is None:
            return
        try:
            await ch.set_permissions(ch.guild.default_role, connect=False, view_channel=True, reason="Temp voice lock")
        except Exception:
            await interaction.response.send_message("I can't lock this channel.", ephemeral=True)
            return
        await interaction.response.send_message("Channel locked.", ephemeral=True)

    @app_commands.command(name="unlock", description="Unlock your temp voice channel.")
    async def unlock(self, interaction: discord.Interaction):
        ch = await self._require_owner(interaction)
        if ch is None:
            return
        try:
            await ch.set_permissions(ch.guild.default_role, connect=True, view_channel=True, reason="Temp voice unlock")
        except Exception:
            await interaction.response.send_message("I can't unlock this channel.", ephemeral=True)
            return
        await interaction.response.send_message("Channel unlocked.", ephemeral=True)

    @app_commands.command(name="claim", description="Claim ownership if the owner left.")
    async def claim(self, interaction: discord.Interaction):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        vs = interaction.user.voice
        if not vs or not isinstance(vs.channel, discord.VoiceChannel):
            await interaction.response.send_message("You are not in a voice channel.", ephemeral=True)
            return

        ch = vs.channel
        if not self._is_temp(ch.id):
            await interaction.response.send_message("This is not a temp voice channel.", ephemeral=True)
            return

        owner_id = self._owner_id(ch.id)
        owner = interaction.guild.get_member(owner_id) if owner_id else None
        if owner and owner.voice and owner.voice.channel and owner.voice.channel.id == ch.id:
            await interaction.response.send_message("The owner is still in the channel.", ephemeral=True)
            return

        bypass = _bypass_role_ids(self.bot)
        if not _is_bypass(interaction.user, bypass):
            self._temp_owner[ch.id] = interaction.user.id
        else:
            self._temp_owner[ch.id] = interaction.user.id

        ow_cfg = _owner_overwrites(self.bot)
        owner_overwrites = discord.PermissionOverwrite(
            manage_channels=bool(ow_cfg.get("manage_channels", True)),
            move_members=bool(ow_cfg.get("move_members", True)),
            mute_members=bool(ow_cfg.get("mute_members", True)),
            deafen_members=bool(ow_cfg.get("deafen_members", True))
        )
        try:
            await ch.set_permissions(interaction.user, overwrite=owner_overwrites, reason="Temp voice claim")
        except Exception:
            pass

        await interaction.response.send_message("You are now the owner of this temp voice channel.", ephemeral=True)
