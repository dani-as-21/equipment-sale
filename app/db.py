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

CREATE TABLE IF NOT EXISTS valuation_subjects (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  row_id TEXT,
  quantity_text TEXT,
  included_text TEXT,
  excluded_text TEXT,
  family TEXT NOT NULL,
  subtype TEXT,
  family_evidence TEXT,
  family_source TEXT NOT NULL,
  premise TEXT NOT NULL DEFAULT 'unset',
  condition_status TEXT NOT NULL DEFAULT 'unknown',
  condition_text TEXT,
  market_text TEXT,
  currency TEXT,
  tax_basis TEXT,
  price_boundary TEXT,
  marketing_period TEXT,
  project_target TEXT NOT NULL,
  assumptions TEXT,
  limitations TEXT,
  research_status TEXT NOT NULL DEFAULT 'not_researched',
  method_preference TEXT NOT NULL DEFAULT 'unset',
  replacement_allowance TEXT,
  review_due TEXT,
  updated_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS valuation_members (
  subject_id TEXT NOT NULL,
  row_id TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'included',
  PRIMARY KEY (subject_id, row_id)
);

CREATE TABLE IF NOT EXISTS valuation_requirements (
  id TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL,
  req_key TEXT NOT NULL,
  impact TEXT NOT NULL,
  question TEXT NOT NULL,
  why TEXT NOT NULL,
  how_to TEXT NOT NULL,
  contact_role TEXT NOT NULL,
  site_presence TEXT NOT NULL,
  priority TEXT NOT NULL,
  evidence_status TEXT NOT NULL,
  evidence_json TEXT NOT NULL DEFAULT '[]',
  answer_text TEXT,
  na_reason TEXT,
  consequence TEXT,
  answered_by TEXT,
  UNIQUE(subject_id, req_key)
);

CREATE TABLE IF NOT EXISTS valuation_files (
  id TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL,
  requirement_id TEXT,
  stored_path TEXT NOT NULL,
  thumb_path TEXT,
  original_name TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_evidence (
  id TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL,
  source_url TEXT,
  source_document TEXT,
  title TEXT NOT NULL,
  research_date TEXT NOT NULL,
  listing_date TEXT,
  seller_type TEXT,
  identifiers TEXT,
  specifications TEXT,
  condition_text TEXT,
  location TEXT,
  price_text TEXT,
  price_amount REAL,
  currency TEXT,
  tax_treatment TEXT,
  included_services TEXT,
  price_type TEXT NOT NULL,
  role TEXT NOT NULL,
  exclusion_reason TEXT,
  differences TEXT,
  adjustment_note TEXT,
  duplicate_of TEXT,
  limitations TEXT,
  added_by TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS valuation_versions (
  id TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL,
  version_no INTEGER NOT NULL,
  status TEXT NOT NULL,
  info_readiness TEXT NOT NULL,
  market_readiness TEXT NOT NULL,
  range_low REAL,
  range_high REAL,
  currency TEXT,
  range_label TEXT NOT NULL,
  confidence TEXT,
  confidence_why TEXT,
  method TEXT,
  method_why TEXT,
  assumptions TEXT,
  sensitivities TEXT,
  outdated INTEGER NOT NULL DEFAULT 0,
  outdated_reason TEXT,
  author TEXT NOT NULL,
  reviewer_name TEXT,
  reviewer_scope TEXT,
  scenario TEXT NOT NULL DEFAULT 'normal_marketing',
  evidence_ids_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  UNIQUE(subject_id, scenario, version_no)
);

CREATE TABLE IF NOT EXISTS commercial_positions (
  row_id TEXT PRIMARY KEY,
  offer_text TEXT,
  target_text TEXT,
  note TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS machines (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  status TEXT NOT NULL,
  sold_together TEXT NOT NULL,
  basis TEXT NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS machine_members (
  id TEXT PRIMARY KEY,
  machine_id TEXT NOT NULL,
  tag_norm TEXT NOT NULL,
  tag_original TEXT NOT NULL,
  inventory_row_id TEXT,
  member_role TEXT NOT NULL,
  prefix_category TEXT,
  link_status TEXT NOT NULL,
  evidence TEXT NOT NULL,
  UNIQUE(machine_id, tag_norm)
);

CREATE TABLE IF NOT EXISTS intake_documents (
  id TEXT PRIMARY KEY,
  sha256 TEXT NOT NULL UNIQUE,
  original_name TEXT NOT NULL,
  stored_path TEXT NOT NULL,
  media_kind TEXT NOT NULL,
  extracted_text TEXT,
  extraction_note TEXT,
  doc_type TEXT NOT NULL,
  type_confidence TEXT NOT NULL,
  classification_reason TEXT,
  summary_json TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intake_links (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  inventory_row_id TEXT,
  tag_norm TEXT NOT NULL,
  link_status TEXT NOT NULL,
  reason TEXT NOT NULL,
  UNIQUE(document_id, tag_norm, inventory_row_id)
);

CREATE TABLE IF NOT EXISTS intake_facts (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  inventory_row_id TEXT,
  tag_norm TEXT NOT NULL,
  fact_key TEXT NOT NULL,
  fact_text TEXT NOT NULL,
  source_ref TEXT NOT NULL,
  conflict_with TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS purchase_offers (
  id TEXT PRIMARY KEY,
  family_id TEXT NOT NULL,
  version_no INTEGER NOT NULL,
  document_id TEXT NOT NULL UNIQUE,
  buyer TEXT,
  contacts TEXT,
  offer_date TEXT,
  expiry TEXT,
  price_basis TEXT NOT NULL,
  amount REAL,
  currency TEXT,
  vat TEXT,
  payment_terms TEXT,
  inspection TEXT,
  contingencies TEXT,
  dismantling TEXT,
  loading TEXT,
  transport TEXT,
  removal_date TEXT,
  exclusions TEXT,
  missing_terms_json TEXT NOT NULL,
  supersedes_id TEXT,
  assessment_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS offer_assets (
  id TEXT PRIMARY KEY,
  offer_id TEXT NOT NULL,
  inventory_row_id TEXT,
  tag_norm TEXT NOT NULL,
  tag_original TEXT NOT NULL,
  role TEXT NOT NULL,
  quantity TEXT,
  UNIQUE(offer_id, tag_norm, role)
);

CREATE TABLE IF NOT EXISTS service_quotes (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL UNIQUE,
  supplier TEXT,
  service_kind TEXT,
  amount REAL,
  currency TEXT,
  scope_text TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rows_tag ON inventory_rows(tag_norm);
CREATE INDEX IF NOT EXISTS idx_intake_status ON intake_documents(status);
CREATE INDEX IF NOT EXISTS idx_intake_links_row ON intake_links(inventory_row_id);
CREATE INDEX IF NOT EXISTS idx_offer_family ON purchase_offers(family_id);
CREATE INDEX IF NOT EXISTS idx_offer_assets_row ON offer_assets(inventory_row_id);
CREATE INDEX IF NOT EXISTS idx_machine_members_tag ON machine_members(tag_norm);
CREATE INDEX IF NOT EXISTS idx_machine_members_row ON machine_members(inventory_row_id);
CREATE INDEX IF NOT EXISTS idx_rows_kind ON inventory_rows(kind);
CREATE INDEX IF NOT EXISTS idx_photos_capture ON photos(capture_id);
CREATE INDEX IF NOT EXISTS idx_links_row ON evidence_links(inventory_row_id);
CREATE INDEX IF NOT EXISTS idx_links_capture ON evidence_links(capture_id);
CREATE INDEX IF NOT EXISTS idx_review_status ON review_items(status);
CREATE INDEX IF NOT EXISTS idx_val_req_subject ON valuation_requirements(subject_id);
CREATE INDEX IF NOT EXISTS idx_val_evidence_subject ON market_evidence(subject_id);

CREATE TABLE IF NOT EXISTS project_files (
  id TEXT PRIMARY KEY,
  sha256 TEXT NOT NULL UNIQUE,
  original_name TEXT,
  stored_path TEXT,
  media_kind TEXT,
  extracted_text TEXT,
  note_text TEXT,
  doc_type TEXT,
  confidence TEXT,
  summary TEXT,
  status TEXT NOT NULL,
  user_locked INTEGER NOT NULL DEFAULT 0,
  matched_source_id TEXT,
  matched_category TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_file_links (
  id TEXT PRIMARY KEY,
  file_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  tag_norm TEXT,
  confidence TEXT,
  reason TEXT,
  user_locked INTEGER NOT NULL DEFAULT 0,
  UNIQUE(file_id, asset_id)
);

CREATE TABLE IF NOT EXISTS manual_assets (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  category TEXT NOT NULL,
  tag TEXT,
  note TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS valuation_notes (
  id TEXT PRIMARY KEY,
  asset_id TEXT NOT NULL,
  source TEXT,
  evidence_date TEXT,
  currency TEXT,
  price_text TEXT,
  description TEXT,
  manufacturer TEXT,
  model TEXT,
  year TEXT,
  condition_text TEXT,
  location TEXT,
  value_kind TEXT,
  body TEXT,
  file_id TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_project_links_asset ON project_file_links(asset_id);
CREATE INDEX IF NOT EXISTS idx_valuation_notes_asset ON valuation_notes(asset_id);

CREATE TABLE IF NOT EXISTS asset_components (
  id TEXT PRIMARY KEY,
  asset_id TEXT NOT NULL,
  tag_norm TEXT NOT NULL DEFAULT '',
  tag_original TEXT NOT NULL DEFAULT '',
  name TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  inventory_row_id TEXT,
  origin TEXT NOT NULL,
  link_status TEXT NOT NULL,
  user_locked INTEGER NOT NULL DEFAULT 0,
  explanation TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_asset_components_asset ON asset_components(asset_id);
CREATE INDEX IF NOT EXISTS idx_asset_components_row ON asset_components(inventory_row_id);

CREATE TABLE IF NOT EXISTS asset_estimates (
  asset_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
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
    columns = {row[1] for row in conn.execute("PRAGMA table_info(project_files)")}
    if "matched_source_id" not in columns:
        conn.execute("ALTER TABLE project_files ADD COLUMN matched_source_id TEXT")
    if "matched_category" not in columns:
        conn.execute("ALTER TABLE project_files ADD COLUMN matched_category TEXT")
    if own:
        conn.commit()
        conn.close()
