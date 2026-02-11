"""Unit tests for Daemon _maybe_archive_closed() lazy loading behavior.

These tests verify that the daemon correctly:
- Lazy loads project metadata when not cached
- Logs warning once when metadata fetch fails
- Silently skips subsequent calls after fetch failure
- Proceeds with archiving when metadata is already cached
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

from src.daemon import Daemon
from src.database import ProjectMetadata
from src.interfaces.ticket import TicketItem


@pytest.fixture
def daemon(temp_workspace_dir):
    """Fixture providing Daemon with mocked dependencies."""
    config = MagicMock()
    config.poll_interval = 60
    config.watched_statuses = ["Research", "Plan", "Implement"]
    config.max_concurrent_workflows = 2
    config.database_path = f"{temp_workspace_dir}/test.db"
    config.workspace_dir = temp_workspace_dir
    config.project_urls = ["https://github.com/orgs/test/projects/1"]

    config.github_enterprise_version = None
    config.username_self = "test-bot"

    with patch("src.ticket_clients.github.GitHubTicketClient"):
        daemon = Daemon(config)
        daemon.ticket_client = MagicMock()
        daemon.runner = MagicMock()
        daemon.database = MagicMock()
        yield daemon
        daemon.stop()


def make_closed_ticket_item(
    state_reason: str = "NOT_PLANNED",
    has_merged_changes: bool = False,
    board_url: str = "https://github.com/orgs/test-org/projects/1",
    ticket_id: int = 42,
) -> TicketItem:
    """Helper to create a closed TicketItem for testing."""
    return TicketItem(
        item_id="PVTI_item123",
        board_url=board_url,
        ticket_id=ticket_id,
        repo="github.com/test-org/test-repo",
        status="Unknown",
        title="Test Issue",
        labels=set(),
        state="CLOSED",
        state_reason=state_reason,
        has_merged_changes=has_merged_changes,
        comment_count=0,
    )


@pytest.mark.unit
class TestMaybeArchiveClosedLazyLoading:
    """Tests for _maybe_archive_closed() lazy loading behavior."""

    def test_lazy_fetch_succeeds_archiving_proceeds(self, daemon, caplog):
        """Test that when metadata is missing but lazy fetch succeeds, archiving proceeds."""
        item = make_closed_ticket_item()

        # No metadata cached
        daemon._project_metadata = {}
        daemon._archive_metadata_fetch_failed = set()

        # Mock successful metadata fetch
        daemon.ticket_client.get_board_metadata.return_value = {
            "project_id": "PVT_proj123",
            "status_field_id": "PVTSSF_field456",
            "status_options": {"Done": "option789"},
        }
        daemon.ticket_client.archive_item.return_value = True

        with caplog.at_level(logging.INFO):
            daemon._maybe_archive_closed(item)

        # Verify metadata was fetched
        daemon.ticket_client.get_board_metadata.assert_called_once_with(item.board_url)

        # Verify metadata was cached
        assert item.board_url in daemon._project_metadata
        cached = daemon._project_metadata[item.board_url]
        assert cached.project_id == "PVT_proj123"

        # Verify metadata was persisted
        daemon.database.upsert_project_metadata.assert_called_once()

        # Verify archive was called
        daemon.ticket_client.archive_item.assert_called_once_with(
            "PVT_proj123", "PVTI_item123", hostname="github.com"
        )

        # Verify info logs for lazy loading
        assert "Project metadata not cached" in caplog.text
        assert "Successfully fetched and cached metadata" in caplog.text

    def test_lazy_fetch_fails_warning_logged_once(self, daemon, caplog):
        """Test that when metadata fetch fails, warning is logged once."""
        item = make_closed_ticket_item()

        # No metadata cached
        daemon._project_metadata = {}
        daemon._archive_metadata_fetch_failed = set()

        # Mock failed metadata fetch (returns None)
        daemon.ticket_client.get_board_metadata.return_value = None

        with caplog.at_level(logging.WARNING):
            daemon._maybe_archive_closed(item)

        # Verify metadata fetch was attempted
        daemon.ticket_client.get_board_metadata.assert_called_once_with(item.board_url)

        # Verify warning was logged
        assert "Failed to fetch project metadata" in caplog.text

        # Verify URL was added to failed set
        assert item.board_url in daemon._archive_metadata_fetch_failed

        # Verify archive was NOT called
        daemon.ticket_client.archive_item.assert_not_called()

    def test_lazy_fetch_exception_warning_logged_once(self, daemon, caplog):
        """Test that when metadata fetch raises exception, warning is logged once."""
        item = make_closed_ticket_item()

        # No metadata cached
        daemon._project_metadata = {}
        daemon._archive_metadata_fetch_failed = set()

        # Mock metadata fetch raising exception
        daemon.ticket_client.get_board_metadata.side_effect = Exception("API Error")

        with caplog.at_level(logging.WARNING):
            daemon._maybe_archive_closed(item)

        # Verify metadata fetch was attempted
        daemon.ticket_client.get_board_metadata.assert_called_once_with(item.board_url)

        # Verify warning was logged
        assert "Error fetching project metadata" in caplog.text
        assert "API Error" in caplog.text

        # Verify URL was added to failed set
        assert item.board_url in daemon._archive_metadata_fetch_failed

        # Verify archive was NOT called
        daemon.ticket_client.archive_item.assert_not_called()

    def test_subsequent_calls_are_silent_after_failure(self, daemon, caplog):
        """Test that after metadata fetch failure, subsequent calls are silent."""
        item = make_closed_ticket_item()

        # No metadata cached, but URL already in failed set
        daemon._project_metadata = {}
        daemon._archive_metadata_fetch_failed = {item.board_url}

        with caplog.at_level(logging.WARNING):
            daemon._maybe_archive_closed(item)

        # Verify NO fetch attempt (URL already in failed set)
        daemon.ticket_client.get_board_metadata.assert_not_called()

        # Verify NO warning logged (silent skip)
        assert "Failed to fetch" not in caplog.text
        assert "Error fetching" not in caplog.text

        # Verify archive was NOT called
        daemon.ticket_client.archive_item.assert_not_called()

    def test_cached_metadata_no_fetch_attempt(self, daemon, caplog):
        """Test that when metadata is cached, no fetch is attempted and archiving proceeds."""
        item = make_closed_ticket_item()

        # Metadata already cached
        daemon._project_metadata = {
            item.board_url: ProjectMetadata(
                project_url=item.board_url,
                repo="test-org/test-repo",
                project_id="PVT_cached123",
                status_field_id="PVTSSF_field456",
                status_options={"Done": "option789"},
            )
        }
        daemon._archive_metadata_fetch_failed = set()
        daemon.ticket_client.archive_item.return_value = True

        with caplog.at_level(logging.INFO):
            daemon._maybe_archive_closed(item)

        # Verify NO fetch attempt (metadata already cached)
        daemon.ticket_client.get_board_metadata.assert_not_called()

        # Verify archive was called with cached project_id
        daemon.ticket_client.archive_item.assert_called_once_with(
            "PVT_cached123", "PVTI_item123", hostname="github.com"
        )

        # Verify no lazy loading logs
        assert "Project metadata not cached" not in caplog.text

    def test_lazy_fetch_returns_empty_project_id(self, daemon, caplog):
        """Test that when lazy fetch returns empty project_id, warning is logged."""
        item = make_closed_ticket_item()

        # No metadata cached
        daemon._project_metadata = {}
        daemon._archive_metadata_fetch_failed = set()

        # Mock metadata fetch returning empty project_id
        daemon.ticket_client.get_board_metadata.return_value = {
            "project_id": None,
            "status_field_id": "PVTSSF_field456",
        }

        with caplog.at_level(logging.WARNING):
            daemon._maybe_archive_closed(item)

        # Verify warning was logged
        assert "Failed to fetch project metadata" in caplog.text

        # Verify URL was added to failed set
        assert item.board_url in daemon._archive_metadata_fetch_failed

        # Verify archive was NOT called
        daemon.ticket_client.archive_item.assert_not_called()

    def test_open_issue_not_processed(self, daemon):
        """Test that open issues are not processed for archiving."""
        item = TicketItem(
            item_id="PVTI_item123",
            board_url="https://github.com/orgs/test-org/projects/1",
            ticket_id=42,
            repo="github.com/test-org/test-repo",
            status="Unknown",
            title="Test Issue",
            labels=set(),
            state="OPEN",  # Open issue
            state_reason=None,
            has_merged_changes=False,
            comment_count=0,
        )

        daemon._project_metadata = {}

        daemon._maybe_archive_closed(item)

        # Verify no fetch attempt (issue is open)
        daemon.ticket_client.get_board_metadata.assert_not_called()
        daemon.ticket_client.archive_item.assert_not_called()

    def test_completed_with_merged_pr_not_archived(self, daemon):
        """Test that COMPLETED with merged PR is not archived (goes to Done instead)."""
        item = make_closed_ticket_item(state_reason="COMPLETED", has_merged_changes=True)

        daemon._project_metadata = {
            item.board_url: ProjectMetadata(
                project_url=item.board_url,
                repo="test-org/test-repo",
                project_id="PVT_proj123",
            )
        }

        daemon._maybe_archive_closed(item)

        # Verify archive was NOT called (goes to Done, not archived)
        daemon.ticket_client.archive_item.assert_not_called()

    def test_enterprise_hostname_extracted_correctly(self, daemon):
        """Test that enterprise hostnames are correctly extracted for archiving."""
        item = make_closed_ticket_item(
            board_url="https://github.example.com/orgs/enterprise-org/projects/5"
        )
        item = TicketItem(
            item_id="PVTI_enterprise456",
            board_url="https://github.example.com/orgs/enterprise-org/projects/5",
            ticket_id=99,
            repo="github.example.com/enterprise-org/enterprise-repo",
            status="Unknown",
            title="Enterprise Issue",
            labels=set(),
            state="CLOSED",
            state_reason="NOT_PLANNED",
            has_merged_changes=False,
            comment_count=0,
        )

        daemon._project_metadata = {
            item.board_url: ProjectMetadata(
                project_url=item.board_url,
                repo="enterprise-org/enterprise-repo",
                project_id="PVT_enterprise123",
            )
        }
        daemon.ticket_client.archive_item.return_value = True

        daemon._maybe_archive_closed(item)

        # Verify archive was called with enterprise hostname
        daemon.ticket_client.archive_item.assert_called_once_with(
            "PVT_enterprise123", "PVTI_enterprise456", hostname="github.example.com"
        )
