# Fix CI Failures

You are running in **headless, non-interactive mode** as part of an automated CI validation workflow.

**CRITICAL**: This is an automated pipeline, so:
- Do NOT ask clarifying questions
- Do NOT wait for user approval

## Context

All implementation tasks are complete. The PR is in draft state awaiting CI validation. CI checks have failed and you must fix the issues before the PR can be marked ready for review.

This prompt is **modular and editable** - operators can customize the guidelines below to control agent behavior during the validation phase.

## CI Failure Output

The following CI check(s) failed:

{ci_output}

## Your Task

1. Analyze the CI failure output above
2. Determine the root cause of each failure
3. Apply fixes that address the actual issues
4. Commit your fixes with clear, descriptive messages
5. Output a summary of what was fixed

---

## Fix Guidelines

<!-- OPERATOR CUSTOMIZATION SECTION - Edit these guidelines to control fix behavior -->

### Priority Order
1. **Fix implementation code first** - The implementation should match the intended behavior
2. **Update tests only when appropriate** - Tests should verify correct behavior, not just pass

### When to Fix Implementation
- The implementation has a bug that doesn't match the intended behavior
- Type errors, syntax errors, or import issues in implementation code
- Logic errors that cause test assertions to fail
- Missing or incorrect function/method implementations

### When to Modify Tests
- The test logic is incorrect for the **new** implementation
- Tests are verifying **old** behavior that has intentionally changed
- Test setup or fixtures don't account for new requirements
- Assertions are checking wrong values based on new logic

### NEVER Do This
- Modify tests just to make them pass without understanding why they fail
- Delete or skip tests to avoid failures
- Weaken assertions (e.g., changing exact match to contains, removing checks)
- Ignore type errors or linting issues
- Make unrelated changes to "clean up" code

### Linting and Formatting
- For `ruff check` failures: Fix the actual issue, don't just add ignores
- For `ruff format` failures: Run `ruff format` on affected files
- For `mypy` failures: Add proper type annotations or fix type mismatches

<!-- END OPERATOR CUSTOMIZATION SECTION -->

---

## Output Requirements

After applying fixes, output a summary in this format:

```
## Fixed CI Issues

### Failures Addressed
- <check_name>: <brief description of what failed>

### Changes Made
- <file_path>: <what was changed and why>

### Reasoning
<1-2 sentences explaining the root cause and fix approach>
```

Then commit the changes with a message following this pattern:
```
fix: <brief description of what was fixed>

Addresses CI failures:
- <check_name>: <what failed>

Changes:
- <file>: <change description>
```

---

## Verification

After committing, run the relevant checks locally if possible:
- `ruff check src/ tests/` for linting
- `ruff format --check src/ tests/` for formatting
- `pytest -x` for tests (stop on first failure)
- `mypy` for type checking

If local verification passes, push the changes. If not, continue fixing.

ARGUMENTS: $ARGUMENTS
