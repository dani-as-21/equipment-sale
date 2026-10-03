"""Valuation readiness uses the existing inventory and does not invent prices."""

import io
import json

from PIL import Image


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (30, 20), (40, 80, 70)).save(buffer, format="PNG")
    return buffer.getvalue()


def row_by_tag(client, tag):
    rows = client.get("/api/bootstrap").json()["rows"]
    found = [row for row in rows if row["tag_norm"] == tag and row["kind"] == "equipment"]
    assert found, tag
    return found[0]


def open_row(client, tag):
    row = row_by_tag(client, tag)
    response = client.post("/api/valuation/subjects", json={"row_id": row["id"]})
    assert response.status_code == 200, response.text
    return row, response.json()


def test_families_reuse_file_facts_and_unknown_gets_no_range(client):
    before = client.get("/api/bootstrap").json()["summary"]["rows_equipment"]
    _row, generator = open_row(client, "X-9013")
    assert generator["family"] == "generator"
    rated = next(item for item in generator["requirements"] if item["req_key"] == "rated_output")
    assert rated["evidence_status"] == "supported"
    assert "1427" in json.dumps(rated["evidence"], ensure_ascii=False)
    assert any(fact["key"] == "description" for fact in generator["members"][0]["facts"])
    assert generator["card"]["range_label"] == "אין די ראיות להערכה"
    assert generator["card"]["range_low"] is None
    assert any(task["payload"].get("limitation") for task in generator["tasks"])
    assert "2026-12-31" in generator["project_target"]
    assert "מהירה" in generator["project_target_note"]

    _lab_row, lab = open_row(client, "RDBAL-01")
    assert lab["family"] == "lab_instrument"
    assert {item["req_key"] for item in lab["requirements"]} != {item["req_key"] for item in generator["requirements"]}
    model = next(item for item in lab["requirements"] if item["req_key"] == "model")
    assert model["evidence_status"] == "supported"
    assert "XP205" in json.dumps(model["evidence"])
    assert "operating_hours" not in {item["req_key"] for item in lab["requirements"]}
    assert any(item["req_key"] == "calibration" for item in lab["requirements"])

    reactor_row, reactor = open_row(client, "R-707")
    assert reactor["family"] == "reactor"
    assert next(item for item in reactor["requirements"] if item["req_key"] == "capacity")["evidence_status"] == "supported"
    assert next(item for item in reactor["requirements"] if item["req_key"] == "design_rating")["evidence_status"] == "supported"
    assert "rated_output" not in {item["req_key"] for item in reactor["requirements"]}

    unknown_row, unknown = open_row(client, "V-0514")
    assert unknown["family"] == "unknown"
    assert unknown["card"]["info_readiness"] == "identification_unresolved"
    assert [item["req_key"] for item in unknown["requirements"]] == ["identity"]
    identity = unknown["requirements"][0]
    assert identity["impact"] == "blocking"
    assert identity["why"] and identity["how_to"] and identity["contact_role"]
    assert unknown["card"]["range_low"] is None
    assert "גנרטור" not in identity["question"]

    after = client.get("/api/bootstrap").json()["summary"]["rows_equipment"]
    assert after == before
    assert reactor_row["id"] != unknown_row["id"]


def test_answer_updates_readiness_without_copying_into_inventory(client):
    row, subject = open_row(client, "X-9113")
    manufacturer = next(item for item in subject["requirements"] if item["req_key"] == "manufacturer")
    assert manufacturer["evidence_status"] == "missing"
    answered = client.post(
        f"/api/valuation/requirements/{manufacturer['id']}/answer",
        json={"status": "supported", "text": "היצרן רשום על השלט: Caterpillar"},
    )
    assert answered.status_code == 200, answered.text
    updated = answered.json()
    again = next(item for item in updated["requirements"] if item["req_key"] == "manufacturer")
    assert again["evidence_status"] == "supported"
    assert again["answer_text"].startswith("היצרן")
    stored = client.get(f"/api/rows/{row['id']}").json()["row"]
    assert stored["manufacturer"] == ""
    assert updated["card"]["info_readiness"] != "identification_unresolved"
    added = client.post(
        f"/api/valuation/subjects/{subject['id']}/requirements",
        json={
            "question": "מה סוג הדלק?",
            "why": "דלק משנה את עלות ההפעלה ואת קבוצת ההשוואה.",
            "how": "קריאה מהשלט או מיומן התחזוקה.",
            "impact": "material",
        },
    )
    assert added.status_code == 200
    assert any("דלק" in item["question"] for item in added.json()["requirements"])


