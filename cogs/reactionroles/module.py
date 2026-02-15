import json
import os
import re
import discord
from discord import app_commands
from typing import Any

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


def _cfg(bot) -> dict:
    v = getattr(bot, "cfg", {}).get("reaction_roles", {})
    return v if isinstance(v, dict) else {}


def _msg_cfg(bot) -> dict:
    v = getattr(bot, "_rr_messages", None)
    return v if isinstance(v, dict) else {}


def _msg(bot, key: str, default: str) -> str:
    root = _msg_cfg(bot).get("responses", {})
    if isinstance(root, dict):
        v = root.get(key, default)
        return str(v) if v is not None else default
    return default


def _enabled(bot) -> bool:
    return _to_bool(_cfg(bot).get("enabled", True), True)


def _guild_only(bot) -> bool:
    return _to_bool(_cfg(bot).get("guild_only", True), True)


def _max_buttons(bot) -> int:
    return max(1, min(25, _to_int(_cfg(bot).get("max_buttons", 25), 25)))


def _max_select_options(bot) -> int:
    return max(1, min(25, _to_int(_cfg(bot).get("max_select_options", 25), 25)))


def _select_max_values(bot) -> int:
    return max(1, min(25, _to_int(_cfg(bot).get("select_max_values", 1), 1)))


def _remove_unselected(bot) -> bool:
    return _to_bool(_cfg(bot).get("remove_unselected_on_select", True), True)


def _exclusive_groups_enabled(bot) -> bool:
    v = _cfg(bot).get("exclusive_groups", {})
    if isinstance(v, dict):
        return _to_bool(v.get("enabled", True), True)
    return True


def _default_toggle_mode(bot, panel_type: str) -> bool:
    v = _cfg(bot).get("default_panel_toggle_mode", {})
    if not isinstance(v, dict):
        return panel_type == "buttons"
    if panel_type == "select":
        return _to_bool(v.get("select", False), False)
    return _to_bool(v.get("buttons", True), True)


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


def _is_allowed(bot, member: discord.Member) -> bool:
    role_ids = _admin_role_ids(bot)
    if role_ids and any(r.id in role_ids for r in member.roles):
        return True
    if _require_admin(bot) and member.guild_permissions.administrator:
        return True
    if _require_manage_guild(bot) and member.guild_permissions.manage_guild:
        return True
    return False


def _style_from_str(s: str) -> discord.ButtonStyle:
    x = (s or "").strip().lower()
    if x == "primary":
        return discord.ButtonStyle.primary
    if x == "secondary":
        return discord.ButtonStyle.secondary
    if x == "success":
        return discord.ButtonStyle.success
    if x == "danger":
        return discord.ButtonStyle.danger
    return discord.ButtonStyle.secondary


def _mode_from_str(s: str) -> str:
    x = (s or "").strip().lower()
    if x in ("add", "add_only"):
        return "add"
    if x in ("remove", "remove_only"):
        return "remove"
    return "toggle"


def _parse_color_raw(color_raw: str | None) -> discord.Color | None:
    s = str(color_raw or "").strip()
    if not s:
        return None
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", s)
    if m:
        return discord.Color(int(m.group(1), 16))
    try:
        v = int(s)
        v = max(0, min(0xFFFFFF, v))
        return discord.Color(v)
    except Exception:
        return None


async def _safe_add_role(member: discord.Member, role: discord.Role) -> bool:
    try:
        await member.add_roles(role, reason="Reaction roles")
        return True
    except Exception:
        return False


async def _safe_remove_role(member: discord.Member, role: discord.Role) -> bool:
    try:
        await member.remove_roles(role, reason="Reaction roles")
        return True
    except Exception:
        return False


def _find_item(panel: dict[str, Any], role_id: int) -> dict[str, Any] | None:
    items = panel.get("items", [])
    if not isinstance(items, list):
        return None
    for it in items:
        if isinstance(it, dict) and _to_int(it.get("role_id", 0), 0) == role_id:
            return it
    return None


