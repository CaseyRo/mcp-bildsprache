// Vanilla JS gallery. Uses fflate (exposed on window) for client-side ZIP.
// URL query string is the source of truth for filter + view state.

const EM_DASH = "—";
const SOFT_ZIP_CAP_MB = 250;
const SOFT_ZIP_CAP = SOFT_ZIP_CAP_MB * 1024 * 1024;
const DEBOUNCE_MS = 250;
const API_LIMIT = 500;

const state = {
  items: [],
  total: 0,
  selected: new Set(), // set of entry.path
  lastClickedPath: null,
  view: "grid",
  filters: {
    brand: [],
    platform: "",
    from: "",
    to: "",
    q: "",
  },
};

// ---------------------------------------------------------------------------
// URL state serialization (tested as a pure helper in tests)
// ---------------------------------------------------------------------------

export function stateToQueryString(view, filters) {
  const params = new URLSearchParams();
  if (view && view !== "grid") params.set("view", view);
  if (filters.brand && filters.brand.length > 0) {
    params.set("brand", filters.brand.join(","));
  }
  if (filters.platform) params.set("platform", filters.platform);
  if (filters.from) params.set("from", filters.from);
  if (filters.to) params.set("to", filters.to);
  if (filters.q) params.set("q", filters.q);
  const s = params.toString();
  return s ? "?" + s : "";
}

export function queryStringToState(search) {
  const params = new URLSearchParams(search || "");
  const view = params.get("view") === "list" ? "list" : "grid";
  const brandRaw = params.get("brand");
  return {
    view,
    filters: {
      brand: brandRaw ? brandRaw.split(",").filter(Boolean) : [],
      platform: params.get("platform") || "",
      from: params.get("from") || "",
      to: params.get("to") || "",
      q: params.get("q") || "",
    },
  };
}

// Derive a ZIP filename from an entry path. Basename by default; prefixed
// with the brand dir when two selections would collide.
export function zipFilenameFor(path, counts) {
  const parts = path.split("/");
  const base = parts[parts.length - 1];
  if (counts && counts[base] && counts[base] > 1 && parts.length > 1) {
    return parts.slice(-2).join("/");
  }
  return base;
}

export function countBasenames(paths) {
  const out = {};
  for (const p of paths) {
    const base = p.split("/").pop();
    out[base] = (out[base] || 0) + 1;
  }
  return out;
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "dataset") Object.assign(n.dataset, v);
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v === true) n.setAttribute(k, "");
    else if (v === false || v == null) {
      /* skip */
    } else n.setAttribute(k, String(v));
  }
  for (const c of children) {
    if (c == null) continue;
    if (typeof c === "string") n.appendChild(document.createTextNode(c));
    else n.appendChild(c);
  }
  return n;
}

function dash(value) {
  if (value === null || value === undefined || value === "") return EM_DASH;
  return String(value);
}

function formatDate(iso) {
  if (!iso) return EM_DASH;
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return EM_DASH;
    return d.toISOString().slice(0, 10);
  } catch (_e) {
    return EM_DASH;
  }
}

function renderGrid() {
  const root = document.getElementById("grid");
  root.replaceChildren();
  const frag = document.createDocumentFragment();
  for (const entry of state.items) {
    const selected = state.selected.has(entry.path);
    const basename = entry.path.split("/").pop();
    const dl = el(
      "a",
      {
        class: "dl",
        href: entry.hosted_url,
        download: basename,
        title: "Download this image",
        onclick: (e) => e.stopPropagation(),
      },
      "↓",
    );
    const card = el(
      "div",
      {
        class: "card" + (selected ? " selected" : ""),
        dataset: { path: entry.path },
        onclick: (e) => onItemClick(e, entry.path),
      },
      el("img", {
        src: entry.hosted_url,
        alt: entry.prompt || entry.path,
        loading: "lazy",
        decoding: "async",
      }),
      el(
        "div",
        { class: "meta" },
        el("span", {}, dash(entry.brand)),
        el("span", {}, `${entry.width}×${entry.height}`),
        dl,
      ),
    );
    frag.appendChild(card);
  }
  root.appendChild(frag);
}

function renderList() {
  const body = document.getElementById("list-body");
  body.replaceChildren();
  const frag = document.createDocumentFragment();
  for (const entry of state.items) {
    const selected = state.selected.has(entry.path);
    const basename = entry.path.split("/").pop();
    const tr = el(
      "tr",
      {
        class: "row" + (selected ? " selected" : ""),
        dataset: { path: entry.path },
        onclick: (e) => onItemClick(e, entry.path),
      },
      el("td", {}, dash(entry.prompt)),
      el("td", {}, dash(entry.brand)),
      el("td", {}, formatDate(entry.created_at)),
      el("td", {}, `${entry.width}×${entry.height}`),
      el("td", {}, dash(entry.model)),
      el("td", {}, dash(entry.cost_estimate)),
      el(
        "td",
        {},
        el(
          "a",
          {
            href: entry.hosted_url,
            download: basename,
            title: "Download",
            onclick: (e) => e.stopPropagation(),
          },
          "↓",
        ),
      ),
    );
    frag.appendChild(tr);
  }
  body.appendChild(frag);
}

