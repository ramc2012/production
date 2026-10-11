"""Plain-Python views of engine tables, for databases and JSON APIs."""
from __future__ import annotations

import json
from typing import Dict, List

import numpy as np
import pandas as pd


def frame_records(df) -> List[dict]:
    """DataFrame -> list of dicts with JSON-safe values (NaN/inf -> None, timestamps -> ISO)."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []
    out = df.replace([np.inf, -np.inf], np.nan)
    return json.loads(out.to_json(orient="records", date_format="iso", double_precision=15, default_handler=str))


def records_frame(records: List[dict]) -> pd.DataFrame:
    return pd.DataFrame(records)


def tables_records(tables: Dict[str, pd.DataFrame]) -> Dict[str, List[dict]]:
    return {k: frame_records(v) for k, v in tables.items()}
