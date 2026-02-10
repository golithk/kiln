---
name: kiln-process-issue-phase
description: Process a GitHub issue through a specific Kiln workflow phase (research, plan, implement, feedback). Use when the user wants to progress an issue through the RPI workflow.
---

# Process GitHub Issue Phase

Route a GitHub issue to the appropriate Kiln command based on phase, matching daemon behavior.

## When to Use

- "process issue X in research/plan/implement/feedback phase"
- "run research/plan/implement on issue X"
- "apply feedback to issue X" (feedback phase)

## Parameters

- **issue_url**: GitHub issue URL (e.g., `https://github.com/owner/repo/issues/123`)
- **phase**: `research`, `plan`, `implement`, or `feedback`
- **comment_body**: (for feedback phase only) The user comment/feedback to apply
- **target_type**: (for feedback phase only) `description`, `research`, or `plan`

## Instructions

### Step 1: Parse URL and Check State

```bash
# Extract: hostname, owner, repo, issue_number from URL
# Then check labels:
gh issue view <issue_url> --json labels --jq '[.labels[].name]'
```

| Phase | Complete If | Dependency |
|-------|-------------|------------|
| research | `research_ready` label | None |
| plan | `plan_ready` label | `research_ready` |
| implement | Open PR exists | `plan_ready` |
| feedback | N/A (always runnable) | Issue has content to edit |

If phase already complete, ask user before re-running.

### Step 2: Route to Command

| Phase | Command |
|-------|---------|
| research | `/kiln-research_codebase_github <hostname>/<owner>/<repo> <issue_number>` |
| plan | `/kiln-create_plan_github <hostname>/<owner>/<repo> <issue_number>` |
| implement | See implement flow below |
| feedback | See feedback flow below |

**Implement phase flow** (must be in target repo context):

**IMPORTANT: Execute ALL steps in order. Do NOT skip any step.**

1. Clone/navigate to the target repository
2. Create and checkout a feature branch: `git checkout -b <issue_number>-<slug> main`
3. **REQUIRED**: Run `/kiln-prepare_implementation_github <issue_url>`
   - Creates a draft PR with TASK checkboxes from the plan
4. Run `/kiln-implement_github <hostname>/<owner>/<repo> <issue_number>`
   - Implements tasks based on PR description
   - Updates PR checkboxes as tasks complete
5. Update issue checkboxes to match PR (sync completed tasks)

**Feedback phase flow** (apply user comment to issue content):
1. Read the target content: `gh issue view <issue_url> --json body --jq '.body'`
2. Apply the user's feedback to edit the target section (`description`, `research`, or `plan`) in-place
3. Update the issue: `gh issue edit <issue_url> --body "..."`
4. Preserve overall structure; only modify sections relevant to the feedback
5. Do NOT create new comments - edit existing content directly

### Step 3: Report Result

Report phase completed and next step.

## Example

**Input**: "Run plan on https://github.com/org/repo/issues/5"

**Agent**:
1. Parse → `github.com/org/repo`, issue `5`
2. Check labels → has `research_ready` ✓, no `plan_ready`
3. Invoke: `/kiln-create_plan_github github.com/org/repo 5`
4. Report: "Plan complete. Ready for implement phase."
