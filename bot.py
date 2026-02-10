import discord
from discord.ext import commands

from config import Config
from logger import setup_logger


def build_intents(cfg: dict) -> discord.Intents:
    intents = discord.Intents.none()
    if cfg.get("guilds", True):
        intents.guilds = True
    if cfg.get("members", False):
        intents.members = True
    if cfg.get("messages", True):
        intents.messages = True
    if cfg.get("message_content", False):
        intents.message_content = True
    intents.guild_reactions = True
    return intents


def build_activity(bot_cfg: dict):
    text = bot_cfg.get("status_text", "")
    if not text:
        return None
    t = str(bot_cfg.get("activity_type", "playing")).lower()
    if t == "listening":
        return discord.Activity(type=discord.ActivityType.listening, name=text)
    if t == "watching":
        return discord.Activity(type=discord.ActivityType.watching, name=text)
    if t == "competing":
        return discord.Activity(type=discord.ActivityType.competing, name=text)
    return discord.Game(name=text)


class MyBot(commands.Bot):
    def __init__(self, cfg: Config, logger):
        self.cfg = cfg
        self.log = logger
        intents = build_intents(cfg.section("intents"))
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def setup_hook(self) -> None:
        for ext in self.cfg.get("cogs", []):
            try:
                await self.load_extension(ext)
                self.log.info(f"feature_loaded | {ext}")
            except Exception as e:
                self.log.exception(f"feature_load_failed | {ext} | {e}")

        guild_id = int(self.cfg.get("guild_id", 0) or 0)
        if guild_id:
            guild = discord.Object(id=guild_id)
            try:
                synced = await self.tree.sync(guild=guild)
                self.log.info(f"slash_sync | scope=guild | guild_id={guild_id} | count={len(synced)}")
            except discord.Forbidden:
                synced = await self.tree.sync()
                self.log.info(f"slash_sync | scope=global | reason=missing_access | count={len(synced)}")
                return

            try:
                cmds = await self.tree.fetch_commands(guild=guild)
                names = []
                for c in cmds:
                    if getattr(c, "parent", None):
                        names.append(f"{c.parent.name}.{c.name}")
                    else:
                        names.append(c.name)
                names.sort()
                self.log.info("slash_commands_guild | " + ", ".join(names))
            except Exception as e:
                self.log.exception(f"slash_fetch_failed | {e}")
        else:
            synced = await self.tree.sync()
            self.log.info(f"slash_sync | scope=global | count={len(synced)}")

    async def on_ready(self):
        activity = build_activity(self.cfg.section("bot"))
        if activity:
            await self.change_presence(activity=activity, status=discord.Status.online)
        self.log.info(f"ready | user={self.user} | id={self.user.id}")

    async def on_command_error(self, ctx: commands.Context, error: Exception):
        self.log.exception(f"command_error | cmd={getattr(ctx.command,'name',None)} | {error}")


def main():
    cfg = Config()
    logger = setup_logger(cfg.section("logging"))
    bot = MyBot(cfg, logger)
    bot.run(cfg.get("token"), log_handler=None)


if __name__ == "__main__":
    main()
