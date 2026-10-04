/* Persistent on-device storage. Photos are blobs in IndexedDB, not localStorage. */
(function () {
  const DB_NAME = "equipment-capture";
  const DB_VERSION = 1;
  let dbPromise = null;

  function openDb() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        ["kv", "captures", "photos"].forEach((name) => {
          if (!db.objectStoreNames.contains(name)) db.createObjectStore(name, { keyPath: "id" });
        });
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error("indexedDB"));
    });
    return dbPromise;
  }

  function failIfForced() {
    if (window.__forceStoreFail) throw new Error("storage");
  }

  async function withStore(name, mode, fn) {
    failIfForced();
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(name, mode);
      const store = tx.objectStore(name);
      let result;
      try {
        result = fn(store);
      } catch (error) {
        reject(error);
        return;
      }
      tx.oncomplete = () => resolve(result);
      tx.onerror = () => reject(tx.error || new Error("storage"));
      tx.onabort = () => reject(tx.error || new Error("storage"));
    });
  }

  async function put(name, value) {
    await withStore(name, "readwrite", (store) => store.put(value));
    const back = await get(name, value.id);
    if (!back) throw new Error("readback");
    return back;
  }

  async function get(name, id) {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(name, "readonly");
      const request = tx.objectStore(name).get(id);
      request.onsuccess = () => resolve(request.result || null);
      request.onerror = () => reject(request.error);
    });
  }

  async function all(name) {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(name, "readonly");
      const request = tx.objectStore(name).getAll();
      request.onsuccess = () => resolve(request.result || []);
      request.onerror = () => reject(request.error);
    });
  }

  async function saveCapture(record) {
    try {
      record.state = "saving_locally";
      await put("captures", record);
      const back = await get("captures", record.id);
      if (!back || back.id !== record.id) throw new Error("readback");
      return { ok: true, record: back };
    } catch (error) {
      return { ok: false, error: error.message || "storage", record: null };
    }
  }

  async function savePhoto(record) {
    try {
      record.state = "saving_locally";
      await put("photos", record);
      const back = await get("photos", record.id);
      if (!back || !back.blob || back.blob.size !== record.blob.size) throw new Error("readback");
      return { ok: true, record: back };
    } catch (error) {
      return { ok: false, error: error.message || "storage", record: null };
    }
  }

  async function testOfflineStore() {
    const id = "self-test";
    const blob = new Blob(["capture-test"], { type: "text/plain" });
    const saved = await savePhoto({ id: id, captureId: "test", blob: blob, state: "saving_locally" });
    if (!saved.ok) return false;
    const db = await openDb();
    await new Promise((resolve, reject) => {
      const tx = db.transaction("photos", "readwrite");
      tx.objectStore("photos").delete(id);
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error);
    });
    return true;
  }

  async function remove(name, id) {
    const db = await openDb();
    await new Promise((resolve, reject) => {
      const tx = db.transaction(name, "readwrite");
      tx.objectStore(name).delete(id);
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error || new Error("storage"));
    });
  }

  window.CaptureStore = { openDb, put, get, all, remove, saveCapture, savePhoto, testOfflineStore };
})();
