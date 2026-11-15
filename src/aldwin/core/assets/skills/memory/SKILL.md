---
name: memory
description: Save, update, recall and forget lasting facts about the user. Use when they share a durable fact or preference, ask what you know about them, or ask you to forget something.
summary: Remembers facts and preferences about you, and forgets them when you ask.
---

# Memory

## Save

- Save what will still be true next month: preferences, names, routines, standing instructions. Not today's task.
- One fact per note, short, in the user's own words.
- Save only what the user stated. Your inferences are not their facts.

## Update

- A changed fact: `remember` with `replace` on the old note. Never add a second, contradicting note.

## Recall

- Call `recall` before saying "I don't know".
- "What did we say about…" uses conversation scope.
- Use what you find without narrating the lookup.

## Forget

- Show the exact note. Wait for confirmation. Then `forget`. It is permanent.

## Never store

Passwords, API keys, card or account numbers, health details, anything the user marks private. Say in one sentence that you can't save that kind of detail.

## Honesty

- Memory lives in `~/.aldwin/memory/` as plain files the user can open.
- Saved notes are sent to the model with each message.
- If a save fails, say so.

## Example

User: "I prefer British spelling."
`remember("Prefers British spelling")`. Later: "Actually, American." → `remember` with `replace` → "Updated."
