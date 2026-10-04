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
    valueOverview: null,
    valueDetail: null,
    valueQuery: "",
    valueFamily: "",
    valueInfo: "",
    comparables: null,
    visitPrep: null,
    tourResults: [],
    tourBusy: false,
    machines: [],
    machineQuery: "",
    machineFilter: "active",
    openMachine: null,
    documents: [],
    offers: [],
    docFilter: "",
    activeDocument: null,
    intakeResults: [],
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
    else if (location.hash.startsWith("#/value/row/")) S.screen = "value-detail";
    else if (location.hash.startsWith("#/value")) S.screen = "value";
    else if (location.hash.startsWith("#/comparables")) S.screen = "comparables";
    else if (location.hash.startsWith("#/visit-prep")) S.screen = "visit-prep";
    else if (location.hash.startsWith("#/machines")) S.screen = "machines";
    else if (location.hash.startsWith("#/offers")) S.screen = "offers";
    else if (location.hash.startsWith("#/documents")) S.screen = "documents";
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
    if (S.authed && navigator.onLine) await flushTourQueue();
    render();
  }

  function applyBundle(data, fromServer) {
    S.areas = data.areas || S.areas;
    S.notes = data.notes || S.notes;
    S.files = data.files || S.files;
    S.summary = data.summary || S.summary;
    S.relationships = data.relationships || S.relationships;
    if (data.machines) S.machines = data.machines;
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
      await flushTourQueue();
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
        const photos = (await Store.all("photos")).filter((photo) => photo.captureId === cap.id && !photo.tourPending);
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
          <p class="hint">האזור כאן חל על קליטה חדשה של פריט אחד. הוא לא מזיז קליטות שכבר צולמו, ולא חל על גרירת קבוצת קבצים.</p>
          ${S.error ? `<div class="error">${esc(S.error)}</div>` : ""}
          ${S.toast ? `<div class="ok">${esc(S.toast)}</div>` : ""}
        </div>
        ${body}
      </div>
      <nav class="nav">
        <button type="button" data-go="capture" class="${S.screen === "capture" ? "active" : ""}">קליטה</button>
        <button type="button" data-go="inventory" class="${S.screen === "inventory" || S.screen === "row" ? "active" : ""}">מלאי</button>
        <button type="button" data-go="machines" class="${S.screen === "machines" ? "active" : ""}">מכונות</button>
        <button type="button" data-go="review" class="${S.screen === "review" ? "active" : ""}">בדיקה${openReviews ? " (" + openReviews + ")" : ""}</button>
        <button type="button" data-go="visit" class="${S.screen === "visit" ? "active" : ""}">סיכום</button>
        <button type="button" data-go="documents" class="${S.screen === "documents" || S.screen === "offers" ? "active" : ""}">מסמכים</button>
        <button type="button" data-go="value" class="${S.screen === "value" || S.screen === "value-detail" ? "active" : ""}">שווי</button>
      </nav>`;
  }

  function tourAreaLine(item) {
    if (item.inventory_area) return `אזור שרשום במלאי: ${item.inventory_area}. זה לא מיקום שאומת מהתמונה.`;
    if (item.area_source === "file" && item.observed_area) return `האזור נלקח מהקובץ: ${item.observed_area}`;
    if (item.area_source === "existing_capture") return `האזור נלקח מהקליטה שכבר פתוחה: ${item.observed_area || "לא ידוע"}`;
    const listed = [...new Set((item.rows || []).map((row) => row.listed_area).filter(Boolean))];
    if (listed.length) return `אזור שנצפה: לא ידוע. האזור שרשום במלאי: ${listed.join(", ")}. זה לא מיקום שאומת מהתמונה.`;
    return "אזור: לא ידוע";
  }

  function tourResultHtml(item) {
    const rows = item.filed ? (item.rows || []) : [];
    const where = rows.map((row) => `${ltr(row.tag)} ${esc(row.description)} · גיליון ${ltr(row.sheet_name)} שורה ${esc(row.original_row)}`).join("<br>");
    const labels = (item.labels || []).map((label) => `${ltr(label.tag)}${label.prefix ? " · " + esc(label.prefix) : ""}`).join("<br>");
    const machines = (item.machines || []).map((machine) => {
      const others = (machine.other_tags || []).filter((tag) => tag && tag !== machine.matched_tag);
      const sale = machine.sold_together === "confirmed" ? "מכירה יחד אושרה" : "מכירה יחד עדיין פתוחה";
      const state = machine.status === "confirmed" ? "יחידה מאושרת" : "יחידה מוצעת";
      const prefix = machine.prefix ? ` · ${esc(machine.prefix)}` : "";
      const sibling = others.length ? ` זה לא צילום של ${others.map((tag) => ltr(tag)).join(", ")}.` : "";
      return `<div class="hint">צילום של ${ltr(machine.matched_tag)}${prefix}, בתוך ${esc(machine.name)} (${state}, ${sale}).${sibling}</div>`;
    }).join("");
    const state = item.saved ? "נשמר בשרת" : "לא נשמר בשרת";
    const filedLine = where || labels;
    return `
      <div class="tour-file" data-tour-file="${esc(item.file_id || item.original_name || "")}" data-saved="${item.saved ? "yes" : "no"}" data-filed="${item.filed ? "yes" : "no"}">
        <b>${ltr(item.original_name || "קובץ")}</b>
        <div class="${item.saved ? "ok" : "error"}">${esc(state)}</div>
        ${item.filed ? `<div>תויק אל ${filedLine}</div>${machines}` : (item.saved ? `<div class="warn">אין התאמה ברורה. הקובץ בתור הבדיקה, בלי ניחוש.</div>` : "")}
        <div class="hint">${esc(tourAreaLine(item))}</div>
        ${item.saved ? "" : `<div class="hint">${esc(item.message || "הקובץ לא נשמר בשרת.")}</div>`}
      </div>`;
  }

  function tourPanel() {
    const shown = new Set((S.tourResults || []).map((item) => item.file_id));
    const pending = Object.values(S.photos).filter((photo) => photo.tourPending && photo.state !== "synced" && !shown.has(photo.id));
    const pendingHtml = pending.map((photo) => tourResultHtml({
      file_id: photo.id,
      original_name: photo.name,
      saved: false,
      filed: false,
      rows: [],
      area_source: "unknown",
      message: "אין חיבור, או שההעלאה נכשלה. הקובץ לא נשמר בשרת.",
    })).join("");
    const results = (S.tourResults || []).map(tourResultHtml).join("");
    return `
      <div class="drop-zone" data-drop-zone="tour">
        <h2>קבצים מהסיור</h2>
        <p>העלו תמונה אחת או כמה מיד. בלי לבחור אזור, נכס או מערכת, ובלי לחכות ששאלה קודמת תיסגר.</p>
        <p class="hint">תג או שלט שנקרא בבירור מתויק לפריט ולמכונה שמכילה אותו. אזור שמופיע אחר כך הוא האזור שרשום במלאי, לא מיקום שאומת מהתמונה. אם הזיהוי לא ברור, הקובץ נשמר ומחכה בבדיקה.</p>
        <div class="actions">
          <label class="btn file-btn">מצלמה<input type="file" accept="image/*" capture="environment" data-action="tour-files" multiple></label>
          <label class="btn file-btn">בחירת קבצים<input type="file" data-action="tour-files" multiple accept="image/*,.pdf,.doc,.docx,.txt,.csv,.xls,.xlsx,.rtf,.heic,.tif,.tiff"></label>
        </div>
        ${S.tourBusy ? `<p>מעלה את הקבצים לשרת…</p>` : ""}
        ${pendingHtml}
        ${results}
      </div>`;
  }

  function openCaptureIsClear() {
    const cap = S.captures[S.openCaptureId];
    if (!cap) return false;
    const confirmed = (cap.links || []).filter((link) => link.review_status === "auto_linked" || link.review_status === "user_confirmed");
    if (confirmed.length === 1) return true;
    const groups = new Set(confirmed.map((link) => (S.rowById[link.row_id] || {}).duplicate_group || ""));
    return confirmed.length > 1 && groups.size === 1 && Boolean([...groups][0]);
  }

  async function filesFromList(fileList) {
    return [...fileList].filter((file) => file && typeof file.name === "string");
  }

  async function filesFromDrop(dataTransfer) {
    const items = [...(dataTransfer.items || [])];
    const collected = [];
    async function readAll(reader) {
      const all = [];
      while (true) {
        const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
        if (!batch.length) return all;
        all.push(...batch);
      }
    }
    async function walk(entry) {
      if (!entry) return;
      if (entry.isFile) {
        const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
        if (file) collected.push(file);
      } else if (entry.isDirectory) {
        const entries = await readAll(entry.createReader());
        for (const child of entries) await walk(child);
      }
    }
    if (items.length && items.some((item) => item.webkitGetAsEntry)) {
      for (const item of items) {
        const entry = item.webkitGetAsEntry && item.webkitGetAsEntry();
        if (entry) await walk(entry);
        else if (item.getAsFile && item.getAsFile()) collected.push(item.getAsFile());
      }
    }
    if (collected.length) return collected;
    return filesFromList(dataTransfer.files || []);
  }

  async function flushTourQueue() {
    if (!navigator.onLine || S.tourBusy) return;
    const pending = Object.values(S.photos).filter((photo) => photo.tourPending && photo.blob);
    if (!pending.length) return;
    const files = pending.map((photo) => {
      if (photo.blob.name) return photo.blob;
      return new File([photo.blob], photo.name || "file", { type: photo.blob.type || "" });
    });
    await uploadTourFiles(files, pending.map((photo) => photo.id), { fromQueue: true });
  }

  async function uploadTourFiles(fileList, ids, options) {
    const files = await filesFromList(fileList);
    if (!files.length || S.tourBusy) return;
    const ownIds = ids && ids.length === files.length ? ids : files.map(() => crypto.randomUUID());
    const singleClear = files.length === 1 && openCaptureIsClear();
    if (!navigator.onLine) {
      for (let index = 0; index < files.length; index += 1) {
        const photo = {
          id: ownIds[index],
          tourPending: true,
          name: files[index].name || "file",
          blob: files[index],
          state: "failed",
          created_at: nowIso(),
        };
        const saved = await Store.savePhoto(photo);
        if (saved.ok) {
          photo.state = "failed";
          photo.tourPending = true;
          await Store.put("photos", photo);
          S.photos[photo.id] = photo;
        }
      }
      S.error = "אין חיבור. הקבצים לא נשמרו בשרת.";
      render();
      return;
    }
    S.tourBusy = true;
    S.error = "";
    render();
    try {
      const form = new FormData();
      files.forEach((file, index) => {
        form.append("files", file, file.name || "file");
        form.append("file_ids", ownIds[index]);
      });
      if (singleClear) form.append("capture_id", S.openCaptureId);
      const response = await fetch("/api/tour-files", { method: "POST", body: form, credentials: "include" });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        S.error = body.detail || "הקבצים לא נשמרו בשרת.";
        S.tourResults = files.map((file, index) => ({
          file_id: ownIds[index],
          original_name: file.name,
          saved: false,
          filed: false,
          rows: [],
          area_source: "unknown",
          message: "לא נשמר בשרת.",
        }));
      } else {
        S.tourResults = body.results || [];
        S.toast = "ההעלאה הסתיימה. כל קובץ שנשמר נמצא בשרת.";
        for (const item of S.tourResults) {
          if (item.saved && S.photos[item.file_id] && S.photos[item.file_id].tourPending) {
            delete S.photos[item.file_id];
            await Store.remove("photos", item.file_id);
          }
        }
        await refreshFromServer();
      }
    } catch (error) {
      S.error = "הקבצים לא נשמרו בשרת.";
      S.tourResults = files.map((file, index) => ({
        file_id: ownIds[index],
        original_name: file.name,
        saved: false,
        filed: false,
        rows: [],
        area_source: "unknown",
        message: "לא נשמר בשרת.",
      }));
    } finally {
      S.tourBusy = false;
      if (location.hash && location.hash !== "#/capture" && location.hash !== "#/") {
        location.hash = "#/capture";
      } else {
        S.screen = "capture";
        render();
      }
    }
    if (options && options.fromQueue) return;
  }

  function renderCapture() {
    const cap = S.captures[S.openCaptureId];
    if (!cap) {
      const recent = Object.values(S.captures).sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))).slice(0, 3);
      return `
        ${tourPanel()}
        <div class="card">
          <h2>קליטת ציוד</h2>
          <p class="hint">אפשר לצלם או לגרור קבצים מיד, בלי לבחור אזור. קליטה של פריט אחד עם הערה נשארת למטה, והאזור שלה לא חל על התמונות האלה.</p>
          <button class="btn-primary" type="button" data-action="new-capture">ציוד חדש</button>
        </div>
        ${recent.map(miniCapture).join("")}
        <p class="hint"><a href="#/ready">בדיקת מוכנות לפני הסיור</a> · <a href="#/import">קבצי המלאי</a> · <a href="#/documents">מסמכים נכנסים</a></p>`;
    }
    const photos = photosOf(cap.id);
    const thumbs = photos.map((photo) => {
      const name = photo.name || photo.original_name || "";
      const isDoc = photo.role === "document" || /\.(pdf|docx?|txt|csv|xlsx?|rtf)$/i.test(name);
      const preview = isDoc ? `<div class="ph">${ltr(name || "מסמך")}</div>` : `<img src="${esc(photo.url || `/api/photos/${photo.id}/thumb`)}" alt="צילום">`;
      return `
      <div class="thumb">
        ${preview}
        <select data-action="role" data-photo="${esc(photo.id)}">
          ${["", "overall", "tag", "nameplate", "accessories", "location", "system", "document"].map((role) => `<option value="${role}" ${photo.role === role ? "selected" : ""}>${esc(roleLabel(role))}</option>`).join("")}
        </select>
        <div class="hint">${esc(STATUS_HE[photo.state] || photo.upload_state || "")}</div>
      </div>`;
    }).join("");
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
      ${tourPanel()}
      <div class="card" data-capture="${esc(cap.id)}">
        <h2>קליטה <span class="ltr">${esc(cap.id.slice(0, 8))}</span></h2>
        <p><b>אזור הקליטה הזו:</b> ${ltr(cap.observed_area_name || "לא ידוע")}</p>
        <button class="btn-quiet" type="button" data-action="apply-current-area">עדכן את הקליטה הזו לאזור הנוכחי</button>
        ${statusHtml(cap)}
        <div class="actions">
          <label class="btn file-btn">מצלמה<input type="file" accept="image/*" capture="environment" data-action="tour-files" multiple></label>
          <label class="btn file-btn">קובץ<input type="file" accept="image/*" data-action="tour-files" multiple></label>
          <label class="btn-quiet file-btn">צרף לקליטה הזו<input type="file" accept="image/*" data-action="files" multiple></label>
        </div>
        <p class="hint">המצלמה והקובץ מזוהים מהתג שבצילום, בלי האזור שעל המסך. צירוף לקליטה הזו שומר את התמונה על הפריט שכבר פתוח.</p>
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
    return { "": "בלי תפקיד", overall: "מבט כללי", tag: "תג זיהוי", nameplate: "שלט יצרן", accessories: "אביזר", location: "מיקום כללי", system: "מבט מערכת", document: "מסמך" }[role] || role;
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
          row.prefix_category ? `<span class="chip">${esc(row.prefix_category)}</span>` : "",
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
        <p><a href="#/value/row/${esc(row.id)}">כרטיס שווי של השורה הזו</a> · <a href="#/documents">מסמכים</a> · <a href="#/offers">הצעות</a></p>
        ${rowDocumentsHtml(row)}
        <p class="hint">השווי נשען על אותה שורה ועל אותם צילומים. עובדה שכבר בקובץ לא מוקלדת מחדש.</p>
        ${row.prefix_category ? `<p><b>קטגוריה לפי הקידומת:</b> ${esc(row.prefix_category)}. הקידומת אינה מזהה את המכונה.</p>` : ""}
        ${machineLinksHtml(row)}
        <p><b>אזור ברשימה:</b> ${ltr(row.listed_area || "לא ידוע")}</p>
        <p><b>אזור שנצפה:</b> ${observed.length ? observed.map((area) => ltr(area)).join(", ") : "עדיין לא נצפה בסיור"}</p>
        ${differ ? `<div class="warn">האזור בסיור והאזור ברשימה שונים. שניהם נשמרים.</div>` : ""}
        ${row.system_number ? `<p class="hint">מספר מערכת בקובץ: ${ltr(row.system_number)}. זה לא קשר מערכת מאושר ולא אומר שמוכרים יחד.</p>` : ""}
        ${row.duplicate_group ? `<p class="hint">השורה מסומנת ככפילות משוערת של שורות עם אותו תג ואותם נתונים. השורות לא נמחקו.</p>` : ""}
        <p class="hint">כמות בקובץ: ${row.quantity ? ltr(row.quantity) : "לא צוינה"}.</p>
      </div>
      <div class="card"><h3>צילומים והערות</h3>
        ${photos.map((photo) => `<figure><img src="${esc(photo.url || `/api/photos/${photo.id}/thumb`)}" alt="צילום" style="max-width:100%;border-radius:12px"><figcaption class="hint">${esc(photoSubject(photo, row))} · ${esc((S.captures[photo.captureId || photo.capture_id] || {}).note || "")}</figcaption></figure>`).join("") || `<p class="hint">אין צילום מקושר.</p>`}
      </div>
      <div class="card"><h3>הערות קשר שנאמרו</h3>
        <p class="hint">הערה כזו לא נוצרת מקישור הצילום.</p>
        ${S.relationships.filter((rel) => caps.includes(rel.capture_id)).map((rel) => `<p>${esc(rel.text)} <span class="hint">${esc(rel.person || "")} · ${esc(rel.status)}</span></p>`).join("") || `<p class="hint">אין.</p>`}
      </div>
      <details class="card"><summary>תאים מקוריים</summary><div class="raw">${rawRows}</div></details>`;
  }

  function photoSubject(photo, row) {
    const labels = ((photo.shows || {}).labels || []).map((label) => label.tag).filter(Boolean);
    if (labels.length) return "צילום של " + labels.join(", ");
    if (row && row.tag_original) return "צילום של " + row.tag_original;
    return roleLabel(photo.role || "");
  }

  function machineLinksHtml(row) {
    const found = (S.machines || []).filter((machine) => (machine.members || []).some((member) => member.row_id === row.id && member.link_status !== "removed"));
    if (!found.length) return "";
    return found.map((machine) => {
      const member = machine.members.find((item) => item.row_id === row.id);
      const sale = machine.sold_together === "confirmed" ? "מכירה יחד אושרה" : "מכירה יחד פתוחה";
      return `<p><button type="button" class="btn" data-action="open-machine" data-id="${esc(machine.id)}">${esc(machine.name)}</button> <span class="hint">${member && member.role === "parent" ? "הורה" : "רכיב"} · ${machine.status === "confirmed" ? "אושר" : "הצעה"} · ${sale}. היחידה אינה שורה נוספת במלאי.</span></p>`;
    }).join("");
  }

  function machineStatusText(machine) {
    const state = machine.status === "confirmed" ? "אושר" : machine.status === "dissolved" ? "פורק" : "הצעה";
    const sale = machine.sold_together === "confirmed" ? "מכירה יחד אושרה" : "מכירה יחד פתוחה";
    return `${state} · ${sale}`;
  }

  function machineControls(machine) {
    if (!machine || machine.status === "dissolved") return "";
    const active = (machine.members || []).filter((member) => member.link_status !== "removed");
    return `
      <div class="actions">
        <button class="btn" type="button" data-action="machine-act" data-machine-act="confirm" data-id="${esc(machine.id)}">אשר את הקיבוץ</button>
        <button class="btn-primary" type="button" data-action="machine-act" data-machine-act="confirm_sale" data-id="${esc(machine.id)}">אשר מכירה יחד</button>
        <button class="btn" type="button" data-action="machine-act" data-machine-act="dissolve" data-id="${esc(machine.id)}">פרק בלי למחוק שורות</button>
      </div>
      <label>שם היחידה<input id="machine-name-${esc(machine.id)}" value="${esc(machine.name || "")}"></label>
      <button class="btn" type="button" data-action="machine-act" data-machine-act="correct" data-id="${esc(machine.id)}">שמור שם</button>
      <label>צרף תג קיים<input id="machine-add-${esc(machine.id)}" class="ltr" dir="ltr" placeholder="למשל P-1001"></label>
      <button class="btn" type="button" data-action="machine-act" data-machine-act="combine" data-id="${esc(machine.id)}">צרף</button>
      ${active.length ? `<div class="hint">סמנו רכיב להוצאה. השורה נשארת במלאי.</div>${active.map((member) => `<label class="suggest"><input type="checkbox" data-machine-tag="${esc(member.tag_norm)}" data-machine="${esc(machine.id)}"> ${ltr(member.tag)} · ${esc(member.prefix || "")}</label>`).join("")}<button class="btn" type="button" data-action="machine-act" data-machine-act="split" data-id="${esc(machine.id)}">הוצא את המסומנים</button>` : ""}`;
  }

  function renderMachines() {
    const q = S.machineQuery.trim().toLowerCase();
    let machines = S.machines || [];
    if (S.machineFilter === "active") machines = machines.filter((machine) => machine.status !== "dissolved");
    if (S.machineFilter === "confirmed") machines = machines.filter((machine) => machine.status === "confirmed");
    if (S.machineFilter === "proposed") machines = machines.filter((machine) => machine.status === "proposed");
    if (S.machineFilter === "dissolved") machines = machines.filter((machine) => machine.status === "dissolved");
    if (q) {
      machines = machines.filter((machine) => [machine.name, machine.note, ...(machine.members || []).map((member) => `${member.tag} ${member.description || ""}`)].join(" ").toLowerCase().includes(q));
    }
    const filters = [["active", "פעילות"], ["confirmed", "אושרו"], ["proposed", "הצעות"], ["dissolved", "פורקו"]];
    return `
      <div class="card">
        <h2>מכונות ורכיבים</h2>
        <p class="hint">קידומת היא קטגוריה בלבד. מספר משותף לבדו לא יוצר מכונה. R-4208 ו-F-4208 אושרו על ידי המשתמש כיחידה אחת שנמכרת יחד. שאר הקיבוצים הם הצעה כשהתיאור מזכיר תג אחר. שורת אקסל לא נמחקת, והיחידה לא נספרת שוב כשווי או כהתחייבות מכירה.</p>
      </div>
      <input type="search" placeholder="חיפוש שם או תג" value="${esc(S.machineQuery)}" data-action="machine-search">
      <div class="filters">
        ${filters.map(([id, label]) => `<button type="button" data-action="machine-filter" data-filter="${id}" class="${S.machineFilter === id ? "on" : ""}">${label}</button>`).join("")}
      </div>
      <p class="hint">${machines.length} יחידות לפי הסינון.</p>
      ${machines.slice(0, 80).map((machine) => {
        const open = S.openMachine === machine.id ? "open" : "";
        const members = machine.members || [];
        return `<details class="card" ${open}>
          <summary><b>${esc(machine.name)}</b><div class="hint">${esc(machineStatusText(machine))} · ${ltr(machine.id)}</div></summary>
          <p class="hint">${esc(machine.note || "")}</p>
          ${members.map((member) => `
            <div class="machine-member ${member.link_status === "removed" ? "removed" : ""}">
              <b>${member.role === "parent" ? "הורה" : "רכיב"}: ${ltr(member.tag)}</b>
              ${member.prefix ? `<span class="chip">${esc(member.prefix)}</span>` : ""}
              ${member.link_status === "removed" ? `<span class="chip">הוצא מהיחידה</span>` : ""}
              <div class="hint">${member.in_inventory ? `${esc(member.sheet_name)} שורה ${esc(member.original_row)} · ${esc(member.description || "")}` : "אין שורת אקסל לתג הזה. לא הומצאה שורה."}</div>
              <div class="hint">${esc(member.evidence || "")}</div>
              ${(member.photos || []).map((photo) => `<figure><img src="/api/photos/${esc(photo.id)}/thumb" alt="" style="max-width:100%;border-radius:12px"><figcaption class="hint">צילום של ${ltr(photo.of_tag)}. זה לא צילום של רכיב אחר ביחידה.</figcaption></figure>`).join("")}
              ${member.row_id ? `<p><a href="#/row/${esc(member.row_id)}">שורת המלאי</a></p>` : ""}
            </div>`).join("")}
          ${machineControls(machine)}
        </details>`;
      }).join("")}
      ${machines.length > 80 ? `<p class="hint">מוצגות 80 יחידות. צמצמו בחיפוש.</p>` : ""}`;
  }

  function renderReview() {
    const items = S.reviews.filter((review) => S.reviewFilter === "all" ? true : review.queue === S.reviewFilter);
    const open = S.activeReview ? S.reviews.find((review) => review.id === S.activeReview) : null;
    if (open) return renderReviewDetail(open);
    return `
      <div class="filters">
        <button type="button" data-action="rev-filter" data-filter="visit" class="${S.reviewFilter === "visit" ? "on" : ""}">מהסיור</button>
        <button type="button" data-action="rev-filter" data-filter="import" class="${S.reviewFilter === "import" ? "on" : ""}">מהקובץ</button>
        <button type="button" data-action="rev-filter" data-filter="valuation" class="${S.reviewFilter === "valuation" ? "on" : ""}">שווי</button>
        <button type="button" data-action="rev-filter" data-filter="machine" class="${S.reviewFilter === "machine" ? "on" : ""}">מכונות</button>
        <button type="button" data-action="rev-filter" data-filter="intake" class="${S.reviewFilter === "intake" ? "on" : ""}">מסמכים</button>
      </div>
      <p class="hint">כאן שאלות על תג לא קריא, התאמה לא ברורה, קיבוץ לא ודאי, תיאור סותר, או גבול של יחידת מכירה. העלאה נוספת לא מחכה שהשאלה תיסגר.</p>
      ${items.filter((review) => review.status !== "resolved").map((review) => `
        <button class="card" type="button" data-action="open-review" data-id="${esc(review.id)}" style="width:100%;text-align:right">
          <b>${esc(review.question)}</b>
          <div class="hint">${esc(review.kind)} · ${esc(review.status)} · ${esc(review.priority)}</div>
        </button>`).join("") || `<p class="hint">אין שאלות פתוחות בקבוצה הזו.</p>`}`;
  }

  function reviewPhotoList(review) {
    const ids = new Set();
    const list = [];
    photosOf(review.capture_id || "").forEach((photo) => {
      if (ids.has(photo.id)) return;
      ids.add(photo.id);
      list.push(photo);
    });
    ((review.payload || {}).photo_ids || []).forEach((id) => {
      if (ids.has(id)) return;
      ids.add(id);
      list.push(S.photos[id] || { id });
    });
    return list;
  }

  function renderReviewDetail(review) {
    const cap = review.capture_id ? S.captures[review.capture_id] : null;
    const photos = reviewPhotoList(review);
    const rowIds = review.row_ids || [];
    const rows = rowIds.map((id) => S.rowById[id]).filter(Boolean);
    const payload = review.payload || {};
    const members = payload.members || [];
    const machine = payload.machine_id ? (S.machines || []).find((item) => item.id === payload.machine_id) : null;
    const valueLink = review.queue === "valuation" && rowIds[0] ? `<p><a href="#/value/row/${esc(rowIds[0])}">פתחו את כרטיס השווי כדי לענות. תשובה כאן לא מייצרת מחיר.</a></p>` : "";
    return `
      <button class="btn-quiet" type="button" data-action="close-review">חזרה</button>
      <div class="split">
        <div class="card">
          <h2>השאלה</h2>
          <p>${esc(review.question)}</p>
          ${payload.resolve_by ? `<p class="hint">מה סוגר את השאלה: ${esc(payload.resolve_by)}</p>` : ""}
          ${valueLink}
          ${review.missing_reason ? `<div class="warn">${esc(review.missing_reason)}</div>` : ""}
          ${review.resolution ? `<p class="hint">תשובה שנשמרה: ${esc(review.resolution)}</p>` : ""}
          ${cap ? `<p class="hint">אזור שנצפה: ${ltr(cap.observed_area_name || "לא ידוע")} · תג שהוקלד: ${ltr(cap.tag_text || "")}</p><p>${esc(cap.note || "")}</p>` : ""}
          ${photos.map((photo) => `<figure><img src="${esc(photo.url || `/api/photos/${photo.id}/thumb`)}" alt="צילום" style="width:100%;border-radius:12px"><figcaption class="hint">${esc(photoSubject(photo))}</figcaption></figure>`).join("") || `<p class="hint">אין צילום לשאלה הזו. אפשר להעלות אחד בלי לסגור את השאלה.</p>`}
          <label class="btn file-btn">הוסף צילום או קובץ<input type="file" accept="image/*,.pdf,.txt,.docx" data-action="review-photo" data-id="${esc(review.id)}"></label>
        </div>
        <div class="card">
          <h2>שורות אקסל</h2>
          ${members.map((member) => `<div class="suggest"><b>${member.role === "parent" ? "הורה" : "רכיב"}: ${ltr(member.tag)}</b><div class="hint">${esc(member.evidence || "")}</div>${member.row_id && S.rowById[member.row_id] ? `<div class="hint">${esc(S.rowById[member.row_id].sheet_name)} שורה ${esc(S.rowById[member.row_id].original_row)} · ${esc(S.rowById[member.row_id].description || "")}</div>` : ""}</div>`).join("")}
          ${rows.map((row) => `<label class="suggest"><input type="checkbox" data-review-row="${esc(row.id)}"> ${ltr(row.tag_original)} ${esc(row.description)}<div class="hint">${esc(row.sheet_name)} שורה ${esc(row.original_row)} · אזור שרשום במלאי ${ltr(row.listed_area || "לא ידוע")}</div></label>`).join("") || (members.length ? "" : `<p class="hint">אין שורת מועמדת. אפשר להשאיר בלי התאמה.</p>`)}
          ${(payload.suggestions || []).filter((item) => !rows.some((row) => row.id === item.row_id)).map((item) => `<div class="hint">${ltr(item.tag || "")} · ${esc(item.why || "")}</div>`).join("")}
          ${machine ? `<h3>היחידה</h3><p>${esc(machine.name)} · ${esc(machineStatusText(machine))}</p>${machineControls(machine)}` : ""}
          <textarea id="review-text" placeholder="תשובה כתובה, אם צריך"></textarea>
          <div class="actions">
            <button class="btn-primary" type="button" data-action="review-link" data-id="${esc(review.id)}">קשר</button>
            <button class="btn" type="button" data-action="review-separate" data-id="${esc(review.id)}">השאר נפרד</button>
            <button class="btn" type="button" data-action="review-resolve" data-id="${esc(review.id)}">שמור תשובה</button>
            <button class="btn" type="button" data-action="review-defer" data-id="${esc(review.id)}">דחה</button>
          </div>
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

  const VAL_STATUS = { supported: "נתמך במקור", missing: "חסר", conflict: "יש סתירה", unable: "לא ניתן להשיג", not_applicable: "לא רלוונטי" };
  const VAL_IMPACT = { blocking: "חוסם", material: "משנה את השווי", helpful: "מועיל", not_applicable: "לא רלוונטי" };
  const PRICE_TYPE = { asking: "מחיר מבוקש", transaction: "עסקה", hammer: "מחיר פטיש", buyer_total: "סה״כ לקונה", replacement: "מחיר תחליף", buyer_offer: "הצעת קונה", specialist_opinion: "חוות דעת" };

  let valueLoadSeq = 0;
  async function loadValue() {
    const seq = ++valueLoadSeq;
    const params = new URLSearchParams({ q: S.valueQuery || "", family: S.valueFamily || "", info: S.valueInfo || "" });
    try {
      const response = await fetch("/api/valuation/overview?" + params.toString(), { credentials: "include" });
      if (seq !== valueLoadSeq) return;
      if (!response.ok) {
        S.error = "מוכנות השווי לא נטענה.";
      } else {
        S.error = "";
        S.valueOverview = await response.json();
      }
    } catch (error) {
      if (seq !== valueLoadSeq) return;
      S.error = "מוכנות השווי לא נטענה. בדקו את החיבור.";
    }
    if (seq !== valueLoadSeq) return;
    const typed = document.activeElement && document.activeElement.name === "q" ? document.activeElement.value : null;
    render();
    if (typed != null) {
      const again = document.querySelector("input[name=q]");
      if (again) {
        again.value = typed;
        again.focus();
      }
    }
  }
  async function loadValueDetail(rowId) {
    if (!rowId) return;
    const response = await fetch("/api/valuation/subjects", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ row_id: rowId }),
    });
    if (!response.ok) {
      S.error = "כרטיס השווי לא נפתח.";
      render();
      return;
    }
    S.valueDetail = await response.json();
    render();
  }
  async function loadComparables() {
    const subject = S.valueDetail && S.valueDetail.id ? S.valueDetail.id : "";
    const response = await fetch("/api/valuation/comparables?subject_id=" + encodeURIComponent(subject), { credentials: "include" });
    if (response.ok) S.comparables = await response.json();
    render();
  }
  async function loadVisitPrep() {
    const response = await fetch("/api/valuation/visit-prep", { credentials: "include" });
    if (response.ok) S.visitPrep = await response.json();
    render();
  }

  function renderValue() {
    const data = S.valueOverview;
    if (!data) return `<div class="card"><p>טוען את מוכנות השווי…</p></div>`;
    const families = ["", "generator", "reactor", "lab_instrument", "agitator", "pump", "heat_exchanger", "cooling_tower", "building", "unknown", "other_named"];
    const familyNames = { "": "כל המשפחות", generator: "גנרטור", reactor: "כור", lab_instrument: "מעבדה", agitator: "מערבל", pump: "משאבה", heat_exchanger: "מחליף חום", cooling_tower: "מגדל קירור", building: "מבנה יביל", unknown: "לא זוהה", other_named: "לפי התיאור" };
    return `
      <div class="card">
        <h2>מוכנות לשווי</h2>
        <p class="hint">${esc(data.research_limitation)}</p>
        <p class="hint">${esc(data.project_target_note)}</p>
        <p class="hint">${esc(data.appraisal_label)} אין כאן אחוז השלמה.</p>
        <p><a href="#/visit-prep">רשימה לסיור</a> · <a href="#/comparables">ראיות שוק</a></p>
      </div>
      <form data-action="value-filter" class="card">
        <label>חיפוש<input name="q" value="${esc(S.valueQuery)}" placeholder="תג או תיאור"></label>
        <label>משפחה<select name="family">${families.map((id) => `<option value="${id}" ${S.valueFamily === id ? "selected" : ""}>${familyNames[id]}</option>`).join("")}</select></label>
        <label>מידע<select name="info">
          <option value="">כל מצבי המידע</option>
          <option value="identification_unresolved" ${S.valueInfo === "identification_unresolved" ? "selected" : ""}>הזיהוי לא הוכרע</option>
          <option value="critical_missing" ${S.valueInfo === "critical_missing" ? "selected" : ""}>חסר מידע חוסם</option>
          <option value="provisional" ${S.valueInfo === "provisional" ? "selected" : ""}>ניתוח ראשוני</option>
          <option value="sufficient" ${S.valueInfo === "sufficient" ? "selected" : ""}>מספיק להערכה</option>
        </select></label>
        <button class="btn" type="submit">סנן</button>
      </form>
      <p class="hint">${data.total} שורות לפי הסינון. זו רשימת המלאי הקיימת, לא מלאי חדש.</p>
      ${(data.items || []).map((item) => `
        <button class="list-item" type="button" data-go-value="${esc(item.row_id)}">
          <div class="ph"></div>
          <div><b>${ltr(item.tag || "בלי תג")}</b> ${esc(item.description || "")}
            <div class="hint">${esc(item.sheet_name)} · שורה ${esc(item.original_row)} · ${esc(item.family_label)}</div>
            <div class="chips"><span class="chip">${esc(item.info_label)}</span><span class="chip">${esc(item.market_label)}</span></div>
            <div class="hint">הצעד הבא: ${esc(item.next_action)}</div>
            ${item.latest_range ? `<div class="hint">טווח אחרון: ${ltr(item.latest_range)} · ${esc(item.latest_date || "")}</div>` : ""}
          </div>
        </button>`).join("")}`;
  }

  function renderValueDetail() {
    const subject = S.valueDetail;
    if (!subject) return `<div class="card"><p>טוען את כרטיס השווי…</p></div>`;
    const card = subject.card;
    const facts = (subject.members || []).flatMap((member) => member.facts || []);
    const photos = (subject.members || []).flatMap((member) => member.photos || []);
    return `
      <button class="btn-quiet" type="button" data-go="value">חזרה למוכנות</button>
      <div class="warn">${esc(subject.research_limitation)}</div>
      <p class="hint">${esc(subject.project_target_note)}</p>
      <div class="card">
        <h2>מה מוערך</h2>
        <p><b>${ltr(subject.title)}</b></p>
        <p>משפחה: ${esc(subject.family_label)} ${subject.subtype ? "· " + esc(subject.subtype) : ""}</p>
        <p class="hint">${esc(subject.family_evidence)}</p>
        <p>כמות מהקובץ: ${subject.quantity_text ? ltr(subject.quantity_text) : "לא צוינה"}</p>
        <p><b>כלול:</b> ${esc(subject.included_text)}</p>
        <p><b>לא כלול:</b> ${esc(subject.excluded_text)}</p>
        <p class="hint">${esc(subject.double_count_note)}</p>
        ${(subject.members || []).map((member) => `<p class="hint">${ltr(member.tag)} · ${esc(member.description)} · ${esc(member.sheet_name)} שורה ${esc(member.original_row)} · ${esc(member.source_file)}</p>`).join("")}
        <form data-action="val-family">
          <label>תיקון סיווג
            <select name="family">
              ${Object.entries(subject.labels.family).map(([id, label]) => `<option value="${id}" ${subject.family === id ? "selected" : ""}>${esc(label)}</option>`).join("")}
            </select>
          </label>
          <button class="btn" type="submit">עדכן סיווג</button>
        </form>
      </div>
      <div class="card">
        <h2>כרטיס שווי</h2>
        <p class="hint">${esc(card.appraisal_label)}</p>
        <p><b>טווח:</b> ${ltr(card.range_label)}</p>
        <p>מצב: ${esc(card.status === "unresolved" ? "לא הוכרע" : card.status === "provisional" ? "ראשוני" : card.status === "supported" ? "נתמך" : "נבדק על ידי מומחה")}</p>
        <p>מידע: ${esc(card.info_label)} · שוק: ${esc(card.market_label)}</p>
        ${card.confidence ? `<p>ביטחון: ${esc(card.confidence)} · ${esc(card.confidence_why)}</p>` : ""}
        <p><b>שיטה:</b> ${esc(card.method)}</p>
        <p class="hint">${esc(card.method_why)}</p>
        <p class="hint">${esc(card.assumptions)}</p>
        ${(card.sensitivities || []).slice(0, 8).map((line) => `<p class="hint">· ${esc(line)}</p>`).join("")}
        <p class="hint">מצב: ${esc(card.basis.condition_status)} · הנחה: ${esc(card.basis.premise)} · שוק: ${esc(card.basis.market)} · מטבע: ${ltr(card.basis.currency)}</p>
        <p class="hint">${esc(card.basis.price_boundary)}</p>
        <p class="hint">${esc(card.basis.marketing_period)} · יעד ${ltr(card.basis.project_target)}</p>
        <button class="btn" type="button" data-action="val-time">תרחיש נפרד ליעד 31.12.2026</button>
      </div>
      <div class="card facts">
        <h2>כבר יש במקורות</h2>
        <p class="hint">לא צריך להקליד שוב עובדה שמופיעה כאן.</p>
        ${facts.map((fact) => `<div><b>${esc(fact.label)}:</b> ${ltr(fact.value)}<div class="hint">${esc(fact.source)}${fact.not_used ? " · " + esc(fact.not_used) : ""}</div></div>`).join("") || `<p class="hint">אין שדות מלאים.</p>`}
        ${photos.map((photo) => `<img src="/api/photos/${esc(photo.id)}/thumb" alt="צילום מהסיור" style="width:96px;height:96px;object-fit:cover;border-radius:12px;margin:4px">`).join("")}
      </div>
      <form class="card" data-action="val-basis">
        <h2>בסיס</h2>
        <label>הנחת מכירה<select name="premise">
          ${[["unset", "לא נבחרה"], ["continued_use", "המשך שימוש באתר"], ["relocation", "פירוק לשימוש חוזר"], ["parts", "חלקים"], ["scrap", "גרוטאות"]].map(([id, label]) => `<option value="${id}" ${subject.premise === id ? "selected" : ""}>${label}</option>`).join("")}
        </select></label>
        <label>מצב<select name="condition_status">
          ${[["unknown", "לא ידוע"], ["reported", "דווח"], ["observed", "נצפה"], ["tested", "נבדק"]].map(([id, label]) => `<option value="${id}" ${subject.condition_status === id ? "selected" : ""}>${label}</option>`).join("")}
        </select></label>
        <label>מה ידוע על המצב<textarea name="condition_text">${esc(subject.condition_text || "")}</textarea></label>
        <label>שוק גיאוגרפי<input name="market_text" value="${esc(subject.market_text || "")}" placeholder="רק אם ידוע. לא לנחש"></label>
        <label>כלול<input name="included_text" value="${esc(subject.included_text || "")}"></label>
        <label>לא כלול<input name="excluded_text" value="${esc(subject.excluded_text || "")}"></label>
        <label>תזכורת בדיקה (רשות)<input name="review_due" value="${esc(subject.review_due || "")}" placeholder="2026-12-31"></label>
        <button class="btn-primary" type="submit">שמור בסיס</button>
      </form>
      <div class="card check">
        <h2>מה עוד צריך</h2>
        ${(subject.requirements || []).map((item) => `
          <div>
            <b>${esc(item.question)}</b>
            <div class="chips"><span class="chip">${esc(VAL_IMPACT[item.impact] || item.impact)}</span><span class="chip ${item.evidence_status === "supported" ? "ok" : "warn"}">${esc(VAL_STATUS[item.evidence_status] || item.evidence_status)}</span></div>
            <p class="hint">${esc(item.why)}</p>
            <p class="hint">איך: ${esc(item.how_to)} · תפקיד: ${esc(item.contact_role)} · ${item.site_presence === "site" ? "באתר" : item.site_presence === "remote" ? "מרחוק" : "באתר או מרחוק"}</p>
            ${(item.evidence || []).map((bit) => `<p class="hint">מקור: ${ltr(bit.value)} · ${esc(bit.source)}</p>`).join("")}
            ${item.answer_text ? `<p>${esc(item.answer_text)}</p>` : ""}
            ${item.consequence ? `<p class="warn">${esc(item.consequence)}</p>` : ""}
            ${item.evidence_status === "missing" || item.evidence_status === "conflict" ? `
              <form data-action="val-answer" data-id="${esc(item.id)}">
                <textarea name="text" placeholder="תשובה, או למה אי אפשר להשיג"></textarea>
                <select name="status"><option value="supported">יש תשובה</option><option value="unable">לא ניתן להשיג</option><option value="conflict">סתירה</option><option value="not_applicable">לא רלוונטי</option></select>
                <button class="btn" type="submit">שמור תשובה</button>
              </form>
              <label class="btn file-btn">צרף קובץ<input type="file" accept="image/*,.pdf" data-action="val-photo" data-id="${esc(item.id)}"></label>` : ""}
          </div>`).join("")}
        <form data-action="val-extra">
          <h3>דרישה שעולה מהמחקר</h3>
          <input name="question" placeholder="השאלה">
          <input name="why" placeholder="למה זה משנה את השווי">
          <input name="how" placeholder="איך עונים">
          <button class="btn" type="submit">הוסף דרישה</button>
        </form>
      </div>
      <form class="card" data-action="val-evidence">
        <h2>ראיית שוק</h2>
        <p class="hint">בלי מקור אין ראיה. הצעת קונה ומחיר יעד לא נכנסים לכאן.</p>
        <label>כותרת<input name="title" required></label>
        <label>כתובת או מסמך<input name="source_url" class="ltr" dir="ltr" placeholder="https://"></label>
        <label>סוג מחיר<select name="price_type">
          ${Object.entries(PRICE_TYPE).map(([id, label]) => `<option value="${id}">${label}</option>`).join("")}
        </select></label>
        <label>סכום<input name="price_amount" class="ltr" dir="ltr" inputmode="decimal"></label>
        <label>מטבע<input name="currency" class="ltr" dir="ltr" placeholder="USD"></label>
        <label>מס<select name="tax_treatment"><option value="unknown">לא צוין</option><option value="excluded_vat">ללא מע״מ</option><option value="included_vat">כולל מע״מ</option></select></label>
        <label>מה כלול במחיר<select name="included_services"><option value="unknown">לא ידוע</option><option value="asset_only">נכס בלבד</option><option value="bundled">כולל הובלה או התקנה</option></select></label>
        <label>הבדלים<input name="differences" placeholder="דגם, מצב, מיקום"></label>
        <button class="btn-primary" type="submit">הוסף ראיה</button>
        ${(subject.evidence || []).map((item) => `
          <div class="hint">${ltr(item.title)} · ${esc(PRICE_TYPE[item.price_type] || item.price_type)} · ${item.price_amount != null ? ltr(item.price_amount + " " + (item.currency || "")) : "בלי סכום"} · ${item.role === "direct" ? "ישירה" : item.role === "excluded" ? "הוצאה" : "הקשר"}
          ${item.duplicate_of ? " · כפילות, לא נספרת" : ""} ${item.limitations ? " · " + esc(item.limitations) : ""}</div>`).join("")}
      </form>
      <div class="card">
        <h2>גרסאות</h2>
        ${(subject.versions || []).map((item) => `<p>${item.scenario === "time_constrained" ? "תרחיש זמן" : "שיווק רגיל"} · ${ltr(item.range_label)} · ${esc(item.created_at)} ${item.outdated ? "· דורש בדיקה מחדש: " + esc(item.outdated_reason || "") : ""}</p>`).join("")}
      </div>
      <form class="card commercial" data-action="commercial">
        <h2>מחוץ להערכת השווי</h2>
        <p class="hint">הצעת קנייה ומחיר יעד נשמרים כאן בלבד. הם לא נכנסים לטווח ולא לכרטיס.</p>
        <label>הצעת קנייה<input name="offer_text" autocomplete="off"></label>
        <label>מחיר יעד<input name="target_text" autocomplete="off"></label>
        <button class="btn" type="submit">שמור בנפרד</button>
      </form>`;
  }

  function renderComparables() {
    const data = S.comparables;
    if (!data) return `<div class="card"><p>טוען ראיות…</p></div>`;
    return `
      <div class="warn">${esc(data.research_limitation)}</div>
      ${(data.items || []).map((item) => `
        <div class="card">
          <b>${ltr(item.title)}</b>
          <p>${esc(PRICE_TYPE[item.price_type] || item.price_type)} · ${item.price_amount != null ? ltr(String(item.price_amount) + " " + (item.currency || "")) : "בלי סכום"}</p>
          <p class="hint">${item.counts_as_observation ? "נספרת כתצפית" : "לא נספרת כתצפית נוספת"} · ${esc(item.included_services || "")} · ${esc(item.tax_treatment || "")}</p>
          <p class="hint">${ltr(item.source_url || item.source_document || "")}</p>
          ${item.differences ? `<p>${esc(item.differences)}</p>` : ""}
          ${item.limitations ? `<p class="hint">${esc(item.limitations)}</p>` : ""}
          ${item.exclusion_reason ? `<p class="warn">${esc(item.exclusion_reason)}</p>` : ""}
          ${item.role !== "excluded" ? `<button class="btn" type="button" data-action="val-exclude" data-id="${esc(item.id)}">הוצא מההשוואה</button>` : ""}
        </div>`).join("") || `<p class="hint">עדיין אין ראיות. לא נוצר טווח.</p>`}`;
  }

  function renderVisitPrep() {
    const data = S.visitPrep;
    if (!data) return `<div class="card"><p>טוען את רשימת הסיור…</p></div>`;
    return `
      <div class="card"><h2>מה לאסוף בסיור</h2><p class="hint">${esc(data.note)}</p></div>
      ${(data.groups || []).map((group) => `
        <div class="card"><h3>${esc(group.label)}</h3>
          ${group.tasks.map((task) => `<p><b>${esc(task.question)}</b><br><span class="hint">${esc(task.why)} · ${esc(task.how)} · ${esc(task.where)}</span></p>`).join("")}
        </div>`).join("") || `<p class="hint">עוד אין שאלות. פותחים כרטיס שווי, והחסר המהותי מופיע כאן.</p>`}`;
  }

  function rowDocumentsHtml(row) {
    const docs = (S.documents || []).filter((doc) => (doc.links || []).some((link) => link.inventory_row_id === row.id && link.link_status === "confirmed"));
    const offers = (S.offers || []).filter((offer) => (offer.assets || []).some((asset) => asset.inventory_row_id === row.id && asset.role === "included"));
    if (!docs.length && !offers.length) return "";
    return `<div class="card"><h3>מסמכים והצעות לפריט הזה</h3>
      ${docs.map((doc) => `<p><button type="button" class="btn" data-action="open-document" data-id="${esc(doc.id)}">${esc(doc.original_name)}</button> <span class="hint">${esc(doc.type_label)}</span></p>`).join("")}
      ${offers.map((offer) => `<p><button type="button" class="btn" data-action="open-offer" data-id="${esc(offer.id)}">${esc(offer.buyer || "הצעה")} · גרסה ${esc(offer.version_no)}</button></p>`).join("")}
      <p class="hint">עובדה שנקראה על הרכיב הזה לא הועתקה לשאר הרכיבים במכונה.</p></div>`;
  }

  const DOC_FILTERS = [
    ["", "הכל"],
    ["filed", "עובד ושויך"],
    ["awaiting_match", "ממתין לשיוך"],
    ["awaiting_clarification", "ממתין להבהרה"],
    ["offer_awaiting_valuation", "הצעה ממתינה לשווי"],
    ["offer_ready", "הצעה לבדיקה"],
    ["failed", "נכשל"],
  ];
  const DOC_TYPES = [
    ["equipment", "מידע על ציוד"],
    ["offer", "הצעת רכש"],
    ["service_quote", "הצעת שירות"],
    ["appraisal", "ראיית שווי"],
    ["unclear", "לא ברור"],
  ];

  function renderDocuments() {
    if (S.activeDocument) {
      const doc = (S.documents || []).find((item) => item.id === S.activeDocument);
      if (doc) return renderDocumentDetail(doc);
    }
    const docs = (S.documents || []).filter((doc) => !S.docFilter || doc.status === S.docFilter);
    const results = (S.intakeResults || []).map((item) => `
      <div class="tour-file" data-intake-status="${esc(item.status || "")}" data-saved="${item.saved ? "yes" : "no"}">
        <b>${ltr((item.document && item.document.original_name) || "מסמך")}</b>
        <div class="${item.saved ? "ok" : "error"}">${item.duplicate ? "כבר נקלט, בלי כפילות" : item.saved ? "נקלט" : "לא נשמר"}</div>
        <div>${esc(item.message || "")}</div>
      </div>`).join("");
    return `
      <div class="card">
        <h2>מסמכים נכנסים</h2>
        <p>העלו PDF, גיליון, וורד, תמונה או צילום מסך. בלי לסווג ובלי לבחור ציוד קודם. קליטת הצילומים בסיור נשארת במסך הקליטה.</p>
        <label class="btn file-btn">העלאת מסמכים<input type="file" data-action="intake-files" multiple accept=".pdf,.doc,.docx,.xls,.xlsx,.csv,.txt,.rtf,image/*"></label>
        <p><button type="button" class="btn" data-go="offers">הצעות רכש</button></p>
        ${results}
      </div>
      <div class="filters">
        ${DOC_FILTERS.map(([id, label]) => `<button type="button" data-action="doc-filter" data-filter="${id}" class="${S.docFilter === id ? "on" : ""}">${label}</button>`).join("")}
      </div>
      ${docs.map((doc) => `<button class="card" type="button" data-action="open-document" data-id="${esc(doc.id)}" style="width:100%;text-align:right">
        <b>${ltr(doc.original_name)}</b>
        <div class="hint">${esc(doc.type_label)} · ${esc(doc.status_label)}</div>
        <div>${esc((doc.summary && doc.summary.text) || "")}</div>
      </button>`).join("") || `<p class="hint">עדיין אין מסמכים במסנן הזה.</p>`}`;
  }

  function renderDocumentDetail(doc) {
    const summary = doc.summary || {};
    const links = doc.links || [];
    return `
      <button class="btn-quiet" type="button" data-action="close-document">חזרה</button>
      <div class="card">
        <h2>${ltr(doc.original_name)}</h2>
        <p>${esc(summary.what || doc.type_label)} · ${esc(doc.status_label)}</p>
        <p>${esc(summary.text || "")}</p>
        ${summary.issuer || summary.date ? `<p>מוציא: ${esc(summary.issuer || "לא צוין")}. תאריך: ${ltr(summary.date || "לא צוין")}.</p>` : ""}
        ${summary.unprocessed ? `<div class="warn">${esc(summary.unprocessed)}</div>` : ""}
        ${(summary.sources || []).map((source) => `<p class="hint">${esc(source.ref)}: ${esc(source.note || "")}</p>`).join("")}
        <p><a href="/api/intake/${esc(doc.id)}/file">הקובץ המקורי</a></p>
      </div>
      <div class="card">
        <h3>שיוך</h3>
        <p class="hint">עובדה נשמרת רק על הפריט שזוהה. היא לא מועתקת לכל רכיב במכונה.</p>
        ${links.map((link) => `<p>${ltr(link.tag_norm)} · ${esc(link.link_status)} · ${esc(link.reason || "")}
          ${link.link_status === "confirmed" ? `<button type="button" class="btn" data-action="intake-unlink" data-id="${esc(doc.id)}" data-row="${esc(link.inventory_row_id || "")}">הסר שיוך</button>` : `<button type="button" class="btn" data-action="intake-link" data-id="${esc(doc.id)}" data-row="${esc(link.inventory_row_id || "")}">שייך</button>`}
        </p>`).join("") || `<p class="hint">אין שיוך.</p>`}
        ${(doc.facts || []).map((fact) => `<p class="hint">${esc(fact.source_ref)} · ${esc(fact.fact_key)}: ${esc(fact.fact_text)}${fact.conflict_with ? " · נשמרה סתירה" : ""}</p>`).join("")}
      </div>
      <div class="card">
        <h3>תיקון סוג</h3>
        <div class="actions">
          ${DOC_TYPES.map(([id, label]) => `<button type="button" class="btn${doc.doc_type === id ? " btn-primary" : ""}" data-action="intake-type" data-id="${esc(doc.id)}" data-type="${id}">${label}</button>`).join("")}
        </div>
      </div>`;
  }

  function renderOffers() {
    const offers = S.offers || [];
    if (!offers.length) return `<div class="card"><h2>הצעות רכש</h2><p class="hint">עדיין אין הצעות. מעלים אותן במסמכים הנכנסים.</p><button class="btn" type="button" data-go="documents">אל המסמכים</button></div>`;
    return `
      <button class="btn-quiet" type="button" data-go="documents">אל המסמכים</button>
      <div class="card"><h2>הצעות רכש</h2><p class="hint">הצעה היא תמורה אפשרית. היא לא קובעת את השווי, ובתוך הטווח אינו המלצה לקבל. גרסה חדשה לא מוחקת את הקודמת.</p></div>
      ${offers.map((offer) => {
        const assessment = offer.assessment || {};
        const recommendation = assessment.recommendation || {};
        const range = assessment.range || {};
        return `<article class="card" id="offer-${esc(offer.id)}">
          <h3>${esc(offer.buyer || "קונה לא צוין")} · גרסה ${esc(offer.version_no)}</h3>
          <p>${offer.amount != null ? ltr(offer.amount + " " + (offer.currency || "")) : "סכום לא נקבע"} · ${esc(offer.price_basis)} · מע״מ: ${esc(offer.vat)}</p>
          <p class="hint">תאריך ${ltr(offer.offer_date || "לא צוין")} · תוקף ${ltr(offer.expiry || "לא צוין")}</p>
          <p>פריטים: ${(offer.assets || []).map((asset) => `${ltr(asset.tag_original)} (${esc(asset.role)})`).join(", ") || "לא זוהו"}</p>
          ${offer.exclusions ? `<p>החרגות: ${esc(offer.exclusions)}</p>` : ""}
          <p>תשלום: ${esc(offer.payment_terms || "לא צוין")}. בדיקה: ${esc(offer.inspection || "לא צוינה")}. תנאים: ${esc(offer.contingencies || "לא צוינו")}.</p>
          <p class="hint">פירוק: ${esc(offer.dismantling || "לא צוין")} · העמסה: ${esc(offer.loading || "לא צוין")} · הובלה: ${esc(offer.transport || "לא צוין")} · פינוי: ${ltr(offer.removal_date || "לא צוין")}</p>
          ${(offer.missing || []).length ? `<p class="hint">חסר: ${esc(offer.missing.join(", "))}</p>` : ""}
          <p>${esc(assessment.conclusion || "")}</p>
          ${range.range_label ? `<p class="hint">טווח: ${ltr(range.range_label)} · ${ltr((range.created_at || "").slice(0, 10))} · ביטחון: ${esc(range.confidence || "")}</p>` : ""}
          <p class="hint">${esc(assessment.confidence_why || "")}</p>
          ${(assessment.differences || []).map((item) => `<p class="hint">${esc(item)}</p>`).join("")}
          ${(assessment.market_evidence || []).map((item) => `<p class="hint">ראיית שוק: ${esc(item.title)} · ${ltr((item.amount != null ? item.amount : "") + " " + (item.currency || ""))} · ${ltr(item.date || "")}</p>`).join("")}
          ${assessment.net_proceeds != null ? `<p><b>תמורה נטו משוערת:</b> ${ltr(assessment.net_proceeds + " " + (offer.currency || ""))}</p>` : ""}
          <p>${esc(assessment.net_note || "")}</p>
          <p><b>הצעד המומלץ:</b> ${esc(recommendation.label || "")}</p>
          <p class="hint">${esc(recommendation.separated || "")}</p>
          <p class="hint">גרסאות: ${(offer.versions || []).map((item) => `#${esc(item.version_no)} ${item.amount != null ? ltr(item.amount) : ""}`).join(" · ")}</p>
          ${offer.supersedes_id ? `<p class="hint">המסמך אומר שהגרסה הזו מחליפה גרסה קודמת.</p>` : ""}
        </article>`;
      }).join("")}`;
  }

  async function uploadIntake(fileList) {
    const files = await filesFromList(fileList);
    if (!files.length) return;
    S.screen = "documents";
    location.hash = "#/documents";
    const form = new FormData();
    files.forEach((file) => form.append("files", file, file.name || "document"));
    const response = await fetch("/api/intake", { method: "POST", body: form, credentials: "include" });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      S.error = body.detail || "המסמכים לא נשמרו.";
      S.intakeResults = [];
    } else {
      S.error = "";
      S.intakeResults = body.results || [];
      S.toast = "הקליטה הסתיימה. הסיכום מופיע למטה.";
    }
    await loadIntake();
  }

  let intakeLoadSeq = 0;
  async function loadIntake() {
    const seq = ++intakeLoadSeq;
    const [docs, offers] = await Promise.all([
      fetch("/api/intake", { credentials: "include" }),
      fetch("/api/offers", { credentials: "include" }),
    ]);
    if (seq !== intakeLoadSeq) return;
    if (docs.ok) S.documents = (await docs.json()).documents || [];
    if (seq !== intakeLoadSeq) return;
    if (offers.ok) S.offers = (await offers.json()).offers || [];
    if (seq !== intakeLoadSeq) return;
    render();
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
    else if (S.screen === "value") body = renderValue();
    else if (S.screen === "value-detail") body = renderValueDetail();
    else if (S.screen === "comparables") body = renderComparables();
    else if (S.screen === "visit-prep") body = renderVisitPrep();
    else if (S.screen === "machines") body = renderMachines();
    else if (S.screen === "documents") body = renderDocuments();
    else if (S.screen === "offers") body = renderOffers();
    $(shell(body));
  }

  function go(screen) {
    S.screen = screen;
    S.error = "";
    const nextHash = "#/" + screen;
    const changing = location.hash !== nextHash;
    if (changing) location.hash = nextHash;
    if (screen === "import" && !S.importPreview) loadImport();
    if (screen === "ready") runReady();
    if (!changing && screen === "value") loadValue();
    if (!changing && screen === "comparables") loadComparables();
    if (!changing && screen === "visit-prep") loadVisitPrep();
    if (screen === "documents" || screen === "offers") loadIntake();
    if (!changing) render();
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
    const target = event.target.closest("[data-action], [data-go], [data-go-row], [data-go-value]");
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
    if (target.dataset.goValue) {
      S.screen = "value-detail";
      location.hash = "#/value/row/" + target.dataset.goValue;
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
      if (result.message) S.toast = result.message;
      await refreshFromServer().catch(() => {});
      if (result.status === "resolved") S.activeReview = null;
      render();
    }
    if (action === "open-machine") {
      S.openMachine = target.dataset.id;
      S.screen = "machines";
      location.hash = "#/machines";
      render();
    }
    if (action === "machine-filter") {
      S.machineFilter = target.dataset.filter;
      render();
    }
    if (action === "doc-filter") {
      S.docFilter = target.dataset.filter;
      S.activeDocument = null;
      render();
    }
    if (action === "open-document") {
      S.activeDocument = target.dataset.id;
      S.screen = "documents";
      location.hash = "#/documents";
      render();
    }
    if (action === "close-document") {
      S.activeDocument = null;
      render();
    }
    if (action === "open-offer") {
      S.screen = "offers";
      location.hash = "#/offers";
      render();
    }
    if (action === "intake-type") {
      const response = await fetch(`/api/intake/${target.dataset.id}/type`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ doc_type: target.dataset.type }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) S.error = result.detail || "הסוג לא עודכן.";
      else S.toast = "הסוג עודכן.";
      await loadIntake();
    }
    if (action === "intake-link" || action === "intake-unlink") {
      const response = await fetch(`/api/intake/${target.dataset.id}/links`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: action === "intake-link" ? "add" : "remove", row_ids: [target.dataset.row] }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) S.error = result.detail || "השיוך לא עודכן.";
      else S.toast = "השיוך עודכן. שורת המלאי נשארה.";
      await loadIntake();
    }
    if (action === "machine-act") {
      const id = target.dataset.id;
      const act = target.dataset.machineAct;
      const body = { action: act };
      if (act === "split") {
        body.tag_norms = [...document.querySelectorAll(`[data-machine-tag][data-machine="${id}"]:checked`)].map((box) => box.dataset.machineTag);
      }
      if (act === "combine") {
        const field = document.getElementById("machine-add-" + id);
        body.tag_norms = [field ? field.value.trim() : ""];
      }
      if (act === "correct") {
        const field = document.getElementById("machine-name-" + id);
        body.name = field ? field.value.trim() : "";
      }
      const response = await fetch(`/api/machines/${id}/action`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) S.error = result.detail || "הפעולה לא נשמרה.";
      else {
        S.error = "";
        S.toast = "היחידה עודכנה. שורות האקסל נשארו.";
        S.openMachine = id;
      }
      await refreshFromServer().catch(() => {});
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
    if (action === "val-time" && S.valueDetail) {
      const response = await fetch(`/api/valuation/subjects/${S.valueDetail.id}/time-scenario`, { method: "POST", credentials: "include" });
      if (response.ok) S.valueDetail = await response.json();
      render();
    }
    if (action === "val-exclude") {
      const reason = window.prompt("למה הראיה יוצאת מההשוואה?") || "";
      if (reason.trim().length < 2) return;
      const response = await fetch(`/api/valuation/evidence/${target.dataset.id}`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role: "excluded", exclusion_reason: reason }),
      });
      if (response.ok) {
        S.valueDetail = await response.json();
        await loadComparables();
      }
    }
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
    if (target.dataset.action === "tour-files") {
      await uploadTourFiles(target.files);
      target.value = "";
    }
    if (target.dataset.action === "intake-files") {
      await uploadIntake(target.files);
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
    if (target.dataset.action === "val-photo") {
      const form = new FormData();
      form.append("file", target.files[0]);
      const response = await fetch(`/api/valuation/requirements/${target.dataset.id}/photo`, { method: "POST", body: form, credentials: "include" });
      const result = await response.json().catch(() => ({}));
      S.error = result.message || result.detail || "";
      render();
    }
    if (target.dataset.action === "review-photo") {
      const form = new FormData();
      form.append("file", target.files[0]);
      const response = await fetch(`/api/review/${target.dataset.id}/photo`, { method: "POST", body: form, credentials: "include" });
      const result = await response.json().catch(() => ({}));
      if (result.ok) {
        S.toast = result.message || "הקובץ נשמר. השאלה נשארת פתוחה.";
        S.error = "";
      } else {
        S.error = result.message || result.detail || "הקובץ לא נשמר.";
      }
      await refreshFromServer().catch(() => {});
      render();
    }
  });

  document.getElementById("app").addEventListener("input", async (event) => {
    const target = event.target;
    const cap = S.captures[S.openCaptureId];
    if (target.dataset.action === "machine-search") {
      S.machineQuery = target.value;
      const selectionStart = target.selectionStart;
      const value = target.value;
      render();
      const again = document.querySelector("[data-action=machine-search]");
      if (again) {
        again.focus();
        again.value = value;
        if (again.setSelectionRange) again.setSelectionRange(selectionStart, selectionStart);
      }
      return;
    }
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
    if (form.dataset.action === "value-filter") {
      const data = new FormData(form);
      S.valueQuery = data.get("q") || "";
      S.valueFamily = data.get("family") || "";
      S.valueInfo = data.get("info") || "";
      await loadValue();
    }
    if (form.dataset.action === "val-basis" || form.dataset.action === "val-family") {
      const data = new FormData(form);
      const body = { updated_at: S.valueDetail.updated_at };
      if (form.dataset.action === "val-family") body.family = data.get("family");
      else data.forEach((value, key) => { body[key] = value; });
      const response = await fetch(`/api/valuation/subjects/${S.valueDetail.id}/basis`, {
        method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) S.error = result.detail || "הבסיס לא נשמר.";
      else { S.error = ""; S.valueDetail = result; }
      render();
    }
    if (form.dataset.action === "val-answer") {
      const data = new FormData(form);
      const response = await fetch(`/api/valuation/requirements/${form.dataset.id}/answer`, {
        method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: data.get("status"), text: data.get("text") }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) S.error = result.detail || "התשובה לא נשמרה.";
      else { S.error = ""; S.valueDetail = result; await refreshFromServer().catch(() => {}); }
      render();
    }
    if (form.dataset.action === "val-extra") {
      const data = new FormData(form);
      const response = await fetch(`/api/valuation/subjects/${S.valueDetail.id}/requirements`, {
        method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: data.get("question"), why: data.get("why"), how: data.get("how"), impact: "material" }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) S.error = result.detail || "הדרישה לא נוספה.";
      else { S.error = ""; S.valueDetail = result; }
      render();
    }
    if (form.dataset.action === "val-evidence") {
      const data = new FormData(form);
      const payload = {};
      data.forEach((value, key) => { payload[key] = value; });
      const response = await fetch(`/api/valuation/subjects/${S.valueDetail.id}/evidence`, {
        method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) S.error = result.detail || "הראיה לא נשמרה.";
      else { S.error = ""; S.valueDetail = result; }
      render();
    }
    if (form.dataset.action === "commercial") {
      const data = new FormData(form);
      const rowId = S.valueDetail.row_id || (S.valueDetail.members[0] && S.valueDetail.members[0].row_id);
      const response = await fetch(`/api/commercial/${rowId}`, {
        method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ offer_text: data.get("offer_text"), target_text: data.get("target_text") }),
      });
      const result = await response.json().catch(() => ({}));
      S.toast = response.ok ? result.separated : (result.detail || "לא נשמר.");
      const again = await fetch(`/api/valuation/subjects/${S.valueDetail.id}`, { credentials: "include" });
      if (again.ok) S.valueDetail = await again.json();
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
    else if (hash.startsWith("#/value/row/")) S.screen = "value-detail";
    else if (hash.startsWith("#/value")) S.screen = "value";
    else if (hash.startsWith("#/comparables")) S.screen = "comparables";
    else if (hash.startsWith("#/visit-prep")) S.screen = "visit-prep";
    else if (hash.startsWith("#/machines")) S.screen = "machines";
    else if (hash.startsWith("#/offers")) S.screen = "offers";
    else if (hash.startsWith("#/documents")) S.screen = "documents";
    else S.screen = "capture";
    if (S.screen === "value") loadValue();
    if (S.screen === "value-detail") loadValueDetail(hash.split("/")[3]);
    if (S.screen === "comparables") loadComparables();
    if (S.screen === "visit-prep") loadVisitPrep();
    if (S.screen === "documents" || S.screen === "offers") loadIntake();
    render();
  });
  let dragDepth = 0;
  function dragHasFiles(event) {
    const types = event.dataTransfer && event.dataTransfer.types;
    return Boolean(types && [...types].includes("Files"));
  }
  document.addEventListener("dragenter", (event) => {
    if (!S.authed || !dragHasFiles(event)) return;
    dragDepth += 1;
    document.body.classList.add("dropping");
    event.preventDefault();
  });
  document.addEventListener("dragover", (event) => {
    if (!S.authed || !dragHasFiles(event)) return;
    event.preventDefault();
  });
  document.addEventListener("dragleave", () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) document.body.classList.remove("dropping");
  });
  document.addEventListener("drop", async (event) => {
    dragDepth = 0;
    document.body.classList.remove("dropping");
    if (!S.authed || !event.dataTransfer) return;
    const onPicker = event.target && event.target.closest && event.target.closest("input[type=file]");
    if (onPicker) return;
    event.preventDefault();
    const files = await filesFromDrop(event.dataTransfer);
    if (!files.length) return;
    if (S.screen === "documents") await uploadIntake(files);
    else await uploadTourFiles(files);
  });

  window.addEventListener("online", () => { S.online = true; syncAll(); });
  window.addEventListener("offline", () => { S.online = false; render(); });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") syncAll();
  });

  boot();
})();
