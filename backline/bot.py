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
        self._install_error_logging()
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

    def _install_error_logging(self) -> None:
        """Erros em botões, formulários e comandos vão para o canal admin (e o membro recebe uma resposta)."""
        bot = self

        async def view_error(_self, interaction, error, _item=None):
            logging.exception("interaction error", exc_info=error)
            await bot.log_admin(_error_text(interaction, error))
            await _reply_failed(interaction)

        for cls in {getattr(discord.ui.view, "BaseView", discord.ui.View), discord.ui.View, discord.ui.Modal}:
            cls.on_error = view_error
        if hasattr(discord.ui, "LayoutView"):
            discord.ui.LayoutView.on_error = view_error

        @self.tree.error
        async def on_app_command_error(interaction, error):
            logging.exception("command error", exc_info=error)
            await bot.log_admin(_error_text(interaction, error))
            await _reply_failed(interaction)

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


def _error_text(interaction, error) -> str:
    where = (interaction.data or {}).get("custom_id") or getattr(interaction.command, "qualified_name", "?")
    err = getattr(error, "original", error)
    return f"⚠️ Erro em `{where}` para {interaction.user.mention}: `{type(err).__name__}: {str(err)[:300]}`"


async def _reply_failed(interaction) -> None:
    msg = "😕 Something went wrong on our side — please try again in a moment."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:  # noqa: BLE001
        pass
