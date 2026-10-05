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
    component_rows = [dict(row) for row in conn.execute("SELECT * FROM asset_components")]
    components: dict[str, list[dict]] = defaultdict(list)
    for item in component_rows:
        components[item["asset_id"]].append(item)
        if item["link_status"] == "active" and item.get("inventory_row_id"):
            covered.add(item["inventory_row_id"])
    rows = [dict(row) for row in conn.execute("SELECT * FROM inventory_rows WHERE kind = 'equipment'")]
    by_id = {row["id"]: row for row in rows}
    manuals = [dict(row) for row in conn.execute("SELECT * FROM manual_assets ORDER BY created_at")]
    sources = {row["id"]: dict(row) for row in conn.execute("SELECT id, filename, display_name FROM source_files")}
    return {
        "machines": machines,
        "members": members,
        "components": components,
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


def _component_sort_key(tag: str, role: str) -> tuple:
    primary = 0 if role == "רכיב ראשי" else 1
    text = tag or ""
    if "/" in text:
        head, tail = text.split("/", 1)
        if tail.isdigit():
            return (primary, head, 0, int(tail), "")
        return (primary, head, 1, 0, tail)
    return (primary, text, 0, 0, "")


def _merged_components(data: dict, asset_id: str, machine: dict | None) -> list[dict]:
    items: list[dict] = []
    seen_rows: set[str] = set()
    seen_tags: set[str] = set()
    if machine:
        for member in data["members"].get(asset_id, []):
            public = _component_public(member, machine, data["by_id"], data["sources"])
            public["id"] = member["id"]
            public["name"] = ""
            public["notes"] = ""
            public["origin"] = "machine"
            public["removable"] = member.get("member_role") != "parent"
            items.append(public)
            if member.get("inventory_row_id"):
                seen_rows.add(member["inventory_row_id"])
            if member.get("tag_norm"):
                seen_tags.add(member["tag_norm"])
    for comp in data["components"].get(asset_id, []):
        if comp.get("link_status") != "active":
            continue
        row_id = comp.get("inventory_row_id") or ""
        tag_norm = comp.get("tag_norm") or ""
        if row_id and row_id in seen_rows:
            continue
        if tag_norm and tag_norm in seen_tags and row_id:
            continue
        items.append(_component_from_link(comp, data["by_id"], data["sources"]))
    items.sort(key=lambda item: _component_sort_key(item.get("tag") or item.get("name") or "", item.get("role") or ""))
    return items


def catalog(conn: sqlite3.Connection) -> list[dict]:
    data = _load(conn)
    assets = []
    for machine in data["machines"]:
        brief = _brief_machine(machine, data["members"].get(machine["id"], []), data["by_id"])
        merged = _merged_components(data, machine["id"], machine)
        brief["component_count"] = len(merged)
        for item in merged:
            if item.get("tag") and item["tag"] not in brief["tags"]:
                brief["tags"].append(item["tag"])
        assets.append(brief)
    for row in data["rows"]:
        if row["id"] in data["covered"]:
            continue
        brief = _brief_row(row)
        merged = _merged_components(data, row["id"], None)
        brief["component_count"] = len(merged)
        for item in merged:
            if item.get("tag") and item["tag"] not in brief["tags"]:
                brief["tags"].append(item["tag"])
        assets.append(brief)
    for row in data["manuals"]:
        brief = _brief_manual(row)
        merged = _merged_components(data, row["id"], None)
        brief["component_count"] = len(merged)
        for item in merged:
            if item.get("tag") and item["tag"] not in brief["tags"]:
                brief["tags"].append(item["tag"])
        assets.append(brief)
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


def _component_from_link(comp: dict, by_id: dict, sources: dict) -> dict:
    row = by_id.get(comp.get("inventory_row_id") or "")
    tag = comp.get("tag_original") or (row or {}).get("tag_original") or comp.get("tag_norm") or ""
    description = (comp.get("description") or "").strip() or ((row or {}).get("description") or "")
    confidence = "confirmed"
    return {
        "id": comp["id"],
        "tag": tag,
        "tag_norm": comp.get("tag_norm") or "",
        "name": comp.get("name") or "",
        "type_label": prefix_label(comp.get("tag_norm") or tag),
        "description": description,
        "notes": comp.get("notes") or "",
        "role": "רכיב",
        "confidence": confidence,
        "confidence_label": CONFIDENCE[confidence],
        "explanation": comp.get("explanation") or "",
        "source": _source_label(row, sources) if row else "אין שורת מקור. הרכיב נוצר מהטופס.",
        "row_id": comp.get("inventory_row_id") or "",
        "in_source": bool(row),
        "removable": True,
        "origin": comp.get("origin") or "user",
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
    components = _merged_components(data, asset_id, machine)
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
    from app.logic import now_iso

    reconcile_known_sources(conn, now_iso())
    items = []
    for row in conn.execute("SELECT id FROM project_files WHERE status != 'dismissed' ORDER BY created_at DESC LIMIT 30"):
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
    links = []
    if row["status"] not in {"loaded", "imported", "dismissed"}:
        links = [dict(item) for item in conn.execute("SELECT * FROM project_file_links WHERE file_id = ?", (file_id,))]
    added_tags = []
    note_text = row["note_text"] or ""
    if note_text.startswith("TAGS:"):
        added_tags = [item for item in note_text[5:].split("|") if item]
    source_name = ""
    if row["matched_source_id"]:
        source = conn.execute("SELECT display_name, filename FROM source_files WHERE id = ?", (row["matched_source_id"],)).fetchone()
        if source:
            source_name = source["display_name"] or source["filename"] or ""
    show_text = row["status"] not in {"loaded", "imported", "dismissed"}
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
        "already_loaded": row["status"] == "loaded",
        "imported_list": row["status"] == "imported",
        "source_name": source_name,
        "inventory_category": row["matched_category"] or "",
        "added_tags": added_tags,
        "links": links,
        "text": (row["extracted_text"] or "")[:700] if show_text else "",
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


FIELD_LABELS = {
    "description": "תיאור",
    "manufacturer": "יצרן",
    "model": "דגם",
    "serial": "מספר סידורי",
    "installation_raw": "התקנה",
}


def _category_for_kind(kind: str) -> str:
    return "lab" if (kind or "").startswith("lab") else "process"


def _source_category_label(category: str) -> str:
    return "ציוד מעבדה" if category == "lab" else "ציוד תהליך / ייצור"


def _already_loaded_summary(source_name: str, filename: str) -> str:
    label = source_name or filename or "קובץ שכבר נטען"
    extra = f" ({filename})" if filename and filename not in label else ""
    return f"הקובץ הזה כבר במלאי. הוא זהה ל{label}{extra}. לא נוצרו נכסים חדשים ולא נפתחו שיוכים."


def _save_project_file(
    conn: sqlite3.Connection,
    *,
    file_id: str,
    digest: str,
    name: str,
    stored: str,
    suffix: str,
    text: str,
    note: str,
    doc_type: str,
    confidence: str,
    summary: str,
    status: str,
    source_id: str,
    category: str,
    created_at: str,
) -> None:
    existing = conn.execute("SELECT id FROM project_files WHERE id = ? OR sha256 = ?", (file_id, digest)).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE project_files
            SET original_name = ?, stored_path = ?, media_kind = ?, extracted_text = ?, note_text = ?,
                doc_type = ?, confidence = ?, summary = ?, status = ?, user_locked = 0,
                matched_source_id = ?, matched_category = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                name or "טקסט",
                stored,
                suffix.lstrip(".") or "text",
                (text or "")[:30000],
                note,
                doc_type,
                confidence,
                summary,
                status,
                source_id,
                category,
                created_at,
                existing["id"],
            ),
        )
        conn.execute("DELETE FROM project_file_links WHERE file_id = ?", (existing["id"],))
        return
    conn.execute(
        """
        INSERT INTO project_files (
          id, sha256, original_name, stored_path, media_kind, extracted_text, note_text,
          doc_type, confidence, summary, status, user_locked, matched_source_id, matched_category,
          created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
        """,
        (
            file_id,
            digest,
            name or "טקסט",
            stored,
            suffix.lstrip(".") or "text",
            (text or "")[:30000],
            note,
            doc_type,
            confidence,
            summary,
            status,
            source_id,
            category,
            created_at,
            created_at,
        ),
    )


