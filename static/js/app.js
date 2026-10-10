"use strict";
const API = "/api/v1";
const $ = (id) => document.getElementById(id);
// Safe wiring: a missing element logs a warning instead of crashing the whole script.
const on = (id, evt, fn) => { const e = $(id); if (e) e[evt] = fn; else console.warn(`app.js: missing #${id}`); };
const el = (tag, text, cls) => {
  const e = document.createElement(tag);
  if (text != null) e.textContent = text;   // textContent only: server data can never inject HTML
  if (cls) e.className = cls;
  return e;
};

let token = sessionStorage.getItem("token");
let me = null;
let sessionId = null;
let docsTimer = null;
let asking = false;

/* ---------- helpers ---------- */
function errMsg(j, r) {
  if (j && typeof j.message === "string") return j.message;
  if (j && Array.isArray(j.detail)) return j.detail.map((d) => d.msg).join("; ");   // FastAPI 422
  if (j && typeof j.detail === "string") return j.detail;
  return r.statusText || "Request failed";
}

async function api(path, opts = {}) {
  const { stream, ...init } = opts;
  init.headers = { ...(init.headers || {}), Authorization: `Bearer ${token}` };
  const r = await fetch(API + path, init);
  if (r.status === 401) { logout(); throw new Error("Session expired"); }
  if (!r.ok && !stream) {
    const j = await r.json().catch(() => ({}));
    throw new Error(errMsg(j, r));
  }
  return r;
}

const jsonPost = (body) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

/* ---------- auth ---------- */
async function login() {
  $("loginErr").textContent = "";
  const body = new URLSearchParams({ username: $("email").value.trim(), password: $("password").value });
  let r;
  try { r = await fetch(API + "/auth/login", { method: "POST", body }); }
  catch { $("loginErr").textContent = "Cannot reach the server"; return; }
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    $("loginErr").textContent = r.status === 429 ? errMsg(j, r) : "Invalid credentials";
    return;
  }
  const j = await r.json();
  token = j.access_token; me = j.user;
  sessionStorage.setItem("token", token);
  $("password").value = "";
  showApp();
}

function logout() {
  sessionStorage.clear();
  token = null; me = null; sessionId = null;
  location.reload();   // wipes chat, tables, dashboard and upload status so nothing leaks to the next user
}

async function showApp() {
  if (!me) { try { me = await (await api("/auth/me")).json(); } catch { return; } }
  $("login").hidden = true;
  $("app").hidden = false;
  $("who").textContent = `${me.email} · ${me.role} · L${me.clearance_level}`;
  if ($("dashTab")) $("dashTab").hidden = !me.is_admin;
  await loadSessions();
}

/* ---------- tabs ---------- */
function showTab(name) {
  document.querySelectorAll("nav button[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  ["chat", "upload", "docs", "dash"].forEach((t) => { const s = $("tab-" + t); if (s) s.hidden = t !== name; });
  clearTimeout(docsTimer);
  if (name === "docs") loadDocs();
  if (name === "dash") loadDash();
}

/* ---------- chat ---------- */
function newBox() {
  const box = el("div", null, "msg");
  const text = el("div"), src = el("div"), meta = el("div", null, "meta");
  box.append(text, src, meta);
  $("messages").append(box);
  box.scrollIntoView({ block: "end" });
  return { text, src, meta };
}

function renderSources(host, sources) {
  host.replaceChildren();
  if (!sources || !sources.length) return;
  const d = el("details", null, "sources");
  d.append(el("summary", `Sources (${sources.length} file${sources.length > 1 ? "s" : ""})`));
  sources.forEach((s) => {
    const item = el("div", null, "src");
    item.append(el("strong", s.title), el("span", ` · ${s.doc_type || "file"}`, "mut"));
    item.append(el("p", s.summary || "No summary available."));
    item.append(el("small", (s.passages || []).map((p) => `[${p.n}] ${p.location || "—"} (${p.score})`).join("  ·  "), "mut"));
    d.append(item);
  });
  host.append(d);
}

function addFeedback(meta, logId) {
  [["👍", 1], ["👎", -1]].forEach(([label, v]) => {
    const b = el("button", label);
    b.onclick = async () => {
      try { await api("/rag/feedback", jsonPost({ log_id: logId, rating: v })); b.disabled = true; }
      catch (e) { meta.append(" ", el("span", e.message, "err")); }
    };
    meta.append(" ", b);
  });
}

async function ask() {
  const q = $("question").value.trim();
  if (!q || asking) return;
  asking = true; $("ask").disabled = true; $("question").value = "";
  $("messages").append(el("div", q, "msg q"));
  const { text, src, meta } = newBox();
  try {
    const r = await api("/rag/query", { ...jsonPost({ question: q, session_id: sessionId }), stream: true });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      text.textContent = errMsg(j, r);   // includes the 429 "Try again in Ns" message
      return;
    }
    const reader = r.body.getReader(), dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const raw = buf.slice(0, i); buf = buf.slice(i + 2);
        const ev = /event: (.*)/.exec(raw)?.[1];
        let data = null;
        try { data = JSON.parse(/data: (.*)/s.exec(raw)?.[1] ?? "null"); } catch { continue; }
        if (ev === "session") sessionId = data;
        else if (ev === "token") text.textContent += data;
        else if (ev === "sources") renderSources(src, data);
        else if (ev === "done") {
          meta.textContent = `confidence ${data.confidence} · ${data.latency_ms} ms${data.rewritten_query ? " · query rewritten" : ""}`;
          addFeedback(meta, data.log_id);
          loadSessions();
        } else if (ev === "error") text.textContent = data;
      }
    }
  } catch (e) {
    text.textContent = "Error: " + e.message;
  } finally {
    asking = false; $("ask").disabled = false; $("question").focus();
  }
}

