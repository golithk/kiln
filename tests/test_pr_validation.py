"""Unit tests for PR validation module."""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from src.integrations.pr_validation import (
    DEFAULT_MAX_FIX_ATTEMPTS,
    DEFAULT_TIMEOUT,
    PRValidationEntry,
    PRValidationLoadError,
    PRValidationManager,
    parse_repo_url,
)
from src.interfaces import CheckRunResult
from src.ticket_clients.base import NetworkError
from src.workflows.base import WorkflowContext
from src.workflows.implement import ImplementWorkflow


# =============================================================================
# Tests for parse_repo_url helper function
# =============================================================================
@pytest.mark.unit
class TestParseRepoUrl:
    """Tests for the parse_repo_url() helper function."""

    def test_parse_github_com_url(self):
        """Test parsing standard github.com URL."""
        result = parse_repo_url("https://github.com/owner/repo")
        assert result == "github.com/owner/repo"

    def test_parse_github_com_url_with_trailing_slash(self):
        """Test parsing URL with trailing slash."""
        result = parse_repo_url("https://github.com/owner/repo/")
        assert result == "github.com/owner/repo"

    def test_parse_github_com_url_with_path(self):
        """Test parsing URL with extra path components."""
        result = parse_repo_url("https://github.com/owner/repo/tree/main")
        assert result == "github.com/owner/repo"

    def test_parse_url_without_scheme(self):
        """Test parsing URL without https:// prefix."""
        result = parse_repo_url("github.com/owner/repo")
        assert result == "github.com/owner/repo"

    def test_parse_github_enterprise_url(self):
        """Test parsing GitHub Enterprise URL."""
        result = parse_repo_url("https://ghes.example.com/myorg/myrepo")
        assert result == "ghes.example.com/myorg/myrepo"

    def test_parse_url_with_git_suffix(self):
        """Test parsing URL with .git suffix."""
        result = parse_repo_url("https://github.com/owner/repo.git")
        assert result == "github.com/owner/repo"

    def test_parse_empty_url_raises_error(self):
        """Test that empty URL raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            parse_repo_url("")

    def test_parse_invalid_url_raises_error(self):
        """Test that URL without owner/repo raises ValueError."""
        with pytest.raises(ValueError, match="must contain at least owner/repo"):
            parse_repo_url("https://github.com/")


# =============================================================================
# Tests for PRValidationEntry dataclass
# =============================================================================
@pytest.mark.unit
class TestPRValidationEntry:
    """Tests for PRValidationEntry dataclass."""

    def test_entry_with_defaults(self):
        """Test creating entry with default values."""
        entry = PRValidationEntry(
            repo="github.com/owner/repo",
            validate_before_ready=True,
        )
        assert entry.repo == "github.com/owner/repo"
        assert entry.validate_before_ready is True
        assert entry.max_fix_attempts == DEFAULT_MAX_FIX_ATTEMPTS
        assert entry.timeout == DEFAULT_TIMEOUT

    def test_entry_with_custom_values(self):
        """Test creating entry with custom values."""
        entry = PRValidationEntry(
            repo="github.com/owner/repo",
            validate_before_ready=False,
            max_fix_attempts=5,
            timeout=900,
        )
        assert entry.validate_before_ready is False
        assert entry.max_fix_attempts == 5
        assert entry.timeout == 900


# =============================================================================
# Tests for PRValidationManager config loading
# =============================================================================
@pytest.mark.unit
class TestPRValidationManagerConfigLoading:
    """Tests for PRValidationManager config loading."""

    def test_load_config_file_not_found(self, tmp_path):
        """Test that load_config returns None when file doesn't exist."""
        manager = PRValidationManager(str(tmp_path / "nonexistent.yaml"))
        result = manager.load_config()
        assert result is None

    def test_load_config_empty_file(self, tmp_path):
        """Test loading an empty YAML file."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("")

        manager = PRValidationManager(str(config_file))
        result = manager.load_config()
        assert result is None

    def test_load_config_no_repos_key(self, tmp_path):
        """Test loading config without 'repos' key."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("other_key: value\n")

        manager = PRValidationManager(str(config_file))
        result = manager.load_config()
        assert result is None

    def test_load_config_valid_single_repo(self, tmp_path):
        """Test loading valid config with single repo."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    validate_before_ready: true
    max_fix_attempts: 5
    timeout: 300
""")

        manager = PRValidationManager(str(config_file))
        result = manager.load_config()

        assert result is not None
        assert len(result) == 1
        assert result[0].repo == "github.com/owner/repo"
        assert result[0].validate_before_ready is True
        assert result[0].max_fix_attempts == 5
        assert result[0].timeout == 300

    def test_load_config_valid_multiple_repos(self, tmp_path):
        """Test loading valid config with multiple repos."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/org1/repo1
    validate_before_ready: true
  - url: https://github.com/org2/repo2
    validate_before_ready: false
    max_fix_attempts: 10
""")

        manager = PRValidationManager(str(config_file))
        result = manager.load_config()

        assert result is not None
        assert len(result) == 2
        assert result[0].repo == "github.com/org1/repo1"
        assert result[1].repo == "github.com/org2/repo2"
        assert result[1].max_fix_attempts == 10

    def test_load_config_with_nested_validation_key(self, tmp_path):
        """Test loading config with nested 'validation' key."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    validation:
      validate_before_ready: true
      max_fix_attempts: 7
      timeout: 450
""")

        manager = PRValidationManager(str(config_file))
        result = manager.load_config()

        assert result is not None
        assert len(result) == 1
        assert result[0].validate_before_ready is True
        assert result[0].max_fix_attempts == 7
        assert result[0].timeout == 450

    def test_load_config_uses_defaults(self, tmp_path):
        """Test that missing fields use default values."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    validate_before_ready: true
""")

        manager = PRValidationManager(str(config_file))
        result = manager.load_config()

        assert result is not None
        assert len(result) == 1
        assert result[0].max_fix_attempts == DEFAULT_MAX_FIX_ATTEMPTS
        assert result[0].timeout == DEFAULT_TIMEOUT

    def test_load_config_invalid_yaml_raises_error(self, tmp_path):
        """Test that invalid YAML raises PRValidationLoadError."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("invalid: yaml: content: [\n")

        manager = PRValidationManager(str(config_file))
        with pytest.raises(PRValidationLoadError, match="Invalid YAML"):
            manager.load_config()

    def test_load_config_not_a_dict_raises_error(self, tmp_path):
        """Test that non-dict root raises PRValidationLoadError."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("- item1\n- item2\n")

        manager = PRValidationManager(str(config_file))
        with pytest.raises(PRValidationLoadError, match="must be a YAML mapping"):
            manager.load_config()

    def test_load_config_repos_not_list_raises_error(self, tmp_path):
        """Test that non-list 'repos' raises PRValidationLoadError."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("repos: not_a_list\n")

        manager = PRValidationManager(str(config_file))
        with pytest.raises(PRValidationLoadError, match="'repos' must be a list"):
            manager.load_config()

    def test_load_config_missing_url_raises_error(self, tmp_path):
        """Test that entry without 'url' raises PRValidationLoadError."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - validate_before_ready: true
