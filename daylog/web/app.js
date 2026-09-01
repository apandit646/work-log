/* daylog web UI — plain JS, no framework, no build step. */

const API = "/api";
const CATEGORY_COLORS = ["--cat-0", "--cat-1", "--cat-2", "--cat-3", "--cat-4", "--cat-5", "--cat-6", "--cat-7"];
const AUTOSAVE_DELAY_MS = 2000;

const state = {
  date: todayISO(),
  report: null,
  saveTimer: null,
  categoryColorMap: {},
};

function $(sel) { return document.querySelector(sel); }

// --- small date/format helpers ------------------------------------------

function todayISO() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function shiftDate(iso, days) {
  const d = new Date(iso + "T00:00:00");
  d.setDate(d.getDate() + days);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function fmtMinutes(minutes) {
  const total = Math.round(minutes || 0);
  const h = Math.floor(total / 60);
  const m = total % 60;
  if (h && m) return `${h}h ${m}m`;
  if (h) return `${h}h`;
  return `${m}m`;
}

function fmtTime(iso) {
  return new Date(iso).toTimeString().slice(0, 5);
}

function fmtTimestamp(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s == null ? "" : String(s);
  return div.innerHTML;
}

// --- API helpers ---------------------------------------------------------

async function apiGet(path) {
  const res = await fetch(API + path);
  if (!res.ok) throw new Error(await errorDetail(res));
  return res.json();
}

async function apiSend(method, path, body) {
  const res = await fetch(API + path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await errorDetail(res));
  return res.json();
}

async function errorDetail(res) {
  try {
    const data = await res.json();
    return data.detail || res.statusText;
  } catch {
    return res.statusText;
  }
}

// --- view switching --------------------------------------------------

function setView(view) {
  document.querySelectorAll(".view").forEach((el) => el.classList.toggle("active", el.id === `view-${view}`));
  document.querySelectorAll(".nav-btn").forEach((el) => el.classList.toggle("active", el.dataset.view === view));
  if (view === "today") loadToday(state.date);
  if (view === "week") loadWeek();
  if (view === "history") loadHistory();
  if (view === "settings") loadSettings();
}

document.querySelectorAll(".nav-btn").forEach((btn) => btn.addEventListener("click", () => setView(btn.dataset.view)));

// =========================================================================
// Today
// =========================================================================

async function loadToday(date) {
  state.date = date;
  $("#current-date").textContent = date;
  try {
    const report = await apiGet(`/days/${date}`);
    state.report = report;
    renderToday(report);
  } catch (err) {
    $("#meetings-list").innerHTML = `<p class="empty-note">Could not load: ${escapeHtml(err.message)}</p>`;
  }
}

function renderToday(report) {
  renderBadge(report.status);
  renderMetrics(report);
  renderTimeline(report);
  renderMeetings(report);
  renderCommits(report);
  renderWip(report);
  renderSummaryBox(report);
}

function renderBadge(status) {
  const badge = $("#status-badge");
  const label = status || "missed";
  badge.textContent = label;
  badge.className = "badge " + label;
}

function renderMetrics(report) {
  $("#metric-total").textContent = fmtMinutes(report.total_tracked_minutes);
  const coding = report.category_totals.find((c) => c.category === "Coding");
  $("#metric-coding").textContent = fmtMinutes(coding ? coding.minutes : 0);
  const commitCount = report.commits_by_repo.reduce((sum, r) => sum + r.commits.length, 0);
  $("#metric-commits").textContent = String(commitCount);
}

function categoryColor(name) {
  if (!(name in state.categoryColorMap)) {
    const idx = Object.keys(state.categoryColorMap).length % CATEGORY_COLORS.length;
    state.categoryColorMap[name] = CATEGORY_COLORS[idx];
  }
  return `var(${state.categoryColorMap[name]})`;
}

function renderTimeline(report) {
  state.categoryColorMap = {};
  const bar = $("#timeline-bar");
  bar.innerHTML = "";
  const dayMinutes = 24 * 60;

  for (const block of report.timeline) {
    const start = new Date(block.start);
    const end = new Date(block.end);
    const dayStart = new Date(start);
    dayStart.setHours(0, 0, 0, 0);
    const startMin = (start - dayStart) / 60000;
    const endMin = (end - dayStart) / 60000;
    const left = (startMin / dayMinutes) * 100;
    const width = Math.max(((endMin - startMin) / dayMinutes) * 100, 0.15);

    const seg = document.createElement("div");
    seg.className = "timeline-segment";
    seg.style.left = left + "%";
    seg.style.width = width + "%";
    seg.style.background = categoryColor(block.category);
    seg.addEventListener("mouseenter", (e) => showTooltip(e, block));
    seg.addEventListener("mousemove", positionTooltip);
    seg.addEventListener("mouseleave", hideTooltip);
    bar.appendChild(seg);
  }

  const legend = $("#timeline-legend");
  legend.innerHTML = report.category_totals
    .map(
      (item) =>
        `<div class="legend-item"><span class="legend-swatch" style="background:${categoryColor(item.category)}"></span>${escapeHtml(item.category)} &middot; ${fmtMinutes(item.minutes)}</div>`
    )
    .join("");
}

function showTooltip(e, block) {
  const tooltip = $("#timeline-tooltip");
  tooltip.textContent = `${block.app}${block.title ? " — " + block.title : ""} (${fmtTime(block.start)}–${fmtTime(block.end)})`;
  tooltip.hidden = false;
  positionTooltip(e);
}

function positionTooltip(e) {
  const tooltip = $("#timeline-tooltip");
  tooltip.style.left = e.clientX + 12 + "px";
  tooltip.style.top = e.clientY + 12 + "px";
}

function hideTooltip() {
  $("#timeline-tooltip").hidden = true;
}

function renderMeetings(report) {
  const el = $("#meetings-list");
  if (!report.meetings.length) {
    el.innerHTML = `<p class="empty-note">No meetings today.</p>`;
    return;
  }
  el.innerHTML = report.meetings
    .map((m) => {
      const time = m.all_day ? "All day" : `${fmtTime(m.start)}–${fmtTime(m.end)}`;
      return `<div class="list-row"><span>${escapeHtml(m.title)}</span><span class="muted">${time}</span></div>`;
    })
    .join("");
}

function renderCommits(report) {
  const el = $("#commits-list");
  if (!report.commits_by_repo.length) {
    el.innerHTML = `<p class="empty-note">No commits today.</p>`;
    return;
  }
  el.innerHTML = report.commits_by_repo
    .map(
      (rc) => `
    <div class="repo-group">
      <div class="repo-title">${escapeHtml(rc.repo)}
        <span class="muted">(<span class="stat-add">+${rc.additions}</span>/<span class="stat-del">-${rc.deletions}</span>)</span>
      </div>
      ${rc.commits
        .map(
          (c) =>
            `<div class="list-row"><span>${escapeHtml(c.subject)}</span><span class="muted">${c.hash.slice(0, 7)}</span></div>`
        )
        .join("")}
    </div>`
    )
    .join("");
}

function renderWip(report) {
  const el = $("#wip-list");
  const nonEmpty = report.wip_by_repo.filter((r) => r.files.length);
  if (!nonEmpty.length) {
    el.innerHTML = `<p class="empty-note">Nothing uncommitted.</p>`;
    return;
  }
  el.innerHTML = nonEmpty
    .map(
      (rw) => `
    <div class="repo-group">
      <div class="repo-title">${escapeHtml(rw.repo)}</div>
      ${rw.files
        .map(
          (f) =>
            `<div class="list-row"><span>${escapeHtml(f.path)}</span><span class="muted">${escapeHtml(f.status)}</span></div>`
        )
        .join("")}
    </div>`
    )
    .join("");
}

// --- summary box: edit, autosave, never lose typing on refresh ---------

function draftStorageKey(day) {
  return `daylog:unsaved-draft:${day}`;
}

function renderSummaryBox(report) {
  const textarea = $("#summary-text");
  const unsaved = readUnsavedDraft(report.day);
  textarea.value = unsaved !== null ? unsaved : report.current_text || "";
  textarea.disabled = report.status === "submitted";

  $("#last-edited").textContent = report.updated_at ? `Last edited ${fmtTimestamp(report.updated_at)}` : "";
  $("#save-indicator").textContent = unsaved !== null ? "Unsaved changes (will save shortly)" : "";

  // llm_used/llm_error only reflect the *last regenerate response* — a
  // plain page load (GET) never calls the LLM, so this only lights up
  // right after clicking Regenerate, not on every visit.
  const llmIndicator = $("#llm-indicator");
  if (report.llm_used) {
    llmIndicator.textContent = "Polished by Claude";
    llmIndicator.hidden = false;
  } else if (report.llm_error) {
    llmIndicator.textContent = `LLM polish skipped: ${report.llm_error}`;
    llmIndicator.hidden = false;
  } else {
    llmIndicator.hidden = true;
  }

  $("#btn-submit").hidden = report.status === "submitted";
  $("#btn-reopen").hidden = report.status !== "submitted";

  if (unsaved !== null) scheduleAutosave();
}

function readUnsavedDraft(day) {
  try {
    return localStorage.getItem(draftStorageKey(day));
  } catch {
    return null; // localStorage unavailable (private mode, etc.) — degrade silently
  }
}

function writeUnsavedDraft(day, value) {
  try {
    localStorage.setItem(draftStorageKey(day), value);
  } catch {
    /* ignore — see readUnsavedDraft */
  }
}

function clearUnsavedDraft(day) {
  try {
    localStorage.removeItem(draftStorageKey(day));
  } catch {
    /* ignore */
  }
}

$("#summary-text").addEventListener("input", () => {
  const day = state.date;
  writeUnsavedDraft(day, $("#summary-text").value); // survives a refresh immediately, not just after autosave
  $("#save-indicator").textContent = "Unsaved changes...";
  scheduleAutosave();
});

function scheduleAutosave() {
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(saveSummary, AUTOSAVE_DELAY_MS);
}

async function saveSummary() {
  const day = state.date;
  const text = $("#summary-text").value;
  try {
    const summary = await apiSend("PUT", `/days/${day}/summary`, { edited_md: text });
    clearUnsavedDraft(day);
    if (day === state.date) {
      $("#save-indicator").textContent = "Saved";
      $("#last-edited").textContent = `Last edited ${fmtTimestamp(summary.updated_at)}`;
      setTimeout(() => {
        if ($("#save-indicator").textContent === "Saved") $("#save-indicator").textContent = "";
      }, 2000);
    }
  } catch (err) {
    if (day === state.date) $("#save-indicator").textContent = `Could not save: ${err.message}`;
  }
}

$("#btn-regenerate").addEventListener("click", async () => {
  const day = state.date;
  const btn = $("#btn-regenerate");
  btn.disabled = true;
  try {
    const result = await apiSend("POST", `/days/${day}/regenerate`);
    clearUnsavedDraft(day);
    if (result.had_unsaved_edits) {
      $("#save-indicator").textContent = "Regenerated — your hand-edited text was kept, data sections refreshed.";
    }
    state.report = result;
    renderToday(result);
  } catch (err) {
    alert(`Could not regenerate: ${err.message}`);
  } finally {
    btn.disabled = false;
  }
});

$("#btn-copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("#summary-text").value);
    $("#save-indicator").textContent = "Copied to clipboard";
    setTimeout(() => {
      if ($("#save-indicator").textContent === "Copied to clipboard") $("#save-indicator").textContent = "";
    }, 2000);
  } catch (err) {
    alert(`Could not copy: ${err.message}`);
  }
});