function applyView() {
  const grid = document.getElementById("grid");
  const list = document.getElementById("list");
  const empty = document.getElementById("empty");
  const isEmpty = state.items.length === 0;
  empty.hidden = !isEmpty;
  if (state.view === "list") {
    grid.hidden = true;
    list.hidden = isEmpty;
    renderList();
  } else {
    list.hidden = true;
    grid.hidden = isEmpty;
    renderGrid();
  }
  document.getElementById("view-grid").classList.toggle("active", state.view === "grid");
  document.getElementById("view-list").classList.toggle("active", state.view === "list");
}

function updateSelectedCountUI() {
  document.getElementById("selected-count").textContent = String(state.selected.size);

  let totalBytes = 0;
  for (const entry of state.items) {
    if (state.selected.has(entry.path)) totalBytes += entry.file_size || 0;
  }
  const btn = document.getElementById("download-zip");
  btn.disabled = state.selected.size === 0 || totalBytes > SOFT_ZIP_CAP;
  if (totalBytes > SOFT_ZIP_CAP) {
    btn.title = `Selection exceeds ${SOFT_ZIP_CAP_MB} MB — download in batches`;
  } else if (state.selected.size === 0) {
    btn.title = "No images selected";
  } else {
    btn.title = `Download ${state.selected.size} image(s) as a ZIP (~${Math.round(totalBytes / 1024 / 1024)} MB)`;
  }
}

function setStatus(msg) {
  document.getElementById("status").textContent = msg || "";
}

// ---------------------------------------------------------------------------
// Selection
// ---------------------------------------------------------------------------

function onItemClick(event, path) {
  if (event.shiftKey && state.lastClickedPath) {
    const order = state.items.map((e) => e.path);
    const a = order.indexOf(state.lastClickedPath);
    const b = order.indexOf(path);
    if (a !== -1 && b !== -1) {
      const [lo, hi] = a < b ? [a, b] : [b, a];
      for (let i = lo; i <= hi; i++) state.selected.add(order[i]);
      state.lastClickedPath = path;
      applyView();
      updateSelectedCountUI();
      return;
    }
  }
  if (state.selected.has(path)) state.selected.delete(path);
  else state.selected.add(path);
  state.lastClickedPath = path;
  applyView();
  updateSelectedCountUI();
}

function selectAllVisible() {
  for (const entry of state.items) state.selected.add(entry.path);
  applyView();
  updateSelectedCountUI();
}

function clearSelection() {
  state.selected.clear();
  state.lastClickedPath = null;
  applyView();
  updateSelectedCountUI();
}

// ---------------------------------------------------------------------------
// Download (single + ZIP)
// ---------------------------------------------------------------------------