/* ---------- sessions ---------- */
async function loadSessions() {
  const sel = $("sessions");
  if (!sel) return;
  try {
    const list = await (await api("/rag/sessions")).json();
    sel.replaceChildren(new Option("— new chat —", ""));
    list.forEach((s) => sel.append(new Option(s.title, s.id)));
    sel.value = sessionId || "";
  } catch (e) { console.warn("sessions:", e.message); }
}

async function openSession(id) {
  $("messages").replaceChildren();
  sessionId = id || null;
  if (!id) return;
  try {
    const msgs = await (await api(`/rag/sessions/${id}/messages`)).json();
    msgs.forEach((m) => {
      if (m.role === "user") $("messages").append(el("div", m.content, "msg q"));
      else { const b = newBox(); b.text.textContent = m.content; renderSources(b.src, m.sources); }
    });
  } catch (e) { $("messages").append(el("div", e.message, "msg")); }
}

async function deleteSession() {
  if (!sessionId) return;
  try { await api(`/rag/sessions/${sessionId}`, { method: "DELETE" }); }
  catch (e) { alert(e.message); return; }
  await openSession(null);
  loadSessions();
}

/* ---------- upload ---------- */
async function upload() {
  const f = $("file").files[0];
  const msg = $("uploadMsg");
  if (!f || !$("title").value.trim() || !$("department").value.trim()) {
    msg.textContent = "File, title and department are required"; return;
  }
  const fd = new FormData();
  fd.append("file", f);
  fd.append("title", $("title").value.trim());
  fd.append("department", $("department").value.trim());
  fd.append("doc_type", $("doc_type").value);
  fd.append("classification_level", $("level").value);
  fd.append("allowed_groups", $("groups").value.trim() || "all");
  msg.textContent = "Uploading…";
  try {
    const j = await (await api("/etl/upload", { method: "POST", body: fd })).json();
    msg.textContent = `Queued (${j.doc_id}). Status: ${j.status}. Check the Documents tab.`;
  } catch (e) { msg.textContent = "Error: " + e.message; }
}

/* ---------- documents ---------- */
async function loadDocs() {
  clearTimeout(docsTimer);
  const t = $("docsTable");
  let docs;
  try { docs = await (await api("/etl/documents")).json(); }
  catch (e) { t.replaceChildren(el("caption", "Error: " + e.message)); return; }
  t.replaceChildren();
  const h = el("tr");
  ["Title", "Type", "Level", "Groups", "Status", "Chunks"].forEach((x) => h.append(el("th", x)));
  t.append(h);
  docs.forEach((d) => {
    const r = el("tr");
    [d.title, d.doc_type, d.classification_level, (d.allowed_groups || []).join(","),
     d.status + (d.error ? ` (${d.error})` : ""), d.chunk_count ?? ""]
      .forEach((x) => r.append(el("td", String(x))));
    t.append(r);
  });
  // auto-refresh while something is still being indexed
  if (!$("tab-docs").hidden && docs.some((d) => d.status === "pending" || d.status === "processing")) {
    docsTimer = setTimeout(loadDocs, 5000);
  }
}

/* ---------- dashboard ---------- */
async function loadDash() {
  try { $("dash").textContent = JSON.stringify(await (await api("/observability/summary")).json(), null, 2); }
  catch (e) { $("dash").textContent = "Error: " + e.message; }
}

/* ---------- wiring ---------- */
document.querySelectorAll("nav button[data-tab]").forEach((b) => { b.onclick = () => showTab(b.dataset.tab); });
on("loginBtn", "onclick", login);
on("password", "onkeydown", (e) => { if (e.key === "Enter") login(); });
on("logout", "onclick", logout);
on("ask", "onclick", ask);
on("question", "onkeydown", (e) => { if (e.key === "Enter") ask(); });
on("uploadBtn", "onclick", upload);
on("refreshDocs", "onclick", loadDocs);
on("sessions", "onchange", () => openSession($("sessions").value));
on("newChat", "onclick", () => { openSession(null); const s = $("sessions"); if (s) s.value = ""; });
on("delChat", "onclick", deleteSession);

if (token) showApp();