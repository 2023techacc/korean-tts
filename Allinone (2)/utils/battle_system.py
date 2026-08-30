# utils/battle_system.py
import random
from typing import Dict, List

# Gen1-ish type chart (real stacking for dual types)
TYPE_CHART = {
    "normal": {"rock":0.5, "ghost":0.0},
    "fire":   {"fire":0.5, "water":0.5, "grass":2.0, "ice":2.0, "bug":2.0, "rock":0.5, "dragon":0.5},
    "water":  {"fire":2.0, "water":0.5, "grass":0.5, "ground":2.0, "rock":2.0, "dragon":0.5},
    "electric":{"water":2.0, "electric":0.5, "grass":0.5, "ground":0.0, "flying":2.0, "dragon":0.5},
    "grass":  {"fire":0.5, "water":2.0, "grass":0.5, "ground":2.0, "rock":2.0, "dragon":0.5},
    "ice":    {"fire":0.5, "water":0.5, "grass":2.0, "ground":2.0, "flying":2.0, "dragon":2.0},
    "fighting":{"normal":2.0, "ice":2.0, "rock":2.0, "ghost":0.0, "flying":0.5, "psychic":0.5},
    "poison": {"grass":2.0, "poison":0.5, "ground":0.5, "rock":0.5, "ghost":0.5},
    "ground": {"fire":2.0, "electric":2.0, "grass":0.5, "poison":2.0, "rock":2.0, "flying":0.0},
    "flying": {"grass":2.0, "fighting":2.0, "bug":2.0, "electric":0.5, "rock":0.5},
    "psychic":{"fighting":2.0, "poison":2.0, "psychic":0.5, "ghost":0.0},
    "bug":    {"grass":2.0, "psychic":2.0, "fire":0.5, "fighting":0.5, "flying":0.5, "rock":0.5},
    "rock":   {"fire":2.0, "ice":2.0, "flying":2.0, "bug":2.0, "fighting":0.5, "ground":0.5},
    "ghost":  {"psychic":2.0, "ghost":2.0, "normal":0.0},
    "dragon": {"dragon":2.0},
}

def type_effectiveness(move_type: str, defender_types: List[str]) -> float:
    m = 1.0
    mt = (move_type or "normal").lower()
    for dt in defender_types:
        dt_l = dt.lower()
        m *= TYPE_CHART.get(mt, {}).get(dt_l, 1.0)
    return m

