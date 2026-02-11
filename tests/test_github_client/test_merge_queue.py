"""Tests for GitHub client merge queue operations."""

import json
import subprocess
from unittest.mock import patch

import pytest


@pytest.mark.unit
class TestListPrsByLabel:
    """Tests for GitHubTicketClient.list_prs_by_label() method."""

    def test_list_prs_by_label_returns_pr_list(self, github_client):
        """Test that PRs with the specified label are returned."""
        mock_output = json.dumps(
            [
                {
                    "number": 101,
                    "title": "Bump actions/checkout from 3 to 4",
                    "createdAt": "2024-01-15T10:00:00Z",
                    "headRefOid": "abc123",
                },
                {
                    "number": 102,
                    "title": "Bump actions/setup-python from 4 to 5",
                    "createdAt": "2024-01-16T11:00:00Z",
                    "headRefOid": "def456",
                },
            ]
        )

        with patch.object(github_client, "_run_gh_command", return_value=mock_output) as mock_run:
            prs = github_client.list_prs_by_label("github.com/owner/repo", "dependencies")

        assert len(prs) == 2
        assert prs[0]["number"] == 101
        assert prs[0]["title"] == "Bump actions/checkout from 3 to 4"
        assert prs[0]["createdAt"] == "2024-01-15T10:00:00Z"
        assert prs[0]["headRefOid"] == "abc123"
        assert prs[1]["number"] == 102

        # Verify the correct gh command was called
        call_args = mock_run.call_args[0][0]
        assert "pr" in call_args
        assert "list" in call_args
        assert "--label" in call_args
        assert "dependencies" in call_args
        assert "--state" in call_args
        assert "open" in call_args

    def test_list_prs_by_label_with_custom_state(self, github_client):
        """Test that custom state filter is passed to gh CLI."""
        mock_output = json.dumps([])

        with patch.object(github_client, "_run_gh_command", return_value=mock_output) as mock_run:
            github_client.list_prs_by_label("github.com/owner/repo", "dependencies", state="all")

        call_args = mock_run.call_args[0][0]
        assert "--state" in call_args
        state_idx = call_args.index("--state") + 1
        assert call_args[state_idx] == "all"

    def test_list_prs_by_label_returns_empty_on_no_matches(self, github_client):
        """Test that empty list is returned when no PRs match."""
        mock_output = json.dumps([])

        with patch.object(github_client, "_run_gh_command", return_value=mock_output):
            prs = github_client.list_prs_by_label("github.com/owner/repo", "nonexistent-label")

        assert prs == []

    def test_list_prs_by_label_returns_empty_on_error(self, github_client):
        """Test that empty list is returned on gh command error."""
        error = subprocess.CalledProcessError(1, "gh")
        error.stderr = "label not found"

        with patch.object(github_client, "_run_gh_command", side_effect=error):
            prs = github_client.list_prs_by_label("github.com/owner/repo", "dependencies")

        assert prs == []

    def test_list_prs_by_label_returns_empty_on_json_error(self, github_client):
        """Test that empty list is returned on JSON parse error."""
        with patch.object(github_client, "_run_gh_command", return_value="invalid json"):
            prs = github_client.list_prs_by_label("github.com/owner/repo", "dependencies")

        assert prs == []

    def test_list_prs_by_label_uses_correct_repo_reference(self, github_client):
        """Test that the full repo URL is used for GHES compatibility."""
        mock_output = json.dumps([])

        with patch.object(github_client, "_run_gh_command", return_value=mock_output) as mock_run:
            github_client.list_prs_by_label("github.example.com/myorg/myrepo", "deps")

        call_args = mock_run.call_args[0][0]
        assert "--repo" in call_args
        repo_idx = call_args.index("--repo") + 1
        assert call_args[repo_idx] == "https://github.example.com/myorg/myrepo"


