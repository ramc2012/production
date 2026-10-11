"""PRD F2: a scheduled run (validation + twin L0-L5) for >= 50 wells / 30 days in < 60 s.

Opt-in because it takes about a minute: FLOWTWIN_PERF=1 python -m unittest tests.test_performance
(or python scripts/dev.py perf). FLOWTWIN_PERF_LIMIT_S changes the limit for slower machines.
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


@unittest.skipUnless(os.environ.get("FLOWTWIN_PERF"), "set FLOWTWIN_PERF=1 to run the timing test")
class TestScheduledRunTiming(unittest.TestCase):
    def test_50_wells_30_days_under_limit(self):
        from make_scaled_sample import build
        from flowval.config import load_config
        from flowval.io import load_inputs
        from flowval.network import load_network
        from flowval.twin.run import run_twin_frames
        with tempfile.TemporaryDirectory() as d:
            out = build(5, Path(d))                       # 60 wells
            net = load_network(out / "network.yaml")
            data, cfg = load_inputs(out), load_config(out / "config.yaml")
            t0 = time.time()
            ctx = run_twin_frames(net, data, cfg)
            dt = time.time() - t0
        limit = float(os.environ.get("FLOWTWIN_PERF_LIMIT_S", 60))
        print(f"\n{len(net.wells)} wells, {len(ctx['res'].dates)} days: {dt:.1f} s (limit {limit:.0f} s)")
        self.assertGreaterEqual(len(net.wells), 50)
        self.assertEqual(len(ctx["res"].dates), 30)
        self.assertLess(dt, limit)


if __name__ == "__main__":
    unittest.main()
