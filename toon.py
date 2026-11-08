"""
A tiny flat-style animation engine. Draws creatures that really walk, swim, fly and attack,
in front of a scrolling night/day scene, and writes the result as a video. Needs only Pillow + ffmpeg.
The look is deliberately stylized (flat shapes, rim light), not realistic.
"""
import math
import random
import subprocess
from PIL import Image, ImageDraw

W, H = 1080, 1920       # drawing size
OW, OH = 720, 1280      # size sent to ffmpeg (it scales up, which gives a soft film look)
GROUND = 1360
FPS = 30


# ---------------------------------------------------------------- colors
def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, k):
    a, b = rgb(a) if isinstance(a, str) else a, rgb(b) if isinstance(b, str) else b
    return tuple(int(a[i] * (1 - k) + b[i] * k) for i in range(3))


def dark(c, k=0.7):
    return mix(c, (0, 0, 0), 1 - k)


def light(c, k=0.45):
    return mix(c, (255, 255, 255), k)


# ---------------------------------------------------------------- animals
# kind, color, traits.  kinds: quad, bird, swim, float, snake, bug, crab
ANIMALS = {
    "camel": ("quad", "#c8a165", ["hump", "longneck"]),
    "octopus": ("float", "#b0455f", ["tentacles"]),
    "penguin": ("bird", "#2b3340", ["flipper", "walker"]),
    "axolotl": ("quad", "#e8a5b8", ["gills", "low"]),
    "bat": ("bird", "#3a3045", ["wings", "flying"]),
    "cheetah": ("quad", "#d9a441", ["spots", "tall"]),
    "crocodile": ("quad", "#5f7a4a", ["spikes", "longsnout", "low"]),
    "polar bear": ("quad", "#e8eef2", []),
    "jellyfish": ("float", "#9fd8ff", ["tentacles", "glow"]),
    "owl": ("bird", "#8b6b4a", ["wings", "beak", "walker"]),
    "chameleon": ("quad", "#6fb04e", ["curltail", "crest", "low"]),
    "elephant": ("quad", "#8d8d99", ["trunk", "bigears", "tusks"]),
    "shark": ("swim", "#6d8296", ["fins"]),
    "scorpion": ("bug", "#9a6b3c", ["stinger", "pincers"]),
    "frog": ("quad", "#5fae4e", ["bigeyes", "low"]),
    "eagle": ("bird", "#6b4a2e", ["wings", "beak", "flying"]),
    "wolf": ("quad", "#8a8f98", ["tall"]),
    "snake": ("snake", "#6f9f4a", []),
    "turtle": ("quad", "#6a8f52", ["shell", "low"]),
    "spider": ("bug", "#2e2733", ["eight"]),
    "whale": ("swim", "#4c6a86", ["fluke", "big"]),
    "gecko": ("quad", "#a3b56a", ["curltail", "low"]),
    "dragonfly": ("bird", "#4cc3c9", ["wings", "flying"]),
    "lion": ("quad", "#d1a050", ["mane"]),
    "gorilla": ("quad", "#3b3a40", ["bigarms"]),
    "fox": ("quad", "#d9743a", ["bushtail", "bigears", "tall"]),
    "seal": ("quad", "#7d8a94", ["low"]),
    "ant": ("bug", "#6b3b25", []),
    "crab": ("crab", "#d9553d", ["pincers", "shell"]),
    "hummingbird": ("bird", "#3fae7f", ["wings", "beak", "flying"]),
    "lizard": ("quad", "#8aa05a", ["low", "curltail"]),
    "bear": ("quad", "#5a4030", []),
}
WALKERS = ("quad", "bug", "crab", "bird", "snake")


def animal_spec(name):
    kind, col, traits = ANIMALS.get(name, ("quad", "#a08060", []))
    return {"name": name, "kind": kind, "color": rgb(col), "traits": set(traits), "scale": 1.0,
            "hybrid": False}


def hybrid_spec(a, b, seed=0):
    """An invented creature: the body of one parent with the signature features of the other."""
    A, B = animal_spec(a), animal_spec(b)
    base, other = (A, B) if (A["kind"] in WALKERS or B["kind"] not in WALKERS) else (B, A)
    if base["kind"] == "bird" and "flying" in base["traits"] and other["kind"] in WALKERS and other["kind"] != "bird":
        base, other = other, base  # a flying bird as a base makes a poor walker
    tr = set(base["traits"]) | set(other["traits"])
    if other["kind"] != base["kind"]:
        tr |= {"extra_" + other["kind"]}
    tr -= {"flying", "walker"}
    tr |= {"spikes", "fangs", "glow_eyes", "horns"}
    if base["kind"] == "quad":
        tr.discard("low")
        tr.add("tall")
    col = mix(mix(base["color"], other["color"], 0.45), (18, 16, 30), 0.55)
    return {"name": f"{a}-{b}", "kind": base["kind"], "color": col, "traits": tr,
            "scale": 1.0, "hybrid": True, "accent": other["color"]}


# ---------------------------------------------------------------- pen: draws in creature space
class Pen:
    def __init__(self, draw, cx, gy, scale, facing, rim):
        self.d, self.cx, self.gy, self.s, self.f, self.rim = draw, cx, gy, scale, facing, rim

    def X(self, x):
        return self.cx + self.f * self.s * x

    def Y(self, y):
        return self.gy + self.s * y

    def ell(self, x, y, rx, ry, fill, rim=True):
        x0, x1 = sorted((self.X(x - rx), self.X(x + rx)))
        self.d.ellipse((x0, self.Y(y - ry), x1, self.Y(y + ry)), fill=tuple(fill),
                       outline=self.rim(fill) if rim else None, width=max(2, int(4 * self.s)))

    def poly(self, pts, fill, rim=True):
        self.d.polygon([(self.X(x), self.Y(y)) for x, y in pts], fill=tuple(fill),
                       outline=self.rim(fill) if rim else None, width=max(2, int(3 * self.s)))

    def line(self, pts, w, fill):
        p = [(self.X(x), self.Y(y)) for x, y in pts]
        ww = max(2, int(w * self.s))
        self.d.line(p, fill=tuple(fill), width=ww, joint="curve")
        for q in (p[0], p[-1]):
            r = ww / 2
            self.d.ellipse((q[0] - r, q[1] - r, q[0] + r, q[1] + r), fill=tuple(fill))

    def dot(self, x, y, r, fill):
        self.ell(x, y, r, r, fill, rim=False)


