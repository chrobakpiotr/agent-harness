import unittest

from invoice import total_cents


class TotalTest(unittest.TestCase):
    def test_whole_prices(self):
        self.assertEqual(total_cents([(2.0, 3), (5.0, 1)]), 1100)

    def test_discount_rounds_down(self):
        self.assertEqual(total_cents([(1.0, 1)], discount_percent=15), 85)


if __name__ == "__main__":
    unittest.main()
