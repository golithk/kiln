"""Unit tests for the merge queue daemon handler.

These tests verify the auto-merging functionality in the Daemon class:
- _poll_merge_queue() discovers and processes configured repos
- _process_repo_merge_queue() handles queue discovery, population, and cleanup
- _process_single_pr() processes one PR through the full merge flow
- _recover_merge_queue_from_labels() recovers state after daemon restart
- _trigger_next_pr_rebase() comments on next PR in queue
"""

from unittest.mock import MagicMock, patch

import pytest

from src.daemon import Daemon
from src.database import MergeQueueEntry
from src.integrations.auto_merging import AutoMergingEntry
from src.interfaces import CheckRunResult
from src.labels import Labels


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
    config.username_self = "kiln-bot"
    config.team_usernames = []

    with patch("src.ticket_clients.github.GitHubTicketClient"):
        daemon = Daemon(config)
        daemon.ticket_client = MagicMock()
        daemon.ticket_client.supports_status_actor_check = True
        daemon.auto_merging_manager = MagicMock()
        # Mock database methods for merge queue
        daemon.database.add_to_merge_queue = MagicMock()
        daemon.database.get_merge_queue = MagicMock(return_value=[])
        daemon.database.get_merge_queue_by_status = MagicMock(return_value=None)
        daemon.database.update_merge_queue_status = MagicMock()
        daemon.database.remove_from_merge_queue = MagicMock()
        yield daemon
        daemon.stop()


@pytest.fixture
def auto_merge_config():
    """Fixture providing a sample AutoMergingEntry configuration."""
    return AutoMergingEntry(
        repo="github.com/test-org/test-repo",
        enabled=True,
        merge_method="squash",
        label="dependencies",
    )


@pytest.fixture
def mock_pr_list():
    """Fixture providing sample PR list from GitHub API."""
    return [
        {
            "number": 101,
            "title": "Bump dependency-a",
            "createdAt": "2024-01-15T10:00:00Z",
            "headRefOid": "abc123",
        },
        {
            "number": 102,
            "title": "Bump dependency-b",
            "createdAt": "2024-01-15T11:00:00Z",
            "headRefOid": "def456",
        },
        {
            "number": 103,
            "title": "Bump dependency-c",
            "createdAt": "2024-01-15T12:00:00Z",
            "headRefOid": "ghi789",
        },
    ]


@pytest.mark.unit
class TestPollMergeQueue:
    """Tests for _poll_merge_queue method."""

    def test_poll_merge_queue_skipped_when_no_config(self, daemon):
        """Test that polling is skipped when no repos are configured."""
        daemon.auto_merging_manager.get_enabled_repos.return_value = []

        daemon._poll_merge_queue()

        # No repos processed
        daemon.ticket_client.list_prs_by_label.assert_not_called()

    def test_poll_merge_queue_processes_enabled_repos(self, daemon, auto_merge_config):
        """Test that polling processes all enabled repos."""
        daemon.auto_merging_manager.get_enabled_repos.return_value = [auto_merge_config]
        daemon.ticket_client.list_prs_by_label.return_value = []
        daemon.database.get_merge_queue = MagicMock(return_value=[])

        daemon._poll_merge_queue()

        # Should query PRs for the configured repo (3 calls: auto-merging, auto-merge-queue, dependencies)
        assert daemon.ticket_client.list_prs_by_label.call_count == 3
        # Verify the final call is for the configured label
        daemon.ticket_client.list_prs_by_label.assert_any_call(
            "github.com/test-org/test-repo", "dependencies"
        )

    def test_poll_merge_queue_handles_errors_gracefully(self, daemon, auto_merge_config):
        """Test that errors processing one repo don't affect others."""
        config2 = AutoMergingEntry(
            repo="github.com/test-org/other-repo",
            enabled=True,
            merge_method="squash",
            label="dependencies",
        )
        daemon.auto_merging_manager.get_enabled_repos.return_value = [auto_merge_config, config2]

        # First repo raises error on first call
        daemon.ticket_client.list_prs_by_label.side_effect = [
            Exception("API error"),
            [],  # Second repo recovery calls
            [],
            [],
        ]
        daemon.database.get_merge_queue = MagicMock(return_value=[])

        # Should not raise
        daemon._poll_merge_queue()

        # Both repos were attempted (first one failed early)
        assert daemon.ticket_client.list_prs_by_label.call_count >= 2


