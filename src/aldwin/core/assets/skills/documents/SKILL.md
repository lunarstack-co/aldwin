---
name: documents
description: Read and summarise PDF, Word, PowerPoint, Excel, CSV and HTML files. Use before opening any non-plain-text file or when the user asks what a document says.
summary: Reads PDFs, Word, PowerPoint, Excel and other documents, and tells you what they say.
---

# Documents

## Reading

1. Open with `read_document`. For PDFs over 20 pages, pass a page range: read the first pages and the contents page first, then the pages the question needs.
2. For spreadsheets, pass the sheet name. Read the header row, then only the rows needed.
3. Attach a location to every fact you repeat: page, slide, or sheet and row.
4. If a page returns no text, it is scanned. Say so and ask for a text copy. Do not guess the content.

## Calculating

- A few numbers: use `calculate`.
- Anything larger: write a short pandas script with `write_file`, run it with `run_command`, show the command first.

## Summarising

1. One-sentence answer.
2. Three to seven key points, each with a location.
3. Questions the document does not settle.
4. Offer once to save the summary as `.md` next to the source. Only write it on a yes.

## Rules

- Never repeat a number you have not seen on the page.
- Never call a document "complete" when you read only part of it.

## Example

User: "Summarise Q3.xlsx sheet 2."
`read_document(path, sheet="Sheet2")` → header row → relevant rows → answer first, cited by row → offer `Q3-summary.md` beside the source.
