"""The project-manager view: sellable assets, groupings, questions, and evidence.

This layer does not price anything. A source row stays a source row. A machine
is one sellable asset, and its components are not listed again beside it.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

from app.logic import ACTOR_USER, equipment_for_match, log_change, parse_json
from app.machines import prefix_label, split_prefix
from app.matching import match_inventory

CATEGORIES = {
    "lab": "ציוד מעבדה",
    "api": "ציוד API",
    "process": "ציוד תהליך / ייצור",
}
CONFIDENCE = {"confirmed": "מאושר", "likely": "סביר", "uncertain": "לא ודאי"}
TASK_STATUS = {
    "open": "פתוח",
    "waiting": "ממתין למידע",
    "answered": "נענה",
    "resolved": "נסגר",
    "deferred": "ממתין למידע",
}
VALUE_KINDS = {
    "purchase": "מחיר רכישה מקורי",
    "replacement": "עלות תחליף",
    "asking": "מחיר מבוקש",
    "comparable": "השוואה לשוק",
    "dealer": "הצעת סוחר",
    "offer": "הצעת קונה",
    "auction": "תוצאת מכרז",
    "other": "אחר",
}
LABELS = ("FUTURE", "HOLD", "SPARE", "NEW PROJECT", "NOT IN USE", "UNDER MAINTENANCE")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def relationship_confidence(machine: dict) -> str:
    if machine.get("status") == "confirmed":
        return "confirmed"
    note = machine.get("note") or ""
    if any(mark in note for mark in ("תג אחר", "יותר משורת", "סתירה")):
        return "uncertain"
    return "likely"


def _category_of_row(row: dict) -> str:
    if (row.get("source_kind") or "") == "lab":
        return "lab"
    blob = " ".join(
        [
            row.get("description") or "",
            row.get("remarks") or "",
            row.get("specs") or "",
        ]
    )
    if re.search(r"\bAPI\b", blob):
        return "api"
    return "process"


def _labels(row: dict) -> list[str]:
    return [str(item) for item in parse_json(row.get("labels_json") or "[]", [])]


def _load(conn: sqlite3.Connection) -> dict:
    machines = [dict(row) for row in conn.execute("SELECT * FROM machines WHERE status != 'dissolved'")]
    members: dict[str, list[dict]] = defaultdict(list)
    covered: set[str] = set()
    for row in conn.execute(
        """
        SELECT * FROM machine_members
        WHERE link_status = 'active'
          AND machine_id IN (SELECT id FROM machines WHERE status != 'dissolved')
        """
    ):
        item = dict(row)
        members[item["machine_id"]].append(item)
        if item["inventory_row_id"]:
            covered.add(item["inventory_row_id"])
    rows = [dict(row) for row in conn.execute("SELECT * FROM inventory_rows WHERE kind = 'equipment'")]
    by_id = {row["id"]: row for row in rows}
    manuals = [dict(row) for row in conn.execute("SELECT * FROM manual_assets ORDER BY created_at")]
    sources = {row["id"]: dict(row) for row in conn.execute("SELECT id, filename, display_name FROM source_files")}
    return {
        "machines": machines,
        "members": members,
        "covered": covered,
        "rows": rows,
        "by_id": by_id,
        "manuals": manuals,
        "sources": sources,
    }


def _source_label(row: dict, sources: dict) -> str:
    source = sources.get(row.get("source_file_id") or "", {})
    name = source.get("display_name") or source.get("filename") or ""
    if row.get("source_kind") == "lab":
        return f"{name} · {row.get('sheet_name') or ''} · שורה {row.get('original_row')}"
    return f"{name} · גיליון {row.get('sheet_name') or ''} · שורה {row.get('original_row')}"


def _machine_category(machine_members: list[dict], by_id: dict) -> str:
    cats = {_category_of_row(by_id[item["inventory_row_id"]]) for item in machine_members if item.get("inventory_row_id") in by_id}
    if "api" in cats:
        return "api"
    if cats == {"lab"}:
        return "lab"
    return "process"


def _brief_machine(machine: dict, machine_members: list[dict], by_id: dict) -> dict:
    parent = next((item for item in machine_members if item["member_role"] == "parent"), None)
    parent_row = by_id.get(parent["inventory_row_id"]) if parent and parent.get("inventory_row_id") else None
    tags = [item["tag_original"] or item["tag_norm"] for item in machine_members]
    confidence = relationship_confidence(machine)
    labels: list[str] = []
    if parent_row:
        labels = _labels(parent_row)
    return {
        "id": machine["id"],
        "kind": "machine",
        "name": machine["name"],
        "tag": parent["tag_original"] if parent else "",
        "tags": tags,
        "category": _machine_category(machine_members, by_id),
        "type_label": prefix_label(parent["tag_norm"]) if parent else "",
        "area": (parent_row or {}).get("listed_area") or "",
        "confidence": confidence,
        "confidence_label": CONFIDENCE[confidence],
        "labels": labels,
        "component_count": len(machine_members),
        "sold_together": machine.get("sold_together") == "confirmed",
    }


def _brief_row(row: dict) -> dict:
    category = _category_of_row(row)
    tag = row.get("tag_original") or ""
    name = (row.get("description") or "").strip() or tag or "פריט בלי שם"
    return {
        "id": row["id"],
        "kind": "item",
        "name": name,
        "tag": tag,
        "tags": [tag] if tag else [],
        "category": category,
        "type_label": prefix_label(row.get("tag_norm") or ""),
        "area": row.get("listed_area") or "",
        "confidence": "",
        "confidence_label": "",
        "labels": _labels(row),
        "component_count": 0,
        "sold_together": False,
    }


def _brief_manual(row: dict) -> dict:
    tag = row.get("tag") or ""
    return {
        "id": row["id"],
        "kind": "manual",
        "name": row["name"],
        "tag": tag,
        "tags": [tag] if tag else [],
        "category": row["category"] if row["category"] in CATEGORIES else "process",
        "type_label": prefix_label(tag),
        "area": "",
        "confidence": "confirmed",
        "confidence_label": CONFIDENCE["confirmed"],
        "labels": [],
        "component_count": 0,
        "sold_together": False,
    }


def catalog(conn: sqlite3.Connection) -> list[dict]:
    data = _load(conn)
    assets = [_brief_machine(machine, data["members"].get(machine["id"], []), data["by_id"]) for machine in data["machines"]]
    assets.extend(_brief_row(row) for row in data["rows"] if row["id"] not in data["covered"])
    assets.extend(_brief_manual(row) for row in data["manuals"])
    for asset in assets:
        asset["category_label"] = CATEGORIES.get(asset["category"], asset["category"])
    return assets


def _matches(asset: dict, query: str, category: str, label: str, attention: str) -> bool:
    if category and asset["category"] != category:
        return False
    if label and label.upper() not in {item.upper() for item in asset["labels"]}:
        return False
    if attention == "uncertain" and asset["confidence"] != "uncertain":
        return False
    if attention == "review" and asset["confidence"] != "uncertain" and not asset["labels"]:
        return False
    if query:
        haystack = " ".join([asset["name"], asset["tag"], " ".join(asset["tags"]), asset["area"], asset["type_label"]]).casefold()
        if query.casefold() not in haystack:
            return False
    return True


def list_assets(conn: sqlite3.Connection, *, q: str = "", category: str = "", label: str = "", attention: str = "", limit: int = 40, offset: int = 0) -> dict:
    items = [asset for asset in catalog(conn) if _matches(asset, q.strip(), category, label.strip(), attention)]
    items.sort(key=lambda asset: ({"uncertain": 0, "likely": 1, "confirmed": 2, "": 3}[asset["confidence"]], asset["tag"] or asset["name"]))
    return {"total": len(items), "assets": items[offset : offset + limit]}


def dashboard(conn: sqlite3.Connection) -> dict:
    assets = catalog(conn)
    by_category = {key: 0 for key in CATEGORIES}
    uncertain = 0
    labeled = 0
    for asset in assets:
        by_category[asset["category"]] = by_category.get(asset["category"], 0) + 1
        if asset["confidence"] == "uncertain":
            uncertain += 1
        if asset["labels"]:
            labeled += 1
    pending = conn.execute("SELECT COUNT(*) AS n FROM project_files WHERE status = 'pending' AND user_locked = 0").fetchone()["n"]
    open_tasks = len(list_tasks(conn)["tasks"])
    return {
        "assets": len(assets),
        "by_category": [{"id": key, "label": CATEGORIES[key], "count": by_category.get(key, 0)} for key in CATEGORIES],
        "needs_review": uncertain + pending,
        "missing_info": labeled,
        "uncertain_relationships": uncertain,
        "pending_files": pending,
        "open_tasks": open_tasks,
        "note": "מכונה מוצגת פעם אחת. רכיביה נשארים בכרטיס ולא נספרים שוב כנכס נפרד. אין בגרסה הזו טווח שווי.",
    }


def _find_asset(conn: sqlite3.Connection, asset_id: str) -> dict | None:
    for asset in catalog(conn):
        if asset["id"] == asset_id:
            return asset
    return None


def _technical(row: dict | None, sources: dict) -> list[dict]:
    if not row:
        return []
    source = _source_label(row, sources)
    fields = [
        ("תיאור", row.get("description") or ""),
        ("יצרן", row.get("manufacturer") or ""),
        ("דגם", row.get("model") or ""),
        ("מספר סידורי", row.get("serial") or ""),
        ("חומר", row.get("material") or ""),
        ("מפרט", row.get("specs") or ""),
        ("הערות", row.get("remarks") or ""),
        ("התקנה", row.get("installation_raw") or ""),
    ]
    return [{"label": label, "value": value, "source": source} for label, value in fields if str(value).strip()]


def _component_public(member: dict, machine: dict, by_id: dict, sources: dict) -> dict:
    row = by_id.get(member.get("inventory_row_id") or "")
    confidence = relationship_confidence(machine)
    return {
        "tag": member.get("tag_original") or member.get("tag_norm") or "",
        "tag_norm": member.get("tag_norm") or "",
        "type_label": member.get("prefix_category") or prefix_label(member.get("tag_norm") or ""),
        "description": (row or {}).get("description") or "",
        "role": "רכיב ראשי" if member.get("member_role") == "parent" else "רכיב",
        "confidence": confidence,
        "confidence_label": CONFIDENCE[confidence],
        "explanation": member.get("evidence") or "",
        "source": _source_label(row, sources) if row else "אין שורת מקור. לא הומצאה שורה.",
        "row_id": member.get("inventory_row_id") or "",
        "in_source": bool(row),
    }


def _files_for(conn: sqlite3.Connection, asset_id: str) -> list[dict]:
    found = []
    for row in conn.execute(
        """
        SELECT f.*, l.confidence AS link_confidence, l.reason, l.user_locked AS link_locked, l.tag_norm
        FROM project_file_links l
        JOIN project_files f ON f.id = l.file_id
        WHERE l.asset_id = ? AND f.status = 'filed'
        ORDER BY f.created_at DESC
        """,
        (asset_id,),
    ):
        item = dict(row)
        found.append(
            {
                "id": item["id"],
                "name": item["original_name"] or "טקסט",
                "doc_type": item["doc_type"] or "",
                "confidence": item["link_confidence"] or item["confidence"] or "",
                "confidence_label": CONFIDENCE.get(item["link_confidence"] or item["confidence"] or "", ""),
                "reason": item["reason"] or "",
                "summary": item["summary"] or "",
                "user_locked": bool(item["user_locked"]),
                "tag": item["tag_norm"] or "",
            }
        )
    return found


def _evidence_for(conn: sqlite3.Connection, asset_id: str) -> list[dict]:
    items = []
    for row in conn.execute("SELECT * FROM valuation_notes WHERE asset_id = ? ORDER BY created_at DESC", (asset_id,)):
        item = dict(row)
        item["value_label"] = VALUE_KINDS.get(item.get("value_kind") or "", item.get("value_kind") or "")
        items.append(item)
    return items


def _history(conn: sqlite3.Connection, asset_id: str) -> list[dict]:
    items = []
    for row in conn.execute(
        """
        SELECT actor, action, prior_json, new_json, created_at
        FROM change_log WHERE entity_id = ? ORDER BY id DESC LIMIT 12
        """,
        (asset_id,),
    ):
        items.append(
            {
                "actor": row["actor"],
                "action": row["action"],
                "prior": parse_json(row["prior_json"], None),
                "new": parse_json(row["new_json"], None),
                "at": row["created_at"],
            }
        )
    return items


def _card_questions(asset: dict, machine: dict | None, technical: list[dict]) -> list[str]:
    questions = []
    if asset["labels"]:
        questions.append("תווית מקור: " + ", ".join(asset["labels"]) + ". הפריט נשאר במלאי. זו שאלה, לא הוצאה מהמכירה.")
    if asset["confidence"] == "uncertain":
        questions.append("הקשר בין הרכיבים לא ודאי ודורש אישור. ההסבר מופיע ליד כל רכיב.")
    if asset["kind"] == "item" and asset["category"] == "lab":
        present = {item["label"] for item in technical}
        missing = [label for label in ("יצרן", "דגם", "מספר סידורי") if label not in present]
        if missing:
            questions.append("חסר בכרטיס: " + ", ".join(missing) + ". לא הומצא ערך.")
    if machine and machine.get("sold_together") == "confirmed":
        questions.append("המכונה אושרה למכירה כיחידה אחת. הרכיבים לא נספרים כנכסים נפרדים.")
    return questions


def asset_detail(conn: sqlite3.Connection, asset_id: str) -> dict:
    data = _load(conn)
    asset = _find_asset(conn, asset_id)
    if not asset:
        raise LookupError("הנכס לא נמצא.")
    machine = next((item for item in data["machines"] if item["id"] == asset_id), None)
    manual = next((item for item in data["manuals"] if item["id"] == asset_id), None)
    row = data["by_id"].get(asset_id)
    members = data["members"].get(asset_id, []) if machine else []
    parent = next((item for item in members if item["member_role"] == "parent"), None)
    tech_row = data["by_id"].get(parent["inventory_row_id"]) if parent and parent.get("inventory_row_id") else row
    technical = _technical(tech_row, data["sources"])
    components = [_component_public(item, machine, data["by_id"], data["sources"]) for item in members] if machine else []
    return {
        **asset,
        "category_label": CATEGORIES.get(asset["category"], asset["category"]),
        "note": (machine or {}).get("note") or (manual or {}).get("note") or "",
        "technical": technical,
        "components": components,
        "files": _files_for(conn, asset_id),
        "questions": _card_questions(asset, machine, technical),
        "evidence": _evidence_for(conn, asset_id),
        "history": _history(conn, asset_id),
        "source_row": bool(row),
    }


def ensure_questions(conn: sqlite3.Connection, created_at: str) -> None:
    from app.importer import upsert_reviews

    reviews = []
    labeled: dict[str, list[str]] = defaultdict(list)
    api_rows = 0
    for row in conn.execute("SELECT id, labels_json, description, remarks, specs, source_kind FROM inventory_rows WHERE kind = 'equipment'"):
        item = dict(row)
        if _category_of_row(item) == "api":
            api_rows += 1
        for label in _labels(item):
            key = label.upper()
            if key in LABELS or key.replace(" ", "") in {item.replace(" ", "") for item in LABELS}:
                labeled[key].append(item["id"])
    if api_rows == 0:
        reviews.append(
            {
                "kind": "category",
                "queue": "product",
                "dedupe_key": "product:category:api",
                "priority": "medium",
                "question": "בקובצי המקור אין סימון API מול תהליך. ציוד המעבדה מוצג כציוד מעבדה, ושורות המפעל כציוד תהליך / ייצור. לא חולק ציוד API. אם יש ציוד API, סמנו אותו.",
                "row_ids": [],
                "payload": {"category": "api"},
            }
        )
    for label, row_ids in labeled.items():
        reviews.append(
            {
                "kind": "status_label",
                "queue": "product",
                "dedupe_key": f"product:label:{label}",
                "priority": "medium",
                "question": f"{len(row_ids)} פריטים מסומנים {label}. הם נשארים במלאי עם התווית המקורית. זו שאלה, לא הוצאה מהמכירה.",
                "row_ids": row_ids[:400],
                "payload": {"label": label, "count": len(row_ids)},
            }
        )
    upsert_reviews(conn, reviews, created_at)


def list_uploads(conn: sqlite3.Connection) -> dict:
    items = []
    for row in conn.execute("SELECT id FROM project_files ORDER BY created_at DESC LIMIT 30"):
        items.append(_public_file(conn, row["id"]))
    return {"files": items}


def list_tasks(conn: sqlite3.Connection) -> dict:
    data = _load(conn)
    confidence_by_machine = {machine["id"]: relationship_confidence(machine) for machine in data["machines"]}
    tasks = []
    for row in conn.execute(
        """
        SELECT * FROM review_items
        WHERE status IN ('open', 'waiting', 'answered', 'deferred')
        ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, created_at
        """
    ):
        item = dict(row)
        payload = parse_json(item["payload_json"], {})
        if item["queue"] in {"valuation", "visit"} or item["kind"] in {"offer_gap", "classification", "conflict", "asset_match"}:
            continue
        if item["kind"] == "grouping":
            machine_id = payload.get("machine_id") or ""
            if confidence_by_machine.get(machine_id) != "uncertain":
                continue
        asset_id = payload.get("machine_id") or ""
        if not asset_id:
            row_ids = parse_json(item["row_ids_json"], [])
            asset_id = row_ids[0] if row_ids else ""
        tasks.append(
            {
                "id": item["id"],
                "asset_id": asset_id,
                "question": item["question"],
                "category": item["kind"],
                "priority": item["priority"],
                "status": "waiting" if item["status"] == "deferred" else item["status"],
                "status_label": TASK_STATUS.get(item["status"], item["status"]),
                "note": item["resolution"] or "",
                "responsible": "",
                "due": "",
            }
        )
    for row in conn.execute("SELECT * FROM project_files WHERE status = 'pending' AND user_locked = 0 ORDER BY created_at DESC"):
        tasks.append(
            {
                "id": row["id"],
                "asset_id": "",
                "question": row["summary"] or "קובץ ממתין לשיוך.",
                "category": "file",
                "priority": "high" if row["confidence"] == "uncertain" else "medium",
                "status": "open",
                "status_label": "פתוח",
                "note": row["original_name"] or "",
                "responsible": "",
                "due": "",
                "file_id": row["id"],
            }
        )
    return {"tasks": tasks}


def update_task(conn: sqlite3.Connection, task_id: str, body: dict, created_at: str) -> dict:
    status = body.get("status") or ""
    if status not in {"open", "waiting", "answered", "resolved"}:
        raise ValueError("סטטוס לא מוכר.")
    note = (body.get("note") or "").strip()
    stored = "deferred" if status == "waiting" else status
    row = conn.execute("SELECT id, status, resolution FROM review_items WHERE id = ?", (task_id,)).fetchone()
    if not row:
        raise LookupError("השאלה לא נמצאה.")
    conn.execute(
        "UPDATE review_items SET status = ?, resolution = ?, updated_at = ? WHERE id = ?",
        (stored, note or row["resolution"], created_at, task_id),
    )
    log_change(conn, actor=ACTOR_USER, action="task", entity_type="review", entity_id=task_id, prior={"status": row["status"]}, new={"status": status, "note": note}, created_at=created_at)
    return {"id": task_id, "status": status, "status_label": TASK_STATUS[status]}


def _doc_type(name: str, text: str, suffix: str) -> str:
    haystack = f"{name}\n{text}".casefold()
    pairs = (
        ("הצעת מחיר", ("quotation", "quote", "הצעת מחיר", "הצעת רכש")),
        ("ראיית שווי", ("appraisal", "auction", "market value", "שומה", "שווי שוק")),
        ("תעודה", ("certificate", "תעודה")),
        ("מדריך", ("manual", "instruction book")),
        ("שרטוט", ("drawing", "p&id", "pid", "שרטוט")),
        ("מידע על ציוד", ("specification", "nameplate", "condition report", "maintenance", "מפרט", "דוח")),
    )
    for label, phrases in pairs:
        if any(phrase in haystack for phrase in phrases):
            return label
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff", ".heic"}:
        return "צילום"
    return "מסמך"


def _suggestions(conn: sqlite3.Connection, text: str) -> tuple[str, list[dict], str]:
    from app.logic import _bounded_keys

    rows = equipment_for_match(conn)
    data = _load(conn)
    keys = {row["tag_norm"]: [] for row in rows if row.get("tag_norm")}
    tag_to_asset: dict[str, str] = {}
    for machine in data["machines"]:
        for member in data["members"].get(machine["id"], []):
            if member.get("tag_norm"):
                tag_to_asset.setdefault(member["tag_norm"], machine["id"])
    for row in data["rows"]:
        if row["id"] in data["covered"] or not row.get("tag_norm"):
            continue
        tag_to_asset.setdefault(row["tag_norm"], row["id"])
    found = []
    ambiguous = []
    seen = set()
    for token in _bounded_keys(text or "", keys):
        if token in seen:
            continue
        seen.add(token)
        result = match_inventory(rows, tag=token)
        if result["mode"] == "auto" and result["links"]:
            tag = result["links"][0].get("tag") or token
            norm = token.upper()
            asset_id = tag_to_asset.get(norm) or result["links"][0]["row_id"]
            found.append(
                {
                    "asset_id": asset_id,
                    "tag": tag,
                    "tag_norm": norm,
                    "reason": f"התג {tag} מופיע במסמך ומתאים לרשומה אחת. הקידומת מציינת סוג ציוד בלבד.",
                }
            )
        elif any(row["tag_norm"] == token.upper() for row in rows):
            ambiguous.append(token.upper())
    if ambiguous:
        return "uncertain", found, "יש יותר מרשומה אחת עבור " + ", ".join(ambiguous) + ". לא שויך עד שתבחרו."
    if not found:
        return "uncertain", [], "לא נמצא תג שמתאים לרשומה אחת. הקובץ נשמר ולא שויך."
    if len(found) == 1:
        return "likely", found, found[0]["reason"] + " השיוך ייקבע רק אחרי אישור."
    return "likely", found, "המסמך מזכיר כמה תגים ברורים: " + ", ".join(item["tag"] for item in found) + ". אפשר לאשר את כולם."


def _public_file(conn: sqlite3.Connection, file_id: str) -> dict:
    row = conn.execute("SELECT * FROM project_files WHERE id = ?", (file_id,)).fetchone()
    if not row:
        raise LookupError("הקובץ לא נמצא.")
    links = [dict(item) for item in conn.execute("SELECT * FROM project_file_links WHERE file_id = ?", (file_id,))]
    return {
        "id": row["id"],
        "name": row["original_name"] or "",
        "doc_type": row["doc_type"] or "",
        "confidence": row["confidence"] or "uncertain",
        "confidence_label": CONFIDENCE.get(row["confidence"] or "", ""),
        "summary": row["summary"] or "",
        "status": row["status"],
        "user_locked": bool(row["user_locked"]),
        "duplicate": False,
        "links": links,
        "text": (row["extracted_text"] or "")[:700],
    }


def _store_links(conn, file_id: str, suggestions: list[dict], confidence: str, locked: bool) -> None:
    if locked:
        return
    conn.execute("DELETE FROM project_file_links WHERE file_id = ? AND user_locked = 0", (file_id,))
    for item in suggestions:
        conn.execute(
            """
            INSERT OR IGNORE INTO project_file_links (id, file_id, asset_id, tag_norm, confidence, reason, user_locked)
            VALUES (?, ?, ?, ?, ?, ?, 0)
            """,
            ("FL-" + _digest(f"{file_id}|{item['asset_id']}"), file_id, item["asset_id"], item.get("tag_norm") or "", confidence, item.get("reason") or ""),
        )


def receive_upload(conn: sqlite3.Connection, *, name: str, data: bytes, note: str, photo_dir: Path, created_at: str) -> dict:
    from app.intake import extract_document

    note = (note or "").strip()
    if not data and not note:
        raise ValueError("צריך קובץ או טקסט.")
    payload = data or note.encode()
    digest = hashlib.sha256(payload).hexdigest()
    existing = conn.execute("SELECT id, user_locked FROM project_files WHERE sha256 = ?", (digest,)).fetchone()
    if existing:
        public = _public_file(conn, existing["id"])
        public["duplicate"] = True
        public["summary"] = "הקובץ כבר נשמר. לא נוצר נכס, קשר או שאלה נוספים."
        return public
    suffix = Path(name or "note.txt").suffix.lower()
    file_id = "FIL-" + digest[:16]
    photo_dir.mkdir(parents=True, exist_ok=True)
    stored = photo_dir / f"{file_id}{suffix or '.txt'}"
    if data:
        stored.write_bytes(data)
        extracted = extract_document(stored)
        text = extracted.get("text") or ""
        unread = extracted.get("note") or ""
    else:
        stored.write_text(note, encoding="utf-8")
        text = note
        unread = ""
    if note and note not in text:
        text = (text + "\n" + note).strip()
    doc_type = _doc_type(name or "", text, suffix)
    confidence, suggestions, reason = _suggestions(conn, text)
    if unread and confidence != "uncertain":
        reason = unread + " " + reason
    elif unread and not text.strip():
        confidence = "uncertain"
        reason = unread
    summary = f"זה {doc_type}. {reason}"
    if confidence == "uncertain":
        summary += " לא שויך עד שתאשרו או תבחרו נכס."
    conn.execute(
        """
        INSERT INTO project_files (
          id, sha256, original_name, stored_path, media_kind, extracted_text, note_text,
          doc_type, confidence, summary, status, user_locked, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?)
        """,
        (file_id, digest, name or "טקסט", str(stored), suffix.lstrip(".") or "text", text[:30000], note, doc_type, confidence, summary, created_at, created_at),
    )
    _store_links(conn, file_id, suggestions, confidence, False)
    log_change(conn, actor="קליטה", action="upload", entity_type="file", entity_id=file_id, prior=None, new={"name": name, "confidence": confidence, "suggestions": [item["tag"] for item in suggestions]}, created_at=created_at)
    public = _public_file(conn, file_id)
    public["suggestions"] = suggestions
    return public


def confirm_upload(conn: sqlite3.Connection, file_id: str, body: dict, created_at: str) -> dict:
    row = conn.execute("SELECT * FROM project_files WHERE id = ?", (file_id,)).fetchone()
    if not row:
        raise LookupError("הקובץ לא נמצא.")
    action = body.get("action") or "confirm"
    if action == "create":
        name = (body.get("name") or "").strip()
        if len(name) < 2:
            raise ValueError("צריך שם לנכס. לא נוצר נכס בלי שם שכתבתם.")
        category = body.get("category") if body.get("category") in CATEGORIES else "process"
        asset_id = "MAN-" + _digest(f"{file_id}|{name}|{created_at}")
        conn.execute(
            "INSERT INTO manual_assets (id, name, category, tag, note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (asset_id, name[:240], category, (body.get("tag") or "").strip(), "נוצר מהעלאה, לפי שם שהוקלד.", created_at),
        )
        asset_ids = [asset_id]
    else:
        asset_ids = [item for item in (body.get("asset_ids") or []) if item]
        if action == "confirm" and not asset_ids:
            asset_ids = [item["asset_id"] for item in conn.execute("SELECT asset_id FROM project_file_links WHERE file_id = ?", (file_id,))]
        if not asset_ids:
            raise ValueError("צריך לבחור נכס, או ליצור נכס בשם שתכתבו.")
        for asset_id in asset_ids:
            if not _find_asset(conn, asset_id) and action != "create":
                raise ValueError("הנכס שנבחר לא נמצא. לא נוצר נכס חדש במקומו.")
    prior = [item["asset_id"] for item in conn.execute("SELECT asset_id FROM project_file_links WHERE file_id = ?", (file_id,))]
    conn.execute("DELETE FROM project_file_links WHERE file_id = ?", (file_id,))
    for asset_id in asset_ids:
        conn.execute(
            """
            INSERT INTO project_file_links (id, file_id, asset_id, tag_norm, confidence, reason, user_locked)
            VALUES (?, ?, ?, ?, 'confirmed', ?, 1)
            """,
            ("FL-" + _digest(f"{file_id}|{asset_id}|user"), file_id, asset_id, (body.get("tag") or "").strip().upper(), "אושר על ידי המשתמש."),
        )
    conn.execute(
        "UPDATE project_files SET status = 'filed', confidence = 'confirmed', user_locked = 1, updated_at = ?, summary = ? WHERE id = ?",
        (created_at, "השיוך אושר על ידי המשתמש ולא יוחלף בקליטה הבאה.", file_id),
    )
    log_change(conn, actor=ACTOR_USER, action="file_link", entity_type="file", entity_id=file_id, prior={"assets": prior}, new={"assets": asset_ids}, created_at=created_at)
    for asset_id in asset_ids:
        log_change(conn, actor=ACTOR_USER, action="file_link", entity_type="asset", entity_id=asset_id, prior=None, new={"file_id": file_id}, created_at=created_at)
    return _public_file(conn, file_id)


def set_relationship(conn: sqlite3.Connection, asset_id: str, body: dict, created_at: str) -> dict:
    from app.machines import machine_action

    machine = conn.execute("SELECT * FROM machines WHERE id = ?", (asset_id,)).fetchone()
    if not machine:
        raise LookupError("אין מכונה בכרטיס הזה.")
    action = body.get("action")
    if action == "confirm":
        machine_action(conn, asset_id, {"action": "confirm"}, created_at)
        log_change(conn, actor=ACTOR_USER, action="relationship", entity_type="asset", entity_id=asset_id, prior={"status": machine["status"]}, new={"status": "confirmed"}, created_at=created_at)
    elif action == "reject":
        tags = [str(tag).strip().upper() for tag in (body.get("tag_norms") or []) if str(tag).strip()]
        machine_action(conn, asset_id, {"action": "split", "tag_norms": tags}, created_at)
        log_change(conn, actor=ACTOR_USER, action="relationship", entity_type="asset", entity_id=asset_id, prior={"tags": tags}, new={"removed": tags}, created_at=created_at)
    else:
        raise ValueError("אפשר לאשר את הקשר או להוציא רכיב. שורת המקור לא נמחקת.")
    return asset_detail(conn, asset_id)


def add_evidence(conn: sqlite3.Connection, asset_id: str, body: dict, created_at: str) -> dict:
    if not _find_asset(conn, asset_id):
        raise LookupError("הנכס לא נמצא.")
    kind = body.get("value_kind") or "other"
    if kind not in VALUE_KINDS:
        raise ValueError("סוג הראיה לא מוכר.")
    price = (body.get("price_text") or "").strip()
    source = (body.get("source") or "").strip()
    description = (body.get("description") or "").strip()
    if not any([price, source, description, (body.get("body") or "").strip()]):
        raise ValueError("צריך מקור, תיאור או סכום שכתובים בראיה. לא נוצר מחיר.")
    evidence_id = "EV-" + _digest(f"{asset_id}|{created_at}|{price}|{source}|{description}")
    conn.execute(
        """
        INSERT INTO valuation_notes (
          id, asset_id, source, evidence_date, currency, price_text, description, manufacturer,
          model, year, condition_text, location, value_kind, body, file_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            evidence_id,
            asset_id,
            source,
            (body.get("evidence_date") or "").strip(),
            (body.get("currency") or "").strip().upper(),
            price,
            description,
            (body.get("manufacturer") or "").strip(),
            (body.get("model") or "").strip(),
            (body.get("year") or "").strip(),
            (body.get("condition") or "").strip(),
            (body.get("location") or "").strip(),
            kind,
            (body.get("body") or "").strip(),
            (body.get("file_id") or "").strip(),
            created_at,
        ),
    )
    log_change(conn, actor=ACTOR_USER, action="evidence", entity_type="asset", entity_id=asset_id, prior=None, new={"id": evidence_id, "price_text": price, "value_kind": kind}, created_at=created_at)
    return {"id": evidence_id, "stored": True}
