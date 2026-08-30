# utils/player_manager.py
import json
import os
import random
from typing import Dict, Any, List, Optional

# Gen1-ish stat formulas (IV/EV supported)
def hp_formula(base_hp:int, iv:int, ev:int, level:int) -> int:
    return ((2*base_hp + iv + (ev//4)) * level) // 100 + level + 10

def stat_formula(base_stat:int, iv:int, ev:int, level:int) -> int:
    return ((2*base_stat + iv + (ev//4)) * level) // 100 + 5

def random_ivs():
    return {k: random.randint(0,31) for k in ("hp","attack","defense","sp_atk","sp_def","speed")}

def empty_evs():
    return {k: 0 for k in ("hp","attack","defense","sp_atk","sp_def","speed")}

class PlayerManager:
    MAX_TEAM_SIZE = 3

    def __init__(self, path: str):
        self.path = path
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                json.dump({}, f)
        self._load()

    def _load(self):
        with open(self.path, "r", encoding="utf-8") as f:
            try:
                self.players: Dict[str, Any] = json.load(f)
            except json.JSONDecodeError:
                self.players = {}

    def _save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.players, f, indent=2)

    # -------- Basic getters --------
    def player_exists(self, user_id: str) -> bool:
        return user_id in self.players

    def get_player(self, user_id: str) -> Optional[Dict[str, Any]]:
        return self.players.get(user_id)

    # -------- Internal: compute and store full stats on creation / level change --------
    def _compute_and_store_stats(self, entry: Dict[str, Any]):
        """Given an entry with base_stats, level, ivs, evs — compute full stats and store them."""
        base = entry.get("base_stats", {})
        level = entry.get("level", 5)
        ivs = entry.setdefault("ivs", random_ivs())
        evs = entry.setdefault("evs", empty_evs())

        entry["max_hp"] = hp_formula(base.get("hp", 10), ivs.get("hp",0), evs.get("hp",0), level)
        entry["attack"] = stat_formula(base.get("attack", 5), ivs.get("attack",0), evs.get("attack",0), level)
        entry["defense"] = stat_formula(base.get("defense", 5), ivs.get("defense",0), evs.get("defense",0), level)
        entry["sp_atk"] = stat_formula(base.get("sp_atk", base.get("attack",5)), ivs.get("sp_atk",0), evs.get("sp_atk",0), level)
        entry["sp_def"] = stat_formula(base.get("sp_def", base.get("defense",5)), ivs.get("sp_def",0), evs.get("sp_def",0), level)
        entry["speed"] = stat_formula(base.get("speed", 10), ivs.get("speed",0), evs.get("speed",0), level)

        # Ensure current_hp exists (if missing, set to full)
        entry.setdefault("current_hp", entry["max_hp"])

    # -------- Create a player (starter) --------
    def add_player(self, user_id: str, name: str, starter_base: Dict[str, Any], starter_level: Optional[int] = None):
        level = int(starter_level) if starter_level is not None else int(starter_base.get("level", 5))
        starter_entry = self._make_pokemon_entry(starter_base, level)

        self.players[user_id] = {
            "name": name,
            "pokemon": [starter_entry],   # full collection
            "team": [0],                  # indices into 'pokemon' list
            "inventory": {"pokeball": 5, "greatball": 3, "ultraball": 1},
            "storage": []
        }
        self._save()

    # -------- Build stored pokemon entry --------
    def _make_pokemon_entry(self, base: Dict[str, Any], level: int) -> Dict[str, Any]:
        entry = {
            "name": base["name"],
            "level": int(level),
            "exp": int(base.get("base_exp", 0)) if base.get("base_exp") else 0,
            "ivs": random_ivs(),
            "evs": empty_evs(),
            "ev_style": "B",
            "ev_distribution": {"hp": 1, "attack": 3, "defense": 1, "sp_atk": 0, "sp_def": 1, "speed": 2},
            "base_stats": {
                "hp": base.get("hp", 10),
                "attack": base.get("attack", 5),
                "defense": base.get("defense", 5),
                "sp_atk": base.get("sp_atk", base.get("attack", 5)),
                "sp_def": base.get("sp_def", base.get("defense", 5)),
                "speed": base.get("speed", 10),
                "type": base.get("type", ["Normal"]),
                "catch_rate": base.get("catch_rate", 45),
                "id": base.get("id")
            },
            # store moves if present (not used currently)
            "moves": base.get("moves", [])
        }
        # compute derived stats and add current_hp
        self._compute_and_store_stats(entry)
        return entry

    # -------- Add pokemon to player collection --------
    def add_pokemon_to_player(self, user_id: str, base_pokemon: Dict[str, Any], level: Optional[int] = None):
        if user_id not in self.players:
            return False
        level_to_use = int(level) if level is not None else int(base_pokemon.get("level", 5))
        new_entry = self._make_pokemon_entry(base_pokemon, level_to_use)
        self.players[user_id].setdefault("pokemon", []).append(new_entry)

        # If team less than MAX, auto add index to team
        team = self.players[user_id].setdefault("team", [])
        if len(team) < self.MAX_TEAM_SIZE:
            team.append(len(self.players[user_id]["pokemon"]) - 1)

        self._save()
        return True

    # -------- Team management --------
    def set_team(self, user_id: str, indices: List[int]):
        if user_id not in self.players:
            return False
        pokes = self.players[user_id].get("pokemon", [])
        if not all(isinstance(i, int) and 0 <= i < len(pokes) for i in indices):
            return False
        self.players[user_id]["team"] = indices[: self.MAX_TEAM_SIZE]
        self._save()
        return True

    def get_team_entries(self, user_id: str) -> Optional[List[Dict[str, Any]]]:
        player = self.players.get(user_id)
        if not player:
            return None
        return [player["pokemon"][i] for i in player.get("team", []) if 0 <= i < len(player.get("pokemon", []))]

    # -------- Item management & helpers --------
    def reduce_item(self, user_id: str, item_name: str):
        if item_name == "pokeball":
            return True  # infinite basic Poké Balls
        player = self.players.get(user_id)
        if not player:
            return False
        inv = player.setdefault("inventory", {})
        if inv.get(item_name, 0) > 0:
            inv[item_name] -= 1
            self._save()
            return True
        return False

    def add_item(self, user_id: str, item_name: str, amount: int = 1):
        if user_id not in self.players:
            return False
        inv = self.players[user_id].setdefault("inventory", {})
        inv[item_name] = inv.get(item_name, 0) + amount
        self._save()
        return True

    def heal_team_full(self, user_id: str):
        """Heal all team Pokémon to their max_hp (used after level-up or for heal command)."""
        player = self.players.get(user_id)
        if not player:
            return False
        for idx in player.get("team", []):
            if 0 <= idx < len(player["pokemon"]):
                p = player["pokemon"][idx]
                self._compute_and_store_stats(p)  # recompute in case IV/EV changed
                p["current_hp"] = p["max_hp"]
        self._save()
        return True

    # small utility for debug/listing
    def list_pokemon_names(self, user_id: str) -> List[str]:
        player = self.players.get(user_id)
        if not player:
            return []
        return [f"{i}: {p['name']} (Lv {p.get('level', '?')}) HP:{p.get('current_hp','?')}/{p.get('max_hp','?')}" for i,p in enumerate(player.get("pokemon", []))]

    def add_random_ball(self, user_id: str):
        # Choose reward based on player's Pokémon count
        player = self.get_player(user_id)
        if not player:
            return "pokeball"
        count = len(player.get("pokemon", []))


        weights = [60, 25, 12]  # Poké, Great, Ultra base weights
        balls = ["pokeball", "greatball", "ultraball"]

        # Add Master Ball chance
        master_chance = 1
        if count > 10:
            master_chance = 3
        if count > 30:
            master_chance = 6

        balls.append("masterball")
        weights.append(master_chance)

        reward = random.choices(balls, weights=weights, k=1)[0]

        # Give ball (skip infinite Poké Ball)
        if reward != "pokeball":
            inv = player.setdefault("inventory", {})
            inv[reward] = inv.get(reward, 0) + 1
            self._save()
        return reward
