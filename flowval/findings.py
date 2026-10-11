"""Exception register shared by every validation layer."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import List

import pandas as pd

SEVERITY_ORDER = {"ALARM": 3, "WARN": 2, "INFO": 1, "OK": 0}

LAYERS = ("data_quality", "well_test", "well_daily", "separator", "tank",
          "reconciliation", "allocation")


@dataclass
class Finding:
    date: str            # production day (YYYY-MM-DD) or test date
    layer: str           # one of LAYERS
    severity: str        # ALARM / WARN / INFO
    object_type: str     # well / separator / meter / tank / installation
    object_id: str
    check: str           # short machine-readable check name
    message: str
    value: float | None = None
    limit: str | None = None
    action: str = ""


@dataclass
class FindingLog:
    items: List[Finding] = field(default_factory=list)

    def add(self, *args, **kwargs) -> None:
        self.items.append(Finding(*args, **kwargs))

    def extend(self, other: "FindingLog") -> None:
        self.items.extend(other.items)

    def to_frame(self) -> pd.DataFrame:
        cols = list(Finding.__dataclass_fields__.keys())
        if not self.items:
            return pd.DataFrame(columns=cols + ["rank"])
        df = pd.DataFrame([asdict(f) for f in self.items])
        df["rank"] = df["severity"].map(SEVERITY_ORDER)
        return df.sort_values(["rank", "date", "layer", "object_id"],
                              ascending=[False, True, True, True]).reset_index(drop=True)


def worst(severities) -> str:
    best, score = "OK", 0
    for s in severities:
        if SEVERITY_ORDER.get(s, 0) > score:
            best, score = s, SEVERITY_ORDER[s]
    return best
