"""Unit tests for auto-merging configuration module."""

import pytest

from src.integrations.auto_merging import (
    AUTO_MERGING_CONFIG_PATH,
    DEFAULT_LABEL,
    DEFAULT_MERGE_METHOD,
    AutoMergingEntry,
    AutoMergingLoadError,
    AutoMergingManager,
)


# =============================================================================
# Tests for AutoMergingEntry dataclass
# =============================================================================
@pytest.mark.unit
class TestAutoMergingEntry:
    """Tests for AutoMergingEntry dataclass."""

    def test_entry_with_defaults(self):
        """Test creating entry with default values."""
        entry = AutoMergingEntry(
            repo="github.com/owner/repo",
            enabled=True,
        )
        assert entry.repo == "github.com/owner/repo"
        assert entry.enabled is True
        assert entry.merge_method == DEFAULT_MERGE_METHOD
        assert entry.label == DEFAULT_LABEL

    def test_entry_with_custom_values(self):
        """Test creating entry with custom values."""
        entry = AutoMergingEntry(
            repo="github.com/owner/repo",
            enabled=False,
            merge_method="rebase",
            label="custom-label",
        )
        assert entry.enabled is False
        assert entry.merge_method == "rebase"
        assert entry.label == "custom-label"


# =============================================================================
# Tests for AutoMergingManager config loading
# =============================================================================
@pytest.mark.unit
class TestAutoMergingManagerConfigLoading:
    """Tests for AutoMergingManager config loading."""

    def test_default_config_path(self):
        """Test that default config path is set correctly."""
        manager = AutoMergingManager()
        assert manager.config_path == AUTO_MERGING_CONFIG_PATH

    def test_custom_config_path(self):
        """Test that custom config path is respected."""
        manager = AutoMergingManager("/custom/path/config.yaml")
        assert manager.config_path == "/custom/path/config.yaml"

    def test_load_config_file_not_found(self, tmp_path):
        """Test that load_config returns None when file doesn't exist."""
        manager = AutoMergingManager(str(tmp_path / "nonexistent.yaml"))
        result = manager.load_config()
        assert result is None

    def test_load_config_empty_file(self, tmp_path):
        """Test loading an empty YAML file."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("")

        manager = AutoMergingManager(str(config_file))
        result = manager.load_config()
        assert result is None

    def test_load_config_no_repos_key(self, tmp_path):
        """Test loading config without 'repos' key."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("other_key: value\n")

        manager = AutoMergingManager(str(config_file))
        result = manager.load_config()
        assert result is None

    def test_load_config_valid_single_repo(self, tmp_path):
        """Test loading valid config with single repo."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    enabled: true
    merge_method: squash
    label: dependencies
""")

        manager = AutoMergingManager(str(config_file))
        result = manager.load_config()

        assert result is not None
        assert len(result) == 1
        assert result[0].repo == "github.com/owner/repo"
        assert result[0].enabled is True
        assert result[0].merge_method == "squash"
        assert result[0].label == "dependencies"

    def test_load_config_valid_multiple_repos(self, tmp_path):
        """Test loading valid config with multiple repos."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/org1/repo1
    enabled: true
  - url: https://github.com/org2/repo2
    enabled: false
    merge_method: rebase
""")

        manager = AutoMergingManager(str(config_file))
        result = manager.load_config()

        assert result is not None
        assert len(result) == 2
        assert result[0].repo == "github.com/org1/repo1"
        assert result[0].enabled is True
        assert result[1].repo == "github.com/org2/repo2"
        assert result[1].enabled is False
        assert result[1].merge_method == "rebase"

    def test_load_config_uses_defaults(self, tmp_path):
        """Test that missing fields use default values."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    enabled: true
""")

        manager = AutoMergingManager(str(config_file))
        result = manager.load_config()

        assert result is not None
        assert len(result) == 1
        assert result[0].merge_method == DEFAULT_MERGE_METHOD
        assert result[0].label == DEFAULT_LABEL

    def test_load_config_enabled_defaults_to_false(self, tmp_path):
        """Test that enabled defaults to false when not specified."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
""")

        manager = AutoMergingManager(str(config_file))
        result = manager.load_config()

        assert result is not None
        assert len(result) == 1
        assert result[0].enabled is False

    def test_load_config_invalid_yaml_raises_error(self, tmp_path):
        """Test that invalid YAML raises AutoMergingLoadError."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("invalid: yaml: content: [\n")

        manager = AutoMergingManager(str(config_file))
        with pytest.raises(AutoMergingLoadError, match="Invalid YAML"):
            manager.load_config()

    def test_load_config_not_a_dict_raises_error(self, tmp_path):
        """Test that non-dict root raises AutoMergingLoadError."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("- item1\n- item2\n")

        manager = AutoMergingManager(str(config_file))
        with pytest.raises(AutoMergingLoadError, match="must be a YAML mapping"):
            manager.load_config()

    def test_load_config_repos_not_list_raises_error(self, tmp_path):
        """Test that non-list 'repos' raises AutoMergingLoadError."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("repos: not_a_list\n")

        manager = AutoMergingManager(str(config_file))
        with pytest.raises(AutoMergingLoadError, match="'repos' must be a list"):
            manager.load_config()

    def test_load_config_missing_url_raises_error(self, tmp_path):
        """Test that entry without 'url' raises AutoMergingLoadError."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - enabled: true