def rimfn(c):
    return light(c, 0.35)


# ---------------------------------------------------------------- limbs and parts
def limb(pen, hx, hy, phase, l1, l2, w, col, amp=0.55, lift=0.7):
    a1 = math.sin(phase) * amp
    kx, ky = hx + math.sin(a1) * l1, hy + math.cos(a1) * l1
    a2 = a1 * 0.2 - lift * max(0.0, math.cos(phase))
    fx, fy = kx + math.sin(a2) * l2, ky + math.cos(a2) * l2
    pen.line([(hx, hy), (kx, ky), (fx, fy)], w, col)
    pen.ell(fx + 6, fy, w * 0.75, w * 0.4, dark(col, 0.8), rim=False)


def eye(pen, x, y, r, sp, st):
    if sp["hybrid"]:
        g = 1.0 + 0.15 * math.sin(st.get("t", 0) * 9)
        for k, a in ((3.2, 0.12), (2.2, 0.25), (1.5, 0.5)):
            pen.ell(x, y, r * k * g, r * k * g, mix((20, 14, 26), (255, 170, 30), a), rim=False)
        pen.dot(x, y, r * 1.1, (255, 190, 40))
        pen.ell(x + 1, y, r * 0.35, r * 0.8, (40, 10, 0), rim=False)
    else:
        pen.dot(x, y, r, (245, 245, 235))
        pen.dot(x + r * 0.25, y, r * 0.5, (20, 20, 25))


def mouth(pen, x, y, size, open_, sp):
    """A roaring mouth with teeth, drawn at the tip of the snout."""
    if open_ <= 0.02:
        if sp["hybrid"] or "fangs" in sp["traits"]:
            pen.poly([(x, y + 4), (x + size * 0.12, y + 4), (x + size * 0.06, y + size * 0.32)], (240, 235, 220), rim=False)
        return
    o = size * 0.9 * open_
    pen.poly([(x - size * 1.1, y), (x + size * 0.3, y - 4), (x + size * 0.3, y + o), (x - size * 1.0, y + o * 0.7)],
             (90, 10, 18), rim=False)
    n = 6
    for i in range(n):
        tx = x - size * 1.0 + i * (size * 1.25 / n)
        pen.poly([(tx, y), (tx + size * 0.11, y), (tx + size * 0.055, y + size * 0.3 * open_ + 6)], (245, 240, 225), rim=False)
        pen.poly([(tx, y + o * 0.8), (tx + size * 0.11, y + o * 0.8), (tx + size * 0.055, y + o * 0.8 - size * 0.24 * open_)],
                 (245, 240, 225), rim=False)


def traits_on_back(pen, sp, bx, by, bl, bh, ph, st):
    tr, col = sp["traits"], sp["color"]
    dk = dark(col, 0.75)
    if "hump" in tr:
        pen.ell(bx - bl * 0.05, by - bh * 0.62, bl * 0.2, bh * 0.38, col)
    if "shell" in tr:
        pen.ell(bx, by - bh * 0.18, bl * 0.58, bh * 0.75, mix(col, (40, 50, 30), 0.35))
        for i in (-1, 0, 1):
            pen.ell(bx + i * bl * 0.25, by - bh * 0.5, bl * 0.12, bh * 0.2, dark(col, 0.65), rim=False)
    if "spikes" in tr:
        n = 7
        for i in range(n):
            x = bx - bl * 0.45 + i * bl * 0.9 / (n - 1)
            h = bh * (0.35 + 0.15 * math.sin(i * 1.7))
            pen.poly([(x - 14, by - bh * 0.42), (x + 14, by - bh * 0.42), (x + 4, by - bh * 0.42 - h)], light(col, 0.15))
    if "fins" in tr:
        pen.poly([(bx - 20, by - bh * 0.4), (bx + 70, by - bh * 0.4), (bx - 10, by - bh * 1.0)], dk)
    if "mane" in tr:
        for i in range(9):
            a = -0.2 + i * 0.5
            pen.ell(bx + bl * 0.38 + math.cos(a) * 55, by - bh * 0.1 + math.sin(a) * 55, 40, 40, mix(col, (80, 40, 10), 0.45))
    if "extra_float" in tr or "tentacles" in tr:
        for i in range(5):
            x0 = bx - bl * 0.35 + i * bl * 0.17
            pts = []
            for j in range(7):
                u = j / 6
                pts.append((x0 - u * 70 + 18 * math.sin(ph * 1.5 + i + u * 5),
                            by - bh * 0.35 - u * 160 + 14 * math.cos(ph * 1.3 + i * 2 + u * 4)))
            pen.line(pts, 20 - 2 * i, mix(col, sp.get("accent", col), 0.4))
    if "wings" in tr or "extra_bird" in tr:
        flap = math.sin(ph * 2.2)
        for k, sh in ((0, 0.8), (1, 1.0)):
            ex, ey = bx - bl * 0.1 - k * 25, by - bh * 0.45
            tipx, tipy = ex - 150 - k * 30, ey - 70 - 150 * flap
            pen.poly([(ex, ey), (ex - 80, ey - 10), (tipx, tipy), (ex - 30 - k * 20, ey - 140 * flap - 20)],
                     mix(col, (10, 10, 20), 0.35 + 0.15 * k))
    if "stinger" in tr or "extra_bug" in tr:
        pts = [(bx - bl * 0.45, by - bh * 0.1), (bx - bl * 0.8, by - bh * 0.6), (bx - bl * 0.8, by - bh * 1.3),
               (bx - bl * 0.45, by - bh * 1.6)]
        pen.line(pts, 24, dk)
        pen.poly([(bx - bl * 0.45, by - bh * 1.6), (bx - bl * 0.3, by - bh * 1.5), (bx - bl * 0.32, by - bh * 1.2)], (230, 220, 200))
    if "extra_crab" in tr or "pincers" in tr:
        for k in (0, 1):
            ax = bx + bl * 0.55 + k * 20
            ay = by + bh * 0.1 - k * 25
            pen.line([(bx + bl * 0.3, by), (ax, ay), (ax + 60, ay - 40 + 10 * math.sin(ph * 2 + k))], 20, dk)
            pen.poly([(ax + 60, ay - 40), (ax + 120, ay - 70), (ax + 95, ay - 25)], mix(col, (200, 60, 40), 0.5))
            pen.poly([(ax + 60, ay - 40), (ax + 120, ay - 20), (ax + 85, ay - 5)], mix(col, (200, 60, 40), 0.35))


