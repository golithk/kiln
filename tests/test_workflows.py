"""Unit tests for the workflows module."""

import subprocess

import pytest

from src.ticket_clients.base import NetworkError
from src.workflows.base import WorkflowContext
from src.workflows.implement import (
    PLAN_END_MARKER,
    PLAN_LEGACY_END_MARKER,
    PLAN_START_MARKER,
    ImplementWorkflow,
    _retry_with_backoff,
    collapse_plan_in_issue,
    count_checkboxes,
    count_tasks,
    create_draft_pr,
    extract_plan_from_body,
    extract_plan_from_issue,
)
from src.workflows.plan import PlanWorkflow
from src.workflows.prepare import PrepareWorkflow
from src.workflows.process_comments import ProcessCommentsWorkflow
from src.workflows.research import ResearchWorkflow


@pytest.fixture
def workflow_context():
    """Fixture providing a sample WorkflowContext for tests."""
    return WorkflowContext(
        repo="github.com/owner/test-repo",
        issue_number=42,
        issue_title="Add feature X to improve performance",
        workspace_path="/tmp/worktrees/owner-test-repo-42",
    )


@pytest.mark.unit
class TestWorkflowContext:
    """Tests for WorkflowContext dataclass."""

    def test_workflow_context_creation(self):
        """Test creating a WorkflowContext instance."""
        ctx = WorkflowContext(
            repo="owner/repo",
            issue_number=123,
            issue_title="Test Issue",
            workspace_path="/path/to/workspace",
        )

        assert ctx.repo == "owner/repo"
        assert ctx.issue_number == 123
        assert ctx.issue_title == "Test Issue"
        assert ctx.workspace_path == "/path/to/workspace"

    def test_workflow_context_issue_body_can_be_set(self):
        """Test that issue_body can be set during creation."""
        ctx = WorkflowContext(
            repo="owner/repo",
            issue_number=42,
            issue_title="Test",
            workspace_path="/tmp/workspace",
            issue_body="This is the issue body content.",
        )
        assert ctx.issue_body == "This is the issue body content."

    def test_workflow_context_username_self_can_be_set(self):
        """Test that username_self can be set during creation."""
        ctx = WorkflowContext(
            repo="owner/repo",
            issue_number=42,
            issue_title="Test",
            workspace_path="/tmp/workspace",
            username_self="user1",
        )
        assert ctx.username_self == "user1"


@pytest.mark.unit
class TestResearchWorkflow:
    """Tests for ResearchWorkflow."""

    def test_research_workflow_init_returns_list(self, workflow_context):
        """Test that init() returns a list of prompts."""
        workflow = ResearchWorkflow()
        prompts = workflow.init(workflow_context)

        assert isinstance(prompts, list)
        assert len(prompts) > 0
        assert all(isinstance(prompt, str) for prompt in prompts)

    def test_research_workflow_init_returns_one_prompt(self, workflow_context):
        """Test that ResearchWorkflow returns exactly 1 prompt."""
        workflow = ResearchWorkflow()
        prompts = workflow.init(workflow_context)

        assert len(prompts) == 1

    def test_research_workflow_prompts_contain_issue_number(self, workflow_context):
        """Test that prompts contain the issue number in URL format."""
        workflow = ResearchWorkflow()
        prompts = workflow.init(workflow_context)

        assert str(workflow_context.issue_number) in prompts[0]
        # Issue number appears in URL format: /issues/42
        assert f"/issues/{workflow_context.issue_number}" in prompts[0]

    def test_research_workflow_prompt_uses_slash_command(self, workflow_context):
        """Test that the prompt uses the research slash command."""
        workflow = ResearchWorkflow()
        prompts = workflow.init(workflow_context)

        assert "/kiln-research_codebase_github" in prompts[0]


@pytest.mark.unit
class TestPlanWorkflow:
    """Tests for PlanWorkflow."""

    def test_plan_workflow_init_returns_list(self, workflow_context):
        """Test that init() returns a list of prompts."""
        workflow = PlanWorkflow()
        prompts = workflow.init(workflow_context)

        assert isinstance(prompts, list)
        assert len(prompts) > 0
        assert all(isinstance(prompt, str) for prompt in prompts)

    def test_plan_workflow_init_returns_one_prompt(self, workflow_context):
        """Test that PlanWorkflow returns exactly 1 prompt."""
        workflow = PlanWorkflow()
        prompts = workflow.init(workflow_context)

        assert len(prompts) == 1

    def test_plan_workflow_prompts_contain_issue_number(self, workflow_context):
        """Test that prompts contain the issue number in URL format."""
        workflow = PlanWorkflow()
        prompts = workflow.init(workflow_context)

        assert str(workflow_context.issue_number) in prompts[0]
        # Issue number appears in URL format: /issues/42
        assert f"/issues/{workflow_context.issue_number}" in prompts[0]

    def test_plan_workflow_prompts_contain_repo(self, workflow_context):
        """Test that prompts contain the repository name."""
        workflow = PlanWorkflow()
        prompts = workflow.init(workflow_context)

        assert workflow_context.repo in prompts[0]

    def test_plan_workflow_prompt_uses_slash_command(self, workflow_context):
        """Test that the prompt uses the create_plan slash command."""
        workflow = PlanWorkflow()
        prompts = workflow.init(workflow_context)

        assert "/kiln-create_plan" in prompts[0]


@pytest.mark.unit
class TestProcessCommentsWorkflow:
    """Tests for ProcessCommentsWorkflow."""

    def test_process_comments_workflow_returns_one_prompt(self):
        """Test that workflow returns exactly one prompt."""
        ctx = WorkflowContext(
            repo="owner/repo",
            issue_number=42,
            issue_title="Test Issue",
            workspace_path="/tmp/workspace",
            comment_body="Please add more detail",
            target_type="research",
        )
        workflow = ProcessCommentsWorkflow()
        prompts = workflow.init(ctx)

        assert len(prompts) == 1

    def test_process_comments_workflow_includes_comment_body(self):
        """Test that the prompt includes the user's comment."""
        comment_text = "Please expand on option B with more examples"
        ctx = WorkflowContext(
            repo="owner/repo",
            issue_number=42,
            issue_title="Test Issue",
            workspace_path="/tmp/workspace",
            comment_body=comment_text,
            target_type="research",
        )
        workflow = ProcessCommentsWorkflow()
        prompts = workflow.init(ctx)

        assert comment_text in prompts[0]

    def test_process_comments_workflow_includes_target_type(self):
        """Test that the prompt includes the target type."""
        ctx = WorkflowContext(
            repo="owner/repo",
            issue_number=42,
            issue_title="Test Issue",
            workspace_path="/tmp/workspace",
            comment_body="Feedback",
            target_type="plan",
        )
        workflow = ProcessCommentsWorkflow()
        prompts = workflow.init(ctx)

        assert "plan" in prompts[0]
        assert "Implementation Plan section" in prompts[0]
        assert "issue description" in prompts[0]

    def test_process_comments_workflow_includes_issue_reference(self):
        """Test that the prompt includes repo and issue number."""
        ctx = WorkflowContext(
            repo="myorg/myrepo",
            issue_number=123,
            issue_title="Test Issue",
            workspace_path="/tmp/workspace",
            comment_body="Feedback",
            target_type="description",
        )
        workflow = ProcessCommentsWorkflow()
        prompts = workflow.init(ctx)

        assert "myorg/myrepo" in prompts[0]
        assert "123" in prompts[0]

    def test_process_comments_workflow_defaults_to_description(self):
        """Test that target defaults to description when not specified."""
        ctx = WorkflowContext(
            repo="owner/repo",
            issue_number=42,
            issue_title="Test Issue",
            workspace_path="/tmp/workspace",
            comment_body="Feedback",
            target_type=None,  # Not specified
        )
        workflow = ProcessCommentsWorkflow()
        prompts = workflow.init(ctx)

        assert "description" in prompts[0]

    def test_process_comments_workflow_research_target(self):
        """Test prompt generation for research target."""
        ctx = WorkflowContext(
            repo="owner/repo",
            issue_number=42,
            issue_title="Test Issue",
            workspace_path="/tmp/workspace",
            comment_body="Add more options",
            target_type="research",
        )
        workflow = ProcessCommentsWorkflow()
        prompts = workflow.init(ctx)

        assert "Research Findings section" in prompts[0]
        assert "issue description" in prompts[0]
        assert "research" in prompts[0]

    def test_process_comments_workflow_instructs_in_place_edit(self):
        """Test that the prompt instructs to edit in-place, not create new comments."""
        ctx = WorkflowContext(
            repo="owner/repo",
            issue_number=42,
            issue_title="Test Issue",
            workspace_path="/tmp/workspace",
            comment_body="Feedback",
            target_type="plan",
        )
        workflow = ProcessCommentsWorkflow()
        prompts = workflow.init(ctx)

        assert "IN-PLACE" in prompts[0]
        assert "NOT create new comments" in prompts[0]


