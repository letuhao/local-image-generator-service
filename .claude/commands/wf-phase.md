---
description: Move to the next workflow phase and show current state.
---

# /wf-phase

Enter a workflow phase without retyping long commands.

## Arguments

`/wf-phase <phase_name>`

Valid phases:
`clarify`, `design`, `review-design`, `plan`, `build`, `verify`, `review-code`, `qc`, `post-review`, `session`, `commit`, `retro`

## Steps

1. Run:
   - `bash scripts/workflow-gate.sh phase <phase_name>`
2. Print:
   - `bash scripts/workflow-gate.sh status`
3. If blocked by gate rules, stop and report the exact blocker.
