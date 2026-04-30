---
description: Complete a workflow phase with evidence, then show status.
---

# /wf-complete

Mark a phase done and record evidence in workflow state.

## Arguments

`/wf-complete <phase_name> <evidence_text>`

Example:
- `/wf-complete verify "uv run pytest -q tests/test_async_mode.py => 12 passed"`

## Steps

1. Run:
   - `bash scripts/workflow-gate.sh complete <phase_name> "<evidence_text>"`
2. Print:
   - `bash scripts/workflow-gate.sh status`
3. If the phase is `post-review`, ensure evidence includes user approval context.