$("#btn-submit").addEventListener("click", async () => {
  const day = state.date;
  try {
    await saveSummary();
    const summary = await apiSend("POST", `/days/${day}/submit`);
    await loadToday(day);
  } catch (err) {
    alert(`Could not submit: ${err.message}`);
  }
});

$("#btn-reopen").addEventListener("click", async () => {
  const day = state.date;
  try {
    await apiSend("POST", `/days/${day}/reopen`);
    await loadToday(day);
  } catch (err) {
    alert(`Could not reopen: ${err.message}`);
  }
});

$("#prev-day").addEventListener("click", () => loadToday(shiftDate(state.date, -1)));
$("#next-day").addEventListener("click", () => loadToday(shiftDate(state.date, 1)));

// =========================================================================
// Week — read-only dashboard, never touches collectors (Phase 9)
// =========================================================================

async function loadWeek() {
  const daysEl = $("#week-days");
  daysEl.innerHTML = "Loading…";
  try {
    const week = await apiGet("/week");
    renderWeek(week);
  } catch (err) {
    daysEl.innerHTML = `<p class="empty-note">Could not load: ${escapeHtml(err.message)}</p>`;
  }
}

function renderWeek(week) {
  $("#week-range").textContent = `Week of ${week.start} – ${week.end}`;
  $("#week-total").textContent = fmtMinutes(week.total_tracked_minutes);
  $("#week-commits").textContent = String(week.total_commits);

  const maxMinutes = Math.max(1, ...week.days.map((d) => d.total_tracked_minutes));
  $("#week-days").innerHTML = week.days
    .map((d) => {
      const pct = Math.min(100, (d.total_tracked_minutes / maxMinutes) * 100);
      const status = d.status ? d.status : "Missed";
      const statusClass = d.status ? "" : "status-missed";
      return `<div class="week-day-row">
        <span>${d.day}</span>
        <span class="week-day-bar-track"><span class="week-day-bar-fill" style="width:${pct}%"></span></span>
        <span>${fmtMinutes(d.total_tracked_minutes)}</span>
        <span class="week-day-status ${statusClass}">${escapeHtml(status)}</span>
      </div>`;
    })
    .join("");

  const catsEl = $("#week-categories");
  if (!week.category_totals.length) {
    catsEl.innerHTML = `<p class="empty-note">No activity recorded this week.</p>`;
    return;
  }
  catsEl.innerHTML = week.category_totals
    .map(
      (c, i) =>
        `<div class="legend-item"><span class="legend-swatch" style="background:var(${CATEGORY_COLORS[i % CATEGORY_COLORS.length]})"></span>${escapeHtml(c.category)} &middot; ${fmtMinutes(c.minutes)}</div>`
    )
    .join("");
}