@pytest.mark.unit
class TestPrepareWorkflow:
    """Tests for PrepareWorkflow."""

    def test_prepare_workflow_returns_two_prompts(self):
        """Test that PrepareWorkflow returns exactly 2 prompts."""
        ctx = WorkflowContext(
            repo="owner/repo",
            issue_number=42,
            issue_title="Test Issue",
            workspace_path="/tmp/worktrees",
        )
        workflow = PrepareWorkflow()
        prompts = workflow.init(ctx)

        assert len(prompts) == 2

    def test_prepare_workflow_first_prompt_clones_repo(self):
        """Test that first prompt handles cloning/updating the repo."""
        ctx = WorkflowContext(
            repo="owner/repo",
            issue_number=42,
            issue_title="Test Issue",
            workspace_path="/tmp/worktrees",
        )
        workflow = PrepareWorkflow()
        prompts = workflow.init(ctx)

        assert "Clone https://github.com/owner/repo.git" in prompts[0]
        # Uses owner_repo format to avoid path collisions
        assert "to /tmp/worktrees/owner_repo if missing" in prompts[0]

    def test_prepare_workflow_with_issue_body_includes_body_directly(self):
        """Test that with issue_body, prompt includes the body directly."""
        issue_body = "## Summary\n\nThis is the issue description."
        ctx = WorkflowContext(
            repo="owner/repo",
            issue_number=42,
            issue_title="Test Issue Title",
            workspace_path="/tmp/worktrees",
            issue_body=issue_body,
        )
        workflow = PrepareWorkflow()
        prompts = workflow.init(ctx)

        # Should include the issue body directly
        assert issue_body in prompts[1]
        assert "Issue title: Test Issue Title" in prompts[1]
        assert "Issue description:" in prompts[1]
        # Should NOT ask Claude to read the issue
        assert "Read github issue" not in prompts[1]

    def test_prepare_workflow_with_issue_body_includes_issue_number(self):
        """Test that with issue_body, prompt still references issue number."""
        ctx = WorkflowContext(
            repo="owner/repo",
            issue_number=123,
            issue_title="Test Issue",
            workspace_path="/tmp/worktrees",
            issue_body="Issue body content",
        )
        workflow = PrepareWorkflow()
        prompts = workflow.init(ctx)

        # Issue number appears in the worktree path and branch instructions
        # Uses owner_repo format to avoid path collisions
        assert "owner_repo-issue-123" in prompts[1]
        assert "(123-)" in prompts[1]

    def test_prepare_workflow_worktree_path_correct(self):
        """Test that worktree path is constructed correctly."""
        ctx = WorkflowContext(
            repo="myorg/myrepo",
            issue_number=99,
            issue_title="Test Issue",
            workspace_path="/home/user/worktrees",
            issue_body="Body text",
        )
        workflow = PrepareWorkflow()
        prompts = workflow.init(ctx)

        # Uses owner_repo format to avoid path collisions
        assert "myorg_myrepo-issue-99" in prompts[1]

    def test_prepare_workflow_empty_issue_body_treated_as_provided(self):
        """Test that empty string issue_body is treated as provided (not None)."""
        ctx = WorkflowContext(
            repo="owner/repo",
            issue_number=42,
            issue_title="Test Issue",
            workspace_path="/tmp/worktrees",
            issue_body="",  # Empty string, not None
        )
        workflow = PrepareWorkflow()
        prompts = workflow.init(ctx)

        # Should NOT ask Claude to read the issue (empty body is still "provided")
        assert "Read github issue" not in prompts[1]
        assert "Issue description:" in prompts[1]

    def test_prepare_workflow_with_parent_branch_creates_from_parent(self):
        """Test that workflow uses parent branch when provided."""
        ctx = WorkflowContext(
            repo="github.com/owner/repo",
            issue_number=42,
            issue_title="Child Issue",
            workspace_path="/tmp/worktrees",
            issue_body="Child issue body",
            parent_issue_number=10,
            parent_branch="10-parent-feature",
        )
        workflow = PrepareWorkflow()
        prompts = workflow.init(ctx)

        # Should instruct to create from parent branch
        assert "10-parent-feature" in prompts[1]
        assert "parent branch" in prompts[1]
        assert "parent issue #10" in prompts[1]
        assert "git fetch origin 10-parent-feature" in prompts[1]

    def test_prepare_workflow_without_parent_branch_creates_from_main(self):
        """Test that workflow uses main branch when no parent branch provided."""
        ctx = WorkflowContext(
            repo="github.com/owner/repo",
            issue_number=42,
            issue_title="Standalone Issue",
            workspace_path="/tmp/worktrees",
            issue_body="Issue body",
            parent_issue_number=None,
            parent_branch=None,
        )
        workflow = PrepareWorkflow()
        prompts = workflow.init(ctx)

        # Should instruct to create from main branch
        assert "main branch" in prompts[1]
        # Should NOT mention parent branch
        assert "parent branch" not in prompts[1]

    def test_prepare_workflow_with_parent_number_but_no_branch(self):
        """Test that workflow uses main when parent has no open PR."""
        ctx = WorkflowContext(
            repo="github.com/owner/repo",
            issue_number=42,
            issue_title="Child Issue",
            workspace_path="/tmp/worktrees",
            issue_body="Issue body",
            parent_issue_number=10,  # Has parent but no open PR
            parent_branch=None,
        )
        workflow = PrepareWorkflow()
        prompts = workflow.init(ctx)

        # Should still use main branch since no parent PR exists
        assert "main branch" in prompts[1]
        assert "parent branch" not in prompts[1]

    def test_prepare_workflow_with_explicit_feature_branch_no_parent_issue(self):
        """Test that workflow uses feature_branch messaging when no parent_issue_number.

        When parent_branch is set but parent_issue_number is None, this indicates
        an explicit feature_branch from issue frontmatter, not a parent issue's PR.
        """
        ctx = WorkflowContext(
            repo="github.com/owner/repo",
            issue_number=42,
            issue_title="Feature Issue",
            workspace_path="/tmp/worktrees",
            issue_body="Issue body",
            parent_issue_number=None,  # No parent issue
            parent_branch="my-feature-branch",  # Explicit feature_branch from frontmatter
        )
        workflow = PrepareWorkflow()
        prompts = workflow.init(ctx)

        # Should instruct to create from feature branch with frontmatter messaging
        assert "my-feature-branch" in prompts[1]
        assert "feature branch" in prompts[1]
        assert "specified in issue frontmatter" in prompts[1]
        assert "git fetch origin my-feature-branch" in prompts[1]
        # Should NOT mention parent issue
        assert "parent issue" not in prompts[1]
        assert "parent branch" not in prompts[1]

    def test_prepare_workflow_includes_git_c_instruction(self):
        """Test that worktree prompt instructs Claude to use git -C for correct repo."""
        ctx = WorkflowContext(
            repo="github.com/owner/repo",
            issue_number=42,
            issue_title="Test Issue",
            workspace_path="/tmp/worktrees",
            issue_body="Body text",
        )
        workflow = PrepareWorkflow()
        prompts = workflow.init(ctx)

        # Should instruct to run from inside the cloned repo
        assert "git -C /tmp/worktrees/owner_repo worktree add" in prompts[1]
        assert "MUST run the git worktree command from inside the cloned repo" in prompts[1]


@pytest.mark.unit
class TestCountTasks:
    """Tests for the count_tasks() helper function."""

    def test_count_tasks_h2_header_format(self):
        """Test counting tasks with ## TASK N: format."""
        markdown = """
## TASK 1: First task
Some description here.

## TASK 2: Second task
Another description.
"""
        assert count_tasks(markdown) == 2

    def test_count_tasks_h3_header_format(self):
        """Test counting tasks with ### TASK N: format."""
        markdown = """
### TASK 1: First task
Some description here.

### TASK 2: Second task
Another description.

### TASK 3: Third task
More description.
"""
        assert count_tasks(markdown) == 3

    def test_count_tasks_bold_format(self):
        """Test counting tasks with **TASK N**: format."""
        markdown = """
**TASK 1**: First task description.

**TASK 2**: Second task description.
"""
        assert count_tasks(markdown) == 2

    def test_count_tasks_case_insensitivity(self):
        """Test that task matching is case insensitive."""
        markdown = """
## task 1: lowercase
## Task 2: titlecase
## TASK 3: uppercase
**task 4**: bold lowercase
"""
        assert count_tasks(markdown) == 4

    def test_count_tasks_empty_string(self):
        """Test that empty string returns 0."""
        assert count_tasks("") == 0

    def test_count_tasks_no_tasks_present(self):
        """Test text without any TASK blocks returns 0."""
        markdown = """
## Overview
This is a document without any tasks.

### Section 1
Some content.

- [ ] A checkbox but not a TASK
"""
        assert count_tasks(markdown) == 0

    def test_count_tasks_multiple_formats_mixed(self):
        """Test counting tasks with mixed header and bold formats."""
        markdown = """
## TASK 1: Header format
Description.

**TASK 2**: Bold format
Description.

### TASK 3: H3 header format
Description.
"""
        assert count_tasks(markdown) == 3

    def test_count_tasks_with_surrounding_content(self):
        """Test tasks embedded in a larger document."""
        markdown = """
# Implementation Plan

## Overview
This plan outlines the work to be done.

## TASK 1: Set up infrastructure
- [ ] Create database
- [ ] Configure server

## TASK 2: Implement features
- [ ] Add login
- [ ] Add logout

## Appendix
Additional notes here.
"""
        assert count_tasks(markdown) == 2


@pytest.mark.unit
class TestCountCheckboxes:
    """Tests for the count_checkboxes() helper function."""

    def test_count_checkboxes_mixed_checked_unchecked(self):
        """Test counting a mix of checked and unchecked checkboxes."""
        markdown = """
- [x] Completed task 1
- [ ] Pending task 2
- [x] Completed task 3
- [ ] Pending task 4
"""
        total, completed = count_checkboxes(markdown)
        assert total == 4
        assert completed == 2

    def test_count_checkboxes_all_checked(self):
        """Test when all checkboxes are checked."""
        markdown = """
- [x] Task 1
- [x] Task 2
- [x] Task 3
"""
        total, completed = count_checkboxes(markdown)
        assert total == 3
        assert completed == 3

    def test_count_checkboxes_all_unchecked(self):
        """Test when all checkboxes are unchecked."""
        markdown = """
- [ ] Task 1
- [ ] Task 2
- [ ] Task 3
- [ ] Task 4
"""
        total, completed = count_checkboxes(markdown)
        assert total == 4
        assert completed == 0

    def test_count_checkboxes_empty_string(self):
        """Test that empty string returns (0, 0)."""
        total, completed = count_checkboxes("")
        assert total == 0
        assert completed == 0

    def test_count_checkboxes_uppercase_x(self):
        """Test that [X] uppercase is counted as checked."""
        markdown = """
- [X] Uppercase checked
- [x] Lowercase checked
- [ ] Unchecked
"""
        total, completed = count_checkboxes(markdown)
        assert total == 3
        assert completed == 2

    def test_count_checkboxes_malformed_not_counted(self):
        """Test that malformed checkboxes without proper space are not counted."""
        markdown = """
- [x] Valid checked
- [ ] Valid unchecked
- [] Malformed - no space inside brackets
- [  ] Malformed - double space
-[ ] Malformed - no space after dash
"""
        total, completed = count_checkboxes(markdown)
        # Only the two valid checkboxes should be counted
        assert total == 2
        assert completed == 1


@pytest.mark.unit
class TestExtractPlanFromBody:
    """Tests for extract_plan_from_body() helper function."""

    def test_extracts_plan_with_new_markers(self):
        """Test extraction with new-style end marker."""
        body = f"""Some description here.

---

{PLAN_START_MARKER}
# Implementation Plan

## TASK 1: First task
- [ ] Subtask A
- [ ] Subtask B
{PLAN_END_MARKER}
"""
        result = extract_plan_from_body(body)
        assert result is not None
        assert "# Implementation Plan" in result
        assert "- [ ] Subtask A" in result
        assert "- [ ] Subtask B" in result
        assert PLAN_START_MARKER not in result
        assert PLAN_END_MARKER not in result

    def test_extracts_plan_with_legacy_end_marker(self):
        """Test extraction with legacy <!-- /kiln --> end marker."""
        body = f"""Some description here.

{PLAN_START_MARKER}
# Plan
- [ ] Task 1
- [ ] Task 2
{PLAN_LEGACY_END_MARKER}
"""
        result = extract_plan_from_body(body)
        assert result is not None
        assert "# Plan" in result
        assert "- [ ] Task 1" in result
        assert PLAN_LEGACY_END_MARKER not in result

    def test_returns_none_when_no_start_marker(self):
        """Test that None is returned when start marker is missing."""
        body = """# Implementation Plan

## TASK 1: Some task
- [ ] Do something
"""
        result = extract_plan_from_body(body)
        assert result is None

    def test_handles_plan_without_end_marker(self):
        """Test extraction when end marker is missing (takes everything after start)."""
        body = f"""Description.

{PLAN_START_MARKER}
# Plan
- [ ] Task 1
- [ ] Task 2

Some trailing content
"""
        result = extract_plan_from_body(body)
        assert result is not None
        assert "# Plan" in result
        assert "- [ ] Task 1" in result
        assert "Some trailing content" in result

    def test_extracts_empty_plan(self):
        """Test extraction when plan section is empty."""
        body = f"""{PLAN_START_MARKER}
{PLAN_END_MARKER}"""
        result = extract_plan_from_body(body)
        # Empty string after strip
        assert result == ""

    def test_extracts_plan_with_complex_content(self):
        """Test extraction with complex markdown including code blocks."""
        body = f"""Issue description.

{PLAN_START_MARKER}
# Implementation Plan

## TASK 1: Add helper function

```python
def helper():
    pass
```

- [ ] Create function
- [ ] Add tests
{PLAN_END_MARKER}
"""
        result = extract_plan_from_body(body)
        assert result is not None
        assert "```python" in result
        assert "def helper():" in result
        assert "- [ ] Create function" in result

    def test_prefers_new_end_marker_over_legacy(self):
        """Test that new end marker is preferred when both are present."""
        body = f"""{PLAN_START_MARKER}
# Plan
- [ ] Task 1
{PLAN_END_MARKER}
More content after plan
{PLAN_LEGACY_END_MARKER}
"""
        result = extract_plan_from_body(body)
        assert result is not None
        assert "# Plan" in result
        assert "- [ ] Task 1" in result
        # Should NOT include content after PLAN_END_MARKER
        assert "More content after plan" not in result