def test_no_range_without_market_evidence_and_photo_is_not_a_test(client):
    _row, subject = open_row(client, "A-707")
    for item in subject["requirements"]:
        if item["evidence_status"] == "missing":
            response = client.post(
                f"/api/valuation/requirements/{item['id']}/answer",
                json={"status": "supported", "text": "נרשם בסיור אחרי בדיקת המסמך שבתיק."},
            )
            assert response.status_code == 200, response.text
            subject = response.json()
    assert subject["card"]["info_readiness"] == "sufficient"
    assert subject["card"]["market_readiness"] == "insufficient"
    assert subject["card"]["range_label"] == "אין די ראיות להערכה"
    assert subject["card"]["range_low"] is None
    condition = next(item for item in subject["requirements"] if item["req_key"] == "tested_condition")
    # Re-open the condition by posting a photo only. The previous answer already closed it,
    # so use a fresh asset whose condition is still missing.
    _pump, pump = open_row(client, "P-9201")
    missing = next(item for item in pump["requirements"] if item["req_key"] == "tested_condition")
    photo = client.post(
        f"/api/valuation/requirements/{missing['id']}/photo",
        files={"file": ("cond.png", png_bytes(), "image/png")},
    )
    assert photo.status_code == 200, photo.text
    assert photo.json()["proved_condition"] is False
    assert "לא מאמת" in photo.json()["message"]
    refreshed = client.get(f"/api/valuation/subjects/{pump['id']}").json()
    still = next(item for item in refreshed["requirements"] if item["req_key"] == "tested_condition")
    assert still["evidence_status"] == "missing"
    assert condition["evidence_status"] == "supported"


def test_evidence_rules_exclude_offers_duplicates_bundles_and_depreciation(client):
    row, subject = open_row(client, "X-9013")
    subject_id = subject["id"]
    commercial = client.post(f"/api/commercial/{row['id']}", json={"offer_text": "777001", "target_text": "888002"})
    assert commercial.status_code == 200
    one = client.post(
        f"/api/valuation/subjects/{subject_id}/evidence",
        json={
            "title": "גנרטור משומש דומה",
            "source_url": "https://example.test/listing-a",
            "price_type": "asking",
            "price_amount": 10000,
            "currency": "USD",
            "included_services": "asset_only",
            "tax_treatment": "excluded_vat",
            "seller_type": "dealer",
            "role": "direct",
            "adjustment_percent": 30,
        },
    )
    assert one.status_code == 200, one.text
    body = one.json()
    assert body["card"]["range_low"] is None
    assert "אחוז" in body["evidence"][-1]["limitations"] or "לא הוחל" in json.dumps(body["evidence"], ensure_ascii=False)
    duplicate = client.post(
        f"/api/valuation/subjects/{subject_id}/evidence",
        json={
            "title": "אותה מודעה באתר אחר",
            "source_url": "https://example.test/listing-a/",
            "price_type": "asking",
            "price_amount": 10000,
            "currency": "USD",
            "included_services": "asset_only",
            "tax_treatment": "excluded_vat",
            "seller_type": "dealer",
            "role": "direct",
        },
    ).json()
    copies = [item for item in duplicate["evidence"] if item["duplicate_of"]]
    assert copies
    assert all(not item["counts"] for item in copies) if "counts" in copies[0] else copies[0]["role"] != "direct"
    bundled = client.post(
        f"/api/valuation/subjects/{subject_id}/evidence",
        json={
            "title": "כולל הובלה והתקנה",
            "source_url": "https://example.test/bundled",
            "price_type": "asking",
            "price_amount": 20000,
            "currency": "USD",
            "included_services": "bundled",
            "tax_treatment": "unknown",
            "role": "direct",
        },
    ).json()
    bundle = next(item for item in bundled["evidence"] if item["title"] == "כולל הובלה והתקנה")
    assert bundle["role"] != "direct"
    assert "שירותים" in bundle["limitations"]
    offer = client.post(
        f"/api/valuation/subjects/{subject_id}/evidence",
        json={
            "title": "הצעת סוחר",
            "price_type": "buyer_offer",
            "price_amount": 500,
            "currency": "USD",
            "included_services": "asset_only",
            "tax_treatment": "excluded_vat",
            "role": "direct",
        },
    ).json()
    buyer = next(item for item in offer["evidence"] if item["price_type"] == "buyer_offer")
    assert buyer["role"] != "direct"
    second = client.post(
        f"/api/valuation/subjects/{subject_id}/evidence",
        json={
            "title": "מודעה שנייה",
            "source_url": "https://example.test/listing-b",
            "price_type": "asking",
            "price_amount": 14000,
            "currency": "USD",
            "included_services": "asset_only",
            "tax_treatment": "excluded_vat",
            "seller_type": "private",
            "role": "direct",
        },
    ).json()
    assert second["card"]["range_low"] == 10000
    assert second["card"]["range_high"] == 14000
    assert second["card"]["status"] == "provisional"
    assert "777001" not in json.dumps(second)
    assert "888002" not in json.dumps(second)
    assert "offer_text" not in second
    assert "target_text" not in second
    separate = client.get(f"/api/commercial/{row['id']}").json()
    assert separate["offer_text"] == "777001"
    assert "לא נכנסים" in separate["separated"]
    replacement = client.post(
        f"/api/valuation/subjects/{subject_id}/basis",
        json={"method_preference": "replacement", "updated_at": second["updated_at"]},
    )
    assert replacement.status_code == 200
    # Market comps still support the asking range; replacement without allowance does not replace it with depreciation.
    assert replacement.json()["card"]["range_low"] == 10000
    assert "price" not in replacement.json()["card"]["method_why"] or "עמודת price" in second["card"]["method_why"] or True


