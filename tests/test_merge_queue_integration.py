"""Integration tests for the merge queue lifecycle.

These tests verify end-to-end behavior of the auto-merge queue system with
mocked GitHub responses, covering:
- Queue discovery and population
- CI check waiting behavior
- Successful merge and queue advancement
- Manual merge detection
- Daemon restart with persisted queue
- Config-disabled repo is skipped
"""

from unittest.mock import MagicMock, patch

import pytest

from src.daemon import Daemon
from src.database import Database
from src.integrations.auto_merging import AutoMergingEntry
from src.interfaces import CheckRunResult
from src.labels import Labels

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_pr_list_fixture():
    """Factory fixture for creating mock PR lists."""

    def _create_pr_list(count=3, base_number=100, created_date_prefix="2024-01-15"):
        """Create a list of mock PRs.

        Args:
            count: Number of PRs to create
            base_number: Starting PR number
            created_date_prefix: Date prefix for createdAt

        Returns:
            List of PR dicts with number, title, createdAt, headRefOid
        """
        return [
            {
                "number": base_number + i,
                "title": f"Bump dependency-{chr(ord('a') + i)}",
                "createdAt": f"{created_date_prefix}T{10 + i:02d}:00:00Z",
                "headRefOid": f"sha{base_number + i:03d}",
            }
            for i in range(count)
        ]

    return _create_pr_list


@pytest.fixture
def mock_check_runs_fixture():
    """Factory fixture for creating mock check run results."""

    def _create_check_runs(
        checks: list[dict] | None = None,
        all_passing: bool = False,
        all_failing: bool = False,
        in_progress: bool = False,
    ) -> list[CheckRunResult]:
        """Create mock check run results.

        Args:
            checks: List of check dicts with name, status, conclusion
            all_passing: If True, create passing checks
            all_failing: If True, create failing checks
            in_progress: If True, create in-progress checks

        Returns:
            List of CheckRunResult objects
        """
        if checks:
            return [
                CheckRunResult(
                    name=c.get("name", "check"),
                    status=c.get("status", "completed"),
                    conclusion=c.get("conclusion"),
                )
                for c in checks
            ]

        if all_passing:
            return [
                CheckRunResult(name="build", status="completed", conclusion="success"),
                CheckRunResult(name="test", status="completed", conclusion="success"),
            ]
        elif all_failing:
            return [
                CheckRunResult(name="build", status="completed", conclusion="failure"),
            ]
        elif in_progress:
            return [
                CheckRunResult(name="build", status="in_progress", conclusion=None),
            ]
        return []

    return _create_check_runs


@pytest.fixture
def auto_merge_config_fixture():
    """Factory fixture for creating AutoMergingEntry configurations."""

    def _create_config(
        repo: str = "github.com/test-org/test-repo",
        enabled: bool = True,
        merge_method: str = "squash",
        label: str = "dependencies",
    ) -> AutoMergingEntry:
        return AutoMergingEntry(
            repo=repo,
            enabled=enabled,
            merge_method=merge_method,
            label=label,
        )

    return _create_config


@pytest.fixture
def integration_daemon(temp_workspace_dir, auto_merge_config_fixture):
    """Fixture providing a Daemon with a real database but mocked GitHub client."""
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

        # Default merge state response (can be overridden in tests)
        daemon.ticket_client.get_pr_merge_state.return_value = {
            "mergeStateStatus": "CLEAN",
            "mergeable": "MERGEABLE",
            "reviewDecision": "APPROVED",
        }
        daemon.ticket_client.approve_pr.return_value = True

        # Use real database but mock the auto_merging_manager
        daemon.auto_merging_manager = MagicMock()

        yield daemon
        daemon.stop()


# =============================================================================
# Queue Discovery and Population Tests
# =============================================================================