@pytest.mark.unit
class TestProcessRepoMergeQueue:
    """Tests for _process_repo_merge_queue method."""

    def test_new_prs_added_to_queue(self, daemon, auto_merge_config, mock_pr_list):
        """Test that new PRs are discovered and added to queue."""
        # Recovery returns empty, main list returns PRs
        daemon.ticket_client.list_prs_by_label.side_effect = [[], [], mock_pr_list]
        daemon.database.get_merge_queue = MagicMock(return_value=[])
        daemon.database.get_merge_queue_by_status = MagicMock(return_value=None)
        daemon.ticket_client.get_pr_state.return_value = "OPEN"
        daemon.ticket_client.get_pr_merge_state.return_value = {
            "mergeStateStatus": "CLEAN",
            "mergeable": "MERGEABLE",
            "reviewDecision": "",
        }
        daemon.ticket_client.get_pr_head_sha.return_value = "abc123"
        daemon.ticket_client.get_check_runs.return_value = []

        daemon._process_repo_merge_queue(auto_merge_config)

        # All 3 PRs should be added to queue
        assert daemon.database.add_to_merge_queue.call_count == 3

        # Queue labels should be added
        add_label_calls = [
            call
            for call in daemon.ticket_client.add_label.call_args_list
            if call[0][2] == Labels.AUTO_MERGE_QUEUE
        ]
        assert len(add_label_calls) == 3

    def test_existing_prs_not_re_added(self, daemon, auto_merge_config, mock_pr_list):
        """Test that PRs already in queue are not re-added."""
        # Recovery returns empty, main list returns PRs
        daemon.ticket_client.list_prs_by_label.side_effect = [[], [], mock_pr_list]

        # PR 101 already in queue
        existing_entry = MergeQueueEntry(
            repo="github.com/test-org/test-repo",
            pr_number=101,
            position=0,
            status="queued",
            queued_at=MagicMock(),
        )
        daemon.database.get_merge_queue = MagicMock(return_value=[existing_entry])
        daemon.database.get_merge_queue_by_status = MagicMock(return_value=None)
        daemon.ticket_client.get_pr_state.return_value = "OPEN"
        daemon.ticket_client.get_pr_merge_state.return_value = {
            "mergeStateStatus": "CLEAN",
            "mergeable": "MERGEABLE",
            "reviewDecision": "",
        }
        daemon.ticket_client.get_pr_head_sha.return_value = "def456"
        daemon.ticket_client.get_check_runs.return_value = []

        daemon._process_repo_merge_queue(auto_merge_config)

        # Only PRs 102 and 103 should be added (not 101)
        add_calls = daemon.database.add_to_merge_queue.call_args_list
        pr_numbers_added = [call[0][1] for call in add_calls]
        assert 101 not in pr_numbers_added
        assert 102 in pr_numbers_added
        assert 103 in pr_numbers_added

    def test_merged_prs_removed_from_queue(self, daemon, auto_merge_config):
        """Test that merged PRs are removed from queue."""
        daemon.ticket_client.list_prs_by_label.return_value = []

        merged_entry = MergeQueueEntry(
            repo="github.com/test-org/test-repo",
            pr_number=100,
            position=0,
            status="queued",
            queued_at=MagicMock(),
        )
        daemon.database.get_merge_queue = MagicMock(return_value=[merged_entry])
        daemon.ticket_client.get_pr_state.return_value = "MERGED"

        daemon._process_repo_merge_queue(auto_merge_config)

        # PR should be removed from queue
        daemon.database.remove_from_merge_queue.assert_called_with(
            "github.com/test-org/test-repo", 100
        )

    def test_closed_prs_removed_from_queue(self, daemon, auto_merge_config):
        """Test that closed PRs are removed from queue."""
        daemon.ticket_client.list_prs_by_label.return_value = []

        closed_entry = MergeQueueEntry(
            repo="github.com/test-org/test-repo",
            pr_number=100,
            position=0,
            status="queued",
            queued_at=MagicMock(),
        )
        daemon.database.get_merge_queue = MagicMock(return_value=[closed_entry])
        daemon.ticket_client.get_pr_state.return_value = "CLOSED"

        daemon._process_repo_merge_queue(auto_merge_config)

        # PR should be removed from queue
        daemon.database.remove_from_merge_queue.assert_called_with(
            "github.com/test-org/test-repo", 100
        )

    def test_processes_only_first_pr_in_queue(self, daemon, auto_merge_config):
        """Test that only the first PR in queue is processed (sequential)."""
        daemon.ticket_client.list_prs_by_label.return_value = []

        entries = [
            MergeQueueEntry(
                repo="github.com/test-org/test-repo",
                pr_number=101,
                position=0,
                status="queued",
                queued_at=MagicMock(),
            ),
            MergeQueueEntry(
                repo="github.com/test-org/test-repo",
                pr_number=102,
                position=1,
                status="queued",
                queued_at=MagicMock(),
            ),
        ]
        daemon.database.get_merge_queue = MagicMock(return_value=entries)
        daemon.ticket_client.get_pr_state.return_value = "OPEN"
        daemon.ticket_client.get_pr_merge_state.return_value = {
            "mergeStateStatus": "CLEAN",
            "mergeable": "MERGEABLE",
            "reviewDecision": "APPROVED",
        }
        daemon.ticket_client.get_pr_head_sha.return_value = "abc123"
        daemon.ticket_client.get_check_runs.return_value = [
            CheckRunResult(name="build", status="completed", conclusion="success")
        ]
        daemon.ticket_client.merge_pr.return_value = True

        daemon._process_repo_merge_queue(auto_merge_config)

        # Only first PR (101) should be processed
        daemon.ticket_client.merge_pr.assert_called_once_with(
            "github.com/test-org/test-repo", 101, "squash"
        )


