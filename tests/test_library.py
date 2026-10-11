"""M1: the engine as a library - in-memory entry points must reproduce the file-based ones."""
import math
import unittest
from pathlib import Path

import pandas as pd

from flowval import run, run_frames, run_window
from flowval.config import config_hash, load_config
from flowval.io import load_inputs, prepare_inputs, read_csv_folder
from flowval.network import load_network, network_from_dict

ROOT = Path(__file__).resolve().parents[1]
S = ROOT / "data" / "sample"
# Findings that describe the whole run period rather than one day (stamped on the last day).
# Spike messages quote a centred 7-day median, so their wording (not their verdict) can differ
# between a daily run (no future days) and a month-end run; compare verdicts only.
PERIOD_CHECKS = {"proving_overdue", "systematic_bias"}


def _keys(f: pd.DataFrame, day: str):
    f = f[(f["date"] == day) & ~f["check"].isin(PERIOD_CHECKS)]
    return sorted(map(tuple, f[["object_id", "check", "severity"]].values))


class TestLibrary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.net = load_network(S / "network.yaml")
        cls.cfg = load_config(S / "config.yaml")
        cls.data = load_inputs(S)
        cls.full = run_frames(cls.net, cls.data, cls.cfg)

    def test_run_frames_equals_run(self):
        a = run(S / "network.yaml", S, S / "config.yaml")
        pd.testing.assert_frame_equal(a.findings_daily, self.full.findings_daily)
        pd.testing.assert_frame_equal(a.wells, self.full.wells)

    def test_prepare_inputs_from_raw_frames(self):
        d2 = prepare_inputs(read_csv_folder(S))
        for k in self.data:
            pd.testing.assert_frame_equal(d2[k], self.data[k])

    def test_network_from_dict(self):
        import yaml
        cfg = yaml.safe_load((S / "network.yaml").read_text())
        static = pd.read_csv(S / "well_static.csv")
        strap = {t["id"]: pd.read_csv(S / t["strapping_table"]) for t in cfg["tanks"]}
        net = network_from_dict(cfg, static, strap)
        self.assertEqual(sorted(net.wells), sorted(self.net.wells))
        self.assertEqual(net.wells["ANK-101"].perf_depth_m, self.net.wells["ANK-101"].perf_depth_m)
        self.assertAlmostEqual(net.tanks["T-1"].volume(5000), self.net.tanks["T-1"].volume(5000))

    def test_window_matches_full_run(self):
        for day in ("2026-09-26", "2026-09-29"):
            w = run_window(self.net, self.data, self.cfg, day, history_days=35)
            self.assertEqual(w.dates[-1], day)
            a = self.full.wells[self.full.wells["date"] == day].set_index("well_id").sort_index()
            b = w.wells[w.wells["date"] == day].set_index("well_id").sort_index()
            for c in ("theo_oil", "prop_oil", "dvr_oil", "dvr_water", "dvr_gas", "vfm_comb"):
                if c in a:
                    pd.testing.assert_series_equal(a[c], b[c], check_names=False, atol=1e-9)
            self.assertEqual(_keys(self.full.findings_daily, day), _keys(w.findings_daily, day))

    def test_to_records_json_safe(self):
        recs = self.full.to_records()
        self.assertEqual(set(recs), set(self.full.TABLES))
        self.assertEqual(len(recs["wells"]), len(self.full.wells))
        for row in recs["wells"][:50]:
            for v in row.values():
                self.assertFalse(isinstance(v, float) and (math.isnan(v) or math.isinf(v)))
        # precision kept to well below the 0.01 m3 allocation requirement
        self.assertAlmostEqual(sum(r["dvr_oil"] or 0 for r in recs["wells"]),
                               float(self.full.wells["dvr_oil"].sum()), places=6)

    def test_config_hash(self):
        self.assertEqual(config_hash(self.cfg), config_hash(load_config(S / "config.yaml")))
        other = load_config(S / "config.yaml")
        other["dvr"]["alpha"] = 0.01
        self.assertNotEqual(config_hash(self.cfg), config_hash(other))

    def test_test_override(self):
        t = self.full.tests
        rej = t[t["status"] == "REJECTED"].iloc[0]
        ov = {rej["test_id"]: {"status": "ACCEPTED", "reason": "unit test"}}
        r = run_frames(self.net, self.data, self.cfg, test_overrides=ov)
        row = r.tests.set_index("test_id").loc[rej["test_id"]]
        self.assertEqual(row["status"], "ACCEPTED")
        self.assertEqual(row["engine_status"], "REJECTED")
        self.assertTrue((r.findings_daily["check"] == "test_override").any())


if __name__ == "__main__":
    unittest.main()
