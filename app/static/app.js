const S = {
  authed: false,
  screen: "dashboard",
  error: "",
  notice: "",
  dashboard: null,
  assets: [],
  total: 0,
  query: "",
  category: "",
  asset: null,
  tasks: [],
  uploads: [],
  searchHits: [],
};

const $ = (html) => {
  document.getElementById("app").innerHTML = html;
};
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
const tag = (value) => `<bdi class="tag">${esc(value)}</bdi>`;

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "include", ...options });
  const body = await response.json().catch(() => ({}));
  if (response.status === 401) {
    S.authed = false;
    render();
    throw new Error("נדרשת כניסה.");
  }
  if (!response.ok) throw new Error(body.detail || "הפעולה לא נשמרה.");
  return body;
}

function shell(body) {
  if (!S.authed) return body;
  const nav = [
    ["dashboard", "לוח"],
    ["inventory", "מלאי"],
    ["tasks", "שאלות"],
    ["upload", "העלאה"],
  ].map(([id, label]) => `<button type="button" data-go="${id}" class="${S.screen === id ? "on" : ""}">${label}</button>`).join("");
  return `<div class="wrap">${S.notice ? `<p class="muted">${esc(S.notice)}</p>` : ""}${body}</div><nav class="nav">${nav}</nav>`;
}

function render() {
  if (!S.authed) {
    $(`<form class="card login" data-action="login"><h1>ציוד למכירה</h1><p class="muted">ארבעה דברים: מה יש למכירה, מה שייך לאותה מכונה, מה חסר, ואיזו ראיית שווי כבר יש.</p>${S.error ? `<div class="error">${esc(S.error)}</div>` : ""}<label>סיסמה<input type="password" name="password" autocomplete="current-password"></label><button class="btn-primary" type="submit">כניסה</button></form>`);
    return;
  }
  let body = "";
  if (S.screen === "inventory") body = renderInventory();
  else if (S.screen === "asset") body = renderAsset();
  else if (S.screen === "tasks") body = renderTasks();
  else if (S.screen === "upload") body = renderUpload();
  else body = renderDashboard();
  $(shell(body));
}

function renderDashboard() {
  const data = S.dashboard;
  if (!data) return `<p>טוען את הלוח…</p>`;
  const cats = (data.by_category || []).map((item) => `<button class="stat" type="button" data-go="inventory" data-category="${esc(item.id)}"><b>${esc(item.count)}</b>${esc(item.label)}</button>`).join("");
  return `
    <div class="top"><h1>לוח הבקרה</h1></div>
    <p>${esc(data.note || "")}</p>
    <div class="grid">
      <button class="stat" type="button" data-go="inventory"><b>${esc(data.assets)}</b>נכסים למכירה</button>
      <button class="stat" type="button" data-go="inventory" data-attention="uncertain"><b>${esc(data.uncertain_relationships)}</b>קשרים לא ודאיים</button>
      <button class="stat" type="button" data-go="tasks"><b>${esc(data.open_tasks)}</b>שאלות פתוחות</button>
      <button class="stat" type="button" data-go="upload"><b>${esc(data.pending_files)}</b>קבצים ממתינים לשיוך</button>
    </div>
    <h2 style="margin-top:18px">לפי סוג</h2>
    <div class="grid">${cats}</div>
    <p class="muted" style="margin-top:12px">${esc(data.missing_info)} נכסים נושאים תווית כמו FUTURE או HOLD. הם נשארים במלאי.</p>`;
}

