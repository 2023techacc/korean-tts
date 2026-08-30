# ======================================================
# Combined Pokémon RPG, Music, School-Info & TTS Discord Bot
# ======================================================

import asyncio
import datetime
import functools
import json
import os
import random
import shutil
import tempfile

import discord
import requests
import yt_dlp
from bs4 import BeautifulSoup
from discord import FFmpegPCMAudio, Interaction, app_commands
from discord.ext import commands
from discord.ui import Button, View
from dotenv import load_dotenv
from korean_tts import PAUSE, build_audio, text_to_groups, text_to_samples, to_wav_bytes

# PyNaCl is not used directly, but discord.py needs it for voice.
# Fail loudly at startup instead of mysteriously at the first !join.
try:
    import nacl  # noqa: F401
except ImportError:
    print("!!! WARNING: PyNaCl is not installed. Voice will not work. (pip install PyNaCl)")

# ------------------------------------------------------
# POKÉMON UTILS
# ------------------------------------------------------
# These files must exist in a 'utils' folder:
#   utils/pokemon_manager.py
#   utils/player_manager.py
#   utils/battle_system.py

try:
    from utils.pokemon_manager import PokemonManager
    from utils.player_manager import PlayerManager
    from utils.battle_system import BattleSystem

    POKEMON_ENABLED = True
except ImportError as exc:
    print("=" * 50)
    print(f"!!! CRITICAL WARNING: could not import the `utils` package ({exc}).")
    print("The bot will RUN, but all Pokémon commands will be disabled.")
    print("Please ensure you have the following files:")
    print("  - utils/pokemon_manager.py")
    print("  - utils/player_manager.py")
    print("  - utils/battle_system.py")
    print("=" * 50)
    POKEMON_ENABLED = False
    PokemonManager = PlayerManager = BattleSystem = None

# ------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

BASE_PATH = os.path.dirname(os.path.abspath(__file__))
POKEMON_FILE = os.path.join(BASE_PATH, "pokemon.json")
PLAYER_FILE = os.path.join(BASE_PATH, "data", "players.json")
SOUND_FOLDER = os.path.join(BASE_PATH, "sound")

# --- Pokémon tuning ---
ENCOUNTER_RATE = 0.8       # chance /catch finds anything at all
BALL_REWARD_CHANCE = 1.0   # chance of a bonus ball after a successful catch
WILD_LEVEL_RANGE = (5, 40)

# --- FFmpeg ---
# Set FFMPEG_PATH in .env to the full path of ffmpeg.exe, or leave it unset if
# ffmpeg is already on your PATH.
FFMPEG_PATH = os.getenv("FFMPEG_PATH") or shutil.which("ffmpeg")
if not FFMPEG_PATH or not os.path.exists(FFMPEG_PATH):
    print("=" * 50)
    print(f"!!! WARNING: ffmpeg not found (looked at: {FFMPEG_PATH!r}).")
    print("!!! Music and TTS commands will FAIL.")
    print("!!! Download it from https://www.gyan.dev/ffmpeg/builds/ and either")
    print("!!! add it to your PATH or set FFMPEG_PATH=C:\\path\\to\\ffmpeg.exe in .env")
    print("=" * 50)
    FFMPEG_PATH = "ffmpeg"  # let discord.py try PATH anyway

# --- yt-dlp ---
YDL_OPTIONS = {
    "format": "bestaudio[ext=m4a]/bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "noplaylist": True,
}
FFMPEG_BEFORE_OPTIONS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin"
FFMPEG_OPTIONS = "-vn -f s16le -ar 48000 -ac 2"

os.makedirs(os.path.dirname(PLAYER_FILE), exist_ok=True)
if not os.path.exists(PLAYER_FILE):
    with open(PLAYER_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f, indent=2)

# ------------------------------------------------------
# BOT INITIALIZATION
# ------------------------------------------------------

# message_content is needed for the "!" prefix commands.
INTENTS = discord.Intents.default()
INTENTS.message_content = True

bot = commands.Bot(command_prefix="!", intents=INTENTS)

# ------------------------------------------------------
# POKÉMON SYSTEM INITIALIZATION
# ------------------------------------------------------

if POKEMON_ENABLED:
    POKEMON_MANAGER = PokemonManager(POKEMON_FILE)
    PLAYER_MANAGER = PlayerManager(PLAYER_FILE)
    BATTLE_SYSTEM = BattleSystem(POKEMON_MANAGER, PLAYER_MANAGER)
else:
    POKEMON_MANAGER = PLAYER_MANAGER = BATTLE_SYSTEM = None

