"""CLI entry point for kiln.

This module provides the main command-line interface for kiln.
On first run, it creates a .kiln/ directory with a sample config.
On subsequent runs, it loads the config and starts the daemon.

Subcommands:
    kiln           - Run the daemon (default behavior)
    kiln logs      - View run history and logs for issues
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.database import Database

# Version is set during build
__version__ = "1.1.0"

KILN_DIR = ".kiln"
CONFIG_FILE = "config"

# ANSI escape codes for startup message colors
RESET = "\033[0m"
STARTUP_COLORS = {
    "glow": "\033[38;2;250;204;21m",  # #FACC15 - Phase 1 (brightest)
    "ember": "\033[38;2;245;158;11m",  # #F59E0B - Phase 2
    "fire": "\033[38;2;249;115;22m",  # #F97316 - Phase 3
    "heat": "\033[38;2;239;98;52m",  # #EF6234 - Phase 4+ (hottest)
}


def get_sample_config() -> str:
    """Load sample config from bundled .env.example."""
    # PyInstaller sets sys._MEIPASS when running from bundle, else use repo root
    base_path = Path(getattr(sys, "_MEIPASS", None) or Path(__file__).parent.parent)
    return (base_path / ".env.example").read_text()


def get_readme() -> str:
    """Load README from bundled README.md."""
    base_path = Path(getattr(sys, "_MEIPASS", None) or Path(__file__).parent.parent)
    return (base_path / "README.md").read_text()


def get_banner() -> str:
    """Generate the KILN banner with fire gradient colors."""
    # ANSI 256-color codes for fire gradient
    # From bright yellow (226) -> orange (214) -> red-orange (202) -> red (196)
    RESET = "\033[0m"

    # Line 1: █▄▀ █ █   █▄ █
    # Gradient each character from yellow to orange
    c = [226, 220, 214, 214, 208, 208, 202, 202, 202, 196, 196, 196, 196, 196]
    chars1 = "█▄▀ █ █   █▄ █"
    line1 = "".join(f"\033[38;5;{c[i % len(c)]}m{ch}" for i, ch in enumerate(chars1))

    # Line 2: █ █ █ █▄▄ █ ▀█
    # Continue gradient into deeper reds
    c2 = [214, 208, 202, 202, 196, 196, 160, 160, 160, 124, 124, 124, 124, 124]
    chars2 = "█ █ █ █▄▄ █ ▀█"
    line2 = "".join(f"\033[38;5;{c2[i % len(c2)]}m{ch}" for i, ch in enumerate(chars2))

    return f"\n\n  {line1}{RESET}\n  {line2}{RESET}\n  v{__version__}\n\n"


BANNER_PLAIN = f"""

  █▄▀ █ █   █▄ █
  █ █ █ █▄▄ █ ▀█
  v{__version__}

