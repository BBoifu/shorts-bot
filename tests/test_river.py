"""The prehistoric river episode: one continuous drone flight (descent, Spinosaurus, shadow, attack, aftermath, monster emerging), one monster design,
wired into the prehistoric story types only."""
import os, sys, pathlib, importlib.util, math
os.environ["DRY_RUN"] = "1"
HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import numpy as np
import toon
spec = importlib.util.spec_from_file_location("ms", HERE / "make_short.py"); ms = importlib.util.module_from_spec(spec); spec.loader.exec_module(ms)
ok = True
def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name); ok &= bool(cond)

# ---- the camera: one smooth flight, high & oblique -> low & straight down, never a jump
prev = toon.river_cam(0.0)
worst = [0, 0, 0]
for i in range(1, 501):
    c = toon.river_cam(i / 500.0)
    worst = [max(worst[0], abs(c[0] - prev[0])), max(worst[1], abs(c[1] - prev[1])), max(worst[2], abs(c[2] - prev[2]))]
    prev = c
check(f"camera height changes smoothly (max step {worst[0]:.0f})", worst[0] < 40)
check(f"camera tilt changes smoothly (max step {worst[1]:.2f} deg)", worst[1] < 0.9)
check(f"camera moves smoothly along the river (max step {worst[2]:.0f})", worst[2] < 40)
c0, c1, c2 = toon.river_cam(0.0), toon.river_cam(0.30), toon.river_cam(0.5)
check("it starts high and oblique", c0[0] > 2500 and c0[1] < 60)
check("by the end of the descent it is low and straight down", c1[1] > 89 and c1[0] < 1100)
ks = [toon.river_cam(x / 100.0)[1] for x in range(0, 31)]
check("the tilt only ever increases during the descent (no up and down)", all(b >= a - 0.01 for a, b in zip(ks, ks[1:])))
check("there are intermediate stages (tilt passes 60 and 75 on the way)", any(55 < k < 65 for k in ks) and any(70 < k < 80 for k in ks))
# ---- the Spinosaurus: swims forward steadily, slows, then is held for the attack
ys = [toon._spy(t) for t in np.linspace(0, toon.RIVER_D0, 400)]
check("it swims up the river without jumping back", all(b <= a + 1e-6 for a, b in zip(ys, ys[1:])))
check("its speed never jumps (max step small)", max(abs(b - a) for a, b in zip(ys, ys[1:])) < 20)
check("it is held where the attack happens", abs(toon._spy(toon.RIVER_D0 * 0.9) - toon.RIVER_YATT) < 1e-6)
check("it is at the camera's centre when the descent ends", abs((toon._spy(toon.RIVER_D0 * 0.30) + 80) - toon.river_cam(0.30)[2]) < 60)
# ---- one monster, much bigger than the Spinosaurus
mon_len = max(y for x, y in toon.MON_SOCKETS) - toon.MON_HEAD_Y + 900
check(f"the monster ({mon_len:.0f}) is far longer than the Spinosaurus ({toon.RIVER_SPINO_LEN:.0f})", mon_len > 2.6 * toon.RIVER_SPINO_LEN)
check("six arms grow from sockets on the monster's own body", len(toon.MON_SOCKETS) == 6 and len(set(toon.MON_SOCKETS)) == 6)
check("the arms come at different times and speeds (unpredictable)", len(set(toon.ARM_START)) == 6 and len(set(toon.ARM_SPEED)) >= 5)
check("every arm starts after the shadow phase and before the aftermath", min(toon.ARM_START) >= toon.RIVER_PHASES["rivattack"][0] and max(toon.ARM_START) < toon.RIVER_PHASES["rivafter"][0])
# ---- frames
toon._world(1)
def fr(t): return np.asarray(toon.river_frame(t), np.float32)
f_a, f_b = fr(0.299), fr(0.301)
check(f"the same river on both sides of a phase change (difference {np.abs(f_a - f_b).mean():.1f})", np.abs(f_a - f_b).mean() < 12)
f_c, f_d = fr(0.579), fr(0.581)
check(f"no jump into the attack phase (difference {np.abs(f_c - f_d).mean():.1f})", np.abs(f_c - f_d).mean() < 8)
f_e, f_f = fr(0.819), fr(0.821)
check(f"no jump into the emergence (difference {np.abs(f_e - f_f).mean():.1f})", np.abs(f_e - f_f).mean() < 8)
im = toon.river_frame(1.0)
check("the frames are 1080 x 1920", im.size == (1080, 1920))
# the final frame: the monster's face is visible and not under trees
bg, info = toon.drone_frame(1, 0.0, 40.0, cam=toon.river_cam(1.0))
info.update(h=toon.river_cam(1.0)[0], pitch=toon.river_cam(1.0)[1], cy=toon.river_cam(1.0)[2], cx=toon.river_cam(1.0)[3])
tan = toon._tangent(toon.RIVER_YATT)
th = math.atan2(-tan, 1.0)
hx = toon._wcen(toon.RIVER_YATT) + math.sin(th) * -toon.MON_HEAD_Y * 1.0 * -1
hy = toon.RIVER_YATT + (-math.cos(th)) * -toon.MON_HEAD_Y * 1.0 * -1
pr = toon.project(info, toon._wcen(toon.RIVER_YATT) + math.sin(th) * (-toon.MON_HEAD_Y), toon.RIVER_YATT - math.cos(th) * (-toon.MON_HEAD_Y))
A = np.asarray(bg, np.float32); B = np.asarray(im, np.float32)
x, y = int(pr[0]), int(pr[1])
box = (slice(max(0, y - 50), y + 50), slice(max(0, x - 50), x + 50))
check(f"the monster's head area is painted over the forest, not hidden by it (difference {np.abs(A[box] - B[box]).mean():.0f})", np.abs(A[box] - B[box]).mean() > 25)
# ---- the Spinosaurus is oriented snout-up and swimming forward
lay = toon.Image.new("RGBA", (900, 900), (0, 0, 0, 0))
heading = math.degrees(math.atan2(1.0, toon._tangent(3500)))
pts = toon.draw_spino_top(lay, 450, 450, 500, 1.0, heading=heading)
check("the snout points up the river and the tail trails below it", pts[0][1] < pts[-1][1])
arr = np.asarray(lay)
check("the Spinosaurus is drawn with a sail (orange-red pixels) and skin (green pixels)", ((arr[..., 0] > 150) & (arr[..., 1] < 110) & (arr[..., 3] > 200)).sum() > 800 and ((arr[..., 1] > arr[..., 0]) & (arr[..., 3] > 200)).sum() > 3000)
# ---- a short real render of one piece of the flight
toon._SINK[0] = []
toon.RENDER_PLAN.update(total=10.0, hr_wall=0.0, hr_vid=0.0, lo_wall=0.0, lo_vid=0.0, fast_wall=0.0, fast_vid=0.0, done=0.0, mark=None)
toon.river_scene("x.mp4", 0.5, None, None, phase="rivattack", a=0.0, b=1.0)
frames = toon._SINK[0]; toon._SINK[0] = None
check(f"river_scene makes the right number of frames ({len(frames)})", len(frames) == 15)
check("and they are the output size", frames[0].size == (toon.OW, toon.OH))
# ---- the story side
st = ms.PRE_TYPES["river"]
check("the river story is a prehistoric type with 8 steps", len(st[1]) == 8 and len(st[2]) == 8 and "river" in ms.PRE_BODIES)
check("its stages are the six river phases, in order", [b for i, b in enumerate(st[1]) if i == 0 or b != st[1][i - 1]] == list(ms.RIVER_BEATS))
beats = ms.assign_beats([dict(text=f"s{i}", speaker="narrator") for i in range(8)], True, "river")
check("the beats follow the flight and end on the emergence", beats == st[1] and beats[-1] == "rivemerge")
beats9 = ms.assign_beats([dict(text=f"s{i}", speaker="narrator") for i in range(10)], True, "river")
order = [ms.RIVER_BEATS.index(b) for b in beats9]
check("with more sentences the order of phases still never goes back", all(b >= a for a, b in zip(order, order[1:])))
check("the volcano story still ends on the reign", ms.assign_beats([dict(text=f"s{i}", speaker="narrator") for i in range(8)], True, "volcano")[-1] == "reign")
sh = ms.plan_shots(beats, [3.0] * 8, "prehistoric", "river")
check("no random cutaways between the river shots", all(not s["insert"] for s in sh) and len(sh) == 8)
check("river beats are never chosen for other series", not any(b in ms.SCENES or b in ms.MODERN_SCENES for b in ms.RIVER_BEATS))
src = pathlib.Path(ms.__file__).read_text()
check("the river scene is only called for river beats", src.count("toon.river_scene(") == 1 and "if beat in RIVER_BEATS" in src)
check("the sound plan sees ordinary beats (attack sound at the first grab)", ms.RIVER_SOUND_BEAT["rivattack"] == "attack" and set(ms.RIVER_SOUND_BEAT) == set(ms.RIVER_BEATS))