BALL_FACTORS = {
    "pokeball": 1.0,
    "greatball": 1.5,
    "ultraball": 2.0,
    "masterball": 255.0,
}

# ------------------------------------------------------
# MUSIC / VOICE STATE
# ------------------------------------------------------
repeat_flags = {}    # {guild_id: bool}
current_songs = {}   # {guild_id: query/url}
say_locks = {}       # {guild_id: asyncio.Lock} -- one TTS playback at a time


# ------------------------------------------------------
# BOT EVENTS
# ------------------------------------------------------

@bot.event
async def on_ready():
    # on_ready fires again after every reconnect; only sync the tree once.
    if not getattr(bot, "_commands_synced", False):
        try:
            synced = await bot.tree.sync()
            bot._commands_synced = True
            print(f"✅ Synced {len(synced)} slash commands.")
        except Exception as e:
            print(f"[ERROR] Slash command sync failed: {e}")
    print(f"✅ Logged in as {bot.user}")
    if not POKEMON_ENABLED:
        print("⚠️  Pokémon commands are disabled (utils package missing).")


@bot.event
async def on_command_error(ctx, error):
    """Prefix commands swallow errors by default; tell the user what went wrong."""
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(
            f"❌ 인자가 부족합니다. 사용법: `!{ctx.command.qualified_name} {ctx.command.signature}`"
        )
        return
    print(f"[ERROR] !{getattr(ctx.command, 'name', '?')}: {error!r}")
    await ctx.send(f"❌ 명령 실행 중 오류가 발생했습니다: `{error}`")