function renderInventory() {
  const filters = [
    ["", "הכל"],
    ["lab", "מעבדה"],
    ["process", "תהליך / ייצור"],
    ["api", "API"],
  ].map(([id, label]) => `<button type="button" data-category="${id}" class="${S.category === id ? "on" : ""}">${label}</button>`).join("");
  const rows = S.assets.map((asset) => `
    <button class="card row" type="button" data-asset="${esc(asset.id)}">
      <b>${esc(asset.name)}</b>
      <div>${asset.tag ? tag(asset.tag) : ""} ${esc(asset.type_label || "")}</div>
      <div class="muted">${esc(asset.category_label)}${asset.area ? " · " + tag(asset.area) : ""}${asset.component_count ? " · " + esc(asset.component_count) + " רכיבים" : ""}</div>
      <div class="chips">
        ${asset.confidence_label ? `<span class="chip">${esc(asset.confidence_label)}</span>` : ""}
        ${(asset.labels || []).map((label) => `<span class="chip">${tag(label)}</span>`).join("")}
        ${asset.sold_together ? `<span class="chip">נמכר יחד</span>` : ""}
      </div>
    </button>`).join("");
  return `
    <div class="top"><h1>מלאי</h1><span class="muted">${esc(S.total)} נכסים</span></div>
    <form data-action="search"><input name="q" value="${esc(S.query)}" placeholder="חיפוש לפי תג, שם או אזור"></form>
    <div class="filters">${filters}</div>
    ${rows || `<p class="muted">אין נכסים בסינון הזה.</p>`}`;
}

function renderAsset() {
  const asset = S.asset;
  if (!asset) return `<p>טוען את הכרטיס…</p>`;
  const tech = (asset.technical || []).map((item) => `<p><b>${esc(item.label)}:</b> ${item.label === "תיאור" ? esc(item.value) : tag(item.value)}<br><span class="muted">${esc(item.source)}</span></p>`).join("");
  const components = (asset.components || []).map((item) => `
    <div class="card" style="margin-bottom:8px">
      <b>${tag(item.tag)}</b> · ${esc(item.role)} · ${esc(item.type_label || "")}
      <p>${esc(item.description || "")}</p>
      <p>${esc(item.confidence_label)}. ${esc(item.explanation || "")}</p>
      <p class="muted">${esc(item.source)}</p>
      ${item.tag_norm ? `<button class="btn" type="button" data-action="reject" data-tag="${esc(item.tag_norm)}">הרכיב לא שייך</button>` : ""}
    </div>`).join("");
  const files = (asset.files || []).map((file) => `<p><b>${tag(file.name)}</b> · ${esc(file.doc_type)} · ${esc(file.confidence_label)}<br>${esc(file.summary || "")}</p>`).join("");
  const evidence = (asset.evidence || []).map((item) => `
    <p><b>${esc(item.value_label || "ראיה")}</b>${item.price_text ? " · " + tag(item.price_text + " " + (item.currency || "")) : ""}
    <br>${esc(item.description || item.body || "")}
    <br><span class="muted">${esc(item.source || "בלי מקור")} ${item.evidence_date ? "· " + tag(item.evidence_date) : ""}</span></p>`).join("");
  const history = (asset.history || []).map((item) => `<p class="muted">${tag((item.at || "").slice(0, 16))} · ${esc(item.actor)} · ${esc(item.action)}</p>`).join("");
  return `
    <button class="btn" type="button" data-go="inventory">חזרה למלאי</button>
    <div class="top"><h1>${esc(asset.name)}</h1></div>
    <div class="card">
      <p>${esc(asset.category_label)}${asset.type_label ? " · " + esc(asset.type_label) : ""}</p>
      <p>${asset.tag ? "תג ראשי " + tag(asset.tag) : "בלי תג ראשי"}</p>
      ${asset.area ? `<p>אזור במקור: ${tag(asset.area)}</p>` : ""}
      ${asset.confidence_label ? `<p>קשר: ${esc(asset.confidence_label)}</p>` : ""}
      ${(asset.labels || []).length ? `<p class="chips">${asset.labels.map((label) => `<span class="chip">${tag(label)}</span>`).join("")}</p>` : ""}
      ${asset.kind === "machine" && asset.confidence !== "confirmed" ? `<button class="btn-primary" type="button" data-action="confirm-machine">אשר שהרכיבים שייכים למכונה</button>` : ""}
    </div>
    <h2>מידע טכני</h2>
    <div class="card">${tech || `<p class="muted">אין במקור שדות טכניים לכרטיס הזה. לא מולאו ערכים.</p>`}</div>
    <h2>רכיבים</h2>
    ${components || `<p class="muted">זה נכס בודד. אין רכיבים מקושרים.</p>`}
    <h2>מה חסר או לא סגור</h2>
    <div class="card">${(asset.questions || []).map((item) => `<p>${esc(item)}</p>`).join("") || `<p class="muted">אין שאלה פתוחה על הכרטיס.</p>`}</div>
    <h2>קבצים</h2>
    <div class="card">${files || `<p class="muted">עדיין אין קובץ מאושר. מעלים במסך ההעלאה.</p>`}</div>
    <h2>ראיות שווי</h2>
    <div class="card">
      <p class="muted">נשמר רק מה שהוזן. אין בגרסה הזו טווח שווי.</p>
      ${evidence}
      <form data-action="evidence">
        <label>סוג הראיה<select name="value_kind">
          <option value="asking">מחיר מבוקש</option>
          <option value="comparable">השוואה לשוק</option>
          <option value="dealer">הצעת סוחר</option>
          <option value="offer">הצעת קונה</option>
          <option value="auction">תוצאת מכרז</option>
          <option value="purchase">מחיר רכישה</option>
          <option value="replacement">עלות תחליף</option>
          <option value="other">אחר</option>
        </select></label>
        <label>מקור<input name="source" placeholder="מי כתב, או שם הקובץ"></label>
        <label>תאריך<input name="evidence_date" placeholder="YYYY-MM-DD"></label>
        <label>סכום כפי שכתוב<input name="price_text"></label>
        <label>מטבע<input name="currency" placeholder="USD"></label>
        <label>תיאור<input name="description"></label>
        <button class="btn-primary" type="submit">שמור ראיה</button>
      </form>
    </div>
    <h2>היסטוריה</h2>
    <div class="card">${history || `<p class="muted">עדיין אין שינוי שנרשם כאן.</p>`}</div>`;
}

