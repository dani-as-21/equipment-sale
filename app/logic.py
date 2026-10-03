"""Capture sync, documentation status, review actions, and export."""

from __future__ import annotations

import csv
import io
import json
import re
import shutil
import sqlite3
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.matching import match_inventory

ID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
ITEM_ROLES = {"", "overall", "tag", "nameplate", "accessories"}
GENERAL_ROLES = {"location", "system"}
ACTOR_USER = "מנהלת הפרויקט"
ACTOR_SYSTEM = "מערכת"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_json(value: str | None, fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def valid_id(value: str) -> bool:
    return bool(value and ID_RE.fullmatch(value))


def link_id(capture_id: str, row_id: str) -> str:
    import hashlib

    digest = hashlib.sha256(f"{capture_id}|{row_id}".encode()).hexdigest()
    return "LNK-" + digest[:16]


def log_change(
    conn: sqlite3.Connection,
    *,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str,
    prior,
    new,
    created_at: str | None = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO change_log (actor, action, entity_type, entity_id, prior_json, new_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            actor,
            action,
            entity_type,
            entity_id,
            json.dumps(prior, ensure_ascii=False) if prior is not None else None,
            json.dumps(new, ensure_ascii=False) if new is not None else None,
            created_at or now_iso(),
        ),
    )
    return int(cursor.lastrowid)


def equipment_for_match(conn: sqlite3.Connection) -> list[dict]:
    rows = []
    for record in conn.execute("SELECT * FROM inventory_rows WHERE kind = 'equipment'"):
        rows.append(
            {
                "id": record["id"],
                "kind": record["kind"],
                "tag_norm": record["tag_norm"] or "",
                "tag_original": record["tag_original"] or "",
                "serial_norm": record["serial_norm"] or "",
                "serial": record["serial"] or "",
                "model": record["model"] or "",
                "description": record["description"] or "",
                "listed_area": record["listed_area"] or "",
                "identity_area": record["identity_area"] or "",
                "duplicate_group": record["duplicate_group"] or "",
                "sheet_name": record["sheet_name"],
                "original_row": record["original_row"],
            }
        )
    return rows


def area_context(conn: sqlite3.Connection, area_id: str | None, area_name: str | None) -> tuple[str, str, str | None]:
    if area_id:
        area = conn.execute("SELECT * FROM areas WHERE id = ?", (area_id,)).fetchone()
        if area:
            parent = ""
            if area["parent_id"]:
                parent_row = conn.execute("SELECT name FROM areas WHERE id = ?", (area["parent_id"],)).fetchone()
                parent = parent_row["name"] if parent_row else ""
            return area["id"], area["name"], parent
    name = (area_name or "").strip()
    if not name:
        return "", "", ""
    found = conn.execute("SELECT * FROM areas WHERE name = ?", (name,)).fetchone()
    if not found:
        return "", name, ""
    parent = ""
    if found["parent_id"]:
        parent_row = conn.execute("SELECT name FROM areas WHERE id = ?", (found["parent_id"],)).fetchone()
        parent = parent_row["name"] if parent_row else ""
    return found["id"], found["name"], parent


def capture_links(conn: sqlite3.Connection, capture_id: str) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM evidence_links WHERE capture_id = ? ORDER BY created_at",
            (capture_id,),
        )
    ]


def replace_links(
    conn: sqlite3.Connection,
    capture_id: str,
    links: list[dict],
    *,
    actor: str,
    created_at: str,
) -> None:
    prior = capture_links(conn, capture_id)
    prior_key = sorted((item["inventory_row_id"], item["review_status"], item["reason"]) for item in prior)
    new_key = sorted((item["row_id"], item["review_status"], item["reason"]) for item in links)
    if prior_key == new_key:
        return
    conn.execute("DELETE FROM evidence_links WHERE capture_id = ?", (capture_id,))
    for link in links:
        conn.execute(
            """
            INSERT INTO evidence_links (
              id, capture_id, inventory_row_id, method, reason, review_status, creator, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                link_id(capture_id, link["row_id"]),
                capture_id,
                link["row_id"],
                link["method"],
                link["reason"],
                link["review_status"],
                actor,
                created_at,
            ),
        )
    log_change(
        conn,
        actor=actor,
        action="links",
        entity_type="capture",
        entity_id=capture_id,
        prior=prior,
        new=links,
        created_at=created_at,
    )


def linked_row_dicts(conn: sqlite3.Connection, capture_id: str) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT r.* FROM inventory_rows r
            JOIN evidence_links l ON l.inventory_row_id = r.id
            WHERE l.capture_id = ? AND l.review_status != 'rejected'
            """,
            (capture_id,),
        )
    ]


def maybe_assign_shows(conn: sqlite3.Connection, capture_id: str) -> None:
    confirmed = [
        row
        for row in conn.execute(
            """
            SELECT r.*, l.review_status FROM inventory_rows r
            JOIN evidence_links l ON l.inventory_row_id = r.id
            WHERE l.capture_id = ? AND l.review_status IN ('auto_linked', 'user_confirmed')
            """,
            (capture_id,),
        )
    ]
    if not confirmed:
        return
    groups = {(row["duplicate_group"] or "") for row in confirmed}
    same_item = len(confirmed) == 1 or (len(groups) == 1 and next(iter(groups)))
    if not same_item:
        return
    row_ids = [row["id"] for row in confirmed]
    for photo in conn.execute("SELECT * FROM photos WHERE capture_id = ?", (capture_id,)):
        shows = parse_json(photo["shows_json"], {"scope": "unassigned", "row_ids": []})
        if shows.get("scope") not in (None, "", "unassigned"):
            continue
        role = photo["role"] or ""
        if role in GENERAL_ROLES:
            shows = {"scope": "general", "row_ids": []}
        else:
            shows = {"scope": "rows", "row_ids": row_ids}
        conn.execute("UPDATE photos SET shows_json = ? WHERE id = ?", (json.dumps(shows), photo["id"]))


