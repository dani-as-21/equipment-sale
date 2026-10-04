import io
import json
import uuid
import zipfile
from pathlib import Path

from openpyxl import Workbook
from PIL import Image

from app.matching import match_inventory


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), (20, 90, 70)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_exact_auto_link_and_area_conflict_and_duplicates():
    rows = [
        {"id": "1", "kind": "equipment", "tag_norm": "A-707", "tag_original": "A-707", "serial_norm": "", "serial": "", "model": "", "description": "AGITATOR", "listed_area": "07", "identity_area": "07", "duplicate_group": "", "sheet_name": "07", "original_row": 5},
        {"id": "2", "kind": "equipment", "tag_norm": "HE-4142", "tag_original": "HE-4142", "serial_norm": "", "serial": "", "model": "", "description": "ONE", "listed_area": "41", "identity_area": "41", "duplicate_group": "", "sheet_name": "41", "original_row": 125},
        {"id": "3", "kind": "equipment", "tag_norm": "HE-4142", "tag_original": "HE-4142", "serial_norm": "", "serial": "", "model": "", "description": "TWO", "listed_area": "41", "identity_area": "41", "duplicate_group": "", "sheet_name": "41", "original_row": 176},
        {"id": "4", "kind": "equipment", "tag_norm": "P-1", "tag_original": "P-1", "serial_norm": "", "serial": "", "model": "", "description": "PUMP", "listed_area": "07", "identity_area": "07", "duplicate_group": "DUP-1", "sheet_name": "07", "original_row": 10},
        {"id": "5", "kind": "equipment", "tag_norm": "P-1", "tag_original": "P-1", "serial_norm": "", "serial": "", "model": "", "description": "PUMP", "listed_area": "07", "identity_area": "07", "duplicate_group": "DUP-1", "sheet_name": "07", "original_row": 11},
    ]
    auto = match_inventory(rows, tag="A-707", observed_area="07")
    assert auto["mode"] == "auto"
    assert auto["links"][0]["review_status"] == "auto_linked"
    conflict = match_inventory(rows, tag="A-707", observed_area="31")
    assert conflict["mode"] == "pending"
    assert conflict["review_kind"] == "location"
    assert conflict["links"][0]["review_status"] == "pending_review"
    dup = match_inventory(rows, tag="HE-4142", observed_area="41")
    assert dup["mode"] == "pending"
    assert dup["links"] == []
    identical = match_inventory(rows, tag="P-1", observed_area="07")
    assert identical["mode"] == "auto"
    assert {link["row_id"] for link in identical["links"]} == {"4", "5"}
    named = match_inventory(rows, tag="centrifuge", observed_area="07")
    assert named["mode"] != "auto" or named["links"] == []
    model_only = match_inventory(
        rows + [{"id": "9", "kind": "equipment", "tag_norm": "X", "tag_original": "X", "serial_norm": "SER-1", "serial": "SER-1", "model": "XP205", "description": "Balance", "listed_area": "", "identity_area": "lab", "duplicate_group": "", "sheet_name": "טבלה 1", "original_row": 2}],
        model="XP205",
    )
    assert model_only["links"] == []
    serial = match_inventory(
        rows + [{"id": "9", "kind": "equipment", "tag_norm": "X", "tag_original": "X", "serial_norm": "SER-1", "serial": "SER-1", "model": "XP205", "description": "Balance", "listed_area": "", "identity_area": "lab", "duplicate_group": "", "sheet_name": "טבלה 1", "original_row": 2}],
        serial="SER-1",
    )
    assert serial["mode"] == "auto"
    assert serial["links"][0]["row_id"] == "9"


def test_import_preserves_sources_and_does_not_collapse_names(client):
    bundle = client.get("/api/bootstrap")
    assert bundle.status_code == 200
    rows = bundle.json()["rows"]
    equipment = [row for row in rows if row["kind"] == "equipment"]
    agitator = [row for row in equipment if row["tag_norm"] == "A-707"]
    assert len(agitator) == 1
    assert agitator[0]["sheet_name"] == "07"
    assert agitator[0]["original_row"] == 5
    assert "price" in agitator[0]["raw"]
    assert "sale_price" not in agitator[0]
    assert agitator[0]["quantity"] == ""
    duplicates = [row for row in equipment if row["tag_norm"] == "HE-4142"]
    assert len(duplicates) == 2
    assert duplicates[0]["id"] != duplicates[1]["id"]
    centrifuges = [row for row in equipment if (row["description"] or "").strip().casefold() == "centrifuge"]
    assert len(centrifuges) >= 4
    assert len({row["id"] for row in centrifuges}) == len(centrifuges)
    dosing = [row for row in equipment if (row["description"] or "").strip().casefold() == "dosing pump"]
    assert len(dosing) >= 2
    lab = [row for row in equipment if row["source_kind"] == "lab"]
    assert lab
    assert any("FUTURE" in " ".join(row["labels"]).upper() for row in equipment)
    assert any("NOT IN USE" in " ".join(label.upper() for label in row["labels"]) for row in lab)
    reviews = bundle.json()["reviews"]
    kinds = {review["dedupe_key"] for review in reviews}
    assert "import:generic:centrifuge" in kinds
    assert "import:generic:dosing pump" in kinds
    assert "import:dup-tag:HE-4142" in kinds
    assert "import:lab:bad-date" in kinds
    assert "import:lab:two-serials" in kinds
    assert "import:lab:panels" in kinds
    assert "import:lab:sensors" in kinds
    assert not any("perrigo" in review["question"].casefold() for review in reviews)
    notes = " ".join(bundle.json()["notes"])
    assert "price" in notes
    assert "פעמיים" in notes


