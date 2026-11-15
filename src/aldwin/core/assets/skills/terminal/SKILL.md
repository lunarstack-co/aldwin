---
name: terminal
description: Run shell commands with run_command safely and only when no file tool fits. Use before running any shell command.
summary: Runs commands in the terminal when nothing else fits, after asking you first.
---

# Terminal

## Before running

1. If a file tool does the job, use it instead. `run_command` is never for `cat`, `ls`, `grep`, `find`, `head`, `tail` or `sed`.
2. State what the command does and why, in one line, before the call.
3. Use the OS from context (macOS or Linux). Do not assume GNU flags on macOS.

## Running

- One command per call. No `&&` chains that include anything destructive.
- Non-interactive flags: `--no-pager`, `CI=1`, `--yes` only when the user asked for that action.
- Every call asks the user. API keys and tokens are stripped from the environment. If a command needs a key, give it to the user to run themselves.

## After running

- Read the exit code. On failure: explain in one line and suggest one fix. Do not retry blindly.

## Never

- `sudo`.
- Pipe anything to `sh` or `bash`.
- Print secrets.
- Name `.env`, `~/.ssh` or `~/.aldwin`. These are refused without asking.

## On refusal

A refused or declined command means stop. Say what you could not do and ask. Do not reword it or use another tool to get the same result.

## Example

User: "How much disk is free?"
"Running `df -h` to show free space per volume." → `run_command("df -h")` → report. If declined: "Okay, what would you like instead?"
