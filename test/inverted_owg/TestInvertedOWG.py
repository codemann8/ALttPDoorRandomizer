from test.TestBase import TestBase, make_logic_test_world


class TestInvertedOWG(TestBase):
    def setUp(self):
        self.world = make_logic_test_world(logic='owglitches', mode='inverted')
