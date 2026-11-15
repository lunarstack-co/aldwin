---
name: skill-creator
description: Write a skill and save it with create_skill. Use when the user asks Aldwin to learn a routine, make or change a skill, or keeps repeating the same multi-step request.
summary: Teaches Aldwin a routine you repeat, so you can run it again with @.
---

# Skill creator

## Steps

1. Ask at most two questions, together: what triggers it, and what the finished output looks like. Skip them if the conversation already answers both.
2. Draft the skill (format below).
3. Show the draft. On a yes, call `create_skill`.
4. Tell the user: type `@name` to use it; deleting the skill folder restores any default it replaced.

## Format

- **name**: lowercase words joined by hyphens, specific to the task (`weekly-report`, not `report`).
- **description**: one line, under 200 characters, saying what it does and when to use it. This line decides whether the skill loads, so name the trigger.
- **body**: numbered steps, one action each, naming the tool to use. One short example. One "Rules" section for the most likely mistake.

## Rules

- No secrets in a skill.
- No `tools:` field.
- Keep it under 50 lines. If it needs more, split it.

## Example

User: "Make a skill for my weekly report."
Ask trigger and output → draft → show → `create_skill` → "Type `@weekly-report` to use it."