""")

        manager = AutoMergingManager(str(config_file))
        with pytest.raises(AutoMergingLoadError, match="missing required field 'url'"):
            manager.load_config()

    def test_load_config_invalid_url_raises_error(self, tmp_path):
        """Test that invalid URL raises AutoMergingLoadError."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: invalid_url
    enabled: true
""")

        manager = AutoMergingManager(str(config_file))
        with pytest.raises(AutoMergingLoadError, match="invalid url"):
            manager.load_config()

    def test_load_config_invalid_enabled_type(self, tmp_path):
        """Test that non-boolean enabled raises error."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    enabled: "yes"
""")

        manager = AutoMergingManager(str(config_file))
        with pytest.raises(AutoMergingLoadError, match="must be a boolean"):
            manager.load_config()

    def test_load_config_invalid_merge_method_type(self, tmp_path):
        """Test that non-string merge_method raises error."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    enabled: true
    merge_method: 123
""")

        manager = AutoMergingManager(str(config_file))
        with pytest.raises(AutoMergingLoadError, match="must be a string"):
            manager.load_config()

    def test_load_config_invalid_merge_method_value(self, tmp_path):
        """Test that invalid merge_method value raises error."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    enabled: true
    merge_method: fast-forward
""")

        manager = AutoMergingManager(str(config_file))
        with pytest.raises(AutoMergingLoadError, match="must be one of"):
            manager.load_config()

    def test_load_config_invalid_label_type(self, tmp_path):
        """Test that non-string label raises error."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    enabled: true
    label: 123
""")

        manager = AutoMergingManager(str(config_file))
        with pytest.raises(AutoMergingLoadError, match="must be a string"):
            manager.load_config()

    def test_load_config_empty_label_raises_error(self, tmp_path):
        """Test that empty label raises error."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    enabled: true
    label: "   "
""")

        manager = AutoMergingManager(str(config_file))
        with pytest.raises(AutoMergingLoadError, match="cannot be empty"):
            manager.load_config()

    def test_load_config_sets_cache(self, tmp_path):
        """Test that load_config sets the cached entries."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    enabled: true
""")

        manager = AutoMergingManager(str(config_file))
        assert manager._cached_entries is None

        result = manager.load_config()

        # Cache should now be set
        assert manager._cached_entries is not None
        assert result is manager._cached_entries
        assert len(result) == 1

    def test_load_config_all_merge_methods(self, tmp_path):
        """Test that all valid merge methods are accepted."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo1
    enabled: true
    merge_method: merge
  - url: https://github.com/owner/repo2
    enabled: true
    merge_method: squash
  - url: https://github.com/owner/repo3
    enabled: true
    merge_method: rebase