function renderTasks() {
  const items = S.tasks.map((task) => `
    <article class="card" style="margin-bottom:8px">
      <p><b>${esc(task.question)}</b></p>
      <p class="muted">${esc(task.status_label)} · ${esc(task.category)}${task.note ? " · " + esc(task.note) : ""}</p>
      <div class="actions">
        ${task.asset_id ? `<button class="btn" type="button" data-asset="${esc(task.asset_id)}">אל הנכס</button>` : ""}
        ${task.file_id ? `<button class="btn" type="button" data-go="upload">אל ההעלאה</button>` : ""}
        ${task.category !== "file" ? `<button class="btn" type="button" data-task="${esc(task.id)}" data-status="answered">סמן שנענה</button><button class="btn" type="button" data-task="${esc(task.id)}" data-status="resolved">סגור</button>` : ""}
      </div>
    </article>`).join("");
  return `<div class="top"><h1>שאלות</h1></div><p class="muted">רק מה שלא סגור: קשר לא ודאי, שם כללי, תווית סטטוס, או קובץ בלי שיוך.</p>${items || `<p>אין שאלות פתוחות.</p>`}`;
}

function renderUpload() {
  const files = (S.uploads || []).map((file) => `
    <article class="card" style="margin-bottom:8px" data-file="${esc(file.id)}">
      <b>${tag(file.name || "טקסט")}</b>
      <p>${esc(file.confidence_label)} · ${esc(file.doc_type || "")}</p>
      <p>${esc(file.summary || "")}</p>
      ${(file.links || []).map((link) => `<p class="muted">הצעה: ${tag(link.tag_norm || link.asset_id)} · ${esc(link.reason || "")}</p>`).join("")}
      ${file.user_locked ? `<p class="muted">השיוך אושר ולא יוחלף אוטומטית.</p>` : `
        <div class="actions">
          ${(file.links || []).length ? `<button class="btn-primary" type="button" data-confirm="${esc(file.id)}">אשר את השיוך</button>` : ""}
          <button class="btn" type="button" data-assign="${esc(file.id)}">שייך לנכס אחר</button>
        </div>
        <form data-action="create-asset" data-file="${esc(file.id)}">
          <label>נכס חדש, רק אם אין רשומה<input name="name" placeholder="שם שהקלדתם"></label>
          <button class="btn" type="submit">צור נכס ושייך</button>
        </form>`}
    </article>`).join("");
  const hits = (S.searchHits || []).map((asset) => `<button class="btn" type="button" data-pick="${esc(asset.id)}" data-file="${esc(S.pickFile || "")}">${esc(asset.name)} ${asset.tag ? tag(asset.tag) : ""}</button>`).join("");
  return `
    <div class="top"><h1>העלאה</h1></div>
    <p>אפשר להעלות קובץ, כמה קבצים, או להדביק טקסט. בלי לבחור קודם נכס. שיוך לא ודאי מחכה לאישור.</p>
    <form class="card" data-action="upload">
      <label class="btn file">בחירת קבצים<input type="file" name="files" multiple accept=".pdf,.doc,.docx,.xls,.xlsx,.csv,.txt,.rtf,.png,.jpg,.jpeg,.webp,image/*"></label>
      <label>או טקסט<textarea name="note" placeholder="הדביקו כאן מידע, מכתב, או הצעה"></textarea></label>
      <button class="btn-primary" type="submit">קלוט</button>
    </form>
    ${S.pickFile ? `<form class="card" data-action="find-asset"><label>חיפוש נכס לשיוך<input name="q" placeholder="תג או שם"></label><div class="actions">${hits}</div></form>` : ""}
    ${files || `<p class="muted">עדיין לא הועלה כאן קובץ.</p>`}`;
}

