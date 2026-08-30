# ======================================================
# Pokémon RPG Discord Bot — Interactive v4
# ======================================================

import discord
from discord.ext import commands
import json, os, random
from dotenv import load_dotenv
from discord.ui import View, Button
import asyncio

from utils.pokemon_manager import PokemonManager
from utils.player_manager import PlayerManager
from utils.battle_system import BattleSystem
from discord import app_commands



# ------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

POKEMON_FILE = "pokemon.json"
PLAYER_FILE = "data/players.json"

ENCOUNTER_RATE = 0.8
ITEM_DROP_RATE = 1.0
XP_WIN_REWARD = 30
RANDOM_VARIANCE = 0.2

os.makedirs("data", exist_ok=True)
if not os.path.exists(PLAYER_FILE):
    with open(PLAYER_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f, indent=2)

# ------------------------------------------------------
# INITIALIZATION
# ------------------------------------------------------

INTENTS = discord.Intents.default()
INTENTS.message_content = False
BOT = commands.Bot(command_prefix="!", intents=INTENTS)

POKEMON_MANAGER = PokemonManager(POKEMON_FILE)
PLAYER_MANAGER = PlayerManager(PLAYER_FILE)
BATTLE_SYSTEM = BattleSystem(POKEMON_MANAGER, PLAYER_MANAGER)

BALL_TYPES = {
    "pokeball": 1.0,
    "greatball": 1.5,
    "ultraball": 2.0,
    "masterball": 255.0
}

# ------------------------------------------------------
# BOT EVENTS
# ------------------------------------------------------

@BOT.event
async def on_ready():
    await BOT.tree.sync()
    print(f"✅ Logged in as {BOT.user} | Slash commands ready.")

# ------------------------------------------------------
# PLAYER COMMANDS
# ------------------------------------------------------

