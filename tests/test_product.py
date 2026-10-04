import json


def test_dashboard_counts_sellable_assets_without_an_api_split(client):
    health = client.get("/api/health").json()
    dashboard = client.get("/api/dashboard")
    assert dashboard.status_code == 200, dashboard.text
    body = dashboard.json()
    counts = {item["id"]: item["count"] for item in body["by_category"]}
    assert body["assets"] == sum(counts.values())
    assert counts["lab"] > 0
    assert counts["process"] > counts["lab"]
    assert counts["api"] == 0
    assert health["inventory_rows"] > body["assets"]
    assert "טווח" in body["note"]
    tasks = client.get("/api/tasks").json()["tasks"]
    assert any("FUTURE" in task["question"] for task in tasks)
    assert any("API" in task["question"] and "לא חולק" in task["question"] for task in tasks)
    assert any("Centrifuge" in task["question"] or "צנטריפוגה" in task["question"] or "השם Centrifuge" in task["question"] for task in tasks)


def test_confirmed_machine_is_one_asset_and_likely_links_stay_explained(client):
    found = client.get("/api/assets", params={"q": "R-4208"}).json()
    machine = next(asset for asset in found["assets"] if "F-4208" in asset["tags"] and "R-4208" in asset["tags"])
    assert machine["confidence"] == "confirmed"
    assert machine["sold_together"] is True
    detail = client.get(f"/api/assets/{machine['id']}")
    assert detail.status_code == 200, detail.text
    card = detail.json()
    assert {item["tag"] for item in card["components"]} == {"R-4208", "F-4208"}
    assert all(item["in_source"] is False for item in card["components"])
    assert "אין שורת מקור" in card["components"][0]["source"]

    reactor = client.get("/api/assets", params={"q": "A-707"}).json()["assets"]
    group = next(asset for asset in reactor if "A-707" in asset["tags"] and "R-707" in asset["tags"])
    assert group["confidence"] == "likely"
    card = client.get(f"/api/assets/{group['id']}").json()
    agitator = next(item for item in card["components"] if item["tag"] == "A-707")
    assert agitator["explanation"].strip()
    assert agitator["confidence"] == "likely"
    assert agitator["in_source"] is True
    tasks = client.get("/api/tasks").json()["tasks"]
    assert not any(task["category"] == "grouping" and task["asset_id"] == group["id"] for task in tasks)
    assert any(task["category"] == "grouping" for task in tasks)

    separate = client.get("/api/assets", params={"q": "4208"}).json()["assets"]
    assert not any({"P-4208", "T-4208"} <= set(asset["tags"]) for asset in separate)
    rows = client.get("/api/bootstrap").json()["rows"]
    agitator_row = next(row for row in rows if row["tag_norm"] == "A-707" and row["sheet_name"] == "07")
    listed = client.get("/api/assets", params={"q": "A-707"}).json()["assets"]
    assert agitator_row["id"] not in {asset["id"] for asset in listed}


def test_upload_can_be_confirmed_and_is_not_overwritten(client):
    body = "Condition report\nAsset A-707\nBearing checked in the plant file.\n".encode()
    first = client.post("/api/uploads", files=[("files", ("a707-note.txt", body, "text/plain"))])
    assert first.status_code == 200, first.text
    uploaded = first.json()["results"][0]
    assert uploaded["duplicate"] is False
    assert uploaded["confidence"] == "likely"
    assert uploaded["status"] == "pending"
    assert uploaded["links"]
    again = client.post("/api/uploads", files=[("files", ("copy.txt", body, "text/plain"))])
    assert again.json()["results"][0]["duplicate"] is True
    assert len(client.get("/api/uploads").json()["files"]) >= 1
    confirmed = client.post(f"/api/uploads/{uploaded['id']}", json={"action": "confirm"})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["user_locked"] is True
    target = client.get("/api/assets", params={"q": "X-9013"}).json()["assets"]
    assert target and target[0]["tag"] == "X-9013"
    moved = client.post(f"/api/uploads/{uploaded['id']}", json={"action": "assign", "asset_ids": [target[0]["id"]]})
    assert moved.status_code == 200, moved.text
    assert {link["asset_id"] for link in moved.json()["links"]} == {target[0]["id"]}
    client.post("/api/uploads", files=[("files", ("copy.txt", body, "text/plain"))])
    kept = client.get("/api/uploads").json()["files"]
    same = next(item for item in kept if item["id"] == uploaded["id"])
    assert {link["asset_id"] for link in same["links"]} == {target[0]["id"]}
    card = client.get(f"/api/assets/{target[0]['id']}").json()
    assert any(item["id"] == uploaded["id"] for item in card["files"])

    vague = client.post("/api/uploads", data={"note": "המסדרון היה רועש והאור כבוי"})
    assert vague.status_code == 200, vague.text
    pending = vague.json()["results"][0]
    assert pending["confidence"] == "uncertain"
    assert pending["links"] == []
    assert "לא שויך" in pending["summary"]


def test_evidence_is_stored_without_a_value_range(client):
    asset = client.get("/api/assets", params={"q": "X-9013"}).json()["assets"][0]
    saved = client.post(
        f"/api/assets/{asset['id']}/evidence",
        json={"value_kind": "asking", "source": "דוגמה כתובה", "price_text": "1500", "currency": "USD", "description": "מחיר מבוקש שנמסר"},
    )
    assert saved.status_code == 200, saved.text
    card = client.get(f"/api/assets/{asset['id']}").json()
    dumped = json.dumps(card)
    assert "range_low" not in dumped
    assert "range_high" not in dumped
    evidence = card["evidence"][0]
    assert evidence["price_text"] == "1500"
    assert evidence["currency"] == "USD"
    assert evidence["value_kind"] == "asking"
    empty = client.post(f"/api/assets/{asset['id']}/evidence", json={"value_kind": "other"})
    assert empty.status_code == 400
