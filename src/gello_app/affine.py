"""Gello joint affine — same semantics as sensors-dcs ``GelloAgent``."""

from __future__ import annotations

from typing import Any, Sequence


def _as_float_list(values: Any, *, n: int, fill: float) -> list[float]:
    seq = list(values or [])
    out = [float(x) for x in seq[:n]]
    if len(out) < n:
        out.extend([fill] * (n - len(out)))
    return out


def apply_gello_affine(
    joints_raw: Sequence[float],
    *,
    offsets: Sequence[float],
    signs: Sequence[float],
) -> list[float]:
    """``q = (q_raw - offsets) * signs``."""
    n = len(joints_raw)
    off = _as_float_list(offsets, n=n, fill=0.0)
    sgn = _as_float_list(signs, n=n, fill=1.0)
    return [(float(joints_raw[i]) - off[i]) * sgn[i] for i in range(n)]


def invert_gello_affine(
    joints: Sequence[float],
    *,
    offsets: Sequence[float],
    signs: Sequence[float],
) -> list[float]:
    """Inverse of ``apply_gello_affine`` (sign may be any non-zero float)."""
    n = len(joints)
    off = _as_float_list(offsets, n=n, fill=0.0)
    sgn = _as_float_list(signs, n=n, fill=1.0)
    out: list[float] = []
    for i in range(n):
        s = sgn[i] if sgn[i] != 0.0 else 1.0
        out.append(float(joints[i]) / s + off[i])
    return out
