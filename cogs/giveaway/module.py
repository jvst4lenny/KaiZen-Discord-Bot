import asyncio
import re
import secrets
import time
from typing import Any

import discord
from discord import app_commands

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


def _cfg(bot) -> dict:
    cfg = getattr(bot, "cfg", None)
    if cfg is None:
        return {}
    v = cfg.get("giveaway", {})
    return v if isinstance(v, dict) else {}


def _enabled(bot) -> bool:
    return _to_bool(_cfg(bot).get("enabled", True), True)


def _tick_seconds(bot) -> int:
    return max(3, _to_int(_cfg(bot).get("tick_seconds", 10), 10))


def _default_winners(bot) -> int:
    return max(1, _to_int(_cfg(bot).get("default_winners", 1), 1))


def _max_winners(bot) -> int:
    return max(1, _to_int(_cfg(bot).get("max_winners", 20), 20))


def _max_prize_len(bot) -> int:
    return max(20, _to_int(_cfg(bot).get("max_prize_length", 120), 120))


def _button_label(bot) -> str:
    return str(_cfg(bot).get("button_label", "Join Giveaway"))[:80]


def _perm_cfg(bot) -> dict:
    v = _cfg(bot).get("start_permissions", {})
    return v if isinstance(v, dict) else {}


def _admin_role_ids(bot) -> set[int]:
    v = _perm_cfg(bot).get("role_ids", [])
    if not isinstance(v, list):
        return set()
    out = set()
    for x in v:
        i = _to_int(x, 0)
        if i > 0:
            out.add(i)
    return out


def _require_admin(bot) -> bool:
    return _to_bool(_perm_cfg(bot).get("require_administrator", True), True)


def _require_manage_guild(bot) -> bool:
    return _to_bool(_perm_cfg(bot).get("require_manage_guild", False), False)


def _is_allowed_to_start(bot, member: discord.Member) -> bool:
    role_ids = _admin_role_ids(bot)
    if role_ids and any(r.id in role_ids for r in member.roles):
        return True
    if _require_admin(bot) and member.guild_permissions.administrator:
        return True
    if _require_manage_guild(bot) and member.guild_permissions.manage_guild:
        return True
    return False


def _join_req_cfg(bot) -> dict:
    v = _cfg(bot).get("join_requirements", {})
    return v if isinstance(v, dict) else {}


def _req_roles(bot) -> set[int]:
    v = _join_req_cfg(bot).get("required_role_ids", [])
    if not isinstance(v, list):
        return set()
    out = set()
    for x in v:
        i = _to_int(x, 0)
        if i > 0:
            out.add(i)
    return out


def _blacklist_roles(bot) -> set[int]:
    v = _join_req_cfg(bot).get("blacklist_role_ids", [])
    if not isinstance(v, list):
        return set()
    out = set()
    for x in v:
        i = _to_int(x, 0)
        if i > 0:
            out.add(i)
    return out


def _block_missing_required(bot) -> bool:
    return _to_bool(_join_req_cfg(bot).get("block_if_missing_required_roles", True), True)


def _block_blacklist(bot) -> bool:
    return _to_bool(_join_req_cfg(bot).get("block_if_has_blacklist_role", True), True)


def _join_error(bot) -> str:
    s = _join_req_cfg(bot).get("ephemeral_error_message", "You are not allowed to join this giveaway.")
    return str(s) if s is not None else "You are not allowed to join this giveaway."


def _reroll_cfg(bot) -> dict:
    v = _cfg(bot).get("reroll", {})
    return v if isinstance(v, dict) else {}


def _reroll_exclude_prev(bot) -> bool:
    return _to_bool(_reroll_cfg(bot).get("exclude_previous_winners", True), True)


def _can_join(bot, member: discord.Member) -> bool:
    req = _req_roles(bot)
    blk = _blacklist_roles(bot)

    if _block_blacklist(bot) and blk:
        if any(r.id in blk for r in member.roles):
            return False

    if _block_missing_required(bot) and req:
        if not any(r.id in req for r in member.roles):
            return False

    return True


def _make_embed(prize: str, winners: int, host_id: int, end_ts: int, entries: int, ended: bool, winner_ids: list[int] | None) -> discord.Embed:
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


def _pick_winners(entries: list[int], count: int) -> list[int]:
    unique = list(dict.fromkeys([int(x) for x in entries if int(x) > 0]))
    if not unique:
        return []
    secrets.SystemRandom().shuffle(unique)
    return unique[:count]


class GiveawayJoinView(discord.ui.View):
    def __init__(self, bot: discord.Client, giveaway_id: str):
        super().__init__(timeout=None)
        self.bot = bot
        self.giveaway_id = giveaway_id
        self._add_button()

    def _add_button(self):
        custom_id = f"giveaway_join:{self.giveaway_id}"
        b = discord.ui.Button(label=_button_label(self.bot), style=discord.ButtonStyle.success, custom_id=custom_id)
        b.callback = self._on_click
        self.add_item(b)

    async def _on_click(self, interaction: discord.Interaction):
        await handle_join(interaction, self.giveaway_id)