def upsert_review(
    conn: sqlite3.Connection,
    *,
    kind: str,
    queue: str,
    dedupe_key: str,
    question: str,
    priority: str,
    capture_id: str | None,
    row_ids: list[str],
    payload: dict,
    created_at: str,
) -> None:
    import hashlib

    existing = conn.execute("SELECT id, status FROM review_items WHERE dedupe_key = ?", (dedupe_key,)).fetchone()
    if existing:
        if existing["status"] == "resolved":
            return
        conn.execute(
            """
            UPDATE review_items
            SET question = ?, row_ids_json = ?, payload_json = ?, updated_at = ?, capture_id = COALESCE(?, capture_id)
            WHERE id = ?
            """,
            (
                question,
                json.dumps(row_ids),
                json.dumps(payload, ensure_ascii=False),
                created_at,
                capture_id,
                existing["id"],
            ),
        )
        return
    review_id = "REV-" + hashlib.sha256(dedupe_key.encode()).hexdigest()[:16]
    conn.execute(
        """
        INSERT INTO review_items (
          id, kind, queue, dedupe_key, question, priority, status, capture_id,
          row_ids_json, payload_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?)
        """,
        (
            review_id,
            kind,
            queue,
            dedupe_key,
            question,
            priority,
            capture_id,
            json.dumps(row_ids),
            json.dumps(payload, ensure_ascii=False),
            created_at,
            created_at,
        ),
    )


def close_review(conn: sqlite3.Connection, dedupe_key: str, resolution: str, created_at: str) -> None:
    conn.execute(
        """
        UPDATE review_items
        SET status = 'resolved', resolution = ?, updated_at = ?, missing_reason = NULL
        WHERE dedupe_key = ? AND status != 'resolved'
        """,
        (resolution, created_at, dedupe_key),
    )


