"""One-asset value estimate from stored evidence and current listings.

Figures are the low and high of prices that were actually found. No average,
no percentage, and no number when a figure has no source.
"""

from __future__ import annotations

import re
import urllib.parse
import urllib.request
from collections import defaultdict

MARKET_KINDS = {"asking", "comparable", "dealer"}
OFFER_KINDS = {"offer"}
SALE_KINDS = {"auction"}
CONTEXT_KINDS = {"purchase", "replacement"}
COUNTING_KINDS = MARKET_KINDS | OFFER_KINDS | SALE_KINDS

KIND_PHRASES = (
    ("purchase", ("purchase price", "purchased for", "invoice", "מחיר רכישה", "חשבונית")),
    ("replacement", ("replacement cost", "replacement", "list price", "עלות תחליף", "תחליף", "מחיר מחירון")),
    ("auction", ("auction", "hammer price", "מכירה פומבית", "מחיר פטיש")),
    ("offer", ("buyer offer", "offer", "bid", "הצעת קונה")),
    ("dealer", ("dealer", "סוחר")),
    ("asking", ("asking price", "asking", "for sale", "quotation", "הצעת מחיר", "מבוקש", "למכירה")),
    ("comparable", ("comparable", "listing", "מודעה", "השוואה")),
)

CURRENCY_WORDS = {
    "EUR": "EUR",
    "USD": "USD",
    "GBP": "GBP",
    "ILS": "ILS",
    "NIS": "ILS",
    "CAD": "CAD",
    "AUD": "AUD",
    "CHF": "CHF",
    "€": "EUR",
    "$": "USD",
    "£": "GBP",
    "₪": "ILS",
    "אירו": "EUR",
    "דולר": "USD",
    "דולרים": "USD",
    "שקל": "ILS",
    "שקלים": "ILS",
}

INSUFFICIENT = "Insufficient information to estimate value reliably."
LABX = "https://www.labx.com/search?sw="


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def _tokens(value: str) -> list[str]:
    return [item for item in re.split(r"[^A-Za-z0-9]+", value or "") if len(item) >= 3]


def format_amount(amount: float) -> str:
    if float(amount).is_integer():
        return f"{int(amount):,}"
    return f"{amount:,.2f}"


def money_span(low: float, high: float, currency: str) -> str:
    if low == high:
        return f"{format_amount(low)} {currency}"
    return f"{format_amount(low)}–{format_amount(high)} {currency}"


def _parse_number(raw: str) -> float | None:
    text = raw.replace(" ", "").replace(",", "")
    try:
        value = float(text)
    except ValueError:
        return None
    if value < 50 or value > 50_000_000:
        return None
    return value


def valuation_amounts(text: str) -> list[dict]:
    """Prices whose nearby words say what kind of money they are."""
    if not text:
        return []
    pattern = re.compile(
        r"(?:(?<![A-Za-z])(?P<cur1>EUR|USD|GBP|ILS|NIS|CAD|AUD|CHF)(?![A-Za-z])|[€$£₪])\s*"
        r"(?P<num1>\d{1,3}(?:[, ]\d{3})+|\d{4,9})(?:\.(?P<dec1>\d{1,2}))?"
        r"|(?P<num2>\d{1,3}(?:[, ]\d{3})+|\d{4,9})(?:\.(?P<dec2>\d{1,2}))?\s*"
        r"(?:(?<![A-Za-z])(?P<cur2>EUR|USD|GBP|ILS|NIS|CAD|AUD|CHF)(?![A-Za-z])|[€$£₪]|אירו|דולרים|דולר|שקלים|שקל)",
        re.IGNORECASE,
    )
    found: list[dict] = []
    seen: set[tuple] = set()
    for match in pattern.finditer(text):
        raw_number = match.group("num1") or match.group("num2")
        decimals = match.group("dec1") or match.group("dec2") or ""
        amount = _parse_number(raw_number + (("." + decimals) if decimals else ""))
        code = (match.group("cur1") or match.group("cur2") or "").casefold()
        currency = CURRENCY_WORDS.get(code) or CURRENCY_WORDS.get(code.upper())
        if amount is None or not currency:
            continue
        window = text[max(0, match.start() - 180) : match.start()]
        kind = _kind_in(window)
        if not kind:
            continue
        key = (kind, currency, round(amount, 2))
        if key in seen:
            continue
        seen.add(key)
        snippet = re.sub(r"\s+", " ", text[max(0, match.start() - 80) : match.end() + 40]).strip()
        found.append({"kind": kind, "currency": currency, "amount": amount, "snippet": snippet[:180]})
        if len(found) >= 6:
            break
    return found


