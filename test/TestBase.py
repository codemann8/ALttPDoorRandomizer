import unittest

import RaceRandom as random
from BaseClasses import CollectionState
from Bosses import place_bosses
from CLI import parse_cli
from DoorShuffle import link_doors, link_doors_prep
from Doors import create_doors
from Dungeons import create_dungeons
from Fill import get_dungeon_item_pool, set_prize_drops
from ItemList import create_farm_locations, difficulties, generate_itempool
from Items import ItemFactory
from Main import init_world, resolve_random_settings, set_starting_inventory
from OWEdges import create_owedges
from OverworldShuffle import link_overworld
from Regions import adjust_locations, create_regions, create_dungeon_regions, create_shops, mark_light_dark_world_regions
from RoomData import create_rooms
from Rules import set_rules
from source.classes.BabelFish import BabelFish
from source.classes.CustomSettings import CustomSettings
from source.enemizer.DamageTables import DamageTable
from source.enemizer.Enemizer import randomize_enemies
from source.item.District import init_districts
from source.item.FillUtil import create_item_pool_config, verify_item_pool_config
from source.overworld.EntranceShuffle2 import link_entrances_new
from source.rom.DataTables import init_data_tables

PRIZES = ['Green Pendant', 'Red Pendant', 'Blue Pendant', 'Beat Agahnim 1', 'Beat Agahnim 2',
          'Crystal 1', 'Crystal 2', 'Crystal 3', 'Crystal 4', 'Crystal 5', 'Crystal 6', 'Crystal 7']


def build_vanilla_world(mode='open', logic='noglitches', customizer=None, key_logic='partial'):
    player = 1
    args = parse_cli(['--mode', mode, '--logic', logic, '--shuffle', 'vanilla', '--door_shuffle', 'vanilla',
                      '--intensity', '1', '--suppress_rom', '--spoiler', 'none',
                      '--key_logic_algorithm', key_logic])
    world = init_world(args, BabelFish(lang='en'))
    world.customizer = customizer
    world.seed = 1
    random.seed(world.seed)
    resolve_random_settings(world, args)
    world.rom_seeds = {player: 1}
    world.finish_init()
    world.difficulty_requirements[player] = difficulties[world.difficulty[player]]
    set_starting_inventory(world, args)
    world.settings = CustomSettings()
    world.settings.create_from_world(world, args)

    create_regions(world, player)
    create_dungeon_regions(world, player)
    create_owedges(world, player)
    create_shops(world, player)
    create_doors(world, player)
    create_rooms(world, player)
    create_dungeons(world, player)
    world.damage_table[player] = DamageTable()
    world.data_tables[player] = init_data_tables(world, player)
    place_bosses(world, player)
    randomize_enemies(world, player)
    adjust_locations(world, player)

    link_overworld(world, player)
    create_shops(world, player)
    mark_light_dark_world_regions(world, player)
    init_districts(world)
    link_entrances_new(world, player)
    link_doors_prep(world, player)
    create_item_pool_config(world)
    link_doors(world, player)
    mark_light_dark_world_regions(world, player)

    set_prize_drops(world, player)
    create_farm_locations(world, player)
    generate_itempool(world, player)
    verify_item_pool_config(world)
    world.required_medallions[player] = ['Ether', 'Quake']
    world.itempool.extend(get_dungeon_item_pool(world))
    world.itempool.extend(ItemFactory(PRIZES, player))
    world.get_location('Agahnim 1', player).item = None
    world.get_location('Agahnim 2', player).item = None
    set_rules(world, player)
    return world


class TestBase(unittest.TestCase):

    _state_cache = {}

    def get_state(self, items):
        if (self.world, tuple(items)) in self._state_cache:
            return self._state_cache[self.world, tuple(items)]
        state = CollectionState(self.world)
        for item in items:
            item.advancement = True
            state.collect(item)
        state.sweep_for_events()
        self._state_cache[self.world, tuple(items)] = state
        return state

    def run_location_tests(self, access_pool):
        for location, access, *item_pool in access_pool:
            items = item_pool[0]
            all_except = item_pool[1] if len(item_pool) > 1 else None
            with self.subTest(location=location, access=access, items=items, all_except=all_except):
                if all_except and len(all_except) > 0:
                    items = self.world.itempool[:]
                    items = [item for item in items if item.name not in all_except and not ("Bottle" in item.name and "AnyBottle" in all_except)]
                    items.extend(ItemFactory(item_pool[0], 1))
                else:
                    items = ItemFactory(items, 1)
                state = self.get_state(items)

                self.assertEqual(self.world.get_location(location, 1).can_reach(state), access)

    def run_entrance_tests(self, access_pool):
        for entrance, access, *item_pool in access_pool:
            items = item_pool[0]
            all_except = item_pool[1] if len(item_pool) > 1 else None
            with self.subTest(entrance=entrance, access=access, items=items, all_except=all_except):
                if all_except and len(all_except) > 0:
                    items = self.world.itempool[:]
                    items = [item for item in items if item.name not in all_except and not ("Bottle" in item.name and "AnyBottle" in all_except)]
                    items.extend(ItemFactory(item_pool[0], 1))
                else:
                    items = ItemFactory(items, 1)
                state = self.get_state(items)

                self.assertEqual(self.world.get_entrance(entrance, 1).can_reach(state), access)