""")

        manager = AutoMergingManager(str(config_file))
        result = manager.load_config()

        assert result is not None
        assert len(result) == 3
        assert result[0].merge_method == "merge"
        assert result[1].merge_method == "squash"
        assert result[2].merge_method == "rebase"

    def test_load_config_github_enterprise(self, tmp_path):
        """Test loading config with GitHub Enterprise URL."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://ghes.example.com/myorg/myrepo
    enabled: true
""")

        manager = AutoMergingManager(str(config_file))
        result = manager.load_config()

        assert result is not None
        assert len(result) == 1
        assert result[0].repo == "ghes.example.com/myorg/myrepo"


# =============================================================================
# Tests for AutoMergingManager.get_config()
# =============================================================================
@pytest.mark.unit
class TestAutoMergingManagerGetConfig:
    """Tests for AutoMergingManager.get_config()."""

    def test_get_config_found(self, tmp_path):
        """Test getting config for a matching repo."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    enabled: true
    merge_method: rebase
""")

        manager = AutoMergingManager(str(config_file))
        result = manager.get_config("github.com/owner/repo")

        assert result is not None
        assert result.enabled is True
        assert result.merge_method == "rebase"

    def test_get_config_not_found(self, tmp_path):
        """Test getting config for a non-matching repo."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    enabled: true
""")

        manager = AutoMergingManager(str(config_file))
        result = manager.get_config("github.com/other/repo")

        assert result is None

    def test_get_config_case_insensitive(self, tmp_path):
        """Test that repo matching is case-insensitive."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/Owner/Repo
    enabled: true
""")

        manager = AutoMergingManager(str(config_file))
        result = manager.get_config("github.com/owner/repo")

        assert result is not None
        assert result.enabled is True

    def test_get_config_no_config_file(self, tmp_path):
        """Test getting config when config file doesn't exist."""
        manager = AutoMergingManager(str(tmp_path / "nonexistent.yaml"))
        result = manager.get_config("github.com/owner/repo")

        assert result is None

    def test_get_config_loads_on_demand(self, tmp_path):
        """Test that config is loaded on demand if not cached."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    enabled: true
""")

        manager = AutoMergingManager(str(config_file))
        # Don't call load_config first
        result = manager.get_config("github.com/owner/repo")

        assert result is not None
        assert result.enabled is True


# =============================================================================
# Tests for AutoMergingManager.has_config()
# =============================================================================
@pytest.mark.unit
class TestAutoMergingManagerHasConfig:
    """Tests for AutoMergingManager.has_config()."""

    def test_has_config_true(self, tmp_path):
        """Test has_config returns True when repos exist."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    enabled: true
""")

        manager = AutoMergingManager(str(config_file))
        assert manager.has_config() is True

    def test_has_config_false_no_file(self, tmp_path):
        """Test has_config returns False when file doesn't exist."""
        manager = AutoMergingManager(str(tmp_path / "nonexistent.yaml"))
        assert manager.has_config() is False

    def test_has_config_false_empty_repos(self, tmp_path):
        """Test has_config returns False when repos list is empty."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("repos: []\n")

        manager = AutoMergingManager(str(config_file))
        # Empty list returns None from load_config
        assert manager.has_config() is False


# =============================================================================
# Tests for AutoMergingManager.validate_config()
# =============================================================================
@pytest.mark.unit
class TestAutoMergingManagerValidateConfig:
    """Tests for AutoMergingManager.validate_config()."""

    def test_validate_config_no_warnings(self, tmp_path):
        """Test validate_config returns empty list for valid config."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    enabled: true
    merge_method: squash
    label: dependencies
