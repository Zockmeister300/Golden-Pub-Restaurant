import discord
from discord.ext import commands, tasks
import asyncio
import datetime
from flask import Flask
from threading import Thread
import os
from dotenv import load_dotenv

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

eingestempelt_users = {}
arbeitszeiten_records = {}

CHANNEL_NAME_ZEITSTEMPELN = "zeitstempeln"
CHANNEL_NAME_ARBEITSZEITEN = "arbeitszeiten"
CHANNEL_NAME_REMINDER = "stempel-reminder"
CHANNEL_NAME_LEADERSCHAFT = "leaderschaft"
DIENSTROLLE_NAME = "Im Dienst"

def format_dauer(td: datetime.timedelta):
    total_seconds = int(td.total_seconds())
    wochen, rest = divmod(total_seconds, 604800)
    tage, rest = divmod(rest, 86400)
    stunden, rest = divmod(rest, 3600)
    minuten, sekunden = divmod(rest, 60)
    teile = []
    if wochen > 0:
        teile.append(f"{wochen} Woche{'n' if wochen != 1 else ''}")
    if tage > 0:
        teile.append(f"{tage} Tag{'e' if tage != 1 else ''}")
    if stunden > 0:
        teile.append(f"{stunden} Stunde{'n' if stunden != 1 else ''}")
    if minuten > 0:
        teile.append(f"{minuten} Minute{'n' if minuten != 1 else ''}")
    if sekunden > 0 or not teile:
        teile.append(f"{sekunden} Sekunde{'n' if sekunden != 1 else ''}")
    return ", ".join(teile)

@bot.event
async def on_ready():
    print(f"Bot ist ready als {bot.user}")
    reminder_loop.start()
    await sende_stempel_nachricht()
    await aktualisiere_leaderschaft()

@bot.command()
async def start(ctx):
    if ctx.channel.name != CHANNEL_NAME_ZEITSTEMPELN:
        await ctx.send(f"Bitte nutze diesen Befehl im Kanal #{CHANNEL_NAME_ZEITSTEMPELN}")
        return
    try:
        await ctx.message.delete()
    except discord.Forbidden:
        await ctx.send("Ich kann `!start` nicht löschen – bitte gib mir die Berechtigung `Nachrichten verwalten`.")
    await sende_stempel_nachricht()

async def sende_stempel_nachricht():
    for guild in bot.guilds:
        kanal = discord.utils.get(guild.text_channels, name=CHANNEL_NAME_ZEITSTEMPELN)
        if kanal:
            message = await kanal.send("Reagiere mit ✅ um dich einzustempeln oder mit ❌ um dich auszustempeln.")
            await message.add_reaction("✅")
            await message.add_reaction("❌")

@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return
    emoji = str(reaction.emoji)
    message = reaction.message
    if message.channel.name != CHANNEL_NAME_ZEITSTEMPELN:
        return
    if emoji == "✅":
        if user.id in eingestempelt_users:
            warn_msg = await message.channel.send(f"{user.mention}, du bist bereits eingestempelt!")
            await asyncio.sleep(5)
            await warn_msg.delete()
            try: await reaction.remove(user)
            except discord.Forbidden: pass
            return
        startzeit = datetime.datetime.now()
        info_msg = await message.channel.send(f"{user.mention} hat sich eingestempelt.")
        eingestempelt_users[user.id] = {"start": startzeit, "msg": info_msg, "last_reminder": startzeit}
        await verwalte_dienstrolle(user, hinzufügen=True)
        try: await reaction.remove(user)
        except discord.Forbidden: pass
    elif emoji == "❌":
        if user.id not in eingestempelt_users:
            warn_msg = await message.channel.send(f"{user.mention}, du bist nicht eingestempelt.")
            await asyncio.sleep(5)
            await warn_msg.delete()
            try: await reaction.remove(user)
            except discord.Forbidden: pass
            return
        await ausstempeln(user, message.guild, message.channel, reaction)

