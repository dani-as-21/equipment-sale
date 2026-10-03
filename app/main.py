"""Hebrew, mobile-first equipment capture site."""

from __future__ import annotations

import hashlib
import hmac
import json
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import importer, logic, valuation
from app.db import connect, init_db
from app.matching import match_inventory
from app.settings import get_settings

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def get_conn():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def require_user(request: Request) -> None:
    if not request.session.get("ok"):
        raise HTTPException(status_code=401, detail="נדרשת כניסה.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.photo_dir.mkdir(parents=True, exist_ok=True)
    settings.thumb_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    conn = connect()
    try:
        importer.seed_sources(conn, settings.sources_dir, logic.now_iso())
        conn.commit()
    finally:
        conn.close()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="סיור ציוד", lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=settings.secret, same_site="lax", https_only=False, max_age=14 * 24 * 3600)
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        return TEMPLATES.TemplateResponse(request, "index.html", {})

    @app.get("/api/health")
    def health():
        settings = get_settings()
        probe = settings.photo_dir / ".write-probe"
        writable = False
        try:
            settings.photo_dir.mkdir(parents=True, exist_ok=True)
            probe.write_text("ok", encoding="utf-8")
            writable = probe.read_text(encoding="utf-8") == "ok"
            probe.unlink(missing_ok=True)
        except OSError:
            writable = False
        conn = connect()
        try:
            count = conn.execute("SELECT COUNT(*) AS n FROM inventory_rows WHERE kind = 'equipment'").fetchone()["n"]
        except Exception:
            count = 0
        finally:
            conn.close()
        return {
            "ok": True,
            "inventory_rows": count,
            "storage_writable": writable,
            "ocr_available": bool(shutil.which("tesseract")),
        }

    @app.post("/api/login")
    def login(request: Request, body: dict):
        password = body.get("password") or ""
        if not hmac.compare_digest(password, get_settings().password):
            raise HTTPException(status_code=401, detail="סיסמה שגויה.")
        request.session["ok"] = True
        return {"ok": True}

    @app.post("/api/logout")
    def logout(request: Request):
        request.session.clear()
        return {"ok": True}

    @app.get("/api/me")
    def me(request: Request):
        return {"ok": bool(request.session.get("ok"))}

    @app.get("/api/bootstrap")
    def bootstrap(request: Request, conn=Depends(get_conn)):
        require_user(request)
        sources = {row["id"]: row["display_name"] for row in conn.execute("SELECT id, display_name FROM source_files")}
        rows = [
            logic.public_row(row, sources.get(row["source_file_id"], ""))
            for row in conn.execute("SELECT * FROM inventory_rows ORDER BY source_kind, sheet_name, original_row")
        ]
        areas = [dict(row) for row in conn.execute("SELECT * FROM areas ORDER BY source, name")]
        visit = conn.execute("SELECT * FROM visits WHERE is_current = 1").fetchone()
        notes = [row["text"] for row in conn.execute("SELECT text FROM import_notes ORDER BY id")]
        captures = [dict(row) for row in conn.execute("SELECT * FROM captures ORDER BY created_at")]
        photos = []
        for photo in conn.execute("SELECT id, capture_id, role, original_name, upload_state, shows_json, ocr_raw, created_at FROM photos"):
            item = dict(photo)
            item["shows"] = logic.parse_json(item.pop("shows_json"), {})
            photos.append(item)
        links = [dict(row) for row in conn.execute("SELECT * FROM evidence_links")]
        reviews = []
        for review in conn.execute("SELECT * FROM review_items ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, created_at"):
            item = dict(review)
            item["row_ids"] = logic.parse_json(item.pop("row_ids_json"), [])
            item["payload"] = logic.parse_json(item.pop("payload_json"), {})
            reviews.append(item)
        relationships = []
        for claim in conn.execute("SELECT * FROM relationship_claims"):
            item = dict(claim)
            item["row_ids"] = logic.parse_json(item.pop("related_row_ids_json"), [])
            relationships.append(item)
        summary = logic.visit_summary(conn)
        summary.pop("revisit", None)
        files = [dict(row) for row in conn.execute("SELECT id, kind, filename, display_name, sha256, mapping_json FROM source_files")]
        for file_row in files:
            file_row["mapping"] = logic.parse_json(file_row.pop("mapping_json"), {})
        return {
            "areas": areas,
            "visit": dict(visit) if visit else None,
            "notes": notes,
            "rows": rows,
            "captures": captures,
            "photos": photos,
            "links": links,
            "reviews": reviews,
            "relationships": relationships,
            "summary": summary,
            "files": files,
            "labels": {
                "saved_on_device": "נשמר במכשיר אינו גיבוי. מחיקת נתוני האתר או אובדן המכשיר מוחקים צילומים שטרם סונכרנו.",
            },
        }

    @app.get("/api/rows/{row_id}")
    def row_detail(row_id: str, request: Request, conn=Depends(get_conn)):
        require_user(request)
        record = conn.execute("SELECT * FROM inventory_rows WHERE id = ?", (row_id,)).fetchone()
        if not record:
            raise HTTPException(status_code=404, detail="השורה לא נמצאה.")
        source = conn.execute("SELECT display_name FROM source_files WHERE id = ?", (record["source_file_id"],)).fetchone()
        links = [
            dict(row)
            for row in conn.execute("SELECT * FROM evidence_links WHERE inventory_row_id = ?", (row_id,))
        ]
        capture_ids = [link["capture_id"] for link in links]
        captures = []
        photos = []
        if capture_ids:
            marks = ",".join("?" for _ in capture_ids)
            captures = [dict(row) for row in conn.execute(f"SELECT * FROM captures WHERE id IN ({marks})", capture_ids)]
            for photo in conn.execute(f"SELECT * FROM photos WHERE capture_id IN ({marks})", capture_ids):
                item = dict(photo)
                item["shows"] = logic.parse_json(item.pop("shows_json"), {})
                item.pop("stored_path", None)
                item.pop("thumb_path", None)
                photos.append(item)
        claims = []
        if capture_ids:
            marks = ",".join("?" for _ in capture_ids)
            for claim in conn.execute(f"SELECT * FROM relationship_claims WHERE capture_id IN ({marks})", capture_ids):
                item = dict(claim)
                item["row_ids"] = logic.parse_json(item.pop("related_row_ids_json"), [])
                claims.append(item)
        reviews = []
        for review in conn.execute("SELECT * FROM review_items"):
            row_ids = logic.parse_json(review["row_ids_json"], [])
            if row_id in row_ids or review["capture_id"] in capture_ids:
                item = dict(review)
                item["row_ids"] = logic.parse_json(item.pop("row_ids_json"), [])
                item["payload"] = logic.parse_json(item.pop("payload_json"), {})
                reviews.append(item)
        history = [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM change_log
                WHERE entity_id = ? OR entity_id IN (
                  SELECT id FROM captures WHERE id IN (SELECT capture_id FROM evidence_links WHERE inventory_row_id = ?)
                )
                ORDER BY id DESC LIMIT 40
                """,
                (row_id, row_id),
            )
        ]
        observed = []
        for capture in captures:
            if capture["observed_area_name"]:
                observed.append(capture["observed_area_name"])
        return {
            "row": logic.public_row(record, source["display_name"] if source else ""),
            "links": links,
            "captures": captures,
            "photos": photos,
            "relationships": claims,
            "reviews": reviews,
            "history": history,
            "observed_areas": list(dict.fromkeys(observed)),
            "relationship_note": "הערת קשר נשמרת רק אם מישהו אמר אותה. קישור צילום לכמה שורות אינו קשר מערכת.",
        }

    @app.post("/api/match")
    def match(request: Request, body: dict, conn=Depends(get_conn)):
        require_user(request)
        _area_id, area_name, parent = logic.area_context(conn, body.get("observed_area_id"), body.get("observed_area"))
        result = match_inventory(
            logic.equipment_for_match(conn),
            tag=body.get("tag") or "",
            serial=body.get("serial") or "",
            model=body.get("model") or "",
            observed_area=area_name or body.get("observed_area") or "",
            observed_parent=parent or "",
        )
        return result

    @app.post("/api/areas")
    def add_area(request: Request, body: dict, conn=Depends(get_conn)):
        require_user(request)
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="חסר שם אזור.")
        parent_id = body.get("parent_id") or None
        if parent_id and not conn.execute("SELECT id FROM areas WHERE id = ?", (parent_id,)).fetchone():
            raise HTTPException(status_code=400, detail="אזור האב לא נמצא.")
        existing = conn.execute("SELECT * FROM areas WHERE name = ?", (name,)).fetchone()
        if existing:
            return dict(existing)
        area = {
            "id": importer.area_id(name if not parent_id else f"{parent_id}/{name}"),
            "name": name,
            "parent_id": parent_id,
            "source": "user",
            "note": "נוסף בזמן הסיור.",
        }
        # Keep the id stable for the same child name under the same parent.
        conn.execute(
            "INSERT INTO areas (id, name, parent_id, source, note) VALUES (:id, :name, :parent_id, :source, :note)",
            area,
        )
        return area

    @app.post("/api/visits")
    def new_visit(request: Request, body: dict, conn=Depends(get_conn)):
        require_user(request)
        created = logic.now_iso()
        visit_id = "VISIT-" + hashlib.sha256(f"{created}:{body.get('description','')}".encode()).hexdigest()[:12]
        conn.execute("UPDATE visits SET is_current = 0")
        conn.execute(
            "INSERT INTO visits (id, visit_date, description, is_current, created_at) VALUES (?, ?, ?, 1, ?)",
            (visit_id, created[:10], (body.get("description") or "סיור ציוד")[:200], created),
        )
        return {"id": visit_id}

    @app.post("/api/sync/capture")
    def sync_capture(request: Request, body: dict, conn=Depends(get_conn)):
        require_user(request)
        try:
            return logic.apply_capture(conn, body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/sync/photo")
    async def sync_photo(
        request: Request,
        conn=Depends(get_conn),
        file: UploadFile = File(...),
        photo_id: str = Form(...),
        capture_id: str = Form(...),
        role: str = Form(""),
        shows_json: str = Form(""),
    ):
        require_user(request)
        data = await file.read()
        if len(data) > 25 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="הקובץ גדול מדי ולא נשמר.")
        shows = logic.parse_json(shows_json, None) if shows_json else None
        settings = get_settings()
        try:
            return logic.save_photo_file(
                conn,
                photo_id=photo_id,
                capture_id=capture_id,
                role=role,
                original_name=file.filename or "photo.jpg",
                data=data,
                shows=shows,
                photo_dir=settings.photo_dir,
                thumb_dir=settings.thumb_dir,
                created_at=logic.now_iso(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail="שמירת הצילום בשרת נכשלה. הקליטה לא סומנה כמסונכרנת.") from exc

    @app.post("/api/sync/capture/{capture_id}/confirm")
    def confirm(capture_id: str, request: Request, body: dict, conn=Depends(get_conn)):
        require_user(request)
        return logic.confirm_capture(conn, capture_id, body.get("photo_ids") or [], logic.now_iso())

    @app.post("/api/captures/{capture_id}/split")
    def split(capture_id: str, request: Request, body: dict, conn=Depends(get_conn)):
        require_user(request)
        try:
            return logic.split_capture(conn, capture_id, body.get("new_capture_id") or "", body.get("photo_ids") or [], logic.now_iso())
        except (ValueError, LookupError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch("/api/photos/{photo_id}")
    def patch_photo(photo_id: str, request: Request, body: dict, conn=Depends(get_conn)):
        require_user(request)
        try:
            return logic.update_photo(conn, photo_id, body, logic.now_iso())
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/photos/{photo_id}/{variant}")
    def photo_file(photo_id: str, variant: str, request: Request, conn=Depends(get_conn)):
        require_user(request)
        photo = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
        if not photo:
            raise HTTPException(status_code=404)
        path = photo["thumb_path"] if variant == "thumb" and photo["thumb_path"] else photo["stored_path"]
        if not path or not Path(path).exists():
            raise HTTPException(status_code=404)
        return FileResponse(path)

    @app.post("/api/ocr/{photo_id}")
    def ocr(photo_id: str, request: Request, conn=Depends(get_conn)):
        require_user(request)
        photo = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
        if not photo or not photo["stored_path"]:
            raise HTTPException(status_code=404, detail="הצילום עוד לא נשמר בשרת.")
        result = logic.run_ocr(photo["stored_path"])
        if result.get("available"):
            conn.execute("UPDATE photos SET ocr_raw = ? WHERE id = ?", (result.get("raw_text") or "", photo_id))
        return result

    @app.post("/api/captures/{capture_id}/relationship")
    def relationship(capture_id: str, request: Request, body: dict, conn=Depends(get_conn)):
        require_user(request)
        if not conn.execute("SELECT id FROM captures WHERE id = ?", (capture_id,)).fetchone():
            raise HTTPException(status_code=404, detail="הקליטה לא נמצאה.")
        try:
            return logic.add_relationship(conn, capture_id, body, logic.now_iso())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/captures/{capture_id}/nameplate-unreadable")
    def nameplate(capture_id: str, request: Request, body: dict, conn=Depends(get_conn)):
        require_user(request)
        photo_id = body.get("photo_id") or ""
        logic.upsert_review(
            conn,
            kind="nameplate",
            queue="visit",
            dedupe_key=f"cap:{capture_id}:nameplate:{photo_id}",
            question="השלט לא קריא. צריך צילום חדש מקרוב, או להקליד את מה שכן רואים. העלאה לא ברורה לא סוגרת את השאלה.",
            priority="high",
            capture_id=capture_id,
            row_ids=[],
            payload={"photo_id": photo_id},
            created_at=logic.now_iso(),
        )
        return {"ok": True}

    @app.post("/api/review/{review_id}/action")
    def review_act(review_id: str, request: Request, body: dict, conn=Depends(get_conn)):
        require_user(request)
        try:
            return logic.review_action(conn, review_id, body, logic.now_iso())
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/review/{review_id}/photo")
    async def review_photo(review_id: str, request: Request, conn=Depends(get_conn), file: UploadFile = File(...)):
        require_user(request)
        item = conn.execute("SELECT * FROM review_items WHERE id = ?", (review_id,)).fetchone()
        if not item or not item["capture_id"]:
            raise HTTPException(status_code=400, detail="אין קליטה לצרף אליה את הקובץ.")
        data = await file.read()
        photo_id = __import__("uuid").uuid4().__str__()
        settings = get_settings()
        logic.save_photo_file(
            conn,
            photo_id=photo_id,
            capture_id=item["capture_id"],
            role="nameplate",
            original_name=file.filename or "photo.jpg",
            data=data,
            shows={"scope": "unassigned", "row_ids": []},
            photo_dir=settings.photo_dir,
            thumb_dir=settings.thumb_dir,
            created_at=logic.now_iso(),
        )
        message = "הקובץ נוסף לקליטה, אבל השאלה עדיין פתוחה. העלאה לבד לא עונה עליה."
        conn.execute(
            "UPDATE review_items SET missing_reason = ?, updated_at = ? WHERE id = ?",
            (message, logic.now_iso(), review_id),
        )
        return {"ok": True, "closed": False, "message": message, "photo_id": photo_id}

    @app.post("/api/undo/{log_id}")
    def undo(log_id: int, request: Request, conn=Depends(get_conn)):
        require_user(request)
        try:
            return logic.undo_change(conn, log_id, logic.now_iso())
        except (LookupError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/import/preview")
    def import_preview(request: Request, conn=Depends(get_conn)):
        require_user(request)
        settings = get_settings()
        plant_row = conn.execute("SELECT filename FROM source_files WHERE kind = 'plant'").fetchone()
        plant_name = plant_row["filename"] if plant_row else "plant-equipment.xlsx"
        plant = settings.sources_dir / plant_name
        if not plant.exists():
            plant = settings.sources_dir / "plant-equipment.xlsx"
        preview = importer.preview_workbook(plant) if plant.exists() else {"sheets": [], "suggested_mapping": importer.PLANT_MAPPING}
        files = []
        for row in conn.execute("SELECT id, kind, filename, display_name, sha256, mapping_json FROM source_files"):
            item = dict(row)
            item["mapping"] = json.loads(item.pop("mapping_json"))
            files.append(item)
        notes = [row["text"] for row in conn.execute("SELECT text FROM import_notes ORDER BY id")]
        return {"preview": preview, "files": files, "notes": notes, "suggested_lab": importer.LAB_MAPPING}

    @app.post("/api/import/mapping")
    def import_mapping(request: Request, body: dict, conn=Depends(get_conn)):
        require_user(request)
        try:
            return importer.remap_file(conn, body.get("kind") or "plant", body.get("mapping") or {}, logic.now_iso())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/import/file")
    async def import_file(
        request: Request,
        conn=Depends(get_conn),
        file: UploadFile = File(...),
        kind: str = Form(...),
        replace: str = Form("false"),
    ):
        require_user(request)
        settings = get_settings()
        suffix = Path(file.filename or "").suffix.lower()
        data = await file.read()
        sha = hashlib.sha256(data).hexdigest()
        target_dir = settings.sources_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        created = logic.now_iso()
        do_replace = replace.lower() in {"1", "true", "yes"}
        try:
            if kind == "plant":
                if suffix not in {".xlsx", ".csv"}:
                    raise HTTPException(status_code=400, detail="רשימת המפעל צריכה להיות XLSX או CSV.")
                target = target_dir / ("plant-upload.xlsx" if suffix == ".xlsx" else "plant-upload.csv")
                records, sheets = _read_plant_bytes(data, suffix)
                result = importer.import_parsed_file(
                    conn,
                    kind="plant",
                    filename=target.name,
                    display_name="רשימת ציוד המפעל",
                    sha256=sha,
                    records=records,
                    sheet_names=sheets,
                    mapping=importer.PLANT_MAPPING,
                    now=created,
                    replace=do_replace,
                )
                target.write_bytes(data)
                return result | {"refreshed": _refresh(conn, sheets, created)}
            if kind == "lab":
                if suffix != ".docx":
                    raise HTTPException(status_code=400, detail="רשימת המעבדה צריכה להיות מסמך Word.")
                target = target_dir / "lab-upload.docx"
                records = _read_lab_bytes(data)
                result = importer.import_parsed_file(
                    conn,
                    kind="lab",
                    filename=target.name,
                    display_name="רשימת מכשירי מעבדה",
                    sha256=sha,
                    records=records,
                    sheet_names=[],
                    mapping=importer.LAB_MAPPING,
                    now=created,
                    replace=do_replace,
                )
                target.write_bytes(data)
                importer.refresh_import_reviews(conn, created)
                importer.write_import_notes(conn)
                return result
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail="סוג קובץ לא נתמך.")

    @app.get("/api/summary")
    def summary(request: Request, conn=Depends(get_conn)):
        require_user(request)
        return logic.visit_summary(conn)

    @app.get("/api/export/inventory.csv")
    def export_csv(request: Request, conn=Depends(get_conn)):
        require_user(request)
        content = "\ufeff" + logic.inventory_csv(conn)
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=inventory.csv"},
        )

    @app.get("/api/export/package.zip")
    def export_zip(request: Request, conn=Depends(get_conn)):
        require_user(request)
        settings = get_settings()
        target = settings.root / "data" / "exports" / "survey-package.zip"
        logic.export_package(conn, target)
        return FileResponse(target, filename="survey-package.zip", media_type="application/zip")

    @app.get("/api/valuation/overview")
    def valuation_overview(request: Request, conn=Depends(get_conn), area: str = "", family: str = "", info: str = "", q: str = "", limit: int = 40, offset: int = 0):
        require_user(request)
        return valuation.overview(conn, area=area, family=family, info=info, q=q, limit=min(limit, 80), offset=offset)

    @app.get("/api/valuation/visit-prep")
    def valuation_visit_prep(request: Request, conn=Depends(get_conn)):
        require_user(request)
        return valuation.visit_preparation(conn)

    @app.get("/api/valuation/comparables")
    def valuation_comparables(request: Request, conn=Depends(get_conn), subject_id: str = ""):
        require_user(request)
        return valuation.comparables(conn, subject_id or None)

    @app.post("/api/valuation/subjects")
    def valuation_open(request: Request, body: dict, conn=Depends(get_conn)):
        require_user(request)
        try:
            return valuation.ensure_row_subject(conn, body.get("row_id") or "", logic.now_iso())
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/valuation/subjects/{subject_id}")
    def valuation_subject(subject_id: str, request: Request, conn=Depends(get_conn)):
        require_user(request)
        try:
            return valuation.subject_payload(conn, subject_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/valuation/groups")
    def valuation_group(request: Request, body: dict, conn=Depends(get_conn)):
        require_user(request)
        try:
            return valuation.create_group(conn, body.get("row_ids") or [], body.get("title") or "", logic.now_iso())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/valuation/subjects/{subject_id}/basis")
    def valuation_basis(subject_id: str, request: Request, body: dict, conn=Depends(get_conn)):
        require_user(request)
        try:
            return valuation.update_basis(conn, subject_id, body, logic.now_iso())
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/valuation/subjects/{subject_id}/requirements")
    def valuation_add_requirement(subject_id: str, request: Request, body: dict, conn=Depends(get_conn)):
        require_user(request)
        try:
            return valuation.add_requirement(conn, subject_id, body, logic.now_iso())
        except (LookupError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/valuation/requirements/{requirement_id}/answer")
    def valuation_answer(requirement_id: str, request: Request, body: dict, conn=Depends(get_conn)):
        require_user(request)
        try:
            return valuation.answer_requirement(conn, requirement_id, body, logic.now_iso())
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/valuation/requirements/{requirement_id}/photo")
    async def valuation_photo(requirement_id: str, request: Request, conn=Depends(get_conn), file: UploadFile = File(...)):
        require_user(request)
        data = await file.read()
        settings = get_settings()
        try:
            return valuation.save_requirement_file(
                conn, requirement_id, data, file.filename or "photo.jpg", settings.photo_dir, settings.thumb_dir, logic.now_iso()
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail="הקובץ לא נשמר, והדרישה לא סומנה כמוכחת.") from exc

    @app.post("/api/valuation/subjects/{subject_id}/evidence")
    def valuation_evidence(subject_id: str, request: Request, body: dict, conn=Depends(get_conn)):
        require_user(request)
        try:
            return valuation.add_evidence(conn, subject_id, body, logic.now_iso())
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/valuation/evidence/{evidence_id}")
    def valuation_evidence_update(evidence_id: str, request: Request, body: dict, conn=Depends(get_conn)):
        require_user(request)
        try:
            return valuation.update_evidence(conn, evidence_id, body, logic.now_iso())
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/valuation/subjects/{subject_id}/time-scenario")
    def valuation_time(subject_id: str, request: Request, conn=Depends(get_conn)):
        require_user(request)
        try:
            return valuation.add_time_scenario(conn, subject_id, logic.now_iso())
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/valuation/subjects/{subject_id}/specialist")
    def valuation_specialist(subject_id: str, request: Request, body: dict, conn=Depends(get_conn)):
        require_user(request)
        try:
            return valuation.record_specialist(conn, subject_id, body, logic.now_iso())
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/commercial/{row_id}")
    def commercial_get(row_id: str, request: Request, conn=Depends(get_conn)):
        require_user(request)
        return valuation.commercial_payload(conn, row_id)

    @app.post("/api/commercial/{row_id}")
    def commercial_save(row_id: str, request: Request, body: dict, conn=Depends(get_conn)):
        require_user(request)
        try:
            return valuation.save_commercial(conn, row_id, body, logic.now_iso())
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return app


def _read_plant_bytes(data: bytes, suffix: str):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / f"upload{suffix}"
        path.write_bytes(data)
        if suffix == ".xlsx":
            return importer.read_plant_xlsx(path)
        return importer.read_plant_csv(path)


def _read_lab_bytes(data: bytes):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "upload.docx"
        path.write_bytes(data)
        return importer.read_lab_docx(path)


def _refresh(conn, sheets, created: str) -> bool:
    if sheets:
        importer.ensure_areas(conn, sheets)
    importer.refresh_import_reviews(conn, created)
    importer.write_import_notes(conn)
    importer.ensure_current_visit(conn, created)
    return True


app = create_app()
