import glob
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SizeTest(unittest.TestCase):
    def test_code_stays_small(self):
        total = 0
        for path in glob.glob(os.path.join(ROOT, "ledger", "**", "*.py"), recursive=True):
            with open(path, encoding="utf-8") as f:
                total += sum(1 for _ in f)
        self.assertLessEqual(total, 2400)


if __name__ == "__main__":
    unittest.main()
