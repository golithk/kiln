"""Unit tests for the merge queue database operations."""

import tempfile
import time
from datetime import datetime
from pathlib import Path

import pytest

from src.database import Database, MergeQueueEntry


@pytest.fixture
def temp_db():
    """Fixture providing a temporary database for tests."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".db", delete=False) as f:
        db_path = f.name

    # Create and yield the database
    db = Database(db_path)
    yield db

    # Cleanup
    db.close()
    Path(db_path).unlink(missing_ok=True)


@pytest.mark.unit
class TestMergeQueueTableCreation:
    """Tests for merge_queue table creation and schema."""

    def test_merge_queue_table_created(self, temp_db):
        """Test that merge_queue table is created on init."""
        cursor = temp_db.conn.cursor()
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='merge_queue'
        """)
        result = cursor.fetchone()
        assert result is not None
        assert result["name"] == "merge_queue"

    def test_merge_queue_table_schema(self, temp_db):
        """Test that merge_queue table has correct schema."""
        cursor = temp_db.conn.cursor()
        cursor.execute("PRAGMA table_info(merge_queue)")
        columns = {row["name"]: row for row in cursor.fetchall()}

        assert "repo" in columns
        assert "pr_number" in columns
        assert "position" in columns
        assert "status" in columns
        assert "queued_at" in columns
        assert "last_checked" in columns

        # Check primary key constraint
        cursor.execute("""
            SELECT sql FROM sqlite_master
            WHERE type='table' AND name='merge_queue'
        """)
        schema = cursor.fetchone()["sql"]
        assert "PRIMARY KEY (repo, pr_number)" in schema

    def test_merge_queue_index_created(self, temp_db):
        """Test that merge_queue index is created."""
        cursor = temp_db.conn.cursor()
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='index' AND name='idx_merge_queue_repo_position'
        """)
        result = cursor.fetchone()
        assert result is not None


@pytest.mark.unit
class TestMergeQueueEntry:
    """Tests for MergeQueueEntry dataclass."""

    def test_merge_queue_entry_creation(self):
        """Test creating a MergeQueueEntry instance."""
        timestamp = datetime.now()
        entry = MergeQueueEntry(
            repo="github.com/owner/repo",
            pr_number=123,
            position=0,
            status="queued",
            queued_at=timestamp,
            last_checked=None,
        )

        assert entry.repo == "github.com/owner/repo"
        assert entry.pr_number == 123
        assert entry.position == 0
        assert entry.status == "queued"
        assert entry.queued_at == timestamp
        assert entry.last_checked is None

    def test_merge_queue_entry_with_last_checked(self):
        """Test creating a MergeQueueEntry with last_checked."""
        queued_at = datetime.now()
        last_checked = datetime.now()
        entry = MergeQueueEntry(
            repo="github.com/owner/repo",
            pr_number=456,
            position=1,
            status="waiting_ci",
            queued_at=queued_at,
            last_checked=last_checked,
        )

        assert entry.last_checked == last_checked


@pytest.mark.unit
class TestAddToMergeQueue:
    """Tests for add_to_merge_queue method."""

    def test_add_to_merge_queue(self, temp_db):
        """Test adding a PR to the merge queue."""
        temp_db.add_to_merge_queue("github.com/owner/repo", 123, 0)

        cursor = temp_db.conn.cursor()
        cursor.execute(
            "SELECT * FROM merge_queue WHERE repo = ? AND pr_number = ?",
            ("github.com/owner/repo", 123),
        )
        row = cursor.fetchone()

        assert row is not None
        assert row["repo"] == "github.com/owner/repo"
        assert row["pr_number"] == 123
        assert row["position"] == 0
        assert row["status"] == "queued"
        assert row["queued_at"] is not None
        assert row["last_checked"] is None

    def test_add_multiple_prs_to_queue(self, temp_db):
        """Test adding multiple PRs to the queue."""
        temp_db.add_to_merge_queue("github.com/owner/repo", 1, 0)
        temp_db.add_to_merge_queue("github.com/owner/repo", 2, 1)
        temp_db.add_to_merge_queue("github.com/owner/repo", 3, 2)

        cursor = temp_db.conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) as count FROM merge_queue WHERE repo = ?", ("github.com/owner/repo",)
        )
        assert cursor.fetchone()["count"] == 3

    def test_add_to_merge_queue_replaces_existing(self, temp_db):
        """Test that adding same PR replaces existing entry."""
        temp_db.add_to_merge_queue("github.com/owner/repo", 123, 0)
        time.sleep(0.01)
        temp_db.add_to_merge_queue("github.com/owner/repo", 123, 5)

        cursor = temp_db.conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) as count FROM merge_queue WHERE repo = ? AND pr_number = ?",
            ("github.com/owner/repo", 123),
        )
        assert cursor.fetchone()["count"] == 1

        cursor.execute(
            "SELECT position FROM merge_queue WHERE repo = ? AND pr_number = ?",
            ("github.com/owner/repo", 123),
        )
        assert cursor.fetchone()["position"] == 5


@pytest.mark.unit
class TestGetMergeQueue:
    """Tests for get_merge_queue method."""

    def test_get_merge_queue_empty(self, temp_db):
        """Test getting queue for repo with no entries."""
        queue = temp_db.get_merge_queue("github.com/owner/repo")
        assert queue == []

    def test_get_merge_queue_single_entry(self, temp_db):
        """Test getting queue with single entry."""
        temp_db.add_to_merge_queue("github.com/owner/repo", 123, 0)

        queue = temp_db.get_merge_queue("github.com/owner/repo")

        assert len(queue) == 1
        assert isinstance(queue[0], MergeQueueEntry)
        assert queue[0].repo == "github.com/owner/repo"
        assert queue[0].pr_number == 123
        assert queue[0].position == 0
        assert queue[0].status == "queued"
        assert isinstance(queue[0].queued_at, datetime)
        assert queue[0].last_checked is None

    def test_get_merge_queue_ordered_by_position(self, temp_db):
        """Test that queue entries are returned ordered by position."""
        # Add in non-sequential order
        temp_db.add_to_merge_queue("github.com/owner/repo", 3, 2)
        temp_db.add_to_merge_queue("github.com/owner/repo", 1, 0)
        temp_db.add_to_merge_queue("github.com/owner/repo", 2, 1)

        queue = temp_db.get_merge_queue("github.com/owner/repo")

        assert len(queue) == 3
        assert queue[0].pr_number == 1
        assert queue[0].position == 0
        assert queue[1].pr_number == 2
        assert queue[1].position == 1
        assert queue[2].pr_number == 3
        assert queue[2].position == 2

    def test_get_merge_queue_different_repos(self, temp_db):
        """Test that queues are separate per repository."""
        temp_db.add_to_merge_queue("github.com/owner/repo1", 1, 0)
        temp_db.add_to_merge_queue("github.com/owner/repo2", 2, 0)

        queue1 = temp_db.get_merge_queue("github.com/owner/repo1")
        queue2 = temp_db.get_merge_queue("github.com/owner/repo2")

        assert len(queue1) == 1
        assert queue1[0].pr_number == 1
        assert len(queue2) == 1
        assert queue2[0].pr_number == 2


@pytest.mark.unit
class TestUpdateMergeQueueStatus:
    """Tests for update_merge_queue_status method."""

    def test_update_merge_queue_status(self, temp_db):
        """Test updating PR status in queue."""
        temp_db.add_to_merge_queue("github.com/owner/repo", 123, 0)

        temp_db.update_merge_queue_status("github.com/owner/repo", 123, "merging")

        queue = temp_db.get_merge_queue("github.com/owner/repo")
        assert len(queue) == 1
        assert queue[0].status == "merging"
        assert queue[0].last_checked is not None

    def test_update_merge_queue_status_updates_last_checked(self, temp_db):
        """Test that updating status also updates last_checked timestamp."""
        temp_db.add_to_merge_queue("github.com/owner/repo", 123, 0)

        # Initially last_checked is None
        queue = temp_db.get_merge_queue("github.com/owner/repo")
        assert queue[0].last_checked is None

        temp_db.update_merge_queue_status("github.com/owner/repo", 123, "waiting_ci")

        queue = temp_db.get_merge_queue("github.com/owner/repo")
        assert queue[0].last_checked is not None
        assert isinstance(queue[0].last_checked, datetime)

    def test_update_merge_queue_status_without_last_checked(self, temp_db):
        """Test updating status without updating last_checked."""
        temp_db.add_to_merge_queue("github.com/owner/repo", 123, 0)

        temp_db.update_merge_queue_status(
            "github.com/owner/repo", 123, "waiting_rebase", update_last_checked=False
        )

        queue = temp_db.get_merge_queue("github.com/owner/repo")
        assert queue[0].status == "waiting_rebase"
        assert queue[0].last_checked is None

    def test_update_merge_queue_status_nonexistent(self, temp_db):
        """Test updating status for non-existent PR is a no-op."""
        # Should not raise an error
        temp_db.update_merge_queue_status("github.com/owner/repo", 999, "merging")

        # Verify nothing was inserted
        queue = temp_db.get_merge_queue("github.com/owner/repo")
        assert len(queue) == 0


@pytest.mark.unit
class TestRemoveFromMergeQueue:
    """Tests for remove_from_merge_queue method."""

    def test_remove_from_merge_queue(self, temp_db):
        """Test removing a PR from the queue."""
        temp_db.add_to_merge_queue("github.com/owner/repo", 123, 0)
        temp_db.remove_from_merge_queue("github.com/owner/repo", 123)

        queue = temp_db.get_merge_queue("github.com/owner/repo")
        assert len(queue) == 0

    def test_remove_from_merge_queue_reorders_positions(self, temp_db):
        """Test that removing a PR reorders remaining positions."""
        temp_db.add_to_merge_queue("github.com/owner/repo", 1, 0)
        temp_db.add_to_merge_queue("github.com/owner/repo", 2, 1)
        temp_db.add_to_merge_queue("github.com/owner/repo", 3, 2)

        # Remove the middle one
        temp_db.remove_from_merge_queue("github.com/owner/repo", 2)

        queue = temp_db.get_merge_queue("github.com/owner/repo")
        assert len(queue) == 2
        assert queue[0].pr_number == 1
        assert queue[0].position == 0
        assert queue[1].pr_number == 3
        assert queue[1].position == 1  # Position decremented

    def test_remove_from_merge_queue_first_position(self, temp_db):
        """Test removing the first PR reorders all others."""
        temp_db.add_to_merge_queue("github.com/owner/repo", 1, 0)
        temp_db.add_to_merge_queue("github.com/owner/repo", 2, 1)
        temp_db.add_to_merge_queue("github.com/owner/repo", 3, 2)

        temp_db.remove_from_merge_queue("github.com/owner/repo", 1)

        queue = temp_db.get_merge_queue("github.com/owner/repo")
        assert len(queue) == 2
        assert queue[0].pr_number == 2
        assert queue[0].position == 0
        assert queue[1].pr_number == 3
        assert queue[1].position == 1

    def test_remove_from_merge_queue_last_position(self, temp_db):
        """Test removing the last PR doesn't affect others."""
        temp_db.add_to_merge_queue("github.com/owner/repo", 1, 0)
        temp_db.add_to_merge_queue("github.com/owner/repo", 2, 1)
        temp_db.add_to_merge_queue("github.com/owner/repo", 3, 2)

        temp_db.remove_from_merge_queue("github.com/owner/repo", 3)

        queue = temp_db.get_merge_queue("github.com/owner/repo")
        assert len(queue) == 2
        assert queue[0].pr_number == 1
        assert queue[0].position == 0
        assert queue[1].pr_number == 2
        assert queue[1].position == 1

    def test_remove_from_merge_queue_nonexistent(self, temp_db):
        """Test removing a non-existent PR is a no-op."""
        temp_db.add_to_merge_queue("github.com/owner/repo", 1, 0)

        # Should not raise an error
        temp_db.remove_from_merge_queue("github.com/owner/repo", 999)

        queue = temp_db.get_merge_queue("github.com/owner/repo")
        assert len(queue) == 1

    def test_remove_from_merge_queue_different_repos(self, temp_db):
        """Test removing from one repo doesn't affect another."""
        temp_db.add_to_merge_queue("github.com/owner/repo1", 1, 0)
        temp_db.add_to_merge_queue("github.com/owner/repo2", 1, 0)

        temp_db.remove_from_merge_queue("github.com/owner/repo1", 1)

        queue1 = temp_db.get_merge_queue("github.com/owner/repo1")
        queue2 = temp_db.get_merge_queue("github.com/owner/repo2")

        assert len(queue1) == 0
        assert len(queue2) == 1


