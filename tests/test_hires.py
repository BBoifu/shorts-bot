"""Checks for the double-size (hi-res) drawing: scenes are drawn with HR_SCALE times the pixels and shrunk to the video size.
It must look the same as the old way (only sharper), switch itself off cleanly, and fall back by itself if a scene cannot be drawn big."""
import os, sys, random, pathlib, tempfile
os.environ["DRY_RUN"] = "1"
import numpy as np
HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import toon
from PIL import Image, ImageDraw
ok = True
def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name); ok &= bool(cond)

TD = pathlib.Path(tempfile.mkdtemp())
sp = dict(toon.folk_creature("wolf", {"body": "beast", "palette": "ash", "eyes": "amber"}), veil=0.0)

# ---- 1. when it is off, PIL behaves exactly as before
toon.RSF[0] = 1.0
im = Image.new("RGB", (toon.W, toon.H), (10, 10, 10))
check("off: a 1080x1920 picture is still 1080x1920", im.size == (1080, 1920))
d = ImageDraw.Draw(im, "RGBA"); d.line((0, 5, 100, 5), fill=(255, 255, 255, 255))
check("off: drawing is unchanged (a 1-pixel line is 1 pixel)", np.asarray(im)[4:7, 50, 0].tolist() == [10, 255, 10])

# ---- 2. on: everything keeps its 1080-based numbers but gets more pixels
toon.RSF[0] = 2.0
big = Image.new("RGB", (toon.W, toon.H), (10, 10, 10))
check("on: the frame really has 2160x3840 pixels (and still reports 1080x1920 to the drawing code)", toon._psize(big) == (2160, 3840) and big.size == (1080, 1920))
d = ImageDraw.Draw(big, "RGBA"); d.line((0, 5, 100, 5), fill=(255, 255, 255, 255))
col = np.asarray(big)[:, 100, 0]
check("on: a 1-pixel line is 2 big pixels thick and in the same place", int((col > 200).sum()) == 2 and 9 <= int(np.argmax(col)) <= 11)
d.ellipse((100, 100, 200, 200), fill=(255, 0, 0, 255))
check("on: shapes land in the same place", np.asarray(big)[300, 300, 0] == 255 and np.asarray(big)[100, 100, 0] == 10)
small = Image.new("RGBA", (50, 50), (0, 255, 0, 255))
big.paste(small, (500, 500), small)
a = np.asarray(big)
check("on: an ordinary small picture pasted in is placed and scaled correctly", a[1010, 1010, 1] == 255 and a[1090, 1090, 1] == 255 and a[1110, 1110, 1] != 255)
toon.RSF[0] = 1.0
check("off again: a new picture is the old size", Image.new("RGB", (toon.W, toon.H)).size == (1080, 1920))

# ---- 3. a whole scene drawn big matches the old way and comes out at the video size
w1 = toon.World("a dark forest", 3)
frames = {}
for mode, hs in (("1x", 1.0), ("2x", 2.0)):
    toon.HR_SCALE = hs
    toon._SINK[0] = []
    random.seed(5); np.random.seed(5)
    toon.walk_scene(str(TD / "x.mp4"), 0.5, toon.World("a dark forest", 3), sp, cwd=TD)
    frames[mode] = toon._SINK[0]
    toon._SINK[0] = None
check("the big-drawn scene gives frames at the video size", all(f.size == (toon.OW, toon.OH) for f in frames["2x"]) and len(frames["2x"]) == len(frames["1x"]))
A = np.asarray(frames["1x"][-1].resize((360, 640), Image.BOX), np.float32)
B = np.asarray(frames["2x"][-1].resize((360, 640), Image.BOX), np.float32)
check(f"...and looks the same as the old way (mean difference {np.abs(A - B).mean():.2f})", np.abs(A - B).mean() < 3.0)
check("the hi-res switch is back off after the scene", toon.RSF[0] == 1.0)

# ---- 4. nothing gets blurrier: the finer detail (above the old 1080 grid) is at least as strong as in the enlarged old way
def fine_detail(im):
    g = im.convert("L")
    low = g.resize((1080, 1920), Image.BOX).resize(g.size, Image.BICUBIC)
    return float(np.abs(np.asarray(g, np.float32) - np.asarray(low, np.float32)).mean())
check(f"the hi-res frame has at least as much fine detail ({fine_detail(frames['2x'][-1]):.3f} vs {fine_detail(frames['1x'][-1]):.3f})",
      fine_detail(frames["2x"][-1]) >= 0.9 * fine_detail(frames["1x"][-1]))

# ---- 5. a scene that cannot be drawn big falls back to the old way by itself
toon.HR_SCALE = 2.0
state = {"n": 0}
def bad_painter(im, d, t, u, cam):
    if toon.RSF[0] != 1.0:
        raise ValueError("this painter cannot draw big")
w2 = toon.World("a dark forest", 3)
toon._SINK[0] = []
toon.write_video(str(TD / "y.mp4"), 0.3, bad_painter, w2, TD, 0)
got = toon._SINK[0]; toon._SINK[0] = None
check("a scene that fails big is drawn the old way instead (frames still made)", len(got) >= 8 and all(f.size == (toon.OW, toon.OH) for f in got))
check("...and the switch is back off", toon.RSF[0] == 1.0)
check("the world's own pictures are put back after a big scene", all(getattr(w2, n).size[0] <= toon.W * 2 + 10 for n in ("far", "ground")) and w2.sky.size == (toon.W, toon.H))

# ---- 6. every kind of creature comes out the same size and in the same place when drawn big (a body that came out twice as large once)
def silhouette_box(im):
    a = np.asarray(im.resize((1080, 1920), Image.BOX).convert("L"), np.int16)
    m = np.abs(a - 45) > 14
    ys, xs = np.where(m)
    return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())) if len(xs) else None
for body in ("beast", "chimera", "theropod", "orb", "wraith"):
    spc = dict(toon.folk_creature("wolf", {"body": body, "palette": "ash", "eyes": "amber"}), veil=0.0)
    boxes = {}
    for rs in (1.0, 2.0):
        toon.RSF[0] = rs
        pic = Image.new("RGB", (toon.W, toon.H), (45, 45, 45))
        dd = ImageDraw.Draw(pic, "RGBA")
        toon.draw_creature(dd, spc, 540, 1500, 0.5, 1, 1.0, {"t": 1.0, "moving": True})
        toon.RSF[0] = 1.0
        boxes[rs] = silhouette_box(pic)
    b1, b2 = boxes[1.0], boxes[2.0]
    check(f"a {spc['kind']} ({body}) is the same size and place drawn big: {b1} / {b2}",
          b1 is not None and b2 is not None and all(abs(p - q) <= 12 for p, q in zip(b1, b2)))
print("ALL HI-RES CHECKS PASSED" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
