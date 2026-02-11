"""Unit tests for MCP validation at startup and before workflow execution.

These tests verify:
- MCP_FAIL_ON_ERROR=true blocks startup when MCP servers fail
- MCP_FAIL_ON_ERROR=false allows startup with warnings
- Pre-workflow health check sends Slack notifications on failure
- OAuth token refresh before workflow execution
- Graceful degradation when MCP fails pre-workflow check
- Token substitution during validation (issue #304)
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.daemon import Daemon
from src.integrations.mcp_client import MCPTestResult
from src.integrations.mcp_config import MCPConfigManager


@pytest.fixture
def base_config(tmp_path):
    """Base configuration for daemon tests."""
    config = MagicMock()
    config.poll_interval = 60
    config.watched_statuses = ["Research", "Plan", "Implement"]
    config.max_concurrent_workflows = 2
    config.workspace_dir = str(tmp_path)
    config.database_path = str(tmp_path / "test.db")
    config.project_urls = ["https://github.com/orgs/test/projects/1"]

    config.github_enterprise_version = None
    config.github_enterprise_host = None
    config.github_token = None
    config.github_enterprise_token = None
    config.username_self = "test-bot"
    config.team_usernames = []
    config.ghes_logs_mask = False

    config.azure_tenant_id = None
    config.azure_client_id = None
    config.azure_username = None
    config.azure_password = None
    config.azure_scope = None
    config.mcp_fail_on_error = False
    return config


@pytest.fixture
def mock_mcp_config(tmp_path):
    """Create a temporary MCP config with servers."""
    kiln_dir = tmp_path / ".kiln"
    kiln_dir.mkdir()
    mcp_config_path = kiln_dir / "mcp.json"

    config_data = {
        "mcpServers": {
            "jenkins": {
                "url": "https://jenkins.example.com/mcp",
                "env": {"AUTHORIZATION": "Bearer test-token"},
            },
            "filesystem": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/fs-server"],
            },
        }
    }
    mcp_config_path.write_text(json.dumps(config_data))

    # Change to this directory so MCPConfigManager finds the config
    import os

    original_cwd = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(original_cwd)


@pytest.mark.unit
class TestMCPFailOnErrorStartup:
    """Tests for MCP_FAIL_ON_ERROR startup validation behavior."""

    def test_mcp_fail_on_error_true_raises_on_failure(self, base_config, mock_mcp_config):
        """Test that daemon raises RuntimeError when MCP_FAIL_ON_ERROR=true and servers fail."""
        base_config.mcp_fail_on_error = True

        # Mock check_all_mcp_servers to return failure results
        mock_results = [
            MCPTestResult(
                server_name="jenkins",
                success=False,
                error="timeout after 30s",
            ),
            MCPTestResult(
                server_name="filesystem",
                success=True,
                tools=["read_file"],
            ),
        ]

        with (
            patch("src.ticket_clients.github.GitHubTicketClient"),
            patch("src.daemon.check_all_mcp_servers", return_value=mock_results),
            pytest.raises(RuntimeError) as exc_info,
        ):
            Daemon(base_config)

        assert "MCP validation failed for: jenkins" in str(exc_info.value)

    def test_mcp_fail_on_error_true_with_all_failures(self, base_config, mock_mcp_config):
        """Test that daemon raises RuntimeError listing all failed servers."""
        base_config.mcp_fail_on_error = True

        mock_results = [
            MCPTestResult(
                server_name="jenkins",
                success=False,
                error="network unreachable",
            ),
            MCPTestResult(
                server_name="filesystem",
                success=False,
                error="command not found",
            ),
        ]

        with (
            patch("src.ticket_clients.github.GitHubTicketClient"),
            patch("src.daemon.check_all_mcp_servers", return_value=mock_results),
            pytest.raises(RuntimeError) as exc_info,
        ):
            Daemon(base_config)

        error_msg = str(exc_info.value)
        assert "MCP validation failed for:" in error_msg
        assert "jenkins" in error_msg
        assert "filesystem" in error_msg

    def test_mcp_fail_on_error_true_success_does_not_raise(self, base_config, mock_mcp_config):
        """Test that daemon starts normally when all MCP servers pass."""
        base_config.mcp_fail_on_error = True

        mock_results = [
            MCPTestResult(
                server_name="jenkins",
                success=True,
                tools=["build_job", "get_logs"],
            ),
            MCPTestResult(
                server_name="filesystem",
                success=True,
                tools=["read_file", "write_file"],
            ),
        ]

        with (
            patch("src.ticket_clients.github.GitHubTicketClient"),
            patch("src.daemon.check_all_mcp_servers", return_value=mock_results),
        ):
            daemon = Daemon(base_config)
            assert daemon is not None
            daemon.stop()

    def test_mcp_fail_on_error_false_allows_startup_with_warnings(
        self, base_config, mock_mcp_config
    ):
        """Test that daemon starts with warnings when MCP_FAIL_ON_ERROR=false and servers fail."""
        base_config.mcp_fail_on_error = False

        mock_results = [
            MCPTestResult(
                server_name="jenkins",
                success=False,
                error="timeout after 30s",
            ),
            MCPTestResult(
                server_name="filesystem",
                success=False,
                error="command not found",
            ),
        ]

        with (
            patch("src.ticket_clients.github.GitHubTicketClient"),
            patch("src.daemon.check_all_mcp_servers", return_value=mock_results),
            patch("src.daemon.logger") as mock_logger,
        ):
            # Daemon should initialize successfully despite MCP failures
            daemon = Daemon(base_config)
            assert daemon is not None

            # Verify warnings were logged
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            jenkins_warning = next((c for c in warning_calls if "jenkins" in c.lower()), None)
            filesystem_warning = next((c for c in warning_calls if "filesystem" in c.lower()), None)

            assert jenkins_warning is not None, "Jenkins MCP failure should be logged"
            assert filesystem_warning is not None, "Filesystem MCP failure should be logged"

            daemon.stop()

    def test_mcp_fail_on_error_logs_hint_when_false(self, base_config, mock_mcp_config):
        """Test that daemon logs a hint about MCP_FAIL_ON_ERROR when servers fail."""
        base_config.mcp_fail_on_error = False

        mock_results = [
            MCPTestResult(
                server_name="jenkins",
                success=False,
                error="connection refused",
            ),
        ]

        with (
            patch("src.ticket_clients.github.GitHubTicketClient"),
            patch("src.daemon.check_all_mcp_servers", return_value=mock_results),
            patch("src.daemon.logger") as mock_logger,
        ):
            daemon = Daemon(base_config)

            # Verify hint about MCP_FAIL_ON_ERROR is logged
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            hint_logged = any("MCP_FAIL_ON_ERROR=true" in call for call in warning_calls)
            assert hint_logged, "Should log hint about MCP_FAIL_ON_ERROR setting"

            daemon.stop()


@pytest.mark.unit
class TestPreWorkflowMCPHealthCheck:
    """Tests for pre-workflow MCP health check behavior."""

    def test_health_check_returns_true_when_no_mcp_config(self, base_config):
        """Test that health check returns True when no MCP config exists."""
        with (
            patch("src.ticket_clients.github.GitHubTicketClient"),
            patch("src.daemon.MCPConfigManager") as mock_mcp_class,
        ):
            mock_mcp_instance = MagicMock()
            mock_mcp_instance.validate_config.return_value = []
            mock_mcp_instance.load_config.return_value = None
            mock_mcp_class.return_value = mock_mcp_instance

            daemon = Daemon(base_config)

            result = daemon._check_mcp_health_before_workflow(issue_number=42)

            assert result is True
            daemon.stop()

    def test_health_check_returns_true_when_all_healthy(self, base_config, mock_mcp_config):
        """Test that health check returns True when all servers are healthy."""
        mock_results = [
            MCPTestResult(server_name="jenkins", success=True, tools=["build"]),
            MCPTestResult(server_name="filesystem", success=True, tools=["read"]),
        ]

        with (
            patch("src.ticket_clients.github.GitHubTicketClient"),
            patch("src.daemon.check_all_mcp_servers", return_value=mock_results),
        ):
            daemon = Daemon(base_config)

            # Now mock check_all_mcp_servers for the health check call
            with patch("src.daemon.check_all_mcp_servers", return_value=mock_results):
                result = daemon._check_mcp_health_before_workflow(issue_number=42)

            assert result is True
            daemon.stop()

    def test_health_check_returns_false_on_failure(self, base_config, mock_mcp_config):
        """Test that health check returns False when servers fail."""
        # Startup passes
        startup_results = [
            MCPTestResult(server_name="jenkins", success=True, tools=["build"]),
        ]

        # Health check fails
        health_check_results = [
            MCPTestResult(
                server_name="jenkins",
                success=False,
                error="connection refused",
            ),
        ]

        with (
            patch("src.ticket_clients.github.GitHubTicketClient"),
            patch("src.daemon.check_all_mcp_servers", return_value=startup_results),
        ):
            daemon = Daemon(base_config)

            with patch("src.daemon.check_all_mcp_servers", return_value=health_check_results):
                result = daemon._check_mcp_health_before_workflow(issue_number=42)

            assert result is False
            daemon.stop()

    def test_health_check_sends_slack_notification_on_failure(self, base_config, mock_mcp_config):
        """Test that health check sends Slack notification when servers fail."""
        startup_results = [
            MCPTestResult(server_name="jenkins", success=True, tools=["build"]),
        ]

        health_check_results = [
            MCPTestResult(
                server_name="jenkins",
                success=False,
                error="timeout after 30s",
            ),
        ]

        with (
            patch("src.ticket_clients.github.GitHubTicketClient"),
            patch("src.daemon.check_all_mcp_servers", return_value=startup_results),
        ):
            daemon = Daemon(base_config)

            with (
                patch("src.daemon.check_all_mcp_servers", return_value=health_check_results),
                patch("src.daemon.send_mcp_failure_notification") as mock_slack,
            ):
                daemon._check_mcp_health_before_workflow(issue_number=42)

                mock_slack.assert_called_once_with(
                    "jenkins",
                    "timeout after 30s",
                    42,
                )

            daemon.stop()

    def test_health_check_sends_notification_for_each_failed_server(
        self, base_config, mock_mcp_config
    ):
        """Test that health check sends Slack notification for each failed server."""
        startup_results = [
            MCPTestResult(server_name="jenkins", success=True, tools=["build"]),
            MCPTestResult(server_name="filesystem", success=True, tools=["read"]),
        ]

        health_check_results = [
            MCPTestResult(
                server_name="jenkins",
                success=False,
                error="network unreachable",
            ),
            MCPTestResult(
                server_name="filesystem",
                success=False,
                error="command not found",
            ),
        ]

        with (
            patch("src.ticket_clients.github.GitHubTicketClient"),
            patch("src.daemon.check_all_mcp_servers", return_value=startup_results),
        ):
            daemon = Daemon(base_config)

            with (
                patch("src.daemon.check_all_mcp_servers", return_value=health_check_results),
                patch("src.daemon.send_mcp_failure_notification") as mock_slack,
            ):
                daemon._check_mcp_health_before_workflow(issue_number=123)

                assert mock_slack.call_count == 2

                # Verify calls for each server
                call_args_list = mock_slack.call_args_list
                server_names = [call[0][0] for call in call_args_list]
                assert "jenkins" in server_names
                assert "filesystem" in server_names

            daemon.stop()

    def test_health_check_logs_warning_on_failure(self, base_config, mock_mcp_config):
        """Test that health check logs warning when servers fail."""
        startup_results = [
            MCPTestResult(server_name="jenkins", success=True, tools=["build"]),
        ]

        health_check_results = [
            MCPTestResult(
                server_name="jenkins",
                success=False,
                error="connection refused",
            ),
        ]

        with (
            patch("src.ticket_clients.github.GitHubTicketClient"),
            patch("src.daemon.check_all_mcp_servers", return_value=startup_results),
        ):
            daemon = Daemon(base_config)

            with (
                patch("src.daemon.check_all_mcp_servers", return_value=health_check_results),
                patch("src.daemon.send_mcp_failure_notification"),
                patch("src.daemon.logger") as mock_logger,
            ):
                daemon._check_mcp_health_before_workflow(issue_number=42)

                warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
                jenkins_warning = next((c for c in warning_calls if "jenkins" in c.lower()), None)
                assert jenkins_warning is not None
                assert "unavailable before workflow" in jenkins_warning

            daemon.stop()


@pytest.mark.unit
class TestPreWorkflowOAuthRefresh:
    """Tests for OAuth token refresh before workflow execution."""

    def test_refresh_tokens_called_before_workflow(self):
        """Test that refresh_mcp_tokens is called before writing MCP config to worktree."""
        from src.integrations.azure_oauth import AzureOAuthClient

        mock_azure_client = MagicMock(spec=AzureOAuthClient)
        mock_azure_client.get_token.return_value = "test-token"

        manager = MCPConfigManager(azure_client=mock_azure_client)

        # Simulate refresh call
        result = manager.refresh_mcp_tokens()

        assert result is True
        mock_azure_client.clear_token.assert_called_once()
        mock_azure_client.get_token.assert_called_once()

    def test_refresh_tokens_handles_failure(self):
        """Test that refresh_mcp_tokens returns False on failure."""
        from src.integrations.azure_oauth import AzureOAuthClient, AzureTokenRequestError

        mock_azure_client = MagicMock(spec=AzureOAuthClient)
        mock_azure_client.get_token.side_effect = AzureTokenRequestError("Auth failed")

        manager = MCPConfigManager(azure_client=mock_azure_client)

        result = manager.refresh_mcp_tokens()

        assert result is False

    def test_refresh_tokens_returns_true_when_no_azure_client(self):
        """Test that refresh_mcp_tokens returns True when no Azure client configured."""
        manager = MCPConfigManager(azure_client=None)

        result = manager.refresh_mcp_tokens()

        assert result is True


@pytest.mark.unit
class TestGracefulDegradation:
    """Tests for graceful degradation when MCP fails pre-workflow check."""

    def test_workflow_proceeds_without_mcp_when_health_check_fails(
        self, base_config, mock_mcp_config
    ):
        """Test that workflow can proceed without MCP config when health check fails."""
        startup_results = [
            MCPTestResult(server_name="jenkins", success=True, tools=["build"]),
        ]

        health_check_results = [
            MCPTestResult(
                server_name="jenkins",
                success=False,
                error="server down",
            ),
        ]

        with (
            patch("src.ticket_clients.github.GitHubTicketClient"),
            patch("src.daemon.check_all_mcp_servers", return_value=startup_results),
        ):
            daemon = Daemon(base_config)

            # Health check should return False
            with (
                patch("src.daemon.check_all_mcp_servers", return_value=health_check_results),
                patch("src.daemon.send_mcp_failure_notification"),
            ):
                mcp_healthy = daemon._check_mcp_health_before_workflow(issue_number=42)

            assert mcp_healthy is False
            # The caller should set mcp_config_path = None based on this result
            # This allows workflow to proceed without MCP

            daemon.stop()

    def test_mixed_success_failure_reports_only_failures(self, base_config, mock_mcp_config):
        """Test that only failed servers are reported when some succeed."""
        startup_results = [
            MCPTestResult(server_name="jenkins", success=True, tools=["build"]),
            MCPTestResult(server_name="filesystem", success=True, tools=["read"]),
        ]

        health_check_results = [
            MCPTestResult(server_name="jenkins", success=False, error="down"),
            MCPTestResult(server_name="filesystem", success=True, tools=["read"]),
        ]

        with (
            patch("src.ticket_clients.github.GitHubTicketClient"),
            patch("src.daemon.check_all_mcp_servers", return_value=startup_results),
        ):
            daemon = Daemon(base_config)

            with (
                patch("src.daemon.check_all_mcp_servers", return_value=health_check_results),
                patch("src.daemon.send_mcp_failure_notification") as mock_slack,
            ):
                result = daemon._check_mcp_health_before_workflow(issue_number=42)

            assert result is False  # Still fails because one server failed
            mock_slack.assert_called_once()  # Only one notification (for jenkins)
            assert mock_slack.call_args[0][0] == "jenkins"

            daemon.stop()

    def test_health_check_without_issue_number(self, base_config, mock_mcp_config):
        """Test that health check works without issue number context."""
        startup_results = [
            MCPTestResult(server_name="jenkins", success=True, tools=["build"]),
        ]

        health_check_results = [
            MCPTestResult(server_name="jenkins", success=False, error="timeout"),
        ]

        with (
            patch("src.ticket_clients.github.GitHubTicketClient"),
            patch("src.daemon.check_all_mcp_servers", return_value=startup_results),
        ):
            daemon = Daemon(base_config)

            with (
                patch("src.daemon.check_all_mcp_servers", return_value=health_check_results),
                patch("src.daemon.send_mcp_failure_notification") as mock_slack,
            ):
                daemon._check_mcp_health_before_workflow(issue_number=None)

                # Should still send notification with None as issue_number
                mock_slack.assert_called_once_with("jenkins", "timeout", None)

            daemon.stop()


@pytest.fixture
def mock_mcp_config_with_token_placeholder(tmp_path):
    """Create a temporary MCP config with token placeholder."""
    kiln_dir = tmp_path / ".kiln"
    kiln_dir.mkdir()
    mcp_config_path = kiln_dir / "mcp.json"

    config_data = {
        "mcpServers": {
            "azure-mcp": {
                "url": "https://azure.example.com/mcp",
                "headers": {"Authorization": "Bearer ${AZURE_BEARER_TOKEN}"},
            },
        }
    }
    mcp_config_path.write_text(json.dumps(config_data))

    # Change to this directory so MCPConfigManager finds the config
    import os

    original_cwd = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(original_cwd)


@pytest.mark.unit
class TestTokenSubstitutionDuringValidation:
    """Tests for token substitution during MCP validation (issue #304).

    These tests verify that both `_validate_mcp_connections()` (startup validation)
    and `_check_mcp_health_before_workflow()` use token-substituted configs
    instead of raw configs with placeholders.
    """

    def test_startup_validation_uses_substituted_tokens(
        self, base_config, mock_mcp_config_with_token_placeholder
    ):
        """Test that _validate_mcp_connections() uses get_substituted_mcp_servers()."""
        base_config.mcp_fail_on_error = False

        mock_results = [
            MCPTestResult(
                server_name="azure-mcp",
                success=True,
                tools=["test_tool"],
            ),
        ]

        # Create a mock Azure client that returns a test token
        mock_azure_client = MagicMock()
        mock_azure_client.get_token.return_value = "actual-azure-token"

        with (
            patch("src.ticket_clients.github.GitHubTicketClient"),
            patch("src.daemon.check_all_mcp_servers", return_value=mock_results) as mock_check,
            patch("src.daemon.AzureOAuthClient", return_value=mock_azure_client),
        ):
            # Set Azure credentials so the client is created
            base_config.azure_tenant_id = "test-tenant"
            base_config.azure_client_id = "test-client"
            base_config.azure_username = "test-user"
            base_config.azure_password = "test-pass"
            base_config.azure_scope = "test-scope"

            daemon = Daemon(base_config)

            # Verify check_all_mcp_servers was called with substituted tokens
            mock_check.assert_called()
            call_args = mock_check.call_args[0][0]

            # The token placeholder should be substituted with actual token
            azure_mcp_config = call_args.get("azure-mcp", {})
            headers = azure_mcp_config.get("headers", {})
            auth_header = headers.get("Authorization", "")

            assert "actual-azure-token" in auth_header
            assert "${AZURE_BEARER_TOKEN}" not in auth_header

            daemon.stop()

    def test_pre_workflow_health_check_uses_substituted_tokens(
        self, base_config, mock_mcp_config_with_token_placeholder
    ):
        """Test that _check_mcp_health_before_workflow() uses get_substituted_mcp_servers()."""
        base_config.mcp_fail_on_error = False

        startup_results = [
            MCPTestResult(server_name="azure-mcp", success=True, tools=["test_tool"]),
        ]

        health_check_results = [
            MCPTestResult(server_name="azure-mcp", success=True, tools=["test_tool"]),
        ]

        # Create a mock Azure client that returns a test token
        mock_azure_client = MagicMock()
        mock_azure_client.get_token.return_value = "workflow-azure-token"

        with (
            patch("src.ticket_clients.github.GitHubTicketClient"),
            patch("src.daemon.check_all_mcp_servers", return_value=startup_results),
            patch("src.daemon.AzureOAuthClient", return_value=mock_azure_client),
        ):
            # Set Azure credentials so the client is created
            base_config.azure_tenant_id = "test-tenant"
            base_config.azure_client_id = "test-client"
            base_config.azure_username = "test-user"
            base_config.azure_password = "test-pass"
            base_config.azure_scope = "test-scope"

            daemon = Daemon(base_config)

            # Now call health check and verify substitution
            with patch(
                "src.daemon.check_all_mcp_servers", return_value=health_check_results
            ) as mock_check:
                result = daemon._check_mcp_health_before_workflow(issue_number=42)

                assert result is True

                # Verify check_all_mcp_servers was called with substituted tokens
                mock_check.assert_called()
                call_args = mock_check.call_args[0][0]

                # The token placeholder should be substituted with actual token
                azure_mcp_config = call_args.get("azure-mcp", {})
                headers = azure_mcp_config.get("headers", {})
                auth_header = headers.get("Authorization", "")

                assert "workflow-azure-token" in auth_header
                assert "${AZURE_BEARER_TOKEN}" not in auth_header

            daemon.stop()

    def test_validation_works_without_azure_client(self, base_config, mock_mcp_config):
        """Test that validation works normally when no Azure client is configured."""
        base_config.mcp_fail_on_error = False

        mock_results = [
            MCPTestResult(server_name="jenkins", success=True, tools=["build"]),
            MCPTestResult(server_name="filesystem", success=True, tools=["read"]),
        ]

        with (
            patch("src.ticket_clients.github.GitHubTicketClient"),
            patch("src.daemon.check_all_mcp_servers", return_value=mock_results) as mock_check,
        ):
            daemon = Daemon(base_config)

            # Verify check_all_mcp_servers was called
            mock_check.assert_called()

            daemon.stop()

    def test_startup_validation_returns_empty_when_no_config(self, base_config):
        """Test that validation with empty config doesn't call check_all_mcp_servers."""
        with (
            patch("src.ticket_clients.github.GitHubTicketClient"),
            patch("src.daemon.check_all_mcp_servers") as mock_check,
            patch("src.daemon.MCPConfigManager") as mock_mcp_class,
        ):
            mock_mcp_instance = MagicMock()
            mock_mcp_instance.validate_config.return_value = []
            mock_mcp_instance.get_substituted_mcp_servers.return_value = {}
            mock_mcp_class.return_value = mock_mcp_instance

            daemon = Daemon(base_config)

            # check_all_mcp_servers should not be called when no servers exist
            mock_check.assert_not_called()

            daemon.stop()