@pytest.mark.unit
class TestGetMergeQueueByStatus:
    """Tests for get_merge_queue_by_status method."""

    def test_get_merge_queue_by_status_found(self, temp_db):
        """Test getting PR by status when it exists."""
        temp_db.add_to_merge_queue("github.com/owner/repo", 1, 0)
        temp_db.add_to_merge_queue("github.com/owner/repo", 2, 1)
        temp_db.update_merge_queue_status("github.com/owner/repo", 2, "merging")

        result = temp_db.get_merge_queue_by_status("github.com/owner/repo", "merging")

        assert result is not None
        assert isinstance(result, MergeQueueEntry)
        assert result.pr_number == 2
        assert result.status == "merging"

    def test_get_merge_queue_by_status_not_found(self, temp_db):
        """Test getting PR by status when none exists."""
        temp_db.add_to_merge_queue("github.com/owner/repo", 1, 0)

        result = temp_db.get_merge_queue_by_status("github.com/owner/repo", "merging")

        assert result is None

    def test_get_merge_queue_by_status_returns_first_by_position(self, temp_db):
        """Test that first PR by position is returned when multiple match."""
        temp_db.add_to_merge_queue("github.com/owner/repo", 1, 0)
        temp_db.add_to_merge_queue("github.com/owner/repo", 2, 1)
        temp_db.add_to_merge_queue("github.com/owner/repo", 3, 2)
        # All default to 'queued' status

        result = temp_db.get_merge_queue_by_status("github.com/owner/repo", "queued")

        assert result is not None
        assert result.pr_number == 1
        assert result.position == 0

    def test_get_merge_queue_by_status_different_repos(self, temp_db):
        """Test that status search is scoped to repo."""
        temp_db.add_to_merge_queue("github.com/owner/repo1", 1, 0)
        temp_db.update_merge_queue_status("github.com/owner/repo1", 1, "merging")
        temp_db.add_to_merge_queue("github.com/owner/repo2", 2, 0)

        result1 = temp_db.get_merge_queue_by_status("github.com/owner/repo1", "merging")
        result2 = temp_db.get_merge_queue_by_status("github.com/owner/repo2", "merging")

        assert result1 is not None
        assert result1.pr_number == 1
        assert result2 is None

    def test_get_merge_queue_by_status_all_statuses(self, temp_db):
        """Test getting PRs by all valid statuses."""
        temp_db.add_to_merge_queue("github.com/owner/repo", 1, 0)
        temp_db.add_to_merge_queue("github.com/owner/repo", 2, 1)
        temp_db.add_to_merge_queue("github.com/owner/repo", 3, 2)
        temp_db.add_to_merge_queue("github.com/owner/repo", 4, 3)

        temp_db.update_merge_queue_status("github.com/owner/repo", 2, "merging")
        temp_db.update_merge_queue_status("github.com/owner/repo", 3, "waiting_rebase")
        temp_db.update_merge_queue_status("github.com/owner/repo", 4, "waiting_ci")

        queued = temp_db.get_merge_queue_by_status("github.com/owner/repo", "queued")
        merging = temp_db.get_merge_queue_by_status("github.com/owner/repo", "merging")
        waiting_rebase = temp_db.get_merge_queue_by_status(
            "github.com/owner/repo", "waiting_rebase"
        )
        waiting_ci = temp_db.get_merge_queue_by_status("github.com/owner/repo", "waiting_ci")

        assert queued is not None and queued.pr_number == 1
        assert merging is not None and merging.pr_number == 2
        assert waiting_rebase is not None and waiting_rebase.pr_number == 3
        assert waiting_ci is not None and waiting_ci.pr_number == 4