def tail(pen, sp, bx, by, bl, bh, ph):
    tr, col = sp["traits"], sp["color"]
    x0, y0 = bx - bl * 0.48, by - bh * 0.1
    if "bushtail" in tr:
        pen.ell(x0 - 70, y0 - 15 + 10 * math.sin(ph), 80, 32, col)
    elif "curltail" in tr:
        pts = [(x0 + k * -26, y0 + 18 * math.sin(ph * 0.8 + k) - k * 6 * (1 if k < 3 else -2)) for k in range(7)]
        pen.line(pts, 18, col)
    else:
        L = 120 if "longtail" in tr else 80
        pts = [(x0 - u * L, y0 + 20 * math.sin(ph * 0.9 + u * 3) * u) for u in (0, 0.5, 1)]
        pen.line(pts, 24, col)


# ---------------------------------------------------------------- creature kinds
def draw_quad(pen, sp, ph, st):
    tr, col = sp["traits"], sp["color"]
    crouch, roar = st.get("crouch", 0), st.get("roar", 0)
    moving = st.get("moving", True)
    legH = 80 if "low" in tr else (200 if "tall" in tr else 150)
    if "bigarms" in tr:
        legH = 140
    bl = 330 if "low" not in tr else 360
    bh = 170 if "shell" not in tr else 150
    if "bigarms" in tr:
        bh = 200
    legH *= (1 - 0.5 * crouch)
    bob = -abs(math.sin(ph)) * 9 if moving else math.sin(ph * 0.4) * 3
    bx, by = 0, -legH - bh / 2 + bob
    amp = 0.55 if moving else 0.04
    dk = dark(col, 0.72)
    hy_ = by + bh * 0.28
    hipf, hipr = bl * 0.3, -bl * 0.3
    L1 = max(30, (legH - 4) * 0.5 + bh * 0.1)
    # far legs, tail, body, near legs
    limb(pen, hipf, hy_, ph + math.pi, L1, L1 + 10, 34, dk, amp)
    limb(pen, hipr, hy_, ph, L1, L1 + 10, 34, dk, amp)
    tail(pen, sp, bx, by, bl, bh, ph)
    pen.ell(bx, by, bl / 2, bh / 2, col)
    if "spots" in tr:
        for i in range(8):
            pen.dot(bx - bl * 0.35 + (i % 4) * bl * 0.22, by - bh * 0.15 + (i // 4) * bh * 0.4, 10, dark(col, 0.45))
    if "bigarms" in tr:
        pen.ell(bx + bl * 0.3, by + bh * 0.2, 55, 90, dark(col, 0.85))
    limb(pen, hipf, hy_, ph, L1, L1 + 10, 38, col, amp)
    limb(pen, hipr, hy_, ph + math.pi, L1, L1 + 10, 38, col, amp)
    traits_on_back(pen, sp, bx, by, bl, bh, ph, st)
    # neck and head
    longneck = "longneck" in tr
    hr = 62 * (1.1 if sp["hybrid"] else 1.0)
    nx, ny = bl * 0.36, by - bh * 0.25
    hx, hy = bl * 0.5 + hr * 0.6, by - bh * 0.2 - (170 if longneck else 20) - st.get("headup", 0) * 40 + roar * -20
    if sp["hybrid"] and not longneck:
        hy -= 10
    pen.line([(nx, ny), ((nx + hx) / 2 - 10, (ny + hy) / 2), (hx, hy)], 74 if not longneck else 56, col)
    if "bigears" in tr:
        pen.ell(hx - hr * 0.5, hy - hr * 0.95, hr * 0.4, hr * 0.9, col)
    else:
        pen.poly([(hx - hr * 0.5, hy - hr * 0.7), (hx - hr * 0.1, hy - hr * 1.35), (hx + hr * 0.1, hy - hr * 0.7)], col)
    if "horns" in tr:
        pen.poly([(hx - hr * 0.1, hy - hr * 0.7), (hx + hr * 0.1, hy - hr * 2.0), (hx + hr * 0.45, hy - hr * 0.75)], (225, 215, 190))
        pen.poly([(hx - hr * 0.7, hy - hr * 0.55), (hx - hr * 0.8, hy - hr * 1.7), (hx - hr * 0.3, hy - hr * 0.7)], (205, 195, 170))
    pen.ell(hx, hy, hr, hr * 0.85, col)
    sl = 1.9 if "longsnout" in tr else 1.0
    pen.ell(hx + hr * 0.85, hy + hr * 0.25, hr * 0.75 * sl, hr * 0.42, mix(col, (255, 255, 255), 0.08))
    if "trunk" in tr:
        pts = [(hx + hr * 0.8, hy + hr * 0.3 + k * 28, ) for k in range(6)]
        pts = [(x + 24 * math.sin(ph * 0.8 + k * 0.6) + k * 5, y) for k, (x, y) in enumerate(pts)]
        pen.line(pts, 40, col)
    if "tusks" in tr:
        pen.poly([(hx + hr * 0.6, hy + hr * 0.6), (hx + hr * 1.3, hy + hr * 1.1), (hx + hr * 0.5, hy + hr * 0.85)], (240, 235, 215))
    if "gills" in tr or "crest" in tr:
        for i in range(3):
            pen.line([(hx - hr * 0.55, hy - hr * 0.2 + i * 18), (hx - hr * 1.2, hy - hr * 0.5 + i * 24)], 10,
                     mix(col, (255, 120, 140), 0.6))
    eye(pen, hx + hr * 0.3, hy - hr * 0.2, hr * (0.28 if "bigeyes" not in tr else 0.4), sp, st)
    mouth(pen, hx + hr * 0.9 + (hr * 0.5 if "longsnout" in tr else 0), hy + hr * 0.4, hr * 0.9, roar, sp)
    if "extra_float" in tr and not sp["hybrid"]:
        pass


def draw_bird(pen, sp, ph, st):
    tr, col = sp["traits"], sp["color"]
    flying = "flying" in tr
    flap = math.sin(ph * (3.0 if flying else 1.0))
    y0 = -230 - 40 * math.sin(ph * 0.7) if flying else -150
    bob = 0 if flying else -abs(math.sin(ph)) * 8
    bx, by = 0, y0 + bob
    if not flying:
        for k in (0, 1):
            limb(pen, -10 + k * 30, by + 70, ph + k * math.pi, 50, 50, 16, mix(col, (230, 170, 50), 0.6), 0.5)
    pen.ell(bx - 60, by + 10, 110, 40, dark(col, 0.8))
    pen.ell(bx, by, 95, 100 if not flying else 60, col)
    if "walker" in tr:
        pen.ell(bx + 15, by + 15, 55, 80, (235, 235, 235))
    hx, hy = bx + 100, by - 85 if not flying else by - 25
    pen.ell(hx, hy, 55, 52, col)
    if "beak" in tr or "flying" in tr:
        pen.poly([(hx + 40, hy - 8), (hx + 120, hy + 12), (hx + 40, hy + 24)], (235, 190, 60))
    elif "walker" in tr:
        pen.poly([(hx + 40, hy), (hx + 90, hy + 14), (hx + 40, hy + 26)], (235, 150, 50))
    eye(pen, hx + 22, hy - 10, 11, sp, st)
    if "wings" in tr or "flying" in tr:
        for k, sh in ((0, 0.7), (1, 1.0)):
            ex, ey = bx - 10 - k * 30, by - 20
            pen.poly([(ex, ey), (ex - 120, ey - 20 - 120 * flap * sh), (ex - 230, ey + 30 - 150 * flap * sh),
                      (ex - 90, ey + 60)], mix(col, (0, 0, 0), 0.2 + 0.2 * k))
    elif "flipper" in tr:
        pen.poly([(bx - 10, by - 10), (bx - 90, by + 50 + 20 * flap), (bx - 20, by + 60)], dark(col, 0.8))
    traits_on_back(pen, {**sp, "traits": sp["traits"] - {"wings"}}, bx, by, 190, 100, ph, st)
    if sp["hybrid"]:
        mouth(pen, hx + 70, hy + 20, 60, st.get("roar", 0), sp)


def draw_swim(pen, sp, ph, st):
    tr, col = sp["traits"], sp["color"]
    big = 1.4 if "big" in tr else 1.0
    y0 = -330 + 25 * math.sin(ph * 0.5)
    L = 420 * big
    pts_top, pts_bot = [], []
    n = 12
    for i in range(n + 1):
        u = i / n
        x = L / 2 - u * L
        wave = math.sin(ph * 1.6 - u * 5) * (10 + 45 * u)
        th = 95 * big * math.sin(math.pi * min(1, u * 0.9 + 0.1)) ** 0.7 * (1 - 0.75 * u * u) + 6
        pts_top.append((x, y0 + wave - th))
        pts_bot.append((x, y0 + wave + th * 0.8))
    tailx, taily = pts_top[-1][0], (pts_top[-1][1] + pts_bot[-1][1]) / 2
    fl = 90 * big
    pen.poly([(tailx + 10, taily), (tailx - fl * 0.9, taily - fl * 1.1 * (1 if "fluke" not in tr else 0.4)),
              (tailx - fl * 0.3, taily), (tailx - fl * 0.9, taily + fl * 1.0 * (1 if "fluke" not in tr else 0.4))], dark(col, 0.8))
    pen.poly(pts_top + pts_bot[::-1], col)
    belly = mix(col, (255, 255, 255), 0.35)
    pen.poly(pts_bot[:-3] + [(p[0], p[1] - 22) for p in pts_bot[:-3][::-1]], belly, rim=False)
    if "fins" in tr:
        mx = pts_top[4]
        pen.poly([(mx[0] + 30, mx[1] + 6), (mx[0] - 70, mx[1] + 6), (mx[0] - 10, mx[1] - 110)], dark(col, 0.85))
    pen.poly([(-40, y0 + 70), (-130, y0 + 150 + 20 * math.sin(ph * 2)), (-10, y0 + 85)], dark(col, 0.85))
    hx = L / 2 - 50
    eye(pen, hx, y0 - 20, 13, sp, st)
    mouth(pen, L / 2 - 10, y0 + 28, 90, st.get("roar", 0), sp)
    traits_on_back(pen, {**sp, "traits": sp["traits"] - {"fins", "fluke"}}, 0, y0, L * 0.7, 150, ph, st)


def draw_float(pen, sp, ph, st):
    tr, col = sp["traits"], sp["color"]
    pulse = math.sin(ph * 1.2)
    y0 = -330 + 30 * math.sin(ph * 0.6)
    ry = 120 + 14 * pulse
    rx = 135 - 8 * pulse
    for i in range(8):
        x0 = -rx * 0.8 + i * rx * 1.6 / 7
        pts = []
        for j in range(9):
            u = j / 8
            pts.append((x0 + 26 * math.sin(ph * 1.4 + i * 0.8 + u * 5) * u + u * 40,
                        y0 + ry * 0.6 + u * (230 + 20 * (i % 2))))
        pen.line(pts, max(8, 24 - 2 * i % 6), mix(col, (255, 255, 255), 0.1 + 0.05 * (i % 3)))
    pen.ell(0, y0, rx, ry, col)
    pen.ell(0, y0 + ry * 0.35, rx * 0.95, ry * 0.45, mix(col, (255, 255, 255), 0.2), rim=False)
    eye(pen, 30, y0 - 10, 18, sp, st)
    eye(pen, -45, y0 - 10, 18, sp, st)
    if sp["hybrid"]:
        mouth(pen, 55, y0 + 60, 90, st.get("roar", 0), sp)
    traits_on_back(pen, {**sp, "traits": sp["traits"] - {"tentacles"}}, 0, y0 + 40, 280, 200, ph, st)


def draw_snake(pen, sp, ph, st):
    col = sp["color"]
    pts = []
    n = 16
    for i in range(n + 1):
        u = i / n
        pts.append((190 - u * 420, -40 + 38 * math.sin(ph * 1.5 - u * 9)))
    pen.line(pts, 58, col)
    pen.line(pts[:-2], 22, mix(col, (255, 255, 255), 0.3))
    hx, hy = pts[0]
    pen.ell(hx + 20, hy - 20 - st.get("headup", 0) * 40, 62, 46, col)
    eye(pen, hx + 35, hy - 32, 11, sp, st)
    pen.line([(hx + 70, hy - 18), (hx + 105, hy - 12), (hx + 125, hy - 25)], 6, (200, 40, 60))
    mouth(pen, hx + 70, hy - 5, 70, st.get("roar", 0), sp)


def draw_bug(pen, sp, ph, st):
    tr, col = sp["traits"], sp["color"]
    nl = 4 if "eight" in tr else 3
    y0 = -90
    for side in (0, 1):
        for i in range(nl):
            x = -60 + i * 55 * (1.3 if nl == 3 else 1.0)
            c = dark(col, 0.65 if side == 0 else 1.0)
            hy_ = y0 + 10
            for sgn in (1,):
                p = ph + i * 1.2 + side * math.pi
                a = math.sin(p) * 0.5
                kx, ky = x + 60 + math.sin(a) * 40, hy_ - 70 + 6 * abs(math.cos(p))
                pen.line([(x, hy_), (kx, ky), (kx + 30 + math.sin(a) * 30, 0)], 12, c)
    sx = 1.0
    pen.ell(0, y0, 130 * sx, 70, col)
    pen.ell(-150, y0 + 5, 90, 58, dark(col, 0.85)) if "eight" in tr else pen.ell(-120, y0, 80, 50, dark(col, 0.9))
    pen.ell(130, y0 - 10, 55, 48, col)
    for k in range(4 if "eight" in tr else 2):
        pen.dot(150 + k * 8 - (k // 2) * 14, y0 - 22 + (k % 2) * 16 - (k // 2) * 0, 9, (255, 40, 40) if sp["hybrid"] else (240, 240, 230))
    traits_on_back(pen, sp, 0, y0, 260, 130, ph, st)
    mouth(pen, 170, y0 + 8, 60, st.get("roar", 0), sp)


def draw_crab(pen, sp, ph, st):
    col = sp["color"]
    y0 = -100
    for side in (-1, 1):
        for i in range(3):
            x = -70 + i * 55
            p = ph * 1.3 + i * 1.3 + (0 if side > 0 else math.pi)
            kx = x + side * 70 + 12 * math.sin(p)
            pen.line([(x, y0 + 20), (kx, y0 - 60 + 8 * math.cos(p)), (kx + side * 25, 0)], 12, dark(col, 0.7))
    pen.ell(0, y0, 150, 75, col)
    pen.ell(0, y0 - 25, 120, 40, mix(col, (255, 255, 255), 0.1), rim=False)
    for k in (-1, 1):
        ax = k * 190
        pen.line([(k * 100, y0 - 10), (ax, y0 - 90 + 12 * math.sin(ph * 2 + k)), (ax + k * 20, y0 - 150)], 18, dark(col, 0.75))
        pen.poly([(ax + k * 20, y0 - 150), (ax + k * 80, y0 - 190), (ax + k * 70, y0 - 120)], col)
        pen.poly([(ax + k * 20, y0 - 150), (ax + k * 85, y0 - 130), (ax + k * 40, y0 - 100)], mix(col, (160, 30, 30), 0.4))
    pen.line([(30, y0 - 70), (35, y0 - 110)], 8, col)
    pen.line([(-30, y0 - 70), (-35, y0 - 110)], 8, col)
    eye(pen, 35, y0 - 112, 12, sp, st)
    eye(pen, -35, y0 - 112, 12, sp, st)
    traits_on_back(pen, {**sp, "traits": sp["traits"] - {"pincers", "extra_crab"}}, 0, y0, 300, 150, ph, st)
    mouth(pen, 60, y0 + 15, 70, st.get("roar", 0), sp)


KINDS = {"quad": draw_quad, "bird": draw_bird, "swim": draw_swim, "float": draw_float,
         "snake": draw_snake, "bug": draw_bug, "crab": draw_crab}


def draw_creature(img_draw, sp, cx, gy, phase, facing=1, scale=None, st=None):
    st = st or {}
    s = 1.4 * (scale if scale is not None else 1.0) * sp.get("scale", 1.0) * (0.78 if sp["kind"] in ("swim", "float") else 1.0)
    pen = Pen(img_draw, cx, gy, s, facing, rimfn)
    if sp["kind"] not in ("swim", "float") and sp["kind"] != "bird" or (sp["kind"] == "bird" and "flying" not in sp["traits"]):
        shadow(img_draw, cx, gy, 150 * s)
    KINDS[sp["kind"]](pen, sp, phase, st)


def shadow(d, cx, gy, w):
    d.ellipse((cx - w, gy - 14, cx + w, gy + 18), fill=(8, 8, 14))


def draw_prey(d, cx, gy, phase, facing, scale=0.5):
    sp = {"name": "prey", "kind": "quad", "color": (200, 170, 130), "traits": {"bigears", "low"},
          "scale": 1.0, "hybrid": False}
    draw_creature(d, sp, cx, gy, phase * 1.8, facing, scale, {"moving": True})


# ---------------------------------------------------------------- scenes
PLACES = {
    "the Sahara desert": dict(sky=("#f2b46d", "#8a3d2c"), far="#b5623c", near="#6e3426", ground="#4a2419", sil="dunes", sun="#ffe0a3", part="dust"),
    "Antarctica": dict(sky=("#b5cfe6", "#4a6682"), far="#8aa5bd", near="#547089", ground="#e3edf5", sil="spikes", sun="#ffffff", part="snow"),
    "the deep ocean": dict(sky=("#0d4468", "#02121f"), far="#0e3550", near="#071e30", ground="#06141f", sil="rocks", sun=None, part="bubbles"),
    "a land of active volcanoes": dict(sky=("#3a0d0d", "#e0602a"), far="#2c0d0b", near="#170706", ground="#0e0404", sil="cones", sun="#ffb060", part="embers"),
    "Mars": dict(sky=("#e09a78", "#5e2c24"), far="#93492f", near="#5e2c20", ground="#41201a", sil="rocks", sun="#ffd9c0", part="dust"),
    "the clouds of Venus": dict(sky=("#e8ab55", "#6b3a1a"), far="#b8702f", near="#7a3f17", ground="#4c2610", sil="clouds", sun="#fff0c0", part="dust"),
    "an icy moon like Europa": dict(sky=("#05060f", "#1d2e50"), far="#3a5780", near="#243a5c", ground="#afc9e6", sil="spikes", sun="planet", part="snow"),
    "a rainforest after a flood": dict(sky=("#5a8269", "#1f382c"), far="#33594a", near="#17331f", ground="#0f2018", sil="trees", sun=None, part="rain"),
    "a mountain peak above the clouds": dict(sky=("#86afe0", "#eaf0f8"), far="#a3b8d2", near="#6d819e", ground="#f4f7fb", sil="peaks", sun="#ffffff", part="snow"),
    "a planet with two suns": dict(sky=("#6e2c80", "#ee9a55"), far="#7e3062", near="#471c3e", ground="#2a1226", sil="rocks", sun="two", part="dust"),
    "a swamp full of toxic mud": dict(sky=("#2f3e25", "#101c0f"), far="#213623", near="#122414", ground="#1c2c14", sil="reeds", sun="#d8e8b0", part="fireflies"),
    "the edge of a black hole": dict(sky=("#000000", "#180b2a"), far="#1c1036", near="#0c071a", ground="#0b0816", sil="rocks", sun="hole", part="stars"),
}
DEFAULT_PLACE = dict(sky=("#1a2a44", "#0a0f1c"), far="#1f3050", near="#101a30", ground="#0a1020", sil="dunes", sun="#e8e8f0", part="dust")


def _hills(rnd, width, base, amp, period_w):
    ks = [(rnd.choice([1, 2, 3, 5]), rnd.uniform(0, 6.28), rnd.uniform(0.4, 1.0)) for _ in range(3)]
    pts = []
    for x in range(0, width + 1, 12):
        y = base + sum(a * amp * math.sin(2 * math.pi * k * x / period_w + ph) for k, ph, a in ks)
        pts.append((x, y))
    return pts


class World:
    PW = 2160  # the repeating width of the scrolling layers

    def __init__(self, place, seed=0):
        self.st = PLACES.get(place, DEFAULT_PLACE)
        self.rnd = random.Random(seed * 7919 + len(place))
        st, rnd = self.st, self.rnd
        # sky
        sky = Image.new("RGB", (W, H))
        d = ImageDraw.Draw(sky)
        for y in range(H):
            d.line((0, y, W, y), fill=mix(st["sky"][0], st["sky"][1], min(1, y / (GROUND + 80))))
        sx = rnd.randint(620, 880)
        sun = st["sun"]
        if sun == "two":
            for cx, cy, r, c in ((300, 560, 130, "#ffe9b0"), (760, 420, 80, "#ffb38a")):
                for k, a in ((2.4, 0.1), (1.7, 0.2)):
                    d.ellipse((cx - r * k, cy - r * k, cx + r * k, cy + r * k), fill=mix(st["sky"][0], c, a))
                d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=rgb(c))
        elif sun == "planet":
            d.ellipse((520, 180, 1020, 680), fill=rgb("#c9a27a"))
            for i in range(6):
                d.arc((520, 180 + i * 40, 1020, 680 - i * 40), 200, 340, fill=rgb("#a9825e"), width=10)
        elif sun == "hole":
            for k in range(14, 0, -1):
                d.ellipse((sx - k * 34, 520 - k * 12, sx + k * 34, 520 + k * 12), fill=mix("#000000", "#ff9a3c", 0.04 * (15 - k)))
            d.ellipse((sx - 120, 400, sx + 120, 640), fill=(0, 0, 0))
        elif sun:
            c = sun
            for k, a in ((3.0, 0.08), (2.2, 0.14), (1.5, 0.25)):
                r = 80 * k
                d.ellipse((sx - r, 520 - r, sx + r, 520 + r), fill=mix(st["sky"][0], c, a))
            d.ellipse((sx - 85, 435, sx + 85, 605), fill=rgb(c))
        stars = st["part"] in ("stars", "snow") or st["sky"][0] in ("#05060f", "#000000", "#0d4468")
        if st["sky"][0] in ("#05060f", "#000000", "#2f3e25", "#3a0d0d"):
            for _ in range(160):
                x, y = rnd.randint(0, W), rnd.randint(0, 900)
                d.point((x, y), fill=(230, 230, 255))
                if rnd.random() < 0.1:
                    d.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(255, 255, 255))
        self.sky = sky
        # layers
        self.far = self._layer(st["far"], GROUND - 520, 520, 0.0, far=True)
        self.near = self._layer(st["near"], GROUND - 260, 330, 1.0, far=False)
        self.ground = Image.new("RGB", (W, H - GROUND))
        gd = ImageDraw.Draw(self.ground)
        for y in range(H - GROUND):
            gd.line((0, y, W, y), fill=mix(st["ground"], (0, 0, 0), min(0.6, y / 900)))
        self.parts = [[rnd.uniform(0, W), rnd.uniform(0, H), rnd.uniform(0.5, 1.5), rnd.uniform(0, 6.28)] for _ in range(70)]

    def _layer(self, color, top, height, density, far):
        PW, rnd, st = self.PW, self.rnd, self.st
        img = Image.new("RGBA", (PW, height), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        col = rgb(color) + (255,)
        sil = st["sil"]
        base_y = height * (0.55 if far else 0.45)
        pts = _hills(rnd, PW, base_y, height * (0.18 if far else 0.12), PW)
        d.polygon(pts + [(PW, height), (0, height)], fill=col)
        n = int(PW / (150 if far else 190))
        for i in range(n):
            x = i * PW / n + rnd.uniform(-30, 30)
            hy = min([p[1] for p in pts if abs(p[0] - x) < 14] or [base_y])
            s = rnd.uniform(0.7, 1.4) * (1.0 if far else 1.25)
            shapes = []
            if sil in ("spikes", "peaks", "cones"):
                w, h = (60 if sil != "peaks" else 130) * s, (120 if sil == "spikes" else 200) * s * (1.4 if sil == "peaks" else 1)
                shapes.append([(x - w, hy + 8), (x + w * 0.15, hy - h), (x + w, hy + 8)])
                if sil == "cones":
                    shapes[-1][1] = (x, hy - h * 0.8)
            elif sil == "rocks":
                w, h = 70 * s, 55 * s
                shapes.append([(x - w, hy + 6), (x - w * 0.5, hy - h), (x + w * 0.3, hy - h * 0.8), (x + w, hy + 6)])
            elif sil == "trees":
                h = 260 * s
                d.rectangle((x - 9 * s, hy - h, x + 9 * s, hy + 8), fill=col)
                for k in range(5):
                    a = k * 1.25
                    shapes.append([(x, hy - h), (x + math.cos(a) * 80 * s, hy - h - 30 * s + math.sin(a) * 30 * s),
                                   (x + math.cos(a + .5) * 60 * s, hy - h + 15 * s)])
            elif sil == "reeds":
                for k in range(4):
                    d.line((x + k * 12, hy + 6, x + k * 12 + rnd.uniform(-14, 14), hy - 110 * s - k * 12), fill=col, width=6)
            elif sil == "clouds":
                for k in range(4):
                    d.ellipse((x - 60 + k * 45, hy - 40 * s - (k % 2) * 20, x + 40 + k * 45, hy + 20), fill=col)
            for sh in shapes:
                d.polygon(sh, fill=col)
        # light edge along the top so shapes stand out from the sky (rim light)
        rim = light(rgb(color), 0.25) + (255,)
        d.line(pts, fill=rim, width=3)
        img.top = top
        return img

    def frame(self, cam, t):
        """The background for one frame: cam is how far the world has scrolled."""
        im = self.sky.copy()
        for layer, k in ((self.far, 0.18), (self.near, 0.6)):
            off = int(cam * k) % self.PW
            for ox in (-off, -off + self.PW):
                if ox < W and ox + self.PW > 0:
                    im.paste(layer, (ox, layer.top), layer)
        im.paste(self.ground, (0, GROUND))
        d = ImageDraw.Draw(im, "RGBA")
        # ground streaks that scroll with the creature's steps
        for i in range(22):
            x = (i * 137 - cam * 1.0) % (W + 200) - 100
            y = GROUND + 40 + (i * 53) % 420
            d.ellipse((x, y, x + 60 + (i % 5) * 20, y + 6), fill=(255, 255, 255, 18))
        p = self.st["part"]
        for q in self.parts:
            x = (q[0] - cam * 0.9 * q[2] - t * 25 * q[2]) % W
            y = (q[1] + t * (140 if p in ("rain",) else 40 if p in ("snow",) else -50 if p in ("bubbles", "embers") else 6) * q[2]) % H
            if p == "rain":
                d.line((x, y, x - 8, y + 36), fill=(200, 220, 255, 90), width=2)
            elif p == "snow":
                d.ellipse((x, y, x + 5 * q[2], y + 5 * q[2]), fill=(255, 255, 255, 200))
            elif p == "embers":
                d.ellipse((x, y, x + 6 * q[2], y + 6 * q[2]), fill=(255, 150, 40, 220))
            elif p == "bubbles":
                d.ellipse((x, y, x + 14 * q[2], y + 14 * q[2]), outline=(200, 235, 255, 140), width=2)
            elif p == "fireflies":
                a = 120 + int(100 * math.sin(t * 3 + q[3]))
                d.ellipse((x, y, x + 6, y + 6), fill=(190, 255, 90, a))
            elif p == "stars":
                pass
            else:
                d.ellipse((x, y, x + 4 * q[2], y + 3 * q[2]), fill=(255, 230, 200, 80))
        return im


# ---------------------------------------------------------------- video writer
def write_video(out, dur, painter, world, cwd=None, cam_dir=1):
    """painter(draw, t, u, cam) draws the actors. Frames are piped straight into ffmpeg."""
    n = max(int(dur * FPS), 2)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{OW}x{OH}",
           "-r", str(FPS), "-i", "-", "-vf", "scale=1080:1920:flags=bicubic", "-c:v", "libx264",
           "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-an", out]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, cwd=cwd)
    try:
        for i in range(n):
            t = i / FPS
            u = i / max(n - 1, 1)
            cam = t * 260 * cam_dir
            im = world.frame(cam, t)
            d = ImageDraw.Draw(im)
            ov = painter(im, d, t, u, cam)
            if ov is not None:
                im = ov
            proc.stdin.write(im.resize((OW, OH), Image.LANCZOS).tobytes())
    finally:
        proc.stdin.close()
        rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"toon render failed (ffmpeg code {rc})")