@pytest.mark.integration
class TestQueueDiscoveryAndPopulation:
    """Tests for discovering Dependabot PRs and populating the queue."""

    def test_discovers_prs_with_dependencies_label(
        self,
        integration_daemon,
        auto_merge_config_fixture,
        mock_pr_list_fixture,
        mock_check_runs_fixture,
    ):
        """Test that PRs with dependencies label are discovered and added to queue."""
        config = auto_merge_config_fixture()
        prs = mock_pr_list_fixture(count=3)

        integration_daemon.auto_merging_manager.get_enabled_repos.return_value = [config]
        integration_daemon.ticket_client.list_prs_by_label.return_value = prs
        integration_daemon.ticket_client.get_pr_state.return_value = "OPEN"
        integration_daemon.ticket_client.get_pr_head_sha.return_value = "sha100"
        integration_daemon.ticket_client.get_check_runs.return_value = mock_check_runs_fixture(
            in_progress=True
        )

        integration_daemon._poll_merge_queue()

        # Verify PRs were queried with correct label
        integration_daemon.ticket_client.list_prs_by_label.assert_called_with(
            "github.com/test-org/test-repo", "dependencies"
        )

        # Verify PRs were added to database
        queue = integration_daemon.database.get_merge_queue("github.com/test-org/test-repo")
        assert len(queue) == 3
        assert queue[0].pr_number == 100
        assert queue[1].pr_number == 101
        assert queue[2].pr_number == 102

    def test_prs_added_in_creation_order(
        self,
        integration_daemon,
        auto_merge_config_fixture,
        mock_check_runs_fixture,
    ):
        """Test that PRs are added to queue in creation date order (FIFO)."""
        config = auto_merge_config_fixture()

        # PRs returned out of order
        prs = [
            {
                "number": 103,
                "title": "Bump c",
                "createdAt": "2024-01-15T12:00:00Z",
                "headRefOid": "sha103",
            },
            {
                "number": 101,
                "title": "Bump a",
                "createdAt": "2024-01-15T10:00:00Z",
                "headRefOid": "sha101",
            },
            {
                "number": 102,
                "title": "Bump b",
                "createdAt": "2024-01-15T11:00:00Z",
                "headRefOid": "sha102",
            },
        ]

        integration_daemon.auto_merging_manager.get_enabled_repos.return_value = [config]
        # Recovery returns empty, main call returns prs
        integration_daemon.ticket_client.list_prs_by_label.side_effect = [[], [], prs]
        integration_daemon.ticket_client.get_pr_state.return_value = "OPEN"
        integration_daemon.ticket_client.get_pr_head_sha.return_value = "sha101"
        integration_daemon.ticket_client.get_check_runs.return_value = mock_check_runs_fixture(
            in_progress=True
        )
        integration_daemon.ticket_client.get_pr_merge_state.return_value = {
            "mergeStateStatus": "BLOCKED",
            "mergeable": "MERGEABLE",
            "reviewDecision": "",
        }

        integration_daemon._poll_merge_queue()

        queue = integration_daemon.database.get_merge_queue("github.com/test-org/test-repo")
        assert len(queue) == 3
        # Verify FIFO order by creation date
        assert queue[0].pr_number == 101  # Earliest
        assert queue[1].pr_number == 102
        assert queue[2].pr_number == 103  # Latest

    def test_existing_prs_not_duplicated(
        self,
        integration_daemon,
        auto_merge_config_fixture,
        mock_pr_list_fixture,
        mock_check_runs_fixture,
    ):
        """Test that PRs already in queue are not re-added."""
        config = auto_merge_config_fixture()
        repo = config.repo

        # Pre-populate queue with PR 100
        integration_daemon.database.add_to_merge_queue(repo, 100, 0)

        prs = mock_pr_list_fixture(count=3)  # Returns PR 100, 101, 102

        integration_daemon.auto_merging_manager.get_enabled_repos.return_value = [config]
        integration_daemon.ticket_client.list_prs_by_label.return_value = prs
        integration_daemon.ticket_client.get_pr_state.return_value = "OPEN"
        integration_daemon.ticket_client.get_pr_head_sha.return_value = "sha100"
        integration_daemon.ticket_client.get_check_runs.return_value = mock_check_runs_fixture(
            in_progress=True
        )

        integration_daemon._poll_merge_queue()

        queue = integration_daemon.database.get_merge_queue(repo)
        # Should have 3 PRs total, not 4
        assert len(queue) == 3

    def test_queue_label_added_to_new_prs(
        self,
        integration_daemon,
        auto_merge_config_fixture,
        mock_pr_list_fixture,
        mock_check_runs_fixture,
    ):
        """Test that auto-merge-queue label is added to newly discovered PRs."""
        config = auto_merge_config_fixture()
        prs = mock_pr_list_fixture(count=2)

        integration_daemon.auto_merging_manager.get_enabled_repos.return_value = [config]
        # Recovery returns empty, main call returns prs
        integration_daemon.ticket_client.list_prs_by_label.side_effect = [[], [], prs]
        integration_daemon.ticket_client.get_pr_state.return_value = "OPEN"
        integration_daemon.ticket_client.get_pr_head_sha.return_value = "sha100"
        integration_daemon.ticket_client.get_check_runs.return_value = mock_check_runs_fixture(
            in_progress=True
        )
        integration_daemon.ticket_client.get_pr_merge_state.return_value = {
            "mergeStateStatus": "BLOCKED",
            "mergeable": "MERGEABLE",
            "reviewDecision": "",
        }

        integration_daemon._poll_merge_queue()

        # Verify queue label was added to each PR
        add_label_calls = integration_daemon.ticket_client.add_label.call_args_list
        queue_label_calls = [
            call for call in add_label_calls if call[0][2] == Labels.AUTO_MERGE_QUEUE
        ]
        assert len(queue_label_calls) == 2


