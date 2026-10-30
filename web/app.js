// The Piano: browse the library, and play it on the Disklavier through pianod on the Pi.
// The library is library.json, published beside this page; everything that plays or changes
// something goes to pianod's API (/api/, which murdoch passes through to the Pi). There is no
// sound in the browser: the piano is the only output.

const $ = (sel, el = document) => el.querySelector(sel);
const view = $("#view");

// --- the library ----------------------------------------------------------------------------
const lib = { composers: [], pianists: [], works: new Map(), bySlug: new Map(), pianistBySlug: new Map(), people: {} };
const ERAS = [
  ["baroque", "Baroque", "1600–1750"],
  ["classical", "Classical", "1750–1820"],
  ["romantic", "Romantic", "1820–1910"],
  ["modern", "Modern", "1900 –"],
];

async function loadLibrary() {
  const [data, people] = await Promise.all([
    fetch("library.json", { cache: "no-cache" }).then(r => r.json()),
    fetch("people.json", { cache: "no-cache" }).then(r => (r.ok ? r.json() : {})).catch(() => ({})),
  ]);
  lib.people = people || {};
  lib.pianists = data.pianists || [];
  for (const p of lib.pianists) lib.pianistBySlug.set(p.slug, p);
  lib.composers = (data.composers || []).filter(c => c.works && c.works.length);
  for (const c of lib.composers) {
    lib.bySlug.set(c.slug, c);
    for (const w of c.works) {
      w.composer = c;
      w.pianist = w.performer ? lib.pianistBySlug.get(w.performer) : null;
      lib.works.set(w.id, w);
    }
  }
}