@pytest.mark.unit
class TestExtractPlanFromIssue:
    """Tests for extract_plan_from_issue() helper function."""

    def test_extracts_plan_and_title_successfully(self):
        """Test successful extraction of plan and title from issue."""
        import json
        from unittest.mock import MagicMock, patch

        mock_result = MagicMock()
        mock_result.stdout = json.dumps(
            {
                "title": "Add new feature",
                "body": f"""Description.

{PLAN_START_MARKER}
# Plan
- [ ] Task 1
{PLAN_END_MARKER}
""",
            }
        )

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            plan, title = extract_plan_from_issue("github.com/owner/repo", 123)

        assert plan is not None
        assert "# Plan" in plan
        assert "- [ ] Task 1" in plan
        assert title == "Add new feature"

        # Verify gh command was called correctly
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "gh" in call_args
        assert "issue" in call_args
        assert "view" in call_args
        assert "https://github.com/owner/repo/issues/123" in call_args

    def test_returns_none_when_no_plan_in_issue(self):
        """Test that plan is None when issue has no plan markers."""
        import json
        from unittest.mock import MagicMock, patch

        mock_result = MagicMock()
        mock_result.stdout = json.dumps(
            {
                "title": "Simple issue",
                "body": "Just a description without any plan.",
            }
        )

        with patch("subprocess.run", return_value=mock_result):
            plan, title = extract_plan_from_issue("github.com/owner/repo", 456)

        assert plan is None
        assert title == "Simple issue"

    def test_handles_empty_body(self):
        """Test handling of issue with empty body."""
        import json
        from unittest.mock import MagicMock, patch

        mock_result = MagicMock()
        mock_result.stdout = json.dumps(
            {
                "title": "Empty body issue",
                "body": "",
            }
        )

        with patch("subprocess.run", return_value=mock_result):
            plan, title = extract_plan_from_issue("github.com/owner/repo", 789)

        assert plan is None
        assert title == "Empty body issue"

    def test_handles_null_body(self):
        """Test handling of issue with null body."""
        import json
        from unittest.mock import MagicMock, patch

        mock_result = MagicMock()
        mock_result.stdout = json.dumps(
            {
                "title": "Null body issue",
                "body": None,
            }
        )

        with patch("subprocess.run", return_value=mock_result):
            plan, title = extract_plan_from_issue("github.com/owner/repo", 101)

        assert plan is None
        assert title == "Null body issue"

    def test_raises_runtime_error_on_subprocess_failure(self):
        """Test that RuntimeError is raised when gh command fails."""
        from unittest.mock import patch

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                returncode=1,
                cmd=["gh"],
                stderr="Could not resolve to a Repository",
            )
            with pytest.raises(RuntimeError, match="Failed to fetch issue"):
                extract_plan_from_issue("github.com/owner/repo", 999)

    def test_raises_runtime_error_on_json_parse_failure(self):
        """Test that RuntimeError is raised when JSON parsing fails."""
        from unittest.mock import MagicMock, patch

        mock_result = MagicMock()
        mock_result.stdout = "not valid json"

        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(RuntimeError, match="Failed to parse issue response"):
                extract_plan_from_issue("github.com/owner/repo", 111)


@pytest.mark.unit
class TestCreateDraftPr:
    """Tests for create_draft_pr() helper function."""

    def test_creates_pr_with_plan(self, tmp_path):
        """Test successful PR creation with plan content."""
        from unittest.mock import MagicMock, patch

        # Mock subprocess calls: empty commit, push, pr create
        mock_results = [
            MagicMock(),  # git commit
            MagicMock(),  # git push
            MagicMock(stdout="https://github.com/owner/repo/pull/42\n"),  # gh pr create
        ]
        call_idx = {"value": 0}

        def mock_run(*args, **kwargs):
            result = mock_results[call_idx["value"]]
            call_idx["value"] += 1
            return result

        with patch("subprocess.run", side_effect=mock_run) as mock_subprocess:
            pr_number = create_draft_pr(
                str(tmp_path),
                "github.com/owner/repo",
                123,
                "Test Issue",
                "# Plan\n- [ ] Task 1\n- [ ] Task 2",
                None,
            )

        assert pr_number == 42
        assert mock_subprocess.call_count == 3

        # Verify git commit call
        commit_call = mock_subprocess.call_args_list[0]
        assert "git" in commit_call[0][0]
        assert "commit" in commit_call[0][0]
        assert "--allow-empty" in commit_call[0][0]
        assert "feat: begin implementation for #123" in commit_call[0][0]

        # Verify git push call
        push_call = mock_subprocess.call_args_list[1]
        assert "git" in push_call[0][0]
        assert "push" in push_call[0][0]
        assert "-u" in push_call[0][0]
        assert "origin" in push_call[0][0]

        # Verify gh pr create call
        pr_call = mock_subprocess.call_args_list[2]
        assert "gh" in pr_call[0][0]
        assert "pr" in pr_call[0][0]
        assert "create" in pr_call[0][0]
        assert "--draft" in pr_call[0][0]

    def test_includes_base_branch_when_provided(self, tmp_path):
        """Test that --base flag is included when base_branch is provided."""
        from unittest.mock import MagicMock, patch

        mock_results = [
            MagicMock(),  # git commit
            MagicMock(),  # git push
            MagicMock(stdout="https://github.com/owner/repo/pull/42\n"),  # gh pr create
        ]
        call_idx = {"value": 0}

        def mock_run(*args, **kwargs):
            result = mock_results[call_idx["value"]]
            call_idx["value"] += 1
            return result

        with patch("subprocess.run", side_effect=mock_run) as mock_subprocess:
            create_draft_pr(
                str(tmp_path),
                "github.com/owner/repo",
                123,
                "Test Issue",
                "# Plan",
                "feature-branch",
            )

        # Verify --base flag is in the gh pr create call
        pr_call = mock_subprocess.call_args_list[2]
        cmd = pr_call[0][0]
        assert "--base" in cmd
        assert "feature-branch" in cmd

    def test_pr_body_includes_closes_keyword(self, tmp_path):
        """Test that PR body includes 'Closes #N' keyword."""
        from unittest.mock import MagicMock, patch

        captured_body = {"value": None}

        def mock_run(cmd, **kwargs):
            if "gh" in cmd and "pr" in cmd and "create" in cmd:
                # Find --body and capture the next arg
                for i, arg in enumerate(cmd):
                    if arg == "--body":
                        captured_body["value"] = cmd[i + 1]
                        break
                return MagicMock(stdout="https://github.com/owner/repo/pull/99\n")
            return MagicMock()

        with patch("subprocess.run", side_effect=mock_run):
            create_draft_pr(
                str(tmp_path),
                "github.com/owner/repo",
                456,
                "Test Issue",
                "# Plan\n- [ ] Task",
                None,
            )

        assert captured_body["value"] is not None
        assert "Closes #456" in captured_body["value"]
        assert "# Plan" in captured_body["value"]
        assert "- [ ] Task" in captured_body["value"]

    def test_pr_body_includes_base_branch_note(self, tmp_path):
        """Test that PR body includes note about base branch when provided."""
        from unittest.mock import MagicMock, patch

        captured_body = {"value": None}

        def mock_run(cmd, **kwargs):
            if "gh" in cmd and "pr" in cmd and "create" in cmd:
                for i, arg in enumerate(cmd):
                    if arg == "--body":
                        captured_body["value"] = cmd[i + 1]
                        break
                return MagicMock(stdout="https://github.com/owner/repo/pull/99\n")
            return MagicMock()

        with patch("subprocess.run", side_effect=mock_run):
            create_draft_pr(
                str(tmp_path),
                "github.com/owner/repo",
                123,
                "Test Issue",
                "# Plan",
                "my-feature-branch",
            )

        assert captured_body["value"] is not None
        assert "my-feature-branch" in captured_body["value"]
        assert "not the default branch" in captured_body["value"]

    def test_raises_runtime_error_on_commit_failure(self, tmp_path):
        """Test that RuntimeError is raised when git commit fails."""
        from unittest.mock import patch

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                returncode=1,
                cmd=["git"],
                stderr="error: unable to create commit",
            )
            with pytest.raises(RuntimeError, match="Failed to create empty commit"):
                create_draft_pr(
                    str(tmp_path),
                    "github.com/owner/repo",
                    123,
                    "Test Issue",
                    "# Plan",
                    None,
                )

    def test_raises_runtime_error_on_push_failure(self, tmp_path):
        """Test that RuntimeError is raised when git push fails."""
        from unittest.mock import MagicMock, patch

        call_count = {"value": 0}

        def mock_run(cmd, **kwargs):
            call_count["value"] += 1
            if call_count["value"] == 1:
                return MagicMock()  # commit succeeds
            raise subprocess.CalledProcessError(
                returncode=1,
                cmd=["git"],
                stderr="error: failed to push",
            )

        with patch("subprocess.run", side_effect=mock_run):
            with pytest.raises(RuntimeError, match="Failed to push to remote"):
                create_draft_pr(
                    str(tmp_path),
                    "github.com/owner/repo",
                    123,
                    "Test Issue",
                    "# Plan",
                    None,
                )

    def test_raises_runtime_error_on_pr_create_failure(self, tmp_path):
        """Test that RuntimeError is raised when gh pr create fails."""
        from unittest.mock import MagicMock, patch

        call_count = {"value": 0}

        def mock_run(cmd, **kwargs):
            call_count["value"] += 1
            if call_count["value"] <= 2:
                return MagicMock()  # commit and push succeed
            raise subprocess.CalledProcessError(
                returncode=1,
                cmd=["gh"],
                stderr="error: pull request already exists",
            )

        with patch("subprocess.run", side_effect=mock_run):
            with pytest.raises(RuntimeError, match="Failed to create draft PR"):
                create_draft_pr(
                    str(tmp_path),
                    "github.com/owner/repo",
                    123,
                    "Test Issue",
                    "# Plan",
                    None,
                )

    def test_retries_on_network_error(self, tmp_path):
        """Test that PR creation is retried on network errors."""
        from unittest.mock import MagicMock, patch

        call_count = {"value": 0}

        def mock_run(cmd, **kwargs):
            call_count["value"] += 1
            if call_count["value"] <= 2:
                return MagicMock()  # commit and push succeed
            if call_count["value"] <= 4:  # First 2 PR create attempts fail with network error
                raise subprocess.CalledProcessError(
                    returncode=1,
                    cmd=["gh"],
                    stderr="net/http: TLS handshake timeout",
                )
            return MagicMock(stdout="https://github.com/owner/repo/pull/42\n")

        with (
            patch("subprocess.run", side_effect=mock_run),
            patch("src.workflows.implement.time.sleep"),  # Speed up test
        ):
            pr_number = create_draft_pr(
                str(tmp_path),
                "github.com/owner/repo",
                123,
                "Test Issue",
                "# Plan",
                None,
            )

        assert pr_number == 42
        # commit (1) + push (1) + 3 pr create attempts (2 fail, 1 success)
        assert call_count["value"] == 5

    def test_parses_pr_number_from_various_url_formats(self, tmp_path):
        """Test parsing PR number from different URL formats."""
        from unittest.mock import MagicMock, patch

        test_cases = [
            ("https://github.com/owner/repo/pull/123\n", 123),
            ("https://github.com/owner/repo/pull/456", 456),
            ("https://github.com/owner/repo/pull/789/", 789),
        ]

        for url, expected_number in test_cases:
            call_count = {"value": 0}
            captured_url = url  # Capture in closure

            def mock_run(cmd, captured=captured_url, counter=call_count, **kwargs):
                counter["value"] += 1
                if counter["value"] <= 2:
                    return MagicMock()
                return MagicMock(stdout=captured)

            with patch("subprocess.run", side_effect=mock_run):
                pr_number = create_draft_pr(
                    str(tmp_path),
                    "github.com/owner/repo",
                    1,
                    "Test",
                    "Plan",
                    None,
                )

            assert pr_number == expected_number, f"Failed for URL: {url}"