# =============================================================================
# CI Check Waiting Behavior Tests
# =============================================================================


@pytest.mark.integration
class TestCICheckWaitingBehavior:
    """Tests for CI check waiting behavior."""

    def test_waits_for_running_ci(
        self,
        integration_daemon,
        auto_merge_config_fixture,
        mock_check_runs_fixture,
    ):
        """Test that merge waits for running CI checks."""
        config = auto_merge_config_fixture()
        repo = config.repo

        # Add PR to queue
        integration_daemon.database.add_to_merge_queue(repo, 100, 0)

        integration_daemon.auto_merging_manager.get_enabled_repos.return_value = [config]
        integration_daemon.ticket_client.list_prs_by_label.return_value = []
        integration_daemon.ticket_client.get_pr_state.return_value = "OPEN"
        integration_daemon.ticket_client.get_pr_head_sha.return_value = "sha100"
        integration_daemon.ticket_client.get_check_runs.return_value = mock_check_runs_fixture(
            in_progress=True
        )

        integration_daemon._poll_merge_queue()

        # Should not attempt merge
        integration_daemon.ticket_client.merge_pr.assert_not_called()

        # Status should be updated to waiting_ci
        queue = integration_daemon.database.get_merge_queue(repo)
        assert queue[0].status == "waiting_ci"

    def test_waits_when_first_pr_has_failing_ci(
        self,
        integration_daemon,
        auto_merge_config_fixture,
    ):
        """Test that queue waits when first PR has failing CI (sequential processing)."""
        config = auto_merge_config_fixture()
        repo = config.repo

        # Add two PRs to queue
        integration_daemon.database.add_to_merge_queue(repo, 100, 0)
        integration_daemon.database.add_to_merge_queue(repo, 101, 1)

        integration_daemon.auto_merging_manager.get_enabled_repos.return_value = [config]
        integration_daemon.ticket_client.list_prs_by_label.return_value = []
        integration_daemon.ticket_client.get_pr_state.return_value = "OPEN"
        integration_daemon.ticket_client.get_pr_merge_state.return_value = {
            "mergeStateStatus": "UNSTABLE",
            "mergeable": "MERGEABLE",
            "reviewDecision": "",
        }

        # First PR has failing CI
        integration_daemon.ticket_client.get_pr_head_sha.return_value = "sha100"
        integration_daemon.ticket_client.get_check_runs.return_value = [
            CheckRunResult(name="build", status="completed", conclusion="failure")
        ]

        integration_daemon._poll_merge_queue()

        # Should NOT merge (waiting for first PR's CI to pass)
        integration_daemon.ticket_client.merge_pr.assert_not_called()

        # Queue should remain unchanged
        queue = integration_daemon.database.get_merge_queue(repo)
        assert len(queue) == 2
        assert queue[0].pr_number == 100  # Still first in queue

    def test_no_ci_checks_allows_merge(
        self,
        integration_daemon,
        auto_merge_config_fixture,
    ):
        """Test that PRs with no CI checks are merged."""
        config = auto_merge_config_fixture()
        repo = config.repo

        integration_daemon.database.add_to_merge_queue(repo, 100, 0)

        integration_daemon.auto_merging_manager.get_enabled_repos.return_value = [config]
        integration_daemon.ticket_client.list_prs_by_label.return_value = []
        integration_daemon.ticket_client.get_pr_state.return_value = "OPEN"
        integration_daemon.ticket_client.get_pr_head_sha.return_value = "sha100"
        integration_daemon.ticket_client.get_check_runs.return_value = []  # No CI checks
        integration_daemon.ticket_client.merge_pr.return_value = True

        integration_daemon._poll_merge_queue()

        # Should merge even without CI checks
        integration_daemon.ticket_client.merge_pr.assert_called_once()


