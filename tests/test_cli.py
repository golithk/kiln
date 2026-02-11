"""Unit tests for the CLI module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.cli import (
    BANNER_PLAIN,
    __version__,
    extract_claude_resources,
    get_banner,
    get_readme,
    get_sample_config,
    install_claude_resources,
)


@pytest.mark.unit
class TestCli:
    """Tests for CLI functions."""

    def test_get_sample_config_returns_content(self):
        """Test that get_sample_config() returns .env.example content."""
        content = get_sample_config()
        assert "GITHUB_TOKEN" in content
        assert "PROJECT_URLS" in content

    def test_get_readme_returns_content(self):
        """Test that get_readme() returns README content."""
        content = get_readme()
        assert "Kiln" in content or "kiln" in content
        # README should have reasonable content
        assert len(content) > 100

    def test_get_banner_includes_version(self):
        """Test that get_banner() includes the version string."""
        banner = get_banner()
        assert f"v{__version__}" in banner

    def test_banner_plain_includes_version(self):
        """Test that BANNER_PLAIN includes the version string."""
        assert f"v{__version__}" in BANNER_PLAIN


@pytest.mark.unit
class TestInitKiln:
    """Tests for init_kiln function."""

    def test_init_kiln_creates_worktrees_directory(self, tmp_path, monkeypatch, capsys):
        """Test that init_kiln creates worktrees directory with .gitkeep."""
        monkeypatch.chdir(tmp_path)

        from src.cli import init_kiln

        init_kiln()

        # Verify worktrees directory was created
        workspace_dir = tmp_path / "worktrees"
        assert workspace_dir.exists()
        assert workspace_dir.is_dir()

        # Verify .gitkeep file was created
        gitkeep_file = workspace_dir / ".gitkeep"
        assert gitkeep_file.exists()
        assert gitkeep_file.is_file()

        # Verify output includes worktrees/
        captured = capsys.readouterr()
        assert "worktrees/" in captured.out

    def test_init_kiln_creates_readme(self, tmp_path, monkeypatch):
        """Test that init_kiln() creates .kiln/README.md."""
        from src.cli import init_kiln

        monkeypatch.chdir(tmp_path)
        init_kiln()

        # Check that README.md was created
        readme_path = tmp_path / ".kiln" / "README.md"
        assert readme_path.exists()
        content = readme_path.read_text()
        assert "Kiln" in content or "kiln" in content

    def test_init_kiln_creates_all_expected_files(self, tmp_path, monkeypatch):
        """Test that init_kiln() creates all expected files."""
        from src.cli import init_kiln

        monkeypatch.chdir(tmp_path)
        init_kiln()

        # Check all expected files/dirs exist
        assert (tmp_path / ".kiln").is_dir()
        assert (tmp_path / ".kiln" / "config").is_file()
        assert (tmp_path / ".kiln" / "logs").is_dir()
        assert (tmp_path / ".kiln" / "README.md").is_file()
        assert (tmp_path / "worktrees").is_dir()
        assert (tmp_path / "worktrees" / ".gitkeep").is_file()

    def test_init_kiln_is_idempotent(self, tmp_path, monkeypatch):
        """Test that running init_kiln twice doesn't fail."""
        monkeypatch.chdir(tmp_path)

        from src.cli import init_kiln

        # Run twice - should not raise
        init_kiln()
        init_kiln()

        # Verify directories still exist
        assert (tmp_path / "worktrees").exists()
        assert (tmp_path / "worktrees" / ".gitkeep").exists()
        assert (tmp_path / ".kiln").exists()
        assert (tmp_path / ".kiln" / "README.md").exists()