def _kind_in(window: str) -> str:
    haystack = window.casefold()
    best_kind = ""
    best_at = -1
    for kind, phrases in KIND_PHRASES:
        for phrase in phrases:
            at = haystack.rfind(phrase.casefold())
            if at > best_at:
                best_at = at
                best_kind = kind
    return best_kind


def _field(part: str, name: str) -> str:
    matches = re.findall(rf',{name}:"([^"]*)"', part)
    return matches[-1] if matches else ""


def parse_labx_listings(html: str, manufacturer: str, model: str, source_url: str) -> list[dict]:
    maker_tokens = _tokens(manufacturer)
    model_norm = _norm(model)
    if not maker_tokens or len(model_norm) < 3 or not html:
        return []
    hits: list[dict] = []
    seen: set[tuple] = set()
    for part in html.split('{id:"listing-')[1:]:
        title = _field(part, "title") or _field(part, "name")
        listing_model = _field(part, "model")
        listing_maker = _field(part, "manufacturer")
        condition = _field(part, "condition")
        identity = " ".join([title, listing_model, listing_maker])
        if not all(_norm(token) in _norm(identity) for token in maker_tokens):
            continue
        title_model = _norm(title + " " + listing_model)
        if model_norm not in title_model:
            continue
        price_match = re.search(r",price:(-?\d+(?:\.\d+)?)", part)
        currency_match = re.search(r',currency:"([A-Z]{3})"', part)
        if not price_match or not currency_match:
            continue
        amount = _parse_number(price_match.group(1))
        currency = currency_match.group(1)
        if amount is None or currency not in {"USD", "EUR", "GBP", "ILS", "CAD", "AUD", "CHF"}:
            continue
        exact = _norm(listing_model) == model_norm
        key = (currency, round(amount, 2), _norm(title)[:80])
        if key in seen:
            continue
        seen.add(key)
        listing_id = re.search(r"unique_id:(\d+)", part)
        detail = title or model
        if condition:
            detail = f"{detail} · {condition}"
        hits.append(
            {
                "kind": "asking",
                "currency": currency,
                "amount": amount,
                "title": title or f"{manufacturer} {model}",
                "detail": detail,
                "url": source_url,
                "source": "LabX",
                "exact": exact,
                "listing_id": listing_id.group(1) if listing_id else "",
            }
        )
    hits.sort(key=lambda item: (0 if item["exact"] else 1, item["amount"]))
    exact_hits = [item for item in hits if item["exact"]]
    chosen = exact_hits or hits
    return chosen[:4]


def research_market(manufacturer: str, model: str) -> dict:
    query = f"{manufacturer} {model}".strip()
    url = LABX + urllib.parse.quote_plus(query)
    if len(_tokens(manufacturer)) == 0 or len(_norm(model)) < 3:
        return {"query": query, "url": "", "hits": [], "note": "לא בוצע חיפוש שוק כי חסר יצרן או דגם."}
    try:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept": "text/html",
                "Accept-Language": "en",
            },
        )
        with urllib.request.urlopen(request, timeout=12) as response:
            html = response.read().decode("utf-8", "replace")
    except Exception:
        return {"query": query, "url": url, "hits": [], "note": "חיפוש השוק לא הושלם."}
    hits = parse_labx_listings(html, manufacturer, model, url)
    if hits:
        note = "נבדקו מודעות LabX שכוללות את היצרן והדגם."
    else:
        note = "חיפוש ב-LabX לפי היצרן והדגם לא מצא מודעה עם מחיר תואם."
    return {"query": query, "url": url, "hits": hits, "note": note}


def _span(amounts: list[float]) -> tuple[float, float] | None:
    if not amounts:
        return None
    return min(amounts), max(amounts)


def _basis_line(anchor: dict) -> dict:
    return {
        "label": anchor.get("label") or anchor.get("title") or anchor["kind"],
        "detail": anchor.get("detail") or "",
        "url": anchor.get("url") or "",
        "amount_text": money_span(anchor["amount"], anchor["amount"], anchor["currency"]),
    }


