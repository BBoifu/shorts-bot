"""Time of day: each episode is day, dawn, dusk or night, dealt like cards, the same in planning and building, shown in the sky and the light."""
import os, sys, pathlib, importlib.util
os.environ["DRY_RUN"] = "1"
HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import numpy as np
import toon
from PIL import Image
spec = importlib.util.spec_from_file_location("ms", HERE / "make_short.py"); ms = importlib.util.module_from_spec(spec); spec.loader.exec_module(ms)
ok = True
def check(n, c):
    global ok
    print(("PASS " if c else "FAIL ") + n); ok &= bool(c)

for series in ("folklore", "hybrid"):
    runs = [[ms.pick_tod(series, e) for e in range(r * 4 + 1, r * 4 + 5)] for r in range(6)]
    check(f"{series}: every block of four episodes has day, dawn, dusk and night once each", all(sorted(r) == sorted(ms.TODS) for r in runs))
    check(f"{series}: the order changes between blocks", len({tuple(r) for r in runs}) > 1)
check("the same series and episode always give the same time of day", all(ms.pick_tod("folklore", e) == ms.pick_tod("folklore", e) for e in range(1, 20)))
ms.set_scene_memory("folklore", 2); a = toon.TOD["name"]; ms.set_scene_memory("folklore", 2)
check("set_scene_memory sets the engine's time of day, and planning and building agree", toon.TOD["name"] == a == ms.pick_tod("folklore", 2))
for tod in ms.TODS:
    toon.TOD["name"] = tod
    tl = ms.time_line()
    check(f"the story prompt names the time of day ({tod})", tod.upper() in tl.upper() or (tod == "night" and "NIGHT" in tl))
toon.TOD["name"] = "day"
check("a daytime story is told not to mention night or the moon", "Never mention night" in ms.time_line())
check("daytime clock times are daytime", all(int(t.split(":")[0]) in (10, 11, 1, 2, 3) for t in ms.TOD_TIMES["day"]))
src = pathlib.Path(ms.__file__).read_text()
check("all four prompts carry the time-of-day line", src.count("{time_line()}") >= 4)
# the engine
st = {"sky": ("#05070f", "#10182c"), "sun": "#f6f1d6", "stars": True, "far": "#1a2238", "near": "#0f1524"}
toon.TOD["name"] = "day"; d = dict(st); toon.tod_apply(d)
check("by day: blue sky, no stars, a sun, daytime hills", d["sky"][0] == "#3f86d6" and d["stars"] is False and d["sun"] == "#fff1b0" and d["far"] != st["far"])
toon.TOD["name"] = "night"; d = dict(st); toon.tod_apply(d)
check("at night the place keeps its own look", d == st)
toon.TOD["name"] = "dusk"; d = dict(st); toon.tod_apply(d)
check("at dusk the sky is orange", d["sky"][1] == "#f28d50")
d = dict(st); toon.TOD["name"] = "day"; toon.tod_apply(d, "eclipse")
check("an eclipse sky is never overridden", d == st)
img = Image.new("RGB", (60, 60), (60, 60, 90))
means = {}
for tod in ms.TODS:
    toon.TOD["name"] = tod; means[tod] = np.asarray(toon.tod_grade(img), np.float32).mean((0, 1))
check("night is unchanged by the grade", np.allclose(means["night"], (60, 60, 90)))
check("day is brighter than night", means["day"].mean() > means["night"].mean() + 25)
check("dusk is warmer (more red than blue) than night", means["dusk"][0] - means["dusk"][2] > means["night"][0] - means["night"][2] + 15)
toon.TOD["name"] = "night"
w = toon.World("a misty Irish village at night", 3); toon.TOD["name"] = "day"; wd = toon.World("a misty Irish village at night", 3)
a_ = np.asarray(w.frame(0.0, 0.0).convert("RGB"), np.float32).mean(); b_ = np.asarray(wd.frame(0.0, 0.0).convert("RGB"), np.float32).mean()
check(f"the same place is much brighter by day ({a_:.0f} -> {b_:.0f})", b_ > a_ + 30)
toon.TOD["name"] = "night"
print("ALL TIME-OF-DAY CHECKS PASSED" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
