"""SQLite schema and connection helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.settings import get_settings

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS source_files (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  filename TEXT NOT NULL,
  display_name TEXT NOT NULL,
  sha256 TEXT NOT NULL UNIQUE,
  imported_at TEXT NOT NULL,
  mapping_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory_rows (
  id TEXT PRIMARY KEY,
  source_file_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  sheet_name TEXT NOT NULL,
  original_row INTEGER NOT NULL,
  identity_area TEXT NOT NULL,
  listed_area TEXT,
  tag_original TEXT,
  tag_norm TEXT,
  description TEXT,
  manufacturer TEXT,
  model TEXT,
  serial TEXT,
  serial_norm TEXT,
  quantity TEXT,
  remarks TEXT,
  specs TEXT,
  material TEXT,
  system_number TEXT,
  efd TEXT,
  installation_raw TEXT,
  labels_json TEXT NOT NULL DEFAULT '[]',
  raw_json TEXT NOT NULL,
  duplicate_group TEXT,
  source_kind TEXT NOT NULL,
  UNIQUE(source_file_id, sheet_name, original_row)
);

CREATE TABLE IF NOT EXISTS areas (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  parent_id TEXT,
  source TEXT NOT NULL,
  note TEXT
);

CREATE TABLE IF NOT EXISTS visits (
  id TEXT PRIMARY KEY,
  visit_date TEXT NOT NULL,
  description TEXT,
  is_current INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS captures (
  id TEXT PRIMARY KEY,
  visit_id TEXT,
  observed_area_id TEXT,
  observed_area_name TEXT,
  note TEXT,
  info_provided_by TEXT,
  tag_text TEXT,
  model_text TEXT,
  serial_text TEXT,
  manufacturer_text TEXT,
  raw_ocr TEXT,
  match_mode TEXT,
  finalized INTEGER NOT NULL DEFAULT 0,
  client_updated_at TEXT,
  created_at TEXT NOT NULL,
  sync_status TEXT NOT NULL,
  server_received_at TEXT
);

CREATE TABLE IF NOT EXISTS photos (
  id TEXT PRIMARY KEY,
  capture_id TEXT NOT NULL,
  role TEXT,
  original_name TEXT,
  stored_path TEXT,
  thumb_path TEXT,
  ocr_raw TEXT,
  upload_state TEXT NOT NULL,
  shows_json TEXT NOT NULL DEFAULT '{"scope":"unassigned","row_ids":[]}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_links (
  id TEXT PRIMARY KEY,
  capture_id TEXT NOT NULL,
  inventory_row_id TEXT NOT NULL,
  method TEXT NOT NULL,
  reason TEXT NOT NULL,
  review_status TEXT NOT NULL,
  creator TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(capture_id, inventory_row_id)
);

CREATE TABLE IF NOT EXISTS relationship_claims (
  id TEXT PRIMARY KEY,
  capture_id TEXT,
  text TEXT NOT NULL,
  person TEXT,
  status TEXT NOT NULL,
  related_row_ids_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_items (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  queue TEXT NOT NULL,
  dedupe_key TEXT NOT NULL UNIQUE,
  question TEXT NOT NULL,
  priority TEXT NOT NULL,
  status TEXT NOT NULL,
  capture_id TEXT,
  row_ids_json TEXT NOT NULL DEFAULT '[]',
  payload_json TEXT NOT NULL DEFAULT '{}',
  resolution TEXT,
  missing_reason TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS change_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  prior_json TEXT,
  new_json TEXT,
  created_at TEXT NOT NULL,
  undone INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS import_notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  level TEXT NOT NULL,
  text TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rows_tag ON inventory_rows(tag_norm);
CREATE INDEX IF NOT EXISTS idx_rows_kind ON inventory_rows(kind);
CREATE INDEX IF NOT EXISTS idx_photos_capture ON photos(capture_id);
CREATE INDEX IF NOT EXISTS idx_links_row ON evidence_links(inventory_row_id);
CREATE INDEX IF NOT EXISTS idx_links_capture ON evidence_links(capture_id);
CREATE INDEX IF NOT EXISTS idx_review_status ON review_items(status);
"""


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    settings = get_settings()
    path = Path(db_path) if db_path else settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    if own:
        conn = connect()
    conn.executescript(SCHEMA)
    if own:
        conn.commit()
        conn.close()