async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Slash commands swallow errors by default; tell the user what went wrong."""
    print(f"[ERROR] /{getattr(interaction.command, 'name', '?')}: {error!r}")
    message = f"❌ 명령 실행 중 오류가 발생했습니다: `{error}`"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        pass


bot.tree.on_error = on_app_command_error


async def require_pokemon(interaction: discord.Interaction) -> bool:
    """Reply and return False if the Pokémon backend is unavailable."""
    if POKEMON_ENABLED:
        return True
    await interaction.response.send_message(
        "⚠️ Pokémon 기능을 사용할 수 없습니다 (`utils` 패키지 누락).", ephemeral=True
    )
    return False


# ------------------------------------------------------
# POKÉMON: PLAYER COMMANDS (Slash)
# ------------------------------------------------------

@bot.tree.command(name="start", description="Begin your Pokémon adventure!")
async def start(interaction: discord.Interaction):
    if not await require_pokemon(interaction):
        return

    user_id = str(interaction.user.id)
    if PLAYER_MANAGER.player_exists(user_id):
        await interaction.response.send_message(f"👋 You've already started, {interaction.user.mention}!")
        return

    starter = POKEMON_MANAGER.get_random_starter()
    PLAYER_MANAGER.add_player(user_id, interaction.user.name, starter)
    await interaction.response.send_message(
        f"🎉 Welcome {interaction.user.mention}! You received **{starter['name']}**!"
    )


@bot.tree.command(name="pokemon", description="View your Pokémon collection.")
async def pokemon(interaction: discord.Interaction):
    if not await require_pokemon(interaction):
        return

    player = PLAYER_MANAGER.get_player(str(interaction.user.id))
    if not player:
        await interaction.response.send_message("❌ Start your journey first using `/start`.")
        return

    pokemon_list = player.get("pokemon", [])
    if not pokemon_list:
        await interaction.response.send_message("😔 You don't have any Pokémon yet.")
        return

    lines = [
        f"`{i}` {p['name']} (Lv {p.get('level', '?')}) "
        f"HP:{p.get('current_hp', '?')}/{p.get('max_hp', '?')}"
        f"{' 📦' if p.get('stored') else ''}"
        for i, p in enumerate(pokemon_list)
    ]

    # Discord hard-caps messages at 2000 characters.
    header = f"📜 **{interaction.user.name}'s Pokémon:**\n"
    shown = list(lines)
    while shown and len(header) + len("\n".join(shown)) > 1900:
        shown.pop()
    body = "\n".join(shown)
    if len(shown) < len(lines):
        body += f"\n… ({len(lines) - len(shown)} more)"
    await interaction.response.send_message(header + body)


@bot.tree.command(name="info", description="Get Pokédex info for any Pokémon.")
async def info(interaction: discord.Interaction, name: str):
    if not await require_pokemon(interaction):
        return

    entry = POKEMON_MANAGER.get_pokemon_by_name(name)
    if not entry:
        await interaction.response.send_message(f"❌ Pokémon '{name}' not found.")
        return

    embed = discord.Embed(
        title=entry["name"],
        description=f"Type: {', '.join(entry.get('type', ['Normal']))}",
        color=discord.Color.green(),
    )
    for label, key in (
        ("HP", "hp"),
        ("Attack", "attack"),
        ("Defense", "defense"),
        ("Sp. Atk", "sp_atk"),
        ("Sp. Def", "sp_def"),
        ("Speed", "speed"),
    ):
        embed.add_field(name=label, value=entry.get(key, "?"))

    moves = ", ".join(m["name"] for m in entry.get("moves", []))
    embed.add_field(name="Moves", value=moves or "None")
    await interaction.response.send_message(embed=embed)


# ------------------------------------------------------
# POKÉMON: INTERACTIVE CATCH SYSTEM (Slash)
# ------------------------------------------------------

class CatchView(discord.ui.View):
    def __init__(self, interaction: discord.Interaction, wild_base: dict, wild_state: dict):
        super().__init__(timeout=30)
        self.interaction = interaction
        # wild_base is the full Pokédex entry. It has to be kept around so the
        # caught Pokémon is built from real base stats rather than the
        # 10/5/5/5/5/10 fallbacks in PlayerManager._make_pokemon_entry.
        self.wild_base = wild_base
        self.wild_state = wild_state
        self.finished = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.interaction.user.id:
            await interaction.response.send_message("❌ 당신의 조우가 아닙니다!", ephemeral=True)
            return False
        return True

    async def _close(self):
        self.finished = True
        self.stop()
        try:
            await self.interaction.edit_original_response(view=None)
        except discord.HTTPException:
            pass

    async def on_timeout(self):
        if self.finished:
            return
        self.finished = True
        try:
            await self.interaction.edit_original_response(
                content=f"⌛ Time's up! The wild **{self.wild_state['name']}** ran away.", view=None
            )
        except discord.HTTPException:
            pass

    async def attempt_catch(self, interaction: discord.Interaction, ball_type: str):
        if self.finished:
            await interaction.response.send_message("⚠️ 이미 끝난 조우입니다!", ephemeral=True)
            return

        user_id = str(interaction.user.id)
        if not PLAYER_MANAGER.player_exists(user_id):
            await interaction.response.send_message("❗ `/start` 로 모험을 시작하세요.", ephemeral=True)
            return

        # Poké Balls are infinite; every other ball must be in the inventory.
        # reduce_item() already encodes that rule, so trust its return value.
        if not PLAYER_MANAGER.reduce_item(user_id, ball_type):
            await interaction.response.send_message(f"❌ You have no {ball_type}s left!", ephemeral=True)
            return

        max_hp = max(1, self.wild_state["max_hp"])
        current_hp = self.wild_state["current_hp"]
        catch_rate = self.wild_state.get("catch_rate", 45)

        status_factor = 1.0
        if self.wild_state.get("status") in ("sleep", "freeze"):
            status_factor = 3.0
        elif self.wild_state.get("status") in ("paralyze", "burn", "poison"):
            status_factor = 2.0

        ball_factor = BALL_FACTORS.get(ball_type, 1.0)
        success_chance = min(
            1.0,
            (catch_rate / 255)
            * ((2 * max_hp - current_hp) / (2 * max_hp))
            * status_factor
            * ball_factor
            * 1.5,
        )

        if random.random() <= success_chance:
            PLAYER_MANAGER.add_pokemon_to_player(user_id, self.wild_base, level=self.wild_state["level"])

            reward_msg = ""
            if random.random() < BALL_REWARD_CHANCE:
                reward = PLAYER_MANAGER.add_random_ball(user_id)
                if reward != "pokeball":
                    reward_msg = f"\n🎁 You received 1 **{reward}** as a bonus!"

            await interaction.response.send_message(
                f"🎉 You caught **{self.wild_state['name']}** "
                f"(Lv {self.wild_state['level']}) with a {ball_type}!{reward_msg}"
            )
        else:
            await interaction.response.send_message(
                f"💨 The wild **{self.wild_state['name']}** broke free!"
            )

        await self._close()

    @discord.ui.button(label="Poké Ball", style=discord.ButtonStyle.primary)
    async def pokeball_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.attempt_catch(interaction, "pokeball")

    @discord.ui.button(label="Great Ball", style=discord.ButtonStyle.blurple)
    async def greatball_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.attempt_catch(interaction, "greatball")

    @discord.ui.button(label="Ultra Ball", style=discord.ButtonStyle.green)
    async def ultraball_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.attempt_catch(interaction, "ultraball")

    @discord.ui.button(label="Master Ball", style=discord.ButtonStyle.red)
    async def masterball_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.attempt_catch(interaction, "masterball")


@bot.tree.command(name="catch", description="Search the tall grass for wild Pokémon!")
async def catch(interaction: discord.Interaction):
    if not await require_pokemon(interaction):
        return

    user_id = str(interaction.user.id)
    if not PLAYER_MANAGER.player_exists(user_id):
        await interaction.response.send_message("❗ Start your journey first using `/start`.")
        return

    if random.random() > ENCOUNTER_RATE:
        await interaction.response.send_message("🌿 You searched the grass... but found nothing.")
        return

    wild_base = POKEMON_MANAGER.get_random_pokemon()
    wild_level = random.randint(*WILD_LEVEL_RANGE)

    max_hp = wild_base.get("hp", 50)
    current_hp = random.randint(max(1, int(max_hp * 0.3)), max_hp)

    wild_state = {
        "name": wild_base["name"],
        "max_hp": max_hp,
        "current_hp": current_hp,
        "level": wild_level,
        "catch_rate": wild_base.get("catch_rate", 45),
        "status": random.choices(["none", "paralyze", "sleep"], weights=[7, 2, 1])[0],
    }

    await interaction.response.send_message(
        f"🌾 A wild **{wild_state['name']}** (Lv {wild_level}) appeared! "
        f"HP: {current_hp}/{max_hp} | Status: {wild_state['status'].capitalize()}",
        view=CatchView(interaction, wild_base, wild_state),
    )


@bot.tree.command(name="store", description="Send a Pokémon to storage")
@app_commands.describe(poke_index="Index of your Pokémon to store (see /pokemon)")
async def store(interaction: discord.Interaction, poke_index: int):
    if not await require_pokemon(interaction):
        return

    player = PLAYER_MANAGER.get_player(str(interaction.user.id))
    if not player:
        await interaction.response.send_message("❌ Start your journey first using `/start`.")
        return
    if not 0 <= poke_index < len(player["pokemon"]):
        await interaction.response.send_message("❌ Invalid Pokémon index!")
        return

    entry = player["pokemon"][poke_index]
    if entry.get("stored"):
        await interaction.response.send_message("⚠️ Pokémon is already in storage!")
        return

    active = [i for i, p in enumerate(player["pokemon"]) if not p.get("stored")]
    if len(active) <= 1:
        await interaction.response.send_message("❌ 마지막 남은 포켓몬은 보관할 수 없습니다!")
        return

    entry["stored"] = True
    # Keep the battle team consistent with storage.
    player["team"] = [i for i in player.get("team", []) if i != poke_index]
    PLAYER_MANAGER._save()
    await interaction.response.send_message(f"✅ Pokémon **{entry['name']}** moved to storage.")


@bot.tree.command(name="unstorage", description="Bring a Pokémon back to the team")
@app_commands.describe(poke_index="Index of your Pokémon to unstore (see /pokemon)")
async def unstorage(interaction: discord.Interaction, poke_index: int):
    if not await require_pokemon(interaction):
        return

    player = PLAYER_MANAGER.get_player(str(interaction.user.id))
    if not player:
        await interaction.response.send_message("❌ Start your journey first using `/start`.")
        return
    if not 0 <= poke_index < len(player["pokemon"]):
        await interaction.response.send_message("❌ Invalid Pokémon index!")
        return

    entry = player["pokemon"][poke_index]
    if not entry.get("stored"):
        await interaction.response.send_message("❌ Pokémon is not in storage!")
        return

    entry["stored"] = False
    PLAYER_MANAGER._save()
    await interaction.response.send_message(f"✅ Pokémon **{entry['name']}** returned from storage.")


# ------------------------------------------------------
# POKÉMON: INTERACTIVE BATTLE SYSTEM (Slash)
# ------------------------------------------------------

class BattleView(View):
    def __init__(self, challenger_id: str, opponent_id: str):
        super().__init__(timeout=300)
        self.challenger_id = challenger_id
        self.opponent_id = opponent_id
        self.turn = challenger_id  # Challenger always goes first
        self.message = None

    async def interaction_check(self, interaction: Interaction) -> bool:
        if str(interaction.user.id) not in (self.challenger_id, self.opponent_id):
            await interaction.response.send_message("❌ 이 배틀의 참가자가 아닙니다!", ephemeral=True)
            return False
        if str(interaction.user.id) != self.turn:
            await interaction.response.send_message("❌ It's not your turn!", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(content="⌛ 배틀이 시간 초과로 종료되었습니다.", view=None)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Attack", style=discord.ButtonStyle.primary)
    async def attack(self, interaction: Interaction, button: Button):
        attacker_id = self.turn
        defender_id = self.opponent_id if self.turn == self.challenger_id else self.challenger_id

        defender = PLAYER_MANAGER.get_player(defender_id)
        if not PLAYER_MANAGER.get_player(attacker_id) or not defender:
            await interaction.response.edit_message(content="❌ 트레이너 정보를 찾을 수 없습니다.", view=None)
            self.stop()
            return

        # single_attack_turn already appends the win message and heals both teams.
        log = BATTLE_SYSTEM.single_attack_turn(attacker_id, defender_id)

        if BATTLE_SYSTEM.get_active_pokemon_index(defender) == -1:
            self.stop()
            await interaction.response.edit_message(content=log, view=None)
            PLAYER_MANAGER._save()
            return

        self.turn = defender_id
        log += f"\n🔄 It's now {defender['name']}'s turn!"

        await interaction.response.edit_message(content=log, view=self)
        PLAYER_MANAGER._save()


@bot.tree.command(name="battle", description="Challenge another player to a Pokémon duel!")
async def battle(interaction: discord.Interaction, opponent: discord.User):
    if not await require_pokemon(interaction):
        return

    challenger_id = str(interaction.user.id)
    opponent_id = str(opponent.id)

    if challenger_id == opponent_id:
        await interaction.response.send_message("⚠️ You can't battle yourself!")
        return
    if opponent.bot:
        await interaction.response.send_message("⚠️ 봇과는 배틀할 수 없습니다!")
        return
    if not PLAYER_MANAGER.player_exists(challenger_id) or not PLAYER_MANAGER.player_exists(opponent_id):
        await interaction.response.send_message("❗ Both trainers must start with `/start` first.")
        return

    challenger_player = PLAYER_MANAGER.get_player(challenger_id)
    opponent_player = PLAYER_MANAGER.get_player(opponent_id)

    # Auto-select up to 3 Pokémon, skipping stored ones.
    for player in (challenger_player, opponent_player):
        team = [i for i, p in enumerate(player["pokemon"]) if not p.get("stored")][:3]
        if not team:
            await interaction.response.send_message(
                f"❌ **{player['name']}** 님은 사용할 수 있는 포켓몬이 없습니다 (전부 보관 중)."
            )
            return
        player["team"] = team
        for idx in team:
            p = player["pokemon"][idx]
            p.setdefault("level", 5)
            p.setdefault("exp", 0)
            # Heal to full, otherwise a team left fainted by the previous battle
            # would start this one already defeated.
            p["current_hp"] = p.get("max_hp", 10)

    PLAYER_MANAGER._save()

    ch_active = BATTLE_SYSTEM.get_active_pokemon(challenger_player)
    op_active = BATTLE_SYSTEM.get_active_pokemon(opponent_player)

    view = BattleView(challenger_id, opponent_id)
    await interaction.response.send_message(
        f"⚔️ {interaction.user.mention} challenged {opponent.mention}!\n"
        f"**{challenger_player['name']}** sends **{ch_active['name']}** (Lv {ch_active.get('level', '?')})\n"
        f"**{opponent_player['name']}** sends **{op_active['name']}** (Lv {op_active.get('level', '?')})\n"
        "Battle begins!",
        view=view,
    )
    view.message = await interaction.original_response()


# ------------------------------------------------------
# MUSIC: HELPER FUNCTIONS
# ------------------------------------------------------

async def get_info(query: str):
    """Resolve a search query or URL to a yt-dlp info dict, off the event loop."""
    loop = asyncio.get_running_loop()
    ydl = yt_dlp.YoutubeDL(YDL_OPTIONS)
    func = functools.partial(ydl.extract_info, query, download=False)
    try:
        info = await loop.run_in_executor(None, func)
    except Exception as e:
        print(f"[ERROR] yt_dlp 오류: {e}")
        return None
    if not info:
        return None
    if "entries" in info:
        entries = [e for e in info["entries"] if e]
        if not entries:
            return None
        info = entries[0]
    return info


def make_source(stream_url: str) -> discord.FFmpegPCMAudio:
    return discord.FFmpegPCMAudio(
        stream_url,
        executable=FFMPEG_PATH,
        before_options=FFMPEG_BEFORE_OPTIONS,
        options=FFMPEG_OPTIONS,
    )


def schedule_after_play(guild: discord.Guild):
    """Build an `after=` callback for VoiceClient.play.

    The callback runs on the voice thread, so it has to hand the coroutine back
    to the event loop with run_coroutine_threadsafe — loop.create_task is not
    thread-safe and the repeat never fires reliably when called that way.
    """

    def _after(error):
        asyncio.run_coroutine_threadsafe(after_play(guild, error), bot.loop)

    return _after


async def after_play(guild: discord.Guild, error):
    if error:
        print(f"[ERROR] 재생 오류: {error}")
        return

    if not repeat_flags.get(guild.id, False) or not guild.voice_client:
        return

    query = current_songs.get(guild.id)
    if not query:
        return

    info = await get_info(query)
    if not info or not info.get("url"):
        return

    guild.voice_client.play(make_source(info["url"]), after=schedule_after_play(guild))
    print(f"[DEBUG] 반복 재생 중: {info.get('title', 'Unknown Title')}")


async def ensure_voice(member: discord.Member, guild: discord.Guild):
    """Connect to (or move to) the member's voice channel. Returns the VoiceClient."""
    if not member.voice or not member.voice.channel:
        return None

    channel = member.voice.channel
    vc = guild.voice_client
    if vc is None:
        return await channel.connect()
    if vc.channel != channel:
        await vc.move_to(channel)
    return vc


