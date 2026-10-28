const HALF_PI = Math.PI / 2;
const ARM_DOF = 6;

async function api(path, opts) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const text = await r.text();
  let data;
  try { data = JSON.parse(text); } catch { data = { raw: text }; }
  if (!r.ok) throw new Error((data && (data.detail || data.error)) || r.statusText);
  return data;
}

function $(id) { return document.getElementById(id); }

/** Draft affine edited in UI (not yet applied unless 下发). */
let draftSigns = [];
let draftOffsets = [];
let affineHydrated = false;
let pollTimer = null;

function ensureBars(n) {
  const root = $("bars");
  if (root.childElementCount === n) return;
  root.innerHTML = "";
  for (let i = 0; i < n; i++) {
    const row = document.createElement("div");
    row.className = "row";
    const label = i < ARM_DOF ? `J${i + 1}` : "J7·夹爪";
    row.innerHTML =
      `<span class="name">${label}</span>` +
      `<div class="track"><div class="fill" id="fill${i}"></div></div>` +
      `<span class="val" id="val${i}">—</span>`;
    root.appendChild(row);
  }
}

function fmtOffArm(rad) {
  const k = Math.round(rad / HALF_PI);
  const x = (k * 0.5).toFixed(1).replace(/\.0$/, "");
  return `${k}*π/2 (${x}π)`;
}

function fmtFloat(v, digits) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return n.toFixed(digits);
}

function isGripperJoint(i) {
  return i >= ARM_DOF;
}

function renderAffineEditor() {
  const root = $("affineEditor");
  const n = Math.max(draftSigns.length, draftOffsets.length, 1);
  root.innerHTML = "";
  for (let i = 0; i < n; i++) {
    const row = document.createElement("div");
    row.className = "affine-row";
    if (isGripperJoint(i)) {
      row.innerHTML =
        `<span class="jn">J${i + 1} · 夹爪→mm</span>` +
        `<div class="ctrl float" data-kind="sign" data-i="${i}">` +
          `<span class="lbl">sign</span>` +
          `<input type="number" class="affine-num" step="any" value="${fmtFloat(draftSigns[i], 8)}" />` +
        `</div>` +
        `<div class="ctrl float" data-kind="off" data-i="${i}">` +
          `<span class="lbl">offset</span>` +
          `<input type="number" class="affine-num" step="any" value="${fmtFloat(draftOffsets[i], 6)}" />` +
        `</div>`;
    } else {
      const s = draftSigns[i] >= 0 ? 1 : -1;
      const off = draftOffsets[i] || 0;
      row.innerHTML =
        `<span class="jn">J${i + 1}</span>` +
        `<div class="ctrl" data-kind="sign" data-i="${i}">` +
          `<span class="lbl">sign</span>` +
          `<button type="button" data-act="-">−</button>` +
          `<span class="v">${s >= 0 ? "+1" : "−1"}</span>` +
          `<button type="button" data-act="+">+</button>` +
        `</div>` +
        `<div class="ctrl" data-kind="off" data-i="${i}">` +
          `<span class="lbl">offset</span>` +
          `<button type="button" data-act="-">−</button>` +
          `<span class="v">${fmtOffArm(off)}</span>` +
          `<button type="button" data-act="+">+</button>` +
        `</div>`;
    }
    root.appendChild(row);
  }
  root.querySelectorAll("button[data-act]").forEach((btn) => {
    btn.onclick = () => {
      const wrap = btn.closest(".ctrl");
      const i = Number(wrap.dataset.i);
      const kind = wrap.dataset.kind;
      const act = btn.dataset.act;
      if (kind === "sign") {
        draftSigns[i] = act === "+" ? 1 : -1;
      } else {
        const step = act === "+" ? HALF_PI : -HALF_PI;
        draftOffsets[i] = (draftOffsets[i] || 0) + step;
      }
      renderAffineEditor();
    };
  });
  root.querySelectorAll("input.affine-num").forEach((inp) => {
    const commit = () => {
      const wrap = inp.closest(".ctrl");
      const i = Number(wrap.dataset.i);
      const kind = wrap.dataset.kind;
      const v = Number(inp.value);
      if (!Number.isFinite(v)) return;
      if (kind === "sign") {
        draftSigns[i] = v === 0 ? 1 : v;
      } else {
        draftOffsets[i] = v;
      }
    };
    inp.onchange = commit;
    inp.onblur = commit;
  });
}

function hydrateAffineFromStatus(s) {
  const aff = s.affine || {};
  const signs = aff.joint_signs || s.joint_signs || [];
  const offs = aff.joint_offsets || s.joint_offsets || [];
  if (!signs.length && !offs.length) return;
  if (!affineHydrated) {
    draftSigns = signs.map((x, i) => {
      const v = Number(x);
      if (!Number.isFinite(v)) return 1;
      if (i < ARM_DOF) return v >= 0 ? 1 : -1;
      return v === 0 ? 1 : v;
    });
    draftOffsets = offs.map((x) => Number(x) || 0);
    affineHydrated = true;
    renderAffineEditor();
  }
  const ov = !!(aff.override || s.affine_override);
  $("affineMeta").textContent = ov ? "运行时已覆盖 YAML" : "与 YAML / 传感器一致";
}