@pytest.mark.unit
class TestCollapsePlanInIssue:
    """Tests for collapse_plan_in_issue() helper function."""

    def test_collapses_plan_section_successfully(self):
        """Test successful collapse of plan section."""
        import json
        from unittest.mock import MagicMock, patch

        original_body = f"""Issue description.

---

{PLAN_START_MARKER}
# Implementation Plan

## TASK 1: First task
- [ ] Subtask A
- [ ] Subtask B
{PLAN_END_MARKER}
"""

        mock_view_result = MagicMock()
        mock_view_result.stdout = json.dumps({"body": original_body})

        captured_body = {"value": None}

        def mock_run(cmd, **kwargs):
            if "view" in cmd:
                return mock_view_result
            if "edit" in cmd:
                # Capture the new body
                for i, arg in enumerate(cmd):
                    if arg == "--body":
                        captured_body["value"] = cmd[i + 1]
                        break
                return MagicMock()
            return MagicMock()

        with patch("subprocess.run", side_effect=mock_run):
            collapse_plan_in_issue("github.com/owner/repo", 123)

        assert captured_body["value"] is not None
        assert "<details>" in captured_body["value"]
        assert "<summary><h2>Implementation Plan</h2></summary>" in captured_body["value"]
        assert PLAN_START_MARKER in captured_body["value"]
        assert "</details>" in captured_body["value"]

    def test_skips_when_no_plan_marker(self):
        """Test that function does nothing when no plan marker exists."""
        import json
        from unittest.mock import MagicMock, patch

        original_body = "Just a simple issue without a plan."

        mock_view_result = MagicMock()
        mock_view_result.stdout = json.dumps({"body": original_body})

        with patch("subprocess.run", return_value=mock_view_result) as mock_run:
            collapse_plan_in_issue("github.com/owner/repo", 123)

        # Only the view call should be made, not edit
        assert mock_run.call_count == 1
        assert "view" in mock_run.call_args[0][0]

    def test_skips_when_already_collapsed(self):
        """Test idempotency - does nothing if plan is already collapsed."""
        import json
        from unittest.mock import MagicMock, patch

        already_collapsed = f"""Issue description.

---

<details>
<summary><h2>Implementation Plan</h2></summary>

{PLAN_START_MARKER}
# Plan
- [ ] Task 1
{PLAN_END_MARKER}

</details>
"""

        mock_view_result = MagicMock()
        mock_view_result.stdout = json.dumps({"body": already_collapsed})

        with patch("subprocess.run", return_value=mock_view_result) as mock_run:
            collapse_plan_in_issue("github.com/owner/repo", 123)

        # Only the view call should be made, not edit
        assert mock_run.call_count == 1
        assert "view" in mock_run.call_args[0][0]

    def test_skips_when_no_end_marker(self):
        """Test that function does nothing when end marker is missing."""
        import json
        from unittest.mock import MagicMock, patch

        body_without_end = f"""Issue description.

{PLAN_START_MARKER}
# Plan
- [ ] Task 1
(no end marker)
"""

        mock_view_result = MagicMock()
        mock_view_result.stdout = json.dumps({"body": body_without_end})

        with patch("subprocess.run", return_value=mock_view_result) as mock_run:
            collapse_plan_in_issue("github.com/owner/repo", 123)

        # Only the view call should be made, not edit
        assert mock_run.call_count == 1

    def test_handles_legacy_end_marker(self):
        """Test that function works with legacy <!-- /kiln --> end marker."""
        import json
        from unittest.mock import MagicMock, patch

        original_body = f"""Issue description.

{PLAN_START_MARKER}
# Plan
- [ ] Task 1
{PLAN_LEGACY_END_MARKER}
"""

        mock_view_result = MagicMock()
        mock_view_result.stdout = json.dumps({"body": original_body})

        captured_body = {"value": None}

        def mock_run(cmd, **kwargs):
            if "view" in cmd:
                return mock_view_result
            if "edit" in cmd:
                for i, arg in enumerate(cmd):
                    if arg == "--body":
                        captured_body["value"] = cmd[i + 1]
                        break
                return MagicMock()
            return MagicMock()

        with patch("subprocess.run", side_effect=mock_run):
            collapse_plan_in_issue("github.com/owner/repo", 123)

        assert captured_body["value"] is not None
        assert "<details>" in captured_body["value"]
        assert PLAN_LEGACY_END_MARKER in captured_body["value"]

    def test_raises_runtime_error_on_fetch_failure(self):
        """Test that RuntimeError is raised when gh view fails."""
        from unittest.mock import patch

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                returncode=1,
                cmd=["gh"],
                stderr="Could not resolve to an Issue",
            )
            with pytest.raises(RuntimeError, match="Failed to fetch issue"):
                collapse_plan_in_issue("github.com/owner/repo", 999)

    def test_raises_runtime_error_on_update_failure(self):
        """Test that RuntimeError is raised when gh edit fails."""
        import json
        from unittest.mock import MagicMock, patch

        original_body = f"""{PLAN_START_MARKER}
# Plan
- [ ] Task
{PLAN_END_MARKER}"""

        mock_view_result = MagicMock()
        mock_view_result.stdout = json.dumps({"body": original_body})

        call_count = {"value": 0}

        def mock_run(cmd, **kwargs):
            call_count["value"] += 1
            if call_count["value"] == 1:
                return mock_view_result
            raise subprocess.CalledProcessError(
                returncode=1,
                cmd=["gh"],
                stderr="Permission denied",
            )

        with patch("subprocess.run", side_effect=mock_run):
            with pytest.raises(RuntimeError, match="Failed to update issue"):
                collapse_plan_in_issue("github.com/owner/repo", 123)

    def test_uses_correct_repo_ref_format(self):
        """Test that repo ref is correctly formatted for gh CLI."""
        import json
        from unittest.mock import MagicMock, patch

        original_body = f"""{PLAN_START_MARKER}
# Plan
- [ ] Task
{PLAN_END_MARKER}"""

        mock_view_result = MagicMock()
        mock_view_result.stdout = json.dumps({"body": original_body})

        captured_repo_ref = {"value": None}

        def mock_run(cmd, **kwargs):
            if "view" in cmd:
                return mock_view_result
            if "edit" in cmd:
                for i, arg in enumerate(cmd):
                    if arg == "--repo":
                        captured_repo_ref["value"] = cmd[i + 1]
                        break
                return MagicMock()
            return MagicMock()

        with patch("subprocess.run", side_effect=mock_run):
            collapse_plan_in_issue("github.com/owner/repo", 123)

        # For github.com, should use just owner/repo
        assert captured_repo_ref["value"] == "owner/repo"


@pytest.mark.unit
class TestDirectToImplementFlow:
    """Tests for direct-to-implement flow (auto-triggered planning when no plan exists)."""

    def test_posts_comment_when_no_plan_exists(self):
        """Test that execute() posts explanatory comment when no plan is found in issue."""
        import json
        from unittest.mock import MagicMock, patch

        from src.config import Config
        from src.workflows.base import WorkflowContext

        workflow = ImplementWorkflow()

        mock_config = MagicMock(spec=Config)
        mock_config.safety_allow_appended_tasks = 0

        ctx = WorkflowContext(
            repo="github.com/owner/test-repo",
            issue_number=42,
            issue_title="Test Issue",
            workspace_path="/tmp/worktrees/test",
        )

        # No PR exists initially, then PR is created
        pr_responses = [
            None,  # Initial check
            {"number": 42, "body": "Closes #42\n\n## TASK 1: Test\n- [x] Done"},
            {"number": 42, "body": "Closes #42\n\n## TASK 1: Test\n- [x] Done"},
        ]
        call_count = {"value": 0}

        def mock_get_pr(*_args, **_kwargs):
            result = pr_responses[min(call_count["value"], len(pr_responses) - 1)]
            call_count["value"] += 1
            return result

        # Track subprocess calls
        subprocess_calls = []
        issue_view_call_count = {"value": 0}

        def mock_subprocess_run(cmd, **kwargs):
            subprocess_calls.append(cmd)
            mock_result = MagicMock()
            if "issue" in cmd and "view" in cmd:
                # First call: no plan
                # Second call (after planning): has plan
                issue_view_call_count["value"] += 1
                if issue_view_call_count["value"] == 1:
                    mock_result.stdout = json.dumps(
                        {
                            "title": "Test Issue",
                            "body": "Just a description without plan markers",
                        }
                    )
                else:
                    mock_result.stdout = json.dumps(
                        {
                            "title": "Test Issue",
                            "body": f"{PLAN_START_MARKER}\n## TASK 1: Test\n- [ ] Task\n{PLAN_END_MARKER}",
                        }
                    )
            elif "issue" in cmd and "comment" in cmd:
                # gh issue comment - should be called with explanatory message
                pass
            elif ("git" in cmd and "commit" in cmd) or ("git" in cmd and "push" in cmd):
                # git commit or git push
                pass
            elif "gh" in cmd and "pr" in cmd and "create" in cmd:
                mock_result.stdout = "https://github.com/owner/test-repo/pull/42\n"
            elif "issue" in cmd and "edit" in cmd:
                mock_result.stdout = json.dumps({"body": "collapsed"})
            return mock_result

        with (
            patch.object(workflow, "_get_pr_for_issue", side_effect=mock_get_pr),
            patch.object(workflow, "_run_prompt") as mock_run_prompt,
            patch("src.workflows.implement.subprocess.run", side_effect=mock_subprocess_run),
        ):
            workflow.execute(ctx, mock_config)

        # Verify comment was posted
        comment_calls = [c for c in subprocess_calls if "issue" in c and "comment" in c]
        assert len(comment_calls) == 1
        comment_cmd = comment_calls[0]
        # The comment body should mention auto-triggering planning
        comment_body_idx = comment_cmd.index("--body") + 1
        assert "Implement" in comment_cmd[comment_body_idx]
        assert "implementation plan" in comment_cmd[comment_body_idx].lower()

        # Verify /kiln-create_plan_simple was called
        plan_simple_calls = [
            c for c in mock_run_prompt.call_args_list if "/kiln-create_plan_simple" in c[0][0]
        ]
        assert len(plan_simple_calls) == 1

    def test_validates_checkboxes_after_plan_creation(self):
        """Test that execute() validates plan has checkboxes before proceeding."""
        import json
        from unittest.mock import MagicMock, patch

        from src.config import Config
        from src.workflows.base import WorkflowContext

        workflow = ImplementWorkflow()

        mock_config = MagicMock(spec=Config)
        mock_config.safety_allow_appended_tasks = 0

        ctx = WorkflowContext(
            repo="github.com/owner/test-repo",
            issue_number=42,
            issue_title="Test Issue",
            workspace_path="/tmp/worktrees/test",
        )

        def mock_subprocess_run(cmd, **kwargs):
            mock_result = MagicMock()
            if "issue" in cmd and "view" in cmd:
                # Plan exists but has NO checkboxes
                mock_result.stdout = json.dumps(
                    {
                        "title": "Test Issue",
                        "body": f"{PLAN_START_MARKER}\n## TASK 1: Test\nNo checkboxes here\n{PLAN_END_MARKER}",
                    }
                )
            return mock_result

        with (
            patch.object(workflow, "_get_pr_for_issue", return_value=None),
            patch("src.workflows.implement.subprocess.run", side_effect=mock_subprocess_run),
            pytest.raises(RuntimeError, match="contains no checkboxes"),
        ):
            workflow.execute(ctx, mock_config)