async def start_playback(guild: discord.Guild, vc: discord.VoiceClient, query: str):
    """Resolve `query` and start playing it. Returns (title, error_message)."""
    info = await get_info(query)
    if not info:
        return None, "❌ 노래 정보를 가져올 수 없습니다."

    stream_url = info.get("url")
    if not stream_url:
        return None, "❌ 재생 가능한 오디오 스트림을 찾지 못했습니다."

    current_songs[guild.id] = query
    repeat_flags.setdefault(guild.id, False)

    if vc.is_playing() or vc.is_paused():
        vc.stop()

    vc.play(make_source(stream_url), after=schedule_after_play(guild))
    return info.get("title", "Unknown Title"), None


# ------------------------------------------------------
# MUSIC: SLASH COMMANDS
# ------------------------------------------------------

@bot.tree.command(name="play", description="Plays a song from YouTube.")
@app_commands.describe(query="The song name, search query, or YouTube URL.")
async def play_slash(interaction: discord.Interaction, query: str):
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message("먼저 음성 채널에 들어가야 합니다.", ephemeral=True)
        return

    # Connecting plus the yt-dlp lookup easily exceeds the 3s interaction deadline.
    await interaction.response.defer()

    vc = await ensure_voice(interaction.user, interaction.guild)
    if vc is None:
        await interaction.edit_original_response(content="먼저 음성 채널에 들어가야 합니다.")
        return

    title, error = await start_playback(interaction.guild, vc, query)
    await interaction.edit_original_response(content=error or f"▶️ **{title}** 재생 시작")
    if not error:
        print(f"[DEBUG] Now playing: {title}")


