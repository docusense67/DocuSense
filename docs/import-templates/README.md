# Import templates

Both imports accept `.csv` (UTF-8) or `.xlsx`, are all-or-nothing, and return a per-row error report (row 1 = header).

| File | Endpoint | Required columns | Notes |
| --- | --- | --- | --- |
| `students.csv` | `POST /api/students/import` (`?dry_run=true` to validate only) | student_number, first_name, last_name, program_code | Dates `YYYY-MM-DD`; year_level 1–6; status active/graduated/inactive. Creates profiles only — logins are added per student. |
| `grades.csv` | `POST /api/sections/{id}/grades/import` | student_number, grade | Grade on the configured scale (1.00–3.00, 5.00) or `INC` / `DRP`. Term must be unlocked. Registrar imports need `?reason=`. |