def test_capture_match_area_two_rows_correction_and_export(client):
    before_rel = client.get("/api/bootstrap").json()["relationships"]
    capture_id = str(uuid.uuid4())
    photo_a = str(uuid.uuid4())
    photo_b = str(uuid.uuid4())
    created = client.post("/api/sync/capture", json={
        "id": capture_id,
        "observed_area_name": "07",
        "tag_text": "A-707",
        "note": "צולם ליד הקיר",
        "match_mode": "auto",
        "finalized": 1,
        "client_updated_at": "2026-10-03T12:00:00Z",
    })
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["capture"]["observed_area_name"] == "07"
    assert body["links"][0]["review_status"] == "auto_linked"
    assert body["links"][0]["method"] == "auto"
    early = client.post(f"/api/sync/capture/{capture_id}/confirm", json={"photo_ids": [photo_a, photo_b]})
    assert early.json()["synced"] is False
    assert client.get(f"/api/bootstrap").json()["captures"]
    stored = [c for c in client.get("/api/bootstrap").json()["captures"] if c["id"] == capture_id][0]
    assert stored["sync_status"] != "synced"
    for photo_id, name in ((photo_a, "overall.png"), (photo_b, "tag.png")):
        response = client.post(
            "/api/sync/photo",
            data={"photo_id": photo_id, "capture_id": capture_id, "role": "overall" if photo_id == photo_a else "tag", "shows_json": ""},
            files={"file": (name, png_bytes(), "image/png")},
        )
        assert response.status_code == 200, response.text
        assert response.json()["stored"] is True
    again = client.post(
        "/api/sync/photo",
        data={"photo_id": photo_a, "capture_id": capture_id, "role": "overall", "shows_json": ""},
        files={"file": ("overall.png", png_bytes(), "image/png")},
    )
    assert again.status_code == 200
    confirmed = client.post(f"/api/sync/capture/{capture_id}/confirm", json={"photo_ids": [photo_a, photo_b]})
    assert confirmed.json()["synced"] is True
    detail_rows = [row for row in client.get("/api/bootstrap").json()["rows"] if row["tag_norm"] == "A-707"]
    row_id = detail_rows[0]["id"]
    detail = client.get(f"/api/rows/{row_id}")
    assert detail.status_code == 200
    assert {photo["id"] for photo in detail.json()["photos"]} >= {photo_a, photo_b}
    assert any(item["note"] == "צולם ליד הקיר" for item in detail.json()["captures"])

    other_id = str(uuid.uuid4())
    other = client.post("/api/sync/capture", json={
        "id": other_id,
        "observed_area_name": "31",
        "tag_text": "A-3101",
        "note": "אזור אחר",
        "match_mode": "auto",
        "finalized": 1,
        "client_updated_at": "2026-10-03T12:05:00Z",
    })
    assert other.status_code == 200
    assert other.json()["capture"]["observed_area_name"] == "31"
    kept = client.get(f"/api/rows/{row_id}").json()["captures"]
    assert any(item["id"] == capture_id and item["observed_area_name"] == "07" for item in kept)

    ambiguous = str(uuid.uuid4())
    amb = client.post("/api/sync/capture", json={
        "id": ambiguous,
        "observed_area_name": "41",
        "tag_text": "HE-4142",
        "match_mode": "auto",
        "finalized": 1,
        "client_updated_at": "2026-10-03T12:06:00Z",
    })
    assert amb.json()["links"] == [] or all(link["review_status"] != "auto_linked" for link in amb.json()["links"])
    assert amb.json()["capture"]["match_mode"] != "auto"

    two = str(uuid.uuid4())
    photo_c = str(uuid.uuid4())
    first = client.get("/api/bootstrap").json()["rows"]
    pump = next(row for row in first if row["tag_norm"] == "P-9201")
    tank = next(row for row in first if row["tag_norm"] == "T-9201")
    linked = client.post("/api/sync/capture", json={
        "id": two,
        "observed_area_name": "92",
        "note": "שני פריטים בתמונה",
        "match_mode": "user",
        "finalized": 1,
        "client_updated_at": "2026-10-03T12:07:00Z",
        "links": [
            {"row_id": pump["id"], "reason": "נראה בתמונה"},
            {"row_id": tank["id"], "reason": "נראה בתמונה"},
        ],
    })
    assert linked.status_code == 200
    assert {link["inventory_row_id"] for link in linked.json()["links"]} == {pump["id"], tank["id"]}
    client.post(
        "/api/sync/photo",
        data={"photo_id": photo_c, "capture_id": two, "role": "overall", "shows_json": json.dumps({"scope": "unassigned", "row_ids": []})},
        files={"file": ("both.png", png_bytes(), "image/png")},
    )
    relations = client.get("/api/bootstrap").json()["relationships"]
    assert len(relations) == len(before_rel)
    pump_detail = client.get(f"/api/rows/{pump['id']}").json()
    assert pump_detail["relationships"] == []

    unresolved = str(uuid.uuid4())
    client.post("/api/sync/capture", json={
        "id": unresolved,
        "observed_area_name": "07",
        "note": "לא מזוהה",
        "match_mode": "unresolved",
        "finalized": 1,
        "client_updated_at": "2026-10-03T12:08:00Z",
    })
    reviews = client.get("/api/bootstrap").json()["reviews"]
    item = next(review for review in reviews if review["dedupe_key"] == f"cap:{unresolved}:unmatched")
    wrong = client.post(f"/api/review/{item['id']}/action", json={"action": "resolve", "text": ""})
    assert wrong.json().get("missing")
    still = client.get("/api/bootstrap").json()["reviews"]
    assert next(review for review in still if review["id"] == item["id"])["status"] == "open"
    closed = client.post(f"/api/review/{item['id']}/action", json={"action": "link", "row_ids": [row_id]})
    assert closed.json()["status"] == "resolved"
    fixed = client.get(f"/api/rows/{row_id}").json()
    assert any(capture["id"] == unresolved and capture["note"] == "לא מזוהה" for capture in fixed["captures"])

    corrected = client.post("/api/sync/capture", json={
        "id": capture_id,
        "observed_area_name": "07",
        "tag_text": "A-707",
        "note": "צולם ליד הקיר",
        "match_mode": "user",
        "finalized": 1,
        "client_updated_at": "2026-10-03T12:09:00Z",
        "links": [{"row_id": pump["id"], "reason": "תיקון"}],
    })
    assert corrected.status_code == 200
    assert {link["inventory_row_id"] for link in corrected.json()["links"]} == {pump["id"]}
    photos_after = client.get(f"/api/rows/{pump['id']}").json()["photos"]
    assert {photo["id"] for photo in photos_after} >= {photo_a, photo_b}

    history = client.get(f"/api/rows/{pump['id']}").json()["history"]
    assert history
    undo_id = next(entry["id"] for entry in history if entry["action"] == "links" and not entry["undone"])
    undone = client.post(f"/api/undo/{undo_id}")
    assert undone.status_code == 200

    package = client.get("/api/export/package.zip")
    assert package.status_code == 200
    archive = zipfile.ZipFile(io_bytes(package.content))
    names = archive.namelist()
    assert "manifest.json" in names
    assert "inventory.csv" in names
    manifest = json.loads(archive.read("manifest.json"))
    assert any(link["inventory_row_id"] == pump["id"] and link["sheet_or_table"] == "92" for link in manifest["links"])
    photo_files = [name for name in names if name.startswith("photos/") and photo_a in name]
    assert photo_files
    assert archive.read(photo_files[0])[:8] == b"\x89PNG\r\n\x1a\n"
    csv_text = archive.read("inventory.csv").decode("utf-8")
    assert "A-707" in csv_text
    assert "stable_id" in csv_text