async def ausstempeln(user, guild, channel, reaction=None):
    startzeit = eingestempelt_users[user.id]["start"]
    eincheck_msg = eingestempelt_users[user.id]["msg"]
    dauer = datetime.datetime.now() - startzeit
    eingestempelt_users.pop(user.id)
    await verwalte_dienstrolle(user, hinzufügen=False)
    if user.id in arbeitszeiten_records:
        arbeitszeiten_records[user.id]["dauer"] += dauer
        gesamt_dauer = arbeitszeiten_records[user.id]["dauer"]
        arbeits_msg = arbeitszeiten_records[user.id]["msg"]
        try:
            await arbeits_msg.edit(content=f"{user.mention} hat insgesamt {format_dauer(gesamt_dauer)} gearbeitet.")
        except discord.NotFound:
            pass
    else:
        gesamt_dauer = dauer
        arbeitszeiten_kanal = discord.utils.get(guild.text_channels, name=CHANNEL_NAME_ARBEITSZEITEN)
        if arbeitszeiten_kanal:
            arbeits_msg = await arbeitszeiten_kanal.send(f"{user.mention} hat insgesamt {format_dauer(gesamt_dauer)} gearbeitet.")
            arbeitszeiten_records[user.id] = {"dauer": gesamt_dauer, "msg": arbeits_msg}
    confirm_msg = await channel.send(f"{user.mention} hat sich erfolgreich ausgestempelt.")
    await asyncio.sleep(5)
    for msg in (confirm_msg, eincheck_msg):
        try: await msg.delete()
        except discord.NotFound: pass
    if reaction:
        try: await reaction.remove(user)
        except discord.Forbidden: pass
    await aktualisiere_leaderschaft()

async def verwalte_dienstrolle(user, hinzufügen=True):
    rolle = discord.utils.get(user.guild.roles, name=DIENSTROLLE_NAME)
    if rolle:
        try:
            if hinzufügen:
                await user.add_roles(rolle)
            else:
                await user.remove_roles(rolle)
        except discord.Forbidden:
            print(f"Keine Berechtigung, um Rolle '{DIENSTROLLE_NAME}' bei {user} zu verwalten.")

async def aktualisiere_leaderschaft():
    for guild in bot.guilds:
        kanal = discord.utils.get(guild.text_channels, name=CHANNEL_NAME_LEADERSCHAFT)
        if not kanal:
            continue
        einträge = []
        for user_id, daten in arbeitszeiten_records.items():
            mitglied = guild.get_member(user_id)
            if mitglied:
                einträge.append((mitglied, daten["dauer"]))
        einträge.sort(key=lambda x: x[1], reverse=True)
        if not einträge:
            await kanal.send("Noch keine Daten für die Leaderschaft.")
            return
        lines = []
        for index, (user, dauer) in enumerate(einträge, start=1):
            emoji = {1: "🥇", 2: "🥈", 3: "🥉"}.get(index, f"{index}.")
            lines.append(f"{emoji} {user.mention} – {format_dauer(dauer)}")
        await kanal.purge()
        await kanal.send("**Leaderschaft der fleißigsten Mitglieder:**\n\n" + "\n".join(lines))

@tasks.loop(minutes=1)
async def reminder_loop():
    now = datetime.datetime.now()
    for user_id, data in list(eingestempelt_users.items()):
        letzte_stunde = data["last_reminder"] + datetime.timedelta(hours=1)
        if now >= letzte_stunde:
            guild = bot.guilds[0]
            user = guild.get_member(user_id)
            reminder_channel = discord.utils.get(guild.text_channels, name=CHANNEL_NAME_REMINDER)
            if not user or not reminder_channel:
                continue
            reminder_msg = await reminder_channel.send(f"{user.mention}, bist du noch da? Reagiere innerhalb von 10 Minuten mit ✅.")
            await reminder_msg.add_reaction("✅")
            def check(reaction, u):
                return reaction.message.id == reminder_msg.id and str(reaction.emoji) == "✅" and u.id == user_id
            try:
                await bot.wait_for("reaction_add", timeout=600, check=check)
                eingestempelt_users[user_id]["last_reminder"] = now
            except asyncio.TimeoutError:
                zeitstempeln_channel = discord.utils.get(guild.text_channels, name=CHANNEL_NAME_ZEITSTEMPELN)
                await ausstempeln(user, guild, zeitstempeln_channel)
                try: await reminder_msg.delete()
                except discord.NotFound: pass

# Flask-Webserver für Keep-Alive
app = Flask('')

@app.route('/')
def home():
    return "Bot läuft!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

keep_alive()
bot.run(os.getenv("DISCORD_TOKEN"))