@pytest.mark.unit
class TestExtractClaudeResources:
    """Tests for extract_claude_resources function."""

    def test_extract_creates_directories(self, tmp_path, monkeypatch):
        """Test that extract_claude_resources creates commands, agents, skills directories."""
        monkeypatch.chdir(tmp_path)

        # Create .kiln directory first
        kiln_dir = tmp_path / ".kiln"
        kiln_dir.mkdir()

        # Create mock source .claude directory with resources
        source_claude = tmp_path / "mock_source" / ".claude"
        for subdir in ["commands", "agents", "skills"]:
            (source_claude / subdir).mkdir(parents=True)
            (source_claude / subdir / f"test_{subdir}.md").write_text(f"# Test {subdir}")

        # Mock the base_path to use our mock source
        with patch("src.cli.Path") as mock_path_class:
            # We need to be careful - only override the parent.parent lookup
            real_path = Path

            def path_side_effect(arg):
                if arg == Path(__file__):
                    mock = MagicMock()
                    mock.parent.parent = tmp_path / "mock_source"
                    return mock
                return real_path(arg)

            # Instead, patch at the module level by setting the base_path calculation
            with patch.object(Path, "__new__", lambda cls, *args: real_path(*args)):
                # Simpler approach: monkeypatch the function's internal logic
                pass

        # Directly test by creating source and using monkeypatch
        # Set up source .claude relative to where cli.py thinks it is
        repo_root = Path(__file__).parent.parent
        source_claude = repo_root / ".claude"

        # Call the function
        result = extract_claude_resources()

        # Verify extraction happened
        assert result == kiln_dir
        for subdir in ["commands", "agents", "skills"]:
            dest = kiln_dir / subdir
            # Check that directory was created if source exists
            if (source_claude / subdir).exists():
                assert dest.exists(), f".kiln/{subdir} should exist"
                assert dest.is_dir(), f".kiln/{subdir} should be a directory"

    def test_extract_copies_files(self, tmp_path, monkeypatch):
        """Test that extract_claude_resources copies files from source."""
        monkeypatch.chdir(tmp_path)

        # Create .kiln directory
        kiln_dir = tmp_path / ".kiln"
        kiln_dir.mkdir()

        # Call extract - uses real .claude source from repo
        result = extract_claude_resources()

        # The repo has .claude/commands, .claude/agents, .claude/skills
        # At minimum, commands should have files
        commands_dir = kiln_dir / "commands"
        if commands_dir.exists():
            files = list(commands_dir.glob("*.md"))
            assert len(files) > 0, "Should have extracted command files"

    def test_extract_overwrites_existing(self, tmp_path, monkeypatch):
        """Test that extract_claude_resources replaces existing directories."""
        monkeypatch.chdir(tmp_path)

        # Create .kiln directory
        kiln_dir = tmp_path / ".kiln"
        kiln_dir.mkdir()

        # Create existing commands directory with old file
        old_commands = kiln_dir / "commands"
        old_commands.mkdir()
        old_file = old_commands / "old_file.md"
        old_file.write_text("old content")

        # Call extract
        extract_claude_resources()

        # Old file should be gone (directory was replaced)
        assert not old_file.exists(), "Old file should be removed after extraction"

    def test_extract_is_idempotent(self, tmp_path, monkeypatch):
        """Test that calling extract_claude_resources twice doesn't fail."""
        monkeypatch.chdir(tmp_path)

        # Create .kiln directory
        (tmp_path / ".kiln").mkdir()

        # Call twice - should not raise
        extract_claude_resources()
        extract_claude_resources()

        # Verify .kiln still has expected structure
        assert (tmp_path / ".kiln").exists()

    def test_extract_handles_missing_source(self, tmp_path, monkeypatch):
        """Test that extract_claude_resources handles missing .claude gracefully."""
        monkeypatch.chdir(tmp_path)

        # Create .kiln directory
        kiln_dir = tmp_path / ".kiln"
        kiln_dir.mkdir()

        # Create a fake base path with no .claude directory
        fake_base = tmp_path / "fake_base"
        fake_base.mkdir()

        # Patch sys to simulate bundled mode without .claude
        with patch("src.cli.sys") as mock_sys:
            mock_sys._MEIPASS = str(fake_base)

            # Should return without error
            result = extract_claude_resources()
            assert result == kiln_dir


@pytest.mark.unit
class TestInstallClaudeResources:
    """Tests for install_claude_resources function."""

    def test_install_copies_files(self, tmp_path, monkeypatch):
        """Test that install_claude_resources copies files to ~/.claude/."""
        monkeypatch.chdir(tmp_path)

        # Create .kiln directory with kiln-prefixed resources
        kiln_dir = tmp_path / ".kiln"
        (kiln_dir / "commands").mkdir(parents=True)
        (kiln_dir / "commands" / "kiln-create_plan_github.md").write_text("# Test command")
        (kiln_dir / "agents").mkdir(parents=True)
        (kiln_dir / "agents" / "kiln-codebase-analyzer.md").write_text("# Test agent")
        (kiln_dir / "skills" / "kiln-edit-github-issue-components").mkdir(parents=True)
        (kiln_dir / "skills" / "kiln-edit-github-issue-components" / "SKILL.md").write_text(
            "# Test skill"
        )

        # Create fake home directory
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()

        with patch.object(Path, "home", return_value=fake_home):
            install_claude_resources()

        # Verify files were copied (not symlinks)
        cmd_file = fake_home / ".claude" / "commands" / "kiln-create_plan_github.md"
        assert cmd_file.exists(), "Command file should be copied"
        assert not cmd_file.is_symlink(), "Should be a file, not a symlink"
        assert cmd_file.read_text() == "# Test command"

        agent_file = fake_home / ".claude" / "agents" / "kiln-codebase-analyzer.md"
        assert agent_file.exists(), "Agent file should be copied"

        skill_dir = fake_home / ".claude" / "skills" / "kiln-edit-github-issue-components"
        assert skill_dir.exists(), "Skill directory should be copied"
        assert skill_dir.is_dir(), "Skill should be a directory"

    def test_install_creates_parent_directories(self, tmp_path, monkeypatch):
        """Test that install_claude_resources creates parent directories if needed."""
        monkeypatch.chdir(tmp_path)

        # Create .kiln with a command
        kiln_dir = tmp_path / ".kiln"
        (kiln_dir / "commands").mkdir(parents=True)
        (kiln_dir / "commands" / "kiln-implement_github.md").write_text("# Test")

        # Create fake home without ~/.claude/
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()

        with patch.object(Path, "home", return_value=fake_home):
            install_claude_resources()

        # Verify parent directory was created
        assert (fake_home / ".claude" / "commands").exists()
        assert (fake_home / ".claude" / "commands").is_dir()

    def test_install_overwrites_existing(self, tmp_path, monkeypatch):
        """Test that install_claude_resources overwrites existing files."""
        monkeypatch.chdir(tmp_path)

        # Create .kiln with a command
        kiln_dir = tmp_path / ".kiln"
        (kiln_dir / "commands").mkdir(parents=True)
        (kiln_dir / "commands" / "kiln-implement_github.md").write_text("# New content")

        # Create fake home with existing file
        fake_home = tmp_path / "fake_home"
        (fake_home / ".claude" / "commands").mkdir(parents=True)
        existing = fake_home / ".claude" / "commands" / "kiln-implement_github.md"
        existing.write_text("# Old content")

        with patch.object(Path, "home", return_value=fake_home):
            install_claude_resources()

        # Verify file was overwritten
        assert existing.read_text() == "# New content"

    def test_install_is_idempotent(self, tmp_path, monkeypatch):
        """Test that calling install_claude_resources twice doesn't fail."""
        monkeypatch.chdir(tmp_path)

        # Create .kiln with a command
        kiln_dir = tmp_path / ".kiln"
        (kiln_dir / "commands").mkdir(parents=True)
        (kiln_dir / "commands" / "kiln-implement_github.md").write_text("# Test")

        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()

        with patch.object(Path, "home", return_value=fake_home):
            # Call twice - should not raise
            install_claude_resources()
            install_claude_resources()

        # File should still exist
        cmd_file = fake_home / ".claude" / "commands" / "kiln-implement_github.md"
        assert cmd_file.exists()

    def test_install_raises_on_write_failure(self, tmp_path, monkeypatch):
        """Test that install_claude_resources raises RuntimeError on failure."""
        monkeypatch.chdir(tmp_path)

        # Create .kiln with a command
        kiln_dir = tmp_path / ".kiln"
        (kiln_dir / "commands").mkdir(parents=True)
        (kiln_dir / "commands" / "kiln-implement_github.md").write_text("# Test")

        # Create fake home where commands dir is a file (will fail to copy)
        fake_home = tmp_path / "fake_home"
        (fake_home / ".claude").mkdir(parents=True)
        (fake_home / ".claude" / "commands").write_text("not a directory")

        with patch.object(Path, "home", return_value=fake_home):
            with pytest.raises(RuntimeError, match="Failed to install kiln resources"):
                install_claude_resources()

    def test_install_skips_missing_source_dirs(self, tmp_path, monkeypatch):
        """Test that install_claude_resources handles missing source directories."""
        monkeypatch.chdir(tmp_path)

        # Create .kiln with only commands (no agents or skills dirs)
        kiln_dir = tmp_path / ".kiln"
        (kiln_dir / "commands").mkdir(parents=True)
        (kiln_dir / "commands" / "kiln-implement_github.md").write_text("# Test")

        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()

        with patch.object(Path, "home", return_value=fake_home):
            # Should not raise even with missing source dirs
            install_claude_resources()

        # Command should still be installed
        assert (fake_home / ".claude" / "commands" / "kiln-implement_github.md").exists()