function renderStatus(s) {
  if (s.host_config) $("hostConfigPath").textContent = s.host_config;
  if (s.sensors_config) $("sensorsConfigPath").textContent = s.sensors_config;

  hydrateAffineFromStatus(s);

  const n = (s.joints_rad && s.joints_rad.length)
    || (s.joints_deg && s.joints_deg.length)
    || draftSigns.length
    || 7;
  ensureBars(n);
  for (let i = 0; i < n; i++) {
    const fill = $("fill" + i);
    const val = $("val" + i);
    if (isGripperJoint(i)) {
      const mm = (s.joints_rad && s.joints_rad[i] != null) ? Number(s.joints_rad[i]) : NaN;
      const pct = Number.isFinite(mm)
        ? Math.max(0, Math.min(100, (mm / 65) * 100))
        : 50;
      if (fill) fill.style.width = pct.toFixed(1) + "%";
      if (val) val.textContent = Number.isFinite(mm) ? (mm.toFixed(2) + " mm") : "—";
    } else {
      const deg = (s.joints_deg && s.joints_deg[i]) || 0;
      const pct = Math.max(0, Math.min(100, 50 + (deg / 180) * 50));
      if (fill) fill.style.width = pct.toFixed(1) + "%";
      if (val) val.textContent = Number(deg).toFixed(2) + "°";
    }
  }
  $("statusBox").textContent = JSON.stringify({
    connected: s.connected,
    dry_run: s.dry_run,
    synth: s.synth,
    hz: s.hz,
    port: s.port,
    seq: s.seq,
    error: s.error,
    sensor_id: s.sensor_id,
    affine_override: s.affine_override,
  }, null, 2);
  $("rawBox").textContent = JSON.stringify({
    joint_ids: s.joint_ids,
    joints_rad: s.joints_rad,
    joints_rad_raw: s.joints_rad_raw,
    joint_offsets: s.joint_offsets,
    joint_signs: s.joint_signs,
  }, null, 2);
  $("meta").textContent = s.connected
    ? `live · ${Number(s.hz || 0).toFixed(1)} Hz · ${s.dry_run ? "dry_run" : "hardware"}`
    : "idle";
  if (typeof s.dry_run === "boolean") $("dryRun").checked = !!s.dry_run;
}

async function refresh() {
  try {
    const s = await api("/api/status");
    renderStatus(s);
  } catch (e) {
    $("meta").textContent = "status error: " + e.message;
  }
}

$("btnConnect").onclick = async () => {
  try {
    await api("/api/connect", {
      method: "POST",
      body: JSON.stringify({ dry_run: $("dryRun").checked }),
    });
    await refresh();
  } catch (e) { alert(e.message); }
};

$("btnDisconnect").onclick = async () => {
  try { await api("/api/disconnect", { method: "POST" }); await refresh(); }
  catch (e) { alert(e.message); }
};

$("btnProbe").onclick = async () => {
  try {
    const r = await api("/api/probe", { method: "POST" });
    alert(JSON.stringify(r, null, 2));
  } catch (e) { alert(e.message); }
};

$("btnRate").onclick = async () => {
  try {
    const r = await api("/api/rate-probe", {
      method: "POST",
      body: JSON.stringify({ duration_s: 3 }),
    });
    alert(JSON.stringify(r, null, 2));
  } catch (e) { alert(e.message); }
};

$("btnPorts").onclick = async () => {
  try {
    const r = await api("/api/ports");
    alert((r.ports || []).join("\n") || "(no ports)");
  } catch (e) { alert(e.message); }
};

$("btnAffineApply").onclick = async () => {
  try {
    const r = await api("/api/affine/apply", {
      method: "POST",
      body: JSON.stringify({
        joint_signs: draftSigns,
        joint_offsets: draftOffsets,
      }),
    });
    affineHydrated = false;
    hydrateAffineFromStatus({ affine: r, affine_override: true });
    $("affineMeta").textContent = r.message || "已下发（临时）";
    await refresh();
  } catch (e) { alert(e.message); }
};

$("btnAffineSync").onclick = async () => {
  try {
    await api("/api/affine/apply", {
      method: "POST",
      body: JSON.stringify({
        joint_signs: draftSigns,
        joint_offsets: draftOffsets,
      }),
    });
    const r = await api("/api/affine/sync", { method: "POST" });
    affineHydrated = false;
    hydrateAffineFromStatus({ affine: r, affine_override: true });
    $("affineMeta").textContent = r.message || "已写入 YAML";
    await refresh();
  } catch (e) { alert(e.message); }
};

async function waitForBackend(timeoutMs) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    try {
      const r = await fetch("/api/health", { cache: "no-store" });
      if (r.ok) return true;
    } catch (_) { /* restarting */ }
    await new Promise((res) => setTimeout(res, 400));
  }
  return false;
}

$("btnRestart").onclick = async () => {
  if (!confirm("重启 gello 后端并刷新页面？未同步到 YAML 的临时仿射会丢失。")) return;
  $("meta").textContent = "restarting…";
  if (pollTimer) clearInterval(pollTimer);
  try {
    await api("/api/restart", { method: "POST" });
  } catch (_) {
    // Connection may drop as process exits — expected.
  }
  const ok = await waitForBackend(20000);
  if (ok) {
    location.reload();
  } else {
    alert("后端重启超时，请手动刷新或重新启动 gello web");
    location.reload();
  }
};

pollTimer = setInterval(refresh, 200);
refresh();
