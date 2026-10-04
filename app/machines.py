"""Machine groupings over the existing inventory rows.

A prefix is only a category. A shared number is not a machine.
A proposed machine exists when a component's own text names another inventory tag.
R-4208 and F-4208 are the one grouping the user confirmed as a single sale unit.
Component rows are never deleted or folded into the parent.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3

TAG_IN_TEXT = re.compile(r"(?<![A-Z0-9])([A-Z]{1,5})-([0-9]{3,5}[A-Z]?)(?![A-Z0-9])")
PREFIX_LABELS = {
    "R": "כור",
    "A": "בוחש / מערבל",
    "P": "משאבה",
    "F": "מסנן",
    "D": "מייבש",
    "H": "מחליף חום",
    "HVAC": "יחידת טיפול באוויר",
    "AHU": "יחידת טיפול באוויר",
    "B": "מפוח",
    "C": "עמוד זיקוק או ספיגה",
    "E": "ציוד כללי",
    "W": "ציוד שקילה / משקל",
    "T": "מכל",
    "X": "יחידת ציוד נוספת, בדרך כלל עם מדחס, כולל חימום וקירור",
}
PARENT_PREFIXES = {"R", "T", "D", "C", "X", "F"}
COMPONENT_PREFIXES = {"A", "P", "F", "D", "H", "B", "C", "E", "W", "T", "X"}
USER_MACHINE = ("R-4208", "F-4208")


def machine_id(key: str) -> str:
    return "MCH-" + hashlib.sha256(key.encode()).hexdigest()[:16]


def member_id(machine: str, tag_norm: str) -> str:
    return "MM-" + hashlib.sha256(f"{machine}|{tag_norm}".encode()).hexdigest()[:16]


def split_prefix(tag: str) -> str:
    text = (tag or "").strip().upper()
    for prefix in ("HVAC", "AHU"):
        if text.startswith(prefix + "-") or text.startswith(prefix + " ") or text.startswith(prefix + "/"):
            return prefix
    match = re.match(r"^([A-Z]{1,4})", text)
    if not match:
        return ""
    prefix = match.group(1)
    return prefix if prefix in PREFIX_LABELS else ""


def prefix_label(tag: str) -> str:
    return PREFIX_LABELS.get(split_prefix(tag), "")


def _norm_text(value: str) -> str:
    return re.sub(r"\s*-\s*", "-", (value or "").upper())


def _named_tags(text: str, known: set[str], self_tag: str) -> list[str]:
    found = []
    for match in TAG_IN_TEXT.finditer(_norm_text(text)):
        token = f"{match.group(1)}-{match.group(2)}"
        if token != self_tag and token in known and token not in found:
            found.append(token)
    return found


def ensure_machines(conn: sqlite3.Connection, created_at: str) -> None:
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, tag_norm, tag_original, description, remarks, sheet_name, original_row, kind
            FROM inventory_rows WHERE kind = 'equipment'
            """
        )
    ]
    by_tag: dict[str, list[dict]] = {}
    for row in rows:
        if row["tag_norm"]:
            by_tag.setdefault(row["tag_norm"], []).append(row)
    known = set(by_tag)
    proposed: dict[str, dict] = {}
    for row in rows:
        prefix = split_prefix(row["tag_norm"] or "")
        if prefix not in COMPONENT_PREFIXES:
            continue
        text = f"{row['description'] or ''} {row['remarks'] or ''}"
        named = [token for token in _named_tags(text, known, row["tag_norm"] or "") if split_prefix(token) in PARENT_PREFIXES]
        if not named:
            continue
        for parent in named:
            bucket = proposed.setdefault(parent, {"components": {}})
            bucket["components"][row["tag_norm"]] = {
                "tag_original": row["tag_original"] or row["tag_norm"],
                "row": row if len(by_tag.get(row["tag_norm"], [])) == 1 else None,
                "evidence": f"התיאור מזכיר את {parent}. מספר משותף לבדו לא יצר את הקישור.",
                "also": [token for token in named if token != parent],
            }
    for parent, bucket in proposed.items():
        _upsert_proposed(conn, parent, by_tag.get(parent, []), bucket["components"], created_at)
    _upsert_user_confirmed(conn, created_at)


