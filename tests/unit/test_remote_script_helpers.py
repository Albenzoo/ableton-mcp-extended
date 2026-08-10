"""Unit tests for AbletonMCP Remote Script helpers."""

import os
import socket
import sys
import types
from unittest.mock import MagicMock, PropertyMock, patch


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
    sent = mock_sock.sendall.call_args[0][0].decode("ascii")
    assert "POST /save_as" in sent
    assert '"path": "C:/out.als"' in sent


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


def test_stop_master_recording_writes_wav():
    cs = _StubControlSurface(None)
    cs._song = MagicMock()
    cs.log_message = lambda msg: None
    rec_clip = MagicMock()
    rec_track = MagicMock()
    rec_track.arrangement_clips = [rec_clip]
    cs._song.tracks = [rec_track]
    cs._recording = {
        "track": rec_track,
        "path": "C:/Music/out.wav",
        "was_playing": False,
        "started": 1000.0,
    }
    result = cs._stop_master_recording()
    assert result["wav_path"] == "C:/Music/out.wav"
    rec_clip.create_audio_clip.assert_called_once_with("C:/Music/out.wav")
    cs._song.stop_playing.assert_called()
