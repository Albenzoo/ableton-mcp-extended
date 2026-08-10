"""Unit tests for AbletonMCP Remote Script helpers."""

import os
import sys
import types
from unittest.mock import MagicMock, PropertyMock


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


def test_save_project_calls_app_save_live_set():
    cs = _StubControlSurface(None)
    cs._song = MagicMock()
    cs.log_message = lambda msg: None
    mock_app = MagicMock()
    cs.application = MagicMock(return_value=mock_app)
    result = cs._save_project("C:/Music/Lofi Animal/Panda/001_Panda_Study.als")
    assert result["saved_to"] == "C:/Music/Lofi Animal/Panda/001_Panda_Study.als"
    assert result["mode"] == "application.save_live_set"
    mock_app.save_live_set.assert_called_once_with(
        "C:/Music/Lofi Animal/Panda/001_Panda_Study.als")


def test_save_project_falls_back_to_song_save_as():
    cs = _StubControlSurface(None)
    cs._song = MagicMock()
    cs.log_message = lambda msg: None
    mock_app = MagicMock()
    del mock_app.save_live_set
    cs.application = MagicMock(return_value=mock_app)
    result = cs._save_project("C:/Music/Lofi Animal/Panda/001_Panda_Study.als")
    assert result["saved_to"] == "C:/Music/Lofi Animal/Panda/001_Panda_Study.als"
    assert result["mode"] == "song.save_as"
    cs._song.save_as.assert_called_once_with(
        "C:/Music/Lofi Animal/Panda/001_Panda_Study.als")


def test_record_master_to_wav_creates_audio_track():
    cs = _StubControlSurface(None)
    cs._song = MagicMock()
    cs.log_message = lambda msg: None
    # First read (start of polling) returns 0.0, second read returns 200.0 (> 64) -> loop exits
    type(cs._song).current_song_time = PropertyMock(side_effect=[0.0, 200.0])
    cs._song.tempo = 78.0
    cs._song.tracks = []
    result = cs._record_master_to_wav("C:/Music/out.wav")
    assert result["wav_path"] == "C:/Music/out.wav"
    assert cs._song.create_audio_track.called