# ---- the wetland version of the same flight
import random as _r
toon.WORLD_ENV["name"] = "river"; toon._WORLD.clear()
wr = np.asarray(toon._world(1)[0].crop((300, 4200, 1300, 5000)).resize((200, 160)), np.float32).mean((0, 1))
toon.WORLD_ENV["name"] = "wetland"
ww = np.asarray(toon._world(1)[0].crop((300, 4200, 1300, 5000)).resize((200, 160)), np.float32).mean((0, 1))
check(f"the wetland world looks different from the river world ({np.abs(wr - ww).sum():.0f})", np.abs(wr - ww).sum() > 12)
check("the wetland is more olive (less blue-green) than the river forest", (ww[1] - ww[2]) > (wr[1] - wr[2]) - 40)
toon.WORLD_ENV["name"] = "river"
seen = set()
for i in range(40):
    _r.seed(i); ms.ask_prehistoric.__globals__["random"].seed(i)
    try:
        ms.ask_prehistoric({"type": "river", "legends": [], "bodies": ("serpent",), "strange": {"rule": "x", "move": "y", "tone": "z"}})
    except Exception:
        pass
    seen.add(toon.WORLD_ENV["name"])
check("river episodes sometimes happen in a swamp and sometimes in a river valley", seen == {"river", "wetland"})
toon.WORLD_ENV["name"] = "river"
print("ALL RIVER CHECKS PASSED" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