def _present_already_loaded(conn: sqlite3.Connection, *, digest: str, name: str, stored: Path, suffix: str, source: sqlite3.Row, created_at: str, duplicate: bool) -> dict:
    file_id = "FIL-" + digest[:16]
    category = _category_for_kind(source["kind"])
    summary = _already_loaded_summary(source["display_name"] or "", source["filename"] or "")
    _save_project_file(
        conn,
        file_id=file_id,
        digest=digest,
        name=name,
        stored=str(stored),
        suffix=suffix,
        text="",
        note="",
        doc_type="רשימת מלאי",
        confidence="confirmed",
        summary=summary,
        status="loaded",
        source_id=source["id"],
        category=category,
        created_at=created_at,
    )
    public = _public_file(conn, file_id)
    public["duplicate"] = duplicate
    public["already_loaded"] = True
    return public


def reconcile_known_sources(conn: sqlite3.Connection, created_at: str) -> None:
    rows = conn.execute(
        """
        SELECT p.sha256, p.original_name, p.stored_path, s.id AS source_id
        FROM project_files p
        JOIN source_files s ON s.sha256 = p.sha256
        WHERE p.status = 'pending'
        """
    ).fetchall()
    for row in rows:
        source = conn.execute("SELECT * FROM source_files WHERE id = ?", (row["source_id"],)).fetchone()
        stored = Path(row["stored_path"] or "")
        suffix = stored.suffix or ".docx"
        _present_already_loaded(
            conn,
            digest=row["sha256"],
            name=row["original_name"] or source["filename"],
            stored=stored,
            suffix=suffix,
            source=source,
            created_at=created_at,
            duplicate=False,
        )