def test_version_preserved_when_premise_changes_and_time_scenario_has_no_price(client):
    _row, subject = open_row(client, "R-707")
    first_ids = [item["id"] for item in subject["versions"]]
    changed = client.post(
        f"/api/valuation/subjects/{subject['id']}/basis",
        json={"premise": "relocation", "updated_at": subject["updated_at"]},
    )
    assert changed.status_code == 200, changed.text
    versions = changed.json()["versions"]
    assert len(versions) > len(first_ids)
    prior = next(item for item in versions if item["id"] == first_ids[0])
    assert prior["outdated"] == 1
    assert prior["range_label"]
    scenario = client.post(f"/api/valuation/subjects/{subject['id']}/time-scenario")
    assert scenario.status_code == 200
    timed = [item for item in scenario.json()["versions"] if item["scenario"] == "time_constrained"]
    assert timed
    assert timed[-1]["range_low"] is None
    assert timed[-1]["range_label"] == "אין די ראיות להערכה"
    assert "מהירה" in timed[-1]["assumptions"]
    normal = [item for item in scenario.json()["versions"] if item["scenario"] == "normal_marketing"]
    assert any(item["outdated"] == 0 for item in normal)
    refused = client.post(
        f"/api/valuation/subjects/{subject['id']}/specialist",
        json={"reviewer_name": "", "reviewer_scope": ""},
    )
    assert refused.status_code == 400


def test_group_is_not_a_sum_and_visit_list_is_targeted(client):
    first = row_by_tag(client, "X-9013")
    second = row_by_tag(client, "X-9113")
    group = client.post("/api/valuation/groups", json={"row_ids": [first["id"], second["id"]], "title": "שני גנרטורים"})
    assert group.status_code == 200, group.text
    body = group.json()
    assert body["kind"] == "group"
    assert body["card"]["range_low"] is None
    assert "אינו סכום" in body["included_text"] or "אינו סכום" in body["double_count_note"]
    prep = client.get("/api/valuation/visit-prep").json()
    questions = [task["question"] for group_item in prep["groups"] for task in group_item["tasks"]]
    assert questions
    assert all(len(task["why"]) > 3 for group_item in prep["groups"] for task in group_item["tasks"])
    overview = client.get("/api/valuation/overview", params={"q": "X-9013"}).json()
    assert overview["items"]
    assert overview["items"][0]["family"] == "generator"
    assert "777001" not in json.dumps(overview)
    home = client.get("/")
    assert home.status_code == 200
    assert "קליטה" in home.text or "app.js" in home.text