async def _edit_message(bot: discord.Client, guild_id: int, channel_id: int, message_id: int, embed: discord.Embed, ended: bool, giveaway_id: str):
    guild = bot.get_guild(guild_id)
    if guild is None:
        try:
            guild = await bot.fetch_guild(guild_id)
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
        view = GiveawayJoinView(bot, giveaway_id)
    await msg.edit(embed=embed, view=view)


async def _end_giveaway(bot: discord.Client, giveaway_id: str, force: bool = False) -> list[int]:
    storage: JsonStorage = bot._giveaway_storage
    gw = await storage.get(giveaway_id)
    if not gw:
        return []

    if _to_bool(gw.get("ended", False), False) and not force:
        w = gw.get("winner_ids", [])
        return w if isinstance(w, list) else []

    guild_id = _to_int(gw.get("guild_id", 0), 0)
    channel_id = _to_int(gw.get("channel_id", 0), 0)
    message_id = _to_int(gw.get("message_id", 0), 0)
    prize = str(gw.get("prize", "Unknown"))[: _max_prize_len(bot)]
    winners = max(1, min(_max_winners(bot), _to_int(gw.get("winners", 1), 1)))
    host_id = _to_int(gw.get("host_id", 0), 0)
    end_ts = _to_int(gw.get("end_ts", 0), 0)

    entries = gw.get("entries", [])
    if not isinstance(entries, list):
        entries = []

    winner_ids = _pick_winners([_to_int(x, 0) for x in entries], winners)

    gw["ended"] = True
    gw["winner_ids"] = winner_ids
    gw["ended_ts"] = int(time.time())
    await storage.set(giveaway_id, gw)

    embed = _make_embed(prize, winners, host_id, end_ts, len(entries), True, winner_ids)
    try:
        await _edit_message(bot, guild_id, channel_id, message_id, embed, True, giveaway_id)
    except Exception:
        pass

    return winner_ids


async def handle_join(interaction: discord.Interaction, giveaway_id: str):
    bot = interaction.client
    if not _enabled(bot):
        await interaction.response.send_message("Giveaways are disabled.", ephemeral=True)
        return
    if interaction.guild is None:
        await interaction.response.send_message("This is only available in a server.", ephemeral=True)
        return
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("This is only available in a server.", ephemeral=True)
        return

    if not _can_join(bot, interaction.user):
        await interaction.response.send_message(_join_error(bot), ephemeral=True)
        return

    storage: JsonStorage = bot._giveaway_storage
    gw = await storage.get(giveaway_id)
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
        await storage.set(giveaway_id, gw)
        await interaction.response.send_message("You left the giveaway.", ephemeral=True)
    else:
        entries.append(uid)
        gw["entries"] = entries
        await storage.set(giveaway_id, gw)
        await interaction.response.send_message("You joined the giveaway.", ephemeral=True)

    try:
        embed = _make_embed(
            str(gw.get("prize", "Unknown")),
            _to_int(gw.get("winners", 1), 1),
            _to_int(gw.get("host_id", 0), 0),
            _to_int(gw.get("end_ts", 0), 0),
            len(entries),
            False,
            None,
        )
        await _edit_message(
            bot,
            _to_int(gw.get("guild_id", 0), 0),
            _to_int(gw.get("channel_id", 0), 0),
            _to_int(gw.get("message_id", 0), 0),
            embed,
            False,
            giveaway_id,
        )
    except Exception:
        pass


async def _runner(bot: discord.Client):
    while True:
        await asyncio.sleep(_tick_seconds(bot))
        if not _enabled(bot):
            continue
        storage: JsonStorage = bot._giveaway_storage
        all_gw = await storage.all()
        now = int(time.time())
        for gid, gw in all_gw.items():
            try:
                if _to_bool(gw.get("ended", False), False):
                    continue
                end_ts = _to_int(gw.get("end_ts", 0), 0)
                if end_ts > 0 and now >= end_ts:
                    await _end_giveaway(bot, gid)
            except Exception:
                continue


giveaway_group = app_commands.Group(name="giveaway", description="Giveaway commands.")


@giveaway_group.command(name="start", description="Start a giveaway.")
@app_commands.describe(duration="Example: 10m, 2h, 1d", prize="Prize text", winners="Number of winners", channel="Channel to post in")
async def giveaway_start(interaction: discord.Interaction, duration: str, prize: str, winners: int | None = None, channel: discord.TextChannel | None = None):
    bot = interaction.client
    if not _enabled(bot):
        await interaction.response.send_message("Giveaways are disabled.", ephemeral=True)
        return
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("This command is only available in a server.", ephemeral=True)
        return
    if not _is_allowed_to_start(bot, interaction.user):
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
    prize = prize[: _max_prize_len(bot)]

    w = winners if winners is not None else _default_winners(bot)
    w = max(1, min(_max_winners(bot), int(w)))

    ch = channel or interaction.channel
    if not isinstance(ch, discord.TextChannel):
        await interaction.response.send_message("Invalid channel.", ephemeral=True)
        return

    end_ts = int(time.time()) + int(secs)
    embed = _make_embed(prize, w, interaction.user.id, end_ts, 0, False, None)

    await interaction.response.send_message("Giveaway created.", ephemeral=True)
    msg = await ch.send(embed=embed, view=GiveawayJoinView(bot, "pending"))

    giveaway_id = str(msg.id)
    await msg.edit(view=GiveawayJoinView(bot, giveaway_id))
    bot.add_view(GiveawayJoinView(bot, giveaway_id))

    storage: JsonStorage = bot._giveaway_storage
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
    await storage.set(giveaway_id, gw)