def _norm_field(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _matching_rows(conn: sqlite3.Connection, tag_norm: str) -> list[sqlite3.Row]:
    if not tag_norm:
        return []
    return conn.execute(
        "SELECT * FROM inventory_rows WHERE kind = 'equipment' AND tag_norm = ?",
        (tag_norm,),
    ).fetchall()


def _insert_source_row(conn: sqlite3.Connection, source_id: str, row: dict) -> None:
    conn.execute(
        """
        INSERT INTO inventory_rows (
          id, source_file_id, kind, sheet_name, original_row, identity_area, listed_area,
          tag_original, tag_norm, description, manufacturer, model, serial, serial_norm,
          quantity, remarks, specs, material, system_number, efd, installation_raw,
          labels_json, raw_json, duplicate_group, source_kind
        ) VALUES (
          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            row["id"],
            source_id,
            row["kind"],
            row["sheet_name"],
            row["original_row"],
            row["identity_area"],
            row["listed_area"],
            row["tag_original"],
            row["tag_norm"],
            row["description"],
            row["manufacturer"],
            row["model"],
            row["serial"],
            row["serial_norm"],
            row["quantity"],
            row["remarks"],
            row["specs"],
            row["material"],
            row["system_number"],
            row["efd"],
            row["installation_raw"],
            json.dumps(row["labels"], ensure_ascii=False),
            json.dumps(row["raw"], ensure_ascii=False),
            row["duplicate_group"],
            row["source_kind"],
        ),
    )


def _remember_source(conn: sqlite3.Connection, *, digest: str, name: str, kind: str, created_at: str) -> str:
    source_id = "FILE-" + digest[:12]
    existing = conn.execute("SELECT id FROM source_files WHERE sha256 = ?", (digest,)).fetchone()
    if existing:
        return existing["id"]
    stored_kind = "lab-upload" if kind == "lab" else "plant-upload"
    conn.execute(
        """
        INSERT INTO source_files (id, kind, filename, display_name, sha256, imported_at, mapping_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (source_id, stored_kind, name or source_id, name or source_id, digest, created_at, "{}"),
    )
    return source_id


def _apply_inventory_list(conn: sqlite3.Connection, parsed: dict, *, digest: str, name: str, created_at: str) -> dict:
    from app.importer import upsert_reviews, _build_rows

    built = _build_rows(parsed["records"], parsed["mapping"], digest, parsed["kind"])
    equipment = [row for row in built if row["kind"] == "equipment" and (row.get("tag_norm") or row.get("description"))]
    added: list[str] = []
    enriched = 0
    conflicts: list[dict] = []
    pending_rows: list[dict] = []
    seen_new: set[str] = set()
    for row in equipment:
        tag_norm = row.get("tag_norm") or ""
        if tag_norm and tag_norm in seen_new:
            conflicts.append(
                {
                    "kind": "field_conflict",
                    "queue": "product",
                    "dedupe_key": f"upload:file-tag:{digest}:{tag_norm}",
                    "priority": "medium",
                    "question": f"התג {row['tag_original'] or tag_norm} מופיע יותר מפעם אחת ברשימה שהועלתה. לא נוצרה שורה נוספת עבור החזרה.",
                    "row_ids": [],
                    "payload": {"tag": row["tag_original"] or tag_norm},
                }
            )
            continue
        matches = _matching_rows(conn, tag_norm)
        if row.get("tag_norm") and len(matches) > 1:
            conflicts.append(
                {
                    "kind": "field_conflict",
                    "queue": "product",
                    "dedupe_key": f"upload:tag:{row['tag_norm']}",
                    "priority": "medium",
                    "question": f"לתג {row['tag_original'] or row['tag_norm']} יש יותר מרשומה אחת במלאי. הרשימה שהועלתה לא יצרה שורה נוספת ולא שינתה ערכים.",
                    "row_ids": [item["id"] for item in matches],
                    "payload": {"tag": row["tag_original"] or row["tag_norm"]},
                }
            )
            continue
        if len(matches) == 1:
            current = matches[0]
            updates: dict[str, str] = {}
            for field, label in FIELD_LABELS.items():
                new_value = _norm_field(row.get(field) or "")
                old_value = _norm_field(current[field] or "")
                if not new_value or new_value.casefold() == old_value.casefold():
                    continue
                if not old_value:
                    updates[field] = row.get(field) or ""
                    enriched += 1
                    continue
                conflicts.append(
                    {
                        "kind": "field_conflict",
                        "queue": "product",
                        "dedupe_key": f"upload:field:{current['id']}:{field}",
                        "priority": "medium",
                        "question": f"לתג {current['tag_original'] or current['tag_norm']} השדה {label} ברשימה שהועלתה הוא {new_value}, ובמלאי רשום {old_value}. הערך לא הוחלף.",
                        "row_ids": [current["id"]],
                        "payload": {"tag": current["tag_original"] or "", "field": field, "uploaded": new_value, "existing": old_value},
                    }
                )
            if updates:
                if "serial" in updates:
                    from app.importer import norm_id, two_serials

                    serial = updates["serial"]
                    updates["serial_norm"] = "" if two_serials(serial) else norm_id(serial)
                assignments = ", ".join(f"{field} = ?" for field in updates)
                conn.execute(
                    f"UPDATE inventory_rows SET {assignments} WHERE id = ?",
                    (*updates.values(), current["id"]),
                )
            continue
        if tag_norm:
            seen_new.add(tag_norm)
        pending_rows.append(row)
        added.append(row.get("tag_original") or row.get("description") or row["id"])
    source_id = ""
    if pending_rows:
        source_id = _remember_source(conn, digest=digest, name=name, kind=parsed["kind"], created_at=created_at)
        for row in pending_rows:
            _insert_source_row(conn, source_id, row)
        ensure_slash_components(conn, created_at)
    if conflicts:
        upsert_reviews(conn, conflicts, created_at)
    category = _category_for_kind(parsed["kind"])
    return {
        "added": added,
        "enriched": enriched,
        "conflicts": len(conflicts),
        "source_id": source_id,
        "category": category,
        "equipment": len(equipment),
    }


def _list_summary(result: dict, source_name: str, filename: str) -> tuple[str, str]:
    added = result["added"]
    category = _source_category_label(result["category"])
    if not result["equipment"]:
        return "imported", "הרשימה נקראה ואין בה שורות ציוד. לא נוצרו נכסים ולא שיוכים."
    if not added and not result["enriched"] and not result["conflicts"]:
        return "loaded", _already_loaded_summary(source_name, filename)
    parts = []
    if added:
        shown = ", ".join(added[:8])
        more = f" ועוד {len(added) - 8}" if len(added) > 8 else ""
        noun = "פריט" if len(added) == 1 else "פריטים"
        parts.append(f"נוסף {noun} ל{category}: {shown}{more}." if len(added) == 1 else f"נוספו {len(added)} {noun} ל{category}: {shown}{more}.")
    if result["enriched"]:
        parts.append(f"מולאו {result['enriched']} שדות שהיו ריקים.")
    if result["conflicts"]:
        parts.append(f"{result['conflicts']} סתירות נשארו שאלות. ערך קיים לא הוחלף.")
    parts.append("לא נוצר מחיר.")
    status = "imported" if added or result["enriched"] or result["conflicts"] else "loaded"
    return status, " ".join(parts)


def _dominant_source(conn: sqlite3.Connection, parsed: dict) -> sqlite3.Row | None:
    from app.importer import _build_rows

    built = _build_rows(parsed["records"], parsed["mapping"], "preview", parsed["kind"])
    tags = [row["tag_norm"] for row in built if row["kind"] == "equipment" and row.get("tag_norm")]
    if not tags:
        return None
    counts: dict[str, int] = defaultdict(int)
    for tag in tags:
        for row in conn.execute(
            "SELECT source_file_id FROM inventory_rows WHERE kind = 'equipment' AND tag_norm = ?",
            (tag,),
        ):
            counts[row["source_file_id"]] += 1
    if not counts:
        return None
    source_id = max(counts, key=counts.get)
    if counts[source_id] < len(set(tags)):
        return None
    return conn.execute("SELECT * FROM source_files WHERE id = ?", (source_id,)).fetchone()


def receive_upload(conn: sqlite3.Connection, *, name: str, data: bytes, note: str, photo_dir: Path, created_at: str) -> dict:
    from app.importer import parse_uploaded_inventory, text_looks_like_inventory
    from app.intake import extract_document

    note = (note or "").strip()
    if not data and not note:
        raise ValueError("צריך קובץ או טקסט.")
    payload = data or note.encode()
    digest = hashlib.sha256(payload).hexdigest()
    suffix = Path(name or "note.txt").suffix.lower()
    file_id = "FIL-" + digest[:16]
    photo_dir.mkdir(parents=True, exist_ok=True)
    stored = photo_dir / f"{file_id}{suffix or '.txt'}"
    if data:
        if not stored.exists():
            stored.write_bytes(data)
    else:
        stored.write_text(note, encoding="utf-8")
    source = conn.execute("SELECT * FROM source_files WHERE sha256 = ?", (digest,)).fetchone()
    existing = conn.execute("SELECT id, status FROM project_files WHERE sha256 = ?", (digest,)).fetchone()
    if source:
        return _present_already_loaded(
            conn,
            digest=digest,
            name=name or source["filename"],
            stored=stored,
            suffix=suffix,
            source=source,
            created_at=created_at,
            duplicate=existing is not None,
        )
    if existing and existing["status"] in {"loaded", "imported", "dismissed"}:
        if existing["status"] == "dismissed":
            restore = "loaded" if conn.execute("SELECT matched_source_id FROM project_files WHERE id = ?", (existing["id"],)).fetchone()["matched_source_id"] else "imported"
            conn.execute("UPDATE project_files SET status = ?, updated_at = ? WHERE id = ?", (restore, created_at, existing["id"]))
        public = _public_file(conn, existing["id"])
        public["duplicate"] = True
        if public["status"] == "loaded":
            public["summary"] = public["summary"] or "הקובץ הזה כבר במלאי. לא נוצרו נכסים חדשים."
        else:
            public["summary"] = "הקובץ כבר נשמר ונקלט למלאי. לא נוספו נכסים שוב."
        return public
    if existing:
        public = _public_file(conn, existing["id"])
        public["duplicate"] = True
        public["summary"] = "הקובץ כבר נשמר. לא נוצר נכס, קשר או שאלה נוספים."
        return public
    parsed = parse_uploaded_inventory(stored) if data else None
    if parsed:
        same = _dominant_source(conn, parsed)
        preview = _apply_inventory_list(conn, parsed, digest=digest, name=name or stored.name, created_at=created_at)
        if not preview["added"] and not preview["enriched"] and not preview["conflicts"] and same:
            return _present_already_loaded(
                conn,
                digest=digest,
                name=name or same["filename"],
                stored=stored,
                suffix=suffix,
                source=same,
                created_at=created_at,
                duplicate=False,
            )
        if not preview["added"] and not preview["enriched"] and not preview["conflicts"]:
            status, summary = "loaded", "כל שורות הרשימה כבר במלאי. לא נוצרו נכסים חדשים ולא נפתחו שיוכים."
            category = preview["category"]
            source_id = ""
        else:
            status, summary = _list_summary(preview, "", name or "")
            category = preview["category"]
            source_id = preview["source_id"]
            if status == "loaded" and same:
                return _present_already_loaded(
                    conn,
                    digest=digest,
                    name=name or same["filename"],
                    stored=stored,
                    suffix=suffix,
                    source=same,
                    created_at=created_at,
                    duplicate=False,
                )
        _save_project_file(
            conn,
            file_id=file_id,
            digest=digest,
            name=name or stored.name,
            stored=str(stored),
            suffix=suffix,
            text="",
            note=note or ("TAGS:" + "|".join(preview["added"][:12])),
            doc_type="רשימת מלאי",
            confidence="confirmed",
            summary=summary,
            status=status,
            source_id=source_id,
            category=category,
            created_at=created_at,
        )
        public = _public_file(conn, file_id)
        public["added_tags"] = preview["added"][:12]
        return public
    if data:
        extracted = extract_document(stored)
        text = extracted.get("text") or ""
        unread = extracted.get("note") or ""
    else:
        text = note
        unread = ""
    if note and note not in text:
        text = (text + "\n" + note).strip()
    if text_looks_like_inventory(text):
        summary = "הקובץ נראה כמו רשימת מלאי, אבל השורות לא נקראו בצורה חד-משמעית. לא נוספו נכסים ולא נפתחו שיוכים."
        _save_project_file(
            conn,
            file_id=file_id,
            digest=digest,
            name=name or "טקסט",
            stored=str(stored),
            suffix=suffix,
            text=text,
            note=note,
            doc_type="רשימת מלאי",
            confidence="uncertain",
            summary=summary,
            status="imported",
            source_id="",
            category="",
            created_at=created_at,
        )
        return _public_file(conn, file_id)
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
    if action == "dismiss":
        conn.execute("UPDATE project_files SET status = 'dismissed', updated_at = ? WHERE id = ?", (created_at, file_id))
        log_change(conn, actor=ACTOR_USER, action="dismiss_upload", entity_type="file", entity_id=file_id, prior={"status": row["status"]}, new={"status": "dismissed"}, created_at=created_at)
        return _public_file(conn, file_id)
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


def _host_for_parent(parent_row_id: str, data: dict) -> str:
    as_parent = []
    as_component = []
    for machine in data["machines"]:
        for member in data["members"].get(machine["id"], []):
            if member.get("inventory_row_id") != parent_row_id:
                continue
            if member.get("member_role") == "parent":
                as_parent.append(machine)
            else:
                as_component.append(machine)
    if as_parent:
        as_parent.sort(key=lambda machine: (0 if machine.get("basis") == "user" else 1, machine["id"]))
        return as_parent[0]["id"]
    if len(as_component) == 1:
        return as_component[0]["id"]
    return parent_row_id


def ensure_slash_components(conn: sqlite3.Connection, created_at: str) -> None:
    """A tag shaped like PARENT/SUFFIX belongs to the parent tag. Nothing else is grouped here."""
    data = _load(conn)
    by_tag: dict[str, list[dict]] = defaultdict(list)
    for row in data["rows"]:
        if row.get("tag_norm"):
            by_tag[row["tag_norm"]].append(row)
    known = {row["id"] for row in conn.execute("SELECT id FROM asset_components")}
    linked_rows = {
        row["inventory_row_id"]
        for row in conn.execute("SELECT inventory_row_id FROM asset_components WHERE inventory_row_id IS NOT NULL AND inventory_row_id != ''")
    }
    for row in data["rows"]:
        tag = row.get("tag_norm") or ""
        if "/" not in tag:
            continue
        parent_tag, suffix = tag.split("/", 1)
        if not parent_tag or not suffix.strip():
            continue
        parents = by_tag.get(parent_tag) or []
        if len(parents) != 1:
            continue
        parent = parents[0]
        if parent["id"] == row["id"] or row["id"] in linked_rows:
            continue
        component_id = "CMP-" + _digest(f"row|{row['id']}")
        if component_id in known:
            continue
        host = _host_for_parent(parent["id"], data)
        explanation = (
            f"התג הוא {parent_tag} ואחריו לוכסן וסיומת {suffix}. "
            "החיבור הוא לפי הצורה הזו. מספר משותף או קידומת לבדם לא מחברים."
        )
        conn.execute(
            """
            INSERT INTO asset_components (
              id, asset_id, tag_norm, tag_original, name, description, notes, inventory_row_id,
              origin, link_status, user_locked, explanation, created_at, updated_at
            ) VALUES (?, ?, ?, ?, '', '', '', ?, 'slash', 'active', 0, ?, ?, ?)
            """,
            (
                component_id,
                host,
                tag,
                row.get("tag_original") or tag,
                row["id"],
                explanation,
                created_at,
                created_at,
            ),
        )
        known.add(component_id)
        linked_rows.add(row["id"])


def search_inventory(conn: sqlite3.Connection, query: str, limit: int = 30) -> dict:
    needle = (query or "").strip().casefold()
    if not needle:
        return {"items": []}
    data = _load(conn)
    found = []
    for row in data["rows"]:
        haystack = " ".join(
            [
                row.get("tag_original") or "",
                row.get("tag_norm") or "",
                row.get("description") or "",
                row.get("listed_area") or "",
                row.get("manufacturer") or "",
                row.get("model") or "",
            ]
        ).casefold()
        if needle not in haystack:
            continue
        tag = row.get("tag_original") or ""
        found.append(
            {
                "id": row["id"],
                "tag": tag,
                "description": row.get("description") or "",
                "area": row.get("listed_area") or "",
                "covered": row["id"] in data["covered"],
                "rank": 0 if (row.get("tag_norm") or "").casefold().startswith(needle) or tag.casefold().startswith(needle) else 1,
            }
        )
    found.sort(key=lambda item: (item["rank"], item["tag"], item["description"]))
    items = found[:limit]
    for item in items:
        item.pop("rank", None)
    return {"items": items}


def _primary_row_ids(data: dict, asset_id: str) -> set[str]:
    ids = set()
    if asset_id in data["by_id"]:
        ids.add(asset_id)
    for member in data["members"].get(asset_id, []):
        if member.get("member_role") == "parent" and member.get("inventory_row_id"):
            ids.add(member["inventory_row_id"])
    return ids


def _assert_not_machine_parent(conn: sqlite3.Connection, row_id: str) -> None:
    hit = conn.execute(
        """
        SELECT 1
        FROM machine_members mm
        JOIN machines m ON m.id = mm.machine_id
        WHERE mm.inventory_row_id = ? AND mm.member_role = 'parent'
          AND mm.link_status = 'active' AND m.status != 'dissolved'
        """,
        (row_id,),
    ).fetchone()
    if hit:
        raise ValueError("הפריט הזה הוא נכס ראשי עם כרטיס משלו. בחרו רכיב אחר, או פתחו את הכרטיס שלו.")


def _release_row(conn: sqlite3.Connection, row_id: str, keep_asset_id: str) -> None:
    _assert_not_machine_parent(conn, row_id)
    conn.execute(
        """
        UPDATE machine_members
        SET link_status = 'removed',
            evidence = evidence || ' הועבר על ידי המשתמש. שורת המקור נשמרה.'
        WHERE inventory_row_id = ? AND link_status = 'active' AND member_role != 'parent'
          AND machine_id != ?
        """,
        (row_id, keep_asset_id),
    )
    conn.execute(
        """
        UPDATE asset_components
        SET link_status = 'removed', user_locked = 1, updated_at = updated_at
        WHERE inventory_row_id = ? AND asset_id != ? AND link_status = 'active'
        """,
        (row_id, keep_asset_id),
    )


def _place_row_component(conn: sqlite3.Connection, asset_id: str, row: dict, created_at: str, explanation: str) -> str:
    component_id = "CMP-" + _digest(f"row|{row['id']}")
    existing = conn.execute("SELECT id FROM asset_components WHERE id = ?", (component_id,)).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE asset_components
            SET asset_id = ?, tag_norm = ?, tag_original = ?, inventory_row_id = ?, origin = 'user',
                link_status = 'active', user_locked = 1, explanation = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                asset_id,
                row.get("tag_norm") or "",
                row.get("tag_original") or row.get("tag_norm") or "",
                row["id"],
                explanation,
                created_at,
                component_id,
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO asset_components (
              id, asset_id, tag_norm, tag_original, name, description, notes, inventory_row_id,
              origin, link_status, user_locked, explanation, created_at, updated_at
            ) VALUES (?, ?, ?, ?, '', '', '', ?, 'user', 'active', 1, ?, ?, ?)
            """,
            (
                component_id,
                asset_id,
                row.get("tag_norm") or "",
                row.get("tag_original") or row.get("tag_norm") or "",
                row["id"],
                explanation,
                created_at,
                created_at,
            ),
        )
    return component_id


def update_components(conn: sqlite3.Connection, asset_id: str, body: dict, created_at: str) -> dict:
    if not _find_asset(conn, asset_id):
        raise LookupError("הנכס לא נמצא.")
    action = body.get("action") or ""
    if action == "add_existing":
        row_ids = [str(item).strip() for item in (body.get("row_ids") or []) if str(item).strip()]
        if not row_ids:
            raise ValueError("צריך לבחור לפחות פריט אחד מהמלאי.")
        data = _load(conn)
        primary = _primary_row_ids(data, asset_id)
        rows = []
        for row_id in row_ids:
            row = data["by_id"].get(row_id)
            if not row:
                raise ValueError("הפריט לא נמצא במלאי. לא נוצר פריט חדש.")
            if row_id in primary:
                raise ValueError("אי אפשר לצרף את הנכס כרכיב של עצמו.")
            _assert_not_machine_parent(conn, row_id)
            rows.append(row)
        explanation = "המשתמש הוסיף פריט קיים מהמלאי. שורת המקור נשמרה."
        for row in rows:
            _release_row(conn, row["id"], asset_id)
            _place_row_component(conn, asset_id, row, created_at, explanation)
        log_change(
            conn,
            actor=ACTOR_USER,
            action="add_component",
            entity_type="asset",
            entity_id=asset_id,
            prior=None,
            new={"row_ids": row_ids},
            created_at=created_at,
        )
        return asset_detail(conn, asset_id)
    if action == "create":
        tag = (body.get("tag") or "").strip()
        name = (body.get("name") or "").strip()
        description = (body.get("description") or "").strip()
        notes = (body.get("notes") or "").strip()
        if len(tag) < 1 and len(name) < 1:
            raise ValueError("צריך תג או שם לרכיב.")
        tag_norm = tag.upper()
        if tag_norm:
            hits = conn.execute(
                "SELECT id FROM inventory_rows WHERE kind = 'equipment' AND tag_norm = ?",
                (tag_norm,),
            ).fetchall()
            if len(hits) == 1:
                raise ValueError("התג כבר קיים במלאי. הוסיפו אותו כפריט קיים, בלי ליצור רכיב כפול.")
            if len(hits) > 1:
                raise ValueError("יש יותר מרשומה אחת עם התג הזה. לא נוצר רכיב חדש.")
        component_id = "CMP-" + _digest(f"new|{asset_id}|{tag_norm}|{name}|{created_at}")
        conn.execute(
            """
            INSERT INTO asset_components (
              id, asset_id, tag_norm, tag_original, name, description, notes, inventory_row_id,
              origin, link_status, user_locked, explanation, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'user', 'active', 1, ?, ?, ?)
            """,
            (
                component_id,
                asset_id,
                tag_norm,
                tag,
                name,
                description,
                notes,
                "נוצר בטופס על ידי המשתמש. לא נוצרה שורת מקור ולא הומצא מספר סידורי.",
                created_at,
                created_at,
            ),
        )
        log_change(
            conn,
            actor=ACTOR_USER,
            action="create_component",
            entity_type="asset",
            entity_id=asset_id,
            prior=None,
            new={"id": component_id, "tag": tag, "name": name},
            created_at=created_at,
        )
        return asset_detail(conn, asset_id)
    if action == "remove":
        return _remove_component(conn, asset_id, str(body.get("component_id") or ""), created_at)
    if action == "move":
        target = str(body.get("target_asset_id") or "").strip()
        if not target or target == asset_id:
            raise ValueError("צריך לבחור נכס אחר.")
        if not _find_asset(conn, target):
            raise ValueError("הנכס שנבחר לא נמצא. לא נוצר נכס חדש.")
        _move_component(conn, asset_id, str(body.get("component_id") or ""), target, created_at)
        return asset_detail(conn, asset_id)
    raise ValueError("אפשר להוסיף, ליצור, להסיר או להעביר רכיב.")


def _remove_component(conn: sqlite3.Connection, asset_id: str, component_id: str, created_at: str) -> dict:
    if not component_id:
        raise ValueError("צריך לבחור רכיב.")
    linked = conn.execute(
        "SELECT * FROM asset_components WHERE id = ? AND asset_id = ? AND link_status = 'active'",
        (component_id, asset_id),
    ).fetchone()
    if linked:
        conn.execute(
            "UPDATE asset_components SET link_status = 'removed', user_locked = 1, updated_at = ? WHERE id = ?",
            (created_at, component_id),
        )
        log_change(conn, actor=ACTOR_USER, action="remove_component", entity_type="asset", entity_id=asset_id, prior={"id": component_id}, new={"removed": component_id}, created_at=created_at)
        return asset_detail(conn, asset_id)
    member = conn.execute(
        "SELECT * FROM machine_members WHERE id = ? AND machine_id = ? AND link_status = 'active'",
        (component_id, asset_id),
    ).fetchone()
    if not member:
        raise LookupError("הרכיב לא נמצא בכרטיס.")
    if member["member_role"] == "parent":
        raise ValueError("אי אפשר להסיר את הרכיב הראשי מהכרטיס.")
    conn.execute(
        """
        UPDATE machine_members
        SET link_status = 'removed',
            evidence = evidence || ' הוסר על ידי המשתמש. שורת המקור נשמרה.'
        WHERE id = ?
        """,
        (component_id,),
    )
    if member["inventory_row_id"]:
        conn.execute(
            """
            UPDATE asset_components
            SET link_status = 'removed', user_locked = 1, updated_at = ?
            WHERE inventory_row_id = ? AND asset_id = ? AND link_status = 'active'
            """,
            (created_at, member["inventory_row_id"], asset_id),
        )
    log_change(conn, actor=ACTOR_USER, action="remove_component", entity_type="asset", entity_id=asset_id, prior={"tag": member["tag_norm"]}, new={"removed": member["tag_norm"]}, created_at=created_at)
    return asset_detail(conn, asset_id)


def _move_component(conn: sqlite3.Connection, asset_id: str, component_id: str, target: str, created_at: str) -> None:
    if not component_id:
        raise ValueError("צריך לבחור רכיב.")
    explanation = "המשתמש העביר את הרכיב לנכס הזה."
    linked = conn.execute(
        "SELECT * FROM asset_components WHERE id = ? AND asset_id = ? AND link_status = 'active'",
        (component_id, asset_id),
    ).fetchone()
    if linked:
        if linked["inventory_row_id"]:
            _release_row(conn, linked["inventory_row_id"], target)
        conn.execute(
            """
            UPDATE asset_components
            SET asset_id = ?, link_status = 'active', user_locked = 1, origin = 'user', explanation = ?, updated_at = ?
            WHERE id = ?
            """,
            (target, explanation, created_at, component_id),
        )
        log_change(conn, actor=ACTOR_USER, action="move_component", entity_type="asset", entity_id=asset_id, prior={"id": component_id, "asset_id": asset_id}, new={"asset_id": target}, created_at=created_at)
        log_change(conn, actor=ACTOR_USER, action="move_component", entity_type="asset", entity_id=target, prior=None, new={"id": component_id}, created_at=created_at)
        return
    member = conn.execute(
        "SELECT * FROM machine_members WHERE id = ? AND machine_id = ? AND link_status = 'active'",
        (component_id, asset_id),
    ).fetchone()
    if not member:
        raise LookupError("הרכיב לא נמצא בכרטיס.")
    if member["member_role"] == "parent":
        raise ValueError("אי אפשר להעביר את הרכיב הראשי.")
    conn.execute(
        """
        UPDATE machine_members
        SET link_status = 'removed',
            evidence = evidence || ' הועבר לנכס אחר על ידי המשתמש. שורת המקור נשמרה.'
        WHERE id = ?
        """,
        (component_id,),
    )
    if member["inventory_row_id"]:
        row = conn.execute("SELECT * FROM inventory_rows WHERE id = ?", (member["inventory_row_id"],)).fetchone()
        if not row:
            raise LookupError("שורת המקור לא נמצאה.")
        _release_row(conn, row["id"], target)
        _place_row_component(conn, target, dict(row), created_at, explanation)
    else:
        moved_id = "CMP-" + _digest(f"member|{member['id']}")
        conn.execute(
            """
            INSERT INTO asset_components (
              id, asset_id, tag_norm, tag_original, name, description, notes, inventory_row_id,
              origin, link_status, user_locked, explanation, created_at, updated_at
            ) VALUES (?, ?, ?, ?, '', '', '', NULL, 'user', 'active', 1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              asset_id = excluded.asset_id, link_status = 'active', user_locked = 1, explanation = excluded.explanation, updated_at = excluded.updated_at
            """,
            (
                moved_id,
                target,
                member["tag_norm"],
                member["tag_original"],
                explanation,
                created_at,
                created_at,
            ),
        )
    log_change(conn, actor=ACTOR_USER, action="move_component", entity_type="asset", entity_id=asset_id, prior={"tag": member["tag_norm"]}, new={"asset_id": target}, created_at=created_at)
    log_change(conn, actor=ACTOR_USER, action="move_component", entity_type="asset", entity_id=target, prior=None, new={"tag": member["tag_norm"]}, created_at=created_at)