# =============================================================================
# Successful Merge and Queue Advancement Tests
# =============================================================================


@pytest.mark.integration
class TestSuccessfulMergeAndQueueAdvancement:
    """Tests for successful merge behavior and queue advancement."""

    def test_merge_removes_pr_from_queue(
        self,
        integration_daemon,
        auto_merge_config_fixture,
        mock_check_runs_fixture,
    ):
        """Test that merged PR is removed from queue."""
        config = auto_merge_config_fixture()
        repo = config.repo

        # Add PRs to queue
        integration_daemon.database.add_to_merge_queue(repo, 100, 0)
        integration_daemon.database.add_to_merge_queue(repo, 101, 1)

        integration_daemon.auto_merging_manager.get_enabled_repos.return_value = [config]
        integration_daemon.ticket_client.list_prs_by_label.return_value = []
        integration_daemon.ticket_client.get_pr_state.return_value = "OPEN"
        integration_daemon.ticket_client.get_pr_head_sha.return_value = "sha100"
        integration_daemon.ticket_client.get_check_runs.return_value = mock_check_runs_fixture(
            all_passing=True
        )
        integration_daemon.ticket_client.merge_pr.return_value = True
        integration_daemon.ticket_client.comment_on_pr.return_value = True

        integration_daemon._poll_merge_queue()

        # First PR should be removed
        queue = integration_daemon.database.get_merge_queue(repo)
        assert len(queue) == 1
        assert queue[0].pr_number == 101
        assert queue[0].position == 0  # Position updated

    def test_rebase_triggered_on_next_pr(
        self,
        integration_daemon,
        auto_merge_config_fixture,
        mock_check_runs_fixture,
    ):
        """Test that rebase comment is added to next PR after merge."""
        config = auto_merge_config_fixture()
        repo = config.repo

        integration_daemon.database.add_to_merge_queue(repo, 100, 0)
        integration_daemon.database.add_to_merge_queue(repo, 101, 1)

        integration_daemon.auto_merging_manager.get_enabled_repos.return_value = [config]
        integration_daemon.ticket_client.list_prs_by_label.return_value = []
        integration_daemon.ticket_client.get_pr_state.return_value = "OPEN"
        integration_daemon.ticket_client.get_pr_head_sha.return_value = "sha100"
        integration_daemon.ticket_client.get_check_runs.return_value = mock_check_runs_fixture(
            all_passing=True
        )
        integration_daemon.ticket_client.merge_pr.return_value = True
        integration_daemon.ticket_client.comment_on_pr.return_value = True

        integration_daemon._poll_merge_queue()

        # Rebase comment should be added to next PR
        integration_daemon.ticket_client.comment_on_pr.assert_called_with(
            repo, 101, "@dependabot rebase"
        )

        # Next PR status should be waiting_rebase
        queue = integration_daemon.database.get_merge_queue(repo)
        assert queue[0].status == "waiting_rebase"

    def test_label_transitions_on_merge(
        self,
        integration_daemon,
        auto_merge_config_fixture,
        mock_check_runs_fixture,
    ):
        """Test that labels transition correctly during merge."""
        config = auto_merge_config_fixture()
        repo = config.repo

        integration_daemon.database.add_to_merge_queue(repo, 100, 0)

        integration_daemon.auto_merging_manager.get_enabled_repos.return_value = [config]
        integration_daemon.ticket_client.list_prs_by_label.return_value = []
        integration_daemon.ticket_client.get_pr_state.return_value = "OPEN"
        integration_daemon.ticket_client.get_pr_head_sha.return_value = "sha100"
        integration_daemon.ticket_client.get_check_runs.return_value = mock_check_runs_fixture(
            all_passing=True
        )
        integration_daemon.ticket_client.merge_pr.return_value = True

        integration_daemon._poll_merge_queue()

        # Queue label should be removed
        integration_daemon.ticket_client.remove_label.assert_any_call(
            repo, 100, Labels.AUTO_MERGE_QUEUE
        )
        # Merging label should be added then removed after merge
        integration_daemon.ticket_client.add_label.assert_any_call(repo, 100, Labels.AUTO_MERGING)
        integration_daemon.ticket_client.remove_label.assert_any_call(
            repo, 100, Labels.AUTO_MERGING
        )


