"""Unit tests verifying contracts and data classes."""
from src.core.contracts import KeyType, KeyEvent, HarmonyEvent, PlaybackState, AudioVoice


def test_contracts_initialization():
    k_ev = KeyEvent(timestamp=1.0, key_type="regular", raw_char="a", wpm=60.0)
    assert k_ev.timestamp == 1.0
    assert k_ev.key_type == "regular"

    h_ev = HarmonyEvent(
        event_type="note",
        frequency=440.0,
        velocity=0.8,
        lpf_cutoff=1.0,
        duration=1.0,
        timestamp=1.0
    )
    assert h_ev.frequency == 440.0

    pb = PlaybackState()
    assert pb.target_bpm == 75.0
    assert pb.scale_root == 62

    v = AudioVoice()
    assert v.frequency == 0.0
    assert v.is_active is False

    assert KeyType.RHYTHM != KeyType.BACKSPACE_LPF
