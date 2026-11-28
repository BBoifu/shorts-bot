"""Regression test for the kill close-up, the opening hook, camera angles and the narrator-protection guard.
Run before delivering ANY upgrade:  python3 tests/test_features.py"""
import importlib.util, pathlib, sys
here = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(here))
sys.argv = ["x"]
spec = importlib.util.spec_from_file_location("ms", here / "make_short.py")
ms = importlib.util.module_from_spec(spec); spec.loader.exec_module(ms)
import toon
from PIL import Image

# 1. camera angles: the kill, host scenes, the first and last shot keep their own framing; neighbours differ
sh = {"insert": False}
assert ms.shot_angle(2, "attack", sh, False, 10, 2) == "wide"
assert ms.shot_angle(2, "hosts", sh, False, 10, 2) == "wide"
assert ms.shot_angle(0, "walk", sh, False, 10, 0) == "wide" and ms.shot_angle(9, "walk", sh, False, 10, 9) == "wide"
assert ms.shot_angle(3, "walk", {"insert": True}, False, 10, 3) == "wide"
got = [ms.shot_angle(j, "stalk", sh, False, 12, j) for j in range(1, 11)]
assert len(set(got)) >= 5 and all(a != b for a, b in zip(got, got[1:])), got
assert ms.shot_angle(1, "home", sh, False, 10, 1) in ("wide", "drift", "high", "dutch"), "close/low only on creature beats"

# 2. the kill close-up: right size every moment, wounds really appear, jaws really close
sp = toon.hybrid_spec_from(toon.design_hybrid("crocodile", "lion", seed=5))
blank = Image.new("RGB", (1080, 1920))
frames = {u: toon.maul_frame(blank, u * 2, u, sp, (201, 168, 120), "fur") for u in (0.1, 0.3, 0.5, 0.7, 0.9)}
assert all(f.size == (1080, 1920) for f in frames.values())
def red(im):
    px = im.resize((108, 192)).getdata()
    return sum(1 for r, g, b in px if r > 110 and g < 60 and b < 60)
assert red(frames[0.1]) < 40, "no blood before the strike"
assert red(frames[0.5]) > 100, "the wounds are open and bleeding mid-rake"
assert red(frames[0.7]) > 800, "the wounds have opened wide"
assert red(frames[0.9]) > 2500, "the bite is bleeding"
cloth = toon.maul_frame(blank, 1.0, 0.5, sp, (44, 50, 82), "cloth")
assert cloth.size == (1080, 1920) and red(cloth) > 60

# 3. sound for the kill lines up with the picture
sounds = {k: f"{k}.wav" for k in ("stab", "rip", "crunch", "growl")}
ev, quiet = ms.maul_sound_events(sounds, 10.0, 2.0)
stabs = [e for e in ev if e["file"] == "stab.wav"]
assert len(stabs) >= 5 and abs(stabs[0]["at"] - 10.6) < 0.01, "claws touch at 30% of the close-up"
assert any(e["file"] == "crunch.wav" and abs(e["at"] - 11.62) < 0.02 for e in ev), "the fangs sink in at 81%"
assert quiet[1] < 10.6 and quiet[0] < quiet[1], "a short silence just before the claws land"
assert "stab" in ms.MAJOR_SOUNDS and "crunch" in ms.MAJOR_SOUNDS

# 4. the opening hook
for t in (0.05, 0.15, 0.5, 1.0, 1.6):
    assert toon.hook_frame(t, (220, 190, 60)).size == (1080, 1920)
assert abs(toon.HOOK_LEN - 1.7) < 1e-6

# 5. the narrator is protected: the guard exists and its limits are sensible
assert ms.VOICE_MEDIAN_DB >= 10 and ms.VOICE_P10_DB >= 5
assert callable(ms.voice_dominance) and "loudnorm" in ms.voice_chain()
print("ALL FEATURE CHECKS PASSED")
