# סיור ציוד

A Hebrew, right-to-left, mobile-first tool for photographing factory equipment and matching each stop to a row in the inventory. Capture is the screen that opens after login. Sales, valuation, buyer research, and a project dashboard are not part of this release.

A photo link is evidence that the item was seen. It is not a system relationship and it does not mean the items must be sold together.

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. The local password is `local-visit` unless `EQUIPMENT_PASSWORD` is set.

On first start the app loads, once:

- `data/sources/plant-equipment.xlsx` — the only plant list
- `data/sources/lab-instruments.docx` — laboratory instruments, included in the visit

A second plant file is rejected unless you explicitly replace the current one on the import screen. The spreadsheet column named `price` is not money (values are `0`, `1`, or `Y`) and is not mapped. Empty quantities, serials, and locations stay empty. `FUTURE`, `HOLD`, `SPARE`, `NEW PROJECT`, `Not in Use`, and `Under maintenance` stay on the row with their original text. Identical generic names (`Centrifuge`, `Dosing Pump`) are not collapsed. Counts in the visit summary are inventory rows, not physical machines.

Optional OCR uses the `tesseract` binary (`eng`). Capture still works when it is missing. English tags, models, and serials stay left-to-right.

This server is for a private visit. It uses a shared password over HTTP and is not meant to be exposed on the public internet. “נשמר במכשיר” is not a backup: clearing site data or losing the phone deletes photos that have not reached the server.

Photos and notes are written to IndexedDB as they are taken. The service worker caches the app shell only, not `/api`. Status labels are: שומר במכשיר, נשמר במכשיר, ממתין לסנכרון, מסנכרן, סונכרן לשרת, השמירה נכשלה. **סונכרן** appears only after the server confirms every photo file exists and is non-empty.

```bash
python3 -m pytest tests -q
```

## Acceptance tests (brief section 11)

| # | Check | Where it is covered |
|---|---|---|
| 1 | Import keeps the original sheet and row | `tests/test_acceptance.py` (`A-707` is sheet `07`, spreadsheet row 5). The row screen shows the source file, sheet, and row. |
| 2 | One capture holds the equipment photo and the tag photo | API test uploads two images on one capture id. In the app: choose an area, tap **ציוד חדש**, add both images, then **שמור ועבור לפריט הבא**. |
| 3 | A clear tag matches the right row, and the inventory opens its images | Exact unique tag `A-707` in area `07` auto-links. Opening that row shows the photos. |
| 4 | Changing the current area does not move earlier captures | The area selector applies to a new or still-empty capture. An earlier capture keeps its area unless you tap **עדכן את הקליטה הזו לאזור הנוכחי**. Covered by the API test (area `07`, then `31`). |
| 5 | Duplicate tags with different data are not merged | `HE-4142` (sheet `41`, rows 125 and 176) stays two rows and is not auto-linked. Same tag, same area, and identical filled data links every source row and keeps them all. The real file has no identical pair; that rule is unit-tested. |
| 6 | Linking two rows does not create a system relationship | Linking `P-9201` and `T-9201` stores evidence only. A relationship exists only after someone writes one. |
| 7 | An unidentified item can be saved and resolved later | **שמור בלי התאמה** leaves a visit review. Closing it with an empty answer keeps it open. Linking a row closes it. |
| 8 | A wrong match can be corrected without losing photos or notes | Undo and a manual relink are in the API test. Photos and the note stay on the capture. |
| 9 | Airplane mode keeps photos and notes across refresh | IndexedDB stores the image blobs. The shell can reload from the service worker. **This was not run on the physical phone.** Before the visit, turn on airplane mode, take several photos, refresh, and confirm they are still there. |
| 10 | Reconnect uploads each file once, and Synced waits for confirmation | Repeating the same photo id does not duplicate it. Confirming before the files exist does not mark the capture synced. |
| 11 | A storage failure is not shown as saved | If IndexedDB read-back fails, the capture is marked **השמירה נכשלה** and the message says it was not saved. A missing server file on confirm does the same. |
| 12 | The export package traces images to inventory rows | `/api/export/package.zip` contains `manifest.json`, `inventory.csv`, and the photos. The manifest records the stable row id, sheet, and original row. The CSV download is `/api/export/inventory.csv`. |

## Field workflow

1. Sign in once while online so the inventory is cached on the device.
2. On **קליטה**, pick the area. It stays on screen.
3. Tap **ציוד חדש**. Photograph the equipment and its tag, or upload images. Add a short note.
4. Accept an automatic link, pick rows yourself, or save without a match.
5. Tap **שמור ועבור לפריט הבא**.
6. Use **מלאי** to open a row, **בדיקה** for open questions, and **סיכום** for counts and the export.

Automatic linking happens only for an exact unique tag or serial, or for an identical same-area duplicate group, and only when the observed area does not conflict with the listed area. Similar names, models, and partial tags are suggestions. Unknown location is allowed.