const fold = s => (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const mmss = s => { s = Math.max(0, Math.floor(s || 0)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
const dates = (b, d) => (b && d ? `${b}–${d}` : b ? `b. ${b}` : "");
const plural = (n, one, many = one + "s") => `${n} ${n === 1 ? one : many}`;
const totalTime = ws => { const s = ws.reduce((a, w) => a + (w.duration || 0), 0); const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60); return h ? `${h} h ${m} min` : `${m} min`; };

// --- pianod ---------------------------------------------------------------------------------
const piano = { reachable: false, state: null, at: 0, favourites: new Set() };

async function api(cmd, args = {}) {
  let r;
  try {
    const res = await fetch(`api/${cmd}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(args) });
    r = await res.json();
  } catch {
    toast("The piano isn't answering", true);
    return null;
  }
  if (!r.ok) toast(r.error || "That didn't work", true);
  return r.ok ? r : null;
}

function connect() {
  setStatus("connecting");
  const es = new EventSource("api/events");
  let alive = null;
  const dead = () => { es.close(); clearTimeout(alive); piano.reachable = false; setStatus("offline"); setTimeout(connect, 3000); };
  const watch = () => { clearTimeout(alive); alive = setTimeout(dead, 40000); };   // heartbeats every 15 s
  es.onmessage = e => {
    watch();
    const msg = JSON.parse(e.data);
    if (msg.type === "state") onState(msg);
    else if (msg.type === "keys") keys.press(msg.on || [], msg.off || []);
  };
  es.onopen = watch;
  es.onerror = dead;
}

function setStatus(kind) {
  const el = $("#status");
  const st = piano.state;
  let label = "Connecting", title = "Looking for the piano…";
  if (kind === "offline") { label = "Not connected"; title = "The Pi isn't answering. Is it switched on?"; }
  if (kind === "connected") {
    if (st && !st.midi_out) { kind = "offline"; label = "Not connected"; title = "The Pi is on, but the piano's MIDI cable isn't connected"; }
    else { label = "Connected"; title = `The piano is ready · ${st ? st.works : ""} pieces`; }
  }
  el.dataset.state = kind;
  el.title = title;
  $(".label", el).textContent = label;
  document.body.classList.toggle("offline", kind !== "connected");
}

function onState(st) {
  const first = !piano.reachable;
  piano.reachable = true;
  piano.state = st;
  piano.at = performance.now();
  setStatus("connected");
  renderPlayer();
  markPlaying();
  if (first) {
    loadFavourites();
    if (location.hash.replace(/^#\/?/, "") === "") home();       // the collections come from the piano
  }
}

async function loadFavourites() {
  const r = await api("favourites");
  if (!r) return;
  piano.favourites = new Set(r.works.map(w => w.id));
  document.querySelectorAll(".star").forEach(b => b.classList.toggle("on", piano.favourites.has(b.dataset.id)));
  if (location.hash.startsWith("#/favourites")) route();
}

async function play(args) {
  if (await api("play", args)) toast("Playing on the piano");
}

async function star(id, btn) {
  const r = await api("fav", { id });
  if (!r) return;
  r.fav ? piano.favourites.add(id) : piano.favourites.delete(id);
  document.querySelectorAll(`.star[data-id="${id}"]`).forEach(b => b.classList.toggle("on", r.fav));
  toast(r.fav ? "Added to favourites" : "Removed from favourites");
  if (piano.state && piano.state.id === id) piano.state.fav = r.fav;
}

// --- the player bar ---------------------------------------------------------------------------
function position() {
  const st = piano.state;
  if (!st) return 0;
  const extra = st.playing ? (performance.now() - piano.at) / 1000 : 0;
  return Math.min(st.len || 0, (st.pos || 0) + extra);
}

function renderPlayer() {
  const st = piano.state || {};
  const player = $("#player");
  const has = !!st.title;
  player.dataset.empty = String(!has);
  player.classList.toggle("is-playing", !!st.playing);
  const w = st.id && lib.works.get(st.id);
  $(".np-title").textContent = has ? [st.title, st.movement].filter(Boolean).join(" · ") : "Nothing playing";
  const sub = has
    ? [st.composer, st.performer ? `played by ${st.performer}` : null, st.roll, st.count > 1 ? `${st.index + 1} of ${st.count}` : null].filter(Boolean).join(" · ")
    : piano.reachable ? "Choose something, or let the piano surprise you" : "The piano isn't connected";
  $(".np-sub").textContent = sub;
  $("#np").onclick = () => { if (w) location.hash = `#/composer/${w.composer.slug}`; };
  $("#toggle").setAttribute("aria-label", st.playing ? "Pause" : "Play");
  for (const id of ["prev", "next"]) $("#" + id).disabled = !(st.count > 1);
  $("#stop").disabled = !has;
  if (document.activeElement !== $("#vol")) $("#vol").value = st.volume || 8;
  $("#len").textContent = mmss(st.len);
  tick();
}

function tick() {
  const st = piano.state;
  const p = position();
  $("#pos").textContent = mmss(p);
  $("#fill").style.width = st && st.len ? `${(100 * p) / st.len}%` : "0";
}
setInterval(tick, 250);

$("#toggle").onclick = () => (piano.state && piano.state.title ? api("toggle") : play({ all: true, shuffle: true }));
$("#prev").onclick = () => api("prev");
$("#next").onclick = () => api("next");
$("#stop").onclick = () => api("stop");
$("#vol").onchange = e => api("volume", { v: Number(e.target.value) });
$("#bar").onclick = e => {
  const st = piano.state;
  if (!st || !st.len) return;
  const r = e.currentTarget.getBoundingClientRect();
  api("seek", { t: ((e.clientX - r.left) / r.width) * st.len });
};

// The keyboard: 88 keys, lit as the piano plays them (its own key sensors, echoed by pianod).
const keys = (() => {
  const canvas = $("#keys");
  const ctx = canvas.getContext("2d");
  const down = new Map();          // key -> time it went down (for the glow)
  const black = k => [1, 3, 6, 8, 10].includes(k % 12);
  let geo = [];
  function layout() {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    canvas.width = w * dpr; canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const ww = w / 52;
    geo = [];
    let wi = 0;
    for (let k = 21; k <= 108; k++) {
      if (black(k)) geo[k] = { x: wi * ww - ww * 0.3, w: ww * 0.6, h: h * 0.6, black: true };
      else { geo[k] = { x: wi * ww, w: ww, h, black: false }; wi++; }
    }
    draw();
  }
  function draw() {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    ctx.clearRect(0, 0, w, h);
    for (const pass of [false, true]) {
      for (let k = 21; k <= 108; k++) {
        const g = geo[k];
        if (!g || g.black !== pass) continue;
        const lit = down.has(k);
        ctx.fillStyle = lit ? (pass ? "#e2c172" : "#c9a24a") : pass ? "#0c0a09" : "#4a4038";
        ctx.fillRect(g.x + (pass ? 0 : 0.5), 0, g.w - (pass ? 0 : 1), g.h);
      }
    }
  }
  window.addEventListener("resize", layout);
  requestAnimationFrame(layout);
  return {
    press(on, off) {
      for (const k of off) down.delete(k);
      for (const k of on) down.set(k, performance.now());
      draw();
    },
  };
})();

// --- views ------------------------------------------------------------------------------------
const icon = {
  play: '<svg viewBox="0 0 24 24"><path d="M7 4v16l13-8z"/></svg>',
  shuffle: '<svg viewBox="0 0 24 24"><path d="M16 3h5v5l-1.8-1.8L5.4 20 4 18.6 17.8 4.8zM4 5.4 5.4 4l5 5L9 10.4zm10.6 9.2 1.4-1.4 3.2 3.2L21 14.6V20h-5.4l1.8-1.8z"/></svg>',
  star: '<svg viewBox="0 0 24 24"><path d="M12 3.5l2.6 5.6 6.1.7-4.5 4.2 1.2 6L12 17l-5.4 3 1.2-6-4.5-4.2 6.1-.7z"/></svg>',
};

function workRow(w, i, { showComposer = false, hidePianist = false } = {}) {
  const sub = [];
  if (showComposer) sub.push(esc(w.composer.short || w.composer.name));
  if (w.pianist && !hidePianist) sub.push(`played by <a class="roll" href="#/pianist/${w.pianist.slug}">${esc(w.pianist.name)}</a>`);
  if (w.roll) sub.push(esc(w.roll) + " roll");
  return `<li class="work" data-id="${w.id}">
    <span class="num"><span class="n-text">${i + 1}</span><span class="eq"><i></i><i></i><i></i></span></span>
    <div><div class="title">${esc(w.title)}${w.movement ? ` <span class="sub">· ${esc(w.movement)}</span>` : ""}</div>
      ${sub.length ? `<div class="sub">${sub.join(" · ")}</div>` : ""}</div>
    <span class="dur">${w.duration ? mmss(w.duration) : ""}</span>
    <button class="star${piano.favourites.has(w.id) ? " on" : ""}" data-id="${w.id}" aria-label="Favourite">${icon.star}</button>
  </li>`;
}

function worksList(ws, opts) {
  return `<ol class="works">${ws.map((w, i) => workRow(w, i, opts)).join("")}</ol>`;
}

function bindWorks(ws) {
  view.querySelectorAll(".work").forEach(li => {
    li.onclick = e => {
      if (e.target.closest("a")) return;
      const btn = e.target.closest(".star");
      if (btn) return star(btn.dataset.id, btn);
      // Play from here to the end of the list, so the piano carries on.
      const i = ws.findIndex(w => w.id === li.dataset.id);
      play({ ids: ws.slice(i).map(w => w.id) });
    };
  });
  markPlaying();
}

function markPlaying() {
  const st = piano.state;
  view.querySelectorAll(".work").forEach(li => {
    const on = !!st && li.dataset.id === st.id;
    li.classList.toggle("playing", on);
    li.classList.toggle("paused", on && !st.playing);
  });
}

function actions(ws, args) {
  return `<div class="actions">
    <button class="btn primary" data-act="all">${icon.play} Play all</button>
    <button class="btn" data-act="shuffle">${icon.shuffle} Shuffle</button>
    <span class="note">${plural(ws.length, "piece")}${ws.some(w => w.duration) ? ` · ${totalTime(ws)}` : ""}</span>
  </div>`;
}
function bindActions(ws) {
  view.querySelectorAll("[data-act]").forEach(b => {
    b.onclick = () => play({ ids: ws.map(w => w.id), shuffle: b.dataset.act === "shuffle" });
  });
}

function portrait(kind, slug, name) {
  const p = (lib.people[kind] || {})[slug];
  if (p && p.image) return `<img class="portrait" src="${esc(p.image)}" alt="" loading="lazy" referrerpolicy="no-referrer">`;
  return `<div class="portrait none" aria-hidden="true">${esc((name || "?")[0])}</div>`;
}
function summary(kind, slug) {
  const p = (lib.people[kind] || {})[slug];
  return p && p.summary ? `<p class="lede">${esc(p.summary.length > 420 ? p.summary.slice(0, 420).replace(/\s\S*$/, "") + "…" : p.summary)}</p>` : "";
}

async function home() {
  const byEra = Object.fromEntries(ERAS.map(([k]) => [k, []]));
  for (const c of lib.composers) if (byEra[c.era]) byEra[c.era].push(c);
  const eraCol = ([k, name, span]) => {
    const cs = byEra[k].sort((a, b) => b.works.length - a.works.length || a.sort.localeCompare(b.sort)).slice(0, 12);
    return `<div class="era"><h3>${name}</h3><div class="span">${span}</div><ol>${cs.map((c, i) =>
      `<li class="${i < 3 ? "major" : ""}"><a href="#/composer/${c.slug}"><span class="who">${esc(c.short || c.name)}</span><span class="n">${c.works.length}</span></a></li>`).join("")}</ol></div>`;
  };
  const pianists = [...lib.pianists].sort((a, b) => b.works.length - a.works.length).slice(0, 24);
  view.innerHTML = `
    <div class="hero">
      <div>
        <p class="eyebrow">${lib.works.size} pieces · ${lib.composers.length} composers · ${lib.pianists.length} pianists</p>
        <h1>What shall the<br>piano <em>play?</em></h1>
      </div>
      <div class="actions"><button class="btn primary" id="surprise">${icon.shuffle} Surprise me</button></div>
    </div>
    <section id="shelves" hidden><div class="section-head"><h2>Collections</h2></div><div class="shelf"></div></section>
    <section><div class="section-head"><h2>Composers</h2><a href="#/composers">All, A–Z</a></div><div class="eras">${ERAS.map(eraCol).join("")}</div></section>
    <section><div class="section-head"><h2>Great pianists, on rolls</h2><a href="#/pianists">All pianists</a></div>
      <p class="note" style="margin:-8px 0 16px">Reproducing piano rolls: their own playing, recorded a century ago, on this piano tonight.</p>
      <div class="wall">${pianists.map(p => `<a class="chip" href="#/pianist/${p.slug}">${esc(p.name)}<span class="n">${p.works.length}</span></a>`).join("")}</div></section>`;
  $("#surprise").onclick = () => play({ all: true, shuffle: true });
  const r = piano.reachable ? await api("shelves") : null;
  if (r && r.collections && r.collections.length && location.hash.replace(/^#\/?/, "") === "") {
    const shelf = $("#shelves");
    shelf.hidden = false;
    $(".shelf", shelf).innerHTML = r.collections.map(c =>
      `<a class="card" href="#/collection/${c.slug}"><h3>${esc(c.name)}</h3><span class="count">${plural(c.count, "piece")}</span></a>`).join("");
  }
}

function composers() {
  const cs = [...lib.composers].filter(c => c.slug !== "unknown").sort((a, b) => fold(a.sort).localeCompare(fold(b.sort)));
  const letters = new Map();
  for (const c of cs) {
    const L = fold(c.sort)[0].toUpperCase();
    if (!letters.has(L)) letters.set(L, []);
    letters.get(L).push(c);
  }
  view.innerHTML = `<div class="hero"><div><p class="eyebrow">${cs.length} composers</p><h1>Composers</h1></div></div>
    <section><div class="az">${[...letters].map(([L, list]) => `<div class="letter"><h3>${L}</h3>${list.map(c =>
      `<a href="#/composer/${c.slug}"><span class="who">${esc(c.short && c.first ? `${c.short}, ${c.first}` : c.name)}</span><span class="meta">${dates(c.born, c.died)} · ${c.works.length}</span></a>`).join("")}</div>`).join("")}</div></section>`;
}

function composer(slug) {
  const c = lib.bySlug.get(slug);
  if (!c) return notFound();
  const ws = [...c.works].sort((a, b) => fold(a.title).localeCompare(fold(b.title), undefined, { numeric: true }));
  const era = ERAS.find(e => e[0] === c.era);
  view.innerHTML = `<div class="person">${portrait("composers", slug, c.short || c.name)}<div>
      <p class="eyebrow">Composer</p><h1>${esc(c.name)}</h1>
      <div class="dates">${dates(c.born, c.died)}${era ? `<b>${era[1]}</b>` : ""}</div>${summary("composers", slug)}
      ${actions(ws)}</div></div>
    <section>${worksList(ws)}</section>`;
  bindActions(ws);
  bindWorks(ws);
}

function pianists() {
  const ps = [...lib.pianists].sort((a, b) => fold(a.short || a.name).localeCompare(fold(b.short || b.name)));
  view.innerHTML = `<div class="hero"><div><p class="eyebrow">${ps.length} pianists, on piano rolls</p><h1>Pianists</h1>
      <p class="lede">Reproducing rolls recorded the playing itself: every note, its timing and its weight. These are the pianists, a century on.</p></div></div>
    <section><div class="az">${ps.map(p => `<a href="#/pianist/${p.slug}"><span class="who">${esc(p.name)}</span><span class="meta">${dates(p.born, p.died)} · ${p.works.length}</span></a>`).join("")}</div></section>`;
}

function pianist(slug) {
  const p = lib.pianistBySlug.get(slug);
  if (!p) return notFound();
  const ws = p.works.map(id => lib.works.get(id)).filter(Boolean);
  view.innerHTML = `<div class="person">${portrait("pianists", slug, p.short || p.name)}<div>
      <p class="eyebrow">Pianist, on rolls</p><h1>${esc(p.name)}</h1>
      <div class="dates">${dates(p.born, p.died)}</div>${summary("pianists", slug)}
      ${actions(ws)}</div></div>
    <section>${worksList(ws, { showComposer: true, hidePianist: true })}</section>`;
  bindActions(ws);
  bindWorks(ws);
}

async function listFromPiano(title, eyebrow, cmd, args, emptyText) {
  view.innerHTML = `<div class="hero"><div><p class="eyebrow">${esc(eyebrow)}</p><h1>${esc(title)}</h1></div></div><section><p class="empty">One moment…</p></section>`;
  if (!piano.reachable) {
    $("section", view).innerHTML = `<p class="empty">This comes from the piano, and it isn't connected just now.</p>`;
    return;
  }
  const r = await api(cmd, args);
  const ws = r ? r.works.map(w => lib.works.get(w.id)).filter(Boolean) : [];
  $("section", view).innerHTML = ws.length ? `${actions(ws)}<div style="margin-top:20px">${worksList(ws, { showComposer: true })}</div>` : `<p class="empty">${emptyText}</p>`;
  bindActions(ws);
  bindWorks(ws);
}

async function collection(slug) {
  const r = piano.reachable ? await api("shelves") : null;
  const c = r && r.collections.find(x => x.slug === slug);
  return listFromPiano(c ? c.name : "Collection", "Collection", "collection", { slug }, "Nothing here.");
}

function favourites() {
  return listFromPiano("Favourites", "Starred", "favourites", {}, "Nothing starred yet. Tap the ☆ beside any piece, or hold A on the panel.");
}

function search(q) {
  const f = fold(q).trim();
  if (!f) return home();
  const words = f.split(/\s+/);
  const hit = text => { const t = fold(text); return words.every(w => t.includes(w)); };
  const cs = lib.composers.filter(c => hit(`${c.name} ${c.short}`)).slice(0, 12);
  const ps = lib.pianists.filter(p => hit(p.name)).slice(0, 12);
  const ws = [...lib.works.values()].filter(w =>
    hit(`${w.title} ${w.movement || ""} ${w.composer.name} ${w.composer.short} ${w.pianist ? w.pianist.name : ""} ${w.roll || ""}`)).slice(0, 80);
  view.innerHTML = `<div class="hero"><div><p class="eyebrow">Search</p><h1>“${esc(q)}”</h1></div></div><div class="results">
    ${cs.length ? `<h2>Composers</h2><div class="wall">${cs.map(c => `<a class="chip" href="#/composer/${c.slug}">${esc(c.name)}<span class="n">${c.works.length}</span></a>`).join("")}</div>` : ""}
    ${ps.length ? `<h2>Pianists</h2><div class="wall">${ps.map(p => `<a class="chip" href="#/pianist/${p.slug}">${esc(p.name)}<span class="n">${p.works.length}</span></a>`).join("")}</div>` : ""}
    ${ws.length ? `<h2>Pieces</h2>${worksList(ws, { showComposer: true })}` : ""}
    ${!cs.length && !ps.length && !ws.length ? `<p class="empty">Nothing matches. Try a composer's name, or a word from a title.</p>` : ""}</div>`;
  bindWorks(ws);
}

function notFound() {
  view.innerHTML = `<div class="hero"><div><h1>Not here</h1><p class="lede">That page isn't in the library. <a href="#/" style="color:var(--brass)">Back to the start</a>.</p></div></div>`;
}

// --- routing ----------------------------------------------------------------------------------
function route() {
  const [path, query] = location.hash.replace(/^#\/?/, "").split("?");
  const [page, arg] = path.split("/").map(decodeURIComponent);
  document.querySelectorAll(".nav a").forEach(a => a.toggleAttribute("aria-current", a.getAttribute("href") === `#/${page}`));
  if (page !== "search") $("#q").value = "";
  const pages = { "": home, composers, composer, pianists, pianist, collection, favourites,
                  search: () => search(new URLSearchParams(query).get("q") || "") };
  (pages[page] || notFound)(arg);
  if (page !== "search") window.scrollTo(0, 0);
}

let searchTimer;
$("#q").addEventListener("input", e => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    const q = e.target.value;
    history.replaceState(null, "", q.trim() ? `#/search?q=${encodeURIComponent(q)}` : "#/");
    route();
  }, 120);
});
document.addEventListener("keydown", e => {
  if (e.key === "/" && document.activeElement.tagName !== "INPUT") { e.preventDefault(); $("#q").focus(); }
  if (e.key === " " && !["INPUT", "BUTTON", "A"].includes(document.activeElement.tagName)) { e.preventDefault(); $("#toggle").click(); }
});

let toastTimer;
function toast(text, bad = false) {
  const t = $("#toast");
  t.textContent = text;
  t.classList.toggle("bad", bad);
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
}

window.addEventListener("hashchange", route);
loadLibrary().then(() => { route(); renderPlayer(); }).catch(() => {
  view.innerHTML = `<div class="hero"><div><h1>The library didn't load</h1><p class="lede">library.json is missing or unreadable.</p></div></div>`;
});
connect();
