"""Excel workbook with every intermediate table (for audit and pivoting)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def write_excel(res, path: str | Path) -> Path:
    path = Path(path)
    sheets = {
        "Summary_daily": res.summary,
        "Findings": res.findings.drop(columns=["rank"], errors="ignore"),
        "Findings_daily": res.findings_daily.drop(columns=["rank"], errors="ignore"),
        "Well_tests": res.tests,
        "Well_daily": res.wells.drop(columns=["conflicts"], errors="ignore"),
        "Separators": res.separators,
        "Separator_AF": res.separator_af,
        "Tanks": res.tanks,
        "DVR_days": res.dvr_days,
        "DVR_variables": res.dvr_vars,
        "DVR_constraints": res.dvr_constraints,
        "Bias_suspects": res.bias_suspects,
        "Well_performance": res.well_perf,
    }
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, df in sheets.items():
            df = df if df is not None else pd.DataFrame()
            df.to_excel(xw, sheet_name=name, index=False)
            ws = xw.sheets[name]
            ws.freeze_panes = "A2"
            for i, col in enumerate(df.columns, start=1):
                width = min(max(len(str(col)), *(len(str(v)) for v in df[col].head(200).tolist())) + 2, 60) \
                    if len(df) else len(str(col)) + 2
                ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width
            ws.auto_filter.ref = ws.dimensions
    return path
