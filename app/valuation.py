"""Valuation readiness on top of the existing inventory.

No second inventory. No invented prices, comparables, or contact names.
Offer and target prices are stored elsewhere and are not read here.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.logic import ACTOR_SYSTEM, ACTOR_USER, log_change, now_iso, parse_json, upsert_review

PROJECT_TARGET = "2026-12-31"
RANGE_ABSTAIN = "אין די ראיות להערכה"
RESEARCH_LIMITATION = (
    "במהדורה הזו אין מחקר שוק חי. לא בוצע חיפוש, ולא נוספו עסקאות או מודעות. "
    "אפשר להוסיף מקור ידנית, או להשאיר את השווי בלי טווח."
)
APPRAISAL_LABEL = "אומדן שוק אינדיקטיבי לפי הראיות שנשמרו. זו אינה שומה מקצועית."
PRICE_BOUNDARY = (
    "מחיר הנכס בלבד, בלי פירוק, הובלה, התקנה ועלויות עסקה של המוכר. "
    "לא הופחתו ולא נוספו סכומי לוגיסטיקה בלי ראיה."
)
TAX_BASIS = "להשוואה: מחיר ללא מע״מ, רק אם המקור עצמו מציין זאת. לא מנחשים את המס."
MARKETING = "תקופת שיווק סבירה. זה אינו תרחיש מכירה מהירה."
PHOTO_LIMIT = "צילום מתעד שהפריט נראה. הוא לא מאמת מצב הפעלה, כיול, תקינות, או אפשרות פירוק."

PHOTO_NOT_PROOF = {
    "tested_condition",
    "operating_condition",
    "relocation_feasibility",
    "calibration",
    "physical_integrity",
}

INFO_LABELS = {
    "identification_unresolved": "הזיהוי לא הוכרע",
    "critical_missing": "חסר מידע חוסם",
    "provisional": "מספיק לניתוח ראשוני, עם הנחות",
    "sufficient": "מספיק להערכה שהוגדרה",
}
MARKET_LABELS = {
    "not_researched": "לא נחקר",
    "in_progress": "מחקר בתהליך",
    "insufficient": "אין די ראיות שוק",
    "provisional_range": "די ראיות לטווח ראשוני",
    "supported_range": "די ראיות לטווח נתמך",
}

FAMILY_LABELS = {
    "unknown": "לא זוהה",
    "generator": "גנרטור",
    "reactor": "כור או כלי תהליך",
    "lab_instrument": "מכשיר מעבדה",
    "agitator": "מערבל",
    "pump": "משאבה",
    "heat_exchanger": "מחליף חום",
    "cooling_tower": "מגדל קירור",
    "building": "מבנה יביל",
    "other_named": "ציוד מזוהה לפי התיאור",
    "mixed": "קבוצה מעורבת",
}


def _id(prefix: str, seed: str) -> str:
    return prefix + hashlib.sha256(seed.encode()).hexdigest()[:16]


def _req(key, impact, question, why, how, role, presence, priority="high"):
    return {
        "req_key": key,
        "impact": impact,
        "question": question,
        "why": why,
        "how_to": how,
        "contact_role": role,
        "site_presence": presence,
        "priority": priority,
    }


def _templates(family: str) -> list[dict]:
    identity = _req(
        "identity",
        "blocking",
        "מהו הציוד שמוערך כאן?",
        "בלי זיהוי אי אפשר לבחור מה משפיע על השווי, ואי אפשר לחפש ציוד דומה.",
        "אישור כתוב של התיאור, או צילום שלט הזיהוי עם קריאת התג.",
        "מנהלת הפרויקט",
        "either",
    )
    condition = _req(
        "tested_condition",
        "material",
        "מה מצב ההפעלה שנבדק, אם נבדק?",
        "ציוד שעובד וציוד שלא נבדק אינם אותו שווי. צילום לבדו לא קובע את זה.",
        "בדיקה באתר על ידי אדם מתאים, או מסמך בדיקה. לא מספיק לצלם את המעטפת.",
        "טכנאי תחזוקה באתר",
        "site",
    )
    if family == "unknown":
        return [identity]
    if family == "generator":
        return [
            identity,
            _req("rated_output", "material", "מה ההספק הנקוב?", "הספק שונה משנה את קבוצת ההשוואה.", "צילום שלט היצרן או דף נתונים.", "חשמלאי", "site"),
            _req("manufacturer", "material", "מי היצרן?", "בלי יצרן קשה למצוא ציוד בר-השוואה.", "שלט יצרן או מסמך.", "חשמלאי", "site"),
            _req("model", "material", "מה הדגם?", "דגם שונה יכול לשנות את השווי במידה רבה.", "שלט יצרן.", "חשמלאי", "site"),
            _req("operating_hours", "material", "כמה שעות עבודה רשומות?", "שעות עבודה משנות את הבלאי. אין להן נוסחת פחת קבועה.", "קריאת מונה שעות, או יומן תחזוקה.", "טכנאי תחזוקה באתר", "site"),
            _req("age", "helpful", "מתי הותקן או יוצר?", "גיל עוזר להקשר, אבל לא מחשבים ממנו אחוז פחת.", "מסמך או שלט עם תאריך.", "מנהלת הפרויקט", "either", "medium"),
            _req("maintenance", "material", "איזו תחזוקה ידועה?", "היסטוריית טיפול משנה את אי-הוודאות.", "יומן טיפול או אישור כתוב.", "טכנאי תחזוקה באתר", "either"),
            condition,
            _req("controls", "material", "איזה ציוד פיקוד נכלל?", "לוח פיקוד שנשאר או יוצא משנה את היקף הנכס.", "רשימה כתובה וצילום הלוח.", "חשמלאי", "site"),
        ]
    if family == "reactor":
        return [
            identity,
            _req("capacity", "material", "מה הנפח?", "נפח הוא הבדל מרכזי בין כלים.", "נתון מהשלט או מהתיק.", "טכנאי תחזוקה באתר", "either"),
            _req("material", "material", "ממה הכלי עשוי?", "חומר המבנה משנה עמידות ומחיר.", "מפרט או סימון על הכלי.", "טכנאי תחזוקה באתר", "site"),
            _req("design_rating", "material", "מה לחץ או טמפרטורת התכן?", "דירוג תכן משנה את קבוצת ההשוואה.", "שלט או מפרט.", "טכנאי תחזוקה באתר", "either"),
            _req("manufacturer", "material", "מי היצרן?", "יצרן עוזר למצוא ציוד דומה.", "שלט או מפרט.", "טכנאי תחזוקה באתר", "site"),
            condition,
            _req("included_parts", "material", "האם מערבל, מעטפת או פיקוד נכללים במכירה?", "רכיב שנשאר או יוצא משנה את מה שנמכר. זה לא נקבע משורת שכן בגיליון.", "אישור כתוב של מה כלול ומה לא.", "מנהלת הפרויקט", "either"),
            _req("documentation", "helpful", "יש תיק יצרן או בדיקות?", "מסמכים מורידים אי-ודאות. הם לא מחליפים בדיקת מצב.", "העתק המסמך.", "מנהלת הפרויקט", "remote", "medium"),
        ]
    if family == "lab_instrument":
        return [
            identity,
            _req("model", "material", "מה הדגם המדויק והתצורה?", "במכשיר מעבדה הדגם קובע את שוק היד השנייה.", "השדה שכבר בקובץ, או שלט אם חסר.", "אחראית מעבדה", "either"),
            _req("manufacturer", "material", "מי היצרן?", "יצרן ודגם יחד מזהים את המכשיר.", "הקובץ או השלט.", "אחראית מעבדה", "either"),
            _req("serial", "helpful", "מה המספר הסידורי?", "מספר סידורי מזהה את היחידה. הוא לא מחיר.", "הקובץ או השלט.", "אחראית מעבדה", "either", "medium"),
            _req("age", "helpful", "מתי הותקן?", "תאריך מהקובץ נשמר כפי שהוא. לא מחשבים ממנו פחת.", "שדה התאריך בקובץ, אם הוא תאריך אמיתי.", "אחראית מעבדה", "remote", "low"),
            _req("operating_condition", "material", "האם המכשיר נדלק ומסיים בדיקה רגילה?", "מצב עבודה משנה את השווי. מראה חיצוני לא מוכיח את זה.", "הפעלה קצרה על ידי איש מעבדה, עם תוצאה כתובה.", "אחראית מעבדה", "site"),
            _req("accessories", "helpful", "אילו אביזרים נכללים?", "ראש, מדפסת או ערכה יכולים להיות פריט נפרד.", "רשימה קצרה.", "אחראית מעבדה", "site", "medium"),
            _req("calibration", "material", "יש כיול או שירות בתוקף?", "כיול לא נגזר מתמונה.", "תעודת כיול או אישור שאין.", "אחראית מעבדה", "remote"),
            _req("software_license", "helpful", "האם רישיון התוכנה ניתן להעברה?", "רישיון שלא עובר עם המכשיר משנה את מה שהקונה מקבל.", "בדיקה מול הספק, בלי להמציא שם איש קשר.", "מנהלת הפרויקט", "remote", "medium"),
        ]
    if family == "agitator":
        return [
            identity,
            _req("manufacturer", "material", "מי יצרן המערבל?", "יצרן וסוג ההנעה משנים את ההשוואה.", "שלט, או אישור שהערה בקובץ היא אכן היצרן.", "טכנאי תחזוקה באתר", "site"),
            _req("rated_output", "material", "מה הספק המנוע?", "הספק הוא מאפיין מרכזי.", "שלט המנוע או המפרט שכבר בשורה.", "חשמלאי", "either"),
            _req("material", "material", "מה חומר החלקים הרטובים?", "חומר המבנה משנה את השימוש ואת השווי.", "המפרט בשורה או בדיקה.", "טכנאי תחזוקה באתר", "either"),
            condition,
            _req("included_parts", "material", "האם המערבל נמכר עם הכלי, או בנפרד?", "קשר לכלי לא נובע ממספר מערכת בגיליון. צריך שאישה תאמר זאת.", "אישור כתוב.", "מנהלת הפרויקט", "either"),
        ]
    if family == "pump":
        return [
            identity,
            _req("rated_output", "material", "מה הספיקה והעומד?", "ספיקה ולחץ מגדירים משאבה דומה.", "המפרט בשורה או השלט.", "טכנאי תחזוקה באתר", "either"),
            _req("manufacturer", "material", "מי היצרן?", "יצרן עוזר להשוואה. הערה בקובץ אינה בהכרח יצרן עד שמאשרים אותה.", "שלט או אישור.", "טכנאי תחזוקה באתר", "site"),
            _req("material", "material", "ממה המשאבה עשויה?", "חומר המבנה משנה עמידות.", "השדה בקובץ או השלט.", "טכנאי תחזוקה באתר", "either"),
            condition,
        ]
    if family == "building":
        return [
            identity,
            _req("dimensions", "material", "מה האורך והרוחב?", "מידה היא מאפיין מרכזי. אין כאן מחיר למטר ככלל.", "מדידה באתר.", "מנהלת הפרויקט", "site"),
            _req("construction", "material", "מה סוג הבנייה ומה מספר המודולים?", "בנייה ומודולים משנים את ההשוואה.", "צילום ורישום.", "טכנאי תחזוקה באתר", "site"),
            _req("physical_integrity", "material", "מה מצב הגג, הרצפה והמעטפת, כולל נזילות ידועות?", "מצב המעטפת לא נקבע מתמונה כללית בלבד.", "בדיקה ורשימת נזילות.", "טכנאי תחזוקה באתר", "site"),
            _req("included_parts", "material", "אילו מתקנים פנימיים כלולים?", "ציוד קבוע יכול להיות כלול או נפרד.", "רשימה.", "מנהלת הפרויקט", "site"),
            _req("relocation_feasibility", "material", "האם המבנה ניתן לפירוק ולהעברה?", "אפשרות ההעברה היא מאפיין של הנכס. היא נפרדת מהשאלה מי משלם על ההובלה. קרקע וזכות שימוש באתר לא נכללות.", "בדיקה או מסמך. לא מספיקה תמונה.", "טכנאי תחזוקה באתר", "site"),
        ]
    shared = [
        identity,
        _req("included_parts", "material", "מה כלול במכירה ומה לא?", "היקף הנכס קובע מה מעריכים.", "רשימה קצרה.", "מנהלת הפרויקט", "either"),
        condition,
    ]
    if family == "heat_exchanger":
        shared.insert(1, _req("capacity", "material", "מה שטח החלפת החום או התפוקה?", "גודל מחליף משנה את ההשוואה.", "המפרט בשורה או השלט.", "טכנאי תחזוקה באתר", "either"))
    return shared


def classify_family(row: dict) -> tuple[str, str, str]:
    if row.get("source_kind") == "lab" or str(row.get("identity_area") or "").startswith("lab:"):
        return "lab_instrument", "מכשיר מעבדה", "הרשימה היא רשימת המעבדה, לכן הסיווג הוא מכשיר מעבדה."
    text = (row.get("description") or "").strip()
    if not text:
        return "unknown", "", "אין תיאור בשורה. התג לבד לא קובע משפחה, ולא הופעל צ'ק-ליסט של סוג ציוד אחר."
    upper = text.upper()
    head = re.split(r"\bFOR\b", upper, maxsplit=1)[0]
    evidence = f"לפי התיאור בשורה: {text}"
    if re.search(r"\bPUMPS?\b", head):
        return "pump", "", evidence
    if "GENERATOR" in head and not any(word in upper for word in ("NITROGEN", "HYDROGEN", "OXYGEN", "GAS")):
        return "generator", "גנרטור חירום" if "EMERGENCY" in upper else "", evidence
    if "AGITATOR" in head:
        return "agitator", "", evidence
    if "HEAT EXCHANGER" in head:
        return "heat_exchanger", "", evidence
    if "COOLING TOWER" in head and "FAN" not in head:
        return "cooling_tower", "", evidence
    if "REACTOR" in head or re.search(r"\bVESSEL\b", head):
        return "reactor", "", evidence
    if any(phrase in head for phrase in ("RELOCATABLE", "MODULAR BUILDING", "PORTABLE BUILDING")):
        return "building", "", evidence
    return "other_named", "", evidence


def _specs_has(specs: str, pattern: str) -> str:
    text = specs or ""
    found = re.search(pattern, text, flags=re.IGNORECASE)
    return text.strip() if found else ""


def _date_like(value: str) -> str:
    text = (value or "").strip()
    if re.search(r"\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4}", text):
        return text
    return ""


def known_facts(row: dict, photo_count: int) -> list[dict]:
    sheet = row.get("sheet_name") or ""
    original = row.get("original_row")
    source = f"{row.get('source_file') or 'קובץ המלאי'} · {sheet} · שורה {original}"
    facts = []

    def add(key, label, value, field):
        if value is None or str(value).strip() == "":
            return
        facts.append({"key": key, "label": label, "value": str(value).strip(), "source": f"{source} · {field}"})

    add("tag", "תג", row.get("tag_original"), "תג")
    add("description", "תיאור", row.get("description"), "תיאור")
    add("manufacturer", "יצרן", row.get("manufacturer"), "יצרן")
    add("model", "דגם", row.get("model"), "דגם")
    add("serial", "מספר סידורי", row.get("serial"), "מספר סידורי")
    add("specs", "נתון טכני", row.get("specs"), "מפרט")
    add("material", "חומר", row.get("material"), "חומר")
    add("quantity", "כמות", row.get("quantity"), "כמות")
    add("remarks", "הערות מהקובץ", row.get("remarks"), "הערות")
    add("installation", "תאריך מהקובץ", _date_like(row.get("installation_raw") or ""), "תאריך")
    add("listed_area", "אזור ברשימה", row.get("listed_area"), "אזור")
    labels = row.get("labels") or []
    if labels:
        add("labels", "סימונים מהקובץ", ", ".join(labels), "סימון")
    raw = row.get("raw") or {}
    if "price" in raw and str(raw.get("price")).strip() != "":
        facts.append(
            {
                "key": "sheet_price_flag",
                "label": "עמודת price",
                "value": str(raw.get("price")),
                "source": source + " · price",
                "not_used": "זה לא מחיר מכירה ולא עלות רכישה. הוא לא נכנס לשווי ולא משמש לפחת.",
            }
        )
    if row.get("system_number"):
        facts.append(
            {
                "key": "system_number",
                "label": "מספר מערכת בקובץ",
                "value": row.get("system_number"),
                "source": source + " · System",
                "not_used": "זה שדה מהקובץ. הוא לא קובע מה נמכר יחד.",
            }
        )
    if photo_count:
        facts.append(
            {
                "key": "photos",
                "label": "צילומי סיור",
                "value": f"{photo_count} צילומים",
                "source": "קליטה בסיור",
                "not_used": PHOTO_LIMIT,
            }
        )
    return facts


def _auto_support(family: str, key: str, row: dict) -> tuple[str, list[dict]]:
    specs = row.get("specs") or ""
    evidence = []

    def hit(text, field):
        evidence.append({"value": text, "source": field, "from_file": True})
        return "supported", evidence

    if key == "identity":
        if family == "unknown":
            return "missing", []
        if row.get("description"):
            return hit(row["description"], "תיאור")
        return "missing", []
    if key == "rated_output":
        found = _specs_has(specs, r"\d+(?:[.,]\d+)?\s*K(?:W|VA)\b") or _specs_has(specs, r"\d+(?:[.,]\d+)?\s*M3/H") or _specs_has(specs, r"\d+(?:[.,]\d+)?\s*GPM")
        if found:
            return hit(found, "מפרט")
        return "missing", []
    if key == "capacity":
        found = _specs_has(specs, r"\d+(?:[.,]\d+)?\s*(?:LIT|L|M3|M2)\b")
        if found:
            return hit(found, "מפרט")
        return "missing", []
    if key == "design_rating":
        found = _specs_has(specs, r"\d+(?:[.,]\d+)?\s*(?:BAR|PSI)\b")
        if found:
            return hit(found, "מפרט")
        return "missing", []
    if key == "manufacturer" and (row.get("manufacturer") or "").strip():
        return hit(row["manufacturer"], "יצרן")
    if key == "model" and (row.get("model") or "").strip():
        return hit(row["model"], "דגם")
    if key == "serial" and (row.get("serial") or "").strip():
        return hit(row["serial"], "מספר סידורי")
    if key == "material" and (row.get("material") or "").strip():
        return hit(row["material"], "חומר")
    if key == "age":
        dated = _date_like(row.get("installation_raw") or "")
        if dated:
            return hit(dated, "תאריך התקנה")
        return "missing", []
    if key in PHOTO_NOT_PROOF:
        return "missing", []
    return "missing", []


def _row_public(conn, row_id: str) -> dict | None:
    from app.logic import public_row

    record = conn.execute("SELECT * FROM inventory_rows WHERE id = ?", (row_id,)).fetchone()
    if not record:
        return None
    source = conn.execute("SELECT display_name FROM source_files WHERE id = ?", (record["source_file_id"],)).fetchone()
    return public_row(record, source["display_name"] if source else "")


def _photo_count(conn, row_id: str) -> int:
    count = 0
    for photo in conn.execute("SELECT shows_json FROM photos"):
        shows = parse_json(photo["shows_json"], {})
        if shows.get("scope") == "rows" and row_id in (shows.get("row_ids") or []):
            count += 1
    if count:
        return count
    linked = conn.execute(
        """
        SELECT COUNT(*) AS n FROM photos p
        JOIN evidence_links l ON l.capture_id = p.capture_id
        WHERE l.inventory_row_id = ? AND l.review_status IN ('auto_linked', 'user_confirmed')
        """,
        (row_id,),
    ).fetchone()
    return linked["n"] if linked else 0


def preview_row(row: dict, photo_count: int = 0) -> dict:
    family, subtype, evidence = classify_family(row)
    requirements = []
    for item in _templates(family):
        status, support = _auto_support(family, item["req_key"], row)
        requirements.append({**item, "evidence_status": status, "evidence": support})
    info = _info_readiness(requirements)
    blocking = [item["question"] for item in requirements if item["impact"] == "blocking" and item["evidence_status"] == "missing"]
    material = [item["question"] for item in requirements if item["impact"] == "material" and item["evidence_status"] == "missing"]
    next_action = blocking[0] if blocking else (material[0] if material else "אפשר לאסוף ראיות שוק. עדיין אין טווח.")
    return {
        "row_id": row["id"],
        "tag": row.get("tag_original") or "",
        "description": row.get("description") or "",
        "sheet_name": row.get("sheet_name") or "",
        "original_row": row.get("original_row"),
        "listed_area": row.get("listed_area") or "",
        "family": family,
        "family_label": FAMILY_LABELS.get(family, family),
        "subtype": subtype,
        "family_evidence": evidence,
        "info_readiness": info,
        "info_label": INFO_LABELS[info],
        "market_readiness": "not_researched",
        "market_label": MARKET_LABELS["not_researched"],
        "blocking": blocking,
        "next_action": next_action,
        "latest_range": None,
        "latest_date": None,
        "photo_count": photo_count,
        "facts": known_facts(row, photo_count),
    }


def _info_readiness(requirements: list[dict]) -> str:
    if any(item["req_key"] == "identity" and item["evidence_status"] in {"missing", "conflict"} for item in requirements):
        return "identification_unresolved"
    if any(item["impact"] == "blocking" and item["evidence_status"] in {"missing", "conflict", "unable"} for item in requirements):
        return "critical_missing"
    if any(item["impact"] == "material" and item["evidence_status"] in {"missing", "conflict", "unable"} for item in requirements):
        return "provisional"
    return "sufficient"


def _subject_row(conn, subject_id: str):
    return conn.execute("SELECT * FROM valuation_subjects WHERE id = ?", (subject_id,)).fetchone()


def ensure_row_subject(conn, row_id: str, created_at: str | None = None) -> dict:
    created_at = created_at or now_iso()
    row = _row_public(conn, row_id)
    if not row or row["kind"] != "equipment":
        raise LookupError("אין שורת ציוד כזו.")
    subject_id = _id("VAL-", row_id)
    existing = _subject_row(conn, subject_id)
    family, subtype, evidence = classify_family(row)
    title = row.get("tag_original") or row.get("description") or "פריט בלי תג"
    if row.get("description") and row.get("tag_original"):
        title = f"{row['tag_original']} · {row['description']}"
    if not existing:
        conn.execute(
            """
            INSERT INTO valuation_subjects (
              id, kind, title, row_id, quantity_text, included_text, excluded_text,
              family, subtype, family_evidence, family_source, premise, condition_status,
              condition_text, market_text, currency, tax_basis, price_boundary, marketing_period,
              project_target, assumptions, limitations, research_status, method_preference,
              replacement_allowance, review_due, updated_at, created_at
            ) VALUES (
              ?, 'row', ?, ?, ?, ?, ?, ?, ?, ?, 'system', 'unset', 'unknown',
              '', '', '', ?, ?, ?, ?, '', ?, 'not_researched', 'unset', '', '', ?, ?
            )
            """,
            (
                subject_id,
                title,
                row_id,
                row.get("quantity") or "",
                "לפי שורת המלאי הזו בלבד. לא נוספו רכיבים שלא רשומים.",
                "קרקע וזכות שימוש באתר לא נכללות. פירוק, הובלה והתקנה לא נכללים במחיר.",
                family,
                subtype,
                evidence,
                TAX_BASIS,
                PRICE_BOUNDARY,
                MARKETING,
                PROJECT_TARGET,
                RESEARCH_LIMITATION,
                created_at,
                created_at,
            ),
        )
        conn.execute(
            "INSERT INTO valuation_members (subject_id, row_id, role) VALUES (?, ?, 'included')",
            (subject_id, row_id),
        )
    elif existing["family_source"] != "user":
        conn.execute(
            """
            UPDATE valuation_subjects
            SET title = ?, family = ?, subtype = ?, family_evidence = ?, quantity_text = ?, updated_at = ?
            WHERE id = ?
            """,
            (title, family, subtype, evidence, row.get("quantity") or "", created_at, subject_id),
        )
    _sync_requirements(conn, subject_id, created_at)
    _ensure_research_task(conn, subject_id, created_at)
    _record_version(conn, subject_id, created_at, reason="פתיחת כרטיס")
    return subject_payload(conn, subject_id)


def _sync_requirements(conn, subject_id: str, created_at: str) -> None:
    subject = _subject_row(conn, subject_id)
    row_ids = [row["row_id"] for row in conn.execute("SELECT row_id FROM valuation_members WHERE subject_id = ?", (subject_id,))]
    primary = _row_public(conn, subject["row_id"]) if subject["row_id"] else None
    family = subject["family"]
    template = _templates(family if subject["kind"] == "row" else ("mixed" if family == "mixed" else family))
    if subject["kind"] == "group":
        template = [
            _req(
                "group_scope",
                "blocking",
                "מה כלול בקבוצה ומה לא?",
                "בלי היקף אי אפשר להעריך את הקבוצה. שווי הקבוצה אינו סכום שווי הרכיבים.",
                "רשימת השורות הכלולות והחריגות.",
                "מנהלת הפרויקט",
                "either",
            )
        ] + [item for item in template if item["req_key"] != "identity"]
    keys = set()
    for item in template:
        keys.add(item["req_key"])
        status, support = ("missing", [])
        if primary and subject["kind"] == "row":
            status, support = _auto_support(family, item["req_key"], primary)
        req_id = _id("REQ-", f"{subject_id}|{item['req_key']}")
        current = conn.execute(
            "SELECT * FROM valuation_requirements WHERE subject_id = ? AND req_key = ?",
            (subject_id, item["req_key"]),
        ).fetchone()
        if current and current["answered_by"] == "user":
            conn.execute(
                """
                UPDATE valuation_requirements
                SET impact = ?, question = ?, why = ?, how_to = ?, contact_role = ?, site_presence = ?, priority = ?
                WHERE id = ?
                """,
                (item["impact"], item["question"], item["why"], item["how_to"], item["contact_role"], item["site_presence"], item["priority"], current["id"]),
            )
            continue
        evidence_json = json.dumps(support, ensure_ascii=False)
        if current:
            conn.execute(
                """
                UPDATE valuation_requirements
                SET impact = ?, question = ?, why = ?, how_to = ?, contact_role = ?, site_presence = ?,
                    priority = ?, evidence_status = ?, evidence_json = ?
                WHERE id = ?
                """,
                (
                    item["impact"], item["question"], item["why"], item["how_to"], item["contact_role"],
                    item["site_presence"], item["priority"], status, evidence_json, current["id"],
                ),
            )
            req_id = current["id"]
        else:
            conn.execute(
                """
                INSERT INTO valuation_requirements (
                  id, subject_id, req_key, impact, question, why, how_to, contact_role, site_presence,
                  priority, evidence_status, evidence_json, answer_text, na_reason, consequence, answered_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', '', '')
                """,
                (
                    req_id, subject_id, item["req_key"], item["impact"], item["question"], item["why"],
                    item["how_to"], item["contact_role"], item["site_presence"], item["priority"], status, evidence_json,
                ),
            )
        _sync_one_task(conn, subject, req_id, item, status, row_ids, created_at)
    for stale in conn.execute("SELECT * FROM valuation_requirements WHERE subject_id = ?", (subject_id,)):
        if stale["req_key"] in keys or stale["answered_by"] == "user":
            continue
        conn.execute(
            """
            UPDATE valuation_requirements
            SET impact = 'not_applicable', evidence_status = 'not_applicable',
                na_reason = CASE WHEN answer_text != '' THEN 'לא נדרש לסיווג המעודכן. תשובה קודמת נשמרה.'
                                 ELSE 'לא נדרש לסיווג המעודכן.' END
            WHERE id = ?
            """,
            (stale["id"],),
        )


def _sync_one_task(conn, subject, req_id: str, item: dict, status: str, row_ids: list[str], created_at: str) -> None:
    dedupe = f"val:{subject['id']}:{item['req_key']}"
    if status == "missing" and item["impact"] in {"blocking", "material"}:
        where = "באתר" if item["site_presence"] == "site" else ("מרחוק" if item["site_presence"] == "remote" else "באתר או מרחוק")
        upsert_review(
            conn,
            kind="valuation_gap",
            queue="valuation",
            dedupe_key=dedupe,
            question=item["question"],
            priority=item["priority"],
            capture_id=None,
            row_ids=row_ids,
            payload={
                "subject_id": subject["id"],
                "requirement_id": req_id,
                "req_key": item["req_key"],
                "why": item["why"],
                "how": item["how_to"],
                "contact_role": item["contact_role"],
                "site_presence": item["site_presence"],
                "where_label": where,
            },
            created_at=created_at,
        )
    elif status in {"supported", "not_applicable"}:
        conn.execute(
            """
            UPDATE review_items
            SET status = 'resolved', resolution = ?, updated_at = ?, missing_reason = NULL
            WHERE dedupe_key = ? AND status != 'resolved'
            """,
            ("הנתון כבר נתמך במקור, או שאינו נדרש.", created_at, dedupe),
        )


def _ensure_research_task(conn, subject_id: str, created_at: str) -> None:
    subject = _subject_row(conn, subject_id)
    row_ids = [row["row_id"] for row in conn.execute("SELECT row_id FROM valuation_members WHERE subject_id = ?", (subject_id,))]
    upsert_review(
        conn,
        kind="valuation_research",
        queue="valuation",
        dedupe_key=f"val:{subject_id}:research",
        question="לאסוף ראיות שוק ממקור שניתן לבדוק. במהדורה הזו אין חיפוש חי.",
        priority="high",
        capture_id=None,
        row_ids=row_ids,
        payload={
            "subject_id": subject_id,
            "why": "בלי עסקה, מודעה או הצעת ספק אין בסיס לטווח.",
            "how": "מוסיפים ראיה עם כתובת או מסמך, סוג מחיר, מטבע, ומה כלול במחיר.",
            "contact_role": "מנהלת הפרויקט",
            "site_presence": "remote",
            "where_label": "מרחוק",
            "limitation": RESEARCH_LIMITATION,
        },
        created_at=created_at,
    )
    if subject["research_status"] == "not_researched":
        return


def add_requirement(conn, subject_id: str, body: dict, created_at: str) -> dict:
    subject = _subject_row(conn, subject_id)
    if not subject:
        raise LookupError("אין כרטיס שווי.")
    question = (body.get("question") or "").strip()
    why = (body.get("why") or "").strip()
    how = (body.get("how") or "").strip()
    if len(question) < 4 or len(why) < 4 or len(how) < 4:
        raise ValueError("צריך שאלה, למה זה משנה, ואיך עונים. לא נוצרה משימה כללית.")
    key = "extra-" + hashlib.sha256(question.encode()).hexdigest()[:10]
    item = _req(
        key,
        body.get("impact") or "material",
        question,
        why,
        how,
        body.get("contact_role") or "מנהלת הפרויקט",
        body.get("site_presence") or "either",
        body.get("priority") or "medium",
    )
    if item["impact"] not in {"blocking", "material", "helpful"}:
        raise ValueError("סוג ההשפעה אינו מוכר.")
    req_id = _id("REQ-", f"{subject_id}|{key}")
    conn.execute(
        """
        INSERT INTO valuation_requirements (
          id, subject_id, req_key, impact, question, why, how_to, contact_role, site_presence,
          priority, evidence_status, evidence_json, answer_text, na_reason, consequence, answered_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'missing', '[]', '', '', '', 'user')
        """,
        (
            req_id, subject_id, key, item["impact"], question, why, how, item["contact_role"],
            item["site_presence"], item["priority"],
        ),
    )
    row_ids = [row["row_id"] for row in conn.execute("SELECT row_id FROM valuation_members WHERE subject_id = ?", (subject_id,))]
    if item["impact"] in {"blocking", "material"}:
        _sync_one_task(conn, subject, req_id, item, "missing", row_ids, created_at)
    _record_version(conn, subject_id, created_at, reason="נוספה דרישה מתוך המחקר")
    return subject_payload(conn, subject_id)


def answer_requirement(conn, requirement_id: str, body: dict, created_at: str) -> dict:
    req = conn.execute("SELECT * FROM valuation_requirements WHERE id = ?", (requirement_id,)).fetchone()
    if not req:
        raise LookupError("הדרישה לא נמצאה.")
    status = body.get("status") or ""
    text = (body.get("text") or "").strip()
    if status not in {"supported", "unable", "conflict", "not_applicable"}:
        raise ValueError("צריך לבחור: נתמך, לא ניתן להשיג, סתירה, או לא רלוונטי.")
    if status == "unable":
        if len(text) < 2:
            raise ValueError("\"לא ניתן להשיג\" אינו תשובה מאומתת. צריך לרשום מה המשמעות לשווי.")
        conn.execute(
            """
            UPDATE valuation_requirements
            SET evidence_status = 'unable', consequence = ?, answered_by = 'user'
            WHERE id = ?
            """,
            (text, requirement_id),
        )
        conn.execute(
            """
            UPDATE review_items
            SET missing_reason = ?, updated_at = ?, status = 'open'
            WHERE dedupe_key = ?
            """,
            (text, created_at, f"val:{req['subject_id']}:{req['req_key']}"),
        )
    elif status == "not_applicable":
        if len(text) < 2:
            raise ValueError("צריך סיבה למה הדרישה לא חלה.")
        conn.execute(
            """
            UPDATE valuation_requirements
            SET evidence_status = 'not_applicable', impact = 'not_applicable', na_reason = ?, answered_by = 'user'
            WHERE id = ?
            """,
            (text, requirement_id),
        )
        _close_req_review(conn, req, "סומן שלא רלוונטי: " + text, created_at)
    elif status == "conflict":
        if len(text) < 2:
            raise ValueError("צריך לרשום את שתי הטענות. אף אחת לא נמחקת.")
        prior = parse_json(req["evidence_json"], [])
        prior.append({"value": text, "source": "תשובה שסותרת את המקור", "from_file": False})
        conn.execute(
            """
            UPDATE valuation_requirements
            SET evidence_status = 'conflict', answer_text = ?, evidence_json = ?, answered_by = 'user'
            WHERE id = ?
            """,
            (text, json.dumps(prior, ensure_ascii=False), requirement_id),
        )
        conn.execute(
            "UPDATE review_items SET missing_reason = ?, updated_at = ?, status = 'open' WHERE dedupe_key = ?",
            ("נשארו שתי טענות. צריך הכרעה.", created_at, f"val:{req['subject_id']}:{req['req_key']}"),
        )
    else:
        if len(text) < 2:
            raise ValueError("צריך תשובה כתובה. קובץ לבדו לא סוגר את השאלה.")
        if req["req_key"] in PHOTO_NOT_PROOF and body.get("photo_only"):
            raise ValueError(PHOTO_LIMIT)
        prior = parse_json(req["evidence_json"], [])
        prior.append({"value": text, "source": "תשובה בכרטיס השווי", "from_file": False})
        conn.execute(
            """
            UPDATE valuation_requirements
            SET evidence_status = 'supported', answer_text = ?, evidence_json = ?, answered_by = 'user'
            WHERE id = ?
            """,
            (text, json.dumps(prior, ensure_ascii=False), requirement_id),
        )
        _close_req_review(conn, req, text, created_at)
    log_change(
        conn,
        actor=ACTOR_USER,
        action="valuation_answer",
        entity_type="valuation_requirement",
        entity_id=requirement_id,
        prior={"status": req["evidence_status"]},
        new={"status": status, "text": text},
        created_at=created_at,
    )
    _record_version(conn, req["subject_id"], created_at, reason="עודכנה דרישת מידע")
    return subject_payload(conn, req["subject_id"])


def _close_req_review(conn, req, resolution: str, created_at: str) -> None:
    conn.execute(
        """
        UPDATE review_items
        SET status = 'resolved', resolution = ?, updated_at = ?, missing_reason = NULL
        WHERE dedupe_key = ? AND status != 'resolved'
        """,
        (resolution, created_at, f"val:{req['subject_id']}:{req['req_key']}"),
    )


def save_requirement_file(conn, requirement_id: str, data: bytes, original_name: str, photo_dir: Path, thumb_dir: Path, created_at: str) -> dict:
    req = conn.execute("SELECT * FROM valuation_requirements WHERE id = ?", (requirement_id,)).fetchone()
    if not req:
        raise LookupError("הדרישה לא נמצאה.")
    if not data:
        raise ValueError("הקובץ ריק ולא נשמר.")
    file_id = str(uuid.uuid4())
    photo_dir.mkdir(parents=True, exist_ok=True)
    thumb_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(original_name or "photo.jpg").suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        suffix = ".jpg"
    stored = photo_dir / f"{file_id}{suffix}"
    stored.write_bytes(data)
    if stored.stat().st_size <= 0:
        stored.unlink(missing_ok=True)
        raise OSError("הקובץ לא נשמר.")
    thumb = thumb_dir / f"{file_id}.jpg"
    try:
        with Image.open(stored) as image:
            image = image.convert("RGB")
            image.thumbnail((480, 480))
            image.save(thumb, format="JPEG", quality=70)
    except (UnidentifiedImageError, OSError):
        thumb = None
    conn.execute(
        """
        INSERT INTO valuation_files (id, subject_id, requirement_id, stored_path, thumb_path, original_name, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (file_id, req["subject_id"], requirement_id, str(stored), str(thumb) if thumb else "", original_name or "", created_at),
    )
    proved = False
    message = "הקובץ נשמר על הדרישה הזו."
    if req["req_key"] in PHOTO_NOT_PROOF:
        message = PHOTO_LIMIT + " הדרישה נשארה פתוחה."
        conn.execute(
            "UPDATE review_items SET missing_reason = ?, updated_at = ? WHERE dedupe_key = ?",
            (message, created_at, f"val:{req['subject_id']}:{req['req_key']}"),
        )
    else:
        message = "הקובץ נשמר. כדי לסגור את השאלה צריך גם משפט שאומר מה רואים."
    return {"stored": True, "file_id": file_id, "proved_condition": proved, "message": message}


def update_basis(conn, subject_id: str, body: dict, created_at: str) -> dict:
    subject = _subject_row(conn, subject_id)
    if not subject:
        raise LookupError("אין כרטיס שווי.")
    incoming = body.get("updated_at")
    if incoming and subject["updated_at"] and incoming < subject["updated_at"]:
        raise ValueError("נשמרה בינתיים גרסה חדשה יותר. רעננו לפני שמירה.")
    premise = body.get("premise", subject["premise"])
    if premise not in {"unset", "continued_use", "relocation", "parts", "scrap"}:
        raise ValueError("הנחת המכירה אינה מוכרת.")
    condition_status = body.get("condition_status", subject["condition_status"])
    if condition_status not in {"unknown", "reported", "observed", "tested"}:
        raise ValueError("מצב הנכס צריך להיות מדווח, נצפה, נבדק, או לא ידוע.")
    if condition_status == "tested" and len((body.get("condition_text") or subject["condition_text"] or "").strip()) < 2:
        raise ValueError("מצב \"נבדק\" דורש מה נבדק ומה הייתה התוצאה. תמונה לא מספיקה.")
    family = body.get("family", subject["family"])
    if family not in FAMILY_LABELS:
        raise ValueError("הסיווג אינו מוכר.")
    changed_basis = (
        premise != subject["premise"]
        or condition_status != subject["condition_status"]
        or (body.get("condition_text") or "") != (subject["condition_text"] or "")
        or family != subject["family"]
        or (body.get("included_text") or subject["included_text"]) != subject["included_text"]
        or (body.get("excluded_text") or subject["excluded_text"]) != subject["excluded_text"]
    )
    research_status = body.get("research_status", subject["research_status"])
    if research_status not in {"not_researched", "in_progress"}:
        raise ValueError("אי אפשר לסמן את המחקר כגמור בלי ראיות. אפשר רק להתחיל לאסוף.")
    method_preference = body.get("method_preference", subject["method_preference"])
    if method_preference not in {"unset", "market", "replacement"}:
        raise ValueError("השיטה אינה מוכרת.")
    conn.execute(
        """
        UPDATE valuation_subjects SET
          premise = ?, condition_status = ?, condition_text = ?, market_text = ?, currency = ?,
          included_text = ?, excluded_text = ?, family = ?, family_source = ?,
          research_status = ?, method_preference = ?, replacement_allowance = ?, review_due = ?,
          assumptions = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            premise,
            condition_status,
            body.get("condition_text", subject["condition_text"]) or "",
            body.get("market_text", subject["market_text"]) or "",
            (body.get("currency", subject["currency"]) or "").upper(),
            body.get("included_text", subject["included_text"]) or "",
            body.get("excluded_text", subject["excluded_text"]) or "",
            family,
            "user" if family != subject["family"] or subject["family_source"] == "user" else subject["family_source"],
            research_status,
            method_preference,
            body.get("replacement_allowance", subject["replacement_allowance"]) or "",
            body.get("review_due", subject["review_due"]) or "",
            body.get("assumptions", subject["assumptions"]) or "",
            created_at,
            subject_id,
        ),
    )
    if family != subject["family"]:
        _sync_requirements(conn, subject_id, created_at)
    if changed_basis:
        conn.execute(
            """
            UPDATE valuation_versions
            SET outdated = 1, outdated_reason = ?
            WHERE subject_id = ? AND outdated = 0 AND scenario = 'normal_marketing'
            """,
            ("הזיהוי, הרכיבים, המצב או הנחת המכירה השתנו. הגרסה הקודמת נשמרה.", subject_id),
        )
    log_change(
        conn,
        actor=ACTOR_USER,
        action="valuation_basis",
        entity_type="valuation_subject",
        entity_id=subject_id,
        prior={"premise": subject["premise"], "family": subject["family"], "condition_status": subject["condition_status"]},
        new={"premise": premise, "family": family, "condition_status": condition_status},
        created_at=created_at,
    )
    _record_version(conn, subject_id, created_at, reason="עודכן בסיס השווי", force=changed_basis)
    return subject_payload(conn, subject_id)


def _norm_url(url: str) -> str:
    text = (url or "").strip().split("#")[0].strip().rstrip("/").lower()
    return text


def add_evidence(conn, subject_id: str, body: dict, created_at: str) -> dict:
    subject = _subject_row(conn, subject_id)
    if not subject:
        raise LookupError("אין כרטיס שווי.")
    title = (body.get("title") or "").strip()
    if len(title) < 2:
        raise ValueError("צריך כותרת למקור.")
    price_type = body.get("price_type") or ""
    if price_type not in {"asking", "transaction", "hammer", "buyer_total", "replacement", "buyer_offer", "specialist_opinion"}:
        raise ValueError("צריך סוג מחיר: מבוקש, עסקה, פטיש, סה״כ קונה, תחליף, הצעת קונה, או חוות דעת.")
    services = body.get("included_services") or "unknown"
    if services not in {"asset_only", "bundled", "unknown"}:
        raise ValueError("צריך לומר אם המחיר הוא לנכס בלבד, כולל שירותים, או לא ידוע.")
    tax = body.get("tax_treatment") or "unknown"
    if tax not in {"excluded_vat", "included_vat", "unknown"}:
        raise ValueError("טיפול המס אינו מוכר.")
    role = body.get("role") or "context"
    if role not in {"direct", "context", "excluded"}:
        raise ValueError("התפקיד אינו מוכר.")
    amount = body.get("price_amount")
    if amount is not None and amount != "":
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            raise ValueError("הסכום אינו מספר.")
    else:
        amount = None
    currency = (body.get("currency") or "").strip().upper()
    limitations = (body.get("limitations") or "").strip()
    exclusion = (body.get("exclusion_reason") or "").strip()
    if price_type == "buyer_offer":
        role = "context"
        limitations = (limitations + " " if limitations else "") + "הצעת קונה מעוניין אינה שומה ואינה נכנסת לטווח."
    if price_type == "replacement":
        role = "context"
        limitations = (limitations + " " if limitations else "") + "מחיר תחליף חדש אינו שווי מכירה, ולא חושב פחת."
    if services == "bundled":
        role = "context"
        limitations = (limitations + " " if limitations else "") + "המחיר כולל שירותים ולא הופרד. הוא לא ראיה ישירה למחיר הנכס בלבד."
    elif services == "unknown" and role == "direct":
        role = "context"
        limitations = (limitations + " " if limitations else "") + "לא ידוע אם המחיר כולל הובלה או התקנה, לכן הוא לא ראיה ישירה."
    if body.get("adjustment_percent") not in (None, "", 0):
        limitations = (limitations + " " if limitations else "") + "אחוז התאמה שנשלח לא הוחל. אין נוסחת פחת או בלאי."
    url = body.get("source_url") or ""
    duplicate_of = ""
    norm = _norm_url(url)
    signature = (title.casefold(), amount, currency, (body.get("seller_type") or "").strip().casefold())
    for prior in conn.execute("SELECT * FROM market_evidence WHERE subject_id = ?", (subject_id,)):
        if norm and _norm_url(prior["source_url"] or "") == norm:
            duplicate_of = prior["id"]
            break
        if signature[0] and signature == (
            (prior["title"] or "").casefold(),
            prior["price_amount"],
            prior["currency"] or "",
            (prior["seller_type"] or "").strip().casefold(),
        ):
            duplicate_of = prior["id"]
            break
    if duplicate_of:
        limitations = (limitations + " " if limitations else "") + "זו אותה מודעה או אותו נכס שכבר נרשם. היא לא נספרת שוב."
        role = "context"
    evidence_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO market_evidence (
          id, subject_id, source_url, source_document, title, research_date, listing_date, seller_type,
          identifiers, specifications, condition_text, location, price_text, price_amount, currency,
          tax_treatment, included_services, price_type, role, exclusion_reason, differences,
          adjustment_note, duplicate_of, limitations, added_by, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            evidence_id,
            subject_id,
            url,
            body.get("source_document") or "",
            title,
            body.get("research_date") or created_at[:10],
            body.get("listing_date") or "",
            body.get("seller_type") or "",
            body.get("identifiers") or "",
            body.get("specifications") or "",
            body.get("condition_text") or "",
            body.get("location") or "",
            body.get("price_text") or "",
            amount,
            currency,
            tax,
            services,
            price_type,
            role,
            exclusion,
            body.get("differences") or "",
            body.get("adjustment_note") or "",
            duplicate_of,
            limitations.strip(),
            ACTOR_USER,
            created_at,
        ),
    )
    if subject["research_status"] == "not_researched":
        conn.execute(
            "UPDATE valuation_subjects SET research_status = 'in_progress', updated_at = ? WHERE id = ?",
            (created_at, subject_id),
        )
    log_change(
        conn,
        actor=ACTOR_USER,
        action="market_evidence",
        entity_type="valuation_subject",
        entity_id=subject_id,
        prior=None,
        new={"id": evidence_id, "title": title, "price_type": price_type, "role": role, "duplicate_of": duplicate_of},
        created_at=created_at,
    )
    _record_version(conn, subject_id, created_at, reason="נוספה ראיית שוק", force=True)
    return subject_payload(conn, subject_id)


def update_evidence(conn, evidence_id: str, body: dict, created_at: str) -> dict:
    row = conn.execute("SELECT * FROM market_evidence WHERE id = ?", (evidence_id,)).fetchone()
    if not row:
        raise LookupError("הראיה לא נמצאה.")
    role = body.get("role", row["role"])
    if role not in {"direct", "context", "excluded"}:
        raise ValueError("התפקיד אינו מוכר.")
    exclusion = body.get("exclusion_reason", row["exclusion_reason"]) or ""
    if role == "excluded" and len(exclusion.strip()) < 2:
        raise ValueError("ראיה שהוצאה נשארת ברשימה, אבל צריך סיבה.")
    if role == "direct" and (row["included_services"] != "asset_only" or row["price_type"] in {"buyer_offer", "replacement"} or row["duplicate_of"]):
        raise ValueError("אי אפשר לסמן את הראיה הזו כישירה בלי בסיס מחיר נפרד לנכס בלבד.")
    conn.execute(
        """
        UPDATE market_evidence
        SET role = ?, exclusion_reason = ?, differences = ?, adjustment_note = ?, limitations = ?
        WHERE id = ?
        """,
        (
            role,
            exclusion,
            body.get("differences", row["differences"]) or "",
            body.get("adjustment_note", row["adjustment_note"]) or "",
            body.get("limitations", row["limitations"]) or "",
            evidence_id,
        ),
    )
    _record_version(conn, row["subject_id"], created_at, reason="עודכנה ראיית שוק", force=True)
    return subject_payload(conn, row["subject_id"])


def _analysis(conn, subject_id: str) -> dict:
    subject = _subject_row(conn, subject_id)
    requirements = [dict(row) for row in conn.execute("SELECT * FROM valuation_requirements WHERE subject_id = ?", (subject_id,))]
    for item in requirements:
        item["evidence"] = parse_json(item.pop("evidence_json"), [])
    evidence = [dict(row) for row in conn.execute("SELECT * FROM market_evidence WHERE subject_id = ? ORDER BY created_at", (subject_id,))]
    info = _info_readiness(requirements)
    direct_groups: dict[tuple, list] = {}
    for item in evidence:
        item["counts"] = False
        if item["duplicate_of"] or item["role"] != "direct":
            continue
        if item["price_type"] not in {"asking", "transaction", "hammer", "buyer_total"}:
            continue
        if item["included_services"] != "asset_only" or item["price_amount"] is None or not item["currency"]:
            continue
        key = (item["price_type"], item["currency"], item["tax_treatment"] or "unknown")
        direct_groups.setdefault(key, []).append(item)
    chosen = None
    for key, group in direct_groups.items():
        if len(group) >= 2 and (chosen is None or len(group) > len(chosen[1])):
            chosen = (key, group)
    range_low = range_high = None
    range_label = RANGE_ABSTAIN
    status = "unresolved"
    confidence = ""
    confidence_why = ""
    method = "לא נבחרה שיטת שווי."
    method_why = "אין די ראיות שוק מאותו סוג מחיר כדי לתמוך בטווח. לא חושב ממוצע, לא הוחל אחוז, ולא נלקחה עמודת price מהגיליון."
    market = "insufficient" if evidence else ("in_progress" if subject["research_status"] == "in_progress" else "not_researched")
    if chosen:
        key, group = chosen
        prices = [item["price_amount"] for item in group]
        for item in group:
            item["counts"] = True
        range_low = min(prices)
        range_high = max(prices)
        range_label = f"{range_low:g}–{range_high:g} {key[1]}"
        price_type = key[0]
        method = "השוואה לשוק"
        type_he = {"asking": "מחיר מבוקש", "transaction": "עסקה", "hammer": "מחיר פטיש", "buyer_total": "סה״כ קונה"}[price_type]
        method_why = (
            f"הטווח הוא המחיר הנמוך והגבוה של {len(group)} ראיות מסוג {type_he}, "
            f"במטבע {key[1]}, למחיר נכס בלבד. לא חושב ממוצע ולא הוחל אחוז התאמה."
        )
        if price_type == "asking":
            status = "provisional"
            market = "provisional_range"
            confidence = "low"
            confidence_why = "אלה מחירי בקשה, לא עסקאות סגורות. הטווח ראשוני."
        else:
            status = "supported"
            market = "supported_range"
            confidence = "medium" if len(group) >= 2 else "low"
            confidence_why = "יש יותר מתצפית אחת מאותו סוג מחיר. זו עדיין לא שומה."
        if key[2] == "unknown":
            confidence = "low"
            confidence_why += " הטיפול במס לא צוין במקורות."
        if info in {"identification_unresolved", "critical_missing"}:
            status = "unresolved"
            range_low = range_high = None
            range_label = RANGE_ABSTAIN
            market = "insufficient"
            confidence = ""
            confidence_why = ""
            method_why += " הזיהוי או היקף הנכס עדיין חוסמים, ולכן לא פורסם טווח."
    if subject["method_preference"] == "replacement" and range_low is None:
        allowance = (subject["replacement_allowance"] or "").strip()
        method = "עלות תחליף"
        if len(allowance) < 8:
            method_why = "עלות תחליף דורשת ניכוי נתמך לבלאי ולאי-התאמה. בלי זה לא מחשבים פחת ולא נותנים טווח."
        else:
            method_why = "נרשמה הנחת בלאי, אבל אין מקור מחיר תחליף שניתן להשתמש בו. עדיין אין טווח."
        market = "insufficient" if evidence or info == "sufficient" else market
    if range_low is None and info == "sufficient" and market == "not_researched":
        market = "insufficient"
    assumptions = [
        MARKETING,
        PRICE_BOUNDARY,
        TAX_BASIS,
        f"יעד הפרויקט {PROJECT_TARGET} גלוי, והוא לא הופך את האומדן למכירה מהירה.",
    ]
    if subject["premise"] == "unset":
        assumptions.append("הנחת המכירה לא נבחרה. לא נבחרה מכירת חלקים או גרוטאות רק כי חסר מידע.")
    if subject["condition_status"] == "unknown":
        assumptions.append("מצב ההפעלה לא ידוע. הציוד לא תומחר כעובד ולא כתקול.")
    elif subject["condition_status"] == "tested" and subject["condition_text"]:
        assumptions.append("מצב שנבדק, לפי מה שנרשם: " + subject["condition_text"])
    sensitivities = []
    for item in requirements:
        if item["evidence_status"] == "unable" and item["consequence"]:
            sensitivities.append(item["question"] + " — לא ניתן להשיג: " + item["consequence"])
        elif item["evidence_status"] in {"missing", "conflict"} and item["impact"] in {"blocking", "material"}:
            sensitivities.append(item["question"])
    if not evidence:
        sensitivities.append(RESEARCH_LIMITATION)
    for item in evidence:
        if item["price_type"] == "buyer_offer":
            sensitivities.append("הצעת קונה נשמרה מחוץ לטווח: " + item["title"])
        if item["duplicate_of"]:
            sensitivities.append("מודעה כפולה לא נספרה: " + item["title"])
    return {
        "info_readiness": info,
        "market_readiness": market,
        "range_low": range_low,
        "range_high": range_high,
        "currency": chosen[0][1] if chosen and range_low is not None else "",
        "range_label": range_label,
        "status": status,
        "confidence": confidence,
        "confidence_why": confidence_why,
        "method": method,
        "method_why": method_why,
        "assumptions": " ".join(assumptions),
        "sensitivities": sensitivities,
        "evidence": evidence,
        "requirements": requirements,
        "counted_ids": [item["id"] for item in evidence if item.get("counts")],
    }


def _record_version(conn, subject_id: str, created_at: str, reason: str, force: bool = False, scenario: str = "normal_marketing") -> None:
    card = _analysis(conn, subject_id)
    latest = conn.execute(
        """
        SELECT * FROM valuation_versions
        WHERE subject_id = ? AND scenario = ?
        ORDER BY version_no DESC LIMIT 1
        """,
        (subject_id, scenario),
    ).fetchone()
    snapshot = (
        card["range_label"],
        card["status"],
        card["info_readiness"],
        card["market_readiness"],
        card["method"],
        card["assumptions"],
    )
    if latest and not force:
        previous = (latest["range_label"], latest["status"], latest["info_readiness"], latest["market_readiness"], latest["method"], latest["assumptions"])
        if previous == snapshot:
            return
    version_no = (latest["version_no"] + 1) if latest else 1
    if latest and not latest["outdated"] and force and reason.startswith("הזיהוי"):
        pass
    conn.execute(
        """
        INSERT INTO valuation_versions (
          id, subject_id, version_no, status, info_readiness, market_readiness, range_low, range_high,
          currency, range_label, confidence, confidence_why, method, method_why, assumptions, sensitivities,
          outdated, outdated_reason, author, reviewer_name, reviewer_scope, scenario, evidence_ids_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '', ?, '', '', ?, ?, ?)
        """,
        (
            _id("VER-", f"{subject_id}|{scenario}|{version_no}|{created_at}"),
            subject_id,
            version_no,
            card["status"],
            card["info_readiness"],
            card["market_readiness"],
            card["range_low"],
            card["range_high"],
            card["currency"],
            card["range_label"],
            card["confidence"],
            card["confidence_why"],
            card["method"],
            card["method_why"],
            card["assumptions"],
            "\n".join(card["sensitivities"]),
            ACTOR_SYSTEM + " · " + APPRAISAL_LABEL,
            scenario,
            json.dumps(card["counted_ids"]),
            created_at,
        ),
    )


def add_time_scenario(conn, subject_id: str, created_at: str) -> dict:
    if not _subject_row(conn, subject_id):
        raise LookupError("אין כרטיס שווי.")
    latest = conn.execute(
        "SELECT version_no FROM valuation_versions WHERE subject_id = ? AND scenario = 'time_constrained' ORDER BY version_no DESC LIMIT 1",
        (subject_id,),
    ).fetchone()
    version_no = (latest["version_no"] + 1) if latest else 1
    note = (
        f"תרחיש נפרד ליעד {PROJECT_TARGET}. זה אינו האומדן של תקופת השיווק הרגילה, "
        "ולא חושב ממנו מחיר מכירה מהירה. אין ראיה להפרש."
    )
    conn.execute(
        """
        INSERT INTO valuation_versions (
          id, subject_id, version_no, status, info_readiness, market_readiness, range_low, range_high,
          currency, range_label, confidence, confidence_why, method, method_why, assumptions, sensitivities,
          outdated, outdated_reason, author, reviewer_name, reviewer_scope, scenario, evidence_ids_json, created_at
        ) VALUES (?, ?, ?, 'unresolved', 'provisional', 'insufficient', NULL, NULL, '', ?, '', '', ?, ?, ?, ?, 0, '', ?, '', '', 'time_constrained', '[]', ?)
        """,
        (
            _id("VER-", f"{subject_id}|time|{version_no}"),
            subject_id,
            version_no,
            RANGE_ABSTAIN,
            "תרחיש מוגבל בזמן",
            note,
            note,
            "אין מחיר.",
            ACTOR_SYSTEM + " · " + APPRAISAL_LABEL,
            created_at,
        ),
    )
    return subject_payload(conn, subject_id)


def record_specialist(conn, subject_id: str, body: dict, created_at: str) -> dict:
    name = (body.get("reviewer_name") or "").strip()
    scope = (body.get("reviewer_scope") or "").strip()
    if len(name) < 2 or len(scope) < 2:
        raise ValueError("צריך שם של בודק אמיתי ומה נבדק. לא נרשם שהמומחה כבר בדק.")
    card = _analysis(conn, subject_id)
    latest = conn.execute(
        "SELECT version_no FROM valuation_versions WHERE subject_id = ? AND scenario = 'normal_marketing' ORDER BY version_no DESC LIMIT 1",
        (subject_id,),
    ).fetchone()
    version_no = (latest["version_no"] + 1) if latest else 1
    conn.execute(
        """
        INSERT INTO valuation_versions (
          id, subject_id, version_no, status, info_readiness, market_readiness, range_low, range_high,
          currency, range_label, confidence, confidence_why, method, method_why, assumptions, sensitivities,
          outdated, outdated_reason, author, reviewer_name, reviewer_scope, scenario, evidence_ids_json, created_at
        ) VALUES (?, ?, ?, 'specialist_reviewed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '', ?, ?, ?, 'normal_marketing', ?, ?)
        """,
        (
            _id("VER-", f"{subject_id}|spec|{version_no}|{created_at}"),
            subject_id,
            version_no,
            card["info_readiness"],
            card["market_readiness"],
            card["range_low"],
            card["range_high"],
            card["currency"],
            card["range_label"],
            card["confidence"],
            card["confidence_why"],
            card["method"],
            card["method_why"],
            card["assumptions"],
            "\n".join(card["sensitivities"]),
            name,
            name,
            scope,
            json.dumps(card["counted_ids"]),
            created_at,
        ),
    )
    return subject_payload(conn, subject_id)


def create_group(conn, row_ids: list[str], title: str, created_at: str) -> dict:
    cleaned = []
    for row_id in row_ids:
        row = _row_public(conn, row_id)
        if row and row["kind"] == "equipment" and row_id not in cleaned:
            cleaned.append(row_id)
    if len(cleaned) < 2:
        raise ValueError("קבוצה צריכה לפחות שתי שורות ציוד קיימות. לא נוצר מלאי חדש.")
    families = {classify_family(_row_public(conn, row_id))[0] for row_id in cleaned}
    family = families.pop() if len(families) == 1 else "mixed"
    subject_id = _id("VALG-", "|".join(sorted(cleaned)))
    if _subject_row(conn, subject_id):
        return subject_payload(conn, subject_id)
    conn.execute(
        """
        INSERT INTO valuation_subjects (
          id, kind, title, row_id, quantity_text, included_text, excluded_text,
          family, subtype, family_evidence, family_source, premise, condition_status,
          condition_text, market_text, currency, tax_basis, price_boundary, marketing_period,
          project_target, assumptions, limitations, research_status, method_preference,
          replacement_allowance, review_due, updated_at, created_at
        ) VALUES (
          ?, 'group', ?, '', ?, ?, ?, ?, '', ?, 'system', 'unset', 'unknown',
          '', '', '', ?, ?, ?, ?, ?, ?, 'not_researched', 'unset', '', '', ?, ?
        )
        """,
        (
            subject_id,
            title.strip() or "קבוצה",
            str(len(cleaned)),
            "השורות שסומנו כלולות. שווי הקבוצה אינו סכום שווי הרכיבים.",
            "תשתית משותפת לא נספרת במלואה בכמה קבוצות.",
            family,
            "הקבוצה הוגדרה ידנית. היא אינה נגזרת ממספר מערכת בגיליון.",
            TAX_BASIS,
            PRICE_BOUNDARY,
            MARKETING,
            PROJECT_TARGET,
            "שווי הקבוצה אינו סכום הרכיבים. קיבוץ אחר הוא תרחיש, לא תוספת למלאי.",
            RESEARCH_LIMITATION,
            created_at,
            created_at,
        ),
    )
    conn.executemany(
        "INSERT INTO valuation_members (subject_id, row_id, role) VALUES (?, ?, 'included')",
        [(subject_id, row_id) for row_id in cleaned],
    )
    _sync_requirements(conn, subject_id, created_at)
    _ensure_research_task(conn, subject_id, created_at)
    _record_version(conn, subject_id, created_at, reason="קבוצה")
    return subject_payload(conn, subject_id)


def subject_payload(conn, subject_id: str) -> dict:
    subject = _subject_row(conn, subject_id)
    if not subject:
        raise LookupError("אין כרטיס שווי.")
    card = _analysis(conn, subject_id)
    members = []
    for member in conn.execute("SELECT * FROM valuation_members WHERE subject_id = ?", (subject_id,)):
        row = _row_public(conn, member["row_id"])
        if not row:
            continue
        photos = []
        for photo in conn.execute("SELECT id, capture_id, role, shows_json FROM photos"):
            shows = parse_json(photo["shows_json"], {})
            linked = conn.execute(
                "SELECT 1 FROM evidence_links WHERE capture_id = ? AND inventory_row_id = ?",
                (photo["capture_id"], member["row_id"]),
            ).fetchone()
            if (shows.get("scope") == "rows" and member["row_id"] in (shows.get("row_ids") or [])) or linked:
                photos.append({"id": photo["id"], "capture_id": photo["capture_id"], "role": photo["role"] or ""})
        members.append(
            {
                "row_id": member["row_id"],
                "role": member["role"],
                "tag": row.get("tag_original") or "",
                "description": row.get("description") or "",
                "sheet_name": row.get("sheet_name") or "",
                "original_row": row.get("original_row"),
                "listed_area": row.get("listed_area") or "",
                "source_file": row.get("source_file") or "",
                "facts": known_facts(row, _photo_count(conn, member["row_id"])),
                "photos": photos,
            }
        )
    versions = []
    for version in conn.execute(
        "SELECT * FROM valuation_versions WHERE subject_id = ? ORDER BY created_at, version_no",
        (subject_id,),
    ):
        item = dict(version)
        item["evidence_ids"] = parse_json(item.pop("evidence_ids_json"), [])
        versions.append(item)
    files = [
        {"id": row["id"], "requirement_id": row["requirement_id"], "original_name": row["original_name"]}
        for row in conn.execute("SELECT id, requirement_id, original_name FROM valuation_files WHERE subject_id = ?", (subject_id,))
    ]
    tasks = []
    for review in conn.execute("SELECT * FROM review_items WHERE queue = 'valuation' AND status != 'resolved'"):
        payload = parse_json(review["payload_json"], {})
        if payload.get("subject_id") == subject_id:
            tasks.append(
                {
                    "id": review["id"],
                    "question": review["question"],
                    "priority": review["priority"],
                    "status": review["status"],
                    "missing_reason": review["missing_reason"] or "",
                    "payload": payload,
                }
            )
    review_due = subject["review_due"] or ""
    stale = bool(review_due) and review_due < created_today()
    return {
        "id": subject["id"],
        "kind": subject["kind"],
        "title": subject["title"],
        "row_id": subject["row_id"] or "",
        "quantity_text": subject["quantity_text"] or "",
        "included_text": subject["included_text"] or "",
        "excluded_text": subject["excluded_text"] or "",
        "family": subject["family"],
        "family_label": FAMILY_LABELS.get(subject["family"], subject["family"]),
        "subtype": subject["subtype"] or "",
        "family_evidence": subject["family_evidence"] or "",
        "family_source": subject["family_source"],
        "premise": subject["premise"],
        "condition_status": subject["condition_status"],
        "condition_text": subject["condition_text"] or "",
        "market_text": subject["market_text"] or "",
        "currency": subject["currency"] or "",
        "tax_basis": subject["tax_basis"] or "",
        "price_boundary": subject["price_boundary"] or "",
        "marketing_period": subject["marketing_period"] or "",
        "project_target": subject["project_target"],
        "project_target_note": f"יעד הפרויקט {PROJECT_TARGET} גלוי. הוא לא הופך את האומדן למכירה מהירה.",
        "assumptions": subject["assumptions"] or "",
        "limitations": subject["limitations"] or RESEARCH_LIMITATION,
        "research_status": subject["research_status"],
        "research_limitation": RESEARCH_LIMITATION,
        "method_preference": subject["method_preference"],
        "replacement_allowance": subject["replacement_allowance"] or "",
        "review_due": review_due,
        "stale": stale,
        "updated_at": subject["updated_at"],
        "members": members,
        "requirements": card["requirements"],
        "evidence": card["evidence"],
        "tasks": tasks,
        "files": files,
        "card": {
            "subject": subject["title"],
            "quantity": subject["quantity_text"] or "",
            "included": subject["included_text"] or "",
            "excluded": subject["excluded_text"] or "",
            "basis": {
                "condition_status": subject["condition_status"],
                "condition_text": subject["condition_text"] or "",
                "premise": subject["premise"],
                "market": subject["market_text"] or "לא צוין בקבצים",
                "date": subject["updated_at"][:10],
                "currency": card["currency"] or subject["currency"] or "לא נקבע",
                "tax_basis": subject["tax_basis"],
                "price_boundary": subject["price_boundary"],
                "marketing_period": subject["marketing_period"],
                "project_target": PROJECT_TARGET,
            },
            "range_label": card["range_label"],
            "range_low": card["range_low"],
            "range_high": card["range_high"],
            "status": card["status"],
            "info_readiness": card["info_readiness"],
            "info_label": INFO_LABELS[card["info_readiness"]],
            "market_readiness": card["market_readiness"],
            "market_label": MARKET_LABELS[card["market_readiness"]],
            "confidence": card["confidence"],
            "confidence_why": card["confidence_why"],
            "method": card["method"],
            "method_why": card["method_why"],
            "assumptions": card["assumptions"],
            "sensitivities": card["sensitivities"],
            "appraisal_label": APPRAISAL_LABEL,
            "counted_evidence_ids": card["counted_ids"],
        },
        "versions": versions,
        "double_count_note": "שווי קבוצה או מערכת אינו סכום הרכיבים. תשתית משותפת לא נספרת כמה פעמים.",
        "labels": {"info": INFO_LABELS, "market": MARKET_LABELS, "family": FAMILY_LABELS},
    }


def created_today() -> str:
    return now_iso()[:10]


def overview(conn, *, area: str = "", family: str = "", info: str = "", q: str = "", limit: int = 40, offset: int = 0) -> dict:
    stored = {row["row_id"]: row for row in conn.execute("SELECT * FROM valuation_subjects WHERE kind = 'row' AND row_id IS NOT NULL")}
    items = []
    needle = q.strip().casefold()
    for record in conn.execute("SELECT * FROM inventory_rows WHERE kind = 'equipment' ORDER BY sheet_name, original_row"):
        from app.logic import public_row

        source = ""
        row = public_row(record, source)
        if area and (row["listed_area"] or row["sheet_name"]) != area and row["sheet_name"] != area:
            continue
        if needle and needle not in " ".join([row["tag_original"], row["description"], row["model"], row["sheet_name"]]).casefold():
            continue
        preview = preview_row(row, 0)
        saved = stored.get(row["id"])
        if saved:
            payload_family = saved["family"]
            preview["family"] = payload_family
            preview["family_label"] = FAMILY_LABELS.get(payload_family, payload_family)
            latest = conn.execute(
                """
                SELECT range_label, created_at, market_readiness, info_readiness
                FROM valuation_versions
                WHERE subject_id = ? AND scenario = 'normal_marketing'
                ORDER BY version_no DESC LIMIT 1
                """,
                (saved["id"],),
            ).fetchone()
            if latest:
                preview["info_readiness"] = latest["info_readiness"]
                preview["info_label"] = INFO_LABELS.get(latest["info_readiness"], latest["info_readiness"])
                preview["market_readiness"] = latest["market_readiness"]
                preview["market_label"] = MARKET_LABELS.get(latest["market_readiness"], latest["market_readiness"])
                preview["latest_range"] = latest["range_label"]
                preview["latest_date"] = latest["created_at"][:10]
            preview["subject_id"] = saved["id"]
        if family and preview["family"] != family:
            continue
        if info and preview["info_readiness"] != info:
            continue
        items.append({key: preview[key] for key in (
            "row_id", "tag", "description", "sheet_name", "original_row", "listed_area", "family",
            "family_label", "info_readiness", "info_label", "market_readiness", "market_label",
            "blocking", "next_action", "latest_range", "latest_date",
        )})
    total = len(items)
    window = items[offset:offset + limit]
    return {
        "project_target": PROJECT_TARGET,
        "project_target_note": f"יעד הפרויקט {PROJECT_TARGET} גלוי. הוא לא הופך את האומדן למכירה מהירה.",
        "research_limitation": RESEARCH_LIMITATION,
        "appraisal_label": APPRAISAL_LABEL,
        "total": total,
        "items": window,
    }


def visit_preparation(conn) -> dict:
    groups: dict[str, list] = {}
    for review in conn.execute("SELECT * FROM review_items WHERE queue = 'valuation' AND status = 'open' ORDER BY priority"):
        payload = parse_json(review["payload_json"], {})
        row_ids = parse_json(review["row_ids_json"], [])
        area = "לא ידוע"
        if row_ids:
            row = conn.execute("SELECT listed_area, sheet_name FROM inventory_rows WHERE id = ?", (row_ids[0],)).fetchone()
            if row:
                area = row["listed_area"] or row["sheet_name"] or "לא ידוע"
        role = payload.get("contact_role") or "מנהלת הפרויקט"
        key = f"{area} · {role}"
        groups.setdefault(key, []).append(
            {
                "id": review["id"],
                "question": review["question"],
                "why": payload.get("why") or "",
                "how": payload.get("how") or "",
                "where": payload.get("where_label") or "",
                "priority": review["priority"],
                "subject_id": payload.get("subject_id") or "",
                "requirement_id": payload.get("requirement_id") or "",
                "row_ids": row_ids,
                "area": area,
                "contact_role": role,
            }
        )
    return {
        "note": "הרשימה כוללת רק שאלות שווי שנפתחו מכרטיס. היא לא מייצרת משימה לכל שדה ריק במלאי.",
        "groups": [{"label": key, "tasks": value} for key, value in sorted(groups.items())],
    }


def comparables(conn, subject_id: str | None = None) -> dict:
    query = "SELECT * FROM market_evidence"
    args: tuple = ()
    if subject_id:
        query += " WHERE subject_id = ?"
        args = (subject_id,)
    rows = []
    for row in conn.execute(query + " ORDER BY created_at", args):
        item = dict(row)
        item["counts_as_observation"] = bool(
            item["role"] == "direct"
            and not item["duplicate_of"]
            and item["included_services"] == "asset_only"
            and item["price_type"] in {"asking", "transaction", "hammer", "buyer_total"}
            and item["price_amount"] is not None
        )
        rows.append(item)
    return {"research_limitation": RESEARCH_LIMITATION, "items": rows}


def save_commercial(conn, row_id: str, body: dict, created_at: str) -> dict:
    if not conn.execute("SELECT id FROM inventory_rows WHERE id = ?", (row_id,)).fetchone():
        raise LookupError("השורה לא נמצאה.")
    conn.execute(
        """
        INSERT INTO commercial_positions (row_id, offer_text, target_text, note, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(row_id) DO UPDATE SET
          offer_text = excluded.offer_text,
          target_text = excluded.target_text,
          note = excluded.note,
          updated_at = excluded.updated_at
        """,
        (row_id, body.get("offer_text") or "", body.get("target_text") or "", body.get("note") or "", created_at),
    )
    return commercial_payload(conn, row_id)


def commercial_payload(conn, row_id: str) -> dict:
    row = conn.execute("SELECT * FROM commercial_positions WHERE row_id = ?", (row_id,)).fetchone()
    return {
        "row_id": row_id,
        "offer_text": row["offer_text"] if row else "",
        "target_text": row["target_text"] if row else "",
        "note": row["note"] if row else "",
        "separated": "ההצעה ומחיר היעד לא נכנסים לכרטיס השווי ולא לחישוב הטווח.",
    }
