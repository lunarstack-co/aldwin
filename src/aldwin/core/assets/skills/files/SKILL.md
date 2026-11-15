---
name: files
description: Find, read, edit, create, move and tidy files without losing data. Use before any search, read, edit, write or reorganisation of files.
summary: Finds, reads, edits and tidies files on your computer without losing anything.
---

# Files

## Finding

- `list_dir` for structure, `glob` for names, `grep` for content. Never guess a path.
- Never run `cat`, `ls`, `find`, `grep`, `head`, `tail` or `sed` through `run_command`. Use the file tool.

## Reading

- `read_file` with `offset` and `limit` for long files.
- Read the whole file only when it is short or you are about to edit it.

## Editing

1. `read_file` first, every time, in the same turn.
2. `edit_file` with the smallest string that is unique in the file.
3. Preserve indentation, line endings and trailing whitespace exactly.
4. `write_file` only for new files, or a file you have fully read and are replacing on purpose. Say which before calling it.

## Moving and deleting

- Nothing is deleted. Move unwanted files to `_to_delete/` at the workspace root and tell the user.
- Move with `run_command` and `mv -n` (refuses to overwrite). Never plain `mv`.
- After moving, `list_dir` to confirm each file landed.

## Tidying a folder

1. `list_dir`.
2. Propose a table: file → destination → one-line reason.
3. Wait for an explicit yes.
4. `mv -n`, one file per command.
5. `list_dir` and report anything that did not move.

## On refusal

A declined or refused call means stop. Say what you could not do and ask. Do not retry with another tool or the shell.

## Example

User: "Tidy my Downloads folder."
`list_dir` → table of moves → "Go ahead?" → yes → `mv -n` each → `list_dir` → "Moved 12, skipped 1 (name clash)."