@BOT.tree.command(name="start", description="Begin your Pokémon adventure!")
async def start(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    if PLAYER_MANAGER.player_exists(user_id):
        await interaction.response.send_message(f"👋 You’ve already started, {interaction.user.mention}!")
        return

    starter = POKEMON_MANAGER.get_random_starter()
    PLAYER_MANAGER.add_player(user_id, interaction.user.name, starter)
    await interaction.response.send_message(f"🎉 Welcome {interaction.user.mention}! You received **{starter['name']}**!")

@BOT.tree.command(name="pokemon", description="View your Pokémon collection.")
async def pokemon(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    player = PLAYER_MANAGER.get_player(user_id)
    if not player:
        await interaction.response.send_message("❌ Start your journey first using `/start`.")
        return

    pokemon_list = player.get("pokemon", [])
    if not pokemon_list:
        await interaction.response.send_message("😔 You don't have any Pokémon yet.")
        return

    formatted = "\n".join([f"• {p['name']} (Lv {p['level']}) HP:{p.get('current_hp', '?')}" for p in pokemon_list])
    await interaction.response.send_message(f"📜 **{interaction.user.name}’s Pokémon:**\n{formatted}")

@BOT.tree.command(name="info", description="Get Pokédex info for any Pokémon.")
async def info(interaction: discord.Interaction, name: str):
    pokemon = POKEMON_MANAGER.get_pokemon_by_name(name)
    if not pokemon:
        await interaction.response.send_message(f"❌ Pokémon '{name}' not found.")
        return

    embed = discord.Embed(
        title=pokemon["name"],
        description=f"Type: {', '.join(pokemon['type'])}",
        color=discord.Color.green(),
    )
    embed.add_field(name="HP", value=pokemon["hp"])
    embed.add_field(name="Attack", value=pokemon["attack"])
    embed.add_field(name="Defense", value=pokemon["defense"])
    embed.add_field(name="Sp. Atk", value=pokemon["sp_atk"])
    embed.add_field(name="Sp. Def", value=pokemon["sp_def"])
    embed.add_field(name="Speed", value=pokemon["speed"])
    moves = ", ".join([m["name"] for m in pokemon.get("moves", [])])
    embed.add_field(name="Moves", value=moves or "None")
    await interaction.response.send_message(embed=embed)

# ------------------------------------------------------
# INTERACTIVE CATCH SYSTEM
# ------------------------------------------------------

from discord.ui import View, Button

# Random ball reward pool
BALL_REWARDS = ["pokeball", "greatball", "ultraball"]

class CatchView(discord.ui.View):
    def __init__(self, interaction: discord.Interaction, wild_pokemon: dict, player_manager):
        super().__init__(timeout=30)
        self.interaction = interaction
        self.wild_pokemon = wild_pokemon
        self.player_manager = player_manager
        self.caught = False  # Prevent double-catching

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Only the trainer who initiated the catch can click buttons
        return interaction.user.id == self.interaction.user.id

    async def on_timeout(self):
        if not self.caught:
            try:
                await self.interaction.followup.send(f"⌛ Time's up! The wild **{self.wild_pokemon['name']}** ran away.")
            except:
                pass

    async def attempt_catch(self, interaction: discord.Interaction, ball_type: str):
        if self.caught:
            await interaction.response.send_message("⚠️ You already caught this Pokémon or it ran away!", ephemeral=True)
            return

        user_id = str(interaction.user.id)

        # Infinite basic pokéballs
        if ball_type != "pokeball" and self.player_manager.players[user_id]["inventory"].get(ball_type, 0) <= 0:
            await interaction.response.send_message(f"❌ You have no {ball_type}s left!", ephemeral=True)
            return

        # Reduce ball count if not pokeball
        if ball_type != "pokeball":
            self.player_manager.reduce_item(user_id, ball_type)

        # Catch formula
        max_hp = self.wild_pokemon["max_hp"]
        current_hp = self.wild_pokemon["current_hp"]
        catch_rate = self.wild_pokemon.get("catch_rate", 45)
        status_factor = 1.0
        if self.wild_pokemon.get("status") in ["sleep", "freeze"]:
            status_factor = 3.0
        elif self.wild_pokemon.get("status") in ["paralyze", "burn", "poison"]:
            status_factor = 2.0

        ball_factor = {"pokeball": 1.0, "greatball": 1.5, "ultraball": 2.0, "masterball": 255.0}.get(ball_type, 1.0)
        success_chance = (catch_rate / 255) * ((2 * max_hp - current_hp) / (2 * max_hp)) * status_factor * ball_factor * 1.5
        success_chance = min(1.0, success_chance)

        if random.random() <= success_chance:
            self.player_manager.add_pokemon_to_player(user_id, self.wild_pokemon)
            self.caught = True

            # Random reward ball (20% chance)
            reward_msg = ""
            if random.random() < 1.0:
                reward_ball = random.choice(BALL_REWARDS)
                if reward_ball != "pokeball":
                    self.player_manager.players[user_id]["inventory"][reward_ball] = \
                        self.player_manager.players[user_id]["inventory"].get(reward_ball, 0) + 1
                    reward_msg = f"\n🎁 You received 1 **{reward_ball}** as a bonus!"

            await interaction.response.send_message(
                f"🎉 You caught **{self.wild_pokemon['name']}** with a {ball_type}!{reward_msg}"
            )
        else:
            await interaction.response.send_message(f"💨 The wild **{self.wild_pokemon['name']}** broke free!")

        self.stop()  # Close the view after catching attempt

    # Buttons
    @discord.ui.button(label="Poké Ball", style=discord.ButtonStyle.primary, custom_id="catch_pokeball")
    async def pokeball_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.attempt_catch(interaction, "pokeball")

    @discord.ui.button(label="Great Ball", style=discord.ButtonStyle.blurple, custom_id="catch_greatball")
    async def greatball_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.attempt_catch(interaction, "greatball")

    @discord.ui.button(label="Ultra Ball", style=discord.ButtonStyle.green, custom_id="catch_ultraball")
    async def ultraball_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.attempt_catch(interaction, "ultraball")

    @discord.ui.button(label="Master Ball", style=discord.ButtonStyle.red, custom_id="catch_masterball")
    async def masterball_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.attempt_catch(interaction, "masterball")

        
@BOT.tree.command(name="catch", description="Search the tall grass for wild Pokémon!")
async def catch(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    if not PLAYER_MANAGER.player_exists(user_id):
        await interaction.response.send_message("❗ Start your journey first using `/start`.")
        return

    if random.random() > ENCOUNTER_RATE:
        await interaction.response.send_message("🌿 You searched the grass... but found nothing.")
        return

    # Choose a random wild Pokémon
    wild_base = POKEMON_MANAGER.get_random_pokemon()
    wild_level = random.randint(5, 40)  # scale as needed

    max_hp = wild_base.get("hp", 50)
    current_hp = random.randint(int(max_hp * 0.3), max_hp)

    # Wild Pokémon instance for display
    wild_preview = {
        "name": wild_base["name"],
        "max_hp": max_hp,
        "current_hp": current_hp,
        "level": wild_level,
        "catch_rate": wild_base.get("catch_rate", 45),
        "status": random.choices(["none", "paralyze", "sleep"], weights=[7,2,1])[0]
    }

    # Build a simple embed to show HP, level, and status
    embed = discord.Embed(
        title=f"A wild {wild_preview['name']} appeared!",
        description=f"Lv {wild_level} | HP: {current_hp}/{max_hp} | Status: {wild_preview['status'].capitalize()}",
        color=discord.Color.green()
    )

    await interaction.response.send_message(
        f"🌾 A wild **{wild_preview['name']}** (Lv {wild_level}) appeared! HP: {current_hp}/{max_hp}",
        view=CatchView(interaction, wild_preview, PLAYER_MANAGER)
    )

@BOT.tree.command(name="store", description="Send a Pokémon to storage")
@app_commands.describe(poke_index="Index of your Pokémon to store (0-based)")
async def store(interaction: discord.Interaction, poke_index: int):
    player = PLAYER_MANAGER.get_player(str(interaction.user.id))
    if poke_index < 0 or poke_index >= len(player["pokemon"]):
        await interaction.response.send_message("❌ Invalid Pokémon index!")
        return

    pokemon = player["pokemon"][poke_index]
    if pokemon.get("stored"):
        await interaction.response.send_message("⚠️ Pokémon is already in storage!")
        return

    pokemon["stored"] = True  # Mark the Pokémon as stored
    PLAYER_MANAGER._save()
    await interaction.response.send_message(
        f"✅ Pokémon **{pokemon['name']}** moved to storage."
    )


@BOT.tree.command(name="unstorage", description="Bring a Pokémon back to the team")
@app_commands.describe(poke_index="Index of your Pokémon to unstore (0-based)")
async def unstorage(interaction: discord.Interaction, poke_index: int):
    player = PLAYER_MANAGER.get_player(str(interaction.user.id))
    if poke_index < 0 or poke_index >= len(player["pokemon"]):
        await interaction.response.send_message("❌ Invalid Pokémon index!")
        return

    pokemon = player["pokemon"][poke_index]
    if not pokemon.get("stored"):
        await interaction.response.send_message("❌ Pokémon is not in storage!")
        return

    pokemon["stored"] = False  # Remove stored status
    PLAYER_MANAGER._save()
    await interaction.response.send_message(
        f"✅ Pokémon **{pokemon['name']}** returned from storage."
    )

# ------------------------------------------------------
# INTERACTIVE BATTLE SYSTEM
# ------------------------------------------------------

from discord.ui import View, Button
from discord import Interaction
from discord.ui import View, Button
from discord import Interaction

class BattleView(View):
    def __init__(self, challenger_id: str, opponent_id: str):
        super().__init__(timeout=None)
        self.challenger_id = challenger_id
        self.opponent_id = opponent_id
        self.turn = challenger_id  # Challenger always goes first

        # Get player data
        challenger = PLAYER_MANAGER.get_player(challenger_id)
        opponent = PLAYER_MANAGER.get_player(opponent_id)

        # Auto-select 3 Pokémon skipping storage
        challenger_team = [i for i, _ in enumerate(challenger["pokemon"]) if i not in challenger.get("storage", [])] or [0]
        opponent_team = [i for i, _ in enumerate(opponent["pokemon"]) if i not in opponent.get("storage", [])] or [0]

        challenger["team"] = challenger_team[:3]
        opponent["team"] = opponent_team[:3]

        # Ensure current_hp and other defaults exist
        for idx in challenger["team"]:
            p = challenger["pokemon"][idx]
            p.setdefault("current_hp", p.get("max_hp"))
            p.setdefault("level", p.get("level", 5))
            p.setdefault("exp", p.get("exp", 0))

        for idx in opponent["team"]:
            p = opponent["pokemon"][idx]
            p.setdefault("current_hp", p.get("max_hp"))
            p.setdefault("level", p.get("level", 5))
            p.setdefault("exp", p.get("exp", 0))

        PLAYER_MANAGER._save()

    async def interaction_check(self, interaction: Interaction) -> bool:
        # Only allow the player whose turn it is to press buttons
        if str(interaction.user.id) != self.turn:
            await interaction.response.send_message("❌ It's not your turn!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Attack", style=discord.ButtonStyle.primary)
    async def attack(self, interaction: Interaction, button: Button):
        # Determine attacker and defender
        attacker_id = self.turn
        defender_id = self.opponent_id if self.turn == self.challenger_id else self.challenger_id

        attacker = PLAYER_MANAGER.get_player(attacker_id)
        defender = PLAYER_MANAGER.get_player(defender_id)

        # Execute a single attack turn
        log = BATTLE_SYSTEM.single_attack_turn(attacker_id, defender_id)

        # Check if battle is over
        attacker_alive = BATTLE_SYSTEM.get_active_pokemon_index(attacker) != -1
        defender_alive = BATTLE_SYSTEM.get_active_pokemon_index(defender) != -1

        if not attacker_alive or not defender_alive:
            winner = attacker if attacker_alive else defender
            log += f"\n🏆 **{winner['name']}** wins the battle!"

            # Heal all Pokémon in both teams
            for idx in winner["team"]:
                winner["pokemon"][idx]["current_hp"] = winner["pokemon"][idx].get("max_hp", 10)
            loser = defender if winner == attacker else attacker
            for idx in loser["team"]:
                loser["pokemon"][idx]["current_hp"] = loser["pokemon"][idx].get("max_hp", 10)

            # Disable buttons to end the battle
            for child in self.children:
                child.disabled = True

            # Update the message and stop the view
            await interaction.response.edit_message(content=log, view=self)
            PLAYER_MANAGER._save()
            self.stop()  # End the BattleView so no more interactions are possible
            return

        # Switch turn
        self.turn = defender_id
        log += f"\n🔄 It's now {PLAYER_MANAGER.get_player(self.turn)['name']}'s turn!"

        # Update the message with new battle log
        await interaction.response.edit_message(content=log, view=self)
        PLAYER_MANAGER._save()
                
@BOT.tree.command(name="battle", description="Challenge another player to a Pokémon duel!")
async def battle(interaction: discord.Interaction, opponent: discord.User):
    challenger_id = str(interaction.user.id)
    opponent_id = str(opponent.id)

    if challenger_id == opponent_id:
        await interaction.response.send_message("⚠️ You can’t battle yourself!")
        return

    if not PLAYER_MANAGER.player_exists(challenger_id) or not PLAYER_MANAGER.player_exists(opponent_id):
        await interaction.response.send_message("❗ Both trainers must start with `/start` first.")
        return

    challenger_player = PLAYER_MANAGER.get_player(challenger_id)
    opponent_player = PLAYER_MANAGER.get_player(opponent_id)

    # Auto-select 3 Pokémon skipping stored ones
    challenger_team = [i for i, p in enumerate(challenger_player["pokemon"]) if not p.get("stored")] or [0]
    opponent_team = [i for i, p in enumerate(opponent_player["pokemon"]) if not p.get("stored")] or [0]
    challenger_player["team"] = challenger_team[:3]
    opponent_player["team"] = opponent_team[:3]

    # Initialize current_hp and other defaults
    for idx in challenger_player["team"]:
        p = challenger_player["pokemon"][idx]
        p.setdefault("level", p.get("level", 5))
        p.setdefault("exp", p.get("exp", 0))
        p.setdefault("current_hp", p.get("max_hp"))

    for idx in opponent_player["team"]:
        p = opponent_player["pokemon"][idx]
        p.setdefault("level", p.get("level", 5))
        p.setdefault("exp", p.get("exp", 0))
        p.setdefault("current_hp", p.get("max_hp"))

    PLAYER_MANAGER._save()

    # Announce battle start using first active Pokémon
    ch_active = BATTLE_SYSTEM.get_active_pokemon(challenger_player)
    op_active = BATTLE_SYSTEM.get_active_pokemon(opponent_player)
    ch_name = ch_active["name"] if ch_active else "No Pokémon"
    op_name = op_active["name"] if op_active else "No Pokémon"

    await interaction.response.send_message(
        f"⚔️ {interaction.user.mention} challenged {opponent.mention}!\n"
        f"**{challenger_player['name']}** sends **{ch_name}** (Lv {ch_active.get('level', '?')})\n"
        f"**{opponent_player['name']}** sends **{op_name}** (Lv {op_active.get('level', '?')})\n"
        "Battle begins!",
        view=BattleView(challenger_id, opponent_id)
    )
# ------------------------------------------------------
# MAIN EXECUTION
# ------------------------------------------------------

if __name__ == "__main__":
    try:
        BOT.run(TOKEN)
    except Exception as e:
        print(f"Error : {e}")