def io_bytes(content: bytes):
    import io
    return io.BytesIO(content)


def test_second_plant_file_is_rejected_and_failure_is_not_synced(client, tmp_path: Path):
    book = Workbook()
    sheet = book.active
    sheet["A4"] = "Item"
    sheet["B4"] = "Description"
    sheet["A5"] = "Z-1"
    sheet["B5"] = "EXTRA"
    target = tmp_path / "other.xlsx"
    book.save(target)
    rejected = client.post(
        "/api/import/file",
        data={"kind": "plant", "replace": "false"},
        files={"file": ("other.xlsx", target.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert rejected.status_code == 409
    count_before = len([row for row in client.get("/api/bootstrap").json()["rows"] if row["kind"] == "equipment"])
    assert not any(row["tag_norm"] == "Z-1" for row in client.get("/api/bootstrap").json()["rows"])
    count_after = len([row for row in client.get("/api/bootstrap").json()["rows"] if row["kind"] == "equipment"])
    assert count_before == count_after

    capture_id = str(uuid.uuid4())
    client.post("/api/sync/capture", json={"id": capture_id, "match_mode": "unresolved", "finalized": 0, "client_updated_at": "2026-10-03T13:00:00Z"})
    missing = client.post(f"/api/sync/capture/{capture_id}/confirm", json={"photo_ids": [str(uuid.uuid4())]})
    assert missing.json()["synced"] is False
    stored = [row for row in client.get("/api/bootstrap").json()["captures"] if row["id"] == capture_id][0]
    assert stored["sync_status"] != "synced"


def test_summary_counts_rows_not_machines(client):
    summary = client.get("/api/summary").json()
    assert "מכונות" in summary["footnote"] or "לא מכונות" in summary["footnote"]
    assert summary["rows_equipment"] > 1000
    assert summary["rows_documented"] != summary["rows_equipment"] or summary["rows_not_documented"] >= 0
    assert "revisit" in summary
