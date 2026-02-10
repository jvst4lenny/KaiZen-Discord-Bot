import asyncio
import re
import secrets
import time
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from .storage import JsonStorage


def _to_int(v, default=0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _to_bool(v, default=False) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "1", "yes", "y", "on"):
            return True
        if s in ("false", "0", "no", "n", "off"):
            return False
    return default


def _parse_duration_to_seconds(text: str) -> int:
    s = (text or "").strip().lower().replace(" ", "")
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    total = 0
    for num, unit in re.findall(r"(\d+)([smhdw])", s):
        n = int(num)
        if unit == "s":
            total += n
        elif unit == "m":
            total += n * 60
        elif unit == "h":
            total += n * 3600
        elif unit == "d":
            total += n * 86400
        elif unit == "w":
            total += n * 604800
    return total


class GiveawayJoinView(discord.ui.View):
    def __init__(self, cog: "Giveaway", giveaway_id: str):
        super().__init__(timeout=None)
        self.cog = cog
        self.giveaway_id = giveaway_id
        self._add_button()

    def _add_button(self):
        custom_id = f"giveaway_join:{self.giveaway_id}"
        b = discord.ui.Button(label=self.cog._button_label(), style=discord.ButtonStyle.success, custom_id=custom_id)
        b.callback = self._on_click
        self.add_item(b)

    async def _on_click(self, interaction: discord.Interaction):
        await self.cog.handle_join(interaction, self.giveaway_id)


