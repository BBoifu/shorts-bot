"""Scene memory: the bot remembers which scenes the latest videos used and prefers fresh ones where a sentence names no scene itself.
Scenes the sentence says (a window, a dig, a chase...) are never changed, so the pictures still match the words."""
import os, sys, json, pathlib, tempfile
os.environ["DRY_RUN"] = "1"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import make_short as ms
ok = True
def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name); ok &= bool(cond)
TD = pathlib.Path(tempfile.mkdtemp())
ms.HISTORY_FILE = TD / "history.json"

# ---- 1. the memory lines live in history.json without disturbing the story list
ms.HISTORY_FILE.write_text(json.dumps(["folk:a:b|x|y", "hyb:one", "folk:c:d|x|y"]))
h = ms.load_history()
check("old history loads unchanged", h == ["folk:a:b|x|y", "hyb:one", "folk:c:d|x|y"])
h.append("hyb:two")
ms.save_history(h, ms.scene_entry("hybrid", 2, ["face", "feed"]))
ms.save_history(h + ["folk:e:f|x|y"], ms.scene_entry("folklore", 3, ["walk", "window", "chase"]))
raw = json.loads(ms.HISTORY_FILE.read_text())
check("the story list and the turn counts do not change (4 stories + 1)", len(ms.load_history()) == 5 and not any(k.startswith("scenes|") for k in ms.load_history()))
check("both memory lines are kept", len(ms.load_scene_log()) == 2)
ms.save_history(ms.load_history(), ms.scene_entry("hybrid", 2, ["face", "turn"]))
log_ = ms.load_scene_log()
check("saving the same video again replaces its line", len(log_) == 2 and [e for e in log_ if e[0] == "hybrid"][0][2] == ["face", "turn"])
ms.save_history(ms.load_history())
check("saving without a new line keeps the old lines", len(ms.load_scene_log()) == 2)

# ---- 2. recency: the latest videos count, this video's own line does not
ms.HISTORY_FILE.write_text("[]")
for ep, bs in enumerate((["walk", "window", "dig"], ["walk", "door", "chase"], ["walk", "window", "pov"]), 1):
    ms.save_history([], ms.scene_entry("folklore", ep, bs))
ms.set_scene_memory("folklore", 4)
R = dict(ms.SCENE_RECENCY)
check("the most used scene counts most", R["walk"] > R["window"] > R["door"] > 0)
ms.set_scene_memory("folklore", 3)
check("this video's own line is left out", ms.SCENE_RECENCY.get("pov", 0) == 0 and ms.MIX["offset"] == 3)

# ---- 3. folklore beats
def S(*texts):
    return [{"text": t} for t in texts]
gen = S("The night was long.", "Nobody spoke of it.", "A cold wind came down.", "The dog would not leave the porch.", "They waited for the sun.",
        "The old woman lit a candle.", "Something moved behind the barn.", "She heard it breathe.", "The candle went out.", "Nothing was ever found.")
ms.SCENE_RECENCY.clear(); ms.MIX["offset"] = 0
plain = ms.assign_beats(gen, False, None)
check("no memory: the filler scene is the old plain walk", plain[0] == "walk" and plain[4] == "walk")
ms.set_scene_memory("folklore", 4)
mem = ms.assign_beats(gen, False, None)
check("with memory the filler scenes change", mem != plain)
check("the first scene is still a walk", mem[0] == "walk")
check("never the same scene twice in a row", all(mem[i] != mem[i - 1] for i in range(1, len(mem) - 1)))
check("the least used scenes are preferred (fewer plain walks)", mem.count("walk") < plain.count("walk"))
said = S("The night was long.", "Nobody spoke of it.", "A cold wind came down.", "She opened the window slowly.", "They waited for the sun.",
         "He dug a deep hole.", "Something moved behind the barn.", "They ran down the road.", "The candle went out.", "Nothing was ever found.")
b1 = ms.assign_beats(said, False, None)
ms.SCENE_RECENCY.clear(); ms.MIX["offset"] = 0
b0 = ms.assign_beats(said, False, None)
check("sentences that name a scene keep it, memory or not (window, dig, chase)", b1[3] == b0[3] == "window" and b1[5] == b0[5] == "dig" and b1[7] == b0[7] == "chase")
ms.set_scene_memory("folklore", 9)
big = ms.assign_beats(gen * 3, False, None)
check("a long story still ends on a stalk or aftermath", big[-1] in ("stalk", "aftermath"))
check("a long story uses several different scenes", len(set(big)) >= 4)