@bot.tree.command(name="stop", description="Stops the music and clears repeat.")
async def stop_slash(interaction: discord.Interaction):
    repeat_flags[interaction.guild.id] = False
    current_songs.pop(interaction.guild.id, None)

    vc = interaction.guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()
        await interaction.response.send_message("⏹️ 재생 중지")
    else:
        await interaction.response.send_message("🤷 재생 중인 노래 없음", ephemeral=True)


@bot.tree.command(name="leave", description="Disconnects the bot from the voice channel.")
async def leave_slash(interaction: discord.Interaction):
    repeat_flags[interaction.guild.id] = False
    current_songs.pop(interaction.guild.id, None)

    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("👋 음성 채널 연결 해제")
    else:
        await interaction.response.send_message("🤷 음성 채널에 연결되어 있지 않음", ephemeral=True)


@bot.tree.command(name="repeat", description="Sets the repeat mode.")
@app_commands.describe(mode="반복 재생 켜기/끄기")
@app_commands.choices(mode=[
    app_commands.Choice(name="On (켜기)", value="on"),
    app_commands.Choice(name="Off (끄기)", value="off"),
])
async def repeat_slash(interaction: discord.Interaction, mode: app_commands.Choice[str]):
    if mode.value == "on":
        repeat_flags[interaction.guild.id] = True
        await interaction.response.send_message("🔁 반복 모드 **켜짐**.")
    else:
        repeat_flags[interaction.guild.id] = False
        await interaction.response.send_message("🚫 반복 모드 **꺼짐**.")