async function loadDashboard() {
  S.dashboard = await api("/api/dashboard");
  render();
}
async function loadInventory() {
  const params = new URLSearchParams();
  if (S.query) params.set("q", S.query);
  if (S.category) params.set("category", S.category);
  if (S.attention) params.set("attention", S.attention);
  const body = await api("/api/assets?" + params.toString());
  S.assets = body.assets || [];
  S.total = body.total || 0;
  render();
}
async function loadAsset(id) {
  S.asset = await api("/api/assets/" + encodeURIComponent(id));
  S.screen = "asset";
  location.hash = "#/asset/" + encodeURIComponent(id);
  render();
}
async function loadTasks() {
  S.tasks = (await api("/api/tasks")).tasks || [];
  render();
}
async function loadUploads() {
  S.uploads = (await api("/api/uploads")).files || [];
  render();
}

async function go(screen) {
  S.screen = screen;
  S.error = "";
  S.notice = "";
  if (screen !== "asset") location.hash = "#/" + screen;
  if (screen === "dashboard") return loadDashboard();
  if (screen === "inventory") return loadInventory();
  if (screen === "tasks") return loadTasks();
  if (screen === "upload") return loadUploads();
  render();
}

document.getElementById("app").addEventListener("click", async (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  if (button.dataset.go) {
    S.category = button.dataset.category || "";
    S.attention = button.dataset.attention || "";
    if (button.dataset.go === "inventory" && button.dataset.category !== undefined && button.closest(".grid")) {
      S.category = button.dataset.category || "";
    }
    await go(button.dataset.go);
    return;
  }
  if (button.dataset.category !== undefined && S.screen === "inventory") {
    S.category = button.dataset.category;
    S.attention = "";
    await loadInventory();
    return;
  }
  if (button.dataset.asset) {
    await loadAsset(button.dataset.asset);
    return;
  }
  if (button.dataset.action === "confirm-machine" && S.asset) {
    S.asset = await api(`/api/assets/${encodeURIComponent(S.asset.id)}/relationship`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "confirm" }) });
    S.notice = "הקשר אושר. שורות המקור נשארו.";
    render();
    return;
  }
  if (button.dataset.action === "reject" && S.asset) {
    S.asset = await api(`/api/assets/${encodeURIComponent(S.asset.id)}/relationship`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "reject", tag_norms: [button.dataset.tag] }) });
    S.notice = "הרכיב הוצא מהמכונה. שורת המקור נשארה.";
    render();
    return;
  }
  if (button.dataset.task) {
    const note = window.prompt("הערה, אם יש") || "";
    await api("/api/tasks/" + encodeURIComponent(button.dataset.task), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: button.dataset.status, note }) });
    await loadTasks();
    return;
  }
  if (button.dataset.confirm) {
    await api("/api/uploads/" + encodeURIComponent(button.dataset.confirm), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "confirm" }) });
    S.notice = "השיוך אושר.";
    await loadUploads();
    return;
  }
  if (button.dataset.assign) {
    S.pickFile = button.dataset.assign;
    S.searchHits = [];
    render();
    return;
  }
  if (button.dataset.pick && S.pickFile) {
    await api("/api/uploads/" + encodeURIComponent(S.pickFile), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "assign", asset_ids: [button.dataset.pick] }) });
    S.pickFile = "";
    S.notice = "הקובץ שויך לנכס שבחרתם.";
    await loadUploads();
  }
});