@giveaway_group.command(name="end", description="End a giveaway early.")
@app_commands.describe(message_id="Giveaway message ID")
async def giveaway_end(interaction: discord.Interaction, message_id: str):
    bot = interaction.client
    if not _enabled(bot):
        await interaction.response.send_message("Giveaways are disabled.", ephemeral=True)
        return
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("This command is only available in a server.", ephemeral=True)
        return
    if not _is_allowed_to_start(bot, interaction.user):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    gid = str(_to_int(message_id, 0))
    if gid == "0":
        await interaction.response.send_message("Invalid message ID.", ephemeral=True)
        return

    storage: JsonStorage = bot._giveaway_storage
    gw = await storage.get(gid)
    if not gw or _to_int(gw.get("guild_id", 0), 0) != interaction.guild.id:
        await interaction.response.send_message("Giveaway not found.", ephemeral=True)
        return

    await interaction.response.send_message("Ending giveaway...", ephemeral=True)
    await _end_giveaway(bot, gid, force=True)


@giveaway_group.command(name="reroll", description="Reroll winners for an ended giveaway.")
@app_commands.describe(message_id="Giveaway message ID", winners="New number of winners")
async def giveaway_reroll(interaction: discord.Interaction, message_id: str, winners: int | None = None):
    bot = interaction.client
    if not _enabled(bot):
        await interaction.response.send_message("Giveaways are disabled.", ephemeral=True)
        return
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("This command is only available in a server.", ephemeral=True)
        return
    if not _is_allowed_to_start(bot, interaction.user):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    gid = str(_to_int(message_id, 0))
    if gid == "0":
        await interaction.response.send_message("Invalid message ID.", ephemeral=True)
        return

    storage: JsonStorage = bot._giveaway_storage
    gw = await storage.get(gid)
    if not gw or _to_int(gw.get("guild_id", 0), 0) != interaction.guild.id:
        await interaction.response.send_message("Giveaway not found.", ephemeral=True)
        return
    if not _to_bool(gw.get("ended", False), False):
        await interaction.response.send_message("This giveaway has not ended yet.", ephemeral=True)
        return

    entries = gw.get("entries", [])
    if not isinstance(entries, list):
        entries = []

    prev = gw.get("winner_ids", [])
    prev_set = set([_to_int(x, 0) for x in prev]) if isinstance(prev, list) else set()

    filtered = []
    for x in entries:
        uid = _to_int(x, 0)
        if uid <= 0:
            continue
        if _reroll_exclude_prev(bot) and uid in prev_set:
            continue
        filtered.append(uid)

    w = winners if winners is not None else _to_int(gw.get("winners", _default_winners(bot)), _default_winners(bot))
    w = max(1, min(_max_winners(bot), int(w)))

    winner_ids = _pick_winners(filtered, w)
    gw["winner_ids"] = winner_ids
    await storage.set(gid, gw)

    await interaction.response.send_message("Rerolled.", ephemeral=True)


async def setup(bot: discord.Client):
    path = str(_cfg(bot).get("storage_path", "data/giveaways.json"))
    bot._giveaway_storage = JsonStorage(path, log=getattr(bot, "log", None))

    guild_id = int(getattr(bot, "cfg", {}).get("guild_id", 0) or 0)
    guild_obj = discord.Object(id=guild_id) if guild_id else None

    if guild_obj:
        bot.tree.add_command(giveaway_group, guild=guild_obj, override=True)
    else:
        bot.tree.add_command(giveaway_group, override=True)

    try:
        all_gw = await bot._giveaway_storage.all()
        for gid, gw in all_gw.items():
            if not _to_bool(gw.get("ended", False), False):
                bot.add_view(GiveawayJoinView(bot, str(gid)))
    except Exception:
        pass

    bot._giveaway_task = asyncio.create_task(_runner(bot))


async def teardown(bot: discord.Client):
    try:
        guild_id = int(getattr(bot, "cfg", {}).get("guild_id", 0) or 0)
        guild_obj = discord.Object(id=guild_id) if guild_id else None
        if guild_obj:
            bot.tree.remove_command("giveaway", guild=guild_obj)
        else:
            bot.tree.remove_command("giveaway")
    except Exception:
        pass

    t = getattr(bot, "_giveaway_task", None)
    if t and not t.done():
        t.cancel()