# ------------------------------------------------------
# MUSIC: PREFIX COMMANDS (same behaviour as the slash ones)
# ------------------------------------------------------

@bot.command(name="play", aliases=["p", "재생"])
async def play_prefix(ctx, *, url: str):
    vc = await ensure_voice(ctx.author, ctx.guild)
    if vc is None:
        await ctx.send("먼저 음성 채널에 들어가야 합니다.")
        return

    msg = await ctx.send("🎵 노래 정보를 가져오는 중...")
    title, error = await start_playback(ctx.guild, vc, url)
    await msg.edit(content=error or f"▶️ **{title}** 재생 시작")


@bot.command(name="stop", aliases=["s", "종료"])
async def stop_prefix(ctx):
    repeat_flags[ctx.guild.id] = False
    current_songs.pop(ctx.guild.id, None)

    if ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
        ctx.voice_client.stop()
        await ctx.send("⏹️ 재생 중지")
    else:
        await ctx.send("🤷 재생 중인 노래 없음")


@bot.command(name="leave", aliases=["l", "나가"])
async def leave_prefix(ctx):
    repeat_flags[ctx.guild.id] = False
    current_songs.pop(ctx.guild.id, None)

    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("👋 음성 채널 연결 해제")
    else:
        await ctx.send("🤷 음성 채널에 연결되어 있지 않음")


