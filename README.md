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

## Photos, tags, and machines

Photos upload from **קליטה** immediately: the camera button, the file button, or a drop. No area, asset, or system has to be selected first, and an open question does not block the next upload. The area selector still applies only when you start a one-item capture and tap **צרף לקליטה הזו**.

A tag prefix is a category only, read without regard to case. The original tag text is kept. `R` reactor, `A` agitator or mixer, `P` pump, `F` filter, `D` dryer, `H` heat exchanger, `HVAC` or `AHU` air handling unit, `B` blower, `C` distillation or absorption column, `E` general equipment, `W` weighing equipment, `T` tank, `X` an additional unit that typically includes a compressor, including heating and cooling. The prefix does not identify the asset or its parent.

A clear tag or nameplate files the photo onto that inventory row and, when that row belongs to a machine, shows it inside the parent as a photo of that component. A photo of `F-4208` is not labeled as a photo of `R-4208`. Several clear tags on one photo link to each of those rows. An area stored on the matched inventory row is shown as the area recorded in the inventory, not as a location verified from the photo. An unreadable tag, an ambiguous match, or text that does not identify a row stays unmatched, with the possible rows and a review task.

Machines are built from item names, tag prefixes, shared numbers, descriptions, and explicit references. A shared number alone does not create a machine. The user confirmed that `R-4208` and `F-4208` are one machine and are sold together; that pair is stored as confirmed by the user. Those tags are not in the loaded spreadsheet, so no inventory row was created for them. A similar explicit mention in a description becomes a proposed machine, with a clarification task when the boundary or the sale unit is still open. Confirmed and proposed stay distinct. Each component keeps its Excel row, tag, description, and source. Confirming, renaming, splitting, combining, or dissolving a machine does not delete those rows or add a second inventory value or a second sale commitment.

Open questions are on **בדיקה**, including the **מכונות** filter: unreadable tags, ambiguous matches, uncertain groupings, conflicting descriptions, and unclear sale-unit boundaries. A task shows the photos, the Excel rows, the question, and what would resolve it. An answer or an added file updates the affected record and leaves the task open until the boundary is actually confirmed or dissolved.

## Incoming documents

**מסמכים** is a separate upload from the photo capture on **קליטה**. PDFs, spreadsheets, Word files, images, and screenshots can be uploaded without choosing a type or an asset first.

Each file is classified as equipment information, a purchase offer (possible proceeds), a service quotation (possible cost), appraisal or market evidence, or mixed/unclear. An uncertain type becomes a question. After processing, the screen shows a Hebrew summary: what the file is, who issued it and the date when those are written, the assets, amounts, conditions, deadlines, exclusions, what was added, what is unclear, and the next action, with the page, paragraph, or spreadsheet row.

Equipment facts are filed only onto rows the text actually identifies. A fact about one component is not copied to the other components in its machine. Conflicts are kept and opened as a task. An uncertain tag stays a candidate. No new asset is created because the wording differs. The same file bytes are not ingested twice.

A purchase offer keeps buyer, dates, scope, price basis, amount, currency, VAT, terms, and exclusions when they are written. A later document is a new version. It does not delete the previous version, and it does not cancel it unless the text says so. Offers are on **הצעות** and on the linked equipment. The offer is compared with an evidence-backed range for the same assets. It does not set that range. A package is not split across components and is not compared by adding component values. Unknown seller costs stay unknown. Being inside the range is not a recommendation to accept, and the app does not accept, reject, or contact anyone.

## Tour files

On **קליטה**, drop a group of photos and documents onto the screen, or choose several files together. There is no folder picker, no rename step, and no need to type an asset id. The area selector is not used for that upload: the observed area stays unknown unless the file itself names an area (for example `אזור: 07`) or the file is the single file attached to a capture that is already clearly linked.

A clear tag or nameplate text files the copy onto that inventory row. The same tag on different rows, a generic name, or text with no identifier stays in the review queue. The file is written on the server before it is shown as saved, including which row it was filed to. A copy that exists only on the phone is not shown as saved.

Automatic linking happens only for an exact unique tag or serial, or for an identical same-area duplicate group, and only when the observed area does not conflict with the listed area. Similar names, models, and partial tags are suggestions. Unknown location is allowed.

## Valuation readiness

The same inventory rows, photos, and review tasks feed a valuation module. Capture stays the screen after login. Open **שווי** or the link on an inventory row.

The card shows what is being valued, which facts already come from the file or from photos, and what is still missing. A current-value range appears only when at least two comparable observations share a price type, currency, and an asset-only price. Otherwise the card says **אין די ראיות להערכה**. Nothing in this release invents a price, a comparable, or a contact name.

Live web research is not available. The screen says so and opens a research task. Asking prices, completed transactions, hammer prices, buyer totals, replacement prices, and interested-buyer offers stay labeled and are not mixed into one range. A repeated listing is not a second observation. A price that bundles delivery or installation is not treated as an asset-only price. The spreadsheet column `price` is not a purchase cost and is not depreciated into a value.

The project date 31 December 2026 stays visible. It does not turn the normal-marketing estimate into a quick-sale price. A separate time-constrained scenario can be added, and it has no price unless evidence supports one. A purchase offer and a target price can be stored on the row, outside the valuation card, and are not inputs to the range.

A photograph does not mark operating condition, calibration, or relocation as verified.

| Addendum check | Where it is covered |
|---|---|
| 1. Reuse file facts and list real gaps | `tests/test_valuation.py`: generator `X-9013` keeps `1427 KW` from the sheet |
| 2. Different families, different checklists | Generator, lab balance `RDBAL-01`, and reactor `R-707` |
| 3. Unknown identity is a task, not a price | `V-0514` has no description and no range |
| 4. Every request has a reason, method, and role | Requirement fields on the card and in the visit list |
| 5. An answer updates readiness without a second inventory | Manufacturer answer stays on the valuation requirement |
| 6. Research can start early and add a requirement | Research task exists before every field is filled; a fuel question can be added |
| 7. Full information and no market evidence abstains | `A-707` after answers: market state insufficient, no range |
| 8. Price types stay distinct | Asking, buyer offer, and replacement are stored as different types |
| 9. Duplicate listings do not count twice | Same URL is marked as a copy |
| 10. Bundled service prices are not direct comps | Delivery/installation price is kept as context |
| 11. No invented percent or depreciation | A submitted percent is ignored; `price` is not a cost basis |
| 12. The live offer is outside the valuation | `777001` / `888002` are absent from the valuation payload |
| 13. A range has evidence, method, and assumptions | Two asset-only asking prices produce a low–high range |
| 14. A basis change keeps the prior version | Changing the premise marks the old version outdated |
| 15. Existing records are reused | Equipment row count is unchanged |
| 16. A photo is not a test | A condition photo stays “missing” |
