import json
import random

class PokemonManager:
    def __init__(self, filepath: str):
        with open(filepath, "r", encoding="utf-8") as f:
            self.pokemon_data = json.load(f)

    def get_random_starter(self):
        """Return a random starter Pokémon (Bulbasaur, Charmander, or Squirtle)."""
        starters = [p for p in self.pokemon_data if p["name"] in ["Bulbasaur", "Charmander", "Squirtle"]]
        return random.choice(starters)

    def get_random_pokemon(self):
        """Return a random Pokémon from the full Pokédex list."""
        return random.choice(self.pokemon_data)

    def get_pokemon_by_name(self, name: str):
        """Find a Pokémon by its name (case-insensitive)."""
        for p in self.pokemon_data:
            if p["name"].lower() == name.lower():
                return p
        return None
