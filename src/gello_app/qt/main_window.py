"""PyQt5 desktop shell for Gello Host."""

from __future__ import annotations

import json
import threading
import webbrowser
from typing import Optional

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gello_app.paths import APP_TITLE
from gello_app.service import GelloService
from gello_app.web.server import create_app


class MainWindow(QMainWindow):
    def __init__(self, service: GelloService) -> None:
        super().__init__()
        self.service = service
        self.setWindowTitle(APP_TITLE)
        self.resize(720, 560)
        self._web_thread: Optional[threading.Thread] = None
        self._web_started = False

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        title = QLabel("Gello Host — Dynamixel leader 单测")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(title)

        row = QHBoxLayout()
        self.dry = QCheckBox("dry_run")
        self.dry.setChecked(bool(service.cfg.dry_run))
        row.addWidget(self.dry)
        self.btn_connect = QPushButton("连接")
        self.btn_disconnect = QPushButton("断开")
        self.btn_probe = QPushButton("Probe")
        self.btn_rate = QPushButton("测速 3s")
        self.btn_web = QPushButton("启动 Web UI")
        self.btn_browser = QPushButton("打开浏览器")
        for b in (
            self.btn_connect,
            self.btn_disconnect,
            self.btn_probe,
            self.btn_rate,
            self.btn_web,
            self.btn_browser,
        ):
            row.addWidget(b)
        row.addStretch(1)
        layout.addLayout(row)

        self.meta = QLabel("idle")
        layout.addWidget(self.meta)

        self.view = QTextEdit()
        self.view.setReadOnly(True)
        self.view.setStyleSheet("font-family: Consolas, monospace; font-size: 12px;")
        layout.addWidget(self.view, 1)

        self.btn_connect.clicked.connect(self.on_connect)
        self.btn_disconnect.clicked.connect(self.on_disconnect)
        self.btn_probe.clicked.connect(self.on_probe)
        self.btn_rate.clicked.connect(self.on_rate)
        self.btn_web.clicked.connect(self.on_start_web)
        self.btn_browser.clicked.connect(self.on_open_browser)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(200)

    def on_connect(self) -> None:
        try:
            out = self.service.connect(dry_run=self.dry.isChecked())
            self.meta.setText(out.get("message") or "connected")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Connect failed", str(e))

    def on_disconnect(self) -> None:
        out = self.service.disconnect()
        self.meta.setText(out.get("message") or "disconnected")

    def on_probe(self) -> None:
        try:
            out = self.service.probe()
            QMessageBox.information(self, "Probe", json.dumps(out, indent=2, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Probe failed", str(e))

    def on_rate(self) -> None:
        out = self.service.rate_probe(3.0)
        if not out.get("ok"):
            QMessageBox.warning(self, "Rate probe", out.get("error") or "failed")
            return
        QMessageBox.information(self, "Rate probe", json.dumps(out, indent=2, ensure_ascii=False))

    def on_start_web(self) -> None:
        if self._web_started:
            QMessageBox.information(self, "Web", "Web UI already running")
            return
        host = self.service.cfg.web_host
        port = int(self.service.cfg.web_port)

        def _run() -> None:
            import uvicorn

            uvicorn.run(create_app(self.service), host=host, port=port, log_level="warning")

        self._web_thread = threading.Thread(target=_run, name="gello-web", daemon=True)
        self._web_thread.start()
        self._web_started = True
        self.meta.setText(f"web http://{host}:{port}/")

    def on_open_browser(self) -> None:
        host = self.service.cfg.web_host
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
        webbrowser.open(f"http://{host}:{int(self.service.cfg.web_port)}/")

    def refresh(self) -> None:
        s = self.service.status()
        self.dry.setChecked(bool(s.get("dry_run")))
        lines = [
            f"connected={s.get('connected')}  dry_run={s.get('dry_run')}  synth={s.get('synth')}",
            f"hz={s.get('hz')}  port={s.get('port')}  seq={s.get('seq')}  err={s.get('error')}",
            "",
            "joints_deg (cal):",
            "  " + ", ".join(f"{float(x):+.2f}" for x in (s.get("joints_deg") or [])),
            "joints_deg_raw:",
            "  " + ", ".join(f"{float(x):+.2f}" for x in (s.get("joints_deg_raw") or [])),
            "",
            "affine offsets:",
            "  " + ", ".join(f"{float(x):+.4f}" for x in (s.get("joint_offsets") or [])),
            "affine signs:",
            "  " + ", ".join(str(x) for x in (s.get("joint_signs") or [])),
        ]
        self.view.setPlainText("\n".join(lines))
        if s.get("connected"):
            self.meta.setText(
                f"live · {float(s.get('hz') or 0):.1f} Hz · "
                f"{'dry_run' if s.get('dry_run') else 'hardware'}"
            )
