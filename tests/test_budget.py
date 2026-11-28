"""The whole run shares one clock: the build job has a time limit (it was cancelled at 40 minutes). Scenes are drawn hi-res while it fits,
the normal way when time runs short, and in an emergency mode (every second frame) when even that would not fit. The steps after the scenes
(final film pass, quality check, upload) are planned for, and the quality check's second render only happens if there is time."""
import os, sys, pathlib, time, tempfile, random
os.environ["DRY_RUN"] = "1"
HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import numpy as np
import toon
ok = True
def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name); ok &= bool(cond)
P = toon.RENDER_PLAN
toon.HR_SCALE = 2.0
toon.RENDER_BUDGET_S = 20 * 60
toon.RUN_END[0] = None
toon.POST_REF_S[0] = 0.0
def reset(total, **kw):
    P.update(total=total, hr_wall=0.0, hr_vid=0.0, lo_wall=0.0, lo_vid=0.0, fast_wall=0.0, fast_vid=0.0, done=0.0, mark=None)
    P.update(kw)

# ---- 1. the old behaviour (no run clock set): hi-res as long as the scenes' own budget fits
reset(55.0)
check("the first scene is hi-res on an unknown machine", toon._scene_mode(5.0) == "hr")
reset(55.0, hr_wall=80.0, hr_vid=10.0, done=10.0)
check("a fast machine keeps hi-res", toon._scene_mode(5.0) == "hr")
reset(55.0, hr_wall=600.0, hr_vid=10.0, done=10.0)
check("a very slow machine drops to the normal way", toon._scene_mode(5.0) == "lo")
reset(55.0, hr_wall=330.0, hr_vid=10.0, done=10.0)   # 33 s per second: all hi-res would take 30 min
early = toon._scene_mode(5.0)
reset(55.0, hr_wall=900.0, hr_vid=25.0, done=40.0, lo_wall=250.0, lo_vid=10.0)
late = toon._scene_mode(5.0)
check("a slowish machine is hi-res first and the normal way at the end", early == "hr" and late == "lo")
toon.HR_SCALE = 1.0
check("HR_SCALE=1 always means the normal way", toon._scene_mode(5.0) == "lo" and not toon._use_hr(5.0))
toon.HR_SCALE = 2.0

# ---- 2. the run clock: the scenes must end by RUN_END, taking a slower machine's longer final steps into account
toon.RENDER_BUDGET_S = 100 * 60            # (so that only the clock decides here)
now = time.time()
toon.POST_REF_S[0] = 8 * 60
toon.RUN_END[0] = now + 22 * 60           # 22 minutes left for the scenes
reset(55.0)
check("plenty of time: the first scene is hi-res", toon._scene_mode(5.0) == "hr")
toon.RUN_END[0] = now + 14 * 60
reset(55.0, hr_wall=320.0, hr_vid=10.0, done=10.0)        # the reference machine (32 s of work per video second, hi-res)
check("a reference-speed machine starts hi-res", toon._scene_mode(5.0) == "hr")
toon.RUN_END[0] = now + 7 * 60
reset(55.0, hr_wall=800.0, hr_vid=25.0, done=25.0)
check("...and switches to the normal way before it is too late", toon._scene_mode(5.0) == "lo")
toon.RUN_END[0] = now + 3 * 60
check("...and to the emergency mode when even the normal way would not fit", toon._scene_mode(5.0) == "fast")
# a slow machine also needs a longer final pass, so the scenes must end earlier
toon.RUN_END[0] = now + 25 * 60
reset(55.0, lo_wall=128.0, lo_vid=10.0, done=10.0)         # normal way 12.8 s/s: same as the reference machine
ref_mode = toon._scene_mode(5.0)
reset(55.0, lo_wall=300.0, lo_vid=10.0, done=10.0)         # 30 s/s: 2.3x slower than the reference: the final steps take 2.3x longer too
slow_mode = toon._scene_mode(5.0)
check("on the reference machine 25 minutes are plenty", ref_mode in ("hr", "lo"))
check("on a 2.3x slower machine the planned longer final steps force the emergency mode", slow_mode == "fast")
check("a 2.3x slower machine is measured as about 2.3", 2.2 < toon.speed_factor() < 2.5)
reset(55.0, lo_wall=900.0, lo_vid=10.0, done=10.0)
check("the factor is capped at 3", toon.speed_factor() == 3.0)
reset(55.0)
check("speed factor is 1 with no measurements", toon.speed_factor() == 1.0)
toon.RUN_END[0] = None
toon.POST_REF_S[0] = 0.0
toon.RENDER_BUDGET_S = 20 * 60