def _row_choice(hits: list[dict]) -> tuple[str | None, str]:
    if len(hits) == 1:
        row = hits[0]
        return row["id"], row["tag_original"] or row["tag_norm"]
    return None, ""


def _upsert_member(
    conn: sqlite3.Connection,
    *,
    machine: str,
    tag_norm: str,
    tag_original: str,
    row_id: str | None,
    role: str,
    evidence: str,
) -> None:
    existing = conn.execute(
        "SELECT link_status FROM machine_members WHERE machine_id = ? AND tag_norm = ?",
        (machine, tag_norm),
    ).fetchone()
    if existing:
        return
    conn.execute(
        """
        INSERT INTO machine_members (
          id, machine_id, tag_norm, tag_original, inventory_row_id, member_role,
          prefix_category, link_status, evidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)
        """,
        (
            member_id(machine, tag_norm),
            machine,
            tag_norm,
            tag_original,
            row_id,
            role,
            prefix_label(tag_norm),
            evidence,
        ),
    )


def _upsert_proposed(conn: sqlite3.Connection, parent: str, parent_rows: list[dict], components: dict, created_at: str) -> None:
    mid = machine_id(f"ref:{parent}")
    parent_id, parent_original = _row_choice(parent_rows)
    parent_label = parent_original or parent
    description = ""
    if len(parent_rows) == 1:
        description = parent_rows[0]["description"] or ""
    name = f"{parent_label} {description}".strip()
    note = "הצעה לפי אזכור מפורש בתיאור. לא אושר למכירה יחד."
    if len(parent_rows) > 1:
        note += " לתג ההורה יש יותר משורת אקסל אחת, והשורות לא אוחדו."
    conflicting = [tag for tag, item in components.items() if item["also"]]
    if conflicting:
        note += " חלק מהרכיבים מזכירים גם תג אחר, ולכן גבול היחידה פתוח."
    existing = conn.execute("SELECT id, status FROM machines WHERE id = ?", (mid,)).fetchone()
    if not existing:
        conn.execute(
            """
            INSERT INTO machines (id, name, status, sold_together, basis, note, created_at, updated_at)
            VALUES (?, ?, 'proposed', 'open', 'explicit_reference', ?, ?, ?)
            """,
            (mid, name[:240], note, created_at, created_at),
        )
    elif existing["status"] == "proposed":
        conn.execute("UPDATE machines SET note = ?, updated_at = ? WHERE id = ?", (note, created_at, mid))
    _upsert_member(
        conn,
        machine=mid,
        tag_norm=parent,
        tag_original=parent_label,
        row_id=parent_id,
        role="parent",
        evidence="התג שאליו מפנים התיאורים. הקידומת היא קטגוריה בלבד.",
    )
    for tag_norm, item in components.items():
        row = item["row"]
        _upsert_member(
            conn,
            machine=mid,
            tag_norm=tag_norm,
            tag_original=item["tag_original"],
            row_id=row["id"] if row else None,
            role="component",
            evidence=item["evidence"],
        )
    if not existing or existing["status"] != "confirmed":
        _grouping_task(conn, mid, parent_label, created_at, contradictory=bool(conflicting or len(parent_rows) > 1))


def _upsert_user_confirmed(conn: sqlite3.Connection, created_at: str) -> None:
    tags = list(USER_MACHINE)
    mid = machine_id("user:" + "|".join(tags))
    existing = conn.execute("SELECT id FROM machines WHERE id = ?", (mid,)).fetchone()
    note = "אושר על ידי המשתמש: R-4208 ו-F-4208 מכונה אחת שנמכרת יחד. זה הדפוס, לא כלל לכל זוג עם אותו מספר."
    missing = []
    if not existing:
        conn.execute(
            """
            INSERT INTO machines (id, name, status, sold_together, basis, note, created_at, updated_at)
            VALUES (?, ?, 'confirmed', 'confirmed', 'user', ?, ?, ?)
            """,
            (mid, "R-4208 עם F-4208", note, created_at, created_at),
        )
    for index, tag in enumerate(tags):
        row = conn.execute(
            "SELECT id, tag_original FROM inventory_rows WHERE tag_norm = ? AND kind = 'equipment'",
            (tag,),
        ).fetchall()
        row_id, original = _row_choice([dict(item) for item in row])
        if not row:
            missing.append(tag)
        _upsert_member(
            conn,
            machine=mid,
            tag_norm=tag,
            tag_original=original or tag,
            row_id=row_id,
            role="parent" if index == 0 else "component",
            evidence="אושר על ידי המשתמש, לא מתוך מספר משותף בלבד.",
        )
    if missing:
        extra = " " + " ו-".join(missing) + " לא נמצאו בקובץ האקסל שנטען. לא נוצרה שורת מלאי במקומם."
        conn.execute("UPDATE machines SET note = ?, updated_at = ? WHERE id = ?", (note + extra, created_at, mid))


