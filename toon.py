"""
A tiny flat-style animation engine. Draws creatures that really walk, swim, fly and attack,
in front of a scrolling night/day scene, and writes the result as a video. Needs only Pillow + ffmpeg.
The look is deliberately stylized (flat shapes, rim light), not realistic.
"""
import math
import random
import subprocess
from PIL import Image, ImageDraw, ImageFont

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


def head_anchor(sp):
    """Where the face is, in creature units (used to zoom in on it)."""
    tr, k = sp["traits"], sp["kind"]
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


def draw_creature(img_draw, sp, cx, gy, phase, facing=1, scale=None, st=None, focus=None):
    st = st or {}
    s = 1.4 * (scale if scale is not None else 1.0) * sp.get("scale", 1.0) * KIND_SCALE.get(sp["kind"], 1.0)
    if focus:  # (ax, ay, tx, ty): put the creature point (ax, ay) at screen position (tx, ty)
        ax, ay, tx, ty = focus
        cx, gy = tx - facing * s * ax, ty - s * ay
    if sp["hybrid"] and st.get("aura", True):
        for k, a in ((1.0, 22), (0.78, 26), (0.56, 30)):
            img_draw.ellipse((cx - 380 * s * k, gy - 640 * s * k, cx + 380 * s * k, gy + 40 * s * k), fill=(170, 10, 14, a))
    ox, oy = _REC_OFF
    _BOXES.append((cx + ox - 330 * s, gy + oy - 1050 * s, cx + ox + 330 * s, gy + oy + 40 * s))
    pen = Pen(img_draw, cx, gy, s, facing, rimfn)
    if sp["kind"] not in ("swim", "float", "wraith") and sp["kind"] != "bird" or (sp["kind"] == "bird" and "flying" not in sp["traits"]):
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
def write_video(out, dur, painter, world, cwd=None, cam_dir=1):
    """painter(draw, t, u, cam) draws the actors. Frames are piped straight into ffmpeg."""
    n = max(int(dur * FPS), 2)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{OW}x{OH}",
           "-r", str(FPS), "-i", "-", "-vf", "scale=1080:1920:flags=bicubic", "-c:v", "libx264",
           "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-an", out]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, cwd=cwd)
    track = []
    try:
        for i in range(n):
            t = i / FPS
            u = i / max(n - 1, 1)
            cam = t * 260 * cam_dir
            im = world.frame(cam, t)
            d = ImageDraw.Draw(im, "RGBA")
            _BOXES.clear()
            ov = painter(im, d, t, u, cam)
            if ov is not None:
                im = ov
            box = None
            if _BOXES:
                box = [min(b[0] for b in _BOXES), min(b[1] for b in _BOXES), max(b[2] for b in _BOXES), max(b[3] for b in _BOXES)]
            push, shake = getattr(world, "push", 0.0), getattr(world, "shake", 0.0)
            if push or shake:
                z = 1.0 + push * (u * u * (3 - 2 * u)) + (0.05 if shake else 0.0)
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
        sh = (6 * math.sin(t * 70)) if roar > 0.1 else 0
        draw_creature(d, sp, (400 if facing == 1 else 680) + sh, GROUND + 20, t * STEP * 0.5,
                      facing, z, {"t": t, "roar": roar, "moving": True})
    write_video(out, dur, paint, world, cwd, cam_dir=facing)


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
                      {"t": t, "crouch": crouch, "roar": roar, "moving": moving, "headup": roar})
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
BODIES = ("beast", "stalker", "arachnid", "serpent", "wraith", "owlman", "crawler", "djinn", "hollow")
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


def reveal_creature(im, sp, cx, gy, scale, t, lit, facing=1, moving=True, roar=0.2):
    """Draw the creature as a pure black silhouette (no aura, no glowing eyes). `lit` (0..1) brings its real colours
    back around the head, as if a light has just hit its face."""
    s = 1.4 * scale * sp.get("scale", 1.0) * KIND_SCALE.get(sp["kind"], 1.0)
    x0, x1 = int(max(0, cx - 520 * s)), int(min(W, cx + 520 * s))
    y0, y1 = int(max(0, gy - 1250 * s)), int(min(H, gy + 80 * s))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return
    layer = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer, "RGBA")
    _REC_OFF[0], _REC_OFF[1] = x0, y0
    try:
        draw_creature(ld, sp, cx - x0, gy - y0, t * STEP * 0.3, facing, scale, {"t": t, "moving": moving, "roar": roar, "aura": False})
    finally:
        _REC_OFF[0], _REC_OFF[1] = 0, 0
    a = layer.getchannel("A")
    # the shadow it casts on the floor is part of the layer; keep the whole body black
    base = im.crop((x0, y0, x1, y1)).convert("RGBA")
    sil = Image.new("RGBA", layer.size, (3, 3, 6, 255))
    sil.putalpha(a)
    base.alpha_composite(sil)
    if lit > 0.01:
        ax, ay = head_anchor(sp)
        hx, hy = cx - x0 + facing * s * ax, gy - y0 + s * ay
        rad = max(40.0, 300 * s)
        mask = Image.new("L", layer.size, 0)
        ImageDraw.Draw(mask).ellipse((hx - rad, hy - rad, hx + rad, hy + rad), fill=int(255 * min(1.0, lit)))
        mask = mask.filter(ImageFilter.GaussianBlur(rad * 0.3))
        real = layer.convert("RGB").point(lambda v: min(255, int(v * 2.3 + 34)))
        real = Image.merge("RGBA", (*real.split(), ImageChops.multiply(a, mask)))
        base.alpha_composite(real)
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
