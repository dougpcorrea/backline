"""Ponto de entrada do bot do Backline."""
from __future__ import annotations

import logging

import aiohttp
import discord
from discord.ext import commands

from .collectors import Collectors
from .config import config
from .db import Database

log = logging.getLogger("backline")

EXTENSIONS = [
    "backline.cogs.server_setup",
    "backline.cogs.onboarding",
    "backline.cogs.feed",
    "backline.cogs.mutual",
    "backline.cogs.discover",
    "backline.cogs.ratings",
    "backline.cogs.community",
    "backline.cogs.demo",
    "backline.cogs.oneoff",
    "backline.cogs.participation",
    "backline.cogs.recruitment",
    "backline.cogs.admin",
]


class Backline(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True   # precisa estar ligado no Developer Portal
        intents.invites = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.cfg = config
        self.db = Database(config.database_url)
        self.session: aiohttp.ClientSession | None = None
        self.collectors: Collectors | None = None

    async def setup_hook(self) -> None:
        await self.db.connect()
        self.session = aiohttp.ClientSession(headers={"User-Agent": "BacklineBot/1.0"})
        self.collectors = Collectors(self.cfg, self.session)
        for ext in EXTENSIONS:
            await self.load_extension(ext)
        if self.cfg.guild_id:
            guild = discord.Object(id=self.cfg.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def close(self) -> None:
        if self.session:
            await self.session.close()
        await self.db.close()
        await super().close()

    async def on_ready(self) -> None:
        log.info("Conectado como %s (%s)", self.user, self.user and self.user.id)

    # ---------- atalhos usados pelos módulos ----------
    @property
    def guild(self) -> discord.Guild | None:
        return self.get_guild(self.cfg.guild_id) if self.cfg.guild_id else (self.guilds[0] if self.guilds else None)

    async def channel(self, key: str) -> discord.TextChannel | None:
        cid = await self.db.get_int_setting(f"ch_{key}")
        ch = self.get_channel(cid) if cid else None
        return ch if isinstance(ch, discord.TextChannel) else None

    async def channel_mention(self, key: str) -> str:
        ch = await self.channel(key)
        return ch.mention if ch else f"#{key}"

    async def role(self, key: str) -> discord.Role | None:
        guild = self.guild
        rid = await self.db.get_int_setting(f"role_{key}")
        return guild.get_role(rid) if guild and rid else None

    async def is_paused(self) -> bool:
        return (await self.db.get_setting("paused", "0")) == "1"

    async def log_admin(self, text: str) -> None:
        log.info(text)
        ch = await self.channel("admin")
        if ch:
            try:
                await ch.send(text[:1900])
            except discord.HTTPException:
                pass


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not config.token:
        raise SystemExit("Defina DISCORD_TOKEN no arquivo .env")
    Backline().run(config.token, log_handler=None)


if __name__ == "__main__":
    main()
