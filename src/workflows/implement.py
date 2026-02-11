"""Implementation workflow for executing the implementation plan."""

import json
import re
import subprocess
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

from tenacity import wait_exponential

from src.claude_runner import run_claude
from src.config import STAGE_MODELS
from src.integrations.pr_validation import PRValidationManager
from src.integrations.slack import (
    send_implementation_beginning_notification,
    send_ready_for_validation_notification,
)
from src.interfaces import CheckRunResult
from src.logger import get_logger, log_message
from src.ticket_clients.base import NetworkError
from src.ticket_clients.github import GitHubTicketClient
from src.workflows.base import WorkflowContext

if TYPE_CHECKING:
    from src.config import Config

logger = get_logger(__name__)


class ImplementationIncompleteError(Exception):
    """Raised when implementation exits without completing all tasks.

    This exception is used to signal that the implementation workflow exited
    without completing all tasks due to stall detection, max iterations reached,
    or no checkbox tasks found. The daemon catches this to apply the
    'implementation_failed' label.
    """

    def __init__(self, reason: str, message: str):
        """Initialize the exception.

        Args:
            reason: Short reason code (stall, max_iterations, no_tasks)
            message: Human-readable description of what happened
        """
        self.reason = reason
        super().__init__(message)


# Constants for the implementation loop
DEFAULT_MAX_ITERATIONS = 8  # Fallback if no TASKs detected
MAX_STALL_COUNT = 2  # Stop after 2 iterations with no progress

# Network error patterns to detect transient failures
# These are checked case-insensitively against stderr output
NETWORK_ERROR_PATTERNS = [
    "tls handshake timeout",
    "connection timeout",
    "network error",
    "connection refused",
    "temporary failure",
    "i/o timeout",
    "dial tcp",
    "no such host",
    "error connecting to",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
    "(500)",
    "(502)",
    "(503)",
    "(504)",
]

T = TypeVar("T")


class _BackoffState:
    """Minimal state object for tenacity's wait_exponential.

    Tenacity's wait functions expect a RetryCallState with an attempt_number.
    This provides a lightweight alternative to avoid importing the full class.
    """

    def __init__(self, attempt_number: int):
        self.attempt_number = attempt_number


def _retry_with_backoff(
    func: Callable[[], T],
    max_attempts: int = 3,
    initial_delay: float = 70.0,
    max_delay: float = 120.0,
    description: str = "operation",
) -> T:
    """Retry a function with exponential backoff on NetworkError.

    Args:
        func: Zero-argument callable to retry
        max_attempts: Maximum number of attempts (default 3)
        initial_delay: Starting delay between retries (default 70s for GitHub ALB TTL)
        max_delay: Maximum delay between retries (default 120s)
        description: Description for log messages

    Returns:
        The function result

    Raises:
        NetworkError: After exhausting retry attempts
    """
    backoff = wait_exponential(multiplier=1, min=initial_delay, max=max_delay)

    for attempt in range(1, max_attempts + 1):
        try:
            return func()
        except NetworkError as e:
            if attempt >= max_attempts:
                raise NetworkError(f"{description} failed after {attempt} attempts: {e}") from e

            delay = backoff(_BackoffState(attempt))  # type: ignore[arg-type]

            logger.warning(
                f"{description} failed (attempt {attempt}/{max_attempts}): {e}. "
                f"Retrying in {delay:.0f}s..."
            )
            time.sleep(delay)

    # This should never be reached, but satisfies type checker
    raise RuntimeError("Unexpected: retry loop exhausted without raising")


def count_tasks(markdown_text: str) -> int:
    """Count the number of TASK blocks in markdown text.

    Looks for patterns like:
    - ## TASK 1: Description
    - ### TASK 2: Description
    - **TASK 3**: Description

    Args:
        markdown_text: Markdown content to parse

    Returns:
        Number of TASK blocks found
    """
    # Match TASK headers in various formats
    task_pattern = re.compile(r"^#+\s*TASK\s+\d+|^\*\*TASK\s+\d+\*\*", re.MULTILINE | re.IGNORECASE)
    return len(task_pattern.findall(markdown_text))


def count_checkboxes(markdown_text: str) -> tuple[int, int]:
    """Count total and completed checkboxes in markdown text.

    Args:
        markdown_text: Markdown content to parse

    Returns:
        Tuple of (total_tasks, completed_tasks)
    """
    checked = len(re.findall(r"- \[x\]", markdown_text, re.IGNORECASE))
    unchecked = len(re.findall(r"- \[ \]", markdown_text))
    return checked + unchecked, checked


