// Keyboard cull deck (admin/cull.html). One big photo at a time, ranked by keeper score; K/X to
// decide, H/→ to skip, ← back, R to undecide, U to undo the last decision. Each decision is a
// same-origin fetch POST to the cull route (origin-based CSRF passes; HX-Request asks the server
// for an empty 204 so the deck never reloads). The page render is authoritative; this only mutates
// the embedded queue + DOM in step with the server. No framework — vanilla, behind the cull flag.
(function () {
  const app = document.getElementById("cull-app");
  if (!app) return;
  const dataEl = document.getElementById("cull-data");
  if (!dataEl) return;

  const galleryId = app.dataset.galleryId;
  let queue;
  try {
    queue = JSON.parse(dataEl.textContent) || [];
  } catch (e) {
    return;
  }
  if (!queue.length) return;

  const photo = document.getElementById("cull-photo");
  const stage = document.getElementById("cull-stage");
  const scoreBadge = document.getElementById("cull-scorebadge");
  const fileEl = document.getElementById("cull-file");
  const progressEl = document.getElementById("cull-progress");
  const grid = document.getElementById("cull-grid");
  const cells = Array.from(grid.querySelectorAll(".cull-cell"));

  let idx = 0;
  const undoStack = [];

  const pct = (s) => (s == null ? null : Math.round(s * 100));

  // --- network ------------------------------------------------------------
  async function post(url, params) {
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: {
          "HX-Request": "true",
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body: params,
      });
      return res.ok;
    } catch (e) {
      return false;
    }
  }
  const postDecision = (id, action) =>
    post(
      `/admin/galleries/${galleryId}/assets/${id}/cull`,
      new URLSearchParams({ action })
    );
  function postBulk(ids, action) {
    const body = new URLSearchParams();
    body.set("action", action);
    ids.forEach((id) => body.append("asset_ids", id));
    return post(`/admin/galleries/${galleryId}/assets/bulk-cull`, body);
  }

  // --- render -------------------------------------------------------------
  function renderCard() {
    const item = queue[idx];
    photo.src = `/admin/galleries/${galleryId}/cull/preview/${item.id}`;
    fileEl.textContent = item.file || "";
    progressEl.textContent = `${idx + 1} / ${queue.length}`;
    const p = pct(item.score);
    scoreBadge.textContent = p == null ? "unscored" : `score ${p}`;
    stage.classList.toggle("is-keep", item.state === "keep");
    stage.classList.toggle("is-cut", item.state === "cut");
    cells.forEach((c, i) => c.classList.toggle("is-current", i === idx));
    const cur = cells[idx];
    if (cur) cur.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function refreshCell(i) {
    const item = queue[i];
    cells[i].dataset.state = item.state || "";
  }

  function refreshCounts() {
    let keep = 0,
      cut = 0;
    queue.forEach((q) => {
      if (q.state === "keep") keep++;
      else if (q.state === "cut") cut++;
    });
    setText("c-keep", keep);
    setText("c-cut", cut);
    setText("c-undecided", queue.length - keep - cut);
  }
  function setText(id, v) {
    const el = document.getElementById(id);
    if (el) el.textContent = v;
  }

  // --- navigation ---------------------------------------------------------
  function go(i) {
    idx = Math.max(0, Math.min(queue.length - 1, i));
    renderCard();
  }

  // --- decisions ----------------------------------------------------------
  async function decide(action) {
    const item = queue[idx];
    const prev = item.state;
    const next = action === "restore" ? null : action;
    if (prev === next) {
      if (action !== "restore") go(idx + 1); // already in that state — just advance
      return;
    }
    const ok = await postDecision(item.id, action);
    if (!ok) return;
    undoStack.push({ at: idx, prev });
    item.state = next;
    refreshCell(idx);
    refreshCounts();
    if (action === "restore") renderCard();
    else go(idx + 1); // keep/cut: step forward
  }

  async function undo() {
    const last = undoStack.pop();
    if (!last) return;
    const item = queue[last.at];
    // re-assert the previous state: null -> restore, else keep/cut
    const action = last.prev == null ? "restore" : last.prev;
    const ok = await postDecision(item.id, action);
    if (!ok) {
      undoStack.push(last); // leave the stack intact on failure
      return;
    }
    item.state = last.prev;
    refreshCell(last.at);
    refreshCounts();
    go(last.at);
  }

  // --- threshold selector -------------------------------------------------
  const thr = document.getElementById("cull-threshold");
  const thrLabel = document.getElementById("thr-label");
  const thrCount = document.getElementById("thr-count");
  const thrCut = document.getElementById("thr-cut");
  const thrClear = document.getElementById("thr-clear");

  function selectedIdx() {
    const cutoff = Number(thr.value) / 100;
    if (!thr.value || thr.value === "0") return [];
    const out = [];
    queue.forEach((q, i) => {
      if (q.score != null && q.score < cutoff && q.state !== "cut") out.push(i);
    });
    return out;
  }
  function paintSelection() {
    const sel = new Set(selectedIdx());
    cells.forEach((c, i) => c.classList.toggle("is-selected", sel.has(i)));
    thrLabel.textContent = `${thr.value}%`;
    thrCount.textContent = sel.size;
    thrCut.disabled = sel.size === 0;
    thrClear.disabled = Number(thr.value) === 0 && sel.size === 0;
  }
  if (thr) {
    thr.addEventListener("input", paintSelection);
    thrClear.addEventListener("click", () => {
      thr.value = "0";
      paintSelection();
    });
    thrCut.addEventListener("click", async () => {
      const sel = selectedIdx();
      if (!sel.length) return;
      const ids = sel.map((i) => queue[i].id);
      const ok = await postBulk(ids, "cut");
      if (!ok) return;
      sel.forEach((i) => {
        queue[i].state = "cut";
        refreshCell(i);
      });
      refreshCounts();
      thr.value = "0";
      paintSelection();
      renderCard();
    });
  }

  // --- wiring -------------------------------------------------------------
  document.querySelectorAll("[data-act]").forEach((b) =>
    b.addEventListener("click", () => decide(b.dataset.act))
  );
  document.querySelectorAll("[data-nav]").forEach((b) =>
    b.addEventListener("click", () => go(idx + (b.dataset.nav === "next" ? 1 : -1)))
  );
  const undoBtn = document.querySelector("[data-undo]");
  if (undoBtn) undoBtn.addEventListener("click", undo);
  cells.forEach((c) =>
    c.addEventListener("click", () => go(Number(c.dataset.idx)))
  );

  document.addEventListener("keydown", (ev) => {
    const t = ev.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
    switch (ev.key.toLowerCase()) {
      case "k": decide("keep"); break;
      case "x": decide("cut"); break;
      case "r": decide("restore"); break;
      case "u": undo(); break;
      case "h": case "arrowright": go(idx + 1); break;
      case "arrowleft": go(idx - 1); break;
      default: return;
    }
    ev.preventDefault();
  });

  renderCard();
})();