# ---- 3. emergency mode: same number of frames, motion at half rate, and the clock accounting works
TD = pathlib.Path(tempfile.mkdtemp())
sp = dict(toon.folk_creature("wolf", {"body": "beast", "palette": "ash", "eyes": "amber"}), veil=0.0)
toon.HR_SCALE = 1.0
reset(10.0)
got = {}
for label, end in (("normal", None), ("emergency", time.time() - 10.0)):      # an end in the past forces the emergency mode
    toon.RUN_END[0] = end
    toon._SINK[0] = []
    reset(10.0)
    random.seed(5); np.random.seed(5)
    toon.walk_scene(str(TD / "x.mp4"), 1.0, toon.World("a dark forest", 3), sp, cwd=TD)
    got[label] = (toon._SINK[0], dict(P))
    toon._SINK[0] = None
toon.RUN_END[0] = None
fn, fe = got["normal"][0], got["emergency"][0]
check(f"emergency mode makes the same number of frames ({len(fe)} vs {len(fn)})", len(fe) == len(fn))
check("emergency mode was really used", got["emergency"][1]["fast_vid"] > 0.9 and got["normal"][1]["lo_vid"] > 0.9)
pairs_equal = sum(1 for i in range(1, len(fe) - 1, 2) if np.array_equal(np.asarray(fe[i]), np.asarray(fe[i - 1])))
check("in emergency mode every second frame repeats the one before it", pairs_equal >= (len(fe) - 2) // 2 - 1)
check("the first and the last frame are real frames", not np.array_equal(np.asarray(fe[-1]), np.asarray(fe[-2])) or len(fe) % 2 == 1)
A = np.asarray(fn[-1].resize((360, 640)), np.float32); B = np.asarray(fe[-1].resize((360, 640)), np.float32)
check(f"the emergency picture looks like the normal one (mean difference {np.abs(A - B).mean():.2f})", np.abs(A - B).mean() < 6.0)
check("the scene's time was recorded", got["normal"][1]["done"] > 0.9 and got["normal"][1]["lo_wall"] > 0.0)

# ---- 4. make_short: the clock arms toon, the final steps are measured by a speed test, and the second render only happens if there is time
import make_short as ms
ms.RUN_LIMIT_MIN, ms.RUN_SETUP_MIN, ms.RUN_MARGIN_MIN = 40.0, 5.0, 3.0
r = ms.bench_final_pass()
check(f"the speed test of the final video pass runs ({r if r is None else round(r, 1)} s of work per video second)", r is not None and 0.5 < r < 80)
real_bench = ms.bench_final_pass
ms.bench_final_pass = lambda: 5.4                   # a machine like the test machine
ms.RUN_T0 = time.time() - 4 * 60
reset(55.0)
ms.arm_clock(55.0)
mins_to_end = (toon.RUN_END[0] - ms.RUN_T0) / 60.0
check(f"scenes must end well before the 40 minute limit (minute {mins_to_end:.1f})", 15.0 < mins_to_end < 24.0)
ms.bench_final_pass = lambda: 12.0                  # a machine 2x slower: the final steps need about twice as long, so the scenes must end sooner
ms.arm_clock(55.0)
slower_end = (toon.RUN_END[0] - ms.RUN_T0) / 60.0
check(f"a slower machine must end its scenes sooner (minute {slower_end:.1f} instead of {mins_to_end:.1f})", slower_end < mins_to_end - 5.0)
ms.bench_final_pass = lambda: None                  # no speed test possible: the standard estimate is used
ms.arm_clock(55.0)
check("without a speed test the standard estimate is used", toon.POST_REF_S[0] > 300 and toon.RUN_END[0] is not None)
ms.bench_final_pass = lambda: 5.4
ms.arm_clock(55.0)
ms.RUN_T0 = time.time() - 12 * 60
check("early in the run there is time for a second render", ms.time_for_second_pass())
ms.RUN_T0 = time.time() - 30 * 60
check("late in the run there is no time for a second render", not ms.time_for_second_pass())
os.environ["SCENES_END_MIN"] = "9"
ms.arm_clock(55.0)
check("SCENES_END_MIN overrides the plan", abs((toon.RUN_END[0] - ms.RUN_T0) / 60.0 - 9.0) < 0.01)
del os.environ["SCENES_END_MIN"]
ms.bench_final_pass = real_bench
toon.RUN_END[0] = None
toon.POST_REF_S[0] = 0.0

print("ALL BUDGET CHECKS PASSED" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
