from test.TestBase import TestBase, make_logic_test_world


class TestInverted(TestBase):
    def setUp(self):
        self.world = make_logic_test_world(logic='noglitches', mode='inverted')
