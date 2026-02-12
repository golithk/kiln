"""Auto-merging module for kiln.

This module provides management of auto-merging configuration for Dependabot PRs,
including loading settings from .kiln/auto-merging.yaml and per-repo lookup for
determining which repositories have auto-merging enabled.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.integrations.pr_validation import parse_repo_url

logger = logging.getLogger(__name__)

# Default paths
AUTO_MERGING_CONFIG_PATH = ".kiln/auto-merging.yaml"

# Default values
DEFAULT_MERGE_METHOD = "squash"
DEFAULT_LABEL = "dependencies"


class AutoMergingError(Exception):
    """Base exception for auto-merging errors."""

    pass


class AutoMergingLoadError(AutoMergingError):
    """Error loading auto-merging configuration file."""

    pass


@dataclass
class AutoMergingEntry:
    """Represents auto-merging settings for a single repository.

    Attributes:
        repo: Repository identifier in hostname/owner/repo format
            (e.g., "github.com/my-org/api-service").
        enabled: Whether auto-merging is enabled for this repository.
        merge_method: Merge method to use ('merge', 'squash', 'rebase').
        label: Label used to identify Dependabot PRs to auto-merge.
    """

    repo: str
    enabled: bool
    merge_method: str = field(default=DEFAULT_MERGE_METHOD)
    label: str = field(default=DEFAULT_LABEL)


class AutoMergingManager:
    """Manager for auto-merging configuration.

    This class handles:
    - Loading per-repo auto-merging settings from .kiln/auto-merging.yaml
    - Looking up auto-merging config by repository
    - Schema validation for config file

    Attributes:
        config_path: Path to the auto-merging YAML configuration file.
    """

    def __init__(self, config_path: str | None = None):
        """Initialize the auto-merging manager.

        Args:
            config_path: Optional path to auto-merging config file.
                Defaults to .kiln/auto-merging.yaml if not specified.
        """
        self.config_path = config_path or AUTO_MERGING_CONFIG_PATH
        self._cached_entries: list[AutoMergingEntry] | None = None

    def load_config(self) -> list[AutoMergingEntry] | None:
        """Load auto-merging settings from the config file.

        Returns:
            List of AutoMergingEntry if the file exists and is valid,
            None if the file doesn't exist.

        Raises:
            AutoMergingLoadError: If the file exists but cannot be parsed
                or contains invalid entries.
        """
        config_path = Path(self.config_path)

        if not config_path.exists():
            logger.debug(f"Auto-merging config file not found at {self.config_path}")
            return None

        try:
            with open(config_path, encoding="utf-8") as f:
                raw_config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise AutoMergingLoadError(
                f"Invalid YAML in auto-merging config file {self.config_path}: {e}"
            ) from e
        except OSError as e:
            raise AutoMergingLoadError(
                f"Failed to read auto-merging config file {self.config_path}: {e}"
            ) from e

        if raw_config is None:
            logger.debug("Auto-merging config file is empty")
            return None

        if not isinstance(raw_config, dict):
            raise AutoMergingLoadError(
                f"Auto-merging config must be a YAML mapping, got {type(raw_config).__name__}"
            )

        repos = raw_config.get("repos")
        if repos is None:
            logger.debug("No 'repos' key in auto-merging config")
            return None

        if not isinstance(repos, list):
            raise AutoMergingLoadError(f"'repos' must be a list, got {type(repos).__name__}")

        entries: list[AutoMergingEntry] = []
        for i, repo_entry in enumerate(repos):
            entry = self._parse_repo_entry(i, repo_entry)
            entries.append(entry)

        self._cached_entries = entries
        logger.debug(f"Loaded auto-merging config with {len(entries)} repository setting(s)")
        return self._cached_entries

    def _parse_repo_entry(self, index: int, repo_entry: dict[str, Any]) -> AutoMergingEntry:
        """Parse and validate a single repository entry from the config.

        Args:
            index: Index of the entry in the config list (for error messages).
            repo_entry: Raw dictionary from the YAML config.

        Returns:
            AutoMergingEntry with validated settings.

        Raises:
            AutoMergingLoadError: If the entry is invalid.
        """
        if not isinstance(repo_entry, dict):
            raise AutoMergingLoadError(
                f"Repository entry {index} must be a mapping, got {type(repo_entry).__name__}"
            )

        # Validate required 'url' field
        if "url" not in repo_entry:
            raise AutoMergingLoadError(f"Repository entry {index} is missing required field 'url'")

        # Parse repo_url into host/owner/repo format
        repo_url = str(repo_entry["url"])
        try:
            repo_key = parse_repo_url(repo_url)
        except ValueError as e:
            raise AutoMergingLoadError(f"Repository entry {index} has invalid url: {e}") from e

        # Parse enabled (required, defaults to False)
        enabled = repo_entry.get("enabled", False)
        if not isinstance(enabled, bool):
            raise AutoMergingLoadError(
                f"Repository entry {index} 'enabled' must be a boolean, "
                f"got {type(enabled).__name__}"
            )

        # Parse merge_method (defaults to DEFAULT_MERGE_METHOD)
        merge_method = repo_entry.get("merge_method", DEFAULT_MERGE_METHOD)
        if not isinstance(merge_method, str):
            raise AutoMergingLoadError(
                f"Repository entry {index} 'merge_method' must be a string, "
                f"got {type(merge_method).__name__}"
            )
        valid_merge_methods = ("merge", "squash", "rebase")
        if merge_method not in valid_merge_methods:
            raise AutoMergingLoadError(
                f"Repository entry {index} 'merge_method' must be one of "
                f"{valid_merge_methods}, got '{merge_method}'"
            )

        # Parse label (defaults to DEFAULT_LABEL)
        label = repo_entry.get("label", DEFAULT_LABEL)
        if not isinstance(label, str):
            raise AutoMergingLoadError(
                f"Repository entry {index} 'label' must be a string, got {type(label).__name__}"
            )
        if not label.strip():
            raise AutoMergingLoadError(
                f"Repository entry {index} 'label' cannot be empty or whitespace"
            )

        return AutoMergingEntry(
            repo=repo_key,
            enabled=enabled,
            merge_method=merge_method,
            label=label.strip(),
        )

    def get_config(self, repo: str) -> AutoMergingEntry | None:
        """Get auto-merging settings for a specific repository.

        Args:
            repo: Repository identifier in hostname/owner/repo format
                (e.g., "github.com/my-org/api-service").

        Returns:
            AutoMergingEntry for the repository if found, None otherwise.
        """
        entries = self._cached_entries
        if entries is None:
            try:
                entries = self.load_config()
            except AutoMergingError:
                return None
        if not entries:
            return None

        # Normalize the repo identifier for matching
        repo_key = repo.lower()

        for entry in entries:
            if entry.repo.lower() == repo_key:
                return entry

        logger.debug(f"No auto-merging config found for repo '{repo}'")
        return None

    def has_config(self) -> bool:
        """Check if auto-merging config exists and has repository settings.

        Returns:
            True if auto-merging config file exists and contains at least
            one repository entry.
        """
        try:
            entries = self.load_config()
            return entries is not None and len(entries) > 0
        except AutoMergingError:
            return False

    def validate_config(self) -> list[str]:
        """Validate the auto-merging configuration and return any warnings.

        Returns:
            List of warning messages for any issues found.
            Empty list if configuration is valid or doesn't exist.
        """
        warnings: list[str] = []

        try:
            entries = self.load_config()
        except AutoMergingError as e:
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

        # Warn about disabled entries (might be intentional but worth noting)
        disabled_count = sum(1 for entry in entries if not entry.enabled)
        if disabled_count == len(entries) and entries:
            warnings.append(
                f"All {disabled_count} repository entries are disabled (enabled: false)"
            )

        return warnings

    def get_enabled_repos(self) -> list[AutoMergingEntry]:
        """Get all enabled repositories for auto-merging.

        Returns:
            List of AutoMergingEntry for repositories with enabled=true.
            Empty list if no config exists or no repos are enabled.
        """
        try:
            entries = self.load_config()
        except AutoMergingError:
            return []

        if not entries:
            return []

        return [entry for entry in entries if entry.enabled]

    def clear_cache(self) -> None:
        """Clear the cached configuration.

        Forces the next load_config() or get_config() call
        to re-read from disk.
        """
        self._cached_entries = None
        logger.debug("Auto-merging config cache cleared")
