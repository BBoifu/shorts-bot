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
    for i in range(9):
        k = i / 8
        x = -200 + k * 380
        y = -240 - 190 * math.sin(k * 2.7) * 0.9 - 30 * k
        h = 46 + 26 * ((i * 53) % 4) / 3
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
    if "horns" in sp["traits"] and sp["name"] == "bull":
        pen.poly([(hx0 + 55, hy0 - 60), (hx0 + 30, hy0 - 180), (hx0 + 125, hy0 - 245), (hx0 + 80, hy0 - 140), (hx0 + 105, hy0 - 62)], bone)
    eye(pen, hx0 + 112, hy0 - 30, 12, sp, st)
    eye(pen, hx0 + 62, hy0 - 40, 7, sp, st)
    eye(pen, hx0 + 160, hy0 - 22, 6, sp, st)
    for k in range(3):  # drool
        x = hx0 + 100 + L - k * 40
        pen.line([(x, hy0 + 18 + o * 0.9), (x, hy0 + 40 + o * 0.9 + 30 + 10 * k)], 4, (205, 215, 220))


KINDS["beast"] = draw_beast


def head_anchor(sp):
    """Where the face is, in creature units (used to zoom in on it)."""
    tr, k = sp["traits"], sp["kind"]
    if k == "beast":
        return 345, -330
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


def draw_creature(img_draw, sp, cx, gy, phase, facing=1, scale=None, st=None, focus=None):
    st = st or {}
    s = 1.4 * (scale if scale is not None else 1.0) * sp.get("scale", 1.0) * (0.78 if sp["kind"] in ("swim", "float") else 0.72 if sp["kind"] == "beast" else 1.0)
    if focus:  # (ax, ay, tx, ty): put the creature point (ax, ay) at screen position (tx, ty)
        ax, ay, tx, ty = focus
        cx, gy = tx - facing * s * ax, ty - s * ay
    if sp["hybrid"] and st.get("aura", True):
        for k, a in ((1.0, 22), (0.78, 26), (0.56, 30)):
            img_draw.ellipse((cx - 380 * s * k, gy - 640 * s * k, cx + 380 * s * k, gy + 40 * s * k), fill=(170, 10, 14, a))
    pen = Pen(img_draw, cx, gy, s, facing, rimfn)
    if sp["kind"] not in ("swim", "float") and sp["kind"] != "bird" or (sp["kind"] == "bird" and "flying" not in sp["traits"]):
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
        if st["sky"][0] in ("#05060f", "#000000", "#2f3e25", "#3a0d0d", "#0a1030"):
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
            d = ImageDraw.Draw(im, "RGBA")
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
def folk_creature(name):
    """A folklore creature drawn in the scary style (glowing eyes, fangs, dark body)."""
    sp = animal_spec(name)
    sp["kind"] = "beast"
    sp["snout"] = SNOUTS.get(name, 0.9)
    sp["hybrid"] = True
    sp["horror"] = True
    sp["eye"] = (255, 40, 30)
    sp["scale"] = 1.05
    sp["color"] = mix(sp["color"], (10, 8, 18), 0.78)
    sp["traits"] = set(sp["traits"]) | {"fangs", "glow_eyes"}
    sp["traits"] -= {"flying", "walker"}
    if sp["kind"] == "bird":
        sp["traits"] |= {"wings", "beak"}
    return sp


def draw_human(d, cx, gy, phase, facing=1, scale=1.0, lamp="lantern"):
    """A flat silhouette of a person walking with a lantern."""
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


def attack_scene(out, dur, world, sp, cwd=None):
    """A person walks. The creature closes in behind. The person stops and, very slowly, starts to turn.
    The creature lunges: a flash, a spray of blood across the frame, and the person is gone.
    Only the lantern is left, dropped on a growing pool of blood."""
    sp2 = dict(sp)
    sp2["bloody"] = False
    sp3 = dict(sp)
    sp3["bloody"] = True
    t_walk, t_still, t_lunge, t_hit = 0.30, 0.50, 0.60, 0.64
    info = {"impact_at": dur * t_hit, "stalk_at": dur * t_walk}

    def paint(im, d, t, u, cam):
        ph = t * STEP * 0.5
        hx = 760
        if u < t_still:
            gap = 960 - 440 * min(1, u / t_still)
            moving = u < t_walk
            draw_creature(d, sp2, hx - gap, GROUND + 20, ph + 0.7, 1, 1.0, {"t": t, "moving": True})
            if moving:
                draw_human(d, hx, GROUND + 20, ph, 1, 1.0)
            else:  # the person stops, frozen, and shakes
                draw_human(d, hx + 3 * math.sin(t * 40), GROUND + 20, 0.0, 1, 1.0)
            if u > t_walk:  # a red pulse like a heartbeat
                beat = max(0.0, math.sin(t * 9)) ** 6 * 0.18 * ((u - t_walk) / (t_still - t_walk))
                return red_wash(im, beat)
            return
        if u < t_hit:
            k = (u - t_still) / (t_hit - t_still)
            cx = hx - 520 + 200 * k * k
            draw_creature(d, sp2, cx, GROUND + 20, ph + 0.7, 1, 1.0 + 0.1 * k, {"t": t, "moving": False, "roar": k})
            draw_human(d, hx, GROUND + 20, 0.0, 1, 1.0)
            return
        k = (u - t_hit) / (1 - t_hit)
        sx = 16 * math.sin(t * 90) * max(0.0, 1 - k * 4)
        sy = 12 * math.cos(t * 80) * max(0.0, 1 - k * 4)
        blood_pool(d, hx - 20, GROUND + 26, min(1, k * 2.2))
        lantern_on_ground(d, hx + 150, GROUND + 28, t, alive=max(0.0, 1.0 - max(0.0, k - 0.6) * 2.5))
        draw_creature(d, sp3, hx - 330 + sx, GROUND + 20 + sy, ph, 1, 1.05, {"t": t, "moving": False, "roar": min(1, k * 3)})
        blood_burst(d, hx + 20, GROUND - 330, min(1, k * 1.6), seed=7)
        if k < 0.07:
            return Image.blend(im, Image.new("RGB", im.size, (255, 255, 255)), 0.7 * (1 - k / 0.07))
        if k < 0.35:
            return red_wash(im, 0.5 * (1 - k / 0.35))
    write_video(out, dur, paint, world, cwd, cam_dir=1)
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
                    _claw_print(d2, x, y, max(0.0, 1 - (t - tc) / 0.6))
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
        draw_human(d, 540 + jit, GROUND + 20, 0.0, 1, 1.15 * pulse)
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
        draw_human(d, 760 + jx, GROUND + 20 + jy, ph, 1, 1.0)
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
