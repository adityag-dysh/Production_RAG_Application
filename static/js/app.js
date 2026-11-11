"use strict";
const API = "/api/v1";
const $ = (id) => document.getElementById(id);
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

const jsonBody = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

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

function renderWho() { $("who").textContent = `${me.name || me.email} · ${me.role} · L${me.clearance_level}`; }

async function showApp() {
  if (!me) { try { me = await (await api("/auth/me")).json(); } catch { return; } }
  $("login").hidden = true;
  $("app").hidden = false;
  renderWho();
  if ($("dashTab")) $("dashTab").hidden = !me.is_admin;
  const canManageKeys = me.role === "ceo" || me.role === "hr" || me.is_admin;
  if ($("usersTab")) $("usersTab").hidden = !canManageKeys;
  if ($("keysTab")) $("keysTab").hidden = !canManageKeys;
  await loadSessions();
}

/* ---------- tabs ---------- */
function showTab(name) {
  document.querySelectorAll("nav button[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  ["chat", "upload", "docs", "users", "keys", "profile", "dash"].forEach((t) => { const s = $("tab-" + t); if (s) s.hidden = t !== name; });
  clearTimeout(docsTimer);
  if (name === "docs") loadDocs();
  if (name === "keys") fillKeysTab();
  if (name === "dash") loadDash();
  if (name === "profile") fillProfile();
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

/* ---------- source inspector drawer ---------- */
let currentSourcesMap = {};

let activeQueryKeywords = [];

function highlightKeywords(text, keywords) {
  if (!keywords || keywords.length === 0 || !text) return text;
  // Escape special regex chars and sort by length descending to match longest phrases first
  const sortedKw = [...new Set(keywords)].sort((a, b) => b.length - a.length);
  const regex = new RegExp(`(${sortedKw.map(k => k.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&')).join('|')})`, 'gi');
  return text.replace(regex, '<mark>$1</mark>');
}

function openSourceDrawer(sourceInfo) {
  const drawer = $("sourceDrawer");
  const content = $("drawerContent");
  if (!drawer || !content) return;

  content.replaceChildren();
  content.append(el("h4", sourceInfo.title));
  
  const metaBox = el("div", null, "meta");
  metaBox.style.marginBottom = "12px";
  metaBox.textContent = `Type: ${sourceInfo.doc_type || "file"} · Department: ${sourceInfo.department || "—"} · Classification L${sourceInfo.classification_level ?? 0}`;
  content.append(metaBox);

  if (sourceInfo.summary) {
    content.append(el("strong", "Summary:"));
    content.append(el("p", sourceInfo.summary));
  }

  content.append(el("strong", "Matching Passages (Full Text & Keyword Matches):"));
  if (sourceInfo.passages && sourceInfo.passages.length > 0) {
    sourceInfo.passages.forEach((p) => {
      const pBox = el("div", null, "card");
      pBox.style.margin = "8px 0"; pBox.style.padding = "10px"; pBox.style.background = "rgba(128,128,128,0.05)";
      pBox.append(el("small", `Passage [${p.n}] — Location: ${p.location || "—"}`, "mut"));
      
      const pText = el("p");
      pText.innerHTML = highlightKeywords(p.text || "", activeQueryKeywords);
      pBox.append(pText);
      content.append(pBox);
    });
  } else {
    content.append(el("p", "No detailed passages available.", "mut"));
  }

  drawer.style.right = "0";
}

function closeSourceDrawer() {
  const drawer = $("sourceDrawer");
  if (drawer) drawer.style.right = "-420px";
}

function renderSources(host, sources) {
  host.replaceChildren();
  if (!sources || !sources.length) return;
  currentSourcesMap = {};
  sources.forEach((s) => {
    (s.passages || []).forEach((p) => {
      currentSourcesMap[p.n] = s;
    });
  });

  const d = el("details", null, "sources");
  d.append(el("summary", `Sources (${sources.length} file${sources.length > 1 ? "s" : ""})`));
  sources.forEach((s) => {
    const item = el("div", null, "src");
    item.style.cursor = "pointer";
    item.onclick = () => openSourceDrawer(s);
    item.append(el("strong", s.title), el("span", ` · ${s.doc_type || "file"}`, "mut"));
    item.append(el("p", s.summary || "No summary available."));
    item.append(el("small", (s.passages || []).map((p) => `[${p.n}] ${p.location || "—"}`).join("  ·  "), "mut"));
    d.append(item);
  });
  host.append(d);
}

function attachCitationClicks(container) {
  container.querySelectorAll(".cite-badge").forEach((badge) => {
    badge.onclick = (e) => {
      e.preventDefault();
      const n = badge.dataset.n;
      const src = currentSourcesMap[n];
      if (src) {
        openSourceDrawer(src);
      }
    };
  });
}

function postProcessMarkdown(textElem, rawText) {
  let html = marked.parse(rawText);
  // Transform citation brackets like [1], [2] into clickable interactive badges
  html = html.replace(/\[(\d+)\]/g, '<span class="cite-badge" data-n="$1">[$1]</span>');
  textElem.innerHTML = html;
  attachCitationClicks(textElem);
}

function addFeedback(meta, logId) {
  [["👍", 1], ["👎", -1]].forEach(([label, v]) => {
    const b = el("button", label);
    b.onclick = async () => {
      try { await api("/rag/feedback", jsonBody("POST", { log_id: logId, rating: v })); b.disabled = true; }
      catch (e) { meta.append(" ", el("span", e.message, "err")); }
    };
    meta.append(" ", b);
  });
}

async function ask() {
  const q = $("question").value.trim();
  if (!q || asking) return;
  
  // Extract keywords from user query for highlighting in source inspector
  activeQueryKeywords = q.split(/\s+/).filter(w => w.length > 2);

  asking = true; $("ask").disabled = true; $("question").value = "";
  $("messages").append(el("div", q, "msg q"));
  const { text, src, meta } = newBox();
  try {
    const r = await api("/rag/query", { ...jsonBody("POST", { question: q, session_id: sessionId }), stream: true });
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
        else if (ev === "token") {
          text._raw = (text._raw || "") + data;
          postProcessMarkdown(text, text._raw);
        }
        else if (ev === "sources") renderSources(src, data);
        else if (ev === "done") {
          const label = data.mode === "document_focus" ? "from the previous document" : `confidence ${data.confidence}`;
          meta.textContent = `${label} · ${data.latency_ms} ms${data.rewritten_query ? " · query rewritten" : ""}`;
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
      else { const b = newBox(); postProcessMarkdown(b.text, m.content); renderSources(b.src, m.sources); }
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

/* ---------- profile ---------- */
function setMsg(id, text, ok) { const m = $(id); m.textContent = text; m.className = ok ? "ok" : "err"; }

function fillProfile() {
  $("profInfo").textContent = `${me.email} · ${me.role} · clearance L${me.clearance_level} · groups: ${(me.groups || []).join(", ") || "—"}`;
  $("profName").value = me.name || "";
  ["nameMsg", "pwMsg"].forEach((id) => setMsg(id, "", true));
}

async function saveName() {
  const name = $("profName").value.trim();
  if (!name) { setMsg("nameMsg", "Name cannot be empty", false); return; }
  try {
    me = await (await api("/auth/me", jsonBody("PATCH", { name }))).json();
    renderWho(); setMsg("nameMsg", "Name updated", true);
  } catch (e) { setMsg("nameMsg", e.message, false); }
}

async function changePassword() {
  const cur = $("curPw").value, nw = $("newPw").value, nw2 = $("newPw2").value;
  if (nw !== nw2) { setMsg("pwMsg", "New passwords do not match", false); return; }
  if (nw.length < 8) { setMsg("pwMsg", "New password must be at least 8 characters", false); return; }
  try {
    const j = await (await api("/auth/change-password", jsonBody("POST", { current_password: cur, new_password: nw }))).json();
    token = j.access_token; sessionStorage.setItem("token", token);   // old token is now invalid
    ["curPw", "newPw", "newPw2"].forEach((id) => { $(id).value = ""; });
    setMsg("pwMsg", "Password changed. Other devices have been signed out.", true);
  } catch (e) { setMsg("pwMsg", e.message, false); }
}

/* ---------- upload ---------- */
let uploadQueueFiles = [];

function setupDropZone() {
  const dropZone = $("dropZone");
  const fileInput = $("file");
  const browseBtn = $("browseBtn");
  if (!dropZone || !fileInput || !browseBtn) return;

  browseBtn.onclick = (e) => { e.preventDefault(); fileInput.click(); };
  dropZone.onclick = () => fileInput.click();

  fileInput.onchange = (e) => {
    addFilesToQueue(e.target.files);
    fileInput.value = "";
  };

  ["dragenter", "dragover"].forEach((eventName) => {
    dropZone.addEventListener(eventName, (e) => {
      e.preventDefault(); e.stopPropagation();
      dropZone.style.borderColor = "var(--acc)";
      dropZone.style.background = "rgba(47, 95, 234, 0.05)";
    }, false);
  });

  ["dragleave", "drop"].forEach((eventName) => {
    dropZone.addEventListener(eventName, (e) => {
      e.preventDefault(); e.stopPropagation();
      dropZone.style.borderColor = "#8886";
      dropZone.style.background = "transparent";
      if (eventName === "drop" && e.dataTransfer.files) {
        addFilesToQueue(e.dataTransfer.files);
      }
    }, false);
  });
}

function addFilesToQueue(files) {
  for (const f of files) {
    uploadQueueFiles.push({ file: f, status: "pending", error: null });
  }
  renderUploadQueue();
}

function renderUploadQueue() {
  const qContainer = $("uploadQueue");
  if (!qContainer) return;
  qContainer.replaceChildren();
  if (uploadQueueFiles.length === 0) return;

  uploadQueueFiles.forEach((item, idx) => {
    const row = el("div", null, "src");
    row.style.display = "flex"; row.style.justifyContent = "space-between"; row.style.alignItems = "center";
    const info = el("div");
    info.append(el("strong", item.file.name), el("span", ` (${Math.round(item.file.size / 1024)} KB) · `, "mut"), el("small", item.status, item.status === "error" ? "err" : "mut"));
    if (item.error) {
      info.append(el("br"), el("small", item.error, "err"));
    }
    if (item.note) {
      info.append(el("br"), el("small", item.note, "mut"));
    }
    row.append(info);
    if (item.status === "pending") {
      const rm = el("button", "Remove");
      rm.style.padding = "2px 6px"; rm.style.fontSize = "12px";
      rm.onclick = () => { uploadQueueFiles.splice(idx, 1); renderUploadQueue(); };
      row.append(rm);
    }
    qContainer.append(row);
  });
}

async function upload() {
  const msg = $("uploadMsg");
  const dept = $("department").value.trim();
  if (uploadQueueFiles.length === 0 || !dept) {
    msg.textContent = "Please select at least one file and provide a department"; return;
  }

  msg.textContent = `Uploading queue (0 / ${uploadQueueFiles.length})…`;
  let successCount = 0;

  for (let i = 0; i < uploadQueueFiles.length; i++) {
    const item = uploadQueueFiles[i];
    if (item.status === "success") { successCount++; continue; }
    item.status = "uploading";
    renderUploadQueue();

    const fd = new FormData();
    fd.append("file", item.file);
    fd.append("title", item.file.name);
    fd.append("department", dept);
    fd.append("doc_type", $("doc_type").value);
    fd.append("classification_level", $("level").value);
    fd.append("allowed_groups", $("groups").value.trim() || "all");

    try {
      const r = await api("/etl/upload", { method: "POST", body: fd });
      const j = await r.json();
      item.status = "success";
      item.note = j.deduplicated ? "duplicate skipped" : (j.parts && j.parts > 1 ? `split into ${j.parts} parts` : null);
      successCount++;
    } catch (e) {
      item.status = "error";
      item.error = e.message;
    }
    renderUploadQueue();
    msg.textContent = `Processing queue (${successCount} / ${uploadQueueFiles.length})…`;
  }

  msg.textContent = `Queue complete! Successfully uploaded ${successCount} file(s). Check the Documents tab.`;
  uploadQueueFiles = uploadQueueFiles.filter(i => i.status !== "success");
  renderUploadQueue();
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
  if (!$("tab-docs").hidden && docs.some((d) => d.status === "pending" || d.status === "processing")) {
    docsTimer = setTimeout(loadDocs, 5000);   // auto-refresh while something is still indexing
  }
}

async function createUser() {
  const email = $("newUserEmail").value.trim();
  const name = $("newUserName").value.trim();
  const role = $("newUserRole").value.trim();
  const password = $("newUserPw").value;
  const clearance_level = parseInt($("newUserLevel").value, 10);
  const groups = $("newUserGroups").value.split(",").map(s => s.trim()).filter(Boolean);
  const can_upload = $("newUserUpload").checked;
  const is_admin = $("newUserAdmin").checked;

  if (!email || !name || !role || !password) {
    setMsg("userMsg", "Email, Name, Role and Password are required", false);
    return;
  }

  try {
    await api("/auth/users", jsonBody("POST", {
      email, name, role, password, clearance_level, groups, can_upload, is_admin
    }));
    setMsg("userMsg", `User ${email} created successfully!`, true);
    $("newUserEmail").value = "";
    $("newUserName").value = "";
    $("newUserRole").value = "";
    $("newUserPw").value = "";
    $("newUserGroups").value = "";
    $("newUserUpload").checked = false;
    $("newUserAdmin").checked = false;
  } catch (e) {
    setMsg("userMsg", e.message, false);
  }
}

/* ---------- MCP API keys ---------- */
let lastGeneratedApiKey = null;

function fmtDate(value) {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString();
}

function mcpUrl() {
  return `${window.location.origin}/mcp`;
}

function mcpConfigSnippet(secret) {
  return JSON.stringify({
    mcpServers: {
      "rag-app": {
        type: "streamable_http",
        url: mcpUrl(),
        headers: { Authorization: `Bearer ${secret}` }
      }
    }
  }, null, 2);
}

function showApiKeyResult(j) {
  lastGeneratedApiKey = j.api_key || "";
  $("apiKeySecret").value = lastGeneratedApiKey;
  $("mcpConfigSnippet").textContent = mcpConfigSnippet(lastGeneratedApiKey);
  $("apiKeyResult").hidden = false;
}

function hideApiKeyResult() {
  lastGeneratedApiKey = null;
  $("apiKeySecret").value = "";
  $("mcpConfigSnippet").textContent = "";
  $("apiKeyResult").hidden = true;
}

async function copyText(text, msgId = "apiKeyMsg") {
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    setMsg(msgId, "Copied to clipboard.", true);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    document.body.append(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
    setMsg(msgId, "Copied to clipboard.", true);
  }
}

function fillKeysTab() {
  const max = Math.max(0, Math.min(4, Number(me?.clearance_level ?? 4)));
  const sel = $("apiKeyClearance");
  sel.replaceChildren(...Array.from({ length: max + 1 }, (_, i) => new Option(`L${i}`, String(i))));
  sel.value = String(Math.min(2, max));
  $("apiKeyExpires").value = "90";
  document.querySelectorAll(".apiKeyScope").forEach((c) => { c.checked = ["read", "generate"].includes(c.value); });
  setMsg("apiKeyMsg", "", true);
  hideApiKeyResult();
  loadKeys();
}

async function loadKeys() {
  const t = $("keysTable");
  let keys;
  try { keys = await (await api("/auth/api-keys")).json(); }
  catch (e) { t.replaceChildren(el("caption", "Error: " + e.message)); return; }

  t.replaceChildren();
  const h = el("tr");
  ["Label", "Prefix", "Max clearance", "Scopes", "Created", "Expires", "Action"].forEach((x) => h.append(el("th", x)));
  t.append(h);

  if (!keys.length) {
    const empty = el("tr");
    const td = el("td", "No MCP API keys yet.");
    td.colSpan = 7;
    empty.append(td);
    t.append(empty);
    return;
  }

  keys.forEach((k) => {
    const r = el("tr");
    [k.label || "—", k.prefix || "—", `L${k.max_clearance}`, (k.scopes || []).join(", ") || "—",
     fmtDate(k.created_at), fmtDate(k.expires_at)].forEach((x) => r.append(el("td", String(x))));
    const td = el("td");
    const btn = el("button", "Revoke");
    btn.onclick = () => revokeApiKey(k.key_id, k.label || "this key");
    td.append(btn);
    r.append(td);
    t.append(r);
  });
}

async function generateApiKey() {
  const label = $("apiKeyLabel").value.trim();
  const scopes = [...document.querySelectorAll(".apiKeyScope:checked")].map((c) => c.value);
  const max_clearance = parseInt($("apiKeyClearance").value, 10);
  const expires_in_days = parseInt($("apiKeyExpires").value, 10);

  if (!label) { setMsg("apiKeyMsg", "Label is required.", false); return; }
  if (!scopes.length) { setMsg("apiKeyMsg", "Select at least one scope.", false); return; }
  if (!Number.isInteger(max_clearance) || max_clearance < 0 || max_clearance > 4) { setMsg("apiKeyMsg", "Invalid clearance level.", false); return; }
  if (!Number.isInteger(expires_in_days) || expires_in_days < 1 || expires_in_days > 365) { setMsg("apiKeyMsg", "Expiration must be between 1 and 365 days.", false); return; }

  try {
    const j = await (await api("/auth/api-keys", jsonBody("POST", { label, max_clearance, scopes, expires_in_days }))).json();
    setMsg("apiKeyMsg", `Key created successfully. Prefix: ${j.prefix}`, true);
    $("apiKeyLabel").value = "";
    showApiKeyResult(j);
    await loadKeys();
  } catch (e) {
    setMsg("apiKeyMsg", e.message, false);
  }
}

async function revokeApiKey(keyId, label) {
  if (!confirm(`Revoke MCP API key "${label}"? Agents using it will lose access immediately.`)) return;
  try {
    await api(`/auth/api-keys/${encodeURIComponent(keyId)}`, { method: "DELETE" });
    setMsg("apiKeyMsg", "Key revoked.", true);
    await loadKeys();
  } catch (e) {
    setMsg("apiKeyMsg", e.message, false);
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
on("saveName", "onclick", saveName);
on("changePw", "onclick", changePassword);
on("createUserBtn", "onclick", createUser);
on("generateApiKeyBtn", "onclick", generateApiKey);
on("refreshKeys", "onclick", loadKeys);
on("copyApiKey", "onclick", () => copyText(lastGeneratedApiKey || $("apiKeySecret").value));
on("copyMcpConfig", "onclick", () => copyText($("mcpConfigSnippet").textContent));
on("dismissApiKey", "onclick", hideApiKeyResult);
on("closeDrawer", "onclick", closeSourceDrawer);
setupDropZone();

if (token) showApp();