@pytest.mark.unit
class TestCmdRun:
    """Tests for cmd_run function."""

    def test_cmd_run_validates_working_directory(self, tmp_path, monkeypatch):
        """Test that cmd_run validates working directory before proceeding."""
        from argparse import Namespace

        from src.cli import cmd_run

        # Mock Path.home() to return a controlled path
        mock_home = tmp_path / "mockhome"
        mock_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: mock_home)

        # Change to home directory (which is restricted)
        monkeypatch.chdir(mock_home)

        # cmd_run should exit with error when in home directory
        args = Namespace(daemon=False)

        with pytest.raises(SystemExit) as exc_info:
            cmd_run(args)

        assert exc_info.value.code == 1

    def test_cmd_run_allows_valid_directory(self, tmp_path, monkeypatch, capsys):
        """Test that cmd_run proceeds for valid directories."""
        from argparse import Namespace

        from src.cli import cmd_run

        # Mock Path.home() to return a controlled path
        mock_home = tmp_path / "mockhome"
        mock_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: mock_home)

        # Create a valid subdirectory
        valid_dir = mock_home / "projects"
        valid_dir.mkdir()

        # Change to valid directory
        monkeypatch.chdir(valid_dir)

        # cmd_run should proceed (will call init_kiln since no config exists)
        args = Namespace(daemon=False)
        cmd_run(args)

        # init_kiln should have been called - check for its output
        captured = capsys.readouterr()
        assert ".kiln/" in captured.out or "Created:" in captured.out

    def test_cmd_run_error_message_includes_recommendation(self, tmp_path, monkeypatch, capsys):
        """Test that error message includes recommendation when in restricted directory."""
        from argparse import Namespace

        from src.cli import cmd_run

        # Mock Path.home()
        mock_home = tmp_path / "mockhome"
        mock_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: mock_home)

        # Change to home directory
        monkeypatch.chdir(mock_home)

        args = Namespace(daemon=False)

        with pytest.raises(SystemExit):
            cmd_run(args)

        captured = capsys.readouterr()
        # Error should include recommendation
        assert "mkdir" in captured.err
        assert "kiln-workspace" in captured.err