async function downloadZip() {
  const selectedPaths = state.items
    .filter((e) => state.selected.has(e.path))
    .map((e) => ({ path: e.path, url: e.hosted_url }));
  if (selectedPaths.length === 0) return;

  setStatus(`Fetching ${selectedPaths.length} images…`);
  const counts = countBasenames(selectedPaths.map((e) => e.path));
  const entries = {};
  for (const e of selectedPaths) {
    try {
      const res = await fetch(e.url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const buf = new Uint8Array(await res.arrayBuffer());
      const name = zipFilenameFor(e.path, counts);
      entries[name] = buf;
    } catch (err) {
      setStatus(`Failed to fetch ${e.url}: ${err}`);
      return;
    }
  }

  setStatus("Compressing…");
  const fflate = window.fflate;
  if (!fflate || typeof fflate.zip !== "function") {
    setStatus("fflate not loaded");
    return;
  }
  fflate.zip(entries, (err, data) => {
    if (err) {
      setStatus(`zip failed: ${err}`);
      return;
    }
    const blob = new Blob([data], { type: "application/zip" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `bildsprache-${new Date().toISOString().slice(0, 10)}.zip`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
    setStatus(`Downloaded ${selectedPaths.length} images as ZIP.`);
  });
}

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

async function fetchImages() {
  const params = new URLSearchParams();
  if (state.filters.brand.length > 0) params.set("brand", state.filters.brand.join(","));
  if (state.filters.platform) params.set("platform", state.filters.platform);
  if (state.filters.from) params.set("from", state.filters.from);
  if (state.filters.to) params.set("to", state.filters.to);
  if (state.filters.q) params.set("q", state.filters.q);
  params.set("limit", String(API_LIMIT));
  const res = await fetch(`/gallery/api/images?${params.toString()}`);
  if (!res.ok) {
    setStatus(`Failed to load: HTTP ${res.status}`);
    return;
  }
  const data = await res.json();
  state.items = data.items || [];
  state.total = data.total || 0;
  document.getElementById("total").textContent = String(state.total);
  applyView();
  updateSelectedCountUI();
}

async function triggerReindex() {
  setStatus("Reindexing…");
  try {
    const res = await fetch("/gallery/api/reindex", { method: "POST" });
    if (res.ok) {
      const data = await res.json();
      setStatus(`Reindexed (${data.total} entries).`);
      await fetchImages();
    } else {
      setStatus(`Reindex failed: HTTP ${res.status}`);
    }
  } catch (e) {
    setStatus(`Reindex failed: ${e}`);
  }
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

function debounce(fn, wait) {
  let t = null;
  return (...args) => {
    if (t) clearTimeout(t);
    t = setTimeout(() => {
      t = null;
      fn(...args);
    }, wait);
  };
}

function syncUrlFromState() {
  const qs = stateToQueryString(state.view, state.filters);
  const next = window.location.pathname + qs;
  if (window.location.pathname + window.location.search !== next) {
    history.replaceState(null, "", next);
  }
}

function readStateFromUrl() {
  const parsed = queryStringToState(window.location.search);
  state.view = parsed.view;
  state.filters = parsed.filters;

  document.getElementById("filter-platform").value = state.filters.platform;
  document.getElementById("filter-from").value = state.filters.from;
  document.getElementById("filter-to").value = state.filters.to;
  document.getElementById("filter-q").value = state.filters.q;
  // Brand is populated from items on first fetch.
}

function populateBrandOptions() {
  const brandSel = document.getElementById("filter-brand");
  const seen = new Set();
  for (const entry of state.items) if (entry.brand) seen.add(entry.brand);
  const sorted = [...seen].sort();
  const current = new Set(state.filters.brand);
  brandSel.replaceChildren(
    ...sorted.map((b) => {
      const opt = document.createElement("option");
      opt.value = b;
      opt.textContent = b;
      if (current.has(b)) opt.selected = true;
      return opt;
    }),
  );
}

function wireEvents() {
  document.getElementById("view-grid").addEventListener("click", () => setView("grid"));
  document.getElementById("view-list").addEventListener("click", () => setView("list"));
  document.getElementById("select-all").addEventListener("click", selectAllVisible);
  document.getElementById("clear-selection").addEventListener("click", clearSelection);
  document.getElementById("download-zip").addEventListener("click", downloadZip);
  document.getElementById("reindex").addEventListener("click", triggerReindex);

  const debouncedFetch = debounce(async () => {
    syncUrlFromState();
    await fetchImages();
    populateBrandOptions();
  }, DEBOUNCE_MS);

  const q = document.getElementById("filter-q");
  q.addEventListener("input", () => {
    state.filters.q = q.value;
    debouncedFetch();
  });

  for (const id of ["filter-platform", "filter-from", "filter-to"]) {
    const field = id.replace("filter-", "");
    const el = document.getElementById(id);
    el.addEventListener("change", () => {
      state.filters[field] = el.value;
      syncUrlFromState();
      fetchImages().then(populateBrandOptions);
    });
  }
  const brandSel = document.getElementById("filter-brand");
  brandSel.addEventListener("change", () => {
    state.filters.brand = [...brandSel.selectedOptions].map((o) => o.value);
    syncUrlFromState();
    fetchImages();
  });

  document.addEventListener("keydown", (e) => {
    const tag = (document.activeElement && document.activeElement.tagName) || "";
    const inInput = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
    if (inInput) {
      // Only `esc` is usable to defocus while typing.
      if (e.key === "Escape") document.activeElement.blur();
      return;
    }
    if (e.key === "g") {
      setView("grid");
    } else if (e.key === "l") {
      setView("list");
    } else if (e.key === "a") {
      selectAllVisible();
    } else if (e.key === "Escape") {
      clearSelection();
    } else if (e.key === "/") {
      e.preventDefault();
      document.getElementById("filter-q").focus();
    }
  });

  window.addEventListener("popstate", () => {
    readStateFromUrl();
    fetchImages().then(populateBrandOptions);
  });
}

function setView(v) {
  state.view = v === "list" ? "list" : "grid";
  syncUrlFromState();
  applyView();
}

async function main() {
  readStateFromUrl();
  wireEvents();
  await fetchImages();
  populateBrandOptions();
  setStatus("Ready.");
}

// Bootstrap — but skip when running in a non-DOM test harness.
if (typeof document !== "undefined" && document.getElementById("main")) {
  main();
}
