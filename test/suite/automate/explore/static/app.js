(() => {
  const ANY_FAIL = "__any_fail__";
  const UNSET = "__unset__";

  const state = {
    tab: "overview",
    preset: "all",
    from: null,
    to: null,
    meta: null,
    health: null,
    showHidden: false,
    hides: [],
    setting: "door_shuffle",
    errorType: ANY_FAIL,
    stage: "",
    minN: 10,
    rowSetting: "door_shuffle",
    colSetting: "intensity",
    crosstabError: ANY_FAIL,
    sortLift: { key: "lift", dir: -1 },
    drawer: null,
    drawerOffset: 0,
    suite: null,
    suites: [],
    _crosstabReq: 0,
    _liftReq: 0,
    _loadReq: 0,
  };

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  function fmtInt(n) {
    if (n == null || Number.isNaN(n)) return "—";
    return Number(n).toLocaleString();
  }

  function fmtPct(x, digits = 1) {
    if (x == null || Number.isNaN(x)) return "—";
    return `${(x * 100).toFixed(digits)}%`;
  }

  function fmtLift(x) {
    if (x == null || Number.isNaN(x)) return "—";
    return `${x.toFixed(2)}×`;
  }

  function fmtDur(ms) {
    if (ms == null) return "—";
    if (ms < 1000) return `${Math.round(ms)}ms`;
    const s = ms / 1000;
    if (s < 90) return `${s.toFixed(1)}s`;
    const m = Math.floor(s / 60);
    const rem = Math.round(s - m * 60);
    return `${m}m ${rem}s`;
  }

  function fmtWhen(ts) {
    if (!ts) return "—";
    const d = new Date(ts * 1000);
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function toDatetimeLocal(ts) {
    if (!ts) return "";
    const d = new Date(ts * 1000);
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function fromDatetimeLocal(value) {
    if (!value) return null;
    const ms = new Date(value).getTime();
    if (Number.isNaN(ms)) return null;
    return Math.floor(ms / 1000);
  }

  function fmtP(p) {
    if (p == null) return "—";
    if (p < 0.001) return "<0.001";
    if (p < 0.01) return p.toFixed(3);
    return p.toFixed(2);
  }

  function hashHue(name) {
    let h = 0;
    const s = String(name);
    for (let i = 0; i < s.length; i++) h = (h * 33 + s.charCodeAt(i)) >>> 0;
    return h % 360;
  }

  function colorFor(name) {
    if (!name) return "#6b7789";
    if (name === "(unclassified)") return "#6b7789";
    if (/timeout/i.test(name)) return "#d89a3a";
    if (/fill/i.test(name)) return "#c45c8a";
    if (/keylock|key/i.test(name)) return "#7a8be0";
    if (/exception|traceback|runtime/i.test(name)) return "#e06b5c";
    return `hsl(${hashHue(name)} 52% 58%)`;
  }

  function heatColor(rate) {
    if (rate == null) return "transparent";
    const stops = [
      [0, [22, 58, 50]],
      [0.35, [196, 163, 90]],
      [0.7, [196, 96, 64]],
      [1, [155, 40, 38]],
    ];
    const t = Math.max(0, Math.min(1, rate));
    let a = stops[0], b = stops[stops.length - 1];
    for (let i = 0; i < stops.length - 1; i++) {
      if (t >= stops[i][0] && t <= stops[i + 1][0]) {
        a = stops[i];
        b = stops[i + 1];
        break;
      }
    }
    const u = (t - a[0]) / Math.max(b[0] - a[0], 1e-6);
    const rgb = a[1].map((v, i) => Math.round(v + (b[1][i] - v) * u));
    return `rgb(${rgb.join(",")})`;
  }

  function qs(extra = {}) {
    const p = new URLSearchParams();
    if (state.from != null) p.set("from", String(state.from));
    if (state.to != null) p.set("to", String(state.to));
    if (state.showHidden) p.set("show_hidden", "1");
    if (state.suite) p.set("suite", state.suite);
    for (const [k, v] of Object.entries(extra)) {
      if (v === undefined || v === null || v === "") continue;
      p.set(k, String(v));
    }
    const s = p.toString();
    return s ? `?${s}` : "";
  }

  async function api(path, extra = {}, opts = {}) {
    const method = (opts.method || "GET").toUpperCase();
    const init = { method };
    if (method !== "GET" && method !== "HEAD") {
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(opts.body || {});
    }
    const url = method === "GET" || method === "DELETE"
      ? path + qs(extra)
      : path + qs(extra);
    const res = await fetch(url, init);
    const text = await res.text();
    let data;
    try { data = JSON.parse(text); } catch {
      throw new Error(text || res.statusText);
    }
    if (!res.ok) throw new Error(data.error || text || res.statusText);
    return data;
  }

  async function hideErrorType(errorType, asOfUnix = null, note = "") {
    const body = { error_type: errorType, note };
    if (asOfUnix != null) body.hidden_as_of = asOfUnix;
    const data = await api("/api/hides", {}, { method: "POST", body });
    state.hides = data.hides || [];
    return data;
  }

  async function unhideErrorType(errorType) {
    const data = await api("/api/hides", { error_type: errorType }, { method: "DELETE" });
    state.hides = data.hides || [];
    return data;
  }

  const RELATIVE_PRESETS = new Set(["8h", "24h", "7d", "30d"]);

  function refreshTimeWindow() {
    const preset = state.preset;
    if (preset === "all") {
      state.from = null;
      state.to = null;
      return;
    }
    if (!RELATIVE_PRESETS.has(preset)) return; // custom keeps fixed bounds
    const now = Math.floor(Date.now() / 1000);
    if (preset === "8h") {
      state.from = now - 8 * 3600;
      state.to = now;
    } else if (preset === "24h") {
      state.from = now - 86400;
      state.to = now;
    } else if (preset === "7d") {
      state.from = now - 7 * 86400;
      state.to = now;
    } else if (preset === "30d") {
      state.from = now - 30 * 86400;
      state.to = now;
    }
  }

  function applyPreset(preset) {
    state.preset = preset;
    refreshTimeWindow();
    $$(".presets [data-preset]").forEach((b) => b.classList.toggle("on", b.dataset.preset === preset));
    $("#customRange").hidden = preset !== "custom";
  }

  function bucketLabel(bucket, grain) {
    if (!bucket) return "";
    const s = String(bucket);
    if (grain === "hour") {
      // "YYYY-MM-DD HH:00" → "MM-DD HH:00"
      if (s.length >= 16) return s.slice(5, 16);
      return s;
    }
    // "YYYY-MM-DD" → "MM-DD"
    return s.length >= 10 ? s.slice(5, 10) : s;
  }

  function applyCustom() {
    const from = $("#fromDate").value;
    const to = $("#toDate").value;
    state.preset = "custom";
    state.from = from ? Math.floor(new Date(`${from}T00:00:00`).getTime() / 1000) : null;
    state.to = to ? Math.floor(new Date(`${to}T23:59:59`).getTime() / 1000) : null;
    $$(".presets [data-preset]").forEach((b) => b.classList.toggle("on", b.dataset.preset === "custom"));
  }

  function settingOptions(selected) {
    const groups = state.meta?.setting_groups || [];
    return groups.map((g) => {
      const opts = g.settings.map((s) => {
        const mark = s.constant ? " (const)" : "";
        return `<option value="${esc(s.name)}" ${s.name === selected ? "selected" : ""}>${esc(s.name)}${mark}</option>`;
      }).join("");
      return `<optgroup label="${esc(g.group)}">${opts}</optgroup>`;
    }).join("");
  }

  function errorOptions(selected, includeAny = true) {
    const items = [];
    if (includeAny) items.push([ANY_FAIL, "Any failure"]);
    for (const e of state.meta?.error_types || []) {
      items.push([e.error_type, `${e.error_type} (${e.n})`]);
    }
    return items.map(([v, l]) =>
      `<option value="${esc(v)}" ${v === selected ? "selected" : ""}>${esc(l)}</option>`
    ).join("");
  }

  function stageOptions(selected) {
    const items = [["", "All stages"]];
    for (const s of state.meta?.stages || []) {
      items.push([s.stage, `${s.stage} (${s.n})`]);
    }
    return items.map(([v, l]) =>
      `<option value="${esc(v)}" ${v === selected ? "selected" : ""}>${esc(l)}</option>`
    ).join("");
  }

  function renderKpis(o) {
    const rate = o.success_rate;
    const hiddenN = o.hidden_fail_n || 0;
    $("#kpis").innerHTML = `
      <div class="kpi"><div class="lbl">Runs</div><div class="val">${fmtInt(o.n)}</div></div>
      <div class="kpi"><div class="lbl">Success</div><div class="val ok">${fmtInt(o.ok)} <span class="faint" style="font-size:.7em">${fmtPct(rate)}</span></div></div>
      <div class="kpi"><div class="lbl">Failures</div><div class="val fail">${fmtInt(o.fail)}</div></div>
      <div class="kpi"><div class="lbl">Unknown fails</div><div class="val ${o.unknown ? "warn" : ""}">${fmtInt(o.unknown ?? "—")}</div></div>
      ${hiddenN && !state.showHidden
        ? `<div class="kpi"><div class="lbl">Hidden fails</div><div class="val faint">${fmtInt(hiddenN)}</div></div>`
        : ""}
    `;
  }

  function stackBar(counts, total) {
    const parts = Object.entries(counts || {}).sort((a, b) => b[1] - a[1]);
    if (!total) return `<div class="bar"></div>`;
    return `<div class="bar">${parts.map(([k, n]) =>
      `<i style="width:${(n / total) * 100}%;background:${colorFor(k)}" title="${esc(k)}: ${n}"></i>`
    ).join("")}</div>`;
  }

  function legend(names) {
    if (!names?.length) return "";
    return `<div class="stack-legend">${names.map((n) =>
      `<span><i class="swatch" style="background:${colorFor(n)}"></i>${esc(n)}</span>`
    ).join("")}</div>`;
  }

  function drawTrend(canvas, days, grain = "day") {
    const dpr = window.devicePixelRatio || 1;
    const cssW = canvas.clientWidth || 640;
    const cssH = canvas.clientHeight || 220;
    canvas.width = Math.floor(cssW * dpr);
    canvas.height = Math.floor(cssH * dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);
    if (!days.length) {
      ctx.fillStyle = "#93a0b3";
      ctx.fillText("No runs in this range", 12, 24);
      return;
    }
    const pad = { l: 36, r: 44, t: 12, b: 28 };
    const w = cssW - pad.l - pad.r;
    const h = cssH - pad.t - pad.b;
    const maxN = Math.max(...days.map((d) => d.n), 1);
    ctx.strokeStyle = "#2d3542";
    ctx.beginPath();
    ctx.moveTo(pad.l, pad.t);
    ctx.lineTo(pad.l, pad.t + h);
    ctx.lineTo(pad.l + w, pad.t + h);
    ctx.stroke();

    const bw = Math.max(2, (w / days.length) * 0.62);
    days.forEach((d, i) => {
      const x = pad.l + (i + 0.5) * (w / days.length);
      const bh = (d.n / maxN) * h;
      ctx.fillStyle = "#2a3344";
      ctx.fillRect(x - bw / 2, pad.t + h - bh, bw, bh);
      ctx.fillStyle = "#e06b5c";
      const fh = ((d.fail || 0) / maxN) * h;
      ctx.fillRect(x - bw / 2, pad.t + h - fh, bw, fh);
    });

    ctx.beginPath();
    ctx.strokeStyle = "#e0b15a";
    ctx.lineWidth = 2;
    days.forEach((d, i) => {
      const x = pad.l + (i + 0.5) * (w / days.length);
      const y = pad.t + h - (d.fail_rate || 0) * h;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();

    ctx.fillStyle = "#6b7789";
    ctx.font = "11px Segoe UI, sans-serif";
    ctx.fillText(String(maxN), 4, pad.t + 10);
    ctx.fillText("0", 4, pad.t + h);
    ctx.textAlign = "right";
    ctx.fillStyle = "#e0b15a";
    ctx.fillText("100%", cssW - 4, pad.t + 10);
    ctx.textAlign = "left";
    const first = bucketLabel(days[0].day || days[0].bucket, grain);
    const last = bucketLabel(days[days.length - 1].day || days[days.length - 1].bucket, grain);
    ctx.fillStyle = "#6b7789";
    ctx.fillText(first, pad.l, cssH - 8);
    ctx.textAlign = "right";
    ctx.fillText(last, pad.l + w, cssH - 8);
  }

  function hbars(items, total, { hideable = false } = {}) {
    const max = Math.max(...items.map((i) => i.n), 1);
    const hiddenSet = new Set((state.hides || []).map((h) => h.error_type));
    return items.map((i) => {
      const isHidden = hideable && hiddenSet.has(i.name);
      return `
      <div class="hbar ${isHidden ? "is-hidden" : ""}" data-click="1" data-name="${esc(i.name)}">
        <div class="name" title="${esc(i.name)}">${esc(i.name)}${isHidden ? ' <span class="tag-hidden">hidden</span>' : ""}</div>
        <div class="track"><i style="width:${(i.n / max) * 100}%;background:${colorFor(i.name)}"></i></div>
        <div class="n">${fmtInt(i.n)}</div>
        ${hideable ? `<button type="button" class="ghost hide-btn" data-hide="${esc(i.name)}" title="Hide this error type as of now (view only)">${isHidden ? "Unhide" : "Hide"}</button>` : ""}
      </div>`;
    }).join("");
  }

  function hidesPanel(hides) {
    if (!hides?.length) {
      return `<p class="muted" style="margin:0">No error types hidden. Use <strong>Hide</strong> on an error bar after shipping a fix.</p>`;
    }
    return `
      <table class="compact">
        <thead><tr><th>Error type</th><th>Hidden as of</th><th></th></tr></thead>
        <tbody>
          ${hides.map((h) => `
            <tr>
              <td>${esc(h.error_type)}</td>
              <td>
                <div class="asof-cell"
                  data-error="${esc(h.error_type)}"
                  data-note="${esc(h.note || "")}"
                  data-asof="${esc(String(h.hidden_as_of))}">
                  <span class="asof-display">${esc(fmtWhen(h.hidden_as_of))}</span>
                  <button type="button" class="asof-edit" title="Edit hidden-as-of" aria-label="Edit hidden-as-of">
                    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
                      <path fill="currentColor" d="M11.7 1.3a1.5 1.5 0 0 1 2.1 2.1l-.4.4-2.1-2.1.4-.4zm-1 1.5 2.1 2.1-7.6 7.6H3.1V10.4l7.6-7.6z"/>
                    </svg>
                  </button>
                  <input type="datetime-local" class="asof-input" hidden>
                </div>
              </td>
              <td class="num"><button type="button" class="ghost" data-unhide="${esc(h.error_type)}">Unhide</button></td>
            </tr>
          `).join("")}
        </tbody>
      </table>
      <p class="hint" style="margin:.5rem 0 0">Failures of that type with timestamp ≤ as-of are excluded unless “Show hidden” is on. Newer ones still appear.</p>
    `;
  }

  function closeAsofEdit(cell) {
    const input = cell.querySelector(".asof-input");
    const display = cell.querySelector(".asof-display");
    const edit = cell.querySelector(".asof-edit");
    if (input) input.hidden = true;
    if (display) display.hidden = false;
    if (edit) edit.hidden = false;
  }

  function openAsofEdit(cell) {
    const input = cell.querySelector(".asof-input");
    const display = cell.querySelector(".asof-display");
    const edit = cell.querySelector(".asof-edit");
    const asof = Number(cell.dataset.asof);
    input.value = toDatetimeLocal(asof);
    display.hidden = true;
    edit.hidden = true;
    input.hidden = false;
    input.focus();
    try { input.showPicker?.(); } catch { /* ignore */ }
  }

  async function renderOverview(prefetched = null) {
    const o = prefetched || await api("/api/overview");
    state.hides = o.hides || [];
    renderKpis(o);
    const range = (o.tmin && o.tmax)
      ? `${fmtWhen(o.tmin)} → ${fmtWhen(o.tmax)}`
      : "no runs";
    const hiddenNote = (!state.showHidden && o.hidden_fail_n)
      ? ` · <span class="faint">${fmtInt(o.hidden_fail_n)} hidden failure(s) excluded</span>`
      : "";
    $("#main").innerHTML = `
      <div class="row overview-tools">
        <span class="muted">Window: ${esc(range)}</span>
        <label class="check">
          <input type="checkbox" id="showHidden" ${state.showHidden ? "checked" : ""}>
          Show hidden errors&nbsp;${hiddenNote}
        </label>
      </div>
      <p class="hint">Gold line is fail rate; bars are ${o.time_grain === "hour" ? "hourly" : "daily"} volume (red = failures). Hide is view-only (sidecar JSON next to the DB).</p>
      <div class="grid-2">
        <div class="card">
          <h3>Fail rate over time <span class="faint" style="font-weight:500;font-size:.8em">(${o.time_grain === "hour" ? "by hour" : "by day"})</span></h3>
          <canvas class="chart" id="trend"></canvas>
        </div>
        <div class="card">
          <h3>Duration</h3>
          <table>
            <thead><tr><th></th><th class="num">n</th><th class="num">median</th><th class="num">p95</th><th class="num">max</th></tr></thead>
            <tbody>
              ${["all", "ok", "fail"].map((k) => {
                const d = o.duration[k];
                const label = k === "all" ? "All" : k === "ok" ? "Success" : "Failure";
                return `<tr><td>${label}</td><td class="num">${fmtInt(d.n)}</td>
                  <td class="num">${fmtDur(d.p50_ms)}</td>
                  <td class="num">${fmtDur(d.p95_ms)}</td>
                  <td class="num">${fmtDur(d.max_ms)}</td></tr>`;
              }).join("")}
            </tbody>
          </table>
        </div>
      </div>
      <div class="grid-2 equal" style="margin-top:1rem">
        <div class="card">
          <h3>Error types</h3>
          <div id="errBars">${hbars(o.errors, o.fail, { hideable: true })}</div>
        </div>
        <div class="col-stack">
          <div class="card">
            <h3>Stages</h3>
            <div id="stageBars">${hbars(o.stages, o.fail)}</div>
          </div>
          <div class="card">
            <h3>Hidden error types</h3>
            <div id="hidesPanel">${hidesPanel(state.hides)}</div>
          </div>
        </div>
      </div>
    `;
    drawTrend($("#trend"), o.days || [], o.time_grain || "day");
    $("#showHidden")?.addEventListener("change", (ev) => {
      state.showHidden = !!ev.target.checked;
      load();
    });
    $("#errBars").addEventListener("click", async (ev) => {
      const hideBtn = ev.target.closest("[data-hide]");
      if (hideBtn) {
        ev.stopPropagation();
        const name = hideBtn.dataset.hide;
        const already = (state.hides || []).some((h) => h.error_type === name);
        try {
          if (already) await unhideErrorType(name);
          else await hideErrorType(name);
          await load();
        } catch (err) {
          alert(err.message || err);
        }
        return;
      }
      const row = ev.target.closest("[data-name]");
      if (!row) return;
      openRuns({
        title: `Error ${row.dataset.name}`,
        extra: { error_type: row.dataset.name },
      });
    });
    $("#stageBars").addEventListener("click", (ev) => {
      const row = ev.target.closest("[data-name]");
      if (!row) return;
      openRuns({ title: `Stage ${row.dataset.name}`, extra: { stage: row.dataset.name, error_type: ANY_FAIL } });
    });
    $("#hidesPanel").addEventListener("click", async (ev) => {
      const unhide = ev.target.closest("[data-unhide]");
      if (unhide) {
        try {
          await unhideErrorType(unhide.dataset.unhide);
          await load();
        } catch (err) {
          alert(err.message || err);
        }
        return;
      }
      const edit = ev.target.closest(".asof-edit");
      if (edit) {
        ev.preventDefault();
        openAsofEdit(edit.closest(".asof-cell"));
      }
    });
    $("#hidesPanel").addEventListener("keydown", (ev) => {
      if (ev.key !== "Escape") return;
      const input = ev.target.closest(".asof-input");
      if (!input) return;
      ev.preventDefault();
      closeAsofEdit(input.closest(".asof-cell"));
    });
    $("#hidesPanel").addEventListener("focusout", async (ev) => {
      const input = ev.target.closest(".asof-input");
      if (!input || input.hidden) return;
      const cell = input.closest(".asof-cell");
      // Still focusing inside this cell (e.g. picker internals)
      if (cell.contains(ev.relatedTarget)) return;
      const unix = fromDatetimeLocal(input.value);
      const prev = Number(cell.dataset.asof);
      closeAsofEdit(cell);
      if (unix == null || unix === prev) return;
      try {
        await hideErrorType(cell.dataset.error, unix, cell.dataset.note || "");
        await load();
      } catch (err) {
        alert(err.message || err);
      }
    });
  }

  async function renderSetting() {
    $("#main").innerHTML = `
      <div class="row">
        <label>Setting
          <select id="settingSel">${settingOptions(state.setting)}</select>
        </label>
      </div>
      <p class="hint">Each value’s failure mix. Click a row for matching seeds, or a stack segment for that error type only.</p>
      <div id="settingBody" class="card"><p class="muted">Loading…</p></div>
    `;
    $("#settingSel").addEventListener("change", (e) => {
      state.setting = e.target.value;
      fillSetting();
    });
    await fillSetting();
  }

  async function fillSetting() {
    const data = await api("/api/setting", { setting: state.setting });
    const body = $("#settingBody");
    const names = data.error_types;
    if (!data.values.length) {
      body.innerHTML = `<p class="muted">No runs in this range.</p>`;
      return;
    }
    body.innerHTML = `
      ${legend(names)}
      <table>
        <thead>
          <tr>
            <th>Value</th>
            <th class="num">n</th>
            <th class="num">Fail</th>
            <th class="num">Fail %</th>
            <th>Error mix</th>
            <th>Stage mix</th>
          </tr>
        </thead>
        <tbody>
          ${data.values.map((v) => `
            <tr data-click="1" data-value="${esc(String(v.value))}">
              <td>${esc(v.label)}</td>
              <td class="num">${fmtInt(v.n)}</td>
              <td class="num">${fmtInt(v.fail)}</td>
              <td class="num">${fmtPct(v.fail_rate)}</td>
              <td data-kind="error">${stackBar(v.errors, v.fail)}</td>
              <td data-kind="stage">${stackBar(v.stages, v.fail)}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    `;
    body.querySelector("tbody").addEventListener("click", (ev) => {
      const tr = ev.target.closest("tr");
      if (!tr) return;
      const value = tr.dataset.value;
      const seg = ev.target.closest("i");
      const extra = { setting: state.setting };
      if (value === UNSET) extra.unset = 1;
      else extra.value = value;
      let title = `${state.setting} = ${value === UNSET ? "(unset)" : value}`;
      if (seg && ev.target.closest("[data-kind]")) {
        const kind = ev.target.closest("[data-kind]").dataset.kind;
        const label = (seg.getAttribute("title") || "").split(":")[0];
        if (label) {
          if (kind === "error") extra.error_type = label;
          else extra.stage = label;
          title += ` · ${label}`;
        }
      }
      openRuns({ title, extra });
    });
  }

  async function renderLift() {
    $("#main").innerHTML = `
      <div class="row">
        <label>Error
          <select id="errSel">${errorOptions(state.errorType)}</select>
        </label>
        <label>Stage
          <select id="stageSel">${stageOptions(state.stage)}</select>
        </label>
        <label>Min n
          <input type="number" id="minN" min="1" max="500" value="${state.minN}" style="width:5rem">
        </label>
      </div>
      <p class="hint">Lift is how much more often this error happens at that setting value versus the window baseline. Excess is extra failures vs that baseline. Click a row to open matching runs.</p>
      <div id="liftBody" class="card"><p class="muted">Loading…</p></div>
    `;
    $("#errSel").addEventListener("change", (e) => { state.errorType = e.target.value; fillLift(); });
    $("#stageSel").addEventListener("change", (e) => { state.stage = e.target.value; fillLift(); });
    $("#minN").addEventListener("change", (e) => {
      state.minN = Math.max(1, parseInt(e.target.value, 10) || 10);
      fillLift();
    });
    await fillLift();
  }

  function sortRows(rows, spec) {
    const { key, dir } = spec;
    return [...rows].sort((a, b) => {
      const av = a[key], bv = b[key];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === "string") return av.localeCompare(bv) * dir;
      return (av - bv) * dir;
    });
  }

  async function fillLift() {
    const reqId = ++state._liftReq;
    const data = await api("/api/lift", {
      error_type: state.errorType,
      stage: state.stage,
      min_n: state.minN,
    });
    if (reqId !== state._liftReq) return;
    const body = $("#liftBody");
    const rows = sortRows(data.rows, state.sortLift);
    body.innerHTML = `
      <p class="muted">Baseline ${fmtPct(data.baseline)} (${fmtInt(data.errors)} / ${fmtInt(data.n)}). ${fmtInt(rows.length)} setting values with n ≥ ${data.min_n}.</p>
      <table>
        <thead>
          <tr>
            ${[
              ["setting", "Setting", ""],
              ["label", "Value", ""],
              ["n", "n", "num"],
              ["errors", "Errors", "num"],
              ["rate", "Rate", "num"],
              ["lift", "Lift", "num"],
              ["excess", "Excess", "num"],
              ["share", "Share", "num"],
              ["p_value", "p", "num"],
            ].map(([k, l, cls]) =>
              `<th class="${cls}" data-sort="${k}">${l}${state.sortLift.key === k ? (state.sortLift.dir < 0 ? " ▾" : " ▴") : ""}</th>`
            ).join("")}
          </tr>
        </thead>
        <tbody>
          ${rows.map((r) => {
            const liftCls = (r.lift ?? 1) >= 1.4 ? "lift-hi" : (r.lift ?? 1) <= 0.7 ? "lift-lo" : "";
            return `<tr data-click="1" data-setting="${esc(r.setting)}" data-value="${esc(String(r.value))}">
              <td>${esc(r.setting)}</td>
              <td>${esc(r.label)}</td>
              <td class="num">${fmtInt(r.n)}</td>
              <td class="num">${fmtInt(r.errors)}</td>
              <td class="num">${fmtPct(r.rate)}</td>
              <td class="num ${liftCls}">${fmtLift(r.lift)}</td>
              <td class="num">${r.excess == null ? "—" : (r.excess >= 0 ? "+" : "") + r.excess.toFixed(1)}</td>
              <td class="num">${fmtPct(r.share)}</td>
              <td class="num">${fmtP(r.p_value)}</td>
            </tr>`;
          }).join("")}
        </tbody>
      </table>
    `;
    body.querySelectorAll("th[data-sort]").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.sort;
        if (state.sortLift.key === key) state.sortLift.dir *= -1;
        else state.sortLift = { key, dir: key === "setting" || key === "label" ? 1 : -1 };
        fillLift();
      });
    });
    body.querySelector("tbody")?.addEventListener("click", (ev) => {
      const tr = ev.target.closest("tr");
      if (!tr) return;
      const extra = {
        setting: tr.dataset.setting,
        error_type: state.errorType,
      };
      if (state.stage) extra.stage = state.stage;
      if (tr.dataset.value === UNSET) extra.unset = 1;
      else extra.value = tr.dataset.value;
      openRuns({
        title: `${tr.dataset.setting} = ${tr.dataset.value === UNSET ? "(unset)" : tr.dataset.value}`,
        extra,
      });
    });
  }

  async function renderCrosstab() {
    $("#main").innerHTML = `
      <div class="row">
        <label>Rows
          <select id="rowSel">${settingOptions(state.rowSetting)}</select>
        </label>
        <label>Columns
          <select id="colSel">${settingOptions(state.colSetting)}</select>
        </label>
        <label>Metric error
          <select id="ctErr">${errorOptions(state.crosstabError)}</select>
        </label>
      </div>
      <p class="hint">Each cell is that error’s rate among all runs with those settings (errors÷n). Heat is absolute for Any failure, scaled to the grid max for a specific error. Click a cell for matching seeds.</p>
      <div id="ctBody" class="card"><p class="muted">Loading…</p></div>
    `;
    $("#rowSel").addEventListener("change", (e) => { state.rowSetting = e.target.value; fillCrosstab(); });
    $("#colSel").addEventListener("change", (e) => { state.colSetting = e.target.value; fillCrosstab(); });
    $("#ctErr").addEventListener("change", (e) => { state.crosstabError = e.target.value; fillCrosstab(); });
    await fillCrosstab();
  }

  async function fillCrosstab() {
    const body = $("#ctBody");
    if (state.rowSetting === state.colSetting) {
      body.innerHTML = `<p class="err">Pick two different settings.</p>`;
      return;
    }
    const reqId = ++state._crosstabReq;
    const requestedError = state.crosstabError;
    const data = await api("/api/crosstab", {
      row: state.rowSetting,
      col: state.colSetting,
      error_type: requestedError,
    });
    // Ignore stale responses when metric/rows/cols changed mid-flight.
    if (reqId !== state._crosstabReq) return;

    const activeError = data.error_type || requestedError || ANY_FAIL;
    const filtered = activeError !== ANY_FAIL;
    const metric = filtered ? "error_rate" : "fail_rate";
    const metricName = filtered ? activeError : "Any failure";
    let maxRate = 0;
    for (const row of data.cells) {
      for (const cell of row) {
        const r = cell[metric];
        if (r != null && r > maxRate) maxRate = r;
      }
    }
    // Specific-error rates are often << 100%; color relative to the grid max.
    const heatScale = filtered ? Math.max(maxRate, 1e-9) : 1;
    const scaleNote = filtered
      ? ` · heat scaled to max ${fmtPct(maxRate, 1)} in this grid`
      : "";
    body.innerHTML = `
      <p class="muted">${esc(metricName)} rate · ${esc(data.row_setting)} × ${esc(data.col_setting)}${scaleNote}</p>
      <div style="overflow:auto">
        <table class="heat">
          <thead>
            <tr>
              <th></th>
              ${data.cols.map((c) => `<th>${esc(c.label)}</th>`).join("")}
            </tr>
          </thead>
          <tbody>
            ${data.rows.map((r, i) => `
              <tr>
                <th>${esc(r.label)}</th>
                ${data.cols.map((c, j) => {
                  const cell = data.cells[i][j];
                  if (!cell.n) return `<td><div class="cell empty">—</div></td>`;
                  const rate = cell[metric];
                  const heat = rate == null ? null : rate / heatScale;
                  const countLine = filtered
                    ? `${fmtInt(cell.errors)}/${fmtInt(cell.n)}`
                    : `n=${fmtInt(cell.n)}`;
                  const tip = filtered
                    ? `${r.label} × ${c.label}: ${fmtInt(cell.errors)} ${activeError} / ${fmtInt(cell.n)} runs (${fmtPct(rate)})`
                    : `${r.label} × ${c.label}: ${fmtInt(cell.fail)} fails / ${fmtInt(cell.n)} runs (${fmtPct(rate)})`;
                  return `<td><div class="cell" data-rv="${esc(String(r.value))}" data-cv="${esc(String(c.value))}"
                    style="background:${heatColor(heat)}" title="${esc(tip)}">
                    <div class="pct">${fmtPct(rate, filtered ? 1 : 0)}</div>
                    <div class="n">${esc(countLine)}</div>
                  </div></td>`;
                }).join("")}
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    `;
    body.onclick = (ev) => {
      const cell = ev.target.closest(".cell[data-rv]");
      if (!cell) return;
      const extra = {
        setting: data.row_setting,
        setting2: data.col_setting,
      };
      if (filtered) extra.error_type = activeError;
      if (cell.dataset.rv === UNSET) extra.unset = 1;
      else extra.value = cell.dataset.rv;
      if (cell.dataset.cv === UNSET) extra.unset2 = 1;
      else extra.value2 = cell.dataset.cv;
      openRuns({
        title: `${data.row_setting}=${cell.dataset.rv} × ${data.col_setting}=${cell.dataset.cv}`,
        extra,
      });
    };
  }

  function setDrawerOpen(open) {
    document.documentElement.classList.toggle("drawer-open", open);
    document.body.classList.toggle("drawer-open", open);
  }

  function isScrollableY(el) {
    if (!el || el === document.body || el === document.documentElement) return false;
    const style = window.getComputedStyle(el);
    const oy = style.overflowY;
    if (oy !== "auto" && oy !== "scroll" && oy !== "overlay") return false;
    return el.scrollHeight > el.clientHeight + 1;
  }

  function scrollParentY(start) {
    let el = start;
    while (el && el !== document.body && el !== document.documentElement) {
      if (isScrollableY(el)) return el;
      el = el.parentElement;
    }
    return null;
  }

  function onDrawerWheel(ev) {
    if (!state.drawer) return;
    const drawer = $("#drawer");
    if (!drawer || !drawer.contains(ev.target)) {
      // Scrim / page behind: never scroll the main document.
      ev.preventDefault();
      return;
    }
    const scroller = scrollParentY(ev.target) || $("#drawerBody");
    if (!scroller) {
      ev.preventDefault();
      return;
    }
    const delta = ev.deltaY;
    const top = scroller.scrollTop;
    const max = scroller.scrollHeight - scroller.clientHeight;
    const atTop = top <= 0;
    const atBottom = top >= max - 1;
    if ((delta < 0 && atTop) || (delta > 0 && atBottom) || max <= 0) {
      // Absorb overscroll so it cannot chain to the page behind.
      ev.preventDefault();
    }
  }

  function onDrawerTouchMove(ev) {
    if (!state.drawer) return;
    const drawer = $("#drawer");
    if (!drawer || !drawer.contains(ev.target)) {
      ev.preventDefault();
      return;
    }
    // Allow touch scrolling only inside an actual scrollable region.
    if (!scrollParentY(ev.target)) ev.preventDefault();
  }

  async function openRuns({ title, extra }) {
    state.drawer = { title, extra };
    state.drawerOffset = 0;
    $("#drawer").hidden = false;
    $("#scrim").hidden = false;
    setDrawerOpen(true);
    $("#drawerTitle").textContent = title;
    await fillDrawer();
  }

  function closeDrawer() {
    state.drawer = null;
    $("#drawer").hidden = true;
    $("#scrim").hidden = true;
    $("#drawerBody").innerHTML = "";
    setDrawerOpen(false);
  }

  async function fillDrawer() {
    const d = state.drawer;
    if (!d) return;
    const data = await api("/api/runs", {
      ...d.extra,
      limit: 50,
      offset: state.drawerOffset,
    });
    $("#drawerSub").textContent = `${fmtInt(data.total)} matching runs`;
    const rows = data.runs.map((r) => `
      <tr data-click="1" data-id="${r.id}">
        <td>${fmtWhen(r.timestamp)}</td>
        <td>${r.seed ?? "—"}</td>
        <td>${r.success ? '<span class="val ok">ok</span>' : '<span class="val fail">fail</span>'}</td>
        <td>${esc(r.stage || "—")}</td>
        <td>${esc(r.error_type || "—")}</td>
        <td class="num">${fmtDur(r.duration_ms)}</td>
      </tr>
    `).join("");
    $("#drawerBody").innerHTML = `
      <table>
        <thead><tr><th>When</th><th>Seed</th><th>Result</th><th>Stage</th><th>Error</th><th class="num">Dur</th></tr></thead>
        <tbody>${rows || `<tr><td colspan="6" class="muted">No matching runs.</td></tr>`}</tbody>
      </table>
      <div class="pager">
        <button type="button" id="prevPage" ${state.drawerOffset <= 0 ? "disabled" : ""}>Prev</button>
        <span>${state.drawerOffset + 1}–${Math.min(state.drawerOffset + data.runs.length, data.total)} of ${fmtInt(data.total)}</span>
        <button type="button" id="nextPage" ${state.drawerOffset + data.runs.length >= data.total ? "disabled" : ""}>Next</button>
      </div>
      <div id="runDetail"></div>
    `;
    $("#drawerBody").querySelector("tbody")?.addEventListener("click", (ev) => {
      const tr = ev.target.closest("tr[data-id]");
      if (tr) showRun(Number(tr.dataset.id));
    });
    $("#prevPage")?.addEventListener("click", () => {
      state.drawerOffset = Math.max(0, state.drawerOffset - 50);
      fillDrawer();
    });
    $("#nextPage")?.addEventListener("click", () => {
      state.drawerOffset += 50;
      fillDrawer();
    });
  }

  async function showRun(id) {
    const r = await api(`/api/run/${id}`);
    const box = $("#runDetail");
    if (!box) return;
    const settings = (r.settings || []).map((s) =>
      `<div><span class="k">${esc(s.name)}</span> <span class="v">${esc(s.label)}</span></div>`
    ).join("");
    box.innerHTML = `
      <h3 style="margin:1rem 0 .4rem">Seed ${esc(r.seed)} · ${r.success ? "success" : "failure"}</h3>
      <p class="muted">${fmtWhen(r.timestamp)} · ${esc(r.app_version || "")} · ${fmtDur(r.duration_ms)} · ${esc(r.settings_code || "")}</p>
      <p><button type="button" class="ghost" id="copySeed">Copy seed</button></p>
      <div class="settings-grid">${settings}</div>
      <div class="log">${esc(r.log || "(no log stored)")}</div>
    `;
    $("#copySeed")?.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(String(r.seed ?? ""));
      } catch {
        /* ignore */
      }
    });
    box.scrollIntoView({ block: "nearest" });
  }

  function fillSuiteSelect(suites) {
    const sel = $("#suiteSel");
    if (!sel) return;
    const names = (suites || []).map((s) => s.suite);
    if (!state.suite || !names.includes(state.suite)) {
      state.suite = names.includes("mystery") ? "mystery" : (names[0] || null);
    }
    sel.innerHTML = (suites || []).map((s) =>
      `<option value="${esc(s.suite)}" ${s.suite === state.suite ? "selected" : ""}>${esc(s.suite)} (${fmtInt(s.n)})</option>`
    ).join("") || `<option value="">(no runs)</option>`;
  }

  function setReloading(on) {
    const btn = $("#reloadBtn");
    if (!btn) return;
    btn.disabled = !!on;
    btn.textContent = on ? "Loading…" : "Reload";
  }

  async function load() {
    const main = $("#main");
    const reqId = ++state._loadReq;
    setReloading(true);
    refreshTimeWindow();
    try {
      const [health, meta, ov] = await Promise.all([
        api("/api/health"),
        api("/api/meta"),
        api("/api/overview"),
      ]);
      if (reqId !== state._loadReq) return;
      state.health = health;
      $("#dbPath").textContent = state.health.db || "";
      if (!state.health.ok) {
        main.innerHTML = `<p class="err">${esc(state.health.error || "Database unavailable")}</p>`;
        return;
      }
      state.meta = meta;
      fillSuiteSelect(state.meta.suites || state.health.suites || []);
      state.hides = ov.hides || state.meta.hides || state.hides || [];
      renderKpis(ov);
      if (state.tab === "overview") await renderOverview(ov);
      else if (state.tab === "setting") await renderSetting();
      else if (state.tab === "lift") await renderLift();
      else if (state.tab === "crosstab") await renderCrosstab();
    } catch (err) {
      if (reqId !== state._loadReq) return;
      main.innerHTML = `<p class="err">${esc(err.message || err)}</p>`;
    } finally {
      if (reqId === state._loadReq) setReloading(false);
    }
  }

  function bind() {
    $$(".presets [data-preset]").forEach((btn) => {
      btn.addEventListener("click", () => {
        applyPreset(btn.dataset.preset);
        if (btn.dataset.preset !== "custom") load();
      });
    });
    $("#applyCustom").addEventListener("click", () => { applyCustom(); load(); });
    $("#reloadBtn").addEventListener("click", () => load());
    $("#suiteSel")?.addEventListener("change", (e) => {
      state.suite = e.target.value || null;
      load();
    });
    $$(".tabs [data-tab]").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.tab = btn.dataset.tab;
        $$(".tabs [data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === state.tab));
        load();
      });
    });
    $("#drawerClose").addEventListener("click", closeDrawer);
    $("#scrim").addEventListener("click", closeDrawer);
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") closeDrawer();
    });
    document.addEventListener("wheel", onDrawerWheel, { passive: false, capture: true });
    document.addEventListener("touchmove", onDrawerTouchMove, { passive: false, capture: true });
  }

  bind();
  load();
})();
