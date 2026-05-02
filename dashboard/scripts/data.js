/* CourtVision Dashboard — data layer
 * Pure fetch + parse + validate. Returns SAFE fallback shapes on missing/invalid data
 * so renderers never crash. All callers consume `{ ok, data, error }` envelopes.
 */
(function (global) {
  "use strict";

  // -------- CSV parser (small, no dep) --------
  function parseCSV(text) {
    if (!text || typeof text !== "string") return [];
    const rows = [];
    let field = "", row = [], inQ = false, i = 0;
    while (i < text.length) {
      const c = text[i];
      if (inQ) {
        if (c === '"') {
          if (text[i + 1] === '"') { field += '"'; i += 2; continue; }
          inQ = false; i++; continue;
        }
        field += c; i++; continue;
      }
      if (c === '"') { inQ = true; i++; continue; }
      if (c === ',') { row.push(field); field = ""; i++; continue; }
      if (c === '\r') { i++; continue; }
      if (c === '\n') { row.push(field); rows.push(row); field = ""; row = []; i++; continue; }
      field += c; i++;
    }
    if (field.length || row.length) { row.push(field); rows.push(row); }
    if (!rows.length) return [];
    const header = rows.shift().map((h) => h.trim());
    return rows
      .filter((r) => r.length === header.length && r.some((v) => v !== ""))
      .map((r) => Object.fromEntries(header.map((h, idx) => [h, r[idx]])));
  }

  // -------- Validation --------
  // Required columns per board kind. Missing required cols => invalid.
  const BOARD_SCHEMAS = {
    elite:        { required: ["entity_name", "selection"], optional: ["grade", "market_type", "team", "opponent"] },
    full_market:  { required: ["entity_name", "selection"], optional: ["grade", "market_type", "team", "opponent"] },
    sgp:          { required: ["entity_name", "selection"], optional: ["grade", "game_id", "leg_index"] },
  };

  function validateRows(rows, schema) {
    if (!Array.isArray(rows) || !rows.length) return { ok: true, rows: [], warnings: ["empty"] };
    const have = new Set(Object.keys(rows[0] || {}));
    const missing = schema.required.filter((c) => !have.has(c));
    if (missing.length) {
      return { ok: false, rows: [], warnings: ["missing_columns:" + missing.join(",")] };
    }
    return { ok: true, rows, warnings: [] };
  }

  // Normalize a row to the dashboard's canonical shape: { player, prop, grade, raw }
  function normalizeBoardRow(r) {
    const grade = (r.grade || r.letter_grade || r.tier || "").toString().trim() || "—";
    return {
      player: (r.entity_name || r.player || r.player_name || "").toString(),
      prop: (r.selection || r.bet || "").toString(),
      grade,
      market_type: (r.market_type || "").toString(),
      team: (r.team || "").toString(),
      opponent: (r.opponent || "").toString(),
      game_id: (r.game_id || "").toString(),
      raw: r,
    };
  }

  // -------- Fetchers --------
  async function fetchText(path) {
    try {
      const res = await fetch(path, { cache: "no-store" });
      if (!res.ok) return { ok: false, status: res.status, text: "" };
      const text = await res.text();
      return { ok: true, status: 200, text };
    } catch (e) {
      return { ok: false, status: 0, text: "", error: String(e) };
    }
  }

  async function fetchJSON(path) {
    const r = await fetchText(path);
    if (!r.ok) return { ok: false, data: null, error: "HTTP " + r.status };
    try {
      return { ok: true, data: JSON.parse(r.text), error: null };
    } catch (e) {
      return { ok: false, data: null, error: "JSON parse: " + String(e) };
    }
  }

  // -------- Public API --------
  // Each loader returns { ok, data, error, warnings }
  // `data` is ALWAYS a safe fallback when ok=false, so renderers can render an empty state.

  async function loadBoard(date, kind) {
    const path = `../outputs/runtime/boards/${date}/${kind}_props.csv`;
    const r = await fetchText(path);
    if (!r.ok) {
      return { ok: false, data: [], error: r.status === 404 ? "not_found" : ("HTTP " + r.status), warnings: [] };
    }
    const rows = parseCSV(r.text);
    const schema = BOARD_SCHEMAS[kind] || BOARD_SCHEMAS.elite;
    const v = validateRows(rows, schema);
    if (!v.ok) {
      return { ok: false, data: [], error: "schema_invalid", warnings: v.warnings };
    }
    return { ok: true, data: v.rows.map(normalizeBoardRow), error: null, warnings: v.warnings };
  }

  async function loadDiagnostics(date) {
    const r = await fetchJSON(`../outputs/runtime/diagnostics/${date}/summary.json`);
    if (!r.ok) {
      return { ok: false, data: { games_analyzed: 0, players_evaluated: 0, markets_evaluated: 0, elite_count: 0, rejected_count: 0 }, error: r.error };
    }
    const d = r.data || {};
    return {
      ok: true,
      data: {
        games_analyzed: Number(d.games_analyzed || 0),
        players_evaluated: Number(d.players_evaluated || 0),
        markets_evaluated: Number(d.markets_evaluated || 0),
        elite_count: Number(d.elite_count || 0),
        rejected_count: Number(d.rejected_count || 0),
        run_completed_at: d.run_completed_at || null,
      },
      error: null,
    };
  }

  async function loadHealth(date) {
    const r = await fetchJSON(`../outputs/runtime/diagnostics/${date}/data_status.json`);
    const fallback = [
      { source: "provider",  status: "unknown", detail: "" },
      { source: "injuries",  status: "unknown", detail: "" },
      { source: "markets",   status: "unknown", detail: "" },
      { source: "baselines", status: "unknown", detail: "" },
    ];
    if (!r.ok) return { ok: false, data: fallback, error: r.error };
    const sources = Array.isArray(r.data?.sources) ? r.data.sources : null;
    if (!sources) return { ok: false, data: fallback, error: "missing_sources_array" };
    const allowed = new Set(["ok", "warn", "error", "stale", "unknown"]);
    const cleaned = sources.map((s) => ({
      source: String(s.source || s.name || ""),
      status: allowed.has(String(s.status)) ? String(s.status) : "unknown",
      detail: String(s.detail || s.message || ""),
    })).filter((s) => s.source);
    return { ok: true, data: cleaned.length ? cleaned : fallback, error: null };
  }

  async function loadEdgeBreakdown(date, player) {
    const safe = String(player).replace(/[^a-z0-9_-]/gi, "_");
    const r = await fetchJSON(`../outputs/runtime/diagnostics/${date}/edge_breakdown_${safe}.json`);
    if (!r.ok) return { ok: false, data: null, error: r.error };
    return { ok: true, data: r.data, error: null };
  }

  async function loadAvailableDates() {
    const r = await fetchJSON(`../outputs/runtime/index.json`);
    if (r.ok && Array.isArray(r.data?.dates) && r.data.dates.length) {
      return { ok: true, data: r.data.dates, error: null };
    }
    const today = new Date().toISOString().slice(0, 10);
    return { ok: false, data: [today], error: r.error || "no_index" };
  }

  global.CVData = {
    loadBoard,
    loadDiagnostics,
    loadHealth,
    loadEdgeBreakdown,
    loadAvailableDates,
    parseCSV,
  };
})(window);