document.getElementById("app").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const data = new FormData(form);
  try {
    if (form.dataset.action === "login") {
      await api("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: data.get("password") }) });
      S.authed = true;
      S.error = "";
      await go("dashboard");
      return;
    }
    if (form.dataset.action === "search") {
      S.query = String(data.get("q") || "");
      await loadInventory();
      return;
    }
    if (form.dataset.action === "upload") {
      const response = await api("/api/uploads", { method: "POST", body: data });
      S.notice = (response.results || []).map((item) => item.summary).filter(Boolean).join(" ");
      await loadUploads();
      return;
    }
    if (form.dataset.action === "create-asset") {
      await api("/api/uploads/" + encodeURIComponent(form.dataset.file), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "create", name: data.get("name"), category: "process" }) });
      S.notice = "נוצר נכס לפי השם שכתבתם, והקובץ שויך אליו.";
      await loadUploads();
      return;
    }
    if (form.dataset.action === "find-asset") {
      const body = await api("/api/assets?q=" + encodeURIComponent(data.get("q") || ""));
      S.searchHits = body.assets || [];
      render();
      return;
    }
    if (form.dataset.action === "evidence" && S.asset) {
      const payload = Object.fromEntries(data.entries());
      await api(`/api/assets/${encodeURIComponent(S.asset.id)}/evidence`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      S.notice = "הראיה נשמרה. לא חושב טווח.";
      await loadAsset(S.asset.id);
    }
  } catch (error) {
    S.error = error.message;
    S.notice = error.message;
    render();
  }
});

window.addEventListener("hashchange", () => {
  const hash = location.hash;
  if (!S.authed) return;
  if (hash.startsWith("#/asset/")) loadAsset(decodeURIComponent(hash.split("/").slice(2).join("/")));
  else if (hash.startsWith("#/inventory")) go("inventory");
  else if (hash.startsWith("#/tasks")) go("tasks");
  else if (hash.startsWith("#/upload")) go("upload");
  else go("dashboard");
});

async function boot() {
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/static/sw.js").catch(() => {});
  const health = await fetch("/api/me", { credentials: "include" }).catch(() => null);
  if (health && health.ok) {
    const body = await health.json();
    S.authed = !!body.ok;
  }
  if (S.authed) {
    const hash = location.hash;
    if (hash.startsWith("#/asset/")) await loadAsset(decodeURIComponent(hash.split("/").slice(2).join("/")));
    else if (hash.startsWith("#/inventory")) await go("inventory");
    else if (hash.startsWith("#/tasks")) await go("tasks");
    else if (hash.startsWith("#/upload")) await go("upload");
    else await go("dashboard");
    return;
  }
  render();
}

boot();