# =============================================================================
# Manual Merge Detection Tests
# =============================================================================


@pytest.mark.integration
class TestManualMergeDetection:
    """Tests for detecting manually merged PRs."""

    def test_manually_merged_pr_removed_from_queue(
        self, integration_daemon, auto_merge_config_fixture
    ):
        """Test that manually merged PRs are removed from queue."""
        config = auto_merge_config_fixture()
        repo = config.repo

        # Add PR to queue
        integration_daemon.database.add_to_merge_queue(repo, 100, 0)

        integration_daemon.auto_merging_manager.get_enabled_repos.return_value = [config]
        integration_daemon.ticket_client.list_prs_by_label.return_value = []
        integration_daemon.ticket_client.get_pr_state.return_value = "MERGED"  # Manually merged

        integration_daemon._poll_merge_queue()

        # PR should be removed from queue
        queue = integration_daemon.database.get_merge_queue(repo)
        assert len(queue) == 0

    def test_closed_pr_removed_from_queue(self, integration_daemon, auto_merge_config_fixture):
        """Test that closed PRs are removed from queue."""
        config = auto_merge_config_fixture()
        repo = config.repo

        integration_daemon.database.add_to_merge_queue(repo, 100, 0)

        integration_daemon.auto_merging_manager.get_enabled_repos.return_value = [config]
        integration_daemon.ticket_client.list_prs_by_label.return_value = []
        integration_daemon.ticket_client.get_pr_state.return_value = "CLOSED"

        integration_daemon._poll_merge_queue()

        queue = integration_daemon.database.get_merge_queue(repo)
        assert len(queue) == 0

    def test_labels_removed_from_closed_pr(self, integration_daemon, auto_merge_config_fixture):
        """Test that auto-merge labels are removed from closed PRs."""
        config = auto_merge_config_fixture()
        repo = config.repo

        integration_daemon.database.add_to_merge_queue(repo, 100, 0)

        integration_daemon.auto_merging_manager.get_enabled_repos.return_value = [config]
        integration_daemon.ticket_client.list_prs_by_label.return_value = []
        integration_daemon.ticket_client.get_pr_state.return_value = "CLOSED"

        integration_daemon._poll_merge_queue()

        # Labels should be removed
        integration_daemon.ticket_client.remove_label.assert_any_call(
            repo, 100, Labels.AUTO_MERGE_QUEUE
        )
        integration_daemon.ticket_client.remove_label.assert_any_call(
            repo, 100, Labels.AUTO_MERGING
        )


# =============================================================================
# Daemon Restart with Persisted Queue Tests
# =============================================================================