def _reopen_boundary(conn: sqlite3.Connection, machine: str, created_at: str) -> None:
    conn.execute(
        """
        UPDATE review_items
        SET status = 'open', resolution = NULL, missing_reason = NULL, updated_at = ?
        WHERE dedupe_key = ? AND status = 'resolved'
        """,
        (created_at, f"machine:{machine}:boundary"),
    )


def _grouping_task(conn: sqlite3.Connection, machine: str, parent: str, created_at: str, contradictory: bool) -> None:
    from app.logic import upsert_review

    members = list(
        conn.execute(
            "SELECT tag_original, inventory_row_id, member_role, evidence FROM machine_members WHERE machine_id = ? AND link_status = 'active'",
            (machine,),
        )
    )
    row_ids = [row["inventory_row_id"] for row in members if row["inventory_row_id"]]
    question = (
        f"האם {parent} והרכיבים שהתיאור שלהם מזכיר אותו הם מכונה אחת? "
        "דפוס דומה לאישור על R-4208 עם F-4208, ועדיין לא אישור למכירה יחד."
    )
    if contradictory:
        question += " יש כאן גם סתירה או יותר מתג הורה אחד, אז הגבול לא הוכרע."
    upsert_review(
        conn,
        kind="grouping",
        queue="machine",
        dedupe_key=f"machine:{machine}:boundary",
        question=question,
        priority="high" if contradictory else "medium",
        capture_id=None,
        row_ids=row_ids,
        payload={
            "machine_id": machine,
            "resolve_by": "אשר את היחידה, הוצא רכיב, צרף רכיב, או תקן את השם. שורות האקסל נשארות, ואין שווי נפרד ליחידה ולרכיבים יחד.",
            "members": [
                {"tag": row["tag_original"], "role": row["member_role"], "row_id": row["inventory_row_id"], "evidence": row["evidence"]}
                for row in members
            ],
        },
        created_at=created_at,
    )


def membership_for_tags(conn: sqlite3.Connection, tag_norms: list[str]) -> list[dict]:
    if not tag_norms:
        return []
    marks = ",".join("?" for _ in tag_norms)
    found = []
    for row in conn.execute(
        f"""
        SELECT m.id, m.name, m.status, m.sold_together, m.basis, mm.tag_norm, mm.tag_original, mm.member_role
        FROM machine_members mm
        JOIN machines m ON m.id = mm.machine_id
        WHERE mm.link_status = 'active' AND mm.tag_norm IN ({marks}) AND m.status != 'dissolved'
        """,
        tag_norms,
    ):
        others = [
            item["tag_original"]
            for item in conn.execute(
                """
                SELECT tag_original FROM machine_members
                WHERE machine_id = ? AND link_status = 'active' AND tag_norm != ?
                """,
                (row["id"], row["tag_norm"]),
            )
        ]
        found.append(
            {
                "id": row["id"],
                "name": row["name"],
                "status": row["status"],
                "sold_together": row["sold_together"],
                "matched_tag": row["tag_original"],
                "matched_role": row["member_role"],
                "prefix": prefix_label(row["tag_norm"]),
                "other_tags": others,
            }
        )
    return found


def sale_note_for_row(conn: sqlite3.Connection, row_id: str) -> str:
    row = conn.execute(
        """
        SELECT m.name FROM machine_members mm
        JOIN machines m ON m.id = mm.machine_id
        WHERE mm.inventory_row_id = ? AND mm.link_status = 'active'
          AND m.status = 'confirmed' AND m.sold_together = 'confirmed'
        """,
        (row_id,),
    ).fetchone()
    if not row:
        return ""
    return f"חלק מיחידת המכירה {row['name']}. אין כאן שווי נוסף ואין התחייבות מכירה נפרדת מעבר ליחידה."


