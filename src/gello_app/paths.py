"""Path helpers — prefer workspace ``./sensors`` symlink."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_ID = "gello-host"
APP_TITLE = "Gello Host"


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def package_root() -> Path:
    return Path(__file__).resolve().parent


def resolve_config_path(path: str | Path, *, cwd: Path | None = None) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = (cwd or Path.cwd()) / p
    return p.resolve()


def sensors_src_dir() -> Path:
    root = project_root()
    for candidate in (
        root / "sensors" / "src",
        root.parent / "sensors" / "src",
    ):
        if (candidate / "sensors").is_dir():
            return candidate
    return root / "sensors" / "src"


def ensure_sensors_import() -> Path:
    src = sensors_src_dir()
    if src.is_dir():
        s = str(src)
        if s in sys.path:
            sys.path.remove(s)
        sys.path.insert(0, s)
    return src


def static_dir() -> Path:
    return package_root() / "web" / "static"


def user_data_dir() -> Path:
    override = os.environ.get("GELLO_HOST_USER_DATA")
    if override:
        p = Path(override).expanduser().resolve()
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        p = base / APP_ID
    else:
        p = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / APP_ID
    p.mkdir(parents=True, exist_ok=True)
    return p
