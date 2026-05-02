/* CourtVision Dashboard — board renderers
 * Reads from CVState ONLY. Never fetches.
 * Renders the design system's Player · Prop · Grade pattern.
 */
(function (global) {
  "use strict";

  // Map raw grade strings (A+, B-, etc) to the design system grade-chip class suffix.
  function gradeClass(grade) {
    const g = (grade || "").toString().trim();
    const map = {
      "A+": "ap", "A": "a", "A-": "am",
      "B+": "bp", "B": "b", "B-": "bm",
      "C+": "cp", "C": "c", "C-": "cm",
      "D+": "dp", "D": "d", "D-": "dm",
      "F": "f",
    };
    return map[g] || "c";
  }

  function escape(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function renderEmpty(container, message, error) {
    container.innerHTML =
      `<div class="cv-empty">${escape(message)}` +
      (error ? `<span class="cv-empty-detail mono">${escape(error)}</span>` : "") +
      `</div>`;
  }

  function renderBoardTable(rows) {
    const body = rows.map((r, i) => `
      <tr data-player="${escape(r.player)}" data-idx="${i}" tabindex="0" role="button" aria-label="Open breakdown for ${escape(r.player)}">
        <td class="name">${escape(r.player)}</td>
        <td class="prop">${escape(r.prop)}</td>
        <td class="grade-cell"><span class="cv-grade cv-grade-${gradeClass(r.grade)}">${escape(r.grade)}</span></td>
      </tr>
    `).join("");
    return `
      <div class="cv-table-wrap">
        <table class="cv-table">
          <thead>
            <tr><th>Player</th><th>Prop</th><th class="num">Grade</th></tr>
          </thead>
          <tbody>${body}</tbody>
        </table>
      </div>
    `;
  }

  function renderSGPGroups(rows) {
    const groups = new Map();
    for (const r of rows) {
      const k = r.game_id || "—";
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(r);
    }
    return [...groups.entries()].map(([gameId, legs]) => `
      <div class="cv-sgp-group">
        <header class="cv-sgp-header">
          <span class="cv-eyebrow">Game</span>
          <span class="mono">${escape(gameId)}</span>
          <span class="cv-sgp-count">${legs.length} legs</span>
        </header>
        ${renderBoardTable(legs)}
      </div>
    `).join("");
  }

  function attachRowHandlers(container, kind) {
    container.querySelectorAll("tr[data-player]").forEach((tr) => {
      const open = () => {
        const player = tr.getAttribute("data-player");
        const idx = Number(tr.getAttribute("data-idx"));
        if (global.CVBreakdown && typeof global.CVBreakdown.open === "function") {
          global.CVBreakdown.open({ player, kind, idx });
        }
      };
      tr.addEventListener("click", open);
      tr.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
      });
    });
  }

  function renderElite() {
    const el = document.getElementById("elite-board");
    if (!el) return;
    const s = global.CVState.get();
    const r = s.boards.elite;
    if (!r.ok && !r.data.length) return renderEmpty(el, "No elite picks for this date.", r.error);
    if (!r.data.length) return renderEmpty(el, "No elite picks for this date.");
    el.innerHTML = renderBoardTable(r.data);
    attachRowHandlers(el, "elite");
  }

  function renderFullMarket() {
    const el = document.getElementById("full-market-board");
    if (!el) return;
    const s = global.CVState.get();
    const r = s.boards.full_market;
    if (!r.ok && !r.data.length) return renderEmpty(el, "No full-market picks for this date.", r.error);
    if (!r.data.length) return renderEmpty(el, "No full-market picks for this date.");
    el.innerHTML = renderBoardTable(r.data);
    attachRowHandlers(el, "full_market");
  }

  function renderSGP() {
    const el = document.getElementById("sgp-board");
    if (!el) return;
    const s = global.CVState.get();
    const r = s.boards.sgp;
    if (!r.ok && !r.data.length) return renderEmpty(el, "SGP builder disabled or no legs returned.", r.error);
    if (!r.data.length) return renderEmpty(el, "SGP builder disabled or no legs returned.");
    el.innerHTML = renderSGPGroups(r.data);
    attachRowHandlers(el, "sgp");
  }

  function renderAll() {
    renderElite();
    renderFullMarket();
    renderSGP();
  }

  global.CVBoards = { renderAll, renderElite, renderFullMarket, renderSGP, gradeClass };
})(window);