@pytest.mark.unit
class TestRunDaemonSlackInitialization:
    """Tests to verify run_daemon() initializes Slack correctly.

    These tests prevent regression of the bug where cli.py's run_daemon()
    was missing Slack initialization, while daemon.py's main() had it.

    These tests use source code inspection to verify Slack initialization
    is present in run_daemon(), avoiding the complexity of mocking the
    entire startup sequence.
    """

    def test_run_daemon_imports_slack_functions(self):
        """Test that run_daemon() imports init_slack and send_startup_ping."""
        import inspect

        from src.cli import run_daemon

        source = inspect.getsource(run_daemon)

        # Verify the imports are present
        assert "from src.integrations.slack import init_slack, send_startup_ping" in source, (
            "run_daemon() must import init_slack and send_startup_ping from src.integrations.slack"
        )

    def test_run_daemon_calls_init_slack(self):
        """Test that run_daemon() calls init_slack with config values."""
        import inspect

        from src.cli import run_daemon

        source = inspect.getsource(run_daemon)

        # Verify init_slack is called with the config's slack settings
        assert "init_slack(config.slack_bot_token, config.slack_user_id)" in source, (
            "run_daemon() must call init_slack(config.slack_bot_token, config.slack_user_id)"
        )

    def test_run_daemon_calls_send_startup_ping(self):
        """Test that run_daemon() calls send_startup_ping()."""
        import inspect

        from src.cli import run_daemon

        source = inspect.getsource(run_daemon)

        # Verify send_startup_ping is called
        assert "send_startup_ping()" in source, "run_daemon() must call send_startup_ping()"

    def test_run_daemon_calls_slack_init_before_daemon_run(self):
        """Test that Slack initialization happens before daemon.run()."""
        import inspect

        from src.cli import run_daemon

        source = inspect.getsource(run_daemon)

        # Find positions of key calls
        init_slack_pos = source.find("init_slack(config.slack_bot_token")
        send_startup_ping_pos = source.find("send_startup_ping()")
        daemon_run_pos = source.find("daemon.run()")

        # Verify order: init_slack -> send_startup_ping -> daemon.run()
        assert init_slack_pos != -1, "init_slack call not found"
        assert send_startup_ping_pos != -1, "send_startup_ping call not found"
        assert daemon_run_pos != -1, "daemon.run() call not found"

        assert init_slack_pos < send_startup_ping_pos, (
            "init_slack must be called before send_startup_ping"
        )
        assert send_startup_ping_pos < daemon_run_pos, (
            "send_startup_ping must be called before daemon.run()"
        )


@pytest.mark.unit
class TestParseIssueUrl:
    """Tests for parse_issue_url function."""

    def test_parse_github_com_url(self):
        """Test parsing a standard github.com issue URL."""
        from src.cli import parse_issue_url

        hostname, owner, repo, issue_num = parse_issue_url(
            "https://github.com/agentic-metallurgy/kiln/issues/285"
        )
        assert hostname == "github.com"
        assert owner == "agentic-metallurgy"
        assert repo == "kiln"
        assert issue_num == 285

    def test_parse_ghes_url(self):
        """Test parsing a GitHub Enterprise Server issue URL."""
        from src.cli import parse_issue_url

        hostname, owner, repo, issue_num = parse_issue_url(
            "https://ghes.example.com/my-org/my-repo/issues/42"
        )
        assert hostname == "ghes.example.com"
        assert owner == "my-org"
        assert repo == "my-repo"
        assert issue_num == 42

    def test_parse_http_url(self):
        """Test parsing an HTTP (non-HTTPS) issue URL."""
        from src.cli import parse_issue_url

        hostname, owner, repo, issue_num = parse_issue_url("http://github.com/owner/repo/issues/1")
        assert hostname == "github.com"
        assert owner == "owner"
        assert repo == "repo"
        assert issue_num == 1

    def test_parse_url_with_trailing_slash(self):
        """Test parsing a URL with trailing slash."""
        from src.cli import parse_issue_url

        hostname, owner, repo, issue_num = parse_issue_url(
            "https://github.com/owner/repo/issues/123/"
        )
        assert hostname == "github.com"
        assert owner == "owner"
        assert repo == "repo"
        assert issue_num == 123

    def test_invalid_url_missing_scheme(self):
        """Test that URL without scheme raises ValueError."""
        from src.cli import parse_issue_url

        with pytest.raises(ValueError, match="Invalid issue URL"):
            parse_issue_url("github.com/owner/repo/issues/123")

    def test_invalid_url_missing_issues_path(self):
        """Test that URL without /issues/ path raises ValueError."""
        from src.cli import parse_issue_url

        with pytest.raises(ValueError, match="Invalid issue URL"):
            parse_issue_url("https://github.com/owner/repo/pull/123")

    def test_invalid_url_missing_issue_number(self):
        """Test that URL without issue number raises ValueError."""
        from src.cli import parse_issue_url

        with pytest.raises(ValueError, match="Invalid issue URL"):
            parse_issue_url("https://github.com/owner/repo/issues")

    def test_invalid_url_non_numeric_issue(self):
        """Test that URL with non-numeric issue number raises ValueError."""
        from src.cli import parse_issue_url

        with pytest.raises(ValueError, match="Issue number must be a valid integer"):
            parse_issue_url("https://github.com/owner/repo/issues/abc")

    def test_invalid_url_empty(self):
        """Test that empty URL raises ValueError."""
        from src.cli import parse_issue_url

        with pytest.raises(ValueError, match="Invalid issue URL"):
            parse_issue_url("")

    def test_invalid_url_malformed(self):
        """Test that completely malformed URL raises ValueError."""
        from src.cli import parse_issue_url

        with pytest.raises(ValueError, match="Invalid issue URL"):
            parse_issue_url("not-a-url")