""")

        manager = PRValidationManager(str(config_file))
        with pytest.raises(PRValidationLoadError, match="missing required field 'url'"):
            manager.load_config()

    def test_load_config_invalid_url_raises_error(self, tmp_path):
        """Test that invalid URL raises PRValidationLoadError."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: invalid_url
    validate_before_ready: true
""")

        manager = PRValidationManager(str(config_file))
        with pytest.raises(PRValidationLoadError, match="invalid url"):
            manager.load_config()

    def test_load_config_invalid_validate_before_ready_type(self, tmp_path):
        """Test that non-boolean validate_before_ready raises error."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    validate_before_ready: "yes"
""")

        manager = PRValidationManager(str(config_file))
        with pytest.raises(PRValidationLoadError, match="must be a boolean"):
            manager.load_config()

    def test_load_config_invalid_max_fix_attempts_type(self, tmp_path):
        """Test that non-integer max_fix_attempts raises error."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    validate_before_ready: true
    max_fix_attempts: "five"
""")

        manager = PRValidationManager(str(config_file))
        with pytest.raises(PRValidationLoadError, match="must be a non-negative integer"):
            manager.load_config()

    def test_load_config_negative_max_fix_attempts(self, tmp_path):
        """Test that negative max_fix_attempts raises error."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    validate_before_ready: true
    max_fix_attempts: -1
""")

        manager = PRValidationManager(str(config_file))
        with pytest.raises(PRValidationLoadError, match="must be a non-negative integer"):
            manager.load_config()

    def test_load_config_invalid_timeout_type(self, tmp_path):
        """Test that non-integer timeout raises error."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    validate_before_ready: true
    timeout: "ten minutes"
""")

        manager = PRValidationManager(str(config_file))
        with pytest.raises(PRValidationLoadError, match="must be a non-negative integer"):
            manager.load_config()

    def test_load_config_sets_cache(self, tmp_path):
        """Test that load_config sets the cached entries."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    validate_before_ready: true
""")

        manager = PRValidationManager(str(config_file))
        assert manager._cached_entries is None

        result = manager.load_config()

        # Cache should now be set
        assert manager._cached_entries is not None
        assert result is manager._cached_entries
        assert len(result) == 1


# =============================================================================
# Tests for PRValidationManager.get_validation_config()
# =============================================================================
@pytest.mark.unit
class TestPRValidationManagerGetValidationConfig:
    """Tests for PRValidationManager.get_validation_config()."""

    def test_get_validation_config_found(self, tmp_path):
        """Test getting config for a matching repo."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    validate_before_ready: true
    max_fix_attempts: 5
""")

        manager = PRValidationManager(str(config_file))
        result = manager.get_validation_config("github.com/owner/repo")

        assert result is not None
        assert result.validate_before_ready is True
        assert result.max_fix_attempts == 5

    def test_get_validation_config_not_found(self, tmp_path):
        """Test getting config for a non-matching repo."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    validate_before_ready: true
""")

        manager = PRValidationManager(str(config_file))
        result = manager.get_validation_config("github.com/other/repo")

        assert result is None

    def test_get_validation_config_case_insensitive(self, tmp_path):
        """Test that repo matching is case-insensitive."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/Owner/Repo
    validate_before_ready: true
""")

        manager = PRValidationManager(str(config_file))
        result = manager.get_validation_config("github.com/owner/repo")

        assert result is not None
        assert result.validate_before_ready is True

    def test_get_validation_config_no_config_file(self, tmp_path):
        """Test getting config when config file doesn't exist."""
        manager = PRValidationManager(str(tmp_path / "nonexistent.yaml"))
        result = manager.get_validation_config("github.com/owner/repo")

        assert result is None

    def test_get_validation_config_loads_on_demand(self, tmp_path):
        """Test that config is loaded on demand if not cached."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    validate_before_ready: true
""")

        manager = PRValidationManager(str(config_file))
        # Don't call load_config first
        result = manager.get_validation_config("github.com/owner/repo")

        assert result is not None
        assert result.validate_before_ready is True


