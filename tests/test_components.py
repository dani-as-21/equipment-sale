def _asset(client, tag):
    found = client.get("/api/assets", params={"q": tag}).json()["assets"]
    return next(asset for asset in found if asset["tag"] == tag)


def _card(client, tag):
    asset = _asset(client, tag)
    response = client.get(f"/api/assets/{asset['id']}")
    assert response.status_code == 200, response.text
    return response.json()


def test_slash_suffix_is_a_component_and_not_its_own_asset(client):
    card = _card(client, "D-7682")
    tags = {item["tag"] for item in card["components"]}
    expected = {f"D-7682/{suffix}" for suffix in ("1", "2", "3", "4", "5", "6", "7", "10")}
    assert expected <= tags
    assert "W-7694" in tags
    for item in card["components"]:
        if item["tag"] in expected:
            assert item["origin"] == "slash"
            assert item["explanation"]
            assert item["in_source"] is True
    for suffix in ("1", "2", "3", "4", "5", "6", "7", "10"):
        listed = client.get("/api/assets", params={"q": f"D-7682/{suffix}"}).json()["assets"]
        assert all(asset["tag"] != f"D-7682/{suffix}" for asset in listed)
        assert any(asset["tag"] == "D-7682" for asset in listed)

    standalone = _card(client, "X-2002")
    assert standalone["kind"] == "item"
    assert {"X-2002/1", "X-2002/2", "X-2002/3"} <= {item["tag"] for item in standalone["components"]}
    loose = client.get("/api/assets", params={"q": "X-2001/1"}).json()["assets"]
    assert any(asset["tag"] == "X-2001/1" for asset in loose)

    pair = _card(client, "R-4208")
    assert {item["tag"] for item in pair["components"]} == {"R-4208", "F-4208"}
    assert pair["sold_together"] is True
    separate = client.get("/api/assets", params={"q": "4208"}).json()["assets"]
    assert not any({"P-4208", "T-4208"} <= set(asset["tags"]) for asset in separate)


def test_user_can_add_remove_and_move_components(client):
    parent = _asset(client, "D-7682")
    created = client.post(
        f"/api/assets/{parent['id']}/components",
        json={"action": "create", "tag": "D-7682/USER", "name": "רכיב בדיקה", "description": "נוסף מהטופס", "notes": "הערה קצרה"},
    )
    assert created.status_code == 200, created.text
    card = created.json()
    made = next(item for item in card["components"] if item["tag"] == "D-7682/USER")
    assert made["name"] == "רכיב בדיקה"
    assert made["description"] == "נוסף מהטופס"
    assert made["notes"] == "הערה קצרה"
    assert made["in_source"] is False
    duplicate = client.post(
        f"/api/assets/{parent['id']}/components",
        json={"action": "create", "tag": "D-7682/1", "name": "כפול"},
    )
    assert duplicate.status_code == 400

    target = _asset(client, "X-2001/1")
    moved = client.post(
        f"/api/assets/{parent['id']}/components",
        json={"action": "move", "component_id": made["id"], "target_asset_id": target["id"]},
    )
    assert moved.status_code == 200, moved.text
    assert all(item["tag"] != "D-7682/USER" for item in moved.json()["components"])
    hosted = client.get(f"/api/assets/{target['id']}").json()
    assert any(item["tag"] == "D-7682/USER" and item["notes"] == "הערה קצרה" for item in hosted["components"])

    found = client.get("/api/inventory-items", params={"q": "B-7611/1"}).json()["items"]
    row = next(item for item in found if item["tag"] == "B-7611/1")
    added = client.post(
        f"/api/assets/{parent['id']}/components",
        json={"action": "add_existing", "row_ids": [row["id"]]},
    )
    assert added.status_code == 200, added.text
    attached = next(item for item in added.json()["components"] if item["tag"] == "B-7611/1")
    listed = client.get("/api/assets", params={"q": "B-7611/1"}).json()["assets"]
    assert all(asset["tag"] != "B-7611/1" for asset in listed)
    removed = client.post(
        f"/api/assets/{parent['id']}/components",
        json={"action": "remove", "component_id": attached["id"]},
    )
    assert removed.status_code == 200, removed.text
    assert all(item["tag"] != "B-7611/1" for item in removed.json()["components"])
    restored = client.get("/api/assets", params={"q": "B-7611/1"}).json()["assets"]
    assert any(asset["tag"] == "B-7611/1" for asset in restored)

    slash = next(item for item in removed.json()["components"] if item["tag"] == "D-7682/10")
    taken = client.post(
        f"/api/assets/{parent['id']}/components",
        json={"action": "remove", "component_id": slash["id"]},
    )
    assert taken.status_code == 200, taken.text
    from app.db import connect
    from app.logic import now_iso
    from app.product import ensure_slash_components

    conn = connect()
    ensure_slash_components(conn, now_iso())
    conn.commit()
    conn.close()
    again = _card(client, "D-7682")
    assert "D-7682/10" not in {item["tag"] for item in again["components"]}
    assert "D-7682/1" in {item["tag"] for item in again["components"]}