def _group_of_item(it: dict[str, Any] | None) -> str:
    if not it:
        return ""
    g = it.get("group", "")
    if not isinstance(g, str):
        return ""
    return g.strip().lower()


async def _remove_other_group_roles(bot, guild: discord.Guild, member: discord.Member, panel: dict[str, Any], group: str, keep_role_id: int):
    if not group:
        return False
    if not _exclusive_groups_enabled(bot):
        return False
    items = panel.get("items", [])
    if not isinstance(items, list):
        return False
    changed = False
    for it in items:
        if not isinstance(it, dict):
            continue
        rid = _to_int(it.get("role_id", 0), 0)
        if rid <= 0 or rid == keep_role_id:
            continue
        if _group_of_item(it) != group:
            continue
        role = guild.get_role(rid)
        if role and role in member.roles:
            ok = await _safe_remove_role(member, role)
            changed = changed or ok
    return changed


class RRButtonsView(discord.ui.View):
    def __init__(self, bot: discord.Client, panel_id: str, items: list[dict[str, Any]]):
        super().__init__(timeout=None)
        self.bot = bot
        self.panel_id = panel_id
        self.items = items
        self._build()

    def _build(self):
        for it in self.items[:25]:
            role_id = _to_int(it.get("role_id", 0), 0)
            if role_id <= 0:
                continue
            label = str(it.get("label", "") or "")[:80]
            style = _style_from_str(str(it.get("style", "secondary")))
            emoji = it.get("emoji", None)
            cid = f"rrb:{self.panel_id}:{role_id}"
            b = discord.ui.Button(label=label or None, style=style, custom_id=cid, emoji=emoji)
            b.callback = self._make_cb(role_id)
            self.add_item(b)

    def _make_cb(self, role_id: int):
        async def cb(interaction: discord.Interaction):
            await handle_button(interaction, self.panel_id, role_id)
        return cb