@pytest.mark.unit
class TestValidateKilnDirectory:
    """Tests for validate_kiln_directory function."""

    def test_valid_directory_with_worktrees(self, tmp_path, monkeypatch):
        """Test validation passes for directory with .kiln/config and worktrees/."""
        from src.cli import validate_kiln_directory

        # Create valid kiln structure
        (tmp_path / ".kiln").mkdir()
        (tmp_path / ".kiln" / "config").write_text("# config")
        (tmp_path / "worktrees").mkdir()

        monkeypatch.chdir(tmp_path)

        result = validate_kiln_directory()
        assert result == "worktrees"

    def test_valid_directory_with_workspaces(self, tmp_path, monkeypatch):
        """Test validation passes for directory with .kiln/config and workspaces/."""
        from src.cli import validate_kiln_directory

        # Create valid kiln structure with workspaces
        (tmp_path / ".kiln").mkdir()
        (tmp_path / ".kiln" / "config").write_text("# config")
        (tmp_path / "workspaces").mkdir()

        monkeypatch.chdir(tmp_path)

        result = validate_kiln_directory()
        assert result == "workspaces"

    def test_prefers_worktrees_over_workspaces(self, tmp_path, monkeypatch):
        """Test that worktrees/ is preferred when both directories exist."""
        from src.cli import validate_kiln_directory

        # Create valid kiln structure with both directories
        (tmp_path / ".kiln").mkdir()
        (tmp_path / ".kiln" / "config").write_text("# config")
        (tmp_path / "worktrees").mkdir()
        (tmp_path / "workspaces").mkdir()

        monkeypatch.chdir(tmp_path)

        result = validate_kiln_directory()
        assert result == "worktrees"

    def test_missing_config_raises_error(self, tmp_path, monkeypatch):
        """Test that missing .kiln/config raises SetupError."""
        from src.cli import validate_kiln_directory
        from src.setup import SetupError

        # Create directory without config
        (tmp_path / "worktrees").mkdir()

        monkeypatch.chdir(tmp_path)

        with pytest.raises(SetupError, match="Not in a kiln directory"):
            validate_kiln_directory()

    def test_missing_workspace_dir_raises_error(self, tmp_path, monkeypatch):
        """Test that missing worktrees/ and workspaces/ raises SetupError."""
        from src.cli import validate_kiln_directory
        from src.setup import SetupError

        # Create .kiln/config but no workspace directory
        (tmp_path / ".kiln").mkdir()
        (tmp_path / ".kiln" / "config").write_text("# config")

        monkeypatch.chdir(tmp_path)

        with pytest.raises(SetupError, match="Not in a valid kiln directory"):
            validate_kiln_directory()

    def test_error_message_is_helpful(self, tmp_path, monkeypatch):
        """Test that error messages include helpful guidance."""
        from src.cli import validate_kiln_directory
        from src.setup import SetupError

        # Test missing config error message
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SetupError) as exc_info:
            validate_kiln_directory()

        error_msg = str(exc_info.value)
        assert ".kiln/config" in error_msg
        assert "kiln root directory" in error_msg


@pytest.mark.unit
class TestFindClaudeSessions:
    """Tests for find_claude_sessions function."""

    def test_finds_sessions_when_exist(self, tmp_path, monkeypatch):
        """Test that sessions are found when they exist in the expected location."""
        from src.cli import find_claude_sessions

        # Create kiln directory structure
        monkeypatch.chdir(tmp_path)

        # Create fake ~/.claude/projects/ with matching session files
        fake_home = tmp_path / "fake_home"
        claude_projects = fake_home / ".claude" / "projects"

        # Create a project directory matching the worktree pattern
        # Worktree path would be: tmp_path/worktrees/owner_repo-issue-123
        project_dir = claude_projects / "some-hash-owner_repo-issue-123-encoded"
        sessions_dir = project_dir / "sessions"
        sessions_dir.mkdir(parents=True)

        # Create a session file
        (sessions_dir / "abc123.jsonl").write_text('{"test": "data"}')

        with patch.object(Path, "home", return_value=fake_home):
            result = find_claude_sessions(
                workspace_dir="worktrees",
                hostname="github.com",
                owner="owner",
                repo="repo",
                issue_number=123,
            )

        assert result is not None
        assert result == sessions_dir
        assert (result / "abc123.jsonl").exists()

    def test_returns_none_when_no_sessions(self, tmp_path, monkeypatch):
        """Test that None is returned when no sessions exist."""
        from src.cli import find_claude_sessions

        monkeypatch.chdir(tmp_path)

        # Create empty ~/.claude/projects/
        fake_home = tmp_path / "fake_home"
        claude_projects = fake_home / ".claude" / "projects"
        claude_projects.mkdir(parents=True)

        with patch.object(Path, "home", return_value=fake_home):
            result = find_claude_sessions(
                workspace_dir="worktrees",
                hostname="github.com",
                owner="owner",
                repo="repo",
                issue_number=123,
            )

        assert result is None

    def test_returns_none_when_claude_projects_missing(self, tmp_path, monkeypatch):
        """Test that None is returned when ~/.claude/projects doesn't exist."""
        from src.cli import find_claude_sessions

        monkeypatch.chdir(tmp_path)

        # Create fake home without .claude directory
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()

        with patch.object(Path, "home", return_value=fake_home):
            result = find_claude_sessions(
                workspace_dir="worktrees",
                hostname="github.com",
                owner="owner",
                repo="repo",
                issue_number=123,
            )

        assert result is None

    def test_finds_sessions_with_workspaces_dir(self, tmp_path, monkeypatch):
        """Test that sessions are found when using workspaces/ instead of worktrees/."""
        from src.cli import find_claude_sessions

        monkeypatch.chdir(tmp_path)

        # Create fake ~/.claude/projects/ with matching session files
        fake_home = tmp_path / "fake_home"
        claude_projects = fake_home / ".claude" / "projects"

        # Create a project directory matching the workspaces pattern
        project_dir = claude_projects / "hash-myorg_myrepo-issue-42-path"
        sessions_dir = project_dir / "sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / "session123.jsonl").write_text('{"data": "test"}')

        with patch.object(Path, "home", return_value=fake_home):
            result = find_claude_sessions(
                workspace_dir="workspaces",
                hostname="ghes.example.com",
                owner="myorg",
                repo="myrepo",
                issue_number=42,
            )

        assert result is not None
        assert result.name == "sessions"

    def test_skips_directories_without_session_files(self, tmp_path, monkeypatch):
        """Test that directories without .jsonl files are skipped."""
        from src.cli import find_claude_sessions

        monkeypatch.chdir(tmp_path)

        fake_home = tmp_path / "fake_home"
        claude_projects = fake_home / ".claude" / "projects"

        # Create a matching project directory but with empty sessions folder
        project_dir = claude_projects / "path-owner_repo-issue-123-hash"
        sessions_dir = project_dir / "sessions"
        sessions_dir.mkdir(parents=True)
        # No .jsonl files

        with patch.object(Path, "home", return_value=fake_home):
            result = find_claude_sessions(
                workspace_dir="worktrees",
                hostname="github.com",
                owner="owner",
                repo="repo",
                issue_number=123,
            )

        assert result is None

    def test_handles_multiple_project_directories(self, tmp_path, monkeypatch):
        """Test that the correct project is found when multiple exist."""
        from src.cli import find_claude_sessions

        monkeypatch.chdir(tmp_path)

        fake_home = tmp_path / "fake_home"
        claude_projects = fake_home / ".claude" / "projects"

        # Create non-matching project directory with sessions
        other_project = claude_projects / "other-project-different-repo"
        other_sessions = other_project / "sessions"
        other_sessions.mkdir(parents=True)
        (other_sessions / "other.jsonl").write_text('{"other": "data"}')

        # Create matching project directory with sessions
        matching_project = claude_projects / "path-test_repo-issue-456-hash"
        matching_sessions = matching_project / "sessions"
        matching_sessions.mkdir(parents=True)
        (matching_sessions / "correct.jsonl").write_text('{"correct": "data"}')

        with patch.object(Path, "home", return_value=fake_home):
            result = find_claude_sessions(
                workspace_dir="worktrees",
                hostname="github.com",
                owner="test",
                repo="repo",
                issue_number=456,
            )

        assert result is not None
        assert result == matching_sessions
        assert (result / "correct.jsonl").exists()


