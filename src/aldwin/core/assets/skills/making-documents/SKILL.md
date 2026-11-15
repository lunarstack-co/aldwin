---
name: making-documents
description: Create .docx, .xlsx and .pdf files with a short Python script. Use when the user asks for a Word document, spreadsheet or PDF to be produced.
summary: Makes Word documents, spreadsheets and PDFs for you.
---

# Making documents

For a plain note, write markdown instead of loading this skill.

## Steps

1. Check the library with `run_command`, one check per command:
   - Word: `python -c "import docx"`
   - Excel: `python -c "import openpyxl"`
   - PDF: `python -c "import reportlab"`
2. If the import fails, stop and offer two options. Never install yourself.
   - They run `pip install 'aldwin[docs]'` or `uv tool install 'aldwin[docs]'`.
   - A markdown (docs) or CSV (sheets) fallback now.
3. Draft the content in the conversation and confirm before building.
4. `write_file` a small script in the workspace (e.g. `make_report.py`). Run it once with `run_command`. Keep the script.
5. Save output next to the source material with a clear name. State the full path.
6. Verify `.docx` and `.xlsx` output with `read_document`.

## Rules

- Never overwrite an existing document with the same name.
- Never delete the script afterwards.

## Example

User: "Turn this summary into a Word file."
`python -c "import docx"` → ok → confirm headings → `write_file make_summary.py` → `run_command` → `read_document` to check → "Saved at ~/Documents/summary.docx".
