import unittest

from invoice import total_cents


class HiddenTest(unittest.TestCase):
    def test_cent_prices_are_exact(self):
        for price, cents in ((19.99, 1999), (0.29, 29), (4.35, 435), (1.15, 115), (0.57, 57)):
            self.assertEqual(total_cents([(price, 3)]), cents * 3, price)

    def test_existing_behaviour(self):
        self.assertEqual(total_cents([(2.0, 3), (5.0, 1)]), 1100)
        self.assertEqual(total_cents([(1.0, 1)], discount_percent=15), 85)
        self.assertEqual(total_cents([(19.99, 1)], discount_percent=10), 1800)


if __name__ == "__main__":
    unittest.main()