@pytest.mark.unit
class TestCreateDebugZip:
    """Tests for create_debug_zip function."""

    def test_creates_zip_with_session_files(self, tmp_path, monkeypatch):
        """Test that zip file includes session files from sessions_path."""
        import zipfile

        from src.cli import create_debug_zip

        monkeypatch.chdir(tmp_path)

        # Create .kiln directory
        (tmp_path / ".kiln").mkdir()

        # Create mock sessions directory with files
        sessions_dir = tmp_path / "mock_sessions"
        sessions_dir.mkdir()
        (sessions_dir / "session1.jsonl").write_text('{"event": "start"}')
        (sessions_dir / "session2.jsonl").write_text('{"event": "end"}')

        result = create_debug_zip(
            sessions_path=sessions_dir,
            debug_data={},
            owner="test-owner",
            repo="test-repo",
            issue_number=123,
        )

        # Verify zip file was created
        assert result.exists()
        assert result.suffix == ".zip"
        assert "debug-test-owner-test-repo-123" in result.name

        # Verify contents
        with zipfile.ZipFile(result, "r") as zf:
            names = zf.namelist()
            assert "sessions/session1.jsonl" in names
            assert "sessions/session2.jsonl" in names

    def test_creates_zip_with_debug_data(self, tmp_path, monkeypatch):
        """Test that zip file includes debug data files."""
        import zipfile

        from src.cli import create_debug_zip

        monkeypatch.chdir(tmp_path)

        # Create .kiln directory
        (tmp_path / ".kiln").mkdir()

        debug_data = {
            "git_status.txt": "On branch main\nnothing to commit",
            "database_records.json": '{"issue_state": {"status": "planning"}}',
        }

        result = create_debug_zip(
            sessions_path=None,
            debug_data=debug_data,
            owner="owner",
            repo="repo",
            issue_number=42,
        )

        # Verify contents
        with zipfile.ZipFile(result, "r") as zf:
            names = zf.namelist()
            assert "git_status.txt" in names
            assert "database_records.json" in names

            # Verify content
            assert zf.read("git_status.txt").decode() == "On branch main\nnothing to commit"

    def test_creates_zip_with_both_sessions_and_data(self, tmp_path, monkeypatch):
        """Test that zip file includes both session files and debug data."""
        import zipfile

        from src.cli import create_debug_zip

        monkeypatch.chdir(tmp_path)

        # Create .kiln directory
        (tmp_path / ".kiln").mkdir()

        # Create mock sessions directory
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        (sessions_dir / "test.jsonl").write_text('{"data": "test"}')

        debug_data = {"git_status.txt": "clean"}

        result = create_debug_zip(
            sessions_path=sessions_dir,
            debug_data=debug_data,
            owner="owner",
            repo="repo",
            issue_number=1,
        )

        with zipfile.ZipFile(result, "r") as zf:
            names = zf.namelist()
            assert "sessions/test.jsonl" in names
            assert "git_status.txt" in names

    def test_creates_support_directory_if_missing(self, tmp_path, monkeypatch):
        """Test that .kiln/support/ is created if it doesn't exist."""
        from src.cli import create_debug_zip

        monkeypatch.chdir(tmp_path)

        # Create only .kiln directory, not support
        (tmp_path / ".kiln").mkdir()

        result = create_debug_zip(
            sessions_path=None,
            debug_data={"test.txt": "content"},
            owner="owner",
            repo="repo",
            issue_number=1,
        )

        # Verify support directory was created
        assert (tmp_path / ".kiln" / "support").exists()
        assert (tmp_path / ".kiln" / "support").is_dir()
        assert result.parent == tmp_path / ".kiln" / "support"

    def test_generates_timestamped_filename(self, tmp_path, monkeypatch):
        """Test that zip filename includes timestamp."""
        from src.cli import create_debug_zip

        monkeypatch.chdir(tmp_path)
        (tmp_path / ".kiln").mkdir()

        result = create_debug_zip(
            sessions_path=None,
            debug_data={"test.txt": "content"},
            owner="myowner",
            repo="myrepo",
            issue_number=999,
        )

        # Check filename format: debug-{owner}-{repo}-{issue}-{timestamp}.zip
        assert result.name.startswith("debug-myowner-myrepo-999-")
        assert result.name.endswith(".zip")
        # Timestamp should be in format YYYYMMDD-HHMMSS (15 chars)
        timestamp_part = result.name.replace("debug-myowner-myrepo-999-", "").replace(".zip", "")
        assert len(timestamp_part) == 15  # YYYYMMDD-HHMMSS

    def test_handles_empty_sessions_directory(self, tmp_path, monkeypatch):
        """Test that empty sessions directory doesn't add files to zip."""
        import zipfile

        from src.cli import create_debug_zip

        monkeypatch.chdir(tmp_path)
        (tmp_path / ".kiln").mkdir()

        # Create empty sessions directory
        sessions_dir = tmp_path / "empty_sessions"
        sessions_dir.mkdir()

        result = create_debug_zip(
            sessions_path=sessions_dir,
            debug_data={"test.txt": "content"},
            owner="owner",
            repo="repo",
            issue_number=1,
        )

        with zipfile.ZipFile(result, "r") as zf:
            names = zf.namelist()
            # Only the debug data file should be present
            assert names == ["test.txt"]

    def test_handles_nonexistent_sessions_path(self, tmp_path, monkeypatch):
        """Test that nonexistent sessions_path is handled gracefully."""
        import zipfile

        from src.cli import create_debug_zip

        monkeypatch.chdir(tmp_path)
        (tmp_path / ".kiln").mkdir()

        nonexistent_path = tmp_path / "does_not_exist"

        result = create_debug_zip(
            sessions_path=nonexistent_path,
            debug_data={"test.txt": "content"},
            owner="owner",
            repo="repo",
            issue_number=1,
        )

        with zipfile.ZipFile(result, "r") as zf:
            names = zf.namelist()
            assert names == ["test.txt"]


