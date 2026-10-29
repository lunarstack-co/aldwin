# Skills

A skill is a short markdown file that teaches Aldwin how to do a kind of job well, such as researching on the web or writing release notes. Aldwin picks a skill on its own when one fits, or you can pick one yourself with `@`.

## Default skills

Aldwin ships nine default skills, loaded from the install:

`using-aldwin`, `files`, `markdown-notes`, `documents`, `making-documents`, `web-research`, `memory`, `terminal`, `skill-creator`

The `making-documents` skill writes Office files via `run_command`. Enable it with the document-writing extra:

```bash
uv tool install "aldwin[docs]"
# or
pip install "aldwin[docs]"
```

## Using skills

- Type `@` in chat to pick a skill for the current message (up to three, most recent first, `Tab` to insert). Picked skills apply to that message only.
- List skills with `/skills` in chat or `aldwin skills` in the terminal.
- Show one skill's body with `/skills show <name>`.

## Writing your own

Drop a folder with a `SKILL.md` file into `~/.aldwin/skills/`. The file needs YAML frontmatter with a `name` and a `description`:

```markdown
---
name: release-notes
description: Draft concise release notes from git history.
---
Write release notes grouped into Added, Fixed, and Known limitations.
```

Save it as `~/.aldwin/skills/release-notes/SKILL.md`.

You can also ask Aldwin to draft a skill for you. It uses the `create_skill` tool, which shows the file as a diff and always asks before saving.

Rules:

- A user skill with the same name as a default skill replaces it.
- Skills can't declare `tools:`. If the key is present, Aldwin strips it.
- After a turn that writes a skill, Aldwin reloads the catalogue before the next turn.