STEP = 1.9 * 2 * math.pi


def walk_scene(out, dur, world, sp, facing=1, zoom=(1.0, 1.0), hybrid_roar_end=False, cwd=None):
    """One creature walking across the scene."""
    def paint(im, d, t, u, cam):
        z = zoom[0] + (zoom[1] - zoom[0]) * u
        roar = 0.0
        if hybrid_roar_end and u > 0.72:
            roar = min(1.0, (u - 0.72) / 0.1) * (1 if u < 0.95 else (1 - (u - 0.95) / 0.05))
        sh = (6 * math.sin(t * 70)) if roar > 0.1 else 0
        draw_creature(d, sp, (400 if facing == 1 else 680) + sh, GROUND + 20, t * STEP * 0.5,
                      facing, z, {"t": t, "roar": roar, "moving": True})
    write_video(out, dur, paint, world, cwd, cam_dir=facing)


def hunt_scene(out, dur, world, sp, cwd=None):
    """The hybrid stalks its prey, crouches, pounces, and roars."""
    def paint(im, d, t, u, cam):
        crouch = 0.0
        roar = 0.0
        lunge = 0.0
        flash = 0.0
        if u < 0.42:
            moving, hx = True, 230
        elif u < 0.58:
            moving, hx = False, 230
            crouch = min(1, (u - 0.42) / 0.06)
        elif u < 0.68:
            moving = True
            k = (u - 0.58) / 0.10
            hx = 230 + 250 * k * k
            crouch = 1 - k
            lunge = k
        else:
            moving, hx = False, 480
            roar = min(1, (u - 0.68) / 0.08) * (1 if u < 0.95 else (1 - (u - 0.95) / 0.05))
        # the prey runs away, then is caught at the pounce
        if u < 0.68:
            px = 620 + 160 * min(u / 0.58, 1)
            if u > 0.6:
                px = 780
        else:
            px = 780
        cam_ = cam if (moving and u < 0.42) else cam * 0.25
        ph = t * STEP * 0.5
        sx, sy = 0, 0
        if 0.66 < u < 0.74:
            flash = 0.6 * (1 - (u - 0.66) / 0.08)
            sx, sy = 14 * math.sin(t * 80), 10 * math.cos(t * 73)
        if u < 0.68:
            draw_prey(d, px + sx, GROUND + 14, t * STEP * 0.5, 1, 0.55)
        else:
            # a puff of dust where the prey was
            for i in range(10):
                a = i * 0.63 + t * 2
                r = 30 + 160 * min(1, (u - 0.68) / 0.12)
                d.ellipse((780 + math.cos(a) * r - 20, GROUND - 30 + math.sin(a) * r * 0.4 - 20,
                           780 + math.cos(a) * r + 20, GROUND - 30 + math.sin(a) * r * 0.4 + 20), fill=(220, 200, 170))
        draw_creature(d, sp, hx + sx, GROUND + 20 + sy, ph, 1, 1.0 + 0.1 * lunge,
                      {"t": t, "crouch": crouch, "roar": roar, "moving": moving, "headup": roar})
        if flash > 0:
            ov = Image.new("RGB", im.size, (255, 255, 255))
            return Image.blend(im, ov, flash)
    write_video(out, dur, paint, world, cwd)


def meet_scene(out, dur, world, sa, sb, cwd=None):
    """Both parent animals walk toward each other and overlap. The morph into the hybrid follows."""
    def paint(im, d, t, u, cam):
        ph = t * STEP * 0.5
        ax, bx = 250 + 230 * u, 830 - 230 * u
        draw_creature(d, sa, ax, GROUND + 20, ph, 1, 0.9, {"t": t, "moving": True})
        draw_creature(d, sb, bx, GROUND + 20, ph + 1.5, -1, 0.9, {"t": t, "moving": True})
    write_video(out, dur, paint, world, cwd, cam_dir=0.3)
