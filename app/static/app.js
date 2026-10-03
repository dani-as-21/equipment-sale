(function () {
  const Store = window.CaptureStore;
  const Match = window.CaptureMatch;
  const S = {
    screen: "capture",
    authed: false,
    online: navigator.onLine,
    areas: [],
    currentAreaId: null,
    rows: [],
    rowById: {},
    captures: {},
    photos: {},
    links: [],
    reviews: [],
    relationships: [],
    notes: [],
    summary: null,
    files: [],
    openCaptureId: null,
    error: "",
    toast: "",
    filter: "all",
    query: "",
    areaFilter: "",
    reviewFilter: "visit",
    activeReview: null,
    activeRow: null,
    importPreview: null,
    readiness: {},
    listLimit: 40,
    syncing: false,
    offlineTest: null,
  };

  const STATUS_HE = {
    saving_locally: "שומר במכשיר",
    saved_on_device: "נשמר במכשיר",
    pending_sync: "ממתין לסנכרון",
    syncing: "מסנכרן",
    synced: "סונכרן לשרת",
    failed: "השמירה נכשלה",
    stored: "ממתין לסנכרון",
  };
  const BACKUP_WARNING = "נשמר במכשיר אינו גיבוי. מחיקת נתוני האתר או אובדן המכשיר מוחקים צילומים שטרם סונכרנו.";

  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  }
  function ltr(value) {
    return `<bdi class="ltr">${esc(value)}</bdi>`;
  }
  function nowIso() {
    return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
  }
  function $(html) {
    document.getElementById("app").innerHTML = html;
  }

  function areaById(id) {
    return S.areas.find((area) => area.id === id) || null;
  }
  function photosOf(captureId) {
    return Object.values(S.photos).filter((photo) => photo.captureId === captureId || photo.capture_id === captureId);
  }
  function normLink(link) {
    const rowId = link.row_id || link.inventory_row_id;
    return { ...link, row_id: rowId, inventory_row_id: rowId };
  }
  function linksOf(captureId) {
    const local = S.captures[captureId];
    if (local && local.dirty && Array.isArray(local.links)) return local.links.map(normLink);
    return S.links.filter((link) => link.capture_id === captureId).map(normLink);
  }
  function captureStarted(cap) {
    return photosOf(cap.id).length > 0 || String(cap.note || "").trim() || String(cap.tag_text || "").trim() || (cap.links || []).length > 0;
  }
  function deriveStatus(cap) {
    const photos = photosOf(cap.id);
    if (photos.some((photo) => photo.state === "failed") || cap.sync_status === "failed") return "failed";
    if (photos.some((photo) => photo.state === "saving_locally") || cap.sync_status === "saving_locally") return "saving_locally";
    if (photos.some((photo) => photo.state === "syncing") || cap.sync_status === "syncing") return "syncing";
    const photoOk = photos.every((photo) => photo.state === "synced" || photo.upload_state === "synced");
    if (cap.sync_status === "synced" && (photos.length === 0 || photoOk)) return "synced";
    if (cap.sync_status === "pending_sync" || photos.some((photo) => photo.state === "pending_sync" || photo.state === "stored")) return "pending_sync";
    return "saved_on_device";
  }
  function statusHtml(cap) {
    const status = deriveStatus(cap);
    const klass = status === "failed" ? "error" : status === "synced" ? "ok" : "warn";
    const extra = status === "synced" ? "" : `<div>${esc(BACKUP_WARNING)}</div>`;
    return `<div class="${klass}" data-status="${status}">${esc(STATUS_HE[status] || status)}${extra}</div>`;
  }

  function rowFlags(rowId) {
    let documented = false;
    Object.values(S.photos).forEach((photo) => {
      const shows = photo.shows || {};
      if (shows.scope === "rows" && (shows.row_ids || []).includes(rowId)) documented = true;
    });
    const rowLinks = S.links.filter((link) => (link.inventory_row_id || link.row_id) === rowId).map(normLink);
    Object.values(S.captures).forEach((cap) => {
      if (!cap.dirty) return;
      (cap.links || []).forEach((link) => {
        if ((link.row_id || link.inventory_row_id) === rowId) rowLinks.push(normLink(link));
      });
    });
    const pending = rowLinks.some((link) => link.review_status === "pending_review");
    const inReview = S.reviews.some((review) => (review.status === "open" || review.status === "deferred") && (review.row_ids || []).includes(rowId));
    let identity = "none";
    if (pending) identity = "pending";
    else if (rowLinks.some((link) => link.review_status === "user_confirmed")) identity = "user_confirmed";
    else if (rowLinks.some((link) => link.review_status === "auto_linked")) identity = "auto_linked";
    return { documented, needs_review: pending || inReview, identity };
  }

  async function boot() {
    const params = new URLSearchParams(location.hash.split("?")[1] || "");
    if (location.hash.startsWith("#/row/")) S.screen = "row";
    else if (location.hash.startsWith("#/review/")) S.screen = "review";
    else if (location.hash.startsWith("#/inventory")) S.screen = "inventory";
    else if (location.hash.startsWith("#/visit")) S.screen = "visit";
    else if (location.hash.startsWith("#/ready")) S.screen = "ready";
    else if (location.hash.startsWith("#/import")) S.screen = "import";
    else S.screen = "capture";
    try {
      await Store.openDb();
      const storedArea = await Store.get("kv", "currentArea");
      if (storedArea) S.currentAreaId = storedArea.value;
      const bundle = await Store.get("kv", "bundle");
      if (bundle) applyBundle(bundle.value, false);
      const caps = await Store.all("captures");
      caps.forEach((cap) => { S.captures[cap.id] = cap; });
      const photos = await Store.all("photos");
      photos.forEach((photo) => {
        if (photo.id === "self-test") return;
        S.photos[photo.id] = photo;
        if (photo.blob) photo.url = URL.createObjectURL(photo.thumbBlob || photo.blob);
      });
    } catch (error) {
      S.error = "לא הצלחנו לפתוח את הזיכרון במכשיר. אי אפשר לסמן צילום כנשמר.";
    }
    try {
      const me = await fetch("/api/me", { credentials: "include" });
      if (me.status === 401) {
        S.authed = false;
        render();
        return;
      }
      S.authed = true;
      S.online = true;
      await refreshFromServer();
    } catch (error) {
      S.online = false;
      S.authed = Boolean(S.rows.length);
      if (!S.authed) S.error = "אין חיבור, והמלאי עוד לא נשמר במכשיר. צריך להתחבר פעם אחת לפני הסיור.";
    }
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/static/sw.js").then((reg) => {
        if (reg.sync) reg.sync.register("capture-sync").catch(() => {});
      }).catch(() => {});
      navigator.serviceWorker.addEventListener("message", (event) => {
        if (event.data && event.data.type === "sync") syncAll();
      });
    }
    render();
  }

  function applyBundle(data, fromServer) {
    S.areas = data.areas || S.areas;
    S.notes = data.notes || S.notes;
    S.files = data.files || S.files;
    S.summary = data.summary || S.summary;
    S.relationships = data.relationships || S.relationships;
    if (data.rows) {
      S.rows = data.rows.filter((row) => row.kind === "equipment");
      S.rowById = {};
      data.rows.forEach((row) => { S.rowById[row.id] = row; });
    }
    if (fromServer) {
      S.reviews = data.reviews || [];
      const serverLinks = (data.links || []).map(normLink);
      (data.captures || []).forEach((cap) => {
        const local = S.captures[cap.id];
        if (!local || (!local.dirty && (local.client_updated_at || "") <= (cap.client_updated_at || ""))) {
          S.captures[cap.id] = { ...cap, links: serverLinks.filter((link) => link.capture_id === cap.id), dirty: false };
        }
      });
      S.links = serverLinks;
      Object.values(S.captures).forEach((cap) => {
        if (!cap.dirty) return;
        S.links = S.links.filter((link) => link.capture_id !== cap.id).concat((cap.links || []).map((link) => normLink({ ...link, capture_id: cap.id })));
      });
      (data.photos || []).forEach((photo) => {
        const local = S.photos[photo.id];
        if (local && local.blob) {
          local.role = local.dirty ? local.role : photo.role;
          local.shows = local.dirty ? local.shows : photo.shows;
          local.upload_state = photo.upload_state;
          if (photo.upload_state === "synced") local.state = "synced";
        } else {
          S.photos[photo.id] = { ...photo, captureId: photo.capture_id, state: photo.upload_state === "synced" ? "synced" : "pending_sync" };
        }
      });
    }
    if (!S.currentAreaId && S.areas.length) {
      const first = S.areas.find((area) => area.source === "file") || S.areas[0];
      S.currentAreaId = first.id;
    }
  }

  async function refreshFromServer() {
    const response = await fetch("/api/bootstrap", { credentials: "include" });
    if (response.status === 401) {
      S.authed = false;
      return;
    }
    if (!response.ok) throw new Error("bootstrap");
    const data = await response.json();
    applyBundle(data, true);
    await Store.put("kv", { id: "bundle", value: data });
    S.online = true;
  }

  async function login(password) {
    S.error = "";
    const response = await fetch("/api/login", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    if (!response.ok) {
      S.error = "סיסמה שגויה.";
      render();
      return;
    }
    S.authed = true;
    await refreshFromServer();
    location.hash = "#/capture";
    render();
  }

  function recompute(cap) {
    if (cap.userTouched) return;
    const area = areaById(cap.observed_area_id);
    const parent = area && area.parent_id ? (areaById(area.parent_id) || {}).name : "";
    const result = Match.matchInventory(S.rows, {
      tag: cap.tag_text || "",
      serial: cap.serial_text || "",
      model: cap.model_text || "",
      observed_area: cap.observed_area_name || "",
      observed_parent: parent || "",
    });
    cap.match_mode = result.mode;
    cap.links = result.links || [];
    cap.suggestions = result.suggestions || [];
    cap.explanation = result.explanation || "";
    cap.review_kind = result.review_kind;
  }

  async function persistCapture(cap) {
    cap.client_updated_at = cap.client_updated_at || nowIso();
    const saved = await Store.saveCapture(cap);
    if (!saved.ok) {
      cap.sync_status = "failed";
      S.error = "הקליטה לא נשמרה במכשיר. היא לא סומנה כנשמרה. נסו שוב.";
      return false;
    }
    if (cap.sync_status !== "synced") {
      cap.sync_status = navigator.onLine ? "pending_sync" : "saved_on_device";
    }
    cap.dirty = cap.sync_status !== "synced";
    await Store.put("captures", cap);
    S.captures[cap.id] = cap;
    return true;
  }

  async function newCapture() {
    const area = areaById(S.currentAreaId);
    const cap = {
      id: crypto.randomUUID(),
      visit_id: (S.summary && S.summary.visit && S.summary.visit.id) || null,
      observed_area_id: S.currentAreaId,
      observed_area_name: area ? area.name : "",
      note: "",
      info_provided_by: "",
      tag_text: "",
      model_text: "",
      serial_text: "",
      manufacturer_text: "",
      raw_ocr: "",
      match_mode: "none",
      finalized: 0,
      created_at: nowIso(),
      client_updated_at: nowIso(),
      sync_status: "saving_locally",
      userTouched: false,
      links: [],
      suggestions: [],
      explanation: "",
      dirty: true,
    };
    const ok = await persistCapture(cap);
    if (!ok) {
      render();
      return;
    }
    S.openCaptureId = cap.id;
    S.toast = "";
    render();
  }

  async function addFiles(cap, fileList) {
    S.error = "";
    for (const file of fileList) {
      let thumbBlob = null;
      try {
        thumbBlob = await makeThumb(file);
      } catch (error) {
        thumbBlob = null;
      }
      const photo = {
        id: crypto.randomUUID(),
        captureId: cap.id,
        capture_id: cap.id,
        role: "",
        name: file.name || "photo.jpg",
        blob: file,
        thumbBlob,
        shows: { scope: "unassigned", row_ids: [] },
        state: "saving_locally",
        created_at: nowIso(),
        dirty: true,
      };
      const saved = await Store.savePhoto(photo);
      if (!saved.ok) {
        S.error = "צילום לא נשמר במכשיר. הוא לא סומן כנשמר. פנו מקום או נסו שוב.";
        render();
        return;
      }
      photo.state = navigator.onLine ? "pending_sync" : "saved_on_device";
      photo.url = URL.createObjectURL(thumbBlob || file);
      await Store.put("photos", photo);
      S.photos[photo.id] = photo;
      assignShowsFor(cap, photo);
    }
    cap.client_updated_at = nowIso();
    cap.dirty = true;
    cap.sync_status = navigator.onLine ? "pending_sync" : "saved_on_device";
    await persistCapture(cap);
    render();
    syncAll();
  }

  function assignShowsFor(cap, photo) {
    const confirmed = (cap.links || []).filter((link) => link.review_status === "auto_linked" || link.review_status === "user_confirmed");
    const ids = confirmed.map((link) => link.row_id);
    const same = ids.length === 1;
    if (!same) return;
    if (photo.role === "location" || photo.role === "system") photo.shows = { scope: "general", row_ids: [] };
    else photo.shows = { scope: "rows", row_ids: ids };
  }

  function makeThumb(file) {
    return new Promise((resolve, reject) => {
      const image = new Image();
      const url = URL.createObjectURL(file);
      image.onload = () => {
        const canvas = document.createElement("canvas");
        const scale = Math.min(1, 480 / Math.max(image.width, image.height));
        canvas.width = Math.max(1, Math.round(image.width * scale));
        canvas.height = Math.max(1, Math.round(image.height * scale));
        canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
        canvas.toBlob((blob) => {
          URL.revokeObjectURL(url);
          if (!blob) reject(new Error("thumb"));
          else resolve(blob);
        }, "image/jpeg", 0.7);
      };
      image.onerror = () => {
        URL.revokeObjectURL(url);
        reject(new Error("thumb"));
      };
      image.src = url;
    });
  }

  async function saveAndNext(cap, modeOverride) {
    if (modeOverride) cap.match_mode = modeOverride;
    if (modeOverride === "unresolved") {
      cap.userTouched = true;
      cap.links = [];
      cap.explanation = "נשמר בלי התאמה למלאי. זו תוצאה תקינה, לא ציוד חדש מוכרז.";
    }
    cap.finalized = 1;
    cap.client_updated_at = nowIso();
    cap.dirty = true;
    cap.sync_status = navigator.onLine ? "pending_sync" : "saved_on_device";
    const ok = await persistCapture(cap);
    if (!ok) {
      render();
      return;
    }
    S.links = S.links.filter((link) => link.capture_id !== cap.id).concat((cap.links || []).map((link) => normLink({ ...link, capture_id: cap.id })));
    S.toast = "הקליטה נשמרה. אפשר לעבור לפריט הבא.";
    await newCapture();
    syncAll();
  }

  async function syncAll() {
    if (!navigator.onLine) return;
    if (S.syncing) {
      S.syncAgain = true;
      return;
    }
    S.syncing = true;
    try {
      const captures = await Store.all("captures");
      for (const cap of captures) {
        if (cap.id === "self-test") continue;
        if (cap.sync_status === "synced" && !cap.dirty) continue;
        const sentAt = cap.client_updated_at;
        cap.sync_status = "syncing";
        S.captures[cap.id] = cap;
        const response = await fetch("/api/sync/capture", {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(cap),
        });
        if (!response.ok) {
          cap.sync_status = "failed";
          cap.dirty = true;
          await Store.put("captures", cap);
          continue;
        }
        const body = await response.json();
        if (!S.captures[cap.id] || (S.captures[cap.id].client_updated_at || "") <= (body.capture.client_updated_at || sentAt)) {
          cap.match_mode = body.capture.match_mode;
          cap.links = (body.links || []).map(normLink);
          cap.sync_status = "pending_sync";
        }
        const photos = (await Store.all("photos")).filter((photo) => photo.captureId === cap.id);
        let failed = false;
        for (const photo of photos) {
          if (photo.state === "synced" && !photo.dirty) continue;
          photo.state = "syncing";
          const form = new FormData();
          form.append("file", photo.blob, photo.name || "photo.jpg");
          form.append("photo_id", photo.id);
          form.append("capture_id", cap.id);
          form.append("role", photo.role || "");
          form.append("shows_json", JSON.stringify(photo.shows || { scope: "unassigned", row_ids: [] }));
          const photoResponse = await fetch("/api/sync/photo", { method: "POST", body: form, credentials: "include" });
          if (!photoResponse.ok) {
            photo.state = "failed";
            failed = true;
          } else {
            const stored = await photoResponse.json();
            photo.state = stored.stored ? "stored" : "failed";
            if (!stored.stored) failed = true;
            if (stored.stored && navigator.onLine && !cap.raw_ocr && (photo.role === "tag" || photo.role === "nameplate" || !photo.role)) {
              requestOcr(photo, cap);
            }
          }
          photo.dirty = photo.state === "failed";
          await Store.put("photos", photo);
          S.photos[photo.id] = photo;
        }
        if (cap.pendingRelationship) {
          await fetch(`/api/captures/${cap.id}/relationship`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(cap.pendingRelationship),
          });
          delete cap.pendingRelationship;
        }
        const confirm = await fetch(`/api/sync/capture/${cap.id}/confirm`, {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ photo_ids: photos.map((photo) => photo.id) }),
        });
        const confirmed = await confirm.json();
        if (confirmed.synced && !failed) {
          cap.sync_status = "synced";
          cap.dirty = false;
          photos.forEach((photo) => { photo.state = "synced"; photo.dirty = false; S.photos[photo.id] = photo; });
          for (const photo of photos) await Store.put("photos", photo);
        } else {
          cap.sync_status = "failed";
          cap.dirty = true;
        }
        await Store.put("captures", cap);
        S.captures[cap.id] = cap;
        S.links = S.links.filter((link) => link.capture_id !== cap.id).concat((cap.links || []).map((link) => normLink({ ...link, capture_id: cap.id })));
      }
      if (navigator.onLine) await refreshFromServer();
    } catch (error) {
      S.online = navigator.onLine;
    } finally {
      S.syncing = false;
      render();
      if (S.syncAgain) {
        S.syncAgain = false;
        syncAll();
      }
    }
  }

  async function requestOcr(photo, cap) {
    try {
      const response = await fetch(`/api/ocr/${photo.id}`, { method: "POST", credentials: "include" });
      if (!response.ok) return;
      const result = await response.json();
      cap.raw_ocr = result.raw_text || "";
      if (!result.available) {
        cap.ocr_message = result.message;
      } else if (!cap.userTouched && !String(cap.tag_text || "").trim()) {
        const lines = String(result.raw_text || "").split(/\n/).map((line) => line.trim()).filter(Boolean);
        if (lines.length === 1 && lines[0].length <= 40) {
          cap.tag_text = lines[0];
          recompute(cap);
          cap.sync_status = "pending_sync";
        }
      }
      cap.client_updated_at = nowIso();
      cap.dirty = true;
      if (cap.sync_status === "synced") cap.sync_status = "pending_sync";
      await persistCapture(cap);
      render();
      syncAll();
    } catch (error) {
      /* OCR can wait. The photo is already stored. */
    }
  }

  function shell(body) {
    const areaOptions = S.areas.map((area) => `<option value="${esc(area.id)}" ${area.id === S.currentAreaId ? "selected" : ""}>${esc(area.name)}</option>`).join("");
    const openReviews = S.reviews.filter((review) => review.status === "open").length;
    return `
      <div class="shell">
        <div class="top">
          <div class="brand">
            <h1>סיור ציוד</h1>
            <button class="pill" data-action="sync" type="button">${S.online ? "סנכרן עכשיו" : "אין חיבור"}</button>
          </div>
          <div class="area-row">
            <label for="current-area">האזור עכשיו</label>
            <select id="current-area" data-action="current-area">${areaOptions}</select>
          </div>
          <p class="hint">האזור כאן חל על קליטה חדשה. הוא לא מזיז קליטות שכבר צולמו.</p>
          ${S.error ? `<div class="error">${esc(S.error)}</div>` : ""}
          ${S.toast ? `<div class="ok">${esc(S.toast)}</div>` : ""}
        </div>
        ${body}
      </div>
      <nav class="nav">
        <button type="button" data-go="capture" class="${S.screen === "capture" ? "active" : ""}">קליטה</button>
        <button type="button" data-go="inventory" class="${S.screen === "inventory" || S.screen === "row" ? "active" : ""}">מלאי</button>
        <button type="button" data-go="review" class="${S.screen === "review" ? "active" : ""}">בדיקה${openReviews ? " (" + openReviews + ")" : ""}</button>
        <button type="button" data-go="visit" class="${S.screen === "visit" ? "active" : ""}">סיכום</button>
      </nav>`;
  }

  function renderCapture() {
    const cap = S.captures[S.openCaptureId];
    if (!cap) {
      const recent = Object.values(S.captures).sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))).slice(0, 3);
      return `
        <div class="card">
          <h2>קליטת ציוד</h2>
          <p class="hint">בוחרים אזור, מצלמים את הציוד ואת התג, רושמים הערה קצרה, ועוברים לפריט הבא.</p>
          <button class="btn-primary" type="button" data-action="new-capture">ציוד חדש</button>
        </div>
        ${recent.map(miniCapture).join("")}
        <p class="hint"><a href="#/ready">בדיקת מוכנות לפני הסיור</a> · <a href="#/import">קבצי המלאי</a></p>`;
    }
    const photos = photosOf(cap.id);
    const thumbs = photos.map((photo) => `
      <div class="thumb">
        <img src="${esc(photo.url || `/api/photos/${photo.id}/thumb`)}" alt="צילום">
        <select data-action="role" data-photo="${esc(photo.id)}">
          ${["", "overall", "tag", "nameplate", "accessories", "location", "system"].map((role) => `<option value="${role}" ${photo.role === role ? "selected" : ""}>${esc(roleLabel(role))}</option>`).join("")}
        </select>
        <div class="hint">${esc(STATUS_HE[photo.state] || photo.upload_state || "")}</div>
      </div>`).join("");
    const suggestions = (cap.suggestions || []).map((item) => `
      <label class="suggest">
        <input type="checkbox" data-action="pick-row" data-row="${esc(item.row_id)}" ${((cap.links || []).some((link) => link.row_id === item.row_id)) ? "checked" : ""}>
        <div>${ltr(item.tag)} ${esc(item.description)}</div>
        <div class="hint">${esc(item.sheet_name)} שורה ${esc(item.original_row)} · אזור ברשימה ${ltr(item.listed_area || "לא ידוע")}</div>
        <div class="hint">${esc(item.why)}</div>
      </label>`).join("");
    const manual = (cap.manualResults || []).map((row) => `
      <label class="suggest">
        <input type="checkbox" data-action="pick-row" data-row="${esc(row.id)}">
        <div>${ltr(row.tag_original)} ${esc(row.description)}</div>
        <div class="hint">${esc(row.sheet_name)} שורה ${esc(row.original_row)} · ${ltr(row.listed_area || "לא ידוע")}</div>
      </label>`).join("");
    return `
      <div class="card" data-capture="${esc(cap.id)}">
        <h2>קליטה <span class="ltr">${esc(cap.id.slice(0, 8))}</span></h2>
        <p><b>אזור הקליטה הזו:</b> ${ltr(cap.observed_area_name || "לא ידוע")}</p>
        <button class="btn-quiet" type="button" data-action="apply-current-area">עדכן את הקליטה הזו לאזור הנוכחי</button>
        ${statusHtml(cap)}
        <div class="actions">
          <label class="btn file-btn">מצלמה<input type="file" accept="image/*" capture="environment" data-action="files" multiple></label>
          <label class="btn file-btn">קובץ<input type="file" accept="image/*" data-action="files" multiple></label>
        </div>
        <div class="thumbs">${thumbs || `<p class="hint">עדיין אין צילום. אפשר גם להמשיך בלי צילום.</p>`}</div>
        <label>תג זיהוי<input id="tag-field" class="ltr" dir="ltr" type="text" data-action="tag" value="${esc(cap.tag_text || "")}" autocomplete="off"></label>
        <div class="actions">
          <label class="grow">דגם<input class="ltr" dir="ltr" type="text" data-action="model" value="${esc(cap.model_text || "")}"></label>
          <label class="grow">מספר סידורי<input class="ltr" dir="ltr" type="text" data-action="serial" value="${esc(cap.serial_text || "")}"></label>
        </div>
        <p class="hint">תג, דגם ומספר סידורי הם שדות נפרדים. שינוי התג מחשב את ההתאמה מחדש.</p>
        ${cap.raw_ocr ? `<div class="card"><div class="hint">טקסט שחולץ מהתמונה, בלי תיקון תווים</div><pre class="ltr">${esc(cap.raw_ocr)}</pre>${ocrChips(cap)}</div>` : ""}
        ${cap.ocr_message ? `<p class="hint">${esc(cap.ocr_message)}</p>` : ""}
        <label>הערה<textarea data-action="note">${esc(cap.note || "")}</textarea></label>
        <div class="actions">
          <button class="btn" type="button" data-action="dictate">הכתבה</button>
        </div>
        <label>נמסר על ידי (רשות)<input type="text" data-action="person" value="${esc(cap.info_provided_by || "")}" placeholder="בלי שם אם לא נאמר"></label>
        <div class="${cap.match_mode === "auto" ? "ok" : "warn"}">
          <b>${cap.match_mode === "auto" ? "קושר אוטומטית" : cap.match_mode === "pending" ? "ממתין לבחירה" : "בלי קישור אוטומטי"}</b>
          <div>${esc(cap.explanation || "")}</div>
          ${cap.match_mode === "auto" ? `<button class="btn" type="button" data-action="clear-link">בטל קישור</button>` : ""}
        </div>
        ${suggestions}
        <label>חיפוש ידני במלאי<input class="ltr" dir="ltr" type="text" data-action="manual" placeholder="תג, תיאור, דגם או מספר סידורי"></label>
        ${manual}
        ${(cap.links || []).length > 1 ? photoAssign(cap, photos) : ""}
        <details>
          <summary>הערה על קשר בין פריטים</summary>
          <p class="hint">נשמר רק אם מישהו אמר את זה. קישור צילום לכמה שורות לא יוצר קשר כזה.</p>
          <textarea data-action="rel-text" placeholder="למשל: הטכנאי אומר שהמשאבה משרתת כלי מסוים"></textarea>
          <input data-action="rel-person" placeholder="מי אמר, אם ידוע">
          <select data-action="rel-status"><option value="tentative">השערה</option><option value="confirmed">אושר בעל פה</option><option value="unclear">לא ברור</option></select>
          <button class="btn" type="button" data-action="save-rel">שמור הערת קשר</button>
        </details>
        <div class="actions">
          <button class="btn" type="button" data-action="unreadable">השלט לא קריא</button>
          <button class="btn" type="button" data-action="save-unresolved">שמור בלי התאמה</button>
          <button class="btn-primary" type="button" data-action="save-next">שמור ועבור לפריט הבא</button>
        </div>
      </div>`;
  }

  function roleLabel(role) {
    return { "": "בלי תפקיד", overall: "מבט כללי", tag: "תג זיהוי", nameplate: "שלט יצרן", accessories: "אביזר", location: "מיקום כללי", system: "מבט מערכת" }[role] || role;
  }
  function ocrChips(cap) {
    const lines = String(cap.raw_ocr || "").split(/\n/).map((line) => line.trim()).filter(Boolean).slice(0, 6);
    return `<div class="actions">${lines.map((line) => `<button type="button" class="btn" data-action="use-ocr" data-text="${esc(line)}">כתג: ${ltr(line)}</button>`).join("")}</div>`;
  }
  function photoAssign(cap, photos) {
    const rows = (cap.links || []).map((link) => S.rowById[link.row_id]).filter(Boolean);
    return photos.map((photo) => `
      <div class="hint">שיוך ${ltr(photo.id.slice(0, 8))}: ${rows.map((row) => `<label><input type="checkbox" data-action="shows" data-photo="${esc(photo.id)}" data-row="${esc(row.id)}" ${((photo.shows || {}).row_ids || []).includes(row.id) ? "checked" : ""}> ${ltr(row.tag_original)}</label>`).join(" ")}
      <button type="button" class="btn-quiet" data-action="shows-general" data-photo="${esc(photo.id)}">תמונת הקשר בלבד</button></div>`).join("");
  }
  function miniCapture(cap) {
    const photos = photosOf(cap.id);
    return `<button class="list-item" type="button" data-action="open-capture" data-id="${esc(cap.id)}">
      ${photos[0] && photos[0].url ? `<img src="${esc(photos[0].url)}" alt="">` : `<div class="ph"></div>`}
      <div><b>${ltr(cap.observed_area_name || "לא ידוע")}</b><div class="hint">${esc(STATUS_HE[deriveStatus(cap)])} · ${photos.length} צילומים · ${ltr(cap.tag_text || "בלי תג")}</div></div>
    </button>`;
  }

  function renderInventory() {
    const q = S.query.trim().toLowerCase();
    let rows = S.rows;
    if (S.areaFilter) rows = rows.filter((row) => (row.listed_area || "") === S.areaFilter || row.sheet_name === S.areaFilter);
    if (q) {
      rows = rows.filter((row) => [row.tag_original, row.description, row.serial, row.model, row.manufacturer, row.remarks, row.sheet_name].join(" ").toLowerCase().includes(q));
    }
    if (S.filter === "documented") rows = rows.filter((row) => rowFlags(row.id).documented);
    if (S.filter === "not_documented") rows = rows.filter((row) => !rowFlags(row.id).documented);
    if (S.filter === "needs_review") rows = rows.filter((row) => rowFlags(row.id).needs_review);
    const shown = rows.slice(0, S.listLimit);
    const areas = [...new Set(S.rows.map((row) => row.listed_area || row.sheet_name || "לא ידוע"))];
    return `
      <input type="search" placeholder="חיפוש תג, תיאור או מספר" value="${esc(S.query)}" data-action="inv-search">
      <div class="filters">
        ${[["all", "הכל"], ["not_documented", "בלי תיעוד"], ["documented", "תועד"], ["needs_review", "צריך בדיקה"]].map(([id, label]) => `<button type="button" data-action="filter" data-filter="${id}" class="${S.filter === id ? "on" : ""}">${label}</button>`).join("")}
      </div>
      <select data-action="area-filter"><option value="">כל האזורים שברשימה</option>${areas.map((area) => `<option ${S.areaFilter === area ? "selected" : ""}>${esc(area)}</option>`).join("")}</select>
      <p class="hint">${rows.length} שורות מוצגות לפי הסינון. זו ספירת שורות, לא ספירת מכונות. תיעוד פירושו צילום של הפריט עצמו, לא אימות מצב או מוכנות למכירה.</p>
      ${shown.map((row) => {
        const flag = rowFlags(row.id);
        const photo = Object.values(S.photos).find((item) => (item.shows || {}).scope === "rows" && (item.shows.row_ids || []).includes(row.id));
        const chips = [
          flag.documented ? `<span class="chip ok">תועד</span>` : `<span class="chip">בלי תיעוד</span>`,
          flag.needs_review ? `<span class="chip warn">צריך בדיקה</span>` : "",
          flag.identity === "auto_linked" ? `<span class="chip ok">קושר אוטומטית</span>` : "",
          flag.identity === "user_confirmed" ? `<span class="chip ok">אושר ידנית</span>` : "",
          ...(row.labels || []).map((label) => `<span class="chip warn">${esc(label)}</span>`),
          row.duplicate_group ? `<span class="chip">כפילות משוערת</span>` : "",
        ].join("");
        return `<button class="list-item" type="button" data-go-row="${esc(row.id)}">
          ${photo && photo.url ? `<img src="${esc(photo.url)}" alt="">` : photo ? `<img src="/api/photos/${esc(photo.id)}/thumb" alt="">` : `<div class="ph"></div>`}
          <div><b>${ltr(row.tag_original || "בלי תג")}</b> ${esc(row.description || "")}
            <div class="hint">${esc(row.sheet_name)} · שורה ${esc(row.original_row)} · ${ltr(row.listed_area || "לא ידוע")}</div>
            <div class="chips">${chips}</div>
          </div>
        </button>`;
      }).join("")}
      ${rows.length > S.listLimit ? `<button class="btn" type="button" data-action="more">עוד שורות</button>` : ""}`;
  }

  function renderRow(id) {
    const row = S.rowById[id];
    if (!row) return `<p>השורה לא נמצאה במכשיר.</p>`;
    const flag = rowFlags(row.id);
    const photos = Object.values(S.photos).filter((photo) => {
      const shows = photo.shows || {};
      return (shows.row_ids || []).includes(row.id) || linksOf(photo.captureId || photo.capture_id).some((link) => link.row_id === row.id);
    });
    const caps = [...new Set(photos.map((photo) => photo.captureId || photo.capture_id).concat(S.links.filter((link) => link.inventory_row_id === row.id).map((link) => link.capture_id)))];
    const observed = caps.map((cid) => S.captures[cid]).filter(Boolean).map((cap) => cap.observed_area_name).filter(Boolean);
    const rawRows = Object.entries(row.raw || {}).map(([key, value]) => {
      const label = key.toLowerCase() === "price" ? "price (לא מחיר)" : key;
      return `<div><span>${esc(label)}</span><span class="ltr">${esc(value)}</span></div>`;
    }).join("");
    const differ = observed.some((area) => Match.areaConflict(area, row.listed_area, ""));
    return `
      <button class="btn-quiet" type="button" data-go="inventory">חזרה למלאי</button>
      <div class="card">
        <h2>${ltr(row.tag_original || "בלי תג")}</h2>
        <p>${esc(row.description || "בלי תיאור")}</p>
        <p class="hint">${esc(row.source_file)} · ${esc(row.sheet_name)} · שורה ${esc(row.original_row)} · מזהה ${ltr(row.id)}</p>
        <div class="chips">
          ${flag.documented ? `<span class="chip ok">יש צילום של הפריט</span>` : `<span class="chip">אין עדיין צילום של הפריט עצמו</span>`}
          ${flag.identity === "auto_linked" ? `<span class="chip ok">קושר אוטומטית</span>` : ""}
          ${flag.identity === "user_confirmed" ? `<span class="chip ok">אושר ידנית</span>` : ""}
          ${(row.labels || []).map((label) => `<span class="chip warn">${esc(label)}</span>`).join("")}
        </div>
        <p><b>אזור ברשימה:</b> ${ltr(row.listed_area || "לא ידוע")}</p>
        <p><b>אזור שנצפה:</b> ${observed.length ? observed.map((area) => ltr(area)).join(", ") : "עדיין לא נצפה בסיור"}</p>
        ${differ ? `<div class="warn">האזור בסיור והאזור ברשימה שונים. שניהם נשמרים.</div>` : ""}
        ${row.system_number ? `<p class="hint">מספר מערכת בקובץ: ${ltr(row.system_number)}. זה לא קשר מערכת מאושר ולא אומר שמוכרים יחד.</p>` : ""}
        ${row.duplicate_group ? `<p class="hint">השורה מסומנת ככפילות משוערת של שורות עם אותו תג ואותם נתונים. השורות לא נמחקו.</p>` : ""}
        <p class="hint">כמות בקובץ: ${row.quantity ? ltr(row.quantity) : "לא צוינה"}.</p>
      </div>
      <div class="card"><h3>צילומים והערות</h3>
        ${photos.map((photo) => `<figure><img src="${esc(photo.url || `/api/photos/${photo.id}/thumb`)}" alt="צילום" style="max-width:100%;border-radius:12px"><figcaption class="hint">${esc(roleLabel(photo.role || ""))} · ${esc((S.captures[photo.captureId || photo.capture_id] || {}).note || "")}</figcaption></figure>`).join("") || `<p class="hint">אין צילום מקושר.</p>`}
      </div>
      <div class="card"><h3>הערות קשר שנאמרו</h3>
        <p class="hint">הערה כזו לא נוצרת מקישור הצילום.</p>
        ${S.relationships.filter((rel) => caps.includes(rel.capture_id)).map((rel) => `<p>${esc(rel.text)} <span class="hint">${esc(rel.person || "")} · ${esc(rel.status)}</span></p>`).join("") || `<p class="hint">אין.</p>`}
      </div>
      <details class="card"><summary>תאים מקוריים</summary><div class="raw">${rawRows}</div></details>`;
  }

  function renderReview() {
    const items = S.reviews.filter((review) => S.reviewFilter === "all" ? true : (S.reviewFilter === "visit" ? review.queue === "visit" : review.queue !== "visit"));
    const open = S.activeReview ? S.reviews.find((review) => review.id === S.activeReview) : null;
    if (open) return renderReviewDetail(open);
    return `
      <div class="filters">
        <button type="button" data-action="rev-filter" data-filter="visit" class="${S.reviewFilter === "visit" ? "on" : ""}">מהסיור</button>
        <button type="button" data-action="rev-filter" data-filter="import" class="${S.reviewFilter === "import" ? "on" : ""}">מהקובץ</button>
      </div>
      <p class="hint">כאן שאלות על תג לא חד-משמעי, אזור סותר, תג כפול, פריט בלי התאמה, שלט לא קריא, או קשר לא ברור.</p>
      ${items.filter((review) => review.status !== "resolved").map((review) => `
        <button class="card" type="button" data-action="open-review" data-id="${esc(review.id)}" style="width:100%;text-align:right">
          <b>${esc(review.question)}</b>
          <div class="hint">${esc(review.kind)} · ${esc(review.status)} · ${esc(review.priority)}</div>
        </button>`).join("") || `<p class="hint">אין שאלות פתוחות בקבוצה הזו.</p>`}`;
  }

  function renderReviewDetail(review) {
    const cap = review.capture_id ? S.captures[review.capture_id] : null;
    const photos = review.capture_id ? photosOf(review.capture_id) : [];
    const rowIds = review.row_ids || [];
    const rows = rowIds.map((id) => S.rowById[id]).filter(Boolean);
    return `
      <button class="btn-quiet" type="button" data-action="close-review">חזרה</button>
      <div class="split">
        <div class="card">
          <h2>השאלה</h2>
          <p>${esc(review.question)}</p>
          ${review.missing_reason ? `<div class="warn">${esc(review.missing_reason)}</div>` : ""}
          ${cap ? `<p class="hint">אזור שנצפה: ${ltr(cap.observed_area_name || "לא ידוע")} · תג שהוקלד: ${ltr(cap.tag_text || "")}</p><p>${esc(cap.note || "")}</p>` : ""}
          ${photos.map((photo) => `<img src="${esc(photo.url || `/api/photos/${photo.id}/thumb`)}" alt="צילום" style="width:100%;border-radius:12px;margin:6px 0">`).join("") || `<p class="hint">אין צילום לשאלה הזו.</p>`}
        </div>
        <div class="card">
          <h2>שורות אפשריות</h2>
          ${rows.map((row) => `<label class="suggest"><input type="checkbox" data-review-row="${esc(row.id)}"> ${ltr(row.tag_original)} ${esc(row.description)}<div class="hint">${esc(row.sheet_name)} שורה ${esc(row.original_row)} · ${ltr(row.listed_area || "לא ידוע")}</div></label>`).join("") || `<p class="hint">אין שורת מועמדת. אפשר להשאיר בלי התאמה.</p>`}
          <textarea id="review-text" placeholder="תשובה כתובה, אם צריך"></textarea>
          <div class="actions">
            <button class="btn-primary" type="button" data-action="review-link" data-id="${esc(review.id)}">קשר</button>
            <button class="btn" type="button" data-action="review-separate" data-id="${esc(review.id)}">השאר נפרד</button>
            <button class="btn" type="button" data-action="review-resolve" data-id="${esc(review.id)}">סגור עם התשובה</button>
            <button class="btn" type="button" data-action="review-defer" data-id="${esc(review.id)}">דחה</button>
          </div>
          ${cap ? `<label class="btn file-btn">הוסף צילום<input type="file" accept="image/*" data-action="review-photo" data-id="${esc(review.id)}"></label>` : ""}
        </div>
      </div>`;
  }

  function localSummary() {
    const flags = {};
    S.rows.forEach((row) => { flags[row.id] = rowFlags(row.id); });
    const by = {};
    S.rows.forEach((row) => {
      const area = row.listed_area || "לא ידוע";
      const bucket = by[area] || (by[area] = { area, rows: 0, documented: 0 });
      bucket.rows += 1;
      if (flags[row.id].documented) bucket.documented += 1;
    });
    const caps = Object.values(S.captures);
    const localOnly = caps.filter((cap) => cap.sync_status !== "synced").length;
    const synced = caps.filter((cap) => deriveStatus(cap) === "synced").length;
    const documented = Object.values(flags).filter((flag) => flag.documented).length;
    const unresolved = caps.filter((cap) => cap.finalized && !(cap.links || linksOf(cap.id)).some((link) => link.review_status === "auto_linked" || link.review_status === "user_confirmed")).length;
    return { by: Object.values(by), localOnly, synced, documented, unresolved, rows: S.rows.length, notDocumented: S.rows.length - documented };
  }

  function renderVisit() {
    const sum = localSummary();
    const revisit = S.rows.filter((row) => !rowFlags(row.id).documented).slice(0, 30);
    return `
      <div class="card">
        <h2>סיכום הביקור</h2>
        <p class="hint">המספרים סופרים שורות מלאי וקליטות, לא מכונות פיזיות. תגים כפולים נשארים שורות נפרדות.</p>
        <div class="metrics">
          <div class="metric"><b>${sum.synced}</b>קליטות שסונכרנו</div>
          <div class="metric"><b>${sum.localOnly}</b>עוד לא סונכרנו</div>
          <div class="metric"><b>${sum.documented}</b>שורות עם צילום פריט</div>
          <div class="metric"><b>${sum.notDocumented}</b>שורות בלי תיעוד</div>
        </div>
        <p class="hint">קליטות בלי התאמה: ${sum.unresolved}.</p>
      </div>
      <div class="card"><h3>לפי האזור שברשימה</h3>
        ${sum.by.map((area) => `<p>${ltr(area.area)} · ${area.documented}/${area.rows} שורות עם צילום</p>`).join("")}
      </div>
      <div class="card"><h3>כדאי לחזור אליהן</h3>
        ${revisit.map((row) => `<button class="btn" type="button" data-go-row="${esc(row.id)}">${ltr(row.tag_original || row.description)} · ${esc(row.sheet_name)}</button>`).join("") || `<p class="hint">אין.</p>`}
      </div>
      <div class="actions">
        <a class="btn" href="/api/export/inventory.csv">ייצוא טבלה</a>
        <a class="btn-primary" href="/api/export/package.zip">חבילת צילומים</a>
        <button class="btn" type="button" data-go="ready">מוכנות</button>
      </div>`;
  }

  function renderReady() {
    const r = S.readiness;
    const line = (ok, text) => `<p>${ok ? "✓" : "…"} ${esc(text)}</p>`;
    return `
      <div class="card">
        <h2>לפני הסיור</h2>
        ${line(S.rows.length > 0, `המלאי זמין (${S.rows.length} שורות ציוד)`)}
        ${line(true, "אפשר לבחור צילום מהמצלמה או מהקבצים")}
        ${line(S.offlineTest === true, S.offlineTest === false ? "בדיקת שמירה בלי רשת נכשלה" : "בדיקת שמירה במכשיר")}
        ${line(r.storage_writable === true, "השרת יכול לשמור קבצים")}
        ${line(r.ocr_available === true, r.ocr_available ? "זיהוי טקסט זמין כשיש חיבור" : "זיהוי טקסט לא זמין. הקליטה עובדת גם בלי זה")}
        <button class="btn" type="button" data-action="run-ready">הרץ בדיקה</button>
        <p class="hint">${esc(BACKUP_WARNING)}</p>
        ${(S.notes || []).map((note) => `<p class="hint">${esc(note)}</p>`).join("")}
      </div>`;
  }

  function renderImport() {
    const preview = S.importPreview;
    if (!preview) return `<div class="card"><p>טוען את מבנה הקבצים…</p></div>`;
    const file = (S.files || []).find((item) => item.kind === "plant");
    const mapping = (file && file.mapping) || preview.preview.suggested_mapping;
    const headers = (preview.preview.sheets[0] && preview.preview.sheets[0].headers) || [];
    const fields = ["tag", "description", "remarks", "manufacturer", "model", "serial", "quantity", "specs", "material", "system_number", "efd"];
    const labels = { tag: "תג", description: "תיאור", remarks: "הערות", manufacturer: "יצרן", model: "דגם", serial: "מספר סידורי", quantity: "כמות", specs: "נתון טכני", material: "חומר", system_number: "מספר מערכת בקובץ", efd: "Efd" };
    return `
      <div class="card">
        <h2>קבצי המלאי</h2>
        ${(preview.notes || []).map((note) => `<p class="hint">${esc(note)}</p>`).join("")}
        <p class="hint">העמודה price לא מוצעת למיפוי. היא אינה מחיר מכירה.</p>
        <form data-action="mapping">
          <label>מקור האזור
            <select name="area_source">
              <option value="sheet" ${mapping.area_source === "sheet" ? "selected" : ""}>שם הגיליון</option>
              <option value="unknown" ${mapping.area_source === "unknown" ? "selected" : ""}>לא ידוע</option>
              ${headers.filter((header) => header.toLowerCase() !== "price").map((header) => `<option value="column:${esc(header)}" ${mapping.area_source === "column:" + header ? "selected" : ""}>${esc(header)}</option>`).join("")}
            </select>
          </label>
          ${fields.map((field) => `<label>${labels[field]}<select name="${field}"><option value="">לא למפות</option>${headers.filter((header) => header.toLowerCase() !== "price").map((header) => `<option ${((mapping.columns || {})[field] === header) ? "selected" : ""}>${esc(header)}</option>`).join("")}</select></label>`).join("")}
          <button class="btn-primary" type="submit">שמור מיפוי</button>
        </form>
      </div>
      ${(preview.preview.sheets || []).map((sheet) => `<details class="card"><summary>${ltr(sheet.name)} · ${sheet.rows} שורות</summary><p class="hint">${esc((sheet.headers || []).join(", "))}</p></details>`).join("")}
      <div class="card">
        <h3>החלפת קובץ</h3>
        <p class="hint">קובץ מפעל נוסף בלי החלפה יידחה, כדי לא לספור את המלאי פעמיים.</p>
        <form data-action="upload-plant"><input type="file" name="file" accept=".xlsx,.csv"><label><input type="checkbox" name="replace"> להחליף את קובץ המפעל</label><button class="btn" type="submit">טען</button></form>
      </div>`;
  }

  function render() {
    if (!S.authed) {
      $(`<form class="card login" data-action="login"><h1>סיור ציוד</h1><p class="hint">כניסה מקומית לפני הסיור.</p>${S.error ? `<div class="error">${esc(S.error)}</div>` : ""}<label>סיסמה<input type="password" name="password" autocomplete="current-password"></label><button class="btn-primary" type="submit">כניסה</button></form>`);
      return;
    }
    let body = "";
    if (S.screen === "capture") body = renderCapture();
    else if (S.screen === "inventory") body = renderInventory();
    else if (S.screen === "row") body = renderRow(location.hash.split("/")[2]);
    else if (S.screen === "review") body = renderReview();
    else if (S.screen === "visit") body = renderVisit();
    else if (S.screen === "ready") body = renderReady();
    else if (S.screen === "import") body = renderImport();
    $(shell(body));
  }

  function go(screen) {
    S.screen = screen;
    S.error = "";
    location.hash = "#/" + screen;
    if (screen === "import" && !S.importPreview) loadImport();
    if (screen === "ready") runReady();
    render();
  }

  async function loadImport() {
    const response = await fetch("/api/import/preview", { credentials: "include" });
    if (response.ok) S.importPreview = await response.json();
    render();
  }

  async function runReady() {
    try {
      const health = await fetch("/api/health", { credentials: "include" });
      S.readiness = await health.json();
      S.online = true;
    } catch (error) {
      S.readiness = { storage_writable: false, ocr_available: false };
      S.online = false;
    }
    try {
      S.offlineTest = await Store.testOfflineStore();
    } catch (error) {
      S.offlineTest = false;
    }
    render();
  }

  document.getElementById("app").addEventListener("click", async (event) => {
    const target = event.target.closest("[data-action], [data-go], [data-go-row]");
    if (!target) return;
    if (target.dataset.go) {
      go(target.dataset.go);
      return;
    }
    if (target.dataset.goRow) {
      S.screen = "row";
      location.hash = "#/row/" + target.dataset.goRow;
      render();
      return;
    }
    const action = target.dataset.action;
    const cap = S.captures[S.openCaptureId];
    if (action === "sync") syncAll();
    if (action === "new-capture") newCapture();
    if (action === "save-next" && cap) saveAndNext(cap);
    if (action === "save-unresolved" && cap) saveAndNext(cap, "unresolved");
    if (action === "clear-link" && cap) {
      cap.userTouched = true;
      cap.match_mode = "unresolved";
      cap.links = [];
      cap.explanation = "הקישור האוטומטי בוטל. אפשר לבחור שורה אחרת או להשאיר בלי התאמה.";
      cap.client_updated_at = nowIso();
      await persistCapture(cap);
      render();
    }
    if (action === "apply-current-area" && cap) {
      const area = areaById(S.currentAreaId);
      cap.observed_area_id = S.currentAreaId;
      cap.observed_area_name = area ? area.name : "";
      cap.client_updated_at = nowIso();
      if (!cap.userTouched) recompute(cap);
      await persistCapture(cap);
      render();
    }
    if (action === "dictate" && cap) {
      const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!Speech) {
        S.toast = "ההכתבה לא זמינה בדפדפן הזה. אפשר להקליד.";
        render();
        return;
      }
      const rec = new Speech();
      rec.lang = "he-IL";
      rec.onresult = async (ev) => {
        cap.note = ((cap.note || "") + " " + ev.results[0][0].transcript).trim();
        await persistCapture(cap);
        render();
      };
      rec.start();
    }
    if (action === "unreadable" && cap) {
      const photo = photosOf(cap.id)[0];
      await fetch(`/api/captures/${cap.id}/nameplate-unreadable`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ photo_id: photo ? photo.id : "" }),
      }).catch(() => {});
      S.toast = "סומן שהשלט לא קריא. השאלה נשארת פתוחה עד שתהיה תשובה.";
      render();
    }
    if (action === "save-rel" && cap) {
      const text = document.querySelector("[data-action=rel-text]").value.trim();
      const person = document.querySelector("[data-action=rel-person]").value.trim();
      const status = document.querySelector("[data-action=rel-status]").value;
      if (!text) return;
      cap.pendingRelationship = { text, person, status, row_ids: (cap.links || []).map((link) => link.row_id) };
      cap.dirty = true;
      await persistCapture(cap);
      S.toast = "הערת הקשר נשמרה עם הקליטה. היא לא נוצרה מקישור הצילום.";
      syncAll();
    }
    if (action === "use-ocr" && cap) {
      cap.tag_text = target.dataset.text || "";
      cap.userTouched = false;
      recompute(cap);
      cap.client_updated_at = nowIso();
      await persistCapture(cap);
      render();
    }
    if (action === "open-capture") {
      S.openCaptureId = target.dataset.id;
      S.screen = "capture";
      render();
    }
    if (action === "filter") {
      S.filter = target.dataset.filter;
      S.listLimit = 40;
      render();
    }
    if (action === "more") {
      S.listLimit += 40;
      render();
    }
    if (action === "rev-filter") {
      S.reviewFilter = target.dataset.filter;
      render();
    }
    if (action === "open-review") {
      S.activeReview = target.dataset.id;
      render();
    }
    if (action === "close-review") {
      S.activeReview = null;
      render();
    }
    if (action === "review-link" || action === "review-separate" || action === "review-defer" || action === "review-resolve") {
      const id = target.dataset.id;
      let body = { action: "defer" };
      if (action === "review-link") {
        body = { action: "link", row_ids: [...document.querySelectorAll("[data-review-row]:checked")].map((box) => box.dataset.reviewRow) };
      } else if (action === "review-separate") body = { action: "leave_separate" };
      else if (action === "review-resolve") body = { action: "resolve", text: document.getElementById("review-text").value };
      const response = await fetch(`/api/review/${id}/action`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const result = await response.json();
      if (result.missing) S.error = result.missing;
      else S.error = "";
      await refreshFromServer().catch(() => {});
      if (result.status === "resolved") S.activeReview = null;
      render();
    }
    if (action === "shows-general") {
      const photo = S.photos[target.dataset.photo];
      if (photo) {
        photo.shows = { scope: "general", row_ids: [] };
        photo.dirty = true;
        await Store.put("photos", photo);
        render();
      }
    }
    if (action === "run-ready") runReady();
  });

  document.getElementById("app").addEventListener("change", async (event) => {
    const target = event.target;
    const cap = S.captures[S.openCaptureId];
    if (target.dataset.action === "current-area") {
      S.currentAreaId = target.value;
      await Store.put("kv", { id: "currentArea", value: S.currentAreaId });
      if (cap && !captureStarted(cap)) {
        const area = areaById(S.currentAreaId);
        cap.observed_area_id = S.currentAreaId;
        cap.observed_area_name = area ? area.name : "";
        if (!cap.userTouched) recompute(cap);
        await persistCapture(cap);
      }
      render();
      return;
    }
    if (target.dataset.action === "files" && cap) {
      await addFiles(cap, target.files);
      target.value = "";
    }
    if (target.dataset.action === "role") {
      const photo = S.photos[target.dataset.photo];
      if (!photo) return;
      photo.role = target.value;
      photo.dirty = true;
      if (photo.role === "location" || photo.role === "system") photo.shows = { scope: "general", row_ids: [] };
      else assignShowsFor(S.captures[photo.captureId] || cap, photo);
      await Store.put("photos", photo);
      render();
    }
    if (target.dataset.action === "pick-row" && cap) {
      const selected = [...document.querySelectorAll("[data-action=pick-row]:checked")].map((box) => box.dataset.row);
      cap.userTouched = true;
      cap.match_mode = selected.length ? "user" : "unresolved";
      cap.links = selected.map((rowId) => ({ row_id: rowId, method: "user", review_status: "user_confirmed", reason: "נבחר ידנית בסיור." }));
      cap.explanation = selected.length ? "נבחרו שורות ידנית. זה לא קשר מערכת." : "אין שורה נבחרת.";
      photosOf(cap.id).forEach((photo) => assignShowsFor(cap, photo));
      cap.client_updated_at = nowIso();
      await persistCapture(cap);
      for (const photo of photosOf(cap.id)) await Store.put("photos", photo);
      render();
    }
    if (target.dataset.action === "shows") {
      const photo = S.photos[target.dataset.photo];
      if (!photo) return;
      const ids = [...document.querySelectorAll(`[data-action=shows][data-photo="${photo.id}"]:checked`)].map((box) => box.dataset.row);
      photo.shows = { scope: ids.length ? "rows" : "unassigned", row_ids: ids };
      photo.dirty = true;
      await Store.put("photos", photo);
    }
    if (target.dataset.action === "area-filter") {
      S.areaFilter = target.value;
      render();
    }
    if (target.dataset.action === "review-photo") {
      const form = new FormData();
      form.append("file", target.files[0]);
      const response = await fetch(`/api/review/${target.dataset.id}/photo`, { method: "POST", body: form, credentials: "include" });
      const result = await response.json();
      S.error = result.message || "";
      await refreshFromServer().catch(() => {});
      render();
    }
  });

  document.getElementById("app").addEventListener("input", async (event) => {
    const target = event.target;
    const cap = S.captures[S.openCaptureId];
    if (!cap) {
      if (target.dataset.action === "inv-search") restoreSearch(target);
      return;
    }
    if (target.dataset.action === "tag" || target.dataset.action === "model" || target.dataset.action === "serial" || target.dataset.action === "note" || target.dataset.action === "person") {
      const field = { tag: "tag_text", model: "model_text", serial: "serial_text", note: "note", person: "info_provided_by" }[target.dataset.action];
      if (target.dataset.action === "tag" && target.value !== cap.tag_text) cap.userTouched = false;
      cap[field] = target.value;
      if (target.dataset.action === "tag" || target.dataset.action === "model" || target.dataset.action === "serial") recompute(cap);
      cap.client_updated_at = nowIso();
      cap.dirty = true;
      const selectionStart = target.selectionStart;
      await persistCapture(cap);
      render();
      const again = document.querySelector(`[data-action="${target.dataset.action}"]`);
      if (again) {
        again.focus();
        if (selectionStart != null && again.setSelectionRange) again.setSelectionRange(selectionStart, selectionStart);
      }
    }
    if (target.dataset.action === "manual") {
      const raw = target.value;
      const q = raw.trim().toLowerCase();
      cap.manualResults = !q ? [] : S.rows.filter((row) => [row.tag_original, row.description, row.serial, row.model, row.manufacturer].join(" ").toLowerCase().includes(q)).slice(0, 8);
      const selectionStart = target.selectionStart;
      render();
      const again = document.querySelector("[data-action=manual]");
      if (again) {
        again.focus();
        again.value = raw;
        if (again.setSelectionRange) again.setSelectionRange(selectionStart, selectionStart);
      }
    }
    if (target.dataset.action === "inv-search") restoreSearch(target);
  });

  function restoreSearch(target) {
    S.query = target.value;
    S.listLimit = 40;
    const selectionStart = target.selectionStart;
    const value = target.value;
    render();
    const again = document.querySelector("[data-action=inv-search]");
    if (again) {
      again.focus();
      again.value = value;
      if (again.setSelectionRange) again.setSelectionRange(selectionStart, selectionStart);
    }
  }

  document.getElementById("app").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    if (form.dataset.action === "login") {
      login(new FormData(form).get("password"));
    }
    if (form.dataset.action === "mapping") {
      const data = new FormData(form);
      const mapping = { area_source: data.get("area_source"), columns: {} };
      ["tag", "description", "remarks", "manufacturer", "model", "serial", "quantity", "specs", "material", "system_number", "efd"].forEach((field) => {
        const value = data.get(field);
        if (value) mapping.columns[field] = value;
      });
      const response = await fetch("/api/import/mapping", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: "plant", mapping }),
      });
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        S.error = err.detail || "המיפוי לא נשמר.";
      } else {
        S.toast = "המיפוי נשמר. קישורי הצילומים נשארו על אותם מזהי שורות.";
        await refreshFromServer();
      }
      render();
    }
    if (form.dataset.action === "upload-plant") {
      const data = new FormData(form);
      const upload = new FormData();
      upload.append("file", data.get("file"));
      upload.append("kind", "plant");
      upload.append("replace", data.get("replace") ? "true" : "false");
      const response = await fetch("/api/import/file", { method: "POST", body: upload, credentials: "include" });
      const body = await response.json().catch(() => ({}));
      S.error = response.ok ? "" : (body.detail || "הטעינה נדחתה.");
      if (response.ok) {
        S.toast = "הקובץ עודכן.";
        await refreshFromServer();
      }
      render();
    }
  });

  window.addEventListener("hashchange", () => {
    const hash = location.hash;
    if (hash.startsWith("#/row/")) S.screen = "row";
    else if (hash.startsWith("#/review")) S.screen = "review";
    else if (hash.startsWith("#/inventory")) S.screen = "inventory";
    else if (hash.startsWith("#/visit")) S.screen = "visit";
    else if (hash.startsWith("#/ready")) S.screen = "ready";
    else if (hash.startsWith("#/import")) S.screen = "import";
    else S.screen = "capture";
    render();
  });
  window.addEventListener("online", () => { S.online = true; syncAll(); });
  window.addEventListener("offline", () => { S.online = false; render(); });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") syncAll();
  });

  boot();
})();