def build_estimate(context: dict, research: dict | None) -> dict:
    research = research or {"hits": [], "note": "", "url": "", "query": ""}
    manufacturer = (context.get("manufacturer") or "").strip()
    model = (context.get("model") or "").strip()
    anchors: list[dict] = []
    for note in context.get("notes") or []:
        amount = _parse_number(str(note.get("amount") or ""))
        currency = (note.get("currency") or "").upper()
        kind = note.get("kind") or ""
        if amount is None or currency not in CURRENCY_WORDS.values() or kind not in COUNTING_KINDS | CONTEXT_KINDS:
            continue
        anchors.append(
            {
                "kind": kind,
                "currency": currency,
                "amount": amount,
                "title": note.get("title") or note.get("source") or kind,
                "detail": note.get("detail") or "",
                "url": note.get("url") or "",
                "label": note.get("label") or "",
                "origin": "stored",
            }
        )
    for hit in research.get("hits") or []:
        if hit.get("kind") not in MARKET_KINDS:
            continue
        identity = _norm(" ".join([hit.get("title") or "", hit.get("detail") or "", hit.get("label") or ""]))
        if not all(_norm(token) in identity for token in _tokens(manufacturer)):
            continue
        if _norm(model) not in identity:
            continue
        anchors.append({**hit, "origin": "market", "label": hit.get("title") or "LabX"})

    counts: dict[str, int] = defaultdict(int)
    for anchor in anchors:
        if anchor["kind"] in COUNTING_KINDS:
            counts[anchor["currency"]] += 1
    currency = ""
    if counts:
        currency = max(counts, key=lambda code: (counts[code], code == "USD", code))

    def of_kind(kinds: set[str]) -> list[dict]:
        return [item for item in anchors if item["kind"] in kinds and item["currency"] == currency]

    market = of_kind(MARKET_KINDS)
    offers = of_kind(OFFER_KINDS)
    sales = of_kind(SALE_KINDS)
    market_span = _span([item["amount"] for item in market])
    deal_span = _span([item["amount"] for item in offers + sales])
    offer_span = _span([item["amount"] for item in offers])
    ready = bool(market_span and offer_span and deal_span)

    component_tags = [tag for tag in context.get("component_tags") or [] if tag]
    scope = ""
    if component_tags:
        shown = ", ".join(component_tags[:8])
        extra = f" ועוד {len(component_tags) - 8}" if len(component_tags) > 8 else ""
        scope = f"ההערכה היא ליחידה הנמכרת כולה, כולל הרכיבים המקושרים: {shown}{extra}. לא חושב שווי נפרד לרכיב."

    other_currencies = sorted({item["currency"] for item in anchors if item["kind"] in COUNTING_KINDS and item["currency"] != currency})
    basis: list[dict] = []
    if ready:
        used_market = sorted(market, key=lambda item: item["amount"])
        shown_market = used_market if len(used_market) <= 4 else [used_market[0], used_market[-1]]
        for item in shown_market:
            basis.append(_basis_line({**item, "label": item.get("label") or "מחיר היצע"}))
        if len(used_market) > 4:
            basis.append({"label": "מחירי היצע נוספים", "detail": f"עוד {len(used_market) - 2} מחירים באותו מטבע נכנסו לטווח.", "url": "", "amount_text": ""})
        seen_urls = set()
        for item in used_market:
            url = item.get("url") or ""
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            basis.append({"label": "מקור חיצוני", "detail": "מודעות שתאמו את היצרן והדגם ונכנסו לטווח.", "url": url, "amount_text": ""})
        for item in sorted(offers, key=lambda item: item["amount"])[:4]:
            basis.append(_basis_line({**item, "label": item.get("label") or "הצעת קונה"}))
        for item in sorted(sales, key=lambda item: item["amount"])[:3]:
            basis.append(_basis_line({**item, "label": item.get("label") or "תוצאת מכירה"}))
        for kind, label in (("purchase", "מחיר רכישה"), ("replacement", "עלות תחליף")):
            for item in [anchor for anchor in anchors if anchor["kind"] == kind][:1]:
                basis.append(_basis_line({**item, "label": label}))
        if context.get("year"):
            basis.append({"label": "גיל / התקנה", "detail": str(context["year"]), "url": "", "amount_text": ""})
        if context.get("condition"):
            basis.append({"label": "מצב", "detail": str(context["condition"])[:180], "url": "", "amount_text": ""})
        if context.get("labels"):
            basis.append({"label": "תווית מקור", "detail": ", ".join(context["labels"]), "url": "", "amount_text": ""})
        if other_currencies:
            basis.append({"label": "מטבע אחר", "detail": "יש סכומים ב-" + ", ".join(other_currencies) + " שלא הומרו ולא נכנסו לטווח.", "url": "", "amount_text": ""})
        if scope:
            basis.append({"label": "היקף", "detail": scope, "url": "", "amount_text": ""})

    required: list[str] = []
    helpful: list[str] = []
    if not ready:
        if not market_span:
            required.append("מחיר השוואה, מודעה, או מחיר מבוקש של אותו יצרן ודגם")
        if not offers and not sales:
            required.append("הצעה שהתקבלה או תוצאת מכירה")
        elif not offers:
            required.append("הצעת קונה, כדי להעריך מכירה מהירה")
        if not market_span and not manufacturer:
            required.append("יצרן")
        if not market_span and not model:
            required.append("דגם")
        if other_currencies and market_span and not offer_span:
            required.append("הצעה או מודעה באותו מטבע. לא בוצעה המרה.")
    if not manufacturer and "יצרן" not in required:
        helpful.append("יצרן")
    if not model and "דגם" not in required:
        helpful.append("דגם")
    if not context.get("year"):
        helpful.append("שנת ייצור או תאריך התקנה")
    if not context.get("condition"):
        helpful.append("מצב נוכחי")
    if not context.get("specs"):
        helpful.append("קיבולת או מפרט טכני")
    if not context.get("photo_count"):
        helpful.append("תמונות")
    if not context.get("operational"):
        helpful.append("סטטוס תפעולי")
    if not any(item["kind"] == "purchase" for item in anchors):
        helpful.append("מחיר רכישה")
    if not any(item["kind"] == "replacement" for item in anchors):
        helpful.append("עלות תחליף")
    if manufacturer and model and (not market_span) and "יצרן" not in required:
        pass

    research_note = research.get("note") or ""
    if not manufacturer or not model:
        research_note = "לא בוצע חיפוש שוק כי חסר יצרן או דגם."

    identity = " ".join(part for part in (manufacturer, model) if part).strip()
    if ready and market_span and offer_span and deal_span and currency:
        fair_low, fair_high = market_span
        expected_low, expected_high = deal_span
        quick_low = quick_high = min(item["amount"] for item in offers)
        same_offer = len(offers) == 1 and not sales
        reasoning_bits = []
        if identity:
            reasoning_bits.append(f"הזיהוי שנבדק הוא {identity}.")
        reasoning_bits.append(
            f"שווי השוק ההוגן הוא {money_span(fair_low, fair_high, currency)}, לפי {len(market)} מחירי היצע או השוואה. לא חושב ממוצע."
        )
        reasoning_bits.append(
            f"מחיר המכירה הצפוי הוא {money_span(expected_low, expected_high, currency)}, לפי הצעות ותוצאות מכירה שנשמרו."
        )
        if same_offer:
            reasoning_bits.append("יש הצעה אחת, ולכן מחיר המכירה המהירה זהה לה. לא הופחת אחוז.")
        else:
            reasoning_bits.append(f"שווי המכירה המהירה הוא {money_span(quick_low, quick_high, currency)}, ההצעה הנמוכה ביותר. לא הופחת אחוז.")
        if scope:
            reasoning_bits.append(scope)
        figures = [
            {
                "key": "fair",
                "label": "שווי שוק הוגן",
                "text": money_span(fair_low, fair_high, currency),
                "explanation": f"{len(market)} מחירי היצע או השוואה ב-{currency}. זה המחיר הנמוך והגבוה, בלי ממוצע.",
            },
            {
                "key": "expected",
                "label": "מחיר מכירה צפוי",
                "text": money_span(expected_low, expected_high, currency),
                "explanation": "המחיר הנמוך והגבוה של הצעות קונה ותוצאות מכירה שנשמרו.",
            },
            {
                "key": "quick",
                "label": "שווי מכירה מהירה",
                "text": money_span(quick_low, quick_high, currency),
                "explanation": "ההצעה הנמוכה ביותר שנשמרה. לא הופחת אחוז נוסף.",
            },
        ]
        return {
            "status": "estimated",
            "headline": "הערכה לפי הראיות שנמצאו",
            "message": "",
            "currency": currency,
            "figures": figures,
            "reasoning": " ".join(reasoning_bits),
            "basis": basis,
            "required": [],
            "helpful": helpful,
            "scope": scope,
            "research_note": research_note,
            "research_url": research.get("url") or "",
            "component_tags": component_tags,
            "separate_component_values": False,
        }

    return {
        "status": "insufficient",
        "headline": "אין די מידע כדי להעריך שווי באופן אמין.",
        "message": INSUFFICIENT,
        "currency": "",
        "figures": [],
        "reasoning": "",
        "basis": [],
        "required": required,
        "helpful": helpful,
        "scope": scope,
        "research_note": research_note,
        "research_url": research.get("url") or "",
        "component_tags": component_tags,
        "separate_component_values": False,
    }
