"""Vista: the far-landmark zoom. Beat choice rules + a short real render of each landmark."""
import os, sys, pathlib, importlib.util, random, subprocess
os.environ["DRY_RUN"] = "1"
HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import toon
spec = importlib.util.spec_from_file_location("ms", HERE / "make_short.py"); ms = importlib.util.module_from_spec(spec); spec.loader.exec_module(ms)
ok = True
def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name); ok &= bool(cond)

S = lambda t, **k: dict(text=t, speaker="narrator", **k)
# --- folk: a sentence that names a waterfall gets the zoom; only one per story; never first or last
fs = [S("The old tales say a river spirit lived there."), S("Behind the waterfall was a cave nobody entered."),
      S("The hunters watched the waterfall for three nights."), S("Then something came out of the cave."),
      S("It walked down toward the village."), S("Nobody saw them again."), S("They say it still waits.")]
b = ms.assign_beats(fs)
check("folk: the sentence where it comes out of the cave is the vista", b[3] == "vista")
check("folk: a sentence that only names the waterfall is not", b[1] != "vista" and b[2] != "vista")
check("folk: only one vista per story", b.count("vista") == 1)
check("folk: never the first or last scene", b[0] != "vista" and b[-1] != "vista")
# --- modern
ms_ = [S("I went camping."), S("We found a ruined tower on the ridge."), S("The ruined tower had a light in it."), S("Something moved out of the cave below."),
       S("We ran."), S("It was behind us."), S("It caught us.")]
b2 = ms.assign_modern_beats(ms_, "woods")
check("modern: at most one vista", b2.count("vista") <= 1)
check("modern: not first or last", b2[0] != "vista" and b2[-1] != "vista")
check("vista is a known scene everywhere", all("vista" in t for t in (ms.SCENES, ms.SCENE_FUNCS, ms.MODERN_SCENES, ms.STORY_SCENES)) and "vista" in ms.SCENE_INFO)
check("vista is a hybrid action", "vista" in ms.HYB_ACTIONS_ENC)
check("the volcano story shows the creature coming down from the mountain", "vista" in ms.PRE_TYPES["volcano"][1])
# --- real render, short, one per landmark and one floating creature
HERE_T = pathlib.Path(os.environ.get("TMPDIR", "/tmp")) / "vista_test"; HERE_T.mkdir(exist_ok=True)
toon.HR_SCALE = 1.0
for kind, body in (("falls", "chimera"), ("peak", "theropod"), ("tower", "orb")):
    sp = dict(toon.folk_creature("wolf", {"body": body, "palette": "ash", "eyes": "amber"}), veil=0.0)
    w = toon.World("a dark forest", 3)
    out = str(HERE_T / f"{kind}.mp4")
    info = toon.vista_scene(out, 2.0, w, sp, kind=kind, seed=4, cwd=HERE_T, finale="maul")
    size = os.path.getsize(out) if os.path.exists(out) else 0
    check(f"{kind}/{body}: renders a video and reports its timing", size > 20000 and 0 < info["impact_at"] < 2.0 and info["hits"] == [info["impact_at"]])
w = toon.World("a dark forest", 3)
sp = dict(toon.folk_creature("wolf", {"body": "chimera", "palette": "ash", "eyes": "amber"}), veil=0.0)
info = toon.vista_scene(str(HERE_T / "face.mp4"), 1.5, w, sp, kinds=("falls", "peak"), finale="face", cwd=HERE_T)
check("the 'face' ending (no killing) also renders", os.path.getsize(HERE_T / "face.mp4") > 20000)
print("ALL VISTA CHECKS PASSED" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
