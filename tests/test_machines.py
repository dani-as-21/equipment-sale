import json
import uuid

from app.machines import prefix_label, split_prefix
from tests.test_tour_files import _post


def _boot(client):
    response = client.get("/api/bootstrap")
    assert response.status_code == 200, response.text
    return response.json()


def _machine_with(data, *tags):
    wanted = set(tags)
    found = []
    for machine in data["machines"]:
        active = {member["tag_norm"] for member in machine["members"] if member["link_status"] == "active"}
        if wanted <= active:
            found.append(machine)
    return found


def test_prefix_is_a_category_and_keeps_the_original_tag():
    assert split_prefix("r-4208") == "R"
    assert split_prefix("HVAC-12") == "HVAC"
    assert split_prefix("ahu-3") == "AHU"
    assert prefix_label("p-100") == "משאבה"
    assert prefix_label("A-707") == "בוחש / מערבל"
    assert prefix_label("f-4208") == "מסנן"
    assert prefix_label("x-1").startswith("יחידת")
    assert prefix_label("W-2") == "ציוד שקילה / משקל"


def test_confirmed_pair_and_explicit_groups_do_not_invent_rows(client):
    data = _boot(client)
    equipment = data["summary"]["rows_equipment"]
    tags = {row["tag_norm"] for row in data["rows"]}
    assert "R-4208" not in tags
    assert "F-4208" not in tags
    user = [machine for machine in data["machines"] if machine["basis"] == "user"]
    assert len(user) == 1
    assert user[0]["status"] == "confirmed"
    assert user[0]["sold_together"] == "confirmed"
    assert {member["tag_norm"] for member in user[0]["members"]} == {"R-4208", "F-4208"}
    assert all(member["in_inventory"] is False for member in user[0]["members"])
    assert "אושר על ידי המשתמש" in user[0]["note"]
    assert not _machine_with(data, "P-4208", "T-4208")

    proposed = _machine_with(data, "A-707", "R-707")
    assert len(proposed) == 1
    machine = proposed[0]
    assert machine["status"] == "proposed"
    assert machine["sold_together"] == "open"
    assert machine["basis"] == "explicit_reference"
    agitator = next(member for member in machine["members"] if member["tag_norm"] == "A-707")
    assert agitator["link_status"] == "active"
    assert agitator["row_id"]
    assert agitator["sheet_name"] == "07"
    assert agitator["original_row"] == 5
    assert "R-707" in agitator["evidence"]
    review = [
        item for item in data["reviews"]
        if item["kind"] == "grouping" and (item["payload"] or {}).get("machine_id") == machine["id"]
    ]
    assert review and review[0]["status"] == "open"
    assert review[0]["queue"] == "machine"
    assert review[0]["payload"]["resolve_by"]
    assert review[0]["row_ids"]

    for item in data["machines"]:
        if item["basis"] != "explicit_reference" or item["status"] == "dissolved":
            continue
        parents = [member for member in item["members"] if member["role"] == "parent" and member["link_status"] == "active"]
        assert len(parents) == 1
        parent = parents[0]["tag_norm"]
        for member in item["members"]:
            if member["role"] != "component" or member["link_status"] != "active":
                continue
            assert parent in (member["evidence"] or "")
    assert _boot(client)["summary"]["rows_equipment"] == equipment

    row = next(item for item in data["rows"] if item["tag_norm"] == "A-707" and item["sheet_name"] == "07")
    assert row["tag_original"] == "A-707"
    assert row["prefix_category"] == "בוחש / מערבל"


def test_photo_of_a_component_is_not_labeled_as_its_sibling(client):
    before = _boot(client)["summary"]["rows_equipment"]
    filter_id = str(uuid.uuid4())
    filed = _post(client, [("f-4208.txt", b"f-4208\n", "text/plain", filter_id)]).json()["results"][0]
    assert filed["saved"] is True
    assert filed["filed"] is True
    assert filed["rows"] == []
    assert filed["observed_area"] in ("", None)
    labels = {label["tag_norm"] for label in filed["labels"]}
    assert labels == {"F-4208"}
    assert filed["labels"][0]["tag"] == "F-4208"
    assert filed["labels"][0]["prefix"] == "מסנן"
    assert len(filed["machines"]) == 1
    machine = filed["machines"][0]
    assert machine["matched_tag"] == "F-4208"
    assert "R-4208" in machine["other_tags"]
    assert "לא צילום של" in filed["message"]
    assert "R-4208" in filed["message"]
    data = _boot(client)
    user = next(item for item in data["machines"] if item["basis"] == "user")
    component = next(member for member in user["members"] if member["tag_norm"] == "F-4208")
    parent = next(member for member in user["members"] if member["tag_norm"] == "R-4208")
    assert any(photo["id"] == filter_id and photo["of_tag"] == "F-4208" for photo in component["photos"])
    assert all(photo["id"] != filter_id for photo in parent["photos"])
    assert data["summary"]["rows_equipment"] == before

    both_id = str(uuid.uuid4())
    both = _post(client, [("pair.txt", b"A-707\nX-9013\n", "text/plain", both_id)]).json()["results"][0]
    assert both["filed"] is True
    assert {row["tag"] for row in both["rows"]} == {"A-707", "X-9013"}
    assert {label["tag_norm"] for label in both["labels"]} == {"A-707", "X-9013"}
    assert "R-707" not in {label["tag_norm"] for label in both["labels"]}
    assert both["inventory_area"] == "" or "לא מיקום שאומת" in both["message"]
    assert "לא מיקום שאומת מהתמונה" in both["message"]
    detail = client.get(f"/api/rows/{both['rows'][0]['id']}").json()
    photo = next(item for item in detail["photos"] if item["id"] == both_id)
    assert {label["tag_norm"] for label in photo["shows"]["labels"]} <= {"A-707", "X-9013"}