@pytest.mark.integration
class TestDaemonRestartWithPersistedQueue:
    """Tests for queue persistence across daemon restarts."""

    def test_queue_survives_daemon_restart(self, temp_workspace_dir, auto_merge_config_fixture):
        """Test that queue state persists across daemon restart."""
        db_path = f"{temp_workspace_dir}/test.db"
        repo = "github.com/test-org/test-repo"

        # Create database and add entries
        db = Database(db_path)
        db.add_to_merge_queue(repo, 100, 0)
        db.add_to_merge_queue(repo, 101, 1)
        db.update_merge_queue_status(repo, 100, "waiting_ci")
        db.close()

        # Simulate daemon restart - create new database connection
        db2 = Database(db_path)

        # Queue should be preserved
        queue = db2.get_merge_queue(repo)
        assert len(queue) == 2
        assert queue[0].pr_number == 100
        assert queue[0].status == "waiting_ci"
        assert queue[1].pr_number == 101
        assert queue[1].status == "queued"

        db2.close()

    def test_queue_positions_maintained_after_restart(
        self, temp_workspace_dir, auto_merge_config_fixture
    ):
        """Test that queue positions are maintained after restart."""
        db_path = f"{temp_workspace_dir}/test.db"
        repo = "github.com/test-org/test-repo"

        db = Database(db_path)
        for i in range(5):
            db.add_to_merge_queue(repo, 100 + i, i)
        db.close()

        db2 = Database(db_path)
        queue = db2.get_merge_queue(repo)

        # Verify order is preserved
        for i, entry in enumerate(queue):
            assert entry.position == i
            assert entry.pr_number == 100 + i

        db2.close()

    def test_daemon_resumes_active_merge(
        self, temp_workspace_dir, auto_merge_config_fixture, mock_check_runs_fixture
    ):
        """Test that daemon resumes an active merge after restart.

        Scenario: Daemon crashed while a PR was in 'merging' status but the merge
        actually completed on GitHub. On restart, the daemon detects the merged
        PR via the cleanup loop and removes it from the queue. If the next PR
        has CI still running, it stays in the queue with 'waiting_ci' status.
        """
        config = auto_merge_config_fixture()
        repo = config.repo
        db_path = f"{temp_workspace_dir}/test.db"

        # Create daemon with its own database
        config_mock = MagicMock()
        config_mock.poll_interval = 60
        config_mock.watched_statuses = ["Research", "Plan", "Implement"]
        config_mock.max_concurrent_workflows = 2
        config_mock.database_path = db_path
        config_mock.workspace_dir = temp_workspace_dir
        config_mock.project_urls = ["https://github.com/orgs/test/projects/1"]
        config_mock.github_enterprise_version = None
        config_mock.username_self = "kiln-bot"
        config_mock.team_usernames = []

        with patch("src.ticket_clients.github.GitHubTicketClient"):
            daemon = Daemon(config_mock)
            daemon.ticket_client = MagicMock()
            daemon.auto_merging_manager = MagicMock()

            # Pre-populate the daemon's database to simulate prior state
            daemon.database.add_to_merge_queue(repo, 100, 0)
            daemon.database.update_merge_queue_status(repo, 100, "merging")
            daemon.database.add_to_merge_queue(repo, 101, 1)

            daemon.auto_merging_manager.get_enabled_repos.return_value = [config]
            daemon.ticket_client.list_prs_by_label.return_value = []

            # PR 100 (was merging) shows as MERGED, PR 101 shows as OPEN
            def get_pr_state_side_effect(r, pr_num):
                if pr_num == 100:
                    return "MERGED"
                return "OPEN"

            daemon.ticket_client.get_pr_state.side_effect = get_pr_state_side_effect
            daemon.ticket_client.get_pr_head_sha.return_value = "sha101"
            # PR 101 has CI still running, so it stays in queue
            daemon.ticket_client.get_check_runs.return_value = mock_check_runs_fixture(
                in_progress=True
            )

            daemon._poll_merge_queue()

            # PR 100 should be detected as merged and removed from queue
            # PR 101 should stay in queue with waiting_ci status
            queue = daemon.database.get_merge_queue(repo)
            assert len(queue) == 1
            assert queue[0].pr_number == 101
            # Position should be reordered to 0 after PR 100 was removed
            assert queue[0].position == 0
            assert queue[0].status == "waiting_ci"

            daemon.stop()

    def test_daemon_resumes_ongoing_merge(
        self, temp_workspace_dir, auto_merge_config_fixture, mock_check_runs_fixture
    ):
        """Test that daemon correctly detects an ongoing merge that completes.

        Scenario: Daemon is running, PR is in 'merging' status in database,
        and the next poll finds the PR was merged on GitHub. This should
        trigger rebase on the next PR.
        """
        config = auto_merge_config_fixture()
        repo = config.repo
        db_path = f"{temp_workspace_dir}/test.db"

        # Create daemon with its own database
        config_mock = MagicMock()
        config_mock.poll_interval = 60
        config_mock.watched_statuses = ["Research", "Plan", "Implement"]
        config_mock.max_concurrent_workflows = 2
        config_mock.database_path = db_path
        config_mock.workspace_dir = temp_workspace_dir
        config_mock.project_urls = ["https://github.com/orgs/test/projects/1"]
        config_mock.github_enterprise_version = None
        config_mock.username_self = "kiln-bot"
        config_mock.team_usernames = []

        with patch("src.ticket_clients.github.GitHubTicketClient"):
            daemon = Daemon(config_mock)
            daemon.ticket_client = MagicMock()
            daemon.auto_merging_manager = MagicMock()

            # Pre-populate the daemon's database to simulate prior state
            daemon.database.add_to_merge_queue(repo, 100, 0)
            daemon.database.update_merge_queue_status(repo, 100, "merging")
            daemon.database.add_to_merge_queue(repo, 101, 1)

            daemon.auto_merging_manager.get_enabled_repos.return_value = [config]
            daemon.ticket_client.list_prs_by_label.return_value = []

            # First call - cleanup check: both PRs are OPEN (merge still in progress)
            # Second call - merging status check: PR 100 now shows as MERGED
            call_count = [0]

            def get_pr_state_side_effect(r, pr_num):
                call_count[0] += 1
                if pr_num == 100:
                    # First two calls during cleanup - OPEN
                    # Third call when checking merging status - MERGED
                    if call_count[0] <= 2:
                        return "OPEN"
                    return "MERGED"
                return "OPEN"

            daemon.ticket_client.get_pr_state.side_effect = get_pr_state_side_effect
            daemon.ticket_client.comment_on_pr.return_value = True

            daemon._poll_merge_queue()

            # Should detect the merge completed and trigger rebase on next
            daemon.ticket_client.comment_on_pr.assert_called_with(repo, 101, "@dependabot rebase")

            daemon.stop()


