---
description: Start a new workflow task in one command (reset + size + first phase).
---

# /wf-start

Initialize workflow state for a new task with one command.

## Arguments

`/wf-start <SIZE> <files> <logic> <side_effects> [first_phase]`

- `SIZE`: `XS|S|M|L|XL`
- `first_phase`: optional, defaults to `clarify`

Example:
- `/wf-start M 4 5 1 clarify`

## Steps

1. Run:
   - `bash scripts/workflow-gate.sh reset`
   - `bash scripts/workflow-gate.sh size <SIZE> <files> <logic> <side_effects>`
   - `bash scripts/workflow-gate.sh phase <first_phase_or_clarify>`
2. Print the resulting `bash scripts/workflow-gate.sh status`.
3. Continue work in that phase.