""")

        manager = AutoMergingManager(str(config_file))
        warnings = manager.validate_config()

        assert warnings == []

    def test_validate_config_duplicate_repos(self, tmp_path):
        """Test validate_config warns about duplicate repos."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    enabled: true
  - url: https://github.com/owner/repo
    enabled: false
""")

        manager = AutoMergingManager(str(config_file))
        warnings = manager.validate_config()

        assert len(warnings) == 1
        assert "Duplicate repository entry" in warnings[0]

    def test_validate_config_all_disabled(self, tmp_path):
        """Test validate_config warns when all repos are disabled."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo1
    enabled: false
  - url: https://github.com/owner/repo2
    enabled: false
""")

        manager = AutoMergingManager(str(config_file))
        warnings = manager.validate_config()

        assert len(warnings) == 1
        assert "All 2 repository entries are disabled" in warnings[0]

    def test_validate_config_returns_error_on_invalid_yaml(self, tmp_path):
        """Test validate_config returns error message for invalid YAML."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("invalid: yaml: [\n")

        manager = AutoMergingManager(str(config_file))
        warnings = manager.validate_config()

        assert len(warnings) == 1
        assert "Invalid YAML" in warnings[0]


# =============================================================================
# Tests for AutoMergingManager.get_enabled_repos()
# =============================================================================
@pytest.mark.unit
class TestAutoMergingManagerGetEnabledRepos:
    """Tests for AutoMergingManager.get_enabled_repos()."""

    def test_get_enabled_repos_returns_only_enabled(self, tmp_path):
        """Test that get_enabled_repos returns only enabled repos."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo1
    enabled: true
  - url: https://github.com/owner/repo2
    enabled: false
  - url: https://github.com/owner/repo3
    enabled: true
""")

        manager = AutoMergingManager(str(config_file))
        result = manager.get_enabled_repos()

        assert len(result) == 2
        assert result[0].repo == "github.com/owner/repo1"
        assert result[1].repo == "github.com/owner/repo3"

    def test_get_enabled_repos_empty_when_all_disabled(self, tmp_path):
        """Test that get_enabled_repos returns empty list when all disabled."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    enabled: false
""")

        manager = AutoMergingManager(str(config_file))
        result = manager.get_enabled_repos()

        assert result == []

    def test_get_enabled_repos_empty_when_no_file(self, tmp_path):
        """Test that get_enabled_repos returns empty list when no file exists."""
        manager = AutoMergingManager(str(tmp_path / "nonexistent.yaml"))
        result = manager.get_enabled_repos()

        assert result == []

    def test_get_enabled_repos_empty_when_invalid_config(self, tmp_path):
        """Test that get_enabled_repos returns empty list on invalid config."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("invalid: yaml: [\n")

        manager = AutoMergingManager(str(config_file))
        result = manager.get_enabled_repos()

        assert result == []


# =============================================================================
# Tests for AutoMergingManager.clear_cache()
# =============================================================================
@pytest.mark.unit
class TestAutoMergingManagerClearCache:
    """Tests for AutoMergingManager.clear_cache()."""

    def test_clear_cache(self, tmp_path):
        """Test that clear_cache clears the cached config."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    enabled: true
""")

        manager = AutoMergingManager(str(config_file))

        # Load config to populate cache
        manager.load_config()
        assert manager._cached_entries is not None

        # Clear cache
        manager.clear_cache()
        assert manager._cached_entries is None


# =============================================================================
# Tests for label stripping
# =============================================================================
@pytest.mark.unit
class TestAutoMergingLabelHandling:
    """Tests for label handling in auto-merging config."""

    def test_label_is_stripped(self, tmp_path):
        """Test that label whitespace is stripped."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
repos:
  - url: https://github.com/owner/repo
    enabled: true
    label: "  custom-label  "
""")

        manager = AutoMergingManager(str(config_file))
        result = manager.load_config()

        assert result is not None
        assert result[0].label == "custom-label"
