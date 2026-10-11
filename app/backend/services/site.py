"""Network model and configuration, versioned: every save creates a new version, validated by
building it with the engine first. Also loads the synthetic sample GGS."""
from __future__ import annotations

import io
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import yaml
from fastapi import HTTPException
from sqlalchemy.orm import Session

from flowval.config import DEFAULTS, deep_merge
from flowval.io import SCHEMAS
from flowval.network import network_from_dict

from .. import engine_adapter as A
from ..models import ConfigVersion, NetworkVersion
from ..security import audit
from . import importer

ROOT = Path(__file__).resolve().parents[3]
SAMPLE = ROOT / "data" / "sample"


def save_network(db: Session, site_id: int, yaml_text: str, well_static_csv: Optional[str],
                 strapping: Dict[str, str], user: str, comment: str = "") -> NetworkVersion:
    try:
        cfg = yaml.safe_load(yaml_text) or {}
        if not isinstance(cfg, dict):
            raise ValueError("network.yaml must be a mapping")
        static = pd.read_csv(io.StringIO(well_static_csv)) if well_static_csv else None
        strap = {}
        for tid, txt in strapping.items():
            df = pd.read_csv(io.StringIO(txt))
            if not {"level_mm", "volume_m3"} <= set(df.columns):
                raise ValueError(f"strapping table for {tid} needs columns level_mm, volume_m3")
            if len(df) < 2 or not df.sort_values("level_mm")["volume_m3"].is_monotonic_increasing:
                raise ValueError(f"strapping table for {tid}: volume must increase with level (≥ 2 points)")
            strap[tid] = df
        net = network_from_dict(cfg, static, strap)
    except (ValueError, KeyError, TypeError, yaml.YAMLError, pd.errors.ParserError) as e:
        raise HTTPException(400, f"Network model not saved: {e}")
    prev = A.current_network(db, site_id)
    nv = NetworkVersion(site_id=site_id, yaml_text=yaml_text, well_static_csv=well_static_csv or None,
                        strapping_csv=dict(strapping), hash=A.text_hash(yaml_text, well_static_csv, strapping),
                        comment=comment, created_by=user)
    db.add(nv)
    db.flush()
    audit(db, user, "network.save", f"network_version:{nv.id}",
          {"version": prev.id} if prev else None,
          {"version": nv.id, "wells": len(net.wells), "separators": len(net.separators), "meters": len(net.meters),
           "tanks": len(net.tanks), "comment": comment})
    return nv


def validate_config(yaml_text: str) -> dict:
    try:
        user = yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError as e:
        raise HTTPException(400, f"YAML error: {e}")
    if not isinstance(user, dict):
        raise HTTPException(400, "config.yaml must be a mapping")
    problems: List[str] = []

    def walk(u, d, path):
        for k, v in u.items():
            p = f"{path}.{k}" if path else k
            if k not in d:
                problems.append(f"unknown key '{p}'")
            elif isinstance(d[k], dict):
                if not isinstance(v, dict):
                    problems.append(f"'{p}' must be a section")
                else:
                    walk(v, d[k], p)
            elif isinstance(d[k], (int, float)) and not isinstance(d[k], bool):
                if not isinstance(v, (int, float)) or isinstance(v, bool):
                    problems.append(f"'{p}' must be a number")
            elif isinstance(d[k], list) and not isinstance(v, list):
                problems.append(f"'{p}' must be a list")
    walk(user, DEFAULTS, "")
    if problems:
        raise HTTPException(400, "Configuration not saved: " + "; ".join(problems[:12]))
    return deep_merge(DEFAULTS, user)


def save_config(db: Session, site_id: int, yaml_text: str, user: str, comment: str = "") -> ConfigVersion:
    cfg = validate_config(yaml_text)
    prev = A.current_config(db, site_id)
    cv = ConfigVersion(site_id=site_id, yaml_text=yaml_text, hash=A.text_hash(cfg), comment=comment, created_by=user)
    db.add(cv)
    db.flush()
    audit(db, user, "config.save", f"config_version:{cv.id}", {"version": prev.id} if prev else None,
          {"version": cv.id, "hash": cv.hash, "comment": comment})
    return cv


def sample_folder() -> Path:
    from ..settings import get_settings
    f = get_settings().sample_dir or SAMPLE
    if not (Path(f) / "network.yaml").exists():
        raise HTTPException(404, f"Sample data not found in {f} (set FLOWTWIN_SAMPLE_DIR)")
    return Path(f)


def seed_sample(db: Session, site_id: int, user: str = "seed", folder: Optional[Path] = None) -> dict:
    """Load the synthetic GGS: network, config and every input CSV as one committed batch."""
    folder = folder or sample_folder()
    ny = (folder / "network.yaml").read_text(encoding="utf-8")
    cfg = yaml.safe_load(ny)
    static_f = folder / cfg.get("well_static", "well_static.csv")
    strap = {t["id"]: (folder / t["strapping_table"]).read_text(encoding="utf-8") for t in cfg.get("tanks", [])}
    nv = save_network(db, site_id, ny, static_f.read_text(encoding="utf-8") if static_f.exists() else None,
                      strap, user, "sample GGS")
    cv = save_config(db, site_id, (folder / "config.yaml").read_text(encoding="utf-8"), user, "sample GGS")
    files = [(f"{ds}.csv", (folder / f"{ds}.csv").read_bytes()) for ds in SCHEMAS if (folder / f"{ds}.csv").exists()]
    b = importer.import_files(db, site_id, files, user, source="seed")
    if b.status != "preview":
        raise RuntimeError(f"sample import failed: {b.validation_report}")
    b.status = "committed"
    audit(db, user, "data.commit", f"batch:{b.id}", None, {"rows": b.rows, "source": "seed"})
    db.commit()
    return dict(network_version=nv.id, config_version=cv.id, batch=b.id, rows=b.rows)