def public_machines(conn: sqlite3.Connection) -> list[dict]:
    photos = []
    for photo in conn.execute("SELECT id, capture_id, original_name, role, shows_json FROM photos"):
        shows = json.loads(photo["shows_json"] or "{}")
        photos.append((photo, shows))
    machines = []
    for machine in conn.execute("SELECT * FROM machines ORDER BY CASE status WHEN 'confirmed' THEN 0 ELSE 1 END, name"):
        members = []
        for member in conn.execute(
            "SELECT * FROM machine_members WHERE machine_id = ? ORDER BY CASE member_role WHEN 'parent' THEN 0 ELSE 1 END, tag_norm",
            (machine["id"],),
        ):
            source = None
            if member["inventory_row_id"]:
                source = conn.execute(
                    "SELECT sheet_name, original_row, description, tag_original, source_kind FROM inventory_rows WHERE id = ?",
                    (member["inventory_row_id"],),
                ).fetchone()
            caption_tag = member["tag_norm"]
            visible = []
            for photo, shows in photos:
                labels = shows.get("labels") or []
                row_ids = shows.get("row_ids") or []
                hit = member["inventory_row_id"] and member["inventory_row_id"] in row_ids
                hit = hit or any((label.get("tag_norm") == caption_tag) for label in labels)
                if not hit or shows.get("scope") not in {"rows", "tags"}:
                    continue
                visible.append(
                    {
                        "id": photo["id"],
                        "capture_id": photo["capture_id"],
                        "original_name": photo["original_name"] or "",
                        "of_tag": member["tag_original"],
                    }
                )
            members.append(
                {
                    "tag": member["tag_original"],
                    "tag_norm": member["tag_norm"],
                    "role": member["member_role"],
                    "prefix": member["prefix_category"] or "",
                    "link_status": member["link_status"],
                    "row_id": member["inventory_row_id"] or "",
                    "sheet_name": source["sheet_name"] if source else "",
                    "original_row": source["original_row"] if source else None,
                    "description": source["description"] if source else "",
                    "source_kind": source["source_kind"] if source else "",
                    "evidence": member["evidence"],
                    "in_inventory": bool(source),
                    "photos": visible,
                }
            )
        machines.append(
            {
                "id": machine["id"],
                "name": machine["name"],
                "status": machine["status"],
                "sold_together": machine["sold_together"],
                "basis": machine["basis"],
                "note": machine["note"] or "",
                "members": members,
            }
        )
    return machines