@pytest.mark.unit
class TestImplementWorkflow:
    """Tests for ImplementWorkflow class."""

    def test_implement_workflow_init_returns_empty_list(self, workflow_context):
        """Test that init() returns an empty list.

        ImplementWorkflow uses execute() instead of init() for its custom loop logic,
        so init() should return an empty list.
        """
        workflow = ImplementWorkflow()
        prompts = workflow.init(workflow_context)

        assert isinstance(prompts, list)
        assert len(prompts) == 0

    def test_get_pr_for_issue_closes_keyword(self):
        """Test that _get_pr_for_issue finds PR with 'closes #N' keyword."""
        import json
        from unittest.mock import MagicMock, patch

        workflow = ImplementWorkflow()
        mock_result = MagicMock()
        mock_result.stdout = json.dumps(
            [{"number": 42, "body": "This PR closes #123\n\nSome description"}]
        )

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = workflow._get_pr_for_issue("github.com/owner/repo", 123)

        assert result is not None
        assert result["number"] == 42
        mock_run.assert_called_once()

    def test_get_pr_for_issue_fixes_keyword(self):
        """Test that _get_pr_for_issue finds PR with 'fixes #N' keyword."""
        import json
        from unittest.mock import MagicMock, patch

        workflow = ImplementWorkflow()
        mock_result = MagicMock()
        mock_result.stdout = json.dumps([{"number": 55, "body": "Fixes #99 - bug fix"}])

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = workflow._get_pr_for_issue("github.com/owner/repo", 99)

        assert result is not None
        assert result["number"] == 55
        mock_run.assert_called_once()

    def test_get_pr_for_issue_resolves_keyword(self):
        """Test that _get_pr_for_issue finds PR with 'resolves #N' keyword."""
        import json
        from unittest.mock import MagicMock, patch

        workflow = ImplementWorkflow()
        mock_result = MagicMock()
        mock_result.stdout = json.dumps([{"number": 77, "body": "RESOLVES #200 in uppercase"}])

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = workflow._get_pr_for_issue("github.com/owner/repo", 200)

        assert result is not None
        assert result["number"] == 77
        mock_run.assert_called_once()

    def test_get_pr_for_issue_no_matching_pr(self):
        """Test that _get_pr_for_issue returns None when no PR matches."""
        import json
        from unittest.mock import MagicMock, patch

        workflow = ImplementWorkflow()
        mock_result = MagicMock()
        # PR exists but doesn't link to the issue we're looking for
        mock_result.stdout = json.dumps([{"number": 42, "body": "Closes #999 - different issue"}])

        with patch("subprocess.run", return_value=mock_result):
            result = workflow._get_pr_for_issue("github.com/owner/repo", 123)

        assert result is None

    def test_get_pr_for_issue_subprocess_failure(self):
        """Test that _get_pr_for_issue returns None on subprocess failure."""
        import subprocess
        from unittest.mock import patch

        workflow = ImplementWorkflow()

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                returncode=1, cmd=["gh"], stderr="error"
            )
            result = workflow._get_pr_for_issue("github.com/owner/repo", 123)

        assert result is None

    def test_get_pr_for_issue_json_parse_error(self):
        """Test that _get_pr_for_issue returns None on JSON parse error."""
        from unittest.mock import MagicMock, patch

        workflow = ImplementWorkflow()
        mock_result = MagicMock()
        mock_result.stdout = "not valid json"

        with patch("subprocess.run", return_value=mock_result):
            result = workflow._get_pr_for_issue("github.com/owner/repo", 123)

        assert result is None

    def test_get_pr_for_issue_empty_list_response(self):
        """Test that _get_pr_for_issue returns None for empty PR list."""
        from unittest.mock import MagicMock, patch

        workflow = ImplementWorkflow()
        mock_result = MagicMock()
        mock_result.stdout = "[]"

        with patch("subprocess.run", return_value=mock_result):
            result = workflow._get_pr_for_issue("github.com/owner/repo", 123)

        assert result is None

    def test_mark_pr_ready_success(self):
        """Test that _mark_pr_ready calls gh pr ready with correct arguments."""
        from unittest.mock import MagicMock, patch

        workflow = ImplementWorkflow()
        mock_result = MagicMock()

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            workflow._mark_pr_ready("github.com/owner/repo", 42)

        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args.args[0] == [
            "gh",
            "pr",
            "ready",
            "42",
            "--repo",
            "https://github.com/owner/repo",
        ]
        assert call_args.kwargs["capture_output"] is True
        assert call_args.kwargs["text"] is True
        assert call_args.kwargs["check"] is True
        # For github.com, get_gh_env returns empty dict, so env should be os.environ
        assert "env" in call_args.kwargs

    def test_mark_pr_ready_failure_logs_warning_no_raise(self):
        """Test that _mark_pr_ready logs warning on failure but doesn't raise."""
        import subprocess
        from unittest.mock import patch

        workflow = ImplementWorkflow()

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                returncode=1, cmd=["gh"], stderr="PR is already ready"
            )
            # Should not raise - just logs a warning
            workflow._mark_pr_ready("github.com/owner/repo", 42)

    def test_run_prompt_uses_stage_model_from_constant(self, workflow_context):
        """Test that _run_prompt selects model from STAGE_MODELS for the stage."""
        from unittest.mock import MagicMock, patch

        from src.config import Config

        workflow = ImplementWorkflow()

        mock_config = MagicMock(spec=Config)

        test_models = {
            "implement": "sonnet",
            "prepare_implementation": "haiku",
            "Implement": "opus",
        }

        with (
            patch("src.workflows.implement.run_claude") as mock_run_claude,
            patch("src.workflows.implement.STAGE_MODELS", test_models),
        ):
            workflow._run_prompt(
                prompt="/kiln-implement_github for issue",
                ctx=workflow_context,
                config=mock_config,
                stage_name="implement",
            )

            mock_run_claude.assert_called_once()
            call_kwargs = mock_run_claude.call_args
            assert call_kwargs.kwargs["model"] == "sonnet"

    def test_run_prompt_falls_back_to_implement_model(self, workflow_context):
        """Test that _run_prompt falls back to 'Implement' model when stage not found."""
        from unittest.mock import MagicMock, patch

        from src.config import Config

        workflow = ImplementWorkflow()

        mock_config = MagicMock(spec=Config)

        test_models = {
            "Implement": "opus",
        }

        with (
            patch("src.workflows.implement.run_claude") as mock_run_claude,
            patch("src.workflows.implement.STAGE_MODELS", test_models),
        ):
            workflow._run_prompt(
                prompt="/kiln-implement_github for issue",
                ctx=workflow_context,
                config=mock_config,
                stage_name="unknown_stage",
            )

            mock_run_claude.assert_called_once()
            call_kwargs = mock_run_claude.call_args
            assert call_kwargs.kwargs["model"] == "opus"

    def test_execute_creates_pr_when_none_exists(self, workflow_context):
        """Test that execute() creates PR programmatically when no PR exists."""
        import json
        from unittest.mock import MagicMock, patch

        from src.config import Config

        workflow = ImplementWorkflow()

        # Mock config
        mock_config = MagicMock(spec=Config)

        mock_config.safety_allow_appended_tasks = 0
        mock_config.prepare_pr_delay = 10

        # First call: no PR found
        # Second call: PR found after programmatic creation
        pr_responses = [
            None,  # Initial check - no PR
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1: Test\n- [x] Done",
            },  # After programmatic PR creation
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1: Test\n- [x] Done",
            },  # In loop (check state)
        ]
        call_count = {"value": 0}

        def mock_get_pr_create(*_args, **_kwargs):
            result = pr_responses[min(call_count["value"], len(pr_responses) - 1)]
            call_count["value"] += 1
            return result

        # Mock subprocess calls for programmatic PR creation
        mock_subprocess_results = []

        def mock_subprocess_run(cmd, **kwargs):
            mock_result = MagicMock()
            if "issue" in cmd and "view" in cmd:
                # extract_plan_from_issue
                mock_result.stdout = json.dumps(
                    {
                        "title": "Test Issue",
                        "body": f"{PLAN_START_MARKER}\n## TASK 1: Test\n- [ ] Task\n{PLAN_END_MARKER}",
                    }
                )
            elif "git" in cmd and "commit" in cmd:
                # git commit --allow-empty
                pass
            elif "git" in cmd and "push" in cmd:
                # git push
                pass
            elif "gh" in cmd and "pr" in cmd and "create" in cmd:
                # gh pr create
                mock_result.stdout = "https://github.com/owner/test-repo/pull/42\n"
            elif "issue" in cmd and "edit" in cmd:
                # collapse_plan_in_issue (may also be called as part of flow)
                mock_result.stdout = json.dumps({"body": "collapsed"})
            mock_subprocess_results.append(cmd)
            return mock_result

        with (
            patch.object(workflow, "_get_pr_for_issue", side_effect=mock_get_pr_create),
            patch("src.workflows.implement.subprocess.run", side_effect=mock_subprocess_run),
            patch("src.workflows.implement.time.sleep"),
        ):
            workflow.execute(workflow_context, mock_config)

        # Verify programmatic PR creation was used (gh pr create was called)
        pr_create_calls = [
            c for c in mock_subprocess_results if "gh" in c and "pr" in c and "create" in c
        ]
        assert len(pr_create_calls) == 1

    def test_execute_fails_when_pr_lookup_fails_after_creation(self, workflow_context):
        """Test that execute() raises RuntimeError when PR is created but lookup never finds it."""
        import json
        from unittest.mock import MagicMock, patch

        from src.config import Config

        workflow = ImplementWorkflow()

        # Mock config with prepare_pr_delay
        mock_config = MagicMock(spec=Config)
        mock_config.prepare_pr_delay = 10

        def mock_subprocess_run(cmd, **kwargs):
            mock_result = MagicMock()
            if "issue" in cmd and "view" in cmd:
                # extract_plan_from_issue
                mock_result.stdout = json.dumps(
                    {
                        "title": "Test Issue",
                        "body": f"{PLAN_START_MARKER}\n## TASK 1: Test\n- [ ] Task\n{PLAN_END_MARKER}",
                    }
                )
            elif "git" in cmd and "commit" in cmd or "git" in cmd and "push" in cmd:
                pass
            elif "gh" in cmd and "pr" in cmd and "create" in cmd:
                # gh pr create succeeds
                mock_result.stdout = "https://github.com/owner/test-repo/pull/42\n"
            elif "issue" in cmd and "edit" in cmd:
                mock_result.stdout = json.dumps({"body": "collapsed"})
            return mock_result

        # PR lookup always returns None (simulating GitHub search API lag)
        with (
            patch.object(workflow, "_get_pr_for_issue", return_value=None),
            patch("src.workflows.implement.subprocess.run", side_effect=mock_subprocess_run),
            patch("src.workflows.implement.time.sleep") as mock_sleep,
            pytest.raises(RuntimeError, match="was created.*but could not be found"),
        ):
            workflow.execute(workflow_context, mock_config)

        # Verify sleep was called with exponential delays: 10, 30, 90
        assert mock_sleep.call_count == 3
        mock_sleep.assert_any_call(10)  # 10 * 1
        mock_sleep.assert_any_call(30)  # 10 * 3
        mock_sleep.assert_any_call(90)  # 10 * 9

    def test_execute_max_iterations_based_on_task_count(self, workflow_context):
        """Test that execute() sets max_iterations based on TASK count in PR body.

        With no progress made, stall detection raises ImplementationIncompleteError.
        """
        from unittest.mock import MagicMock, patch

        from src.config import Config
        from src.workflows.implement import ImplementationIncompleteError

        workflow = ImplementWorkflow()

        mock_config = MagicMock(spec=Config)

        mock_config.safety_allow_appended_tasks = 0

        # PR with 3 TASKs and always-incomplete checkboxes
        pr_info = {
            "number": 42,
            "body": """Closes #42

## TASK 1: First task
- [ ] Subtask 1

## TASK 2: Second task
- [ ] Subtask 2

## TASK 3: Third task
- [ ] Subtask 3
""",
        }

        iterations_run = {"count": 0}

        def mock_run_prompt(*args, **kwargs):
            iterations_run["count"] += 1

        with (
            patch.object(workflow, "_get_pr_for_issue", return_value=pr_info),
            patch.object(workflow, "_run_prompt", side_effect=mock_run_prompt),
            pytest.raises(ImplementationIncompleteError) as exc_info,
        ):
            workflow.execute(workflow_context, mock_config)

        # With 3 TASKs, loop continues indefinitely until stall detection kicks in
        # Iteration 1: 0/3 complete, last_completed=-1, no stall (stall_count stays 0), run prompt, last_completed=0
        # Iteration 2: 0/3 complete, last_completed=0, stall_count=1, run prompt
        # Iteration 3: 0/3 complete, last_completed=0, stall_count=2 (>=MAX_STALL_COUNT), raises exception BEFORE running
        # So 2 iterations run (stall detected on iteration 3 before running)
        assert iterations_run["count"] == 2
        assert exc_info.value.reason == "stall"

    def test_execute_stall_detection(self, workflow_context):
        """Test that execute() raises ImplementationIncompleteError after MAX_STALL_COUNT iterations with no progress."""
        from unittest.mock import MagicMock, patch

        from src.config import Config
        from src.workflows.implement import (
            MAX_STALL_COUNT,
            ImplementationIncompleteError,
        )

        workflow = ImplementWorkflow()

        mock_config = MagicMock(spec=Config)

        mock_config.safety_allow_appended_tasks = 0

        # PR with 5 TASKs that never makes progress after first task
        pr_info = {
            "number": 42,
            "body": """Closes #42

## TASK 1: Test 1
- [x] Done task

## TASK 2: Test 2
- [ ] Pending task 1

## TASK 3: Test 3
- [ ] Pending task 2

## TASK 4: Test 4
- [ ] Pending task 3

## TASK 5: Test 5
- [ ] Pending task 4
""",
        }

        iterations_run = {"count": 0}

        def mock_run_prompt(*_args, **_kwargs):
            iterations_run["count"] += 1

        with (
            patch.object(workflow, "_get_pr_for_issue", return_value=pr_info),
            patch.object(workflow, "_run_prompt", side_effect=mock_run_prompt),
            pytest.raises(ImplementationIncompleteError) as exc_info,
        ):
            workflow.execute(workflow_context, mock_config)

        # Loop continues indefinitely until stall detection:
        # Iteration 1: completed=1, last_completed=-1, no stall (stall_count stays 0), run prompt, last_completed=1
        # Iteration 2: completed=1, last_completed=1, stall_count=1, run prompt
        # Iteration 3: completed=1, last_completed=1, stall_count=2 (>=MAX_STALL_COUNT), raises exception BEFORE running
        # So MAX_STALL_COUNT iterations run (stall detected on iteration 3 before running)
        assert iterations_run["count"] == MAX_STALL_COUNT
        assert exc_info.value.reason == "stall"

    def test_execute_completion_detection(self, workflow_context):
        """Test that execute() exits and marks PR ready when all checkboxes complete."""
        from unittest.mock import MagicMock, patch

        from src.config import Config

        workflow = ImplementWorkflow()

        mock_config = MagicMock(spec=Config)

        mock_config.safety_allow_appended_tasks = 0

        # PR with 2 TASKs, starts incomplete, then becomes complete after 1 implementation
        pr_responses = [
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1: Test 1\n- [ ] Task 1\n\n## TASK 2: Test 2\n- [ ] Task 2",
            },  # Initial check
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1: Test 1\n- [ ] Task 1\n\n## TASK 2: Test 2\n- [ ] Task 2",
            },  # Loop iteration 1: get PR state
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1: Test 1\n- [x] Task 1\n\n## TASK 2: Test 2\n- [x] Task 2",
            },  # Loop iteration 2: all complete
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1: Test 1\n- [x] Task 1\n\n## TASK 2: Test 2\n- [x] Task 2",
            },  # Final check
        ]
        call_count = {"value": 0}

        def mock_get_pr_completion(*_args, **_kwargs):
            result = pr_responses[min(call_count["value"], len(pr_responses) - 1)]
            call_count["value"] += 1
            return result

        with (
            patch.object(workflow, "_get_pr_for_issue", side_effect=mock_get_pr_completion),
            patch.object(workflow, "_run_prompt") as mock_run,
            patch.object(workflow, "_mark_pr_ready") as mock_ready,
        ):
            workflow.execute(workflow_context, mock_config)

        # Implementation should run once, then completion detected on second check
        assert mock_run.call_count == 1
        # PR should be marked ready
        mock_ready.assert_called_once_with(workflow_context.repo, 42)

    def test_execute_pr_disappearance_raises_error(self, workflow_context):
        """Test that execute() raises RuntimeError if PR disappears during execution."""
        from unittest.mock import MagicMock, patch

        from src.config import Config

        workflow = ImplementWorkflow()

        mock_config = MagicMock(spec=Config)

        mock_config.safety_allow_appended_tasks = 0

        # PR exists initially with 2 TASKs, then disappears in loop
        pr_responses = [
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1: Test 1\n- [ ] Task 1\n\n## TASK 2: Test 2\n- [ ] Task 2",
            },  # Initial check
            None,  # Disappeared in loop
        ]
        call_count = {"value": 0}

        def mock_get_pr_disappear(*_args, **_kwargs):
            result = pr_responses[min(call_count["value"], len(pr_responses) - 1)]
            call_count["value"] += 1
            return result

        with (
            patch.object(workflow, "_get_pr_for_issue", side_effect=mock_get_pr_disappear),
            patch.object(workflow, "_run_prompt"),
            pytest.raises(RuntimeError, match="PR disappeared"),
        ):
            workflow.execute(workflow_context, mock_config)

    def test_execute_no_tasks_raises_error(self, workflow_context):
        """Test that execute() raises ImplementationIncompleteError when no checkbox tasks found."""
        from unittest.mock import MagicMock, patch

        from src.config import Config
        from src.workflows.implement import ImplementationIncompleteError

        workflow = ImplementWorkflow()

        mock_config = MagicMock(spec=Config)

        mock_config.safety_allow_appended_tasks = 0

        # PR with no checkbox tasks
        pr_info = {
            "number": 42,
            "body": """Closes #42

## TASK 1: Test task
This task has no checkboxes.

Some other content here.
""",
        }

        with (
            patch.object(workflow, "_get_pr_for_issue", return_value=pr_info),
            patch.object(workflow, "_run_prompt"),
            pytest.raises(ImplementationIncompleteError) as exc_info,
        ):
            workflow.execute(workflow_context, mock_config)

        assert exc_info.value.reason == "no_tasks"
        assert "No checkbox tasks found" in str(exc_info.value)

    def test_execute_stall_raises_error(self, workflow_context):
        """Test that execute() raises ImplementationIncompleteError when no progress made.

        With the current implementation, stall detection (reason="stall") triggers before
        max_iterations check when no progress is made, since MAX_STALL_COUNT=2 is reached first.
        """
        from unittest.mock import MagicMock, patch

        from src.config import Config
        from src.workflows.implement import ImplementationIncompleteError

        workflow = ImplementWorkflow()

        mock_config = MagicMock(spec=Config)

        mock_config.safety_allow_appended_tasks = 0

        # PR with 1 TASK that never makes progress
        pr_info = {
            "number": 42,
            "body": "Closes #42\n\n## TASK 1: Test\n- [ ] Task 1",
        }

        with (
            patch.object(workflow, "_get_pr_for_issue", return_value=pr_info),
            patch.object(workflow, "_run_prompt"),
            pytest.raises(ImplementationIncompleteError) as exc_info,
        ):
            workflow.execute(workflow_context, mock_config)

        assert exc_info.value.reason == "stall"
        assert "No progress" in str(exc_info.value)

    def test_execute_continues_past_max_iterations_when_progress_made(self, workflow_context):
        """Test that execute() continues past max_iterations when progress is made."""
        from unittest.mock import MagicMock, patch

        from src.config import Config

        workflow = ImplementWorkflow()

        mock_config = MagicMock(spec=Config)

        mock_config.safety_allow_appended_tasks = 0  # No limit

        # PR with 2 TASKs, progress made on each iteration, completes on iteration 4
        # This tests that the loop continues past max_iterations=2 when progress is made
        # Call sequence: initial check, iter1 check, iter2 check, iter3 check, iter4 check, final check
        pr_responses = [
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1\n- [ ] A\n- [ ] B\n\n## TASK 2\n- [ ] C\n- [ ] D",
            },  # Initial check: 0/4
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1\n- [ ] A\n- [ ] B\n\n## TASK 2\n- [ ] C\n- [ ] D",
            },  # Iter 1 check: 0/4 -> run prompt
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1\n- [x] A\n- [ ] B\n\n## TASK 2\n- [ ] C\n- [ ] D",
            },  # Iter 2 check: 1/4 -> run prompt
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1\n- [x] A\n- [x] B\n\n## TASK 2\n- [ ] C\n- [ ] D",
            },  # Iter 3 check: 2/4 -> run prompt
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1\n- [x] A\n- [x] B\n\n## TASK 2\n- [x] C\n- [x] D",
            },  # Iter 4 check: 4/4 complete -> exit
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1\n- [x] A\n- [x] B\n\n## TASK 2\n- [x] C\n- [x] D",
            },  # Final check
        ]
        call_count = {"value": 0}

        def mock_get_pr(*_args, **_kwargs):
            result = pr_responses[min(call_count["value"], len(pr_responses) - 1)]
            call_count["value"] += 1
            return result

        with (
            patch.object(workflow, "_get_pr_for_issue", side_effect=mock_get_pr),
            patch.object(workflow, "_run_prompt") as mock_run,
            patch.object(workflow, "_mark_pr_ready") as mock_ready,
        ):
            workflow.execute(workflow_context, mock_config)

        # With 2 TASKs, max_iterations_estimate=2, but loop should continue to iteration 3
        # because progress is being made each iteration (checked at iteration 4 it's complete)
        # Iter 1: 0/4 -> run, Iter 2: 1/4 -> run, Iter 3: 2/4 -> run, Iter 4: 4/4 -> done
        assert mock_run.call_count == 3
        mock_ready.assert_called_once()

    def test_execute_stall_detection_with_overrun(self, workflow_context):
        """Test stall detection after continuing past max_iterations (Scenario 1).

        Scenario: PR has 3 TASKs, 2 completed, 1 never completes.
        The loop should continue past max_iterations=3 but eventually stall and raise exception.
        """
        from unittest.mock import MagicMock, patch

        from src.config import Config
        from src.workflows.implement import (
            MAX_STALL_COUNT,
            ImplementationIncompleteError,
        )

        workflow = ImplementWorkflow()

        mock_config = MagicMock(spec=Config)

        mock_config.safety_allow_appended_tasks = 0  # No limit

        # PR with 3 TASKs, 2 completed, 1 never completes (stays at 2/3)
        pr_info = {
            "number": 42,
            "body": """Closes #42

## TASK 1: Test 1
- [x] Done

## TASK 2: Test 2
- [x] Done

## TASK 3: Test 3
- [ ] Never completes
""",
        }

        iterations_run = {"count": 0}

        def mock_run_prompt(*_args, **_kwargs):
            iterations_run["count"] += 1

        with (
            patch.object(workflow, "_get_pr_for_issue", return_value=pr_info),
            patch.object(workflow, "_run_prompt", side_effect=mock_run_prompt),
            pytest.raises(ImplementationIncompleteError) as exc_info,
        ):
            workflow.execute(workflow_context, mock_config)

        # Iteration 1: 2/3 complete, last_completed=-1, no stall (progress!), run prompt, last=2
        # Iteration 2: 2/3 complete, last_completed=2, stall_count=1, run prompt
        # Iteration 3: 2/3 complete, last_completed=2, stall_count=2 (>=MAX_STALL_COUNT), raises exception BEFORE running
        # So MAX_STALL_COUNT iterations run
        assert iterations_run["count"] == MAX_STALL_COUNT
        assert exc_info.value.reason == "stall"

    def test_execute_successful_overrun(self, workflow_context):
        """Test successful completion after continuing past max_iterations (Scenario 2).

        Scenario: PR has 3 TASKs, completes on iteration 4 (past max_iterations=3).
        """
        from unittest.mock import MagicMock, patch

        from src.config import Config

        workflow = ImplementWorkflow()

        mock_config = MagicMock(spec=Config)

        mock_config.safety_allow_appended_tasks = 0  # No limit

        # PR with 3 TASKs, completes on iteration 4 (past max_iterations=3)
        # Call sequence: initial, iter1, iter2, iter3, iter4 (detects completion), final
        pr_responses = [
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1\n- [ ] A\n\n## TASK 2\n- [ ] B\n\n## TASK 3\n- [ ] C",
            },  # Initial: 0/3
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1\n- [ ] A\n\n## TASK 2\n- [ ] B\n\n## TASK 3\n- [ ] C",
            },  # Iter 1: 0/3 -> run prompt
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1\n- [x] A\n\n## TASK 2\n- [ ] B\n\n## TASK 3\n- [ ] C",
            },  # Iter 2: 1/3 -> run prompt
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1\n- [x] A\n\n## TASK 2\n- [x] B\n\n## TASK 3\n- [ ] C",
            },  # Iter 3: 2/3 -> run prompt (past max_iterations=3)
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1\n- [x] A\n\n## TASK 2\n- [x] B\n\n## TASK 3\n- [x] C",
            },  # Iter 4: 3/3 complete -> exit
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1\n- [x] A\n\n## TASK 2\n- [x] B\n\n## TASK 3\n- [x] C",
            },  # Final check
        ]
        call_count = {"value": 0}

        def mock_get_pr(*_args, **_kwargs):
            result = pr_responses[min(call_count["value"], len(pr_responses) - 1)]
            call_count["value"] += 1
            return result

        with (
            patch.object(workflow, "_get_pr_for_issue", side_effect=mock_get_pr),
            patch.object(workflow, "_run_prompt") as mock_run,
            patch.object(workflow, "_mark_pr_ready") as mock_ready,
        ):
            workflow.execute(workflow_context, mock_config)

        # Should run 3 iterations, then detect completion on iteration 4
        # This proves we continued past max_iterations=3
        assert mock_run.call_count == 3
        mock_ready.assert_called_once()

    def test_execute_task_growth_within_limit(self, workflow_context):
        """Test that loop continues when appended TASKs are within safety limit."""
        from unittest.mock import MagicMock, patch

        from src.config import Config

        workflow = ImplementWorkflow()

        mock_config = MagicMock(spec=Config)

        mock_config.safety_allow_appended_tasks = 2  # Allow up to 2 appended TASKs

        # Start with 2 TASKs, add 1 more (within limit of 2)
        # Call sequence: initial, iter1, iter2, iter3 (detects completion), final
        pr_responses = [
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1\n- [ ] A\n\n## TASK 2\n- [ ] B",
            },  # Initial: 2 TASKs, 0/2
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1\n- [ ] A\n\n## TASK 2\n- [ ] B",
            },  # Iter 1: 2 TASKs, 0/2 -> run prompt
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1\n- [x] A\n\n## TASK 2\n- [ ] B\n\n## TASK 3\n- [ ] C",
            },  # Iter 2: 3 TASKs (+1, within limit), 1/3 -> run prompt
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1\n- [x] A\n\n## TASK 2\n- [x] B\n\n## TASK 3\n- [x] C",
            },  # Iter 3: 3/3 complete -> exit
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1\n- [x] A\n\n## TASK 2\n- [x] B\n\n## TASK 3\n- [x] C",
            },  # Final check
        ]
        call_count = {"value": 0}

        def mock_get_pr(*_args, **_kwargs):
            result = pr_responses[min(call_count["value"], len(pr_responses) - 1)]
            call_count["value"] += 1
            return result

        with (
            patch.object(workflow, "_get_pr_for_issue", side_effect=mock_get_pr),
            patch.object(workflow, "_run_prompt") as mock_run,
            patch.object(workflow, "_mark_pr_ready") as mock_ready,
        ):
            workflow.execute(workflow_context, mock_config)

        # Should run 2 iterations (1 appended TASK is within limit of 2)
        # Then detect completion on iteration 3
        assert mock_run.call_count == 2
        mock_ready.assert_called_once()

    def test_execute_task_growth_exceeds_limit(self, workflow_context):
        """Test that loop exits when appended TASKs exceed safety limit."""
        from unittest.mock import MagicMock, patch

        from src.config import Config

        workflow = ImplementWorkflow()

        mock_config = MagicMock(spec=Config)

        mock_config.safety_allow_appended_tasks = 2  # Allow max 2 appended TASKs

        # Start with 3 TASKs, then add 3 more (exceeds limit of 2)
        pr_responses = [
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1\n- [ ] A\n\n## TASK 2\n- [ ] B\n\n## TASK 3\n- [ ] C",
            },  # Initial: 3 TASKs
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1\n- [ ] A\n\n## TASK 2\n- [ ] B\n\n## TASK 3\n- [ ] C",
            },  # Iter 1: 3 TASKs
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1\n- [x] A\n\n## TASK 2\n- [ ] B\n\n## TASK 3\n- [ ] C\n\n## TASK 4\n- [ ] D\n\n## TASK 5\n- [ ] E\n\n## TASK 6\n- [ ] F",
            },  # Iter 2: 6 TASKs (+3, exceeds limit)
        ]
        call_count = {"value": 0}

        def mock_get_pr(*_args, **_kwargs):
            result = pr_responses[min(call_count["value"], len(pr_responses) - 1)]
            call_count["value"] += 1
            return result

        with (
            patch.object(workflow, "_get_pr_for_issue", side_effect=mock_get_pr),
            patch.object(workflow, "_run_prompt") as mock_run,
            patch.object(workflow, "_mark_pr_ready") as mock_ready,
        ):
            workflow.execute(workflow_context, mock_config)

        # Should run only 1 iteration, then exit on iteration 2 due to safety limit
        assert mock_run.call_count == 1
        # PR should NOT be marked ready (incomplete due to safety exit)
        mock_ready.assert_not_called()

    def test_execute_passes_base_flag_when_parent_branch_set(self, workflow_context):
        """Test that execute() passes --base flag to gh pr create when parent_branch is set."""
        import json
        from unittest.mock import MagicMock, patch

        from src.config import Config

        workflow = ImplementWorkflow()

        # Mock config
        mock_config = MagicMock(spec=Config)

        mock_config.safety_allow_appended_tasks = 0

        # Create context with parent_branch set
        ctx_with_parent = WorkflowContext(
            repo="github.com/owner/test-repo",
            issue_number=42,
            issue_title="Test Issue",
            workspace_path="/tmp/worktrees/test",
            parent_branch="feature/parent-branch",
        )

        # First call: no PR found
        # Second call: PR found after programmatic creation
        pr_responses = [
            None,  # Initial check - no PR
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1: Test\n- [x] Done",
            },  # After programmatic PR creation
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1: Test\n- [x] Done",
            },  # In loop (check state)
        ]
        call_count = {"value": 0}

        def mock_get_pr_create(*_args, **_kwargs):
            result = pr_responses[min(call_count["value"], len(pr_responses) - 1)]
            call_count["value"] += 1
            return result

        captured_pr_create_cmd = {"value": None}

        def mock_subprocess_run(cmd, **kwargs):
            mock_result = MagicMock()
            if "issue" in cmd and "view" in cmd:
                # extract_plan_from_issue
                mock_result.stdout = json.dumps(
                    {
                        "title": "Test Issue",
                        "body": f"{PLAN_START_MARKER}\n## TASK 1: Test\n- [ ] Task\n{PLAN_END_MARKER}",
                    }
                )
            elif "git" in cmd and "commit" in cmd:
                # git commit --allow-empty
                pass
            elif "git" in cmd and "push" in cmd:
                # git push
                pass
            elif "gh" in cmd and "pr" in cmd and "create" in cmd:
                # gh pr create - capture the command
                captured_pr_create_cmd["value"] = cmd
                mock_result.stdout = "https://github.com/owner/test-repo/pull/42\n"
            elif "issue" in cmd and "edit" in cmd:
                # collapse_plan_in_issue
                mock_result.stdout = json.dumps({"body": "collapsed"})
            return mock_result

        with (
            patch.object(workflow, "_get_pr_for_issue", side_effect=mock_get_pr_create),
            patch("src.workflows.implement.subprocess.run", side_effect=mock_subprocess_run),
        ):
            workflow.execute(ctx_with_parent, mock_config)

        # Verify --base flag was passed to gh pr create
        assert captured_pr_create_cmd["value"] is not None
        assert "--base" in captured_pr_create_cmd["value"]
        assert "feature/parent-branch" in captured_pr_create_cmd["value"]

    def test_execute_no_base_flag_when_parent_branch_not_set(self, workflow_context):
        """Test that execute() does NOT pass --base flag when parent_branch is not set."""
        import json
        from unittest.mock import MagicMock, patch

        from src.config import Config

        workflow = ImplementWorkflow()

        # Mock config
        mock_config = MagicMock(spec=Config)

        mock_config.safety_allow_appended_tasks = 0

        # workflow_context fixture does NOT have parent_branch set

        # First call: no PR found
        # Second call: PR found after programmatic creation
        pr_responses = [
            None,  # Initial check - no PR
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1: Test\n- [x] Done",
            },  # After programmatic PR creation
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1: Test\n- [x] Done",
            },  # In loop (check state)
        ]
        call_count = {"value": 0}

        def mock_get_pr_create(*_args, **_kwargs):
            result = pr_responses[min(call_count["value"], len(pr_responses) - 1)]
            call_count["value"] += 1
            return result

        captured_pr_create_cmd = {"value": None}

        def mock_subprocess_run(cmd, **kwargs):
            mock_result = MagicMock()
            if "issue" in cmd and "view" in cmd:
                # extract_plan_from_issue
                mock_result.stdout = json.dumps(
                    {
                        "title": "Test Issue",
                        "body": f"{PLAN_START_MARKER}\n## TASK 1: Test\n- [ ] Task\n{PLAN_END_MARKER}",
                    }
                )
            elif "git" in cmd and "commit" in cmd:
                # git commit --allow-empty
                pass
            elif "git" in cmd and "push" in cmd:
                # git push
                pass
            elif "gh" in cmd and "pr" in cmd and "create" in cmd:
                # gh pr create - capture the command
                captured_pr_create_cmd["value"] = cmd
                mock_result.stdout = "https://github.com/owner/test-repo/pull/42\n"
            elif "issue" in cmd and "edit" in cmd:
                # collapse_plan_in_issue
                mock_result.stdout = json.dumps({"body": "collapsed"})
            return mock_result

        with (
            patch.object(workflow, "_get_pr_for_issue", side_effect=mock_get_pr_create),
            patch("src.workflows.implement.subprocess.run", side_effect=mock_subprocess_run),
        ):
            workflow.execute(workflow_context, mock_config)

        # Verify --base flag was NOT passed to gh pr create
        assert captured_pr_create_cmd["value"] is not None
        assert "--base" not in captured_pr_create_cmd["value"]

    def test_get_pr_for_issue_raises_network_error_on_tls_timeout(self):
        """Test that _get_pr_for_issue raises NetworkError on TLS timeout."""
        from unittest.mock import patch

        workflow = ImplementWorkflow()

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                returncode=1,
                cmd=["gh"],
                stderr="net/http: TLS handshake timeout",
            )
            with pytest.raises(NetworkError, match="Network error getting PR"):
                workflow._get_pr_for_issue("github.com/owner/repo", 123)

    def test_get_pr_for_issue_raises_network_error_on_connection_refused(self):
        """Test that _get_pr_for_issue raises NetworkError on connection refused."""
        from unittest.mock import patch

        workflow = ImplementWorkflow()

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                returncode=1,
                cmd=["gh"],
                stderr="dial tcp: connection refused",
            )
            with pytest.raises(NetworkError, match="Network error getting PR"):
                workflow._get_pr_for_issue("github.com/owner/repo", 123)

    def test_get_pr_for_issue_returns_none_on_non_network_error(self):
        """Test that _get_pr_for_issue returns None on non-network subprocess errors."""
        from unittest.mock import patch

        workflow = ImplementWorkflow()

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                returncode=1,
                cmd=["gh"],
                stderr="GraphQL: Could not resolve to a Repository",
            )
            result = workflow._get_pr_for_issue("github.com/owner/repo", 123)

        assert result is None

    def test_retry_with_backoff_retries_on_network_error(self):
        """Test that _retry_with_backoff retries when NetworkError is raised."""
        from unittest.mock import patch

        call_count = [0]

        def mock_func():
            call_count[0] += 1
            if call_count[0] < 3:
                raise NetworkError("Transient error")
            return "success"

        with patch("src.workflows.implement.time.sleep"):
            result = _retry_with_backoff(mock_func, max_attempts=3)

        assert result == "success"
        assert call_count[0] == 3

    def test_retry_with_backoff_raises_after_max_attempts(self):
        """Test that _retry_with_backoff raises NetworkError after max attempts."""
        from unittest.mock import patch

        def always_fail():
            raise NetworkError("Permanent failure")

        with patch("src.workflows.implement.time.sleep"):
            with pytest.raises(NetworkError, match="failed after 3 attempts"):
                _retry_with_backoff(always_fail, max_attempts=3, description="test operation")

    def test_execute_uses_exponential_delay_with_config_value(self, workflow_context):
        """Test that execute() uses exponential delays based on config.prepare_pr_delay.

        The delay should be: prepare_pr_delay * 1, prepare_pr_delay * 3, prepare_pr_delay * 9
        for lookup attempts 1, 2, 3 respectively.
        """
        import json
        from unittest.mock import MagicMock, patch

        from src.config import Config

        workflow = ImplementWorkflow()

        # Use a custom delay value to verify the config is being used
        mock_config = MagicMock(spec=Config)
        mock_config.prepare_pr_delay = 5  # Custom value (not the default 10)

        def mock_subprocess_run(cmd, **kwargs):
            mock_result = MagicMock()
            if "issue" in cmd and "view" in cmd:
                mock_result.stdout = json.dumps(
                    {
                        "title": "Test Issue",
                        "body": f"{PLAN_START_MARKER}\n## TASK 1: Test\n- [ ] Task\n{PLAN_END_MARKER}",
                    }
                )
            elif "git" in cmd and "commit" in cmd or "git" in cmd and "push" in cmd:
                pass
            elif "gh" in cmd and "pr" in cmd and "create" in cmd:
                mock_result.stdout = "https://github.com/owner/test-repo/pull/42\n"
            elif "issue" in cmd and "edit" in cmd:
                mock_result.stdout = json.dumps({"body": "collapsed"})
            return mock_result

        # PR lookup always returns None to trigger all 3 lookup retries
        with (
            patch.object(workflow, "_get_pr_for_issue", return_value=None),
            patch("src.workflows.implement.subprocess.run", side_effect=mock_subprocess_run),
            patch("src.workflows.implement.time.sleep") as mock_sleep,
            pytest.raises(RuntimeError, match="was created.*but could not be found"),
        ):
            workflow.execute(workflow_context, mock_config)

        # Verify sleep was called with exponential delays using custom config value
        # 5 * 1 = 5, 5 * 3 = 15, 5 * 9 = 45
        assert mock_sleep.call_count == 3
        mock_sleep.assert_any_call(5)  # 5 * 1
        mock_sleep.assert_any_call(15)  # 5 * 3
        mock_sleep.assert_any_call(45)  # 5 * 9

    def test_execute_pr_found_on_first_lookup_attempt_minimal_delay(self, workflow_context):
        """Test that when PR is found on first lookup after creation, only one delay is used."""
        import json
        from unittest.mock import MagicMock, patch

        from src.config import Config

        workflow = ImplementWorkflow()

        mock_config = MagicMock(spec=Config)
        mock_config.prepare_pr_delay = 10
        mock_config.safety_allow_appended_tasks = 0

        # First call: no PR (initial check), second call: found (after creation + delay)
        # Third call: in implementation loop
        pr_responses = [
            None,  # Initial check - no PR
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1: Test\n- [x] Done",
            },  # After creation + first delay
            {
                "number": 42,
                "body": "Closes #42\n\n## TASK 1: Test\n- [x] Done",
            },  # In loop (check state)
        ]
        call_count = {"value": 0}

        def mock_get_pr(*_args, **_kwargs):
            result = pr_responses[min(call_count["value"], len(pr_responses) - 1)]
            call_count["value"] += 1
            return result

        def mock_subprocess_run(cmd, **kwargs):
            mock_result = MagicMock()
            if "issue" in cmd and "view" in cmd:
                mock_result.stdout = json.dumps(
                    {
                        "title": "Test Issue",
                        "body": f"{PLAN_START_MARKER}\n## TASK 1: Test\n- [ ] Task\n{PLAN_END_MARKER}",
                    }
                )
            elif "git" in cmd and "commit" in cmd or "git" in cmd and "push" in cmd:
                pass
            elif "gh" in cmd and "pr" in cmd and "create" in cmd:
                mock_result.stdout = "https://github.com/owner/test-repo/pull/42\n"
            elif "issue" in cmd and "edit" in cmd:
                mock_result.stdout = json.dumps({"body": "collapsed"})
            return mock_result

        with (
            patch.object(workflow, "_get_pr_for_issue", side_effect=mock_get_pr),
            patch("src.workflows.implement.subprocess.run", side_effect=mock_subprocess_run),
            patch("src.workflows.implement.time.sleep") as mock_sleep,
        ):
            workflow.execute(workflow_context, mock_config)

        # Only one delay (first lookup attempt) - 10 * 1 = 10
        mock_sleep.assert_called_once_with(10)

    def test_execute_retries_pr_lookup_on_network_error(self, workflow_context):
        """Test that execute() retries PR lookup on transient network errors."""
        from unittest.mock import MagicMock, patch

        from src.config import Config

        workflow = ImplementWorkflow()

        mock_config = MagicMock(spec=Config)

        mock_config.safety_allow_appended_tasks = 0

        # Track calls to _get_pr_for_issue
        call_count = [0]
        pr_info = {
            "number": 42,
            "body": "Closes #42\n\n## TASK 1: Test\n- [x] Done",
        }

        def mock_get_pr(*_args, **_kwargs):
            call_count[0] += 1
            # First call (initial check) succeeds
            if call_count[0] == 1:
                return pr_info
            # Second and third calls (in loop) fail with network error
            if call_count[0] in (2, 3):
                raise NetworkError("TLS handshake timeout")
            # Fourth call succeeds (all tasks complete)
            return pr_info

        with (
            patch.object(workflow, "_get_pr_for_issue", side_effect=mock_get_pr),
            patch.object(workflow, "_run_prompt"),
            patch.object(workflow, "_mark_pr_ready"),
            patch("src.workflows.implement.time.sleep"),  # Speed up test
        ):
            # Should complete successfully after retrying
            workflow.execute(workflow_context, mock_config)

        # Initial check (1) + retry loop (2 failures + 1 success = 3 calls in retry) + final check (1)
        # But since tasks are complete on first loop iteration, it exits immediately
        # So: initial (1) + loop retry (3 calls in _retry_with_backoff) + final (1) = 5
        # Actually, since all tasks are [x] Done, it detects completion on first loop iteration
        # and doesn't run _run_prompt, so the sequence is:
        # 1. Initial check -> returns pr_info
        # 2-4. Loop iteration 1: _retry_with_backoff calls _get_pr_for_issue 3 times (2 fail, 1 success)
        # 5. Final check after loop exits
        assert call_count[0] >= 4  # At least: initial + retry calls + final