@pytest.mark.unit
class TestCollectDebugData:
    """Tests for collect_debug_data function."""

    def test_collects_git_status_when_worktree_exists(self, tmp_path, monkeypatch):
        """Test that git status is collected from worktree when it exists."""
        from src.cli import collect_debug_data

        monkeypatch.chdir(tmp_path)

        # Create kiln directory structure
        (tmp_path / ".kiln").mkdir()

        # Create worktree with git repo
        worktree = tmp_path / "worktrees" / "owner_repo-issue-123"
        worktree.mkdir(parents=True)

        # Initialize git repo in worktree
        import subprocess

        subprocess.run(["git", "init"], cwd=str(worktree), capture_output=True)
        (worktree / "test.txt").write_text("test content")
        subprocess.run(["git", "add", "."], cwd=str(worktree), capture_output=True)

        result = collect_debug_data(
            workspace_dir="worktrees",
            hostname="github.com",
            owner="owner",
            repo="repo",
            issue_number=123,
        )

        assert "git_status.txt" in result
        assert "test.txt" in result["git_status.txt"]

    def test_returns_empty_when_worktree_missing(self, tmp_path, monkeypatch):
        """Test that empty dict is returned when worktree doesn't exist."""
        from src.cli import collect_debug_data

        monkeypatch.chdir(tmp_path)

        # Create kiln directory structure but no worktree
        (tmp_path / ".kiln").mkdir()
        (tmp_path / "worktrees").mkdir()

        result = collect_debug_data(
            workspace_dir="worktrees",
            hostname="github.com",
            owner="owner",
            repo="repo",
            issue_number=999,  # Non-existent issue
        )

        # No git_status.txt since worktree doesn't exist
        assert "git_status.txt" not in result

    def test_returns_empty_when_not_git_repo(self, tmp_path, monkeypatch):
        """Test that git status is skipped when worktree is not a git repo."""
        from src.cli import collect_debug_data

        monkeypatch.chdir(tmp_path)

        # Create kiln directory structure
        (tmp_path / ".kiln").mkdir()

        # Create worktree directory but not as git repo
        worktree = tmp_path / "worktrees" / "owner_repo-issue-123"
        worktree.mkdir(parents=True)
        (worktree / "file.txt").write_text("content")

        result = collect_debug_data(
            workspace_dir="worktrees",
            hostname="github.com",
            owner="owner",
            repo="repo",
            issue_number=123,
        )

        # git status should either fail silently or include error message
        # The function captures the error in git_status.txt
        if "git_status.txt" in result:
            assert "failed" in result["git_status.txt"] or "fatal" in result["git_status.txt"]

    def test_collects_database_records_when_exist(self, tmp_path, monkeypatch):
        """Test that database records are collected when they exist."""
        import json

        from src.cli import collect_debug_data
        from src.database import Database

        monkeypatch.chdir(tmp_path)

        # Create kiln directory structure with database
        kiln_dir = tmp_path / ".kiln"
        kiln_dir.mkdir()
        (tmp_path / "worktrees").mkdir()

        # Create database and add issue state
        db = Database(str(kiln_dir / "kiln.db"))
        db.update_issue_state(
            repo="github.com/owner/repo",
            issue_number=123,
            status="planning",
        )

        result = collect_debug_data(
            workspace_dir="worktrees",
            hostname="github.com",
            owner="owner",
            repo="repo",
            issue_number=123,
        )

        assert "database_records.json" in result
        records = json.loads(result["database_records.json"])
        assert "issue_state" in records
        assert records["issue_state"]["status"] == "planning"

    def test_handles_missing_database(self, tmp_path, monkeypatch):
        """Test that missing database is handled gracefully."""
        from src.cli import collect_debug_data

        monkeypatch.chdir(tmp_path)

        # Create kiln directory but no database
        (tmp_path / ".kiln").mkdir()
        (tmp_path / "worktrees").mkdir()

        result = collect_debug_data(
            workspace_dir="worktrees",
            hostname="github.com",
            owner="owner",
            repo="repo",
            issue_number=123,
        )

        # No database_records.json since database doesn't exist
        assert "database_records.json" not in result

    def test_handles_database_without_matching_records(self, tmp_path, monkeypatch):
        """Test that empty database records are handled gracefully."""
        from src.cli import collect_debug_data
        from src.database import Database

        monkeypatch.chdir(tmp_path)

        # Create kiln directory with empty database
        kiln_dir = tmp_path / ".kiln"
        kiln_dir.mkdir()
        (tmp_path / "worktrees").mkdir()

        # Create database but don't add any records
        Database(str(kiln_dir / "kiln.db"))

        result = collect_debug_data(
            workspace_dir="worktrees",
            hostname="github.com",
            owner="owner",
            repo="repo",
            issue_number=999,  # Non-existent issue
        )

        # No database_records.json since no matching records
        assert "database_records.json" not in result