def machine_action(conn: sqlite3.Connection, machine: str, body: dict, created_at: str) -> dict:
    from app.logic import close_review

    record = conn.execute("SELECT * FROM machines WHERE id = ?", (machine,)).fetchone()
    if not record:
        raise LookupError("המכונה לא נמצאה.")
    action = body.get("action")
    if action == "note":
        text = (body.get("text") or "").strip()
        if len(text) < 2:
            raise ValueError("צריך תשובה כתובה.")
        note = ((record["note"] or "").rstrip() + "\nתשובה: " + text)[:4000]
        conn.execute("UPDATE machines SET note = ?, updated_at = ? WHERE id = ?", (note, created_at, machine))
        conn.execute(
            """
            UPDATE review_items
            SET resolution = ?, updated_at = ?, missing_reason = NULL
            WHERE dedupe_key = ? AND status = 'open'
            """,
            (text, created_at, f"machine:{machine}:boundary"),
        )
        return {"id": machine, "noted": True, "status": record["status"], "sold_together": record["sold_together"]}
    if action == "confirm":
        conn.execute(
            "UPDATE machines SET status = 'confirmed', updated_at = ? WHERE id = ?",
            (created_at, machine),
        )
        conn.execute(
            """
            UPDATE review_items
            SET question = ?, updated_at = ?
            WHERE dedupe_key = ? AND status = 'open'
            """,
            (
                f"הקיבוץ של {record['name']} אושר. המכירה יחד עדיין פתוחה, ואין שווי נפרד ליחידה ולרכיבים.",
                created_at,
                f"machine:{machine}:boundary",
            ),
        )
        return {"id": machine, "status": "confirmed", "sold_together": record["sold_together"]}
    if action == "confirm_sale":
        conn.execute(
            "UPDATE machines SET status = 'confirmed', sold_together = 'confirmed', updated_at = ? WHERE id = ?",
            (created_at, machine),
        )
        close_review(conn, f"machine:{machine}:boundary", "אושר על ידי המשתמש כיחידת מכירה אחת. השורות נשארו, בלי שווי כפול.", created_at)
        return {"id": machine, "status": "confirmed", "sold_together": "confirmed"}
    if action == "split":
        tags = [str(tag).strip().upper() for tag in (body.get("tag_norms") or []) if str(tag).strip()]
        if not tags:
            raise ValueError("צריך לבחור רכיב להוצאה. השורה לא נמחקת.")
        for tag in tags:
            conn.execute(
                """
                UPDATE machine_members SET link_status = 'removed',
                  evidence = evidence || ' הוצא מהיחידה בלי למחוק את שורת האקסל.'
                WHERE machine_id = ? AND tag_norm = ? AND link_status = 'active'
                """,
                (machine, tag),
            )
        conn.execute(
            "UPDATE machines SET status = 'proposed', sold_together = 'open', updated_at = ? WHERE id = ?",
            (created_at, machine),
        )
        _reopen_boundary(conn, machine, created_at)
        _grouping_task(conn, machine, record["name"], created_at, contradictory=True)
        return {"id": machine, "status": "proposed", "removed": tags}
    if action == "combine":
        tags = [str(tag).strip().upper() for tag in (body.get("tag_norms") or []) if str(tag).strip()]
        if not tags:
            raise ValueError("צריך תג לצירוף.")
        for tag in tags:
            row = conn.execute(
                "SELECT id, tag_original FROM inventory_rows WHERE tag_norm = ? AND kind = 'equipment'",
                (tag,),
            ).fetchall()
            row_id, original = _row_choice([dict(item) for item in row])
            existing = conn.execute(
                "SELECT link_status FROM machine_members WHERE machine_id = ? AND tag_norm = ?",
                (machine, tag),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE machine_members
                    SET link_status = 'active', inventory_row_id = COALESCE(?, inventory_row_id),
                        evidence = 'צורף ליחידה. שורת המקור נשמרה.'
                    WHERE machine_id = ? AND tag_norm = ?
                    """,
                    (row_id, machine, tag),
                )
            else:
                _upsert_member(
                    conn,
                    machine=machine,
                    tag_norm=tag,
                    tag_original=original or tag,
                    row_id=row_id,
                    role="component",
                    evidence="צורף ליחידה. שורת המקור נשמרה.",
                )
            conn.execute(
                """
                UPDATE machine_members SET link_status = 'removed'
                WHERE tag_norm = ? AND machine_id != ? AND link_status = 'active'
                  AND machine_id IN (SELECT id FROM machines WHERE status = 'proposed')
                """,
                (tag, machine),
            )
        conn.execute("UPDATE machines SET sold_together = 'open', updated_at = ? WHERE id = ? AND status != 'confirmed'", (created_at, machine))
        current = conn.execute("SELECT status, sold_together, name FROM machines WHERE id = ?", (machine,)).fetchone()
        if current and current["sold_together"] != "confirmed":
            _reopen_boundary(conn, machine, created_at)
            _grouping_task(conn, machine, current["name"], created_at, contradictory=False)
        return {"id": machine, "added": tags}
    if action == "correct":
        name = (body.get("name") or "").strip()
        if len(name) < 2:
            raise ValueError("צריך שם ליחידה.")
        conn.execute("UPDATE machines SET name = ?, updated_at = ? WHERE id = ?", (name[:240], created_at, machine))
        return {"id": machine, "name": name[:240]}
    if action == "dissolve":
        conn.execute(
            "UPDATE machine_members SET link_status = 'removed' WHERE machine_id = ? AND link_status = 'active'",
            (machine,),
        )
        conn.execute(
            "UPDATE machines SET status = 'dissolved', sold_together = 'open', updated_at = ?, note = ? WHERE id = ?",
            (created_at, "הוחלט שלא לחבר את הרכיבים. שורות האקסל נשארו.", machine),
        )
        close_review(conn, f"machine:{machine}:boundary", "היחידה פורקה. השורות נשארו נפרדות.", created_at)
        return {"id": machine, "status": "dissolved"}
    raise ValueError("פעולה לא מוכרת.")
