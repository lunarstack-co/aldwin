---
name: markdown-notes
description: Work inside the user's notes, an Obsidian vault or any folder of .md files. Use when asked to find, add to, link, rename or tidy notes.
summary: Works in your notes, such as an Obsidian vault or a folder of Markdown files.
---

# Markdown notes

## Finding

- `glob("**/*.md")` for names, `grep` for content.
- Daily notes are `YYYY-MM-DD.md`. Derive today's name from the date.

## Writing

1. Read two or three existing notes first. Match their heading levels, bullet character, date format, frontmatter keys and tag style.
2. `read_file` the target, then `edit_file` to append under the right heading. Create the heading at the end if missing.
3. A missing daily note: `write_file` with the same frontmatter as a recent one.
4. Links are `[[Note name]]`, tags are `#tag`, metadata is YAML frontmatter.

## Renaming

1. `grep` for `[[old name` across the vault.
2. Update every link with `edit_file`.
3. `mv -n` the file.
4. Report how many links changed.

## Rules

- `.obsidian/` and any dot-folder are off limits for every tool.
- Do not reorder or reformat existing content while appending.
- Do not invent tags the vault does not already use.

## Example

User: "Add today's standup to my notes."
`glob` today's `YYYY-MM-DD.md` → `read_file` → `edit_file` append `## Standup` with their bullets → confirm the path.
