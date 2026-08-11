"""Unit tests for AbletonMCP Remote Script helpers."""

import os
import socket
import sys
import types
from unittest.mock import MagicMock, PropertyMock, patch

import pytest


class _FrameworkControlSurface:
    def __init__(self, c_instance):
        pass

    def log_message(self, msg):
        pass


_framework = types.ModuleType("_Framework")
_cs_module = types.ModuleType("_Framework.ControlSurface")
_cs_module.ControlSurface = _FrameworkControlSurface
sys.modules.setdefault("_Framework", _framework)
sys.modules.setdefault("_Framework.ControlSurface", _cs_module)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from AbletonMCP_Remote_Script import AbletonMCP  # noqa: E402

import AbletonMCP_Remote_Script as _remote_script  # noqa: E402


class _FakeInputRoutingType:
    @staticmethod
    def input_routing_type(name):
        return name


class _FakeLive:
    InputRoutingType = _FakeInputRoutingType


# In Ableton's scripting runtime `Live` is a provided builtin module; fake it
# here so the handler's `Live.InputRoutingType.input_routing_type(...)` call works
# outside that runtime.
_remote_script.Live = _FakeLive()


class _StubControlSurface(AbletonMCP):
    def __init__(self, c_instance):
        pass

    def log_message(self, msg):
        pass


class _NormalTrack:
    is_foldable = False

    def __init__(self, name="Track", arm=False, has_midi_input=True,
                 has_audio_input=False, arrangement_clips=None):
        self.name = name
        self.arm = arm
        self.mute = False
        self.solo = False
        self.has_midi_input = has_midi_input
        self.has_audio_input = has_audio_input
        self.clip_slots = []
        self.devices = []
        self.arrangement_clips = list(arrangement_clips or [])
        self.mixer_device = MagicMock()
        self.mixer_device.volume.value = 0.85
        self.mixer_device.panning.value = 0.0


class _GroupTrack:
    is_foldable = True

    def __init__(self, name="Mix Bus"):
        self.name = name
        self.mute = False
        self.solo = False
        self.has_midi_input = False
        self.has_audio_input = False
        self.clip_slots = []
        self.devices = []
        self.mixer_device = MagicMock()
        self.mixer_device.volume.value = 0.85
        self.mixer_device.panning.value = 0.0

    @property
    def arm(self):
        raise RuntimeError("Master and Return Tracks have no 'Arm' state!")

    @property
    def arrangement_clips(self):
        raise RuntimeError(
            "Master, Group and Return Tracks have no arrangement clips")


def _make_script(tracks=()):
    script = AbletonMCP.__new__(AbletonMCP)
    script._song = MagicMock()
    script._song.tracks = list(tracks)
    script._song.return_tracks = []
    script._song.master_track = MagicMock()
    return script


class TestGetTrackInfoOnGroupTrack:
    def test_returns_info_without_raising(self):
        script = _make_script([_GroupTrack("Mix Bus")])

        result = script._get_track_info(0)

        assert result["name"] == "Mix Bus"
        assert result["is_group_track"] is True
        assert result["arm"] is None

    def test_normal_track_still_reports_arm(self):
        script = _make_script([_NormalTrack("Synth", arm=True)])

        result = script._get_track_info(0)

        assert result["arm"] is True
        assert result["is_group_track"] is False


class TestGetArrangementInfoSkipsGroupTracks:
    def test_all_tracks_skips_group(self):
        normal = _NormalTrack("Drums")
        group = _GroupTrack("Mix Bus")
        script = _make_script([group, normal])

        result = script._get_arrangement_info(-1)

        names = [t["name"] for t in result["tracks"]]
        assert names == ["Drums"]

    def test_explicit_group_returns_empty_clips(self):
        script = _make_script([_GroupTrack("Mix Bus")])

        result = script._get_arrangement_info(0)

        assert len(result["tracks"]) == 1
        assert result["tracks"][0]["arrangement_clips"] == []
        assert result["tracks"][0]["is_group_track"] is True

    def test_normal_track_unaffected(self):
        script = _make_script([_NormalTrack("Drums")])

        result = script._get_arrangement_info(0)

        assert result["tracks"][0]["name"] == "Drums"
        assert result["tracks"][0]["is_group_track"] is False