"""


def startup_print(msg: str, color: str) -> None:
    """Print a startup message with the specified fire gradient color."""
    print(f"{STARTUP_COLORS.get(color, '')}{msg}{RESET}")


def get_kiln_dir() -> Path:
    """Get the .kiln directory path in the current working directory."""
    return Path.cwd() / KILN_DIR


def extract_claude_resources() -> Path:
    """Extract bundled Claude resources to .kiln/ directory.

    Copies commands, agents, and skills from the bundled .claude/ folder
    (or repo root in development) to .kiln/commands, .kiln/agents, .kiln/skills.

    This is called on every startup to ensure the latest resources are available.

    Returns:
        Path to the .kiln directory containing extracted resources
    """
    kiln_dir = get_kiln_dir()

    # Source .claude from bundle or repo root
    base_path = Path(getattr(sys, "_MEIPASS", None) or Path(__file__).parent.parent)

    source_claude = base_path / ".claude"

    if not source_claude.exists():
        return kiln_dir

    # Extract each subdirectory (commands, agents, skills)
    for subdir in ["commands", "agents", "skills"]:
        src = source_claude / subdir
        dest = kiln_dir / subdir

        if not src.exists():
            continue

        # Remove existing and copy fresh
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)

    return kiln_dir


def install_claude_resources() -> None:
    """Copy kiln resources to ~/.claude/ for Claude Code to discover.

    Copies kiln-prefixed commands, agents, and skills from .kiln/ to ~/.claude/.
    Files are copied directly (not into a subdirectory), overwriting existing files.

    This approach avoids symlink issues where Claude Code may recreate directories
    on startup, removing symlinks.

    Raises:
        RuntimeError: If resources cannot be written to ~/.claude/
    """
    from src.logger import get_logger

    logger = get_logger(__name__)

    kiln_dir = get_kiln_dir()
    claude_home = Path.home() / ".claude"

    # Define exactly which resources to install (excludes arch-eval.md)
    RESOURCES_TO_INSTALL = {
        "commands": [
            "kiln-create_plan_github.md",
            "kiln-create_plan_simple.md",
            "kiln-implement_github.md",
            "kiln-prepare_implementation_github.md",
            "kiln-research_codebase_github.md",
        ],
        "agents": [
            "kiln-codebase-analyzer.md",
            "kiln-codebase-locator.md",
            "kiln-codebase-pattern-finder.md",
            "kiln-pr-review.md",
            "kiln-thoughts-analyzer.md",
            "kiln-thoughts-locator.md",
            "kiln-web-search-researcher.md",
        ],
        "skills": [
            "kiln-create-worktree-from-issues",
            "kiln-edit-github-issue-components",
        ],
    }

    logger.debug(f"Installing kiln resources from {kiln_dir} to {claude_home}")

    installed_count = 0
    errors = []

    for subdir, items in RESOURCES_TO_INSTALL.items():
        source_dir = kiln_dir / subdir
        dest_dir = claude_home / subdir

        if not source_dir.exists():
            logger.debug(f"Skipping {subdir}: source {source_dir} does not exist")
            continue

        # Ensure destination directory exists
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            errors.append(f"Failed to create {dest_dir}: {e}")
            continue

        for item in items:
            src = source_dir / item
            dest = dest_dir / item

            if not src.exists():
                logger.warning(f"Source not found: {src}")
                continue

            try:
                if src.is_dir():
                    # Skills are directories - remove existing and copy
                    if dest.exists():
                        shutil.rmtree(dest)
                    shutil.copytree(src, dest)
                else:
                    # Commands/agents are files - copy with overwrite
                    shutil.copy2(src, dest)

                logger.debug(f"Installed: {dest}")
                installed_count += 1
            except Exception as e:
                errors.append(f"Failed to copy {src} to {dest}: {e}")

    if errors:
        error_msg = "Failed to install kiln resources:\n" + "\n".join(errors)
        logger.error(error_msg)
        raise RuntimeError(error_msg)

    if installed_count > 0:
        logger.info(f"Installed {installed_count} kiln resource(s) to {claude_home}")
    else:
        logger.warning(f"No kiln resources installed - check {kiln_dir}")


def print_banner() -> None:
    """Print the kiln ASCII banner with fire gradient."""
    print(get_banner())


def init_kiln() -> None:
    """Initialize a new .kiln directory with sample config."""
    print_banner()

    kiln_dir = get_kiln_dir()
    config_path = kiln_dir / CONFIG_FILE
    readme_path = kiln_dir / "README.md"

    # Create .kiln directory
    kiln_dir.mkdir(exist_ok=True)

    # Create logs subdirectory
    (kiln_dir / "logs").mkdir(exist_ok=True)

    # Create worktrees directory with .gitkeep
    workspace_dir = Path.cwd() / "worktrees"
    workspace_dir.mkdir(exist_ok=True)
    (workspace_dir / ".gitkeep").touch()

    # Write sample config
    config_path.write_text(get_sample_config())

    # Write README
    readme_path.write_text(get_readme())

    print("Created:")
    print("  .kiln/")
    print("  .kiln/config")
    print("  .kiln/logs/")
    print("  .kiln/README.md")
    print("  worktrees/")
    print()
    print("Next steps:")
    print("  1. Edit .kiln/config")
    print("  2. Run `kiln` again")


def run_daemon(daemon_mode: bool = False) -> None:
    """Load config and run the daemon.

    Args:
        daemon_mode: If True, log to file only (background mode).
                     If False, log to both stdout and file.
    """
    from src.config import load_config
    from src.daemon import Daemon
    from src.integrations.telemetry import get_git_version, init_telemetry
    from src.logger import _extract_org_from_url, get_logger, setup_logging
    from src.setup import (
        SetupError,
        check_for_updates,
        check_required_tools,
        configure_git_credential_env,
        get_hostnames_from_project_urls,
        validate_project_columns,
    )
    from src.ticket_clients import get_github_client

    print_banner()

    try:
        # Phase 1: Check required CLI tools
        startup_print("Checking required tools...", "glow")
        claude_info = check_required_tools()
        startup_print("  ✓ gh CLI found", "glow")
        startup_print(
            f"  ✓ claude CLI found at {claude_info.path} ({claude_info.install_method}) v{claude_info.version}",
            "glow",
        )

        # Check for updates (non-blocking, fail-silent)
        update_info = check_for_updates(kiln_dir=get_kiln_dir())
        if update_info is not None:
            startup_print(
                f"  Update available: v{update_info.latest_version} (current: v{update_info.current_version})",
                "glow",
            )
            startup_print("  Run: brew upgrade kiln", "glow")
        print()

        # Phase 2: Extract Claude resources to .kiln/
        startup_print("Extracting Claude resources...", "ember")
        extract_claude_resources()
        startup_print("  ✓ Resources extracted to .kiln/", "ember")
        print()

        # Phase 2b: Install kiln resources to ~/.claude/
        startup_print("Installing kiln resources to ~/.claude/...", "ember")
        install_claude_resources()
        startup_print("  ✓ Kiln resources installed", "ember")
        print()

        # Phase 3: Load and validate config
        startup_print("Loading configuration...", "fire")
        config = load_config()
        startup_print("  ✓ PROJECT_URLS configured", "fire")
        startup_print("  ✓ ALLOWED_USERNAMES configured", "fire")
        print()

        # Phase 3b: Configure git credentials
        startup_print("Configuring git credentials...", "fire")
        hostnames = get_hostnames_from_project_urls(config.project_urls)
        configure_git_credential_env(hostnames)
        for hostname in sorted(hostnames):
            startup_print(f"  ✓ Configured credential helper for {hostname}", "fire")
        print()

        # Phase 4: Validate project columns
        startup_print("Validating project boards...", "heat")

        # Build tokens dict for client
        tokens: dict[str, str] = {}
        if config.github_enterprise_host and config.github_enterprise_token:
            tokens[config.github_enterprise_host] = config.github_enterprise_token
        elif config.github_token:
            tokens["github.com"] = config.github_token

        client = get_github_client(
            tokens=tokens,
            enterprise_version=config.github_enterprise_version,
        )

        total_projects = len(config.project_urls)
        for i, project_url in enumerate(config.project_urls, 1):
            result = validate_project_columns(
                client, project_url, project_index=i, total_projects=total_projects
            )
            if result.action == "ok":
                startup_print(f"  ✓ {project_url}", "heat")
                startup_print("      All required columns present and correctly ordered", "heat")
            elif result.action in ("created", "reordered", "replaced"):
                startup_print(f"  ✓ {project_url}", "heat")
                startup_print(f"      {result.message}", "heat")
        print()

        # Extract org name from first project URL for log masking
        org_name = None
        if config.project_urls:
            org_name = _extract_org_from_url(config.project_urls[0])

        # Always log to file; stdout/stderr only in non-daemon mode
        setup_logging(
            log_file=config.log_file,
            log_size=config.log_size,
            log_backups=config.log_backups,
            daemon_mode=daemon_mode,
            ghes_logs_mask=config.ghes_logs_mask,
            ghes_host=config.github_enterprise_host,
            org_name=org_name,
        )

        logger = get_logger(__name__)
        logger.info(f"=== Kiln Starting (v{__version__}) ===")
        logger.info(f"Logging to {config.log_file}")

        if config.workspace_dir == "workspaces":
            logger.info("Using workspaces/ directory (detected existing worktrees)")
            logger.info("New installs use worktrees/ by default")
        else:
            logger.info("Using worktrees/ directory")

        git_version = get_git_version()
        logger.info(f"Git version: {git_version}")

        if config.otel_endpoint:
            init_telemetry(
                config.otel_endpoint,
                config.otel_service_name,
                service_version=git_version,
            )

        # Initialize Slack if configured
        from src.integrations.slack import init_slack, send_startup_ping

        init_slack(config.slack_bot_token, config.slack_user_id)
        send_startup_ping()

        daemon = Daemon(config, version=git_version)
        daemon.run()

    except SetupError as e:
        print(f"\n{e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as e:
        print(f"\n{e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        sys.exit(0)


def parse_issue_arg(issue_arg: str) -> tuple[str, int]:
    """Parse issue argument into repo and issue number.

    Supports formats:
        - owner/repo#42
        - hostname/owner/repo#42

    Args:
        issue_arg: Issue identifier string

    Returns:
        Tuple of (repo, issue_number) where repo includes hostname

    Raises:
        ValueError: If the argument format is invalid
    """
    # Pattern: optional hostname, owner/repo#number
    match = re.match(r"^(?:([^/]+)/)?([^/]+)/([^#]+)#(\d+)$", issue_arg)
    if not match:
        raise ValueError(
            f"Invalid issue format: {issue_arg}\n"
            "Expected format: owner/repo#42 or hostname/owner/repo#42"
        )

    hostname, owner, repo_name, issue_num = match.groups()
    if hostname is None:
        hostname = "github.com"

    repo = f"{hostname}/{owner}/{repo_name}"
    return repo, int(issue_num)


def validate_kiln_directory() -> str:
    """Validate current directory is a kiln root and return workspace dir name.

    Checks for:
    - .kiln/config file existence
    - worktrees/ or workspaces/ directory

    Returns:
        "worktrees" or "workspaces" depending on which exists

    Raises:
        SetupError: If not in a valid kiln directory
    """
    from src.setup import SetupError

    kiln_dir = get_kiln_dir()
    config_path = kiln_dir / CONFIG_FILE

    # Check for .kiln/config
    if not config_path.exists():
        raise SetupError(
            "Not in a kiln directory.\n"
            "Could not find .kiln/config file.\n"
            "Please run this command from a kiln root directory."
        )

    # Check for worktrees/ or workspaces/ directory
    cwd = Path.cwd()
    if (cwd / "worktrees").is_dir():
        return "worktrees"
    elif (cwd / "workspaces").is_dir():
        return "workspaces"
    else:
        raise SetupError(
            "Not in a valid kiln directory.\n"
            "Could not find worktrees/ or workspaces/ directory.\n"
            "Please run this command from a kiln root directory."
        )


def parse_issue_url(url: str) -> tuple[str, str, str, int]:
    """Parse full GitHub issue URL into components.

    Supports formats:
        - https://github.com/owner/repo/issues/123
        - https://ghes.example.com/owner/repo/issues/123
        - http://github.com/owner/repo/issues/123

    Args:
        url: Full issue URL

    Returns:
        Tuple of (hostname, owner, repo, issue_number)

    Raises:
        ValueError: If URL format is invalid
    """
    from urllib.parse import urlparse

    # Parse the URL
    parsed = urlparse(url)

    # Validate scheme
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"Invalid issue URL: {url}\nExpected format: https://github.com/owner/repo/issues/123"
        )

    hostname = parsed.netloc
    if not hostname:
        raise ValueError(
            f"Invalid issue URL: {url}\nExpected format: https://github.com/owner/repo/issues/123"
        )

    # Parse path: /owner/repo/issues/123
    path_parts = [p for p in parsed.path.split("/") if p]
    if len(path_parts) < 4:
        raise ValueError(
            f"Invalid issue URL: {url}\nExpected format: https://github.com/owner/repo/issues/123"
        )

    owner = path_parts[0]
    repo = path_parts[1]

    # Validate that path contains "issues"
    if path_parts[2] != "issues":
        raise ValueError(
            f"Invalid issue URL: {url}\nExpected format: https://github.com/owner/repo/issues/123"
        )

    # Parse issue number
    try:
        issue_number = int(path_parts[3])
    except ValueError as e:
        raise ValueError(f"Invalid issue URL: {url}\nIssue number must be a valid integer") from e

    return hostname, owner, repo, issue_number


def find_claude_sessions(
    workspace_dir: str,  # noqa: ARG001 - kept for future use
    hostname: str,  # noqa: ARG001 - kept for future GHES support
    owner: str,
    repo: str,
    issue_number: int,
) -> Path | None:
    """Find Claude session directory for a given issue.

    Claude stores session files at ~/.claude/projects/<path-hash>/*.jsonl
    where path-hash is derived from the working directory. This function locates
    the project directory for a specific issue by calculating the expected
    worktree path and searching for matching session files.

    Args:
        workspace_dir: "worktrees" or "workspaces" - the directory containing worktrees
        hostname: GitHub hostname (e.g., "github.com")
        owner: Repository owner
        repo: Repository name
        issue_number: Issue number

    Returns:
        Path to project directory containing .jsonl session files, or None if not found
    """
    # Calculate expected worktree name pattern
    # Pattern: {owner}_{repo}-issue-{issue_number}
    repo_id = f"{owner}_{repo}"
    worktree_name = f"{repo_id}-issue-{issue_number}"

    # Claude projects directory
    claude_projects = Path.home() / ".claude" / "projects"
    if not claude_projects.exists():
        return None

    # Claude uses the absolute path of the working directory to create a hash-based
    # directory name. We need to search for a projects directory that corresponds
    # to our worktree path.
    #
    # The path in Claude is stored as: ~/.claude/projects/<escaped-path>/
    # where <escaped-path> is the absolute path with / replaced by other characters
    # Session files are stored directly in this directory (no sessions/ subdirectory).
    #
    # Since we don't know the exact escaping mechanism, we search for directories
    # that contain session files and whose path matches our worktree path pattern.

    # Search all project directories for one that matches our worktree
    for project_dir in claude_projects.iterdir():
        if not project_dir.is_dir():
            continue

        # Check if this project directory name contains our worktree path pattern
        # Claude's path encoding uses the full absolute path, so we can check
        # if the project directory name contains the worktree name
        project_name = project_dir.name

        # The project name is an encoded form of the absolute path
        # Check if the worktree name appears in the encoded path
        if worktree_name in project_name:
            # Verify there are actual session files directly in project_dir
            session_files = list(project_dir.glob("*.jsonl"))
            if session_files:
                return project_dir

    # Alternative: search for any session files that might be associated with this issue
    # This handles edge cases where the encoding differs
    # Look for session files in any project that has our owner_repo pattern
    for project_dir in claude_projects.iterdir():
        if not project_dir.is_dir():
            continue

        project_name = project_dir.name

        # Check for the repo identifier pattern (owner_repo)
        # and issue number in the project path
        if repo_id in project_name and f"issue-{issue_number}" in project_name:
            # Session files are stored directly in project_dir (no sessions/ subdirectory)
            session_files = list(project_dir.glob("*.jsonl"))
            if session_files:
                return project_dir

    return None


def create_debug_zip(
    sessions_path: Path | None,
    debug_data: dict[str, str],
    owner: str,
    repo: str,
    issue_number: int,
) -> Path:
    """Create debug zip file with session files and optional data.

    Creates a timestamped zip file in .kiln/support/ containing:
    - Claude session files (if sessions_path is provided)
    - Additional debug data files (git status, database records, etc.)

    Args:
        sessions_path: Path to Claude sessions directory (may be None)
        debug_data: Dict of filename -> content for additional files
        owner: Repository owner
        repo: Repository name
        issue_number: Issue number

    Returns:
        Path to created zip file

    """
    # Create .kiln/support/ directory if it doesn't exist
    kiln_dir = get_kiln_dir()
    support_dir = kiln_dir / "support"
    support_dir.mkdir(parents=True, exist_ok=True)

    # Generate timestamped filename (UTC for consistency)
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    zip_filename = f"debug-{owner}-{repo}-{issue_number}-{timestamp}.zip"
    zip_path = support_dir / zip_filename

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add session files from Claude projects directory
        if sessions_path is not None and sessions_path.exists():
            for session_file in sessions_path.glob("*.jsonl"):
                # Store with relative path: sessions/<filename>
                arcname = f"sessions/{session_file.name}"
                zf.write(session_file, arcname)

        # Add optional debug data files
        for filename, content in debug_data.items():
            zf.writestr(filename, content)

    return zip_path


def collect_debug_data(
    workspace_dir: str,
    hostname: str,
    owner: str,
    repo: str,
    issue_number: int,
) -> dict[str, str]:
    """Collect optional debug data (git status, database records).

    Collects git status from the worktree (if it exists) and database records
    for the issue. All operations fail silently - missing data is simply
    omitted from the returned dict.

    Args:
        workspace_dir: "worktrees" or "workspaces" - the directory containing worktrees
        hostname: GitHub hostname (e.g., "github.com")
        owner: Repository owner
        repo: Repository name
        issue_number: Issue number

    Returns:
        Dict with keys like "git_status.txt", "database_records.json"
        Values are file contents. Missing data is omitted from dict.
    """
    import json
    import subprocess

    from src.database import Database

    data: dict[str, str] = {}

    # Calculate worktree path
    repo_id = f"{owner}_{repo}"
    worktree_name = f"{repo_id}-issue-{issue_number}"
    worktree_path = Path.cwd() / workspace_dir / worktree_name

    # Try to get git status if worktree exists
    if worktree_path.exists() and worktree_path.is_dir():
        try:
            result = subprocess.run(
                ["git", "status"],
                cwd=str(worktree_path),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                data["git_status.txt"] = result.stdout
            else:
                # Include stderr if git status failed but directory exists
                data["git_status.txt"] = f"git status failed:\n{result.stderr}"
        except (subprocess.TimeoutExpired, OSError):
            # Silently skip git status on errors
            pass

    # Try to get database records
    kiln_dir = get_kiln_dir()
    db_path = kiln_dir / "kiln.db"

    if db_path.exists():
        try:
            db = Database(str(db_path))

            # Build repo string in expected format: hostname/owner/repo
            repo_full = f"{hostname}/{owner}/{repo}"

            db_records: dict[str, object] = {}

            # Get issue state
            issue_state = db.get_issue_state(repo_full, issue_number)
            if issue_state:
                db_records["issue_state"] = {
                    "repo": issue_state.repo,
                    "issue_number": issue_state.issue_number,
                    "status": issue_state.status,
                    "last_updated": issue_state.last_updated.isoformat()
                    if issue_state.last_updated
                    else None,
                    "branch_name": issue_state.branch_name,
                    "project_url": issue_state.project_url,
                }

            # Get run history
            run_history = db.get_run_history(repo_full, issue_number)
            if run_history:
                db_records["run_history"] = [
                    {
                        "id": run.id,
                        "workflow": run.workflow,
                        "started_at": run.started_at.isoformat(),
                        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
                        "outcome": run.outcome,
                        "log_path": run.log_path,
                        "session_id": run.session_id,
                    }
                    for run in run_history
                ]

            if db_records:
                data["database_records.json"] = json.dumps(db_records, indent=2)
        except Exception:
            # Silently skip database errors
            pass

    return data


def cmd_debug(args: argparse.Namespace) -> None:
    """Handle the 'debug' subcommand.

    Collects Claude session files, git status, and database records for a given
    GitHub issue, packaging them into a zip file for support/debugging purposes.
    """
    from src.setup import SetupError

    try:
        # 1. Validate kiln directory
        workspace_dir = validate_kiln_directory()

        # 2. Parse issue URL
        hostname, owner, repo, issue_number = parse_issue_url(args.issue_url)

        # 3. Find Claude sessions
        sessions_path = find_claude_sessions(workspace_dir, hostname, owner, repo, issue_number)

        # 4. Collect optional debug data
        debug_data = collect_debug_data(workspace_dir, hostname, owner, repo, issue_number)

        # 5. Create zip file
        if sessions_path is None and not debug_data:
            print(f"No debug data found for {owner}/{repo}#{issue_number}", file=sys.stderr)
            sys.exit(1)

        zip_path = create_debug_zip(sessions_path, debug_data, owner, repo, issue_number)

        # 6. Print result
        print(f"Debug archive created: {zip_path}")

    except SetupError as e:
        print(f"\n{e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def format_duration(start: datetime, end: datetime | None) -> str:
    """Format duration between two timestamps."""
    if end is None:
        return "running..."
    delta = end - start
    total_seconds = int(delta.total_seconds())
    if total_seconds < 60:
        return f"{total_seconds}s"
    elif total_seconds < 3600:
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        return f"{minutes}m {seconds}s"
    else:
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        return f"{hours}h {minutes}m"


def format_outcome(outcome: str | None) -> str:
    """Format run outcome with checkmark/cross symbol."""
    if outcome is None:
        return "⏳ running"
    elif outcome == "success":
        return "✓ success"
    elif outcome == "failed":
        return "✗ failed"
    elif outcome == "stalled":
        return "⚠ stalled"
    else:
        return f"? {outcome}"


def _determine_state(labels: set[str], board_status: str) -> str:
    """Determine display state from labels, falling back to board status.

    Args:
        labels: Set of label names from the issue
        board_status: Current status from the database

    Returns:
        Display state string
    """
    # Priority order: running labels > failure labels > complete labels > board status
    label_priority = [
        "preparing",
        "researching",
        "planning",
        "implementing",
        "reviewing",
        "editing",
        "implementation_failed",
        "research_failed",
        "yolo_failed",
        "research_ready",
        "plan_ready",
    ]
    for label in label_priority:
        if label in labels:
            return label
    return board_status.lower()


def cmd_logs_summary(db: Database) -> None:
    """Show summary of all tracked issues.

    Args:
        db: Database instance
    """
    from src.config import load_config
    from src.ticket_clients import get_github_client

    # Get all issues from database
    issues = db.get_all_issue_states()
    if not issues:
        print("No tracked issues found.")
        return

    # Build GitHub client for live data
    config = load_config()
    tokens: dict[str, str] = {}
    if config.github_enterprise_host and config.github_enterprise_token:
        tokens[config.github_enterprise_host] = config.github_enterprise_token
    elif config.github_token:
        tokens["github.com"] = config.github_token

    client = get_github_client(
        tokens=tokens,
        enterprise_version=config.github_enterprise_version,
    )

    # Print header
    print(f"\n{'Identifier':<25} {'Branch':<30} {'PR':<30} {'State'}")
    print("-" * 95)

    for issue in issues:
        # Format identifier as owner/repo#42 (strip hostname prefix)
        repo_parts = issue.repo.split("/")
        if len(repo_parts) == 3:
            # hostname/owner/repo -> owner/repo
            identifier = f"{repo_parts[1]}/{repo_parts[2]}#{issue.issue_number}"
        else:
            identifier = f"{issue.repo}#{issue.issue_number}"

        # Fetch labels for YOLO/AUTO detection and state
        labels = client.get_ticket_labels(issue.repo, issue.issue_number)
        auto_label = "yolo" if "yolo" in labels else ("auto" if "auto" in labels else None)

        # Determine state from labels (most specific first)
        state = _determine_state(labels, issue.status)
        if auto_label:
            state = f"{state} ({auto_label})"

        # Fetch PR info
        prs = client.get_linked_prs(issue.repo, issue.issue_number)
        open_pr = next((pr for pr in prs if pr.state == "OPEN"), None)

        # Format branch name (truncate if needed)
        if open_pr and open_pr.branch_name:
            branch = (
                open_pr.branch_name[:27] + "..."
                if len(open_pr.branch_name) > 30
                else open_pr.branch_name
            )
        else:
            branch = "-"

        # Format PR display (truncate title if needed)
        if open_pr and open_pr.title:
            title = open_pr.title
            if len(title) > 20:
                title = title[:20] + "..."
            pr_display = f"#{open_pr.number}: {title}"
        elif open_pr:
            pr_display = f"#{open_pr.number}"
        else:
            pr_display = "-"

        print(f"{identifier:<25} {branch:<30} {pr_display:<30} {state}")


def cmd_logs(args: argparse.Namespace) -> None:
    """Handle the 'logs' subcommand."""
    from src.database import Database

    kiln_dir = get_kiln_dir()
    db_path = kiln_dir / "kiln.db"

    if not db_path.exists():
        print("No database found. Run 'kiln' first to initialize.", file=sys.stderr)
        sys.exit(1)

    db = Database(str(db_path))

    # Summary view when no issue specified
    if args.issue is None:
        cmd_logs_summary(db)
        return

    try:
        repo, issue_number = parse_issue_arg(args.issue)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    # Handle --view: show specific log file contents
    if args.view is not None:
        record = db.get_run_record(args.view)
        if record is None:
            print(f"Run ID {args.view} not found.", file=sys.stderr)
            sys.exit(1)
        if record.repo != repo or record.issue_number != issue_number:
            print(
                f"Run ID {args.view} does not belong to {args.issue}.",
                file=sys.stderr,
            )
            sys.exit(1)
        if record.log_path is None:
            print(f"No log file for run ID {args.view}.", file=sys.stderr)
            sys.exit(1)

        log_file = Path(record.log_path)
        if not log_file.exists():
            print(f"Log file not found: {record.log_path}", file=sys.stderr)
            sys.exit(1)

        print(
            f"=== Log for run {args.view}: {record.workflow} @ {record.started_at.strftime('%Y-%m-%d %H:%M')} ===\n"
        )
        print(log_file.read_text())
        return

    # Handle --session: show Claude session path
    if args.session is not None:
        record = db.get_run_record(args.session)
        if record is None:
            print(f"Run ID {args.session} not found.", file=sys.stderr)
            sys.exit(1)
        if record.repo != repo or record.issue_number != issue_number:
            print(
                f"Run ID {args.session} does not belong to {args.issue}.",
                file=sys.stderr,
            )
            sys.exit(1)
        if record.session_id is None:
            print(f"No session ID for run ID {args.session}.", file=sys.stderr)
            sys.exit(1)

        # Session file is a companion .session file next to the log
        if record.log_path:
            session_file = Path(record.log_path).with_suffix(".session")
            if session_file.exists():
                print(f"Session file: {session_file}")
            else:
                print(f"Session ID: {record.session_id}")
                # Also show the typical Claude projects path
                print("\nClaude conversations are typically stored in:")
                print(f"  ~/.claude/projects/*/sessions/{record.session_id}.jsonl")
        else:
            print(f"Session ID: {record.session_id}")
        return

    # Default: list run history (--list is implicit)
    runs = db.get_run_history(repo, issue_number)

    if not runs:
        print(f"No run history found for {args.issue}.")
        return

    # Print header
    print(f"\nRun history for {args.issue}:\n")
    print(f"{'ID':<6} {'Workflow':<12} {'Started':<18} {'Duration':<12} {'Outcome'}")
    print("-" * 70)

    for run in runs:
        started_str = run.started_at.strftime("%Y-%m-%d %H:%M")
        duration = format_duration(run.started_at, run.completed_at)
        outcome = format_outcome(run.outcome)
        print(f"{run.id:<6} {run.workflow:<12} {started_str:<18} {duration:<12} {outcome}")

    print()
    print("Use 'kiln logs <issue> --view <id>' to view a specific log file.")
    print("Use 'kiln logs <issue> --session <id>' to get Claude session info.")


