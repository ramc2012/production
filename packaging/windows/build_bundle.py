"""Build the offline Windows installation bundle for FlowTwin.

Run on any machine with internet access (macOS, Linux or Windows) and Node.js 18+:

    python packaging/windows/build_bundle.py                 # -> dist/FlowTwin-<version>-win64.zip

The bundle installs on a Windows 10/11 or Server PC with NO internet, NO Docker and NO Python:

    wheels/                 FlowTwin + every dependency as Windows (win_amd64, CPython 3.12) wheels
    python-installer.exe    official python.org 3.12 installer, installed privately for FlowTwin
    WinSW-x64.exe           Windows service wrapper (MIT licence, github.com/winsw/winsw)
    sample/                 the synthetic demo GGS (optional "Load sample GGS" button)
    install.ps1, uninstall.ps1, flowtwin-service.xml, README.txt

Pass --python-installer / --winsw to use files you already have instead of downloading them,
or --no-binaries to build only the wheels (for an upgrade bundle).
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PY_VERSION = "3.12"
PY_INSTALLER_URL = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
WINSW_URL = "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe"
# Windows-only dependencies: pip evaluates environment markers for the build machine, not the
# target, so these are listed explicitly (tzdata: pandas/APScheduler time zones; colorama: click).
WINDOWS_EXTRAS = ["tzdata", "colorama"]


def run(*cmd, cwd=ROOT):
    print("+", " ".join(map(str, cmd)), flush=True)
    subprocess.check_call([str(c) for c in cmd], cwd=cwd)


def fetch(url: str, dest: Path) -> None:
    print(f"downloading {url}", flush=True)
    with urllib.request.urlopen(url, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "dist"))
    ap.add_argument("--python-installer", help="use this python-3.12.x-amd64.exe instead of downloading")
    ap.add_argument("--winsw", help="use this WinSW-x64.exe instead of downloading")
    ap.add_argument("--no-binaries", action="store_true", help="wheels only (upgrade bundle)")
    ap.add_argument("--skip-frontend", action="store_true", help="use the existing app/backend/static build")
    a = ap.parse_args()

    sys.path.insert(0, str(ROOT))
    import flowval
    version = flowval.__version__
    stage = Path(a.out) / f"FlowTwin-{version}-win64"
    if stage.exists():
        shutil.rmtree(stage)
    wheels = stage / "wheels"
    wheels.mkdir(parents=True)

    # 1. frontend into app/backend/static (packaged inside the wheel)
    if not a.skip_frontend:
        npm = shutil.which("npm") or shutil.which("npm.cmd")
        if not npm:
            print("npm not found: install Node.js 18+ or pass --skip-frontend")
            return 1
        run(npm, "ci", cwd=ROOT / "app" / "frontend")
        run(npm, "run", "build", cwd=ROOT / "app" / "frontend")
    if not (ROOT / "app" / "backend" / "static" / "index.html").exists():
        print("frontend build missing (app/backend/static/index.html)")
        return 1

    # 2. FlowTwin wheel + all dependencies as Windows wheels
    run(sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "-w", wheels)
    deps = _app_requirements() + WINDOWS_EXTRAS
    run(sys.executable, "-m", "pip", "download", "--only-binary=:all:", "--platform", "win_amd64",
        "--python-version", PY_VERSION, "--implementation", "cp", "-d", wheels, *deps)

    # 3. installer scripts, service definition, sample data
    for f in ("install.ps1", "uninstall.ps1", "flowtwin-service.xml", "README.txt"):
        shutil.copy(HERE / f, stage / f)
    shutil.copytree(ROOT / "data" / "sample", stage / "sample",
                    ignore=shutil.ignore_patterns("_truth_*", "__pycache__"))

    # 4. Python installer and WinSW
    if not a.no_binaries:
        if a.python_installer:
            shutil.copy(a.python_installer, stage / "python-installer.exe")
        else:
            fetch(PY_INSTALLER_URL, stage / "python-installer.exe")
        if a.winsw:
            shutil.copy(a.winsw, stage / "WinSW-x64.exe")
        else:
            fetch(WINSW_URL, stage / "WinSW-x64.exe")

    # 5. checksums and zip
    lines = []
    for p in sorted(stage.rglob("*")):
        if p.is_file():
            lines.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(stage).as_posix()}")
    (stage / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    zpath = stage.parent / f"{stage.name}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                z.write(p, Path(stage.name) / p.relative_to(stage))
    print(f"\nbundle: {zpath} ({zpath.stat().st_size / 1e6:.0f} MB), {len(list(wheels.iterdir()))} wheels")
    return 0


def _app_requirements():
    try:
        import tomllib
    except ModuleNotFoundError:          # Python 3.10
        import tomli as tomllib          # type: ignore
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return list(meta["dependencies"]) + list(meta["optional-dependencies"]["app"])


if __name__ == "__main__":
    sys.exit(main())