class TestCreateCuePointAssignsName:
    @staticmethod
    def _wire_toggle(script, returned_cue):
        script._song.cue_points = ()

        def toggle():
            script._song.cue_points = (returned_cue,)

        script._song.set_or_delete_cue.side_effect = toggle

    def test_assigns_name_to_created_cue(self):
        script = _make_script()
        cue = MagicMock()
        cue.time = 16.0
        cue.name = ""
        self._wire_toggle(script, cue)

        script._create_cue_point(time=16.0, name="Drop")

        assert cue.name == "Drop"

    def test_blank_name_does_not_overwrite(self):
        script = _make_script()
        cue = MagicMock()
        cue.time = 16.0
        cue.name = "1.1.1"
        self._wire_toggle(script, cue)

        script._create_cue_point(time=16.0, name="")

        assert cue.name == "1.1.1"


def test_save_project_posts_to_save_helper():
    cs = _StubControlSurface(None)
    cs._song = MagicMock()
    cs.log_message = lambda msg: None
    mock_sock = MagicMock()
    mock_sock.recv.side_effect = [b'{"status": "ok", "path": "C:/out.als"}', b""]
    with patch("AbletonMCP_Remote_Script.socket.socket", return_value=mock_sock) as mock_socket_cls:
        result = cs._save_project("C:/out.als")
    assert result["saved_to"] == "C:/out.als"
    assert result["mode"] == "save_helper"
    mock_socket_cls.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM)
    mock_sock.connect.assert_called_once_with(("127.0.0.1", 9878))
    sent = mock_sock.sendall.call_args[0][0].decode("utf-8")
    assert "POST /save_as" in sent
    assert '"path": "C:/out.als"' in sent


def test_save_project_sends_utf8_for_non_ascii_path():
    cs = _StubControlSurface(None)
    cs._song = MagicMock()
    cs.log_message = lambda msg: None
    mock_sock = MagicMock()
    mock_sock.recv.side_effect = [b'{"status": "ok", "path": "C:/out.als"}', b""]
    non_ascii = "C:/M\xfasica/Studio/out.als"
    with patch("AbletonMCP_Remote_Script.socket.socket", return_value=mock_sock):
        result = cs._save_project(non_ascii)
    assert result["saved_to"] == non_ascii
    sent = mock_sock.sendall.call_args[0][0]
    # The request must be utf-8 encoded so non-ASCII path characters survive.
    assert non_ascii in sent.decode("utf-8")


def test_start_master_recording_sets_up_track():
    cs = _StubControlSurface(None)
    cs._song = MagicMock()
    cs.log_message = lambda msg: None
    rec_track = MagicMock()
    rec_track.available_input_routing_types = [
        MagicMock(display_name="Resampling")
    ]
    cs._song.create_audio_track.return_value = rec_track
    result = cs._start_master_recording("C:/Music/out.wav")
    assert result["status"] == "recording"
    assert cs._song.create_audio_track.called
    assert rec_track.arm is True
    assert rec_track.name == "MCP Render Temp"
    cs._song.start_playing.assert_called_once()


def test_start_master_recording_deletes_orphan_track_when_resampling_missing():
    cs = _StubControlSurface(None)
    cs._song = MagicMock()
    cs.log_message = lambda msg: None
    existing = MagicMock()
    rec_track = MagicMock()
    rec_track.available_input_routing_types = []
    cs._song.tracks = [existing]

    def create_track():
        cs._song.tracks = cs._song.tracks + [rec_track]
        return rec_track

    cs._song.create_audio_track.side_effect = create_track

    with pytest.raises(RuntimeError):
        cs._start_master_recording("C:/Music/out.wav")

    # The orphaned "MCP Render Temp" track must be deleted.
    cs._song.delete_track.assert_called_once_with(1)
    assert getattr(cs, "_recording", None) is None