@pytest.mark.unit
class TestProcessSinglePr:
    """Tests for _process_single_pr method."""

    def test_merge_pr_with_passing_ci_and_approval(self, daemon):
        """Test that PR with passing CI and approval is merged."""
        daemon.ticket_client.get_pr_merge_state.return_value = {
            "mergeStateStatus": "CLEAN",
            "mergeable": "MERGEABLE",
            "reviewDecision": "APPROVED",
        }
        daemon.ticket_client.get_pr_head_sha.return_value = "abc123"
        daemon.ticket_client.get_check_runs.return_value = [
            CheckRunResult(name="build", status="completed", conclusion="success"),
        ]
        daemon.ticket_client.merge_pr.return_value = True
        daemon.database.get_merge_queue = MagicMock(return_value=[])

        daemon._process_single_pr("github.com/test-org/test-repo", 101, "squash")

        daemon.ticket_client.merge_pr.assert_called_once_with(
            "github.com/test-org/test-repo", 101, "squash"
        )

    def test_approves_pr_before_merge(self, daemon):
        """Test that PR is approved if not already approved."""
        daemon.ticket_client.get_pr_merge_state.return_value = {
            "mergeStateStatus": "BLOCKED",
            "mergeable": "MERGEABLE",
            "reviewDecision": "",  # Not approved
        }
        daemon.ticket_client.get_pr_head_sha.return_value = "abc123"
        daemon.ticket_client.get_check_runs.return_value = [
            CheckRunResult(name="build", status="completed", conclusion="success"),
        ]
        daemon.ticket_client.approve_pr.return_value = True
        daemon.ticket_client.merge_pr.return_value = True
        daemon.database.get_merge_queue = MagicMock(return_value=[])

        daemon._process_single_pr("github.com/test-org/test-repo", 101, "squash")

        # Should approve first
        daemon.ticket_client.approve_pr.assert_called_once_with(
            "github.com/test-org/test-repo", 101
        )
        # Then merge
        daemon.ticket_client.merge_pr.assert_called_once()

    def test_triggers_rebase_when_behind(self, daemon):
        """Test that rebase is triggered when PR is behind base branch."""
        daemon.ticket_client.get_pr_merge_state.return_value = {
            "mergeStateStatus": "BEHIND",
            "mergeable": "MERGEABLE",
            "reviewDecision": "",
        }
        daemon.database.get_merge_queue_by_status = MagicMock(return_value=None)
        daemon.ticket_client.comment_on_pr.return_value = True

        daemon._process_single_pr("github.com/test-org/test-repo", 101, "squash")

        # Should comment to trigger rebase
        daemon.ticket_client.comment_on_pr.assert_called_once_with(
            "github.com/test-org/test-repo", 101, "@dependabot rebase"
        )
        # Should NOT try to merge
        daemon.ticket_client.merge_pr.assert_not_called()

    def test_waits_when_ci_running(self, daemon):
        """Test that processing waits when CI is still running."""
        daemon.ticket_client.get_pr_merge_state.return_value = {
            "mergeStateStatus": "BLOCKED",
            "mergeable": "MERGEABLE",
            "reviewDecision": "",
        }
        daemon.ticket_client.get_pr_head_sha.return_value = "abc123"
        daemon.ticket_client.get_check_runs.return_value = [
            CheckRunResult(name="build", status="in_progress", conclusion=None),
        ]
        daemon.database.get_merge_queue_by_status = MagicMock(return_value=None)

        daemon._process_single_pr("github.com/test-org/test-repo", 101, "squash")

        # Should NOT try to merge
        daemon.ticket_client.merge_pr.assert_not_called()
        # Should update status to waiting_ci
        daemon.database.update_merge_queue_status.assert_called_with(
            "github.com/test-org/test-repo", 101, "waiting_ci"
        )

    def test_waits_when_ci_failed(self, daemon):
        """Test that processing waits when CI has failed."""
        daemon.ticket_client.get_pr_merge_state.return_value = {
            "mergeStateStatus": "UNSTABLE",
            "mergeable": "MERGEABLE",
            "reviewDecision": "",
        }
        daemon.ticket_client.get_pr_head_sha.return_value = "abc123"
        daemon.ticket_client.get_check_runs.return_value = [
            CheckRunResult(name="build", status="completed", conclusion="failure"),
        ]

        daemon._process_single_pr("github.com/test-org/test-repo", 101, "squash")

        # Should NOT try to merge
        daemon.ticket_client.merge_pr.assert_not_called()

    def test_skips_pr_with_conflicts(self, daemon):
        """Test that PR with conflicts is skipped."""
        daemon.ticket_client.get_pr_merge_state.return_value = {
            "mergeStateStatus": "DIRTY",
            "mergeable": "CONFLICTING",
            "reviewDecision": "",
        }

        daemon._process_single_pr("github.com/test-org/test-repo", 101, "squash")

        # Should NOT try to merge
        daemon.ticket_client.merge_pr.assert_not_called()
        # Should NOT trigger rebase
        daemon.ticket_client.comment_on_pr.assert_not_called()


