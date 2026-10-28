"""PyQt entrypoint."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PyQt5.QtWidgets import QApplication

from gello_app.paths import project_root
from gello_app.qt.main_window import MainWindow
from gello_app.service import GelloService, load_host_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gello-desktop", description="Gello PyQt host")
    parser.add_argument(
        "-c",
        "--config",
        default="configs/default.yaml",
        help="host YAML (default: configs/default.yaml)",
    )
    parser.add_argument("--dry-run", action="store_true", default=None)
    parser.add_argument("--no-dry-run", action="store_true")
    args = parser.parse_args(argv)

    cwd = Path.cwd()
    # allow running from repo root or gello/
    cfg_path = Path(args.config)
    if not cfg_path.is_file():
        alt = project_root() / args.config
        if alt.is_file():
            cfg_path = alt
            cwd = project_root()

    cfg = load_host_config(cfg_path, cwd=cwd)
    if args.no_dry_run:
        cfg.dry_run = False
    elif args.dry_run:
        cfg.dry_run = True

    service = GelloService(cfg)
    app = QApplication(sys.argv)
    win = MainWindow(service)
    win.show()
    return int(app.exec_() if hasattr(app, "exec_") else app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