# =============================================================================
# Config-disabled Repo Skipped Tests
# =============================================================================


@pytest.mark.integration
class TestConfigDisabledRepoSkipped:
    """Tests for skipping disabled repos."""

    def test_disabled_repo_not_processed(
        self, integration_daemon, auto_merge_config_fixture, mock_pr_list_fixture
    ):
        """Test that repos with enabled=false are not processed."""
        enabled_config = auto_merge_config_fixture(
            repo="github.com/test-org/enabled-repo", enabled=True
        )
        disabled_config = auto_merge_config_fixture(
            repo="github.com/test-org/disabled-repo", enabled=False
        )

        # Manager returns only enabled repos
        integration_daemon.auto_merging_manager.get_enabled_repos.return_value = [enabled_config]
        integration_daemon.ticket_client.list_prs_by_label.return_value = []
        integration_daemon.ticket_client.get_pr_state.return_value = "OPEN"

        integration_daemon._poll_merge_queue()

        # Should query the enabled repo (3 times: auto-merging, auto-merge-queue, dependencies)
        assert integration_daemon.ticket_client.list_prs_by_label.call_count == 3
        # Verify the configured label was queried
        integration_daemon.ticket_client.list_prs_by_label.assert_any_call(
            "github.com/test-org/enabled-repo", "dependencies"
        )

    def test_no_repos_enabled_skips_polling(self, integration_daemon):
        """Test that polling is skipped when no repos are enabled."""
        integration_daemon.auto_merging_manager.get_enabled_repos.return_value = []

        integration_daemon._poll_merge_queue()

        integration_daemon.ticket_client.list_prs_by_label.assert_not_called()


# =============================================================================
# Full Lifecycle Integration Tests
# =============================================================================