@pytest.mark.unit
class TestRecoverMergeQueueFromLabels:
    """Tests for _recover_merge_queue_from_labels method."""

    def test_recovers_pr_with_auto_merging_label(self, daemon):
        """Test that PR with auto-merging label is recovered to queue."""
        daemon.database.get_merge_queue = MagicMock(return_value=[])
        daemon.ticket_client.list_prs_by_label.side_effect = [
            [{"number": 101, "createdAt": "2024-01-15T10:00:00Z"}],  # auto-merging
            [],  # auto-merge-queue
        ]

        daemon._recover_merge_queue_from_labels("github.com/test-org/test-repo", "dependencies")

        # Should add to queue with merging status
        daemon.database.add_to_merge_queue.assert_called_once_with(
            "github.com/test-org/test-repo", 101, position=0
        )
        daemon.database.update_merge_queue_status.assert_called_once_with(
            "github.com/test-org/test-repo", 101, "merging"
        )

    def test_recovers_pr_with_queue_label(self, daemon):
        """Test that PR with auto-merge-queue label is recovered to queue."""
        daemon.database.get_merge_queue = MagicMock(return_value=[])
        daemon.ticket_client.list_prs_by_label.side_effect = [
            [],  # auto-merging
            [{"number": 102, "createdAt": "2024-01-15T10:00:00Z"}],  # auto-merge-queue
        ]

        daemon._recover_merge_queue_from_labels("github.com/test-org/test-repo", "dependencies")

        # Should add to queue
        daemon.database.add_to_merge_queue.assert_called_once()

    def test_does_not_duplicate_existing_entries(self, daemon):
        """Test that PRs already in queue are not re-added during recovery."""
        existing_entry = MergeQueueEntry(
            repo="github.com/test-org/test-repo",
            pr_number=101,
            position=0,
            status="merging",
            queued_at=MagicMock(),
        )
        daemon.database.get_merge_queue = MagicMock(return_value=[existing_entry])
        daemon.ticket_client.list_prs_by_label.side_effect = [
            [{"number": 101, "createdAt": "2024-01-15T10:00:00Z"}],  # auto-merging
            [],  # auto-merge-queue
        ]

        daemon._recover_merge_queue_from_labels("github.com/test-org/test-repo", "dependencies")

        # Should NOT add duplicate
        daemon.database.add_to_merge_queue.assert_not_called()