@pytest.mark.unit
class TestMergePr:
    """Tests for GitHubTicketClient.merge_pr() method."""

    def test_merge_pr_success_with_squash(self, github_client):
        """Test successfully merging a PR with squash method."""
        with patch.object(github_client, "_run_gh_command") as mock_run:
            result = github_client.merge_pr("github.com/owner/repo", 123)

        assert result is True
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args == [
            "pr",
            "merge",
            "123",
            "--repo",
            "https://github.com/owner/repo",
            "--squash",
        ]

    def test_merge_pr_success_with_merge_method(self, github_client):
        """Test successfully merging a PR with merge method."""
        with patch.object(github_client, "_run_gh_command") as mock_run:
            result = github_client.merge_pr("github.com/owner/repo", 123, merge_method="merge")

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert "--merge" in call_args

    def test_merge_pr_success_with_rebase_method(self, github_client):
        """Test successfully merging a PR with rebase method."""
        with patch.object(github_client, "_run_gh_command") as mock_run:
            result = github_client.merge_pr("github.com/owner/repo", 123, merge_method="rebase")

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert "--rebase" in call_args

    def test_merge_pr_returns_false_on_error(self, github_client):
        """Test that False is returned when merge fails."""
        error = subprocess.CalledProcessError(1, "gh")
        error.stderr = "Pull request is not mergeable"

        with patch.object(github_client, "_run_gh_command", side_effect=error):
            result = github_client.merge_pr("github.com/owner/repo", 123)

        assert result is False

    def test_merge_pr_returns_false_on_ci_failure(self, github_client):
        """Test that False is returned when CI checks fail."""
        error = subprocess.CalledProcessError(1, "gh")
        error.stderr = "Merge blocked: required status checks have not passed"

        with patch.object(github_client, "_run_gh_command", side_effect=error):
            result = github_client.merge_pr("github.com/owner/repo", 123)

        assert result is False

    def test_merge_pr_uses_correct_repo_reference(self, github_client):
        """Test that the full repo URL is used for GHES compatibility."""
        with patch.object(github_client, "_run_gh_command") as mock_run:
            github_client.merge_pr("github.example.com/myorg/myrepo", 456)

        call_args = mock_run.call_args[0][0]
        assert "--repo" in call_args
        repo_idx = call_args.index("--repo") + 1
        assert call_args[repo_idx] == "https://github.example.com/myorg/myrepo"

    def test_merge_pr_passes_repo_for_hostname_lookup(self, github_client):
        """Test that repo is passed for hostname lookup."""
        with patch.object(github_client, "_run_gh_command") as mock_run:
            github_client.merge_pr("github.com/owner/repo", 99)

        mock_run.assert_called_once()
        assert mock_run.call_args[1]["repo"] == "github.com/owner/repo"


@pytest.mark.unit
class TestCommentOnPr:
    """Tests for GitHubTicketClient.comment_on_pr() method."""

    def test_comment_on_pr_success(self, github_client):
        """Test successfully adding a comment to a PR."""
        with patch.object(github_client, "_run_gh_command") as mock_run:
            result = github_client.comment_on_pr("github.com/owner/repo", 123, "@dependabot rebase")

        assert result is True
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args == [
            "pr",
            "comment",
            "123",
            "--repo",
            "https://github.com/owner/repo",
            "--body",
            "@dependabot rebase",
        ]

    def test_comment_on_pr_with_multiline_body(self, github_client):
        """Test adding a multiline comment to a PR."""
        body = "This is a multiline comment.\n\nWith multiple paragraphs."

        with patch.object(github_client, "_run_gh_command") as mock_run:
            result = github_client.comment_on_pr("github.com/owner/repo", 123, body)

        assert result is True
        call_args = mock_run.call_args[0][0]
        body_idx = call_args.index("--body") + 1
        assert call_args[body_idx] == body

    def test_comment_on_pr_returns_false_on_error(self, github_client):
        """Test that False is returned when comment fails."""
        error = subprocess.CalledProcessError(1, "gh")
        error.stderr = "Pull request not found"

        with patch.object(github_client, "_run_gh_command", side_effect=error):
            result = github_client.comment_on_pr("github.com/owner/repo", 999, "@dependabot rebase")

        assert result is False

    def test_comment_on_pr_uses_correct_repo_reference(self, github_client):
        """Test that the full repo URL is used for GHES compatibility."""
        with patch.object(github_client, "_run_gh_command") as mock_run:
            github_client.comment_on_pr("github.example.com/myorg/myrepo", 456, "Test comment")

        call_args = mock_run.call_args[0][0]
        assert "--repo" in call_args
        repo_idx = call_args.index("--repo") + 1
        assert call_args[repo_idx] == "https://github.example.com/myorg/myrepo"

    def test_comment_on_pr_passes_repo_for_hostname_lookup(self, github_client):
        """Test that repo is passed for hostname lookup."""
        with patch.object(github_client, "_run_gh_command") as mock_run:
            github_client.comment_on_pr("github.com/owner/repo", 99, "Test")

        mock_run.assert_called_once()
        assert mock_run.call_args[1]["repo"] == "github.com/owner/repo"

    def test_comment_on_pr_dependabot_rebase(self, github_client):
        """Test the specific @dependabot rebase comment use case."""
        with patch.object(github_client, "_run_gh_command") as mock_run:
            result = github_client.comment_on_pr("github.com/owner/repo", 42, "@dependabot rebase")

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert "@dependabot rebase" in call_args
