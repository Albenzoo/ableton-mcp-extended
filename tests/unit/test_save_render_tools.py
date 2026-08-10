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

from MCP_Server.server import save_ableton_project, start_master_recording, stop_master_recording


@patch('MCP_Server.server.get_ableton_connection')
def test_save_ableton_project_sends_path(mock_conn):
    mock_ableton = MagicMock()
    mock_ableton.send_command.return_value = {"saved_to": "C:/Music/Lofi Animal/Panda/001_Panda_Study.als"}
    mock_conn.return_value = mock_ableton

    with patch('MCP_Server.server._ensure_save_helper', return_value=""):
        result = save_ableton_project(MagicMock(), path="C:/Music/Lofi Animal/Panda/001_Panda_Study.als")

    assert "001_Panda_Study.als" in result
    mock_ableton.send_command.assert_called_once_with(
        "save_project", {"path": "C:/Music/Lofi Animal/Panda/001_Panda_Study.als"})


@patch('MCP_Server.server.get_ableton_connection')
def test_save_ableton_project_returns_helper_error(mock_conn):
    with patch('MCP_Server.server._ensure_save_helper', return_value="helper not running"):
        result = save_ableton_project(MagicMock(), path="C:/Music/Lofi Animal/Panda/001_Panda_Study.als")

    assert "helper not running" in result
    mock_conn.return_value.send_command.assert_not_called()


@patch('MCP_Server.server.get_ableton_connection')
def test_save_ableton_project_rejects_empty_path(mock_conn):
    mock_ableton = MagicMock()
    mock_conn.return_value = mock_ableton

    result = save_ableton_project(MagicMock(), path="")

    assert "path cannot be empty" in result
    mock_ableton.send_command.assert_not_called()


@patch('MCP_Server.server.get_ableton_connection')
def test_start_master_recording_sends_path(mock_conn):
    mock_ableton = MagicMock()
    mock_ableton.send_command.return_value = {"status": "recording", "path": "C:/Music/out.wav"}
    mock_conn.return_value = mock_ableton

    result = start_master_recording(MagicMock(), path="C:/Music/out.wav")

    assert "recording" in result
    mock_ableton.send_command.assert_called_once_with(
        "start_master_recording", {"path": "C:/Music/out.wav"})


@patch('MCP_Server.server.get_ableton_connection')
def test_start_master_recording_rejects_empty_path(mock_conn):
    mock_ableton = MagicMock()
    mock_conn.return_value = mock_ableton

    result = start_master_recording(MagicMock(), path="")

    assert "path cannot be empty" in result
    mock_ableton.send_command.assert_not_called()


@patch('MCP_Server.server.get_ableton_connection')
def test_stop_master_recording_sends_command(mock_conn):
    mock_ableton = MagicMock()
    mock_ableton.send_command.return_value = {"duration_sec": 172.0, "wav_path": "C:/Music/out.wav"}
    mock_conn.return_value = mock_ableton

    result = stop_master_recording(MagicMock())

    assert "172.0" in result
    assert "out.wav" in result
    mock_ableton.send_command.assert_called_once_with("stop_master_recording")