def test_unclear_photo_stays_open_and_does_not_block_the_next_upload(client):
    ambiguous = _post(client, [("HE-4142.txt", b"HE-4142\n", "text/plain", str(uuid.uuid4()))]).json()["results"][0]
    assert ambiguous["saved"] is True
    assert ambiguous["filed"] is False
    assert ambiguous["review_id"]
    review = next(item for item in _boot(client)["reviews"] if item["id"] == ambiguous["review_id"])
    assert review["status"] == "open"
    assert review["payload"]["resolve_by"]
    assert review["payload"]["photo_ids"] == [ambiguous["file_id"]]
    assert review["payload"]["suggestions"]

    follow = _post(client, [("a-707.txt", b"a-707\n", "text/plain", str(uuid.uuid4()))]).json()["results"][0]
    assert follow["saved"] is True
    assert follow["filed"] is True
    assert follow["rows"][0]["tag"] == "A-707"
    assert follow["labels"][0]["prefix"] == "בוחש / מערבל"
    assert "לא מיקום שאומת מהתמונה" in follow["message"]
    assert follow["observed_area"] in ("", None)
    still = next(item for item in _boot(client)["reviews"] if item["id"] == ambiguous["review_id"])
    assert still["status"] == "open"


def test_answer_split_and_sale_keep_the_excel_rows(client):
    data = _boot(client)
    equipment = data["summary"]["rows_equipment"]
    machine = _machine_with(data, "A-707", "R-707")[0]
    review = next(
        item for item in data["reviews"]
        if item["kind"] == "grouping" and (item["payload"] or {}).get("machine_id") == machine["id"] and item["status"] == "open"
    )
    uploaded = client.post(
        f"/api/review/{review['id']}/photo",
        files={"file": ("support.txt", b"tag A-707\n", "text/plain")},
    )
    assert uploaded.status_code == 200, uploaded.text
    body = uploaded.json()
    assert body["ok"] is True
    assert body["closed"] is False
    assert body["filed"] is True
    answered = client.post(f"/api/review/{review['id']}/action", json={"action": "resolve", "text": "הרכיב נשאר עם הכור"})
    assert answered.status_code == 200, answered.text
    assert answered.json()["noted"] is True
    refreshed = _boot(client)
    updated = next(item for item in refreshed["machines"] if item["id"] == machine["id"])
    assert "הרכיב נשאר עם הכור" in updated["note"]
    open_review = next(item for item in refreshed["reviews"] if item["id"] == review["id"])
    assert open_review["status"] == "open"
    assert body["photo_id"] in open_review["payload"]["photo_ids"]

    confirmed = client.post(f"/api/machines/{machine['id']}/action", json={"action": "confirm"})
    assert confirmed.json()["status"] == "confirmed"
    assert confirmed.json()["sold_together"] == "open"
    sold = client.post(f"/api/machines/{machine['id']}/action", json={"action": "confirm_sale"})
    assert sold.json()["sold_together"] == "confirmed"
    overview = client.get("/api/valuation/overview", params={"q": "A-707"}).json()
    item = next(row for row in overview["items"] if row["tag"] == "A-707")
    assert "יחידת המכירה" in item["sale_unit"]
    assert item.get("latest_range") in (None, "", "אין די ראיות להערכה")
    assert "777001" not in json.dumps(item)

    split = client.post(f"/api/machines/{machine['id']}/action", json={"action": "split", "tag_norms": ["A-707"]})
    assert split.status_code == 200, split.text
    after = _boot(client)
    assert after["summary"]["rows_equipment"] == equipment
    changed = next(item for item in after["machines"] if item["id"] == machine["id"])
    member = next(item for item in changed["members"] if item["tag_norm"] == "A-707")
    assert member["link_status"] == "removed"
    assert member["row_id"]
    row = client.get(f"/api/rows/{member['row_id']}")
    assert row.status_code == 200
    assert row.json()["row"]["tag_original"] == "A-707"
    overview = client.get("/api/valuation/overview", params={"q": "A-707"}).json()
    item = next(row for row in overview["items"] if row["tag"] == "A-707" and row["sheet_name"] == "07")
    assert "sale_unit" not in item
    boundary = next(item for item in after["reviews"] if item["id"] == review["id"])
    assert boundary["status"] == "open"

    restored = client.post(f"/api/machines/{machine['id']}/action", json={"action": "combine", "tag_norms": ["A-707"]})
    assert restored.status_code == 200, restored.text
    final = next(item for item in _boot(client)["machines"] if item["id"] == machine["id"])
    member = next(item for item in final["members"] if item["tag_norm"] == "A-707")
    assert member["link_status"] == "active"
    assert _boot(client)["summary"]["rows_equipment"] == equipment