// =========================================================================
// History
// =========================================================================

async function loadHistory() {
  const body = $("#history-body");
  body.innerHTML = `<tr><td colspan="4">Loading…</td></tr>`;
  try {
    const { days } = await apiGet("/days?limit=60");
    if (!days.length) {
      body.innerHTML = `<tr><td colspan="4" class="empty-note">No days tracked yet.</td></tr>`;
      return;
    }
    body.innerHTML = days
      .map((d) => {
        const statusHtml = d.status
          ? `<span class="badge ${d.status}">${d.status}</span>`
          : `<span class="status-missed">Missed</span>`;
        return `<tr data-day="${d.day}">
          <td>${d.day}</td>
          <td>${fmtMinutes(d.total_tracked_minutes)}</td>
          <td>${d.commit_count}</td>
          <td>${statusHtml}</td>
        </tr>`;
      })
      .join("");
    body.querySelectorAll("tr[data-day]").forEach((row) => {
      row.addEventListener("click", () => {
        setView("today");
        loadToday(row.dataset.day);
      });
    });
  } catch (err) {
    body.innerHTML = `<tr><td colspan="4" class="empty-note">Could not load: ${escapeHtml(err.message)}</td></tr>`;
  }
}

// =========================================================================
// Settings
// =========================================================================