@bot.command(name="repeat", aliases=["r", "반복"])
async def repeat_prefix(ctx, mode: str):
    if mode.lower() in ("on", "켜기"):
        repeat_flags[ctx.guild.id] = True
        await ctx.send("🔁 반복 모드 **켜짐**.")
    elif mode.lower() in ("off", "끄기"):
        repeat_flags[ctx.guild.id] = False
        await ctx.send("🚫 반복 모드 **꺼짐**.")
    else:
        await ctx.send("사용법: `!repeat on` 또는 `!repeat off`")


# -----------------------------
# SCHOOL MEAL & TIMETABLE
# -----------------------------

MEAL_URL = "https://yangji.sjeduhs.kr/yangji-h/ad/fm/foodmenu/selectFoodMenuView.do?mi=9245"
MEAL_LABELS = ["중식", "석식", "간식"]


def get_today_meal(url: str):
    """Scrape today's column out of the school meal table. Blocking — run in a thread."""
    headers = {"User-Agent": "MyMealBot/1.0"}
    response = requests.get(url, headers=headers, timeout=10)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    table = soup.find("table")
    if not table:
        return None

    thead = table.find("thead")
    tbody = table.find("tbody")
    if not thead or not tbody:
        return None

    weekday_map = {0: "월", 1: "화", 2: "수", 3: "목", 4: "금", 5: "토", 6: "일"}
    today_kor = weekday_map[datetime.datetime.today().weekday()]

    header_row = thead.find("tr")
    # Header cells read like "월(11.03)", so match on the first character only.
    initials = [th.get_text(strip=True)[:1] for th in header_row.find_all("th")]
    try:
        # -1 because the leading header column is the row label, which the body
        # rows render as a <th> and so is absent from find_all("td").
        day_index = initials.index(today_kor) - 1
    except ValueError:
        return None
    if day_index < 0:
        return None

    meals = []
    for row in tbody.find_all("tr"):
        cols = row.find_all("td")
        if len(cols) > day_index:
            meals.append(cols[day_index].get_text(separator=" ", strip=True))
    return meals


def format_meal(raw: str) -> str:
    """Turn '김치찌개(5.6) 밥 상세보기' into a clean newline-separated list."""
    items = []
    for token in raw.split():
        if token == "상세보기" or token.startswith("("):
            continue
        name = token.split("(")[0].strip()
        if name:
            items.append(name)
    return "\n".join(items) if items else "_(정보 없음)_"


@bot.command()
async def meal(ctx):
    try:
        # requests is blocking; keep it off the event loop.
        today_meals = await asyncio.to_thread(get_today_meal, MEAL_URL)
    except requests.RequestException as e:
        await ctx.send(f"❌ 급식 정보를 가져오지 못했습니다: `{e}`")
        return

    if not today_meals:
        await ctx.send("오늘의 급식 정보를 찾을 수 없습니다.")
        return

    parts = ["🍴 **Today's Meals** 🍴"]
    for i, raw in enumerate(today_meals):
        label = MEAL_LABELS[i] if i < len(MEAL_LABELS) else f"급식 {i + 1}"
        parts.append(f"\n**{label}**\n{format_meal(raw)}")
    await ctx.send("\n".join(parts))


TIMETABLE = [
    ["국어", "통합사회", "통합과학", "과학탐구실험 1", "정보/기가", "정보/기가"],
    ["체육", "통합사회", "한국사", "과학탐구실험 2", "영어", "음악", "통합과학"],
    ["체육", "수학", "국어", "음악", "통합사회", "창진", "영어"],
    ["미술", "미술", "통합사회", "한국사", "수학", "정보/기가", "영어"],
    ["통합과학", "한국사", "수학", "국어", "창체", "창체"],
    [],  # 토
    [],  # 일
]
WEEKDAY_NAMES = ["월", "화", "수", "목", "금", "토", "일"]