# Plan extraction markers (matching comment_processor.py)
PLAN_START_MARKER = "<!-- kiln:plan -->"
PLAN_END_MARKER = "<!-- /kiln:plan -->"
PLAN_LEGACY_END_MARKER = "<!-- /kiln -->"


def extract_plan_from_body(body: str) -> str | None:
    """Extract plan content from issue body between markers.

    Looks for content between <!-- kiln:plan --> and <!-- /kiln:plan --> markers.
    Falls back to legacy <!-- /kiln --> end marker if new marker not found.

    Args:
        body: Issue body content to parse

    Returns:
        Plan content (without markers) or None if not found.
    """
    start_idx = body.find(PLAN_START_MARKER)
    if start_idx == -1:
        return None

    content_start = start_idx + len(PLAN_START_MARKER)

    # Try new end marker first, then legacy
    end_idx = body.find(PLAN_END_MARKER, content_start)
    if end_idx == -1:
        end_idx = body.find(PLAN_LEGACY_END_MARKER, content_start)

    if end_idx == -1:
        # No end marker, take everything after start
        return body[content_start:].strip()

    return body[content_start:end_idx].strip()


def extract_plan_from_issue(repo: str, issue_number: int) -> tuple[str | None, str]:
    """Fetch issue and extract plan content.

    Uses gh CLI to fetch the issue body and title, then extracts plan content.

    Args:
        repo: Repository in 'hostname/owner/repo' format (e.g., 'github.com/owner/repo')
        issue_number: Issue number

    Returns:
        Tuple of (plan_content, issue_title) where plan_content is None if not found.

    Raises:
        RuntimeError: If issue cannot be fetched
    """
    issue_url = f"https://{repo}/issues/{issue_number}"

    try:
        result = subprocess.run(
            ["gh", "issue", "view", issue_url, "--json", "body,title"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(result.stdout)
        body = data.get("body", "") or ""
        title = data.get("title", "") or ""

        plan_content = extract_plan_from_body(body)
        return plan_content, title

    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to fetch issue {issue_url}: {e.stderr}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse issue response for {issue_url}: {e}") from e


def collapse_plan_in_issue(repo: str, issue_number: int) -> None:
    """Collapse the plan section in the issue description.

    Wraps the plan in <details> tags to reduce visual clutter after PR creation.
    This is idempotent - if the plan is already collapsed, it does nothing.

    Args:
        repo: Repository in 'hostname/owner/repo' format (e.g., 'github.com/owner/repo')
        issue_number: Issue number

    Raises:
        RuntimeError: If issue cannot be fetched or updated
    """
    issue_url = f"https://{repo}/issues/{issue_number}"

    # Fetch current body
    try:
        result = subprocess.run(
            ["gh", "issue", "view", issue_url, "--json", "body"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(result.stdout)
        body = data.get("body", "") or ""
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to fetch issue {issue_url}: {e.stderr}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse issue response for {issue_url}: {e}") from e

    # Check if plan section exists
    start_idx = body.find(PLAN_START_MARKER)
    if start_idx == -1:
        # No plan to collapse
        return

    # Check if already collapsed (details tag before the plan marker)
    # Look for <details> tag that precedes and wraps the plan
    details_before_plan = body.rfind("<details>", 0, start_idx)
    if details_before_plan != -1:
        # Check if there's a </details> after the plan - if so, it's already collapsed
        plan_end_idx = body.find(PLAN_END_MARKER, start_idx)
        if plan_end_idx == -1:
            plan_end_idx = body.find(PLAN_LEGACY_END_MARKER, start_idx)
        if plan_end_idx != -1:
            closing_details = body.find("</details>", plan_end_idx)
            if closing_details != -1:
                # Already collapsed, skip
                return

    # Find the end marker
    end_idx = body.find(PLAN_END_MARKER, start_idx)
    end_marker_len = len(PLAN_END_MARKER)
    if end_idx == -1:
        end_idx = body.find(PLAN_LEGACY_END_MARKER, start_idx)
        end_marker_len = len(PLAN_LEGACY_END_MARKER)
    if end_idx == -1:
        # No end marker found, can't safely collapse
        return

    # Calculate the end position (after the end marker)
    plan_section_end = end_idx + end_marker_len

    # Find the separator (---) before the plan section
    sep_idx = body.rfind("---", 0, start_idx)

    # Extract the plan section (including markers)
    plan_section = body[start_idx:plan_section_end]

    # Build collapsed version
    collapsed = (
        "<details>\n"
        "<summary><h2>Implementation Plan</h2></summary>\n\n"
        f"{plan_section}\n\n"
        "</details>"
    )

    # Build new body
    if sep_idx != -1:
        # Include the separator in the collapsed section
        new_body = body[:sep_idx] + "\n---\n\n" + collapsed + body[plan_section_end:]
    else:
        new_body = body[:start_idx] + collapsed + body[plan_section_end:]

    # Update issue
    # Parse repo to get proper format for gh CLI
    # repo is in format "hostname/owner/repo" (e.g., "github.com/owner/repo")
    parts = repo.split("/", 1)
    if len(parts) == 2:
        hostname, owner_repo = parts
    else:
        hostname, owner_repo = "github.com", repo

    # For github.com, use just owner/repo; for enterprise, use full path
    repo_ref = owner_repo if hostname == "github.com" else f"{hostname}/{owner_repo}"

    try:
        subprocess.run(
            ["gh", "issue", "edit", str(issue_number), "--repo", repo_ref, "--body", new_body],
            capture_output=True,
            text=True,
            check=True,
        )
        logger.info(f"Collapsed plan section in issue #{issue_number}")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to update issue #{issue_number}: {e.stderr}") from e


def create_draft_pr(
    workspace_path: str,
    repo: str,
    issue_number: int,
    title: str,
    plan_body: str,
    base_branch: str | None = None,
) -> int:
    """Create a draft PR with the given plan content.

    Creates an empty commit, pushes to remote, and creates a draft PR linking to the issue.
    Uses retry logic for network errors on the gh pr create command.

    Args:
        workspace_path: Path to git worktree
        repo: Repository in 'hostname/owner/repo' format (e.g., 'github.com/owner/repo')
        issue_number: Issue number this PR closes
        title: PR title (from issue title)
        plan_body: Plan content to use as PR body
        base_branch: Optional base branch for the PR (for child issues)

    Returns:
        PR number

    Raises:
        RuntimeError: If PR creation fails after retries
    """
    # Build PR body with Closes keyword
    if base_branch:
        pr_body = (
            f"Closes #{issue_number}\n\n"
            f"> **Note**: This PR targets the branch `{base_branch}`, not the default branch.\n\n"
            f"{plan_body}\n\n---\n\n"
            "*This PR uses iterative implementation. Tasks are completed one at a time.*"
        )
    else:
        pr_body = (
            f"Closes #{issue_number}\n\n"
            f"{plan_body}\n\n---\n\n"
            "*This PR uses iterative implementation. Tasks are completed one at a time.*"
        )

    # 1. Create empty commit
    try:
        subprocess.run(
            [
                "git",
                "commit",
                "--allow-empty",
                "-m",
                f"feat: begin implementation for #{issue_number}",
            ],
            cwd=workspace_path,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to create empty commit: {e.stderr}") from e

    # 2. Push to remote
    try:
        subprocess.run(
            ["git", "push", "-u", "origin", "HEAD"],
            cwd=workspace_path,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to push to remote: {e.stderr}") from e

    # 3. Create draft PR with retry logic for network errors
    repo_ref = f"https://{repo}"
    cmd = [
        "gh",
        "pr",
        "create",
        "--draft",
        "--repo",
        repo_ref,
        "--title",
        f"feat: {title}",
        "--body",
        pr_body,
    ]
    if base_branch:
        cmd.extend(["--base", base_branch])

    def create_pr() -> str:
        try:
            result = subprocess.run(
                cmd, cwd=workspace_path, capture_output=True, text=True, check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            error_output = (e.stderr or "").lower()
            if any(pattern in error_output for pattern in NETWORK_ERROR_PATTERNS):
                raise NetworkError(f"Network error creating PR: {e.stderr}") from e
            raise RuntimeError(f"Failed to create draft PR: {e.stderr}") from e

    try:
        pr_url = _retry_with_backoff(
            create_pr,
            max_attempts=3,
            description=f"Create draft PR for issue #{issue_number}",
        )
    except NetworkError as e:
        raise RuntimeError(
            f"Failed to create draft PR for issue #{issue_number} after retries: {e}"
        ) from e

    # Parse PR number from URL output (e.g., "https://github.com/owner/repo/pull/123")
    try:
        pr_number = int(pr_url.rstrip("/").split("/")[-1])
    except (ValueError, IndexError) as e:
        raise RuntimeError(f"Failed to parse PR number from URL: {pr_url}") from e

    return pr_number


class ImplementWorkflow:
    """Workflow for implementing the planned changes.

    This workflow:
    1. Creates a draft PR if one doesn't exist (programmatically via create_draft_pr())
    2. Loops through tasks, implementing one per iteration (via /implement_github)
    3. Stops when all tasks complete, no progress detected, or TASK growth exceeds safety limit
    """

    @property
    def name(self) -> str:
        """Return workflow name."""
        return "implement"

    def init(self, ctx: WorkflowContext) -> list[str]:  # noqa: ARG002
        """Return empty list - this workflow uses execute() instead.

        The init() method is required by the Workflow protocol but ImplementWorkflow
        uses execute() for its custom loop logic.
        """
        return []

    def execute(
        self,
        ctx: WorkflowContext,
        config: "Config",
        validation_manager: PRValidationManager | None = None,
    ) -> None:
        """Execute the implementation workflow with internal loop.

        Args:
            ctx: WorkflowContext with issue and repository information
            config: Application configuration for model selection
            validation_manager: Optional PRValidationManager for CI validation.
                If not provided, a new instance will be created for backward
                compatibility.
        """
        issue_url = f"https://{ctx.repo}/issues/{ctx.issue_number}"
        key = f"{ctx.repo}#{ctx.issue_number}"

        logger.info(f"ImplementWorkflow.execute() starting for {key}")

        # Build common prompt parts
        reviewer_flags = ""
        if ctx.username_self:
            reviewer_flags = f" --reviewer {ctx.username_self}"

        project_url_context = ""
        if ctx.project_url:
            project_url_context = f" Project URL: {ctx.project_url}"

        # Step 1: Ensure PR exists (with retry)
        pr_info = self._get_pr_for_issue(ctx.repo, ctx.issue_number)
        logger.info(
            f"PR lookup for {key}: {'found PR #' + str(pr_info.get('number')) if pr_info else 'not found'}"
        )

        if not pr_info:
            logger.info(f"No PR found for {key}, creating programmatically")

            # Extract plan from issue
            plan_content, issue_title = extract_plan_from_issue(ctx.repo, ctx.issue_number)

            if plan_content is None:
                # No plan in issue - direct-to-implement flow
                # Post explanatory comment
                parts = ctx.repo.split("/", 1)
                if len(parts) == 2:
                    hostname, owner_repo = parts
                else:
                    hostname, owner_repo = "github.com", ctx.repo
                repo_ref = owner_repo if hostname == "github.com" else f"{hostname}/{owner_repo}"

                comment_body = (
                    "This issue was moved to 'Implement' status without an implementation plan. "
                    "Automatically triggering planning phase to generate checkboxes for tracking progress."
                )
                try:
                    subprocess.run(
                        [
                            "gh",
                            "issue",
                            "comment",
                            str(ctx.issue_number),
                            "--repo",
                            repo_ref,
                            "--body",
                            comment_body,
                        ],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                except subprocess.CalledProcessError as e:
                    logger.warning(f"Failed to post comment for {key}: {e.stderr}")

                # Run simplified planning phase first
                logger.info(f"No plan found in issue {key}, running /kiln-create_plan_simple")
                simple_plan_prompt = f"/kiln-create_plan_simple {issue_url}"
                self._run_prompt(simple_plan_prompt, ctx, config, "create_plan_simple")
                # Re-extract after plan creation
                plan_content, issue_title = extract_plan_from_issue(ctx.repo, ctx.issue_number)
                if plan_content is None:
                    raise RuntimeError(f"Failed to create plan for {issue_url}")

            # Validate plan has checkboxes
            total_checkboxes, _ = count_checkboxes(plan_content)
            if total_checkboxes == 0:
                raise RuntimeError(f"Plan for {issue_url} contains no checkboxes")

            # Create draft PR programmatically (has internal retries for network errors)
            pr_number = create_draft_pr(
                ctx.workspace_path,
                ctx.repo,
                ctx.issue_number,
                issue_title,
                plan_content,
                ctx.parent_branch,
            )

            # Collapse plan in issue (best effort)
            try:
                collapse_plan_in_issue(ctx.repo, ctx.issue_number)
            except RuntimeError as e:
                logger.warning(f"Failed to collapse plan in issue {key}: {e}")

            # Wait for GitHub API propagation, then look up PR with retries.
            # PR creation succeeded above, so only the lookup needs retrying.
            delay_multipliers = [1, 3, 9]
            for attempt in range(1, 4):
                delay = config.prepare_pr_delay * delay_multipliers[attempt - 1]
                logger.info(f"Waiting {delay}s for GitHub API propagation before PR lookup...")
                time.sleep(delay)

                pr_info = self._get_pr_for_issue(ctx.repo, ctx.issue_number)
                if pr_info:
                    pr_url = f"https://{ctx.repo}/pull/{pr_number}"
                    send_implementation_beginning_notification(pr_url, pr_number)
                    logger.info(f"PR created for {key}: #{pr_number}")
                    break

            if not pr_info:
                raise RuntimeError(
                    f"PR #{pr_number} was created for {issue_url} but could not be found "
                    f"via search after 3 lookup attempts."
                )

        # Step 2: Implementation loop
        # Set initial max iterations estimate based on TASK count (each TASK = 1 iteration)
        pr_body = pr_info.get("body", "")
        initial_task_count = count_tasks(pr_body)
        max_iterations_estimate = (
            initial_task_count if initial_task_count > 0 else DEFAULT_MAX_ITERATIONS
        )
        logger.info(
            f"Detected {initial_task_count} TASKs for {key}, "
            f"initial estimate={max_iterations_estimate} iterations"
        )

        iteration = 0
        last_completed = -1
        stall_count = 0
        logged_overrun = False  # Track if we've logged continuing past estimate

        while True:  # Loop controlled by exit conditions, not iteration count
            iteration += 1

            # Get current PR state (with retry for transient network errors)
            try:
                pr_info = _retry_with_backoff(
                    lambda: self._get_pr_for_issue(ctx.repo, ctx.issue_number),
                    max_attempts=3,
                    description=f"PR lookup for {issue_url}",
                )
            except NetworkError as e:
                raise RuntimeError(
                    f"Failed to reach GitHub after 3 retry attempts while looking up PR for {issue_url}: {e}"
                ) from e

            if not pr_info:
                raise RuntimeError(f"PR disappeared for {issue_url}")

            pr_body = pr_info.get("body", "")
            total_tasks, completed_tasks = count_checkboxes(pr_body)

            # Re-count TASKs to detect dynamic additions
            current_task_count = count_tasks(pr_body)
            tasks_appended = current_task_count - initial_task_count

            # Safety check: exit if too many TASKs appended (when limit is set)
            if (
                config.safety_allow_appended_tasks > 0
                and tasks_appended > config.safety_allow_appended_tasks
            ):
                logger.error(
                    f"SAFETY: TASK count increased from {initial_task_count} to "
                    f"{current_task_count} (+{tasks_appended}) for {key}, exceeds limit of "
                    f"{config.safety_allow_appended_tasks}. Stopping to prevent infinite loop."
                )
                break

            # Log if TASKs were appended (informational, only log once per new count)
            if tasks_appended > 0 and iteration > 1:
                logger.warning(
                    f"TASK count increased from {initial_task_count} to {current_task_count} "
                    f"(+{tasks_appended}) during implementation for {key}"
                )

            if total_tasks == 0:
                logger.warning(f"No checkbox tasks found in PR for {key}")
                raise ImplementationIncompleteError(
                    reason="no_tasks",
                    message=f"No checkbox tasks found in PR for {key}",
                )

            # Check if all tasks complete
            if completed_tasks == total_tasks:
                logger.info(f"All {total_tasks} tasks complete for {key}")
                break

            # Check for stall (no progress)
            if completed_tasks == last_completed:
                stall_count += 1
                if stall_count >= MAX_STALL_COUNT:
                    logger.warning(
                        f"No progress after {MAX_STALL_COUNT} iterations for {key} "
                        f"(stuck at {completed_tasks}/{total_tasks})"
                    )
                    raise ImplementationIncompleteError(
                        reason="stall",
                        message=f"No progress after {MAX_STALL_COUNT} iterations for {key} "
                        f"(stuck at {completed_tasks}/{total_tasks})",
                    )
            else:
                stall_count = 0

            last_completed = completed_tasks

            # Log when continuing past initial estimate (once)
            if iteration > max_iterations_estimate and not logged_overrun:
                logger.info(
                    f"Continuing past initial estimate ({max_iterations_estimate} TASKs) for "
                    f"{key} - {total_tasks - completed_tasks} tasks remaining"
                )
                logged_overrun = True

            logger.info(
                f"Implement iteration {iteration} for {key} "
                f"({completed_tasks}/{total_tasks} tasks complete)"
            )

            # Run implementation for one task
            implement_prompt = f"/kiln-implement_github for issue {issue_url}.{reviewer_flags}{project_url_context}"
            self._run_prompt(implement_prompt, ctx, config, "implement")

        # Check final state and run validation phase if all tasks complete
        pr_info = self._get_pr_for_issue(ctx.repo, ctx.issue_number)
        if pr_info:
            pr_body = pr_info.get("body", "")
            total_tasks, completed_tasks = count_checkboxes(pr_body)
            final_pr_number = pr_info.get("number")
            if total_tasks > 0 and completed_tasks == total_tasks and final_pr_number:
                # Run validation phase which handles CI wait, fix loop, and marking PR ready
                self._run_validation_phase(ctx, config, final_pr_number, validation_manager)
            elif iteration >= max_iterations_estimate:
                # Hit max iterations without completing all tasks
                logger.warning(f"Hit max iterations ({max_iterations_estimate}) for {key}")
                raise ImplementationIncompleteError(
                    reason="max_iterations",
                    message=f"Hit max iterations ({max_iterations_estimate}) for {key} "
                    f"({completed_tasks}/{total_tasks} tasks complete)",
                )

    def _mark_pr_ready(self, repo: str, pr_number: int) -> None:
        """Mark a draft PR as ready for review.

        Args:
            repo: Repository in 'hostname/owner/repo' format
            pr_number: PR number
        """
        try:
            repo_ref = f"https://{repo}"
            cmd = ["gh", "pr", "ready", str(pr_number), "--repo", repo_ref]
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            logger.info(f"Marked PR #{pr_number} as ready for review")
        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to mark PR #{pr_number} as ready: {e.stderr}")

    def _run_prompt(
        self,
        prompt: str,
        ctx: WorkflowContext,
        config: "Config",  # noqa: ARG002
        stage_name: str,
    ) -> None:
        """Run a single prompt through Claude.

        Args:
            prompt: The prompt to execute
            ctx: WorkflowContext with workspace path
            config: Application configuration
            stage_name: Stage name for model selection and logging
        """
        model = STAGE_MODELS.get(stage_name) or STAGE_MODELS.get("Implement")
        issue_context = f"{ctx.repo}#{ctx.issue_number}"

        logger.info(f"Running prompt (model={model}, workspace={ctx.workspace_path})")
        log_message(logger, "Prompt", prompt)

        run_claude(
            prompt,
            ctx.workspace_path,
            model=model,
            issue_context=issue_context,
            execution_stage=stage_name,
        )

        logger.info(f"Prompt completed: {stage_name}")

    def _get_pr_for_issue(self, repo: str, issue_number: int) -> dict[str, Any] | None:
        """Get the open PR that closes a specific issue.

        Args:
            repo: Repository in 'hostname/owner/repo' format
            issue_number: Issue number

        Returns:
            Dict with PR info (number, body) or None if no PR found
        """
        try:
            # Build repo reference URL
            repo_ref = f"https://{repo}"

            # Use gh CLI to find PRs - search is loose, so we filter in Python
            cmd = [
                "gh",
                "pr",
                "list",
                "--repo",
                repo_ref,
                "--state",
                "open",
                "--search",
                f"closes #{issue_number}",
                "--json",
                "number,body",
            ]

            proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
            output = proc.stdout.strip()

            if not output or output == "[]":
                return None

            prs = json.loads(output)

            # Filter for PRs that actually link to this issue
            # GitHub linking keywords: closes, fixes, resolves (case-insensitive)
            link_pattern = re.compile(
                rf"\b(closes|fixes|resolves)\s+#?{issue_number}\b", re.IGNORECASE
            )

            for pr in prs:
                body = pr.get("body", "") or ""
                if link_pattern.search(body):
                    logger.debug(f"Found PR #{pr['number']} linking to issue #{issue_number}")
                    result: dict[str, Any] = pr
                    return result

            return None

        except subprocess.CalledProcessError as e:
            error_output = (e.stderr or "").lower()
            if any(pattern in error_output for pattern in NETWORK_ERROR_PATTERNS):
                raise NetworkError(
                    f"Network error getting PR for issue #{issue_number}: {e.stderr}"
                ) from e
            logger.warning(f"Failed to get PR for issue #{issue_number}: {e.stderr}")
            return None
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse PR response: {e}")
            return None

    def _add_pr_comment(self, repo: str, pr_number: int, body: str) -> None:
        """Add a comment to a pull request.

        Args:
            repo: Repository in 'hostname/owner/repo' format
            pr_number: PR number
            body: Comment body text
        """
        try:
            repo_ref = f"https://{repo}"
            cmd = ["gh", "pr", "comment", str(pr_number), "--repo", repo_ref, "--body", body]
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            logger.debug(f"Added comment to PR #{pr_number}")
        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to add comment to PR #{pr_number}: {e.stderr}")

    def _wait_for_ci(
        self,
        repo: str,
        pr_number: int,
        sha: str,
        timeout: int = 600,
    ) -> list[CheckRunResult]:
        """Wait for all CI checks to complete on a commit.

        Polls GitHub for check run statuses with exponential backoff until all
        checks complete or the timeout is reached. Comments on the PR at progress
        milestones (3 min, 5 min, and timeout).

        Args:
            repo: Repository in 'hostname/owner/repo' format
            pr_number: PR number for commenting
            sha: Commit SHA to query check runs for
            timeout: Maximum time in seconds to wait for checks (default 600s)

        Returns:
            List of failed CheckRunResult objects. Empty list if all checks passed.
            If timeout is reached, returns whatever failed checks were found.
        """
        client = GitHubTicketClient()
        start_time = time.time()
        poll_interval = 2.0  # Start with 2s interval
        max_poll_interval = 60.0  # Cap at 60s
        backoff_multiplier = 2.0

        # Milestone tracking for PR comments
        commented_3min = False
        commented_5min = False
        commented_timeout = False

        logger.info(
            f"Waiting for CI checks on {repo}#{pr_number} (SHA: {sha[:8]}, timeout: {timeout}s)"
        )

        while True:
            elapsed = time.time() - start_time

            # Check for timeout
            if elapsed >= timeout:
                if not commented_timeout:
                    self._add_pr_comment(
                        repo,
                        pr_number,
                        f"⏰ CI validation timeout reached ({timeout}s). Proceeding with available results.",
                    )
                    commented_timeout = True
                break

            # Add milestone comments
            if elapsed >= 180 and not commented_3min:
                self._add_pr_comment(
                    repo,
                    pr_number,
                    "⏳ Still waiting for CI checks to complete (3 minutes elapsed)...",
                )
                commented_3min = True
            elif elapsed >= 300 and not commented_5min:
                self._add_pr_comment(
                    repo,
                    pr_number,
                    "⏳ Still waiting for CI checks to complete (5 minutes elapsed)...",
                )
                commented_5min = True

            # Query check runs with retry on network errors
            try:
                check_runs = _retry_with_backoff(
                    lambda: client.get_check_runs(repo, sha),
                    max_attempts=3,
                    initial_delay=5.0,
                    max_delay=30.0,
                    description=f"get_check_runs for {sha[:8]}",
                )
            except NetworkError as e:
                logger.error(f"Failed to get check runs after retries: {e}")
                # On persistent network error, return empty list to allow proceeding
                return []

            # Check if we have any checks
            if not check_runs:
                logger.debug(f"No check runs found for {sha[:8]}, waiting...")
                time.sleep(poll_interval)
                poll_interval = min(poll_interval * backoff_multiplier, max_poll_interval)
                continue

            # Count completed vs in-progress checks
            completed = [run for run in check_runs if run.is_completed]
            in_progress = [run for run in check_runs if not run.is_completed]

            logger.debug(
                f"Check runs status: {len(completed)}/{len(check_runs)} completed, "
                f"{len(in_progress)} in progress"
            )

            # If all checks completed, return failed ones
            if not in_progress:
                failed = [run for run in check_runs if run.is_failed]
                if failed:
                    logger.info(
                        f"All CI checks completed. {len(failed)} failed: "
                        f"{', '.join(r.name for r in failed)}"
                    )
                else:
                    logger.info("All CI checks completed successfully")
                return failed

            # Wait before next poll with exponential backoff
            time.sleep(poll_interval)
            poll_interval = min(poll_interval * backoff_multiplier, max_poll_interval)

        # Timeout reached - return whatever failed checks we found
        try:
            check_runs = client.get_check_runs(repo, sha)
            failed = [run for run in check_runs if run.is_failed]
            logger.warning(
                f"CI validation timed out after {timeout}s. Found {len(failed)} failed checks."
            )
            return failed
        except Exception as e:
            logger.error(f"Failed to get final check runs status: {e}")
            return []

    def _format_failed_checks(self, failed_checks: list[CheckRunResult]) -> str:
        """Format failed check runs into a human-readable summary for the fix prompt.

        Args:
            failed_checks: List of failed CheckRunResult objects

        Returns:
            Formatted string describing all failed checks
        """
        if not failed_checks:
            return "No failures detected."

        lines = []
        for check in failed_checks:
            lines.append(f"### {check.name}")
            lines.append(f"- **Status**: {check.status}")
            lines.append(f"- **Conclusion**: {check.conclusion}")
            if check.details_url:
                lines.append(f"- **Details**: {check.details_url}")
            if check.output:
                lines.append(f"- **Output**: {check.output}")
            lines.append("")

        return "\n".join(lines)

    def _run_validation_phase(
        self,
        ctx: WorkflowContext,
        config: "Config",
        pr_number: int,
        validation_manager: PRValidationManager | None = None,
    ) -> None:
        """Run CI validation phase before marking PR ready for review.

        This method:
        1. Loads validation config for the repo
        2. If validate_before_ready is false, skips validation (existing behavior)
        3. If validate_before_ready is true:
           - Gets PR head SHA
           - Waits for CI checks to complete
           - If failures, runs Claude with fix prompt
           - Commits, pushes, and re-checks (loop up to max_fix_attempts)
           - Implements stall detection if same error repeats twice
           - Comments to PR documenting fixes
        4. After validation (or timeout), marks PR ready for review

        Args:
            ctx: WorkflowContext with issue and repository information
            config: Application configuration for model selection
            pr_number: PR number to validate
            validation_manager: Optional PRValidationManager for loading validation
                config. If not provided, a new instance will be created for backward
                compatibility.
        """
        key = f"{ctx.repo}#{ctx.issue_number}"
        logger.info(f"Starting validation phase for {key} (PR #{pr_number})")

        # Use provided validation manager or create one for backward compatibility
        if validation_manager is None:
            validation_manager = PRValidationManager()
        validation_config = validation_manager.get_validation_config(ctx.repo)

        if validation_config is None or not validation_config.validate_before_ready:
            logger.info(f"Validation not enabled for {ctx.repo}, marking PR ready immediately")
            self._mark_pr_ready(ctx.repo, pr_number)
            return

        logger.info(
            f"Validation enabled for {ctx.repo}: "
            f"max_fix_attempts={validation_config.max_fix_attempts}, "
            f"timeout={validation_config.timeout}s"
        )

        # Get GitHub client for PR operations
        client = GitHubTicketClient()

        # Track fix attempts and stall detection
        fix_attempts = 0
        last_error_signature = ""
        pr_url = f"https://{ctx.repo}/pull/{pr_number}"

        while fix_attempts < validation_config.max_fix_attempts:
            # Get current PR head SHA
            sha = client.get_pr_head_sha(ctx.repo, pr_number)
            if not sha:
                logger.error(f"Failed to get HEAD SHA for PR #{pr_number}")
                break

            logger.info(f"Waiting for CI checks on SHA: {sha[:8]}")

            # Wait for CI checks to complete
            failed_checks = self._wait_for_ci(
                ctx.repo,
                pr_number,
                sha,
                timeout=validation_config.timeout,
            )

            # If all checks passed, we're done
            if not failed_checks:
                logger.info(f"All CI checks passed for {key}")
                self._add_pr_comment(
                    ctx.repo,
                    pr_number,
                    "✅ CI validation passed - all checks completed successfully.",
                )
                break

            # Check for stall - same errors repeating
            current_error_signature = ",".join(sorted(c.name for c in failed_checks))
            if current_error_signature == last_error_signature:
                logger.warning(
                    f"Stall detected: same failures repeating ({current_error_signature})"
                )
                self._add_pr_comment(
                    ctx.repo,
                    pr_number,
                    f"⚠️ CI validation stalled: same failures repeating after fix attempt.\n\n"
                    f"Failed checks: {', '.join(c.name for c in failed_checks)}\n\n"
                    "Stopping fix loop to prevent infinite attempts.",
                )
                break

            last_error_signature = current_error_signature
            fix_attempts += 1

            logger.info(
                f"CI check failures detected (attempt {fix_attempts}/{validation_config.max_fix_attempts}): "
                f"{', '.join(c.name for c in failed_checks)}"
            )

            # Format failure information for the fix prompt
            failure_summary = self._format_failed_checks(failed_checks)

            # Comment on PR about attempting to fix
            self._add_pr_comment(
                ctx.repo,
                pr_number,
                f"🔧 CI failures detected, attempting fix (attempt {fix_attempts}/{validation_config.max_fix_attempts}):\n\n"
                f"{', '.join(c.name for c in failed_checks)}",
            )

            # Run Claude with the fix prompt
            # The fix prompt expects ci_output to be passed as arguments
            fix_prompt = f"/kiln-fix_ci_failures {failure_summary}"

            try:
                self._run_prompt(fix_prompt, ctx, config, "fix_ci")
            except Exception as e:
                logger.error(f"Fix prompt failed: {e}")
                self._add_pr_comment(
                    ctx.repo,
                    pr_number,
                    f"❌ Failed to run fix prompt: {e}",
                )
                break

            # After fix prompt runs, Claude should have committed and pushed
            # The loop will continue to re-check CI with the new SHA

        # Check if we exhausted fix attempts without success
        if fix_attempts >= validation_config.max_fix_attempts:
            logger.warning(f"Exhausted max fix attempts ({validation_config.max_fix_attempts})")
            self._add_pr_comment(
                ctx.repo,
                pr_number,
                f"⚠️ CI validation: exhausted maximum fix attempts ({validation_config.max_fix_attempts}). "
                "Marking PR ready despite failures.",
            )

        # Mark PR ready for review after validation phase completes
        self._mark_pr_ready(ctx.repo, pr_number)
        pr_url = f"https://{ctx.repo}/pull/{pr_number}"
        send_ready_for_validation_notification(pr_url, pr_number)