def test_stop_master_recording_writes_wav():
    import tempfile
    cs = _StubControlSurface(None)
    cs._song = MagicMock()
    cs.log_message = lambda msg: None
    rec_clip = MagicMock()
    # Simulate a real recorded file on disk.
    tmpdir = tempfile.mkdtemp()
    src_dir = os.path.join(tmpdir, "Samples", "Recorded")
    os.makedirs(src_dir)
    src = os.path.join(src_dir, "rec.wav")
    with open(src, "wb") as f:
        f.write(b"RIFFdata")
    rec_clip.file_path = src
    slot = MagicMock()
    slot.has_clip = True
    slot.clip = rec_clip
    rec_track = MagicMock()
    rec_track.clip_slots = [slot]
    rec_track.arrangement_clips = []
    cs._song.tracks = [rec_track]
    dest = os.path.join(tmpdir, "out.wav")
    cs._recording = {
        "track": rec_track,
        "track_index": 0,
        "path": dest,
        "was_playing": False,
        "started": 1000.0,
    }
    result = cs._stop_master_recording()
    assert result["wav_path"] == dest
    assert result["recorded_file"] == src
    # The remote script locates the source; the server does the copy.
    assert result["source"] == src
    cs._song.stop_playing.assert_called()
    cs._song.delete_track.assert_called_once_with(0)
    assert cs._song.record_mode is False
    assert cs._song.arrangement_overdub is False
    assert getattr(cs, "_recording", None) is None


def test_stop_master_recording_cleans_state_when_delete_track_raises():
    cs = _StubControlSurface(None)
    cs._song = MagicMock()
    cs.log_message = lambda msg: None
    rec_clip = MagicMock()
    rec_clip.file_path = "C:/Samples/Recorded/rec.wav"
    slot = MagicMock()
    slot.has_clip = True
    slot.clip = rec_clip
    rec_track = MagicMock()
    rec_track.clip_slots = [slot]
    rec_track.arrangement_clips = []
    rec_track.arm = True
    cs._song.tracks = [rec_track]
    cs._song.record_mode = True
    cs._song.arrangement_overdub = True
    cs._recording = {
        "track": rec_track,
        "track_index": 0,
        "path": "C:/out.wav",
        "was_playing": False,
        "started": 1000.0,
    }
    cs._song.delete_track.side_effect = RuntimeError("delete failed")

    result = cs._stop_master_recording()

    # Cleanup still ran even though delete_track raised.
    assert result["source"] == "C:/Samples/Recorded/rec.wav"
    assert rec_track.arm is False
    assert cs._song.record_mode is False
    assert cs._song.arrangement_overdub is False
    assert getattr(cs, "_recording", None) is None


def test_stop_master_recording_cleans_state_when_earlier_step_raises():
    cs = _StubControlSurface(None)
    cs._song = MagicMock()
    cs.log_message = lambda msg: None
    rec_track = MagicMock()
    rec_track.clip_slots = []
    rec_track.arrangement_clips = []
    rec_track.arm = True
    cs._song.tracks = [rec_track]
    cs._song.record_mode = True
    cs._song.arrangement_overdub = True
    cs._recording = {
        "track": rec_track,
        "track_index": 0,
        "path": "C:/out.wav",
        "was_playing": False,
        "started": 1000.0,
    }
    cs._song.stop_playing.side_effect = RuntimeError("stop failed")

    with pytest.raises(RuntimeError):
        cs._stop_master_recording()

    # State is cleaned up in a finally block even when an earlier step raises.
    assert rec_track.arm is False
    assert cs._song.record_mode is False
    assert cs._song.arrangement_overdub is False
    assert getattr(cs, "_recording", None) is None
    cs._song.delete_track.assert_called_once_with(0)