class BattleSystem:
    def __init__(self, pokemon_manager, player_manager):
        self.pokemon_manager = pokemon_manager
        self.player_manager = player_manager

    # find first alive pokemon index in team or -1
    def get_active_pokemon_index(self, player: Dict) -> int:
        for i in player.get("team", []):
            p = player["pokemon"][i]
            if p.get("current_hp", 0) > 0:
                return i
        return -1

    def get_active_pokemon(self, player: Dict):
        idx = self.get_active_pokemon_index(player)
        if idx == -1:
            return None
        return player["pokemon"][idx]

    def compute_stats_from_entry(self, entry: Dict) -> Dict:
        # PlayerManager already writes full stats (max_hp, attack, defense, sp_atk, sp_def, speed)
        # but we return a simple dict to use in calculations
        return {
            "max_hp": entry.get("max_hp"),
            "attack": entry.get("attack"),
            "defense": entry.get("defense"),
            "sp_atk": entry.get("sp_atk"),
            "sp_def": entry.get("sp_def"),
            "speed": entry.get("speed"),
            "types": entry.get("base_stats", {}).get("type", ["Normal"])
        }

    # EXP formula (Medium Fast B2)
    def exp_to_next_level(self, level:int) -> int:
        return int((level ** 3) * 0.8)

    # single attack where attacker active pokemon hits defender active pokemon
    def single_attack_turn(self, attacker_id: str, defender_id: str) -> str:
        attacker_player = self.player_manager.get_player(attacker_id)
        defender_player = self.player_manager.get_player(defender_id)
        if not attacker_player or not defender_player:
            return "Invalid trainer."

        a_idx = self.get_active_pokemon_index(attacker_player)
        d_idx = self.get_active_pokemon_index(defender_player)
        if a_idx == -1 or d_idx == -1:
            return "One side has no available Pokémon."

        a_poke = attacker_player["pokemon"][a_idx]
        d_poke = defender_player["pokemon"][d_idx]

        # Ensure default current_hp exists
        a_poke.setdefault("current_hp", a_poke.get("max_hp", 10))
        d_poke.setdefault("current_hp", d_poke.get("max_hp", 10))

        a_stats = self.compute_stats_from_entry(a_poke)
        d_stats = self.compute_stats_from_entry(d_poke)

        # Basic attack formula
        level = a_poke.get("level", 5)
        power = 35
        atk = a_stats["attack"]
        defe = max(1, d_stats["defense"])

        # STAB
        move_type = a_stats["types"][0]
        stab = 1.5 if move_type.lower() in [t.lower() for t in a_stats["types"]] else 1.0
        eff = type_effectiveness(move_type, d_stats["types"])
        dmg = max(1, int((((2*level/5+2)*power*(atk/defe))/50+2) * stab * eff * random.uniform(0.85,1.0)))

        # Apply damage
        d_poke["current_hp"] = max(0, d_poke.get("current_hp") - dmg)
        logs = [f"⚔️ **{a_poke['name']}** used a basic attack! **{dmg}** damage to **{d_poke['name']}**."]

        # Type effectiveness messages
        if eff > 1: logs.append("💥 It's super effective!")
        elif 0 < eff < 1: logs.append("⚡ It's not very effective...")
        elif eff == 0: logs.append("❌ It had no effect!")

        # Faint handling
        if d_poke["current_hp"] <= 0:
            logs.append(f"💀 **{d_poke['name']}** fainted!")

            # XP gain
            defender_base_hp = d_poke.get("base_stats", {}).get("hp", 10)
            xp_gain = (defender_base_hp + d_poke.get("level", 5)) * 3
            a_poke["exp"] = a_poke.get("exp",0) + xp_gain
            logs.append(f"✨ **{a_poke['name']}** gained **{xp_gain} XP**.")

            # Level-up loop
            while a_poke["exp"] >= self.exp_to_next_level(a_poke.get("level",5)):
                a_poke["exp"] -= self.exp_to_next_level(a_poke.get("level",5))
                a_poke["level"] += 1
                # Simple stat increase per level
                a_poke["max_hp"] += 3
                a_poke["attack"] += 1
                a_poke["defense"] += 1
                a_poke["sp_atk"] += 1
                a_poke["sp_def"] += 1
                a_poke["speed"] += 1
                a_poke["current_hp"] = a_poke["max_hp"]
                logs.append(f"⬆️ **{a_poke['name']}** leveled up to Lv {a_poke['level']}!")

            # Auto-switch defender
            next_idx = self.get_active_pokemon_index(defender_player)
            if next_idx == -1:
                logs.append(f"🏆 **{attacker_player['name']}** wins the battle!")
                for p in attacker_player["pokemon"]:
                    p["current_hp"] = p.get("max_hp", p.get("hp", 10))
                for p in defender_player["pokemon"]:
                    p["current_hp"] = p.get("max_hp", p.get("hp", 10))
    
                logs.append("💖 All Pokémon from both trainers have been healed!")

            else:
                next_poke = defender_player["pokemon"][next_idx]
                logs.append(f"➡️ **{defender_player['name']}** sends out **{next_poke['name']}**!")

        else:
            logs.append(f"🩸 **{d_poke['name']}** has {d_poke['current_hp']} / {d_stats['max_hp']} HP left.")

        # Auto-save players
        self.player_manager._save()
        return "\n".join(logs)
