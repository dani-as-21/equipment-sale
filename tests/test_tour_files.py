import io
import json
import uuid

from docx import Document
from PIL import Image, ImageDraw, ImageFont

from app.settings import get_settings


def _post(client, parts, **form):
    files = [("files", (name, payload, content_type)) for name, payload, content_type, _file_id in parts]
    data = {"file_ids": json.dumps([file_id for *_rest, file_id in parts])}
    data.update({key: value for key, value in form.items()})
    return client.post("/api/tour-files", data=data, files=files)


def _png(text=None) -> bytes:
    image = Image.new("RGB", (40, 30), (20, 90, 70))
    if text:
        image = Image.new("RGB", (900, 240), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 80)
        draw.text((40, 70), text, fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _docx(text: str) -> bytes:
    document = Document()
    document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _row(client, tag, sheet=None):
    rows = client.get("/api/bootstrap").json()["rows"]
    found = [row for row in rows if row["tag_norm"] == tag and (sheet is None or row["sheet_name"] == sheet)]
    assert found, tag
    return found[0]


def test_group_files_by_tag_without_using_a_chosen_area(client):
    before_equipment = client.get("/api/bootstrap").json()["summary"]["rows_equipment"]
    before_relationships = len(client.get("/api/bootstrap").json()["relationships"])
    agitator = _row(client, "A-707", "07")
    clear_id = str(uuid.uuid4())
    loose_id = str(uuid.uuid4())
    duplicate_id = str(uuid.uuid4())
    response = _post(
        client,
        [
            ("A-707.txt", b"nameplate A-707\n", "text/plain", clear_id),
            ("hallway.txt", "המסדרון היה רועש".encode(), "text/plain", loose_id),
            ("HE-4142.txt", b"HE-4142\n", "text/plain", duplicate_id),
        ],
        observed_area_name="31",
        capture_id=str(uuid.uuid4()),
    )
    assert response.status_code == 200, response.text
    assert response.json()["area_required"] is False
    by_id = {item["file_id"]: item for item in response.json()["results"]}

    filed = by_id[clear_id]
    assert filed["saved"] is True
    assert filed["filed"] is True
    assert filed["rows"][0]["id"] == agitator["id"]
    assert filed["rows"][0]["sheet_name"] == "07"
    assert filed["rows"][0]["original_row"] == 5
    assert filed["observed_area"] in ("", None)
    assert filed["area_source"] == "unknown"
    assert "07" in filed["message"]

    loose = by_id[loose_id]
    assert loose["saved"] is True
    assert loose["filed"] is False
    assert loose["rows"] == []
    assert loose["review_id"]
    assert loose["observed_area"] in ("", None)
    capture = [item for item in client.get("/api/bootstrap").json()["captures"] if item["id"] == loose["capture_id"]][0]
    assert (capture["observed_area_name"] or "") == ""
    review = [item for item in client.get("/api/bootstrap").json()["reviews"] if item["id"] == loose["review_id"]][0]
    assert review["status"] == "open"
    assert "לא שויך" in review["question"]

    ambiguous = by_id[duplicate_id]
    assert ambiguous["saved"] is True
    assert ambiguous["filed"] is False
    assert ambiguous["review_id"]
    detail = client.get(f"/api/rows/{agitator['id']}").json()
    assert any(photo["id"] == clear_id for photo in detail["photos"])
    stored = client.get(f"/api/photos/{clear_id}/original")
    assert stored.status_code == 200
    assert stored.content == b"nameplate A-707\n"
    assert len(stored.content) > 0
    loose_bytes = client.get(f"/api/photos/{loose_id}/original")
    assert loose_bytes.status_code == 200
    assert len(loose_bytes.content) > 0

    again = _post(client, [("A-707.txt", b"nameplate A-707\n", "text/plain", clear_id)])
    assert again.json()["results"][0]["filed"] is True
    photos = [photo for photo in client.get("/api/bootstrap").json()["photos"] if photo["id"] == clear_id]
    assert len(photos) == 1

    linked = client.post(
        f"/api/review/{loose['review_id']}/action",
        json={"action": "link", "row_ids": [agitator["id"]]},
    )
    assert linked.json()["status"] == "resolved"
    shows = [photo for photo in client.get(f"/api/rows/{agitator['id']}").json()["photos"] if photo["id"] == loose_id][0]["shows"]
    assert shows["scope"] == "rows"
    assert agitator["id"] in shows["row_ids"]

    after = client.get("/api/bootstrap").json()
    assert after["summary"]["rows_equipment"] == before_equipment
    assert len(after["relationships"]) == before_relationships


def test_document_text_and_stated_area_and_open_capture(client):
    generator = _row(client, "X-9013", "90")
    doc_id = str(uuid.uuid4())
    response = _post(
        client,
        [("scan.docx", _docx("Emergency set X-9013"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document", doc_id)],
        observed_area_name="07",
    )
    filed = response.json()["results"][0]
    assert filed["saved"] is True and filed["filed"] is True
    assert filed["rows"][0]["id"] == generator["id"]
    assert filed["observed_area"] in ("", None)
    stored = client.get(f"/api/photos/{doc_id}/original")
    assert stored.content[:2] == b"PK"

    conflict_id = str(uuid.uuid4())
    conflict = _post(
        client,
        [("note.txt", "A-707\nאזור: 31\n".encode(), "text/plain", conflict_id)],
    ).json()["results"][0]
    assert conflict["saved"] is True
    assert conflict["filed"] is False
    assert conflict["observed_area"] == "31"
    assert conflict["area_source"] == "file"
    assert conflict["review_id"]

    capture_id = str(uuid.uuid4())
    created = client.post("/api/sync/capture", json={
        "id": capture_id,
        "match_mode": "user",
        "links": [{"row_id": generator["id"], "reason": "נבחר ידנית בסיור."}],
        "observed_area_name": "90",
        "tag_text": "X-9013",
        "finalized": 1,
        "client_updated_at": "2026-10-04T08:00:00Z",
    })
    assert created.status_code == 200, created.text
    attached_id = str(uuid.uuid4())
    attached = _post(
        client,
        [("walk.txt", b"no identifier in this note\n", "text/plain", attached_id)],
        capture_id=capture_id,
        observed_area_name="31",
    ).json()["results"][0]
    assert attached["saved"] is True and attached["filed"] is True
    assert attached["rows"][0]["id"] == generator["id"]
    assert attached["capture_id"] == capture_id
    assert attached["area_source"] == "existing_capture"
    assert attached["observed_area"] == "90"

    other_id = str(uuid.uuid4())
    other = _post(
        client,
        [("other.txt", b"tag A-707 only\n", "text/plain", other_id)],
        capture_id=capture_id,
    ).json()["results"][0]
    assert other["filed"] is True
    assert other["rows"][0]["tag"] == "A-707"
    assert other["capture_id"] != capture_id

    blocked_id = str(uuid.uuid4())
    group = _post(
        client,
        [
            ("A-707.txt", b"A-707\n", "text/plain", str(uuid.uuid4())),
            ("loose-2.txt", b"still no tag\n", "text/plain", blocked_id),
        ],
        capture_id=capture_id,
    ).json()["results"]
    loose = [item for item in group if item["file_id"] == blocked_id][0]
    assert loose["filed"] is False
    assert loose["capture_id"] != capture_id
    generator_photos = {photo["id"] for photo in client.get(f"/api/rows/{generator['id']}").json()["photos"]}
    assert attached_id in generator_photos
    assert blocked_id not in generator_photos


def test_nameplate_image_and_rejected_bytes(client):
    image_id = str(uuid.uuid4())
    response = _post(
        client,
        [("shot.png", _png("A-707"), "image/png", image_id)],
    )
    filed = response.json()["results"][0]
    assert filed["saved"] is True
    assert filed["filed"] is True
    assert filed["rows"][0]["tag"] == "A-707"
    assert filed["observed_area"] in ("", None)
    assert client.get(f"/api/photos/{image_id}/original").content == _png("A-707")

    empty = _post(client, [("empty.txt", b"", "text/plain", str(uuid.uuid4()))]).json()["results"][0]
    assert empty["saved"] is False
    assert empty["filed"] is False
    rejected = _post(client, [("payload.exe", b"not-a-photo", "application/octet-stream", str(uuid.uuid4()))]).json()["results"][0]
    assert rejected["saved"] is False
    assert get_settings().photo_dir.exists()
