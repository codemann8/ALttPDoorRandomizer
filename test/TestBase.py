import unittest

from BaseClasses import CollectionState, World
from Items import ItemFactory


_world_cache = {}


def _p(value):
    return {1: value}


def parse_cli_for_tests(argv):
    """Parse CLI args from canned defaults, ignoring the user's saved.json / last.json."""
    from CLI import parse_cli
    return parse_cli(argv, include_user_settings=False)


def make_bare_world(logic='noglitches', mode='open', shuffle='vanilla', door_shuffle='vanilla'):
    """World with the current constructor; no regions yet."""
    world = World(
        1,
        _p('vanilla'),
        _p(True),
        _p('none'),
        _p(False),
        _p(shuffle),
        _p(door_shuffle),
        _p(logic),
        _p(mode),
        _p('random'),
        _p('normal'),
        _p('normal'),
        'none',
        'on',
        _p('ganon'),
        'balanced',
        _p('items'),
        _p(True),
        False,
        None,
        _p(False),
        'none',
    )
    world.pottery = _p('none')
    world.dropshuffle = _p('none')
    world.shuffle_bonk_drops = _p(False)
    world.customizer = None
    world.intensity[1] = 1
    world.flute_mode[1] = 'normal'
    world.bow_mode[1] = 'progressive'
    world.override_bomb_check = True
    return world


def make_logic_test_world(logic='noglitches', mode='open'):
    """Vanilla ER/DR world through set_rules, for location/entrance access tests."""
    cache_key = (logic, mode)
    cached = _world_cache.get(cache_key)
    if cached is not None:
        return cached

    import RaceRandom as random
    from DoorShuffle import link_doors, link_doors_prep
    from Doors import create_doors
    from Dungeons import create_dungeons
    from Fill import get_dungeon_item_pool
    from ItemList import difficulties, generate_itempool
    from Main import init_world, resolve_random_settings
    from OWEdges import create_owedges
    from OverworldShuffle import link_overworld
    from Regions import adjust_locations, create_regions, create_dungeon_regions, create_shops, mark_light_dark_world_regions
    from RoomData import create_rooms
    from Rules import set_rules
    from source.enemizer.DamageTables import DamageTable
    from source.rom.DataTables import init_data_tables
    from source.classes.BabelFish import BabelFish
    from source.classes.CustomSettings import CustomSettings
    from source.overworld.EntranceShuffle2 import link_entrances_new

    args = parse_cli_for_tests([
        '--logic', logic,
        '--mode', mode,
        '--shuffle', 'vanilla',
        '--door_shuffle', 'vanilla',
        '--ow_layout', 'vanilla',
        '--intensity', '1',
        '--spoiler', 'none',
    ])
    world = init_world(args, BabelFish(lang='en'))
    world.seed = 1
    random.seed(1)
    resolve_random_settings(world, args)
    world.finish_init()
    world.settings = CustomSettings()
    world.settings.create_from_world(world, args)
    world.difficulty_requirements[1] = difficulties['normal']
    world.intensity[1] = 1
    world.override_bomb_check = True
    create_regions(world, 1)
    create_dungeon_regions(world, 1)
    create_owedges(world, 1)
    create_shops(world, 1)
    create_doors(world, 1)
    create_rooms(world, 1)
    create_dungeons(world, 1)
    world.damage_table[1] = DamageTable()
    world.data_tables[1] = init_data_tables(world, 1)
    adjust_locations(world, 1)
    link_overworld(world, 1)
    link_entrances_new(world, 1)
    link_doors_prep(world, 1)
    link_doors(world, 1)
    generate_itempool(world, 1)
    world.required_medallions[1] = ['Ether', 'Quake']
    world.itempool.extend(get_dungeon_item_pool(world))
    world.itempool.extend(ItemFactory(
        ['Green Pendant', 'Red Pendant', 'Blue Pendant', 'Beat Agahnim 1', 'Beat Agahnim 2',
         'Crystal 1', 'Crystal 2', 'Crystal 3', 'Crystal 4', 'Crystal 5', 'Crystal 6', 'Crystal 7'], 1))
    world.get_location('Agahnim 1', 1).item = None
    world.get_location('Agahnim 2', 1).item = None
    if logic in ('owglitches', 'hybridglitches'):
        world.precollected_items.clear()
        world.itempool.append(ItemFactory('Pegasus Boots', 1))
    mark_light_dark_world_regions(world, 1)
    set_rules(world, 1)
    _world_cache[cache_key] = world
    return world


class TestBase(unittest.TestCase):

    _state_cache = {}

    def get_state(self, items):
        key = (id(self.world), tuple((item.name, item.player) for item in items))
        cached = self._state_cache.get(key)
        if cached is not None:
            return cached
        state = CollectionState(self.world)
        for item in items:
            item.advancement = True
            state.collect(item)
        state.sweep_for_events()
        self._state_cache[key] = state
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