def apply_capture(conn: sqlite3.Connection, body: dict, created_at: str | None = None) -> dict:
    capture_id = body.get("id") or ""
    if not valid_id(capture_id):
        raise ValueError("מזהה הקליטה אינו תקין.")
    created_at = created_at or now_iso()
    incoming_time = body.get("client_updated_at") or created_at
    existing = conn.execute("SELECT * FROM captures WHERE id = ?", (capture_id,)).fetchone()
    if existing and existing["client_updated_at"] and incoming_time < existing["client_updated_at"]:
        return {"capture": dict(existing), "links": capture_links(conn, capture_id), "kept": "server"}

    area_key, area_name, parent = area_context(conn, body.get("observed_area_id"), body.get("observed_area_name"))
    if body.get("observed_area_name") and not area_name:
        area_name = body.get("observed_area_name")
    visit_id = body.get("visit_id")
    if visit_id and not conn.execute("SELECT id FROM visits WHERE id = ?", (visit_id,)).fetchone():
        visit_id = None
    if not visit_id:
        current = conn.execute("SELECT id FROM visits WHERE is_current = 1").fetchone()
        visit_id = current["id"] if current else None

    prior_fields = dict(existing) if existing else None
    fields = {
        "visit_id": visit_id,
        "observed_area_id": area_key or None,
        "observed_area_name": area_name or body.get("observed_area_name") or "",
        "note": (body.get("note") or "")[:4000],
        "info_provided_by": (body.get("info_provided_by") or "")[:200],
        "tag_text": (body.get("tag_text") or "")[:200],
        "model_text": (body.get("model_text") or "")[:200],
        "serial_text": (body.get("serial_text") or "")[:200],
        "manufacturer_text": (body.get("manufacturer_text") or "")[:200],
        "raw_ocr": body.get("raw_ocr") or "",
        "match_mode": body.get("match_mode") or "none",
        "finalized": 1 if body.get("finalized") else 0,
        "client_updated_at": incoming_time,
        "sync_status": "pending_sync",
    }
    if existing:
        conn.execute(
            """
            UPDATE captures SET
              visit_id = :visit_id,
              observed_area_id = :observed_area_id,
              observed_area_name = :observed_area_name,
              note = :note,
              info_provided_by = :info_provided_by,
              tag_text = :tag_text,
              model_text = :model_text,
              serial_text = :serial_text,
              manufacturer_text = :manufacturer_text,
              raw_ocr = :raw_ocr,
              match_mode = :match_mode,
              finalized = :finalized,
              client_updated_at = :client_updated_at,
              sync_status = :sync_status
            WHERE id = :id
            """,
            {**fields, "id": capture_id},
        )
    else:
        conn.execute(
            """
            INSERT INTO captures (
              id, visit_id, observed_area_id, observed_area_name, note, info_provided_by,
              tag_text, model_text, serial_text, manufacturer_text, raw_ocr, match_mode,
              finalized, client_updated_at, created_at, sync_status
            ) VALUES (
              :id, :visit_id, :observed_area_id, :observed_area_name, :note, :info_provided_by,
              :tag_text, :model_text, :serial_text, :manufacturer_text, :raw_ocr, :match_mode,
              :finalized, :client_updated_at, :created_at, :sync_status
            )
            """,
            {**fields, "id": capture_id, "created_at": body.get("created_at") or created_at},
        )
    if prior_fields and (
        prior_fields.get("tag_text") != fields["tag_text"]
        or prior_fields.get("observed_area_name") != fields["observed_area_name"]
        or prior_fields.get("note") != fields["note"]
    ):
        log_change(
            conn,
            actor=ACTOR_USER,
            action="capture_fields",
            entity_type="capture",
            entity_id=capture_id,
            prior={
                "tag_text": prior_fields.get("tag_text"),
                "observed_area_name": prior_fields.get("observed_area_name"),
                "note": prior_fields.get("note"),
            },
            new={
                "tag_text": fields["tag_text"],
                "observed_area_name": fields["observed_area_name"],
                "note": fields["note"],
            },
            created_at=created_at,
        )

    mode = fields["match_mode"]
    rows = equipment_for_match(conn)
    created_specific_review = False
    if mode == "user":
        user_links = []
        for link in body.get("links") or []:
            row_id = link.get("row_id")
            if not row_id:
                continue
            if not conn.execute("SELECT id FROM inventory_rows WHERE id = ?", (row_id,)).fetchone():
                continue
            user_links.append(
                {
                    "row_id": row_id,
                    "method": "user",
                    "review_status": "user_confirmed",
                    "reason": link.get("reason") or "נבחר ידנית בסיור.",
                }
            )
        replace_links(conn, capture_id, user_links, actor=ACTOR_USER, created_at=created_at)
        maybe_assign_shows(conn, capture_id)
        close_review(conn, f"cap:{capture_id}:unmatched", "קושר ידנית לשורה.", created_at)
    elif mode == "unresolved":
        replace_links(conn, capture_id, [], actor=ACTOR_USER, created_at=created_at)
    else:
        result = match_inventory(
            rows,
            tag=fields["tag_text"],
            serial=fields["serial_text"],
            model=fields["model_text"],
            observed_area=fields["observed_area_name"],
            observed_parent=parent or "",
        )
        server_links = result["links"]
        actor = ACTOR_SYSTEM if result["mode"] == "auto" else ACTOR_SYSTEM
        replace_links(conn, capture_id, server_links, actor=actor, created_at=created_at)
        if result["mode"] == "auto":
            maybe_assign_shows(conn, capture_id)
        fields["match_mode"] = result["mode"] if result["mode"] != "none" else mode
        conn.execute("UPDATE captures SET match_mode = ? WHERE id = ?", (fields["match_mode"], capture_id))
        if body.get("finalized") and result.get("review_kind"):
            created_specific_review = True
            upsert_review(
                conn,
                kind=result["review_kind"],
                queue="visit",
                dedupe_key=f"cap:{capture_id}:{result['review_kind']}",
                question=result["explanation"],
                priority="high",
                capture_id=capture_id,
                row_ids=[item["row_id"] for item in result["suggestions"]],
                payload={"suggestions": result["suggestions"], "explanation": result["explanation"]},
                created_at=created_at,
            )
        elif body.get("finalized") and result["mode"] == "auto":
            close_review(conn, f"cap:{capture_id}:unmatched", "קושר אוטומטית.", created_at)

    if body.get("finalized") and mode == "unresolved":
        upsert_review(
            conn,
            kind="unmatched",
            queue="visit",
            dedupe_key=f"cap:{capture_id}:unmatched",
            question=(
                "הפריט שצולם לא הותאם לשורת מלאי. אפשר לחפש שוב, לקשר ידנית, "
                "או להשאיר אותו כנצפה בסיור. זה לא מכריז על ציוד חדש."
            ),
            priority="high",
            capture_id=capture_id,
            row_ids=[],
            payload={},
            created_at=created_at,
        )
    elif body.get("finalized") and mode not in {"user", "unresolved"} and not created_specific_review:
        current_links = capture_links(conn, capture_id)
        if not current_links:
            upsert_review(
                conn,
                kind="unmatched",
                queue="visit",
                dedupe_key=f"cap:{capture_id}:unmatched",
                question=(
                    "הפריט שצולם לא הותאם לשורת מלאי. אפשר לחפש שוב, לקשר ידנית, "
                    "או להשאיר אותו כנצפה בסיור. זה לא מכריז על ציוד חדש."
                ),
                priority="high",
                capture_id=capture_id,
                row_ids=[],
                payload={},
                created_at=created_at,
            )

    stored = dict(conn.execute("SELECT * FROM captures WHERE id = ?", (capture_id,)).fetchone())
    return {"capture": stored, "links": capture_links(conn, capture_id), "kept": "client"}


