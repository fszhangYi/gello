"""FastAPI host for Gello single-device test."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from typing import Any, AsyncIterator, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from gello_app.paths import APP_TITLE, static_dir
from gello_app.service import GelloService, SampleSnapshot


class ConnectBody(BaseModel):
    dry_run: Optional[bool] = None


class RateProbeBody(BaseModel):
    duration_s: float = Field(default=3.0, ge=0.5, le=30.0)


class AffineBody(BaseModel):
    joint_signs: Optional[list[float]] = None
    joint_offsets: Optional[list[float]] = None


def _step(snap: SampleSnapshot) -> int:
    """Public sequence number (design: step == internal seq)."""
    return int(snap.seq)


def _panel(snap: SampleSnapshot) -> dict[str, Any]:
    return {
        "joint_ids": list(snap.joint_ids),
        "joints_rad": list(snap.joints_rad),
        "joints_rad_raw": list(snap.joints_rad_raw),
        "joint_offsets": list(snap.joint_offsets),
        "joint_signs": list(snap.joint_signs),
    }


def _data_frame(service: GelloService, *, full: bool = False) -> dict[str, Any]:
    snap = service.latest
    frame: dict[str, Any] = {
        "ts": float(snap.ts),
        "step": _step(snap),
        "connected": bool(snap.connected),
        "dry_run": bool(snap.dry_run),
        "synth": bool(snap.synth),
        "panel": _panel(snap),
    }
    if full:
        st = service.status()
        # Do not expose internal name "seq" on public stream; step already set.
        st = {k: v for k, v in st.items() if k != "seq"}
        st["step"] = _step(snap)
        frame["status"] = st
    return frame


def _joints_payload(service: GelloService) -> dict[str, Any]:
    """Arm joints for downstream follow / viz — calibrated ``joints_rad`` (affine applied)."""
    snap = service.latest
    step = _step(snap)
    cal = list(snap.joints_rad)
    if len(cal) < 6:
        return {
            "ok": False,
            "error": "need_6_joints" if cal else "no_sample",
            "ts": float(snap.ts),
            "step": step,
            "connected": bool(snap.connected),
            "unit": "rad",
            "source": "joints_rad[0:6]",
            "joints": None,
        }
    return {
        "ok": True,
        "ts": float(snap.ts),
        "step": step,
        "connected": bool(snap.connected),
        "unit": "rad",
        "source": "joints_rad[0:6]",
        "joints": [float(x) for x in cal[:6]],
    }


def _gripper_payload(service: GelloService) -> dict[str, Any]:
    """7th channel for downstream — calibrated J7 (affine applied).

    With the Huiling mapping (raw 1.83→0 mm, 0.9986→65 mm), ``gripper`` is stroke mm.
    """
    snap = service.latest
    step = _step(snap)
    cal = list(snap.joints_rad)
    if not cal:
        return {
            "ok": False,
            "error": "no_sample",
            "ts": float(snap.ts),
            "step": step,
            "connected": bool(snap.connected),
            "unit": "mm",
            "source": "joints_rad[-1]",
            "gripper": None,
        }
    return {
        "ok": True,
        "ts": float(snap.ts),
        "step": step,
        "connected": bool(snap.connected),
        "unit": "mm",
        "source": "joints_rad[-1]",
        "gripper": float(cal[-1]),
    }


def _sse_pack(payload: dict[str, Any], *, event: str = "gello") -> bytes:
    body = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return f"event: {event}\ndata: {body}\n\n".encode("utf-8")


async def _sse_stream(
    service: GelloService,
    *,
    build_frame,
    hz: float,
    sample_hz: float,
) -> AsyncIterator[bytes]:
    max_hz = max(1.0, min(float(hz), float(sample_hz) if sample_hz > 0 else float(hz)))
    interval = 1.0 / max_hz
    last_step: int | None = None
    last_ping = time.perf_counter()
    while True:
        frame = build_frame()
        step = int(frame.get("step") or 0)
        if last_step is None or step != last_step:
            yield _sse_pack(frame)
            last_step = step
        now = time.perf_counter()
        if now - last_ping >= 15.0:
            yield b": ping\n\n"
            last_ping = now
        await asyncio.sleep(interval)


def create_app(service: GelloService) -> FastAPI:
    app = FastAPI(title=APP_TITLE, version="0.1.0")
    # Allow sensors-arm (other origin/port) to EventSource / fetch /joints SSE.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    static = static_dir()
    if static.is_dir():
        app.mount("/static", StaticFiles(directory=str(static)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> Any:
        index_path = static / "index.html"
        if index_path.is_file():
            return FileResponse(index_path)
        return HTMLResponse("<h1>gello-host</h1><p>static/index.html missing</p>")

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "app": "gello-host"}

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return service.status()

    @app.get("/api/ports")
    def ports() -> dict[str, Any]:
        return service.list_ports()

    @app.post("/api/connect")
    def connect(body: ConnectBody | None = None) -> dict[str, Any]:
        dry = None if body is None else body.dry_run
        try:
            return service.connect(dry_run=dry)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e)) from e

    @app.post("/api/disconnect")
    def disconnect() -> dict[str, Any]:
        return service.disconnect()

    @app.post("/api/probe")
    def probe() -> dict[str, Any]:
        try:
            return service.probe()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e)) from e

    @app.post("/api/rate-probe")
    def rate_probe(body: RateProbeBody | None = None) -> dict[str, Any]:
        dur = 3.0 if body is None else float(body.duration_s)
        out = service.rate_probe(duration_s=dur)
        if not out.get("ok"):
            raise HTTPException(status_code=400, detail=out.get("error") or "rate probe failed")
        return out

    @app.get("/api/affine")
    def affine_get() -> dict[str, Any]:
        return {"ok": True, **service.get_affine()}

    @app.post("/api/affine/apply")
    def affine_apply(body: AffineBody) -> dict[str, Any]:
        try:
            return service.apply_affine(
                joint_signs=body.joint_signs,
                joint_offsets=body.joint_offsets,
            )
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.post("/api/affine/sync")
    def affine_sync() -> dict[str, Any]:
        try:
            out = service.sync_affine_to_yaml()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e)) from e
        if not out.get("ok"):
            raise HTTPException(status_code=400, detail=out.get("error") or "sync failed")
        return out

    @app.post("/api/restart")
    def restart() -> dict[str, Any]:
        info = service.prepare_restart()

        def _reexec() -> None:
            time.sleep(0.35)
            exe = str(info.get("executable") or sys.executable)
            argv = list(info.get("argv") or [])
            if not argv or argv[0] != exe:
                argv = [exe] + [a for a in argv if a != exe]
            if len(argv) < 2:
                argv = [
                    exe,
                    "-m",
                    "gello_app",
                    "web",
                    "-c",
                    str(service.cfg.host_config),
                ]
            try:
                os.execv(exe, argv)
            except Exception:  # noqa: BLE001
                # Fallback for environments where execv cannot replace uvicorn.
                try:
                    import subprocess

                    subprocess.Popen(argv, cwd=os.getcwd())
                except Exception:  # noqa: BLE001
                    pass
                os._exit(1)

        threading.Thread(target=_reexec, name="gello-restart", daemon=True).start()
        return {"ok": True, "message": "backend restarting", **info}

    # ---- Public machine endpoints (design: streaming-endpoints-design.md) ----

    @app.get("/data")
    async def data(
        hz: float = Query(default=20.0, ge=1.0, le=200.0),
        full: int = Query(default=0, ge=0, le=1),
    ) -> StreamingResponse:
        sample_hz = float(service.cfg.sample_hz)

        def build() -> dict[str, Any]:
            return _data_frame(service, full=bool(full))

        gen = _sse_stream(service, build_frame=build, hz=hz, sample_hz=sample_hz)
        return StreamingResponse(
            gen,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/joints")
    async def joints(
        stream: int = Query(default=0, ge=0, le=1),
        hz: float = Query(default=20.0, ge=1.0, le=200.0),
    ) -> Any:
        if not stream:
            return _joints_payload(service)
        sample_hz = float(service.cfg.sample_hz)
        gen = _sse_stream(
            service, build_frame=lambda: _joints_payload(service), hz=hz, sample_hz=sample_hz
        )
        return StreamingResponse(
            gen,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/gripper")
    async def gripper(
        stream: int = Query(default=0, ge=0, le=1),
        hz: float = Query(default=20.0, ge=1.0, le=200.0),
    ) -> Any:
        if not stream:
            return _gripper_payload(service)
        sample_hz = float(service.cfg.sample_hz)
        gen = _sse_stream(
            service,
            build_frame=lambda: _gripper_payload(service),
            hz=hz,
            sample_hz=sample_hz,
        )
        return StreamingResponse(
            gen,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return app


def main(service: GelloService, *, host: str | None = None, port: int | None = None) -> None:
    app = create_app(service)
    uvicorn.run(
        app,
        host=host or service.cfg.web_host,
        port=int(port or service.cfg.web_port),
        log_level="info",
    )