@bot.command()
async def timetable(ctx):
    today_index = datetime.datetime.today().weekday()
    today_schedule = TIMETABLE[today_index]

    if not today_schedule:
        await ctx.send("📅 오늘은 수업이 없습니다.")
        return

    # Pad every cell to the widest subject so the box-drawing lines up.
    col_width = max(max(len(p) for p in today_schedule), 8)
    lines = [
        f"📚 **{WEEKDAY_NAMES[today_index]}요일 시간표**",
        "```",
        f"┌{'─' * (col_width + 2)}┬{'─' * (col_width + 2)}┐",
    ]
    for i, subject in enumerate(today_schedule):
        lines.append(f"│ {f'{i + 1}교시':<{col_width}} │ {subject:<{col_width}} │")
    lines.append(f"└{'─' * (col_width + 2)}┴{'─' * (col_width + 2)}┘")
    lines.append("```")
    await ctx.send("\n".join(lines))


# ------------------------------------------------------
# TTS (Korean syllable -> .wav sample playback)
# ------------------------------------------------------

# The text -> sample-name mapping lives in korean_tts.py so the bot and the
# standalone CLI (tts.py) can never drift apart. See korean_tts.text_to_groups.


@bot.command()
async def join(ctx):
    if not ctx.author.voice:
        await ctx.send("먼저 음성 채널에 들어가주세요.")
        return
    vc = await ensure_voice(ctx.author, ctx.guild)
    await ctx.send(f"음성 채널 **{vc.channel.name}** 에 들어갔어요")


@bot.command()
async def getout(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("나갔어요!")
    else:
        await ctx.send("음성 채널에 있지 않아요.")


@bot.command()
async def say(ctx, *, text: str):
    vc = ctx.voice_client
    if not vc:
        await ctx.send("먼저 `!join` 으로 음성 채널에 들어가주세요.")
        return

    samples = text_to_samples(text)
    if not samples or all(s == PAUSE for s in samples):
        await ctx.send("읽을 수 있는 한글이 없습니다.")
        return

    # Two overlapping !say calls would fight over the same VoiceClient.
    lock = say_locks.setdefault(ctx.guild.id, asyncio.Lock())
    if lock.locked():
        await ctx.send("⏳ 이미 읽는 중입니다. 잠시 후 다시 시도해주세요.")
        return

    async with lock:
        # build_audio does file I/O plus per-sample loudness normalization
        # and crossfading; keep that off the event loop instead of blocking
        # the whole bot.
        groups = text_to_groups(text)
        track, missing = await asyncio.to_thread(build_audio, groups, SOUND_FOLDER)
        if not len(track):
            await ctx.send("재생할 오디오가 없습니다.")
            return

        fd, path = tempfile.mkstemp(prefix="tts_", suffix=".wav")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(to_wav_bytes(track))

            if not ctx.voice_client:  # disconnected while we were rendering
                return
            if vc.is_playing() or vc.is_paused():
                vc.stop()

            done = asyncio.Event()
            loop = asyncio.get_running_loop()

            def _after(error):
                if error:
                    print(f"[ERROR] TTS 재생 오류: {error}")
                loop.call_soon_threadsafe(done.set)

            vc.play(FFmpegPCMAudio(path, executable=FFMPEG_PATH), after=_after)
            await done.wait()
        finally:
            if os.path.exists(path):
                os.remove(path)

        if missing:
            uniq = sorted(set(missing))
            await ctx.send(f"⚠️ 음성 파일 {len(uniq)}개를 찾지 못해 건너뛰었습니다: {', '.join(uniq)}")


# ------------------------------------------------------
# MAIN EXECUTION
# ------------------------------------------------------

if __name__ == "__main__":
    if not TOKEN or TOKEN.strip() in ("", "TOKEN_HERE"):
        print("=" * 50)
        print("!!! CRITICAL ERROR: DISCORD_TOKEN not found or still a placeholder.")
        print("Create a `.env` file next to main.py containing:")
        print("    DISCORD_TOKEN=your_actual_bot_token")
        print("Get one at https://discord.com/developers/applications -> Bot -> Reset Token")
        print("=" * 50)
    else:
        try:
            bot.run(TOKEN)
        except discord.LoginFailure:
            print("❌ 로그인 실패: DISCORD_TOKEN 이 올바르지 않습니다.")
        except discord.PrivilegedIntentsRequired:
            print("❌ Message Content Intent 가 꺼져 있습니다.")
            print("   Developer Portal -> Bot -> Privileged Gateway Intents 에서 켜주세요.")
        except Exception as e:
            print(f"Error running bot: {e}")