def save_photo_file(
    conn: sqlite3.Connection,
    *,
    photo_id: str,
    capture_id: str,
    role: str,
    original_name: str,
    data: bytes,
    shows: dict | None,
    photo_dir: Path,
    thumb_dir: Path,
    created_at: str,
) -> dict:
    if not valid_id(photo_id) or not valid_id(capture_id):
        raise ValueError("מזהה הצילום או הקליטה אינו תקין.")
    if not data:
        raise ValueError("הקובץ ריק ולא נשמר.")
    photo_dir.mkdir(parents=True, exist_ok=True)
    thumb_dir.mkdir(parents=True, exist_ok=True)
    if not conn.execute("SELECT id FROM captures WHERE id = ?", (capture_id,)).fetchone():
        current = conn.execute("SELECT id FROM visits WHERE is_current = 1").fetchone()
        conn.execute(
            """
            INSERT INTO captures (
              id, visit_id, note, match_mode, finalized, created_at, sync_status, client_updated_at
            ) VALUES (?, ?, '', 'none', 0, ?, 'pending_sync', ?)
            """,
            (capture_id, current["id"] if current else None, created_at, created_at),
        )
    suffix = Path(original_name or "photo.jpg").suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif"}:
        suffix = ".jpg"
    target = photo_dir / f"{photo_id}{suffix}"
    target.write_bytes(data)
    if not target.exists() or target.stat().st_size != len(data):
        raise IOError("שמירת הקובץ נכשלה.")
    thumb_path = thumb_dir / f"{photo_id}.jpg"
    try:
        with Image.open(io.BytesIO(data)) as image:
            image = image.convert("RGB")
            image.thumbnail((480, 480))
            image.save(thumb_path, "JPEG", quality=70)
    except (UnidentifiedImageError, OSError):
        thumb_path = None
    if shows is None:
        shows = {"scope": "unassigned", "row_ids": []}
    existing = conn.execute("SELECT id FROM photos WHERE id = ?", (photo_id,)).fetchone()
    payload = {
        "id": photo_id,
        "capture_id": capture_id,
        "role": role or "",
        "original_name": original_name or "photo.jpg",
        "stored_path": str(target),
        "thumb_path": str(thumb_path) if thumb_path and thumb_path.exists() else None,
        "upload_state": "stored",
        "shows_json": json.dumps(shows, ensure_ascii=False),
        "created_at": created_at,
    }
    if existing:
        conn.execute(
            """
            UPDATE photos SET capture_id = :capture_id, role = :role, original_name = :original_name,
              stored_path = :stored_path, thumb_path = :thumb_path, upload_state = :upload_state,
              shows_json = :shows_json
            WHERE id = :id
            """,
            payload,
        )
    else:
        conn.execute(
            """
            INSERT INTO photos (
              id, capture_id, role, original_name, stored_path, thumb_path, upload_state, shows_json, created_at
            ) VALUES (
              :id, :capture_id, :role, :original_name, :stored_path, :thumb_path, :upload_state, :shows_json, :created_at
            )
            """,
            payload,
        )
    maybe_assign_shows(conn, capture_id)
    return {"stored": True, "photo_id": photo_id, "synced": False}


def confirm_capture(conn: sqlite3.Connection, capture_id: str, photo_ids: list[str], created_at: str) -> dict:
    if not conn.execute("SELECT id FROM captures WHERE id = ?", (capture_id,)).fetchone():
        return {"synced": False, "missing": photo_ids, "error": "הקליטה לא נמצאה בשרת."}
    missing = []
    for photo_id in photo_ids:
        photo = conn.execute("SELECT * FROM photos WHERE id = ? AND capture_id = ?", (photo_id, capture_id)).fetchone()
        if not photo or not photo["stored_path"] or not Path(photo["stored_path"]).exists():
            missing.append(photo_id)
            continue
        if Path(photo["stored_path"]).stat().st_size <= 0:
            missing.append(photo_id)
    if missing:
        conn.execute(
            "UPDATE captures SET sync_status = 'failed', server_received_at = NULL WHERE id = ?",
            (capture_id,),
        )
        return {"synced": False, "missing": missing}
    conn.execute(
        "UPDATE captures SET sync_status = 'synced', server_received_at = ? WHERE id = ?",
        (created_at, capture_id),
    )
    if photo_ids:
        marks = ",".join("?" for _ in photo_ids)
        conn.execute(
            f"UPDATE photos SET upload_state = 'synced' WHERE capture_id = ? AND id IN ({marks})",
            (capture_id, *photo_ids),
        )
    return {"synced": True, "missing": []}


def run_ocr(path: str) -> dict:
    if not path or not Path(path).exists():
        return {"available": False, "raw_text": "", "message": "הקובץ לא נמצא."}
    if not shutil.which("tesseract"):
        return {
            "available": False,
            "raw_text": "",
            "message": "זיהוי הטקסט לא זמין כרגע. אפשר להקליד את התג. הצילום נשמר גם בלי זה.",
        }
    try:
        import pytesseract

        with Image.open(path) as image:
            image.thumbnail((1800, 1800))
            text = pytesseract.image_to_string(image, lang="eng") or ""
        return {"available": True, "raw_text": text, "message": ""}
    except Exception:
        return {
            "available": True,
            "raw_text": "",
            "message": "לא הצלחנו לקרוא טקסט מהתמונה. אפשר להקליד את התג או לסמן שהשלט לא קריא.",
        }


def update_photo(conn: sqlite3.Connection, photo_id: str, body: dict, created_at: str) -> dict:
    photo = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if not photo:
        raise LookupError("הצילום לא נמצא.")
    prior = {"role": photo["role"], "shows_json": photo["shows_json"]}
    role = body.get("role", photo["role"]) or ""
    shows = body.get("shows", parse_json(photo["shows_json"], {"scope": "unassigned", "row_ids": []}))
    conn.execute(
        "UPDATE photos SET role = ?, shows_json = ? WHERE id = ?",
        (role, json.dumps(shows, ensure_ascii=False), photo_id),
    )
    log_change(
        conn,
        actor=ACTOR_USER,
        action="photo_assignment",
        entity_type="photo",
        entity_id=photo_id,
        prior=prior,
        new={"role": role, "shows": shows},
        created_at=created_at,
    )
    return {"id": photo_id, "role": role, "shows": shows}