def cmd_run(args: argparse.Namespace) -> None:
    """Handle the 'run' subcommand (default daemon behavior)."""
    from src.setup import SetupError, validate_working_directory

    # Validate working directory before any other operations
    try:
        validate_working_directory()
    except SetupError as e:
        print(f"\n{e}", file=sys.stderr)
        sys.exit(1)

    kiln_dir = get_kiln_dir()
    config_path = kiln_dir / CONFIG_FILE

    if not config_path.exists():
        # First run: initialize
        init_kiln()
    else:
        # Config exists: run
        run_daemon(daemon_mode=args.daemon)


def main() -> None:
    """Main entry point for the kiln CLI."""
    parser = argparse.ArgumentParser(
        prog="kiln",
        description="GitHub project automation daemon with Claude-powered workflows",
    )
    parser.add_argument(
        "--version",
        "-v",
        action="version",
        version=f"kiln {__version__}",
    )
    parser.add_argument(
        "--daemon",
        "-d",
        action="store_true",
        help="Run in daemon mode (log to file only, no stdout)",
    )

    # Create subparsers
    subparsers = parser.add_subparsers(dest="command")

    # 'run' subcommand (also the default behavior)
    run_parser = subparsers.add_parser(
        "run",
        help="Start the kiln daemon (default if no subcommand given)",
    )
    run_parser.add_argument(
        "--daemon",
        "-d",
        action="store_true",
        help="Run in daemon mode (log to file only, no stdout)",
    )

    # 'logs' subcommand
    logs_parser = subparsers.add_parser(
        "logs",
        help="View run history and logs for a specific issue",
    )
    logs_parser.add_argument(
        "issue",
        nargs="?",
        default=None,
        help="Issue identifier (e.g., owner/repo#42). If omitted, shows all issues.",
    )
    logs_parser.add_argument(
        "--list",
        "-l",
        action="store_true",
        default=True,
        help="List run history (default behavior)",
    )
    logs_parser.add_argument(
        "--view",
        type=int,
        metavar="RUN_ID",
        help="View the log file for a specific run ID",
    )
    logs_parser.add_argument(
        "--session",
        type=int,
        metavar="RUN_ID",
        help="Show Claude session info for a specific run ID",
    )

    # 'debug' subcommand
    debug_parser = subparsers.add_parser(
        "debug",
        help="Create debug archive for a GitHub issue",
    )
    debug_parser.add_argument(
        "issue_url",
        help="Full GitHub issue URL (e.g., https://github.com/owner/repo/issues/123)",
    )

    args = parser.parse_args()

    # Handle commands
    if args.command == "logs":
        cmd_logs(args)
    elif args.command == "debug":
        cmd_debug(args)
    elif args.command == "run":
        cmd_run(args)
    else:
        # No subcommand given - default to 'run' behavior
        # Use daemon flag from main parser
        args.command = "run"
        cmd_run(args)


if __name__ == "__main__":
    main()
