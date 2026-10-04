"""Identifier matching.

Automatic links are allowed only for an exact, unambiguous identifier with no
conflicting area. Name similarity, model numbers, and partial tags are suggestions.
A photo link is not created here and is never a system relationship.
"""

from __future__ import annotations

import re

NON_IDENTIFIERS = {"", "-", "--", "—", "N/A", "NA", "NONE", "NULL"}
UNKNOWN_AREAS = {"", "לא ידוע", "unknown"}


def norm_id(value) -> str:
    """Trim and uppercase. Keep internal spaces, punctuation, and leading zeros."""
    if value is None:
        return ""
    text = str(value).strip().upper()
    if text in NON_IDENTIFIERS:
        return ""
    return text


def norm_area(value) -> str:
    if value is None:
        return ""
    return str(value).strip().casefold()


def area_conflict(observed: str | None, listed: str | None, parent: str | None = "") -> bool:
    observed_n = norm_area(observed)
    listed_n = norm_area(listed)
    parent_n = norm_area(parent)
    if observed_n in UNKNOWN_AREAS or listed_n in UNKNOWN_AREAS:
        return False
    if observed_n == listed_n:
        return False
    if parent_n and parent_n == listed_n:
        return False
    return True


def _suggestion(row: dict, why: str) -> dict:
    return {
        "row_id": row["id"],
        "tag": row.get("tag_original") or "",
        "description": row.get("description") or "",
        "sheet_name": row.get("sheet_name") or "",
        "original_row": row.get("original_row"),
        "listed_area": row.get("listed_area") or "",
        "identifier": row.get("tag_original") or row.get("serial") or "",
        "why": why,
    }


def _collapse_spaces(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def _from_hits(hits: list[dict], observed_area: str, observed_parent: str, identifier_label: str) -> dict:
    groups = {h.get("duplicate_group") or "" for h in hits}
    areas = {h.get("identity_area") or "" for h in hits}
    identical_group = len(hits) > 1 and len(areas) == 1 and len(groups) == 1 and next(iter(groups))
    conflict = any(area_conflict(observed_area, h.get("listed_area"), observed_parent) for h in hits)
    suggestions = [_suggestion(h, _hit_why(h, identifier_label)) for h in hits[:20]]

    if len(hits) == 1 or identical_group:
        if identical_group:
            reason = (
                "אותו תג באותו אזור, עם אותם נתונים מלאים. "
                "זו כפילות משוערת: שורות המקור נשמרות, והצילום מקושר לכולן."
            )
        else:
            reason = f"ה{identifier_label} זהה לשורה אחת, ואין סתירה עם האזור שנצפה."
        if conflict:
            listed = hits[0].get("listed_area") or "לא ידוע"
            observed = observed_area or "לא ידוע"
            reason = (
                f"ה{identifier_label} נמצא, אבל האזור בסיור ({observed}) "
                f"שונה מהאזור שברשימה ({listed}). ההתאמה ממתינה לבדיקה ולא הוחלפה בשקט."
            )
            return {
                "mode": "pending",
                "links": [
                    {
                        "row_id": h["id"],
                        "method": "proposed",
                        "review_status": "pending_review",
                        "reason": reason,
                    }
                    for h in hits
                ],
                "suggestions": suggestions,
                "review_kind": "location",
                "explanation": reason,
            }
        return {
            "mode": "auto",
            "links": [
                {
                    "row_id": h["id"],
                    "method": "auto",
                    "review_status": "auto_linked",
                    "reason": reason,
                }
                for h in hits
            ],
            "suggestions": suggestions,
            "review_kind": None,
            "explanation": reason,
        }

    reason = (
        f"ה{identifier_label} מופיע בכמה שורות עם נתונים שונים או באזורים שונים. "
        "לא נבחרה שורה אוטומטית ולא אוחדו רשומות."
    )
    return {
        "mode": "pending",
        "links": [],
        "suggestions": suggestions,
        "review_kind": "duplicate_tag",
        "explanation": reason,
    }


def _hit_why(row: dict, identifier_label: str) -> str:
    area = row.get("listed_area") or "לא ידוע"
    return f"התאמה לפי {identifier_label}. אזור ברשימה: {area}."


def _fuzzy_or_name(rows: list[dict], tag_n: str, raw_tag: str) -> list[dict]:
    collapsed = _collapse_spaces(tag_n)
    name_key = (raw_tag or "").strip().casefold()
    found: list[dict] = []
    for row in rows:
        tag_row = row.get("tag_norm") or ""
        why = None
        if collapsed and tag_row and _collapse_spaces(tag_row) == collapsed and tag_row != tag_n:
            why = "התג זהה רק אחרי מחיקת רווחים. התווים המקוריים נשמרו, ואין קישור אוטומטי."
        elif (
            tag_row
            and tag_n
            and tag_row != tag_n
            and min(len(tag_row), len(tag_n)) >= 4
            and (tag_row.startswith(tag_n) or tag_n.startswith(tag_row))
        ):
            why = "התג דומה רק בחלק מהתווים. אין קישור אוטומטי."
        else:
            description = (row.get("description") or "").strip().casefold()
            if name_key and description == name_key:
                why = "השם זהה, בלי תג תואם. אין קישור אוטומטי לפי שם או לפי מראה."
        if why:
            found.append(_suggestion(row, why))
        if len(found) >= 20:
            break
    return found


def match_inventory(
    rows: list[dict],
    *,
    tag: str = "",
    serial: str = "",
    model: str = "",
    observed_area: str = "",
    observed_parent: str = "",
) -> dict:
    equipment = [row for row in rows if row.get("kind", "equipment") == "equipment"]
    tag_n = norm_id(tag)
    serial_n = norm_id(serial)
    model_n = norm_id(model)

    if tag_n:
        hits = [row for row in equipment if row.get("tag_norm") == tag_n]
        if hits:
            return _from_hits(hits, observed_area, observed_parent, "תג")
        fuzzy = _fuzzy_or_name(equipment, tag_n, tag)
        if fuzzy:
            return {
                "mode": "pending",
                "links": [],
                "suggestions": fuzzy,
                "review_kind": "ambiguous",
                "explanation": fuzzy[0]["why"],
            }

    if serial_n:
        hits = [row for row in equipment if row.get("serial_norm") == serial_n]
        if hits:
            return _from_hits(hits, observed_area, observed_parent, "מספר סידורי")

    if model_n:
        hits = [row for row in equipment if norm_id(row.get("model")) == model_n]
        if hits:
            suggestions = [
                _suggestion(row, "הדגם זהה. דגם אינו תג זיהוי, ולכן אין קישור אוטומטי.")
                for row in hits[:20]
            ]
            return {
                "mode": "pending",
                "links": [],
                "suggestions": suggestions,
                "review_kind": "ambiguous",
                "explanation": "נמצאו שורות עם אותו דגם. צריך לבחור ידנית, או להשאיר בלי התאמה.",
            }

    return {
        "mode": "none",
        "links": [],
        "suggestions": [],
        "review_kind": None,
        "explanation": "לא נמצאה התאמה. הפריט יישמר כנצפה בסיור, בלי להכריז שהוא ציוד חדש במלאי.",
    }