def split_capture(conn: sqlite3.Connection, capture_id: str, new_id: str, photo_ids: list[str], created_at: str) -> dict:
    if not valid_id(new_id):
        raise ValueError("מזהה הקליטה החדשה אינו תקין.")
    source = conn.execute("SELECT * FROM captures WHERE id = ?", (capture_id,)).fetchone()
    if not source:
        raise LookupError("הקליטה לא נמצאה.")
    if not conn.execute("SELECT id FROM captures WHERE id = ?", (new_id,)).fetchone():
        conn.execute(
            """
            INSERT INTO captures (
              id, visit_id, observed_area_id, observed_area_name, note, match_mode, finalized,
              created_at, sync_status, client_updated_at
            ) VALUES (?, ?, ?, ?, '', 'unresolved', 1, ?, 'pending_sync', ?)
            """,
            (
                new_id,
                source["visit_id"],
                source["observed_area_id"],
                source["observed_area_name"],
                created_at,
                created_at,
            ),
        )
    moved = []
    for photo_id in photo_ids:
        photo = conn.execute(
            "SELECT id FROM photos WHERE id = ? AND capture_id = ?",
            (photo_id, capture_id),
        ).fetchone()
        if not photo:
            continue
        conn.execute("UPDATE photos SET capture_id = ? WHERE id = ?", (new_id, photo_id))
        moved.append(photo_id)
    log_change(
        conn,
        actor=ACTOR_USER,
        action="split",
        entity_type="capture",
        entity_id=capture_id,
        prior={"photo_ids": photo_ids},
        new={"new_capture_id": new_id, "moved": moved},
        created_at=created_at,
    )
    return {"new_capture_id": new_id, "moved": moved}


