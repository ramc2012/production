"""Command line: python -m flowval run --network ... --data ... [--config ...] [--out ...]"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .engine import run
from .report_excel import write_excel
from .report_html import build_html


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="flowval", description="Well-to-tank flow validation & reconciliation")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="validate a period and write reports")
    r.add_argument("--network", required=True, help="network.yaml")
    r.add_argument("--data", required=True, help="folder with the input CSVs")
    r.add_argument("--config", help="config.yaml with tolerances (optional)")
    r.add_argument("--start", help="first production day YYYY-MM-DD")
    r.add_argument("--end", help="last production day YYYY-MM-DD")
    r.add_argument("--date", help="validate a single day (= --start X --end X)")
    r.add_argument("--out", default="output", help="output folder")
    tw = sub.add_parser("twin", help="build the digital twin (levels 0-5) and write the twin dashboard")
    tw.add_argument("--network", required=True)
    tw.add_argument("--data", required=True)
    tw.add_argument("--config")
    tw.add_argument("--start")
    tw.add_argument("--end")
    tw.add_argument("--mode", choices=["advisory", "supervised", "autonomous_sim"],
                    help="Level-5 loop mode (default from config)")
    tw.add_argument("--out", default="output")
    a = p.parse_args(argv)
    if a.cmd == "twin":
        from .twin.run import run_twin
        ctx = run_twin(a.network, a.data, a.config, a.out, a.start, a.end, a.mode)
        lv = ctx["levels"]
        print(f"{ctx['twin'].name}: digital twin as of {ctx['twin'].date} ({ctx['runtime_s']:.0f}s)")
        for _, r in lv.iterrows():
            print(f"  L{r['level']} {r['name']:<12} {r['status']:<13} {r['met']}/{r['total']} criteria")
        pl = ctx["l4"]["plan"]["totals"]["oil"]
        print(f"  L4 recommendations: {pl[1] - pl[0]:+.1f} m3/d oil predicted")
        print(f"  L5 ({ctx['l5']['mode']}, simulator): {ctx['l5']['gain']:+.1f} m3/d on the plant")
        print(f"  -> {ctx['out']}")
        return 0

    if a.cmd == "run":
        start, end = (a.date, a.date) if a.date else (a.start, a.end)
        t0 = time.time()
        res = run(a.network, a.data, a.config, start, end)
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        tag = f"{res.dates[0]}_{res.dates[-1]}"
        html_p = out / f"flow_validation_{tag}.html"
        html_p.write_text(build_html(res), encoding="utf-8")
        xl = write_excel(res, out / f"flow_validation_{tag}.xlsx")
        res.findings.to_csv(out / f"findings_{tag}.csv", index=False)
        res.wells.to_csv(out / f"well_allocation_{tag}.csv", index=False)
        f = res.findings
        print(f"{res.network.name}: {len(res.dates)} days {res.dates[0]}..{res.dates[-1]} in {time.time() - t0:.1f}s")
        print(f"  register: {(f.severity == 'ALARM').sum()} alarms, {(f.severity == 'WARN').sum()} warnings")
        print(f"  DVR global test passed on {int(res.dvr_days.passed_initial.sum())}/{len(res.dvr_days)} days")
        for _, x in f[f.severity == "ALARM"].head(12).iterrows():
            print(f"  ALARM {x['first_date']} {x['object_id']:<10} {x['check']:<16} {x['message'][:110]}")
        print(f"  -> {html_p}\n  -> {xl}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
