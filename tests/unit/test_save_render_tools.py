"""Unit tests for save/render MCP tools."""
import sys
import os
from unittest.mock import MagicMock, patch

_mock_mcp_module = MagicMock()
_mock_fastmcp = MagicMock()
_mock_fastmcp.FastMCP.return_value.tool.return_value = lambda fn: fn
sys.modules['mcp'] = _mock_mcp_module
sys.modules['mcp.server'] = MagicMock()
sys.modules['mcp.server.fastmcp'] = _mock_fastmcp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from MCP_Server.server import save_ableton_project


@patch('MCP_Server.server.get_ableton_connection')
def test_save_ableton_project_sends_path(mock_conn):
    mock_ableton = MagicMock()
    mock_ableton.send_command.return_value = {"saved_to": "C:/Music/Lofi Animal/Panda/001_Panda_Study.als"}
    mock_conn.return_value = mock_ableton

    result = save_ableton_project(MagicMock(), path="C:/Music/Lofi Animal/Panda/001_Panda_Study.als")

    assert "001_Panda_Study.als" in result
    mock_ableton.send_command.assert_called_once_with(
        "save_project", {"path": "C:/Music/Lofi Animal/Panda/001_Panda_Study.als"})


@patch('MCP_Server.server.get_ableton_connection')
def test_save_ableton_project_rejects_empty_path(mock_conn):
    mock_ableton = MagicMock()
    mock_conn.return_value = mock_ableton

    result = save_ableton_project(MagicMock(), path="")

    assert "path cannot be empty" in result
    mock_ableton.send_command.assert_not_called()