def add_relationship(conn: sqlite3.Connection, capture_id: str, body: dict, created_at: str) -> dict:
    text = (body.get("text") or "").strip()
    if not text:
        raise ValueError("חסר ניסוח של הקשר.")
    status = body.get("status") or "tentative"
    if status not in {"tentative", "confirmed", "unclear"}:
        status = "tentative"
    claim_id = "REL-" + uuid.uuid4().hex[:16]
    row_ids = body.get("row_ids") or []
    conn.execute(
        """
        INSERT INTO relationship_claims (id, capture_id, text, person, status, related_row_ids_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            claim_id,
            capture_id,
            text[:2000],
            (body.get("person") or "")[:200],
            status,
            json.dumps(row_ids),
            created_at,
        ),
    )
    if status == "unclear":
        upsert_review(
            conn,
            kind="relationship",
            queue="visit",
            dedupe_key=f"cap:{capture_id}:relationship:{claim_id}",
            question=f"נרשמה הערה על קשר, והיא לא ברורה: {text} האם זה נכון, ולגבי איזה ציוד?",
            priority="medium",
            capture_id=capture_id,
            row_ids=row_ids,
            payload={"claim_id": claim_id},
            created_at=created_at,
        )
    return {"id": claim_id, "status": status}


def undo_change(conn: sqlite3.Connection, log_id: int, created_at: str) -> dict:
    entry = conn.execute("SELECT * FROM change_log WHERE id = ?", (log_id,)).fetchone()
    if not entry or entry["undone"]:
        raise LookupError("אין פעולה לביטול.")
    prior = parse_json(entry["prior_json"], None)
    if entry["action"] == "links" and entry["entity_type"] == "capture":
        restored = []
        for link in prior or []:
            restored.append(
                {
                    "row_id": link["inventory_row_id"],
                    "method": link["method"],
                    "review_status": link["review_status"],
                    "reason": link["reason"],
                }
            )
        replace_links(conn, entry["entity_id"], restored, actor=ACTOR_USER, created_at=created_at)
    elif entry["action"] == "capture_fields":
        prior = prior or {}
        conn.execute(
            """
            UPDATE captures SET tag_text = ?, observed_area_name = ?, note = ?, client_updated_at = ?
            WHERE id = ?
            """,
            (prior.get("tag_text"), prior.get("observed_area_name"), prior.get("note"), created_at, entry["entity_id"]),
        )
    elif entry["action"] == "photo_assignment":
        prior = prior or {}
        conn.execute(
            "UPDATE photos SET role = ?, shows_json = ? WHERE id = ?",
            (prior.get("role"), prior.get("shows_json"), entry["entity_id"]),
        )
    else:
        raise ValueError("אי אפשר לבטל את הפעולה הזו אוטומטית.")
    conn.execute("UPDATE change_log SET undone = 1 WHERE id = ?", (log_id,))
    log_change(
        conn,
        actor=ACTOR_USER,
        action="undo",
        entity_type=entry["entity_type"],
        entity_id=entry["entity_id"],
        prior=parse_json(entry["new_json"], None),
        new=prior,
        created_at=created_at,
    )
    return {"undone": log_id}


def review_action(conn: sqlite3.Connection, review_id: str, body: dict, created_at: str) -> dict:
    item = conn.execute("SELECT * FROM review_items WHERE id = ?", (review_id,)).fetchone()
    if not item:
        raise LookupError("השאלה לא נמצאה.")
    action = body.get("action")
    if action == "defer":
        conn.execute(
            "UPDATE review_items SET status = 'deferred', updated_at = ?, missing_reason = NULL WHERE id = ?",
            (created_at, review_id),
        )
        return {"status": "deferred"}
    if action == "link":
        row_ids = [row_id for row_id in (body.get("row_ids") or []) if row_id]
        if not item["capture_id"] or not row_ids:
            conn.execute(
                "UPDATE review_items SET missing_reason = ?, updated_at = ? WHERE id = ?",
                ("כדי לסגור צריך לבחור שורה מהרשימה. העלאה או טקסט כללי לא מספיקים.", created_at, review_id),
            )
            return {"status": item["status"], "missing": "כדי לסגור צריך לבחור שורה מהרשימה."}
        links = [
            {"row_id": row_id, "method": "user", "review_status": "user_confirmed", "reason": "נבחר מתוך תור הבדיקה."}
            for row_id in row_ids
        ]
        replace_links(conn, item["capture_id"], links, actor=ACTOR_USER, created_at=created_at)
        conn.execute(
            "UPDATE captures SET match_mode = 'user', client_updated_at = ? WHERE id = ?",
            (created_at, item["capture_id"]),
        )
        maybe_assign_shows(conn, item["capture_id"])
        conn.execute(
            """
            UPDATE review_items
            SET status = 'resolved', resolution = ?, updated_at = ?, missing_reason = NULL
            WHERE id = ?
            """,
            ("קושר לשורות שנבחרו.", created_at, review_id),
        )
        return {"status": "resolved"}
    if action == "leave_separate":
        if item["kind"] not in {"duplicate_tag", "generic_name", "ambiguous", "unmatched"}:
            return {"status": item["status"], "missing": "הפעולה הזו לא מתאימה לשאלה."}
        conn.execute(
            """
            UPDATE review_items
            SET status = 'resolved', resolution = ?, updated_at = ?, missing_reason = NULL
            WHERE id = ?
            """,
            ("השורות נשארות נפרדות. לא אוחדו ולא הוכרז שהן מערכת אחת.", created_at, review_id),
        )
        return {"status": "resolved"}
    if action == "correct":
        if not item["capture_id"]:
            return {"status": item["status"], "missing": "אין קליטה לעדכון."}
        capture = conn.execute("SELECT * FROM captures WHERE id = ?", (item["capture_id"],)).fetchone()
        prior = {
            "tag_text": capture["tag_text"],
            "observed_area_name": capture["observed_area_name"],
            "note": capture["note"],
        }
        tag_text = body.get("tag_text", capture["tag_text"])
        area_name = body.get("observed_area_name", capture["observed_area_name"])
        conn.execute(
            """
            UPDATE captures SET tag_text = ?, observed_area_name = ?, client_updated_at = ? WHERE id = ?
            """,
            (tag_text, area_name, created_at, item["capture_id"]),
        )
        log_change(
            conn,
            actor=ACTOR_USER,
            action="capture_fields",
            entity_type="capture",
            entity_id=item["capture_id"],
            prior=prior,
            new={"tag_text": tag_text, "observed_area_name": area_name, "note": capture["note"]},
            created_at=created_at,
        )
        if body.get("recompute"):
            apply_capture(
                conn,
                {
                    "id": item["capture_id"],
                    "client_updated_at": created_at,
                    "tag_text": tag_text,
                    "observed_area_name": area_name,
                    "note": capture["note"],
                    "serial_text": capture["serial_text"],
                    "model_text": capture["model_text"],
                    "match_mode": "auto",
                    "finalized": 1,
                    "info_provided_by": capture["info_provided_by"],
                    "raw_ocr": capture["raw_ocr"],
                    "visit_id": capture["visit_id"],
                },
                created_at,
            )
        return {"status": "open", "corrected": True}
    if action == "resolve":
        text = (body.get("text") or "").strip()
        if len(text) < 2:
            conn.execute(
                "UPDATE review_items SET missing_reason = ?, updated_at = ? WHERE id = ?",
                ("חסרה תשובה כתובה. העלאת קובץ לבד לא סוגרת את השאלה.", created_at, review_id),
            )
            return {"status": item["status"], "missing": "חסרה תשובה כתובה. העלאת קובץ לבד לא סוגרת את השאלה."}
        if item["kind"] in {"lab_anomaly", "nameplate", "relationship", "generic_name", "unmatched"}:
            conn.execute(
                """
                UPDATE review_items
                SET status = 'resolved', resolution = ?, updated_at = ?, missing_reason = NULL
                WHERE id = ?
                """,
                (text, created_at, review_id),
            )
            return {"status": "resolved"}
        conn.execute(
            "UPDATE review_items SET missing_reason = ?, updated_at = ? WHERE id = ?",
            ("כדי לסגור את השאלה צריך לקשר שורה, להשאיר את השורות נפרדות, או לדחות.", created_at, review_id),
        )
        return {"status": item["status"], "missing": "כדי לסגור את השאלה צריך לקשר שורה או להשאיר את השורות נפרדות."}
    if action == "split":
        if not item["capture_id"]:
            return {"status": item["status"], "missing": "אין קליטה לפיצול."}
        result = split_capture(conn, item["capture_id"], body.get("new_capture_id") or str(uuid.uuid4()), body.get("photo_ids") or [], created_at)
        return {"status": item["status"], "split": result}
    raise ValueError("פעולה לא מוכרת.")


def row_documentation(conn: sqlite3.Connection) -> dict[str, dict]:
    documented: set[str] = set()
    for photo in conn.execute("SELECT shows_json FROM photos"):
        shows = parse_json(photo["shows_json"], {})
        if shows.get("scope") == "rows":
            documented.update(shows.get("row_ids") or [])
    pending: set[str] = set()
    identity: dict[str, str] = {}
    for link in conn.execute("SELECT inventory_row_id, review_status FROM evidence_links"):
        row_id = link["inventory_row_id"]
        status = link["review_status"]
        if status == "pending_review":
            pending.add(row_id)
            identity[row_id] = "pending"
        elif status == "user_confirmed" and identity.get(row_id) != "pending":
            identity[row_id] = "user_confirmed"
        elif status == "auto_linked" and row_id not in identity:
            identity[row_id] = "auto_linked"
    review_rows: set[str] = set()
    for review in conn.execute("SELECT row_ids_json, status FROM review_items WHERE status IN ('open', 'deferred')"):
        review_rows.update(parse_json(review["row_ids_json"], []))
    flags = {}
    for row in conn.execute("SELECT id, kind, listed_area, sheet_name, duplicate_group FROM inventory_rows"):
        if row["kind"] != "equipment":
            continue
        row_id = row["id"]
        flags[row_id] = {
            "documented": row_id in documented,
            "needs_review": row_id in pending or row_id in review_rows,
            "identity": identity.get(row_id, "none"),
            "listed_area": row["listed_area"] or "",
            "sheet_name": row["sheet_name"],
            "duplicate_group": row["duplicate_group"] or "",
        }
    return flags


def visit_summary(conn: sqlite3.Connection) -> dict:
    flags = row_documentation(conn)
    by_area: dict[str, dict] = {}
    for row_id, flag in flags.items():
        area = flag["listed_area"] or "לא ידוע"
        bucket = by_area.setdefault(area, {"area": area, "rows": 0, "documented": 0, "needs_review": 0})
        bucket["rows"] += 1
        if flag["documented"]:
            bucket["documented"] += 1
        if flag["needs_review"]:
            bucket["needs_review"] += 1
    documented = sum(1 for flag in flags.values() if flag["documented"])
    needs_review = sum(1 for flag in flags.values() if flag["needs_review"])
    equipment = len(flags)
    captures = [dict(row) for row in conn.execute("SELECT * FROM captures")]
    synced = sum(1 for capture in captures if capture["sync_status"] == "synced")
    pending_sync = sum(1 for capture in captures if capture["sync_status"] != "synced")
    unresolved = 0
    for capture in captures:
        if not capture["finalized"]:
            continue
        links = capture_links(conn, capture["id"])
        confirmed = [link for link in links if link["review_status"] in {"auto_linked", "user_confirmed"}]
        if not confirmed:
            unresolved += 1
    revisit = []
    for row in conn.execute(
        """
        SELECT id, tag_original, description, listed_area, sheet_name, original_row
        FROM inventory_rows WHERE kind = 'equipment'
        """
    ):
        flag = flags[row["id"]]
        if flag["documented"] and not flag["needs_review"]:
            continue
        if flag["documented"] and flag["needs_review"]:
            reason = "יש תיעוד, ועדיין יש שאלה פתוחה"
        elif flag["needs_review"]:
            reason = "שאלה פתוחה"
        else:
            reason = "אין עדיין צילום של הפריט עצמו"
        if not flag["documented"] or flag["needs_review"]:
            revisit.append(
                {
                    "row_id": row["id"],
                    "tag": row["tag_original"] or "",
                    "description": row["description"] or "",
                    "listed_area": row["listed_area"] or "לא ידוע",
                    "sheet_name": row["sheet_name"],
                    "original_row": row["original_row"],
                    "reason": reason,
                    "documented": flag["documented"],
                    "needs_review": flag["needs_review"],
                }
            )
    visit = conn.execute("SELECT * FROM visits WHERE is_current = 1").fetchone()
    return {
        "visit": dict(visit) if visit else None,
        "captures_synced": synced,
        "captures_not_synced": pending_sync,
        "rows_equipment": equipment,
        "rows_documented": documented,
        "rows_needs_review": needs_review,
        "rows_not_documented": equipment - documented,
        "unresolved_captures": unresolved,
        "by_area": sorted(by_area.values(), key=lambda item: item["area"]),
        "revisit_count": len(revisit),
        "footnote": "המספרים סופרים שורות מלאי, לא מכונות פיזיות. תגים כפולים וכמויות שלא הוכרעו נשארים שורות נפרדות.",
        "revisit": revisit,
    }


def public_row(record: sqlite3.Row, source_name: str) -> dict:
    return {
        "id": record["id"],
        "kind": record["kind"],
        "source_file": source_name,
        "source_kind": record["source_kind"],
        "sheet_name": record["sheet_name"],
        "original_row": record["original_row"],
        "identity_area": record["identity_area"],
        "listed_area": record["listed_area"] or "",
        "tag_original": record["tag_original"] or "",
        "tag_norm": record["tag_norm"] or "",
        "description": record["description"] or "",
        "manufacturer": record["manufacturer"] or "",
        "model": record["model"] or "",
        "serial": record["serial"] or "",
        "serial_norm": record["serial_norm"] or "",
        "quantity": record["quantity"] or "",
        "remarks": record["remarks"] or "",
        "specs": record["specs"] or "",
        "material": record["material"] or "",
        "system_number": record["system_number"] or "",
        "efd": record["efd"] or "",
        "installation_raw": record["installation_raw"] or "",
        "labels": parse_json(record["labels_json"], []),
        "duplicate_group": record["duplicate_group"] or "",
        "raw": parse_json(record["raw_json"], {}),
    }


def inventory_csv(conn: sqlite3.Connection) -> str:
    flags = row_documentation(conn)
    observed: dict[str, list[str]] = {}
    notes: dict[str, list[str]] = {}
    photos: dict[str, list[str]] = {}
    for link in conn.execute(
        """
        SELECT l.inventory_row_id, c.observed_area_name, c.note, c.id AS capture_id
        FROM evidence_links l JOIN captures c ON c.id = l.capture_id
        """
    ):
        if link["observed_area_name"]:
            observed.setdefault(link["inventory_row_id"], []).append(link["observed_area_name"])
        if link["note"]:
            notes.setdefault(link["inventory_row_id"], []).append(link["note"])
    for photo in conn.execute("SELECT id, shows_json, capture_id FROM photos"):
        shows = parse_json(photo["shows_json"], {})
        if shows.get("scope") != "rows":
            continue
        for row_id in shows.get("row_ids") or []:
            photos.setdefault(row_id, []).append(f"{photo['capture_id']}/{photo['id']}")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "stable_id",
            "source_file",
            "sheet_or_table",
            "original_row",
            "tag",
            "description",
            "listed_area",
            "observed_areas",
            "notes",
            "documentation_status",
            "identity_status",
            "labels",
            "quantity",
            "duplicate_group",
            "photo_references",
        ]
    )
    sources = {row["id"]: row["display_name"] for row in conn.execute("SELECT id, display_name FROM source_files")}
    for record in conn.execute("SELECT * FROM inventory_rows WHERE kind = 'equipment' ORDER BY sheet_name, original_row"):
        flag = flags.get(record["id"], {})
        if flag.get("needs_review"):
            doc = "needs_review"
        elif flag.get("documented"):
            doc = "documented"
        else:
            doc = "not_documented"
        writer.writerow(
            [
                record["id"],
                sources.get(record["source_file_id"], ""),
                record["sheet_name"],
                record["original_row"],
                record["tag_original"] or "",
                record["description"] or "",
                record["listed_area"] or "",
                " | ".join(dict.fromkeys(observed.get(record["id"], []))),
                " | ".join(notes.get(record["id"], [])),
                doc,
                flag.get("identity", "none"),
                " | ".join(parse_json(record["labels_json"], [])),
                record["quantity"] or "",
                record["duplicate_group"] or "",
                " | ".join(photos.get(record["id"], [])),
            ]
        )
    return buffer.getvalue()


def export_package(conn: sqlite3.Connection, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    flags = row_documentation(conn)
    manifest = {
        "exported_at": now_iso(),
        "note": "קישור תמונה לשורת מלאי אינו קשר מערכת ואינו איחוד של פריטים.",
        "captures": [],
        "links": [],
    }
    captures = []
    for capture in conn.execute("SELECT * FROM captures"):
        photos = []
        for photo in conn.execute("SELECT * FROM photos WHERE capture_id = ?", (capture["id"],)):
            shows = parse_json(photo["shows_json"], {})
            photos.append(
                {
                    "photo_id": photo["id"],
                    "role": photo["role"],
                    "file": f"photos/{capture['id']}/{photo['id']}{Path(photo['stored_path']).suffix if photo['stored_path'] else ''}",
                    "shows": shows,
                    "upload_state": photo["upload_state"],
                }
            )
        captures.append(
            {
                "capture_id": capture["id"],
                "observed_area": capture["observed_area_name"],
                "note": capture["note"],
                "info_provided_by": capture["info_provided_by"],
                "tag_text": capture["tag_text"],
                "sync_status": capture["sync_status"],
                "photos": photos,
            }
        )
    manifest["captures"] = captures
    for link in conn.execute(
        """
        SELECT l.*, r.sheet_name, r.original_row, r.tag_original, r.id AS row_id
        FROM evidence_links l JOIN inventory_rows r ON r.id = l.inventory_row_id
        """
    ):
        manifest["links"].append(
            {
                "capture_id": link["capture_id"],
                "inventory_row_id": link["row_id"],
                "sheet_or_table": link["sheet_name"],
                "original_row": link["original_row"],
                "tag": link["tag_original"],
                "method": link["method"],
                "review_status": link["review_status"],
                "reason": link["reason"],
                "documented": flags.get(link["row_id"], {}).get("documented", False),
            }
        )
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr("inventory.csv", inventory_csv(conn))
        for capture in captures:
            for photo in conn.execute("SELECT * FROM photos WHERE capture_id = ?", (capture["capture_id"],)):
                if not photo["stored_path"] or not Path(photo["stored_path"]).exists():
                    continue
                suffix = Path(photo["stored_path"]).suffix
                archive.write(photo["stored_path"], f"photos/{capture['capture_id']}/{photo['id']}{suffix}")
    return destination
