# Screening reference profiles

One JSON file per document type (and layout variant) describes what the form is expected
to look like. `scan_agent/screening/profile.py` loads every `*.json` here that has a
`doc_type`; several files with the same `doc_type` are layout variants of one type (e.g. a
report card's outside and inside pages): the classifier takes the best variant and the
checks run against it.

The v1 files were bootstrapped from the synthetic SAMPLE layouts
(`python -m scan_agent.screening.synthetic --write-profiles`), which only approximate the
real forms. They are data from now on: edit them by hand when the client's samples arrive,
and enroll the samples (below) so measured positions replace the guessed ones.

## Coordinates and units

- Zones are `[x, y, w, h]` fractions of the page width and height (top-left origin), so
  they do not depend on the capture resolution.
- Tolerances are millimetres of the printed page (`tolerance_mm`), or a fraction for text
  heights (`font_groups[].tolerance`, 0.15 = 15 %).
- The page is normalized to 300 DPI on its long side (A4: 2480 x 3508 px).

## Severity

Every indicator's severity is `deviation / (2 x tolerance)`, clipped to 0..1: **0.5 means
exactly at the tolerance written here**, 1.0 twice that or more. Indicators are reported from
0.25. The backend decides what to do with them (default: manual review from 0.5). Widening a
tolerance therefore makes the station less strict; it does not change any decision rule.

## Format (schema 1)

```jsonc
{
  "schema": 1,
  "doc_type": "psa_birth_certificate",        // IntakeDocType (screening-contract.md)
  "variant": "mf102",                          // free name, unique per doc_type
  "description": "...",
  "page_mm": [210, 297],                       // printed page size (portrait / landscape)
  "tolerance_mm": 3.0,                         // default position tolerance

  // Classification evidence: weighted phrases. With a zone, a phrase found elsewhere on
  // the page counts half. Score = weight found / total weight.
  "keywords": [{"text": "CERTIFICATE OF LIVE BIRTH", "weight": 4, "zone": [0.1, 0, 0.8, 0.2]}],

  // Printed headings that must be present, and where.
  "headings": [{"id": "title", "text": "CERTIFICATE OF LIVE BIRTH", "zone": [...],
                "tolerance_mm": 3.0, "missing_severity": 0.8}],

  // Fields: optional printed label (+ its zone) and the zone the value is printed in. When
  // the label is found, the value zone moves with it. Values are extracted as printed.
  "fields": [{"name": "sex", "label": "2. SEX", "label_zone": [...], "value_zone": [...],
              "label_anchor": "center",        // or "left" for left-aligned labels
              "missing_label_severity": 0.7,   // label not found
              "missing_value_severity": 0.3}], // label found, value blank / unreadable

  // Tables: header headings plus one field per expected row (label = row name, value =
  // the cell in value_x). Expanded at load time; rows get baseline groups, the labels a
  // left-edge group and the values a font group.
  "tables": [{"id": "grades", "label_x": [0.081, 0.429], "value_x": [0.714, 0.819],
              "first_row_y": 0.37, "row_h": 0.0269, "tolerance_mm": 4.0,
              "rows": [{"key": "mathematics", "label": "Mathematics"}],
              "header": [{"id": "final_rating", "text": "Final Rating", "zone": [...]}]}],

  // Members (<field> = its value, label:<field>, heading:<id>) that share a baseline or a
  // left edge (alignment_score, alignment_off) ...
  "alignment": [{"id": "child_name_row", "kind": "baseline", "tolerance_mm": 1.2,
                 "members": ["child_first_name", "child_middle_name", "child_last_name"]}],
  // ... or a text height (font_consistency, font_inconsistent).
  "font_groups": [{"id": "typed_values", "tolerance": 0.15, "members": ["sex", "..."]}],

  // Seals / logos matched as image templates. The file is looked up in the reference
  // store (<store>/<doc_type>/templates/), then in profiles/templates/. No file: the
  // seal is not measured (checks.seal_logo_score = null).
  "seals": [{"id": "psa_logo", "template": "psa_logo.png", "zone": [...],
             "min_score": 0.5, "search_margin_mm": 10}],

  // Other printed text that is normal (instructions, body paragraphs, footers). Text
  // outside every zone raises unexpected_text. Once 3+ samples are enrolled, the places
  // where the samples have text are used instead of text_zones.
  "text_zones": [[...]],
  "unexpected_min_chars": 8,                   // characters of stray text = tolerance

  // Printed tints (shaded bars) ignored by the paper-tone check (altered_region).
  "shaded_zones": [[...]],
  "tone_tolerance": 6.0                        // grey levels of local paper-tone change
}
```

## Calibration

`calibration.json` holds the confidence calibration (logistic fit of the classifier's raw
scores, see `classify.py`). The packaged values were fitted on synthetic pages; refit on the
client's labelled samples with `python -m scan_agent screening-calibrate --dir <labelled>
--refs <store>` (written to `<store>/calibration.json`, which takes precedence).

## Enrolling real samples

See scan-agent/README.md, "Screening incoming documents". Real samples contain minors'
personal data: never commit them, and keep the reference store outside the repository.