# =============================================================================
# Tests for PRValidationManager.has_config()
# =============================================================================
@pytest.mark.unit
class TestPRValidationManagerHasConfig:
    """Tests for PRValidationManager.has_config()."""

    def test_has_config_true(self, tmp_path):
        """Test has_config returns True when repos exist."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    validate_before_ready: true
""")

        manager = PRValidationManager(str(config_file))
        assert manager.has_config() is True

    def test_has_config_false_no_file(self, tmp_path):
        """Test has_config returns False when file doesn't exist."""
        manager = PRValidationManager(str(tmp_path / "nonexistent.yaml"))
        assert manager.has_config() is False

    def test_has_config_false_empty_repos(self, tmp_path):
        """Test has_config returns False when repos list is empty."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("repos: []\n")

        manager = PRValidationManager(str(config_file))
        # Empty list returns None from load_config
        assert manager.has_config() is False


# =============================================================================
# Tests for PRValidationManager.validate_config()
# =============================================================================
@pytest.mark.unit
class TestPRValidationManagerValidateConfig:
    """Tests for PRValidationManager.validate_config()."""

    def test_validate_config_no_warnings(self, tmp_path):
        """Test validate_config returns empty list for valid config."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    validate_before_ready: true
    max_fix_attempts: 3
    timeout: 600
""")

        manager = PRValidationManager(str(config_file))
        warnings = manager.validate_config()

        assert warnings == []

    def test_validate_config_duplicate_repos(self, tmp_path):
        """Test validate_config warns about duplicate repos."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    validate_before_ready: true
  - url: https://github.com/owner/repo
    validate_before_ready: false
""")

        manager = PRValidationManager(str(config_file))
        warnings = manager.validate_config()

        assert len(warnings) == 1
        assert "Duplicate repository entry" in warnings[0]

    def test_validate_config_high_max_fix_attempts(self, tmp_path):
        """Test validate_config warns about unusually high max_fix_attempts."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    validate_before_ready: true
    max_fix_attempts: 15
""")

        manager = PRValidationManager(str(config_file))
        warnings = manager.validate_config()

        assert len(warnings) == 1
        assert "unusually high" in warnings[0]

    def test_validate_config_low_timeout(self, tmp_path):
        """Test validate_config warns about too short timeout."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    validate_before_ready: true
    timeout: 30
""")

        manager = PRValidationManager(str(config_file))
        warnings = manager.validate_config()

        assert len(warnings) == 1
        assert "may be too short" in warnings[0]

    def test_validate_config_high_timeout(self, tmp_path):
        """Test validate_config warns about unusually high timeout."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    validate_before_ready: true
    timeout: 7200
""")

        manager = PRValidationManager(str(config_file))
        warnings = manager.validate_config()

        assert len(warnings) == 1
        assert "unusually high" in warnings[0]


# =============================================================================
# Tests for PRValidationManager.clear_cache()
# =============================================================================
@pytest.mark.unit
class TestPRValidationManagerClearCache:
    """Tests for PRValidationManager.clear_cache()."""

    def test_clear_cache(self, tmp_path):
        """Test that clear_cache clears the cached config."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    validate_before_ready: true