class RRSelect(discord.ui.Select):
    def __init__(self, bot: discord.Client, panel_id: str, items: list[dict[str, Any]], placeholder: str):
        self.bot = bot
        self.panel_id = panel_id
        options: list[discord.SelectOption] = []
        for it in items[:25]:
            role_id = _to_int(it.get("role_id", 0), 0)
            if role_id <= 0:
                continue
            label = str(it.get("label", "") or "")[:100]
            desc = str(it.get("description", "") or "")[:100]
            emoji = it.get("emoji", None)
            options.append(discord.SelectOption(label=label or f"Role {role_id}", value=str(role_id), description=desc or None, emoji=emoji))
        super().__init__(
            custom_id=f"rrs:{panel_id}",
            placeholder=(placeholder or "Select roles")[:100],
            min_values=0,
            max_values=_select_max_values(bot),
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        await handle_select(interaction, self.panel_id, list(self.values))


class RRSelectView(discord.ui.View):
    def __init__(self, bot: discord.Client, panel_id: str, items: list[dict[str, Any]], placeholder: str):
        super().__init__(timeout=None)
        self.add_item(RRSelect(bot, panel_id, items, placeholder))


async def handle_button(interaction: discord.Interaction, panel_id: str, role_id: int):
    bot = interaction.client
    if not _enabled(bot):
        return
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(_msg(bot, "server_only", "This command can only be used in a server."), ephemeral=True)
        return

    storage: JsonStorage = bot._rr_storage
    panel = await storage.get(panel_id)
    if not panel or _to_int(panel.get("guild_id", 0), 0) != interaction.guild.id:
        await interaction.response.send_message(_msg(bot, "panel_missing", "This panel no longer exists."), ephemeral=True)
        return

    role = interaction.guild.get_role(role_id)
    if role is None:
        await interaction.response.send_message(_msg(bot, "role_not_found", "Role not found."), ephemeral=True)
        return

    member = interaction.user
    it = _find_item(panel, role_id)
    mode = _mode_from_str(str((it or {}).get("mode", "toggle")))
    group = _group_of_item(it)

    if mode == "add":
        if role not in member.roles:
            await _remove_other_group_roles(bot, interaction.guild, member, panel, group, role_id)
            ok = await _safe_add_role(member, role)
            if ok:
                await interaction.response.send_message(_msg(bot, "role_added", "Role added: {role}").replace("{role}", role.name), ephemeral=True)
            else:
                await interaction.response.send_message(_msg(bot, "cant_add_role", "I can't add that role."), ephemeral=True)
        else:
            await interaction.response.send_message(_msg(bot, "no_changes", "No changes."), ephemeral=True)
        return

    if mode == "remove":
        if role in member.roles:
            ok = await _safe_remove_role(member, role)
            if ok:
                await interaction.response.send_message(_msg(bot, "role_removed", "Role removed: {role}").replace("{role}", role.name), ephemeral=True)
            else:
                await interaction.response.send_message(_msg(bot, "cant_remove_role", "I can't remove that role."), ephemeral=True)
        else:
            await interaction.response.send_message(_msg(bot, "no_changes", "No changes."), ephemeral=True)
        return

    if role in member.roles:
        ok = await _safe_remove_role(member, role)
        if ok:
            await interaction.response.send_message(_msg(bot, "role_removed", "Role removed: {role}").replace("{role}", role.name), ephemeral=True)
        else:
            await interaction.response.send_message(_msg(bot, "cant_remove_role", "I can't remove that role."), ephemeral=True)
    else:
        await _remove_other_group_roles(bot, interaction.guild, member, panel, group, role_id)
        ok = await _safe_add_role(member, role)
        if ok:
            await interaction.response.send_message(_msg(bot, "role_added", "Role added: {role}").replace("{role}", role.name), ephemeral=True)
        else:
            await interaction.response.send_message(_msg(bot, "cant_add_role", "I can't add that role."), ephemeral=True)


async def handle_select(interaction: discord.Interaction, panel_id: str, values: list[str]):
    bot = interaction.client
    if not _enabled(bot):
        return
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(_msg(bot, "server_only", "This command can only be used in a server."), ephemeral=True)
        return

    storage: JsonStorage = bot._rr_storage
    panel = await storage.get(panel_id)
    if not panel or _to_int(panel.get("guild_id", 0), 0) != interaction.guild.id:
        await interaction.response.send_message(_msg(bot, "panel_missing", "This panel no longer exists."), ephemeral=True)
        return

    items = panel.get("items", [])
    if not isinstance(items, list):
        items = []

    panel_toggle_mode = _to_bool(panel.get("toggle_mode", _default_toggle_mode(bot, "select")), _default_toggle_mode(bot, "select"))

    valid_items: dict[int, dict[str, Any]] = {}
    for it in items[:25]:
        if not isinstance(it, dict):
            continue
        rid = _to_int(it.get("role_id", 0), 0)
        if rid > 0:
            valid_items[rid] = it

    selected_ids_raw: list[int] = []
    for v in values:
        rid = _to_int(v, 0)
        if rid in valid_items:
            selected_ids_raw.append(rid)

    group_pick: dict[str, int] = {}
    selected_ids: list[int] = []
    for rid in selected_ids_raw:
        g = _group_of_item(valid_items.get(rid))
        if g and _exclusive_groups_enabled(bot):
            group_pick[g] = rid
        else:
            selected_ids.append(rid)

    for g, rid in group_pick.items():
        selected_ids.append(rid)

    selected_set = set(selected_ids)
    member = interaction.user
    changed = False

    if _exclusive_groups_enabled(bot):
        for rid in selected_set:
            it = valid_items.get(rid)
            g = _group_of_item(it)
            if g:
                changed = await _remove_other_group_roles(bot, interaction.guild, member, panel, g, rid) or changed

    if _remove_unselected(bot) and not panel_toggle_mode:
        for rid, it in valid_items.items():
            g = _group_of_item(it)
            if g and _exclusive_groups_enabled(bot):
                continue
            role = interaction.guild.get_role(rid)
            if role and role in member.roles and rid not in selected_set:
                ok = await _safe_remove_role(member, role)
                changed = changed or ok

    for rid in selected_ids:
        it = valid_items.get(rid)
        mode = _mode_from_str(str((it or {}).get("mode", "toggle")))
        role = interaction.guild.get_role(rid)
        if role is None:
            continue

        if mode == "add":
            if role not in member.roles:
                ok = await _safe_add_role(member, role)
                changed = changed or ok
            continue

        if mode == "remove":
            if role in member.roles:
                ok = await _safe_remove_role(member, role)
                changed = changed or ok
            continue

        if panel_toggle_mode:
            if role in member.roles:
                ok = await _safe_remove_role(member, role)
                changed = changed or ok
            else:
                ok = await _safe_add_role(member, role)
                changed = changed or ok
        else:
            if role not in member.roles:
                ok = await _safe_add_role(member, role)
                changed = changed or ok

    await interaction.response.send_message(_msg(bot, "updated", "Updated.") if changed else _msg(bot, "no_changes", "No changes."), ephemeral=True)


async def _fetch_channel(guild: discord.Guild, channel_id: int):
    ch = guild.get_channel(channel_id)
    if ch is not None:
        return ch
    try:
        return await guild.fetch_channel(channel_id)
    except Exception:
        return None


async def _fetch_message(channel: discord.abc.Messageable, message_id: int):
    try:
        return await channel.fetch_message(message_id)
    except Exception:
        return None


async def _render_panel(bot: discord.Client, panel_id: str):
    storage: JsonStorage = bot._rr_storage
    panel = await storage.get(panel_id)
    if not panel:
        return

    guild_id = _to_int(panel.get("guild_id", 0), 0)
    channel_id = _to_int(panel.get("channel_id", 0), 0)
    message_id = _to_int(panel.get("message_id", 0), 0)
    ptype = str(panel.get("type", "buttons")).lower()
    title = str(panel.get("title", "Reaction Roles"))
    desc = str(panel.get("description", ""))
    items = panel.get("items", [])
    if not isinstance(items, list):
        items = []
    placeholder = str(panel.get("placeholder", "Select roles"))
    color_raw = str(panel.get("color", "") or "").strip()
    c = _parse_color_raw(color_raw)

    guild = bot.get_guild(guild_id)
    if guild is None:
        try:
            guild = await bot.fetch_guild(guild_id)
        except Exception:
            return

    ch = await _fetch_channel(guild, channel_id)
    if ch is None or not hasattr(ch, "fetch_message"):
        return

    msg = await _fetch_message(ch, message_id)
    if msg is None:
        return

    embed = discord.Embed(title=title or "Reaction Roles", description=desc or "", color=c)
    if ptype == "buttons":
        view = RRButtonsView(bot, panel_id, items)
    else:
        view = RRSelectView(bot, panel_id, items, placeholder)
    try:
        await msg.edit(embed=embed, view=view)
    except Exception:
        return


rr = app_commands.Group(name="rr", description="Reaction roles manager.")


@rr.command(name="create_buttons", description="Create a reaction role panel with buttons.")
@app_commands.describe(channel="Channel to post in", title="Embed title", description="Embed description", toggle_mode="If true: clicking again removes the role", color="Embed color (#RRGGBB or int)")
async def rr_create_buttons(interaction: discord.Interaction, channel: discord.TextChannel, title: str = "Reaction Roles", description: str = "", toggle_mode: bool | None = None, color: str | None = None):
    bot = interaction.client
    if not _enabled(bot):
        return
    if _guild_only(bot) and interaction.guild is None:
        await interaction.response.send_message(_msg(bot, "server_only", "This command can only be used in a server."), ephemeral=True)
        return
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(_msg(bot, "server_only", "This command can only be used in a server."), ephemeral=True)
        return
    if not _is_allowed(bot, interaction.user):
        await interaction.response.send_message(_msg(bot, "no_permission", "You do not have permission to use this command."), ephemeral=True)
        return

    tm = _default_toggle_mode(bot, "buttons") if toggle_mode is None else bool(toggle_mode)
    c = _parse_color_raw(color)

    embed = discord.Embed(title=title or "Reaction Roles", description=description or "", color=c)
    await interaction.response.send_message(_msg(bot, "creating_panel", "Creating panel..."), ephemeral=True)
    msg = await channel.send(embed=embed)

    panel_id = str(msg.id)
    panel = {
        "guild_id": interaction.guild.id,
        "channel_id": channel.id,
        "message_id": msg.id,
        "type": "buttons",
        "toggle_mode": tm,
        "title": title,
        "description": description,
        "color": str(color or ""),
        "items": []
    }

    storage: JsonStorage = bot._rr_storage
    await storage.set(panel_id, panel)

    view = RRButtonsView(bot, panel_id, [])
    await msg.edit(view=view)
    bot.add_view(view)
    await interaction.followup.send(_msg(bot, "panel_created", "Panel created. ID: `{panel_id}`").replace("{panel_id}", panel_id), ephemeral=True)


@rr.command(name="create_select", description="Create a reaction role panel with a dropdown.")
@app_commands.describe(channel="Channel to post in", title="Embed title", description="Embed description", placeholder="Dropdown placeholder", toggle_mode="If true: selecting a role you already have removes it", color="Embed color (#RRGGBB or int)")
async def rr_create_select(interaction: discord.Interaction, channel: discord.TextChannel, title: str = "Reaction Roles", description: str = "", placeholder: str = "Select roles", toggle_mode: bool | None = None, color: str | None = None):
    bot = interaction.client
    if not _enabled(bot):
        return
    if _guild_only(bot) and interaction.guild is None:
        await interaction.response.send_message(_msg(bot, "server_only", "This command can only be used in a server."), ephemeral=True)
        return
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(_msg(bot, "server_only", "This command can only be used in a server."), ephemeral=True)
        return
    if not _is_allowed(bot, interaction.user):
        await interaction.response.send_message(_msg(bot, "no_permission", "You do not have permission to use this command."), ephemeral=True)
        return

    tm = _default_toggle_mode(bot, "select") if toggle_mode is None else bool(toggle_mode)
    c = _parse_color_raw(color)

    embed = discord.Embed(title=title or "Reaction Roles", description=description or "", color=c)
    await interaction.response.send_message(_msg(bot, "creating_panel", "Creating panel..."), ephemeral=True)
    msg = await channel.send(embed=embed)

    panel_id = str(msg.id)
    panel = {
        "guild_id": interaction.guild.id,
        "channel_id": channel.id,
        "message_id": msg.id,
        "type": "select",
        "toggle_mode": tm,
        "placeholder": placeholder[:100],
        "title": title,
        "description": description,
        "color": str(color or ""),
        "items": []
    }

    storage: JsonStorage = bot._rr_storage
    await storage.set(panel_id, panel)

    view = RRSelectView(bot, panel_id, [], placeholder)
    await msg.edit(view=view)
    bot.add_view(view)
    await interaction.followup.send(_msg(bot, "panel_created", "Panel created. ID: `{panel_id}`").replace("{panel_id}", panel_id), ephemeral=True)


@rr.command(name="add", description="Add a role to a panel (button or dropdown).")
@app_commands.describe(panel_id="Panel ID (message id)", role="Role", label="Button/option label", style="Button style: primary/secondary/success/danger", emoji="Emoji", group="Exclusive group name (optional)", mode="toggle/add/remove")
async def rr_add(interaction: discord.Interaction, panel_id: str, role: discord.Role, label: str, style: str = "secondary", emoji: str | None = None, group: str | None = None, mode: str = "toggle"):
    bot = interaction.client
    if not _enabled(bot):
        return
    if _guild_only(bot) and interaction.guild is None:
        await interaction.response.send_message(_msg(bot, "server_only", "This command can only be used in a server."), ephemeral=True)
        return
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(_msg(bot, "server_only", "This command can only be used in a server."), ephemeral=True)
        return
    if not _is_allowed(bot, interaction.user):
        await interaction.response.send_message(_msg(bot, "no_permission", "You do not have permission to use this command."), ephemeral=True)
        return

    pid = str(_to_int(panel_id, 0))
    if pid == "0":
        await interaction.response.send_message(_msg(bot, "invalid_panel_id", "Invalid panel ID."), ephemeral=True)
        return

    storage: JsonStorage = bot._rr_storage
    panel = await storage.get(pid)
    if not panel or _to_int(panel.get("guild_id", 0), 0) != interaction.guild.id:
        await interaction.response.send_message(_msg(bot, "panel_not_found", "Panel not found."), ephemeral=True)
        return

    items = panel.get("items", [])
    if not isinstance(items, list):
        items = []

    ptype = str(panel.get("type", "buttons")).lower()
    limit = _max_buttons(bot) if ptype == "buttons" else _max_select_options(bot)
    if len(items) >= limit:
        await interaction.response.send_message(_msg(bot, "panel_full", "Panel is full."), ephemeral=True)
        return

    rid = role.id
    for it in items:
        if isinstance(it, dict) and _to_int(it.get("role_id", 0), 0) == rid:
            await interaction.response.send_message(_msg(bot, "role_already_in_panel", "That role is already in the panel."), ephemeral=True)
            return

    item = {
        "role_id": rid,
        "label": label[:100],
        "style": style[:20],
        "emoji": emoji,
        "group": (group or "").strip()[:32],
        "mode": _mode_from_str(mode)
    }

    items.append(item)
    panel["items"] = items
    await storage.set(pid, panel)

    await interaction.response.send_message(_msg(bot, "added_updating", "Added. Updating panel..."), ephemeral=True)
    await _render_panel(bot, pid)


@rr.command(name="remove", description="Remove a role from a panel.")
@app_commands.describe(panel_id="Panel ID (message id)", role="Role")
async def rr_remove(interaction: discord.Interaction, panel_id: str, role: discord.Role):
    bot = interaction.client
    if not _enabled(bot):
        return
    if _guild_only(bot) and interaction.guild is None:
        await interaction.response.send_message(_msg(bot, "server_only", "This command can only be used in a server."), ephemeral=True)
        return
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(_msg(bot, "server_only", "This command can only be used in a server."), ephemeral=True)
        return
    if not _is_allowed(bot, interaction.user):
        await interaction.response.send_message(_msg(bot, "no_permission", "You do not have permission to use this command."), ephemeral=True)
        return

    pid = str(_to_int(panel_id, 0))
    if pid == "0":
        await interaction.response.send_message(_msg(bot, "invalid_panel_id", "Invalid panel ID."), ephemeral=True)
        return

    storage: JsonStorage = bot._rr_storage
    panel = await storage.get(pid)
    if not panel or _to_int(panel.get("guild_id", 0), 0) != interaction.guild.id:
        await interaction.response.send_message(_msg(bot, "panel_not_found", "Panel not found."), ephemeral=True)
        return

    items = panel.get("items", [])
    if not isinstance(items, list):
        items = []

    rid = role.id
    new_items = [it for it in items if not (isinstance(it, dict) and _to_int(it.get("role_id", 0), 0) == rid)]
    if len(new_items) == len(items):
        await interaction.response.send_message(_msg(bot, "role_not_in_panel", "That role is not in the panel."), ephemeral=True)
        return

    panel["items"] = new_items
    await storage.set(pid, panel)

    await interaction.response.send_message(_msg(bot, "removed_updating", "Removed. Updating panel..."), ephemeral=True)
    await _render_panel(bot, pid)


@rr.command(name="delete", description="Delete a panel from storage (optionally delete the message).")
@app_commands.describe(panel_id="Panel ID (message id)", delete_message="Also delete the panel message")
async def rr_delete(interaction: discord.Interaction, panel_id: str, delete_message: bool = False):
    bot = interaction.client
    if not _enabled(bot):
        return
    if _guild_only(bot) and interaction.guild is None:
        await interaction.response.send_message(_msg(bot, "server_only", "This command can only be used in a server."), ephemeral=True)
        return
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(_msg(bot, "server_only", "This command can only be used in a server."), ephemeral=True)
        return
    if not _is_allowed(bot, interaction.user):
        await interaction.response.send_message(_msg(bot, "no_permission", "You do not have permission to use this command."), ephemeral=True)
        return

    pid = str(_to_int(panel_id, 0))
    if pid == "0":
        await interaction.response.send_message(_msg(bot, "invalid_panel_id", "Invalid panel ID."), ephemeral=True)
        return

    storage: JsonStorage = bot._rr_storage
    panel = await storage.get(pid)
    if not panel or _to_int(panel.get("guild_id", 0), 0) != interaction.guild.id:
        await interaction.response.send_message(_msg(bot, "panel_not_found", "Panel not found."), ephemeral=True)
        return

    if delete_message:
        channel_id = _to_int(panel.get("channel_id", 0), 0)
        message_id = _to_int(panel.get("message_id", 0), 0)
        ch = interaction.guild.get_channel(channel_id)
        if ch is None:
            try:
                ch = await interaction.guild.fetch_channel(channel_id)
            except Exception:
                ch = None
        if ch and hasattr(ch, "fetch_message"):
            try:
                msg = await ch.fetch_message(message_id)
                await msg.delete()
            except Exception:
                pass

    await storage.delete(pid)
    await interaction.response.send_message(_msg(bot, "panel_deleted", "Deleted."), ephemeral=True)


def _load_messages() -> dict:
    base = os.path.dirname(__file__)
    path = os.path.join(base, "messages.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


async def setup(bot: discord.Client):
    bot._rr_messages = _load_messages()

    path = str(_cfg(bot).get("storage_path", "data/reaction_roles.json"))
    bot._rr_storage = JsonStorage(path, log=getattr(bot, "log", None))

    guild_id = _to_int(getattr(bot, "cfg", {}).get("guild_id", 0), 0)
    guild_obj = discord.Object(id=guild_id) if guild_id else None
    if guild_obj:
        bot.tree.add_command(rr, guild=guild_obj, override=True)
    else:
        bot.tree.add_command(rr, override=True)

    all_panels = await bot._rr_storage.all()
    for pid, panel in all_panels.items():
        ptype = str(panel.get("type", "buttons")).lower()
        items = panel.get("items", [])
        if not isinstance(items, list):
            items = []
        placeholder = str(panel.get("placeholder", "Select roles"))
        if ptype == "buttons":
            bot.add_view(RRButtonsView(bot, str(pid), items))
        else:
            bot.add_view(RRSelectView(bot, str(pid), items, placeholder))


async def teardown(bot: discord.Client):
    try:
        guild_id = _to_int(getattr(bot, "cfg", {}).get("guild_id", 0), 0)
        guild_obj = discord.Object(id=guild_id) if guild_id else None
        if guild_obj:
            bot.tree.remove_command("rr", guild=guild_obj)
        else:
            bot.tree.remove_command("rr")
    except Exception:
        pass
