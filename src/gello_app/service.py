"""Gello sampling service on top of hik-sensors ``kind=gello``."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

import yaml

from gello_app.affine import apply_gello_affine, invert_gello_affine
from gello_app.paths import ensure_sensors_import, resolve_config_path

ensure_sensors_import()

from sensors.runtime.manager import SensorManager  # noqa: E402


HALF_PI = math.pi / 2.0


@dataclass
class HostConfig:
    host_config: Path
    sensors_config: Path
    dry_run: bool = True
    sensor_id: str = "gello-leader"
    sample_hz: float = 50.0
    web_host: str = "127.0.0.1"
    web_port: int = 7020


def load_host_config(path: str | Path, *, cwd: Path | None = None) -> HostConfig:
    p = resolve_config_path(path, cwd=cwd)
    with p.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"host config must be a mapping: {p}")
    sc = raw.get("sensors_config") or "./sensors_gello.yaml"
    sc_path = resolve_config_path(sc, cwd=p.parent)
    rt = raw.get("runtime") if isinstance(raw.get("runtime"), dict) else {}
    dry = raw.get("dry_run")
    if dry is None:
        dry = True
    return HostConfig(
        host_config=p.resolve(),
        sensors_config=sc_path,
        dry_run=bool(dry),
        sensor_id=str(raw.get("sensor_id") or "gello-leader"),
        sample_hz=float(rt.get("sample_hz") or 50.0),
        web_host=str(rt.get("web_host") or "127.0.0.1"),
        web_port=int(rt.get("web_port") or 7020),
    )


# Arm DOFs (J1–J6) keep discrete polarity / π/2 offsets; J7 (gripper) is a free float
# affine that can map Dynamixel rad → Huiling stroke mm.
_ARM_DOF = 6


def _normalize_signs(values: Sequence[Any], n: int) -> list[float]:
    """J1–J6 → ±1; J7+ keep arbitrary non-zero float (e.g. rad→mm scale)."""
    out: list[float] = []
    for i in range(n):
        if i < len(values):
            s = float(values[i])
            if i < _ARM_DOF:
                out.append(1.0 if s >= 0.0 else -1.0)
            else:
                out.append(1.0 if s == 0.0 else s)
        else:
            out.append(1.0)
    return out


def _normalize_offsets(values: Sequence[Any], n: int) -> list[float]:
    """J1–J6 snap to nearest k·π/2; J7+ keep raw float (gripper mm mapping)."""
    out: list[float] = []
    for i in range(n):
        if i < len(values):
            v = float(values[i])
            if i < _ARM_DOF:
                k = int(round(v / HALF_PI))
                out.append(float(k) * HALF_PI)
            else:
                out.append(v)
        else:
            out.append(0.0)
    return out


@dataclass
class SampleSnapshot:
    ts: float = 0.0
    seq: int = 0
    connected: bool = False
    dry_run: bool = True
    synth: bool = False
    hz: float = 0.0
    port: str | None = None
    joint_ids: list[int] = field(default_factory=list)
    joints_rad: list[float] = field(default_factory=list)
    joints_rad_raw: list[float] = field(default_factory=list)
    joints_deg: list[float] = field(default_factory=list)
    joints_deg_raw: list[float] = field(default_factory=list)
    joint_offsets: list[float] = field(default_factory=list)
    joint_signs: list[float] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "seq": self.seq,
            "connected": self.connected,
            "dry_run": self.dry_run,
            "synth": self.synth,
            "hz": self.hz,
            "port": self.port,
            "joint_ids": list(self.joint_ids),
            "joints_rad": list(self.joints_rad),
            "joints_rad_raw": list(self.joints_rad_raw),
            "joints_deg": list(self.joints_deg),
            "joints_deg_raw": list(self.joints_deg_raw),
            "joint_offsets": list(self.joint_offsets),
            "joint_signs": list(self.joint_signs),
            "error": self.error,
        }


class GelloService:
    """Open / sample / close a single gello leader device."""

    def __init__(self, cfg: HostConfig, *, restart_argv: list[str] | None = None) -> None:
        self.cfg = cfg
        self.restart_argv = list(restart_argv) if restart_argv else None
        self._lock = threading.RLock()
        self._manager: SensorManager | None = None
        self._sensor = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._latest = SampleSnapshot(dry_run=cfg.dry_run)
        self._read_times: list[float] = []
        self._n_joints = 7
        # Runtime affine override (UI「下发」); None → use YAML / sensor attrs.
        self._override_offsets: list[float] | None = None
        self._override_signs: list[float] | None = None

    @property
    def latest(self) -> SampleSnapshot:
        with self._lock:
            d = self._latest.to_dict()
            return SampleSnapshot(
                ts=float(d["ts"]),
                seq=int(d["seq"]),
                connected=bool(d["connected"]),
                dry_run=bool(d["dry_run"]),
                synth=bool(d["synth"]),
                hz=float(d["hz"]),
                port=d.get("port"),
                joint_ids=list(d.get("joint_ids") or []),
                joints_rad=list(d.get("joints_rad") or []),
                joints_rad_raw=list(d.get("joints_rad_raw") or []),
                joints_deg=list(d.get("joints_deg") or []),
                joints_deg_raw=list(d.get("joints_deg_raw") or []),
                joint_offsets=list(d.get("joint_offsets") or []),
                joint_signs=list(d.get("joint_signs") or []),
                error=d.get("error"),
            )

    def status(self) -> dict[str, Any]:
        snap = self.latest
        aff = self.get_affine()
        return {
            "sensor_id": self.cfg.sensor_id,
            "host_config": str(self.cfg.host_config),
            "sensors_config": str(self.cfg.sensors_config),
            "sample_hz_target": self.cfg.sample_hz,
            "web_host": self.cfg.web_host,
            "web_port": self.cfg.web_port,
            "affine_override": bool(
                self._override_offsets is not None or self._override_signs is not None
            ),
            "affine": aff,
            **snap.to_dict(),
        }

    def _yaml_affine(self) -> tuple[list[int], list[float], list[float]]:
        """Read joint_ids / offsets / signs from sensors YAML (device then defaults)."""
        path = self.cfg.sensors_config
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        defaults = {}
        if isinstance(raw, dict):
            d = raw.get("defaults")
            if isinstance(d, dict) and isinstance(d.get("gello"), dict):
                defaults = d["gello"]
        params: dict[str, Any] = dict(defaults)
        if isinstance(raw, dict):
            for dev in raw.get("devices") or []:
                if not isinstance(dev, dict):
                    continue
                if str(dev.get("id") or "") != self.cfg.sensor_id:
                    continue
                p = dev.get("params") if isinstance(dev.get("params"), dict) else {}
                params.update(p)
                break
        ids = list(params.get("joint_ids") or list(range(1, self._n_joints + 1)))
        n = max(1, len(ids))
        offsets = _normalize_offsets(params.get("joint_offsets") or [], n)
        signs = _normalize_signs(params.get("joint_signs") or [], n)
        return [int(x) for x in ids[:n]], offsets, signs

    def get_affine(self) -> dict[str, Any]:
        ids, offsets, signs = self._calib_from_sensor()
        return {
            "joint_ids": ids,
            "joint_offsets": offsets,
            "joint_signs": signs,
            "offset_step_rad": HALF_PI,
            "override": bool(
                self._override_offsets is not None or self._override_signs is not None
            ),
        }

    def apply_affine(
        self,
        *,
        joint_signs: Sequence[Any] | None = None,
        joint_offsets: Sequence[Any] | None = None,
    ) -> dict[str, Any]:
        """Temporarily override affine in memory (does not write YAML)."""
        ids, cur_off, cur_sgn = self._calib_from_sensor()
        n = len(ids)
        if joint_signs is None:
            signs = list(cur_sgn)
        else:
            signs = _normalize_signs(joint_signs, n)
        if joint_offsets is None:
            offsets = list(cur_off)
        else:
            offsets = _normalize_offsets(joint_offsets, n)
        with self._lock:
            self._override_signs = signs
            self._override_offsets = offsets
            self._push_affine_to_sensor_unlocked(offsets, signs)
        return {"ok": True, "message": "affine applied (runtime)", **self.get_affine()}

    def sync_affine_to_yaml(self) -> dict[str, Any]:
        """Persist current affine into sensors_config YAML (defaults + device params)."""
        aff = self.get_affine()
        offsets = list(aff["joint_offsets"])
        signs = list(aff["joint_signs"])
        path = self.cfg.sensors_config
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        if not isinstance(raw, dict):
            return {"ok": False, "error": f"bad yaml: {path}"}
        defaults = raw.setdefault("defaults", {})
        if not isinstance(defaults, dict):
            defaults = {}
            raw["defaults"] = defaults
        gello_def = defaults.setdefault("gello", {})
        if not isinstance(gello_def, dict):
            gello_def = {}
            defaults["gello"] = gello_def
        gello_def["joint_offsets"] = [float(x) for x in offsets]
        # J7 may be a non-integer scale (rad → mm); keep floats for all.
        gello_def["joint_signs"] = [float(x) for x in signs]
        devices = raw.get("devices")
        if not isinstance(devices, list):
            devices = []
            raw["devices"] = devices
        found = False
        for dev in devices:
            if not isinstance(dev, dict):
                continue
            if str(dev.get("id") or "") != self.cfg.sensor_id:
                continue
            params = dev.setdefault("params", {})
            if not isinstance(params, dict):
                params = {}
                dev["params"] = params
            params["joint_offsets"] = [float(x) for x in offsets]
            params["joint_signs"] = [float(x) for x in signs]
            found = True
            break
        if not found:
            return {"ok": False, "error": f"device {self.cfg.sensor_id!r} not in {path}"}
        with path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(
                raw,
                f,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
        with self._lock:
            self._override_offsets = list(offsets)
            self._override_signs = list(signs)
            self._push_affine_to_sensor_unlocked(offsets, signs)
        return {
            "ok": True,
            "message": f"wrote affine to {path}",
            "sensors_config": str(path),
            **self.get_affine(),
        }

    def _push_affine_to_sensor_unlocked(
        self, offsets: list[float], signs: list[float]
    ) -> None:
        sensor = self._sensor
        if sensor is None:
            return
        try:
            sensor.joint_offsets = list(offsets)
            sensor.joint_signs = list(signs)
        except Exception:  # noqa: BLE001
            pass

    def _calib_from_sensor(self) -> tuple[list[int], list[float], list[float]]:
        sensor = self._sensor
        if sensor is not None:
            ids = list(getattr(sensor, "joint_ids", None) or list(range(1, self._n_joints + 1)))
        else:
            try:
                ids, _, _ = self._yaml_affine()
            except Exception:  # noqa: BLE001
                ids = list(range(1, self._n_joints + 1))
        n = len(ids)
        if self._override_offsets is not None and self._override_signs is not None:
            offsets = _normalize_offsets(self._override_offsets, n)
            signs = _normalize_signs(self._override_signs, n)
            return ids, offsets, signs
        if sensor is not None:
            offsets = list(getattr(sensor, "joint_offsets", None) or [0.0] * n)
            signs = [float(s) for s in (getattr(sensor, "joint_signs", None) or [1.0] * n)]
            while len(offsets) < n:
                offsets.append(0.0)
            while len(signs) < n:
                signs.append(1.0)
            return ids, _normalize_offsets(offsets[:n], n), _normalize_signs(signs[:n], n)
        try:
            return self._yaml_affine()
        except Exception:  # noqa: BLE001
            return ids, [0.0] * n, [1.0] * n

    def _synth_joints(self, t_wall: float, n: int) -> list[float]:
        return [0.35 * math.sin(t_wall * (0.7 + 0.11 * i) + i * 0.4) for i in range(n)]

    def connect(self, *, dry_run: bool | None = None) -> dict[str, Any]:
        with self._lock:
            if self._sensor is not None and getattr(self._sensor, "_opened", False):
                return {"ok": True, "message": "already connected", **self.status()}
            if dry_run is not None:
                self.cfg.dry_run = bool(dry_run)
            self._manager = SensorManager.from_yaml(
                self.cfg.sensors_config, dry_run=self.cfg.dry_run
            )
            sensor = self._manager.get(self.cfg.sensor_id)
            sensor.open()
            self._sensor = sensor
            self._n_joints = len(list(getattr(sensor, "joint_ids", None) or [1, 2, 3, 4, 5, 6, 7]))
            if self._override_offsets is not None and self._override_signs is not None:
                self._push_affine_to_sensor_unlocked(
                    list(self._override_offsets), list(self._override_signs)
                )
            # Public API step maps from seq; reset each connect session (design Q5).
            self._latest = SampleSnapshot(dry_run=bool(self.cfg.dry_run), seq=0, connected=False)
            self._read_times = []
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="gello-sample", daemon=True)
            self._thread.start()
            return {"ok": True, "message": "connected", **self.status()}

    def disconnect(self) -> dict[str, Any]:
        with self._lock:
            self._stop.set()
            thr = self._thread
            sensor = self._sensor
            self._thread = None
            self._sensor = None
        if thr and thr.is_alive():
            thr.join(timeout=2.0)
        if sensor is not None:
            try:
                sensor.close()
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            self._latest.connected = False
            self._latest.error = None
        return {"ok": True, "message": "disconnected", **self.status()}

    def probe(self) -> dict[str, Any]:
        ensure_sensors_import()
        mgr = SensorManager.from_yaml(self.cfg.sensors_config, dry_run=self.cfg.dry_run)
        sensor = mgr.get(self.cfg.sensor_id)
        report = sensor.probe()
        return {
            "ok": True,
            "sensor_id": self.cfg.sensor_id,
            "status": getattr(report.status, "value", str(report.status)),
            "message": report.message,
            "checks": [
                {
                    "name": c.name,
                    "ok": bool(c.ok),
                    "detail": c.detail,
                    "critical": bool(getattr(c, "critical", False)),
                }
                for c in (report.checks or [])
            ],
            "metrics": dict(report.metrics or {}),
            "hints": list(report.hints or []),
        }

    def rate_probe(self, duration_s: float = 3.0) -> dict[str, Any]:
        with self._lock:
            sensor = self._sensor
            opened = bool(sensor and getattr(sensor, "_opened", False))
        if not opened or sensor is None:
            return {"ok": False, "error": "connect first"}
        try:
            out = sensor.probe_max_read_hz(duration_s=float(duration_s))
            return {"ok": True, **(out if isinstance(out, dict) else {"result": out})}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def list_ports(self) -> dict[str, Any]:
        ports: list[str] = []
        try:
            from sensors.drivers.bus.serial_bus import list_serial_by_id

            ports = list(list_serial_by_id())
        except Exception:  # noqa: BLE001
            pass
        try:
            import serial.tools.list_ports

            for p in serial.tools.list_ports.comports():
                ports.append(str(p.device))
        except Exception:  # noqa: BLE001
            pass
        # unique preserve order
        seen: set[str] = set()
        uniq: list[str] = []
        for p in ports:
            if p not in seen:
                seen.add(p)
                uniq.append(p)
        return {"ok": True, "ports": uniq}

    def _loop(self) -> None:
        period = 1.0 / max(1.0, float(self.cfg.sample_hz))
        while not self._stop.is_set():
            t0 = time.perf_counter()
            try:
                self._sample_once()
            except Exception as e:  # noqa: BLE001
                with self._lock:
                    self._latest.error = str(e)
                    self._latest.connected = False
            dt = time.perf_counter() - t0
            self._stop.wait(max(0.0, period - dt))

    def _sample_once(self) -> None:
        sensor = self._sensor
        if sensor is None:
            return
        ids, offsets, signs = self._calib_from_sensor()
        n = len(ids)
        sample = dict(sensor.read())
        t_wall = float(sample.get("ts") or time.time())
        joints = sample.get("joints_rad")
        joints_raw = sample.get("joints_rad_raw")
        dry = bool(sample.get("dry_run")) or (joints is None and joints_raw is None)
        synth = False
        if dry and joints is None and joints_raw is None:
            joints = self._synth_joints(t_wall, n)
            joints_raw = invert_gello_affine(joints, offsets=offsets, signs=signs)
            synth = True
        else:
            if joints_raw is not None:
                joints_raw = [float(x) for x in joints_raw]
                joints = apply_gello_affine(joints_raw, offsets=offsets, signs=signs)
            elif joints is not None:
                joints = [float(x) for x in joints]
                joints_raw = invert_gello_affine(joints, offsets=offsets, signs=signs)
            else:
                joints = [0.0] * n
                joints_raw = [0.0] * n

        now = time.perf_counter()
        self._read_times.append(now)
        cutoff = now - 1.0
        self._read_times = [t for t in self._read_times if t >= cutoff]
        hz = float(len(self._read_times))

        with self._lock:
            prev = self._latest.seq
            self._latest = SampleSnapshot(
                ts=t_wall,
                seq=prev + 1,
                connected=True,
                dry_run=dry or bool(self.cfg.dry_run),
                synth=synth,
                hz=hz,
                port=str(sample.get("port") or getattr(sensor, "port", None) or ""),
                joint_ids=ids,
                joints_rad=list(joints),
                joints_rad_raw=list(joints_raw),
                joints_deg=[float(math.degrees(x)) for x in joints],
                joints_deg_raw=[float(math.degrees(x)) for x in joints_raw],
                joint_offsets=offsets,
                joint_signs=signs,
                error=None,
            )

    def prepare_restart(self) -> dict[str, Any]:
        """Disconnect hardware; caller should ``os.execv`` shortly after.

        Always rebuild a portable ``python -m gello_app web …`` argv so Windows
        console-script launches (``gello.exe``) still restart correctly.
        """
        try:
            self.disconnect()
        except Exception:  # noqa: BLE001
            pass
        import sys

        argv = [
            sys.executable,
            "-m",
            "gello_app",
            "web",
            "-c",
            str(self.cfg.host_config),
            "--host",
            str(self.cfg.web_host),
            "--port",
            str(self.cfg.web_port),
        ]
        if self.cfg.dry_run:
            argv.append("--dry-run")
        else:
            argv.append("--no-dry-run")
        return {
            "ok": True,
            "message": "restart scheduled",
            "executable": sys.executable,
            "argv": argv,
            "prior_argv": list(self.restart_argv) if self.restart_argv else list(sys.argv),
        }
