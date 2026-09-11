from test.TestBase import TestBase, make_logic_test_world


class TestVanilla(TestBase):
    def setUp(self):
        self.world = make_logic_test_world(logic='noglitches', mode='open')