@pytest.mark.unit
class TestTriggerNextPrRebase:
    """Tests for _trigger_next_pr_rebase method."""

    def test_rebase_triggered_on_next_pr(self, daemon):
        """Test that rebase comment is added to next PR in queue."""
        next_entry = MergeQueueEntry(
            repo="github.com/test-org/test-repo",
            pr_number=102,
            position=0,
            status="queued",
            queued_at=MagicMock(),
        )
        daemon.database.get_merge_queue = MagicMock(return_value=[next_entry])
        daemon.ticket_client.comment_on_pr.return_value = True

        daemon._trigger_next_pr_rebase("github.com/test-org/test-repo")

        daemon.ticket_client.comment_on_pr.assert_called_once_with(
            "github.com/test-org/test-repo", 102, "@dependabot rebase"
        )
        daemon.database.update_merge_queue_status.assert_called_with(
            "github.com/test-org/test-repo", 102, "waiting_rebase"
        )

    def test_no_action_on_empty_queue(self, daemon):
        """Test that no action is taken when queue is empty."""
        daemon.database.get_merge_queue = MagicMock(return_value=[])

        daemon._trigger_next_pr_rebase("github.com/test-org/test-repo")

        daemon.ticket_client.comment_on_pr.assert_not_called()

    def test_failed_comment_does_not_update_status(self, daemon):
        """Test that status is not updated if comment fails."""
        next_entry = MergeQueueEntry(
            repo="github.com/test-org/test-repo",
            pr_number=102,
            position=0,
            status="queued",
            queued_at=MagicMock(),
        )
        daemon.database.get_merge_queue = MagicMock(return_value=[next_entry])
        daemon.ticket_client.comment_on_pr.return_value = False

        daemon._trigger_next_pr_rebase("github.com/test-org/test-repo")

        daemon.database.update_merge_queue_status.assert_not_called()


@pytest.mark.unit
class TestValidateAutoMergingConfig:
    """Tests for _validate_auto_merging_config method."""

    def test_validation_skipped_when_no_config(self, daemon):
        """Test that validation is skipped when no config exists."""
        daemon.auto_merging_manager.has_config.return_value = False

        daemon._validate_auto_merging_config()

        daemon.auto_merging_manager.validate_config.assert_not_called()

    def test_warnings_logged_for_invalid_config(self, daemon):
        """Test that warnings are logged for config issues."""
        daemon.auto_merging_manager.has_config.return_value = True
        daemon.auto_merging_manager.validate_config.return_value = [
            "Duplicate repository entry: github.com/test/repo"
        ]
        daemon.auto_merging_manager.get_enabled_repos.return_value = []

        with patch("src.daemon.logger") as mock_logger:
            daemon._validate_auto_merging_config()

            mock_logger.warning.assert_called()
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            assert any("Duplicate" in call for call in warning_calls)


@pytest.mark.unit
class TestMergeQueueIntegration:
    """Integration tests for merge queue lifecycle."""

    def test_full_merge_lifecycle(self, daemon, auto_merge_config):
        """Test complete lifecycle: discover -> approve -> merge -> rebase next."""
        # Setup: one PR discovered
        pr_list = [
            {
                "number": 101,
                "title": "Bump dep",
                "createdAt": "2024-01-15T10:00:00Z",
                "headRefOid": "abc123",
            }
        ]
        # After adding PR to queue, it should appear in get_merge_queue
        queued_entry = MergeQueueEntry(
            repo="github.com/test-org/test-repo",
            pr_number=101,
            position=0,
            status="queued",
            queued_at=MagicMock(),
        )

        # Recovery returns empty, main list returns the PR
        daemon.ticket_client.list_prs_by_label.side_effect = [[], [], pr_list]
        # First call returns empty (during recovery), subsequent calls return the entry
        daemon.database.get_merge_queue = MagicMock(
            side_effect=[[], [], [queued_entry], [queued_entry], []]
        )
        daemon.database.get_merge_queue_by_status = MagicMock(return_value=None)
        daemon.ticket_client.get_pr_state.return_value = "OPEN"
        daemon.ticket_client.get_pr_merge_state.return_value = {
            "mergeStateStatus": "CLEAN",
            "mergeable": "MERGEABLE",
            "reviewDecision": "",  # Not approved yet
        }
        daemon.ticket_client.get_pr_head_sha.return_value = "abc123"
        daemon.ticket_client.get_check_runs.return_value = [
            CheckRunResult(name="build", status="completed", conclusion="success")
        ]
        daemon.ticket_client.approve_pr.return_value = True
        daemon.ticket_client.merge_pr.return_value = True

        daemon._process_repo_merge_queue(auto_merge_config)

        # PR added to queue
        daemon.database.add_to_merge_queue.assert_called_once()

        # PR approved
        daemon.ticket_client.approve_pr.assert_called_once_with(
            "github.com/test-org/test-repo", 101
        )

        # PR merged
        daemon.ticket_client.merge_pr.assert_called_once_with(
            "github.com/test-org/test-repo", 101, "squash"
        )

        # Labels managed
        daemon.ticket_client.add_label.assert_any_call(
            "github.com/test-org/test-repo", 101, Labels.AUTO_MERGE_QUEUE
        )