""")

        manager = PRValidationManager(str(config_file))

        # Load config to populate cache
        manager.load_config()
        assert manager._cached_entries is not None

        # Clear cache
        manager.clear_cache()
        assert manager._cached_entries is None


# =============================================================================
# Tests for GitHubTicketClient.get_check_runs()
# =============================================================================
@pytest.mark.unit
class TestGitHubTicketClientGetCheckRuns:
    """Tests for GitHubTicketClient.get_check_runs() method."""

    def test_get_check_runs_success(self):
        """Test getting check runs with successful response."""
        from src.ticket_clients.github import GitHubTicketClient

        client = GitHubTicketClient()

        mock_check_runs_response = json.dumps(
            {
                "total_count": 2,
                "check_runs": [
                    {
                        "name": "CI / test",
                        "status": "completed",
                        "conclusion": "success",
                        "details_url": "https://github.com/owner/repo/actions/runs/123",
                        "output": {"title": "Tests passed", "summary": "All 50 tests passed"},
                    },
                    {
                        "name": "CI / lint",
                        "status": "completed",
                        "conclusion": "failure",
                        "details_url": "https://github.com/owner/repo/actions/runs/124",
                        "output": {"title": "Linting failed", "summary": "2 errors found"},
                    },
                ],
            }
        )

        mock_status_response = json.dumps({"state": "pending", "statuses": []})

        with patch.object(
            client, "_run_gh_command", side_effect=[mock_check_runs_response, mock_status_response]
        ):
            result = client.get_check_runs("github.com/owner/repo", "abc123")

        assert len(result) == 2
        assert result[0].name == "CI / test"
        assert result[0].status == "completed"
        assert result[0].conclusion == "success"
        assert result[0].is_successful is True
        assert result[0].is_failed is False

        assert result[1].name == "CI / lint"
        assert result[1].conclusion == "failure"
        assert result[1].is_failed is True

    def test_get_check_runs_with_commit_statuses(self):
        """Test getting check runs that includes commit statuses from external CI."""
        from src.ticket_clients.github import GitHubTicketClient

        client = GitHubTicketClient()

        mock_check_runs_response = json.dumps({"total_count": 0, "check_runs": []})

        mock_status_response = json.dumps(
            {
                "state": "failure",
                "statuses": [
                    {
                        "context": "jenkins/build",
                        "state": "success",
                        "description": "Build passed",
                        "target_url": "https://jenkins.example.com/job/123",
                    },
                    {
                        "context": "circleci/test",
                        "state": "failure",
                        "description": "Tests failed",
                        "target_url": "https://circleci.com/gh/owner/repo/456",
                    },
                    {
                        "context": "codecov/patch",
                        "state": "pending",
                        "description": "Waiting for coverage",
                    },
                ],
            }
        )

        with patch.object(
            client, "_run_gh_command", side_effect=[mock_check_runs_response, mock_status_response]
        ):
            result = client.get_check_runs("github.com/owner/repo", "abc123")

        assert len(result) == 3

        # Jenkins build - success
        assert result[0].name == "jenkins/build"
        assert result[0].status == "completed"
        assert result[0].conclusion == "success"
        assert result[0].is_successful is True

        # CircleCI test - failure
        assert result[1].name == "circleci/test"
        assert result[1].status == "completed"
        assert result[1].conclusion == "failure"
        assert result[1].is_failed is True

        # Codecov patch - pending
        assert result[2].name == "codecov/patch"
        assert result[2].status == "in_progress"
        assert result[2].conclusion is None
        assert result[2].is_completed is False

    def test_get_check_runs_empty_response(self):
        """Test getting check runs when there are no checks."""
        from src.ticket_clients.github import GitHubTicketClient

        client = GitHubTicketClient()

        mock_check_runs_response = json.dumps({"total_count": 0, "check_runs": []})
        mock_status_response = json.dumps({"state": "pending", "statuses": []})

        with patch.object(
            client, "_run_gh_command", side_effect=[mock_check_runs_response, mock_status_response]
        ):
            result = client.get_check_runs("github.com/owner/repo", "abc123")

        assert result == []

    def test_get_check_runs_in_progress(self):
        """Test getting check runs that are still in progress."""
        from src.ticket_clients.github import GitHubTicketClient

        client = GitHubTicketClient()

        mock_check_runs_response = json.dumps(
            {
                "total_count": 1,
                "check_runs": [
                    {
                        "name": "CI / test",
                        "status": "in_progress",
                        "conclusion": None,
                    }
                ],
            }
        )
        mock_status_response = json.dumps({"state": "pending", "statuses": []})

        with patch.object(
            client, "_run_gh_command", side_effect=[mock_check_runs_response, mock_status_response]
        ):
            result = client.get_check_runs("github.com/owner/repo", "abc123")

        assert len(result) == 1
        assert result[0].status == "in_progress"
        assert result[0].is_completed is False
        assert result[0].is_failed is False

    def test_get_check_runs_handles_api_error(self):
        """Test that get_check_runs handles API errors gracefully."""
        from src.ticket_clients.github import GitHubTicketClient

        client = GitHubTicketClient()

        with patch.object(
            client,
            "_run_gh_command",
            side_effect=subprocess.CalledProcessError(1, "gh", stderr="API error"),
        ):
            result = client.get_check_runs("github.com/owner/repo", "abc123")

        # Should return empty list on error
        assert result == []

    def test_get_check_runs_pagination(self):
        """Test that get_check_runs handles pagination."""
        from src.ticket_clients.github import GitHubTicketClient

        client = GitHubTicketClient()

        # First page with partial results
        mock_page1 = json.dumps(
            {
                "total_count": 2,
                "check_runs": [{"name": "check1", "status": "completed", "conclusion": "success"}],
            }
        )
        # Second page with remaining results
        mock_page2 = json.dumps(
            {
                "total_count": 2,
                "check_runs": [{"name": "check2", "status": "completed", "conclusion": "success"}],
            }
        )
        # Empty third page (terminates loop)
        mock_page3 = json.dumps({"total_count": 2, "check_runs": []})
        mock_status = json.dumps({"state": "success", "statuses": []})

        with patch.object(
            client, "_run_gh_command", side_effect=[mock_page1, mock_page2, mock_page3, mock_status]
        ):
            result = client.get_check_runs("github.com/owner/repo", "abc123")

        assert len(result) == 2
        assert result[0].name == "check1"
        assert result[1].name == "check2"


# =============================================================================
# Tests for ImplementWorkflow._wait_for_ci()
# =============================================================================
@pytest.mark.unit
class TestImplementWorkflowWaitForCI:
    """Tests for ImplementWorkflow._wait_for_ci() method."""

    def test_wait_for_ci_all_checks_pass(self):
        """Test _wait_for_ci when all checks pass immediately."""
        workflow = ImplementWorkflow()

        mock_check_runs = [
            CheckRunResult(name="CI / test", status="completed", conclusion="success"),
            CheckRunResult(name="CI / lint", status="completed", conclusion="success"),
        ]

        with patch("src.workflows.implement.GitHubTicketClient") as MockClient:
            mock_client = MockClient.return_value
            mock_client.get_check_runs.return_value = mock_check_runs

            result = workflow._wait_for_ci(
                "github.com/owner/repo",
                42,
                "abc123",
                timeout=60,
            )

        assert result == []  # No failures

    def test_wait_for_ci_some_checks_fail(self):
        """Test _wait_for_ci when some checks fail."""
        workflow = ImplementWorkflow()

        mock_check_runs = [
            CheckRunResult(name="CI / test", status="completed", conclusion="success"),
            CheckRunResult(name="CI / lint", status="completed", conclusion="failure"),
        ]

        with patch("src.workflows.implement.GitHubTicketClient") as MockClient:
            mock_client = MockClient.return_value
            mock_client.get_check_runs.return_value = mock_check_runs

            result = workflow._wait_for_ci(
                "github.com/owner/repo",
                42,
                "abc123",
                timeout=60,
            )

        assert len(result) == 1
        assert result[0].name == "CI / lint"
        assert result[0].is_failed is True

    def test_wait_for_ci_waits_for_in_progress(self):
        """Test _wait_for_ci waits for in-progress checks."""
        workflow = ImplementWorkflow()

        # First call: one check in progress
        in_progress_runs = [
            CheckRunResult(name="CI / test", status="in_progress", conclusion=None),
        ]
        # Second call: all complete
        completed_runs = [
            CheckRunResult(name="CI / test", status="completed", conclusion="success"),
        ]

        with (
            patch("src.workflows.implement.GitHubTicketClient") as MockClient,
            patch("src.workflows.implement.time.sleep"),
        ):
            mock_client = MockClient.return_value
            mock_client.get_check_runs.side_effect = [in_progress_runs, completed_runs]

            result = workflow._wait_for_ci(
                "github.com/owner/repo",
                42,
                "abc123",
                timeout=60,
            )

        assert result == []
        assert mock_client.get_check_runs.call_count == 2

    def test_wait_for_ci_timeout(self):
        """Test _wait_for_ci times out and returns current failures."""
        workflow = ImplementWorkflow()

        # Always return in-progress checks
        in_progress_runs = [
            CheckRunResult(name="CI / test", status="in_progress", conclusion=None),
        ]

        with (
            patch("src.workflows.implement.GitHubTicketClient") as MockClient,
            patch("src.workflows.implement.time.sleep"),
            patch("src.workflows.implement.time.time") as mock_time,
            patch.object(workflow, "_add_pr_comment"),
        ):
            mock_client = MockClient.return_value
            mock_client.get_check_runs.return_value = in_progress_runs

            # Simulate time passing: first call returns 0, subsequent calls return beyond timeout
            mock_time.side_effect = [0, 0, 700, 700]  # Start, check elapsed, elapsed > timeout

            result = workflow._wait_for_ci(
                "github.com/owner/repo",
                42,
                "abc123",
                timeout=600,
            )

        # Should return empty list since no checks completed with failure
        assert result == []

    def test_wait_for_ci_adds_3_minute_comment(self):
        """Test _wait_for_ci adds comment at 3 min mark."""
        workflow = ImplementWorkflow()

        in_progress_runs = [
            CheckRunResult(name="CI / test", status="in_progress", conclusion=None),
        ]
        completed_runs = [
            CheckRunResult(name="CI / test", status="completed", conclusion="success"),
        ]

        # Track time calls
        time_values = [
            0,
            0,
            181,
            181,
        ]  # start, first check (0s), second check (181s), then complete
        time_index = [0]

        def mock_time_fn():
            if time_index[0] < len(time_values):
                val = time_values[time_index[0]]
                time_index[0] += 1
                return val
            return 200  # Default for any extra calls

        with (
            patch("src.workflows.implement.GitHubTicketClient") as MockClient,
            patch("src.workflows.implement.time.sleep"),
            patch("src.workflows.implement.time.time", side_effect=mock_time_fn),
            patch.object(workflow, "_add_pr_comment") as mock_comment,
            patch("src.workflows.implement._retry_with_backoff") as mock_retry,
        ):
            mock_client = MockClient.return_value
            # Make _retry_with_backoff just call the function directly
            mock_retry.side_effect = lambda fn, **kwargs: fn()
            mock_client.get_check_runs.side_effect = [
                in_progress_runs,  # Check 1 at ~0s
                completed_runs,  # Check 2 at ~3 min - completes
            ]

            workflow._wait_for_ci(
                "github.com/owner/repo",
                42,
                "abc123",
                timeout=600,
            )

        # Should have added 3 min comment
        comment_calls = [str(call) for call in mock_comment.call_args_list]
        assert any("3 minutes" in call for call in comment_calls)

    def test_wait_for_ci_no_checks_waits(self):
        """Test _wait_for_ci waits when no checks are found initially."""
        workflow = ImplementWorkflow()

        with (
            patch("src.workflows.implement.GitHubTicketClient") as MockClient,
            patch("src.workflows.implement.time.sleep"),
            patch("src.workflows.implement.time.time") as mock_time,
        ):
            mock_client = MockClient.return_value
            # First call: no checks, second call: checks complete
            mock_client.get_check_runs.side_effect = [
                [],
                [CheckRunResult(name="CI / test", status="completed", conclusion="success")],
            ]

            mock_time.side_effect = [0, 0, 10, 10]

            result = workflow._wait_for_ci(
                "github.com/owner/repo",
                42,
                "abc123",
                timeout=60,
            )

        assert result == []
        assert mock_client.get_check_runs.call_count == 2

    def test_wait_for_ci_network_error_returns_empty(self):
        """Test _wait_for_ci returns empty list on persistent network error."""
        workflow = ImplementWorkflow()

        with (
            patch("src.workflows.implement.GitHubTicketClient") as MockClient,
            patch("src.workflows.implement.time.sleep"),
            patch("src.workflows.implement._retry_with_backoff") as mock_retry,
        ):
            mock_retry.side_effect = NetworkError("Network failure")

            result = workflow._wait_for_ci(
                "github.com/owner/repo",
                42,
                "abc123",
                timeout=60,
            )

        assert result == []


# =============================================================================
# Tests for ImplementWorkflow._run_validation_phase()
# =============================================================================
@pytest.mark.unit
class TestImplementWorkflowRunValidationPhase:
    """Tests for ImplementWorkflow._run_validation_phase() method."""

    @pytest.fixture
    def workflow_context(self):
        """Fixture providing a sample WorkflowContext."""
        return WorkflowContext(
            repo="github.com/owner/repo",
            issue_number=42,
            issue_title="Test Issue",
            workspace_path="/tmp/workspace",
        )

    @pytest.fixture
    def mock_config(self):
        """Fixture providing a mock Config."""
        config = MagicMock()
        config.safety_allow_appended_tasks = 0
        return config

    def test_validation_phase_skips_when_not_enabled(self, workflow_context, mock_config):
        """Test that validation phase skips when validate_before_ready is false."""
        workflow = ImplementWorkflow()

        mock_manager = MagicMock()
        mock_manager.get_validation_config.return_value = PRValidationEntry(
            repo="github.com/owner/repo",
            validate_before_ready=False,
        )

        with patch.object(workflow, "_mark_pr_ready") as mock_ready:
            workflow._run_validation_phase(workflow_context, mock_config, 42, mock_manager)

        mock_ready.assert_called_once_with("github.com/owner/repo", 42)

    def test_validation_phase_skips_when_no_config(self, workflow_context, mock_config):
        """Test that validation phase skips when no config exists."""
        workflow = ImplementWorkflow()

        mock_manager = MagicMock()
        mock_manager.get_validation_config.return_value = None

        with patch.object(workflow, "_mark_pr_ready") as mock_ready:
            workflow._run_validation_phase(workflow_context, mock_config, 42, mock_manager)

        mock_ready.assert_called_once_with("github.com/owner/repo", 42)

    def test_validation_phase_creates_default_manager_when_none(
        self, workflow_context, mock_config
    ):
        """Test that validation phase creates default manager when none provided."""
        workflow = ImplementWorkflow()

        with (
            patch("src.workflows.implement.PRValidationManager") as MockManager,
            patch.object(workflow, "_mark_pr_ready"),
        ):
            mock_manager_instance = MockManager.return_value
            mock_manager_instance.get_validation_config.return_value = None

            workflow._run_validation_phase(workflow_context, mock_config, 42, None)

        MockManager.assert_called_once()

    def test_validation_phase_all_checks_pass(self, workflow_context, mock_config):
        """Test validation phase when all CI checks pass."""
        workflow = ImplementWorkflow()

        mock_manager = MagicMock()
        mock_manager.get_validation_config.return_value = PRValidationEntry(
            repo="github.com/owner/repo",
            validate_before_ready=True,
            max_fix_attempts=3,
            timeout=600,
        )

        with (
            patch("src.workflows.implement.GitHubTicketClient") as MockClient,
            patch.object(workflow, "_wait_for_ci") as mock_wait,
            patch.object(workflow, "_mark_pr_ready") as mock_ready,
            patch.object(workflow, "_add_pr_comment") as mock_comment,
        ):
            mock_client = MockClient.return_value
            mock_client.get_pr_head_sha.return_value = "abc123"

            mock_wait.return_value = []  # No failures

            workflow._run_validation_phase(workflow_context, mock_config, 42, mock_manager)

        mock_wait.assert_called_once()
        mock_ready.assert_called_once_with("github.com/owner/repo", 42)
        # Should have added "CI validation passed" comment
        comment_calls = [str(call) for call in mock_comment.call_args_list]
        assert any("CI validation passed" in call for call in comment_calls)

    def test_validation_phase_runs_fix_loop(self, workflow_context, mock_config):
        """Test validation phase runs fix loop when CI fails."""
        workflow = ImplementWorkflow()

        mock_manager = MagicMock()
        mock_manager.get_validation_config.return_value = PRValidationEntry(
            repo="github.com/owner/repo",
            validate_before_ready=True,
            max_fix_attempts=3,
            timeout=600,
        )

        failed_check = CheckRunResult(
            name="CI / test", status="completed", conclusion="failure", output="Test failed"
        )

        with (
            patch("src.workflows.implement.GitHubTicketClient") as MockClient,
            patch.object(workflow, "_wait_for_ci") as mock_wait,
            patch.object(workflow, "_run_prompt") as mock_run_prompt,
            patch.object(workflow, "_mark_pr_ready") as mock_ready,
            patch.object(workflow, "_add_pr_comment"),
        ):
            mock_client = MockClient.return_value
            mock_client.get_pr_head_sha.return_value = "abc123"

            # First call: failure, second call: success
            mock_wait.side_effect = [[failed_check], []]

            workflow._run_validation_phase(workflow_context, mock_config, 42, mock_manager)

        # Should have run fix prompt once
        fix_calls = [c for c in mock_run_prompt.call_args_list if "fix_ci" in str(c)]
        assert len(fix_calls) == 1
        mock_ready.assert_called_once()

    def test_validation_phase_stall_detection(self, workflow_context, mock_config):
        """Test validation phase stops on stall (same error repeating)."""
        workflow = ImplementWorkflow()

        mock_manager = MagicMock()
        mock_manager.get_validation_config.return_value = PRValidationEntry(
            repo="github.com/owner/repo",
            validate_before_ready=True,
            max_fix_attempts=5,
            timeout=600,
        )

        # Same failure every time
        failed_check = CheckRunResult(
            name="CI / test", status="completed", conclusion="failure", output="Test failed"
        )

        with (
            patch("src.workflows.implement.GitHubTicketClient") as MockClient,
            patch.object(workflow, "_wait_for_ci") as mock_wait,
            patch.object(workflow, "_run_prompt"),
            patch.object(workflow, "_mark_pr_ready") as mock_ready,
            patch.object(workflow, "_add_pr_comment") as mock_comment,
        ):
            mock_client = MockClient.return_value
            mock_client.get_pr_head_sha.return_value = "abc123"

            # Always return same failure
            mock_wait.return_value = [failed_check]

            workflow._run_validation_phase(workflow_context, mock_config, 42, mock_manager)

        # Should have added stall detection comment
        comment_calls = [str(call) for call in mock_comment.call_args_list]
        assert any("stalled" in call.lower() for call in comment_calls)
        mock_ready.assert_called_once()

    def test_validation_phase_max_fix_attempts(self, workflow_context, mock_config):
        """Test validation phase stops after max fix attempts."""
        workflow = ImplementWorkflow()

        mock_manager = MagicMock()
        mock_manager.get_validation_config.return_value = PRValidationEntry(
            repo="github.com/owner/repo",
            validate_before_ready=True,
            max_fix_attempts=2,
            timeout=600,
        )

        # Different failures each time (no stall)
        failures = [
            [CheckRunResult(name="CI / test1", status="completed", conclusion="failure")],
            [CheckRunResult(name="CI / test2", status="completed", conclusion="failure")],
            [CheckRunResult(name="CI / test3", status="completed", conclusion="failure")],
        ]

        with (
            patch("src.workflows.implement.GitHubTicketClient") as MockClient,
            patch.object(workflow, "_wait_for_ci") as mock_wait,
            patch.object(workflow, "_run_prompt") as mock_run_prompt,
            patch.object(workflow, "_mark_pr_ready") as mock_ready,
            patch.object(workflow, "_add_pr_comment") as mock_comment,
        ):
            mock_client = MockClient.return_value
            mock_client.get_pr_head_sha.return_value = "abc123"

            mock_wait.side_effect = failures

            workflow._run_validation_phase(workflow_context, mock_config, 42, mock_manager)

        # Should have run fix prompt max_fix_attempts times
        fix_calls = [c for c in mock_run_prompt.call_args_list if "fix_ci" in str(c)]
        assert len(fix_calls) == 2  # max_fix_attempts
        mock_ready.assert_called_once()

        # Should have added "exhausted" comment
        comment_calls = [str(call) for call in mock_comment.call_args_list]
        assert any("exhausted" in call for call in comment_calls)

    def test_validation_phase_handles_sha_fetch_failure(self, workflow_context, mock_config):
        """Test validation phase handles failure to get PR head SHA."""
        workflow = ImplementWorkflow()

        mock_manager = MagicMock()
        mock_manager.get_validation_config.return_value = PRValidationEntry(
            repo="github.com/owner/repo",
            validate_before_ready=True,
        )

        with (
            patch("src.workflows.implement.GitHubTicketClient") as MockClient,
            patch.object(workflow, "_mark_pr_ready") as mock_ready,
        ):
            mock_client = MockClient.return_value
            mock_client.get_pr_head_sha.return_value = None

            workflow._run_validation_phase(workflow_context, mock_config, 42, mock_manager)

        # Should still mark PR ready even if SHA fetch fails
        mock_ready.assert_called_once()


# =============================================================================
# Integration test for full validation cycle
# =============================================================================
@pytest.mark.unit
class TestValidationPhaseIntegration:
    """Integration tests for the full validation cycle with mocked dependencies."""

    def test_full_validation_cycle_success_after_one_fix(self):
        """Test complete validation cycle: fail -> fix -> pass."""
        workflow = ImplementWorkflow()

        ctx = WorkflowContext(
            repo="github.com/owner/repo",
            issue_number=42,
            issue_title="Test Issue",
            workspace_path="/tmp/workspace",
        )
        mock_config = MagicMock()
        mock_config.safety_allow_appended_tasks = 0

        mock_manager = MagicMock()
        mock_manager.get_validation_config.return_value = PRValidationEntry(
            repo="github.com/owner/repo",
            validate_before_ready=True,
            max_fix_attempts=3,
            timeout=300,
        )

        # Sequence: first check fails, after fix check passes
        failed_check = CheckRunResult(
            name="CI / test", status="completed", conclusion="failure", output="Test failed"
        )

        sha_sequence = ["sha1", "sha2"]  # Different SHA after fix
        sha_index = [0]

        def get_sha(*args, **kwargs):
            sha = sha_sequence[min(sha_index[0], len(sha_sequence) - 1)]
            sha_index[0] += 1
            return sha

        with (
            patch("src.workflows.implement.GitHubTicketClient") as MockClient,
            patch.object(workflow, "_wait_for_ci") as mock_wait,
            patch.object(workflow, "_run_prompt") as mock_run_prompt,
            patch.object(workflow, "_mark_pr_ready") as mock_ready,
            patch.object(workflow, "_add_pr_comment") as mock_comment,
            patch("src.workflows.implement.send_ready_for_validation_notification") as mock_notify,
        ):
            mock_client = MockClient.return_value
            mock_client.get_pr_head_sha.side_effect = get_sha

            # First wait: failure, second wait: success
            mock_wait.side_effect = [[failed_check], []]

            workflow._run_validation_phase(ctx, mock_config, 42, mock_manager)

        # Verify flow:
        # 1. Got SHA
        assert mock_client.get_pr_head_sha.call_count == 2

        # 2. Waited for CI twice
        assert mock_wait.call_count == 2

        # 3. Ran fix prompt once
        fix_calls = [c for c in mock_run_prompt.call_args_list if "fix_ci" in str(c)]
        assert len(fix_calls) == 1

        # 4. Marked PR ready
        mock_ready.assert_called_once_with("github.com/owner/repo", 42)

        # 5. Sent notification
        mock_notify.assert_called_once()

        # 6. Added appropriate comments
        comment_bodies = [str(call) for call in mock_comment.call_args_list]
        assert any("failures detected" in body.lower() for body in comment_bodies)
        assert any("validation passed" in body.lower() for body in comment_bodies)

    def test_full_validation_cycle_with_daemon_integration(self):
        """Test validation cycle integrating with daemon flow (PRValidationManager passed in)."""
        workflow = ImplementWorkflow()

        ctx = WorkflowContext(
            repo="github.com/owner/repo",
            issue_number=42,
            issue_title="Test Issue",
            workspace_path="/tmp/workspace",
        )
        mock_config = MagicMock()
        mock_config.safety_allow_appended_tasks = 0

        # Simulate daemon-initialized PRValidationManager
        mock_manager = PRValidationManager.__new__(PRValidationManager)
        mock_manager.config_path = "/path/to/.kiln/pr-validation.yaml"
        mock_manager._cached_entries = [
            PRValidationEntry(
                repo="github.com/owner/repo",
                validate_before_ready=True,
                max_fix_attempts=2,
                timeout=300,
            )
        ]

        with (
            patch("src.workflows.implement.GitHubTicketClient") as MockClient,
            patch.object(workflow, "_wait_for_ci") as mock_wait,
            patch.object(workflow, "_mark_pr_ready") as mock_ready,
            patch.object(workflow, "_add_pr_comment"),
            patch("src.workflows.implement.send_ready_for_validation_notification"),
        ):
            mock_client = MockClient.return_value
            mock_client.get_pr_head_sha.return_value = "abc123"

            # All checks pass
            mock_wait.return_value = []

            workflow._run_validation_phase(ctx, mock_config, 42, mock_manager)

        mock_ready.assert_called_once()


# =============================================================================
# Tests for CheckRunResult helper properties
# =============================================================================
@pytest.mark.unit
class TestCheckRunResult:
    """Tests for CheckRunResult dataclass and its properties."""

    def test_is_completed_true(self):
        """Test is_completed returns True for completed status."""
        check = CheckRunResult(name="test", status="completed", conclusion="success")
        assert check.is_completed is True

    def test_is_completed_false(self):
        """Test is_completed returns False for non-completed status."""
        check = CheckRunResult(name="test", status="in_progress", conclusion=None)
        assert check.is_completed is False

    def test_is_successful_success(self):
        """Test is_successful for success conclusion."""
        check = CheckRunResult(name="test", status="completed", conclusion="success")
        assert check.is_successful is True

    def test_is_successful_neutral(self):
        """Test is_successful for neutral conclusion."""
        check = CheckRunResult(name="test", status="completed", conclusion="neutral")
        assert check.is_successful is True

    def test_is_successful_skipped(self):
        """Test is_successful for skipped conclusion."""
        check = CheckRunResult(name="test", status="completed", conclusion="skipped")
        assert check.is_successful is True

    def test_is_successful_false_when_failed(self):
        """Test is_successful returns False for failure."""
        check = CheckRunResult(name="test", status="completed", conclusion="failure")
        assert check.is_successful is False

    def test_is_successful_false_when_not_completed(self):
        """Test is_successful returns False when not completed."""
        check = CheckRunResult(name="test", status="in_progress", conclusion=None)
        assert check.is_successful is False

    def test_is_failed_failure(self):
        """Test is_failed for failure conclusion."""
        check = CheckRunResult(name="test", status="completed", conclusion="failure")
        assert check.is_failed is True

    def test_is_failed_timed_out(self):
        """Test is_failed for timed_out conclusion."""
        check = CheckRunResult(name="test", status="completed", conclusion="timed_out")
        assert check.is_failed is True

    def test_is_failed_action_required(self):
        """Test is_failed for action_required conclusion."""
        check = CheckRunResult(name="test", status="completed", conclusion="action_required")
        assert check.is_failed is True

    def test_is_failed_false_when_success(self):
        """Test is_failed returns False for success."""
        check = CheckRunResult(name="test", status="completed", conclusion="success")
        assert check.is_failed is False

    def test_is_failed_false_when_not_completed(self):
        """Test is_failed returns False when not completed."""
        check = CheckRunResult(name="test", status="in_progress", conclusion=None)
        assert check.is_failed is False