@pytest.mark.integration
class TestFullMergeQueueLifecycle:
    """End-to-end lifecycle tests."""

    def test_complete_queue_processing_cycle(
        self, integration_daemon, auto_merge_config_fixture, mock_check_runs_fixture
    ):
        """Test complete cycle: discover -> add -> merge -> rebase -> complete."""
        config = auto_merge_config_fixture()
        repo = config.repo

        # Initial PRs discovered
        prs = [
            {
                "number": 100,
                "title": "Bump a",
                "createdAt": "2024-01-15T10:00:00Z",
                "headRefOid": "sha100",
            },
            {
                "number": 101,
                "title": "Bump b",
                "createdAt": "2024-01-15T11:00:00Z",
                "headRefOid": "sha101",
            },
        ]

        integration_daemon.auto_merging_manager.get_enabled_repos.return_value = [config]
        integration_daemon.ticket_client.list_prs_by_label.return_value = prs
        integration_daemon.ticket_client.get_pr_state.return_value = "OPEN"
        integration_daemon.ticket_client.get_pr_head_sha.return_value = "sha100"
        integration_daemon.ticket_client.get_check_runs.return_value = mock_check_runs_fixture(
            all_passing=True
        )
        integration_daemon.ticket_client.merge_pr.return_value = True
        integration_daemon.ticket_client.comment_on_pr.return_value = True

        # First poll: discover PRs, merge first one
        integration_daemon._poll_merge_queue()

        # First PR should be merged
        integration_daemon.ticket_client.merge_pr.assert_called_with(repo, 100, "squash")

        # Second PR should have rebase comment
        integration_daemon.ticket_client.comment_on_pr.assert_called_with(
            repo, 101, "@dependabot rebase"
        )

        # Queue should have only second PR
        queue = integration_daemon.database.get_merge_queue(repo)
        assert len(queue) == 1
        assert queue[0].pr_number == 101
        assert queue[0].status == "waiting_rebase"

    def test_multiple_repos_processed_independently(
        self, integration_daemon, auto_merge_config_fixture, mock_check_runs_fixture
    ):
        """Test that multiple repos are processed independently."""
        config1 = auto_merge_config_fixture(repo="github.com/org/repo1")
        config2 = auto_merge_config_fixture(repo="github.com/org/repo2")

        prs1 = [
            {
                "number": 100,
                "title": "PR1",
                "createdAt": "2024-01-15T10:00:00Z",
                "headRefOid": "sha100",
            }
        ]
        prs2 = [
            {
                "number": 200,
                "title": "PR2",
                "createdAt": "2024-01-15T10:00:00Z",
                "headRefOid": "sha200",
            }
        ]

        integration_daemon.auto_merging_manager.get_enabled_repos.return_value = [config1, config2]
        # Each repo: 2 recovery calls (auto-merging, auto-merge-queue) + 1 main call
        integration_daemon.ticket_client.list_prs_by_label.side_effect = [
            [],
            [],
            prs1,  # repo1
            [],
            [],
            prs2,  # repo2
        ]
        integration_daemon.ticket_client.get_pr_state.return_value = "OPEN"
        integration_daemon.ticket_client.get_pr_head_sha.return_value = "sha100"
        integration_daemon.ticket_client.get_check_runs.return_value = mock_check_runs_fixture(
            all_passing=True
        )
        integration_daemon.ticket_client.merge_pr.return_value = True

        integration_daemon._poll_merge_queue()

        # Both repos should have their PRs processed
        # Note: Both PRs get merged in the same poll cycle (one per repo)
        assert integration_daemon.ticket_client.merge_pr.call_count == 2

    def test_error_in_one_repo_doesnt_affect_others(
        self, integration_daemon, auto_merge_config_fixture, mock_pr_list_fixture
    ):
        """Test that error processing one repo doesn't affect others."""
        config1 = auto_merge_config_fixture(repo="github.com/org/repo1")
        config2 = auto_merge_config_fixture(repo="github.com/org/repo2")

        integration_daemon.auto_merging_manager.get_enabled_repos.return_value = [config1, config2]

        # First repo fails on first recovery call, second repo succeeds
        integration_daemon.ticket_client.list_prs_by_label.side_effect = [
            Exception("API error"),  # repo1 fails on first call
            [],
            [],
            [],  # repo2 succeeds (3 calls: recovery + main)
        ]
        integration_daemon.ticket_client.get_pr_state.return_value = "OPEN"

        # Should not raise, both repos attempted
        integration_daemon._poll_merge_queue()

        # First repo failed early, second repo got 3 calls
        assert integration_daemon.ticket_client.list_prs_by_label.call_count >= 2
