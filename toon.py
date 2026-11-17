"""
A tiny flat-style animation engine. Draws creatures that really walk, swim, fly and attack,
in front of a scrolling night/day scene, and writes the result as a video. Needs only Pillow + ffmpeg.
The look is deliberately stylized (flat shapes, rim light), not realistic.
"""
import math
import random
import subprocess
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import numpy as np

W, H = 1080, 1920       # drawing size
OW, OH = 1080, 1920     # size sent to ffmpeg: native, no upscaling (the film look is added in the final grade)
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
    "bull": ("quad", "#2a2f3d", ["horns", "tall"]),
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
def limb(pen, hx, hy, phase, l1, l2, w, col, amp=0.55, lift=0.7, claws=False):
    a1 = math.sin(phase) * amp
    kx, ky = hx + math.sin(a1) * l1, hy + math.cos(a1) * l1
    a2 = a1 * 0.2 - lift * max(0.0, math.cos(phase))
    fx, fy = kx + math.sin(a2) * l2, ky + math.cos(a2) * l2
    pen.line([(hx, hy), (kx, ky), (fx, fy)], w, col)
    pen.ell(fx + 6, fy, w * 0.75, w * 0.4, dark(col, 0.8), rim=False)
    if claws:
        for k in range(3):
            pen.poly([(fx + 14 + k * 15, fy - 4), (fx + 40 + k * 17, fy + 10), (fx + 10 + k * 15, fy + 12)], (235, 230, 215), rim=False)


def eye(pen, x, y, r, sp, st):
    if sp["hybrid"]:
        r *= 1.35
        g = 1.0 + 0.15 * math.sin(st.get("t", 0) * 9)
        pen.poly([(x - r * 1.9, y - r * 1.5), (x + r * 2.1, y - r * 0.55), (x - r * 0.1, y - r * 2.9)], (12, 8, 16), rim=False)
        ec = sp.get("eye", (255, 190, 40))
        for k, a in ((3.2, 0.12), (2.2, 0.25), (1.5, 0.5)):
            pen.ell(x, y, r * k * g, r * k * g, mix((20, 14, 26), ec, a), rim=False)
        pen.dot(x, y, r * 1.1, ec)
        pen.ell(x + 1, y, r * 0.35, r * 0.8, (40, 10, 0), rim=False)
    else:
        pen.dot(x, y, r, (245, 245, 235))
        pen.dot(x + r * 0.25, y, r * 0.5, (20, 20, 25))


def mouth(pen, x, y, size, open_, sp):
    """A roaring mouth with teeth, drawn at the tip of the snout."""
    if sp["hybrid"]:
        open_ = max(open_, 0.45 if sp.get("horror") else 0.25)  # the hybrid never closes its mouth
    tooth = (236, 200, 190) if sp.get("bloody") else (245, 240, 225)
    if open_ <= 0.02:
        if sp["hybrid"] or "fangs" in sp["traits"]:
            pen.poly([(x, y + 4), (x + size * 0.12, y + 4), (x + size * 0.06, y + size * 0.32)], (240, 235, 220), rim=False)
        return
    o = size * 0.9 * open_
    pen.poly([(x - size * 1.1, y), (x + size * 0.3, y - 4), (x + size * 0.3, y + o), (x - size * 1.0, y + o * 0.7)],
             (90, 10, 18), rim=False)
    n = 9 if sp["hybrid"] else 6
    for i in range(n):
        tx = x - size * 1.0 + i * (size * 1.25 / n)
        pen.poly([(tx, y), (tx + size * 0.11, y), (tx + size * 0.055, y + size * 0.3 * open_ + 6)], tooth, rim=False)
        pen.poly([(tx, y + o * 0.8), (tx + size * 0.11, y + o * 0.8), (tx + size * 0.055, y + o * 0.8 - size * 0.24 * open_)],
                 tooth, rim=False)
        if sp.get("bloody") and i % 2 == 0:
            L = size * (0.5 + 0.4 * ((i * 37) % 5) / 4)
            pen.line([(tx + size * 0.05, y + o * 0.8), (tx + size * 0.05, y + o * 0.8 + L)], 7, (150, 8, 14))
    if sp.get("horror"):  # drool
        for k in range(3):
            pen.line([(x - size * (0.2 + 0.3 * k), y + o * 0.8), (x - size * (0.2 + 0.3 * k), y + o * 0.8 + size * 0.5)], 4, (200, 210, 215))


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
            h = bh * (0.35 + 0.15 * math.sin(i * 1.7)) * (1.7 if sp["hybrid"] else 1.0)
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
    cl = sp["hybrid"]
    limb(pen, hipf, hy_, ph + math.pi, L1, L1 + 10, 34, dk, amp, claws=cl)
    limb(pen, hipr, hy_, ph, L1, L1 + 10, 34, dk, amp, claws=cl)
    tail(pen, sp, bx, by, bl, bh, ph)
    pen.ell(bx, by, bl / 2, bh / 2, col)
    if "spots" in tr:
        for i in range(8):
            pen.dot(bx - bl * 0.35 + (i % 4) * bl * 0.22, by - bh * 0.15 + (i // 4) * bh * 0.4, 10, dark(col, 0.45))
    if "bigarms" in tr:
        pen.ell(bx + bl * 0.3, by + bh * 0.2, 55, 90, dark(col, 0.85))
    limb(pen, hipf, hy_, ph, L1, L1 + 10, 38, col, amp, claws=cl)
    limb(pen, hipr, hy_, ph + math.pi, L1, L1 + 10, 38, col, amp, claws=cl)
    if cl:  # old battle scars
        pen.line([(bx - 70, by - 50), (bx - 10, by + 40)], 7, (190, 70, 70))
        pen.line([(bx - 40, by - 60), (bx + 20, by + 30)], 6, (190, 70, 70))
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



# ---------------------------------------------------------------- the horror beast (folklore monster)
SNOUTS = {"wolf": 1.0, "fox": 1.0, "cheetah": 0.8, "crocodile": 1.3, "bull": 0.55, "bear": 0.6, "lion": 0.6,
          "snake": 0.8, "owl": 0.35, "bat": 0.5}


def dleg(pen, hx, hy, phase, col, amp=1.0):
    """A hind leg that bends backwards like a hunting dog's, ending in a long clawed foot."""
    s, c = math.sin(phase), math.cos(phase)
    t1 = 0.9 + 0.45 * s * amp
    kx, ky = hx + math.sin(t1) * 125, hy + math.cos(t1) * 125
    t2 = -0.55 + 0.3 * s * amp - 0.55 * max(0.0, c) * amp
    ax, ay = kx + math.sin(t2) * 135, ky + math.cos(t2) * 135
    fx, fy = ax + 80, ay + 46
    pen.line([(hx, hy), (kx, ky)], 52, col)
    pen.line([(kx, ky), (ax, ay)], 34, col)
    pen.line([(ax, ay), (fx, fy)], 24, col)
    for k in range(3):
        pen.poly([(fx - 4 + k * 9, fy - 10), (fx + 52 + k * 7, fy + 6 + k * 3), (fx + 6 + k * 9, fy + 8)], (225, 218, 195), rim=False)


def draw_beast(pen, sp, ph, st):
    col = sp["color"]
    dk = dark(col, 0.62)
    bone = (214, 206, 184)
    moving = st.get("moving", True)
    crouch = st.get("crouch", 0.0)
    roar = max(st.get("roar", 0.0), 0.4)
    amp = 1.0 if moving else 0.0
    t = st.get("t", 0.0)
    bob = (-abs(math.sin(ph)) * 12 if moving else math.sin(t * 2.5) * 5) + 80 * crouch
    snout = sp.get("snout", 0.9)

    def B(pts):
        return [(x, y + bob) for x, y in pts]

    # far arm and far leg
    limb(pen, 150, -330 + bob, ph + math.pi, 165, 175, 40, dk, 0.5 * amp + 0.05, 0.8, claws=True)
    dleg(pen, -170, -235 + bob, ph, dk, amp)
    # whip tail with a barb
    pts = [(-235 - u * 270, -215 + bob + 45 * math.sin(ph * 0.9 + u * 4) * u + 55 * u) for u in (0, 0.25, 0.5, 0.75, 1)]
    pen.line(pts[:3], 30, col)
    pen.line(pts[2:], 16, col)
    ex, ey = pts[-1]
    pen.poly([(ex, ey - 12), (ex - 60, ey + 6), (ex, ey + 14)], bone, rim=False)
    # hunched body
    back = [(-235, -215), (-190, -290), (-90, -365), (20, -420), (110, -442), (185, -410)]
    front = [(240, -340), (255, -290), (215, -215)]
    belly = [(120, -185), (0, -205), (-100, -195), (-185, -165)]
    pen.poly(B(back + front + belly), col)
    # ribs, scars, ragged fur
    for i in range(4):
        x = 10 + i * 42
        pen.line(B([(x, -208), (x + 28, -270), (x + 48, -335)]), 6, bone)
    pen.line(B([(-70, -330), (-10, -250)]), 6, (190, 30, 40))
    pen.line(B([(-40, -345), (20, -262)]), 6, (190, 30, 40))
    spine = sp.get("spine", "bone")
    for i in range(0 if spine == "none" else 9):
        k = i / 8
        x = -200 + k * 380
        y = -240 - 190 * math.sin(k * 2.7) * 0.9 - 30 * k
        h = (46 + 26 * ((i * 53) % 4) / 3) * (1.8 if spine == "long" else 1.0)
        pen.poly(B([(x - 18, y + 14), (x + 12, y + 14), (x - 6, y - h)]), bone, rim=False)
    for x in range(-170, 130, 32):
        pen.poly(B([(x, -190), (x + 16, -190), (x + 4, -146 - (x % 3) * 10)]), dk, rim=False)
    # near leg and near arm
    dleg(pen, -130, -235 + bob, ph + math.pi, col, amp)
    limb(pen, 150, -330 + bob, ph, 165, 175, 44, col, 0.5 * amp + 0.05, 0.8, claws=True)
    # head: a long skull, a huge hinged jaw, rows of teeth, several eyes
    hx0, hy0 = 235, -345 + bob * 0.7 + 30 * crouch
    L = 120 + 150 * snout
    o = 150 * roar
    upper = [(hx0, hy0 - 58), (hx0 + 90, hy0 - 62), (hx0 + 90 + L, hy0 - 12), (hx0 + 98 + L, hy0 + 14), (hx0 + 80, hy0 + 17), (hx0, hy0 + 20)]
    lower = [(hx0 + 10, hy0 + 18), (hx0 + 85 + L * 0.95, hy0 + 16 + o * 0.9), (hx0 + 80 + L * 0.95, hy0 + 40 + o), (hx0 + 10, hy0 + 56 + o * 0.3)]
    pen.poly(lower, dk)
    pen.poly([(hx0 + 20, hy0 + 16), (hx0 + 90 + L, hy0 + 14), (hx0 + 85 + L * 0.95, hy0 + 16 + o * 0.9), (hx0 + 20, hy0 + 30 + o * 0.3)],
             (70, 6, 14), rim=False)
    tooth = (236, 200, 190) if sp.get("bloody") else (240, 235, 215)
    n = 9
    for i in range(n):
        k = i / (n - 1)
        x = hx0 + 100 + L - k * (L + 30)
        h = (40 + 14 * ((i * 37) % 3)) * (0.7 + 0.6 * roar)
        pen.poly([(x, hy0 + 15), (x - 15, hy0 + 15), (x - 5, hy0 + 15 + h)], tooth, rim=False)
        ly = hy0 + 17 + (o * 0.9) * (1 - k * 0.05)
        lx = hx0 + 85 + L * 0.95 - k * (L * 0.95 - 10)
        ly = hy0 + 17 + (hy0 + 16 + o * 0.9 - (hy0 + 17)) * (1 - k)
        pen.poly([(lx, ly), (lx - 14, ly), (lx - 4, ly - h * 0.8)], tooth, rim=False)
        if sp.get("bloody") and i % 2 == 0:
            pen.line([(x - 8, hy0 + 15 + h), (x - 8, hy0 + 15 + h + 26 + (i % 3) * 18)], 6, (150, 8, 14))
    pen.poly(upper, col)
    pen.poly([(hx0 + 40, hy0 - 52), (hx0 + 190, hy0 - 40), (hx0 + 200, hy0 - 68), (hx0 + 40, hy0 - 78)], dark(col, 0.5), rim=False)  # angry brow
    # torn ears and horns
    pen.poly([(hx0 + 10, hy0 - 55), (hx0 - 35, hy0 - 155), (hx0 + 55, hy0 - 70)], col)
    pen.poly([(hx0 + 50, hy0 - 62), (hx0 + 44, hy0 - 135), (hx0 + 88, hy0 - 68)], dk)
    horn = sp.get("horn") or ("bull" if ("horns" in sp["traits"] and sp["name"] == "bull") else None)
    if horn == "bull":
        pen.poly([(hx0 + 55, hy0 - 60), (hx0 + 30, hy0 - 180), (hx0 + 125, hy0 - 245), (hx0 + 80, hy0 - 140), (hx0 + 105, hy0 - 62)], bone)
    elif horn == "antlers":
        for off, wd in ((0, 12), (40, 9)):
            pen.line([(hx0 + 40 + off, hy0 - 70), (hx0 + 10 + off, hy0 - 200), (hx0 - 30 + off, hy0 - 290)], wd, bone)
            for bx, by_, ex_, ey_ in ((10, -150, 70, -230), (-5, -205, -70, -260), (-18, -250, 30, -330)):
                pen.line([(hx0 + bx + off, hy0 + by_), (hx0 + ex_ + off, hy0 + ey_)], wd * 0.7, bone)
    elif horn == "ram":
        pts = [(hx0 + 30 + 62 * math.cos(a) * (1 - a / 12), hy0 - 85 - 62 * math.sin(a) * (1 - a / 12) + a * 5) for a in [i * 0.4 for i in range(14)]]
        pen.line([(hx0 + 55, hy0 - 70)] + pts, 20, bone)
    elif horn == "twin":
        pen.line([(hx0 + 40, hy0 - 70), (hx0 - 70, hy0 - 190), (hx0 - 210, hy0 - 215)], 18, bone)
        pen.line([(hx0 + 75, hy0 - 70), (hx0 - 10, hy0 - 205), (hx0 - 150, hy0 - 275)], 14, dk)
    eye(pen, hx0 + 112, hy0 - 30, 12, sp, st)
    eye(pen, hx0 + 62, hy0 - 40, 7, sp, st)
    eye(pen, hx0 + 160, hy0 - 22, 6, sp, st)
    for k in range(max(0, sp.get("eyes", 3) - 3)):  # more eyes along the brow
        eye(pen, hx0 + 85 + k * 38, hy0 - 56 - (k % 2) * 8, 5, sp, st)
    for k in range(3):  # drool
        x = hx0 + 100 + L - k * 40
        pen.line([(x, hy0 + 18 + o * 0.9), (x, hy0 + 40 + o * 0.9 + 30 + 10 * k)], 4, (205, 215, 220))


KINDS["beast"] = draw_beast


# ---------------------------------------------------------------- more horror bodies (every legend gets its own monster)
def draw_stalker(pen, sp, ph, st):
    """Impossibly tall and thin, a stag skull with antlers, arms that nearly drag on the ground."""
    col = sp["color"]
    dk = dark(col, 0.6)
    bone = sp.get("bone", (214, 206, 184))
    moving = st.get("moving", True)
    amp = 1.0 if moving else 0.0
    t = st.get("t", 0.0)
    crouch = st.get("crouch", 0.0)
    roar = max(st.get("roar", 0.0), 0.35)
    bob = (-abs(math.sin(ph)) * 8 if moving else math.sin(t * 2) * 3) + 110 * crouch
    sway = 14 * math.sin(ph * 0.5) if moving else 8 * math.sin(t * 1.4)
    limb(pen, 15, -625 + bob, ph + math.pi, 215, 245, 15, dk, 0.35 * amp + 0.04, 0.3, claws=True)
    limb(pen, -20, -420 + bob, ph + math.pi, 215, 215, 22, dk, 0.5 * amp + 0.02, 0.9)
    pen.poly([(-50, -415 + bob), (-25, -540 + bob), (0, -655 + bob), (42, -645 + bob), (48, -545 + bob), (28, -415 + bob)], col)
    for i in range(5):  # ribs showing through
        y = -620 + i * 40 + bob
        pen.line([(-18, y), (20, y + 8), (40, y - 4)], 5, bone)
    pen.line([(0, -655 + bob), (-8, -415 + bob)], 4, (150, 20, 30))
    limb(pen, -20, -420 + bob, ph, 215, 215, 26, col, 0.5 * amp + 0.02, 0.9)
    limb(pen, 15, -625 + bob, ph, 215, 245, 18, col, 0.35 * amp + 0.04, 0.3, claws=True)
    pen.line([(20, -650 + bob), (35 + sway * 0.5, -705 + bob), (55 + sway, -748 + bob)], 24, col)
    hx0, hy0 = 55 + sway, -750 + bob
    o = 70 * roar
    pen.poly([(hx0 - 5, hy0 + 28), (hx0 + 150, hy0 + 6 + o), (hx0 + 160, hy0 + 26 + o), (hx0 - 5, hy0 + 46 + o * 0.3)], dark(bone, 0.8))
    pen.poly([(hx0 + 10, hy0 + 30), (hx0 + 150, hy0 + 8 + o), (hx0 + 10, hy0 + 40)], (70, 6, 14), rim=False)
    pen.poly([(hx0 - 10, hy0 - 30), (hx0 + 70, hy0 - 48), (hx0 + 170, hy0 - 18), (hx0 + 178, hy0 + 6), (hx0 + 20, hy0 + 14)], bone)
    for i in range(7):
        x = hx0 + 40 + i * 20
        pen.poly([(x, hy0 + 12), (x + 10, hy0 + 12), (x + 5, hy0 + 40 + (i % 2) * 8)], (245, 240, 225), rim=False)
    eye(pen, hx0 + 60, hy0 - 16, 11, sp, st)
    eye(pen, hx0 + 20, hy0 - 12, 7, sp, st)
    for off, wd in ((0, 11), (-26, 8)):  # branching antlers
        pen.line([(hx0 + 30 + off, hy0 - 40), (hx0 + off, hy0 - 120), (hx0 - 30 + off, hy0 - 205)], wd, bone)
        for bx, by_, ex_, ey_ in ((0, -75, 55, -125), (-8, -130, -62, -170), (-22, -175, 22, -240)):
            pen.line([(hx0 + bx + off, hy0 + by_), (hx0 + ex_ + off, hy0 + ey_)], wd * 0.7, bone)


def draw_arachnid(pen, sp, ph, st):
    """A huge spider-thing: a swollen body, many knees high above it, a cluster of eyes and fangs."""
    col = sp["color"]
    dk = dark(col, 0.62)
    moving = st.get("moving", True)
    amp = 1.0 if moving else 0.0
    t = st.get("t", 0.0)
    roar = max(st.get("roar", 0.0), 0.3)
    by = -300 + 70 * st.get("crouch", 0.0) + (-abs(math.sin(ph * 2)) * 8 if moving else math.sin(t * 2.2) * 3)

    def leg(i, side):
        p = ph * 1.0 + i * 1.3 + side * math.pi
        sw = math.sin(p) * amp
        lift = max(0.0, math.cos(p)) * amp
        hx = 30 + (i - 1.5) * 52
        fx = hx + (i - 1.5) * 105 + 45 + 40 * sw
        kx, ky = (hx + fx) / 2 + 25, by - 250 - 50 * lift
        pen.line([(hx, by), (kx, ky), (fx, -38 * lift)], 13 if side else 10, col if side else dk)
        pen.poly([(fx - 8, -38 * lift), (fx + 22, -38 * lift + 4), (fx + 4, -38 * lift - 24)], (225, 218, 195), rim=False)
    for i in range(4):
        leg(i, 0)
    pen.ell(-135, by + 12, 150, 118, col)
    pen.poly([(-175, by - 20), (-95, by - 20), (-135, by + 70)], (170, 20, 30), rim=False)  # the red mark on its back
    for i in range(7):
        a = -2.6 + i * 0.55
        pen.poly([(-135 + 150 * math.cos(a), by + 12 + 118 * math.sin(a)), (-135 + 175 * math.cos(a + 0.12), by + 12 + 142 * math.sin(a + 0.12)),
                  (-135 + 150 * math.cos(a + 0.22), by + 12 + 118 * math.sin(a + 0.22))], dk, rim=False)
    pen.ell(40, by - 2, 88, 66, col)
    pen.ell(140, by - 8, 54, 50, dk)
    for k in range(4):
        pen.dot(165 + (k % 2) * 16 - 12, by - 36 + (k // 2) * 20, 7, sp.get("eye", (255, 40, 30)))
    eye(pen, 172, by - 6, 9, sp, st)
    mouth(pen, 190, by + 14, 56, roar, sp)
    for i in range(4):
        leg(i, 1)


def draw_serpent(pen, sp, ph, st):
    """A great coiling serpent that rears up with a hooded head and dripping fangs."""
    col = sp["color"]
    dk = dark(col, 0.65)
    t = st.get("t", 0.0)
    moving = st.get("moving", True)
    roar = max(st.get("roar", 0.0), 0.35)
    rise = 330 - 70 * st.get("crouch", 0.0) + (0 if moving else 14 * math.sin(t * 2.2))
    n = 18
    pts = []
    for i in range(n + 1):
        u = i / n
        sm = max(0.0, (u - 0.55) / 0.45)
        sm = sm * sm * (3 - 2 * sm)
        x = -380 + u * 450 + (60 * sm)
        y = -34 + 26 * math.sin(ph * 1.6 - u * 11) * (1 - sm) - sm * rise
        pts.append((x, y))
    for i in range(n):
        u = i / n
        w = 82 * (0.35 + 0.65 * math.sin(min(1, u * 1.15) * math.pi / 2))
        pen.line([pts[i], pts[i + 1]], w, col)
        pen.line([(pts[i][0], pts[i][1] + w * 0.12), (pts[i + 1][0], pts[i + 1][1] + w * 0.12)], w * 0.45, mix(col, (255, 245, 220), 0.35))
        if i % 2 == 0 and u > 0.1:
            pen.poly([(pts[i][0], pts[i][1] - w * 0.45), (pts[i][0] + 12, pts[i][1] - w * 0.45), (pts[i][0] + 3, pts[i][1] - w * 0.45 - 22)], dk, rim=False)
    hx, hy = pts[-1]
    pen.poly([(hx - 40, hy + 20), (hx - 78, hy - 70), (hx - 20, hy - 130), (hx + 55, hy - 70), (hx + 40, hy + 20)], dk)  # the hood
    pen.ell(hx + 25, hy - 55, 62, 48, col)
    pen.ell(hx + 40, hy - 70, 24, 14, mix(col, (255, 255, 255), 0.15), rim=False)
    eye(pen, hx + 52, hy - 72, 11, sp, st)
    eye(pen, hx + 5, hy - 80, 7, sp, st)
    pen.line([(hx + 82, hy - 48), (hx + 130, hy - 40), (hx + 160, hy - 62)], 6, (210, 40, 70))
    mouth(pen, hx + 78, hy - 40, 76, roar, sp)


def draw_wraith(pen, sp, ph, st):
    """A floating tattered cloak around a hollow pale face, with long skeletal arms reaching forward."""
    col = sp["color"]
    dk = dark(col, 0.55)
    bone = sp.get("bone", (214, 210, 200))
    t = st.get("t", 0.0)
    roar = max(st.get("roar", 0.0), 0.4)
    moving = st.get("moving", True)
    bob = -60 + 20 * math.sin(t * 2.2 + ph * 0.1) - 40 * st.get("crouch", 0.0)
    sway = 40 * math.sin(ph * 0.5) if moving else 20 * math.sin(t * 1.3)
    hem = []
    for i in range(9):
        x = -170 + i * 52 + sway * (i / 8) * -1
        hem.append((x, -80 + bob + 36 * math.sin(ph * 1.3 + i * 1.7) + (24 if i % 2 else 0)))
    for trail in (1.0, 0.7):  # ragged streaming cloth
        pen.poly([(-70, -520 + bob), (60, -540 + bob)] + hem[::-1][:0] + [(210 * trail, -300 + bob), (hem[-1][0] * trail, hem[-1][1])] + hem[::-1] + [(-190, -300 + bob)],
                 dk if trail == 1.0 else col)
    for i in range(6):  # long hair / tendrils streaming behind
        pen.line([(-10, -570 + bob), (-90 - i * 20, -560 + bob + 30 * math.sin(ph + i)), (-200 - i * 36, -510 + bob + 50 * math.sin(ph * 1.2 + i))], 6, (22, 20, 28))
    for sgn, base in ((1, -450), (-1, -480)):  # skeletal arms reaching out
        pts = [(40, base + bob), (150, base + 20 + bob + 14 * math.sin(ph + sgn)), (300, base + 60 + bob + 20 * math.sin(ph * 1.3 + sgn))]
        pen.line(pts, 12, bone)
        for k in range(4):
            pen.line([(pts[-1][0], pts[-1][1]), (pts[-1][0] + 60 + k * 6, pts[-1][1] - 24 + k * 16)], 5, bone)
    pen.ell(30, -560 + bob, 80, 96, bone)
    pen.poly([(-60, -520 + bob), (-30, -690 + bob), (60, -690 + bob), (115, -560 + bob), (90, -600 + bob), (30, -640 + bob), (-30, -560 + bob)], dk)  # the hood
    pen.ell(70, -575 + bob, 24, 30, (6, 4, 10), rim=False)
    pen.ell(0, -575 + bob, 20, 26, (6, 4, 10), rim=False)
    eye(pen, 70, -575 + bob, 9, sp, st)
    eye(pen, 0, -575 + bob, 8, sp, st)
    mouth(pen, 70, -520 + bob, 64, roar, sp)


def draw_owlman(pen, sp, ph, st):
    """A towering bird-thing: huge round glowing eyes, a hooked beak, taloned legs and a vast wingspan."""
    col = sp["color"]
    dk = dark(col, 0.6)
    t = st.get("t", 0.0)
    moving = st.get("moving", True)
    amp = 1.0 if moving else 0.0
    roar = max(st.get("roar", 0.0), 0.3)
    flap = math.sin(ph * 0.5 + t * 0.4) * 0.5 + 0.2 + (0.35 * math.sin(t * 5) if not moving else 0)
    bob = -abs(math.sin(ph)) * 8 * amp + 60 * st.get("crouch", 0.0)
    for sgn, c in ((1, dk), (-1, col)):  # the wings, far one first
        wy = -440 + bob
        tip = (-60 - 210 * sgn * 0.3, wy - 360 - 160 * flap)
        pen.poly([(0, wy), (-250, wy - 90 - 140 * flap), (-380, wy - 330 - 220 * flap), (-170, wy - 240 - 100 * flap), (-120, wy - 400 - 120 * flap), (40, wy - 120)], c)
        for k in range(5):
            pen.line([(-20 - k * 40, wy - 70 - k * 20), (-180 - k * 40, wy - 170 - 100 * flap - k * 38)], 5, dark(c, 0.7))
        if sgn == 1:
            break
    limb(pen, -30, -260 + bob, ph + math.pi, 130, 135, 30, dk, 0.4 * amp + 0.02, 0.6, claws=True)
    pen.ell(0, -360 + bob, 120, 175, col)
    for i in range(6):  # ragged feathers on the chest
        pen.poly([(-60 + i * 22, -300 + bob + (i % 2) * 40), (-40 + i * 22, -300 + bob + (i % 2) * 40), (-50 + i * 22, -230 + bob + (i % 2) * 40)], dark(col, 0.75), rim=False)
    limb(pen, 20, -260 + bob, ph, 130, 135, 34, col, 0.4 * amp + 0.02, 0.6, claws=True)
    pen.ell(40, -560 + bob, 92, 88, col)
    pen.poly([(-20, -620 + bob), (-45, -720 + bob), (40, -640 + bob)], col)
    pen.poly([(60, -625 + bob), (95, -725 + bob), (110, -620 + bob)], dk)
    for ex in (86, 8):
        pen.ell(ex, -565 + bob, 40, 40, (28, 20, 16), rim=False)
        eye(pen, ex, -565 + bob, 26, sp, st)
    pen.poly([(100, -550 + bob), (190, -515 + bob), (112, -490 + bob)], (200, 170, 90))
    mouth(pen, 172, -512 + bob, 56, roar, sp)


KINDS.update({"stalker": draw_stalker, "arachnid": draw_arachnid, "serpent": draw_serpent,
              "wraith": draw_wraith, "owlman": draw_owlman})
KIND_SCALE = {"swim": 0.78, "float": 0.78, "beast": 0.72, "stalker": 0.62, "arachnid": 0.82, "serpent": 0.92,
              "wraith": 0.85, "owlman": 0.78}
HEAD_AT = {"beast": (345, -330), "stalker": (110, -745), "arachnid": (180, -300), "serpent": (140, -420),
           "wraith": (40, -560), "owlman": (90, -560)}
PRINT_OF = {"beast": "claw", "stalker": "hoof", "arachnid": "dots", "serpent": "slither", "wraith": "frost", "owlman": "bird"}


def draw_crawler(pen, sp, ph, st):
    """Hangs UPSIDE DOWN from the ceiling: a man-and-dog thing with its head dangling low, grinning, while one
    long arm taps a knife against the floorboards."""
    col = sp["color"]
    dk = dark(col, 0.6)
    bone = (214, 206, 184)
    t = st.get("t", 0.0)
    roar = max(st.get("roar", 0.0), 0.35)
    sway = 10 * math.sin(t * 1.3)
    tap = max(0.0, math.sin(t * 7.0)) ** 3
    top = -770
    for sgn, (hx, ex, gx) in ((0, (-110, -150, -120)), (1, (-40, -60, -20))):  # the hind legs gripping the ceiling
        pen.line([(hx + 40, -560 + sway * 0.3), (ex, -660), (gx, top)], 22 if sgn else 18, col if sgn else dk)
        for k in range(3):
            pen.line([(gx, top), (gx - 18 + k * 18, top - 40)], 6, bone)
    pen.line([(110, -540 + sway * 0.3), (190, -660), (170, top)], 18, dk)  # the free arm gripping the ceiling
    for k in range(3):
        pen.line([(170, top), (150 + k * 20, top - 40)], 6, bone)
    pen.ell(20, -480 + sway * 0.3, 100, 150, col)
    for i in range(6):  # ribs
        pen.line([(-50 + i * 6, -590 + i * 36 + sway * 0.3), (60 + i * 6, -580 + i * 36 + sway * 0.3)], 4, bone)
    for i in range(9):  # spikes along its back
        y = -610 + i * 24 + sway * 0.3
        pen.poly([(-72, y), (-72, y + 12), (-112 - (i % 3) * 14, y + 6)], dk, rim=False)
    # the knife arm: shoulder, elbow, hand, blade tapping the floor
    hand = (150, -90 + 60 * (1 - tap))
    pen.line([(90, -520 + sway * 0.3), (170, -330), hand], 20, col)
    pen.poly([(hand[0] - 10, hand[1] - 6), (hand[0] + 10, hand[1] - 6), (hand[0] + 3, hand[1] + 96 * (1 - 0.3 * tap)), (hand[0] - 5, hand[1] + 90 * (1 - 0.3 * tap))],
             (210, 214, 224))
    pen.line([(hand[0] - 2, hand[1] + 10), (hand[0] + 1, hand[1] + 70)], 3, (255, 255, 255))
    pen.dot(hand[0], hand[1] - 8, 12, (60, 40, 30))
    # the neck goes down, and the head hangs upside down: jaw on top, eyes below, hair hanging
    pen.line([(110, -440 + sway * 0.3), (150, -330), (165 + sway, -262)], 40, col)
    hx0, hy0 = 165 + sway, -225
    pen.ell(hx0, hy0 - 8, 86, 66, col)
    pen.poly([(hx0 + 40, hy0 - 40), (hx0 + 175, hy0 - 12), (hx0 + 180, hy0 + 6), (hx0 + 40, hy0 + 2)], dk)  # a long dog muzzle, pointing forward
    pen.poly([(hx0 - 50, hy0 - 56), (hx0 - 60, hy0 - 120), (hx0 - 10, hy0 - 70)], dk, rim=False)       # ear
    mo = 22 + 30 * roar
    pen.poly([(hx0 - 58, hy0 - 34), (hx0 + 150, hy0 - 40 - mo * 0.2), (hx0 + 170, hy0 - 40 - mo), (hx0 - 50, hy0 - 70 - mo * 0.5)], (70, 6, 14), rim=False)
    for i in range(8):  # a wide grin of teeth
        x = hx0 - 40 + i * 24
        pen.poly([(x, hy0 - 36 - mo * 0.2 * i / 7), (x + 11, hy0 - 38 - mo * 0.2 * i / 7), (x + 5, hy0 - 12 - (i % 2) * 8)], (245, 240, 225), rim=False)
    eye(pen, hx0 + 50, hy0 + 18, 14, sp, st)
    eye(pen, hx0 - 12, hy0 + 24, 10, sp, st)
    for i in range(9):  # lank hair hanging down from the forehead
        pen.line([(hx0 - 60 + i * 14, hy0 + 40), (hx0 - 66 + i * 14 + 6 * math.sin(t * 2 + i), hy0 + 110 + (i % 3) * 20)], 4, (16, 14, 18))
    pen.poly([(hx0 + 160, hy0 - 20), (hx0 + 178, hy0 - 6), (hx0 + 168, hy0 + 8)], (8, 6, 10), rim=False)  # the nose


KINDS["crawler"] = draw_crawler
KIND_SCALE["crawler"] = 0.9
HEAD_AT["crawler"] = (160, -230)
PRINT_OF["crawler"] = "dots"


# ---------------------------------------------------------------- dinosaurs and dragons
def _taper(pen, pts, w0, w1, col):
    n = len(pts) - 1
    for i in range(n):
        w = w0 + (w1 - w0) * i / max(n - 1, 1)
        pen.line([pts[i], pts[i + 1]], w, col)


def _veins(pen, segs, t, glow=(255, 130, 30)):
    """Glowing cracks of lava running across a body."""
    for j, pts in enumerate(segs):
        f = 0.75 + 0.25 * math.sin(t * 3 + j * 1.7)
        c = (int(glow[0] * f), int(glow[1] * f), int(glow[2] * f))
        pen.line(pts, 11, mix(c, (40, 10, 0), 0.5))
        pen.line(pts, 5, c)


def draw_theropod(pen, sp, ph, st):
    """A hulking upright predator on two legs: huge skull, tiny arms, thick tail. With cracks of lava, spikes or horns
    it becomes a new kind of monster."""
    col = sp["color"]
    dk = dark(col, 0.6)
    bone = (214, 206, 184)
    belly = mix(col, (230, 215, 180), 0.28)
    moving = st.get("moving", True)
    t = st.get("t", 0.0)
    amp = 1.0 if moving else 0.0
    roar = st.get("roar", 0.0)
    open_ = roar
    if sp["hybrid"]:
        open_ = max(roar, 0.45 if sp.get("horror") else 0.25)
    bob = (-abs(math.sin(ph)) * 14) if moving else math.sin(t * 2.2) * 5
    sway = math.sin(ph * 0.9) * 40 if moving else math.sin(t * 1.3) * 18
    # far leg, tail
    limb(pen, -20, -335 + bob, ph + math.pi, 165, 175, 58, dk, 0.5 * amp + 0.03, 0.8, claws=True)
    tp = [(-150 - i * 82, -335 + bob + 36 * i * (1 - i / 14.0) * 0.1 + sway * (i / 6.0) ** 1.4 + 6 * i) for i in range(7)]
    _taper(pen, tp, 120, 22, col)
    # the body
    back = [(-170, -345), (-120, -430), (-20, -480), (90, -505), (170, -525), (245, -545)]
    front = [(300, -505), (300, -430), (255, -350)]
    under = [(150, -290), (20, -268), (-90, -282), (-170, -305)]
    pen.poly([(x, y + bob) for x, y in back + front + under], col)
    pen.poly([(x, y + bob) for x, y in [(262, -400), (150, -296), (20, -274), (-90, -286), (-20, -335), (120, -350)]], belly, rim=False)
    if sp.get("spines") or sp.get("cracks"):
        for i in range(7):
            x = -150 + i * 55
            y = -352 - 80 * math.sin(min(1, (i + 1) / 7.0) * 1.2) + bob
            pen.poly([(x - 16, y + 24), (x + 16, y + 24), (x + 4, y - 40 - 12 * (i % 3))], bone)
    if sp.get("cracks"):
        _veins(pen, [[(-130, -360 + bob), (-70, -395 + bob), (-10, -380 + bob), (40, -420 + bob), (110, -415 + bob)],
                     [(60, -330 + bob), (110, -360 + bob), (170, -350 + bob), (215, -395 + bob)],
                     [(-250, -330 + bob), (-330, -340 + bob + sway * 0.2), (-400, -320 + bob + sway * 0.4)],
                     [(-70, -300 + bob), (-40, -330 + bob), (-20, -400 + bob)]], t)
    # tiny arms
    pen.line([(255, -430 + bob), (295, -385 + bob), (330, -392 + bob)], 20, dk)
    for k in range(2):
        pen.poly([(325 + k * 8, -400 + bob), (352 + k * 8, -380 + bob), (322 + k * 8, -384 + bob)], bone, rim=False)
    # near leg
    pen.ell(0, -345 + bob, 92, 108, col)
    limb(pen, 40, -335 + bob, ph, 170, 175, 66, col, 0.5 * amp + 0.03, 0.8, claws=True)
    # neck and skull
    pen.line([(190, -480 + bob), (270, -545 + bob)], 150, col)
    o = 190 * open_ * 0.9
    hb = bob
    pen.poly([(x, y + hb) for x, y in [(270, -498 + o * 0.15), (440, -508 + o), (548, -514 + o * 1.05), (522, -470 + o * 1.1),
                                       (430, -452 + o * 0.95), (300, -452 + o * 0.5)]], dk)
    mouth(pen, 505, -515 + hb, 210, open_, sp)
    pen.poly([(x, y + hb) for x, y in [(250, -592), (335, -632), (455, -616), (565, -572), (588, -535), (545, -514), (430, -506), (300, -500), (250, -522)]], col)
    pen.poly([(x, y + hb) for x, y in [(380, -620), (470, -622), (440, -598)]], dk, rim=False)
    pen.dot(560, -560 + hb, 7, dk)
    if sp.get("horns"):
        pen.poly([(335, -628 + hb), (290, -720 + hb), (372, -630 + hb)], bone)
        pen.poly([(420, -622 + hb), (410, -705 + hb), (456, -618 + hb)], bone)
    eye(pen, 425, -585 + hb, 13, sp, st)
    if sp.get("eye_count", 2) > 2:
        eye(pen, 372, -596 + hb, 7, sp, st)
    if open_ > 0.5 and sp.get("cracks"):  # a glow deep in the throat
        pen.ell(430, -520 + o * 0.55 + hb, 60, 14, (150, 40, 14), rim=False)


def draw_sauropod(pen, sp, ph, st):
    """A long-necked giant that eats leaves. Used as a herd in the background."""
    col = sp["color"]
    dk = dark(col, 0.62)
    moving = st.get("moving", True)
    t = st.get("t", 0.0)
    amp = 1.0 if moving else 0.0
    bob = (-abs(math.sin(ph)) * 8) if moving else math.sin(t * 1.4) * 4
    limb(pen, 120, -300 + bob, ph + math.pi, 130, 130, 62, dk, 0.35 * amp + 0.02, 0.4)
    limb(pen, -120, -300 + bob, ph, 130, 130, 62, dk, 0.35 * amp + 0.02, 0.4)
    tp = [(-200 - i * 70, -330 + bob + 12 * i - 10 * math.sin(ph * 0.7) * i * 0.4) for i in range(7)]
    _taper(pen, tp, 100, 16, col)
    pen.ell(0, -340 + bob, 250, 130, col)
    pen.ell(20, -310 + bob, 190, 70, mix(col, (230, 215, 180), 0.22), rim=False)
    neck = [(170, -380 + bob), (250, -470 + bob), (310, -600 + bob), (360, -720 + bob + 8 * math.sin(t * 1.2)), (410, -770 + bob)]
    _taper(pen, neck, 84, 40, col)
    pen.ell(430, -776 + bob, 52, 30, col)
    pen.dot(446, -786 + bob, 7, (240, 235, 220))
    limb(pen, 120, -300 + bob, ph, 130, 130, 70, col, 0.35 * amp + 0.02, 0.4)
    limb(pen, -120, -300 + bob, ph + math.pi, 130, 130, 70, col, 0.35 * amp + 0.02, 0.4)


def draw_dragon(pen, sp, ph, st):
    """A winged dragon: long neck, swept horns, spiked back, barbed tail. Breathes fire when it roars."""
    col = sp["color"]
    dk = dark(col, 0.6)
    bone = (214, 206, 184)
    belly = mix(col, (235, 215, 160), 0.3)
    moving = st.get("moving", True)
    t = st.get("t", 0.0)
    amp = 1.0 if moving else 0.0
    roar = st.get("roar", 0.0)
    open_ = roar
    if sp["hybrid"]:
        open_ = max(roar, 0.4 if sp.get("horror") else 0.25)
    bob = (-abs(math.sin(ph)) * 10) if moving else math.sin(t * 2.0) * 6
    flap = math.sin(t * 3.0 + ph * 0.3) if (moving or sp.get("flying")) else 0.25 * math.sin(t * 1.4)
    sx, sy = 40, -430 + bob

    def wing(scale, color, edge, lift):
        base = 1.05 + 0.55 * flap * lift
        tips = []
        for k, (da, L) in enumerate(((0.0, 560), (-0.42, 600), (-0.84, 540), (-1.24, 430))):
            a = base + da * 0.7
            tips.append((sx - scale * L * math.cos(a) * 0.82, sy - scale * L * math.sin(a) * 0.95))
        pts = [(sx, sy)] + tips + [(sx - 120 * scale, sy + 70)]
        pen.poly(pts, color, rim=False)
        for p in tips:
            pen.line([(sx, sy), p], 9, edge)
    wing(0.92, dark(col, 0.45), dark(col, 0.7), 0.9)       # far wing
    limb(pen, 110, -300 + bob, ph + math.pi, 115, 125, 42, dk, 0.5 * amp + 0.05, 0.8, claws=True)
    limb(pen, -120, -300 + bob, ph, 115, 125, 46, dk, 0.5 * amp + 0.05, 0.8, claws=True)
    sway = math.sin(ph * 0.8) * 50 if moving else math.sin(t * 1.5) * 26
    tp = [(-160 - i * 85, -320 + bob + 22 * i + sway * (i / 6.0) ** 1.5) for i in range(8)]
    _taper(pen, tp, 90, 18, col)
    ex, ey = tp[-1]
    pen.poly([(ex, ey - 18), (ex - 80, ey + 8), (ex, ey + 30), (ex - 22, ey + 6)], bone)
    pen.ell(-5, -325 + bob, 200, 108, col)
    pen.ell(15, -285 + bob, 150, 56, belly, rim=False)
    for i in range(7):
        x = -150 + i * 48
        y = -420 + bob + 18 * abs(i - 3) * 0.6
        pen.poly([(x - 13, y + 20), (x + 13, y + 20), (x + 2, y - 30 - 6 * (i % 2))], bone)
    neck = [(140, -360 + bob), (215, -430 + bob), (250, -540 + bob), (300, -625 + bob + 5 * math.sin(t * 1.5))]
    _taper(pen, neck, 96, 58, col)
    hb = bob + 5 * math.sin(t * 1.5)
    o = 150 * open_ * 0.9
    pen.poly([(x, y + hb) for x, y in [(268, -630 + o * 0.1), (400, -640 + o), (500, -640 + o * 1.05), (480, -602 + o * 1.05), (390, -588 + o * 0.9), (285, -590 + o * 0.4)]], dk)
    mouth(pen, 460, -640 + hb, 190, open_, sp)
    pen.poly([(x, y + hb) for x, y in [(255, -672), (340, -702), (450, -690), (532, -655), (535, -638), (460, -632), (340, -634), (260, -640)]], col)
    for hx_, hy_, ex_, ey_ in ((300, -690, 120, -790), (340, -698, 190, -820)):  # swept-back horns
        pen.poly([(hx_, hy_ + hb), (hx_ + 30, hy_ + 10 + hb), (ex_, ey_ + hb)], bone)
    pen.poly([(300, -640 + hb), (255, -600 + hb), (290, -580 + hb)], dk, rim=False)
    eye(pen, 380, -664 + hb, 12, sp, st)
    pen.dot(510, -648 + hb, 6, dk)
    if open_ > 0.55 and sp.get("fire", True):  # fire
        for j, (L, c) in enumerate(((330, (255, 120, 30)), (230, (255, 190, 70)), (130, (255, 245, 190)))):
            wob = 14 * math.sin(t * 22 + j)
            pen.poly([(485, -620 + o * 0.5 + hb), (485 + L, -650 + o * 0.5 + wob + hb), (485 + L * 1.1, -600 + o * 0.5 + hb), (485 + L * 0.6, -570 + o * 0.5 + wob + hb)], c, rim=False)
    if sp.get("cracks"):
        _veins(pen, [[(-120, -370 + bob), (-50, -395 + bob), (20, -380 + bob)], [(60, -300 + bob), (120, -330 + bob), (160, -360 + bob)]], t)
    limb(pen, 110, -300 + bob, ph, 115, 125, 48, col, 0.5 * amp + 0.05, 0.8, claws=True)
    limb(pen, -120, -300 + bob, ph + math.pi, 115, 125, 52, col, 0.5 * amp + 0.05, 0.8, claws=True)
    wing(1.0, mix(col, (10, 6, 12), 0.15), mix(col, (255, 255, 255), 0.12), 1.0)   # near wing


KINDS.update({"theropod": draw_theropod, "sauropod": draw_sauropod, "dragon": draw_dragon})
KIND_SCALE.update({"theropod": 0.5, "sauropod": 0.5, "dragon": 0.55})
HEAD_AT.update({"theropod": (420, -580), "sauropod": (430, -775), "dragon": (390, -650)})
PRINT_OF.update({"theropod": "claw", "sauropod": "claw", "dragon": "claw"})


def dino_spec(kind, color, eye=(240, 230, 200), **kw):
    sp = {"name": kind, "kind": kind, "color": color, "traits": set(), "scale": 1.0, "hybrid": False, "snout": 0.9, "eye": eye}
    sp.update(kw)
    return sp


def rex_spec():
    """The old king of the prehistoric world."""
    return dino_spec("theropod", (104, 92, 58), (240, 200, 70))


def sauro_spec(color=(84, 92, 72)):
    return dino_spec("sauropod", color)


def head_anchor(sp):
    """Where the face is, in creature units (used to zoom in on it)."""
    tr, k = sp["traits"], sp["kind"]
    if k == "chimera":
        return sp["head0"]
    if k in HEAD_AT:
        return HEAD_AT[k]
    if k == "quad":
        legH = 80 if "low" in tr else (200 if "tall" in tr else 150)
        if "bigarms" in tr:
            legH = 140
        bl = 360 if "low" in tr else 330
        bh = 200 if "bigarms" in tr else (150 if "shell" in tr else 170)
        hr = 62 * (1.1 if sp["hybrid"] else 1.0)
        by = -legH - bh / 2
        hy = by - bh * 0.2 - (170 if "longneck" in tr else 20) - (10 if sp["hybrid"] and "longneck" not in tr else 0)
        return bl * 0.5 + hr * 0.9, hy
    return {"bird": (110, -235), "swim": (160, -350), "float": (0, -330), "snake": (210, -60),
            "bug": (140, -100), "crab": (0, -210)}.get(k, (100, -250))


_BOXES = []          # where the creature was drawn in the current frame (for captions to stay out of its way)
_REC_OFF = [0, 0]
LAST_TRACK = []      # [(time, (x0, y0, x1, y1) or None)] of the last video written


# ---------------------------------------------------------------- slow reveal: eyes first, then the face, then the body
EMERGE = {"dur": 0.0, "t": 0.0, "t0": None, "last": -9.0, "seen": False}   # state of the current scene


def _sm(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def _emerge_k(force_dur=None):
    """How far the creature's slow fade-in has got (0..1). 1.0 means fully shown (or this scene does not fade)."""
    E = EMERGE
    dur = force_dur if force_dur is not None else E["dur"]
    if dur <= 0:
        return 1.0
    if E["t0"] is None or E["t"] - E["last"] > 0.3 or E["t"] < E["last"]:
        E["t0"] = E["t"]
    E["last"] = E["t"]
    k = min(1.0, (E["t"] - E["t0"]) / dur)
    if k >= 1.0:
        E["seen"] = True
    return k


def _reveal_mask(a, hx, hy, k, s):
    """Visibility 0..1 for every pixel: starts at the head and sweeps outward to the tail and feet."""
    h, w = a.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dist = np.hypot(xx - hx, (yy - hy) * 0.85)
    solid = a > 0.05
    maxd = float(dist[solid].max()) if solid.any() else 1.0
    maxd = max(maxd, 60.0)
    p = _sm((k - 0.14) / 0.86)
    feather = 70 * s + 0.16 * maxd
    R = (p ** 1.25) * (maxd + feather) + 18 * s
    return np.clip((R - dist) / feather, 0.0, 1.0)


def _eye_glow(layer, sp, hx, hy, k, s):
    """Just the glowing eyes, as a soft RGBA layer. They fade in first, long before the rest of the creature."""
    ec = np.array(sp.get("eye", (255, 190, 40)), dtype=np.float32)
    rgb = np.asarray(layer.convert("RGB"), dtype=np.float32)
    a = np.asarray(layer.getchannel("A"), dtype=np.float32) / 255.0
    near = (np.abs(rgb - ec).sum(axis=-1) < 70) & (a > 0.6)
    mask = Image.fromarray((near * 255).astype(np.uint8), "L")
    if not near.any():  # no coloured eye found: two small lights where the face should be
        dm = ImageDraw.Draw(mask)
        for dx in (-22, 22):
            dm.ellipse((hx + dx * s - 9 * s, hy - 9 * s, hx + dx * s + 9 * s, hy + 9 * s), fill=255)
    core = mask.filter(ImageFilter.GaussianBlur(max(1.5, 2.5 * s)))
    halo = mask.filter(ImageFilter.GaussianBlur(max(6.0, 20 * s)))
    amp = _sm(k / 0.2) * (0.9 + 0.1 * math.sin(k * 60))
    ca = np.asarray(core, dtype=np.float32) / 255.0
    ha = np.asarray(halo, dtype=np.float32) / 255.0
    alpha = np.clip(ca * 1.6 + ha * 1.4, 0, 1) * amp
    out = np.zeros(rgb.shape[:2] + (4,), dtype=np.uint8)
    hot = np.clip(ec * 0.9 + 70 * ca[..., None], 0, 255)
    out[..., :3] = hot.astype(np.uint8)
    out[..., 3] = (alpha * 255).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def _put_piece(im, piece, x0, y0):
    if im.mode == "RGBA":
        im.alpha_composite(piece, (x0, y0))
    else:
        im.paste(piece.convert("RGB"), (x0, y0), piece.getchannel("A"))


def _draw_emerging(img_draw, sp, cx, gy, phase, facing, scale, st, s, k):
    im = img_draw._image
    ax, ay = head_anchor(sp)
    x0, x1 = int(max(0, cx - 560 * s)), int(min(im.width, cx + 560 * s))
    y0, y1 = int(max(0, gy - 1300 * s)), int(min(im.height, gy + 90 * s))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return
    if sp["hybrid"] and sp.get("aura", True) and st.get("aura", True):  # the red glow comes up with the body
        al = _sm((k - 0.4) / 0.6)
        for kk, a_ in ((1.0, 22), (0.78, 26), (0.56, 30)):
            img_draw.ellipse((cx - 380 * s * kk, gy - 640 * s * kk, cx + 380 * s * kk, gy + 40 * s * kk), fill=(170, 10, 14, int(a_ * al)))
    layer = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer, "RGBA")
    ox, oy = _REC_OFF
    _REC_OFF[0], _REC_OFF[1] = ox + x0, oy + y0
    try:
        draw_creature(ld, sp, cx - x0, gy - y0, phase, facing, scale, dict(st, noemerge=True, aura=False))
    finally:
        _REC_OFF[0], _REC_OFF[1] = ox, oy
    hx, hy = cx - x0 + facing * s * ax, gy - y0 + s * ay
    a = np.asarray(layer.getchannel("A"), dtype=np.float32) / 255.0
    vis = _reveal_mask(a, hx, hy, k, s)
    gain = 0.45 + 0.55 * _sm((k - 0.1) / 0.9)          # it also comes up out of the dark
    rgb = np.asarray(layer.convert("RGB"), dtype=np.float32) * gain
    body = np.zeros(a.shape + (4,), dtype=np.uint8)
    body[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    body[..., 3] = (a * vis * 255).astype(np.uint8)
    piece = Image.fromarray(body, "RGBA")
    piece.alpha_composite(_eye_glow(layer, sp, hx, hy, k, s))
    _put_piece(im, piece, x0, y0)


def draw_creature(img_draw, sp, cx, gy, phase, facing=1, scale=None, st=None, focus=None):
    st = st or {}
    s = 1.4 * (scale if scale is not None else 1.0) * sp.get("scale", 1.0) * KIND_SCALE.get(sp["kind"], 1.0)
    if focus:  # (ax, ay, tx, ty): put the creature point (ax, ay) at screen position (tx, ty)
        ax, ay, tx, ty = focus
        cx, gy = tx - facing * s * ax, ty - s * ay
    if not st.get("noemerge") and EMERGE["dur"] > 0:
        k = _emerge_k()
        if k < 0.999:
            _draw_emerging(img_draw, sp, cx, gy, phase, facing, scale, st, s, k)
            return
    if sp["hybrid"] and sp.get("aura", True) and st.get("aura", True):
        for k, a in ((1.0, 22), (0.78, 26), (0.56, 30)):
            img_draw.ellipse((cx - 380 * s * k, gy - 640 * s * k, cx + 380 * s * k, gy + 40 * s * k), fill=(170, 10, 14, a))
    ox, oy = _REC_OFF
    _BOXES.append((cx + ox - 330 * s, gy + oy - 1050 * s, cx + ox + 330 * s, gy + oy + 40 * s))
    if sp["kind"] == "chimera" and st.get("moving", True) and not st.get("rawphase"):
        r = chim_rate(sp, st.get("ground"))   # step rate follows the ground speed: planted feet do not slide
        if r:
            phase = phase / (STEP * 0.5) * r
    pen = Pen(img_draw, cx, gy, s, facing, rimfn)
    if st.get("noshadow") or sp["kind"] == "chimera":
        pass
    elif sp["kind"] not in ("swim", "float", "wraith") and sp["kind"] != "bird" or (sp["kind"] == "bird" and "flying" not in sp["traits"]):
        shadow(img_draw, cx, gy, 150 * s)
    KINDS[sp["kind"]](pen, sp, phase, st)


def shadow(d, cx, gy, w):
    d.ellipse((cx - w, gy - 14, cx + w, gy + 18), fill=(8, 8, 14))


def prey_spec(text):
    """Pick a small animal to chase, based on what the story says the hybrid hunts."""
    x = (text or "").lower()
    has = lambda *w: any(k in x for k in w)
    if has("bird", "eagle", "owl", "hawk", "sparrow", "pigeon", "vulture", "gull", "bat"):
        return animal_spec("owl"), 0.42
    if has("fish", "shark", "eel", "squid", "krill", "shrimp", "seal", "penguin", "jelly"):
        return animal_spec("shark"), 0.4
    if has("lizard", "gecko", "snake", "skink", "scorpion", "beetle", "insect", "spider", "ant ", "crab", "frog"):
        return animal_spec("lizard"), 0.5
    if has("deer", "antelope", "gazelle", "goat", "sheep", "ibex", "caribou", "camel", "elk"):
        sp = animal_spec("wolf")
        sp["color"] = rgb("#c9a878")
        return sp, 0.42
    return {"name": "hare", "kind": "quad", "color": (200, 170, 130), "traits": {"bigears", "low"},
            "scale": 1.0, "hybrid": False}, 0.55


def draw_prey(d, cx, gy, phase, facing, prey):
    sp, sc = prey
    draw_creature(d, sp, cx, gy, phase * 1.8, facing, sc, {"moving": True})


def dim(im, amount=110):
    im.paste(Image.new("RGB", im.size, (0, 0, 0)), mask=Image.new("L", im.size, amount))


def closeup(im, d, sp, t, dur, z0, z1, roar=0.6, shake=0.0, tt=0.0):
    """The camera is right up in the creature's face."""
    dim(im, 120)
    ax, ay = head_anchor(sp)
    k = min(1.0, t / max(dur, 0.01))
    k = k * k * (3 - 2 * k)
    z = z0 + (z1 - z0) * k
    ox = shake * math.sin(tt * 90)
    oy = shake * 0.7 * math.cos(tt * 77)
    draw_creature(d, sp, 0, 0, tt * 2.0, 1, z, {"t": tt, "roar": roar, "moving": False},
                  focus=(ax, ay, 600 + ox, 800 + oy))


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
PLACES["a village at night"] = dict(sky=("#0a1030", "#1b2a55"), far="#101c3a", near="#080f22",
                                    ground="#050914", sil="trees", sun="#d8dcc8", part="fireflies")
PLACES["an eerie pathway"] = dict(sky=("#1a0f2e", "#3a2548"), far="#241638", near="#120a20", ground="#0a0612",
                                  sil="dead", sun="#c9c3d8", part="fog", fog=0.35, stars=True)
PLACES["a dark forest"] = dict(sky=("#08141a", "#1f4440"), far="#173a34", near="#0b211d", ground="#040a09",
                               sil="pines", sun=None, part="fireflies", fog=0.3, stars=True)
PLACES["a graveyard at night"] = dict(sky=("#0b1020", "#2a3558"), far="#1a2340", near="#0b1022", ground="#070a14",
                                      sil="graves", sun="#e6e6d8", part="fog", fog=0.5, stars=True)
PLACES["a misty river bank"] = dict(sky=("#0e1a2a", "#324a5e"), far="#1f3345", near="#0e1c28", ground="#08121a",
                                    sil="reeds", sun="#cfd8e0", part="fog", fog=0.6, stars=True)
PLACES["a moonlit field"] = dict(sky=("#0a1030", "#2a3a68"), far="#16224a", near="#0b1330", ground="#080d20",
                                 sil="wheat", sun="#f0ecd0", part="leaves", fog=0.1, stars=True)
PLACES["a stormy hillside"] = dict(sky=("#0b0e1a", "#444e6c"), far="#2a3350", near="#141a2e", ground="#06080f",
                                   sil="peaks", sun=None, part="rain", fog=0.2, storm=True, stars=False)
PLACES["a village at night"]["houses"] = True
SETTINGS = {"pathway": "an eerie pathway", "forest": "a dark forest", "graveyard": "a graveyard at night",
            "river": "a misty river bank", "village": "a village at night", "field": "a moonlit field",
            "hills": "a stormy hillside"}
DEFAULT_PLACE = dict(sky=("#1a2a44", "#0a0f1c"), far="#1f3050", near="#101a30", ground="#0a1020", sil="dunes", sun="#e8e8f0", part="dust")


def _hills(rnd, width, base, amp, period_w):
    ks = [(rnd.choice([1, 2, 3, 5]), rnd.uniform(0, 6.28), rnd.uniform(0.4, 1.0)) for _ in range(3)]
    pts = []
    for x in range(0, width + 1, 12):
        y = base + sum(a * amp * math.sin(2 * math.pi * k * x / period_w + ph) for k, ph, a in ks)
        pts.append((x, y))
    return pts


# ---------------------------------------------------------------- signs in the sky (how the monster arrives)
def sky_effects(im, fx, t, sun):
    d = ImageDraw.Draw(im, "RGBA")
    sx, sy, sr = sun
    if fx == "meteors":
        for i in range(4):
            ph = (t * 0.45 + i * 0.27) % 1.0
            x0, y0 = 150 + i * 260, 80 + (i * 97) % 200
            x1, y1 = x0 + 520 * ph, y0 + 300 * ph
            a = int(230 * max(0.0, 1 - ph) ** 0.5)
            d.line((x1 - 160, y1 - 92, x1, y1), fill=(255, 235, 200, a), width=4)
            d.ellipse((x1 - 6, y1 - 6, x1 + 6, y1 + 6), fill=(255, 250, 235, a))
    elif fx == "comet":
        cx, cy = 780 + 40 * math.sin(t * 0.3), 300
        for k in range(14):
            d.ellipse((cx + k * 34 - 22 + k, cy - k * 11 - 10, cx + k * 34 + 22 + k, cy - k * 11 + 10), fill=(190, 230, 255, max(0, 150 - k * 11)))
        for r, a in ((60, 40), (36, 90), (18, 230)):
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(235, 250, 255, a))
    elif fx == "aurora":
        for j, c in enumerate(((60, 255, 150), (120, 100, 255), (60, 220, 230))):
            top = [(x, 260 + j * 90 + 60 * math.sin(x * 0.006 + t * 0.7 + j) + 26 * math.sin(x * 0.017 + t * 1.3)) for x in range(0, W + 60, 60)]
            for x, y in top:
                d.polygon([(x, y), (x + 60, y), (x + 60, y + 260), (x, y + 260)], fill=c + (26,))
    elif fx == "ufo":
        for k, (ux, uy, us) in enumerate(((sx - 80, 330, 1.0), (240, 190, 0.45), (900, 250, 0.4))):
            uy += 12 * math.sin(t * 1.3 + k)
            if k == 0:
                d.polygon([(ux - 28 * us, uy + 38 * us), (ux + 28 * us, uy + 38 * us), (ux + 330 * us, GROUND - 40), (ux - 330 * us, GROUND - 40)],
                          fill=(150, 255, 190, int(40 + 14 * math.sin(t * 9))))
            d.ellipse((ux - 140 * us, uy - 30 * us, ux + 140 * us, uy + 44 * us), fill=(60, 70, 90, 255), outline=(150, 170, 200, 255), width=3)
            d.ellipse((ux - 62 * us, uy - 74 * us, ux + 62 * us, uy + 10 * us), fill=(120, 220, 255, 190))
            for i in range(7):
                on = (int(t * 6) + i) % 3 == 0
                lx = ux - 110 * us + i * 36.6 * us
                d.ellipse((lx - 7 * us, uy + 8 * us, lx + 7 * us, uy + 22 * us), fill=(255, 235, 120, 255) if on else (120, 110, 70, 255))
    elif fx == "asteroid":
        ph = (t * 0.22) % 1.0
        x, y = 140 + 760 * ph, 90 + 760 * ph
        for k in range(26):
            d.ellipse((x - k * 26 - 20, y - k * 26 - 20, x - k * 26 + 20, y - k * 26 + 20), fill=(255, 150 - k * 4, 40, max(0, 190 - k * 8)))
        for r, a in ((70, 50), (44, 120), (24, 255)):
            d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 220, 140, a))
    elif fx == "redsky":
        for i in range(9):  # crows streaming across the red sky
            bx = (i * 190 - t * 120) % (W + 200) - 100
            by = 240 + (i * 83) % 360 + 20 * math.sin(t * 3 + i)
            wing = 18 * math.sin(t * 9 + i)
            d.line((bx - 26, by - wing, bx, by, bx + 26, by - wing), fill=(6, 4, 6, 255), width=5)
    elif fx == "thunder":
        ph = (t * 0.5) % 1.0
        if ph < 0.05:
            x = 300 + 440 * ((int(t * 0.5) * 0.37) % 1)
            pts, y = [(x, 0)], 0
            while y < GROUND - 100:
                y += 90
                x += random.Random(int(t * 30) + y).randint(-60, 60)
                pts.append((x, y))
            d.line(pts, fill=(235, 240, 255, 255), width=8)
            d.line(pts, fill=(160, 190, 255, 120), width=22)


def lightning_bolt(d, x, k, seed=1):
    """A branching bolt that grows down the sky as k goes from 0 to 1."""
    rnd = random.Random(seed)
    pts, y, cx = [(x, 0)], 0, x
    ymax = int((GROUND - 60) * min(1, k * 1.6))
    while y < ymax:
        y += 80
        cx += rnd.randint(-70, 70)
        pts.append((cx, y))
        if rnd.random() < 0.4:
            d.line([(cx, y), (cx + rnd.randint(-160, 160), y + 120)], fill=(200, 215, 255, 220), width=5)
    d.line(pts, fill=(160, 190, 255, 120), width=26)
    d.line(pts, fill=(245, 248, 255, 255), width=9)


class World:
    PW = 2160  # the repeating width of the scrolling layers

    def __init__(self, place, seed=0, weather=None):
        weather = weather or {}
        self.st = dict(PLACES.get(place, DEFAULT_PLACE))
        self.fog = weather.get("fog", self.st.get("fog", 0.0))
        self.storm = weather.get("storm", self.st.get("storm", False))
        self.push, self.shake, self.tilt, self.focus_y = 0.0, 0.0, 0.0, 0.62
        self.skyfx = weather.get("sky")
        fx = self.skyfx
        sr = 85
        if fx == "fullmoon":
            self.st["sun"], sr = "#f6f1d6", 125
        elif fx == "bluemoon":
            self.st["sun"], sr = "#79b2ff", 120
        elif fx == "eclipse":
            self.st["sun"], sr = "eclipse", 100
            self.st["sky"] = ("#05050c", "#26121c")
        elif fx == "redsky":
            self.st["sky"] = ("#2a0606", "#8a1f12")
        elif fx == "virus":
            self.st["sky"] = ("#0c1608", "#4a6a2a")
            self.fog = max(self.fog, 0.75)
        elif fx == "thunder":
            self.st["part"], self.storm = "rain", True
        if weather.get("redmoon") and self.st.get("sun") not in (None, "eclipse"):
            self.st["sun"] = "#ff4a3a"
        if fx in ("fullmoon", "bluemoon", "eclipse", "meteors", "aurora", "comet", "ufo", "asteroid", "tomb"):
            self.st["stars"] = True
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
        elif sun == "eclipse":
            for k, a in ((3.4, 0.10), (2.4, 0.20), (1.6, 0.4), (1.25, 0.7)):
                r = sr * k
                d.ellipse((sx - r, 520 - r, sx + r, 520 + r), fill=mix(st["sky"][0], "#d8281c", a))
            d.ellipse((sx - sr, 520 - sr, sx + sr, 520 + sr), fill=(4, 3, 8))
        elif sun:
            c = sun
            for k, a in ((3.0, 0.08), (2.2, 0.14), (1.5, 0.25)):
                r = sr * 0.94 * k
                d.ellipse((sx - r, 520 - r, sx + r, 520 + r), fill=mix(st["sky"][0], c, a))
            d.ellipse((sx - sr, 520 - sr, sx + sr, 520 + sr), fill=rgb(c))
            if fx in ("fullmoon", "bluemoon"):  # craters
                for ox, oy, rr in ((-40, -30, 22), (30, 20, 30), (-10, 55, 16), (45, -45, 14)):
                    d.ellipse((sx + ox - rr, 520 + oy - rr, sx + ox + rr, 520 + oy + rr), fill=mix(c, st["sky"][0], 0.12))
        self.sun_xy = (sx, 520, sr)
        if st.get("stars") or st["sky"][0] in ("#05060f", "#000000", "#2f3e25", "#3a0d0d", "#0a1030"):
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
            elif sil == "dead":  # bare, twisted trees
                h = 300 * s
                d.polygon([(x - 12 * s, hy + 8), (x - 5 * s, hy - h), (x + 5 * s, hy - h), (x + 14 * s, hy + 8)], fill=col)
                for k in range(6):
                    sgn = 1 if k % 2 else -1
                    by = hy - h * (0.35 + 0.1 * k)
                    d.line((x, by, x + sgn * 80 * s, by - 55 * s), fill=col, width=max(3, int(8 * s - k)))
                    d.line((x + sgn * 80 * s, by - 55 * s, x + sgn * 120 * s, by - 100 * s), fill=col, width=max(2, int(5 * s)))
            elif sil == "pines":  # dense dark pines
                h = 400 * s
                for k in range(5):
                    w = (100 - k * 14) * s
                    yy = hy - h * (0.2 + 0.18 * k)
                    shapes.append([(x - w, yy + 70 * s), (x, yy - 90 * s), (x + w, yy + 70 * s)])
                d.rectangle((x - 8 * s, hy - 60 * s, x + 8 * s, hy + 8), fill=col)
            elif sil == "graves":
                for k in range(3):
                    gx = x + (k - 1) * 70
                    gh = rnd.uniform(60, 120) * s
                    if rnd.random() < 0.5:
                        d.rectangle((gx - 7, hy - gh, gx + 7, hy + 6), fill=col)
                        d.rectangle((gx - 30 * s, hy - gh * 0.8, gx + 30 * s, hy - gh * 0.8 + 14), fill=col)
                    else:
                        d.rounded_rectangle((gx - 30 * s, hy - gh, gx + 30 * s, hy + 6), radius=24, fill=col)
            elif sil == "wheat":
                for k in range(9):
                    d.line((x + k * 9, hy + 6, x + k * 9 + rnd.uniform(-8, 8), hy - rnd.uniform(60, 120) * s), fill=col, width=4)
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
        if self.skyfx:
            sky_effects(im, self.skyfx, t, getattr(self, "sun_xy", (700, 520, 85)))
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
            elif p == "leaves":
                xx = (q[0] - cam * 0.9 - t * 120 * q[2]) % W
                yy = (q[1] * 0.5 + 700 + 40 * math.sin(t * 2 + q[3]) + t * 20) % 1000 + 600
                d.ellipse((xx, yy, xx + 12 * q[2], yy + 6 * q[2]), fill=(120, 80, 40, 190))
            else:
                d.ellipse((x, y, x + 4 * q[2], y + 3 * q[2]), fill=(255, 230, 200, 80))
        if self.fog > 0:  # low drifting fog banks
            for i in range(9):
                fx = (i * 260 - cam * 0.5 - t * 18 * (1 + i % 3)) % (W + 600) - 300
                fy = GROUND - 140 + (i % 4) * 55 + 12 * math.sin(t * 0.7 + i)
                d.ellipse((fx - 330, fy - 70, fx + 330, fy + 70), fill=((150, 220, 110, int(40 * self.fog)) if self.skyfx == "virus" else (190, 205, 225, int(34 * self.fog))))
        if self.storm:
            ph = (t * 0.45) % 1.0
            f = 0.0
            if ph < 0.03:
                f = 0.6
            elif 0.08 < ph < 0.10:
                f = 0.35
            if f:
                im = Image.blend(im.convert("RGB"), Image.new("RGB", im.size, (225, 232, 255)), f)
        return im


# ---------------------------------------------------------------- video writer
def CINEMA_ON():
    return globals().get("CINEMA", {}).get("on", False)


def write_video(out, dur, painter, world, cwd=None, cam_dir=1, cam_fn=None):
    """painter(draw, t, u, cam) draws the actors. Frames are piped straight into ffmpeg."""
    n = max(int(dur * FPS), 2)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{OW}x{OH}",
           "-r", str(FPS), "-i", "-", "-vf", "scale=1080:1920:flags=bicubic", "-c:v", "libx264",
           "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-an", out]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, cwd=cwd)
    _LIGHT_WORLD[0] = world
    track = []
    EMERGE.update(dur=float(getattr(world, "emerge", 0.0) or 0.0), t0=None, last=-9.0, t=0.0)
    try:
        for i in range(n):
            t = i / FPS
            EMERGE["t"] = t
            u = i / max(n - 1, 1)
            cam = cam_fn(t) if cam_fn else t * 260 * cam_dir   # cam_fn: the ground can slow down and stop
            im = world.frame(cam, t)
            d = ImageDraw.Draw(im, "RGBA")
            _BOXES.clear()
            ov = painter(im, d, t, u, cam)
            if ov is not None:
                im = ov
            if CINEMA_ON() and not getattr(world, "no_cinema", False):
                im = world.cinema_front(im, cam, t)
            box = None
            if _BOXES:
                box = [min(b[0] for b in _BOXES), min(b[1] for b in _BOXES), max(b[2] for b in _BOXES), max(b[3] for b in _BOXES)]
            push, shake = getattr(world, "push", 0.0), getattr(world, "shake", 0.0)
            pull = getattr(world, "pull", 0.0)  # a pull-back: the camera starts close and drifts away
            if push or shake or pull:
                z = 1.0 + push * (u * u * (3 - 2 * u)) + pull * (1 - u * u * (3 - 2 * u)) + (0.05 if shake else 0.0)
                cw, ch = W / z, H / z
                cx = W / 2 + shake * math.sin(t * 61) * 1.0
                cy = min(max(H * getattr(world, "focus_y", 0.62) + shake * math.cos(t * 53), ch / 2), H - ch / 2)
                cx = min(max(cx, cw / 2), W - cw / 2)
                if box:  # where that box ends up after the zoom
                    ax, ay = cx - cw / 2, cy - ch / 2
                    box = [(box[0] - ax) * z, (box[1] - ay) * z, (box[2] - ax) * z, (box[3] - ay) * z]
                im = im.crop((int(cx - cw / 2), int(cy - ch / 2), int(cx + cw / 2), int(cy + ch / 2)))
            track.append((t, box))
            tilt = getattr(world, "tilt", 0.0)
            if tilt:
                a = math.radians(abs(tilt))
                k = 1.0 / (math.cos(a) + (im.size[1] / im.size[0]) * math.sin(a))
                im = im.rotate(tilt, resample=Image.BICUBIC)
                w2, h2 = im.size
                im = im.crop((int(w2 * (1 - k) / 2), int(h2 * (1 - k) / 2), int(w2 * (1 + k) / 2), int(h2 * (1 + k) / 2)))
            proc.stdin.write(im.resize((OW, OH), Image.LANCZOS).tobytes())
    finally:
        EMERGE["dur"] = 0.0
        proc.stdin.close()
        rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"toon render failed (ffmpeg code {rc})")
    LAST_TRACK[:] = track


STEP = 1.9 * 2 * math.pi


def walk_scene(out, dur, world, sp, facing=1, zoom=(1.0, 1.0), hybrid_roar_end=False, cwd=None,
               intro=None, outro=None):
    """One creature walking across the scene. intro/outro = (creature, seconds): a close-up of
    that creature's face at the start or at the end of the shot."""
    def paint(im, d, t, u, cam):
        if intro and t < intro[1]:
            closeup(im, d, intro[0], t, intro[1], 3.0, 2.5, roar=0.7, shake=5, tt=t)
            return
        if outro and t > dur - outro[1]:
            closeup(im, d, outro[0], t - (dur - outro[1]), outro[1], 2.5, 3.0, roar=0.7, shake=3, tt=t)
            return
        z = zoom[0] + (zoom[1] - zoom[0]) * u
        roar = 0.0
        if hybrid_roar_end and u > 0.72:
            roar = min(1.0, (u - 0.72) / 0.1) * (1 if u < 0.95 else (1 - (u - 0.95) / 0.05))
        sh = (3 * math.sin(t * 70)) if roar > 0.1 else 0
        draw_creature(d, sp, (400 if facing == 1 else 680) + sh, GROUND + 20, t * STEP * 0.5,
                      facing, z, {"t": t, "roar": roar, "moving": True})
    cf = None
    if sp["kind"] == "chimera":
        cf = (lambda t, g0=chim_ground(sp): t * g0 * facing)
    write_video(out, dur, paint, world, cwd, cam_dir=facing, cam_fn=cf)


def face_scene(out, dur, world, sp, cwd=None):
    """A close-up of the face with a roar."""
    def paint(im, d, t, u, cam):
        closeup(im, d, sp, t, dur, 1.9, 3.0, roar=min(1.0, u * 2.2), shake=4 + 8 * u, tt=t)
    write_video(out, dur, paint, world, cwd, cam_dir=0.2)


def hunt_scene(out, dur, world, sp, prey_text="", cwd=None):
    """The hybrid stalks, snaps into a close-up of its face (the eerie sound plays here), and then
    immediately goes after its prey: it crouches, pounces and roars."""
    prey = prey_spec(prey_text)
    zt = min(1.5, dur * 0.3)          # length of the stalk + zoom part
    pre = zt * 0.35                    # the stalk before the snap zoom
    rest = max(dur - zt, 0.5)

    def paint(im, d, t, u0, cam):
        if t < pre:
            draw_prey(d, 700, GROUND + 14, t * STEP * 0.5, 1, prey)
            draw_creature(d, sp, 260, GROUND + 20, t * STEP * 0.5, 1, 1.0, {"t": t, "moving": True})
            return
        if t < zt:
            closeup(im, d, sp, t - pre, zt - pre, 1.2, 3.0, roar=min(1.0, (t - pre) / (zt - pre) * 1.6),
                    shake=12 * (1 - (t - pre) / (zt - pre)) + 3, tt=t)
            return
        u = (t - zt) / rest
        crouch = roar = lunge = flash = 0.0
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
        px = 620 + 160 * min(u / 0.58, 1) if u < 0.6 else 780
        sx = sy = 0
        if 0.66 < u < 0.74:
            flash = 0.6 * (1 - (u - 0.66) / 0.08)
            sx, sy = 14 * math.sin(t * 80), 10 * math.cos(t * 73)
        if u < 0.68:
            draw_prey(d, px + sx, GROUND + 14, t * STEP * 0.5, 1, prey)
        else:
            for i in range(10):
                a = i * 0.63 + t * 2
                r = 30 + 160 * min(1, (u - 0.68) / 0.12)
                d.ellipse((780 + math.cos(a) * r - 20, GROUND - 30 + math.sin(a) * r * 0.4 - 20,
                           780 + math.cos(a) * r + 20, GROUND - 30 + math.sin(a) * r * 0.4 + 20), fill=(220, 200, 170))
            if u < 0.9:
                blood_burst(d, 790, GROUND - 70, (u - 0.68) / 0.22, seed=3, power=0.6)
        draw_creature(d, sp, hx + sx, GROUND + 20 + sy, t * STEP * 0.5, 1, 1.0 + 0.1 * lunge,
                      {"t": t, "crouch": crouch, "roar": roar, "moving": moving, "headup": roar, "lunge": lunge})
        if flash > 0:
            return Image.blend(im, Image.new("RGB", im.size, (255, 255, 255)), flash)
    write_video(out, dur, paint, world, cwd, cam_dir=1)
    return {"zoom_at": pre, "lunge_at": zt + rest * 0.58}


def meet_scene(out, dur, world, sa, sb, cwd=None):
    """Both parent animals walk toward each other and overlap. The morph into the hybrid follows."""
    def paint(im, d, t, u, cam):
        ph = t * STEP * 0.5
        ax, bx = 250 + 230 * u, 830 - 230 * u
        draw_creature(d, sa, ax, GROUND + 20, ph, 1, 0.9, {"t": t, "moving": True})
        draw_creature(d, sb, bx, GROUND + 20, ph + 1.5, -1, 0.9, {"t": t, "moving": True})
    write_video(out, dur, paint, world, cwd, cam_dir=0.3)


# ---------------------------------------------------------------- folklore: a person and the creature that follows
PALETTES = {"ash": (92, 90, 100), "bone": (150, 142, 124), "rust": (130, 58, 42), "green": (52, 92, 62),
            "blue": (48, 66, 128), "violet": (96, 54, 128), "charcoal": (46, 44, 56), "yellow": (140, 120, 46),
            "crimson": (128, 24, 36), "teal": (34, 110, 112)}
EYES = {"red": (255, 40, 30), "gold": (255, 190, 40), "green": (90, 255, 110), "white": (235, 245, 255),
        "violet": (200, 110, 255), "orange": (255, 130, 30), "blue": (90, 190, 255)}
BODIES = ("beast", "stalker", "arachnid", "serpent", "wraith", "owlman", "crawler", "djinn", "hollow", "theropod", "dragon")
HORNS = (None, "antlers", "ram", "twin", "bull")


def folk_creature(name, design=None):
    """A folklore monster. `design` picks its body shape, colour, eyes and features so that every legend
    gets its own unique monster: {"body", "palette", "eyes", "size", "horn", "spine", "eye_count"}."""
    design = design or {}
    sp = animal_spec(name)
    body = design.get("body") if design.get("body") in BODIES else "beast"
    sp["kind"] = body
    sp["snout"] = SNOUTS.get(name, 0.9)
    sp["hybrid"] = True
    sp["horror"] = True
    sp["eye"] = EYES.get(design.get("eyes"), (255, 40, 30))
    sp["scale"] = {"small": 0.85, "huge": 1.2}.get(design.get("size"), 1.05 if body == "beast" else 1.0)
    pal = PALETTES.get(design.get("palette"))
    sp["color"] = mix(pal, (10, 8, 18), 0.45) if pal else mix(sp["color"], (10, 8, 18), 0.78)
    if body == "beast":
        sp["horn"] = design.get("horn") if design.get("horn") in HORNS else None
        sp["spine"] = design.get("spine") if design.get("spine") in ("bone", "none", "long") else "bone"
        try:
            sp["eyes"] = max(2, min(6, int(design.get("eye_count", 3))))
        except Exception:
            sp["eyes"] = 3
    if body in ("theropod", "dragon"):
        sp["cracks"] = bool(design.get("cracks"))
        sp["horns"] = body == "dragon" or bool(design.get("cracks"))
        sp["spines"] = True
        sp["eye_count"] = 2
        sp["scale"] = {"small": 0.9, "huge": 1.25}.get(design.get("size"), 1.1)
    sp["traits"] = set(sp["traits"]) | {"fangs", "glow_eyes"}
    sp["traits"] -= {"flying", "walker"}
    return sp


CAST = {"kind": None, "role": None}   # companion: child, elder, pair, dog.  role: how the main person looks
COMPANION = {"child": 0.6, "elder": 0.88, "pair": 0.96, "dog": 0.5}
ROLES = ("traveller", "farmer", "hunter", "priest", "shepherd", "doctor", "soldier", "girl", "worker")


def role_gear(pen, role, bob, phase):
    """What makes this person recognisable: hats, robes, a white coat, a dress."""
    c = (8, 10, 20)
    rim = (80, 100, 150)
    if role == "farmer":  # straw hat
        pen.ell(0, -398 + bob, 62, 9, (150, 125, 60), rim=False)
        pen.poly([(-30, -398 + bob), (-24, -438 + bob), (24, -438 + bob), (30, -398 + bob)], (150, 125, 60), rim=False)
    elif role == "hunter":  # wide hat, long cloak, a bow on the back
        pen.poly([(-52, -396 + bob), (-30, -430 + bob), (30, -430 + bob), (52, -396 + bob), (30, -388 + bob)], (30, 26, 22), rim=False)
        pen.poly([(-34, -330 + bob), (-70 - 14 * math.sin(phase), -200 + bob), (-10, -190 + bob), (30, -330 + bob)], (22, 28, 22))
        pen.line([(-20, -330 + bob), (-44, -420 + bob), (-14, -300 + bob)], 5, (110, 80, 50))
    elif role == "priest":  # a long dark robe, a hood, a white collar, a cross
        pen.poly([(-44, -300 + bob), (44, -300 + bob), (62, -8), (-62, -8)], (12, 12, 18))
        pen.poly([(-40, -372 + bob), (0, -430 + bob), (40, -372 + bob), (30, -330 + bob), (-30, -330 + bob)], (12, 12, 18))
        pen.line([(0, -320 + bob), (0, -270 + bob)], 5, (235, 235, 225))
        pen.line([(-14, -300 + bob), (14, -300 + bob)], 5, (235, 235, 225))
    elif role == "shepherd":  # a shawl and a long staff
        pen.poly([(-44, -330 + bob), (44, -330 + bob), (60, -230 + bob), (-30, -250 + bob)], (70, 40, 40), rim=False)
        pen.line([(48, 0), (52, -400)], 7, (95, 70, 45))
        pen.line([(52, -400), (70, -420), (82, -400)], 7, (95, 70, 45))
    elif role == "doctor":  # a long white coat, a mask and a head mirror
        pen.poly([(-40, -300 + bob), (40, -300 + bob), (54, -80), (-54, -80)], (205, 210, 222))
        pen.poly([(-38, -372 + bob), (38, -372 + bob), (34, -352 + bob), (-34, -352 + bob)], (200, 220, 230), rim=False)
        pen.ell(0, -395 + bob, 12, 12, (235, 240, 245), rim=False)
    elif role == "soldier":  # a helmet and shoulder straps
        pen.poly([(-40, -378 + bob), (-36, -420 + bob), (0, -440 + bob), (36, -420 + bob), (40, -378 + bob)], (58, 70, 52))
        pen.line([(-30, -320 + bob), (30, -250 + bob)], 8, (58, 70, 52))
    elif role == "girl":  # a dress, two plaits
        pen.poly([(-34, -290 + bob), (34, -290 + bob), (66, -70), (-66, -70)], (150, 52, 60))
        pen.line([(-30, -380 + bob), (-44, -330 + bob), (-40, -300 + bob)], 9, c)
        pen.line([(30, -380 + bob), (44, -330 + bob), (40, -300 + bob)], 9, c)
    elif role == "worker":  # a yellow hard hat
        pen.poly([(-40, -380 + bob), (-34, -424 + bob), (34, -424 + bob), (44, -380 + bob)], (235, 190, 40), rim=False)
        pen.ell(0, -382 + bob, 48, 8, (235, 190, 40), rim=False)


def draw_dog(d, cx, gy, phase, facing=1, scale=1.0):
    pen = Pen(d, cx, gy, 1.35 * scale, facing, lambda c: (80, 100, 150))
    col = (14, 14, 22)
    bob = -abs(math.sin(phase * 2)) * 6
    limb(pen, 60, -105 + bob, phase * 2 + math.pi, 55, 55, 16, col, 0.7)
    limb(pen, -60, -105 + bob, phase * 2, 55, 55, 16, col, 0.7)
    pen.ell(0, -135 + bob, 85, 38, col)
    limb(pen, 62, -108 + bob, phase * 2, 55, 55, 16, col, 0.7)
    limb(pen, -58, -108 + bob, phase * 2 + math.pi, 55, 55, 16, col, 0.7)
    pen.line([(-80, -145 + bob), (-120, -190 + bob + 12 * math.sin(phase * 3))], 10, col)
    pen.ell(110, -175 + bob, 32, 28, col)
    pen.poly([(130, -170 + bob), (175, -160 + bob), (132, -150 + bob)], col, rim=False)
    pen.poly([(98, -196 + bob), (92, -240 + bob), (118, -200 + bob)], col, rim=False)
    pen.dot(118, -180 + bob, 4, (255, 255, 235))


def draw_companion(d, cx, gy, phase, facing=1, scale=1.0):
    kind = CAST["kind"]
    if not kind:
        return
    cs = COMPANION[kind]
    if kind == "dog":
        draw_dog(d, cx - facing * 20, gy, phase, facing, scale)
        return
    draw_human(d, cx, gy, phase * (1.6 if kind == "child" else 1.0) + 1.3, facing, scale * cs, lamp=None, cast=False)
    if kind == "elder":  # a walking stick
        s = 1.35 * scale * cs
        d.line((cx + facing * 52 * s, gy, cx + facing * 70 * s, gy - 300 * s), fill=(40, 28, 20), width=max(4, int(8 * s)))


def draw_human(d, cx, gy, phase, facing=1, scale=1.0, lamp="lantern", cast=True, role="auto"):
    """A flat silhouette of a person walking with a lantern. With cast=True, a companion walks just ahead."""
    if cast and CAST["kind"] and lamp == "lantern":
        draw_companion(d, cx + facing * 170 * scale, gy, phase, facing, scale)
    s = 1.35 * scale
    pen = Pen(d, cx, gy, s, facing, lambda c: (80, 100, 150))
    col = (8, 10, 20)
    shadow(d, cx, gy, 90 * s)
    bob = -abs(math.sin(phase)) * 6
    limb(pen, -6, -165 + bob, phase + math.pi, 85, 85, 24, col, 0.5)
    limb(pen, 6, -165 + bob, phase, 85, 85, 26, col, 0.5)
    pen.poly([(-38, -170 + bob), (38, -170 + bob), (30, -330 + bob), (-30, -330 + bob)], col)
    sw = math.sin(phase)
    pen.line([(0, -315 + bob), (30 * sw, -240 + bob), (45 * sw, -175 + bob)], 18, col)
    ax, ay = 20 + 30 * -sw, -245 + bob
    pen.line([(0, -315 + bob), (ax, ay), (ax + 22, ay + 70)], 18, col)
    if lamp:
        big = 1.9 if lamp == "torch" else 1.0
        for k, a in ((3.2, 26), (2.2, 40), (1.4, 70)):
            r = 22 * k * s * big
            d.ellipse((pen.X(ax + 22) - r, pen.Y(ay + 78) - r, pen.X(ax + 22) + r, pen.Y(ay + 78) + r), fill=(255, 170, 70, a))
        pen.ell(ax + 22, ay + 78, 12 * big, 15 * big, (255, 215, 130), rim=False)
        if lamp == "torch":
            pen.line([(ax + 22, ay + 90), (ax + 24, ay + 150)], 8, (60, 40, 25))
    pen.ell(0, -372 + bob, 36, 40, col)
    if role == "auto":
        role = CAST.get("role") if lamp == "lantern" else None
    if role and role != "traveller":
        role_gear(pen, role, bob, phase)


def folk_scene(out, dur, world, sp, gap0, gap1, intro=None, outro=None, roar_end=False, cwd=None):
    """A person walks through the night and the creature follows, closer and closer.
    gap = how many pixels behind the person the creature is (large means it is off-screen)."""
    def paint(im, d, t, u, cam):
        if intro and t < intro[1]:
            closeup(im, d, intro[0], t, intro[1], 3.0, 2.5, roar=0.7, shake=5, tt=t)
            return
        if outro and t > dur - outro[1]:
            closeup(im, d, outro[0], t - (dur - outro[1]), outro[1], 2.5, 3.0, roar=0.8, shake=4, tt=t)
            return
        gap = gap0 + (gap1 - gap0) * u
        ph = t * STEP * 0.5
        hx = 700
        roar = 0.0
        if roar_end and u > 0.7:
            roar = min(1.0, (u - 0.7) / 0.1)
        cx = hx - gap
        if cx > -420:
            draw_creature(d, sp, cx, GROUND + 20, ph + 0.7, 1, 1.0, {"t": t, "moving": True, "roar": roar})
        draw_human(d, hx, GROUND + 20, ph, 1, 1.0)
    write_video(out, dur, paint, world, cwd, cam_dir=1)


# ---------------------------------------------------------------- blood and horror effects (stylized, not anatomical)
def blood_burst(d, cx, cy, k, seed=1, power=1.0):
    """Red droplets flying out from a hit. k goes from 0 (the hit) to 1 (everything has landed)."""
    rnd = random.Random(seed)
    if k < 0.3:  # the big splat right at the hit
        pts = []
        for i in range(18):
            a = i * math.pi * 2 / 18
            r = (90 + rnd.uniform(-30, 120)) * (0.4 + 2 * k) * power
            pts.append((cx + math.cos(a) * r, cy + math.sin(a) * r * 0.8))
        d.polygon(pts, fill=(140, 6, 10, 230))
    for i in range(54):
        a = rnd.uniform(-math.pi * 0.98, -math.pi * 0.02) + rnd.uniform(-0.4, 0.4)
        v = rnd.uniform(200, 820) * power
        r = rnd.uniform(5, 19) * (1 - 0.4 * k)
        x0, y0 = cx + math.cos(a) * v * k * 0.8, cy + math.sin(a) * v * k * 0.8 + 700 * (k * 0.8) ** 2
        x, y = cx + math.cos(a) * v * k, cy + math.sin(a) * v * k + 700 * k * k
        if k < 0.7:
            d.line((x0, y0, x, y), fill=(120, 5, 9, 210), width=max(2, int(r * 0.8)))
        d.ellipse((x - r, y - r * 1.2, x + r, y + r * 1.2), fill=(150, 8, 12, 240))


def blood_pool(d, cx, gy, k, wide=260):
    w = wide * min(1, k)
    d.ellipse((cx - w, gy - 6, cx + w, gy + 34 * min(1, k) + 6), fill=(92, 4, 8, 240))
    d.ellipse((cx - w * 0.7, gy - 2, cx + w * 0.5, gy + 12), fill=(150, 10, 16, 200))


def blood_trail(d, x0, x1, gy, k):
    """A smear on the ground where something was dragged away."""
    xe = x0 + (x1 - x0) * min(1, k)
    for i in range(5):
        off = i * 9 - 18
        d.line((x0, gy + 14 + off, xe, gy + 14 + off * 0.6), fill=(100, 5, 9, 230), width=9)


def lantern_on_ground(d, x, gy, t, alive=1.0):
    fl = 0.6 + 0.4 * math.sin(t * 17) * math.sin(t * 5.3)
    for kk, a in ((5.0, 22), (3.4, 38), (2.0, 70)):
        r = 22 * kk * (0.6 + 0.4 * fl) * alive
        d.ellipse((x - r, gy - 24 - r * 0.6, x + r, gy - 24 + r * 0.6), fill=(255, 150, 60, int(a * alive)))
    d.rectangle((x - 16, gy - 46, x + 16, gy - 8), fill=(30, 22, 18))
    d.rectangle((x - 10, gy - 40, x + 10, gy - 14), fill=(255, int(190 * fl + 40), 80))


def red_wash(im, amount):
    return Image.blend(im, Image.new("RGB", im.size, (140, 0, 0)), max(0.0, min(0.7, amount)))


def claw_slash(d, cx, cy, k, size=1.0):
    """Three parallel diagonal claw strokes, white-hot at first and then red."""
    k = max(0.0, min(1.0, k))
    for j in range(3):
        x0, y0 = cx - 150 * size + j * 70 * size, cy - 230 * size
        x1, y1 = x0 + 190 * size * min(1, k * 2.2), y0 + 460 * size * min(1, k * 2.2)
        col = (255, 245, 235, 255) if k < 0.3 else (190, 10, 16, int(255 * (1 - (k - 0.3) / 0.9)))
        d.line((x0, y0, x1, y1), fill=col, width=int(26 * size * (1.2 - k)))


def attack_scene(out, dur, world, sp, cwd=None):
    """The creature closes in. The person turns, sees it and RUNS. It gallops after them, gains on them, leaps,
    and rakes its claws across their back: a flash, a spray of blood, and the person is gone. Only the lantern
    is left, dropped on a growing pool of blood. A companion, if there is one, keeps running and escapes."""
    sp2 = dict(sp)
    sp2["bloody"] = False
    sp3 = dict(sp)
    sp3["bloody"] = True
    t_walk, t_run, t_leap, t_hit = 0.22, 0.32, 0.58, 0.66
    info = {"impact_at": dur * t_hit, "stalk_at": dur * t_walk}
    world.shake = 0.0
    hx = 760

    def paint(im, d, t, u, cam):
        ph = t * STEP * 0.5
        if u < t_hit:
            comp_x = hx + 170 + max(0.0, u - t_walk) * 1900
            running = u >= t_run
            pph = t * STEP * (1.0 if running else 0.5)
            if u < t_walk:
                gap = 960 - 280 * (u / t_walk)
            elif u < t_run:  # it is right there: the person freezes and looks back
                gap = 680 - 60 * ((u - t_walk) / (t_run - t_walk))
            elif u < t_leap:  # the chase: the gap closes while everything speeds past
                gap = 620 - 440 * ((u - t_run) / (t_leap - t_run))
            else:
                gap = 180 - 70 * ((u - t_leap) / (t_hit - t_leap))
            lift = 0.0
            if u >= t_leap:
                kk = (u - t_leap) / (t_hit - t_leap)
                lift = 190 * math.sin(math.pi * min(1, kk))
            jx = 6 * math.sin(t * 61) if running else 0
            draw_companion(d, comp_x, GROUND + 20, pph, 1, 1.0)
            draw_creature(d, sp2, hx - gap, GROUND + 20 - lift, ph * (2.0 if running else 1.0) + 0.7, 1, 1.0 + 0.08 * (u > t_leap),
                          {"t": t, "moving": True, "roar": 0.9 if u > t_run else 0.0})
            if u < t_walk:
                draw_human(d, hx, GROUND + 20, ph, 1, 1.0, cast=False)
            elif u < t_run:
                draw_human(d, hx + 3 * math.sin(t * 40), GROUND + 20, 0.0, 1, 1.0, cast=False)
            else:
                draw_human(d, hx + jx, GROUND + 20, pph, 1, 1.0, cast=False)
            if u >= t_leap:  # the claws rake across the person's back
                claw_slash(d, hx + 10, GROUND - 200, (u - t_leap) / (t_hit - t_leap) * 0.55)
            if u > t_walk:
                beat = max(0.0, math.sin(t * 9 + 3)) ** 6 * 0.2 * min(1, (u - t_walk) / 0.12)
                return red_wash(im, beat + (0.08 if running else 0.0))
            return
        k = (u - t_hit) / (1 - t_hit)
        if k < 0.07:  # jump-scare: a few frames of the monster's face, blood red, right at the impact
            closeup(im, d, sp3, 0.0, 0.3, 2.3, 2.7, roar=1.0, shake=12, tt=t)
            return red_wash(im, 0.4)
        sx = 16 * math.sin(t * 90) * max(0.0, 1 - k * 4)
        sy = 12 * math.cos(t * 80) * max(0.0, 1 - k * 4)
        blood_pool(d, hx - 20, GROUND + 26, min(1, k * 2.2))
        lantern_on_ground(d, hx + 150, GROUND + 28, t, alive=max(0.0, 1.0 - max(0.0, k - 0.6) * 2.5))
        draw_creature(d, sp3, hx - 330 + sx, GROUND + 20 + sy, ph, 1, 1.05, {"t": t, "moving": False, "roar": min(1, k * 3)})
        blood_burst(d, hx + 20, GROUND - 330, min(1, k * 1.6), seed=7)
        claw_slash(d, hx + 10, GROUND - 200, 0.55 + k * 1.2)
        if k < 0.14:
            return Image.blend(im, Image.new("RGB", im.size, (255, 255, 255)), 0.6 * (1 - (k - 0.07) / 0.07))
        if k < 0.35:
            return red_wash(im, 0.5 * (1 - k / 0.35))
    world.shake = 7.0
    write_video(out, dur, paint, world, cwd, cam_dir=2.2)
    return info


def aftermath_scene(out, dur, world, sp, cwd=None, outro=None):
    """The quiet after: a pool of blood, a long smear where something was dragged away, a flickering lantern,
    and the creature standing over it with red eyes, its fangs dripping."""
    sp3 = dict(sp)
    sp3["bloody"] = True

    def paint(im, d, t, u, cam):
        if outro and t > dur - outro:
            closeup(im, d, sp3, t - (dur - outro), outro, 2.5, 3.0, roar=0.8, shake=4, tt=t)
            return
        blood_pool(d, 520, GROUND + 26, 1.0, wide=300)
        blood_trail(d, 520, 40, GROUND + 26, min(1, u * 2.5))
        lantern_on_ground(d, 690, GROUND + 28, t, alive=max(0.0, 1.0 - max(0.0, u - 0.55) * 2.2))
        z = 1.0 + 0.1 * u
        draw_creature(d, sp3, 760, GROUND + 20, 0.0, -1, z * 0.95, {"t": t, "moving": False, "roar": 0.2 + 0.3 * math.sin(t * 3) ** 2})
        if u > 0.55:
            dim(im, int(150 * min(1, (u - 0.55) / 0.4)))
    write_video(out, dur, paint, world, cwd, cam_dir=0.05)


# ---------------------------------------------------------------- story scenes
def draw_house(d, x, gy, s=1.0, lit=1.0, door=0.0, t=0.0):
    """A small village house in silhouette: a lit window, a door, a chimney with smoke."""
    col, roof = (7, 9, 18), (4, 6, 12)
    w, h = 130 * s, 150 * s
    d.polygon([(x + w * 0.45, gy - h - 60 * s), (x + w * 0.72, gy - h - 60 * s), (x + w * 0.72, gy - h), (x + w * 0.45, gy - h)], fill=roof)
    d.polygon([(x - w, gy), (x - w, gy - h), (x + w, gy - h), (x + w, gy)], fill=col)
    d.polygon([(x - w - 30 * s, gy - h + 8 * s), (x, gy - h - 115 * s), (x + w + 30 * s, gy - h + 8 * s)], fill=roof)
    d.line([(x - w - 30 * s, gy - h + 8 * s), (x, gy - h - 115 * s), (x + w + 30 * s, gy - h + 8 * s)], fill=(60, 80, 130), width=max(2, int(3 * s)))
    wc = mix((7, 9, 18), (255, 200, 110), lit)
    d.rectangle((x - w * 0.72, gy - h * 0.82, x - w * 0.22, gy - h * 0.42), fill=wc)
    if lit > 0.1:
        r = 120 * s * lit
        d.ellipse((x - w * 0.47 - r, gy - h * 0.62 - r, x - w * 0.47 + r, gy - h * 0.62 + r), fill=(255, 190, 90, int(28 * lit)))
    d.line((x - w * 0.47, gy - h * 0.82, x - w * 0.47, gy - h * 0.42), fill=(20, 14, 10), width=max(2, int(4 * s)))
    d.line((x - w * 0.72, gy - h * 0.62, x - w * 0.22, gy - h * 0.62), fill=(20, 14, 10), width=max(2, int(4 * s)))
    dx0, dx1 = x + w * 0.15, x + w * 0.62
    d.rectangle((dx0, gy - h * 0.72, dx1, gy), fill=(26, 18, 14))
    if door > 0:
        d.polygon([(dx0, gy - h * 0.72), (dx1, gy - h * 0.72), (dx1, gy), (dx0, gy)], fill=(255, 196, 110, int(230 * door)))
        d.polygon([(dx0, gy), (dx1, gy), (dx1 + 260 * s * door, gy + 40 * s), (dx0 - 140 * s * door, gy + 40 * s)], fill=(255, 190, 100, int(90 * door)))
    for i in range(5):  # chimney smoke
        k = (t * 0.5 + i * 0.2) % 1.0
        r = (12 + 26 * k) * s
        d.ellipse((x + w * 0.58 + 40 * k * s - r, gy - h - 70 * s - 160 * k * s - r, x + w * 0.58 + 40 * k * s + r, gy - h - 70 * s - 160 * k * s + r),
                  fill=(120, 130, 150, int(50 * (1 - k))))


def home_scene(out, dur, world, sp, cwd=None):
    """The person walks toward their house, the door opens and lets the warm light out, they step in and the
    door closes. Far behind, two red eyes open in the dark."""
    info = {"hits": [dur * 0.62]}

    def paint(im, d, t, u, cam):
        hxs = 780
        draw_house(d, hxs + 80, GROUND + 10, 1.5, lit=1.0, door=0.0 if u < 0.55 else min(1, (u - 0.55) / 0.08) * (1 if u < 0.78 else max(0, 1 - (u - 0.78) / 0.08)), t=t)
        px = 120 + 600 * min(1, u / 0.58)
        if u < 0.58:
            draw_human(d, px, GROUND + 20, t * STEP * 0.5, 1, 0.95)
        elif u < 0.66:
            draw_human(d, px, GROUND + 20, 0.0, 1, 0.95)
        if u > 0.7:
            k = min(1, (u - 0.7) / 0.15)
            for ex in (140, 205):
                for r, a in ((46, 40), (30, 80), (16, 200)):
                    d.ellipse((ex - r * k, GROUND - 210 - r * k * 0.6, ex + r * k, GROUND - 210 + r * k * 0.6), fill=(255, 30, 24, a))
    write_video(out, dur, paint, world, cwd, cam_dir=0.1)
    return info


def village_scene(out, dur, world, sp, cwd=None):
    """A sleepy village. The lights go out one by one while two red eyes watch from the hill."""
    houses = [(150, 0.75), (400, 1.0), (680, 0.85), (930, 1.1)]
    off = [0.30, 0.45, 0.60, 0.74]

    def paint(im, d, t, u, cam):
        for (x, s), o in zip(houses, off):
            lit = 1.0 if u < o else max(0.0, 1 - (u - o) / 0.05)
            draw_house(d, x, GROUND + 8, s * 1.15, lit=lit, t=t)
        k = min(1, u / 0.25)
        for ex in (850, 905):
            for r, a in ((44, 30), (26, 80), (13, 230)):
                d.ellipse((ex - r * k, GROUND - 470 - r * k * 0.6, ex + r * k, GROUND - 470 + r * k * 0.6), fill=(255, 30, 24, a))
    write_video(out, dur, paint, world, cwd, cam_dir=0.15)
    return {"hits": [dur * o for o in off]}


_TOP = {}


def _topdown_texture():
    """A dirt path seen from above, as a tall tile that can scroll."""
    if "tex" in _TOP:
        return _TOP["tex"]
    rnd = random.Random(5)
    img = Image.new("RGB", (W, 1920), (16, 22, 24))
    d = ImageDraw.Draw(img)
    for y in range(0, 1920, 8):
        wob = 28 * math.sin(y * 2 * math.pi / 1920 * 2)
        d.rectangle((300 + wob, y, 780 + wob, y + 8), fill=(44, 38, 34))
    for _ in range(900):
        x, y = rnd.randint(0, W), rnd.randint(0, 1919)
        c = (30, 26, 24) if 300 < x < 780 else (10, 30, 22)
        d.ellipse((x, y, x + rnd.randint(3, 10), y + rnd.randint(3, 8)), fill=c)
    for _ in range(140):
        x, y = rnd.choice([rnd.randint(0, 280), rnd.randint(800, W)]), rnd.randint(0, 1919)
        for k in range(4):
            d.line((x, y, x + (k - 1.5) * 9, y - rnd.randint(30, 60)), fill=(14, 52, 36), width=4)
    _TOP["tex"] = img
    return img


def _boot(d, x, y, flip):
    dx = -6 if flip else 6
    d.ellipse((x - 16, y + 4, x + 16, y + 34), fill=(14, 12, 12, 240))                        # heel
    d.ellipse((x - 20 + dx, y - 44, x + 20 + dx, y + 2), fill=(14, 12, 12, 240))             # sole
    d.ellipse((x - 20 + dx, y - 46, x + 20 + dx, y - 38), fill=(70, 62, 56, 120))


def _print(d, x, y, glow, style="claw"):
    """The monster's track. Every kind of monster leaves a different one."""
    if style == "claw":
        return _claw_print(d, x, y, glow)
    k = (6, 4, 8, 250)
    if glow > 0:
        r = 100 * (0.6 + 0.4 * glow)
        d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 30, 24, int(55 * glow)))
    if style == "hoof":  # long split hooves
        for sx in (-22, 22):
            d.ellipse((x + sx - 15, y - 50, x + sx + 15, y + 50), fill=k)
        d.polygon([(x - 6, y + 40), (x + 6, y + 40), (x, y + 78)], fill=k)
    elif style == "dots":  # a ring of eight small stabs
        for a in range(8):
            ang = a * math.pi / 4
            d.polygon([(x + 28 * math.cos(ang) - 5, y + 28 * math.sin(ang) * 1.3), (x + 28 * math.cos(ang) + 5, y + 28 * math.sin(ang) * 1.3),
                       (x + 70 * math.cos(ang), y + 70 * math.sin(ang) * 1.3)], fill=k)
        d.ellipse((x - 20, y - 24, x + 20, y + 24), fill=k)
    elif style == "bird":  # three long toes forward, one back
        for ang in (-0.5, 0.0, 0.5):
            d.line((x, y, x + math.sin(ang) * 80, y - math.cos(ang) * 80), fill=k, width=16)
            d.polygon([(x + math.sin(ang) * 80 - 8, y - math.cos(ang) * 80), (x + math.sin(ang) * 80 + 8, y - math.cos(ang) * 80),
                       (x + math.sin(ang) * 100, y - math.cos(ang) * 100 - 18)], fill=k)
        d.line((x, y, x, y + 50), fill=k, width=14)
    elif style == "slither":  # a wide wavy trench
        for j in range(-3, 4):
            d.ellipse((x + 40 * math.sin(j * 0.9) - 34, y + j * 30 - 22, x + 40 * math.sin(j * 0.9) + 34, y + j * 30 + 22), fill=(18, 14, 18, 220))
    elif style == "frost":  # no tracks, only a ring of frost where it passed
        for a in range(10):
            ang = a * math.pi / 5
            d.line((x, y, x + 62 * math.cos(ang), y + 62 * math.sin(ang)), fill=(190, 225, 245, 200), width=5)
        d.ellipse((x - 12, y - 12, x + 12, y + 12), fill=(220, 240, 255, 230))


def _claw_print(d, x, y, glow):
    if glow > 0:
        r = 110 * (0.6 + 0.4 * glow)
        d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 30, 24, int(60 * glow)))
    d.ellipse((x - 46, y - 24, x + 46, y + 46), fill=(6, 4, 8, 250))
    for k in range(4):
        tx = x + (k - 1.5) * 34
        ty = y - 54 - 10 * (1 - abs(k - 1.5) / 1.5)
        d.ellipse((tx - 14, ty - 22, tx + 14, ty + 18), fill=(6, 4, 8, 250))
        d.polygon([(tx - 6, ty - 18), (tx + 6, ty - 18), (tx, ty - 62)], fill=(6, 4, 8, 250))


def footprints_scene(out, dur, world, sp, cwd=None):
    """Seen from above: a person's footprints appear on the path, step by step. Then, one by one, huge
    clawed prints stamp down exactly inside them."""
    speed, appear_y, stamp_y = 300, 560, 1260
    delay = (stamp_y - appear_y) / speed
    gap = 0.55
    steps = [(i * gap, 520 if i % 2 == 0 else 580) for i in range(int(dur / gap) + 2)]
    hits = [ti + delay for ti, _ in steps if ti + delay < dur - 0.2]
    tex = _topdown_texture()

    def paint(im, d, t, u, cam):
        off = int((t * speed) % 1920)
        im.paste(tex, (0, off - 1920))
        im.paste(tex, (0, off))
        d2 = ImageDraw.Draw(im, "RGBA")
        for i, (ti, x) in enumerate(steps):
            if t >= ti:
                y = appear_y + (t - ti) * speed
                if y < 2100:
                    _boot(d2, x, y, i % 2 == 0)
        for i, (ti, x) in enumerate(steps):
            tc = ti + delay
            if t >= tc:
                y = stamp_y + (t - tc) * speed
                if y < 2200:
                    _print(d2, x, y, max(0.0, 1 - (t - tc) / 0.6), PRINT_OF.get(sp.get("kind"), "claw"))
                if t - tc < 0.25:
                    for k in range(8):
                        a = k * 0.8
                        rr = 30 + 160 * (t - tc)
                        d2.ellipse((x + math.cos(a) * rr - 14, stamp_y + math.sin(a) * rr * 0.5 - 14, x + math.cos(a) * rr + 14, stamp_y + math.sin(a) * rr * 0.5 + 14), fill=(120, 110, 100, 90))
        # a vignette, darker at the bottom where the thing is coming from
        dim(im, 70)
        if t > dur - 1.0:
            return red_wash(im, 0.25 * ((t - (dur - 1.0)) / 1.0))
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


_WALL = {}


def _wall():
    if "w" in _WALL:
        return _WALL["w"]
    wall = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(wall)
    d.rectangle((0, 0, W, H), fill=(24, 15, 11, 255))
    for x in range(0, W, 90):
        d.line((x, 0, x, H), fill=(14, 9, 7, 255), width=4)
    wx0, wy0, wx1, wy1 = 270, 480, 810, 1180
    d.rectangle((wx0, wy0, wx1, wy1), fill=(0, 0, 0, 0))
    d.rectangle((wx0 - 26, wy0 - 26, wx1 + 26, wy1 + 26), outline=(62, 40, 26, 255), width=30)
    d.line((540, wy0, 540, wy1), fill=(62, 40, 26, 255), width=18)
    d.line((wx0, 830, wx1, 830), fill=(62, 40, 26, 255), width=18)
    d.polygon([(wx0 - 30, wy0 - 40), (wx0 + 90, wy0 - 40), (wx0 + 30, wy1), (wx0 - 30, wy1)], fill=(70, 14, 20, 255))
    d.polygon([(wx1 + 30, wy0 - 40), (wx1 - 90, wy0 - 40), (wx1 - 30, wy1), (wx1 + 30, wy1)], fill=(70, 14, 20, 255))
    d.rectangle((wx0 - 50, wy1 + 20, wx1 + 50, wy1 + 70), fill=(48, 30, 20, 255))
    _WALL["w"] = wall
    return wall


def window_scene(out, dur, world, sp, cwd=None):
    """Inside a house: a candle on the sill, the night outside the window. Two red eyes appear. Claws scrape down
    the glass. A face presses against it, its breath fogging the pane. The glass cracks and the candle dies."""
    wall = _wall()
    info = {"hits": [dur * 0.55, dur * 0.9]}

    def paint(im, d, t, u, cam):
        # the night outside
        if u > 0.15:
            k = min(1, (u - 0.15) / 0.2)
            z = 1.2 + 0.6 * max(0.0, (u - 0.5) / 0.4)
            ax, ay = head_anchor(sp)
            draw_creature(d, sp, 0, 0, t * 2, 1, z, {"t": t, "moving": False, "roar": 0.3 + 0.5 * max(0, u - 0.7)}, focus=(ax, ay, 540 - 20 * (1 - k), 840))
            if u < 0.5:  # at first the head is hidden in the dark and only the eyes show
                dim(im, int(235 * (1 - k * 0.4)))
        else:
            for ex in (480, 600):
                for r, a in ((40, 40), (24, 90), (11, 240)):
                    d.ellipse((ex - r, 820 - r * 0.6, ex + r, 820 + r * 0.6), fill=(255, 30, 24, a))
        im.paste(wall, (0, 0), wall)
        d = ImageDraw.Draw(im, "RGBA")
        if 0.35 < u < 0.9:  # claws scraping down the glass
            k = (u - 0.35) / 0.4
            for j in range(4):
                x = 360 + j * 62
                yy = 560 + min(1, k) * 420
                d.line((x, 560, x + 18, yy), fill=(200, 196, 182, 200), width=6)
                d.polygon([(x + 12, yy - 6), (x + 28, yy - 6), (x + 18, yy + 60)], fill=(235, 228, 205, 235))
        if u > 0.55:  # breath fogging the glass
            f = (0.5 + 0.5 * math.sin(t * 5)) * min(1, (u - 0.55) / 0.15)
            d.ellipse((380, 760, 700, 980), fill=(210, 220, 230, int(70 * f)))
        if u > 0.88:  # the glass cracks
            for a in range(0, 360, 40):
                rr = 380 * min(1, (u - 0.88) / 0.05)
                d.line((540, 830, 540 + math.cos(math.radians(a)) * rr, 830 + math.sin(math.radians(a)) * rr), fill=(235, 240, 245, 220), width=3)
        # the candle on the sill
        alive = 1.0 if u < 0.85 else max(0.0, 1 - (u - 0.85) / 0.05)
        fl = 0.7 + 0.3 * math.sin(t * 19)
        d.rectangle((300, 1120, 330, 1200), fill=(220, 210, 190, 255))
        if alive > 0:
            for r, a in ((200, 22), (120, 40), (50, 120)):
                d.ellipse((315 - r * alive, 1110 - r * alive, 315 + r * alive, 1110 + r * alive), fill=(255, 170, 70, int(a * alive * fl)))
            d.ellipse((305, 1070, 325, 1118), fill=(255, 220, 140, 255))
        if u > 0.85:
            dim(im, int(160 * min(1, (u - 0.85) / 0.1)))
        if u > 0.5:
            sx, sy = 6 * math.sin(t * 60) * min(1, (u - 0.5) * 4), 5 * math.cos(t * 51) * min(1, (u - 0.5) * 4)
            return im.transform(im.size, Image.AFFINE, (1, 0, sx, 0, 1, sy))
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return info


def door_scene(out, dur, world, sp, cwd=None):
    """A locked wooden door with warm light underneath. Something large paces outside, then slams into it.
    Claw marks appear and the planks crack, with a red glow leaking through."""
    knocks = [dur * k for k in (0.25, 0.5, 0.68, 0.85)]

    def paint(im, d, t, u, cam):
        im.paste(Image.new("RGB", im.size, (12, 8, 7)))
        shake = 0.0
        for kt in knocks:
            if 0 < t - kt < 0.3:
                shake = 22 * (1 - (t - kt) / 0.3) * math.sin((t - kt) * 70)
        ox = int(shake)
        d = ImageDraw.Draw(im, "RGBA")
        d.rectangle((190 + ox, 360, 890 + ox, 1520), fill=(44, 28, 20))
        for x in range(190, 890, 70):
            d.line((x + ox, 360, x + ox, 1520), fill=(24, 15, 11), width=5)
        for y in (520, 1300):
            d.rectangle((190 + ox, y, 890 + ox, y + 46), fill=(26, 26, 32))
        d.ellipse((780 + ox, 900, 840 + ox, 960), fill=(120, 120, 130))
        d.rectangle((170, 1520, 910, 1550), fill=(6, 4, 4))
        glow = 0.7 + 0.3 * math.sin(t * 13)
        d.rectangle((190, 1508, 890, 1520), fill=(255, int(170 * glow + 30), 80))
        d.polygon([(190, 1520), (890, 1520), (1000, 1700), (80, 1700)], fill=(255, 170, 70, 40))
        pace = 540 + 230 * math.sin(t * 2.2)
        for fx in (pace - 60, pace + 60):  # shadows of feet in the gap of light
            d.polygon([(fx - 24, 1508), (fx + 24, 1508), (fx + 18, 1520), (fx - 18, 1520)], fill=(4, 2, 2))
        n = sum(1 for kt in knocks if t >= kt)
        for j in range(n):  # claw marks
            x = 300 + j * 140
            for k in range(3):
                d.line((x + k * 22 + ox, 640 + j * 60, x + k * 22 + 30 + ox, 940 + j * 40), fill=(10, 6, 5), width=7)
        if t >= knocks[-1] - 0.05:
            a = min(1, (t - knocks[-1]) / 0.3)
            d.line((540 + ox, 700, 520 + ox, 1100), fill=(255, 40, 30, int(220 * a)), width=8)
            d.line((520 + ox, 1100, 580 + ox, 1400), fill=(255, 40, 30, int(220 * a)), width=8)
            d.polygon([(500 + ox, 700), (580 + ox, 700), (600 + ox, 1400), (480 + ox, 1400)], fill=(255, 30, 24, int(60 * a)))
        if shake:
            dim(im, 40)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": knocks}


def transform_scene(out, dur, world, sp, cwd=None):
    """A person stands under the moon. Their body shakes and twists, a bright flash, and the person is gone:
    the monster stands where they were and roars."""
    info = {"hits": [dur * 0.52]}

    def paint(im, d, t, u, cam):
        im2 = im.copy()
        d2 = ImageDraw.Draw(im2, "RGBA")
        k = 0.0 if u < 0.38 else min(1.0, (u - 0.38) / 0.22)
        jit = 0 if u < 0.28 else 14 * math.sin(t * 70) * min(1, (u - 0.28) / 0.1)
        pulse = 1.0 + (0.1 * math.sin(t * 45) if u > 0.3 else 0)
        draw_human(d, 540 + jit, GROUND + 20, 0.0, 1, 1.15 * pulse, cast=False)
        draw_creature(d2, sp, 380 + jit * 0.4, GROUND + 20, 0.0, 1, 1.0, {"t": t, "moving": False, "roar": min(1, max(0, (u - 0.6) * 3))})
        out_im = Image.blend(im, im2, k)
        if 0.5 < u < 0.56:
            return Image.blend(out_im, Image.new("RGB", im.size, (255, 255, 255)), 0.85 * (1 - (u - 0.5) / 0.06))
        if u > 0.3:
            return red_wash(out_im, 0.18 * math.sin(t * 7) ** 2)
        return out_im
    write_video(out, dur, paint, world, cwd, cam_dir=0.05)
    return info


def chase_scene(out, dur, world, sp, cwd=None):
    """The person runs for their life and the monster gallops after them, the camera shaking."""
    def paint(im, d, t, u, cam):
        ph = t * STEP * 1.05
        jx, jy = 8 * math.sin(t * 61), 6 * math.cos(t * 53)
        gap = 900 - 360 * u
        draw_creature(d, sp, 760 - gap + jx, GROUND + 20 + jy, ph + 1.0, 1, 1.0, {"t": t, "moving": True, "roar": 0.7})
        draw_companion(d, 760 + 170 + jx + 500 * u, GROUND + 20 + jy, ph * 1.1, 1, 1.0)
        draw_human(d, 760 + jx, GROUND + 20 + jy, ph, 1, 1.0, cast=False)
        return red_wash(im, 0.10 + 0.08 * math.sin(t * 11) ** 2)
    write_video(out, dur, paint, world, cwd, cam_dir=2.4)
    return {"hits": []}


def hide_scene(out, dur, world, sp, cwd=None):
    """The person crouches behind a rock, wide-eyed. The monster prowls past, stops, sniffs the air and moves on."""
    def paint(im, d, t, u, cam):
        if u < 0.42:
            cx = -520 + 640 * (u / 0.42)
        elif u < 0.72:
            cx = 120 + 50 * ((u - 0.42) / 0.3)
        else:
            cx = 170 + 1400 * ((u - 0.72) / 0.28) ** 1.6
        sniff = 0.0 if not (0.42 < u < 0.72) else 0.35 + 0.2 * math.sin(t * 6)
        draw_creature(d, sp, cx, GROUND - 10, t * STEP * 0.5, 1, 0.9, {"t": t, "moving": not (0.42 < u < 0.74), "roar": sniff})
        d.polygon([(330, GROUND + 40), (360, GROUND - 190), (470, GROUND - 270), (620, GROUND - 240), (700, GROUND - 120), (720, GROUND + 40)], fill=(5, 7, 14), outline=(50, 70, 120), width=3)
        d.ellipse((470, GROUND - 330, 560, GROUND - 240), fill=(8, 10, 20))
        d.ellipse((488, GROUND - 292, 506, GROUND - 270), fill=(240, 240, 235))
        d.ellipse((524, GROUND - 292, 542, GROUND - 270), fill=(240, 240, 235))
        if 0.42 < u < 0.72:
            return red_wash(im, 0.12 + 0.06 * math.sin(t * 8))
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": [dur * 0.45]}


def mob_scene(out, dur, world, sp, cwd=None):
    """Villagers with torches march out to hunt the monster. They stop dead, and two red eyes look back at them."""
    xs = [150, 300, 450, 600, 750]
    def paint(im, d, t, u, cam):
        stop = u > 0.68
        for i, x in enumerate(xs):
            draw_human(d, x + (0 if not stop else 0), GROUND + 20 - (i % 2) * 14, 0.0 if stop else t * STEP * 0.5 + i * 0.4, 1, 0.82 + 0.06 * (i % 3), lamp="torch")
        if u > 0.5:
            k = min(1, (u - 0.5) / 0.15)
            for ex in (930, 995):
                for r, a in ((50, 30), (30, 80), (15, 235)):
                    d.ellipse((ex - r * k, GROUND - 250 - r * k * 0.6, ex + r * k, GROUND - 250 + r * k * 0.6), fill=(255, 30, 24, a))
        if stop:
            return red_wash(im, 0.16 * math.sin(t * 8) ** 2)
    write_video(out, dur, paint, world, cwd, cam_dir=0.9)
    return {"hits": [dur * 0.68]}


def ground_scene(out, dur, world, sp, cwd=None):
    """What is left behind, seen from above: a lantern lying on its side with its glass smashed, its flame
    guttering, shards glinting, and a line of huge clawed prints leading away into the dark.
    There is no monster in the shot, only its footprints."""
    tex = _topdown_texture()
    rnd = random.Random(11)
    shards = [(rnd.uniform(-170, 170), rnd.uniform(-110, 150), rnd.uniform(8, 22), rnd.uniform(0, 6.28)) for _ in range(16)]
    prints = [(560 + (150 if i % 2 else -110) + rnd.uniform(-18, 18), 820 - i * 170, i) for i in range(5)]
    world.push = 0.18
    world.shake = 0.0
    LX, LY = 540, 1040

    def paint(im, d, t, u, cam):
        im.paste(tex, (0, 0))
        d2 = ImageDraw.Draw(im, "RGBA")
        fl = 0.55 + 0.25 * math.sin(t * 17) * math.sin(t * 5.3) + 0.2 * (rnd.random() if int(t * 30) % 7 == 0 else 0.5)
        die = 1.0 if u < 0.7 else max(0.25, 1 - (u - 0.7) / 0.3 * 0.75)  # the flame weakens
        L = max(0.15, fl * die)
        for r, a in ((420, 18), (300, 30), (190, 50), (100, 80)):
            d2.ellipse((LX - r * (0.8 + 0.3 * L), LY - r * (0.8 + 0.3 * L), LX + r * (0.8 + 0.3 * L), LY + r * (0.8 + 0.3 * L)), fill=(255, 150, 60, int(a * L)))
        # the huge clawed prints leading away from the lantern, only the nearest ones are lit
        for px, py, i in prints:
            _print(d2, px, py, 0.0, PRINT_OF.get(sp.get("kind"), "claw"))
            d2.ellipse((px - 60, py - 30, px + 60, py + 70), outline=(30, 24, 20, 120), width=4)
        # a few dark drops
        for k in range(6):
            bx, by = LX - 90 + k * 34, LY + 120 + (k % 3) * 22
            d2.ellipse((bx - 7, by - 5, bx + 7, by + 5), fill=(95, 6, 10, 235))
        # smashed glass
        for sx, sy, sz, a in shards:
            x, y = LX + sx, LY + sy
            pts = [(x + math.cos(a) * sz, y + math.sin(a) * sz), (x + math.cos(a + 2.4) * sz * 0.6, y + math.sin(a + 2.4) * sz * 0.6),
                   (x + math.cos(a + 4.0) * sz * 0.9, y + math.sin(a + 4.0) * sz * 0.9)]
            d2.polygon(pts, fill=(200, 225, 235, 140))
            if int(t * 6 + sx) % 5 == 0:
                d2.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(255, 255, 255, 230))
        # the lantern, lying on its side: frame, no glass, a tiny flame
        d2.rounded_rectangle((LX - 84, LY - 46, LX + 84, LY + 46), radius=8, outline=(24, 20, 18, 255), width=12)
        d2.rectangle((LX - 96, LY - 52, LX - 76, LY + 52), fill=(28, 24, 22, 255))      # cap
        d2.rectangle((LX + 80, LY - 52, LX + 100, LY + 52), fill=(28, 24, 22, 255))     # base
        d2.line((LX - 84, LY - 20, LX + 84, LY - 14), fill=(24, 20, 18, 255), width=6)
        d2.arc((LX - 50, LY - 190, LX + 70, LY - 30), 190, 350, fill=(24, 20, 18, 255), width=9)   # the bent handle
        for r, a in ((46, 70), (28, 120), (14, 230)):
            d2.ellipse((LX - 12 - r * 0.4, LY - r * 0.9 * L, LX - 12 + r * 0.4, LY + r * 0.3), fill=(255, int(190 * L + 40), 70, int(a * L + 20)))
        # smoke curling off the dying flame
        for i in range(5):
            k = (t * 0.45 + i * 0.2) % 1.0
            d2.ellipse((LX - 14 + 30 * math.sin(k * 6 + i) - 10 * k, LY - 40 - 220 * k - 10, LX - 14 + 30 * math.sin(k * 6 + i) + 14 * k + 8, LY - 40 - 220 * k + 12),
                       fill=(130, 130, 140, int(60 * (1 - k))))
        dim(im, 90)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": []}


# ---------------------------------------------------------------- the sign: how the monster enters the world
def _flash(im, amount, col=(255, 255, 255)):
    return Image.blend(im, Image.new("RGB", im.size, col), max(0.0, min(0.9, amount)))


def _look_up(d, x, gy, t, n=1, scale=0.75):
    for i in range(n):
        draw_human(d, x + i * 120, gy + 18, 0.0, 1 if i % 2 == 0 else -1, scale, lamp=None, cast=False)


def omen_scene(out, dur, world, sp, cwd=None):
    """The sign that something terrible has arrived: the moon swells, an eclipse, a bolt of lightning that
    reveals a shape on the hill, an asteroid striking, a ship descending, a sickness spreading, a shadow that
    does not match its owner, the ground cracking open."""
    fx = world.skyfx or "fullmoon"
    sky_type = fx in ("fullmoon", "bluemoon", "eclipse", "thunder", "asteroid", "ufo", "comet", "aurora", "redsky", "meteors")
    world.focus_y = 0.33 if sky_type else 0.66
    world.push = 0.28 if sky_type else 0.18
    world.shake = 0.0
    hits = []
    sx, sy, sr = getattr(world, "sun_xy", (700, 520, 85))
    if fx == "asteroid":
        hits = [dur * 0.62]
        world.shake = 0.0
    if fx == "thunder":
        hits = [dur * 0.22, dur * 0.5, dur * 0.76]
    if fx == "virus":
        hits = [dur * 0.25, dur * 0.5, dur * 0.72]
    if fx == "tomb":
        hits = [dur * 0.2, dur * 0.55]

    def paint(im, d, t, u, cam):
        if fx in ("fullmoon", "bluemoon"):
            k = u * u * (3 - 2 * u)
            r = sr * (1.0 + 0.7 * k)
            col = (246, 241, 214) if fx == "fullmoon" else (121, 178, 255)
            for kk, a in ((3.4, 30), (2.4, 50), (1.6, 90)):
                d.ellipse((sx - r * kk, sy - r * kk, sx + r * kk, sy + r * kk), fill=col + (int(a * (0.4 + k)),))
            d.ellipse((sx - r, sy - r, sx + r, sy + r), fill=col + (255,))
            _look_up(d, 250, GROUND, t, 2)
            if u > 0.6 and fx == "fullmoon":
                return red_wash(im, 0.25 * (u - 0.6) / 0.4)
        elif fx == "eclipse":
            r = sr * 1.3
            for kk, a in ((3.0, 30), (2.0, 55)):
                d.ellipse((sx - r * kk, sy - r * kk, sx + r * kk, sy + r * kk), fill=(240, 235, 215, a))
            d.ellipse((sx - r, sy - r, sx + r, sy + r), fill=(240, 235, 215, 255))
            shift = -2.4 * r * (1 - min(1, u / 0.8))
            d.ellipse((sx + shift - r * 1.02, sy - r * 1.02, sx + shift + r * 1.02, sy + r * 1.02), fill=(4, 3, 8, 255))
            if u > 0.8:  # a blood-red ring
                f = (u - 0.8) / 0.2
                for kk, a in ((1.45, 120), (1.25, 200)):
                    d.ellipse((sx - r * kk, sy - r * kk, sx + r * kk, sy + r * kk), outline=(230, 40, 28, int(a * f)), width=10)
            dim(im, int(150 * u))
            _look_up(d, 250, GROUND, t, 2)
        elif fx == "thunder":
            fl = 0.0
            for tt, seed in ((0.22, 1), (0.5, 2), (0.76, 3)):
                kk = (u - tt) / 0.07
                if 0 <= kk < 1.6:
                    lightning_bolt(d, 260 + seed * 220, kk, seed)
                    if kk < 0.5:
                        fl = max(fl, 0.55 * (1 - kk / 0.5))
                    if seed == 3 and kk > 0.2:  # lit by the bolt, a shape on the hill
                        draw_creature(d, {**sp, "color": (6, 4, 10)}, 830, GROUND - 150, t * 2, -1, 0.55, {"t": t, "moving": False, "roar": 0.8})
            return _flash(im, fl, (225, 232, 255)) if fl else None
        elif fx == "asteroid":
            if u < 0.62:
                p = u / 0.62
                x, y = 100 + 640 * p, 80 + (GROUND - 220) * p
                for k in range(30):
                    d.ellipse((x - k * 24 - 24, y - k * 30 - 24, x - k * 24 + 24, y - k * 30 + 24), fill=(255, max(0, 160 - k * 5), 40, max(0, 200 - k * 6)))
                for r, a in ((90, 60), (56, 130), (30, 255)):
                    d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 215, 130, a))
                world.shake = 3 + 10 * p
            else:
                k = (u - 0.62) / 0.38
                ix, iy = 740, GROUND - 120
                if k < 0.12:
                    im = _flash(im, 0.85 * (1 - k / 0.12))
                d2 = ImageDraw.Draw(im, "RGBA")
                rr = 80 + 900 * k
                d2.ellipse((ix - rr, iy - rr * 0.28, ix + rr, iy + rr * 0.28), outline=(255, 210, 150, int(220 * (1 - k))), width=14)
                for r, a in ((260, 40), (150, 80), (70, 160)):
                    d2.ellipse((ix - r, iy - r * 0.5, ix + r, iy + r * 0.5), fill=(120, 255, 150, int(a * (1 - 0.4 * k))))
                for j in range(14):
                    ang = j * 0.45
                    d2.ellipse((ix + math.cos(ang) * 300 * k - 6, iy - abs(math.sin(ang)) * 420 * k + 140 * k * k - 6,
                                ix + math.cos(ang) * 300 * k + 6, iy - abs(math.sin(ang)) * 420 * k + 140 * k * k + 6), fill=(255, 170, 60, 230))
                world.shake = 16 * (1 - k)
                return im
        elif fx == "ufo":
            k = u * u * (3 - 2 * u)
            ux, uy, us = 540, 150 + 330 * k, 0.9 + 0.7 * k
            d.polygon([(ux - 40 * us, uy + 40 * us), (ux + 40 * us, uy + 40 * us), (ux + 420 * us, GROUND), (ux - 420 * us, GROUND)], fill=(150, 255, 190, 55))
            d.ellipse((ux - 160 * us, uy - 34 * us, ux + 160 * us, uy + 50 * us), fill=(60, 70, 90, 255), outline=(150, 170, 200, 255), width=3)
            d.ellipse((ux - 70 * us, uy - 82 * us, ux + 70 * us, uy + 10 * us), fill=(120, 220, 255, 200))
            for i in range(8):
                lx = ux - 126 * us + i * 36 * us
                d.ellipse((lx - 8 * us, uy + 8 * us, lx + 8 * us, uy + 24 * us), fill=(255, 235, 120, 255) if (int(t * 8) + i) % 2 else (110, 100, 60, 255))
            if u > 0.5:  # something comes down the beam
                q = (u - 0.5) / 0.5
                draw_creature(d, {**sp, "color": (10, 8, 16)}, ux, uy + 80 + (GROUND - uy - 80) * q, t * 2, 1, 0.5 + 0.3 * q, {"t": t, "moving": False, "roar": 0.3})
            _look_up(d, 150, GROUND, t, 2)
            world.shake = 2 + 6 * u
        elif fx == "virus":
            for i in range(5):
                x = 120 + i * 210 + 20 * math.sin(t * 1.5 + i)
                tw = 0.0 if u < 0.2 + i * 0.1 else min(1.0, (u - 0.2 - i * 0.1) * 4)
                gy = GROUND + 20 + (80 * tw if i == 2 else 0)  # one of them collapses
                d.ellipse((x - 120, gy - 330, x + 120, gy + 20), fill=(110, 230, 90, 26))
                draw_human(d, x, gy, t * 1.2 + i, 1, 0.78, lamp=None, cast=False)
                if (int(t * 3) + i) % 4 == 0:
                    for j in range(5):
                        d.ellipse((x + 40 + j * 16, gy - 300 - j * 10, x + 70 + j * 18, gy - 270 - j * 10), fill=(140, 240, 100, 90))
            if u > 0.65:
                ex = 120 + 3 * 210
                for r, a in ((26, 80), (14, 220)):
                    d.ellipse((ex - r, GROUND - 330, ex + r, GROUND - 330 + r), fill=(255, 40, 30, a))
            world.shake = 4 * math.sin(u * 40)
        elif fx == "shadow":
            px = 700
            hero_shadow = {**sp, "color": (2, 2, 6)}
            draw_creature(d, hero_shadow, px - 480, GROUND + 26, t * 0.7, 1, 1.0 + 0.2 * u, {"t": t, "moving": False, "roar": 0.2 + 0.6 * u})
            draw_human(d, px, GROUND + 20, 0.0, 1, 1.05, lamp="lantern", cast=False)
        elif fx == "comet":
            ph = u
            cx, cy = 300 + 480 * ph, 260 + 60 * ph
            for k in range(22):
                d.ellipse((cx + k * 30 - 30, cy - k * 9 - 14 + k * 0.2, cx + k * 30 + 30, cy - k * 9 + 14), fill=(190, 230, 255, max(0, 170 - k * 8)))
            for r, a in ((110, 40), (62, 100), (30, 255)):
                d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(240, 250, 255, a))
            _look_up(d, 200, GROUND, t, 3)
            if u > 0.7:
                return red_wash(im, 0.3 * (u - 0.7) / 0.3)
        elif fx == "aurora":
            for j, c in enumerate(((60, 255, 150), (160, 100, 255), (60, 220, 230))):
                for x in range(0, W + 60, 60):
                    y = 220 + j * 110 + 70 * math.sin(x * 0.006 + t * 1.1 + j) + 30 * math.sin(x * 0.017 + t * 2)
                    d.polygon([(x, y), (x + 60, y), (x + 60, y + 420), (x, y + 420)], fill=c + (int(24 + 30 * u),))
            if u > 0.55:  # a tall figure stands on the far hill
                draw_creature(d, {**sp, "color": (5, 4, 9)}, 760, GROUND - 190, t * 2, 1, 0.4, {"t": t, "moving": False, "roar": 0.2})
            _look_up(d, 200, GROUND, t, 2)
        elif fx == "redsky":
            for i in range(26):
                a = i * 0.8 + t * (0.8 + (i % 3) * 0.3)
                bx, by = 540 + 480 * math.cos(a) * (0.5 + 0.5 * math.sin(i)), 330 + 150 * math.sin(a * 1.3)
                wing = 20 * math.sin(t * 10 + i)
                d.line((bx - 30, by - wing, bx, by, bx + 30, by - wing), fill=(5, 3, 5, 255), width=6)
            _look_up(d, 250, GROUND, t, 2)
            return red_wash(im, 0.12 + 0.1 * math.sin(t * 3) ** 2)
        elif fx == "meteors":
            _look_up(d, 250, GROUND, t, 2)
            for i in range(6):
                ph = (t * 0.8 + i * 0.17) % 1.0
                x0, y0 = 100 + i * 170, 60 + (i * 71) % 160
                x1, y1 = x0 + 600 * ph, y0 + 340 * ph
                d.line((x1 - 200, y1 - 113, x1, y1), fill=(255, 235, 200, int(230 * (1 - ph))), width=5)
        elif fx == "tomb":
            k = min(1.0, u / 0.75)
            rnd = random.Random(4)
            for j in range(11):
                x, y, ang = 540, GROUND + 120, -math.pi / 2 + (j - 5) * 0.38
                pts = [(x, y)]
                for s_ in range(int(2 + 7 * k)):
                    ang += rnd.uniform(-0.45, 0.45)
                    x += math.cos(ang) * 70
                    y += math.sin(ang) * 36 + 22
                    pts.append((x, y))
                d.line(pts, fill=(255, 90, 30, 90), width=24)
                d.line(pts, fill=(255, 190, 90, 255), width=7)
            for r, a in ((340, 30), (220, 60), (110, 110)):
                d.ellipse((540 - r, GROUND + 120 - r * 0.35, 540 + r, GROUND + 120 + r * 0.35), fill=(255, 100, 30, int(a * k)))
            if u > 0.6:  # a clawed hand claws its way out
                q = min(1.0, (u - 0.6) / 0.3)
                for f in range(4):
                    fx0 = 470 + f * 46
                    d.line((fx0, GROUND + 130, fx0 - 6 + f * 4, GROUND + 130 - 150 * q - f % 2 * 22), fill=(18, 14, 18, 255), width=17)
                    d.polygon([(fx0 - 9 + f * 4 - 6, GROUND + 130 - 150 * q - f % 2 * 22), (fx0 - 3 + f * 4 + 6, GROUND + 130 - 150 * q - f % 2 * 22),
                               (fx0 + f * 4 - 2, GROUND + 130 - 150 * q - f % 2 * 22 - 56)], fill=(228, 220, 198, 255))
            _look_up(d, 150, GROUND, t, 2, 0.6)
            world.shake = 12 * (1 - 0.4 * u)
        if fx in ("fullmoon", "bluemoon", "eclipse", "thunder", "ufo", "comet", "aurora", "redsky", "meteors", "asteroid", "virus", "shadow", "tomb"):
            dim(im, 20)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


# ---------------------------------------------------------------- camera angles
def _vignette(im, strength=170, tint=None):
    """Dark tunnel vision around the edges (made once per size and cached)."""
    key = ("vig", im.size, strength)
    if key not in _TOP:
        m = Image.new("L", (64, 114), 0)
        px = m.load()
        for y in range(114):
            for x in range(64):
                r = math.hypot((x - 32) / 32, (y - 57) / 57)
                px[x, y] = int(min(1.0, max(0.0, (r - 0.45) / 0.75)) * strength)
        _TOP[key] = m.resize(im.size, Image.BICUBIC)
    im.paste(Image.new("RGB", im.size, tint or (0, 0, 0)), mask=_TOP[key])


def pov_scene(out, dur, world, sp, cwd=None):
    """FIRST PERSON, through the eyes of the person: the lantern swinging in your own hand, the path bobbing
    with your steps, your breath fogging. You turn your head, and something is coming through the mist."""
    world.push, world.shake = 0.22, 5.0
    turn = 0.4

    def paint(im, d, t, u, cam):
        bob = 10 * math.sin(t * STEP * 0.5)
        if u < turn:
            sc = ImageDraw.Draw(im, "RGBA")
            sc.polygon([(540 - 120, GROUND + 40), (540 + 120, GROUND + 40), (540 + 420, H), (540 - 420, H)], fill=(40, 34, 30, 120))  # the path
        else:
            k = (u - turn) / (1 - turn)
            gap = 1.0 - k
            if u < turn + 0.04:
                return Image.blend(im, im.transform(im.size, Image.AFFINE, (1, 0, -240, 0, 1, 0)), 0.6)  # the head whips round
            cxm = 540 - 120 * gap
            draw_creature(d, sp, cxm, GROUND + 20, t * STEP * (1.1 if k > 0.35 else 0.6), -1, 0.45 + 1.25 * k * k,
                          {"t": t, "moving": True, "roar": 0.2 + 0.8 * k})
            world.shake = 5 + 16 * k
        # the lantern in your own hand, low in the frame
        lx, ly = 860 + 24 * math.sin(t * STEP * 0.5), 1640 + bob * 3
        for r, a in ((520, 22), (330, 40), (190, 70)):
            d.ellipse((lx - r, ly - r - 100, lx + r, ly + r - 100), fill=(255, 170, 70, a))
        d.rounded_rectangle((lx - 56, ly - 150, lx + 56, ly + 20), radius=10, fill=(40, 30, 22, 255), outline=(14, 10, 8, 255), width=8)
        d.ellipse((lx - 30, ly - 120, lx + 30, ly - 20), fill=(255, 215, 130, 255))
        d.polygon([(lx + 40, ly + 30), (lx + 200, ly + 380), (lx - 140, ly + 380), (lx - 60, ly + 30)], fill=(10, 10, 16, 255))  # your arm
        # your breath
        for i in range(5):
            k2 = (t * 0.9 + i * 0.2) % 1.0
            d.ellipse((540 - 80 + 90 * k2, H - 380 - 280 * k2, 540 + 80 + 90 * k2, H - 300 - 280 * k2), fill=(220, 230, 240, int(55 * (1 - k2))))
        _vignette(im, 190)
        if u >= turn:
            return red_wash(im, 0.04 + 0.12 * ((u - turn) / (1 - turn)))
    write_video(out, dur, paint, world, cwd, cam_dir=1.6)
    return {"hits": [dur * (turn + 0.02)]}


def mpov_scene(out, dur, world, sp, cwd=None):
    """THROUGH THE MONSTER'S EYES: a red tunnel, a pounding view and a tiny glowing lantern ahead that is
    getting closer with every stride."""
    world.push, world.shake = 0.3, 9.0

    def paint(im, d, t, u, cam):
        k = u * u
        sc = 0.5 + 1.35 * k
        px = 560 + 40 * math.sin(t * 2.0)
        draw_human(d, px, GROUND + 20 + 80 * k, t * STEP * 0.5, 1, sc, cast=False)
        # the world lurches with the creature's stride
        out_im = im.transform(im.size, Image.AFFINE, (1, 0, 0, 0, 1, 14 * math.sin(t * STEP * 0.5 * 2)))
        _vignette(out_im, 235, (50, 0, 0))
        out_im = red_wash(out_im, 0.18 + 0.1 * math.sin(t * 9) ** 2)
        o = ImageDraw.Draw(out_im, "RGBA")
        for y in range(0, H, 6):  # grainy, hunting view
            o.line((0, y, W, y), fill=(0, 0, 0, 38))
        # the fangs at the edge of the frame
        for i in range(6):
            x = 140 + i * 160
            o.polygon([(x, H), (x + 46, H), (x + 22, H - 90 - (i % 2) * 40)], fill=(235, 225, 205, 220))
        return out_im
    write_video(out, dur, paint, world, cwd, cam_dir=2.2)
    return {"hits": []}


def curtain_scene(out, dur, world, sp, cwd=None):
    """From INSIDE A HOUSE: through a window half closed by curtains, a person runs for their life in the night
    and the monster gallops after them. A candle burns in the foreground, the curtains sway."""
    world.shake = 3.0
    world.push = 0.1
    key = "curtain_wall"
    if key not in _WALL:
        wall = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        wd = ImageDraw.Draw(wall)
        wd.rectangle((0, 0, W, H), fill=(26, 16, 12, 255))
        for x in range(0, W, 80):
            wd.line((x, 0, x, H), fill=(15, 10, 8, 255), width=4)
        wd.rectangle((150, 380, 930, 1300), fill=(0, 0, 0, 0))
        wd.rectangle((126, 356, 954, 1324), outline=(70, 46, 30, 255), width=34)
        wd.line((540, 380, 540, 1300), fill=(70, 46, 30, 255), width=20)
        wd.line((150, 840, 930, 840), fill=(70, 46, 30, 255), width=20)
        wd.rectangle((90, 1324, 990, 1390), fill=(54, 36, 24, 255))
        _WALL[key] = wall
    wall = _WALL[key]

    def paint(im, d, t, u, cam):
        d.rectangle((150, 1150, 930, 1300), fill=(6, 8, 14, 255))  # the ground outside
        k = (u - 0.12) / 0.76
        if 0 <= k <= 1:
            px = 1000 - 1000 * k
            ph = t * STEP * 1.1
            draw_creature(d, sp, px + 330 * (1 - k * 0.5) + 160, 1260, ph + 1.0, -1, 0.42, {"t": t, "moving": True, "roar": 0.7})
            draw_human(d, px, 1262, ph, -1, 0.42, cast=False)
        im.paste(wall, (0, 0), wall)
        d = ImageDraw.Draw(im, "RGBA")
        sway = 10 * math.sin(t * 1.7)
        # the curtains, drawn open just a slit in the middle
        for side in (-1, 1):
            x_edge = 150 if side < 0 else 930
            reach = 420 + sway * side if side < 0 else 330 - sway
            xs = [x_edge + side * i * reach / 14 for i in range(15)]
            for i in range(14):
                a, b = sorted((xs[i], xs[i + 1]))
                shade = 60 + 22 * math.sin(i * 1.7 + 1.0)
                d.rectangle((a, 330, b + 1, 1420), fill=(int(shade + 40), 14, 20, 255))
            d.line((x_edge, 330, x_edge + side * reach, 330), fill=(40, 28, 20, 255), width=16)
        d.line((90, 330, 990, 330), fill=(60, 40, 26, 255), width=16)
        # the candle on the sill in the foreground
        fl = 0.8 + 0.2 * math.sin(t * 17)
        for r, a in ((260, 20), (150, 36), (70, 80)):
            d.ellipse((260 - r, 1290 - r, 260 + r, 1290 + r), fill=(255, 170, 70, int(a * fl)))
        d.rectangle((246, 1300, 274, 1390), fill=(225, 214, 190, 255))
        d.ellipse((251, 1250, 269, 1300), fill=(255, 225, 150, 255))
        dim(im, 70)
        _vignette(im, 150)
        if 0.6 < u < 0.78:
            return red_wash(im, 0.12)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": [dur * 0.55]}


def shoulder_scene(out, dur, world, sp, cwd=None):
    """OVER THE SHOULDER: the person's back fills the foreground while, far ahead in the mist, the creature
    comes closer and closer."""
    world.push, world.shake = 0.12, 4.0

    def paint(im, d, t, u, cam):
        k = u * u * (3 - 2 * u)
        draw_creature(d, sp, 760 + 90 * (1 - k), GROUND - 40, t * STEP * 0.5, -1, 0.35 + 0.65 * k,
                      {"t": t, "moving": True, "roar": 0.1 + 0.8 * k})
        dim(im, int(60 * (1 - k)))
        draw_human(d, 190 + 6 * math.sin(t * 2.0), GROUND + 360, t * STEP * 0.3, 1, 2.3, cast=False)
        _vignette(im, 170)
        return red_wash(im, 0.03 + 0.12 * k)
    write_video(out, dur, paint, world, cwd, cam_dir=0.5)
    return {"hits": []}


def loom_scene(out, dur, world, sp, cwd=None):
    """A LOW, TILTED ANGLE from the ground: the monster towers over the camera and the tiny person below it."""
    world.tilt, world.push, world.shake = -7.0, 0.10, 5.0

    def paint(im, d, t, u, cam):
        im.paste(Image.new("RGB", im.size, (4, 4, 8)), (0, GROUND + 120), None)
        ax, ay = head_anchor(sp)
        draw_creature(d, sp, 0, 0, t * 0.8, 1, 1.7 + 0.25 * u, {"t": t, "moving": False, "roar": 0.35 + 0.65 * math.sin(u * 3.0) ** 2},
                      focus=(ax, ay, 640, 560 - 30 * u))
        draw_human(d, 230, GROUND + 330, 0.0, 1, 0.55, cast=False)
        _vignette(im, 150)
        return red_wash(im, 0.08 + 0.1 * u)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": [dur * 0.35]}



# ---------------------------------------------------------------- suspense: a silhouette that is lit up only at the last moment
def _smooth(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


_RV = {"t0": None, "last": -9.0}


def reveal_creature(im, sp, cx, gy, scale, t, lit, facing=1, moving=True, roar=0.2):
    """The creature as a black silhouette. It sweeps in from the head down, and when `lit` goes on its eyes glow first,
    then the face, then the whole body slowly comes back to colour."""
    s = 1.4 * scale * sp.get("scale", 1.0) * KIND_SCALE.get(sp["kind"], 1.0)
    x0, x1 = int(max(0, cx - 520 * s)), int(min(W, cx + 520 * s))
    y0, y1 = int(max(0, gy - 1250 * s)), int(min(H, gy + 80 * s))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return
    layer = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer, "RGBA")
    _REC_OFF[0], _REC_OFF[1] = x0, y0
    try:
        draw_creature(ld, sp, cx - x0, gy - y0, t * STEP * 0.3, facing, scale,
                      {"t": t, "moving": moving, "roar": roar, "aura": False, "noemerge": True})
    finally:
        _REC_OFF[0], _REC_OFF[1] = 0, 0
    ax, ay = head_anchor(sp)
    hx, hy = cx - x0 + facing * s * ax, gy - y0 + s * ay
    a = np.asarray(layer.getchannel("A"), dtype=np.float32) / 255.0
    k_sil = _emerge_k()
    vis = _reveal_mask(a, hx, hy, k_sil, s) if k_sil < 0.999 else None
    base = im.crop((x0, y0, x1, y1)).convert("RGBA")
    sil = np.zeros(a.shape + (4,), dtype=np.uint8)
    sil[..., 0], sil[..., 1], sil[..., 2] = 3, 3, 6
    sil[..., 3] = (a * (vis if vis is not None else 1.0) * 255).astype(np.uint8)
    base.alpha_composite(Image.fromarray(sil, "RGBA"))
    if t < _RV["last"] - 0.01:
        _RV["t0"] = None
    _RV["last"] = t
    if lit > 0.5:
        if _RV["t0"] is None:
            _RV["t0"] = t
        p = min(1.0, (t - _RV["t0"]) / 2.0)
        m = _reveal_mask(a, hx, hy, p, s) * (vis if vis is not None else 1.0)
        rgb = np.asarray(layer.convert("RGB"), dtype=np.float32)
        real = np.zeros(a.shape + (4,), dtype=np.uint8)
        real[..., :3] = np.clip(rgb * 2.3 + 34, 0, 255).astype(np.uint8)
        real[..., 3] = (a * m * 255).astype(np.uint8)
        base.alpha_composite(Image.fromarray(real, "RGBA"))
        base.alpha_composite(_eye_glow(layer, sp, hx, hy, p, s))
    im.paste(base.convert("RGB"), (x0, y0))


# ---------------------------------------------------------------- indoor stories: bedroom, stairs, basement, corridor, camera
from PIL import ImageFilter, ImageOps

_FONTS = {}


def _font(size):
    if size not in _FONTS:
        for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                  "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf"):
            try:
                _FONTS[size] = ImageFont.truetype(p, size)
                break
            except Exception:
                continue
        else:
            _FONTS[size] = ImageFont.load_default()
    return _FONTS[size]


def hook_scene(out, dur, world, sp, cwd=None):
    """A quick close-up of the monster's face, so a Short can open on it (and loop back to it)."""
    def paint(im, d, t, u, cam):
        closeup(im, d, sp, t, dur, 3.0, 2.5, roar=0.7, shake=5, tt=t)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": []}


def bedroom_scene(out, dur, world, sp, cwd=None):
    """Night, a bedroom. Someone lies awake. The red clock says the same minute it says every night, and from
    somewhere below the floor, a slow tapping. Dust trembles down from the ceiling."""
    clock = getattr(world, "clock", "2:48")
    hits = [0.7 + 0.55 * k + (0.35 if k % 3 == 2 else 0) * 0 for k in range(int(dur / 0.55))]
    hits = [h for h in hits if h < dur - 0.2]
    world.push, world.shake, world.focus_y = 0.22, 0.0, 0.7
    rnd = random.Random(2)
    dust = [(rnd.uniform(0, W), rnd.uniform(0, 900), rnd.uniform(3, 8)) for _ in range(40)]

    def paint(im, d, t, u, cam):
        d.rectangle((0, 0, W, H), fill=(14, 15, 27, 255))
        d.rectangle((0, 1280, W, H), fill=(26, 18, 15, 255))
        for x in range(0, W, 110):
            d.line((x, 0, x, 1280), fill=(18, 19, 33, 255), width=3)
        for x in range(0, W, 140):  # floorboards
            d.line((x, 1280, x - 90, H), fill=(14, 10, 9, 255), width=4)
        d.rectangle((640, 330, 1000, 820), fill=(30, 40, 78, 255), outline=(10, 12, 20, 255), width=16)  # the moonlit window
        d.line((820, 330, 820, 820), fill=(10, 12, 20, 255), width=12)
        d.polygon([(640, 820), (1000, 820), (1080, 1280), (420, 1280)], fill=(36, 48, 90, 38))
        d.rectangle((60, 1060, 600, 1280), fill=(70, 58, 56, 255))                         # the bed
        d.rectangle((60, 980, 600, 1100), fill=(150, 138, 136, 255))                       # blanket
        d.rounded_rectangle((70, 950, 230, 1020), radius=26, fill=(190, 184, 178, 255))    # pillow
        d.ellipse((130, 940, 218, 1020), fill=(222, 196, 170, 255))                         # head
        blink = 0.0 if int(t * 2.1) % 9 == 4 else 1.0
        for ex in (170, 196):
            d.ellipse((ex - 6, 974 - 6 * blink, ex + 6, 974 + 6 * blink), fill=(255, 255, 255, 255))
            d.ellipse((ex - 2, 972, ex + 2, 977), fill=(0, 0, 0, 255))
        d.rectangle((690, 1090, 890, 1280), fill=(52, 40, 36, 255))                        # the bedside table
        d.rectangle((706, 1010, 880, 1090), fill=(16, 16, 18, 255), outline=(40, 40, 44, 255), width=4)
        glitch = u > 0.72 and (int(t * 14) % 4 == 0)
        txt = clock if not glitch else clock.replace("4", "?").replace("8", "#")
        on = int(t * 2) % 2 == 0 or u > 0.5
        for r, a in ((260, 18), (150, 34), (80, 60)):
            d.ellipse((793 - r, 1050 - r, 793 + r, 1050 + r), fill=(255, 20, 20, a))
        d.text((793, 1050), txt if on else txt.replace(":", " "), font=_font(66), fill=(255, 52, 40, 255), anchor="mm")
        for tt in hits:  # each tap: a ripple under the floor, dust shaken loose
            k = t - tt
            if 0 <= k < 0.5:
                for rr in (1, 2, 3):
                    d.arc((360 - 120 * rr * k * 3, 1500 - 40 * rr * k * 3, 360 + 120 * rr * k * 3, 1500 + 40 * rr * k * 3), 180, 360,
                          fill=(255, 140, 90, int(160 * (1 - k * 2))), width=5)
        for x, y, r in dust:
            dd = ((t * 60 + y) % 900) if any(0 <= t - tt < 0.7 for tt in hits) else y
            d.ellipse((x, dd, x + r, dd + r), fill=(220, 220, 230, 90))
        dim(im, 40 + int(70 * max(0, u - 0.8) / 0.2))
        if u > 0.85:
            return red_wash(im, 0.14 * (u - 0.85) / 0.15)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


def stairs_scene(out, dur, world, sp, cwd=None):
    """Down into the basement: a person with a flashlight takes the old wooden stairs one creaking step at a time,
    the dark at the bottom swallowing the beam."""
    steps = 10
    hits = [dur * (0.12 + 0.07 * k) for k in range(steps)]
    hits = [h for h in hits if h < dur - 0.3]
    world.push, world.tilt, world.shake = 0.12, 3.0, 2.0

    def paint(im, d, t, u, cam):
        d.rectangle((0, 0, W, H), fill=(14, 12, 12, 255))
        for x in range(0, W, 90):
            d.line((x, 0, x, H), fill=(20, 17, 16, 255), width=3)
        sx0, sy0, sw, sh = 130, 480, 84, 70
        pts = [(sx0, sy0 + 30)]
        for k in range(12):  # the wooden staircase
            x, y = sx0 + k * sw, sy0 + k * sh
            d.polygon([(x, y), (x + sw, y), (x + sw, y + 24), (x, y + 24)], fill=(88, 64, 44, 255))
            d.polygon([(x + sw, y + 24), (x + sw, y + sh), (x + sw - 14, y + sh), (x + sw - 14, y + 24)], fill=(38, 26, 20, 255))
        d.polygon([(sx0, sy0 - 120), (sx0 + 12 * sw, sy0 + 12 * sh - 120), (sx0 + 12 * sw, sy0 + 12 * sh - 100), (sx0, sy0 - 100)], fill=(46, 36, 28, 255))  # the rail
        p = min(1.0, u / 0.92)
        k = p * 9.3
        px, py = sx0 + k * sw + 30, sy0 + k * sh - 6
        if int(t * 30) % 11 != 0:
            d.polygon([(px + 44, py - 150), (px + 600, py - 40), (px + 620, py + 150), (px + 70, py - 120)], fill=(255, 235, 170, 62))
        draw_human(d, px, py + 26, t * STEP * 0.35 * (1 if p < 0.96 else 0), 1, 0.74, lamp=None, cast=False)
        d.ellipse((px + 40, py - 150, px + 66, py - 128), fill=(255, 240, 190, 255))
        d.polygon([(sx0 + 8 * sw, 0), (W, 0), (W, H), (sx0 + 12 * sw, H), (sx0 + 12 * sw, sy0 + 12 * sh)], fill=(2, 2, 4, 255))  # the dark below
        if u > 0.82:  # two eyes in the dark at the bottom
            f = min(1.0, (u - 0.82) / 0.1)
            for ex in (940, 1000):
                for r, a in ((34, 40), (20, 110), (10, 255)):
                    d.ellipse((ex - r * f, 1500 - r * f * 0.6, ex + r * f, 1500 + r * f * 0.6), fill=(255, 30, 24, a))
        dim(im, 60)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


def basement_scene(out, dur, world, sp, cwd=None):
    """FIRST PERSON, flashlight only: the beam sweeps over boxes, a furnace, a bicycle, then catches something
    hanging from the ceiling, tapping a knife on the floor and staring back."""
    world.shake = 6.0
    world.push = 0.15
    cw, chh = W // 2, H // 2
    taps = [dur * (0.68 + 0.07 * k) for k in range(5)]
    taps = [x for x in taps if x < dur - 0.1]

    def room(t, u):
        r = Image.new("RGB", (W, H), (20, 18, 20))
        d = ImageDraw.Draw(r, "RGBA")
        d.rectangle((0, 0, W, 560), fill=(30, 28, 32))
        d.rectangle((0, 1500, W, H), fill=(46, 34, 26))
        for x in range(0, W, 120):
            d.line((x, 1500, x - 160, H), fill=(22, 16, 12), width=5)
        d.rectangle((60, 1040, 260, 1500), fill=(120, 96, 62), outline=(70, 54, 34), width=6)    # boxes
        d.rectangle((100, 880, 300, 1040), fill=(138, 112, 72), outline=(70, 54, 34), width=6)
        d.rectangle((420, 760, 640, 1500), fill=(70, 74, 82), outline=(30, 32, 38), width=8)      # the furnace
        d.ellipse((470, 940, 590, 1060), fill=(20, 20, 24))
        d.rectangle((780, 1060, 1040, 1500), fill=(96, 80, 60), outline=(60, 48, 34), width=6)    # shelves
        for sy in (1160, 1260, 1360):
            d.line((780, sy, 1040, sy), fill=(50, 40, 30), width=8)
        d.ellipse((330, 1330, 470, 1500), outline=(150, 150, 160), width=8)                         # a bicycle wheel
        d.ellipse((560, 1330, 700, 1500), outline=(150, 150, 160), width=8)
        d.line((400, 1420, 480, 1330, 630, 1420), fill=(150, 150, 160), width=7)
        if u > 0.6:
            draw_creature(d, sp, 700, 1500, t * 1.2, 1, 0.86, {"t": t, "moving": False, "roar": 0.3 + 0.5 * math.sin(t * 2) ** 2})
        return r
    base = None

    def paint(im, d, t, u, cam):
        nonlocal base
        r = room(t, u)
        ang = 0.0
        if u < 0.62:
            ang = -0.6 + 1.3 * (u / 0.62) + 0.05 * math.sin(t * 5)
        else:
            ang = 0.14 + 0.012 * math.sin(t * 9)
        ox, oy = 540, 1900
        tx = ox + math.tan(ang) * 1300
        mask = Image.new("L", (cw, chh), 0)
        md = ImageDraw.Draw(mask)
        md.polygon([(ox / 2 - 20, oy / 2), (ox / 2 + 20, oy / 2), ((tx + 420) / 2, 520 / 2), ((tx - 420) / 2, 520 / 2)], fill=255)
        md.ellipse((tx / 2 - 190, 1000 / 2 - 260, tx / 2 + 190, 1000 / 2 + 260), fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(26)).resize((W, H), Image.BILINEAR)
        flick = 0.55 if (u > 0.86 and int(t * 22) % 5 == 0) else 1.0
        mask = mask.point(lambda v: int(v * flick))
        out_im = Image.composite(r, Image.new("RGB", (W, H), (2, 2, 4)), mask)
        for tt in taps:
            if 0 <= t - tt < 0.18:
                out_im = red_wash(out_im, 0.12)
        return out_im
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": taps}


def hallway_scene(out, dur, world, sp, cwd=None):
    """A long corridor in one-point perspective. The lights fade slowly, the far ones first, and in the thinning dark a
    black figure stands against the glow at the far end, walking closer. It stays a silhouette until it is near; then
    the light right above it snaps on and shows its face."""
    vx, vy = 540, 880
    nl = 6
    fade = [(0.14 + 0.1 * (nl - 1 - i), 0.14 + 0.1 * (nl - 1 - i) + 0.16) for i in range(nl)]  # far lights go first, slowly
    rev = 0.8  # the moment the near light pops on
    hits = [dur * fade[nl - 1 - k][1] for k in range(3)] + [dur * rev]
    world.push, world.shake = 0.1, 2.0

    def bright(i, u):
        a, b = fade[i]
        return 1.0 - _smooth((u - a) / (b - a))

    def paint(im, d, t, u, cam):
        d.rectangle((0, 0, W, H), fill=(8, 8, 12, 255))
        d.polygon([(0, 0), (vx - 90, vy - 70), (vx - 90, vy + 90), (0, H)], fill=(34, 30, 36, 255))
        d.polygon([(W, 0), (vx + 90, vy - 70), (vx + 90, vy + 90), (W, H)], fill=(30, 28, 34, 255))
        d.polygon([(0, H), (vx - 90, vy + 90), (vx + 90, vy + 90), (W, H)], fill=(48, 36, 30, 255))
        d.polygon([(0, 0), (vx - 90, vy - 70), (vx + 90, vy - 70), (W, 0)], fill=(20, 20, 26, 255))
        for k in range(1, 6):  # doors on both walls
            z = 1.0 / k
            for side in (-1, 1):
                x = vx + side * (90 + 360 * z)
                d.polygon([(x, vy - 60 - 420 * z), (x + side * -60 * z, vy - 60 - 380 * z), (x + side * -60 * z, vy + 70 + 300 * z * 0.2 + 60 * z), (x, vy + 90 + 320 * z)],
                          fill=(60, 40, 28, 255), outline=(12, 8, 6, 255))
        tot = 0.0
        for i in range(nl):  # the ceiling lights, near to far, each dimming slowly on its own schedule
            z = 1.0 / (i + 1.2)
            y = vy - 70 - 380 * z * 0.5
            b = bright(i, u)
            if 0.05 < b < 0.95:  # a faint electrical flutter while it dies
                b *= 0.75 + 0.25 * math.sin(t * 31 + i * 2.1)
            tot += b
            if b > 0.02:
                for r, a in ((260 * z + 40, 26), (150 * z + 24, 50)):
                    d.ellipse((vx - r, y - r * 0.5, vx + r, y + r * 0.9), fill=(255, 220, 150, int(a * b)))
                d.ellipse((vx - 70 * z - 10, y - 12 * z - 4, vx + 70 * z + 10, y + 12 * z + 4), fill=(255, int(160 + 85 * b), int(110 + 100 * b), 255))
        dim(im, int(150 * (1 - tot / nl)))
        # a pale glow at the far end of the corridor, which is what the figure is seen against
        gl = 0.5 + 0.5 * (1 - min(1.0, u / 0.85))
        for r, a in ((150, 30), (90, 48)):
            d.ellipse((vx - r, vy - r * 0.7, vx + r, vy + r), fill=(190, 205, 220, int(a * gl)))
        # the figure walks out of the far glow; a black silhouette, no glow, no red
        p = _smooth((u - 0.16) / (rev - 0.16)) if u < rev else 1.0
        sc = 0.05 + 0.62 * p ** 1.4
        gy = vy + 90 + 700 * p ** 1.6
        if u > 0.16:
            popped = u >= rev
            fl = 1.0
            if popped:  # the light right above it snaps on, stutters once, then stays
                k = (u - rev) * dur
                fl = 1.0 if (k < 0.12 or 0.2 < k) else 0.0
            if popped and fl:
                for r, a in ((520, 36), (330, 60), (190, 90)):
                    d.ellipse((vx - r, gy - 900 * sc * 1.4 - r * 0.5, vx + r, gy - 900 * sc * 1.4 + r * 1.2), fill=(255, 226, 170, a))
            reveal_creature(im, sp, vx + 6 * math.sin(t * 0.9), gy, sc, t, 1.0 if (popped and fl) else 0.0, moving=not popped)
        if u > 0.9:
            return red_wash(im, 0.2)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


def cctv_scene(out, dur, world, sp, cwd=None):
    """A grainy night-vision security camera in the corner of a room. The timestamp ticks. Something crosses the
    room, stops under the camera, and looks straight up into the lens."""
    stamp = getattr(world, "clock", "2:48")
    hh, mm = (stamp.split(":") + ["48"])[:2]
    world.shake = 1.5
    rnd = random.Random(8)

    def paint(im, d, t, u, cam):
        d.rectangle((0, 0, W, H), fill=(60, 60, 56, 255))
        d.polygon([(0, 0), (W, 0), (W - 160, 520), (160, 520)], fill=(78, 78, 72, 255))             # the back corner
        d.rectangle((160, 520, W - 160, 1380), fill=(70, 70, 64, 255))
        d.polygon([(0, H), (160, 1380), (W - 160, 1380), (W, H)], fill=(52, 52, 48, 255))
        d.rectangle((220, 1000, 560, 1380), fill=(40, 40, 40, 255), outline=(20, 20, 20, 255), width=6)  # a sofa
        d.rectangle((700, 880, 980, 1380), fill=(50, 50, 46, 255), outline=(20, 20, 20, 255), width=6)   # a cabinet
        k = (u - 0.15) / 0.55
        if 0 <= k <= 1:
            x = 1150 - 700 * k
            draw_creature(d, sp, x, 1380 + 30, t * STEP * 0.4, -1, 0.7, {"t": t, "moving": k < 1, "roar": 0.2})
        elif k > 1:
            draw_creature(d, sp, 450, 1410, t * 0.5, -1, 0.7, {"t": t, "moving": False, "roar": 0.2})
        g = ImageOps.colorize(im.convert("L"), black=(0, 10, 0), white=(160, 255, 150))
        gd = ImageDraw.Draw(g, "RGBA")
        for _ in range(260):  # grain
            x, y = rnd.randint(0, W), rnd.randint(0, H)
            gd.rectangle((x, y, x + rnd.randint(2, 18), y + 2), fill=(200, 255, 200, rnd.randint(20, 60)))
        for y in range(0, H, 8):
            gd.line((0, y, W, y), fill=(0, 20, 0, 40), width=2)
        if int(t * 5) % 11 == 0:  # glitch band
            by = rnd.randint(300, 1500)
            band = g.crop((0, by, W, by + 60)).transform((W, 60), Image.AFFINE, (1, 0, 40, 0, 1, 0))
            g.paste(band, (0, by))
        sec = int(13 + t)
        gd.text((50, 60), f"CAM 02   0{hh}:{mm}:{sec:02d}", font=_font(48), fill=(190, 255, 190, 255))
        if int(t * 2) % 2 == 0:
            gd.ellipse((W - 150, 60, W - 110, 100), fill=(255, 60, 50, 255))
        gd.text((W - 100, 80), "REC", font=_font(36), fill=(190, 255, 190, 255), anchor="lm")
        if u > 0.88:  # it looks into the lens
            f = (u - 0.88) / 0.12
            c = Image.new("RGB", (W, H), (0, 6, 0))
            cd = ImageDraw.Draw(c, "RGBA")
            closeup(c, cd, sp, t, 0.4, 2.2, 2.6, roar=0.9, shake=10 * f, tt=t)
            c = ImageOps.colorize(c.convert("L"), black=(0, 10, 0), white=(160, 255, 150))
            g = Image.blend(g, c, min(1.0, f * 2))
        return g
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": [dur * 0.9]}


def jump_scene(out, dur, world, sp, cwd=None):
    """A dark room. Two red eyes open in the black, slowly, then the face is suddenly right in the lens."""
    cut = 0.72
    world.shake = 0.0

    def paint(im, d, t, u, cam):
        if u < cut:
            im.paste(Image.new("RGB", im.size, (3, 3, 6)))
            d = ImageDraw.Draw(im, "RGBA")
            k = min(1.0, u / 0.35)
            if int(t * 3) % 7 != 6:
                for ex in (470, 610):
                    for r, a in ((40, 40), (24, 100), (12, 255)):
                        d.ellipse((ex - r * k, 900 - r * k * 0.6, ex + r * k, 900 + r * k * 0.6), fill=(255, 30, 24, a))
            return
        k = (u - cut) / (1 - cut)
        closeup(im, d, sp, t - dur * cut, dur * (1 - cut), 3.4, 4.0, roar=1.0, shake=16, tt=t)
        if k < 0.12:
            return Image.blend(im, Image.new("RGB", im.size, (255, 255, 255)), 0.7 * (1 - k / 0.12))
        return red_wash(im, 0.3)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": [dur * cut]}


# ---------------------------------------------------------------- two more monsters: the smoke djinn and the hollow man
def draw_djinn(pen, sp, ph, st):
    """A genie gone wrong: a body of black smoke with no legs, huge arms, curled horns and a burning, grinning face."""
    col = sp["color"]
    dk = dark(col, 0.55)
    t = st.get("t", 0.0)
    roar = max(st.get("roar", 0.0), 0.5)
    bob = -150 + 24 * math.sin(t * 1.8 + ph * 0.1)
    for i in range(9):  # the tail of smoke, curling away below
        k = i / 8
        x = 40 * math.sin(t * 2.0 + i * 0.9) * (0.4 + k) - 50 * k
        y = -280 + bob + 330 * k
        r = 120 - 90 * k
        pen.ell(x, y, r, r * 0.8, mix(col, (30, 30, 40), k * 0.5), rim=False)
    pen.ell(0, -440 + bob, 130, 150, col)                                           # chest
    for sgn in (1, -1):  # arms hanging low with long hands
        sway = 20 * math.sin(t * 2.3 + sgn)
        pen.line([(sgn * 110, -520 + bob), (sgn * 190 + 20, -380 + bob + sway), (sgn * 150 + 60, -230 + bob + sway * 1.5)], 38, dk)
        for k in range(4):
            pen.line([(sgn * 150 + 60, -230 + bob + sway * 1.5), (sgn * 150 + 60 + k * 14 - 20, -150 + bob + sway * 1.5 + 12 * k)], 8, (225, 215, 195))
    pen.ell(10, -620 + bob, 78, 88, col)                                             # head
    for sgn in (1, -1):  # curled horns
        pen.line([(sgn * 50 + 10, -690 + bob), (sgn * 120 + 10, -780 + bob), (sgn * 90 + 10, -860 + bob)], 22, (50, 40, 36))
    pen.ell(-24, -635 + bob, 20, 24, (6, 4, 10), rim=False)
    pen.ell(50, -635 + bob, 20, 24, (6, 4, 10), rim=False)
    eye(pen, -24, -635 + bob, 10, sp, st)
    eye(pen, 50, -635 + bob, 10, sp, st)
    mouth(pen, 14, -575 + bob, 80, roar, sp)


def draw_hollow(pen, sp, ph, st):
    """A far too tall, thin, pale man with arms to his knees, no face but a thin grin cut across it."""
    col = sp["color"]
    dk = dark(col, 0.5)
    skin = (205, 200, 190)
    t = st.get("t", 0.0)
    moving = st.get("moving", True)
    roar = max(st.get("roar", 0.0), 0.3)
    lean = 22 * math.sin(t * 0.9)
    for sgn, off in ((1, 0.0), (-1, math.pi)):  # long jointed legs
        limb(pen, 10 * sgn, -400, ph + off if moving else 0, 215, 215, 20, dk, amp=0.4, lift=0.5)
    pen.poly([(-34, -380), (46, -380), (36 + lean, -690), (-24 + lean, -690)], dk)  # long thin torso in a dark suit
    for sgn in (1, -1):  # arms down to the knees, fingers like twigs
        sw = 18 * math.sin(t * 1.6 + sgn + ph)
        pen.line([(sgn * 26 + lean, -670), (sgn * 52 + sw, -500), (sgn * 40 + sw * 1.4, -300)], 12, skin)
        for k in range(4):
            pen.line([(sgn * 40 + sw * 1.4, -300), (sgn * 40 + sw * 1.4 + (k - 1.5) * 10, -228 + 8 * k)], 4, skin)
    pen.ell(8 + lean, -770, 46, 74, skin)                                              # the smooth head
    for sgn in (1, -1):  # pin-prick eyes, no face around them
        pen.ell(8 + lean + sgn * 18, -792, 6, 6, (10, 8, 12), rim=False)
        pen.ell(8 + lean + sgn * 18, -792, 3, 3, sp["eye"], rim=False)
    pen.d.line([(pen.X(-30 + lean), pen.Y(-735)), (pen.X(8 + lean), pen.Y(-718 + 10 * roar)), (pen.X(46 + lean), pen.Y(-735))],
               fill=(20, 8, 10), width=max(3, int(8 * pen.s)), joint="curve")             # the grin
    for k in range(7):
        x = -22 + k * 11 + lean
        pen.d.line([(pen.X(x), pen.Y(-733 + 4 * abs(k - 3) * 0.4)), (pen.X(x), pen.Y(-722 + 4 * roar))],
                   fill=(235, 232, 220), width=max(2, int(3 * pen.s)))


KINDS["djinn"] = draw_djinn
KIND_SCALE["djinn"] = 1.0
HEAD_AT["djinn"] = (10, -620 - 150)
PRINT_OF["djinn"] = "frost"
KINDS["hollow"] = draw_hollow
KIND_SCALE["hollow"] = 1.0
HEAD_AT["hollow"] = (8, -770)
PRINT_OF["hollow"] = "dots"


# ---------------------------------------------------------------- more places: woods, neighbour's house, magic lamp, road, mirror
from PIL import ImageChops


def _mixc(a, b, k):
    return tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3))


def woods_scene(out, dur, world, sp, cwd=None):
    """FIRST PERSON through a dense, dark, misty wood. Trunks crowd in from both sides, moonlight falls in thin
    shafts, a flashlight bobs ahead, and something pale between the trees is walking toward you."""
    world.push, world.shake = 0.14, 5.0
    rnd = random.Random(11)
    trees = [(rnd.random(), rnd.uniform(-1.25, 1.25), rnd.uniform(0.7, 1.4), rnd.random()) for _ in range(70)]
    trees = [tr for tr in trees if abs(tr[1] + 0.16) > 0.26][:54]  # keep a path open for the figure
    snaps = [dur * (0.18 + 0.17 * k) for k in range(5)]
    hy = 860
    fog = (58, 86, 86)

    def paint(im, d, t, u, cam):
        d.rectangle((0, 0, W, H), fill=(30, 46, 46, 255))
        d.rectangle((0, 0, W, hy), fill=(46, 72, 80, 255))
        d.rectangle((0, hy, W, H), fill=(26, 38, 30, 255))
        for k in range(5):  # moonlight falling through the canopy
            x = 140 + k * 200 + 30 * math.sin(t * 0.4 + k)
            d.polygon([(x, 0), (x + 60, 0), (x + 260, hy + 300), (x + 140, hy + 300)], fill=(190, 235, 225, 34))
        zc = 0.22 + 0.62 * u
        items = [(((b + u * 0.5) % 1.0), "t", (lat, wd, sh)) for b, lat, wd, sh in trees] + [(zc, "c", None)]
        items.sort(key=lambda it: it[0])
        for z, kind, data in items:
            gy = hy + (1700 - hy) * z ** 1.5
            if kind == "c":
                s = 0.07 + 1.0 * z ** 1.7
                reveal_creature(im, sp, 540 - 160 * z, gy, s, t, _smooth((u - 0.72) / 0.06) if int(t * 24) % 9 != 0 else 0.0, facing=-1, roar=0.2 + 0.4 * z)
                continue
            lat, wd, sh = data
            x = 540 + lat * 900 * z ** 1.1
            w = (22 + 90 * wd) * z ** 1.25 + 3
            col = _mixc((4, 7, 6), fog, (1 - z) * 0.85)
            col = _mixc(col, (28, 40, 34), sh * 0.25 * (1 - z))
            top = max(gy - 2600 * z ** 1.5 - 40, -10)
            d.polygon([(x - w * 0.8, top), (x + w * 0.8, top), (x + w, gy), (x - w, gy)], fill=col + (255,))
            if z > 0.45 and sh > 0.4:  # crooked branches
                for j in range(3):
                    by = gy - (500 + 330 * j) * z ** 1.1
                    d.line([(x, by), (x + (1 if lat < 0 else -1) * (170 + 40 * j) * z, by - 90 * z)], fill=col + (255,), width=max(3, int(w * 0.18)))
            d.ellipse((x - w * 1.8, gy - 12 * z, x + w * 1.8, gy + 16 * z), fill=(160, 190, 190, int(24 * (1 - z * 0.5))))  # mist at its base
        for k in range(3):  # drifting ground mist
            y = hy + 80 + k * 180 + 24 * math.sin(t * 0.6 + k)
            d.ellipse((-200 + 120 * k, y, W + 200 - 80 * k, y + 220), fill=(150, 180, 180, 22))
        bob = 12 * math.sin(t * STEP * 0.5)
        d.polygon([(560, H), (760, H), (620 + 120 * math.sin(t * 0.7), 1000 + bob), (480 + 120 * math.sin(t * 0.7), 1000 + bob)], fill=(255, 245, 200, 32))
        dim(im, 36)
        if u > 0.9:
            return red_wash(im, 0.18 * (u - 0.9) / 0.1)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": snaps}


def neighbour_scene(out, dur, world, sp, cwd=None):
    """Across a hedge, the neighbour's house at night. A figure stands in a lit upstairs window and does not move.
    The lights go out one by one and the porch light stutters."""
    hits = [dur * f for f in (0.36, 0.56, 0.66, 0.74)]
    world.push, world.shake = 0.2, 1.5
    warm = (255, 208, 112)

    def paint(im, d, t, u, cam):
        d.rectangle((0, 0, W, H), fill=(9, 11, 28, 255))
        d.ellipse((780, 160, 900, 280), fill=(225, 230, 245, 255))
        d.ellipse((700, 80, 1000, 380), fill=(120, 140, 190, 22))
        d.rectangle((0, 1250, W, H), fill=(12, 18, 16, 255))
        d.rectangle((160, 640, 920, 1290), fill=(52, 38, 44, 255))                                  # the house
        d.polygon([(110, 650), (540, 380), (970, 650)], fill=(30, 22, 30, 255))
        d.rectangle((760, 380, 830, 560), fill=(36, 26, 34, 255))                                  # chimney
        def win(x0, y0, on, who=False):
            d.rectangle((x0, y0, x0 + 170, y0 + 190), fill=(18, 16, 22, 255))
            if on:
                d.rectangle((x0 + 8, y0 + 8, x0 + 162, y0 + 182), fill=warm + (255,))
                for r, a in ((160, 22), (100, 40)):
                    d.ellipse((x0 + 85 - r, y0 + 95 - r, x0 + 85 + r, y0 + 95 + r), fill=warm + (a,))
                if who:
                    draw_creature(d, dict(sp, color=(12, 10, 16)), x0 + 95, y0 + 182, 0.0, -1, 0.34, {"t": t, "moving": False, "roar": 0.1})
            d.line((x0 + 85, y0, x0 + 85, y0 + 190), fill=(18, 16, 22, 255), width=8)
            d.line((x0, y0 + 95, x0 + 170, y0 + 95), fill=(18, 16, 22, 255), width=8)
        a_on = u < 0.62 or (u < 0.7 and int(t * 12) % 2 == 0)
        b_on = u < 0.38
        win(230, 720, a_on, who=False)
        win(670, 720, b_on, who=u < 0.36)
        win(230, 1010, u < 0.5)
        win(670, 1010, u < 0.45)
        d.rectangle((470, 1000, 610, 1290), fill=(26, 18, 22, 255))                                 # the door
        if u > 0.74:  # the door is open a crack, something looking out
            f = min(1.0, (u - 0.74) / 0.12)
            d.rectangle((520, 1000, 560, 1290), fill=(4, 2, 6, 255))
            for ex in (532, 548):
                for r, a in ((26, 40), (14, 110), (7, 255)):
                    d.ellipse((ex - r * f, 1090 - r * f * 0.6, ex + r * f, 1090 + r * f * 0.6), fill=(255, 30, 24, a))
        porch_on = u < 0.64 or (u < 0.8 and int(t * 17) % 3 != 0)
        if porch_on:
            d.ellipse((520, 930, 560, 960), fill=warm + (255,))
            for r, a in ((200, 18), (110, 34)):
                d.ellipse((540 - r, 945 - r, 540 + r, 945 + r), fill=warm + (a,))
        for x in range(-20, W, 66):                                                                   # the fence in front
            d.polygon([(x, 1330), (x + 22, 1300), (x + 44, 1330), (x + 44, 1760), (x, 1760)], fill=(10, 10, 12, 255))
        d.rectangle((0, 1470, W, 1500), fill=(10, 10, 12, 255))
        d.rectangle((0, 1640, W, 1670), fill=(10, 10, 12, 255))
        for k in range(7):  # the hedge
            d.ellipse((-60 + k * 190, 1640, 160 + k * 190, 1900), fill=(8, 14, 10, 255))
        dim(im, 40)
        if u > 0.9:
            return red_wash(im, 0.16)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


def lamp_scene(out, dur, world, sp, cwd=None):
    """A tarnished brass lamp on a dark table. Gold light leaks from it, smoke pours out and thickens into something
    that has been waiting a very long time."""
    world.push, world.shake = 0.1, 2.0
    rnd = random.Random(5)
    puffs = [(rnd.uniform(-1, 1), rnd.random(), rnd.uniform(0.7, 1.3)) for _ in range(46)]
    hits = [dur * 0.34, dur * 0.5]

    def paint(im, d, t, u, cam):
        d.rectangle((0, 0, W, H), fill=(14, 8, 8, 255))
        d.rectangle((0, 1480, W, H), fill=(40, 24, 18, 255))
        d.rectangle((0, 1480, W, 1500), fill=(70, 44, 30, 255))
        pulse = 0.6 + 0.4 * math.sin(t * 3)
        for r, a in ((620, 20), (420, 34), (240, 60)):
            d.ellipse((540 - r, 1440 - r, 540 + r, 1440 + r), fill=(255, 190, 70, int(a * (0.5 + u) * pulse)))
        d.ellipse((380, 1380, 700, 1490), fill=(168, 118, 40, 255), outline=(90, 58, 20, 255), width=6)    # the lamp
        d.polygon([(660, 1400), (780, 1340), (800, 1360), (690, 1450)], fill=(168, 118, 40, 255), outline=(90, 58, 20, 255))
        d.arc((330, 1380, 440, 1470), 90, 270, fill=(168, 118, 40, 255), width=14)
        d.ellipse((430, 1390, 620, 1420), fill=(230, 180, 90, 130))
        for lx, ph, sz in puffs:  # the smoke, rising and widening
            k = ((t * 0.35 + ph) % 1.0)
            x = 540 + lx * 60 * (0.3 + k * 3) + 70 * math.sin(t + ph * 9)
            y = 1370 - k * 1180
            r = (26 + 100 * k) * sz * min(1.0, 0.4 + u * 1.3)
            d.ellipse((x - r, y - r, x + r, y + r), fill=(60, 40, 54, int(60 * (1 - k) + 8)))
        k = (u - 0.4) / 0.5
        if k > 0:
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ld = ImageDraw.Draw(layer)
            draw_creature(ld, sp, 540, 1560, t * 0.9, 1, 1.2, {"t": t, "moving": False, "roar": 0.3 + 0.6 * k})
            a = layer.split()[3].point(lambda v: int(v * min(1.0, k)))
            layer.putalpha(a)
            im.paste(layer, (0, 0), layer)
        dim(im, 30)
        if u > 0.88:
            return red_wash(im, 0.25 * (u - 0.88) / 0.12)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


def road_scene(out, dur, world, sp, cwd=None):
    """FIRST PERSON through a windscreen on an empty night road. Headlights cut the fog. Every time the lights flicker,
    the figure standing in the road is nearer, and in the rear-view mirror something sits in the back seat."""
    world.push, world.shake = 0.1, 3.0
    hy = 820
    stages = (0.24, 0.5, 0.74)
    rnd = random.Random(4)
    pines = [(rnd.choice((-1, 1)), rnd.random(), rnd.uniform(0.8, 1.3)) for _ in range(26)]
    hits = [dur * s for s in stages]

    def paint(im, d, t, u, cam):
        d.rectangle((0, 0, W, H), fill=(8, 10, 16, 255))
        d.rectangle((0, 0, W, hy), fill=(16, 22, 34, 255))
        d.rectangle((0, hy, W, H), fill=(10, 14, 12, 255))
        for side, b, sz in sorted(pines, key=lambda p: (p[1] + u * 1.6) % 1.0):
            z = (b + u * 1.6) % 1.0
            x = 540 + side * (140 + 900 * z ** 1.3)
            gy = hy + 30 + (1300 - hy) * z ** 1.5
            h, w = 160 + 1100 * z ** 1.5 * sz, 40 + 260 * z ** 1.3
            c = _mixc((20, 30, 36), (4, 6, 8), z)
            d.polygon([(x - w, gy), (x + w, gy), (x, gy - h)], fill=c + (255,))
        d.polygon([(500, hy), (580, hy), (1100, H - 120), (-20, H - 120)], fill=(34, 34, 40, 255))  # the road
        for k in range(8):  # the dashed centre line, rushing toward you
            z = ((k / 8) + u * 3.0) % 1.0
            y = hy + (H - 120 - hy) * z ** 1.7
            wd = 3 + 22 * z ** 1.5
            d.polygon([(540 - wd, y), (540 + wd, y), (540 + wd * 1.4, y + 10 + 90 * z ** 1.7), (540 - wd * 1.4, y + 10 + 90 * z ** 1.7)], fill=(220, 215, 160, 255))
        stage = sum(1 for s in stages if u > s)
        flick = any(0 < u - s < 0.035 for s in stages) and int(t * 20) % 2 == 0
        if stage >= 1 and not flick:
            zc = (0.18, 0.42, 0.7, 0.96)[stage - 1 + 0] if stage < 3 else 0.9
            zc = (0.2, 0.42, 0.7)[stage - 1]
            gy = hy + 30 + (H - 160 - hy) * zc ** 1.5
            reveal_creature(im, sp, 540, gy, 0.1 + 0.95 * zc ** 1.6, t, 1.0 if stage >= 3 else 0.0, facing=-1, moving=False)
        if not flick:
            d.polygon([(-100, H), (1180, H), (620, hy + 20), (460, hy + 20)], fill=(255, 245, 205, 34))   # headlights
        for r, a in ((420, 14), (260, 22)):
            d.ellipse((540 - r, hy - 30 - r * 0.2, 540 + r, hy + 60 + r * 0.2), fill=(170, 190, 200, a))   # fog bank
        d.rectangle((0, H - 220, W, H), fill=(6, 6, 8, 255))                                          # the dashboard
        d.arc((120, H - 360, 960, H + 600), 190, 350, fill=(18, 18, 22, 255), width=60)                # the steering wheel
        d.rounded_rectangle((300, 90, 780, 270), radius=40, fill=(8, 10, 14, 255), outline=(40, 44, 52, 255), width=12)  # the mirror
        d.rectangle((312, 102, 768, 258), fill=(18, 22, 30, 255))
        if u > 0.55:
            f = min(1.0, (u - 0.55) / 0.3)
            for ex in (500, 580):
                for r, a in ((30, 40), (18, 120), (8, 255)):
                    d.ellipse((ex - r * f, 180 - r * f * 0.6, ex + r * f, 180 + r * f * 0.6), fill=(255, 30, 24, a))
        dim(im, 50)
        if u > 0.9:
            return red_wash(im, 0.2 * (u - 0.9) / 0.1)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


def mirror_scene(out, dur, world, sp, cwd=None):
    """A tall mirror in a dim hall. The person's reflection is perfectly normal, but something is standing behind it,
    a little nearer each time they blink, and the glass begins to crack."""
    world.push, world.shake = 0.14, 2.0
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).ellipse((250, 430, 830, 1490), fill=255)
    hits = [dur * 0.82, dur * 0.88]

    def paint(im, d, t, u, cam):
        d.rectangle((0, 0, W, H), fill=(18, 24, 24, 255))
        for x in range(0, W, 120):
            d.line((x, 0, x, H), fill=(22, 30, 30, 255), width=4)
        d.rectangle((0, 1560, W, H), fill=(26, 18, 14, 255))
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        ld.rectangle((0, 0, W, H), fill=(60, 78, 84, 255))                      # the reflected room
        ld.rectangle((0, 1300, W, H), fill=(30, 22, 18, 255))
        ld.polygon([(0, 0), (260, 0), (330, 1300), (0, 1300)], fill=(40, 52, 56, 255))
        k = min(1.0, u / 0.85)
        draw_creature(ld, sp, 650, 1330, t * 0.7, -1, 0.16 + 0.8 * k * k, {"t": t, "moving": False, "roar": 0.1 + 0.6 * k})
        draw_human(ld, 430, 1400, 0.0, 1, 0.95, lamp=None, cast=False)
        for r, a in ((520, 30),):
            ld.ellipse((540 - r, 960 - r, 540 + r, 960 + r), fill=(200, 230, 235, a))
        layer.putalpha(ImageChops.multiply(layer.split()[3], mask))
        im.paste(layer, (0, 0), layer)
        d.ellipse((250, 430, 830, 1490), outline=(150, 118, 52, 255), width=34)    # the frame
        d.ellipse((220, 400, 860, 1520), outline=(80, 60, 26, 255), width=14)
        for k2 in range(5):  # fog and drips on the glass
            x = 330 + k2 * 100
            d.line((x, 520, x + 6, 520 + 120 + 90 * k2 % 260), fill=(210, 230, 235, 40), width=6)
        if u > 0.8:  # the glass cracks
            f = min(1.0, (u - 0.8) / 0.12)
            for j in range(9):
                a = j * 0.7 + 0.3
                pts = [(540, 940)]
                for s in range(1, 5):
                    pts.append((540 + math.cos(a + s * 0.15) * 90 * s * f, 940 + math.sin(a - s * 0.1) * 130 * s * f))
                d.line(pts, fill=(235, 240, 245, 230), width=4)
        dim(im, 55)
        if u > 0.9:
            return red_wash(im, 0.2)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


# ---------------------------------------------------------------- the creature DOING things: digging, taking off
def _sprite(sp, scale, t, facing=1, moving=False, roar=0.2, wings=0.0, flying=False):
    """The creature on its own transparent layer. Returns (layer, (feet_x, feet_y), s). `wings` (0..1.6) draws
    leathery bat wings that fold out and flap, for creatures that need to take off."""
    s = 1.4 * scale * sp.get("scale", 1.0) * KIND_SCALE.get(sp["kind"], 1.0)
    if sp["kind"] == "dragon":  # it has wings of its own: they just have to beat
        if wings > 0:
            sp = dict(sp, flying=True)
        wings = 0.0
    pw, ph = int(1500 * s) + 80, int(1500 * s) + 80
    layer = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer, "RGBA")
    fx, fy = pw / 2, ph - 70
    if wings > 0:
        sh = (fx - facing * 20 * s, fy - 560 * s)  # roughly the shoulders
        flap = math.sin(t * (7 + 5 * min(wings, 1.0))) * 0.55
        for side in (-1, 1):
            ang0 = -0.5 + 1.1 * min(wings, 1.0) + flap
            span = 640 * s * min(wings, 1.0)
            pts = [sh]
            for k, (ra, rl) in enumerate(((1.0, 0.55), (0.6, 1.0), (0.2, 0.9), (-0.25, 0.8), (-0.65, 0.5))):
                a = ang0 + ra * 0.55 + 0.0
                pts.append((sh[0] + side * span * rl * math.cos(a), sh[1] - span * rl * math.sin(a) * 1.0))
            pts.append((sh[0] + side * 30 * s, sh[1] + 150 * s))
            ld.polygon(pts, fill=(18, 12, 22, 235))
            for p in pts[1:-1]:
                ld.line([sh, p], fill=(70, 48, 76, 255), width=max(2, int(6 * s)))
    st = {"t": t, "moving": moving, "roar": roar, "noshadow": bool(wings > 0 or flying)}
    _REC_OFF[0], _REC_OFF[1] = 10 ** 6, 10 ** 6
    try:
        draw_creature(ld, sp, fx, fy, t * STEP * 0.3, facing, scale, st)
    finally:
        _REC_OFF[0], _REC_OFF[1] = 0, 0
        if _BOXES:
            _BOXES.pop()
    return layer, (fx, fy), s


def _put_sprite(im, layer, anchor, x, y, angle=0.0, zoom=1.0):
    """Paste a creature layer so that its feet are at (x, y), turned by `angle` degrees about the feet."""
    if angle:
        layer = layer.rotate(angle, center=anchor, resample=Image.BILINEAR)
    if zoom != 1.0:
        layer = layer.resize((max(2, int(layer.width * zoom)), max(2, int(layer.height * zoom))), Image.BILINEAR)
        anchor = (anchor[0] * zoom, anchor[1] * zoom)
    im.paste(layer, (int(x - anchor[0]), int(y - anchor[1])), layer)
    return (x - 330 * zoom, y - 1100 * zoom, x + 330 * zoom, y + 40 * zoom)


def dig_scene(out, dur, world, sp, cwd=None):
    """The creature digs: it leans in and rips at the earth, a pit opens at its feet, a mound grows, clods of dirt fly."""
    gy, hx = 1480, 760
    period = 0.95
    first = 0.35
    strokes = [first + period * k for k in range(int((dur - first) / period))]
    hits = [x + period * 0.5 for x in strokes if x + period * 0.5 < dur - 0.1]
    world.push, world.shake = 0.14, 1.5
    rnd = random.Random(4)
    clods = []
    for k, ts in enumerate(strokes):
        for _ in range(9):
            clods.append((ts + period * 0.5, rnd.uniform(-160, 420), rnd.uniform(-1250, -700), rnd.uniform(12, 30)))

    def paint(im, d, t, u, cam):
        pit = 60 + 330 * _smooth(u * 1.15)
        d.ellipse((hx - pit * 0.62, gy - 26, hx + pit * 0.62, gy + 26 + 30 * u), fill=(2, 1, 1, 255))      # the pit
        d.ellipse((hx - pit * 0.7, gy - 38, hx + pit * 0.7, gy + 38 + 30 * u), outline=(118, 88, 58, 255), width=14)
        mh = 30 + 170 * _smooth(u * 1.1)  # the heap of earth behind it
        d.polygon([(900, gy + 22), (1000, gy - mh), (1160, gy - mh * 0.7), (1260, gy + 22)], fill=(96, 72, 48, 255), outline=(60, 44, 30, 255))
        ph = ((t - first) / period) % 1.0 if t >= first else 0.0
        lean = -3 - 16 * math.sin(math.pi * ph) ** 2
        layer, anc, s = _sprite(sp, 0.78, t, facing=1, moving=False, roar=0.15 + 0.25 * math.sin(math.pi * ph))
        box = _put_sprite(im, layer, anc, 440, gy - 4, angle=lean)
        _BOXES.append(box)
        for ts, vx, vy, r in clods:  # flying dirt
            k = t - ts
            if 0 <= k < 1.1:
                x = hx + vx * k * 1.6
                y = gy - 20 + vy * k + 1500 * k * k * 0.5
                d.ellipse((x - r, y - r, x + r, y + r), fill=(150, 116, 78, int(255 * (1 - k / 1.1))))
        dim(im, 34)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


def fly_scene(out, dur, world, sp, cwd=None):
    """The creature crouches, unfolds huge wings, beats them, kicks up a ring of dust and lifts off into the night sky."""
    gy = 1500
    lift0 = 0.34
    hits = [dur * lift0] + [dur * (lift0 + 0.1 * k) for k in range(1, 5) if dur * (lift0 + 0.1 * k) < dur - 0.1]
    world.push, world.shake = 0.0, 2.0
    world.focus_y = 0.5

    def paint(im, d, t, u, cam):
        wings = min(1.6, 0.25 + 3.4 * u) if u < lift0 else 1.6
        crouch = 0.06 * math.sin(math.pi * min(1.0, u / lift0)) if u < lift0 else 0.0
        p = _smooth((u - lift0) / (1 - lift0)) if u > lift0 else 0.0
        rise = 1350 * p ** 1.3
        zoom = 1.0 - 0.45 * p
        if u > lift0 and u < lift0 + 0.35:  # dust thrown out around the take-off spot
            k = (u - lift0) / 0.35
            for r in (1.0, 0.7, 0.45):
                d.ellipse((540 - 520 * k * r, gy - 30 * k * r - 6, 540 + 520 * k * r, gy + 36 * k * r), fill=(140, 130, 120, int(110 * (1 - k))))
        layer, anc, s = _sprite(sp, 0.74 * (1 - crouch), t, facing=1, moving=False, roar=0.5, wings=wings)
        lean = -10 * p
        box = _put_sprite(im, layer, anc, 540, gy - rise, angle=lean, zoom=zoom)
        _BOXES.append(box)
        dim(im, 30)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


# ---------------------------------------------------------------- nature and epic scales: sea, lake, lava, tornado, tsunami, titan
def _gradient(top, bottom, h=H):
    g = Image.new("RGB", (W, h))
    gd = ImageDraw.Draw(g)
    for y in range(h):
        k = y / max(h - 1, 1)
        gd.line((0, y, W, y), fill=tuple(int(a + (b - a) * k) for a, b in zip(top, bottom)))
    return g


def _moon(d, x, y, r, col=(226, 228, 210)):
    for rr, a in ((r * 3.2, 14), (r * 2.2, 26), (r * 1.5, 46)):
        d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=col + (a,))
    d.ellipse((x - r, y - r, x + r, y + r), fill=col + (255,))


def _cut_below(layer, anchor, y_feet, y_line):
    """Hide the part of a creature layer that is below a water or lava line (so it looks submerged)."""
    cut = int(y_line - (y_feet - anchor[1]))
    if cut < layer.height:
        a = layer.getchannel("A")
        ImageDraw.Draw(a).rectangle((0, max(cut, 0), layer.width, layer.height), fill=0)
        layer.putalpha(a)


def _water_scene(out, dur, world, sp, mode, cwd=None):
    """Something enormous under dark water: a boat and a lantern, a shadow sliding underneath, bubbles, then it surfaces."""
    ocean = mode == "ocean"
    hy = 860 if ocean else 900
    sky = _gradient((10, 16, 40), (44, 70, 104), hy) if ocean else _gradient((70, 86, 98), (170, 184, 188), hy)
    wcol = ((14, 44, 74), (3, 9, 18)) if ocean else ((54, 78, 86), (10, 20, 26))
    amp = 1.0 if ocean else 0.28
    ex, wl = 690, hy + 330                           # where it surfaces, and the water line there
    rnd = random.Random(6)
    bubbles = [(ex + rnd.uniform(-170, 170), rnd.uniform(0, 1), rnd.uniform(5, 14)) for _ in range(26)]
    hits = [dur * 0.28, dur * 0.56, dur * 0.82]
    world.push, world.shake = 0.1, 2.5 if ocean else 1.0

    def wave_y(x, k, t):
        return amp * (14 + 5 * k) * math.sin(x * (0.006 + 0.002 * k) + t * (1.2 + 0.4 * k) + k * 1.7)

    def paint(im, d, t, u, cam):
        im.paste(sky)
        d = ImageDraw.Draw(im, "RGBA")
        if ocean:
            _moon(d, 790, 330, 70)
        else:
            _moon(d, 760, 380, 60, (220, 226, 226))
            for k in range(4):
                d.ellipse((-200 + k * 300 + 40 * math.sin(t * 0.3 + k), hy - 140 - k * 30, 500 + k * 300, hy + 40), fill=(220, 228, 230, 40))
        d.rectangle((0, hy, W, H), fill=wcol[0] + (255,))
        rows = 9
        for k in range(rows):
            y0 = hy + 30 + k * (H - hy) / rows
            col = tuple(int(a + (b - a) * k / (rows - 1)) for a, b in zip(*wcol))
            pts = [(x, y0 + wave_y(x, k, t)) for x in range(0, W + 30, 30)]
            d.polygon(pts + [(W, H), (0, H)], fill=col + (255,))
            d.line(pts, fill=(150, 180, 200, 70 if ocean else 40), width=3)
        # the boat with its lantern
        bx, by = 300, hy + 230 + wave_y(300, 3, t) * 0.8
        tilt = 0.08 * math.sin(t * 1.3)
        d.polygon([(bx - 190, by), (bx + 190, by), (bx + 130, by + 80), (bx - 130, by + 80)], fill=(34, 22, 18, 255))
        d.line((bx, by, bx + tilt * 200, by - 300), fill=(40, 28, 22, 255), width=12)
        d.polygon([(bx + 8, by - 290), (bx + 150, by - 70), (bx + 8, by - 70)], fill=(150, 140, 120, 235))
        fl = 0.85 + 0.15 * math.sin(t * 17)
        for r, a in ((120, 30), (70, 60)):
            d.ellipse((bx - 120 - r, by - 70 - r, bx - 120 + r, by - 70 + r), fill=(255, 190, 90, int(a * fl)))
        d.ellipse((bx - 130, by - 82, bx - 110, by - 58), fill=(255, 230, 150, 255))
        d.line((bx - 120, by - 58, bx - 120, by), fill=(34, 22, 18, 255), width=6)
        # a shadow slides under the surface, bubbles rise
        if 0.18 < u < 0.7:
            k = min(1.0, (u - 0.18) / 0.3)
            sx = 140 + (ex - 140) * k
            d.ellipse((sx - 360 * k, wl + 60, sx + 360 * k, wl + 210), fill=(0, 2, 6, int(150 * k)))
        for bx0, ph, r in bubbles:
            if u > 0.25:
                yb = wl + 200 - ((t * 0.5 + ph) % 1.0) * 260
                d.ellipse((bx0 - r, yb - r, bx0 + r, yb + r), outline=(190, 220, 235, 120), width=2)
        # it surfaces
        p = _smooth((u - 0.52) / 0.38)
        if p > 0:
            for k in range(3):  # rings spread over the water
                rr = (t * 160 + k * 90) % 420 * p
                d.ellipse((ex - rr * 1.6, wl - rr * 0.22, ex + rr * 1.6, wl + rr * 0.22), outline=(200, 225, 240, int(110 * (1 - rr / 420))), width=4)
            layer, anc, s = _sprite(sp, 1.15 if ocean else 1.2, t, facing=-1, moving=False, roar=0.45 * p)
            y_feet = wl + (160 if ocean else 330) + (1.0 - p) * 900
            _cut_below(layer, anc, y_feet, wl)
            box = _put_sprite(im, layer, anc, ex, y_feet, angle=0)
            _BOXES.append(box)
            d = ImageDraw.Draw(im, "RGBA")
            for k in range(2):  # foam and swell where the water meets the body
                d.ellipse((ex - 230 - 40 * k, wl - 14 + 8 * k, ex + 230 + 40 * k, wl + 26 + 8 * k), outline=(210, 232, 244, 150 - 50 * k), width=5)
        dim(im, 26 if ocean else 14)
        return None
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


def ocean_scene(out, dur, world, sp, cwd=None):
    return _water_scene(out, dur, world, sp, "ocean", cwd)


def lake_scene(out, dur, world, sp, cwd=None):
    return _water_scene(out, dur, world, sp, "lake", cwd)


def lava_scene(out, dur, world, sp, cwd=None):
    """A volcano wakes. Rivers of lava crawl down, embers rise, and something stands up out of the molten rock."""
    sky = _gradient((16, 4, 6), (110, 34, 12), 1000)
    rnd = random.Random(12)
    embers = [(rnd.uniform(0, W), rnd.uniform(0, 1), rnd.uniform(3, 9), rnd.uniform(0.3, 1.0)) for _ in range(70)]
    hits = [dur * 0.25, dur * 0.55, dur * 0.8]
    ex, ll = 540, 1470
    world.push, world.shake = 0.12, 4.0

    def paint(im, d, t, u, cam):
        im.paste(sky, (0, 0))
        im.paste(Image.new("RGB", (W, H - 1000), (10, 4, 4)), (0, 1000))
        d = ImageDraw.Draw(im, "RGBA")
        d.polygon([(120, 1000), (470, 420), (610, 420), (960, 1000)], fill=(14, 6, 6, 255))              # the volcano
        for r, a_ in ((260, 40), (160, 70), (90, 120)):
            d.ellipse((540 - r, 420 - r * 0.5, 540 + r, 420 + r * 0.5), fill=(255, 120, 30, a_))
        for k in range(5):  # lava running down its sides
            x0 = 500 + k * 20
            pts = [(x0 + (k - 2) * 28 * (j / 6) + 10 * math.sin(t * 2 + j + k), 430 + j * 95) for j in range(7)]
            d.line(pts, fill=(255, 140 + k * 16, 40, 255), width=9 - k)
        d.rectangle((0, 1000, W, 1120), fill=(18, 8, 6, 255))                                              # the rim of cooled rock
        for k in range(14):  # a lake of lava: a bright body, drifting dark crust, glowing cracks
            y = 1110 + k * 58
            kk = k / 13
            c = (int(150 + 100 * kk), int(34 + 110 * kk), 8)
            d.rectangle((0, y, W, y + 62), fill=c + (255,))
            pts = [(x, y + 28 + 12 * math.sin(x * 0.015 + t * 1.6 + k)) for x in range(0, W + 40, 40)]
            d.line(pts, fill=(255, 226, 120, 190), width=4)
            for j in range(4):
                xx = ((j * 0.31 + k * 0.17 + t * 0.03) % 1.0) * (W + 200) - 100
                d.ellipse((xx - 110, y + 14, xx + 110, y + 50), fill=(34, 12, 8, 150))
        p = _smooth((u - 0.38) / 0.45)
        if p > 0:
            layer, anc, s_ = _sprite(sp, 1.0, t, facing=1, moving=False, roar=0.5 * p)
            y_feet = ll - 20 + 800 * (1 - p)
            _cut_below(layer, anc, y_feet, ll)
            box = _put_sprite(im, layer, anc, ex, y_feet, angle=0)
            _BOXES.append(box)
            d = ImageDraw.Draw(im, "RGBA")
            d.ellipse((ex - 300, ll - 30, ex + 300, ll + 50), outline=(255, 210, 90, 220), width=8)           # molten rim
            for j in range(9):
                xx = ex - 240 + j * 60
                yy = ll - 10 + ((t * 0.9 + j * 0.31) % 1.0) * 260
                d.line((xx, ll - 8, xx + 4, yy), fill=(255, 150, 40, 200), width=7)                           # lava running off its body
            for r, a_ in ((520, 24), (340, 40)):
                d.ellipse((ex - r, ll - 700 * p - r * 0.4, ex + r, ll - 700 * p + r), fill=(255, 100, 20, a_))
        for x, ph, r, sp_ in embers:
            yy = 1500 - ((t * 0.25 * sp_ + ph) % 1.0) * 1500
            d.ellipse((x + 30 * math.sin(t * 2 + ph * 9) - r, yy - r, x + 30 * math.sin(t * 2 + ph * 9) + r, yy + r), fill=(255, 170, 60, 200))
        dim(im, 8)
        if u > 0.9:
            return red_wash(im, 0.18)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


def tornado_scene(out, dur, world, sp, cwd=None):
    """A black funnel crosses open farmland, flinging debris. Lightning flickers inside it and, for a moment, so do eyes."""
    sky = _gradient((22, 30, 28), (86, 100, 88), 1450)
    rnd = random.Random(21)
    debris = [(rnd.uniform(0, 6.28), rnd.uniform(0.1, 1.0), rnd.uniform(6, 18), rnd.uniform(1.5, 4.0)) for _ in range(70)]
    flashes = [dur * 0.2, dur * 0.47, dur * 0.7, dur * 0.88]
    hits = list(flashes)
    world.push, world.shake = 0.2, 5.0

    def cx(y, t):
        k = (1450 - y) / 1150
        return 600 + 130 * math.sin(t * 0.8) * (1 - k * 0.3) + 110 * math.sin(t * 1.4 + y * 0.006) * k

    def wd(y):
        k = max(0.0, (1450 - y) / 1150)
        return 38 + 400 * k ** 1.25

    def paint(im, d, t, u, cam):
        im.paste(sky, (0, 0))
        d = ImageDraw.Draw(im, "RGBA")
        for k in range(5):  # a roof of boiling cloud
            d.ellipse((-200 + k * 300 + 40 * math.sin(t + k), 120 + 30 * k, 480 + k * 300, 420 + 30 * k), fill=(24, 30, 28, 255))
        d.rectangle((0, 1450, W, H), fill=(26, 32, 22, 255))
        for k in range(14):
            d.line((0, 1480 + k * 34, W, 1470 + k * 34), fill=(36, 44, 30, 255), width=3)
        d.rectangle((120, 1360, 290, 1450), fill=(14, 12, 12, 255))                                         # a barn
        d.polygon([(100, 1360), (205, 1290), (310, 1360)], fill=(14, 12, 12, 255))
        y = 360
        while y < 1450:
            w = wd(y)
            shade = 0.5 + 0.5 * math.sin(y * 0.045 - t * 9)
            c = int(26 + 36 * shade)
            d.ellipse((cx(y, t) - w, y - 14, cx(y, t) + w, y + 14), fill=(c, c + 6, c + 2, 255))
            y += 8
        for a0, hgt, r, spd in debris:  # whirling debris
            yy = 1450 - hgt * 1050
            ang = a0 + t * spd * 2.2
            xx = cx(yy, t) + math.cos(ang) * wd(yy) * 1.15
            if math.sin(ang) > -0.2:
                d.rectangle((xx - r, yy - r * 0.5, xx + r, yy + r * 0.5), fill=(12, 10, 8, 255))
        d.ellipse((cx(1450, t) - 280, 1420, cx(1450, t) + 280, 1480), fill=(70, 66, 58, 160))             # dust at the foot
        for ft in flashes:
            k = t - ft
            if 0 <= k < 0.25:
                d.polygon([(cx(500, t), 380), (cx(700, t) - 40, 700), (cx(700, t) + 20, 700), (cx(1000, t) - 60, 1100), (cx(1000, t), 1100), (cx(1200, t), 1300)],
                          fill=(230, 240, 255, int(255 * (1 - k * 4))))
        if u > 0.55:  # inside the funnel, something looks back
            f = min(1.0, (u - 0.55) / 0.15) * (0.4 + 0.6 * (int(t * 9) % 3 != 0))
            ey = 880
            for dx in (-40, 40):
                for r, a in ((46, 40), (26, 110), (12, 255)):
                    d.ellipse((cx(ey, t) + dx - r * f, ey - r * f * 0.6, cx(ey, t) + dx + r * f, ey + r * f * 0.6), fill=(255, 40, 26, a))
        dim(im, 22)
        for ft in flashes:
            if 0 <= t - ft < 0.12:
                return Image.blend(im, Image.new("RGB", im.size, (235, 240, 255)), 0.5 * (1 - (t - ft) / 0.12))
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


def tsunami_scene(out, dur, world, sp, cwd=None):
    """The sea pulls back from the shore, wet sand and stranded boats, then a wall of water climbs the horizon with
    something vast rising behind it, and the whole frame goes to foam."""
    sky = _gradient((8, 12, 34), (46, 66, 104), 820)
    rnd = random.Random(33)
    foam = [rnd.uniform(0, 1) for _ in range(40)]
    hits = [dur * 0.2, dur * 0.45, dur * 0.88]
    world.push, world.shake = 0.14, 3.0

    def paint(im, d, t, u, cam):
        im.paste(sky, (0, 0))
        d = ImageDraw.Draw(im, "RGBA")
        _moon(d, 300, 300, 64)
        draw_back = _smooth(u / 0.35)
        sea_y = 820 + 220 * draw_back          # the sea drains away, exposing wet sand
        d.rectangle((0, 820, W, H), fill=(54, 46, 40, 255))
        d.rectangle((0, 820, W, sea_y), fill=(14, 38, 66, 255))
        for k in range(8):
            d.line((0, 1000 + k * 120, W, 1000 + k * 120 + 14), fill=(70, 62, 54, 255), width=4)
        d.polygon([(700, 1500), (960, 1500), (900, 1560), (760, 1560)], fill=(24, 16, 12, 255))                # a stranded boat
        d.line((830, 1500, 840, 1360), fill=(24, 16, 12, 255), width=10)
        p = _smooth((u - 0.2) / 0.55)
        surge = _smooth((u - 0.76) / 0.16)
        top = 800 - 330 * p - 900 * surge
        q = _smooth((u - 0.3) / 0.4)
        if q > 0:  # a face rising behind the wall of water; its jaw is hidden by the wave
            dim(im, 80)
            ax_, ay_ = head_anchor(sp)
            draw_creature(d, sp, 0, 0, t * 2.0, 1, 1.35 + 0.3 * q, {"t": t, "roar": 0.2 + 0.5 * q, "moving": False},
                          focus=(ax_, ay_, 540 + 3 * math.sin(t * 90), 360 + 260 * (1 - q)))
            d = ImageDraw.Draw(im, "RGBA")
        if p > 0:
            xs = list(range(0, W + 20, 20))
            pts = [(x, top + 26 * math.sin(x * 0.012 + t * 2) + 10 * math.sin(x * 0.05 + t * 5)) for x in xs]
            d.polygon(pts + [(W, 1920), (0, 1920)], fill=(10, 40, 72, 255))
            for k in range(1, 9):  # darker, deeper water lower in the wall
                d.polygon([(x, y + 90 * k) for x, y in pts] + [(W, 1920), (0, 1920)], fill=(8 - k // 2, 32 - 2 * k, 62 - 3 * k, 255))
            d.line(pts, fill=(235, 245, 250, 255), width=16)
            for f in foam:
                xx = f * W
                yy = top + 20 + (t * 70 + f * 300) % 200
                d.ellipse((xx - 22, yy - 8, xx + 22, yy + 8), fill=(235, 245, 250, 150))
        dim(im, 20)
        if u > 0.82:
            k = min(1.0, (u - 0.82) / 0.14)
            return Image.blend(im, Image.new("RGB", im.size, (214, 232, 242)), k * 0.92)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


def _cloud_layer(seed, w=2400, h=700, n=34):
    rnd = random.Random(seed)
    lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ld = ImageDraw.Draw(lay)
    for _ in range(n):
        x, y = rnd.uniform(0, w), rnd.uniform(120, h - 120)
        rw, rh = rnd.uniform(160, 380), rnd.uniform(60, 130)
        c = rnd.randint(150, 215)
        ld.ellipse((x - rw, y - rh, x + rw, y + rh), fill=(c, c, c + 10, rnd.randint(110, 200)))
    return lay.filter(ImageFilter.GaussianBlur(34))


def giant_scene(out, dur, world, sp, cwd=None):
    """A titan, five hundred feet tall. Mountains and a few lit windows far below show the scale; its face comes
    out of the cloud bank while the clouds drift over it."""
    sky = _gradient((30, 32, 62), (150, 120, 130), 1500)
    c1, c2 = _cloud_layer(5), _cloud_layer(9, n=40)
    hits = [dur * 0.25, dur * 0.6]
    world.push, world.shake = 0.0, 2.0

    def paint(im, d, t, u, cam):
        im.paste(sky, (0, 0))
        d = ImageDraw.Draw(im, "RGBA")
        closeup(im, d, sp, t, dur, 1.9, 2.35, roar=0.15 + 0.5 * _smooth((u - 0.5) / 0.4), shake=3.5, tt=t)
        d = ImageDraw.Draw(im, "RGBA")
        cover = 0.95 - 0.8 * _smooth(u / 0.65)  # clouds hide the face at first, then pull apart
        lay = c1.copy()
        lay.putalpha(lay.getchannel("A").point(lambda v: int(v * cover)))
        im.paste(lay.crop((int(t * 60) % 1300, 0, int(t * 60) % 1300 + W, 700)), (0, 300), lay.crop((int(t * 60) % 1300, 0, int(t * 60) % 1300 + W, 700)))
        # the world far below: ridges and a few tiny lights
        d.polygon([(0, 1760), (180, 1640), (360, 1730), (560, 1590), (780, 1720), (940, 1650), (W, 1740), (W, H), (0, H)], fill=(8, 10, 20, 255))
        for x, y in ((220, 1760), (250, 1768), (640, 1730), (680, 1738), (880, 1740)):
            d.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(255, 210, 120, 255))
        l2 = c2.crop((int(t * 110 + 400) % 1300, 0, int(t * 110 + 400) % 1300 + W, 700))
        l2a = l2.copy()
        l2a.putalpha(l2.getchannel("A").point(lambda v: int(v * 0.85)))
        im.paste(l2a, (0, 1180), l2a)
        _BOXES.append((0, 200, W, 1500))
        dim(im, 10)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


# ---------------------------------------------------------------- the age of dinosaurs: valley, eruption, birth, clash, sky, reign
def _fern(d, x, y, size, col, t=0.0, seed=0):
    """A cycad or tree-fern: a thick trunk and a crown of drooping leaves."""
    rnd = random.Random(seed)
    top = (x + 10 * math.sin(t * 0.6 + seed), y - 190 * size)
    d.line([(x, y), top], fill=col + (255,), width=max(3, int(16 * size)))
    for i in range(11):
        a = math.pi * (0.08 + 0.84 * i / 10.0)
        L = (150 + rnd.uniform(-20, 30)) * size
        sway = 6 * math.sin(t * 1.1 + i + seed)
        mx, my = top[0] + math.cos(a) * L * 0.55, top[1] - math.sin(a) * L * 0.62
        tx, ty = top[0] + math.cos(a) * L, top[1] - math.sin(a) * L * 0.45 + 0.5 * L * abs(math.cos(a)) + sway
        nx, ny = -math.sin(a), math.cos(a)
        w = 11 * size
        d.polygon([top, (mx + nx * w, my + ny * w), (tx, ty), (mx - nx * w, my - ny * w)], fill=col + (255,))


def _ptero(d, x, y, s, t, col, ph=0.0, flip=1):
    """A pterosaur seen from the side, wings beating."""
    flap = math.sin(t * 5 + ph)
    c = col + (255,)
    d.ellipse((x - 22 * s, y - 9 * s, x + 22 * s, y + 9 * s), fill=c)
    d.polygon([(x + flip * 16 * s, y - 8 * s), (x + flip * 82 * s, y - 1 * s), (x + flip * 16 * s, y + 4 * s)], fill=c)
    d.polygon([(x + flip * 12 * s, y - 8 * s), (x + flip * 2 * s, y - 32 * s), (x + flip * 30 * s, y - 9 * s)], fill=c)
    for side, k in ((-1, 1.0), (1, 0.8)):
        tip = (x + side * 150 * s * k, y - 80 * s * flap * k + 6 * s)
        d.polygon([(x - side * 2 * s, y - 2 * s), tip, (x + side * 96 * s * k, y - 16 * s * flap + 14 * s), (x + side * 40 * s, y + 18 * s)], fill=c)


def _ash(d, t, n, seed, color=(70, 66, 70), speed=0.18, size=5):
    rnd = random.Random(seed)
    for _ in range(n):
        x, ph, sz, sp_ = rnd.uniform(0, W), rnd.uniform(0, 1), rnd.uniform(0.5, 1.4), rnd.uniform(0.6, 1.3)
        y = ((t * speed * sp_ + ph) % 1.0) * (H + 60) - 30
        xx = x + 40 * math.sin(t * 0.8 + ph * 12)
        r = size * sz
        d.ellipse((xx - r, y - r, xx + r, y + r), fill=color + (190,))


def _ember_rain(d, t, n, seed):
    rnd = random.Random(seed)
    for _ in range(n):
        x, ph, sp_ = rnd.uniform(0, W), rnd.uniform(0, 1), rnd.uniform(0.5, 1.2)
        y = H - ((t * 0.22 * sp_ + ph) % 1.0) * H
        xx = x + 36 * math.sin(t * 1.5 + ph * 9)
        r = rnd.uniform(2.5, 7)
        d.ellipse((xx - r, y - r, xx + r, y + r), fill=(255, 170, 60, 210))


def _volcano(d, cx, base, h, w, t, glow=1.0, erupt=0.0):
    d.polygon([(cx - w, base), (cx - w * 0.16, base - h), (cx + w * 0.16, base - h), (cx + w, base)], fill=(26, 14, 16, 255))
    for r, a in ((w * 0.5, 40), (w * 0.3, 70), (w * 0.15, 130)):
        d.ellipse((cx - r, base - h - r * 0.45, cx + r, base - h + r * 0.45), fill=(255, 120, 30, int(a * glow)))
    for k in range(4):
        x0 = cx - w * 0.1 + k * w * 0.07
        pts = [(x0 + (k - 1.5) * w * 0.12 * (j / 6) + 8 * math.sin(t * 2 + j + k), base - h + 20 + j * h / 6.5) for j in range(7)]
        d.line(pts, fill=(255, 130 + k * 20, 40, 255), width=max(3, int(w * 0.03) - k))


def valley_scene(out, dur, world, sp, cwd=None):
    """Dusk in the age of dinosaurs. A distant volcano smokes, long-necked giants graze, and the old king of the
    valley, a tyrant lizard, strides through the ferns and roars."""
    sky = _gradient((250, 176, 102), (74, 36, 44), 1250)
    rex, sau = rex_spec(), sauro_spec((60, 44, 56))
    hits = [dur * 0.52]
    world.push, world.shake = 0.08, 0.0
    herd = [(0.1, 0.62), (0.42, 0.5), (0.74, 0.58)]

    def paint(im, d, t, u, cam):
        im.paste(sky, (0, 0))
        d = ImageDraw.Draw(im, "RGBA")
        for r, a in ((360, 24), (250, 44), (170, 90)):
            d.ellipse((300 - r, 520 - r, 300 + r, 520 + r), fill=(255, 214, 140, a))                      # the sun
        d.ellipse((300 - 140, 520 - 140, 300 + 140, 520 + 140), fill=(255, 236, 190, 255))
        _volcano(d, 800, 1180, 520, 420, t, 0.7)
        for k in range(12):                                                                             # its smoke, drifting
            q = k / 11.0
            yy = 690 - k * 70
            xx = 800 + k * 34 + 26 * math.sin(t * 0.5 + k * 1.3)
            rr = 46 + 78 * q + 14 * math.sin(k * 2.3)
            d.ellipse((xx - rr, yy - rr * 0.8, xx + rr, yy + rr * 0.8), fill=(64, 46, 54, int(150 - 60 * q)))
        for i in range(6):
            _ptero(d, ((t * (50 + 12 * i) + i * 260) % (W + 400)) - 200, 330 + 70 * (i % 3) + 24 * math.sin(t + i), 0.5 + 0.1 * (i % 3), t, (40, 24, 36), ph=i)
        d.polygon([(0, 1180), (260, 1120), (540, 1170), (820, 1110), (W, 1160), (W, H), (0, H)], fill=(52, 34, 42, 255))   # far hills
        for x0, sc in herd:
            x = ((x0 + t * 0.012) % 1.2) * 1200 - 60
            draw_creature(d, sau, x, 1240, t * STEP * 0.18, 1, sc * 0.6, {"t": t, "moving": True})
        d.rectangle((0, 1250, W, H), fill=(34, 24, 30, 255))
        for i in range(9):
            _fern(d, 80 + i * 130 + 30 * math.sin(i * 7), 1320 + 18 * (i % 3), 0.5, (36, 52, 36), t, i)
        x = -280 + 1000 * u ** 0.9
        roar = 0.2 + 0.8 * _smooth((u - 0.4) / 0.1) * (1 - _smooth((u - 0.62) / 0.12))
        draw_creature(d, rex, x, 1640, t * STEP * 0.3, 1, 1.0, {"t": t, "moving": True, "roar": roar})
        _BOXES.append((x - 420, 1050, x + 520, 1700))
        _fern(d, 90, 1960, 1.5, (10, 16, 12), t, 31)
        _fern(d, 1000, 1990, 1.7, (10, 16, 12), t, 32)
        _ash(d, t, 22, 5, (110, 90, 90), 0.05, 3)
        dim(im, 12)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


def erupt_scene(out, dur, world, sp, cwd=None):
    """The ground splits. A volcano blows its top (or, in a meteor story, the sky itself burns), the ash cloud rolls out and
    every animal in the valley runs, even the king."""
    meteor = getattr(world, "mode", "") == "meteor"
    sky = _gradient((20, 8, 10), (180, 52, 20), 1250)
    rex, sau = rex_spec(), sauro_spec((30, 22, 28))
    hits = [dur * 0.22, dur * 0.5, dur * 0.78]
    rnd = random.Random(21)
    world.push, world.shake = 0.1, 7.0
    bombs = [(rnd.uniform(-1, 1), rnd.uniform(0.6, 1.4), rnd.uniform(0, 1)) for _ in range(34)]
    fire = [(rnd.uniform(0.1, 0.9), rnd.uniform(0.3, 1.0), rnd.uniform(0.0, 0.8)) for _ in range(9)]
    flashes = [dur * f for f in (0.18, 0.44, 0.7)]

    def paint(im, d, t, u, cam):
        im.paste(sky, (0, 0))
        d = ImageDraw.Draw(im, "RGBA")
        d.rectangle((0, 1180, W, H), fill=(18, 8, 10, 255))
        if not meteor:
            _volcano(d, 560, 1180, 640, 440, t, 1.6, u)
            for k in range(30):                                                                          # a fountain of molten rock
                ph = (t * 0.5 + bombs[k % len(bombs)][2] + k * 0.13) % 1.0
                a = bombs[k % len(bombs)][0]
                x = 560 + a * 520 * ph
                y = 490 - 760 * ph * (1 - ph) * 1.6 * bombs[k % len(bombs)][1] + 420 * ph * ph
                r = 9 + 12 * (1 - ph)
                d.ellipse((x - r * 2, y - r * 2, x + r * 2, y + r * 2), fill=(255, 110, 30, 70))
                d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 190, 80, 235))
            g = _smooth(u / 0.55)
            for r, a_ in ((460, 30), (300, 50)):
                d.ellipse((560 - r, 560 - r * 0.6, 560 + r, 560 + r * 0.6), fill=(255, 100, 30, int(a_ * g)))
            for k in range(16):                                                                          # the ash column, boiling upward
                q = k / 15.0
                cy = 560 - 560 * g * q - 20
                rr = (60 + 250 * q ** 0.8) * (0.35 + 0.65 * g) + 18 * math.sin(t * 1.6 + k)
                cx_ = 560 + 120 * math.sin(k * 1.9 + t * 0.35) * q
                shade = 34 + 20 * q + 8 * (k % 3)
                d.ellipse((cx_ - rr, cy - rr * 0.72, cx_ + rr, cy + rr * 0.72), fill=(int(shade), int(shade) - 4, int(shade) - 2, 225))
        else:
            d.polygon([(0, 1230), (300, 1170), (620, 1230), (900, 1160), (W, 1220), (W, H), (0, H)], fill=(24, 12, 12, 255))
            for k, (fx, fs, fo) in enumerate(fire):                                                       # fireballs falling out of the sky
                ph = (t * 0.45 * fs + fo) % 1.0
                x = W * fx + 700 * (0.5 - ph) * 1.1
                y = -80 + 1330 * ph
                for j in range(14):
                    q = j / 14.0
                    r = 26 * (1 - q) + 6
                    d.ellipse((x + 62 * q * 3.2 - r, y - 150 * q * 1.2 - r, x + 62 * q * 3.2 + r, y - 150 * q * 1.2 + r), fill=(255, 150 - int(90 * q), 40, int(220 * (1 - q))))
                d.ellipse((x - 30, y - 30, x + 30, y + 30), fill=(255, 235, 170, 255))
                if ph > 0.95:
                    d.ellipse((x - 190, 1210, x + 190, 1290), fill=(255, 200, 100, 150))
        for i in range(5):
            _ptero(d, ((-t * (160 + 20 * i) + i * 300) % (W + 400)) - 200, 260 + 90 * (i % 3), 0.55, t, (14, 8, 12), ph=i, flip=-1)
        run = t * 0.55
        for i, (kind, sc, x0) in enumerate(((sau, 0.8, 1.3), (sau, 0.62, 2.1), (rex, 1.0, 3.0))):
            x = (x0 - run * (1.5 if kind is rex else 1.0)) * 520 - 200
            gyy = 1660 + 70 * (i % 2)
            draw_creature(d, kind, x, gyy, t * STEP * (0.9 if kind is rex else 0.5), -1, sc, {"t": t, "moving": True, "roar": 0.9 if kind is rex else 0.1})
            _BOXES.append((x - 420, gyy - 1000, x + 420, gyy + 30))
        for k, (fx, fs, fo) in enumerate(fire[:5]):
            d.ellipse((fx * W - 40, 1290 - 30 * fs, fx * W + 40, 1300), fill=(255, 130, 30, 120))
        _ember_rain(d, t, 50, 3)
        _ash(d, t, 70, 9, (80, 70, 72), 0.3, 6)
        dim(im, 6)
        for f in flashes:
            if 0 < t - f < 0.22:
                return _flash(im, 0.55 * (1 - (t - f) / 0.22), (255, 230, 190))
        if u > 0.9:
            return red_wash(im, 0.2)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


def birth_scene(out, dur, world, sp, cwd=None):
    """Out of the molten crater something stands up, dripping fire. Ferns burn at the edge, ash falls like snow."""
    sky = _gradient((14, 4, 6), (130, 36, 14), 1100)
    hits = [dur * 0.3, dur * 0.58, dur * 0.82]
    ex, ll = 540, 1430
    world.push, world.shake = 0.12, 4.0

    def paint(im, d, t, u, cam):
        im.paste(sky, (0, 0))
        d = ImageDraw.Draw(im, "RGBA")
        d.rectangle((0, 1100, W, H), fill=(12, 5, 6, 255))
        d.polygon([(0, 1120), (240, 1020), (520, 1110), (860, 1000), (W, 1090), (W, 1200), (0, 1200)], fill=(18, 8, 8, 255))
        for k in range(14):                                                                              # a lake of lava
            y = 1190 + k * 52
            kk = k / 13
            d.rectangle((0, y, W, y + 56), fill=(int(160 + 90 * kk), int(36 + 100 * kk), 8, 255))
            pts = [(x, y + 24 + 11 * math.sin(x * 0.014 + t * 1.5 + k)) for x in range(0, W + 40, 40)]
            d.line(pts, fill=(255, 226, 120, 190), width=4)
            for j in range(4):
                xx = ((j * 0.31 + k * 0.17 + t * 0.03) % 1.0) * (W + 200) - 100
                d.ellipse((xx - 110, y + 12, xx + 110, y + 46), fill=(34, 12, 8, 140))
        p = _smooth((u - 0.3) / 0.5)
        if p > 0:
            layer, anc, s_ = _sprite(sp, 1.0, t, facing=-1, moving=False, roar=0.5 * p)
            y_feet = ll - 20 + 900 * (1 - p)
            _cut_below(layer, anc, y_feet, ll)
            _BOXES.append(_put_sprite(im, layer, anc, ex, y_feet, angle=0))
            d = ImageDraw.Draw(im, "RGBA")
            for j in range(10):
                xx = ex - 260 + j * 58
                yy = ll - 10 + ((t * 0.9 + j * 0.29) % 1.0) * 260
                d.line((xx, ll - 8, xx + 4, yy), fill=(255, 150, 40, 200), width=7)
            for r, a_ in ((560, 22), (360, 38)):
                d.ellipse((ex - r, ll - 650 * p - r * 0.4, ex + r, ll - 650 * p + r), fill=(255, 100, 20, a_))
        for i in range(6):                                                                               # burning ferns on the edge
            _fern(d, 40 + i * 200, 1130 + 20 * (i % 2), 0.55, (14, 10, 10), t, i + 3)
            d.ellipse((40 + i * 200 - 50, 1010, 40 + i * 200 + 50, 1130), fill=(255, 120, 30, 60))
        _ember_rain(d, t, 60, 4)
        _ash(d, t, 50, 2, (90, 80, 80), 0.16, 5)
        dim(im, 6)
        if u > 0.92:
            return red_wash(im, 0.2)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


def clash_scene(out, dur, world, sp, cwd=None):
    """The old king faces the new apex. The newcomer is far bigger. One lunge, a flash, and the king goes down."""
    sky = _gradient((60, 20, 22), (190, 70, 34), 1300)
    rex = rex_spec()
    gy = 1560
    hits = [dur * 0.28, dur * 0.52, dur * 0.56]
    world.push, world.shake = 0.1, 0.0
    t_hit = 0.54

    def paint(im, d, t, u, cam):
        im.paste(sky, (0, 0))
        d = ImageDraw.Draw(im, "RGBA")
        _volcano(d, 150, 1250, 520, 380, t, 1.2)
        for r, a in ((460, 26), (300, 40)):
            d.ellipse((150 - r, 700 - r, 150 + r, 700 + r), fill=(255, 110, 40, a))
        d.polygon([(0, 1260), (300, 1200), (700, 1250), (W, 1210), (W, H), (0, H)], fill=(34, 16, 14, 255))
        d.rectangle((0, 1300, W, H), fill=(26, 12, 12, 255))
        for i in range(8):
            _fern(d, 60 + i * 150, 1330 + 14 * (i % 2), 0.5, (22, 14, 14), t, i + 8)
        stride = _smooth((u - 0.3) / 0.22)
        lunge = _smooth((u - 0.46) / 0.08)
        ax = 900 - 250 * stride - 120 * lunge
        fall = _smooth((u - t_hit) / 0.28)
        rx = 230 - 130 * fall
        shake = 0.0
        if abs(u - t_hit) < 0.05:
            shake = 16 * (1 - abs(u - t_hit) / 0.05)
        sx, sy = shake * math.sin(t * 90), shake * math.cos(t * 77)
        la, ra, rs = _sprite(rex, 0.7, t, facing=1, moving=u < 0.3, roar=0.5 + 0.5 * math.sin(t * 3) ** 2 if u < t_hit else 0.9)
        if fall > 0:
            a_ = ra
            ang = 78 * fall
            alpha = la.getchannel("A").point(lambda v: int(v * (1 - 0.85 * _smooth((u - 0.8) / 0.18))))
            la.putalpha(alpha)
            _BOXES.append(_put_sprite(im, la, a_, rx + sx, gy + sy + 30 * fall, angle=ang))
        else:
            _BOXES.append(_put_sprite(im, la, ra, rx + sx, gy + sy, angle=0))
        lb, rb, bs = _sprite(sp, 0.85, t, facing=-1, moving=0.3 < u < 0.52, roar=0.3 + 0.7 * _smooth((u - 0.2) / 0.1) if u < 0.6 else 0.9)
        _BOXES.append(_put_sprite(im, lb, rb, ax + sx, gy + 30 + sy, angle=-4 * lunge))
        d = ImageDraw.Draw(im, "RGBA")
        if u > t_hit:
            k = min(1.0, (u - t_hit) / 0.35)
            blood_burst(d, 380, 1330, max(k, 0.15), seed=4, power=1.3)
        _ember_rain(d, t, 40, 6)
        _ash(d, t, 40, 7, (90, 80, 82), 0.2, 5)
        dim(im, 6)
        if 0 < u - t_hit < 0.04:
            return _flash(im, 0.7 * (1 - (u - t_hit) / 0.04), (255, 240, 220))
        if u > t_hit + 0.04:
            return red_wash(im, 0.16 * min(1.0, (u - t_hit) * 3))
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


def skies_scene(out, dur, world, sp, cwd=None):
    """A flock of pterosaurs wheels across a burning sunset, until a far bigger winged shape comes out of the smoke
    and the flock scatters."""
    sky = _gradient((70, 30, 50), (255, 150, 70), H)
    hits = [dur * 0.3, dur * 0.6]
    world.push, world.shake = 0.06, 2.0

    def paint(im, d, t, u, cam):
        im.paste(sky, (0, 0))
        d = ImageDraw.Draw(im, "RGBA")
        for r, a in ((420, 26), (280, 44), (180, 100)):
            d.ellipse((760 - r, 1100 - r, 760 + r, 1100 + r), fill=(255, 190, 110, a))
        d.ellipse((620, 960, 900, 1240), fill=(255, 232, 180, 255))
        for k in range(6):                                                                              # smoke banks
            y = 330 + k * 230
            for j in range(4):
                x = ((j * 0.31 + k * 0.17 + t * 0.012 * (1 + k % 2)) % 1.4) * W * 1.2 - 220
                rw, rh = 150 + 90 * ((j * 7 + k * 3) % 4), 34 + 22 * ((j + k) % 3)
                d.ellipse((x - rw, y - rh, x + rw, y + rh), fill=(60, 34, 46, 80))
        p = _smooth((u - 0.32) / 0.5)
        for i in range(9):
            home = 0.4 + 0.6 * ((i * 37) % 10) / 10.0
            x = (((t * 70 * home + i * 180) % (W + 500)) - 250)
            y = 460 + 110 * (i % 4) + 30 * math.sin(t * 1.2 + i)
            if p > 0:   # scatter, away from the newcomer
                x += (-1 if x < 540 else 1) * 500 * p
                y -= 240 * p
            _ptero(d, x, y, 0.55 + 0.09 * (i % 3), t, (26, 14, 26), ph=i * 0.7)
        if p > 0:
            if sp["kind"] == "dragon":
                layer, anc, s_ = _sprite(dict(sp, flying=True), 1.0, t, facing=1, moving=True, roar=0.8, flying=True)
            else:
                layer, anc, s_ = _sprite(sp, 1.0, t, facing=1, moving=True, roar=0.8, wings=1.2)
            x = -260 + 1500 * p
            y = 1180 + 90 * math.sin(t * 2.2) - 300 * p
            _BOXES.append(_put_sprite(im, layer, anc, x, y, angle=8 - 12 * p, zoom=0.7 + 0.2 * p))
        d = ImageDraw.Draw(im, "RGBA")
        _ash(d, t, 30, 13, (100, 70, 70), 0.1, 4)
        dim(im, 10)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


def reign_scene(out, dur, world, sp, cwd=None):
    """The new apex on a ridge over the valley, roaring at a blood-red sky, then its face filling the screen as ash falls."""
    sky = _gradient((30, 8, 12), (190, 60, 26), H)
    rex = rex_spec()
    hits = [dur * 0.3, dur * 0.62]
    world.push, world.shake = 0.0, 2.5
    swap = 0.5

    def wide(im, t, u):
        im.paste(sky, (0, 0))
        d = ImageDraw.Draw(im, "RGBA")
        _volcano(d, 760, 1230, 640, 460, t, 1.3)
        for r, a in ((520, 24), (340, 40)):
            d.ellipse((760 - r, 600 - r, 760 + r, 600 + r), fill=(255, 100, 30, a))
        d.polygon([(0, 1320), (200, 1280), (520, 1330), (W, 1290), (W, H), (0, H)], fill=(20, 10, 10, 255))
        for i in range(9):
            _fern(d, 40 + i * 135, 1370, 0.5, (14, 10, 10), t, i + 21)
        la, ra, _ = _sprite(rex, 0.6, t, facing=1, moving=False, roar=0.0)       # the old king, lying where he fell
        _put_sprite(im, la, ra, 250, 1560, angle=82)
        lb, rb, _ = _sprite(sp, 0.95, t, facing=-1, moving=False, roar=0.35 + 0.65 * _smooth((u - 0.2) / 0.1))
        _BOXES.append(_put_sprite(im, lb, rb, 700, 1580))
        d = ImageDraw.Draw(im, "RGBA")
        _ember_rain(d, t, 40, 8)
        _ash(d, t, 50, 11, (90, 80, 82), 0.2, 5)
        return im

    def paint(im, d, t, u, cam):
        im = wide(im, t, u)
        if u > swap:
            f = _smooth((u - swap) / 0.1)
            im2 = Image.new("RGB", im.size)
            im2.paste(sky, (0, 0))
            d2 = ImageDraw.Draw(im2, "RGBA")
            for r, a in ((700, 30), (460, 46)):
                d2.ellipse((800 - r, 560 - r, 800 + r, 560 + r), fill=(255, 100, 30, a))
            closeup(im2, d2, sp, t, dur, 2.1, 2.5, roar=0.15 + 0.7 * _smooth((u - 0.72) / 0.15), shake=3.0, tt=t)
            d2 = ImageDraw.Draw(im2, "RGBA")
            _ember_rain(d2, t, 40, 9)
            _ash(d2, t, 60, 14, (96, 84, 86), 0.25, 6)
            im = Image.blend(im, im2, f)
        dim(im, 8)
        return im
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}



# ---------------------------------------------------------------- which scenes show the creature with the slow fade-in
EMERGE_SCENES = {"window": 2.4, "door": 2.4, "curtain": 2.2, "loom": 2.4, "shoulder": 2.2, "pov": 2.2, "mirror": 2.4, "lamp": 2.2,
                 "neighbour": 2.4, "basement": 2.4, "stairs": 2.2, "cctv": 2.0, "omen": 2.0, "hide": 2.0, "transform": 2.0,
                 "hallway": 2.0, "woods": 2.0, "road": 2.0, "dig": 1.8, "fly": 1.8, "ocean": 2.6, "lake": 2.6, "lava": 2.6,
                 "tornado": 2.4, "tsunami": 2.6, "giant": 3.0, "birth": 3.0, "skies": 2.4}


def _with_emerge(fn, secs):
    def wrapper(out, dur, world, sp, *a, **k):
        full = min(secs, dur * 0.55)
        world.emerge = min(0.9, full) if EMERGE["seen"] else full   # the first sighting is slow, later ones are quicker
        return fn(out, dur, world, sp, *a, **k)
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


for _name, _secs in EMERGE_SCENES.items():
    _fn = globals().get(_name + "_scene")
    if _fn is not None:
        globals()[_name + "_scene"] = _with_emerge(_fn, _secs)


# ---------------------------------------------------------------- cutaway shots (inserted between the main scenes for variety)
def _one_eye(base, x, y, s, ec, amp, lid, slit, look=0.0, flip=1):
    """One detailed eye: almond lids, a ringed iris with fibres, a pupil, wet highlights and a brow shadow.
    Drawn at double size on a small layer and shrunk, so the edges are smooth."""
    S = 2
    hw, hh = 54 * s, 25 * s * max(lid, 0.04)
    pad = int(hw * 0.9)
    wd, ht = int(2 * hw + 2 * pad), int(2 * (25 * s) + 2 * pad + 30 * s)
    if wd < 8 or ht < 8:
        return
    L = Image.new("RGBA", (wd * S, ht * S), (0, 0, 0, 0))
    g = ImageDraw.Draw(L, "RGBA")
    cx_, cy_ = wd * S / 2, ht * S / 2 + 9 * s * S
    # brow shadow and socket
    g.ellipse((cx_ - hw * 1.5 * S, cy_ - 25 * s * 2.6 * S, cx_ + hw * 1.5 * S, cy_ + 25 * s * 1.2 * S), fill=(0, 0, 0, 62))
    # almond shape
    n = 28
    top, bot = [], []
    for i in range(n + 1):
        u = -1 + 2 * i / n
        k = max(0.0, 1 - u * u) ** 0.75
        top.append((cx_ + u * hw * S, cy_ - hh * S * k * (1.0 + 0.18 * u * flip)))
        bot.append((cx_ + u * hw * S, cy_ + hh * S * k * 0.8))
    almond = top + bot[::-1]
    mask = Image.new("L", L.size, 0)
    ImageDraw.Draw(mask).polygon(almond, fill=255)
    inner = Image.new("RGBA", L.size, (0, 0, 0, 0))
    gi = ImageDraw.Draw(inner, "RGBA")
    dk = tuple(int(c * 0.22) for c in ec[:3])
    gi.rectangle((0, 0, L.size[0], L.size[1]), fill=dk + (255,))
    # iris: concentric rings from a dark rim to a bright centre
    r0 = 25 * s * S * 1.5
    ix = cx_ + look * hw * 0.25 * S
    for i in range(10):
        f = i / 9.0
        r = r0 * (1 - f * 0.82)
        col = tuple(int(min(255, ec[c] * (0.38 + 0.8 * f) + 40 * f * f)) for c in range(3))
        gi.ellipse((ix - r, cy_ - r, ix + r, cy_ + r), fill=col + (255,))
    for k in range(26):            # fibres
        a_ = k / 26 * 6.2832
        gi.line((ix + math.cos(a_) * r0 * 0.35, cy_ + math.sin(a_) * r0 * 0.35, ix + math.cos(a_) * r0 * 0.95, cy_ + math.sin(a_) * r0 * 0.95),
                fill=(min(255, ec[0] + 70), min(255, ec[1] + 70), min(255, ec[2] + 60), 70), width=max(1, int(S * s * 0.6)))
    # pupil
    pw = r0 * (0.16 if slit else 0.40)
    gi.ellipse((ix - pw, cy_ - r0 * (0.92 if slit else 0.40), ix + pw, cy_ + r0 * (0.92 if slit else 0.40)), fill=(6, 3, 4, 255))
    # lid shadow across the top of the eye, and a darker rim
    for j in range(8):
        gi.rectangle((0, cy_ - hh * S + j * hh * S * 0.07, L.size[0], cy_ - hh * S + (j + 1) * hh * S * 0.07), fill=(0, 0, 0, 120 - j * 14))
    # wet highlights
    gi.ellipse((ix - r0 * 0.55, cy_ - r0 * 0.62, ix - r0 * 0.22, cy_ - r0 * 0.30), fill=(255, 255, 255, 235))
    gi.ellipse((ix + r0 * 0.28, cy_ + r0 * 0.25, ix + r0 * 0.44, cy_ + r0 * 0.40), fill=(255, 255, 255, 150))
    L.paste(inner, (0, 0), mask)
    # lid edge line
    g.line(top, fill=(8, 5, 6, 255), width=max(2, int(S * s * 1.6)))
    g.line(bot, fill=(8, 5, 6, 200), width=max(1, int(S * s * 1.0)))
    L = L.resize((wd, ht), Image.LANCZOS)
    if amp < 1.0:
        L.putalpha(L.getchannel("A").point(lambda v: int(v * amp)))
    base.paste(L.convert("RGB"), (int(x - wd / 2), int(y - ht / 2 - 9 * s)), L.getchannel("A"))


def _eye_pair(d, x, y, s, t, ec, appear, hybrid=True, blink_seed=0.3):
    """Two glowing, detailed eyes in the dark. They fade in, blink, and the glow breathes."""
    ph = (t * 0.55 + blink_seed) % 1.0
    lid = 1.0 - min(1.0, abs(ph - 0.955) / 0.03) if ph > 0.925 else 0.0   # a quick blink
    amp = appear
    if amp <= 0.01:
        return
    gap = 130 * s
    glow = 0.85 + 0.15 * math.sin(t * 7 + blink_seed)
    base = getattr(d, "_image", None)
    for sx in (-1, 1):
        ex = x + sx * gap
        if base is not None and base.mode == "RGB":    # a smooth blurred glow, no visible rings
            R = int(150 * s * glow)
            gl = Image.new("L", (R * 4, R * 3), 0)
            ImageDraw.Draw(gl).ellipse((R, int(R * 0.7), R * 3, int(R * 2.3)), fill=int(150 * amp))
            gl = gl.filter(ImageFilter.GaussianBlur(R * 0.42))
            base.paste(tuple(int(c) for c in ec[:3]), (int(ex - R * 2), int(y - R * 1.5)), gl)
        else:
            for r, a in ((125, 16), (78, 36), (46, 70)):
                rr = r * s * glow
                d.ellipse((ex - rr, y - rr * 0.7, ex + rr, y + rr * 0.7), fill=(ec[0], ec[1], ec[2], int(a * amp)))
        if base is not None and base.mode == "RGB":
            _one_eye(base, ex, y, s, ec, amp, 1.0 - lid, bool(hybrid), look=0.35 * math.sin(t * 0.7 + blink_seed), flip=sx)
        else:
            w, h = 28 * s, (12 + 8 * (1 - lid)) * s
            d.ellipse((ex - w, y - h, ex + w, y + h), fill=(min(255, ec[0] + 40), min(255, ec[1] + 50), min(255, ec[2] + 60), int(255 * amp)))


def _mist(d, t, seed, strength=1.0, tint=(150, 160, 190)):
    rnd = random.Random(seed)
    for _ in range(9):
        x0, y0, sp_, sz = rnd.uniform(0, W), rnd.uniform(300, H - 200), rnd.uniform(15, 50), rnd.uniform(200, 420)
        x = (x0 + t * sp_) % (W + 700) - 350
        d.ellipse((x - sz, y0 - sz * 0.35, x + sz, y0 + sz * 0.35), fill=tint + (int(20 * strength),))


def eyes_scene(out, dur, world, sp, cwd=None):
    """Extreme close-up: nothing but the world swallowed by darkness and a pair of eyes moving closer through the mist."""
    ec = sp.get("eye", (255, 190, 40))
    rnd = random.Random(int(dur * 100) % 997)
    x0, y0 = rnd.choice((330, 760)), rnd.choice((880, 1010))
    world.push = 0.06

    def paint(im, d, t, u, cam):
        dim(im, 222)
        _mist(d, t, 3, 1.0)
        k = _sm(u)
        s = 0.55 + 1.5 * k
        x = x0 + (540 - x0) * 0.75 * k + 14 * math.sin(t * 0.8)
        y = y0 + (860 - y0) * 0.6 * k
        _eye_pair(d, x, y, s, t, ec, _sm(u / 0.22), bool(sp.get("hybrid")), blink_seed=0.3)
        _vignette(im, 215)
    write_video(out, dur, paint, world, cwd, cam_dir=0.0)
    return {"hits": [dur * 0.4]}


def lantern_scene(out, dur, world, sp, cwd=None):
    """Close-up of a lantern on a chain, swinging, a moth fluttering around the flame. In the 'drop' version the chain
    snaps, the lantern falls, the glass shatters and the flame dies."""
    mode = getattr(world, "variant", "swing")
    drop_at = dur * 0.55
    rnd = random.Random(21)
    shards = [(rnd.uniform(-1, 1), rnd.uniform(0.4, 1.0), rnd.uniform(6, 16)) for _ in range(16)]
    pivot_x, pivot_y, L = 540, 150, 760

    def lamp_at(t):
        if mode == "swing" or t < drop_at:
            a = 0.34 * math.sin(t * 2.3) * (1.0 + 0.1 * math.sin(t * 5.1))
            return pivot_x + L * math.sin(a), pivot_y + L * math.cos(a), a, 1.0
        tt = t - drop_at
        x = pivot_x + L * math.sin(0.34 * math.sin(drop_at * 2.3))
        y0 = pivot_y + L * math.cos(0.34 * math.sin(drop_at * 2.3))
        y = y0 + 900 * tt * tt
        if y > 1560:
            y = 1560
        return x, y, 0.6 * min(tt * 3, 1.0), max(0.0, 1 - max(0.0, tt - 0.5) / 0.9)

    def paint(im, d, t, u, cam):
        d.rectangle((0, 0, W, H), fill=(7, 5, 9, 255))
        d.rectangle((0, 1590, W, H), fill=(20, 13, 11, 255))
        x, y, a, flame = lamp_at(t)
        fl = (0.78 + 0.22 * math.sin(t * 17) + 0.08 * math.sin(t * 29)) * flame
        for r, al in ((1000, 14), (680, 26), (420, 46), (240, 80)):
            d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 150, 50, int(al * fl)))
        if mode == "swing" or t < drop_at:
            d.line((pivot_x, -20, x, y - 150), fill=(46, 36, 30, 255), width=9)
        c, sn = math.cos(a), math.sin(a)

        def P(px, py):
            return (x + px * c - py * sn, y + px * sn + py * c)
        glass = (255, int(170 + 40 * fl), int(70 + 30 * fl), 235)
        broken = mode == "drop" and t > drop_at + 0.55
        d.polygon([P(-70, -130), P(70, -130), P(95, 120), P(-95, 120)], fill=(30, 22, 18, 255))
        if not broken:
            d.polygon([P(-52, -105), P(52, -105), P(72, 100), P(-72, 100)], fill=glass)
            d.ellipse((P(0, 20)[0] - 30 * fl, P(0, 20)[1] - 60 * fl, P(0, 20)[0] + 30 * fl, P(0, 20)[1] + 60 * fl), fill=(255, 245, 190, int(255 * flame)))
        for bx in (-52, 0, 52):
            d.line((P(bx, -110), P(bx * 1.35, 108)), fill=(12, 9, 8, 255), width=8)
        d.polygon([P(-60, -130), P(60, -130), P(30, -180), P(-30, -180)], fill=(20, 15, 12, 255))
        d.polygon([P(-100, 120), P(100, 120), P(80, 160), P(-80, 160)], fill=(20, 15, 12, 255))
        for k in range(3):  # a moth around the flame
            ph = t * (3.0 + k) + k * 2
            mx, my = x + 150 * math.sin(ph) * (0.6 + 0.4 * k), y - 40 + 110 * math.sin(ph * 1.3 + k)
            d.ellipse((mx - 5, my - 4, mx + 5, my + 4), fill=(20, 14, 12, 255))
        if mode == "drop" and t > drop_at:
            tt = t - drop_at
            if y >= 1555:
                for sx, sv, sz in shards:
                    px = x + sx * 340 * min(tt, 1.2) * sv
                    py = 1570 - 260 * sv * (1 - (min(tt, 1.0) * 2 - 1) ** 2) * max(0.0, 1 - tt / 1.2)
                    d.polygon([(px, py), (px + sz, py + sz * 0.4), (px + sz * 0.3, py + sz)], fill=(255, 200, 120, int(220 * max(0.15, 1 - tt / 2.0))))
                for r, al in ((300, 30), (170, 60)):
                    d.ellipse((x - r, 1590 - r * 0.25, x + r, 1590 + r * 0.25), fill=(255, 140, 40, int(al * flame)))
        _vignette(im, 200)
    write_video(out, dur, paint, world, cwd, cam_dir=0.0)
    return {"hits": [drop_at] if mode == "drop" else [dur * 0.3, dur * 0.65]}


def notice_scene(out, dur, world, sp, cwd=None):
    """A person walks into frame with a lantern, stops, raises it, and stares at something in the dark. Far off, two eyes
    open. The camera slowly pushes in towards them."""
    ec = sp.get("eye", (255, 190, 40))
    world.push, world.focus_y = 0.24, 0.62
    stop_u = 0.38

    def paint(im, d, t, u, cam):
        ph = t * STEP * 0.5
        moving = u < stop_u
        x = 130 + 380 * _sm(u / stop_u) if moving else 510 + 6 * math.sin(t * 1.3)
        sc = 1.1 + (0.0 if moving else 0.01 * math.sin(t * 2.2))  # breathing
        draw_human(d, x, GROUND + 20, ph if moving else 0.35 + 0.05 * math.sin(t * 2.0), 1, sc, cast=False)
        k = _sm((u - 0.5) / 0.15)
        _eye_pair(d, 880, GROUND - 330, 0.55, t, ec, k, bool(sp.get("hybrid")), blink_seed=0.6)
    write_video(out, dur, paint, world, cwd, cam_dir=0.35)
    return {"hits": [dur * stop_u, dur * 0.55]}


def establish_scene(out, dur, world, sp, cwd=None):
    """A wide shot of the place with a tiny figure for scale. Several camera moves: pan, push-in, pull-back, storm, low angle."""
    v = getattr(world, "variant", "pan")
    cam_dir, tiny = 1.2, True
    if v == "push":
        world.push, cam_dir = 0.34, 0.2
    elif v == "pull":
        world.pull, cam_dir = 0.34, 0.2
    elif v == "storm":
        world.storm, world.push, cam_dir = True, 0.1, 1.8
    elif v == "low":
        world.focus_y, world.push, cam_dir, tiny = 0.86, 0.14, 0.5, False
    else:
        cam_dir = 1.3

    def paint(im, d, t, u, cam):
        if tiny:
            ph = t * STEP * 0.5
            draw_human(d, 640 + 160 * u if v == "pan" else 700, GROUND + 20, ph if v in ("pan", "storm") else 0.3, 1, 0.42, cast=False)
        dim(im, 40)
    write_video(out, dur, paint, world, cwd, cam_dir=cam_dir)
    return {"hits": [dur * 0.1]}


INSERT_FUNCS = {"valley": valley_scene, "eyes": eyes_scene, "lantern": lantern_scene, "notice": notice_scene, "establish": establish_scene}


# =====================================================================================================
#  HYBRID DESIGN SYSTEM  (Impossible Animals)
#  1) SPECIES: what each real animal really looks like and how it moves (the parent analysis)
#  2) design_hybrid(): invents ONE new body out of two parents (nothing generic is added)
#  3) draw_chimera(): draws any design (the parents themselves are drawn by the same code)
# =====================================================================================================
import hashlib
import json


def _H(skull=(55, 50), snout=("snout", 80, 34, 24, 0.0), teeth="rows", ears=("point", 60), eyes="round",
       brow=0.1, extras=(), eyecol=(235, 190, 60)):
    return dict(skull=skull, snout=snout, teeth=teeth, ears=ears, eyes=eyes, brow=brow,
                extras=list(extras), eyecol=eyecol)


def _S(**k):
    d = dict(torso=("barrel", 320, 150, 0.0), legs=("digi", 4, 170, 30), foot="paw", neck=(50, 0.3),
             head=_H(), tail="stub", wings=None, shell=None, dorsal=(), skin="fur", gait="trot", hover=0,
             pal=((140, 120, 100), (80, 60, 50), (200, 180, 160)), pat="plain", arms=None,
             temper="stoic", strike="pounce", flies=False, words={}, arch=0.0, tail_len=1.0)
    d.update(k)
    return d


# torso = (profile, length, height, pitch)   legs = (style, count, length, thickness)
# wings = (kind, size)    arms = (kind, length)    pal = (main, accent, belly)
SPECIES = {
    "wolf": _S(torso=("sleek", 330, 125, 0.0), legs=("digi", 4, 205, 28), foot="paw", neck=(58, 0.4),
               head=_H((60, 54), ("snout", 105, 36, 24, 0.0), "rows", ("point", 72), "round", 0.18, eyecol=(235, 190, 60)),
               tail="bushy", skin="fur", gait="trot", pal=((128, 134, 146), (62, 66, 80), (204, 207, 212)), pat="mask",
               temper="stoic", strike="pounce",
               words=dict(body="a lean, deep-chested wolf body", head="a long wolf muzzle with pointed ears",
                          limbs="long, tireless running legs", tail="a thick brush of a tail", skin="thick grey fur",
                          move="lopes in a tireless trot", ability="a pack-hunter's endless stamina")),
    "fox": _S(torso=("sleek", 270, 100, 0.0), legs=("digi", 4, 165, 20), neck=(46, 0.4),
              head=_H((50, 44), ("snout", 95, 28, 16, 0.0), "rows", ("point", 100), "slit", 0.05, eyecol=(240, 170, 40)),
              tail="bushy", skin="fur", gait="trot", pal=((214, 112, 48), (40, 30, 28), (246, 236, 220)), pat="socks",
              temper="sly", strike="pounce",
              words=dict(body="a slim fox body", head="a sharp fox face with huge ears", limbs="slim black-socked legs",
                         tail="a huge white-tipped brush of a tail", skin="russet fur", move="trots with a silent, sly step",
                         ability="razor-sharp hearing")),
    "cheetah": _S(torso=("sleek", 340, 105, 0.0), legs=("digi", 4, 240, 24), neck=(62, 0.3),
                  head=_H((48, 44), ("snout", 40, 34, 28, 0.0), "fangs", ("round", 38), "round", 0.1,
                          extras=("tearmarks",), eyecol=(220, 160, 40)),
                  tail="whip", skin="fur", gait="bound", pal=((224, 174, 78), (50, 36, 22), (242, 228, 192)), pat="spots",
                  arch=0.5, temper="alert", strike="pounce",
                  words=dict(body="a whip-thin sprinter's body", head="a small round cat face with black tear lines",
                             limbs="very long sprinting legs", tail="a long ringed tail for balance",
                             skin="spotted golden fur", move="bounds in great loping leaps", ability="explosive speed")),
    "lion": _S(torso=("barrel", 340, 165, 0.0), legs=("digi", 4, 180, 38), neck=(50, 0.35),
               head=_H((66, 62), ("snout", 50, 44, 36, 0.0), "fangs", ("round", 36), "round", 0.15, extras=("mane",),
                       eyecol=(230, 170, 40)),
               tail="tuft", skin="fur", gait="prowl", pal=((208, 160, 84), (112, 62, 30), (232, 204, 154)), pat="plain",
               temper="regal", strike="pounce",
               words=dict(body="a heavy, muscular big-cat body", head="a broad face ringed by a great mane",
                          limbs="thick, padded paws", tail="a long tail ending in a dark tuft", skin="tawny fur",
                          move="prowls with slow, heavy confidence", ability="a thunderous roar")),
    "bear": _S(torso=("barrel", 335, 195, 0.05), legs=("pillar", 4, 150, 46), foot="bigpaw", neck=(40, 0.2),
               head=_H((64, 60), ("snout", 70, 40, 30, 0.05), "rows", ("round", 30), "beady", 0.1),
               tail="stub", skin="fur", gait="lumber", pal=((94, 66, 50), (54, 38, 30), (112, 82, 62)), pat="plain",
               temper="brooding", strike="pounce",
               words=dict(body="a massive barrel-chested bear body", head="a heavy bear head with small round ears",
                          limbs="thick pillar legs with huge clawed paws", tail="a stub of a tail", skin="shaggy brown fur",
                          move="lumbers with swaying power", ability="crushing strength")),
    "polar bear": _S(torso=("barrel", 350, 178, 0.05), legs=("pillar", 4, 150, 44), foot="bigpaw", neck=(62, 0.15),
                     head=_H((56, 48), ("snout", 90, 34, 26, 0.08), "rows", ("round", 24), "beady", 0.05),
                     tail="stub", skin="fur", gait="lumber", pal=((232, 238, 240), (170, 186, 196), (244, 246, 248)),
                     pat="plain", temper="patient", strike="pounce",
                     words=dict(body="a long, heavy polar-bear body", head="a long, flat white head with a black nose",
                                limbs="huge webbed paws", tail="a short tail", skin="dense white fur",
                                move="pads silently across the ice", ability="a patient ambush-hunter's focus")),
    "gorilla": _S(torso=("barrel", 250, 235, 0.6), legs=("pillar", 2, 115, 50), foot="bigpaw", neck=(18, 0.1),
                  head=_H((62, 58), ("snout", 40, 42, 36, 0.0), "flat", ("round", 18), "beady", 0.3,
                          extras=("browridge", "crown"), eyecol=(110, 70, 40)),
                  tail="none", skin="fur", gait="knuckle", arms=("ape", 290), pal=((52, 52, 60), (28, 28, 34), (74, 72, 80)),
                  pat="plain", temper="brooding", strike="claw",
                  words=dict(body="a broad, hunched gorilla torso", head="a heavy brow and a bony crown",
                             limbs="arms so long they brush the ground", tail="no tail", skin="coarse black fur",
                             move="knuckle-walks, swinging its great arms", ability="enormous arm strength")),
    "camel": _S(torso=("barrel", 330, 160, 0.0), legs=("digi", 4, 255, 24), foot="pad", neck=(190, 1.0),
                head=_H((44, 40), ("snout", 76, 28, 24, 0.25), "flat", ("round", 22), "sleepy", 0.0, eyecol=(80, 50, 30)),
                tail="tuft", dorsal=("hump",), skin="fur", gait="pace", neck_bow=0.2,
                pal=((206, 166, 106), (150, 110, 70), (224, 192, 142)), pat="plain", temper="aloof", strike="snap",
                words=dict(body="a tall, deep-bodied camel frame with a fat hump", head="a droopy-lipped long face with sleepy eyes",
                           limbs="stilt-long legs with wide padded feet", tail="a short tuft", skin="sandy fur",
                           move="paces in a rolling, swaying stride", ability="surviving weeks without water")),
    "elephant": _S(torso=("barrel", 390, 235, 0.0), legs=("pillar", 4, 200, 60), foot="flat", neck=(24, 0.0),
                   head=_H((82, 78), ("trunk", 170, 34, 18, 0.0), "none", ("flap", 150), "beady", 0.0,
                           extras=("tusks",), eyecol=(70, 50, 40)),
                   tail="whip", skin="wrinkled", gait="lumber", pal=((142, 142, 152), (110, 110, 122), (162, 162, 170)),
                   pat="plain", temper="gentle", strike="claw",
                   words=dict(body="a vast, grey, pillar-legged body", head="a huge head with fan-like ears, a long trunk and tusks",
                              limbs="four tree-trunk legs", tail="a thin tufted tail", skin="deeply wrinkled grey hide",
                              move="walks with slow, earth-shaking steps", ability="a trunk that can lift, smash or sniff out anything")),
    "bull": _S(torso=("barrel", 330, 190, 0.0), legs=("digi", 4, 172, 36), foot="hoof", neck=(34, 0.1),
               head=_H((60, 56), ("snout", 60, 42, 38, 0.05), "flat", ("point", 46), "beady", 0.3,
                       extras=("horns_bull",), eyecol=(150, 40, 30)),
               tail="whip", skin="fur", gait="trot", pal=((62, 52, 54), (212, 202, 182), (84, 72, 74)), pat="plain",
               temper="savage", strike="pounce",
               words=dict(body="a thick, muscled shoulder-heavy body", head="a broad head crowned by curving horns",
                          limbs="hard-hoofed legs", tail="a swishing tufted tail", skin="dark coarse hide",
                          move="trots and charges head-down", ability="a battering-ram charge")),
    "giraffe": _S(torso=("barrel", 290, 150, 0.12), legs=("digi", 4, 310, 22), foot="hoof", neck=(270, 1.15),
                  head=_H((40, 35), ("snout", 56, 26, 22, 0.0), "flat", ("point", 30), "round", 0.0,
                          extras=("ossicones",), eyecol=(90, 60, 40)),
                  tail="whip", skin="fur", gait="pace", neck_bow=0.1,
                  pal=((216, 172, 88), (112, 66, 32), (238, 214, 152)), pat="patches", temper="serene", strike="snap",
                  words=dict(body="a short, sloping body on stilts", head="a small head on the tallest neck of any animal",
                             limbs="impossibly long legs", tail="a thin tufted tail", skin="patchwork-patterned hide",
                             move="paces with long, floating strides", ability="seeing everything from above")),
    "rhino": _S(torso=("barrel", 365, 205, 0.0), legs=("pillar", 4, 130, 52), foot="flat", neck=(26, -0.15),
                head=_H((68, 56), ("snout", 105, 46, 36, 0.14), "flat", ("point", 38), "beady", 0.2,
                        extras=("horn_nose",), eyecol=(60, 40, 30)),
                tail="whip", skin="plated", gait="lumber", pal=((132, 130, 134), (92, 90, 96), (152, 150, 154)),
                pat="plain", temper="savage", strike="pounce",
                words=dict(body="a bulldozer body wrapped in armoured folds", head="a low head with a great horn on its nose",
                           limbs="stout, flat-footed legs", tail="a thin tail", skin="thick plated grey armour-hide",
                           move="plods, then charges like a locomotive", ability="an unstoppable charge")),
    "hippo": _S(torso=("barrel", 335, 205, 0.0), legs=("pillar", 4, 90, 56), foot="flat", neck=(18, 0.0),
                head=_H((70, 56), ("blunt", 100, 62, 60, 0.05), "flat", ("round", 18), "periscope", 0.0,
                        extras=("tusk_row",), eyecol=(70, 40, 40)),
                tail="stub", skin="smooth", gait="lumber", pal=((142, 112, 122), (200, 150, 150), (170, 132, 134)),
                pat="plain", temper="brooding", strike="snap",
                words=dict(body="a barrel-shaped, short-legged body", head="a vast square mouth and periscope eyes",
                           limbs="four stubby pillars", tail="a short paddle of a tail", skin="smooth, pink-grey hide",
                           move="plods and wades", ability="a jaw that opens almost twice as wide as a lion's")),
    "kangaroo": _S(torso=("teardrop", 250, 170, 0.75), legs=("bigdigi", 2, 205, 46), foot="longfoot", neck=(52, 0.6),
                   head=_H((44, 40), ("snout", 72, 28, 20, 0.0), "flat", ("big", 95), "round", 0.05, eyecol=(80, 50, 30)),
                   tail="thick", tail_len=1.2, arms=("small", 90), skin="fur", gait="hop",
                   pal=((192, 132, 88), (122, 82, 52), (228, 198, 164)), pat="plain", temper="alert", strike="kick",
                   words=dict(body="an upright body that balances on its tail", head="a deer-like face with tall ears",
                              limbs="huge spring-loaded hind legs and small grasping arms", tail="a thick tail used as a third leg",
                              skin="soft russet fur", move="bounds in enormous hops", ability="a kick that can break bone")),
    "armadillo": _S(torso=("round", 255, 160, 0.0), legs=("sprawl", 4, 62, 30), foot="claw", neck=(8, 0.0),
                    head=_H((40, 34), ("snout", 78, 22, 12, 0.15), "none", ("round", 26), "beady", 0.0, eyecol=(40, 30, 30)),
                    tail="thick", shell="banded", skin="plated", gait="scurry",
                    pal=((152, 132, 122), (100, 86, 80), (202, 182, 172)), pat="plain", temper="timid", strike="claw",
                    words=dict(body="a low, banded armoured body", head="a small pointed snout with tiny eyes",
                               limbs="short, digging, clawed legs", tail="a thick tapering tail", skin="hard bony plates",
                               move="scurries low to the ground", ability="armour that rolls into a ball")),
    "porcupine": _S(torso=("round", 270, 170, 0.0), legs=("sprawl", 4, 82, 28), foot="claw", neck=(8, 0.0),
                    head=_H((40, 38), ("snout", 46, 26, 18, 0.1), "none", ("round", 18), "beady", 0.0, eyecol=(50, 36, 30)),
                    tail="stub", dorsal=("quills",), skin="fur", gait="waddle",
                    pal=((86, 72, 64), (242, 238, 222), (112, 96, 84)), pat="plain", temper="wary", strike="claw",
                    words=dict(body="a round, low body", head="a blunt little face", limbs="short clawed legs",
                               tail="a short tail", skin="a coat of long barbed quills",
                               move="waddles warily, quills rattling", ability="a coat of detachable quills")),
    "crocodile": _S(torso=("tube", 430, 100, 0.0), legs=("sprawl", 4, 72, 36), foot="webbed", neck=(14, 0.0),
                    head=_H((50, 40), ("snout", 215, 36, 34, 0.0), "rows", ("none", 0), "bump", 0.25,
                            extras=("nostril_bump",), eyecol=(220, 190, 60)),
                    tail="thick", tail_len=1.5, dorsal=("osteoderm",), skin="scutes", gait="sprawl",
                    pal=((92, 118, 70), (58, 78, 48), (206, 204, 146)), pat="plain", temper="cold", strike="snap",
                    words=dict(body="a long, low, armoured body", head="a huge toothy snout with eyes on top",
                               limbs="short, sprawling legs", tail="a thick, powerful swimming tail",
                               skin="bony scutes in tough rows", move="crawls low, then explodes forward",
                               ability="the strongest bite of any living animal")),
    "lizard": _S(torso=("tube", 300, 85, 0.0), legs=("sprawl", 4, 62, 20), foot="claw", neck=(24, 0.2),
                 head=_H((38, 32), ("snout", 62, 26, 16, 0.0), "needle", ("none", 0), "slit", 0.1, eyecol=(220, 180, 50)),
                 tail="whip", tail_len=1.4, dorsal=("ridge",), skin="scales", gait="scurry",
                 pal=((132, 152, 86), (72, 92, 50), (204, 204, 142)), pat="bands", temper="cold", strike="tongue",
                 words=dict(body="a long, low, scaly body", head="a wedge-shaped head with slit-pupil eyes",
                            limbs="short sprawling legs", tail="a very long whip of a tail", skin="fine overlapping scales",
                            move="scurries and freezes", ability="a darting tongue")),
    "gecko": _S(torso=("tube", 250, 75, 0.0), legs=("sprawl", 4, 56, 18), foot="pad", neck=(16, 0.0),
                head=_H((44, 36), ("snout", 44, 26, 20, 0.0), "needle", ("none", 0), "bigslit", 0.0, eyecol=(200, 220, 90)),
                tail="whip", tail_len=1.2, skin="scales", gait="scurry",
                pal=((172, 186, 108), (110, 92, 60), (216, 222, 162)), pat="bands", temper="alert", strike="tongue",
                words=dict(body="a slim, flat body", head="a broad head with huge slit-pupil eyes",
                           limbs="wide sticky toe-pads", tail="a tail that can break off", skin="velvety scales",
                           move="darts and clings to anything", ability="feet that stick to any surface")),
    "chameleon": _S(torso=("round", 230, 150, 0.0), legs=("sprawl", 4, 95, 18), foot="zygo", neck=(12, 0.0),
                    head=_H((42, 40), ("snout", 38, 22, 18, 0.0), "none", ("none", 0), "turret", 0.0,
                            extras=("casque",), eyecol=(230, 200, 60)),
                    tail="curl", skin="scales", gait="sway", arch=0.8,
                    pal=((106, 170, 82), (232, 200, 60), (172, 212, 122)), pat="bands", temper="watchful", strike="tongue",
                    words=dict(body="a narrow, leaf-thin body", head="swivelling turret eyes under a tall helmet-crest",
                               limbs="pincer-gripping feet", tail="a tightly curled tail", skin="colour-shifting scales",
                               move="sways like a leaf, one slow step at a time", ability="a tongue that shoots out like a spear")),
    "snake": _S(torso=("tube", 560, 56, 0.0), legs=("none", 0, 0, 0), neck=(0, 0.0),
                head=_H((36, 30), ("snout", 46, 24, 20, 0.0), "fangs", ("none", 0), "slit", 0.2, eyecol=(240, 200, 40)),
                tail="taper", skin="scales", gait="slither", pal=((112, 152, 74), (56, 76, 40), (224, 212, 152)),
                pat="diamond", temper="cold", strike="snap",
                words=dict(body="a limbless, endlessly long body", head="a diamond-shaped head with flickering tongue",
                           limbs="no legs at all", tail="a tapering tail", skin="smooth patterned scales",
                           move="slithers in smooth S-curves", ability="a strike faster than the eye")),
    "turtle": _S(torso=("dome", 300, 200, 0.0), legs=("pillar", 4, 62, 42), foot="flat", neck=(62, 0.1),
                 head=_H((36, 30), ("beak_turtle", 34, 20, 14, 0.0), "none", ("none", 0), "beady", 0.2, eyecol=(230, 200, 60)),
                 tail="stub", shell="dome", skin="scales", gait="plod",
                 pal=((112, 140, 86), (62, 74, 44), (212, 202, 152)), pat="plain", temper="patient", strike="snap",
                 words=dict(body="a heavy, domed body", head="a small beaked head on a wrinkled neck",
                            limbs="stumpy, tough legs", tail="a short tail", skin="a hard domed shell",
                            move="plods on, unstoppable", ability="a shell nothing can crack")),
    "frog": _S(torso=("round", 240, 160, 0.25), legs=("frog", 4, 118, 36), foot="webbed", neck=(0, 0.0),
               head=_H((68, 44), ("wide", 82, 40, 20, 0.0), "none", ("none", 0), "bulge", 0.0, eyecol=(230, 190, 60)),
               tail="none", skin="warty", gait="hop", pal=((98, 174, 78), (40, 92, 42), (224, 232, 172)), pat="spots",
               temper="comical", strike="tongue",
               words=dict(body="a squat, round body", head="a vast, wide mouth under bulging domed eyes",
                          limbs="huge folded hind legs for jumping", tail="no tail", skin="damp, warty skin",
                          move="hops in huge leaps", ability="a tongue that snaps out in a blink")),
    "axolotl": _S(torso=("tube", 285, 100, 0.0), legs=("sprawl", 4, 50, 22), foot="toed", neck=(10, 0.0),
                  head=_H((56, 38), ("wide", 42, 32, 26, 0.0), "none", ("none", 0), "beady", 0.0,
                          extras=("gills_frill",), eyecol=(30, 20, 30)),
                  tail="paddle", tail_len=1.3, skin="smooth", gait="crawl",
                  pal=((238, 172, 192), (232, 100, 130), (252, 218, 228)), pat="plain", temper="serene", strike="snap",
                  words=dict(body="a soft, slim, tadpole-like body", head="a wide smiling face crowned by feathery gills",
                             limbs="tiny four-fingered legs", tail="a long swimming-fin tail", skin="smooth, pale pink skin",
                             move="paddles and crawls", ability="regrowing any lost body part")),
    "shark": _S(torso=("teardrop", 450, 130, 0.0), legs=("none", 0, 0, 0), neck=(0, 0.0),
                head=_H((50, 42), ("cone", 125, 40, 12, 0.0), "rows", ("none", 0), "beady", 0.2,
                        extras=("gills",), eyecol=(10, 10, 14)),
                tail="lunate", wings=("fin", 140), dorsal=("fin_big",), skin="smooth", gait="swim", hover=175,
                pal=((112, 132, 152), (70, 90, 112), (228, 234, 238)), pat="countershade", temper="cold", strike="snap",
                words=dict(body="a streamlined torpedo body", head="a pointed snout filled with rows of triangle teeth",
                           limbs="no legs, only stiff fins", tail="a crescent tail fin", skin="smooth sandpaper hide",
                           move="glides in effortless, sweeping S-curves", ability="a nose that smells one drop in a million")),
    "whale": _S(torso=("teardrop", 500, 205, 0.0), legs=("none", 0, 0, 0), neck=(0, 0.0),
                head=_H((72, 70), ("blunt", 95, 82, 70, 0.1), "none", ("none", 0), "beady", 0.0,
                        extras=("barnacles",), eyecol=(20, 20, 26)),
                tail="fluke", wings=("fin", 150), dorsal=("fin_small",), skin="smooth", gait="swim", hover=195,
                pal=((74, 102, 132), (50, 70, 96), (192, 206, 216)), pat="countershade", temper="gentle", strike="snap",
                words=dict(body="a gigantic streamlined body", head="a vast blunt head and a great curved mouth",
                           limbs="long paddle fins", tail="a broad horizontal tail fluke", skin="smooth blue-grey skin",
                           move="swims in slow, powerful sweeps", ability="a song that carries across oceans")),
    "seal": _S(torso=("teardrop", 330, 135, 0.1), legs=("flipper", 2, 70, 40), foot="none", neck=(34, 0.2),
               head=_H((50, 46), ("snout", 32, 34, 28, 0.0), "none", ("none", 0), "round", 0.0,
                       extras=("whiskers",), eyecol=(30, 24, 24)),
               tail="stub", skin="smooth", gait="hump", pal=((130, 140, 150), (92, 100, 110), (192, 198, 206)),
               pat="mottled", temper="comical", strike="snap",
               words=dict(body="a sleek, tapering body", head="a round face with long whiskers and big dark eyes",
                          limbs="flippers instead of legs", tail="paired rear flippers", skin="sleek mottled fur",
                          move="humps itself forward on its flippers", ability="whiskers that feel a fish's wake")),
    "octopus": _S(torso=("round", 205, 195, 0.35), legs=("tentacle", 8, 130, 28), neck=(0, 0.0),
                  head=_H((0, 0), ("none", 0, 0, 0, 0), "beak", ("none", 0), "slitbulge", 0.0, eyecol=(240, 200, 60)),
                  tail="none", skin="smooth", gait="ripple", hover=0,
                  pal=((178, 72, 98), (112, 42, 72), (232, 142, 162)), pat="mottled", temper="eerie", strike="grab",
                  words=dict(body="a soft bulb of a body", head="two huge slit-pupil eyes set in the bulb of its head",
                             limbs="eight boneless tentacles", tail="no tail", skin="colour-changing skin",
                             move="flows over the ground on eight rippling arms", ability="changing colour in an instant")),
    "jellyfish": _S(torso=("bell", 240, 170, 0.0), legs=("tentacle", 9, 190, 12), neck=(0, 0.0),
                    head=_H((0, 0), ("none", 0, 0, 0, 0), "none", ("none", 0), "none", 0.0, eyecol=(255, 255, 255)),
                    tail="none", skin="translucent", gait="pulse", hover=215,
                    pal=((160, 216, 255), (224, 182, 255), (212, 242, 255)), pat="plain", temper="eerie", strike="sting",
                    words=dict(body="a glowing, see-through bell", head="no head and no eyes",
                               limbs="a curtain of trailing stinging tentacles", tail="no tail", skin="glassy translucent flesh",
                               move="drifts and pulses through the air", ability="a sting that paralyses")),
    "penguin": _S(torso=("teardrop", 235, 200, 1.3), legs=("webbed", 2, 54, 26), foot="webbed", neck=(8, 0.5),
                  head=_H((36, 34), ("beak_short", 58, 16, 8, 0.0), "none", ("none", 0), "round", 0.1),
                  tail="stub", wings=("flipper", 150), skin="feathers", gait="waddle",
                  pal=((38, 46, 62), (244, 204, 64), (242, 242, 246)), pat="countershade", temper="comical", strike="snap",
                  words=dict(body="an upright, bowling-pin body", head="a small head with a short sharp beak",
                             limbs="short webbed feet", tail="a stiff stub tail", skin="dense waterproof feathers",
                             move="waddles upright, side to side", ability="gliding on its belly at high speed")),
    "owl": _S(torso=("teardrop", 225, 175, 1.0), legs=("bird", 2, 72, 16), foot="talon", neck=(10, 0.0),
              head=_H((60, 58), ("beak_hook", 34, 16, 10, 0.0), "none", ("tufts", 40), "owl", 0.0,
                      extras=("facial_disc",), eyecol=(240, 170, 30)),
              tail="fan_short", wings=("bird", 250), skin="feathers", gait="strut", flies=True,
              pal=((152, 114, 78), (232, 216, 184), (228, 210, 178)), pat="barring", temper="eerie", strike="dive",
              words=dict(body="a round, upright, feathered body", head="a flat disc of a face with enormous forward eyes",
                         limbs="feathered legs ending in talons", tail="a short fan of tail feathers",
                         skin="soft barred feathers", move="struts upright, head swivelling",
                         ability="silent flight and a head that turns almost all the way round")),
    "eagle": _S(torso=("teardrop", 255, 125, 0.7), legs=("bird", 2, 95, 16), foot="talon", neck=(42, 0.6),
                head=_H((42, 38), ("beak_hook", 64, 26, 12, 0.0), "none", ("none", 0), "fierce", 0.4, eyecol=(240, 190, 40)),
                tail="fan", wings=("bird", 340), skin="feathers", gait="strut", flies=True,
                pal=((106, 76, 48), (246, 246, 242), (152, 114, 78)), pat="head_white", temper="savage", strike="dive",
                words=dict(body="a broad-chested raptor's body", head="a hooked beak under a fierce, frowning brow",
                           limbs="powerful legs with hooked talons", tail="a broad tail fan",
                           skin="layered brown feathers", move="struts proudly, wings half-open",
                           ability="eyesight that spots prey from two miles up")),
    "hummingbird": _S(torso=("teardrop", 150, 70, 0.35), legs=("bird", 2, 30, 8), foot="talon", neck=(10, 0.3),
                      head=_H((30, 27), ("beak_needle", 125, 6, 3, 0.0), "none", ("none", 0), "beady", 0.0, eyecol=(10, 10, 10)),
                      tail="fan_short", wings=("bird", 150), skin="feathers", gait="hover", hover=185, flies=True,
                      pal=((52, 178, 130), (224, 42, 72), (212, 232, 212)), pat="throat", temper="alert", strike="stab",
                      words=dict(body="a tiny, jewel-bright body", head="a needle-thin beak as long as its body",
                                 limbs="tiny, almost useless feet", tail="a short fan tail", skin="shimmering iridescent feathers",
                                 move="hovers and darts in any direction", ability="wings that beat eighty times a second")),
    "peacock": _S(torso=("teardrop", 235, 130, 0.6), legs=("bird", 2, 125, 16), foot="talon", neck=(115, 0.9),
                  head=_H((28, 26), ("beak_short", 36, 14, 8, 0.0), "none", ("none", 0), "round", 0.0,
                          extras=("crest_fan",), eyecol=(30, 24, 20)),
                  tail="train", wings=("bird", 110), skin="feathers", gait="strut", neck_bow=0.25,
                  pal=((34, 112, 152), (44, 172, 112), (30, 92, 132)), pat="eyespots", temper="vain", strike="snap",
                  words=dict(body="a slim, long-necked body", head="a tiny head with a fan-shaped crest",
                             limbs="long strutting legs", tail="a gigantic train of eyed feathers",
                             skin="iridescent blue-green feathers", move="struts in proud, stiff steps",
                             ability="a tail that stares back with a hundred eyes")),
    "flamingo": _S(torso=("teardrop", 200, 120, 0.5), legs=("bird", 2, 270, 10), foot="webbed", neck=(190, 0.95),
                   head=_H((30, 26), ("beak_bent", 52, 14, 10, 0.0), "none", ("none", 0), "round", 0.0, eyecol=(240, 220, 90)),
                   tail="fan_short", wings=("bird", 230), skin="feathers", gait="strut", neck_bow=0.35,
                   pal=((242, 134, 152), (28, 28, 34), (250, 174, 184)), pat="plain", temper="serene", strike="snap",
                   words=dict(body="a slim body on stilt legs", head="a tiny head with a sharply bent beak",
                              limbs="legs as thin and tall as reeds", tail="a short tail", skin="rose-pink feathers",
                              move="wades with slow, high-stepping grace", ability="filtering food with its upside-down beak")),
    "bat": _S(torso=("round", 135, 105, 0.45), legs=("bird", 2, 42, 9), foot="talon", neck=(12, 0.2),
              head=_H((40, 38), ("snout", 36, 24, 16, 0.0), "fangs", ("big", 115), "beady", 0.2,
                      extras=("noseleaf",), eyecol=(240, 60, 50)),
              tail="whip", wings=("bat", 330), skin="fur", gait="hover", hover=205, flies=True,
              pal=((68, 56, 84), (142, 100, 122), (98, 86, 108)), pat="plain", temper="eerie", strike="dive",
              words=dict(body="a small, furry body", head="a pug face with huge funnel ears",
                         limbs="thumb-claws and hook feet", tail="a thin tail", skin="velvet fur and leathery skin",
                         move="flutters and hangs in the dark", ability="seeing with sound")),
    "dragonfly": _S(torso=("tube", 370, 34, 0.0), legs=("insect", 6, 72, 6), foot="tipclaw", neck=(5, 0.0),
                    head=_H((32, 30), ("tiny", 14, 12, 10, 0.0), "none", ("none", 0), "compound", 0.0,
                            extras=("mandibles",), eyecol=(60, 200, 190)),
                    tail="none", wings=("insect4", 270), skin="chitin", gait="hover", hover=215, flies=True,
                    pal=((62, 192, 202), (40, 112, 172), (152, 222, 222)), pat="bands", temper="alert", strike="dive",
                    words=dict(body="a long, jewel-blue segmented body", head="a head that is almost all giant glittering eyes",
                               limbs="six thin hooked legs", tail="a long jointed abdomen", skin="shining glassy armour",
                               move="hovers, then darts sideways in a blink", ability="four wings that move independently")),
    "butterfly": _S(torso=("tube", 140, 36, 0.2), legs=("insect", 6, 42, 5), foot="tipclaw", neck=(6, 0.0),
                    head=_H((22, 22), ("tiny", 12, 10, 8, 0.0), "none", ("none", 0), "compound", 0.0,
                            extras=("antennae_club",), eyecol=(30, 20, 20)),
                    tail="none", wings=("butterfly", 300), skin="chitin", gait="flutter", hover=205, flies=True,
                    pal=((240, 150, 44), (30, 24, 28), (66, 44, 32)), pat="eyespots", temper="serene", strike="dive",
                    words=dict(body="a thin, fuzzy body", head="a tiny head with club-tipped antennae",
                               limbs="six dainty legs", tail="no tail", skin="powdery scale-covered wings",
                               move="flutters in lazy, tumbling loops", ability="huge patterned wings with false eyes")),
    "mantis": _S(torso=("tube", 310, 38, 0.35), legs=("insect", 4, 150, 8), foot="tipclaw", neck=(66, 0.9),
                 head=_H((34, 30), ("tiny", 18, 14, 10, 0.0), "none", ("none", 0), "compound", 0.0,
                         extras=("antennae", "triangle"), eyecol=(200, 230, 120)),
                 tail="none", wings=("insect2", 190), arms=("raptor", 170), skin="chitin", gait="sway",
                 pal=((132, 178, 72), (196, 214, 104), (192, 212, 122)), pat="plain", temper="cold", strike="claw",
                 words=dict(body="a long, leaf-thin body", head="a triangular head that turns to watch you",
                            limbs="folded, spiked, grasping forelegs", tail="no tail", skin="leaf-green armour",
                            move="rocks like a leaf in the wind, then strikes", ability="forelegs that snap shut in a blur")),
    "ant": _S(torso=("pear", 235, 72, 0.0), legs=("insect", 6, 125, 8), foot="tipclaw", neck=(14, 0.0),
              head=_H((44, 38), ("tiny", 20, 14, 10, 0.0), "none", ("none", 0), "beady", 0.2,
                      extras=("mandibles", "antennae"), eyecol=(20, 16, 16)),
              tail="none", skin="chitin", gait="skitter", pal=((112, 58, 38), (72, 34, 22), (132, 72, 46)),
              pat="plain", temper="driven", strike="snap",
              words=dict(body="a three-part segmented body", head="a head with powerful pincer jaws and bending antennae",
                         limbs="six long, quick legs", tail="a swollen rear segment", skin="glossy red-brown armour",
                         move="skitters in a fast, unstoppable tripod gait", ability="carrying fifty times its own weight")),
    "spider": _S(torso=("pear", 255, 135, 0.0), legs=("arach", 8, 210, 9), foot="tipclaw", neck=(10, 0.0),
                 head=_H((40, 34), ("tiny", 22, 16, 12, 0.0), "none", ("none", 0), "multi", 0.0,
                         extras=("chelicerae", "pedipalps"), eyecol=(220, 40, 40)),
                 tail="none", skin="chitin", gait="skitter", pal=((48, 40, 54), (176, 32, 42), (68, 58, 72)),
                 pat="mark", temper="cold", strike="pounce",
                 words=dict(body="a swollen abdomen behind a small armoured front", head="a cluster of eyes above venom fangs",
                            limbs="eight jointed legs that arch high above the body", tail="no tail",
                            skin="dark, hairy armour", move="skitters on eight silent legs", ability="silk and venom")),
    "scorpion": _S(torso=("pear", 250, 80, 0.0), legs=("arach", 8, 100, 10), foot="tipclaw", neck=(10, 0.0),
                   head=_H((30, 26), ("tiny", 16, 12, 10, 0.0), "none", ("none", 0), "multi", 0.0, eyecol=(30, 20, 20)),
                   tail="stinger", tail_len=1.2, arms=("pincers", 190), skin="chitin", gait="skitter",
                   pal=((164, 120, 66), (92, 62, 30), (184, 138, 78)), pat="plain", temper="cold", strike="stab",
                   words=dict(body="a flat, segmented armoured body", head="a tiny head with a cluster of eyes",
                              limbs="eight walking legs and two big pincers", tail="a curved tail tipped with a venom stinger",
                              skin="glossy armour plates", move="skitters low with pincers raised",
                              ability="a stinger that arcs over its back")),
    "crab": _S(torso=("flatdome", 285, 112, 0.0), legs=("crab", 8, 112, 12), foot="tipclaw", neck=(0, 0.0),
               head=_H((0, 0), ("none", 0, 0, 0, 0), "none", ("none", 0), "stalk", 0.0, eyecol=(20, 14, 14)),
               tail="none", arms=("pincers", 215), skin="chitin", gait="scuttle", shell="carapace",
               pal=((218, 90, 66), (152, 50, 40), (234, 152, 112)), pat="plain", temper="testy", strike="claw",
               words=dict(body="a wide, flat armoured shell", head="two eyes on stalks",
                          limbs="eight bent walking legs and two huge claws", tail="no tail", skin="a hard red carapace",
                          move="scuttles sideways, claws high", ability="claws that crush shell and bone")),
    "stingray": _S(torso=("flat", 390, 62, 0.0), legs=("none", 0, 0, 0), neck=(0, 0.0),
                   head=_H((0, 0), ("none", 0, 0, 0, 0), "none", ("none", 0), "toplook", 0.0, eyecol=(20, 20, 20)),
                   tail="whip_barb", tail_len=1.5, wings=("disc", 260), skin="smooth", gait="glide", hover=130,
                   pal=((150, 120, 90), (84, 66, 48), (232, 226, 212)), pat="spots", temper="eerie", strike="stab",
                   words=dict(body="a flat, diamond-shaped body", head="eyes on top and a mouth underneath",
                              limbs="no legs, just wide wing-like fins", tail="a long whip tail with a venom barb",
                              skin="smooth, sand-coloured skin", move="glides like a ghost", ability="a barbed tail")),
    "anglerfish": _S(torso=("round", 300, 235, 0.0), legs=("none", 0, 0, 0), neck=(0, 0.0),
                     head=_H((0, 0), ("none", 0, 0, 0, 0), "needle", ("none", 0), "round", 0.4,
                             extras=("lure", "bigmouth"), eyecol=(240, 230, 120)),
                     tail="fan_fin", wings=("fin", 90), skin="smooth", gait="swim", hover=165,
                     pal=((62, 52, 64), (120, 255, 220), (92, 80, 96)), pat="plain", temper="eerie", strike="snap",
                     words=dict(body="a swollen, lumpy body", head="an enormous mouth full of needle fangs and a glowing lure",
                                limbs="no legs, stubby fins", tail="a ragged tail fin", skin="dark, lumpy skin",
                                move="drifts in the dark", ability="a glowing lure on a stalk")),
}

ALL_SPECIES = sorted(SPECIES)


# ---------------------------------------------------------------- the designer
ARTHROPOD = ("insect", "arach", "crab")
DISTINCT_SNOUTS = ("beak_hook", "beak_needle", "beak_short", "beak_bent", "beak_turtle", "trunk", "cone", "wide", "blunt")
DISTINCT_EYES = ("owl", "fierce", "bulge", "periscope", "turret", "compound", "multi", "bigslit", "slit", "stalk",
                 "slitbulge", "toplook", "sleepy", "bump")
FLY_WINGS = ("bird", "bat", "insect4", "insect2", "butterfly")
BODY_WINGS = ("fin", "flipper", "disc")


def _seed(*parts):
    return int(hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()[:8], 16)


def _lerp(a, b, k):
    return a + (b - a) * k


def _mixc(a, b, k):
    return tuple(int(a[i] * (1 - k) + b[i] * k) for i in range(3))


def _has_legs(S):
    return S["legs"][0] != "none" and S["legs"][1] > 0


def _headless(S):
    return S["head"]["skull"][0] <= 0


def _tuplify(x):
    if isinstance(x, (list, tuple)):
        return tuple(_tuplify(i) for i in x)
    if isinstance(x, dict):
        return {k: _tuplify(v) for k, v in x.items()}
    return x


def _clip(v, lo, hi):
    return max(lo, min(hi, v))


def _finish(g):
    """Fill in everything the drawing code needs so that a design can be drawn on its own."""
    d = _S()
    for k, v in d.items():
        g.setdefault(k, v)
    g.setdefault("glow", False)
    g.setdefault("sig", None)
    g.setdefault("sigscale", {})
    g.setdefault("hybrid", False)
    g.setdefault("donors", {})
    g.setdefault("wingfly", False)
    g.setdefault("skin2", g.get("skin", "fur"))
    return g


def pure_design(name):
    """A normal, un-mixed animal: it looks like itself (used for the two parents)."""
    S = SPECIES[name]
    g = copy_design(S)
    g["wingfly"] = bool(S["wings"] and S["wings"][0] in FLY_WINGS and S["flies"])
    g["hybrid"] = False
    g["parents"] = (name,)
    g["name"] = name
    g["donors"] = {k: name for k in ("body", "head", "face", "legs", "tail", "wings", "skin", "palette")}
    return _finish(g)


def copy_design(S):
    return _deep(S)


def _deep(x):
    if isinstance(x, dict):
        return {k: _deep(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return tuple(_deep(v) for v in x) if isinstance(x, tuple) else [_deep(v) for v in x]
    return x


def _compose(a, b, rnd):
    A, B = SPECIES[a], SPECIES[b]
    if rnd.random() < 0.5:
        D, O, dn, on = A, B, a, b
    else:
        D, O, dn, on = B, A, b, a
    w = rnd.choice([0.88, 0.75, 0.62])      # how strongly the body parent dominates
    g = {"hybrid": True, "parents": (a, b), "dominant": dn, "secondary": on, "dom_weight": w}
    don = {}

    # ---- torso: normally the dominant parent's, sometimes the other's
    TN, T, TO, ton = (dn, D, O, on)
    if rnd.random() < 0.22 and O["torso"][0] != D["torso"][0]:
        TN, T, TO, ton = on, O, D, dn
    don["body"] = TN
    prof = T["torso"][0]
    L = _lerp(T["torso"][1], TO["torso"][1], 1 - w) * rnd.uniform(0.9, 1.12)
    Hh = _lerp(T["torso"][2], TO["torso"][2], 1 - w) * rnd.uniform(0.9, 1.14)
    pitch = _lerp(T["torso"][3], TO["torso"][3], 0.2)
    if prof in ("pear", "tube", "flat") and TO["torso"][0] in ("barrel", "sleek", "round", "teardrop") and rnd.random() < 0.4:
        Hh = max(Hh, TO["torso"][2] * 0.7)
    g["torso"] = (prof, L, Hh, pitch)
    g["arch"] = T["arch"]

    # ---- legs
    if _has_legs(T):
        LG, lgn = T, TN
        if _has_legs(TO) and TO["legs"][0] in ARTHROPOD and T["legs"][0] not in ARTHROPOD and rnd.random() < 0.42:
            LG, lgn = TO, ton
        elif _has_legs(TO) and TO["legs"][0] in ("frog", "bigdigi", "bird", "pillar", "digi") and \
                T["legs"][0] != TO["legs"][0] and rnd.random() < 0.28:
            LG, lgn = TO, ton
    elif _has_legs(TO):
        LG, lgn = TO, ton
    else:
        LG, lgn = None, None
    if LG is not None:
        st, n, ln, th = LG["legs"]
        refH = LG["torso"][2]
        th = th * _clip((Hh / max(refH, 1)) ** 0.5, 0.65, 1.35)
        ln = ln * rnd.uniform(0.88, 1.15) * (_clip((L / max(LG["torso"][1], 1)) ** 0.3, 0.8, 1.25))
        if st in ("tentacle", "flipper"):
            ln *= 0.9
        g["legs"] = (st, n, ln, th)
        g["foot"] = LG["foot"]
        g["gait"] = LG["gait"]
        g["legs_from"] = lgn
        don["legs"] = lgn
    else:
        g["legs"] = ("none", 0, 0, 0)
        g["foot"] = "none"
        g["gait"] = D["gait"] if not _has_legs(D) else TO["gait"]
        if g["gait"] not in ("swim", "slither", "glide", "pulse", "hover", "flutter"):
            g["gait"] = "swim"
        don["legs"] = None
    # ---- arms / claws
    arms = None
    for S, nm in ((D, dn), (O, on)):
        if S["arms"] and (arms is None or rnd.random() < 0.5):
            arms = (S["arms"][0], S["arms"][1] * rnd.uniform(0.9, 1.15) * _clip((Hh / 150) ** 0.2, 0.9, 1.2))
            don["arms"] = nm
    if arms and arms[0] == "ape" and g["legs"][1] == 4:
        g["legs"] = (g["legs"][0], 2, g["legs"][2], g["legs"][3])
    if arms and arms[0] == "small":
        arms = None
    g["arms"] = arms

    # ---- head: shape from one parent, face details from the other
    HD, hdn, FD, fdn = (D, dn, O, on) if rnd.random() < 0.5 else (O, on, D, dn)
    if _headless(HD):
        HD, hdn, FD, fdn = FD, fdn, HD, hdn
    if _headless(HD):                       # two headless parents: build a plain head
        head = _H((46, 42), ("snout", 70, 32, 22, 0.0), "rows")
        hdn = None
    else:
        head = _deep(HD["head"])
    face_sp = FD["head"]
    don["head"] = hdn
    don["face"] = fdn
    hs = _clip((Hh / max(HD["torso"][2], 1)) ** 0.3, 0.85, 1.2) * rnd.uniform(0.92, 1.1)
    head["skull"] = (head["skull"][0] * hs, head["skull"][1] * hs)
    sn = list(head["snout"])
    # a very distinctive mouth (beak, trunk, cone...) from the other parent replaces the plain snout
    fsn = face_sp["snout"]
    if fsn[0] in DISTINCT_SNOUTS and fsn[0] != sn[0] and rnd.random() < 0.6 and not _headless(FD):
        k = head["skull"][0] / max(face_sp["skull"][0], 1)
        sn = [fsn[0], fsn[1] * _clip(k, 0.8, 1.5), fsn[2] * k, fsn[3] * k, fsn[4]]
        head["teeth"] = face_sp["teeth"]
        don["mouth"] = fdn
    else:
        if fsn[0] == "snout" and sn[0] == "snout" and rnd.random() < 0.5:   # blend two snouts
            sn = [sn[0], _lerp(sn[1], fsn[1], 0.4), _lerp(sn[2], fsn[2], 0.4), _lerp(sn[3], fsn[3], 0.4), _lerp(sn[4], fsn[4], 0.5)]
        elif face_sp["teeth"] not in ("none",) and head["teeth"] in ("none", "flat") and rnd.random() < 0.5:
            head["teeth"] = face_sp["teeth"]
        don["mouth"] = hdn
    head["snout"] = tuple(sn)
    # eyes
    if face_sp["eyes"] in DISTINCT_EYES and (head["eyes"] not in DISTINCT_EYES or rnd.random() < 0.7):
        head["eyes"] = face_sp["eyes"]
        head["brow"] = face_sp["brow"]
        head["eyecol"] = face_sp["eyecol"]
    elif rnd.random() < 0.3:
        head["eyecol"] = face_sp["eyecol"]
    # ears
    if face_sp["ears"][0] != "none" and (head["ears"][0] == "none" or rnd.random() < 0.4):
        head["ears"] = (face_sp["ears"][0], face_sp["ears"][1] * hs)
    elif head["ears"][0] in ("tufts", "flap", "big") and rnd.random() < 0.3:
        head["ears"] = ("point", 60 * hs)
    # extras (horns, mane, tusks, lure, casque, antennae...)
    ex = list(head["extras"])
    for e in face_sp["extras"]:
        if e not in ex and rnd.random() < 0.8:
            ex.append(e)
    rnd.shuffle(ex)
    head["extras"] = ex[:3]
    g["head"] = head
    # neck
    nk = _lerp(HD["neck"][0], T["neck"][0], 0.35)
    nu = _lerp(HD["neck"][1], T["neck"][1], 0.35)
    if HD["neck"][0] >= 150:
        nk, nu = HD["neck"]
    g["neck"] = (nk * rnd.uniform(0.9, 1.1), nu)
    g["neck_bow"] = HD.get("neck_bow", 0.12)

    # ---- tail
    tails = [(D["tail"], D.get("tail_len", 1.0), dn), (O["tail"], O.get("tail_len", 1.0), on)]
    real = [t for t in tails if t[0] not in ("stub", "none")]
    pick = rnd.choice(real) if real else tails[0]
    if len(real) == 2 and rnd.random() < 0.6:
        pick = real[1] if real[0][2] == TN else real[0]    # often the tail of the *other* parent than the body
    g["tail"], g["tail_len"] = pick[0], pick[1] * rnd.uniform(0.9, 1.15)
    don["tail"] = pick[2]

    # ---- wings
    cand = []
    for S, nm in ((D, dn), (O, on)):
        if S["wings"]:
            cand.append((S["wings"], nm, S))
    g["wings"], g["wingfly"] = None, False
    if cand:
        fly = [c for c in cand if c[0][0] in FLY_WINGS]
        wing, wn, WS = (rnd.choice(fly) if fly else rnd.choice(cand))
        kind, size = wing
        if kind in FLY_WINGS:
            size = max(size * _clip((L / max(WS["torso"][1], 1)) ** 0.55, 0.8, 1.6), 0.82 * L) * rnd.uniform(0.95, 1.2)
            size = min(size, L * (0.95 if kind == "butterfly" else 1.45))   # wings must not swallow the body or the frame
            g["wingfly"] = True
        else:
            size = size * _clip((L / max(WS["torso"][1], 1)) ** 0.6, 0.7, 1.4)
        g["wings"] = (kind, size)
        don["wings"] = wn
    # ---- shell / armour
    g["shell"] = None
    for S, nm in ((D, dn), (O, on)):
        if S["shell"] and (nm == TN or rnd.random() < 0.7):
            g["shell"] = S["shell"]
            don["shell"] = nm
            break
    # ---- dorsal features
    dors = list(D["dorsal"]) + [d for d in O["dorsal"] if rnd.random() < 0.7]
    g["dorsal"] = tuple(dict.fromkeys(dors))[:2]

    # ---- skin and texture
    g["skin"] = T["skin"]
    sk2 = [x["skin"] for x in (D, O) if x["skin"] != T["skin"]]
    g["skin2"] = sk2[0] if sk2 else T["skin"]
    don["skin"] = TN

    # ---- colour: a new palette inspired by both parents
    m1, a1, b1 = D["pal"]
    m2, a2, b2 = O["pal"]
    k = rnd.uniform(0.28, 0.45)
    main = _mixc(m1, m2, k)
    acc = a2 if rnd.random() < 0.55 else _mixc(a1, a2, 0.5)
    acc = _mixc(acc, (255, 255, 255), 0.0)
    belly = b1 if sum(b1) > sum(b2) else b2
    belly = _mixc(belly, main, 0.25)
    # keep it from becoming mud: push the colour away from grey a little
    avg = sum(main) / 3
    main = tuple(int(_clip(avg + (c - avg) * 1.12, 20, 245)) for c in main)
    g["pal"] = (main, acc, belly)
    g["pat"] = O["pat"] if (O["pat"] != "plain" and rnd.random() < 0.6) or D["pat"] == "plain" else D["pat"]
    if g["pat"] in ("head_white", "socks", "throat", "mark"):
        g["pat"] = rnd.choice(["spots", "stripes", "bands", "mottled", "countershade"])
    don["palette"] = (dn, on)

    # ---- hovering / flying
    g["hover"] = 0
    if g["wingfly"] and g["wings"][1] > 0.7 * L:
        light = Hh < 150 and L < 330
        g["hover"] = (170 + rnd.uniform(-20, 30)) if light else rnd.uniform(70, 120)
        g["gait"] = "hover" if light else g["gait"]
    elif g["gait"] in ("swim", "glide", "pulse", "hover", "flutter"):
        g["hover"] = max(D["hover"], O["hover"], 150)
    if g["legs"][0] == "none" and g["hover"] == 0 and g["gait"] != "slither":
        g["hover"] = 150

    # ---- character
    g["temper"] = D["temper"] if rnd.random() < 0.6 else O["temper"]
    g["glow"] = g["temper"] in ("savage", "eerie", "cold") and rnd.random() < 0.55
    # horror anatomy (rare, only for menacing temperaments): a showing ribcage, and glowing cracks that follow the body
    grim = g["temper"] in ("savage", "eerie", "cold")
    g["ribs"] = bool(grim and g["skin"] in ("fur", "scales", "leathery", "hide") and rnd.random() < 0.30 and g["torso"][1] > 150)
    g["cracks"] = bool(g["glow"] and rnd.random() < 0.65)
    pstrike = [S["strike"] for S in (D, O)]
    g["strike"] = rnd.choice(pstrike)
    if arms and arms[0] == "pincers":
        g["strike"] = "claw"
    if g["tail"] == "stinger" and rnd.random() < 0.7:
        g["strike"] = "stab"
    if g["head"]["snout"][0] == "beak_needle":
        g["strike"] = "stab"
    # one signature feature, drawn larger than life
    pool = []
    if g["wings"] and g["wingfly"]:
        pool.append(("wings", "a pair of enormous wings"))
    if g["tail"] in ("stinger", "train", "bushy", "fan", "lunate", "fluke", "whip_barb"):
        pool.append(("tail", "an oversized " + g["tail"].replace("_", " ") + " tail"))
    if arms and arms[0] in ("pincers", "raptor", "ape"):
        pool.append(("arms", "huge " + arms[0] + " arms"))
    for e in g["head"]["extras"]:
        if e in ("mane", "horns_bull", "horn_nose", "tusks", "lure", "casque", "antennae", "ossicones", "crest_fan",
                 "facial_disc", "mandibles", "gills_frill", "noseleaf"):
            pool.append((e, "a striking " + e.replace("_", " ")))
    for e in g["dorsal"]:
        pool.append((e, "a prominent " + e.replace("_", " ")))
    if g["shell"]:
        pool.append(("shell", "a heavy " + g["shell"] + " shell"))
    if g["head"]["snout"][0] in ("beak_needle", "beak_hook", "trunk", "cone"):
        pool.append(("snout", "a " + g["head"]["snout"][0].replace("_", " ") + " snout"))
    sig = rnd.choice(pool) if pool else ("head", "a face unlike any real animal")
    g["sig"] = sig
    g["sigscale"] = {sig[0]: 1.3}
    g["donors"] = don
    return g


# ---------------------------------------------------------------- comparing designs (no two hybrids may look alike)
def _bin(v, edges):
    return sum(1 for e in edges if v >= e)


def fingerprint(g):
    prof, L, Hh, pitch = g["torso"]
    hue = _hue(g["pal"][0])
    fp = {
        "prof": prof, "pitch": _bin(pitch, (0.25, 0.6, 0.95)), "L": _bin(L, (200, 290, 380, 450)),
        "H": _bin(Hh, (80, 120, 170, 210)), "legs": g["legs"][0], "nlegs": g["legs"][1],
        "legh": _bin(g["legs"][2], (60, 110, 170, 230)), "neck": _bin(g["neck"][0], (30, 80, 150)),
        "snout": g["head"]["snout"][0], "snoutl": _bin(g["head"]["snout"][1], (40, 90, 150)),
        "teeth": g["head"]["teeth"], "ears": g["head"]["ears"][0], "eyes": g["head"]["eyes"],
        "tail": g["tail"], "wings": g["wings"][0] if g["wings"] else "none", "shell": g["shell"] or "none",
        "dorsal": "+".join(sorted(g["dorsal"])) or "none", "skin": g["skin"], "skin2": g["skin2"], "pat": g["pat"],
        "hue": hue, "light": _bin(sum(g["pal"][0]) / 3, (80, 140, 190)), "gait": g["gait"],
        "hover": g["hover"] > 40, "arms": g["arms"][0] if g["arms"] else "none",
        "extras": "+".join(sorted(g["head"]["extras"])),
    }
    return fp


def _hue(c):
    import colorsys
    h, s, v = colorsys.rgb_to_hsv(c[0] / 255, c[1] / 255, c[2] / 255)
    return -1 if s < 0.12 else int(h * 12) % 12


FP_WEIGHTS = {"prof": 2.2, "pitch": 1.2, "L": 1.2, "H": 1.2, "legs": 2.2, "nlegs": 1.6, "legh": 1.0, "neck": 1.2,
              "snout": 1.8, "snoutl": 0.8, "teeth": 0.5, "ears": 1.0, "eyes": 1.0, "tail": 1.8, "wings": 2.2,
              "shell": 1.6, "dorsal": 1.8, "skin": 1.0, "skin2": 0.5, "pat": 1.0, "hue": 1.6, "light": 0.8,
              "gait": 1.0, "hover": 1.2, "arms": 1.5, "extras": 1.0}
FP_MIN_DISTANCE = 11.0


def fp_distance(f1, f2):
    d = 0.0
    for k, w in FP_WEIGHTS.items():
        a, b = f1.get(k), f2.get(k)
        if a == b:
            continue
        if k == "hue" and a != -1 and b != -1 and min((a - b) % 12, (b - a) % 12) == 1:
            d += w * 0.4
        elif isinstance(a, int) and isinstance(b, int) and not isinstance(a, bool):
            d += w * min(1.0, abs(a - b) * 0.6)
        else:
            d += w
    return d


# ---------------------------------------------------------------- description (the identity profile)
def _pick_words(name, key):
    S = SPECIES.get(name)
    return (S["words"].get(key) if S else "") or ""


def describe(g):
    don = g["donors"]
    a, b = g["parents"][0], g["parents"][-1]
    bits = []
    TN = don.get("body")
    bits.append(f"{_pick_words(TN, 'body')} (from the {TN})")
    if don.get("head") or don.get("face"):
        hn, fn = don.get("head"), don.get("face")
        sn = g["head"]["snout"][0]
        face = f"a {hn}-shaped head" if hn else "a head"
        if don.get("mouth") and don.get("mouth") != hn:
            face += f" with the {don['mouth']}'s mouth"
        if fn and fn != hn:
            face += f" and the {fn}'s eyes"
        bits.append(face)
    if don.get("legs"):
        bits.append(f"{_pick_words(don['legs'], 'limbs')} (from the {don['legs']})")
    else:
        bits.append("no legs")
    if g.get("arms"):
        bits.append({"pincers": "two huge pincers", "raptor": "spiked grasping forelegs", "ape": "long knuckle-walking arms"}.get(g["arms"][0], "strong arms"))
    if g["wings"]:
        bits.append(f"{g['wings'][0].replace('4', '').replace('2', '')} wings (from the {don.get('wings')})")
    if g["shell"]:
        bits.append(f"a {g['shell']} shell")
    bits.append(f"{_pick_words(don.get('tail'), 'tail')} (from the {don.get('tail')})")
    if g["dorsal"]:
        bits.append("a back with " + " and ".join(d.replace("_", " ") for d in g["dorsal"]))
    bits.append(f"{_pick_words(TN, 'skin')} mixed with {g['skin2']} texture")
    return bits


def describe_move(g):
    parts = [_pick_words(g["donors"].get("legs") or g["donors"].get("body"), "move")]
    if g["hover"] > 40:
        parts.append("hovers off the ground")
    if g["wingfly"]:
        parts.append("beats its wings")
    return ", ".join(p for p in parts if p)


def design_profile(g, name=""):
    """The identity profile: one place that says exactly what this creature is. Saved with the plan."""
    prof, L, Hh, pitch = g["torso"]
    a, b = g["parents"][0], g["parents"][-1]
    return {
        "hybrid_name": name, "parent_a": a, "parent_b": b, "dominant_parent": g.get("dominant"),
        "dominant": [f"{g['donors'].get('body')} body structure"],
        "secondary": [x for x in (f"{g['donors'].get('face')} eyes and face details" if g["donors"].get("face") else "",
                                  f"{g['donors'].get('tail')} tail", f"{g['donors'].get('wings')} wings" if g["wings"] else "") if x],
        "proportions": f"{prof} torso, {int(L)}x{int(Hh)}, {'upright' if pitch > 0.6 else 'horizontal'} posture, "
                       f"neck {int(g['neck'][0])}, legs {g['legs'][0]} x{g['legs'][1]}",
        "head": describe(g)[1] if len(describe(g)) > 1 else "",
        "limbs": f"{g['legs'][0]} x{g['legs'][1]}" + (f", {g['arms'][0]} arms" if g["arms"] else ""),
        "texture": f"{g['skin']} body, {g['skin2']} limbs and tail",
        "colour": {"main": g["pal"][0], "accent": g["pal"][1], "belly": g["pal"][2], "pattern": g["pat"]},
        "silhouette": "-".join(str(v) for v in (prof, "wings" if g["wings"] else "", g["tail"], "shell" if g["shell"] else "",
                                                  "+".join(g["dorsal"])) if v),
        "locomotion": describe_move(g), "gait": g["gait"], "hovers": g["hover"] > 40,
        "signature_ability": g["strike"], "temperament": g["temper"],
        "signature_feature": g["sig"][1] if g["sig"] else "",
        "summary": describe(g),
    }


def design_hybrid(a, b, avoid=(), seed=0, tries=60):
    """Invent a creature from parents a and b. `avoid` = fingerprints of earlier hybrids; the new design must
    differ from all of them (not just in name or colour, but in body, silhouette, face and movement)."""
    best = None
    for attempt in range(tries):
        rnd = random.Random(_seed(a, b, seed, attempt))
        g = _finish(_compose(a, b, rnd))
        g["name"] = f"{a}-{b}"
        fp = fingerprint(g)
        dist = min([fp_distance(fp, f) for f in avoid] or [99.0])
        if best is None or dist > best[0]:
            best = (dist, g, fp, attempt)
        if dist >= FP_MIN_DISTANCE:
            break
    dist, g, fp, attempt = best
    g["fingerprint"] = fp
    g["fp_distance"] = round(dist, 1)
    g["attempt"] = attempt
    return g


# ---------------------------------------------------------------- drawing helpers
def _tube(pen, pts, w0, w1, col, rim=True, cap=True, prof=None):
    """A smooth tapering tube along pts. prof(u) (0..1) can reshape the width."""
    n = len(pts)
    if n < 2:
        return
    Lp, Rp = [], []
    for i, (x, y) in enumerate(pts):
        a = pts[max(0, i - 1)]
        b = pts[min(n - 1, i + 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        m = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / m, dx / m
        u = i / (n - 1)
        w = (w0 + (w1 - w0) * u) * (prof(u) if prof else 1.0) / 2
        Lp.append((x + nx * w, y + ny * w))
        Rp.append((x - nx * w, y - ny * w))
    pen.poly(Lp + Rp[::-1], col, rim=rim)
    if cap:
        pen.ell(pts[0][0], pts[0][1], w0 / 2 * (prof(0) if prof else 1), w0 / 2 * (prof(0) if prof else 1), col, rim=False)
        e = (w1 / 2) * (prof(1) if prof else 1)
        pen.ell(pts[-1][0], pts[-1][1], e, e, col, rim=False)


def _ik(hx, hy, fx, fy, l1, l2, bend):
    dx, dy = fx - hx, fy - hy
    d = math.hypot(dx, dy)
    d = max(1e-3, min(d, l1 + l2 - 1e-3))
    a = (l1 * l1 - l2 * l2 + d * d) / (2 * d)
    h = math.sqrt(max(0.0, l1 * l1 - a * a))
    ux, uy = dx / math.hypot(dx, dy) if math.hypot(dx, dy) > 1e-6 else 0.0, dy / math.hypot(dx, dy) if math.hypot(dx, dy) > 1e-6 else 1.0
    return hx + ux * a - uy * h * bend, hy + uy * a + ux * h * bend


def _ellpts(cx, cy, rx, ry, n=22):
    return [(cx + rx * math.cos(2 * math.pi * i / n), cy + ry * math.sin(2 * math.pi * i / n)) for i in range(n)]


def _prof(name, u):
    """(top, bottom) half-thickness (0..1) at u along the torso, 0 = tail end, 1 = chest end."""
    s = math.sin
    if name == "barrel":
        t = 0.6 + 0.4 * s(math.pi * min(1, max(0, u * 0.92 + 0.04))) ** 0.7
        return t, t * 0.92
    if name == "sleek":
        t = 0.56 + 0.44 * math.exp(-((u - 0.76) / 0.28) ** 2) + 0.10 * math.exp(-((u - 0.1) / 0.16) ** 2)
        return min(1.0, t), min(1.0, t) * 0.78
    if name == "round":
        v = math.sqrt(max(0.0, 1 - (2 * u - 1) ** 2))
        t = 0.1 + 0.9 * v
        return t, t
    if name == "tube":
        tf = math.sqrt(max(0.0, 1 - ((u - 0.84) / 0.16) ** 2)) if u > 0.84 else 1.0
        t = max(0.14, (0.84 + 0.16 * u) * tf)
        return t, t * 0.9
    if name == "pear":
        v1 = math.sqrt(max(0.0, 1 - ((u - 0.27) / 0.29) ** 2))
        v2 = 0.62 * math.sqrt(max(0.0, 1 - ((u - 0.77) / 0.21) ** 2))
        t = max(v1, v2, 0.24 if 0.5 < u < 0.62 else 0.08)
        return t, t
    if name == "teardrop":
        t = 0.1 + 0.9 * s(math.pi * (0.05 + 0.8 * u ** 1.3)) ** 0.8
        return t, t
    if name in ("dome", "bell"):
        t = max(0.08, s(math.pi * min(1, max(0, u))) ** (0.55 if name == "dome" else 0.7))
        return t, t * (0.2 if name == "dome" else 0.14)
    if name == "flatdome":
        t = max(0.08, s(math.pi * u) ** 0.5)
        return t, t * 0.42
    if name == "flat":
        t = max(0.1, s(math.pi * u) ** 0.8)
        return t, t
    return 0.8, 0.8


def _foot(pen, fx, fy, kind, w, col, dk, lift=0.0):
    claw = (235, 230, 214)
    if kind == "none":
        return
    if kind in ("paw", "bigpaw"):
        r = w * (0.9 if kind == "paw" else 1.2)
        pen.ell(fx + r * 0.35, fy, r, r * 0.45, dk, rim=False)
        for k in range(3):
            pen.poly([(fx + r * (1.0 + k * 0.05) + k * 5, fy - 3 + k * 4), (fx + r * 1.5 + k * 5, fy + 3 + k * 4), (fx + r * 0.95 + k * 5, fy + 7 + k * 4)], claw, rim=False)
    elif kind == "hoof":
        pen.poly([(fx - w * 0.5, fy - w * 0.9), (fx + w * 0.6, fy - w * 0.9), (fx + w * 0.7, fy + 3), (fx - w * 0.45, fy + 3)], (34, 28, 30), rim=False)
    elif kind == "flat":
        pen.ell(fx + w * 0.2, fy - 2, w * 1.1, w * 0.45, dk, rim=False)
        for k in range(3):
            pen.ell(fx + w * (0.6 + k * 0.35), fy - 4, w * 0.16, w * 0.16, (224, 214, 190), rim=False)
    elif kind == "pad":
        pen.ell(fx + w * 0.4, fy - 2, w * 1.2, w * 0.4, dk, rim=False)
    elif kind in ("webbed", "toed"):
        pen.poly([(fx - w * 0.2, fy - w * 0.4), (fx + w * 1.6, fy + 2), (fx + w * 1.2, fy + w * 0.3), (fx - w * 0.2, fy + w * 0.3)], dk, rim=False)
        for k in range(3):
            pen.line([(fx, fy - 2), (fx + w * (1.3 + 0.1 * k), fy - 3 + k * 3)], 3, light(dk, 0.2))
    elif kind == "talon":
        for k in range(3):
            pen.line([(fx, fy - 4), (fx + w * 2.4, fy + k * 3 - 2), (fx + w * 2.9, fy + 5)], 4, (216, 190, 80))
        pen.line([(fx, fy - 4), (fx - w * 1.6, fy + 3)], 4, (216, 190, 80))
    elif kind == "longfoot":
        pen.poly([(fx - w * 0.2, fy - w * 0.5), (fx + w * 3.2, fy + 1), (fx + w * 3.4, fy + 6), (fx - w * 0.2, fy + 6)], dk, rim=False)
        pen.poly([(fx + w * 3.2, fy), (fx + w * 3.9, fy + 6), (fx + w * 3.2, fy + 6)], claw, rim=False)
    elif kind == "claw":
        for k in range(3):
            pen.poly([(fx + k * 6, fy - 4), (fx + w * 1.3 + k * 6, fy + 3), (fx + k * 6 + 2, fy + 4)], claw, rim=False)
    elif kind == "zygo":
        pen.poly([(fx - 5, fy - 6), (fx + 18, fy + 2), (fx - 5, fy + 4)], dk, rim=False)
        pen.poly([(fx - 4, fy - 6), (fx - 20, fy + 3), (fx - 4, fy + 4)], dk, rim=False)
    elif kind == "tipclaw":
        pen.poly([(fx - 4, fy - 3), (fx + 14, fy + 3), (fx - 4, fy + 4)], (30, 24, 24), rim=False)


# ---------------------------------------------------------------- patterns and textures on the body
def _body_marks(pen, g, spine, top, bot, col, dk, lt, rseed, scale=1.0):
    pat, skin = g["pat"], g["skin"]
    rnd = random.Random(rseed)
    n = len(spine)
    pcol = mix(col, (14, 10, 12), 0.42)
    acc = g["pal"][1]
    def T(i, v):   # v 0 = back, 1 = belly; i may fall between two rings
        i0 = int(i); i1 = min(i0 + 1, len(top) - 1); f = i - i0
        tx = top[i0][0] * (1 - f) + top[i1][0] * f; ty = top[i0][1] * (1 - f) + top[i1][1] * f
        bx = bot[i0][0] * (1 - f) + bot[i1][0] * f; by = bot[i0][1] * (1 - f) + bot[i1][1] * f
        return (tx * (1 - v) + bx * v, ty * (1 - v) + by * v)
    if pat == "spots":
        for _ in range(16):
            i = rnd.randint(1, n - 3)
            x, y = T(i, rnd.uniform(0.12, 0.6))
            r = rnd.uniform(6, 12) * scale
            pen.dot(x, y, r, pcol)
    elif pat == "stripes":
        for i in range(2, n - 2, 2):
            a, b = T(i, 0.0), T(i, 0.78)
            a2, b2 = T(i + 1, 0.0), T(i + 1, 0.7)
            pen.poly([a, a2, b2, b], pcol, rim=False)
    elif pat == "bands":
        for i in range(2, n - 3, 3):
            pen.poly([T(i, 0.0), T(i + 1, 0.0), T(i + 1, 0.85), T(i, 0.85)], mix(col, acc, 0.45), rim=False)
    elif pat == "mottled":
        for _ in range(24):
            i = rnd.randint(1, n - 3)
            x, y = T(i, rnd.uniform(0.05, 0.7))
            r = rnd.uniform(8, 20) * scale
            pen.ell(x, y, r, r * 0.7, mix(col, acc if rnd.random() < 0.5 else (20, 16, 20), 0.35), rim=False)
    elif pat == "diamond":
        for i in range(2, n - 2, 3):
            x, y = T(i, 0.3)
            pen.poly([(x - 14, y), (x, y - 16), (x + 14, y), (x, y + 16)], mix(col, acc, 0.3), rim=False)
    elif pat == "patches":
        for _ in range(11):
            i = rnd.randint(1, n - 3)
            x, y = T(i, rnd.uniform(0.15, 0.55))
            pen.poly([(x + math.cos(a) * rnd.uniform(14, 26), y + math.sin(a) * rnd.uniform(12, 22)) for a in
                      [k * 1.05 for k in range(6)]], mix(col, acc, 0.65), rim=False)
    elif pat == "barring":
        for i in range(2, n - 2, 2):
            for v in (0.25, 0.55):
                x, y = T(i, v)
                pen.line([(x - 10, y - 5), (x, y + 5), (x + 10, y - 5)], 4, pcol)
    elif pat == "eyespots":
        for _ in range(7):
            i = rnd.randint(2, n - 3)
            x, y = T(i, rnd.uniform(0.2, 0.55))
            pen.ell(x, y, 15 * scale, 15 * scale, acc, rim=False)
            pen.ell(x, y, 9 * scale, 9 * scale, (30, 30, 60), rim=False)
            pen.dot(x + 2, y - 2, 3, (240, 240, 255))
    elif pat == "mark":
        i = n // 3
        x, y = T(i, 0.35)
        pen.poly([(x - 22, y - 10), (x + 22, y - 10), (x, y + 30)], acc, rim=False)
    # ---- texture of the skin
    if skin == "scales" or skin == "scutes":
        step = 2
        for r_, v in enumerate((0.12, 0.3, 0.48, 0.66, 0.82)):
            for i in range(1 + (r_ % 2), n - 2, step):
                x, y = T(i, v)
                s = 7.5 * scale
                pen.poly([(x - s, y), (x, y - s * 0.8), (x + s, y), (x, y + s * 0.8)], lt if (i + r_) % 2 else dk, rim=False)
    elif skin == "feathers":
        for r_, v in enumerate((0.12, 0.3, 0.48, 0.66, 0.82)):
            for i in range(1 + (r_ % 2), n - 2, 2):
                x, y = T(i, v)
                pen.ell(x, y, 12 * scale, 8 * scale, mix(col, dk, 0.35), rim=False)
                pen.line([(x - 8 * scale, y + 2), (x + 8 * scale, y + 2)], 2, lt)
    elif skin == "chitin":
        for i in range(2, n - 2, 2):
            pen.line([T(i, 0.0), T(i, 0.5), T(i, 1.0)], 3, dk)
        pen.line([T(2, 0.22), T(n // 2, 0.2), T(n - 3, 0.22)], 3, lt)
    elif skin == "wrinkled":
        for _ in range(22):
            i = rnd.randint(1, n - 3)
            x, y = T(i, rnd.uniform(0.1, 0.9))
            pen.line([(x - 12, y - 3), (x, y + 4), (x + 12, y - 3)], 3, dk)
    elif skin == "plated":
        for i in range(2, n - 2, 3):
            pen.line([T(i, 0.05), T(i + 0.5, 0.5), T(i, 0.95)], 5, dk)
        pen.line([T(1, 0.35), T(n // 2, 0.3), T(n - 2, 0.35)], 5, dk)
    elif skin == "warty":
        for _ in range(26):
            i = rnd.randint(1, n - 2)
            x, y = T(i, rnd.uniform(0.05, 0.7))
            pen.dot(x, y, rnd.uniform(3, 6) * scale, dk)
    elif skin == "translucent":
        for i in range(2, n - 2, 2):
            x, y = T(i, 0.3)
            pen.dot(x, y, 6, lt)
    elif skin == "smooth" or skin == "scales":
        pen.line([T(2, 0.2), T(n // 2, 0.12), T(n - 2, 0.22)], 6, lt)


def _fur_tufts(pen, top, bot, col, scale=1.0):
    lt = light(col, 0.18)
    for pts, sgn in ((top, 1), (bot, -1)):
        for i in range(1, len(pts) - 1):
            x, y = pts[i]
            tx, ty = pts[i + 1][0] - pts[i - 1][0], pts[i + 1][1] - pts[i - 1][1]
            m = math.hypot(tx, ty) or 1
            tx, ty = tx / m, ty / m
            ox, oy = ty * sgn, -tx * sgn          # points away from the body
            pen.poly([(x - tx * 9, y - ty * 9), (x + tx * 9, y + ty * 9),
                      (x - tx * 5 + ox * 15 * scale, y - ty * 5 + oy * 15 * scale)], lt, rim=False)


def _horror_marks(pen, g, spine, top, bot, rseed):
    """Exposed ribs and glowing cracks. Both are placed on the body's own rings (ring index, back-to-belly), so they move
    with every bend of the spine."""
    n = len(spine)
    rnd = random.Random(rseed * 7 + 3)
    def T(i, v):
        i0 = int(i); i1 = min(i0 + 1, len(top) - 1); f = i - i0
        tx = top[i0][0] * (1 - f) + top[i1][0] * f; ty = top[i0][1] * (1 - f) + top[i1][1] * f
        bx = bot[i0][0] * (1 - f) + bot[i1][0] * f; by = bot[i0][1] * (1 - f) + bot[i1][1] * f
        return (tx * (1 - v) + bx * v, ty * (1 - v) + by * v)
    if g.get("ribs"):
        lo, hi = int(n * 0.30), int(n * 0.72)
        for i in range(lo, hi, 2):
            arc = [T(i, 0.12), T(i + 0.5, 0.42), T(i + 0.2, 0.70), T(i - 0.3, 0.92)]
            pen.line(arc, 7, (118, 108, 92))
            pen.line(arc, 4, (206, 198, 176))
    if g.get("cracks"):
        ec = tuple(g["head"].get("eyecol", (255, 150, 50)))[:3]
        for _ in range(7):
            i = rnd.uniform(2, n - 4); v = rnd.uniform(0.1, 0.8); pts = [T(i, v)]
            for k in range(5):
                i += rnd.uniform(0.3, 0.9); v = min(0.95, max(0.05, v + rnd.uniform(-0.18, 0.18))); pts.append(T(i, v))
            pen.line(pts, 9, (ec[0], ec[1], ec[2], 70))
            pen.line(pts, 4, (min(255, ec[0] + 20), min(255, ec[1] + 60), min(255, ec[2] + 40), 190))
            pen.line(pts, 1.6, (255, 232, 170))


# ---------------------------------------------------------------- head: a face built from the parts both parents gave it
BEAKCOL = (236, 190, 70)


def _head(pen, hx, hy, rot, g, roar, t, st):
    hd = g["head"]
    rx, ry = hd["skull"]
    col, acc, belly = [tuple(c) for c in g["pal"]]
    hcol = acc if g["pat"] == "head_white" else col
    dk, lt = dark(hcol, 0.62), light(hcol, 0.25)
    cr, sr = math.cos(rot), math.sin(rot)

    def P(x, y):
        return (hx + x * cr - y * sr, hy + x * sr + y * cr)

    def poly(pts, fill, rim=True):
        pen.poly([P(*p) for p in pts], fill, rim=rim)

    def E(x, y, a, b, fill, rim=True):
        poly(_ellpts(x, y, a, b, 20), fill, rim)

    def line(pts, w, fill):
        pen.line([P(*p) for p in pts], w, fill)

    sk = hd["snout"]
    kind, ln, bb, tp, dr = sk
    teeth = hd["teeth"]
    temper_open = 0.1 if g["temper"] in ("savage", "cold", "eerie") else 0.0
    opn = max(roar * 0.55, temper_open) if not kind.startswith("beak") else max(roar * 0.42, 0.0)
    if kind in ("tiny", "none", "trunk"):
        opn = roar * 0.5
    eyes = hd["eyes"]
    ex_, ey_ = rx * 0.34, -ry * 0.26
    ear_k, ear_s = hd["ears"]
    extras = hd["extras"]
    sig = g["sigscale"]
    bone = (232, 224, 200)

    # ---- things behind the skull
    if "mane" in extras:
        k = sig.get("mane", 1.0)
        mc = mix(acc, (60, 30, 12), 0.35)
        for i in range(16):
            a = i * 0.42 + 0.4
            E(-rx * 0.15 + math.cos(a) * rx * 0.95 * k, math.sin(a) * ry * 1.0 * k, rx * 0.55, rx * 0.5, mix(mc, col, 0.1 * (i % 3)), rim=False)
    if "gills_frill" in extras:
        for j in range(3):
            for k_ in range(4):
                a = -0.9 + j * 0.55 + k_ * 0.07 * 0
                pts = [(-rx * 0.5, -ry * 0.2 + j * ry * 0.3), (-rx * 0.5 - 40 - k_ * 6, -ry * 0.5 + j * ry * 0.3 - 28 * math.sin(t * 3 + j + k_))]
                line(pts, 6 - k_ * 0.8, mix(acc, (255, 130, 160), 0.4))
            E(-rx * 0.5 - 52, -ry * 0.5 + j * ry * 0.3, 9, 7, mix(acc, (255, 130, 160), 0.4), rim=False)
    if ear_k == "flap":
        E(-rx * 0.45, ry * 0.1, ear_s * 0.55, ear_s * 0.72, mix(col, (160, 110, 110), 0.22))
    if "crest_fan" in extras:
        for j in range(9):
            a = -1.9 + j * 0.3
            ex2, ey2 = math.cos(a) * 52 * sig.get("crest_fan", 1.0), math.sin(a) * 52 * sig.get("crest_fan", 1.0)
            line([(0, -ry * 0.8), (ex2, -ry * 0.8 + ey2)], 3, (30, 30, 40))
            E(ex2, -ry * 0.8 + ey2, 8, 8, acc, rim=False)
    if "antennae" in extras or "antennae_club" in extras:
        for j, ph_ in ((0, 0.0), (1, 1.4)):
            sw = 12 * math.sin(t * 2.2 + ph_)
            pts = [(rx * 0.1, -ry * 0.7), (rx * 0.5 + sw * 0.3, -ry * 1.6), (rx * 1.3 + sw, -ry * 2.1 + sw * 0.4)]
            line(pts, 3.5, dark(col, 0.5))
            if "antennae_club" in extras:
                E(pts[-1][0], pts[-1][1], 7, 7, dark(col, 0.5), rim=False)
    if "casque" in extras:
        k = sig.get("casque", 1.0)
        poly([(-rx * 0.9, -ry * 0.2), (-rx * 0.8, -ry * (1.6 + 0.5 * k)), (rx * 0.05, -ry * (1.0 + 0.3 * k)), (rx * 0.4, -ry * 0.5)], acc)
    if ear_k == "big":
        for off, c2 in ((0.2, dark(col, 0.8)), (0.0, col)):
            poly([(-rx * (0.5 - off * 0.2), -ry * 0.55), (-rx * 0.1 + off * 4, -ry * 0.7), (-rx * 0.4 + off * 6, -ry * 0.7 - ear_s)], c2)
        poly([(-rx * 0.42, -ry * 0.62), (-rx * 0.2, -ry * 0.7), (-rx * 0.36, -ry * 0.7 - ear_s * 0.72)], mix(col, (230, 150, 160), 0.5), rim=False)
    elif ear_k == "point":
        for off, c2 in ((0.16, dark(col, 0.78)), (0.0, col)):
            poly([(-rx * 0.62 + off * 10, -ry * 0.6), (rx * 0.05 + off * 8, -ry * 0.7), (-rx * 0.28 + off * 12, -ry * 0.62 - ear_s)], c2)
        poly([(-rx * 0.45, -ry * 0.64), (-rx * 0.12, -ry * 0.7), (-rx * 0.28, -ry * 0.64 - ear_s * 0.62)], mix(col, (220, 150, 150), 0.4), rim=False)
    elif ear_k == "round":
        E(-rx * 0.35, -ry * 0.72, ear_s * 0.45, ear_s * 0.45, col)
        E(-rx * 0.35, -ry * 0.72, ear_s * 0.26, ear_s * 0.26, mix(col, (220, 150, 150), 0.4), rim=False)
    elif ear_k == "tufts":
        poly([(-rx * 0.3, -ry * 0.8), (-rx * 0.05, -ry * 0.9), (-rx * 0.55, -ry * 0.9 - ear_s)], dark(col, 0.8))
        poly([(rx * 0.0, -ry * 0.85), (rx * 0.3, -ry * 0.8), (rx * 0.05, -ry * 0.85 - ear_s * 0.9)], col)
    if "horns_bull" in extras:
        k = sig.get("horns_bull", 1.0)
        for off, c2 in ((0.25, dark(bone, 0.8)), (0.0, bone)):
            pts = [(-rx * (0.1 - off * 0.3), -ry * 0.8), (-rx * 0.3, -ry * 1.5 * k), (rx * 0.35, -ry * 2.1 * k), (rx * 0.85, -ry * 2.2 * k)]
            _tube(pen, [P(*p) for p in pts], rx * 0.36, rx * 0.04, c2, rim=True)
    if "ossicones" in extras:
        for off, c2 in ((0.3, dark(col, 0.7)), (0.0, col)):
            line([(-rx * 0.1 + off * rx * 0.5, -ry * 0.8), (-rx * 0.15 + off * rx * 0.5, -ry * 1.8)], 8, c2)
            E(-rx * 0.15 + off * rx * 0.5, -ry * 1.9, 9, 9, dark(col, 0.5), rim=False)
    if "crown" in extras:
        poly([(-rx * 0.6, -ry * 0.55), (-rx * 0.1, -ry * 1.45), (rx * 0.3, -ry * 0.8)], dark(col, 0.75))

    # ---- the skull
    E(0, 0, rx, ry, hcol)
    if g["skin"] == "fur" and ear_k != "none":
        for i in range(4):
            a = 2.3 + i * 0.45
            poly([(math.cos(a) * rx * 0.95, math.sin(a) * ry * 0.95), (math.cos(a + 0.3) * rx * 0.95, math.sin(a + 0.3) * ry * 0.95),
                  (math.cos(a + 0.15) * rx * 1.25, math.sin(a + 0.15) * ry * 1.25)], light(hcol, 0.1), rim=False)
    if g["pat"] == "mask":
        E(rx * 0.22, -ry * 0.18, rx * 0.5, ry * 0.36, dark(col, 0.55), rim=False)
    elif g["pat"] == "throat":
        E(rx * 0.1, ry * 0.65, rx * 0.5, ry * 0.3, acc, rim=False)
    elif g["pat"] in ("stripes", "bands"):
        for j in range(2):
            line([(-rx * 0.1 + j * rx * 0.4, -ry * 0.9), (rx * 0.1 + j * rx * 0.4, -ry * 0.1)], 6, dark(col, 0.55))
    elif g["pat"] in ("spots", "mottled", "patches"):
        for dx, dy in ((-rx * 0.3, -ry * 0.4), (-rx * 0.05, -ry * 0.55), (-rx * 0.45, ry * 0.1)):
            E(dx, dy, 7, 7, dark(col, 0.6), rim=False)
    elif g["pat"] == "eyespots":
        E(-rx * 0.3, -ry * 0.2, 10, 10, acc, rim=False)

    # ---- the mouth: upper jaw, lower jaw, interior and teeth
    def jaw_rot(px, py, o):
        c, s = math.cos(o), math.sin(o)
        return lambda x, y: (px + (x - px) * c - (y - py) * s, py + (x - px) * s + (y - py) * c)

    lowcol = mix(hcol, belly, 0.55)
    gum = (112, 18, 28)

    if kind in ("snout", "wide", "blunt", "cone"):
        x0, yc = rx * 0.3, ry * 0.22
        yt = yc + dr * ln
        sc_ = sig.get("snout", 1.0)
        ln *= sc_
        yt = yc + dr * ln
        if kind == "cone":
            upper = [(x0 - 4, yc - bb), (x0 + ln * 0.5, yc - bb * 0.5 + dr * ln * 0.5), (x0 + ln, yt - 3), (x0 + ln, yt), (x0, yc)]
            lw = [(x0, yc), (x0 + ln * 0.82, yt), (x0 + ln * 0.74, yt + 11), (x0 + ln * 0.3, yc + bb * 0.55), (x0 - 2, yc + bb * 0.6)]
        elif kind == "wide":
            upper = [(x0 - 4, yc - bb), (x0 + ln * 0.5, yc - bb * 0.85), (x0 + ln, yt - tp), (x0 + ln + 3, yt - 2), (x0 + ln, yt), (x0 - rx * 0.4, yc - 2)]
            lw = [(x0 - rx * 0.4, yc - 2), (x0 + ln * 0.97, yt), (x0 + ln * 0.9, yt + tp * 0.55 + 4), (x0 + ln * 0.4, yc + bb * 0.35), (x0 - 2, yc + bb * 0.5)]
        else:
            upper = [(x0 - 4, yc - bb), (x0 + ln * 0.5, yc - (bb + tp) * 0.47 + dr * ln * 0.5), (x0 + ln, yt - tp),
                     (x0 + ln + 4, yt - tp * 0.25), (x0 + ln, yt), (x0, yc)]
            lw = [(x0, yc), (x0 + ln * 0.97, yt), (x0 + ln * 0.92, yt + tp * 0.6), (x0 + ln * 0.45, yc + (bb * 0.55 + tp * 0.5) * 0.5 + dr * ln * 0.45), (x0 - 2, yc + bb * 0.6)]
        o = opn * (1.2 if kind in ("wide", "blunt") else 1.0)
        rotf = jaw_rot(x0 * 0.4, yc + 2, o)
        lwr = [rotf(*p) for p in lw]
        if o > 0.04:
            poly([(x0 * 0.4, yc + 2), (x0 + ln * 0.98, yt), lwr[1], (x0 * 0.4, yc + 6)], gum, rim=False)
            tx_, ty_ = (x0 + ln * 0.5), yc + dr * ln * 0.5
            E(tx_, ty_ + 14 + 24 * o, ln * 0.25, 7 + 6 * o, (190, 70, 84), rim=False)
        poly(lwr, lowcol)
        # lower teeth
        if o > 0.08 and teeth not in ("none",):
            nt = max(3, int(ln / 22))
            for i in range(nt):
                fx_ = x0 + ln * (0.2 + 0.7 * i / max(1, nt - 1))
                fy_ = yc + dr * ln * (fx_ - x0) / ln
                p1, p2 = rotf(fx_, fy_), rotf(fx_ + 7, fy_ + dr * 7)
                hgt = 10 + bb * 0.22 if teeth != "needle" else 14 + bb * 0.3
                tip = rotf(fx_ + 3, fy_ - hgt)
                pen.poly([(hx + p1[0] * cr - p1[1] * sr, hy + p1[0] * sr + p1[1] * cr),
                          (hx + p2[0] * cr - p2[1] * sr, hy + p2[0] * sr + p2[1] * cr),
                          (hx + tip[0] * cr - tip[1] * sr, hy + tip[0] * sr + tip[1] * cr)], (244, 240, 226), rim=False)
        poly(upper, hcol)
        if g["pat"] == "mask":
            poly([(x0, yc - bb), (x0 + ln * 0.6, yc - bb * 0.7 + dr * ln * 0.5), (x0 + ln * 0.6, yc - bb * 0.2 + dr * ln * 0.5), (x0, yc - 2)], dark(col, 0.6), rim=False)
        # upper teeth
        if teeth not in ("none",):
            tt = (244, 240, 226)
            if teeth == "fangs":
                for fx_, hg in ((0.78, 1.0), (0.95, 0.42), (0.6, 0.35)):
                    xx = x0 + ln * fx_
                    yy = yc + dr * ln * fx_
                    poly([(xx - 6, yy - 1), (xx + 6, yy - 1), (xx + 1, yy + (16 + bb * 0.55) * hg * (1 + 0.5 * opn))], tt, rim=False)
            elif teeth == "flat":
                for i in range(5):
                    xx = x0 + ln * (0.55 + i * 0.09)
                    yy = yc + dr * ln * (0.55 + i * 0.09)
                    poly([(xx - 4, yy - 1), (xx + 4, yy - 1), (xx + 4, yy + 6), (xx - 4, yy + 6)], tt, rim=False)
            else:
                nt = max(4, int(ln / 15))
                for i in range(nt):
                    fx_ = 0.18 + 0.8 * i / max(1, nt - 1)
                    xx, yy = x0 + ln * fx_, yc + dr * ln * fx_
                    hgt = (9 + bb * 0.22) * (1.5 if teeth == "needle" else 1.0) * (0.8 + 0.4 * ((i * 7) % 3) / 2)
                    poly([(xx - 4, yy - 1), (xx + 4, yy - 1), (xx, yy + hgt * (1 + 0.5 * opn))], tt, rim=False)
        nx_, ny_ = x0 + ln * 0.88, yt - tp * 0.7 - 2
        E(nx_, ny_ - 1, 5, 3.5, (20, 14, 18), rim=False)
        if "nostril_bump" in extras:
            E(x0 + ln * 0.93, yt - tp - 4, 12, 10, hcol)
        if "noseleaf" in extras:
            poly([(x0 + ln * 0.9, yt - tp), (x0 + ln * 0.95, yt - tp - 28), (x0 + ln * 1.05, yt - tp - 2)], mix(col, (220, 140, 150), 0.5))
        if "horn_nose" in extras:
            k = sig.get("horn_nose", 1.0)
            poly([(x0 + ln * 0.5, yc - bb * 0.9 + dr * ln * 0.45), (x0 + ln * 0.9, yt - tp), (x0 + ln * 0.78, yc - bb * 0.9 - 72 * k + dr * ln * 0.8)], (210, 200, 180))
        if "whiskers" in extras:
            for j in range(3):
                line([(x0 + ln * 0.5, yc - 4 + j * 4), (x0 + ln * 0.95, yc + 6 + j * 14), (x0 + ln * 1.1, yc + 10 + j * 22)], 2.5, (230, 230, 230))
        if "tusks" in extras or "tusk_row" in extras:
            k = sig.get("tusks", 1.0)
            poly([(x0 + ln * 0.55, yc + 3), (x0 + ln * 0.63, yc + 3), (x0 + ln * 0.8 + 20 * k, yc + 40 * k + dr * ln * 0.6)], bone)
        if "mandibles" in extras:
            pass
    elif kind.startswith("beak"):
        x0, yc = rx * 0.5, ry * 0.2
        hook = {"beak_hook": bb * 1.35, "beak_short": bb * 0.4, "beak_needle": 2, "beak_bent": bb * 0.3, "beak_turtle": bb * 0.9}.get(kind, bb * 0.4)
        bend = ln * 0.5 if kind == "beak_bent" else 0.0
        bcol = (150, 130, 90) if kind == "beak_turtle" else BEAKCOL
        if kind == "beak_hook" and g["pat"] != "plain" and "owl" in hd["eyes"]:
            bcol = (200, 180, 130)

        def bendp(p):
            return (p[0], p[1] + bend * max(0.0, (p[0] - (x0 + ln * 0.45)) / (ln * 0.55)) ** 1.6)
        sc_ = sig.get("snout", 1.0)
        ln2 = ln * sc_
        upper = [(x0 - 8, yc - bb), (x0 + ln2 * 0.55, yc - bb * 0.78), (x0 + ln2 * 0.93, yc - bb * 0.1 + hook * 0.3), (x0 + ln2, yc + hook),
                 (x0 + ln2 * 0.86, yc + hook * 0.4 + 2), (x0 + ln2 * 0.5, yc + 1), (x0, yc + 1)]
        lw = [(x0, yc + 1), (x0 + ln2 * 0.86, yc + hook * 0.4 + 3), (x0 + ln2 * 0.74, yc + bb * 0.62 + hook * 0.3 + 4), (x0, yc + bb * 0.7)]
        upper = [bendp(p) if i > 1 else p for i, p in enumerate(upper)] if bend else upper
        lw = [bendp(p) for p in lw] if bend else lw
        o = opn
        rotf = jaw_rot(x0, yc + 2, o)
        lwr = [rotf(*p) for p in lw]
        if o > 0.05:
            poly([(x0, yc + 1), (x0 + ln2, yc + hook), lwr[1], (x0, yc + 4)], gum, rim=False)
        poly(lwr, mix(bcol, (80, 60, 40), 0.2))
        poly(upper, bcol)
        E(x0 + ln2 * 0.25, yc - bb * 0.55, 3, 2.2, (50, 36, 24), rim=False)
        if "noseleaf" in extras:
            poly([(x0 + ln2 * 0.1, yc - bb), (x0 + ln2 * 0.15, yc - bb - 22), (x0 + ln2 * 0.3, yc - bb)], mix(col, (220, 140, 150), 0.5))
    elif kind == "trunk":
        x0, yc = rx * 0.55, ry * 0.2
        pts = []
        for j in range(9):
            u = j / 8
            pts.append((x0 + ln * (0.3 * u + 0.14 * math.sin(2.2 * u + t * 1.4)),
                        yc + ln * (0.95 * u - 1.5 * roar * u * (1 - 0.3 * u))))
        _tube(pen, [P(*p) for p in pts], rx * 0.5, rx * 0.2, hcol, rim=True)
        for j in range(1, 8, 2):
            a, b_ = pts[j], pts[j + 1] if j + 1 < len(pts) else pts[j]
            line([(a[0] - rx * 0.2, a[1]), (a[0] + rx * 0.2, a[1] + 2)], 2, dk)
        if "tusks" in extras:
            k = sig.get("tusks", 1.0)
            for off in (0.0, 0.2):
                _tube(pen, [P(x0 + 4 + off * 20, yc + 6), P(x0 + ln * 0.18 + off * 20, yc + ln * 0.25 * k), P(x0 + ln * 0.5, yc + ln * 0.2 * k)], rx * 0.2, rx * 0.04, bone)
    elif kind == "tiny":
        x0, yc = rx * 0.5, ry * 0.2
        E(x0, yc, ln * 0.9, ry * 0.5, mix(hcol, belly, 0.2))
        if "mandibles" in extras:
            k = sig.get("mandibles", 1.0)
            o = 0.25 + opn * 0.9
            for sgn in (1, -1):
                pts = [(x0 + 6, yc + 4), (x0 + 30 * k, yc + 14 + sgn * 16 * (1 + o)), (x0 + 50 * k, yc + 4 + sgn * 6 * (1 + o))]
                _tube(pen, [P(*p) for p in pts], 12, 3, dark(col, 0.45))
        if "chelicerae" in extras:
            for j, off in enumerate((-8, 8)):
                _tube(pen, [P(x0 + 4 + off * 0.3, yc + 8), P(x0 + 14, yc + 28 + off * 0.2), P(x0 + 18 + off * 0.4, yc + 44 + opn * 18)], 11, 2, dark(col, 0.5))
                E(x0 + 18 + off * 0.4, yc + 44 + opn * 18, 3, 3, (180, 255, 90), rim=False)
        if "pedipalps" in extras:
            for off in (-1, 1):
                E(x0 + 18, yc + off * 5 + 6, 8, 5, dark(col, 0.6))
    if "lure" in extras and (kind == "none" or True):
        k = sig.get("lure", 1.0)
        pts = [(rx * 0.2, -ry * 0.9), (rx * 0.9, -ry * 2.0 * k), (rx * 1.9 + 8 * math.sin(t * 2), -ry * 1.9 * k)]
        line(pts, 4, dark(col, 0.5))
        gl = 0.7 + 0.3 * math.sin(t * 5)
        c_ = P(*pts[-1])
        for r_, a_ in ((28, 50), (17, 110), (9, 255)):
            x0_, x1_ = sorted((pen.X(c_[0] - r_), pen.X(c_[0] + r_)))
            pen.d.ellipse((x0_, pen.Y(c_[1] - r_), x1_, pen.Y(c_[1] + r_)), fill=(120, 255, 220, int(a_ * gl)))
    if "gills" in extras:
        for j in range(3):
            line([(-rx * 0.15 - j * 11, -ry * 0.2), (-rx * 0.28 - j * 11, ry * 0.15), (-rx * 0.15 - j * 11, ry * 0.5)], 3, dark(col, 0.45))
    if "barnacles" in extras:
        for dx, dy in ((rx * 0.3, ry * 0.5), (rx * 0.5, ry * 0.3), (rx * 0.1, ry * 0.6)):
            E(dx, dy, 6, 5, (210, 210, 200), rim=False)
    if "browridge" in extras:
        poly([(-rx * 0.1, -ry * 0.55), (rx * 0.7, -ry * 0.5), (rx * 0.72, -ry * 0.25), (-rx * 0.1, -ry * 0.3)], dark(col, 0.6), rim=False)

    # ---- the eyes
    ec = tuple(hd["eyecol"])
    glow = g.get("glow", False)
    brow = hd["brow"] + (0.25 if g["temper"] in ("savage",) else 0.0)
    er = max(6.0, ry * 0.26)

    def glow_eye(x, y, r):
        for k_, a_ in ((3.0, 0.14), (2.0, 0.3), (1.4, 0.55)):
            E(x, y, r * k_, r * k_, mix((20, 14, 26), ec, a_), rim=False)
        E(x, y, r, r * 0.9, ec, rim=False)
        E(x + 1, y, r * 0.3, r * 0.75, (40, 10, 0), rim=False)

    def plain_eye(x, y, r, iris=ec, slit=False, white=True):
        if white:
            E(x, y, r, r * 0.92, (246, 244, 236), rim=False)
        E(x + r * 0.12, y, r * 0.72, r * 0.72, iris, rim=False)
        if slit:
            E(x + r * 0.12, y, r * 0.2, r * 0.7, (12, 8, 10), rim=False)
        else:
            E(x + r * 0.18, y, r * 0.38, r * 0.38, (12, 8, 10), rim=False)
        E(x + r * 0.35, y - r * 0.3, r * 0.16, r * 0.16, (255, 255, 255), rim=False)

    if eyes == "none":
        pass
    elif glow and eyes not in ("compound", "multi", "stalk"):
        glow_eye(ex_, ey_, er * 1.15)
    elif eyes in ("round", "sleepy"):
        plain_eye(ex_, ey_, er)
        if eyes == "sleepy":
            poly([(ex_ - er * 1.1, ey_ - er * 1.1), (ex_ + er * 1.1, ey_ - er * 1.1), (ex_ + er * 1.1, ey_ - er * 0.05), (ex_ - er * 1.1, ey_ + er * 0.05)], hcol, rim=False)
            line([(ex_ - er * 1.1, ey_), (ex_ + er * 1.1, ey_ + 1)], 3, dk)
            for j in range(3):
                line([(ex_ - er * 0.3 + j * er * 0.45, ey_ - 1), (ex_ - er * 0.5 + j * er * 0.5, ey_ - er * 0.9)], 2, dk)
    elif eyes in ("slit", "bigslit"):
        plain_eye(ex_, ey_, er * (1.5 if eyes == "bigslit" else 1.0), iris=ec, slit=True, white=False)
    elif eyes == "fierce":
        plain_eye(ex_, ey_, er * 0.9, iris=(244, 196, 40), white=False)
        E(ex_ + er * 0.15, ey_, er * 0.3, er * 0.3, (12, 8, 10), rim=False)
        brow = max(brow, 0.5)
    elif eyes == "beady":
        E(ex_, ey_, er * 0.62, er * 0.62, (14, 10, 12), rim=False)
        E(ex_ + er * 0.2, ey_ - er * 0.2, er * 0.16, er * 0.16, (255, 255, 255), rim=False)
    elif eyes == "owl":
        E(rx * 0.22, 0, ry * 0.95, ry * 0.92, mix(belly, (255, 255, 255), 0.25))
        E(rx * 0.22, 0, ry * 0.52, ry * 0.52, ec, rim=False)
        E(rx * 0.22, 0, ry * 0.26, ry * 0.26, (10, 8, 8), rim=False)
        E(rx * 0.3, -ry * 0.12, ry * 0.1, ry * 0.1, (255, 255, 255), rim=False)
    elif eyes in ("bulge", "periscope"):
        k = 1.0 if eyes == "bulge" else 0.6
        E(rx * 0.12, -ry * 0.92, rx * 0.34 * k + 6, rx * 0.3 * k + 6, hcol)
        plain_eye(rx * 0.16, -ry * 0.96, rx * 0.2 * k + 4, iris=ec, slit=False, white=False)
        E(rx * 0.16, -ry * 0.96, rx * 0.2 * k * 0.28 + 1, rx * 0.2 * k * 0.8 + 3, (10, 8, 8), rim=False)
    elif eyes == "bump":
        E(rx * 0.2, -ry * 0.95, ry * 0.42, ry * 0.38, hcol)
        plain_eye(rx * 0.24, -ry * 1.0, ry * 0.26, iris=ec, slit=True, white=False)
    elif eyes == "turret":
        E(rx * 0.1, -ry * 0.1, ry * 0.7, ry * 0.7, mix(hcol, acc, 0.3))
        E(rx * 0.1, -ry * 0.1, ry * 0.45, ry * 0.45, dark(hcol, 0.6), rim=False)
        E(rx * 0.1, -ry * 0.1, ry * 0.16, ry * 0.16, (8, 6, 6), rim=False)
        E(rx * 0.2, -ry * 0.18, ry * 0.07, ry * 0.07, (255, 255, 255), rim=False)
    elif eyes == "compound":
        E(rx * 0.25, -ry * 0.05, rx * 0.75 + 4, ry * 0.8 + 4, mix(ec, (10, 40, 50), 0.35))
        for j in range(-2, 3):
            line([(rx * 0.25 + j * rx * 0.28, -ry * 0.7), (rx * 0.25 + j * rx * 0.28, ry * 0.6)], 1.8, dark(ec, 0.5))
        for j in range(-2, 3):
            line([(rx * 0.25 - rx * 0.6, j * ry * 0.3), (rx * 0.25 + rx * 0.6, j * ry * 0.3)], 1.8, dark(ec, 0.5))
        E(rx * 0.4, -ry * 0.3, rx * 0.12, ry * 0.12, (230, 255, 255), rim=False)
    elif eyes == "multi":
        for (dx, dy, r) in ((0.2, -0.45, 0.2), (0.5, -0.42, 0.16), (0.05, -0.2, 0.1), (0.35, -0.12, 0.1), (0.62, -0.18, 0.08), (0.2, 0.08, 0.07)):
            E(rx * dx, ry * dy, ry * r + 2, ry * r + 2, (14, 10, 14))
            E(rx * dx + 1, ry * dy - 1, ry * r * 0.3 + 0.5, ry * r * 0.3 + 0.5, (255, 255, 255), rim=False)
            if g.get("glow"):
                E(rx * dx, ry * dy, ry * r * 0.8, ry * r * 0.8, ec, rim=False)
    elif eyes == "stalk":
        for off, c2 in ((0.0, hcol), (rx * 0.55, hcol)):
            line([(rx * 0.1 + off, -ry * 0.3), (rx * 0.1 + off, -ry * 1.1 - 4)], 8, dark(col, 0.7))
            E(rx * 0.1 + off, -ry * 1.2 - 6, 11, 11, (14, 10, 12))
            E(rx * 0.1 + off + 3, -ry * 1.2 - 9, 3, 3, (255, 255, 255), rim=False)
    elif eyes == "slitbulge":
        E(rx * 0.3, -ry * 0.28, ry * 0.32, ry * 0.34, mix(hcol, belly, 0.2))
        plain_eye(rx * 0.3, -ry * 0.28, ry * 0.24, iris=ec, slit=False, white=False)
        E(rx * 0.3, -ry * 0.28, ry * 0.2, ry * 0.07, (8, 6, 6), rim=False)
    elif eyes == "toplook":
        E(rx * 0.3, -ry * 0.3, 9, 7, (14, 10, 12))
    elif eyes == "bump":
        pass
    if abs(brow) > 0.08 and eyes not in ("compound", "multi", "stalk", "bulge", "periscope", "bump", "turret", "owl", "none"):
        b_ = brow
        poly([(ex_ - er * 1.5, ey_ - er * (1.15 + b_ * 1.9)), (ex_ + er * 1.5, ey_ - er * (1.15 - b_ * 1.9)),
              (ex_ + er * 1.5, ey_ - er * (0.55 - b_ * 1.9)), (ex_ - er * 1.5, ey_ - er * (0.55 + b_ * 1.9))], dark(hcol, 0.5), rim=False)
    if "tearmarks" in extras:
        line([(ex_ + 2, ey_ + er), (ex_ + 8, ry * 0.4), (ex_ + 24, ry * 0.5)], 6, (30, 24, 20))
    return P(rx * 0.7, 0)


# ---------------------------------------------------------------- wings, tails and other appendages
FLAP_SPEED = {"hummingbird": 30.0, "dragonfly": 34.0, "butterfly": 5.0, "bat": 7.0, "eagle": 4.2, "owl": 4.6,
              "peacock": 3.0, "flamingo": 4.0, "mantis": 0.0}


def _wing(pen, kind, size, sx, sy, flap, col, acc, dk, far=False, pat="plain", t=0.0, fast=False):
    k = size
    c = dark(col, 0.62) if far else col
    c2 = dark(acc, 0.6) if far else acc
    if kind in ("bird", "bat"):
        ph = 1.0 + 0.62 * flap
        wr = (sx - math.cos(ph) * 0.5 * k, sy - math.sin(ph) * 0.5 * k)
        ph2 = ph * 0.55 - 0.18
        tip = (wr[0] - math.cos(ph2) * 0.58 * k, wr[1] - math.sin(ph2) * 0.58 * k)
        tr = (sx - 0.3 * k, sy + 0.1 * k)
        if kind == "bird":
            pts = [(sx, sy - 6), wr, tip]
            for i in range(1, 7):
                u = i / 6
                px, py = tip[0] + (tr[0] - tip[0]) * u, tip[1] + (tr[1] - tip[1]) * u
                dxn, dyn = tr[1] - tip[1], -(tr[0] - tip[0])
                m = math.hypot(dxn, dyn) or 1
                pts.append((px - dxn / m * 0.08 * k * (1 if i % 2 else 0.4), py - dyn / m * 0.08 * k * (1 if i % 2 else 0.4)))
            pts.append((sx - 0.1 * k, sy + 8))
            pen.poly(pts, mix(c, (0, 0, 0), 0.12))
            for i in range(3, 3 + 5):
                pen.line([wr, pts[i]], 3, dark(c, 0.7))
            pen.line([(sx, sy), wr, tip], 9 * k / 300 + 4, light(c, 0.1))
        else:
            fing = []
            for j, a in enumerate((ph2 - 0.35, ph2 + 0.05, ph2 + 0.45)):
                fing.append((wr[0] - math.cos(a) * 0.6 * k, wr[1] - math.sin(a) * 0.6 * k))
            mem = [(sx, sy), wr, fing[0], ((fing[0][0] + fing[1][0]) / 2 + 6, (fing[0][1] + fing[1][1]) / 2 + 22), fing[1],
                   ((fing[1][0] + fing[2][0]) / 2 + 6, (fing[1][1] + fing[2][1]) / 2 + 22), fing[2], (sx - 0.28 * k, sy + 0.18 * k)]
            pen.poly(mem, mix(c, c2, 0.3))
            for f in fing:
                pen.line([wr, f], 4, dark(c, 0.6))
            pen.line([(sx, sy), wr], 8, dark(c, 0.7))
    elif kind in ("insect4", "insect2"):
        n = 2 if kind == "insect4" else 1
        for j in range(n):
            for fw in (0, 1):
                a = 0.9 + 0.5 * flap * (1 if fw == 0 else -0.8) - j * 0.0
                ln = k * (0.62 if fw == 0 else 0.5)
                if kind == "insect2":
                    a = 0.05
                    ln = k * 0.8
                bx, by = sx - j * 0.1 * k + fw * 0.12 * k, sy - 4
                pts = _ellpts(0, 0, ln / 2, 12 + k * 0.025, 14)
                ca, sa = math.cos(a), math.sin(a)
                cx_, cy_ = bx - ca * ln / 2, by - sa * ln / 2
                dx_, dy_ = -ca, -sa
                pp = [(cx_ + px * dx_ - py * dy_, cy_ + px * dy_ + py * dx_) for px, py in pts]
                al = 70 if (fast and kind == "insect4") else 120
                pen.poly(pp, (214, 240, 244, al) if not far else (150, 190, 200, 80), rim=True)
                pen.line([(bx, by), (cx_ - math.cos(a) * ln / 2, cy_ - math.sin(a) * ln / 2)], 2, (160, 200, 210, 150))
    elif kind == "butterfly":
        a = 1.0 + 0.7 * flap
        up = [(sx, sy), (sx - math.cos(a) * 0.5 * k, sy - math.sin(a) * 0.55 * k), (sx - math.cos(a - 0.35) * 0.78 * k, sy - math.sin(a - 0.35) * 0.78 * k),
              (sx - math.cos(a - 0.9) * 0.62 * k, sy - math.sin(a - 0.9) * 0.62 * k), (sx - 0.1 * k, sy + 0.04 * k)]
        lo = [(sx, sy + 4), (sx - math.cos(a - 1.1) * 0.5 * k, sy - math.sin(a - 1.1) * 0.5 * k + 0.1 * k),
              (sx - math.cos(a - 1.4) * 0.4 * k, sy - math.sin(a - 1.4) * 0.3 * k + 0.2 * k), (sx - 0.05 * k, sy + 0.12 * k)]
        for poly_ in (lo, up):
            pen.poly(poly_, c)
            pen.poly([(sx + (x - sx) * 0.82, sy + (y - sy) * 0.82) for x, y in poly_], mix(c, c2, 0.0), rim=False)
        for poly_ in (up, lo):
            m = poly_[2]
            pen.ell(m[0] + (sx - m[0]) * 0.3, m[1] + (sy - m[1]) * 0.3, 0.06 * k, 0.06 * k, c2, rim=False)
            pen.ell(m[0] + (sx - m[0]) * 0.3, m[1] + (sy - m[1]) * 0.3, 0.035 * k, 0.035 * k, (20, 20, 50), rim=False)
    elif kind == "flipper":
        a = -0.45 + 0.5 * flap
        L_ = k * 0.55
        tx, ty = sx - math.sin(a) * L_ * 0.5 - 6, sy + math.cos(a) * L_
        pen.poly([(sx, sy - 10), (sx - 22, sy + 8), (tx - 8, ty), (tx + 12, ty - 4), (sx + 14, sy + 4)], c)
    elif kind == "fin":
        a = 0.2 * flap
        pen.poly([(sx + 28, sy), (sx - 18, sy + 6), (sx - k * 0.55, sy + k * 0.42 + a * 20), (sx - k * 0.1, sy + k * 0.12)], c)
    elif kind == "disc":
        pass


def _path(R, back, length, n, wave=0.0, wph=0.0, droop=0.0):
    pts = []
    bx, by = back
    nx, ny = -by, bx
    for i in range(n + 1):
        u = i / n
        w = wave * math.sin(wph - u * 4.0) * u
        pts.append((R[0] + bx * length * u + nx * w, R[1] + by * length * u + ny * w + droop * length * u * u))
    return pts


def _tail(pen, g, R, back, Hh, ph, t, col, acc, dk, lt, lunge=0.0, strike=False):
    kind = g["tail"]
    tl = g["tail_len"] * g["sigscale"].get("tail", 1.0)
    if kind == "none":
        return
    ang = math.atan2(back[1], back[0])
    if kind == "stub":
        pen.ell(R[0] + back[0] * 12, R[1] + back[1] * 12, 22, 18, col)
        return
    if kind in ("whip", "thick", "taper", "paddle", "tuft", "whip_barb"):
        ln = {"whip": 190, "thick": 210, "taper": 220, "paddle": 170, "tuft": 190, "whip_barb": 230}[kind] * tl
        w0 = {"whip": Hh * 0.2, "thick": Hh * 0.5, "taper": Hh * 0.24, "paddle": Hh * 0.28, "tuft": Hh * 0.16, "whip_barb": Hh * 0.2}[kind]
        w0 = max(10, min(w0, 70))
        pts = _path(R, back, ln, 12, wave=24 if kind != "thick" else 18, wph=ph * 1.1, droop=0.12 if kind in ("thick", "whip") else 0.0)
        if kind == "paddle":
            _tube(pen, pts, w0, 6, col, prof=lambda u: 1 + 0.9 * math.sin(math.pi * min(1, u * 1.1)) ** 1.2)
            pen.line(pts[2:], 4, lt)
        else:
            _tube(pen, pts, w0, 5 if kind != "taper" else 2, col)
        if kind == "tuft":
            e = pts[-1]
            pen.ell(e[0], e[1], 22, 15, dark(acc, 0.7))
        if kind == "whip_barb":
            m = pts[7]
            pen.poly([(m[0] - 4, m[1]), (m[0] + 4, m[1]), (m[0] + back[0] * 30 - 2, m[1] - 34)], (226, 220, 200), rim=False)
        return
    if kind == "bushy":
        ln = 170 * tl
        pts = _path(R, back, ln, 10, wave=16, wph=ph * 1.0, droop=0.5)
        _tube(pen, pts, Hh * 0.35, Hh * 0.1, col, prof=lambda u: 0.55 + 1.0 * math.sin(math.pi * min(1, u * 0.95)) ** 0.8, cap=False)
        e = pts[-1]
        pen.ell(e[0], e[1], 20, 20, light(col, 0.5) if g["pat"] == "socks" else dark(col, 0.85), rim=False)
        return
    if kind in ("fan", "fan_short", "train", "fan_fin"):
        n = 7 if kind != "train" else 11
        ln = {"fan": 190, "fan_short": 120, "train": 330, "fan_fin": 130}[kind] * tl
        base = ang
        for i in range(n):
            u = i / (n - 1) - 0.5
            a = base - 0.1 + u * (0.55 if kind != "train" else 1.0) + (-0.3 if kind == "train" else 0) + 0.04 * math.sin(ph + i)
            L_ = ln * (0.8 + 0.2 * math.cos(u * 3))
            e = (R[0] + math.cos(a) * L_, R[1] + math.sin(a) * L_)
            mid = (R[0] + math.cos(a) * L_ * 0.5, R[1] + math.sin(a) * L_ * 0.5)
            pen.line([R, mid, e], 8 if kind != "train" else 5, mix(col, dk, 0.3 + 0.3 * (i % 2)))
            if kind == "train":
                pen.ell(e[0], e[1], 22, 28, acc, rim=False)
                pen.ell(e[0], e[1], 14, 18, (30, 90, 160), rim=False)
                pen.ell(e[0], e[1], 7, 9, (20, 30, 60), rim=False)
            elif kind == "fan_fin":
                pen.ell(e[0], e[1], 8, 8, mix(col, dk, 0.4), rim=False)
        return
    if kind == "stinger":
        n = 8
        k = tl
        a0 = ang - 0.2
        stab = lunge if strike else 0.0
        P0 = R
        pts = [P0]
        x, y = P0
        a = a0
        for i in range(n):
            a += -0.5 + 0.1 * math.sin(ph * 0.9) + stab * 0.34   # curls up and over the back (and strikes forward)
            seg = 36 * k
            x += math.cos(a) * seg
            y += math.sin(a) * seg
            pts.append((x, y))
        for i in range(n):
            r = 20 - i * 1.4
            pen.ell(pts[i][0], pts[i][1], r, r * 0.9, col)
            pen.line([(pts[i][0] - r * 0.5, pts[i][1] + 2), (pts[i][0] + r * 0.5, pts[i][1] - 2)], 2, dk)
        e = pts[-1]
        pen.ell(e[0], e[1], 15, 15, mix(col, acc, 0.5))
        pen.poly([(e[0] - 7, e[1] - 4), (e[0] + 7, e[1] + 5), (e[0] + math.cos(a) * 46, e[1] + math.sin(a) * 46)], (232, 226, 206), rim=False)
        return
    if kind == "lunate":
        e = (R[0] + back[0] * 40, R[1] + back[1] * 40)
        s = 120 * tl
        sw = 18 * math.sin(ph * 1.6)
        pen.poly([(R[0], R[1] - 8), (e[0] + back[0] * s * 0.5 - 20, e[1] - s * 0.95 + sw), (e[0] + back[0] * s * 0.25, e[1] - s * 0.1),
                  (e[0] + back[0] * s * 0.55 - 20, e[1] + s * 0.65 + sw), (R[0], R[1] + 8)], dark(col, 0.8))
        return
    if kind == "fluke":
        e = (R[0] + back[0] * 45, R[1] + back[1] * 45 + 10 * math.sin(ph * 1.6))
        s = 130 * tl
        pen.poly([(R[0], R[1] - 14), (e[0], e[1] - 14), (e[0] + back[0] * s * 0.7, e[1] - s * 0.28), (e[0] + back[0] * s * 0.9, e[1] - s * 0.1),
                  (e[0] + back[0] * s * 0.35, e[1] + 6), (e[0] + back[0] * s * 0.9, e[1] + s * 0.2), (e[0] + back[0] * s * 0.7, e[1] + s * 0.32),
                  (e[0], e[1] + 14), (R[0], R[1] + 14)], dark(col, 0.85))
        return
    if kind == "curl":
        pts = []
        x, y = R
        a = ang
        for i in range(14):
            a += 0.34 * (0.6 + i * 0.07)
            r = 18 - i * 0.8
            x += math.cos(a) * r * 1.6
            y += math.sin(a) * r * 1.6
            pts.append((x, y))
        _tube(pen, [R] + pts, 18, 5, col)
        return


# ---------------------------------------------------------------- the one drawing function for every creature
GAITS = {
    "trot": dict(bob=8, stride=0.30, lift=0.55, pat="trot", tilt=0.0, speed=1.0),
    "bound": dict(bob=16, stride=0.42, lift=0.6, pat="bound", tilt=0.05, speed=1.15),
    "pace": dict(bob=5, stride=0.26, lift=0.5, pat="pace", tilt=0.015, speed=0.9),
    "lumber": dict(bob=6, stride=0.22, lift=0.35, pat="trot", tilt=0.02, speed=0.75),
    "prowl": dict(bob=4, stride=0.26, lift=0.4, pat="trot", tilt=0.0, speed=0.75),
    "knuckle": dict(bob=10, stride=0.28, lift=0.4, pat="pace", tilt=0.04, speed=0.9),
    "sprawl": dict(bob=2, stride=0.38, lift=0.55, pat="trot", tilt=0.0, speed=0.9, wave=7),
    "scurry": dict(bob=2, stride=0.4, lift=0.7, pat="trot", tilt=0.0, speed=1.5, wave=5),
    "crawl": dict(bob=1, stride=0.3, lift=0.5, pat="trot", tilt=0.0, speed=0.8, wave=8),
    "hop": dict(bob=0, stride=0.25, lift=0.0, pat="hop", tilt=0.0, speed=0.8),
    "waddle": dict(bob=5, stride=0.25, lift=0.5, pat="pace", tilt=0.1, speed=0.9),
    "plod": dict(bob=3, stride=0.22, lift=0.4, pat="pace", tilt=0.04, speed=0.6),
    "strut": dict(bob=5, stride=0.28, lift=0.55, pat="pace", tilt=0.0, speed=1.0),
    "skitter": dict(bob=2, stride=0.32, lift=0.9, pat="tripod", tilt=0.0, speed=1.6),
    "scuttle": dict(bob=2, stride=0.22, lift=0.8, pat="tripod", tilt=0.0, speed=1.7),
    "sway": dict(bob=2, stride=0.22, lift=0.6, pat="trot", tilt=0.04, speed=0.55),
    "slither": dict(bob=0, stride=0.0, lift=0.0, pat="trot", tilt=0.0, speed=1.0, wave=34),
    "swim": dict(bob=12, stride=0.0, lift=0.0, pat="trot", tilt=0.0, speed=1.0, wave=16),
    "glide": dict(bob=10, stride=0.0, lift=0.0, pat="trot", tilt=0.0, speed=1.0, wave=12),
    "pulse": dict(bob=14, stride=0.0, lift=0.0, pat="trot", tilt=0.0, speed=1.0, wave=0),
    "hover": dict(bob=14, stride=0.0, lift=0.0, pat="trot", tilt=0.0, speed=1.0, wave=0),
    "flutter": dict(bob=26, stride=0.0, lift=0.0, pat="trot", tilt=0.0, speed=1.0, wave=0),
    "ripple": dict(bob=2, stride=0.3, lift=0.6, pat="tripod", tilt=0.0, speed=1.0, wave=0),
    "hump": dict(bob=7, stride=0.2, lift=0.2, pat="pace", tilt=0.0, speed=0.8, wave=0),
}
STAND = {"digi": 0.95, "bigdigi": 0.92, "pillar": 0.97, "bird": 0.92, "sprawl": 0.6, "frog": 0.78, "insect": 0.78,
         "arach": 0.8, "crab": 0.8, "webbed": 0.88, "flipper": 0.55, "tentacle": 0.35}
BEND = {"digi": (1, -1), "bigdigi": (1, -1), "pillar": (1, 1), "bird": (1, 1), "sprawl": (-1, -1), "frog": (-1, -1),
        "insect": (-1, -1), "arach": (-1, -1), "crab": (-1, -1), "webbed": (1, 1)}   # (front legs, hind legs): 1 = knee points back


DUTY = {"trot": 0.62, "pace": 0.66, "bound": 0.50, "tripod": 0.52}
FOOT_LOG = []          # filled only by tests: (leg, side, foot_x, foot_y)


def _duty(GA):
    return DUTY.get(GA["pat"], 0.6)


def _planted(p, stride, lift_h, duty):
    """A real step cycle. Landing is at p = pi/2 (as before). While the foot supports the body it is PLANTED: it moves back
    relative to the body at a constant speed (so it stays fixed on the ground); then it swings forward in an arc and lands."""
    f = ((p - math.pi / 2) / (2 * math.pi)) % 1.0
    if f < duty:
        return stride * (1 - 2 * f / duty), -2.0
    u = (f - duty) / (1 - duty)
    e = u * u * (3 - 2 * u)
    return -stride + 2 * stride * e, -2.0 - lift_h * math.sin(math.pi * u)


def _leg_phase(pat, pair, npairs, side):
    if pat == "hop":
        return 0.0
    if pat == "pace":
        return math.pi * side
    if pat == "bound":
        return (0.6 * math.pi if pair < npairs / 2 else 0.0) + 0.12 * math.pi * side
    return math.pi * ((pair + side) % 2)


def _leg_u(style, n):
    if n <= 0:
        return []
    if n == 2:
        return [0.4]
    if n == 4:
        return [0.17, 0.80]
    if n == 6:
        return [0.26, 0.53, 0.78]
    if n == 8 and style != "tentacle":
        return [0.14, 0.36, 0.58, 0.8]
    npair = (n + 1) // 2
    return [0.12 + 0.76 * i / max(1, npair - 1) for i in range(npair)]


def _legs_draw(pen, g, hips, feet, side_far, col, dk, ph, t, moving):
    pass


def draw_chimera(pen, sp, ph, st):
    g = sp["g"]
    t = st.get("t", ph / 6.0)
    moving = st.get("moving", True)
    crouch, roar, headup, lunge = st.get("crouch", 0.0), st.get("roar", 0.0), st.get("headup", 0.0), st.get("lunge", 0.0)
    # full-body actions (0..1): dip = reach down to the ground (feeding), swallow = a swallow travelling down the neck,
    # look = head turning to a sound, land = body compressing after a leap, settle = weight easing back to rest
    dip, swallow, look, land = st.get("dip", 0.0), st.get("swallow", 0.0), st.get("look", 0.0), st.get("land", 0.0)
    anti = st.get("anticipate", 0.0)
    col, acc, belly = [tuple(c) for c in g["pal"]]
    dk, lt = dark(col, 0.66), light(col, 0.2)
    gait = g["gait"]
    GA = GAITS.get(gait, GAITS["trot"])
    prof, L, Hh, pitch0 = g["torso"]
    lstyle, nleg, ll, lw = g["legs"]
    strike = g["strike"]
    sg = g["sigscale"]
    flying_pose = g["hover"] > 40
    phs = ph * GA["speed"]
    seed = _seed(g.get("name", "x"))

    # ---- posture
    bob = 0.0
    tilt = 0.0
    wave = GA.get("wave", 0) if (moving or gait in ("swim", "glide", "pulse", "hover", "flutter", "slither")) else GA.get("wave", 0) * 0.15
    if moving:
        if gait == "hop":
            bob = -ll * 0.4 * max(0.0, math.cos(phs)) if ll else -40 * max(0.0, math.cos(phs))
        elif flying_pose:
            bob = math.sin(t * 2.2) * GA["bob"]
        else:
            bob = -abs(math.sin(phs)) * GA["bob"]
        tilt = GA["tilt"] * math.sin(phs)
    else:
        bob = math.sin(t * 1.6) * (3 if not flying_pose else 9)
    # nose-down as the front of the body reaches for the ground; a small rock back first (anticipation)
    pitch = pitch0 + tilt + 0.05 * roar - 0.12 * lunge - 0.28 * dip + 0.05 * anti
    n = int(_clip(L / 18, 14, 34))

    def spine_at(cy):
        pts = []
        for i in range(n + 1):
            u = i / n
            sx = -L / 2 + u * L
            sy = -g["arch"] * math.sin(math.pi * u) * L * 0.07
            if wave:
                if gait in ("swim", "glide", "slither"):
                    sy += wave * math.sin(phs * 1.5 - u * (5.5 if gait == "slither" else 3.5)) * ((1 - u) ** 0.7 * 0.8 + 0.2 if gait != "slither" else 1.0)
                else:
                    sy += wave * 0.3 * math.sin(phs - u * 5)
            x2 = sx * math.cos(pitch) + sy * math.sin(pitch)
            y2 = -sx * math.sin(pitch) + sy * math.cos(pitch)
            pts.append((x2, cy + y2))
        return pts

    spine = spine_at(0.0)
    tops, bots, nrms = [], [], []
    for i, (x, y) in enumerate(spine):
        a = spine[max(0, i - 1)]
        b = spine[min(n, i + 1)]
        tx, ty = b[0] - a[0], b[1] - a[1]
        m = math.hypot(tx, ty) or 1
        nx, ny = ty / m, -tx / m                      # up
        tt, tb = _prof(prof, i / n)
        nrms.append((nx, ny))
        tops.append((x + nx * tt * Hh / 2, y + ny * tt * Hh / 2))
        bots.append((x - nx * tb * Hh / 2, y - ny * tb * Hh / 2))
    # hips along the belly
    us = _leg_u(lstyle, nleg)
    hip0 = []
    for u in us:
        i = int(round(u * n))
        px, py = spine[i]
        nx, ny = nrms[i]
        tt, tb = _prof(prof, i / n)
        hip0.append((px - nx * tb * Hh / 2 * 0.5, py - ny * tb * Hh / 2 * 0.5))
    stand = ll * STAND.get(lstyle, 0.9) if nleg else 0.0
    if g["hover"] > 40:
        dy = -(g["hover"] + Hh * 0.5)
    elif nleg and hip0 and lstyle not in ("tentacle",):
        dy = -stand - sum(h[1] for h in hip0) / len(hip0)
    elif lstyle == "tentacle":
        dy = -(ll * 0.55 + Hh * 0.3)
    else:
        dy = -Hh * 0.5 * 0.92
    short_neck = 1.0 if g["neck"][0] < 110 else 0.0
    # weight: legs flex and the whole body sinks when it reaches down or lands (a short neck has to sink further)
    dy += bob + crouch * (stand * 0.32 + 12) - 6 * lunge + dip * stand * (0.16 + 0.30 * short_neck) \
        + land * (stand * 0.22 + 10) - anti * 5
    sh = lambda p: (p[0], p[1] + dy)
    spine, tops, bots = [sh(p) for p in spine], [sh(p) for p in tops], [sh(p) for p in bots]
    hips = [sh(h) for h in hip0]
    # the body's footprint (for the shadow) and where its rear and chest are
    R = spine[0]
    F = spine[-1]
    tx, ty = spine[1][0] - spine[0][0], spine[1][1] - spine[0][1]
    mm = math.hypot(tx, ty) or 1
    back = (-tx / mm, -ty / mm)
    fwd = (-back[0], -back[1])

    # ---- shadow
    sw = max(60, L * 0.42)
    if g["hover"] > 40:
        h_ = max(0.0, (-(sum(s_[1] for s_ in spine) / len(spine))) - 0) / 330.0
        a_ = int(max(30, 120 - h_ * 60))
        pen.d.ellipse((pen.X(-sw * 0.8), pen.Y(-6), pen.X(sw * 0.8), pen.Y(12)) if pen.f == 1 else (pen.X(sw * 0.8), pen.Y(-6), pen.X(-sw * 0.8), pen.Y(12)),
                      fill=(6, 8, 12, a_))
    elif not st.get("noshadow"):
        x0, x1 = sorted((pen.X(-sw), pen.X(sw)))
        pen.d.ellipse((x0, pen.Y(-10), x1, pen.Y(14)), fill=(8, 8, 14))

    # ---- legs
    fast = FLAP_SPEED.get(g["donors"].get("wings"), 5.0) > 20
    stride = ll * GA["stride"]
    liftH = ll * GA["lift"] * 0.55
    npairs = len(us)
    leg_draws = []
    for pi_, hip in enumerate(hips):
        for side in (1, 0):            # far leg first
            ph0 = _leg_phase(GA["pat"], pi_, npairs, side)
            p = phs + ph0
            sx_off = 0.0 if side == 0 else 14
            if flying_pose:
                foot = (hip[0] + (10 if side else 0) + 8 * math.sin(t * 2 + pi_), hip[1] + stand * 0.85 if stand else hip[1] + ll * 0.7)
                if fast:
                    foot = (hip[0] + 6, hip[1] + ll * 0.5)
            elif lstyle == "tentacle":
                spread = ((pi_ - (npairs - 1) / 2) * ll * 0.55 + (14 if side else -6))
                foot = (hip[0] + spread, -4)
            elif moving and GA["stride"] > 0:
                if GA["pat"] == "hop":
                    lift = max(0.0, math.cos(p))
                    foot = (hip[0] + stride * 0.3 + 10 * side, -liftH * lift * 1.2 - 2 - 0.0)
                else:
                    fx_, fy_ = _planted(p, stride, liftH, _duty(GA))
                    foot = (hip[0] + fx_ + sx_off * 0.3, fy_)
            else:
                foot = (hip[0] + (12 if side else -4) * (1 if pi_ else -1) * 0.5 + (stride * 0.25 if pi_ >= npairs / 2 else -stride * 0.25) * 0.4, -2)
            if lstyle in ("sprawl", "insect", "arach", "crab") and not flying_pose and lstyle != "tentacle":
                reach = (pi_ - (npairs - 1) / 2) * ll * 0.5
                foot = (foot[0] + reach * (0.8 if lstyle != "sprawl" else 0.4), foot[1])
            if FOOT_LOG is not None and st.get("log_feet"):
                FOOT_LOG.append((pi_, side, foot[0] - hip[0], foot[1]))
            leg_draws.append((side, pi_, hip, foot))
    far = [x for x in leg_draws if x[0] == 1]
    near = [x for x in leg_draws if x[0] == 0]
    skin2 = g["skin2"]

    def draw_leg(side, pi_, hip, foot):
        c = dark(col, 0.64) if side else col
        d_ = dark(c, 0.7)
        front = pi_ >= npairs / 2
        bend = BEND.get(lstyle, (1, 1))[0 if front else 1]
        if lstyle == "tentacle":
            pts = []
            for j in range(11):
                u = j / 10
                x = hip[0] + (foot[0] - hip[0]) * u + (18 if not flying_pose else 12) * math.sin(phs * 1.2 + pi_ * 1.3 + side + u * 5) * u
                y = hip[1] + (foot[1] - hip[1]) * (u ** 0.7 if not flying_pose else u) - (math.sin(math.pi * u) * ll * 0.12 if not flying_pose else 0)
                pts.append((x, min(y, -2) if not flying_pose else y))
            _tube(pen, pts, lw * 1.15, 5, mix(c, acc, 0.12 * (pi_ % 2)))
            for j in range(2, 9, 2):
                pen.dot(pts[j][0], pts[j][1] + lw * 0.2, 3, light(c, 0.4))
            return
        if lstyle == "flipper":
            a = 0.3 + 0.4 * math.sin(phs + side * 2) if moving else 0.35
            tx_, ty_ = hip[0] - math.sin(a) * ll, hip[1] + math.cos(a) * ll * 0.8
            pen.poly([(hip[0] - 10, hip[1] - 6), (hip[0] + 24, hip[1]), (tx_ + 16, ty_), (tx_ - 12, ty_ - 4)], c)
            return
        l1, l2 = ll * 0.52, ll * 0.52
        if lstyle in ("sprawl",):
            l1, l2 = ll * 0.72, ll * 0.8
        elif lstyle in ("insect", "arach", "crab"):
            l1, l2 = ll * 0.75, ll * 0.95
        elif lstyle == "frog":
            l1, l2 = ll * 0.72, ll * 0.72
        elif lstyle in ("bigdigi",):
            l1, l2 = ll * 0.46, ll * 0.5
        w = lw * (0.62 if lstyle in ("insect", "arach", "crab", "bird") else 1.0)
        knee = _ik(hip[0], hip[1], foot[0], foot[1], l1, l2, bend)
        if lstyle in ("pillar",):
            _tube(pen, [hip, knee, foot], w * 1.05, w * 0.88, c)
        elif lstyle in ("digi", "bigdigi", "frog", "webbed"):
            _tube(pen, [hip, knee], w * 1.25, w * 0.85, c)
            _tube(pen, [knee, foot], w * 0.8, w * 0.5, c)
            pen.ell(knee[0], knee[1], w * 0.5, w * 0.5, c, rim=False)
        elif lstyle in ("insect", "arach", "crab"):
            _tube(pen, [hip, knee], w, w * 0.7, c)
            _tube(pen, [knee, foot], w * 0.7, w * 0.35, d_)
            pen.ell(knee[0], knee[1], w * 0.55, w * 0.55, c, rim=False)
        else:
            _tube(pen, [hip, knee], w * 1.0, w * 0.75, c)
            _tube(pen, [knee, foot], w * 0.75, w * 0.5, c)
        if skin2 == "fur" and lstyle in ("digi", "pillar", "bigdigi"):
            pen.ell(hip[0], hip[1] + 4, w * 0.9, w * 0.7, c, rim=False)
        _foot(pen, foot[0], foot[1], g["foot"], lw * 0.55, c, d_)

    # ---- arms (they can strike)
    arms = g["arms"]

    def draw_arm(far_side):
        if not arms:
            return
        kind_, al = arms
        al *= sg.get("arms", 1.0)
        c = dark(col, 0.64) if far_side else col
        i = int(0.82 * n)
        base = (spine[i][0] + (6 if far_side else 0), spine[i][1] + Hh * 0.1)
        sw_ = math.sin(phs + (math.pi if far_side else 0)) if moving else 0.0
        if kind_ == "ape":
            tipx = base[0] + 40 + 30 * sw_ + 40 * lunge
            tip = (tipx, -6 - max(0, math.cos(phs + (math.pi if far_side else 0))) * 24 * (1 if moving else 0))
            knee = _ik(base[0], base[1], tip[0], tip[1], al * 0.5, al * 0.5, 1)
            _tube(pen, [base, knee], lw * 1.1, lw * 0.95, c)
            _tube(pen, [knee, tip], lw * 0.95, lw * 0.75, c)
            pen.ell(tip[0] + 6, tip[1], lw * 0.7, lw * 0.5, dark(c, 0.8), rim=False)
        elif kind_ == "pincers":
            a = -0.55 + 0.25 * math.sin(t * 2.2 + (1.5 if far_side else 0)) + 0.5 * lunge
            reach = al * (1 + 0.2 * lunge)
            e1 = (base[0] + math.cos(a + 0.5) * reach * 0.55, base[1] + math.sin(a + 0.5) * reach * 0.55 - 10)
            e2 = (e1[0] + math.cos(a - 0.25) * reach * 0.5, e1[1] + math.sin(a - 0.25) * reach * 0.5)
            wd = max(12, Hh * 0.2)
            _tube(pen, [base, e1], wd * 0.8, wd * 0.7, dark(c, 0.8))
            _tube(pen, [e1, e2], wd * 0.7, wd * 0.85, c)
            op = 0.35 + 0.3 * math.sin(t * 3 + far_side) + 0.4 * lunge
            for sgn, cc in ((-1, c), (1, dark(c, 0.8))):
                aa = a - 0.25 + sgn * op * 0.5
                tipp = (e2[0] + math.cos(aa) * reach * 0.42, e2[1] + math.sin(aa) * reach * 0.42)
                pen.poly([(e2[0] - 4, e2[1] - sgn * wd * 0.45), (e2[0] + 4, e2[1] + sgn * wd * 0.45), tipp], mix(cc, acc, 0.25))
        elif kind_ == "raptor":
            a1 = -1.0 + 0.2 * math.sin(t * 1.4) + 0.9 * lunge
            e1 = (base[0] + math.cos(a1) * al * 0.5, base[1] + math.sin(a1) * al * 0.5 - 10)
            a2 = a1 + 2.2 - 1.8 * lunge
            e2 = (e1[0] + math.cos(a2) * al * 0.55, e1[1] + math.sin(a2) * al * 0.55)
            _tube(pen, [base, e1], 16, 12, c)
            _tube(pen, [e1, e2], 12, 6, dark(c, 0.8))
            for j in range(4):
                u = (j + 1) / 5
                px, py = e1[0] + (e2[0] - e1[0]) * u, e1[1] + (e2[1] - e1[1]) * u
                pen.poly([(px - 3, py - 3), (px + 3, py - 3), (px + 2, py + 12)], (228, 222, 200), rim=False)

    # ---- wings
    wk = g["wings"]
    wing_flap = 0.0
    if wk:
        fs = FLAP_SPEED.get(g["donors"].get("wings"), 4.5)
        if g["wingfly"] and g["hover"] > 40:
            wing_flap = math.sin(t * fs)
        elif g["wingfly"]:
            wing_flap = 0.1 + 0.18 * math.sin(t * 2.4) - (0.2 if not moving else 0.0)
        else:
            wing_flap = math.sin(phs) * 0.4 if moving else 0.2
        if wk[0] == "bird" and not g["wingfly"]:
            wing_flap = -0.3
    wi = int(0.58 * n)
    wsz = wk[1] * sg.get("wings", 1.0) if wk else 0
    wroot = (spine[wi][0], (tops[wi][1] * 0.7 + spine[wi][1] * 0.3)) if wk else None
    if wk and wk[0] in ("fin", "flipper"):
        wroot = (bots[wi][0] * 0.55 + spine[wi][0] * 0.45, bots[wi][1] * 0.6 + spine[wi][1] * 0.4)
    if wk and wk[0] == "flipper":
        wroot = (spine[wi][0] + 4, spine[wi][1] - Hh * 0.05)

    # ---- draw everything in order
    for leg in far:
        draw_leg(*leg)
    draw_arm(True)
    if wk and wk[0] not in ("fin", "flipper", "disc"):
        _wing(pen, wk[0], wsz * 0.92, wroot[0] - 14, wroot[1] + 4, wing_flap * 0.85 - 0.1, col, acc, dk, far=True, pat=g["pat"], t=t, fast=fast)
    # tail
    stab = strike in ("stab",)
    _tail(pen, g, R, back, Hh, phs - 0.9 - 1.2 * land, t, col, acc, dk, lt, lunge=lunge, strike=stab)   # follows with a delay
    # neck behind the torso for long necks would look odd: neck is drawn after the body
    # the torso
    torso_poly = tops + bots[::-1]
    pen.poly(torso_poly, col)
    if wk and wk[0] == "disc":
        wsz2 = wsz
        up = [(spine[i][0], spine[i][1] - Hh * 0.45 - 12 * math.sin(phs * 1.4 - i * 0.5)) for i in range(0, n + 1)]
        dn = [(spine[i][0], spine[i][1] + Hh * 0.45 + 12 * math.sin(phs * 1.4 - i * 0.5 + 1.5)) for i in range(n, -1, -1)]
        pen.poly(up + dn, mix(col, dk, 0.2))
    # belly
    belly_pts = [((spine[i][0] * 0.82 + bots[i][0] * 0.18), (spine[i][1] * 0.82 + bots[i][1] * 0.18)) for i in range(2, n - 1)]
    if belly_pts:
        pen.poly(belly_pts + [bots[i] for i in range(n - 2, 1, -1)], mix(belly, col, 0.12), rim=False)
    _body_marks(pen, g, spine, tops, bots, col, dk, lt, seed, scale=max(0.7, Hh / 150))
    if g.get("ribs") or g.get("cracks"):
        _horror_marks(pen, g, spine, tops, bots, seed)
    if g["skin"] == "fur":
        _fur_tufts(pen, tops, bots, col, scale=max(0.7, Hh / 160))
    # dorsal features
    for dname in g["dorsal"]:
        k = sg.get(dname, 1.0)
        if dname == "hump":
            i = int(0.42 * n)
            pen.ell(tops[i][0], tops[i][1] + 10, L * 0.15 * k, Hh * 0.32 * k, col)
        elif dname in ("fin_big", "fin_small"):
            i = int(0.5 * n)
            hh = Hh * (0.95 if dname == "fin_big" else 0.45) * k
            pen.poly([(tops[i - 2][0], tops[i - 2][1] + 6), (tops[i + 1][0] - 4, tops[i + 1][1] + 6), (tops[i - 1][0] - hh * 0.25, tops[i - 1][1] - hh)], dark(col, 0.78))
        elif dname in ("osteoderm", "ridge"):
            for i in range(2, n - 1, 1 if dname == "osteoderm" else 2):
                h_ = (14 if dname == "osteoderm" else 10) * k
                pen.poly([(tops[i][0] - 7, tops[i][1] + 2), (tops[i][0] + 7, tops[i][1] + 2), (tops[i][0], tops[i][1] - h_)], dark(col, 0.7) if dname == "osteoderm" else lt, rim=dname == "osteoderm")
        elif dname == "quills":
            r_ = random.Random(seed)
            for i in range(1, n):
                for _ in range(2):
                    a = -1.2 + r_.random() * 1.6
                    ln_ = 40 + r_.random() * 30
                    x, y = tops[i][0] + 3, tops[i][1] + 10
                    pen.line([(x, y), (x - math.cos(a) * ln_ * 0.9 + 14, y - math.sin(-a) * ln_ * 0.0 - ln_ * 0.8)], 4, belly)
    # shell on a body that is not already a dome
    if g["shell"] and prof not in ("dome", "flatdome"):
        sc2 = mix(col, acc, 0.35)
        sc2 = mix(sc2, (40, 36, 28), 0.3)
        pts = []
        for i in range(2, n - 1):
            pts.append((tops[i][0], tops[i][1] - Hh * 0.28 * math.sin(math.pi * i / n) ** 0.7))
        base = [tops[i] for i in range(n - 2, 1, -1)]
        pen.poly(pts + base, sc2)
        if g["shell"] == "banded":
            for i in range(3, n - 2, 2):
                pen.line([(pts[i - 2][0], pts[i - 2][1]), (tops[i][0], tops[i][1] + Hh * 0.18)], 4, dark(sc2, 0.6))
        else:
            for i in range(3, n - 3, 3):
                x, y = pts[i - 2]
                pen.poly([(x - 14, y + 12), (x, y + 2), (x + 14, y + 12), (x + 10, y + 26), (x - 10, y + 26)], dark(sc2, 0.75), rim=False)
        if g["shell"] == "carapace":
            for i in range(2, n - 2, 2):
                pen.poly([(tops[i][0] - 5, tops[i][1] + 3), (tops[i][0] + 5, tops[i][1] + 3), (tops[i][0], tops[i][1] - 12)], dark(sc2, 0.7), rim=False)
    elif prof in ("dome", "flatdome") and g["shell"]:
        sc2 = dark(mix(col, acc, 0.25), 0.8)
        for i in range(3, n - 2, 3):
            x, y = tops[i][0], tops[i][1] + Hh * 0.2
            pen.poly([(x - 18, y), (x, y - 14), (x + 18, y), (x + 14, y + 22), (x - 14, y + 22)], sc2, rim=False)
    for leg in near:
        draw_leg(*leg)
    draw_arm(False)
    # ---- neck and head
    hd = g["head"]
    rx, ry = hd["skull"]
    has_head = rx > 0
    nk_len, nk_up = g["neck"]
    head_pos = F
    if has_head:
        ang = _clip(nk_up + max(pitch, 0) * 0.6, -0.3, 1.45) - 0.35 * crouch + headup * 0.35 + (-0.18 * lunge if g["neck"][0] > 100 else 0) \
            + 0.3 * look - 0.1 * land
        L_n = nk_len * (1 + 0.35 * lunge) * (1.0 if not lunge else 1.0) + (60 * lunge if strike in ("snap", "pounce", "tongue", "claw", "dive") else 0)
        bx_, by_ = F[0] - fwd[0] * rx * 0.2, F[1] - fwd[1] * rx * 0.2
        Lh_ = L_n + rx * 0.55
        if dip > 0.001:
            # reach: the neck swings down until the mouth is at the ground (as far as its length allows)
            want = _clip((by_ + ry * 0.55) / max(Lh_, 1.0), -1.0, 1.0)
            e_ = dip * dip * (3 - 2 * dip)
            ang = ang * (1 - e_) + math.asin(want) * e_
        hxp = bx_ + math.cos(ang) * Lh_
        hyp = by_ - math.sin(ang) * Lh_ + (6 * math.sin(phs * 2) if moving and gait in ("strut", "waddle") else 0)
        if moving and not flying_pose:
            hyp -= bob * 0.55   # the head rides steadier than the torso: it counter-balances each step
        if nk_len > 4:
            bow = g.get("neck_bow", 0.12) * (1 + 1.6 * dip)
            pts = []
            for j in range(9):
                u = j / 8
                x = bx_ + (hxp - bx_) * u - bow * nk_len * math.sin(math.pi * u) * 0.9
                y = by_ + (hyp - by_) * u - bow * nk_len * math.sin(math.pi * u) * 0.35
                pts.append((x, y))
            wN = max(ry * 1.0, Hh * 0.38) if nk_len < 100 else max(ry * 0.8, 34)
            _tube(pen, pts, wN * (1.2 if nk_len < 100 else 1.5), wN * 0.8, col)
            if swallow > 0.001:   # a lump travels from the mouth down the throat
                j_ = min(7, int(swallow * 8))
                pen.ell(pts[j_][0], pts[j_][1], wN * 0.95, wN * 0.8, light(col, 0.18), rim=False)
            if "mane" in hd["extras"]:
                for j in range(1, 8):
                    pen.ell(pts[j][0] - 12, pts[j][1] + 6, 30, 26, mix(mix(acc, (60, 30, 12), 0.35), col, 0.1 * (j % 3)), rim=False)
        else:
            pen.ell(F[0] - 6, F[1] + 4, ry * 0.9, ry * 0.9, col, rim=False)
        rot = -0.25 * roar + hd["snout"][4] * 0.5 + (0.12 if nk_len > 150 else 0.0) - 0.3 * headup * 0 + (0.18 if lunge else 0) \
            + 0.85 * dip - 0.2 * look
        if pitch > 0.6:
            rot += 0.1
        rot -= 0.5 * max(0.0, pitch - 0.3) * 0.3
        head_pos = _head(pen, hxp, hyp, rot, g, roar, t, st)
        sp["_head"] = (hxp, hyp)
    else:
        # the face sits on the body itself (octopus, crab, ray, anglerfish, jellyfish)
        i = int(0.8 * n)
        hxp, hyp = tops[i][0], tops[i][1] + Hh * 0.28
        fake = _deep(g)
        fake["head"] = _deep(hd)
        fake["head"]["skull"] = (Hh * 0.28 if g["head"]["eyes"] != "none" else 1.0, Hh * 0.26)
        if hd["extras"] and "bigmouth" in hd["extras"]:
            kx = hd
            o = roar * 0.6 + 0.12
            x0 = hxp
            pen.poly([(x0 - 30, hyp + 10), (x0 + 70, hyp + 20), (x0 + 130, hyp + 20 + 40 * o), (x0 + 50, hyp + 70 + 50 * o)], (110, 16, 26), rim=False)
            for j in range(9):
                xx = x0 + j * 15
                pen.poly([(xx, hyp + 14), (xx + 7, hyp + 14), (xx + 3, hyp + 14 + 34)], (244, 240, 226), rim=False)
                pen.poly([(xx, hyp + 60 + 40 * o), (xx + 7, hyp + 60 + 40 * o), (xx + 3, hyp + 60 + 40 * o - 30)], (244, 240, 226), rim=False)
        if g["head"]["eyes"] != "none":
            fake["head"]["snout"] = ("none", 0, 0, 0, 0)
            fake["head"]["ears"] = ("none", 0)
            fake["head"]["teeth"] = "none"
            fake["head"]["extras"] = [e for e in hd["extras"] if e in ("lure",)]
            fake["pat"] = "plain"
            _eye_body(pen, g, hxp, hyp - Hh * 0.18, t)
        sp["_head"] = (hxp + 30, hyp)
    # tongue strike
    if strike == "tongue" and lunge > 0.05 and has_head:
        hx0, hy0 = sp["_head"]
        tl_ = 330 * lunge
        pen.line([(hx0 + rx * 0.9, hy0 + ry * 0.3), (hx0 + rx * 0.9 + tl_, hy0 + ry * 0.2 + 10 * math.sin(t * 20))], 12, (214, 82, 104))
        pen.dot(hx0 + rx * 0.9 + tl_, hy0 + ry * 0.2, 14, (230, 100, 124))
    # near wing last, so it covers the shoulder
    if wk and wk[0] not in ("disc",):
        _wing(pen, wk[0], wsz, wroot[0], wroot[1], wing_flap, col, acc, dk, far=False, pat=g["pat"], t=t, fast=fast)
    # the strike with a flyer: the wings throw wide
    sp["_boxtop"] = min(p[1] for p in tops) - (wsz * 0.9 if wk and g["wingfly"] else 0)


def _eye_body(pen, g, x, y, t):
    hd = g["head"]
    ec = tuple(hd["eyecol"])
    kind = hd["eyes"]
    if kind == "slitbulge":
        pen.ell(x + 8, y, 30, 30, mix(g["pal"][0], g["pal"][2], 0.25))
        pen.ell(x + 8, y, 20, 20, ec, rim=False)
        pen.ell(x + 8, y, 18, 5, (8, 6, 6), rim=False)
        pen.ell(x + 16, y - 6, 4, 4, (255, 255, 255), rim=False)
    elif kind == "stalk":
        for off in (0, 30):
            pen.line([(x + off, y + 10), (x + off, y - 40)], 9, dark(g["pal"][0], 0.7))
            pen.ell(x + off, y - 46, 12, 12, (14, 10, 12))
            pen.ell(x + off + 3, y - 49, 3, 3, (255, 255, 255), rim=False)
    elif kind == "toplook":
        pen.ell(x + 20, y - 12, 9, 7, (14, 10, 12))
    elif kind == "round":
        pen.ell(x + 20, y, 14, 14, (244, 240, 230))
        pen.ell(x + 24, y, 9, 9, ec, rim=False)
        pen.ell(x + 26, y, 4, 4, (10, 8, 8), rim=False)
    elif kind == "compound":
        pen.ell(x + 10, y, 20, 24, mix(ec, (10, 40, 50), 0.35))
    if "lure" in hd["extras"]:
        k = g["sigscale"].get("lure", 1.0)
        pts = [(x - 20, y - 70), (x + 50, y - 140 * k), (x + 120 + 8 * math.sin(t * 2), y - 120 * k)]
        pen.line(pts, 4, dark(g["pal"][0], 0.5))
        gl = 0.7 + 0.3 * math.sin(t * 5)
        for r_, a_ in ((28, 50), (17, 110), (9, 255)):
            x0_, x1_ = sorted((pen.X(pts[-1][0] - r_), pen.X(pts[-1][0] + r_)))
            pen.d.ellipse((x0_, pen.Y(pts[-1][1] - r_), x1_, pen.Y(pts[-1][1] + r_)), fill=(120, 255, 220, int(a_ * gl)))


KINDS["chimera"] = draw_chimera
KIND_SCALE["chimera"] = 1.0
PRINT_OF["chimera"] = "claw"


# ---------------------------------------------------------------- glue: designs become creatures the scenes can draw
def _measure(g):
    sp = {"name": g.get("name", "x"), "kind": "chimera", "g": g, "color": g["pal"][0], "traits": set(), "scale": 1.0,
          "hybrid": False}
    W_, H_ = 2600, 2200
    im = Image.new("RGBA", (W_, H_), (0, 0, 0, 0))
    d = ImageDraw.Draw(im, "RGBA")
    ox, oy = 1300, 1900
    pen = Pen(d, ox, oy, 1.0, 1, rimfn)
    draw_chimera(pen, sp, 0.7, {"t": 0.4, "moving": False, "noshadow": True})
    bb = im.getchannel("A").point(lambda v: 255 if v > 40 else 0).getbbox() or (ox - 200, oy - 300, ox + 200, oy)
    hx, hy = sp.get("_head", (150, -250))
    return {"bbox": (bb[0] - ox, bb[1] - oy, bb[2] - ox, bb[3] - oy), "head": (hx, hy)}


def chimera_spec(g):
    """A design (a dict) -> the creature record the scene code draws."""
    g = _finish(_tuplify(g))
    m = _measure(g)
    x0, y0, x1, y1 = m["bbox"]
    w, h = max(x1 - x0, 1), max(-y0, 1)
    tw = 700 if g["hybrid"] else 470      # hybrids dominate the frame
    k = _clip(min(tw / w, 860 / h), 0.5, 1.9)
    ec = tuple(g["head"]["eyecol"]) if g["glow"] or g["hybrid"] else (255, 190, 40)
    if g["glow"]:
        ec = tuple(g["head"]["eyecol"])
    return {"name": g.get("name", "x"), "kind": "chimera", "g": g, "color": tuple(g["pal"][0]), "traits": set(),
            "scale": k, "hybrid": bool(g["hybrid"]), "aura": False, "eye": ec, "head0": m["head"],
            "bbox": m["bbox"]}


def pure_spec(name):
    """The real animal (a parent), drawn by the same engine so the viewer can recognise what it was made from."""
    if name not in SPECIES:
        return animal_spec(name)
    return chimera_spec(pure_design(name))


def hybrid_spec_from(g):
    return chimera_spec(g)


def designs_to_json(g):
    return json.loads(json.dumps(g, default=list))


# ================================================================ full-body actions for the hybrids
def chim_ground(sp):
    """The creature's natural walking speed across the ground (px/s): heavy, long-legged animals are slow and sweeping,
    small light ones quick and busy. The scenery scrolls at exactly this speed."""
    g = sp["g"]
    GA = GAITS.get(g["gait"], GAITS["trot"])
    lstyle, nleg, ll, lw = g["legs"]
    if g["hover"] > 40 or GA["stride"] <= 0 or not nleg or GA["pat"] == "hop":
        return 260.0
    s = 1.4 * sp.get("scale", 1.0) * KIND_SCALE.get("chimera", 1.0)
    stride_px = max(ll * GA["stride"] * s, 20.0)
    heavy = _clip((g["torso"][1] * g["torso"][2]) / 40000.0, 0.4, 2.0)      # a rough mass
    f = _clip(2.2 - 0.9 * heavy - ll / 400.0, 0.7, 2.2) * (0.6 + 0.6 * GA["speed"])   # strides per second
    return _clip(4.0 * stride_px * f, 140.0, 400.0)


def chim_rate(sp, ground=None):
    """Step rate (engine phase units) at which the planted foot travels exactly as fast as the ground moves."""
    g = sp["g"]
    GA = GAITS.get(g["gait"], GAITS["trot"])
    lstyle, nleg, ll, lw = g["legs"]
    if g["hover"] > 40 or GA["stride"] <= 0 or not nleg or GA["pat"] == "hop":
        return None
    ground = ground or chim_ground(sp)
    s = 1.4 * sp.get("scale", 1.0) * KIND_SCALE.get("chimera", 1.0)
    stride_px = max(ll * GA["stride"] * s, 20.0)
    dpdt = ground * math.pi * _duty(GA) / stride_px      # the planted foot moves back at exactly the ground's speed: stride*2 over duty*cycle
    return _clip(dpdt / GA["speed"], 2.0, 15.0)


def _ease(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def _speed_profile(segments, fps=None):
    """segments: [(t_end, speed_multiplier), ...] -> (P(t), m(t)): distance travelled and current speed, smoothly blended."""
    fps = fps or FPS
    ts, ms = [0.0], [segments[0][1]]
    def m_at(t):
        prev_t, prev_m = 0.0, segments[0][1]
        for te, mv in segments:
            if t <= te:
                k = _ease((t - (te - 0.45)) / 0.45) if te - prev_t > 0.45 and False else 0.0
                break
            prev_t, prev_m = te, mv
        # blend toward the next segment's speed over 0.45 s around each boundary
        out = segments[-1][1]
        for i, (te, mv) in enumerate(segments):
            if t <= te:
                out = mv
                if i + 1 < len(segments):
                    k = _ease((t - (te - 0.4)) / 0.4)
                    out = mv + (segments[i + 1][1] - mv) * k
                break
        return out
    n = int(segments[-1][0] * fps) + 3
    P, acc = [0.0], 0.0
    for i in range(1, n):
        acc += m_at(i / fps) / fps
        P.append(acc)
    def Pf(t):
        i = min(int(t * fps), n - 1)
        return P[i]
    return Pf, m_at


def _food(d, x, gy, size, col, facing=1):
    """Something lying on the ground to be eaten: a plain, un-gory shape that shrinks as it is eaten."""
    if size <= 0.02:
        return
    w, h = 120 * size, 34 * size
    body = tuple(int(c * 0.8) for c in col)
    d.ellipse((x - w, gy - h * 1.8, x + w, gy + 4), fill=body, outline=light(body, 0.3), width=3)
    d.polygon([(x - facing * w * 0.9, gy - h * 0.9), (x - facing * w * 1.5, gy - h * 1.6), (x - facing * w * 1.4, gy - h * 0.2)], fill=body)
    d.ellipse((x + facing * w * 0.55, gy - h * 1.35, x + facing * w * 0.8, gy - h * 1.0), fill=light(body, 0.35))


def _dust(d, x, y, k, seed=1, size=1.0, up=1.0):
    """Dust thrown up where something heavy lands or a mouth hits the ground; it rises, spreads and fades."""
    if k <= 0 or k >= 1:
        return
    rnd = random.Random(seed)
    for i in range(9):
        a = rnd.uniform(-0.9, 0.9)
        dx = (rnd.uniform(-1, 1) * 60 + a * 90 * k) * size
        dy = -(18 + 70 * k * up * rnd.uniform(0.5, 1.1)) * size
        r = (10 + 28 * k) * size * rnd.uniform(0.7, 1.2)
        al = int(120 * (1 - k) ** 1.4)
        d.ellipse((x + dx - r, y + dy - r * 0.8, x + dx + r, y + dy + r * 0.8), fill=(150, 132, 108, al))


def _head_reach(sp):
    """How far in front of its feet the mouth lands when the creature reaches down (measured, not guessed)."""
    if "_reach" in sp:
        return sp["_reach"]
    W_, H_ = 2600, 2200
    im = Image.new("RGBA", (W_, H_), (0, 0, 0, 0))
    dd = ImageDraw.Draw(im, "RGBA")
    pen = Pen(dd, 1300, 1900, 1.0, 1, rimfn)
    draw_chimera(pen, sp, 0.7, {"t": 0.4, "moving": False, "noshadow": True, "dip": 1.0})
    hx, hy = sp.get("_head", (200, -40))
    sp["_reach"] = (hx + sp["g"]["head"]["snout"][1] * 0.8, hy)
    return sp["_reach"]


def _feed_timeline(u, shot0=0.0):
    """u in [0,1] -> the state of a feeding sequence (reach, bites, swallow, rise)."""
    st = dict(dip=0.0, swallow=0.0, look=0.0, anticipate=0.0, roar=0.0, bites=0, headup=0.0)
    if u < 0.14:                                   # notice: head comes up and turns
        k = _ease(u / 0.14)
        st["look"], st["headup"] = k, 0.5 * k
    elif u < 0.24:                                 # anticipation: weight rocks back
        st["look"], st["headup"] = 1.0 - _ease((u - 0.14) / 0.1) * 0.6, 0.3
        st["anticipate"] = _ease((u - 0.14) / 0.1)
    elif u < 0.42:                                 # reach: neck, shoulders and body all go down together
        st["dip"] = _ease((u - 0.24) / 0.18)
        st["anticipate"] = 1.0 - _ease((u - 0.24) / 0.06)
    elif u < 0.78:                                 # three bites: jaw works, head lifts a little between them
        k = (u - 0.42) / 0.36
        c = math.sin(k * 3 * math.pi)
        st["dip"] = 0.88 + 0.12 * abs(c) ** 0.5 * (1 if c > 0 else 0.6)
        st["dip"] = 0.82 + 0.18 * (0.5 + 0.5 * math.cos(k * 3 * 2 * math.pi))
        st["roar"] = 0.1 + 0.3 * (0.5 + 0.5 * math.sin(k * 3 * 2 * math.pi + 1.0)) ** 2
        st["bites"] = min(3, int(k * 3) + 1)
    elif u < 0.9:                                  # lift and swallow
        k = _ease((u - 0.78) / 0.12)
        st["dip"] = 0.9 - 0.7 * k
        st["swallow"] = (u - 0.78) / 0.12
        st["bites"] = 3
    else:                                          # settle back to rest
        k = _ease((u - 0.9) / 0.1)
        st["dip"] = 0.2 * (1 - k)
        st["bites"] = 3
    return st


def feed_scene(out, dur, world, sp, facing=1, zoom=(0.95, 1.0), cwd=None, food_col=(120, 100, 90)):
    """Walk in, stop, notice, reach down with the whole body, take three bites, swallow, straighten up."""
    G = chim_ground(sp)
    t_stop = dur * 0.30
    Pf, m_at = _speed_profile([(t_stop, 1.0), (dur + 1.0, 0.0)])
    cx = 380 if facing == 1 else 700
    rx, ry = _head_reach(sp)
    s0 = 1.4 * sp.get("scale", 1.0) * KIND_SCALE.get("chimera", 1.0)

    def paint(im, d, t, u, cam):
        z = zoom[0] + (zoom[1] - zoom[0]) * u
        m = m_at(t)
        s_ = s0 * z
        mouth_x = cx + facing * rx * s_
        food_x = mouth_x + facing * 30 * s_
        # the food is part of the ground: it arrives with the scenery and the creature stops right at it
        wx = food_x + facing * G * (Pf(dur + 0.9) - Pf(t)) * 1.0
        ft = _feed_timeline(max(0.0, (t - t_stop - 0.35) / max(dur - t_stop - 0.45, 0.5)))
        size = 1.0 - 0.28 * ft["bites"] if ft["bites"] else 1.0
        if ft["bites"] == 3 and ft["swallow"] > 0:
            size = 0.04
        _food(d, wx, GROUND + 18, size, food_col, facing)
        st = {"t": t, "moving": m > 0.12, "ground": G, "headup": ft["headup"], "roar": ft["roar"], "dip": ft["dip"],
              "swallow": ft["swallow"], "look": ft["look"], "anticipate": ft["anticipate"]}
        draw_creature(d, sp, cx, GROUND + 20, Pf(t) * STEP * 0.5, facing, z, st)
        if ft["dip"] > 0.85 and ft["roar"] > 0.3:       # the mouth meets the ground: a little dust
            _dust(d, mouth_x, GROUND + 14, (t * 5) % 1.0, seed=int(t * 5), size=0.5 * z)

    steps = _contacts(sp, Pf, dur, lambda t: m_at(t) > 0.18)
    write_video(out, dur, paint, world, cwd, cam_dir=facing, cam_fn=lambda t: facing * G * Pf(t))
    return {"stop_at": t_stop, "bite_at": t_stop + 0.35 + (dur - t_stop - 0.45) * 0.42, "steps": steps}


def turn_scene(out, dur, world, sp, cwd=None, zoom=(0.95, 1.0)):
    """Walks right, hears something, the head swings round first, then shoulders and hips, and it walks back the other way.
    The tail follows a beat late and the body settles with weight when the turn is done."""
    G = chim_ground(sp)
    t_stop, t_turn, t_done = dur * 0.30, dur * 0.50, dur * 0.66
    Pf, m_at = _speed_profile([(t_stop, 1.0), (t_done, 0.0), (dur + 1.0, 1.0)])
    cx = 540
    s0 = 1.4 * sp.get("scale", 1.0) * KIND_SCALE.get("chimera", 1.0)

    def paint(im, d, t, u, cam):
        z = zoom[0] + (zoom[1] - zoom[0]) * u
        m = m_at(t)
        facing = 1 if t < (t_turn + t_done) / 2 else -1
        if t < t_stop:
            look = 0.0
        elif t < t_turn:
            look = _ease((t - t_stop) / (t_turn - t_stop))        # head goes first
        else:
            look = 1.0 - _ease((t - t_done) / 0.5) if t > t_done else 1.0
        k = _ease((t - t_turn) / (t_done - t_turn))                 # the body rotates through the middle
        land = 0.0
        if t > t_done:
            land = max(0.0, 1.0 - (t - t_done) / 0.45) * 0.8       # weight comes down after the turn
        st = {"t": t, "moving": m > 0.12, "ground": G, "look": look, "headup": 0.25 * look, "land": land}
        ph = Pf(t) * STEP * 0.5
        if 0.02 < k < 0.98:
            # yaw the whole body: head side crosses the middle first, tail side lags, so the turn reads as a twist
            L_ = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            dd = ImageDraw.Draw(L_, "RGBA")
            draw_creature(dd, sp, cx, GROUND + 20, ph, 1, z, st)
            box = (int(cx - 520), int(GROUND - 1000), int(cx + 520), int(GROUND + 120))
            crop = L_.crop(box)
            w0, h0 = crop.size
            ang = k * math.pi
            out_ = Image.new("RGBA", crop.size, (0, 0, 0, 0))
            cols = max(40, w0 // 12)
            def fmap(x):                       # where the picture column x lands: head end turns first, tail end lags
                rel = x / w0
                a_ = _clip(ang * (0.65 + 0.7 * rel), 0.0, math.pi)
                return w0 / 2 + (x - w0 / 2) * max(math.cos(a_), -1.0) if abs(math.cos(a_)) > 0.12 else \
                    w0 / 2 + (x - w0 / 2) * (0.12 if math.cos(a_) >= 0 else -0.12)
            for c in range(cols):
                x0, x1 = int(c * w0 / cols), int((c + 1) * w0 / cols)
                if x1 <= x0:
                    continue
                f0, f1 = fmap(x0), fmap(x1)
                flip = f1 < f0
                lo, hi = (f1, f0) if flip else (f0, f1)
                ww = max(1, int(round(hi - lo)) + 1)
                strip = crop.crop((x0, 0, x1, h0)).resize((ww, h0), Image.BILINEAR)
                if flip:
                    strip = strip.transpose(Image.FLIP_LEFT_RIGHT)
                out_.alpha_composite(strip, (int(max(0, min(w0 - ww, round(lo)))), 0))
            im.paste(out_, (box[0], box[1]), out_)
        else:
            draw_creature(d, sp, cx, GROUND + 20, ph, facing, z, st)

    steps = _contacts(sp, Pf, dur, lambda t: m_at(t) > 0.18)
    write_video(out, dur, paint, world, cwd, cam_dir=1,
                cam_fn=lambda t: G * Pf(t) * (1 if t < (t_turn + t_done) / 2 else -1) + (0 if t < (t_turn + t_done) / 2 else 2 * G * Pf((t_turn + t_done) / 2)))
    return {"turn_at": t_turn, "steps": steps}


def pounce_scene(out, dur, world, sp, prey_text="", cwd=None):
    """A real hunt, done with bodies: stalk, the prey notices and bolts, the hybrid sprints, crouches, leaps, lands on it
    (dust, body compressing), then feeds. No flashes, no slash marks, no bursts."""
    G = chim_ground(sp)
    prey, pscale = prey_spec(prey_text)
    pcol = tuple(prey["color"])[:3]
    t1, t2, t3, t4 = dur * 0.22, dur * 0.36, dur * 0.60, dur * 0.70   # stalk end, notice end, leap start, landing
    Pf, m_at = _speed_profile([(t1, 0.55), (t2, 0.2), (t3, 2.1), (t4, 0.0), (dur + 1.0, 0.0)])
    s0 = 1.4 * sp.get("scale", 1.0) * KIND_SCALE.get("chimera", 1.0)
    reach_x, _ = _head_reach(sp)
    front = max(sp["bbox"][2], 100.0)                       # how far ahead of its middle the creature reaches
    back_ = max(-sp["bbox"][0], 100.0)
    # shrink the creature until the chase fits: tail end on screen, prey well ahead of the nose, room to run
    zc = _clip((1080 - 150 - 330) / ((front + back_) * s0 + 1.0), 0.5, 1.0)
    cx = int(90 + back_ * s0 * zc)

    def paint(im, d, t, u, cam):
        m = m_at(t)
        z = zc
        s_ = s0 * z
        # prey: grazing, startles (freezes, head up), then bolts away to the right until it is caught
        px0 = cx + front * s_ + 300
        flee = min(190, 1010 - px0)
        if t < t2:
            px = px0
        elif t < t3:
            px = px0 + flee * _ease((t - t2) / (t3 - t2)) ** 1.4
        else:
            px = px0 + flee
        land_x = px0 + flee - reach_x * s_ + 30
        # the hybrid closes the distance with its own speed: x grows with the sprint, then the leap covers the rest
        hx = cx
        if t >= t2:
            hx = cx + (land_x - cx) * 0.55 * _ease((t - t2) / (t3 - t2))
        crouch = anti = lunge = land = 0.0
        gy_off = 0.0
        if t2 <= t < t3:
            near = _ease((t - (t3 - 0.45)) / 0.45)
            crouch, anti = 0.6 * near, near
        if t3 <= t < t4:
            k = (t - t3) / (t4 - t3)
            hx = cx + (land_x - cx) * (0.55 + 0.45 * k)
            lunge = math.sin(math.pi * min(k * 1.15, 1.0))
            gy_off = -170 * math.sin(math.pi * k)
            crouch = 0.5 * (1 - k)
        if t >= t4:
            hx = land_x
            land = max(0.0, 1.0 - (t - t4) / 0.55)
            gy_off = 0.0
        ground = G
        if t < t4:
            moving_prey = t >= t2 and t < t3
            if t < t3:
                draw_prey(d, px, GROUND + 14, t * STEP * (0.5 if t < t2 else 1.6), 1, (prey, pscale))
        else:
            fk = _feed_timeline(max(0.0, (t - t4 - 0.25) / max(dur - t4 - 0.35, 0.4)))
            sz = 1.0 - 0.27 * fk["bites"] if fk["bites"] else 1.0
            if fk["bites"] == 3 and fk["swallow"] > 0:
                sz = 0.04
            _food(d, hx + reach_x * s_ + 20, GROUND + 18, sz, pcol, 1)
        st = {"t": t, "moving": t < t4 and t3 > t, "ground": ground, "crouch": crouch, "anticipate": anti,
              "lunge": lunge, "land": land, "headup": 0.3 if (t2 > t > t1) else 0.0}
        if t >= t4:
            fk = _feed_timeline(max(0.0, (t - t4 - 0.25) / max(dur - t4 - 0.35, 0.4)))
            st.update({"dip": fk["dip"], "swallow": fk["swallow"], "roar": fk["roar"], "look": fk["look"] * 0.5,
                       "anticipate": 0.0})
            if t < t4 + 0.6:
                st["dip"] = max(st["dip"], 0.0)
        draw_creature(d, sp, hx, GROUND + 20 + gy_off, Pf(t) * STEP * 0.5, 1, z, st)
        if t4 <= t < t4 + 0.7:
            _dust(d, hx + 60, GROUND + 14, (t - t4) / 0.7, seed=4, size=1.2)
            _dust(d, hx - 100, GROUND + 14, (t - t4) / 0.7, seed=9, size=0.9)

    steps = _contacts(sp, Pf, dur, lambda t: m_at(t) > 0.18 and not (t3 <= t < t4) and t < t4)
    write_video(out, dur, paint, world, cwd, cam_dir=1, cam_fn=lambda t: G * Pf(t))
    return {"zoom_at": t1, "lunge_at": t4 - 0.25, "land_at": t4, "steps": steps}


def chim_mass(sp):
    g = sp["g"]
    return _clip((g["torso"][1] * g["torso"][2]) / 40000.0, 0.4, 2.0)


def _contacts(sp, Pf, dur, active):
    """Times (seconds into the scene) at which a foot lands, read from the same phase the legs are drawn with."""
    r = chim_rate(sp, None)
    if not r:
        return []
    GA = GAITS.get(sp["g"]["gait"], GAITS["trot"])
    k_ = r * GA["speed"]
    out, last = [], None
    for i in range(int(dur * FPS)):
        t = i / FPS
        n = math.floor((Pf(t) * k_ - math.pi / 2) / math.pi)
        if last is not None and n > last and active(t):
            out.append(round(t, 3))
        last = n
    return out


def walk_steps(sp, dur):
    return _contacts(sp, lambda t: t, dur, lambda t: True)


# ================================================================ cinematic depth (the "Thendan" presentation)
# Distant layers fade into haze, mist drifts at two depths, the moon/sun glows, dark foreground silhouettes pass in
# front of the action, and a vignette closes the frame. Everything is drawn by the engine; no stock footage.
from PIL import ImageFilter
CINEMA = {"on": True, "fg": False, "dof": 1.6}      # "fg": dark foreground plants/rocks (used for the hybrid series)


def _cin_assets(self):
    if getattr(self, "_cin", None) is not None:
        return self._cin
    st = self.st
    rnd = random.Random(11 + len(str(st.get("ground"))))
    PW = self.PW
    sky_lo = rgb(st["sky"][1])
    A = {}
    # atmospheric perspective: far hills sit in the haze, near hills a little less
    def hazed(layer, k, blur):
        r, g, b, a = layer.split()
        rgb_ = Image.merge("RGB", (r, g, b))
        rgb_ = Image.blend(rgb_, Image.new("RGB", rgb_.size, sky_lo), k)
        if blur:
            rgb_ = rgb_.filter(ImageFilter.GaussianBlur(blur))
            a = a.filter(ImageFilter.GaussianBlur(blur))
        out = Image.merge("RGBA", (*rgb_.split(), a))
        out.top = layer.top
        return out
    A["far"], A["near"] = hazed(self.far, 0.38, 1.6), hazed(self.near, 0.16, 0.0)
    self.far, self.near = A["far"], A["near"]
    # two mist strips, soft and wide
    def mist(h, n, alpha, col):
        im = Image.new("RGBA", (PW, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        for _ in range(n):
            x, y = rnd.uniform(0, PW), rnd.uniform(h * 0.25, h * 0.75)
            rx, ry = rnd.uniform(220, 460), rnd.uniform(24, 60)
            d.ellipse((x - rx, y - ry, x + rx, y + ry), fill=col + (alpha,))
            d.ellipse((x - rx + PW, y - ry, x + rx + PW, y + ry), fill=col + (alpha,))
        im = im.filter(ImageFilter.GaussianBlur(34))
        return im
    mc = mix(sky_lo, (230, 235, 245), 0.45)
    A["mist_hi"], A["mist_lo"] = mist(260, 9, 70, tuple(int(c) for c in mc)), mist(200, 7, 44, tuple(int(c) for c in mc))
    # glow around the moon / sun
    sx, sy, sr = getattr(self, "sun_xy", (700, 520, 85))
    glow = Image.new("RGBA", (W, 1100), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    sc = rgb(st["sun"]) if isinstance(st.get("sun"), str) and st["sun"] not in ("eclipse", "two", "planet", "hole") else (255, 235, 200)
    for k in range(18, 0, -1):
        r = sr * (1 + k * 0.38)
        gd.ellipse((sx - r, sy - r, sx + r, sy + r), fill=tuple(int(c) for c in sc) + (int(3 + 1.2 * (19 - k)),))
    A["glow"] = glow.filter(ImageFilter.GaussianBlur(26))
    # vignette
    import numpy as np
    yy, xx = np.mgrid[0:H, 0:W].astype("float32")
    r = np.sqrt(((xx - W / 2) / (W * 0.62)) ** 2 + ((yy - H * 0.55) / (H * 0.6)) ** 2)
    a = (np.clip((r - 0.62) / 0.55, 0, 1) ** 1.7 * 175).astype("uint8")
    v = np.zeros((H, W, 4), dtype="uint8")
    v[..., 3] = a
    A["vig"] = Image.fromarray(v, "RGBA")
    # foreground silhouettes: grass blades along the bottom, fronds hanging in from the top corners, boulders
    A["fg"] = None
    if CINEMA.get("fg"):
        dark_ = tuple(int(c * 0.10) for c in mix(rgb(st["ground"]), rgb(st["far"]), 0.4)) + (255,)
        fh = 560
        im = Image.new("RGBA", (PW, fh), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        base = fh - 20
        for i in range(int(PW / 14)):                         # blades
            x = i * 14 + rnd.uniform(-6, 6)
            h = rnd.uniform(40, 150) * (1.5 if rnd.random() < 0.08 else 1.0)
            lean = rnd.uniform(-28, 28)
            for off in (0, PW):
                d.polygon([(x - 6 + off, base + 60), (x + lean + off, base - h), (x + 7 + off, base + 60)], fill=dark_)
        for _ in range(5):                                    # a few big out-of-focus plants / rocks between the viewer and the creature
            x = rnd.uniform(0, PW)
            if st["sil"] in ("rocks", "peaks", "spikes", "cones", "graves", "clouds"):
                w, h = rnd.uniform(90, 200), rnd.uniform(60, 130)
                for off in (0, PW):
                    d.ellipse((x - w + off, base - h, x + w + off, base + 90), fill=dark_)
            else:
                for off in (0, PW):
                    for k in range(7):
                        a_ = -0.2 + k * 0.28
                        d.line((x + off, base + 30, x + off + math.sin(a_) * 170, base - math.cos(a_) * 230), fill=dark_, width=10)
        A["fg"] = im.filter(ImageFilter.GaussianBlur(1.4))
        # fronds hanging from the top-left / top-right corners
        top = Image.new("RGBA", (W, 640), (0, 0, 0, 0))
        td = ImageDraw.Draw(top)
        for side in (0, 1):
            ox = 0 if side == 0 else W
            sg = 1 if side == 0 else -1
            for k in range(9):
                a_ = 0.25 + k * 0.16
                L_ = rnd.uniform(260, 470)
                ex, ey = ox + sg * math.cos(a_) * L_ * 0.75, math.sin(a_) * L_
                td.line((ox, -10, ex, ey), fill=dark_, width=9)
                for j in range(1, 8):
                    px, py = ox + (ex - ox) * j / 8, -10 + (ey + 10) * j / 8
                    td.line((px, py, px + sg * 34, py + 40), fill=dark_, width=4)
                    td.line((px, py, px - sg * 6, py + 44), fill=dark_, width=4)
        A["top"] = top.filter(ImageFilter.GaussianBlur(2.2))
    self._cin = A
    return A


def _cin_back(self, im, cam, t):
    """Called on the finished background (before any creature is drawn): swap in hazed layers, glow, mid mist."""
    A = _cin_assets(self)
    base = self.sky.copy() if False else None
    if A.get("glow") is not None and self.st.get("sun") not in (None,):
        im.paste(A["glow"], (0, 0), A["glow"])
    return im


def _cin_front(self, im, cam, t):
    """Called after the creature is drawn: low mist in front of its feet, foreground silhouettes, vignette."""
    A = _cin_assets(self)
    im = im.convert("RGBA")
    for strip, k, y, sp in ((A["mist_hi"], 0.35, GROUND - 330, 14), (A["mist_lo"], 0.95, GROUND - 40, 26)):
        off = int(cam * k + t * sp) % self.PW
        for ox in (-off, -off + self.PW):
            if ox < W and ox + self.PW > 0:
                im.alpha_composite(strip, (ox, y)) if ox >= 0 else im.alpha_composite(strip.crop((-ox, 0, min(self.PW, -ox + W), strip.size[1])), (0, y))
    if A.get("fg") is not None:
        off = int(cam * 1.5 + math.sin(t * 0.9) * 5) % self.PW
        for ox in (-off, -off + self.PW):
            if ox < W and ox + self.PW > 0:
                piece = A["fg"]
                if ox >= 0:
                    im.alpha_composite(piece.crop((0, 0, min(self.PW, W - ox), piece.size[1])), (ox, H - piece.size[1] + 30))
                else:
                    im.alpha_composite(piece.crop((-ox, 0, min(self.PW, -ox + W), piece.size[1])), (0, H - piece.size[1] + 30))
        sway = int(6 * math.sin(t * 0.8))
        im.alpha_composite(A["top"], (sway, 0))
    im = im.convert("RGB")
    # one colour world: pull everything a little toward the scene's own haze colour so the creature sits IN the scene
    tint = Image.new("RGB", im.size, rgb(self.st["sky"][1]))
    im = Image.blend(im, tint, 0.11)
    im = im.convert("RGBA")
    im.alpha_composite(A["vig"])
    return im.convert("RGB")


_world_frame0 = World.frame


def _world_frame_cinema(self, cam, t):
    if CINEMA["on"] and not getattr(self, "no_cinema", False):
        A = _cin_assets(self)      # (also swaps in the hazed hills before the first frame is drawn)
    im = _world_frame0(self, cam, t)
    if CINEMA.get("dof") and CINEMA["on"] and not getattr(self, "no_cinema", False):
        im = im.filter(ImageFilter.GaussianBlur(CINEMA["dof"]))    # soft background, sharp creature
    if CINEMA["on"] and not getattr(self, "no_cinema", False):
        if A.get("glow") is not None and self.st.get("sun") not in (None, "eclipse", "two", "planet", "hole"):
            im = im.convert("RGBA")
            im.alpha_composite(A["glow"])
            im = im.convert("RGB")
    return im


World.frame = _world_frame_cinema
World.cinema_front = _cin_front


# ================================================================ lighting: rim light, shaded far side, soft cast shadow, light spill
# Every creature is drawn on its own transparent layer first, so its real silhouette is known. Then:
#  - the edge that faces the scene's light picks up a thin coloured rim,
#  - the far side falls into shade,
#  - a long soft shadow (the creature's own silhouette, flattened and stretched away from the light) lies on the ground,
#  - eyes and fire glow spill a little coloured light onto the ground in front of it.
LIGHT = {"on": True, "dir": (0.55, -0.83)}
_LIGHT_WORLD = [None]
_draw_creature_plain = draw_creature


def _light_color(world):
    try:
        base = rgb(world.st["sky"][1])
    except Exception:
        base = (120, 150, 210)
    lit = tuple(min(255, int(c * 0.55 + 150)) for c in base)           # a pale version of the horizon colour
    return mix(lit, (255, 255, 255), 0.15)


def _shift(a, dx, dy):
    out = np.zeros_like(a)
    h, w = a.shape
    xs, xd = (slice(dx, w), slice(0, w - dx)) if dx >= 0 else (slice(0, w + dx), slice(-dx, w))
    ys, yd = (slice(dy, h), slice(0, h - dy)) if dy >= 0 else (slice(0, h + dy), slice(-dy, h))
    out[yd, xd] = a[ys, xs]
    return out


def _lit_draw_creature(img_draw, sp, cx, gy, phase, facing=1, scale=None, st=None, focus=None):
    st = st or {}
    if not LIGHT["on"] or st.get("nolight") or st.get("noemerge") or not hasattr(img_draw, "_image"):
        return _draw_creature_plain(img_draw, sp, cx, gy, phase, facing, scale, st, focus)
    base = img_draw._image
    if base.mode != "RGB":
        return _draw_creature_plain(img_draw, sp, cx, gy, phase, facing, scale, st, focus)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer, "RGBA")
    s = 1.4 * (scale if scale is not None else 1.0) * sp.get("scale", 1.0) * KIND_SCALE.get(sp["kind"], 1.0)
    if focus:
        ax, ay, tx, ty = focus
        cx, gy = tx - facing * s * ax, ty - s * ay
    _draw_creature_plain(ld, sp, cx, gy, phase, facing, scale, dict(st, noshadow=True), None)
    bb = layer.getchannel("A").point(lambda v: 255 if v > 150 else 0).getbbox()
    if not bb:
        return
    pad = 30
    x0, y0 = max(int(bb[0]) - pad, 0), max(int(bb[1]) - pad, 0)
    x1, y1 = min(int(bb[2]) + pad, base.width), min(int(bb[3]) + pad, base.height)
    crop = layer.crop((x0, y0, x1, y1))
    A = np.asarray(crop.getchannel("A"), dtype=np.float32) / 255.0
    solid = (A > 0.6).astype(np.float32)
    C = np.asarray(crop.convert("RGB"), dtype=np.float32)
    dx0, dy0 = LIGHT["dir"]
    world = _LIGHT_WORLD[0]
    lc = np.array(_light_color(world) if world is not None else (150, 185, 255), dtype=np.float32)
    h, w = solid.shape
    # --- soft cast shadow (drawn first, under the creature) ---
    ground = int(gy + 18)
    if ground > y0 + 8:
        top = max(y0, 0)
        sil = Image.fromarray((solid[: max(ground - y0, 1)] * 255).astype(np.uint8), "L")
        H0 = sil.height
        sh_h = max(int(H0 * 0.20), 6)
        sil = sil.transpose(Image.FLIP_TOP_BOTTOM).resize((sil.width, sh_h), Image.BILINEAR)   # feet at row 0, head far away
        reach = 0.45 * H0 * abs(dx0 / dy0)                      # the shadow falls away from the light
        extra = int(reach) + 8
        wide = Image.new("L", (sil.width + 2 * extra, sh_h), 0)
        wide.paste(sil, (extra, 0))
        a_ = (reach / sh_h) * (1 if dx0 > 0 else -1)            # row j of the shadow moves sideways by a_ * j
        wide = wide.transform(wide.size, Image.AFFINE, (1, a_, 0, 0, 1, 0), Image.BILINEAR)
        wide = wide.filter(ImageFilter.GaussianBlur(max(5, 8 * s)))
        wide = wide.point(lambda v: int(v * (0.30 if sp["kind"] in ("bird", "float", "swim", "wraith") else 0.55)))
        sx = x0 - extra
        base.paste((5, 7, 16), (sx, ground - 6), wide)
    # --- light spill from glowing eyes / fire onto the ground ---
    ec = sp.get("eye") if isinstance(sp, dict) else None
    if ec and (sp.get("hybrid") or sp.get("kind") in ("hollow", "wraith", "djinn", "stalker", "owlman", "crawler")):
        pad_ = int(90 * s)
        gl = Image.new("L", (int(520 * s) + 2 * pad_, int(130 * s) + 2 * pad_), 0)     # padded so the blur is never cut off
        ImageDraw.Draw(gl).ellipse((pad_, pad_, gl.width - pad_, gl.height - pad_), fill=58)
        gl = gl.filter(ImageFilter.GaussianBlur(28 * s))
        base.paste(tuple(int(c) for c in ec[:3]), (int(cx + facing * 120 * s - gl.width / 2), int(gy - 40 * s - pad_)), gl)
    # --- shade the far side, rim the lit side ---
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    proj = ((xx - w / 2) * -dx0 + (yy - h / 2) * -dy0)
    proj = proj / max(float(np.abs(proj).max()), 1.0)             # -1 lit side .. +1 shadow side
    shade = 1.0 - 0.50 * np.clip((proj + 0.05) / 1.0, 0, 1) - 0.12 * np.clip((yy / max(h, 1) - 0.6) / 0.4, 0, 1)
    if world is not None:       # one colour world: the creature picks up the scene's own tone and loses a little saturation
        try:
            tint = np.array(rgb(world.st["sky"][1]), dtype=np.float32)
            grey = C.mean(axis=2, keepdims=True)
            C = C * 0.90 + grey * 0.10
            C = C * 0.86 + tint * 0.14
        except Exception:
            pass
    C = C * shade[..., None]
    kpx = max(4, int(9 * s))
    lit_edge = solid * (1.0 - _shift(solid, int(round(dx0 * kpx)), int(round(dy0 * kpx))))
    # the rim only where the surface really faces the light, and softened
    r = Image.fromarray((np.clip(lit_edge, 0, 1) * 255).astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(1.3 * max(s, 0.6)))
    r = np.asarray(r, dtype=np.float32)[..., None] / 255.0
    C = 255 - (255 - C) * (1 - np.clip(r * 1.6, 0, 1) * (lc / 255.0))
    # a faint wide glow so the lit side reads as lit, not just outlined
    wide = Image.fromarray((np.clip(lit_edge, 0, 1) * 255).astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(7 * max(s, 0.6)))
    wide = np.asarray(wide, dtype=np.float32)[..., None] / 255.0
    C = np.clip(C + wide * solid[..., None] * lc * 0.30, 0, 255)
    out = np.zeros((h, w, 4), dtype=np.uint8)
    out[..., :3] = C.astype(np.uint8)
    out[..., 3] = (A * 255).astype(np.uint8)
    base.paste(Image.fromarray(out, "RGBA").convert("RGB"), (x0, y0), Image.fromarray(out[..., 3], "L"))


draw_creature = _lit_draw_creature


# ================================================================ the towering shot: low angle, a huge creature, a tiny person for scale
def tower_scene(out, dur, world, sp, cwd=None):
    """The camera sits near the ground and looks up. The creature fills the frame and breathes; a tiny figure with a lantern
    stands at its feet. Slow push-in, no cuts."""
    world.push = 0.04

    def paint(im, d, t, u, cam):
        dim(im, 150)
        k = _sm(u)
        z = 1.22 + 0.20 * k
        draw_creature(d, sp, 790 + 8 * math.sin(t * 0.6), 1760 + 4 * math.sin(t * 1.4), t * 0.6, -1, z,
                      {"t": t, "roar": 0.0, "moving": False})
        # the tiny person at the bottom of the frame, looking up, with a lantern
        draw_human(d, 960, 1775, 0.0, -1, 0.62, "lantern", cast=False)
        _vignette(im, 230)
    write_video(out, dur, paint, world, cwd, cam_dir=0.0)
    return {"hits": [dur * 0.45]}


INSERT_FUNCS["tower"] = tower_scene