@pytest.mark.unit
class TestCmdDebug:
    """Tests for cmd_debug function error handling."""

    def test_exits_with_error_for_invalid_kiln_directory(self, tmp_path, monkeypatch, capsys):
        """Test that cmd_debug exits with error when not in kiln directory."""
        from argparse import Namespace

        from src.cli import cmd_debug

        # Change to empty directory (not a kiln directory)
        monkeypatch.chdir(tmp_path)

        args = Namespace(issue_url="https://github.com/owner/repo/issues/123")

        with pytest.raises(SystemExit) as exc_info:
            cmd_debug(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Not in a kiln directory" in captured.err

    def test_exits_with_error_for_invalid_url(self, tmp_path, monkeypatch, capsys):
        """Test that cmd_debug exits with error for invalid URL."""
        from argparse import Namespace

        from src.cli import cmd_debug

        # Create valid kiln directory
        (tmp_path / ".kiln").mkdir()
        (tmp_path / ".kiln" / "config").write_text("# config")
        (tmp_path / "worktrees").mkdir()

        monkeypatch.chdir(tmp_path)

        args = Namespace(issue_url="not-a-valid-url")

        with pytest.raises(SystemExit) as exc_info:
            cmd_debug(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Invalid issue URL" in captured.err

    def test_exits_with_error_when_no_debug_data_found(self, tmp_path, monkeypatch, capsys):
        """Test that cmd_debug exits with error when no data can be collected."""
        from argparse import Namespace

        from src.cli import cmd_debug

        # Create valid kiln directory
        (tmp_path / ".kiln").mkdir()
        (tmp_path / ".kiln" / "config").write_text("# config")
        (tmp_path / "worktrees").mkdir()

        # Create fake home without any Claude sessions
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()

        monkeypatch.chdir(tmp_path)

        with patch.object(Path, "home", return_value=fake_home):
            args = Namespace(issue_url="https://github.com/owner/repo/issues/999")

            with pytest.raises(SystemExit) as exc_info:
                cmd_debug(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "No debug data found" in captured.err

    def test_creates_zip_and_prints_path_on_success(self, tmp_path, monkeypatch, capsys):
        """Test that cmd_debug creates zip file and prints path on success."""
        from argparse import Namespace

        from src.cli import cmd_debug
        from src.database import Database

        # Create valid kiln directory
        kiln_dir = tmp_path / ".kiln"
        kiln_dir.mkdir()
        (kiln_dir / "config").write_text("# config")
        (tmp_path / "worktrees").mkdir()

        # Create database with issue state (so we have some debug data)
        db = Database(str(kiln_dir / "kiln.db"))
        db.update_issue_state(
            repo="github.com/test-owner/test-repo",
            issue_number=42,
            status="planning",
        )

        monkeypatch.chdir(tmp_path)

        args = Namespace(issue_url="https://github.com/test-owner/test-repo/issues/42")

        # Should not raise
        cmd_debug(args)

        captured = capsys.readouterr()
        assert "Debug archive created:" in captured.out
        assert ".kiln/support/debug-test-owner-test-repo-42-" in captured.out
        assert ".zip" in captured.out

        # Verify zip file was actually created
        support_dir = tmp_path / ".kiln" / "support"
        zip_files = list(support_dir.glob("debug-*.zip"))
        assert len(zip_files) == 1


@pytest.mark.unit
class TestDebugSubparser:
    """Tests for debug CLI subparser integration."""

    def test_debug_subparser_exists(self):
        """Test that the debug subparser is registered."""
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "src.cli", "debug", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "issue_url" in result.stdout
        assert "GitHub issue URL" in result.stdout

    def test_debug_help_shows_correct_usage(self):
        """Test that debug --help shows correct usage information."""
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "src.cli", "debug", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "issue_url" in result.stdout
        assert "https://github.com/owner/repo/issues/123" in result.stdout