@pytest.mark.unit
class TestMergeQueueIntegration:
    """Integration tests for merge queue operations."""

    def test_full_queue_lifecycle(self, temp_db):
        """Test a complete queue lifecycle: add, process, remove."""
        # Add multiple PRs to queue
        for i, pr in enumerate([10, 20, 30]):
            temp_db.add_to_merge_queue("github.com/owner/repo", pr, i)

        # Verify queue state
        queue = temp_db.get_merge_queue("github.com/owner/repo")
        assert len(queue) == 3
        assert all(e.status == "queued" for e in queue)

        # Start merging the first one
        temp_db.update_merge_queue_status("github.com/owner/repo", 10, "merging")
        merging = temp_db.get_merge_queue_by_status("github.com/owner/repo", "merging")
        assert merging.pr_number == 10

        # Merge complete, remove from queue
        temp_db.remove_from_merge_queue("github.com/owner/repo", 10)

        # Trigger rebase on next
        temp_db.update_merge_queue_status("github.com/owner/repo", 20, "waiting_rebase")

        queue = temp_db.get_merge_queue("github.com/owner/repo")
        assert len(queue) == 2
        assert queue[0].pr_number == 20
        assert queue[0].position == 0
        assert queue[0].status == "waiting_rebase"
        assert queue[1].pr_number == 30
        assert queue[1].position == 1
        assert queue[1].status == "queued"

    def test_queue_survives_restart(self, temp_db):
        """Test that queue state persists across database reconnection."""
        temp_db.add_to_merge_queue("github.com/owner/repo", 123, 0)
        temp_db.update_merge_queue_status("github.com/owner/repo", 123, "merging")

        # Close and reopen database (simulating restart)
        temp_db.close()

        queue = temp_db.get_merge_queue("github.com/owner/repo")
        assert len(queue) == 1
        assert queue[0].pr_number == 123
        assert queue[0].status == "merging"
