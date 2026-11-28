"""Narrator regression test: the approved voice must stay natural. Run:  python3 tests/test_voice.py"""
import importlib.util, pathlib, sys, re
here = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(here))
spec = importlib.util.spec_from_file_location("ms", here / "make_short.py")
ms = importlib.util.module_from_spec(spec); spec.loader.exec_module(ms)
N = ms.NARRATOR
assert N["provider"] == "gemini" and N["voice"] == "Charon" and N["approved_by_listening"] is True, N
assert N["pitch_shift_semitones"] == 0 and N["speaking_rate"] >= 1.0, "the approved narrator is not pitch-shifted or slowed (a lively pace is allowed)"
assert ms.DEEPEN == "", "no artificial pitch-down in the main mix"
assert "asetrate" not in ms.SCARY_CHAIN and "atempo" not in ms.SCARY_CHAIN, "no pitch/tempo change on the narrator"
src = (here / "make_short.py").read_text()
assert 'rate="-8%"' not in src and 'pitch="-6Hz"' not in src, "no slowed or lowered fallback voice"
assert ms.GEMINI_VOICE == "Charon" or "GEMINI_VOICE" in __import__("os").environ
print("NARRATOR VOICE CHECKS PASSED")