class Giveaway(commands.GroupCog, group_name="giveaway", group_description="Giveaway commands."):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cfg = getattr(bot, "cfg", None)
        self.log = getattr(bot, "log", None)
        path = str(self._cfg().get("storage_path", "data/giveaways.json"))
        self.storage = JsonStorage(path, log=self.log)
        self._runner_task: asyncio.Task | None = None
        super().__init__()

    def _cfg(self) -> dict:
        if self.cfg is None:
            return {}
        v = self.cfg.get("giveaway", {})
        return v if isinstance(v, dict) else {}

    def _enabled(self) -> bool:
        return _to_bool(self._cfg().get("enabled", True), True)

    def _tick_seconds(self) -> int:
        return max(3, _to_int(self._cfg().get("tick_seconds", 10), 10))

    def _default_winners(self) -> int:
        return max(1, _to_int(self._cfg().get("default_winners", 1), 1))

    def _max_winners(self) -> int:
        return max(1, _to_int(self._cfg().get("max_winners", 20), 20))

    def _max_prize_len(self) -> int:
        return max(20, _to_int(self._cfg().get("max_prize_length", 120), 120))

    def _button_label(self) -> str:
        return str(self._cfg().get("button_label", "Join Giveaway"))[:80]

    def _perm_cfg(self) -> dict:
        v = self._cfg().get("start_permissions", {})
        return v if isinstance(v, dict) else {}

    def _admin_role_ids(self) -> set[int]:
        v = self._perm_cfg().get("role_ids", [])
        if not isinstance(v, list):
            return set()
        out = set()
        for x in v:
            i = _to_int(x, 0)
            if i > 0:
                out.add(i)
        return out

    def _require_admin(self) -> bool:
        return _to_bool(self._perm_cfg().get("require_administrator", True), True)

    def _require_manage_guild(self) -> bool:
        return _to_bool(self._perm_cfg().get("require_manage_guild", False), False)

    def _is_allowed(self, member: discord.Member) -> bool:
        role_ids = self._admin_role_ids()
        if role_ids and any(r.id in role_ids for r in member.roles):
            return True
        if self._require_admin() and member.guild_permissions.administrator:
            return True
        if self._require_manage_guild() and member.guild_permissions.manage_guild:
            return True
        return False

    def _make_embed(self, prize: str, winners: int, host_id: int, end_ts: int, entries: int, ended: bool, winner_ids: list[int] | None) -> discord.Embed:
        embed = discord.Embed(title="Giveaway", description=f"**Prize:** {prize}")
        embed.add_field(name="Winners", value=str(winners), inline=True)
        embed.add_field(name="Entries", value=str(entries), inline=True)
        embed.add_field(name="Hosted by", value=f"<@{host_id}>", inline=True)
        if ended:
            embed.add_field(name="Status", value="Ended", inline=True)
            if winner_ids:
                embed.add_field(name="Winner(s)", value=" ".join([f"<@{i}>" for i in winner_ids]), inline=False)
            else:
                embed.add_field(name="Winner(s)", value="No valid entries.", inline=False)
        else:
            embed.add_field(name="Ends", value=f"<t:{end_ts}:R>", inline=True)
        return embed

    async def _edit_message(self, guild_id: int, channel_id: int, message_id: int, embed: discord.Embed, ended: bool, giveaway_id: str):
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            try:
                guild = await self.bot.fetch_guild(guild_id)
            except Exception:
                return

        channel = guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(channel_id)
            except Exception:
                return

        try:
            msg = await channel.fetch_message(message_id)
        except Exception:
            return

        view = None
        if not ended:
            view = GiveawayJoinView(self, giveaway_id)
        await msg.edit(embed=embed, view=view)

    def _pick_winners(self, entries: list[int], count: int) -> list[int]:
        unique = list(dict.fromkeys([int(x) for x in entries if int(x) > 0]))
        if not unique:
            return []
        secrets.SystemRandom().shuffle(unique)
        return unique[:count]

    async def handle_join(self, interaction: discord.Interaction, giveaway_id: str):
        if not self._enabled():
            await interaction.response.send_message("Giveaways are disabled.", ephemeral=True)
            return
        if interaction.guild is None:
            await interaction.response.send_message("This is only available in a server.", ephemeral=True)
            return

        gw = await self.storage.get(giveaway_id)
        if not gw:
            await interaction.response.send_message("This giveaway no longer exists.", ephemeral=True)
            return
        if _to_bool(gw.get("ended", False), False):
            await interaction.response.send_message("This giveaway already ended.", ephemeral=True)
            return

        uid = interaction.user.id
        entries = gw.get("entries", [])
        if not isinstance(entries, list):
            entries = []

        if uid in entries:
            entries = [x for x in entries if x != uid]
            gw["entries"] = entries
            await self.storage.set(giveaway_id, gw)
            await interaction.response.send_message("You left the giveaway.", ephemeral=True)
        else:
            entries.append(uid)
            gw["entries"] = entries
            await self.storage.set(giveaway_id, gw)
            await interaction.response.send_message("You joined the giveaway.", ephemeral=True)

        try:
            embed = self._make_embed(
                str(gw.get("prize", "Unknown")),
                _to_int(gw.get("winners", 1), 1),
                _to_int(gw.get("host_id", 0), 0),
                _to_int(gw.get("end_ts", 0), 0),
                len(entries),
                False,
                None,
            )
            await self._edit_message(
                _to_int(gw.get("guild_id", 0), 0),
                _to_int(gw.get("channel_id", 0), 0),
                _to_int(gw.get("message_id", 0), 0),
                embed,
                False,
                giveaway_id,
            )
        except Exception:
            pass

    async def _end_giveaway(self, giveaway_id: str, force: bool = False) -> list[int]:
        gw = await self.storage.get(giveaway_id)
        if not gw:
            return []

        if _to_bool(gw.get("ended", False), False) and not force:
            w = gw.get("winner_ids", [])
            return w if isinstance(w, list) else []

        guild_id = _to_int(gw.get("guild_id", 0), 0)
        channel_id = _to_int(gw.get("channel_id", 0), 0)
        message_id = _to_int(gw.get("message_id", 0), 0)
        prize = str(gw.get("prize", "Unknown"))[: self._max_prize_len()]
        winners = max(1, min(self._max_winners(), _to_int(gw.get("winners", 1), 1)))
        host_id = _to_int(gw.get("host_id", 0), 0)
        end_ts = _to_int(gw.get("end_ts", 0), 0)

        entries = gw.get("entries", [])
        if not isinstance(entries, list):
            entries = []
        winner_ids = self._pick_winners([_to_int(x, 0) for x in entries], winners)

        gw["ended"] = True
        gw["winner_ids"] = winner_ids
        gw["ended_ts"] = int(time.time())
        await self.storage.set(giveaway_id, gw)

        embed = self._make_embed(prize, winners, host_id, end_ts, len(entries), True, winner_ids)

        try:
            await self._edit_message(guild_id, channel_id, message_id, embed, True, giveaway_id)
        except Exception:
            pass

        guild = self.bot.get_guild(guild_id)
        channel = guild.get_channel(channel_id) if guild else None
        if channel is None and guild:
            try:
                channel = await guild.fetch_channel(channel_id)
            except Exception:
                channel = None

        if channel and hasattr(channel, "send"):
            if winner_ids:
                text = "🎉 Giveaway ended! Winners: " + " ".join([f"<@{i}>" for i in winner_ids]) + f" | Prize: **{prize}**"
            else:
                text = f"Giveaway ended. No valid entries. | Prize: **{prize}**"
            try:
                await channel.send(text)
            except Exception:
                pass

        return winner_ids

    async def _runner(self):
        while True:
            await asyncio.sleep(self._tick_seconds())
            if not self._enabled():
                continue
            all_gw = await self.storage.all()
            now = int(time.time())
            for gid, gw in all_gw.items():
                try:
                    if _to_bool(gw.get("ended", False), False):
                        continue
                    end_ts = _to_int(gw.get("end_ts", 0), 0)
                    if end_ts > 0 and now >= end_ts:
                        await self._end_giveaway(gid)
                except Exception:
                    continue

    async def cog_load(self) -> None:
        all_gw = await self.storage.all()
        for gid, gw in all_gw.items():
            try:
                if not _to_bool(gw.get("ended", False), False):
                    self.bot.add_view(GiveawayJoinView(self, str(gid)))
            except Exception:
                continue

        if self._runner_task is None or self._runner_task.done():
            self._runner_task = asyncio.create_task(self._runner())

    async def cog_unload(self) -> None:
        if self._runner_task and not self._runner_task.done():
            self._runner_task.cancel()

    @app_commands.command(name="start", description="Start a giveaway.")
    @app_commands.describe(duration="Example: 10m, 2h, 1d", prize="Prize text", winners="Number of winners", channel="Channel to post in")
    async def start(self, interaction: discord.Interaction, duration: str, prize: str, winners: int | None = None, channel: discord.TextChannel | None = None):
        if not self._enabled():
            await interaction.response.send_message("Giveaways are disabled.", ephemeral=True)
            return
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command is only available in a server.", ephemeral=True)
            return
        if not self._is_allowed(interaction.user):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        secs = _parse_duration_to_seconds(duration)
        if secs <= 0:
            await interaction.response.send_message("Invalid duration. Use like: 10m, 2h, 1d.", ephemeral=True)
            return
        if secs < 10:
            await interaction.response.send_message("Duration must be at least 10 seconds.", ephemeral=True)
            return

        prize = (prize or "").strip()
        if not prize:
            await interaction.response.send_message("Prize cannot be empty.", ephemeral=True)
            return
        prize = prize[: self._max_prize_len()]

        w = winners if winners is not None else self._default_winners()
        w = max(1, min(self._max_winners(), int(w)))

        ch = channel or interaction.channel
        if not isinstance(ch, discord.TextChannel):
            await interaction.response.send_message("Invalid channel.", ephemeral=True)
            return

        end_ts = int(time.time()) + int(secs)
        embed = self._make_embed(prize, w, interaction.user.id, end_ts, 0, False, None)

        await interaction.response.send_message("Giveaway created.", ephemeral=True)
        msg = await ch.send(embed=embed, view=GiveawayJoinView(self, "pending"))

        giveaway_id = str(msg.id)
        await msg.edit(view=GiveawayJoinView(self, giveaway_id))
        self.bot.add_view(GiveawayJoinView(self, giveaway_id))

        gw: dict[str, Any] = {
            "guild_id": interaction.guild.id,
            "channel_id": ch.id,
            "message_id": msg.id,
            "prize": prize,
            "winners": w,
            "host_id": interaction.user.id,
            "end_ts": end_ts,
            "entries": [],
            "ended": False,
            "winner_ids": []
        }
        await self.storage.set(giveaway_id, gw)

    @app_commands.command(name="end", description="End a giveaway early.")
    @app_commands.describe(message_id="Giveaway message ID")
    async def end(self, interaction: discord.Interaction, message_id: str):
        if not self._enabled():
            await interaction.response.send_message("Giveaways are disabled.", ephemeral=True)
            return
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command is only available in a server.", ephemeral=True)
            return
        if not self._is_allowed(interaction.user):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        gid = str(_to_int(message_id, 0))
        if gid == "0":
            await interaction.response.send_message("Invalid message ID.", ephemeral=True)
            return

        gw = await self.storage.get(gid)
        if not gw or _to_int(gw.get("guild_id", 0), 0) != interaction.guild.id:
            await interaction.response.send_message("Giveaway not found.", ephemeral=True)
            return

        await interaction.response.send_message("Ending giveaway...", ephemeral=True)
        await self._end_giveaway(gid, force=True)

    @app_commands.command(name="reroll", description="Reroll winners for an ended giveaway.")
    @app_commands.describe(message_id="Giveaway message ID", winners="New number of winners")
    async def reroll(self, interaction: discord.Interaction, message_id: str, winners: int | None = None):
        if not self._enabled():
            await interaction.response.send_message("Giveaways are disabled.", ephemeral=True)
            return
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command is only available in a server.", ephemeral=True)
            return
        if not self._is_allowed(interaction.user):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        gid = str(_to_int(message_id, 0))
        if gid == "0":
            await interaction.response.send_message("Invalid message ID.", ephemeral=True)
            return

        gw = await self.storage.get(gid)
        if not gw or _to_int(gw.get("guild_id", 0), 0) != interaction.guild.id:
            await interaction.response.send_message("Giveaway not found.", ephemeral=True)
            return
        if not _to_bool(gw.get("ended", False), False):
            await interaction.response.send_message("This giveaway has not ended yet.", ephemeral=True)
            return

        entries = gw.get("entries", [])
        if not isinstance(entries, list):
            entries = []

        w = winners if winners is not None else _to_int(gw.get("winners", self._default_winners()), self._default_winners())
        w = max(1, min(self._max_winners(), int(w)))

        winner_ids = self._pick_winners([_to_int(x, 0) for x in entries], w)
        gw["winner_ids"] = winner_ids
        await self.storage.set(gid, gw)

        prize = str(gw.get("prize", "Unknown"))[: self._max_prize_len()]
        host_id = _to_int(gw.get("host_id", 0), 0)
        end_ts = _to_int(gw.get("end_ts", 0), 0)
        embed = self._make_embed(prize, w, host_id, end_ts, len(entries), True, winner_ids)

        await interaction.response.send_message("Rerolled.", ephemeral=True)
        try:
            await self._edit_message(
                interaction.guild.id,
                _to_int(gw.get("channel_id", 0), 0),
                _to_int(gw.get("message_id", 0), 0),
                embed,
                True,
                gid,
            )
        except Exception:
            pass
