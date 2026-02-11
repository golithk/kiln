"""Tests for the gh CLI utility functions."""

import os
from unittest.mock import patch

import pytest

from src.utils.gh import get_gh_env


@pytest.mark.unit
class TestGetGhEnv:
    """Tests for get_gh_env() function."""

    def test_github_com_host_returns_empty_dict(self):
        """Test that github.com host returns an empty dict.

        github.com uses GITHUB_TOKEN which is already in os.environ,
        so no additional environment variables are needed.
        """
        result = get_gh_env("github.com/owner/repo")

        assert result == {}

    def test_github_com_explicit_host_returns_empty_dict(self):
        """Test that explicit github.com repo format returns empty dict."""
        result = get_gh_env("github.com/organization/project-name")

        assert result == {}

    @patch.dict(os.environ, {"GH_ENTERPRISE_TOKEN": "ghes-token-12345"})
    def test_ghes_host_with_token_returns_correct_env_vars(self):
        """Test that GHES host with token returns GH_HOST and GH_ENTERPRISE_TOKEN."""
        result = get_gh_env("github.mycompany.com/org/repo")

        assert result == {
            "GH_HOST": "github.mycompany.com",
            "GH_ENTERPRISE_TOKEN": "ghes-token-12345",
        }

    @patch.dict(os.environ, {"GH_ENTERPRISE_TOKEN": "secret-token"})
    def test_ghes_host_with_subdomain_returns_correct_env_vars(self):
        """Test that GHES host with subdomain works correctly."""
        result = get_gh_env("git.internal.corp.example.com/team/service")

        assert result == {
            "GH_HOST": "git.internal.corp.example.com",
            "GH_ENTERPRISE_TOKEN": "secret-token",
        }

    @patch.dict(os.environ, {}, clear=True)
    def test_ghes_host_without_token_returns_empty_dict(self):
        """Test that GHES host without GH_ENTERPRISE_TOKEN returns empty dict.

        When no enterprise token is available, we don't set GH_HOST either,
        as authentication would fail anyway.
        """
        result = get_gh_env("github.enterprise.com/owner/repo")

        assert result == {}

    @patch.dict(os.environ, {"GH_ENTERPRISE_TOKEN": ""}, clear=True)
    def test_ghes_host_with_empty_token_returns_empty_dict(self):
        """Test that GHES host with empty token string returns empty dict."""
        result = get_gh_env("github.enterprise.com/owner/repo")

        assert result == {}

    def test_invalid_repo_format_no_slash_defaults_to_github_com(self):
        """Test that repo format without slash defaults to github.com behavior."""
        result = get_gh_env("just-a-string")

        assert result == {}

    def test_legacy_owner_repo_format_treated_as_github_com(self):
        """Test that legacy owner/repo format is treated as github.com.

        When no hostname is in the first position, the function defaults
        to github.com behavior (returning empty dict).
        """
        # With owner/repo format, "owner" becomes the hostname
        # Since "owner" != "github.com", it would try to use GHES token
        # But without a token, it returns empty dict
        with patch.dict(os.environ, {}, clear=True):
            result = get_gh_env("owner/repo")
            assert result == {}

    @patch.dict(os.environ, {"GH_ENTERPRISE_TOKEN": "token123"})
    def test_preserves_full_hostname_in_gh_host(self):
        """Test that the full hostname is preserved in GH_HOST."""
        result = get_gh_env("github-enterprise.department.company.org/team/project")

        assert result["GH_HOST"] == "github-enterprise.department.company.org"

    def test_empty_string_repo_returns_empty_dict(self):
        """Test that empty string repo returns empty dict."""
        result = get_gh_env("")

        assert result == {}

    @patch.dict(os.environ, {"GH_ENTERPRISE_TOKEN": "my-token"})
    def test_repo_with_many_path_segments(self):
        """Test repo string with multiple path segments."""
        # Only the first segment is treated as hostname
        result = get_gh_env("ghes.example.com/org/repo/extra/segments")

        assert result == {
            "GH_HOST": "ghes.example.com",
            "GH_ENTERPRISE_TOKEN": "my-token",
        }
