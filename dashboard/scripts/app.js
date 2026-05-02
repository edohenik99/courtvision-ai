/* CourtVision Dashboard — bootstrap + centralized state
 * State shape (single source of truth):
 *   {
 *     selectedDate: "YYYY-MM-DD",
 *     availableDates: ["YYYY-MM-DD", ...],
 *     boards:      { elite: Result, full_market: Result, sgp: Result },
 *     diagnostics: Result,
 *     health:      Result,
 *     status:      "idle" | "loading" | "ready" | "error",
 *     lastError:   string | null
 *   }
 *
 * Result envelope (from CVData): { ok: bool, data: any, error: string|null, warnings?: string[] }
 *
 * Renderers (CVBoards, CVDiagnostics, CVBreakdown) MUST read from CVState.get().
 * Only the dispatcher in this file calls CVData.* and writes state.
 */
(function (global) {
  "use strict";

  // ----- Centralized state -----
  const listeners = new Set();
  const state = {
    selectedDate: null,
    availableDates: [],
    boards: {
      elite:       { ok: false, data: [], error: null, warnings: [] },
      full_market: { ok: false, data: [], error: null, warnings: [] },
      sgp:         { ok: false, data: [], error: null, warnings: [] },
    },
    diagnostics: { ok: false, data: { games_analyzed: 0, players_evaluated: 0, markets_evaluated: 0, elite_count: 0, rejected_count: 0 }, error: null },
    health:      { ok: false, data: [], error: null },
    status: "idle",
    lastError: null,
  };

  function get() { return state; }
  function subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); }
  function notify() { listeners.forEach((fn) => { try { fn(state); } catch (e) { console.error(e); } }); }
  function set(patch) { Object.assign(state, patch); notify(); }
  function setBoard(kind, result) { state.boards[kind] = result; notify(); }

  global.CVState = { get, subscribe, set, setBoard };

  // ----- Status pill -----
  function setStatus(s, msg) {
    state.status = s;
    state.lastError = s === "error" ? (msg || "Unknown error") : null;
    const pill = document.getElementById("run-status");
    if (!pill) return;
    pill.dataset.state = s;
    const labels = { idle: "Idle", loading: "Loading…", ready: "Ready", error: "Error" };
    const label = pill.querySelector(".cv-pill-label");
    if (label) label.textContent = msg || labels[s] || s;
  }

  // ----- Dispatcher: the only place that calls CVData and writes state -----
  async function loadDate(date) {
    if (!date) return;
    setStatus("loading", "Loading " + date + "…");
    set({ selectedDate: date });

    const [elite, full_market, sgp, diagnostics, health] = await Promise.all([
      CVData.loadBoard(date, "elite"),
      CVData.loadBoard(date, "full_market"),
      CVData.loadBoard(date, "sgp"),
      CVData.loadDiagnostics(date),
      CVData.loadHealth(date),
    ]);

    state.boards.elite = elite;
    state.boards.full_market = full_market;
    state.boards.sgp = sgp;
    state.diagnostics = diagnostics;
    state.health = health;
    notify();

    const anyOk = elite.ok || full_market.ok || diagnostics.ok || health.ok;
    setStatus(anyOk ? "ready" : "error", anyOk ? "Ready" : "No data found for " + date);
  }

  // ----- Render binding: renderers re-run on every state change -----
  function bindRenderers() {
    subscribe(() => {
      if (global.CVBoards)      CVBoards.renderAll();
      if (global.CVDiagnostics) CVDiagnostics.renderAll();
    });
  }

  // ----- Boot -----
  async function boot() {
    bindRenderers();

    const dateInput = document.getElementById("run-date");
    const refreshBtn = document.getElementById("refresh-btn");

    const dates = await CVData.loadAvailableDates();
    state.availableDates = dates.data;
    const initial = dates.data[0];
    if (dateInput) dateInput.value = initial;

    if (dateInput) {
      dateInput.addEventListener("change", (e) => {
        const v = e.target.value;
        if (v) loadDate(v);
      });
    }
    if (refreshBtn) {
      refreshBtn.addEventListener("click", () => {
        if (state.selectedDate) loadDate(state.selectedDate);
      });
    }

    await loadDate(initial);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})(window);