async function loadSettings() {
  await Promise.all([loadSettingsConfig(), refreshTrackerStatus()]);
}

async function loadSettingsConfig() {
  try {
    const cfg = await apiGet("/config");
    $("#settings-scan-paths").value = cfg.git.scan_paths.join("\n");
    $("#settings-ics-urls").value = cfg.calendar.ics_urls.join("\n");
    $("#settings-llm-enabled").checked = cfg.llm.enabled;
    $("#settings-llm-model").value = cfg.llm.model;
    renderCategoryRows(cfg.categories);
  } catch (err) {
    $("#settings-save-indicator").textContent = `Could not load config: ${err.message}`;
  }
}

function renderCategoryRows(categories) {
  const el = $("#settings-categories");
  el.innerHTML = categories
    .map(
      (c) => `
    <div class="category-row">
      <input type="text" name="cat-name" value="${escapeHtml(c.name)}" placeholder="Category name">
      <input type="text" name="cat-keywords" value="${escapeHtml(c.keywords.join(", "))}" placeholder="keyword, keyword, ...">
    </div>`
    )
    .join("");
}

$("#btn-save-settings").addEventListener("click", async () => {
  const indicator = $("#settings-save-indicator");
  try {
    const cfg = await apiGet("/config");
    cfg.git.scan_paths = $("#settings-scan-paths").value.split("\n").map((s) => s.trim()).filter(Boolean);
    cfg.calendar.ics_urls = $("#settings-ics-urls").value.split("\n").map((s) => s.trim()).filter(Boolean);
    cfg.categories = Array.from($("#settings-categories").querySelectorAll(".category-row")).map((row) => ({
      name: row.querySelector('[name="cat-name"]').value.trim(),
      keywords: row
        .querySelector('[name="cat-keywords"]')
        .value.split(",")
        .map((k) => k.trim())
        .filter(Boolean),
    }));
    cfg.llm.enabled = $("#settings-llm-enabled").checked;
    cfg.llm.model = $("#settings-llm-model").value.trim() || cfg.llm.model;

    await apiSend("PUT", "/config", cfg);
    indicator.textContent = "Saved";
    setTimeout(() => {
      if (indicator.textContent === "Saved") indicator.textContent = "";
    }, 2000);
  } catch (err) {
    indicator.textContent = `Could not save: ${err.message}`;
  }
});

