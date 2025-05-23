import unittest
from decision_maker import DecisionMaker

class TestDecisionMaker(unittest.TestCase):

    def setUp(self):
        self.decision_maker = DecisionMaker()

    def test_initialization(self):
        self.assertIsInstance(self.decision_maker, DecisionMaker)

    def test_make_decision(self):
        # Assuming make_decision method exists that returns a decision
        decision = self.decision_maker.make_decision({"price": 10000})
        self.assertIn(decision, ['buy', 'sell', 'hold'])

if __name__ == '__main__':
    unittest.main()