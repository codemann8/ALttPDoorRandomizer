import unittest

from BaseClasses import CollectionState, CrystalBarrier
from Items import ItemFactory
from test.TestBase import make_logic_test_world


class TestDungeon(unittest.TestCase):
    def setUp(self):
        self.world = make_logic_test_world(logic='noglitches', mode='open')
        self.starting_regions = []

    def run_tests(self, access_pool):
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
                state = CollectionState(self.world)
                for item in items:
                    item.advancement = True
                    state.collect(item)
                # Region.can_reach now consults reachable_regions only;
                # this is the old can_reach_private = True override.
                for name in self.starting_regions:
                    region = self.world.get_region(name, 1)
                    state.reachable_regions[1][region] = CrystalBarrier.Orange
                    for conn in region.exits:
                        state.blocked_connections[1][conn] = CrystalBarrier.Orange
                state.stale[1] = True

                self.assertEqual(self.world.get_location(location, 1).can_reach(state), access)
