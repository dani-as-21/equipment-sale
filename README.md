# ציוד למכירה

A Hebrew, right-to-left tool for one question at a time: what can be sold, which parts belong to the same machine, what is still missing, and what valuation evidence is already on file. There is no value range in this version.

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open the site and sign in with `local-visit`.

On first start the app loads one plant workbook, `data/sources/plant-equipment.xlsx`, and the laboratory list, `data/sources/lab-instruments.docx`. A second copy of the plant list is not loaded. English tags, model names, and source text stay as written.

The screens are לוח, מלאי, כרטיס נכס, שאלות, and העלאה.

Laboratory rows are individual assets. Plant rows are Process / Production equipment. Nothing is placed in API, because the source files do not say so. `FUTURE`, `HOLD`, `SPARE`, `NEW PROJECT`, `Not in Use`, and `Under maintenance` stay in the inventory with their original labels. Identical generic names such as Centrifuge and Dosing Pump stay separate and remain open questions.

`R-4208` and `F-4208` are the one user-confirmed machine, sold together. Those tags are not in the spreadsheet, so no source rows were created for them. A shared number alone does not create a machine. A prefix is only an equipment type. A proposed link needs an explanation and a status of מאושר, סביר, or לא ודאי. Uncertain links are questions. Confirming a link does not delete the source row and does not count the machine and its components as two assets.

An upload can be a file or pasted text. A likely match waits for confirmation. An uncertain file is not assigned. A confirmed link is not replaced by a later upload of the same file. Valuation evidence is stored on the asset card only, and only with the words and amounts that were entered.

```bash
python3 -m pytest tests -q
```