async function refreshTrackerStatus() {
  try {
    const status = await apiGet("/status");
    $("#tracker-status").textContent = status.tracker_running
      ? `running (pid ${status.tracker_pid})`
      : "not running";
  } catch (err) {
    $("#tracker-status").textContent = "unknown";
  }
}

$("#btn-tracker-start").addEventListener("click", async () => {
  await apiSend("POST", "/tracker/start");
  await refreshTrackerStatus();
});

$("#btn-tracker-stop").addEventListener("click", async () => {
  await apiSend("POST", "/tracker/stop");
  await refreshTrackerStatus();
});

$("#btn-run-doctor").addEventListener("click", async () => {
  const el = $("#doctor-results");
  el.innerHTML = "Running…";
  try {
    const { checks } = await apiGet("/doctor");
    el.innerHTML = checks
      .map(
        (c) => `
      <div class="doctor-row">
        <span class="doctor-mark ${c.ok ? "pass" : "fail"}">${c.ok ? "PASS" : "FAIL"}</span>
        <span>${escapeHtml(c.label)}${c.detail ? ` — ${escapeHtml(c.detail)}` : ""}</span>
      </div>`
      )
      .join("");
  } catch (err) {
    el.innerHTML = `<p class="empty-note">Could not run doctor: ${escapeHtml(err.message)}</p>`;
  }
});

// =========================================================================
// Boot
// =========================================================================

setView("today");
