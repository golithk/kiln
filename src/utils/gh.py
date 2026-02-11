"""GitHub CLI utility functions for GHES support."""

import os


def get_gh_env(repo: str) -> dict[str, str]:
    """Build environment dict for gh CLI with GHES token if needed.

    The gh CLI requires different environment variables for github.com vs
    GitHub Enterprise Server (GHES) authentication:
    - github.com: Uses GITHUB_TOKEN (already in os.environ from config)
    - GHES: Needs GH_HOST and GH_ENTERPRISE_TOKEN explicitly set

    Args:
        repo: Repository in 'hostname/owner/repo' format

    Returns:
        Dict to merge with os.environ for subprocess calls.
        Empty dict for github.com, {"GH_HOST": ..., "GH_ENTERPRISE_TOKEN": ...}
        for GHES hosts.

    Example:
        >>> env = {**os.environ, **get_gh_env("github.mycompany.com/org/repo")}
        >>> subprocess.run(["gh", "pr", "create", ...], env=env)
    """
    hostname = repo.split("/")[0] if "/" in repo else "github.com"

    if hostname != "github.com":
        token = os.environ.get("GH_ENTERPRISE_TOKEN")
        if token:
            return {"GH_HOST": hostname, "GH_ENTERPRISE_TOKEN": token}

    return {}
