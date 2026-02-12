"""PR validation module for kiln.

This module provides management of PR validation configuration,
including loading settings from .kiln/pr-validation.yaml and
per-repo lookup for CI validation before marking PRs ready for review.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

logger = logging.getLogger(__name__)

# Default paths
PR_VALIDATION_CONFIG_PATH = ".kiln/pr-validation.yaml"

# Default values
DEFAULT_MAX_FIX_ATTEMPTS = 3
DEFAULT_TIMEOUT = 600  # seconds


class PRValidationError(Exception):
    """Base exception for PR validation errors."""

    pass


class PRValidationLoadError(PRValidationError):
    """Error loading PR validation configuration file."""

    pass


def parse_repo_url(url: str) -> str:
    """Parse a repository URL into hostname/owner/repo format.

    Accepts URLs in various formats:
        - https://github.com/owner/repo
        - https://github.com/owner/repo/
        - https://github.com/owner/repo/tree/main
        - github.com/owner/repo
        - github.com/owner/repo/tree/main
        - http://ghes.example.com/owner/repo

    Args:
        url: Repository URL string (with or without scheme).

    Returns:
        Repository identifier in hostname/owner/repo format.

    Raises:
        ValueError: If the URL cannot be parsed into host/owner/repo.
    """
    raw = url.strip()
    if not raw:
        raise ValueError("repo_url cannot be empty")

    # Prepend scheme if missing so urlparse works correctly
    if "://" not in raw:
        raw = f"https://{raw}"

    parsed = urlparse(raw)
    host = parsed.netloc
    if not host:
        raise ValueError(f"Could not extract hostname from repo_url '{url}'")

    # Split path into segments, filtering empty strings from leading/trailing slashes
    segments = [s for s in parsed.path.split("/") if s]
    if len(segments) < 2:
        raise ValueError(f"repo_url must contain at least owner/repo in the path, got '{url}'")

    owner = segments[0]
    repo = segments[1]
    # Strip .git suffix if present
    if repo.endswith(".git"):
        repo = repo[:-4]

    return f"{host}/{owner}/{repo}"


@dataclass
class PRValidationEntry:
    """Represents PR validation settings for a single repository.

    Attributes:
        repo: Repository identifier in hostname/owner/repo format
            (e.g., "github.com/my-org/api-service").
        validate_before_ready: Whether to wait for CI checks and auto-fix
            failures before marking PR ready for review.
        max_fix_attempts: Maximum number of fix attempts before giving up.
        timeout: Maximum time in seconds to wait for CI checks.
    """

    repo: str
    validate_before_ready: bool
    max_fix_attempts: int = field(default=DEFAULT_MAX_FIX_ATTEMPTS)
    timeout: int = field(default=DEFAULT_TIMEOUT)


class PRValidationManager:
    """Manager for PR validation configuration.

    This class handles:
    - Loading per-repo validation settings from .kiln/pr-validation.yaml
    - Looking up validation config by repository
    - Schema validation for config file

    Attributes:
        config_path: Path to the PR validation YAML configuration file.
    """

    def __init__(self, config_path: str | None = None):
        """Initialize the PR validation manager.

        Args:
            config_path: Optional path to validation config file.
                Defaults to .kiln/pr-validation.yaml if not specified.
        """
        self.config_path = config_path or PR_VALIDATION_CONFIG_PATH
        self._cached_entries: list[PRValidationEntry] | None = None

    def load_config(self) -> list[PRValidationEntry] | None:
        """Load PR validation settings from the config file.

        Returns:
            List of PRValidationEntry if the file exists and is valid,
            None if the file doesn't exist.

        Raises:
            PRValidationLoadError: If the file exists but cannot be parsed
                or contains invalid entries.
        """
        config_path = Path(self.config_path)

        if not config_path.exists():
            logger.debug(f"PR validation config file not found at {self.config_path}")
            return None

        try:
            with open(config_path, encoding="utf-8") as f:
                raw_config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise PRValidationLoadError(
                f"Invalid YAML in PR validation config file {self.config_path}: {e}"
            ) from e
        except OSError as e:
            raise PRValidationLoadError(
                f"Failed to read PR validation config file {self.config_path}: {e}"
            ) from e

        if raw_config is None:
            logger.debug("PR validation config file is empty")
            return None

        if not isinstance(raw_config, dict):
            raise PRValidationLoadError(
                f"PR validation config must be a YAML mapping, got {type(raw_config).__name__}"
            )

        repos = raw_config.get("repos")
        if repos is None:
            logger.debug("No 'repos' key in PR validation config")
            return None

        if not isinstance(repos, list):
            raise PRValidationLoadError(f"'repos' must be a list, got {type(repos).__name__}")

        entries: list[PRValidationEntry] = []
        for i, repo_entry in enumerate(repos):
            entry = self._parse_repo_entry(i, repo_entry)
            entries.append(entry)

        self._cached_entries = entries
        logger.info(f"Loaded PR validation config with {len(entries)} repository setting(s)")
        return self._cached_entries

    def _parse_repo_entry(self, index: int, repo_entry: dict[str, Any]) -> PRValidationEntry:
        """Parse and validate a single repository entry from the config.

        Args:
            index: Index of the entry in the config list (for error messages).
            repo_entry: Raw dictionary from the YAML config.

        Returns:
            PRValidationEntry with validated settings.

        Raises:
            PRValidationLoadError: If the entry is invalid.
        """
        if not isinstance(repo_entry, dict):
            raise PRValidationLoadError(
                f"Repository entry {index} must be a mapping, got {type(repo_entry).__name__}"
            )

        # Validate required 'url' field
        if "url" not in repo_entry:
            raise PRValidationLoadError(f"Repository entry {index} is missing required field 'url'")

        # Parse repo_url into host/owner/repo format
        repo_url = str(repo_entry["url"])
        try:
            repo_key = parse_repo_url(repo_url)
        except ValueError as e:
            raise PRValidationLoadError(f"Repository entry {index} has invalid url: {e}") from e

        # Get validation settings (nested under 'validation' key or at top level)
        validation = repo_entry.get("validation", repo_entry)
        if not isinstance(validation, dict):
            raise PRValidationLoadError(
                f"Repository entry {index} 'validation' must be a mapping, "
                f"got {type(validation).__name__}"
            )

        # Parse validate_before_ready (defaults to False)
        validate_before_ready = validation.get("validate_before_ready", False)
        if not isinstance(validate_before_ready, bool):
            raise PRValidationLoadError(
                f"Repository entry {index} 'validate_before_ready' must be a boolean, "
                f"got {type(validate_before_ready).__name__}"
            )

        # Parse max_fix_attempts (defaults to DEFAULT_MAX_FIX_ATTEMPTS)
        max_fix_attempts = validation.get("max_fix_attempts", DEFAULT_MAX_FIX_ATTEMPTS)
        if not isinstance(max_fix_attempts, int) or max_fix_attempts < 0:
            raise PRValidationLoadError(
                f"Repository entry {index} 'max_fix_attempts' must be a non-negative integer, "
                f"got {max_fix_attempts}"
            )

        # Parse timeout (defaults to DEFAULT_TIMEOUT)
        timeout = validation.get("timeout", DEFAULT_TIMEOUT)
        if not isinstance(timeout, int) or timeout < 0:
            raise PRValidationLoadError(
                f"Repository entry {index} 'timeout' must be a non-negative integer, got {timeout}"
            )

        return PRValidationEntry(
            repo=repo_key,
            validate_before_ready=validate_before_ready,
            max_fix_attempts=max_fix_attempts,
            timeout=timeout,
        )

    def get_validation_config(self, repo: str) -> PRValidationEntry | None:
        """Get validation settings for a specific repository.

        Args:
            repo: Repository identifier in hostname/owner/repo format
                (e.g., "github.com/my-org/api-service").

        Returns:
            PRValidationEntry for the repository if found, None otherwise.
        """
        entries = self._cached_entries
        if entries is None:
            try:
                entries = self.load_config()
            except PRValidationError:
                return None
        if not entries:
            return None

        # Normalize the repo identifier for matching
        repo_key = repo.lower()

        for entry in entries:
            if entry.repo.lower() == repo_key:
                return entry

        logger.debug(f"No validation config found for repo '{repo}'")
        return None

    def has_config(self) -> bool:
        """Check if validation config exists and has repository settings.

        Returns:
            True if validation config file exists and contains at least
            one repository entry.
        """
        try:
            entries = self.load_config()
            return entries is not None and len(entries) > 0
        except PRValidationError:
            return False

    def validate_config(self) -> list[str]:
        """Validate the PR validation configuration and return any warnings.

        Returns:
            List of warning messages for any issues found.
            Empty list if configuration is valid or doesn't exist.
        """
        warnings: list[str] = []

        try:
            entries = self.load_config()
        except PRValidationError as e:
            return [str(e)]

        if entries is None:
            return []

        # Check for duplicate repository entries
        seen_repos: set[str] = set()
        for entry in entries:
            repo_lower = entry.repo.lower()
            if repo_lower in seen_repos:
                warnings.append(f"Duplicate repository entry: {entry.repo}")
            seen_repos.add(repo_lower)

        # Warn about unusually high/low values
        for entry in entries:
            if entry.max_fix_attempts > 10:
                warnings.append(
                    f"Repository {entry.repo}: max_fix_attempts={entry.max_fix_attempts} "
                    "is unusually high (recommended: 1-5)"
                )
            if entry.timeout < 60:
                warnings.append(
                    f"Repository {entry.repo}: timeout={entry.timeout}s "
                    "may be too short for CI to complete"
                )
            if entry.timeout > 3600:
                warnings.append(
                    f"Repository {entry.repo}: timeout={entry.timeout}s "
                    "is unusually high (recommended: 300-1800)"
                )

        return warnings

    def clear_cache(self) -> None:
        """Clear the cached configuration.

        Forces the next load_config() or get_validation_config() call
        to re-read from disk.
        """
        self._cached_entries = None
        logger.debug("PR validation config cache cleared")