# ---- 4. modern: the filler rooms come in a different order after use
ms.SCENE_RECENCY.clear(); ms.MIX["offset"] = 0
m0 = ms.fresh_order(ms.MODERN_FILL)
check("no memory: the rooms keep their order", tuple(m0) == tuple(ms.MODERN_FILL))
ms.SCENE_RECENCY.update({"bedroom": 9, "hallway": 8, "cctv": 7})
m1 = ms.fresh_order(ms.MODERN_FILL)
check("used rooms move to the back", m1[-1] == "bedroom" and m1.index("hallway") > m1.index("window"))
mod = S("I moved in last spring.", "The house was quiet.", "I heard a sound.", "I checked the camera.", "Something stood there.", "I could not move.", "It looked at me.", "I ran.", "It followed.", "I never went back.")
bm = ms.assign_beats(mod, True, "home")
check("modern stories still end with the jump scare and start a valid scene", bm[-1] in ("jump", "giant") and bm[0] in ms.MODERN_SCENES)

# ---- 5. mixing rotates from video to video
ms.SCENE_RECENCY.clear()
ms.MIX["offset"] = 0; a0 = ms.shot_angle(0, "stalk", {}, False, 10, 3)
ms.MIX["offset"] = 1; a1 = ms.shot_angle(0, "stalk", {}, False, 10, 3)
check("the camera angles start differently in the next video", a0 != a1)
ms.MIX["offset"] = 0
k0 = ms.hybrid_kinds(); ms.MIX["offset"] = 2; k2 = ms.hybrid_kinds(); ms.MIX["offset"] = 0
check("the hybrid shot order rotates", k0 != k2 and sorted(k0) == sorted(k2))
ms.SCENE_RECENCY.update({"face": 9})
check("a recently used hybrid shot moves to the back", ms.hybrid_kinds()[-1] == "face")
ms.SCENE_RECENCY.clear()

# ---- 6. the story writer is told what was used lately
check("no memory: no hint", ms.variety_hint() == "")
ms.SCENE_RECENCY.update({"window": 9, "walk": 12, "door": 4})
h_ = ms.variety_hint()
check("the hint names used events but not filler scenes", "window" in h_ and "door" in h_ and "walk" not in h_ and "Never change the legend" in h_)
ms.SCENE_RECENCY.clear()

# ---- 7. plan-time entry
plan = {"story": {"sentences": gen, "format": "folk", "story_type": None}}
e = ms.plan_scene_entry(plan, "folklore", 5)
check("the plan records this video's scenes", e and e.startswith("scenes|folklore|5|walk"))
e2 = ms.plan_scene_entry({"story": {"sentences": [{"text": "x", "action": "feed"}, {"text": "y", "action": "face"}]}}, "hybrid", 5)
check("a hybrid plan records its shot kinds and actions", e2 and "feed" in e2 and "face" in e2)

# ---- 8. series memory: only every third video may glance back, never inside the legend
h8 = ["folk:wolf:The Briar Eye|beast|haunt|ash|County Clare, Ireland|x", "folk:cat:Night Cat|b|t|p|Kyoto, Japan|o"]
ms.set_continuity(h8, "folklore", 3)
check("every third video may glance back at an earlier story", "Briar Eye" in ms.variety_hint() and "Never change the legend" in ms.variety_hint())
ms.set_continuity(h8, "folklore", 4)
check("the other videos get no such line (never a formula)", "SERIES" not in ms.variety_hint())
ms.set_continuity([], "folklore", 3)
check("with no earlier story there is nothing to mention", ms.variety_hint() == "")
ms.CONTINUITY[0] = ""

# ---- 9. the person-walk scenes (toward you / away into mist / up a hill)
import numpy as np, toon, tempfile as _tf
for nm in ("walktoward", "walkaway", "walkclimb"):
    check(f"{nm} is a known folk scene", nm in ms.SCENE_FUNCS and nm in ms.SCENES and hasattr(toon, nm + "_scene"))
check("the person walks can be chosen as filler", {"walktoward", "walkaway", "walkclimb"} <= set(ms.GENERIC_SCENES))
ms.SCENE_RECENCY.clear(); ms.SCENE_RECENCY.update({"walk": 9, "stalk": 9, "pov": 9, "shoulder": 9, "loom": 9}); ms.MIX["offset"] = 1
fill = ms.assign_beats(gen, False, None)
check("when the usual filler scenes were used lately the person walks are chosen", any(b.startswith("walk") and b != "walk" for b in fill))
ms.SCENE_RECENCY.clear(); ms.MIX["offset"] = 0
toon.HR_SCALE = 1.0; toon.RUN_END[0] = None
TD2 = pathlib.Path(_tf.mkdtemp())
w_ = toon.World("a dark forest", 3)
for nm in ("walktoward", "walkaway", "walkclimb"):
    toon._SINK[0] = []
    r_ = getattr(toon, nm + "_scene")(str(TD2 / (nm + ".mp4")), 1.0, w_, {"kind": "x"}, cwd=TD2)
    fr = toon._SINK[0]; toon._SINK[0] = None
    a0 = np.asarray(fr[0].convert("RGB"), np.float32); a1 = np.asarray(fr[-1].convert("RGB"), np.float32)
    check(f"{nm} draws {len(fr)} frames that change over time and return hits", len(fr) >= 28 and np.abs(a0 - a1).mean() > 0.3 and r_ == {"hits": []})
print("ALL SCENE MEMORY CHECKS PASSED" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
