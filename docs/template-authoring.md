# Template authoring guide

How to turn a school document (TOR, COE, or a future type) into a system template. This guide is for developers converting official formats; registrars upload, preview and activate the result in the web app.

## Workflow

1. Start from the current template: `GET /api/templates?document_type=TOR`, or `backend/app/documents/defaults/`.
2. Edit the HTML/CSS to match the school's official layout. Placeholders must come from the catalog below.
3. Upload it as a new draft: `POST /api/templates` with `document_type`, `html_content` and `field_zones`. The response lists anything that blocks activation.
4. Preview it: `POST /api/templates/{id}/preview` (sample data) or `?student_id=…` (a real student's data). Nothing is issued.
5. Adjust and re-preview: `PATCH /api/templates/{id}` while the version is still a draft.
6. Activate: `POST /api/templates/{id}/activate`. The previous version is retired for **new** documents only. Documents already issued keep the version and OCR zones they were printed with.

`GET /api/document-types` returns the required placeholders and zones for each type.

## Placeholders

Templates use Jinja2 syntax in a sandbox: `{{ student_name }}`, `{% for term in terms %}…{% endfor %}`. Values are HTML-escaped. A name that isn't in the catalog blocks activation.

| Placeholder | Example | Notes |
| --- | --- | --- |
| `student_name` | JUAN D. DELA CRUZ JR. | uppercase, middle initial |
| `student_number` | 2021-00123 | |
| `program` | Bachelor of Science in Information Technology | |
| `year_level` | 3rd Year | COE |
| `semester` / `academic_year` | 1st Semester / 2025-2026 | COE: the current term |
| `date_issued` | October 5, 2026 | |
| `document_number` | TOR-2026-000123 | required on every type |
| `qr_code` | data URI | use as `<img src="{{ qr_code }}">`; required on every type |
| `gwa` | 1.52 | TOR: cumulative |
| `purpose` | Employment | COE |
| `terms` | list | TOR: each term has `semester`, `academic_year`, `gwa`, `subjects` (`subject_code`, `subject_title`, `units`, `grade`, `remarks`) |
| `data_hash_short` | 6d95476a59d4 | optional; the footer already prints it |
| `institution_name`, `institution_address`, `registrar_name`, `registrar_title` | | from server settings |

## Rules the system enforces

- **Page and footer are fixed.** A4, margins 18/20/22/20 mm, and a footer on every page reading `TOR-2026-000123 | Page 1 of 2 | Ref 6d95476a59d4`. Templates cannot override this. Bulk scanning uses the footer to split stacks and to detect missing pages.
- **No remote content.** `<script>`, iframes, `@import` and external `http(s)://` or `file:` URLs are rejected at upload, and the renderer only loads `data:` URIs. Embed the logo as a data URI.
- **No blank required fields.** If a student is missing required data, generation stops and lists what's missing.

## Designing for OCR (Section 27)

- Sans-serif font (DejaVu Sans is installed), at least 10 pt, black on white.
- Put each scanned field at a **fixed position**: use `position: absolute` in mm inside a fixed-height header block, as the defaults do.
- Put the grades in a bordered table. The table may flow across pages; its header row repeats automatically.
- Keep stamps, signatures and seals away from OCR zones.
- Don't use handwriting fields.

## OCR field zones

`field_zones` gives page coordinates in millimetres from the top-left corner of the physical A4 sheet:

```json
{
  "unit": "mm",
  "fields": {
    "qr_code":        {"x": 164, "y": 18, "w": 26, "h": 26},
    "student_name":   {"x": 56,  "y": 48, "w": 132, "h": 5.5},
    "grades_table":      {"x": 20, "y": 84, "w": 170, "h": 191, "pages": "first"},
    "grades_table_cont": {"x": 20, "y": 18, "w": 170, "h": 257, "pages": "rest"}
  }
}
```

- A zone's name is the snapshot key it reads (`student_name` is compared with `snapshot["student_name"]`). The exceptions are `qr_code` and the grade-table regions.
- With the default margins, an element at `left: Xmm; top: Ymm` inside the header block sits at page `x = 20 + X`, `y = 18 + Y`.
- `pages` is `first` (default), `rest`, or `all`.
- Every type has required zones (see `/api/document-types`). Activation is blocked until they're all defined.
- Check zone positions against a real 300 DPI scan during OCR tuning (Epic 5).

## Adding a new document type (e.g. COG)

1. Add the value to `DocumentType` in `backend/app/models/enums.py`.
2. Write a context builder in `backend/app/documents/context.py`. It must return only printed strings and raise `MissingDataError`.
3. Add a `DocumentTypeSpec` entry in `backend/app/documents/registry.py` with its required placeholders and zones, sample data and default template.
4. Add the default HTML and zones under `backend/app/documents/defaults/`.
5. Add tests for the builder, then preview the template.

Extra document types are outside the MVP scope (Section 19). Agree each one as a change request first.
