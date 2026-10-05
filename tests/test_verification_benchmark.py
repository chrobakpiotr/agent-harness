import os
import pathlib
import sys
import unittest

BENCHMARKS = pathlib.Path(__file__).resolve().parents[1] / 'benchmarks'
sys.path.insert(0, str(BENCHMARKS))
from verification_planning import run_benchmark


# Limits are calibrated for CI runners; slower hosts (Showcase's own code also misses them on a 1.4 GHz i5) skip.
@unittest.skipUnless(os.environ.get("AGENT_HARNESS_BENCHMARK") == "1", "CI only: set AGENT_HARNESS_BENCHMARK=1")
class VerificationBenchmarkTest(unittest.TestCase):
    def test_representative_planning_benchmark_stays_within_contract(self):
        result = run_benchmark()
        self.assertEqual({'gates': 100, 'files': 5000, 'bytes_per_file': 256,
                          'disjoint_matches_per_gate': 50, 'bytes_total': 1280000}, result['fixture'])
        self.assertEqual(3, result['runs'])
        self.assertLessEqual(result['median_seconds'], 10)
        self.assertLessEqual(result['maximum_seconds'], 20)
        self.assertLessEqual(result['git_subprocess_calls_per_run'], 12)


if __name__ == '__main__':
    unittest.main()
