from test.TestBase import TestBase, make_logic_test_world


class TestVanillaOWG(TestBase):
    def setUp(self):
        self.world = make_logic_test_world(logic='owglitches', mode='open')
