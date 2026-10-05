import hashlib
import io
from pathlib import Path

from docx import Document

from app.db import connect

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "data" / "sources" / "lab-instruments.docx"
LAB_SHA = "c9b632c7ec85df53504a636a63109ade06454637b3298a9323f61165c392a55a"


def _docx(rows: list[list[str]]) -> bytes:
    document = Document()
    table = document.add_table(rows=1 + len(rows), cols=6)
    headers = ["Instrument Name", "Instrument Tag No.", "Manufacturer", "Model No.", "Serial No.", "Date of Installation"]
    for index, header in enumerate(headers):
        table.rows[0].cells[index].text = header
    for row_index, values in enumerate(rows, start=1):
        for index, value in enumerate(values):
            table.rows[row_index].cells[index].text = value
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_existing_lab_list_is_recognized_and_not_linked(client):
    data = LAB.read_bytes()
    assert hashlib.sha256(data).hexdigest() == LAB_SHA
    before = client.get("/api/dashboard").json()["assets"]
    conn = connect()
    conn.execute("DELETE FROM project_file_links WHERE file_id = 'FIL-c9b632c7ec85df53'")
    conn.execute("DELETE FROM project_files WHERE id = 'FIL-c9b632c7ec85df53'")
    conn.execute(
        """
        INSERT INTO project_files (
          id, sha256, original_name, stored_path, media_kind, extracted_text, note_text,
          doc_type, confidence, summary, status, user_locked, created_at, updated_at
        ) VALUES (
          'FIL-c9b632c7ec85df53', ?, 'Lab Equipment list Monolom.docx', '', 'docx', '', '',
          'מסמך', 'uncertain', 'יש כפילויות ולא שויך עד אישור.', 'pending', 0, '2026-10-05T00:00:00Z', '2026-10-05T00:00:00Z'
        )
        """,
        (LAB_SHA,),
    )
    conn.execute(
        """
        INSERT INTO project_file_links (id, file_id, asset_id, tag_norm, confidence, reason, user_locked)
        VALUES ('FL-stuck', 'FIL-c9b632c7ec85df53', 'ROW-stuck', 'RDBAL-01', 'uncertain', 'ניחוש', 0)
        """
    )
    conn.commit()
    conn.close()

    listed = client.get("/api/uploads").json()["files"]
    stuck = next(item for item in listed if item["id"] == "FIL-c9b632c7ec85df53")
    assert stuck["already_loaded"] is True
    assert stuck["status"] == "loaded"
    assert stuck["links"] == []
    assert "רשימת מכשירי מעבדה" in stuck["summary"]
    assert "כבר במלאי" in stuck["summary"]
    assert "lab-instruments.docx" in stuck["summary"]
    assert stuck["inventory_category"] == "lab"

    uploaded = client.post(
        "/api/uploads",
        files=[("files", ("Lab Equipment list Monolom.docx", data, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))],
    )
    assert uploaded.status_code == 200, uploaded.text
    body = uploaded.json()["results"][0]
    assert body["duplicate"] is True
    assert body["already_loaded"] is True
    assert body["links"] == []
    assert "כבר במלאי" in body["summary"]
    assert "רשימת מכשירי מעבדה" in body["summary"]
    assert client.get("/api/dashboard").json()["assets"] == before
    tasks = client.get("/api/tasks").json()["tasks"]
    assert not any(task.get("file_id") == "FIL-c9b632c7ec85df53" for task in tasks)


def test_new_lab_list_adds_a_laboratory_asset_and_keeps_conflicts(client):
    before = client.get("/api/dashboard").json()["assets"]
    fresh = _docx([["Bench Meter", "LAB-NEW-4292", "Acme", "M-100", "", ""]])
    uploaded = client.post(
        "/api/uploads",
        files=[("files", ("new-lab-list.docx", fresh, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))],
    )
    assert uploaded.status_code == 200, uploaded.text
    body = uploaded.json()["results"][0]
    assert body["imported_list"] is True
    assert body["links"] == []
    assert "LAB-NEW-4292" in body["summary"]
    assert body["inventory_category"] == "lab"
    assert client.get("/api/dashboard").json()["assets"] == before + 1
    found = client.get("/api/assets", params={"q": "LAB-NEW-4292", "category": "lab"}).json()["assets"]
    asset = next(item for item in found if item["tag"] == "LAB-NEW-4292")
    assert asset["category"] == "lab"
    card = client.get(f"/api/assets/{asset['id']}").json()
    technical = {item["label"]: item["value"] for item in card["technical"]}
    assert technical["יצרן"] == "Acme"
    assert technical["דגם"] == "M-100"
    again = client.post(
        "/api/uploads",
        files=[("files", ("new-lab-list.docx", fresh, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))],
    )
    assert again.json()["results"][0]["duplicate"] is True
    assert client.get("/api/dashboard").json()["assets"] == before + 1

    conflict = _docx([["Analytical Balance", "RDBAL-01", "Other Maker", "XP205", "B039071728", ""]])
    changed = client.post(
        "/api/uploads",
        files=[("files", ("conflict-lab.docx", conflict, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))],
    )
    assert changed.status_code == 200, changed.text
    assert "לא הוחלף" in changed.json()["results"][0]["summary"]
    rows = client.get("/api/bootstrap").json()["rows"]
    original = next(row for row in rows if row["tag_norm"] == "RDBAL-01" and row["source_kind"] == "lab")
    assert original["manufacturer"] == "Mettler Toledo"
    tasks = client.get("/api/tasks").json()["tasks"]
    assert any("RDBAL-01" in task["question"] and "Other Maker" in task["question"] and "Mettler Toledo" in task["question"] for task in tasks)
