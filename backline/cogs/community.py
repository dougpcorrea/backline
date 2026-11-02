"""#suggestions: sugestão anônima (vai pro canal admin sem autor) ou ticket privado com a equipe."""
from __future__ import annotations

import time

import discord
from discord.ext import commands

ANON_COOLDOWN = 600  # 1 sugestão anônima a cada 10 min por pessoa (só em memória, nada é gravado)


def suggestions_embed() -> discord.Embed:
    return discord.Embed(
        title="💡 Suggestions & Support",
        description=("Help us shape Backline.\n\n"
                     "🕶️ **Anonymous suggestion** — sent to the team without your name. We can't reply to it.\n"
                     "🎫 **Private ticket** — opens a private thread only you and the team can see. "
                     "Use it for questions, problems or anything you'd rather discuss directly."),
        colour=0xF5A623)


class SuggestionModal(discord.ui.Modal, title="Anonymous suggestion"):
    text = discord.ui.TextInput(label="Your suggestion", style=discord.TextStyle.paragraph,
                                min_length=10, max_length=1500)

    def __init__(self, bot):
        super().__init__(timeout=900)
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        admin = await self.bot.channel("admin")
        if admin:
            embed = discord.Embed(title="🕶️ Anonymous suggestion", description=self.text.value, colour=0xF5A623)
            await admin.send(embed=embed)
        await interaction.response.send_message("✅ Thanks! Your suggestion was sent anonymously.", ephemeral=True)


class SuggestionsView(discord.ui.View):
    _last: dict[int, float] = {}

    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot
        a = discord.ui.Button(label="Anonymous suggestion", emoji="🕶️", style=discord.ButtonStyle.primary,
                              custom_id="bl:suggest")
        a.callback = self.on_anon
        b = discord.ui.Button(label="Private ticket", emoji="🎫", style=discord.ButtonStyle.secondary,
                              custom_id="bl:ticket")
        b.callback = self.on_ticket
        self.add_item(a)
        self.add_item(b)

    async def on_anon(self, interaction: discord.Interaction) -> None:
        last = self._last.get(interaction.user.id, 0)
        if time.time() - last < ANON_COOLDOWN:
            await interaction.response.send_message("⏳ Please wait a few minutes before sending another one.",
                                                    ephemeral=True)
            return
        self._last[interaction.user.id] = time.time()
        await interaction.response.send_modal(SuggestionModal(self.bot))

    async def on_ticket(self, interaction: discord.Interaction) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        user = interaction.user
        open_ = [t for t in channel.threads if not t.archived and t.name.endswith(f"· {user.id}")]
        if open_:
            await interaction.followup.send(f"You already have an open ticket: {open_[0].mention}", ephemeral=True)
            return
        try:
            thread = await channel.create_thread(name=f"🎫 {user.display_name}"[:80] + f" · {user.id}",
                                                 type=discord.ChannelType.private_thread, invitable=False,
                                                 auto_archive_duration=10080, reason="Backline ticket")
            await thread.add_user(user)
            await thread.send(f"Hi {user.mention}! Tell us what's on your mind — the team will reply here. "
                              "Only you and the Backline team can see this thread.", view=TicketView())
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ Couldn't open a ticket right now ({e.status}).", ephemeral=True)
            return
        await interaction.followup.send(f"✅ Ticket opened: {thread.mention}", ephemeral=True)
        await self.bot.log_admin(f"🎫 Novo ticket de {user.mention}: {thread.mention}")


class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close ticket", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="bl:ticket_close")
    async def close(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        thread = interaction.channel
        if isinstance(thread, discord.Thread):
            await interaction.response.send_message(f"🔒 Ticket closed by {interaction.user.mention}.")
            await thread.edit(archived=True, locked=True)


class Community(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        bot.add_view(SuggestionsView(bot))
        bot.add_view(TicketView())


async def setup(bot):
    await bot.add_cog(Community(bot))
