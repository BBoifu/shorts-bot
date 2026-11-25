"""
A tiny flat-style animation engine. Draws creatures that really walk, swim, fly and attack,
in front of a scrolling night/day scene, and writes the result as a video. Needs only Pillow + ffmpeg.
The look is deliberately stylized (flat shapes, rim light), not realistic.
"""
import math
import os
import random
import subprocess
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import numpy as np

W, H = 1080, 1920       # drawing size
# size sent to ffmpeg. The camera zoom is cut straight from the 1080x1920 drawing into this size in ONE step (no second enlargement later),
# and sharpened according to how much it was enlarged. OUT_RES=1080 gives the old size.
OW, OH = (1080, 1920) if os.environ.get("OUT_RES", "1440") == "1080" else (1440, 2560)
GROUND = 1360
FPS = 30


# ====================================================================================================== HI-RES RENDERING
# The painters were written for a 1080 x 1920 picture. To make the video truly sharper, a scene can be drawn with RSF[0] times
# more pixels (2.0 = four times the pixels): every drawing call keeps its old 1080-based numbers and is scaled here, and the
# finished big frame is shrunk to the video size (so edges are smooth, thin lines stay crisp, and camera zooms do not blur).
# RSF[0] == 1.0 means "off": every patch below then just calls the original PIL function, nothing changes.
import weakref

RSF = [1.0]
_HR = {}          # id(image) -> weakref, for pictures that hold RSF times more pixels than their 1080-based size


def _raw(fn, *a, **k):
    """Run a real PIL function with the hi-res patches switched off, because PIL's own insides call these same methods."""
    old = RSF[0]
    RSF[0] = 1.0
    try:
        return fn(*a, **k)
    finally:
        RSF[0] = old


def _hr_reg(im):
    if isinstance(im, Image.Image):
        k = id(im)
        _HR[k] = weakref.ref(im, lambda _r, k=k: _HR.pop(k, None))
    return im


def _big():
    return (int(round(W * RSF[0])), int(round(H * RSF[0])))


_size_p = Image.Image.size.fget
_width_p = Image.Image.width.fget
_height_p = Image.Image.height.fget


def _psize(im):
    """The real number of pixels (PIL's own idea of size)."""
    return _size_p(im)


def _is_hr(im):
    """A picture counts as hi-res if it was made hi-res, or if it is exactly the big frame size."""
    if RSF[0] == 1.0 or not isinstance(im, Image.Image):
        return False
    r = _HR.get(id(im))
    if r is not None and r() is im:
        return True
    return _size_p(im) == _big()


def _lsize(self):
    """While a scene is drawn big, a hi-res picture reports its 1080-based size, so every `im.size`, `im.width`... in the painters
    keeps meaning what it always meant. (PIL's own code runs through _raw(), where sizes are real again.)"""
    s = _size_p(self)
    if RSF[0] != 1.0 and _is_hr(self):
        r = RSF[0]
        return (int(round(s[0] / r)), int(round(s[1] / r)))
    return s


Image.Image.size = property(_lsize)
Image.Image.width = property(lambda self: _lsize(self)[0])
Image.Image.height = property(lambda self: _lsize(self)[1])


_o_new = Image.new


def _hr_new(mode, size, color=0):
    r = RSF[0]
    if r != 1.0 and tuple(size) in ((W, H), _big()):
        return _hr_reg(_o_new(mode, _big(), color))
    return _o_new(mode, size, color)


Image.new = _hr_new


def _up(im, r=None):
    """A 1080-based picture brought up to hi-res size so it can be combined with a hi-res one."""
    r = r or RSF[0]
    s = _psize(im)
    res = _raw(im.resize, (int(round(s[0] * r)), int(round(s[1] * r))), Image.BICUBIC)
    return _hr_reg(res)


def _scpt(v, r):
    return tuple(int(round(x * r)) for x in v)


def _scxy(xy, r):
    if isinstance(xy, (list, tuple)) and len(xy):
        if isinstance(xy[0], (int, float)):
            return [v * r for v in xy]
        return [(p[0] * r, p[1] * r) for p in xy]
    return xy


def _drs(self):
    r = RSF[0]
    if r == 1.0:
        return 1.0
    im = getattr(self, "_image", None)
    return r if (im is not None and _is_hr(im)) else 1.0


def _wrap_draw(name, wpos, radius=False):
    o = getattr(ImageDraw.ImageDraw, name)
    always = name in ("line", "arc")

    def f(self, xy, *a, **k):
        r = _drs(self)
        if r != 1.0:
            xy = _scxy(xy, r)
            given = k.get("width") or (a[wpos] if (wpos is not None and len(a) > wpos and a[wpos]) else 0)
            if given:
                nw = max(1, int(round(given * r)))
                if k.get("width"):
                    k["width"] = nw
                else:
                    a = list(a)
                    a[wpos] = nw
                    a = tuple(a)
            else:                                   # the default 1-pixel line / outline must become r pixels wide
                has_outline = k.get("outline") is not None or (len(a) > wpos - 1 and a[wpos - 1] is not None)
                if always or has_outline:
                    k["width"] = max(1, int(round(r)))
            if radius:
                if "radius" in k and k["radius"]:
                    k["radius"] = k["radius"] * r
                elif a and isinstance(a[0], (int, float)):
                    a = (a[0] * r,) + tuple(a[1:])
        return _raw(o, self, xy, *a, **k) if r != 1.0 else o(self, xy, *a, **k)
    setattr(ImageDraw.ImageDraw, name, f)


for _n, _w in (("polygon", 2), ("line", 1), ("ellipse", 2), ("rectangle", 2), ("arc", 3), ("pieslice", 4), ("chord", 4)):
    _wrap_draw(_n, _w)
_wrap_draw("rounded_rectangle", 3, radius=True)

_o_text = ImageDraw.ImageDraw.text


def _hr_text(self, xy, text, *a, **k):
    r = _drs(self)
    if r != 1.0:
        xy = _scxy(xy, r)
        f = k.get("font")
        if f is not None and hasattr(f, "font_variant"):
            try:
                k["font"] = f.font_variant(size=int(round(f.size * r)))
            except Exception:
                pass
        if k.get("stroke_width"):
            k["stroke_width"] = int(round(k["stroke_width"] * r))
    return _raw(_o_text, self, xy, text, *a, **k) if r != 1.0 else _o_text(self, xy, text, *a, **k)


ImageDraw.ImageDraw.text = _hr_text

_o_point = ImageDraw.ImageDraw.point


def _hr_point(self, xy, fill=None):
    r = _drs(self)
    return _raw(_o_point, self, _scxy(xy, r), fill) if r != 1.0 else _o_point(self, xy, fill)


ImageDraw.ImageDraw.point = _hr_point

# ---- pictures combined with each other
_o_paste = Image.Image.paste


def _down(im):
    """A hi-res picture brought down to a 1080-based picture (for the rare picture that is built the normal way)."""
    s = _psize(im)
    r = RSF[0]
    return _raw(im.resize, (int(round(s[0] / r)), int(round(s[1] / r))), Image.LANCZOS)


def _hr_paste(self, im, box=None, mask=None):
    if not _is_hr(self):
        if RSF[0] != 1.0:                                   # a normal-size picture receiving a hi-res piece: bring the piece down
            if isinstance(im, Image.Image) and _is_hr(im):
                im = _down(im)
            if isinstance(mask, Image.Image) and _is_hr(mask):
                mask = _down(mask)
        return _o_paste(self, im, box, mask)
    r = RSF[0]
    if isinstance(im, Image.Image) and not _is_hr(im):
        im = _up(im, r)
    if isinstance(mask, Image.Image) and not _is_hr(mask):
        mask = _up(mask, r)
    if box is not None and not isinstance(box, Image.Image):
        box = _scpt(box, r)
    return _raw(_o_paste, self, im, box, mask)


Image.Image.paste = _hr_paste

_o_acm = Image.Image.alpha_composite


def _hr_acm(self, im, dest=(0, 0), source=(0, 0)):
    if not _is_hr(self):
        if RSF[0] != 1.0 and isinstance(im, Image.Image) and _is_hr(im):
            im = _down(im)
        return _o_acm(self, im, dest, source)
    r = RSF[0]
    if not _is_hr(im):
        im = _up(im, r)
    return _raw(_o_acm, self, im, _scpt(dest, r), _scpt(source, r))


Image.Image.alpha_composite = _hr_acm


def _same(a, b):
    """Bring two pictures to the same size when exactly one of them is hi-res."""
    ha, hb = _is_hr(a), _is_hr(b)
    if ha and not hb and _psize(a) != _psize(b):
        b = _up(b)
    elif hb and not ha and _psize(a) != _psize(b):
        a = _up(a)
    return a, b


def _wrap2(mod, name, nargs=2):
    o = getattr(mod, name)

    def f(*args, **k):
        if RSF[0] != 1.0 and len(args) >= nargs and isinstance(args[0], Image.Image) and isinstance(args[1], Image.Image):
            a, b = _same(args[0], args[1])
            args = (a, b) + tuple(args[2:])
            res = _raw(o, *args, **k)
            return _hr_reg(res) if (_is_hr(a) or _is_hr(b)) and isinstance(res, Image.Image) else res
        return o(*args, **k)
    setattr(mod, name, f)


for _n in ("blend", "alpha_composite"):
    _wrap2(Image, _n)

_o_composite = Image.composite


def _hr_composite(im1, im2, mask):
    if RSF[0] != 1.0 and (_is_hr(im1) or _is_hr(im2) or _is_hr(mask)):
        im1 = im1 if _is_hr(im1) else _up(im1)
        im2 = im2 if _is_hr(im2) else _up(im2)
        mask = mask if _is_hr(mask) else _up(mask)
        return _hr_reg(_raw(_o_composite, im1, im2, mask))
    return _o_composite(im1, im2, mask)


Image.composite = _hr_composite

from PIL import ImageChops as _IC
for _n in ("multiply", "screen", "add", "subtract", "lighter", "darker", "difference", "add_modulo", "subtract_modulo", "overlay", "soft_light", "hard_light"):
    if hasattr(_IC, _n):
        _wrap2(_IC, _n)

_o_offset = _IC.offset


def _hr_chops_offset(image, xoffset, yoffset=None):
    if _is_hr(image):
        r = RSF[0]
        return _hr_reg(_raw(_o_offset, image, int(round(xoffset * r)), None if yoffset is None else int(round(yoffset * r))))
    return _o_offset(image, xoffset, yoffset)


_IC.offset = _hr_chops_offset

# ---- pictures made from a hi-res picture stay hi-res
_o_crop = Image.Image.crop


def _hr_crop(self, box=None):
    if not _is_hr(self):
        return _o_crop(self, box)
    return _hr_reg(_raw(_o_crop, self, _scpt(box, RSF[0]) if box is not None else None))


Image.Image.crop = _hr_crop


def _wrap_keep(name):
    o = getattr(Image.Image, name)

    def f(self, *a, **k):
        hr = _is_hr(self)
        res = _raw(o, self, *a, **k) if hr else o(self, *a, **k)
        if hr:
            if isinstance(res, Image.Image):
                _hr_reg(res)
            elif isinstance(res, tuple):
                for q in res:
                    _hr_reg(q)
        return res
    setattr(Image.Image, name, f)


for _n in ("copy", "convert", "point", "getchannel", "transpose", "split"):
    _wrap_keep(_n)

_o_filter = Image.Image.filter


def _hr_filter(self, flt):
    if not _is_hr(self):
        return _o_filter(self, flt)
    r = RSF[0]
    try:
        if isinstance(flt, ImageFilter.GaussianBlur):
            flt = ImageFilter.GaussianBlur(flt.radius * r)
        elif isinstance(flt, ImageFilter.UnsharpMask):
            flt = ImageFilter.UnsharpMask(radius=flt.radius * r, percent=flt.percent, threshold=flt.threshold)
        elif isinstance(flt, ImageFilter.BoxBlur):
            flt = ImageFilter.BoxBlur(flt.radius * r)
        elif isinstance(flt, (ImageFilter.MaxFilter, ImageFilter.MinFilter, ImageFilter.MedianFilter, ImageFilter.ModeFilter)):
            sz = int(round(flt.size * r)) | 1
            flt = type(flt)(sz)
    except Exception:
        pass
    return _hr_reg(_raw(_o_filter, self, flt))


Image.Image.filter = _hr_filter

_o_transform = Image.Image.transform


def _hr_transform(self, size, method, data=None, resample=Image.NEAREST, fill=1, fillcolor=None):
    """A hi-res picture keeps hi-res: the output size and every pixel-based number of the transform are multiplied by RSF."""
    hr = _is_hr(self)
    if hr:
        r = RSF[0]
        size = (int(round(size[0] * r)), int(round(size[1] * r)))
        if data is not None:
            if method == Image.AFFINE and len(data) == 6:
                data = (data[0], data[1], data[2] * r, data[3], data[4], data[5] * r)
            elif method == Image.PERSPECTIVE and len(data) == 8:
                data = (data[0], data[1], data[2] * r, data[3], data[4], data[5] * r, data[6] / r, data[7] / r)
            elif method == Image.QUAD and len(data) == 8:
                data = tuple(v * r for v in data)
            elif method == Image.MESH:
                data = [((b[0] * r, b[1] * r, b[2] * r, b[3] * r), tuple(v * r for v in q)) for b, q in data]
    res = _raw(_o_transform, self, size, method, data, resample, fill, fillcolor) if hr else _o_transform(self, size, method, data, resample, fill, fillcolor)
    return _hr_reg(res) if hr else res


Image.Image.transform = _hr_transform

_o_rotate = Image.Image.rotate


def _hr_rotate(self, angle, resample=Image.NEAREST, expand=0, center=None, translate=None, fillcolor=None):
    hr = _is_hr(self)
    if hr:
        r = RSF[0]
        if center is not None:
            center = (center[0] * r, center[1] * r)
        if translate is not None:
            translate = (translate[0] * r, translate[1] * r)
    res = _raw(_o_rotate, self, angle, resample, expand, center, translate, fillcolor) if hr else _o_rotate(self, angle, resample, expand, center, translate, fillcolor)
    return _hr_reg(res) if hr else res


Image.Image.rotate = _hr_rotate

_o_resize = Image.Image.resize


def _hr_resize(self, size, resample=None, box=None, reducing_gap=None):
    """A hi-res picture resized to a 1080-based size stays hi-res (its size is multiplied)."""
    if _is_hr(self) and not getattr(_hr_resize, "raw", False):
        r = RSF[0]
        size = (int(round(size[0] * r)), int(round(size[1] * r)))
        if box is not None:
            box = tuple(v * r for v in box)
        return _hr_reg(_raw(_o_resize, self, size, resample, box, reducing_gap))
    return _o_resize(self, size, resample, box, reducing_gap)


Image.Image.resize = _hr_resize

_o_getbbox = Image.Image.getbbox


def _hr_getbbox(self, *a, **k):
    hr = _is_hr(self)
    bb = _raw(_o_getbbox, self, *a, **k) if hr else _o_getbbox(self, *a, **k)
    if bb is not None and hr:
        r = RSF[0]
        bb = (int(bb[0] // r), int(bb[1] // r), int(-(-bb[2] // r)), int(-(-bb[3] // r)))
    return bb


Image.Image.getbbox = _hr_getbbox


def raw_resize(im, size, resample=None, box=None):
    """The one place that really wants physical pixels (the last step before ffmpeg)."""
    return _raw(_o_resize, im, size, resample, box)


def hr_size(im):
    return im.size


_o_putalpha = Image.Image.putalpha


def _hr_putalpha(self, alpha):
    if _is_hr(self) and isinstance(alpha, Image.Image) and not _is_hr(alpha):
        alpha = _up(alpha)
    return _raw(_o_putalpha, self, alpha)


Image.Image.putalpha = _hr_putalpha

_o_reduce = Image.Image.reduce


def _hr_reduce(self, factor, box=None):
    hr = _is_hr(self)
    res = _raw(_o_reduce, self, factor, box) if hr else _o_reduce(self, factor, box)
    return _hr_reg(res) if hr else res


Image.Image.reduce = _hr_reduce

_o_arr = Image.Image.__array_interface__


def _hr_arr(self):
    return _raw(_o_arr.fget, self) if _is_hr(self) else _o_arr.fget(self)


Image.Image.__array_interface__ = property(_hr_arr)


# ---------------------------------------------------------------- colors
def rgb(h):
    if not isinstance(h, str):
        return tuple(int(c) for c in h[:3])
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
_SSK = [1]       # while a creature is drawn at 2x for smooth edges, the smallest line widths double too


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
                       outline=self.rim(fill) if rim else None, width=max(2 * _SSK[0], int(4 * self.s)))

    def poly(self, pts, fill, rim=True):
        self.d.polygon([(self.X(x), self.Y(y)) for x, y in pts], fill=tuple(fill),
                       outline=self.rim(fill) if rim else None, width=max(2 * _SSK[0], int(3 * self.s)))

    def line(self, pts, w, fill):
        p = [(self.X(x), self.Y(y)) for x, y in pts]
        ww = max(2 * _SSK[0], int(w * self.s))
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


_ARACH_LEG = None


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
        if _ARACH_LEG is not None and sp.get("horror") and not sp.get("noskel"):
            return _ARACH_LEG(pen, sp, ph, st, by, i, side)
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


JUMP_CUT = [None]          # where in the scene the jump-scare lands (a fraction); set so it follows the last spoken word
PAUSE_REMAIN = [None]   # seconds the narrator keeps quiet at the end of a scene while its loud sound plays; the kill is timed to start it
_CLOSEUP = [False]   # set while a macro close-up is on screen (no mist, no overlays)
_BOXES = []          # where the creature was drawn in the current frame (for captions to stay out of its way)
_REC_OFF = [0, 0]
LAST_TRACK = []      # [(time, (x0, y0, x1, y1) or None)] of the last video written


class _Box(list):
    """The union box of everything protected in a frame; .parts keeps the separate boxes so a caption can slip BETWEEN two subjects."""
    parts = ()




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
    ox = shake * 0.5 * math.sin(tt * 90)
    oy = shake * 0.35 * math.cos(tt * 77)
    _nb = len(_BOXES)
    draw_creature(d, sp, 0, 0, tt * 2.0, 1, z, {"t": tt, "roar": roar, "moving": False},
                  focus=(ax, ay, 600 + ox, 800 + oy))
    if len(_BOXES) > _nb:       # in a close-up the protected subject is the FACE (eyes, mouth), not every pixel of the neck and body
        g = sp.get("g") if isinstance(sp.get("g"), dict) else {}
        hd = g.get("head") or {}
        sw_, sh_ = hd.get("skull") or (60, 50)
        sn = hd.get("snout")
        s_ = 1.4 * z * sp.get("scale", 1.0) * KIND_SCALE.get(sp.get("kind"), 1.0)
        hw = _clip((sw_ * 0.6 + (float(sn[1]) if sn else 60.0)) * s_ * 1.05, 260, 460)
        hh = _clip(sh_ * 0.75 * s_, 230, 340)
        del _BOXES[_nb:]
        _BOXES.append((600 + ox - hw * 0.8, 800 + oy - hh, 600 + ox + hw * 1.0, 800 + oy + hh))


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
PLACES["a deep cave"] = dict(sky=("#07070d", "#1a1626"), far="#241d36", near="#100c1c", ground="#08060e", sil="spikes", sun=None, part="dust")
PLACES["open grasslands"] = dict(sky=("#79b0dc", "#efe0b0"), far="#8aa35a", near="#587a3a", ground="#34502a", sil="dunes", sun="#fff3c8", part="dust")
PLACES["a snowy forest"] = dict(sky=("#8fa8c4", "#dfe8f0"), far="#5d7a8a", near="#3a5260", ground="#e6eef4", sil="trees", sun="#ffffff", part="snow")
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
AURORA_COLS = {"green": ((70, 255, 150), (110, 120, 255), (70, 230, 225)), "red": ((255, 45, 70), (215, 35, 120), (255, 120, 70)),
               "violet": ((170, 90, 255), (90, 110, 255), (230, 90, 200)), "blue": ((80, 150, 255), (70, 230, 235), (130, 110, 255)),
               "gold": ((255, 200, 70), (255, 120, 60), (240, 240, 120))}


def _aurora(im, t, color="green", strength=1.0):
    """Northern-lights curtains: smooth glowing ribbons with a bright lower edge that fades upwards and drifting rays.
    Drawn small and scaled up, so it is soft; the colour can be anything the story says (crimson lights are crimson)."""
    import numpy as np
    cols = AURORA_COLS.get(color, AURORA_COLS["green"])
    W_, H_ = im.size
    gw, gh = max(8, W_ // 8), max(8, H_ // 8)
    xs = np.linspace(0, 1, gw, dtype=np.float32)[None, :]
    ys = np.linspace(0, 1, gh, dtype=np.float32)[:, None]
    rgb = np.zeros((gh, gw, 3), np.float32)
    al = np.zeros((gh, gw), np.float32)
    for j, c in enumerate(cols):
        base = 0.14 + 0.065 * j + 0.045 * np.sin(xs * 5.5 + t * 0.55 + j * 1.9) + 0.02 * np.sin(xs * 15 + t * 1.1 + j)
        dy = base - ys                                                       # above the bright edge
        band = np.where(dy >= 0, np.exp(-np.maximum(dy, 0) / 0.13), np.exp(np.minimum(dy, 0) / 0.012))
        rays = 0.62 + 0.38 * np.sin(xs * 70 + j * 2.1 + 1.4 * np.sin(t * 0.8 + xs * 6)) ** 2
        a = band * rays * (0.62 if j == 0 else 0.5) * strength
        rgb += a[..., None] * np.array(c, np.float32)
        al += a
    col = rgb / np.maximum(al[..., None], 1e-3)
    lay = Image.fromarray(np.clip(col, 0, 255).astype(np.uint8)).resize((W_, H_), Image.BICUBIC)
    mask = Image.fromarray(np.clip(al * 140, 0, 215).astype(np.uint8)).resize((W_, H_), Image.BICUBIC)
    im.paste(lay, mask=mask)


def sky_effects(im, fx, t, sun, aurora="green"):
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
        _aurora(im, t, aurora, 0.8)
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


# ---------------------------------------------------------------- background quality
# The backgrounds are made once per scene, so they can afford to be drawn big and shrunk (smooth, anti-aliased edges),
# opened up in the shadows, brightened and given local contrast ("clarity") before any frame is rendered.
SS = 2                                                                       # background layers are drawn at 2x and shrunk
BG_TONE = {"shadows": 0.62, "bright": 1.12, "clarity": 0.60, "sharp": 0.9}


class _SSDraw:
    """Looks like an ImageDraw but draws at SS times the size, so a picture shrunk afterwards has smooth edges."""

    def __init__(self, d, s):
        self.d, self.s = d, s

    def _pts(self, pts):
        s = self.s
        if pts and isinstance(pts[0], (int, float)):
            return tuple(v * s for v in pts)
        return [(x * s, y * s) for x, y in pts]

    def polygon(self, pts, fill=None, outline=None):
        self.d.polygon(self._pts(pts), fill=fill, outline=outline)

    def rectangle(self, box, fill=None, outline=None, width=1):
        self.d.rectangle(self._pts(box), fill=fill, outline=outline, width=max(1, int(round(width * self.s))))

    def rounded_rectangle(self, box, radius=0, fill=None, outline=None, width=1):
        self.d.rounded_rectangle(self._pts(box), radius=radius * self.s, fill=fill, outline=outline, width=max(1, int(round(width * self.s))))

    def ellipse(self, box, fill=None, outline=None, width=1):
        self.d.ellipse(self._pts(box), fill=fill, outline=outline, width=max(1, int(round(width * self.s))))

    def line(self, pts, fill=None, width=1):
        self.d.line(self._pts(pts), fill=fill, width=max(1, int(round(width * self.s))))

    def arc(self, box, start, end, fill=None, width=1):
        self.d.arc(self._pts(box), start, end, fill=fill, width=max(1, int(round(width * self.s))))


def _lum01(c):
    c = rgb(c) if isinstance(c, str) else c
    return (0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]) / 255.0


def bg_polish(img, ambient, need=1.0, clarity=True):
    """Open up the shadows (they pick up the scene's own ambient colour, like real shadow fill), brighten, and add
    local contrast and crisp detail. need: 1 for a dark scene, less for one that is already bright."""
    k = BG_TONE
    has_a = img.mode == "RGBA"
    arr = np.asarray(img, dtype=np.float32)
    col = arr[..., :3]
    if clarity and need > 0.05:
        pic = Image.fromarray(np.clip(col, 0, 255).astype(np.uint8))
        pic = pic.filter(ImageFilter.UnsharpMask(radius=26, percent=int(120 * k["clarity"] * need), threshold=0))     # local contrast
        pic = pic.filter(ImageFilter.UnsharpMask(radius=1.3, percent=int(130 * k["sharp"] * max(need, 0.5)), threshold=2))   # crisp fine detail
        col = np.asarray(pic, dtype=np.float32)
    lum = (col @ np.array([0.299, 0.587, 0.114], dtype=np.float32)) / 255.0
    dk = (1.0 - np.clip(lum, 0, 1)) ** 2.4
    amb = np.asarray(ambient, dtype=np.float32)
    amb = amb / max(1.0, float(amb.max())) * 0.8 + 0.2                          # a tint, never pure black or one-colour
    sh = k["shadows"] * need
    col = col * (1.0 + sh * 0.95 * dk[..., None]) + amb[None, None, :] * (sh * 46.0) * dk[..., None]
    col = col * (1.0 + (k["bright"] - 1.0) * need)
    col = np.where(col > 215, 215 + (col - 215) * 0.6, col)                     # a soft shoulder, so highlights do not burn
    out = np.clip(col, 0, 255).astype(np.uint8)
    if has_a:
        out = np.dstack([out, arr[..., 3].astype(np.uint8)])
        return Image.fromarray(out, "RGBA")
    return Image.fromarray(out)


class World:
    PW = 2160  # the repeating width of the scrolling layers

    def __init__(self, place, seed=0, weather=None):
        weather = weather or {}
        self.st = dict(PLACES.get(place, DEFAULT_PLACE))
        self.fog = weather.get("fog", self.st.get("fog", 0.0))
        self.storm = weather.get("storm", self.st.get("storm", False))
        self.push, self.shake, self.tilt, self.focus_y = 0.0, 0.0, 0.0, 0.62
        self.skyfx = weather.get("sky")
        self.aurora = weather.get("aurora", "green")
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
            sky_a = np.asarray(sky, dtype=np.float32)
            yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
            dist = np.sqrt((xx - sx) ** 2 + (yy - 520) ** 2)
            glow = np.clip(1.0 - dist / (sr * 4.2), 0, 1) ** 2.4 * 0.55   # soft halo, no hard rings
            core = np.clip((sr * 1.0 - dist) / 3.0, 0, 1)                    # anti-aliased disc edge
            cc = np.array(rgb(c), dtype=np.float32)
            sky_a = sky_a * (1 - glow[..., None]) + cc * glow[..., None]
            sky_a = sky_a * (1 - core[..., None]) + cc * 0.96 * core[..., None]
            sky = Image.fromarray(sky_a.clip(0, 255).astype(np.uint8))
            d = ImageDraw.Draw(sky)
            if fx in ("fullmoon", "bluemoon") or sun in ("#f6f1d6", "#e8e8d0", "#dfe2cf", "#ff4a3a"):  # craters
                for ox, oy, rr in ((-40, -30, 22), (30, 20, 30), (-10, 55, 16), (45, -45, 14)):
                    d.ellipse((sx + ox - rr, 520 + oy - rr, sx + ox + rr, 520 + oy + rr), fill=mix(c, st["sky"][0], 0.10))
        self.sun_xy = (sx, 520, sr)
        if st.get("stars") or st["sky"][0] in ("#05060f", "#000000", "#2f3e25", "#3a0d0d", "#0a1030"):
            S3 = 3                                                              # stars: drawn at 3x so they are round and soft
            star_im = Image.new("RGBA", (W * S3, 900 * S3), (0, 0, 0, 0))
            sdr = ImageDraw.Draw(star_im)
            for _ in range(160):
                x, y = rnd.randint(0, W), rnd.randint(0, 900)
                big_star = rnd.random() < 0.1
                rs_ = random.Random(x * 7919 + y)
                r_ = (1.7 + rs_.random() * 0.9) if big_star else (0.6 + rs_.random() * 0.8)
                br = int(150 + 105 * rs_.random())
                cx_, cy_ = x * S3, y * S3
                sdr.ellipse((cx_ - r_ * 2.8 * S3, cy_ - r_ * 2.8 * S3, cx_ + r_ * 2.8 * S3, cy_ + r_ * 2.8 * S3), fill=(200, 210, 255, 26 if big_star else 12))
                sdr.ellipse((cx_ - r_ * S3, cy_ - r_ * S3, cx_ + r_ * S3, cy_ + r_ * S3), fill=(min(255, br + 20), min(255, br + 20), 255, 255))
                if big_star:
                    sdr.line((cx_ - r_ * 4.4 * S3, cy_, cx_ + r_ * 4.4 * S3, cy_), fill=(230, 236, 255, 120), width=max(1, S3 // 2))
                    sdr.line((cx_, cy_ - r_ * 4.4 * S3, cx_, cy_ + r_ * 4.4 * S3), fill=(230, 236, 255, 120), width=max(1, S3 // 2))
            star_im = star_im.reduce(S3)
            sky = sky.convert("RGBA")
            sky.alpha_composite(star_im, (0, 0))
            sky = sky.convert("RGB")
        sky_a2 = np.asarray(sky, dtype=np.float32)                              # a whisper of noise: smooth gradients never band
        sky_a2 = sky_a2 + np.random.RandomState(seed * 31 + 7).normal(0.0, 1.15, sky_a2.shape[:2])[..., None].astype(np.float32)
        sky = Image.fromarray(np.clip(sky_a2, 0, 255).astype(np.uint8))
        self.sky = sky
        self.clouds = None
        try:
            dark = sum(rgb(st["sky"][0])) < 260
            if dark and isinstance(sun, str) and sun not in ("two", "planet", "hole", "eclipse") and fx not in ("redsky", "virus", "thunder"):
                self.clouds = self._cloud_strip(sx)
        except Exception:
            self.clouds = None
        # layers
        sky_lo_ = rgb(st["sky"][1])
        self.hz = self._enrich(self._layer(mix(st["far"], st["sky"][1], 0.55), GROUND - 700, 520, 0.0, far=True), 0.55, "hz")
        self.far = self._enrich(self._layer(st["far"], GROUND - 520, 520, 0.0, far=True), 0.30, "far")
        self.mid = self._enrich(self._layer(mix(st["far"], st["near"], 0.5), GROUND - 390, 420, 0.5, far=True), 0.16, "mid")
        self.near = self._enrich(self._layer(st["near"], GROUND - 260, 330, 1.0, far=False), 0.0, "near")
        self.ground = self._ground_tile()
        self._polish_bg()
        self.parts = [[rnd.uniform(0, W), rnd.uniform(0, H), rnd.uniform(0.5, 1.5), rnd.uniform(0, 6.28)] for _ in range(70)]

    def _polish_bg(self):
        """Shadows opened up, brighter, clearer: applied once to the finished background pictures (see bg_polish)."""
        st = self.st
        Lm = (_lum01(st["sky"][0]) + _lum01(st["sky"][1]) + _lum01(st["far"]) + _lum01(st["ground"])) / 4.0
        need = float(min(1.0, max(0.22, (0.50 - Lm) / 0.36)))                  # dark scenes get the full lift, bright ones little
        self.bg_need = need
        amb = rgb(st["sky"][1])
        if getattr(self, "sky", None) is not None:
            self.sky = bg_polish(self.sky, amb, need, clarity=False)
        for name in ("hz", "far", "mid", "near"):
            lay = getattr(self, name, None)
            if lay is not None:
                top = lay.top
                lay = bg_polish(lay, amb, need)
                lay.top = top
                setattr(self, name, lay)
        for name in ("ground", "ground_tile"):
            g_ = getattr(self, name, None)
            if g_ is not None:
                setattr(self, name, bg_polish(g_, amb, need))

    def _noise(self, h, w, blur, seed):
        """Smooth noise that tiles sideways (so scrolling layers never show a seam)."""
        rs = np.random.RandomState(seed)
        n = Image.fromarray((rs.rand(h, w) * 255).astype(np.uint8))
        three = Image.new("L", (w * 3, h))
        for i in range(3):
            three.paste(n, (i * w, 0))
        three = three.filter(ImageFilter.GaussianBlur(blur))
        a = np.asarray(three.crop((w, 0, 2 * w, h)), dtype=np.float32) / 255.0
        a = (a - a.mean()) / (a.std() + 1e-5)
        return a

    def _enrich(self, layer, haze, tag):
        """Give a flat silhouette layer body: a light-to-dark gradient, rock/earth texture, valley haze, and the
        place's own accents (lava veins, snow caps, rock strata)."""
        st, PW = self.st, self.PW
        a = np.asarray(layer, dtype=np.float32)
        hh = a.shape[0]
        alpha = a[..., 3]
        rgb_ = a[..., :3]
        top = np.argmax(alpha > 8, axis=0).astype(np.float32)               # the ridge line, column by column
        yy = np.arange(hh, dtype=np.float32)[:, None]
        depth = np.clip((yy - top[None, :]) / 260.0, 0, 1)                    # 0 at the ridge, 1 deep inside the mass
        sky_lo = np.array(rgb(st["sky"][1]), dtype=np.float32)
        seed = __import__("zlib").crc32((tag + str(st["far"])).encode()) % 100000
        n1 = self._noise(hh, PW, 5, seed)
        n2 = self._noise(hh, PW, 1.6, seed + 1)
        n3 = self._noise(hh, PW, 0.7, seed + 2)
        tex = 1.0 + 0.06 * n1 + 0.035 * n2 + 0.022 * n3
        light_ = 1.22 - 0.55 * depth                                          # lit ridge, darker body
        col = rgb_ * (tex * light_)[..., None]
        fog = (haze + 0.30 * (1 - depth) ** 2 * (0.35 if tag != "near" else 0.2) + 0.22 * depth ** 3 * (0.5 if tag != "near" else 0.7))
        col = col * (1 - fog[..., None]) + sky_lo * fog[..., None]
        sil = st["sil"]
        rnd = random.Random(seed)
        if tag in ("far", "mid", "near"):
            if st.get("part") == "embers":                                    # glowing lava veins running down the slopes
                vein = Image.new("L", (PW, hh), 0)
                vd = ImageDraw.Draw(vein)
                for _ in range({"far": 10, "mid": 16, "near": 14}[tag]):
                    x = rnd.uniform(0, PW)
                    y = float(top[int(x) % PW]) + 4
                    pts = [(x, y)]
                    for _k in range(rnd.randint(5, 9)):
                        x += rnd.uniform(-22, 22)
                        y += rnd.uniform(18, 46)
                        pts.append((x, y))
                    for off in (0, PW):
                        vd.line([(px + off, py) for px, py in pts], fill=255, width=3 if tag == "near" else 2)
                vg = np.asarray(vein.filter(ImageFilter.GaussianBlur(1.2)), dtype=np.float32) / 255.0 * (alpha > 8)
                glow = np.asarray(vein.filter(ImageFilter.GaussianBlur(7)), dtype=np.float32) / 255.0 * (alpha > 8)
                lava = np.array([255, 120, 40], dtype=np.float32)
                col = col + lava * (vg * 0.95 + glow * 0.5)[..., None]
            if sil in ("peaks", "spikes"):                                    # snow caught on the ridge line
                cap = np.clip(1 - (yy - top[None, :]) / (40 + 12 * n1), 0, 1) * (alpha > 8)
                col = col * (1 - cap[..., None] * 0.8) + np.array([235, 242, 255], dtype=np.float32) * (cap[..., None] * 0.8)
            if sil in ("rocks", "dunes", "cones", "clouds"):                  # layered rock strata / wind ripples
                strata = (np.sin((yy + 6 * n1) * 0.34) > 0.82).astype(np.float32) * (alpha > 8) * 0.10
                col = col * (1 - strata[..., None]) + sky_lo * strata[..., None] * 0.6
        # a thin, bright edge where the ridge meets the sky
        edge = np.clip(1 - (yy - top[None, :]) / 5.0, 0, 1) * (alpha > 8)
        col = col + (np.array(rgb(st["sky"][1]), dtype=np.float32) * 0.35 + 25) * (edge[..., None] * (0.40 if tag != "near" else 0.24))
        out = np.dstack([np.clip(col, 0, 255), alpha]).astype(np.uint8)
        img = Image.fromarray(out, "RGBA")
        img.top = layer.top
        return img

    def _ground_tile(self, gh=None):
        """The ground: a gradient with soil texture, scattered stones, tufts, cracks, and a haze at the horizon.
        It is as wide as the scrolling layers so it can move under the creature's feet."""
        st, PW, rnd = self.st, self.PW, random.Random(self.rnd.random())
        gh = gh or (H - GROUND)
        g = np.zeros((gh, PW, 3), dtype=np.float32)
        base = np.array(rgb(st["ground"]), dtype=np.float32)
        ys = np.arange(gh, dtype=np.float32)[:, None]
        shade = 1.35 - 0.95 * np.clip(ys / 900.0, 0, 1)
        n1, n2 = self._noise(gh, PW, 14, 3), self._noise(gh, PW, 2.0, 4)
        g[:] = base[None, None, :] * (shade * (1 + 0.07 * n1 + 0.03 * n2))[..., None]
        sky_lo = np.array(rgb(st["sky"][1]), dtype=np.float32)
        hz = np.clip(1 - ys / 120.0, 0, 1) ** 1.6 * 0.55                       # haze lying along the horizon
        g = g * (1 - hz[..., None]) + sky_lo * hz[..., None]
        img = Image.fromarray(np.clip(g, 0, 255).astype(np.uint8))
        big = img.resize((PW * SS, gh * SS), Image.BILINEAR)                      # details are drawn at 2x and shrunk
        d = _SSDraw(ImageDraw.Draw(big, "RGBA"), SS)
        sil, part = st["sil"], st.get("part")
        lightc = tuple(int(min(255, c * 1.6 + 18)) for c in base)
        darkc = tuple(int(c * 0.45) for c in base)
        for _ in range(170):                                                 # stones: small far, bigger near
            y = rnd.uniform(14, gh - 10)
            k = 0.35 + 1.5 * (y / gh)
            x = rnd.uniform(0, PW)
            w_, h_ = rnd.uniform(8, 24) * k, rnd.uniform(4, 11) * k
            for off in (0, -PW):
                d.ellipse((x + off - 2, y + h_ * 0.45, x + off + w_ + 6, y + h_ * 1.30), fill=(0, 0, 0, 80))      # contact shadow
                d.ellipse((x + off, y, x + off + w_, y + h_), fill=darkc + (210,))
                d.ellipse((x + off + 1, y, x + off + w_ - 2, y + h_ * 0.55), fill=lightc + (120,))
        green = sil in ("trees", "pines", "reeds", "dead", "wheat", "graves") or st["ground"] in ("#040a09", "#080d20", "#08121a", "#070a14", "#0a0612", "#06080f")
        if green:                                                            # grass tufts
            tuft = tuple(int(min(255, c * 1.3 + 14)) for c in base) + (230,)
            for _ in range(240):
                y = rnd.uniform(10, gh - 6)
                k = 0.4 + 1.3 * (y / gh)
                x = rnd.uniform(0, PW)
                for off in (0, -PW):
                    for b in range(4):
                        d.line((x + off + b * 4 * k, y, x + off + b * 4 * k + rnd.uniform(-7, 7) * k, y - rnd.uniform(16, 38) * k), fill=tuft, width=max(1, int(2 * k)))
        if part == "embers":                                                 # glowing cracks in the scorched earth
            for _ in range(26):
                x, y = rnd.uniform(0, PW), rnd.uniform(20, gh - 20)
                pts = [(x, y)]
                for _k in range(rnd.randint(4, 8)):
                    x += rnd.uniform(-40, 40)
                    y += rnd.uniform(-10, 18)
                    pts.append((x, y))
                for off in (0, -PW):
                    d.line([(px + off, py) for px, py in pts], fill=(255, 90, 30, 90), width=9)
                    d.line([(px + off, py) for px, py in pts], fill=(255, 190, 90, 230), width=3)
        if part == "snow" or st["ground"] in ("#e3edf5", "#f4f7fb", "#afc9e6"):    # sparkling drifts
            for _ in range(140):
                x, y = rnd.uniform(0, PW), rnd.uniform(10, gh - 10)
                d.ellipse((x, y, x + 3, y + 2), fill=(255, 255, 255, 200))
        if sil == "dunes" or part == "dust":                                 # wind ripples
            for _ in range(60):
                x, y = rnd.uniform(0, PW), rnd.uniform(20, gh - 10)
                L_ = rnd.uniform(90, 260) * (0.4 + y / gh)
                for off in (0, -PW):
                    d.arc((x + off, y, x + off + L_, y + 26), 200, 340, fill=lightc + (70,), width=2)
        return big.reduce(SS).convert("RGB")

    def _cloud_strip(self, sx):
        """Thin, soft, wide clouds (tileable) that drift slowly across the night sky and catch the moonlight."""
        PW, rnd = self.PW, random.Random(self.rnd.random())
        hh = 560
        g = np.zeros((hh, PW), dtype=np.float32)
        for _ in range(11):
            cx, cy = rnd.uniform(0, PW), rnd.uniform(90, 470)
            wd, ht = rnd.uniform(260, 620), rnd.uniform(14, 34)
            for _k in range(3):
                ox = rnd.uniform(-wd * 0.35, wd * 0.35)
                yy, xx = np.ogrid[0:hh, 0:PW]
                dx = np.minimum(np.abs(xx - (cx + ox)), PW - np.abs(xx - (cx + ox)))
                g += np.exp(-((dx / (wd * 0.5)) ** 2 + ((yy - cy - rnd.uniform(-8, 8)) / ht) ** 2)).astype(np.float32) * rnd.uniform(0.25, 0.5)
        g = np.clip(g, 0, 1)
        sky_col = np.array(rgb(self.st["sky"][1]), dtype=np.float32)
        moon = np.array(rgb(self.st["sun"]), dtype=np.float32)
        col = np.clip(sky_col * 0.55 + moon * 0.25, 0, 255)
        arr = np.zeros((hh, PW, 4), dtype=np.uint8)
        arr[..., :3] = col.astype(np.uint8)
        arr[..., 3] = (g * 105).astype(np.uint8)
        img = Image.fromarray(arr, "RGBA")
        img.top = 60
        return img

    def _layer(self, color, top, height, density, far):
        PW, rnd, st = self.PW, self.rnd, self.st
        img = Image.new("RGBA", (PW * SS, height * SS), rgb(color) + (0,))       # drawn at 2x and shrunk: smooth edges
        d = _SSDraw(ImageDraw.Draw(img), SS)
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
        img = img.reduce(SS)
        img.top = top
        return img

    def frame(self, cam, t):
        """The background for one frame: cam is how far the world has scrolled."""
        im = self.sky.copy()
        if self.skyfx:
            sky_effects(im, self.skyfx, t, getattr(self, "sun_xy", (700, 520, 85)), getattr(self, "aurora", "green"))
        if getattr(self, "clouds", None) is not None:
            off = int(t * 9 + cam * 0.03) % self.PW   # slow drift
            for ox in (-off, -off + self.PW):
                if ox < W and ox + self.PW > 0:
                    im.paste(self.clouds, (ox, self.clouds.top), self.clouds)
        for layer, k in ((self.hz, 0.07), (self.far, 0.18), (self.mid, 0.36), (self.near, 0.6)):
            off = int(cam * k) % self.PW
            for ox in (-off, -off + self.PW):
                if ox < W and ox + self.PW > 0:
                    im.paste(layer, (ox, layer.top), layer)
        goff = int(cam * 1.0) % self.PW
        gw = self.ground
        im.paste(gw.crop((goff, 0, min(self.PW, goff + W), gw.size[1])), (0, GROUND))
        if goff + W > self.PW:
            im.paste(gw.crop((0, 0, goff + W - self.PW, gw.size[1])), (self.PW - goff, GROUND))
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
                d.ellipse((fx - 330, fy - 70, fx + 330, fy + 70), fill=((150, 220, 110, int(30 * self.fog)) if self.skyfx == "virus" else (190, 205, 225, int(24 * self.fog))))
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


# camera angles: (zoom, where the camera looks, tilt in degrees, sideways drift in px, stays on the creature)
_ANGLES = {"wide": (1.0, None, 0.0, 0, False),
           "low": (1.22, 0.80, 0.0, 0, True),          # the camera near the ground, looking up at a big creature
           "close": (1.42, 0.68, 0.0, 0, True),        # tight on the creature
           "high": (1.12, 0.74, 0.0, 0, False),        # from above: more ground
           "dutch": (1.12, None, 4.0, 0, False),       # a tilted, uneasy frame
           "drift": (1.16, None, 0.0, 90, False)}      # a slow sideways drift


_SINK = [None]                                    # tests only
HR_SCALE = float(os.environ.get("HR_SCALE", "2.0"))   # scenes are drawn with this many times more pixels per side; 1 = the old way
_HR_STRIPS = ("sky", "clouds", "hz", "far", "mid", "near", "ground")


def _hr_world_on(world, rs):
    """The scrolling background pictures, brought up to hi-res size once (and kept for the next scene in the same place)."""
    saved = {}
    cache = world.__dict__.setdefault("_hrc", {})
    for nm in _HR_STRIPS:
        lay = getattr(world, nm, None)
        if lay is None:
            continue
        saved[nm] = lay
        key = (nm, rs, id(lay))
        hr = cache.get(key)
        if hr is None:
            hr = _o_resize(lay, (int(round(lay.size[0] * rs)), int(round(lay.size[1] * rs))), Image.LANCZOS)
            if hasattr(lay, "top"):
                hr.top = lay.top
            _hr_reg(hr)
            cache[key] = hr
        setattr(world, nm, hr)
    return saved


def write_video(out, dur, painter, world, cwd=None, cam_dir=1, cam_fn=None):
    """painter(draw, t, u, cam) draws the actors. Frames are piped straight into ffmpeg.
    The scene is drawn hi-res (HR_SCALE times the pixels) and shrunk to the video size; if anything in a scene cannot be
    drawn that way, it is simply drawn the old way instead."""
    rs = HR_SCALE if HR_SCALE > 1.0 else 1.0
    if rs > 1.0:
        try:
            return _write_video(out, dur, painter, world, cwd, cam_dir, cam_fn, rs)
        except Exception as e:
            RSF[0] = 1.0
            if os.environ.get("HR_DEBUG"):
                import traceback
                traceback.print_exc()
            print(f"[toon] hi-res draw failed for {os.path.basename(str(out))} ({type(e).__name__}: {e}); drawing it normally", flush=True)
    return _write_video(out, dur, painter, world, cwd, cam_dir, cam_fn, 1.0)


def _write_video(out, dur, painter, world, cwd, cam_dir, cam_fn, rs):
    n = max(int(dur * FPS), 2)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{OW}x{OH}",
           "-r", str(FPS), "-i", "-", "-vf", f"scale={OW}:{OH}:flags=bicubic", "-c:v", "libx264",
           "-preset", "veryfast", "-crf", "15", "-pix_fmt", "yuv420p", "-an", out]
    if _SINK[0] is not None:                     # tests collect the finished frames instead of making a video
        proc = subprocess.Popen(["cat"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, cwd=cwd)
    else:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, cwd=cwd)
    _LIGHT_WORLD[0] = world
    REACT.update(t=-99.0, power=0.0)
    BODY.clear()
    _MOT.clear()
    _SCENE[0] = out
    track = []
    if rs != 1.0:
        try:
            world.frame(0.0, 0.0)           # let the world build whatever it builds on first use, at the normal size, before it is made big
        except Exception:
            pass
    EMERGE.update(dur=float(getattr(world, "emerge", 0.0) or 0.0), t0=None, last=-9.0, t=0.0)
    saved_strips = _hr_world_on(world, rs) if rs != 1.0 else {}
    try:
        for i in range(n):
            RSF[0] = rs
            t = i / FPS
            EMERGE["t"] = t
            u = i / max(n - 1, 1)
            cam = cam_fn(t) if cam_fn else t * WALK_PX * cam_dir   # cam_fn: the ground can slow down and stop
            im = world.frame(cam, t)
            d = ImageDraw.Draw(im, "RGBA")
            _BOXES.clear()
            _CLOSEUP[0] = False
            ov = painter(im, d, t, u, cam)
            if ov is not None:
                im = ov
            if rs != 1.0 and _psize(im) != _big():
                im = _up(im, rs)                          # a painter handed back an ordinary-size picture
            if CINEMA_ON() and not getattr(world, "no_cinema", False) and not _CLOSEUP[0]:
                im = world.cinema_front(im, cam, t)
            box = None
            if _BOXES:
                box = _Box([min(b[0] for b in _BOXES), min(b[1] for b in _BOXES), max(b[2] for b in _BOXES), max(b[3] for b in _BOXES)])
                box.parts = tuple(tuple(b) for b in _BOXES)
            zoom_now = 1.0
            push, shake = getattr(world, "push", 0.0), getattr(world, "shake", 0.0)
            pull = getattr(world, "pull", 0.0)  # a pull-back: the camera starts close and drifts away
            ang = _ANGLES.get(getattr(world, "angle", None) or "wide", _ANGLES["wide"])   # (zoom, look height, tilt, drift, follows the creature)
            if push or shake or pull or ang[0] != 1.0:
                z = (1.0 + push * (u * u * (3 - 2 * u)) + pull * (1 - u * u * (3 - 2 * u)) + (0.05 if shake else 0.0)) * ang[0]
                cw, ch = W / z, H / z
                cx = W / 2 + shake * 0.5 * math.sin(t * 61) * 1.0 + ang[3] * (u - 0.5)
                fy = ang[1] if ang[1] is not None else getattr(world, "focus_y", 0.62)
                if ang[4] and box:                    # close and low shots stay on the creature
                    if getattr(world, "angle", None) == "close":          # keep the head (the front end) in frame
                        cx = (box[2] - cw * 0.42) if cam_dir >= 0 else (box[0] + cw * 0.42)
                    else:
                        cx = 0.78 * (box[0] + box[2]) / 2 + 0.22 * W / 2
                    fy = 0.55 * ((box[1] + box[3]) / 2 / H) + 0.45 * fy
                cy = min(max(H * fy + shake * 0.5 * math.cos(t * 53), ch / 2), H - ch / 2)
                cx = min(max(cx, cw / 2), W - cw / 2)
                if box:  # where that box ends up after the zoom
                    ax, ay = cx - cw / 2, cy - ch / 2
                    parts_ = tuple(((q[0] - ax) * z, (q[1] - ay) * z, (q[2] - ax) * z, (q[3] - ay) * z) for q in getattr(box, "parts", ()))
                    box = _Box([(box[0] - ax) * z, (box[1] - ay) * z, (box[2] - ax) * z, (box[3] - ay) * z])
                    box.parts = parts_
                zoom_now = z
                im = raw_resize(im, (OW, OH), Image.LANCZOS, box=tuple(v * rs for v in (cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2)))      # one smooth cut, sub-pixel exact
            if rs != 1.0 and _psize(im) != (OW, OH):
                im = raw_resize(im, (OW, OH), Image.LANCZOS)      # no zoom this frame: shrink the big picture to the video size
            RSF[0] = 1.0                                          # from here on it is an ordinary picture
            track.append((t, box))
            tilt = getattr(world, "tilt", 0.0) or ang[2]
            if tilt:
                a = math.radians(abs(tilt))
                k = 1.0 / (math.cos(a) + (im.size[1] / im.size[0]) * math.sin(a))
                im = im.rotate(tilt, resample=Image.BICUBIC)
                w2, h2 = im.size
                im = im.crop((int(w2 * (1 - k) / 2), int(h2 * (1 - k) / 2), int(w2 * (1 + k) / 2), int(h2 * (1 + k) / 2)))
            if im.size != (OW, OH):
                im = im.resize((OW, OH), Image.LANCZOS)
            up = zoom_now * OW / (W * rs)                  # how much the drawing was enlarged to make this frame
            if up > 1.02:                                  # enlarging softens edges: win the crispness back (more for tighter shots)
                im = im.filter(ImageFilter.UnsharpMask(radius=1.0 + 0.5 * (up - 1.0), percent=int(min(190, 20 + 115 * (up - 1.0))), threshold=3))
            if _SINK[0] is not None:
                _SINK[0].append(im.copy())
            else:
                proc.stdin.write(im.tobytes())
    finally:
        RSF[0] = 1.0
        for nm_, lay_ in saved_strips.items():
            setattr(world, nm_, lay_)
        EMERGE["dur"] = 0.0
        proc.stdin.close()
        rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"toon render failed (ffmpeg code {rc})")
    LAST_TRACK[:] = track


WALK_PX = 176.0                      # how fast the ground scrolls under a walking monster (px/s): heavy, deliberate
STEP = 1.9 * 2 * math.pi * 0.68     # step rate: about one step every 1.5 s for a big creature



def _step_fx(d, world, x, k, seed=1, size=1.0):
    """The ground answers every footfall, in a way that fits the place: dust on dry ground, a snow puff, water droplets in a swamp or sea,
    sparks on burning ground. k = 0..1 through the effect."""
    if k <= 0 or k >= 1:
        return
    st = getattr(world, "st", {}) or {}
    part, rnd = st.get("part"), random.Random(seed)
    y = GROUND + 14
    wet = part in ("bubbles", "fireflies") or st.get("sil") == "reeds"
    if part == "embers":
        for i in range(10):
            a = rnd.uniform(-1.1, 1.1)
            ex, ey = x + 120 * a * k * size, y - 150 * k * size * rnd.uniform(0.4, 1)
            d.ellipse((ex - 4, ey - 4, ex + 4, ey + 4), fill=(255, int(150 + 80 * rnd.random()), 40, int(230 * (1 - k))))
    elif wet:
        for i in range(12):
            a = rnd.uniform(-1.0, 1.0)
            h = 120 * rnd.uniform(0.5, 1.0) * size
            px = x + 150 * a * k * size
            py = y - 4 * h * k * (1 - k) * 2.2
            d.ellipse((px - 5, py - 5, px + 5, py + 5), fill=(205, 230, 245, int(210 * (1 - k))))
    elif part == "snow" or st.get("ground") in ("#e3edf5", "#f4f7fb", "#afc9e6"):
        for i in range(9):
            a = rnd.uniform(-1, 1)
            r = (14 + 30 * k) * size * rnd.uniform(0.7, 1.2)
            d.ellipse((x + 90 * a * k * size - r, y - 30 * k * size - r * 0.8, x + 90 * a * k * size + r, y - 30 * k * size + r * 0.7), fill=(240, 246, 252, int(170 * (1 - k))))
    else:
        _dust(d, x, y, k, seed=seed, size=size * 0.8)


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
        _gs = chim_ground(sp) if sp["kind"] == "chimera" else WALK_PX
        for n_, tc in enumerate(_fx_steps):                                         # each foot presses a mark into the ground; the ground carries it away behind
            ag = t - tc
            if 0 <= ag < 3.0:
                fx0 = (400 if facing == 1 else 680) + (-1) ** n_ * 70 * z * facing + 40 * facing - facing * ag * _gs
                al = int(120 * min(1.0, ag / 0.12) * (1 - ag / 3.0))
                d.ellipse((fx0 - 17 * z, GROUND + 34 - 3 * z, fx0 + 17 * z, GROUND + 34 + 5 * z), fill=(8, 6, 6, al))
                d.arc((fx0 - 17 * z, GROUND + 34 - 3 * z, fx0 + 17 * z, GROUND + 34 + 5 * z), 200, 340, fill=(190, 175, 150, int(al * 0.5)), width=2)
        draw_creature(d, sp, (400 if facing == 1 else 680) + sh, GROUND + 20, t * STEP * 0.5,
                      facing, z, {"t": t, "roar": roar, "moving": True})
        for n_, tc in enumerate(_fx_steps):                                         # the ground reacts to each footfall
            if 0 <= t - tc < 0.7:
                _step_fx(d, world, (400 if facing == 1 else 680) + (-1) ** n_ * 70 * z * facing + 40 * facing, (t - tc) / 0.7, seed=n_ + 3, size=z)
        if roar > 0.3:                                                              # a roar shakes loose a ring of dust / sparks
            _step_fx(d, world, (400 if facing == 1 else 680) - 90 * facing, (t * 1.7) % 1.0, seed=int(t * 9), size=1.1)
    try:
        _fx_steps = _contacts(sp, lambda t_: t_ * STEP * 0.5, dur, lambda t_: True)
    except Exception:
        _fx_steps = []
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
BANNED_BODIES = ("stalker",)      # designs that are never used any more (the drawing code stays, but a banned body is drawn as a beast)


def folk_creature(name, design=None):
    """A folklore monster. `design` picks its body shape, colour, eyes and features so that every legend
    gets its own unique monster: {"body", "palette", "eyes", "size", "horn", "spine", "eye_count"}."""
    design = design or {}
    sp = animal_spec(name)
    body = design.get("body") if design.get("body") in BODIES else "beast"
    if body in BANNED_BODIES:
        body = "beast"
    if body == "beast" and "SKELETAL_FOLK" in globals() and SKELETAL_FOLK:
        try:
            sk = skeletal_folk(name, design)
            if sk:
                return sk
        except Exception as e:
            print("skeletal_folk failed:", e)
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


STRIKE_OF = {"swarm": "swarm", "tendril": "suckers", "orb": "bite", "bloom": "thorns"}     # how each strange body kills; everything else uses claws
NO_BONES = ("swarm", "tendril", "orb", "bloom")                                             # no skeleton to show in the x-ray scene


def strike_of(sp):
    return STRIKE_OF.get((sp or {}).get("kind"), "claws")


def has_bones(sp):
    return (sp or {}).get("kind") not in NO_BONES


def claw_slash(d, cx, cy, k, size=1.0, style="claws"):
    """Three parallel diagonal claw strokes, white-hot at first and then red. Creatures without claws leave marks of their own kind."""
    k = max(0.0, min(1.0, k))
    if style != "claws":
        for j in range(7):
            x, y = cx + ((j * 5) % 7 - 3) * 34 * size, cy - 130 * size + (j % 4) * 80 * size
            r = (13 + (j % 3) * 6) * size * min(1.0, k * 3.0)
            col = (255, 245, 235, 255) if k < 0.3 else (190, 10, 16, int(255 * max(0.0, 1 - (k - 0.3) / 0.9)))
            if style in ("suckers", "bite"):
                d.ellipse((x - r * 1.6, y - r * 1.6, x + r * 1.6, y + r * 1.6), outline=col, width=max(2, int(7 * size)))
            d.ellipse((x - r, y - r, x + r, y + r), fill=col)
        return
    for j in range(3):
        x0, y0 = cx - 150 * size + j * 70 * size, cy - 230 * size
        x1, y1 = x0 + 190 * size * min(1, k * 2.2), y0 + 460 * size * min(1, k * 2.2)
        col = (255, 245, 235, 255) if k < 0.3 else (190, 10, 16, int(255 * (1 - (k - 0.3) / 0.9)))
        d.line((x0, y0, x1, y1), fill=col, width=int(26 * size * (1.2 - k)))


def attack_scene(out, dur, world, sp, cwd=None):
    """The creature closes in. The person turns, sees it and RUNS. It gallops after them, gains on them, leaps,
    and rakes its claws across their back: a flash, a spray of blood, and the person is gone. Only the lantern
    is left, dropped on a growing pool of blood. A companion, if there is one, keeps running and escapes."""
    if not has_bones(sp):          # an eye, a swarm, a flower or a drifting mass does not gallop: it has its own way of closing in
        return float_attack_scene(out, dur, world, sp, cwd)
    sp2 = dict(sp)
    sp2["bloody"] = False
    sp3 = dict(sp)
    sp3["bloody"] = True
    t_walk, t_run, t_leap, t_hit = 0.12, 0.20, 0.36, 0.42
    if PAUSE_REMAIN[0]:      # the narrator has finished: the strike starts exactly when her silence starts
        t_hit = min(0.8, max(0.3, (dur - PAUSE_REMAIN[0]) / dur))
        f_ = t_hit / 0.42
        t_walk, t_run, t_leap = t_walk * f_, t_run * f_, t_leap * f_
    _m0 = dur * (t_hit + (1 - t_hit) * 0.07)
    info = {"impact_at": dur * t_hit, "stalk_at": dur * t_walk, "maul_at": _m0, "maul_dur": dur * (1 - t_hit) * 0.57}
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
                claw_slash(d, hx + 10, GROUND - 200, (u - t_leap) / (t_hit - t_leap) * 0.55, style=strike_of(sp))
            if u > t_walk:
                beat = max(0.0, math.sin(t * 9 + 3)) ** 6 * 0.2 * min(1, (u - t_walk) / 0.12)
                return red_wash(im, beat + (0.08 if running else 0.0))
            return
        k = (u - t_hit) / (1 - t_hit)
        if k < 0.07:  # jump-scare: a few frames of the monster's face, blood red, right at the impact
            closeup(im, d, sp3, 0.0, 0.3, 2.3, 2.7, roar=1.0, shake=12, tt=t)
            return red_wash(im, 0.4)
        if k < 0.64:      # the camera goes in close: claws and teeth through cloth and skin
            _BOXES.append((100, 540, 980, 1240))
            return maul_frame(im, t, (k - 0.07) / (0.64 - 0.07), sp, (44, 50, 82), "cloth", seed=5)
        k = 0.64 + (k - 0.64) * 0.55
        sx = 16 * math.sin(t * 90) * max(0.0, 1 - k * 4)
        sy = 12 * math.cos(t * 80) * max(0.0, 1 - k * 4)
        blood_pool(d, hx - 20, GROUND + 26, min(1, k * 2.2))
        lantern_on_ground(d, hx + 150, GROUND + 28, t, alive=max(0.0, 1.0 - max(0.0, k - 0.6) * 2.5))
        draw_creature(d, sp3, hx - 330 + sx, GROUND + 20 + sy, ph, 1, 1.05, {"t": t, "moving": False, "roar": min(1, k * 3)})
        blood_burst(d, hx + 20, GROUND - 330, min(1, k * 1.6), seed=7)
        claw_slash(d, hx + 10, GROUND - 200, 0.55 + k * 1.2, style=strike_of(sp))
        if k < 0.14:
            return Image.blend(im, Image.new("RGB", im.size, (255, 255, 255)), 0.6 * (1 - (k - 0.07) / 0.07))
        if k < 0.35:
            return red_wash(im, 0.5 * (1 - k / 0.35))
    world.shake = 7.0
    write_video(out, dur, paint, world, cwd, cam_dir=2.2)
    return info


AIR_KINDS = ("orb", "tendril")          # these float; a flower creeps along the ground and a swarm flows over it


def _float_xy(sp, p, scale, t, cx_mid):
    """Where to put the creature so that its face / middle is at the point (cx_mid, height) on the path.
    Returns feet x and feet y for draw_creature. p is how far along the approach it is, 0 (far away) to 1 (on top of the person)."""
    kind = sp.get("kind")
    hxo, hyo = HEAD_AT.get(kind, (60, -350))
    s_eff = 1.4 * scale * sp.get("scale", 1.0) * KIND_SCALE.get(kind, 1.0)
    if kind in AIR_KINDS:                                   # high in the sky when far away, sinking towards the person as it nears
        cy = -640 + 380 * p ** 1.3 + 12 * math.sin(t * 2.3)
        return cx_mid - hxo * s_eff + 14 * math.sin(t * 1.7), GROUND + cy - hyo * s_eff
    far = (1 - p) ** 2                                      # on the ground: far things sit higher on the screen
    return cx_mid - hxo * s_eff + 8 * math.sin(t * 1.9), GROUND + 20 - 150 * far + 6 * math.sin(t * 3.1)


def float_attack_scene(out, dur, world, sp, cwd=None):
    """For a creature with no legs. It does not run and it is not just there beside the person: first it is a small red
    glow far away that the person cannot see, then it drifts closer, growing, while the person walks on; the person stops
    and turns to look, then runs; the creature swells, speeds up and lunges. Then the same kill close-up as every creature."""
    sp2 = dict(sp)
    sp2["bloody"] = False
    sp3 = dict(sp)
    sp3["bloody"] = True
    t_walk, t_run, t_leap, t_hit = 0.30, 0.42, 0.58, 0.64
    if PAUSE_REMAIN[0]:      # the narrator has finished: the strike starts exactly when her silence starts
        t_hit = min(0.8, max(0.35, (dur - PAUSE_REMAIN[0]) / dur))
        f_ = t_hit / 0.64
        t_walk, t_run, t_leap = t_walk * f_, t_run * f_, t_leap * f_
    _m0 = dur * (t_hit + (1 - t_hit) * 0.07)
    info = {"impact_at": dur * t_hit, "stalk_at": dur * t_walk, "maul_at": _m0, "maul_dur": dur * (1 - t_hit) * 0.57}
    world.shake = 0.0
    hx = 760
    air = sp.get("kind") in AIR_KINDS

    def at(u):
        """size and horizontal position of the creature's middle along its approach."""
        p = min(1.0, u / t_hit)
        scale = 0.16 * (1.55 / 0.16) ** (p ** 1.5)          # it grows the way something coming straight at you grows
        mid = 130 + (hx - 90 - 130) * p ** 1.7
        return p, scale, mid

    def paint(im, d, t, u, cam):
        ph = t * STEP * 0.5
        if u < t_hit:
            comp_x = hx + 170 + max(0.0, u - t_walk) * 1900
            running = u >= t_run
            pph = t * STEP * (1.0 if running else 0.5)
            p, scale, mid = at(u)
            fx_, fy_ = _float_xy(sp, p, scale, t, mid)
            roar = 0.0 if u < t_leap else min(1.0, (u - t_leap) / (t_hit - t_leap) * 2.0)
            # a soft red light that grows with it, and its shadow on the ground
            hxo, hyo = HEAD_AT.get(sp.get("kind"), (60, -350))
            s_eff = 1.4 * scale * sp.get("scale", 1.0) * KIND_SCALE.get(sp.get("kind"), 1.0)
            gcx, gcy = fx_ + hxo * s_eff, fy_ + hyo * s_eff
            for rr, aa in ((2.6, 14), (1.8, 22), (1.2, 34)):
                R_ = 150 * s_eff * rr / 1.4
                d.ellipse((gcx - R_, gcy - R_, gcx + R_, gcy + R_), fill=(255, 40, 52, int(aa * (0.5 + p))))
            if air:
                sh_w = 230 * s_eff / 1.4
                d.ellipse((gcx - sh_w, GROUND + 14, gcx + sh_w, GROUND + 14 + sh_w * 0.16), fill=(0, 0, 0, int(80 * min(1.0, 0.3 + p))))
            draw_companion(d, comp_x, GROUND + 20, pph, 1, 1.0)
            if u >= t_leap:                                  # in front of the person: it is on them
                if u < t_walk:
                    draw_human(d, hx, GROUND + 20, ph, 1, 1.0, cast=False)
                else:
                    draw_human(d, hx + 6 * math.sin(t * 61), GROUND + 20, pph, 1, 1.0, cast=False)
                draw_creature(d, sp2, fx_, fy_, ph + 0.7, 1, scale, {"t": t, "moving": True, "roar": roar})
                claw_slash(d, hx + 10, GROUND - 200, (u - t_leap) / (t_hit - t_leap) * 0.55, style=strike_of(sp))
            else:
                draw_creature(d, sp2, fx_, fy_, ph + 0.7, 1, scale, {"t": t, "moving": True, "roar": roar})
                if u < t_walk:
                    draw_human(d, hx, GROUND + 20, ph, 1, 1.0, cast=False)                      # walking on, unaware
                elif u < t_run:
                    draw_human(d, hx + 3 * math.sin(t * 40), GROUND + 20, 0.0, -1, 1.0, cast=False)   # stops, turns and looks
                else:
                    draw_human(d, hx + 6 * math.sin(t * 61), GROUND + 20, pph, 1, 1.0, cast=False)    # and runs
            if u > t_walk:
                beat = max(0.0, math.sin(t * 9 + 3)) ** 6 * 0.2 * min(1, (u - t_walk) / 0.12)
                return red_wash(im, beat + (0.08 if running else 0.0))
            return
        k = (u - t_hit) / (1 - t_hit)
        if k < 0.07:  # jump-scare: a few frames of its face, blood red, right at the impact
            closeup(im, d, sp3, 0.0, 0.3, 2.3, 2.7, roar=1.0, shake=12, tt=t)
            return red_wash(im, 0.4)
        if k < 0.64:      # the camera goes in close on the wound
            _BOXES.append((100, 540, 980, 1240))
            return maul_frame(im, t, (k - 0.07) / (0.64 - 0.07), sp, (44, 50, 82), "cloth", seed=5)
        k = 0.64 + (k - 0.64) * 0.55
        sx = 16 * math.sin(t * 90) * max(0.0, 1 - k * 4)
        sy = 12 * math.cos(t * 80) * max(0.0, 1 - k * 4)
        blood_pool(d, hx - 20, GROUND + 26, min(1, k * 2.2))
        lantern_on_ground(d, hx + 150, GROUND + 28, t, alive=max(0.0, 1.0 - max(0.0, k - 0.6) * 2.5))
        fx2, fy2 = _float_xy(sp, 1.0, 1.05, t, hx - 230)
        draw_creature(d, sp3, fx2 + sx, fy2 + sy, ph, 1, 1.05, {"t": t, "moving": False, "roar": min(1, k * 3)})
        blood_burst(d, hx + 20, GROUND - 330, min(1, k * 1.6), seed=7)
        claw_slash(d, hx + 10, GROUND - 200, 0.55 + k * 1.2, style=strike_of(sp))
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
    img = bg_polish(img, (70, 90, 130), 1.0)                                   # the same shadow lift / brightness / clarity as every background
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


def person_chase_scene(out, dur, world, sp, cwd=None):
    """The person runs for their life and the monster gallops after them, the camera shaking.
    (Named person_chase_scene because the hybrid animal hunt further down is called chase_scene.)"""
    def paint(im, d, t, u, cam):
        ph = t * STEP * 1.05
        jx, jy = 8 * math.sin(t * 61), 6 * math.cos(t * 53)
        gap = 700 - 270 * u                  # starts close enough that the creature's face is on screen from the first frame
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
            _aurora(im, t, getattr(world, "aurora", "green"), 0.75 + 0.5 * u)
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
    cut = JUMP_CUT[0] or 0.72
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
        floater = not has_bones(sp)        # an eye, a swarm, a flower or a drifting mass has no wings: it simply lifts and drifts upward
        wings = 0.0 if floater else (min(1.6, 0.25 + 3.4 * u) if u < lift0 else 1.6)
        crouch = 0.0 if floater else (0.06 * math.sin(math.pi * min(1.0, u / lift0)) if u < lift0 else 0.0)
        p = _smooth((u - lift0) / (1 - lift0)) if u > lift0 else 0.0
        rise = 1350 * p ** 1.3
        zoom = 1.0 - 0.45 * p
        if not floater and u > lift0 and u < lift0 + 0.35:  # dust thrown out around the take-off spot
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
        _land(im, 1450, "tornado", t, 6)
        d = ImageDraw.Draw(im, "RGBA")
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
        _land(im, 820, "sand", t, 8, ridges=False)
        d = ImageDraw.Draw(im, "RGBA")
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
        _land(im, 1250, "valley", t, 1)
        d = ImageDraw.Draw(im, "RGBA")
        for x0, sc in herd:
            x = ((x0 + t * 0.012) % 1.2) * 1200 - 60
            draw_creature(d, sau, x, 1240, t * STEP * 0.18, 1, sc * 0.6, {"t": t, "moving": True})
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
        _land(im, 1180, "erupt", t, 2)
        d = ImageDraw.Draw(im, "RGBA")
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
        _land(im, 1100, "birth", t, 3)
        d = ImageDraw.Draw(im, "RGBA")
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
        _land(im, 1300, "clash", t, 4)
        d = ImageDraw.Draw(im, "RGBA")
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
        _land(im, 1330, "reign", t, 5)
        d = ImageDraw.Draw(im, "RGBA")
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
    if "antlers" in extras:
        for off, wd, c2 in ((0.0, 0.12, bone), (0.45, 0.09, dark(bone, 0.8))):
            bx = -rx * (0.05 - off * 0.35)
            line([(bx, -ry * 0.8), (bx - rx * 0.25, -ry * 1.7), (bx - rx * 0.6, -ry * 2.5)], rx * wd, c2)
            for ax, ay, ex2, ey2 in ((-0.1, -1.4, 0.45, -2.0), (-0.3, -1.9, -0.75, -2.35), (-0.45, -2.2, -0.05, -2.9)):
                line([(bx + rx * ax, ry * ay), (bx + rx * ex2, ry * ey2)], rx * wd * 0.7, c2)
    if "ram" in extras:
        pts = [(-rx * 0.1 + rx * 0.55 * math.cos(a) * (1 - a / 12), -ry * 0.95 - rx * 0.55 * math.sin(a) * (1 - a / 12) + a * ry * 0.05) for a in [i * 0.4 for i in range(14)]]
        line([(-rx * 0.15, -ry * 0.8)] + pts, rx * 0.2, bone)
    if "twin" in extras:
        line([(-rx * 0.1, -ry * 0.8), (-rx * 0.7, -ry * 1.6), (-rx * 1.6, -ry * 1.8)], rx * 0.18, bone)
        line([(rx * 0.1, -ry * 0.8), (-rx * 0.3, -ry * 1.7), (-rx * 1.1, -ry * 2.2)], rx * 0.14, dark(bone, 0.75))
    if "crown" in extras:
        poly([(-rx * 0.6, -ry * 0.55), (-rx * 0.1, -ry * 1.45), (rx * 0.3, -ry * 0.8)], dark(col, 0.75))

    # ---- the skull
    E(0, 0, rx, ry, hcol)
    for k in range(max(0, int(hd.get("eye_count", 2)) - 2)):       # more eyes along the brow
        xe, ye = -rx * (0.05 + 0.24 * k), -ry * (0.72 - 0.1 * (k % 2))
        E(xe, ye, 9, 9, (12, 8, 16), rim=False)
        E(xe, ye, 6, 6, hd.get("eyecol", (255, 40, 30)), rim=False)
        E(xe, ye, 2, 4, (30, 6, 0), rim=False)
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
def _chim_ground0(sp):
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


def chim_ground(sp):
    """A quarter slower than the raw natural pace: monsters should feel heavy and deliberate."""
    return 0.75 * _chim_ground0(sp)


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
    t1, t2, t3, t4 = dur * 0.15, dur * 0.26, dur * 0.42, dur * 0.50   # stalk end, notice end, leap start, landing
    t_cut = dur * 0.56                                                 # then the camera cuts in close: the kill
    if PAUSE_REMAIN[0]:      # the narrator has finished: the close-up (and its sound) gets the silent window
        t_cut = min(dur * 0.85, max(dur * 0.35, dur - PAUSE_REMAIN[0]))
        f_ = t_cut / (dur * 0.56)
        t1, t2, t3, t4 = t1 * f_, t2 * f_, t3 * f_, t4 * f_
    Pf, m_at = _speed_profile([(t1, 0.55), (t2, 0.2), (t3, 2.1), (t4, 0.0), (dur + 1.0, 0.0)])
    s0 = 1.4 * sp.get("scale", 1.0) * KIND_SCALE.get("chimera", 1.0)
    reach_x, _ = _head_reach(sp)
    front = max(sp["bbox"][2], 100.0)                       # how far ahead of its middle the creature reaches
    back_ = max(-sp["bbox"][0], 100.0)
    # shrink the creature until the chase fits: tail end on screen, prey well ahead of the nose, room to run
    zc = _clip((1080 - 150 - 330) / ((front + back_) * s0 + 1.0), 0.5, 1.0)
    cx = int(90 + back_ * s0 * zc)

    def paint(im, d, t, u, cam):
        if t >= t_cut:
            _BOXES.append((100, 540, 980, 1240))
            return maul_frame(im, t, (t - t_cut) / max(dur - t_cut, 0.1), sp, pcol, "fur", seed=11)
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
    return {"zoom_at": t1, "lunge_at": t4 - 0.25, "land_at": t4, "steps": steps, "maul_at": t_cut,
            "impact_at": t_cut + 0.30 * (dur - t_cut)}


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
    A["far"], A["near"] = hazed(self.far, 0.14, 1.2), hazed(self.near, 0.05, 0.0)
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


# ================================================================ detailed land for the story scenes
# The story scenes (valley, eruption, clash, tornado ...) used to draw their land as flat shapes. They now share the same
# layered, hazed, textured scenery as the walking scenes, drawn once per scene and drifting very slowly.
LAND = {
    "valley": dict(sky=("#fab066", "#7a4a52"), far="#6a4048", near="#46303a", ground="#34241e", sil="reeds", sun=None, part="dust"),
    "erupt":  dict(sky=("#140808", "#b43414"), far="#3a1410", near="#200c0c", ground="#12080a", sil="cones", sun=None, part="embers"),
    "birth":  dict(sky=("#14060a", "#6a2012"), far="#321210", near="#1c0a0a", ground="#0c0506", sil="rocks", sun=None, part="embers"),
    "clash":  dict(sky=("#200c0c", "#7a2a18"), far="#341410", near="#22100e", ground="#1a0c0c", sil="cones", sun=None, part="embers"),
    "reign":  dict(sky=("#1a0a0a", "#6a2418"), far="#2c1210", near="#1a0c0c", ground="#140a0a", sil="rocks", sun=None, part="embers"),
    "tornado": dict(sky=("#1a2a20", "#607060"), far="#2c3a2c", near="#1c281c", ground="#1a2016", sil="wheat", sun=None, part="dust"),
    "emerge": dict(sky=("#0a0814", "#4a3a54"), far="#2a2234", near="#1a1422", ground="#1a1216", sil="dead", sun=None, part="dust"),
    "sand":   dict(sky=("#08102a", "#46608a"), far="#26303c", near="#1a222c", ground="#362e28", sil="rocks", sun=None, part="dust"),
}
_LAND_CACHE = {}


class _Scenery(World):
    """Only the drawing helpers of World, for scenes that bring their own sky."""
    def __init__(self, st, seed, ridges=True):
        self.st, self.rnd = dict(st), random.Random(seed)
        self.ridges = ridges
        if ridges:
            self.hz = self._enrich(self._layer(mix(st["far"], st["sky"][1], 0.5), GROUND - 700, 520, 0.0, far=True), 0.50, "hz")
            self.far = self._enrich(self._layer(st["far"], GROUND - 520, 520, 0.0, far=True), 0.28, "far")
            self.mid = self._enrich(self._layer(mix(st["far"], st["near"], 0.5), GROUND - 390, 420, 0.5, far=True), 0.14, "mid")
            self.near = self._enrich(self._layer(st["near"], GROUND - 260, 330, 1.0, far=False), 0.0, "near")
        self.ground_tile = self._ground_tile(1100)
        self._polish_bg()


def _land(im, y, kind, t=0.0, seed=1, ridges=True):
    """Layered ridges behind a textured ground that starts at row y."""
    key = (kind, seed, ridges)
    S = _LAND_CACHE.get(key)
    if S is None:
        S = _LAND_CACHE[key] = _Scenery(LAND[kind], seed, ridges)
    dy = y - GROUND
    if ridges:
        for layer, k in ((S.hz, 0.04), (S.far, 0.09), (S.mid, 0.17), (S.near, 0.30)):
            off = int(t * 7 * k * 10) % S.PW
            for ox in (-off, -off + S.PW):
                if ox < W and ox + S.PW > 0:
                    im.paste(layer, (ox, layer.top + dy), layer)
    gt = S.ground_tile
    off = int(t * 3) % S.PW
    im.paste(gt.crop((off, 0, min(S.PW, off + W), H - y)), (0, y))
    if off + W > S.PW:
        im.paste(gt.crop((0, 0, off + W - S.PW, H - y)), (S.PW - off, y))


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
    R = RSF[0] if _is_hr(base) else 1.0                  # hi-res drawing: pixel arrays hold R times more pixels than the 1080-based numbers
    if R != 1.0 and (st.get("xray") or st.get("tint")):
        raise RuntimeError("hi-res: x-ray / skin-tint scenes are drawn the normal way")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer, "RGBA")
    s = 1.4 * (scale if scale is not None else 1.0) * sp.get("scale", 1.0) * KIND_SCALE.get(sp["kind"], 1.0)
    if focus:
        ax, ay, tx, ty = focus
        cx, gy = tx - facing * s * ax, ty - s * ay
    _nb = len(_BOXES)
    _draw_creature_plain(ld, sp, cx, gy, phase, facing, scale, dict(st, noshadow=True), None)
    layer = _body_mechanics(layer, sp, cx, gy, facing, st, phase)      # weight: lean, squat and settle, feet stay planted
    bb = layer.getchannel("A").point(lambda v: 255 if v > 150 else 0).getbbox()
    if not bb:
        return
    del _BOXES[_nb:]                                   # the nominal box is replaced by the creature's REAL outline
    _motion_note(sp, st, bb)
    _BOXES.append((bb[0], bb[1], bb[2], bb[3]))
    pad = 30
    x0, y0 = max(int(bb[0]) - pad, 0), max(int(bb[1]) - pad, 0)
    x1, y1 = min(int(bb[2]) + pad, base.width), min(int(bb[3]) + pad, base.height)
    crop = layer.crop((x0, y0, x1, y1))
    A = np.asarray(crop.getchannel("A"), dtype=np.float32) / 255.0
    solid = (A > 0.6).astype(np.float32)
    C = np.asarray(crop.convert("RGB"), dtype=np.float32)
    if st.get("tint"):                                   # an ability on screen: the skin changes colour (same drawing, new colours)
        C = _apply_tint(C, A, x0, y0, st["tint"])
    dx0, dy0 = LIGHT["dir"]
    world = _LIGHT_WORLD[0]
    lc = np.array(_light_color(world) if world is not None else (150, 185, 255), dtype=np.float32)
    h, w = solid.shape
    # --- soft cast shadow (drawn first, under the creature) ---
    ground = int(gy + 18)
    if ground > y0 + 8:
        top = max(y0, 0)
        rows = max(int(round((ground - y0) * R)), 1)                 # (pixel work below is in real pixels: R per 1080-based unit)
        sil = _raw(Image.fromarray, (solid[:rows] * 255).astype(np.uint8), "L")
        H0 = _psize(sil)[1]
        sh_h = max(int(H0 * 0.20), int(6 * R))
        sil = _raw(sil.transpose, Image.FLIP_TOP_BOTTOM)
        sil = _raw(sil.resize, (_psize(sil)[0], sh_h), Image.BILINEAR)   # feet at row 0, head far away
        reach = 0.45 * H0 * abs(dx0 / dy0)                      # the shadow falls away from the light
        extra = int(reach) + int(8 * R)
        wide = _raw(Image.new, "L", (_psize(sil)[0] + 2 * extra, sh_h), 0)
        _raw(wide.paste, sil, (extra, 0))
        a_ = (reach / sh_h) * (1 if dx0 > 0 else -1)            # row j of the shadow moves sideways by a_ * j
        wide = _raw(wide.transform, _psize(wide), Image.AFFINE, (1, a_, 0, 0, 1, 0), Image.BILINEAR)
        wide = _raw(wide.filter, ImageFilter.GaussianBlur(max(4, 6 * s) * R))
        wide = _raw(wide.point, lambda v: int(v * (0.42 if sp["kind"] in ("bird", "float", "swim", "wraith") else 0.80)))
        if R != 1.0:
            _hr_reg(wide)
        sx = int(x0 - extra / R)
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
    shade = 1.0 - 0.58 * np.clip((proj + 0.05) / 1.0, 0, 1) - 0.16 * np.clip((yy / max(h, 1) - 0.6) / 0.4, 0, 1)
    if world is not None:       # one colour world: the creature picks up the scene's own tone and loses a little saturation
        try:
            tint = np.array(rgb(world.st["sky"][1]), dtype=np.float32)
            grey = C.mean(axis=2, keepdims=True)
            C = C * 0.94 + grey * 0.06
            C = C * 0.92 + tint * 0.08
        except Exception:
            pass
    C = C * shade[..., None]
    kpx = max(4, int(9 * s)) * R
    lit_edge = solid * (1.0 - _shift(solid, int(round(dx0 * kpx)), int(round(dy0 * kpx))))
    # the rim only where the surface really faces the light, and softened
    r = _raw(Image.fromarray((np.clip(lit_edge, 0, 1) * 255).astype(np.uint8), "L").filter, ImageFilter.GaussianBlur(1.3 * max(s, 0.6) * R))
    r = np.asarray(r, dtype=np.float32)[..., None] / 255.0
    C = 255 - (255 - C) * (1 - np.clip(r * 1.6, 0, 1) * (lc / 255.0))
    # a faint wide glow so the lit side reads as lit, not just outlined
    wide = _raw(Image.fromarray((np.clip(lit_edge, 0, 1) * 255).astype(np.uint8), "L").filter, ImageFilter.GaussianBlur(7 * max(s, 0.6) * R))
    wide = np.asarray(wide, dtype=np.float32)[..., None] / 255.0
    C = np.clip(C + wide * solid[..., None] * lc * 0.30, 0, 255)
    v = float(st.get("veil", sp.get("veil", 0.0)) or 0.0)
    if v > 0:       # suspense: before the reveal the creature is only a dark shape in the fog, with its eyes still glowing
        sat = (((C.max(axis=2) - C.min(axis=2)) > 95) & (C.max(axis=2) > 190))[..., None].astype(np.float32)
        dark = np.array(rgb(world.st["sky"][1]), dtype=np.float32) * 0.22 if world is not None else np.array((8, 8, 14), dtype=np.float32)
        C = (C * (1 - 0.90 * v) + dark * 0.90 * v) * (1 - sat) + C * sat
        A = A * (1 - 0.18 * v)
    C = 255.0 * (np.clip(C, 0, 255) / 255.0) ** 1.07              # deeper darks: the body keeps real shadow, not a milky grey
    out = np.zeros((h, w, 4), dtype=np.uint8)
    out[..., :3] = np.clip(C, 0, 255).astype(np.uint8)
    out[..., 3] = (A * 255).astype(np.uint8)
    body = _raw(Image.fromarray(out[..., :3], "RGB").filter, ImageFilter.UnsharpMask(radius=2.2 * R, percent=70, threshold=2))     # clearer forms
    edge_ = _raw(Image.fromarray(out[..., 3], "L").filter, ImageFilter.GaussianBlur(0.7 if R == 1.0 else 0.45 * R))      # softened silhouette edge: no stair-steps
    if R != 1.0:
        _hr_reg(body)
        _hr_reg(edge_)
    base.paste(body, (x0, y0), edge_)
    if st.get("xray"):
        _xray_overlay(base, solid, x0, y0, sp, cx, gy, s, facing, st["xray"])


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


# ================================================================ the skeleton look for every series
SKELETAL_FOLK = True


def skeletal_folk(name, design=None):
    """A folklore 'beast' drawn by the full-body engine: real legs that plant on the ground, exposed ribs, glowing cracks.
    The legend's own colours, eyes and size still apply, so every monster stays unique to its story."""
    design = design or {}
    if name not in SPECIES:   # an animal the engine doesn't know: use the nearest known body, never the old drawing
        import difflib
        near = difflib.get_close_matches(name, list(SPECIES), 1, 0.5)
        kind = ANIMALS.get(name, ("quad",))[0]
        pool = {"bird": ["owl", "eagle", "bat"], "swim": ["shark", "whale"], "snake": ["snake"], "bug": ["spider", "ant"],
                "crab": ["crab", "scorpion"]}.get(kind, ["wolf", "bear", "lion", "bull"])
        name = near[0] if near else pool[sum(map(ord, name)) % len(pool)]
    g = pure_design(name)
    g["hybrid"] = True
    g["glow"] = True
    g["ribs"] = g["torso"][1] > 120
    g["cracks"] = bool(design.get("cracks")) or True
    pal = PALETTES.get(design.get("palette"))
    if pal:
        main = mix(pal, (10, 8, 18), 0.35)
        g["pal"] = (tuple(main), tuple(mix(pal, (255, 255, 255), 0.18)), tuple(mix(main, (0, 0, 0), 0.4)))
    ec = EYES.get(design.get("eyes"), (255, 40, 30))
    g["head"] = dict(g["head"]); g["head"]["eyecol"] = tuple(ec)
    horn = design.get("horn")
    if horn in ("antlers", "ram", "twin"):
        g["head"]["extras"] = tuple(e for e in g["head"]["extras"] if e not in ("horns_bull",)) + (horn,)
    elif horn == "bull":
        g["head"]["extras"] = tuple(g["head"]["extras"]) + (("horns_bull",) if "horns_bull" not in g["head"]["extras"] else ())
    try:
        g["head"]["eye_count"] = max(2, min(6, int(design.get("eye_count", 2))))
    except Exception:
        g["head"]["eye_count"] = 2
    sp = chimera_spec(g)
    sp["name"] = name
    sp["horror"] = True
    sp["eye"] = tuple(ec)
    sp["scale"] *= {"small": 0.85, "huge": 1.2}.get(design.get("size"), 1.0)
    return sp


# ================================================================ the skeleton look for EVERY monster body
# Each horror body gets: bone-jointed limbs whose feet PLANT on the ground (they do not slide while the world scrolls),
# a visible ribcage and vertebrae, knuckled finger bones, skull marks. Every body part that touches the ground is
# drawn with contact: a dark pressure shadow under the foot, a puff of dust at the moment of landing, and a belly
# shadow along a serpent's coils.
_BONE = (218, 210, 188)
_SK = {"pen": None, "sp": None, "st": None}      # context for the planted-limb replacement
_ORIG_LIMB = limb
_GROUND_PX = WALK_PX                                # how fast the world scrolls in the folklore scenes (px/s)
_PHASE_RATE = STEP * 0.5                          # the engine's usual step rate (phase units per second)


def _ease3(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def _bone_seg(pen, pts, w, bone=_BONE, knobs=True):
    """A bone shaft with a darker outline, a lighter highlight and a knuckle at every joint."""
    pen.line(pts, w * 1.35, dark(bone, 0.35))
    pen.line(pts, w, bone)
    pen.line([(p[0], p[1] - w * 0.18) for p in pts], max(1.0, w * 0.28), light(bone, 0.5))
    if knobs:
        for q in pts:
            pen.dot(q[0], q[1], w * 0.95, dark(bone, 0.7))
            pen.dot(q[0], q[1], w * 0.72, bone)


def _contact(pen, x, y, w, dust=0.0, seed=0):
    """Where a foot or belly touches the ground: a dark pressure shadow, plus kicked-up dust as it lands."""
    pen.ell(x, y + 6, w * 1.25, w * 0.32, (6, 6, 12), rim=False)
    pen.ell(x, y + 4, w * 0.8, w * 0.2, (22, 20, 28), rim=False)
    if dust > 0.02:
        for k in range(3):
            pen.dot(x - 14 - k * 22 * dust, y - 6 - k * 10 * dust, w * (0.3 + 0.12 * k) * dust, (96, 90, 86))


def _ik2(hx, hy, fx, fy, l1, l2, bend):
    dx, dy = fx - hx, fy - hy
    d = math.hypot(dx, dy)
    reach = (l1 + l2) * 0.995
    if d > reach:
        k = reach / d
        fx, fy = hx + dx * k, hy + dy * k
        dx, dy, d = fx - hx, fy - hy, reach
    d = max(d, 1e-3)
    a = (l1 * l1 - l2 * l2 + d * d) / (2 * d)
    h = math.sqrt(max(l1 * l1 - a * a, 0.0))
    mx, my = hx + dx * a / d, hy + dy * a / d
    return (mx - bend * dy / d * h, my + bend * dx / d * h), (fx, fy)


def _foot_track(phase, hx, hy, l1, l2, duty, S, lift, moving):
    """Stance: the foot sits on the ground and travels backward at exactly the ground's speed. Swing: it lifts and reaches forward."""
    if not moving:
        return hx + 0.15 * S, 0.0, 1.0, 0.0
    p = (phase / (2 * math.pi)) % 1.0
    if p < duty:
        u = p / duty
        return hx + S * (0.5 - u), 0.0, 1.0, 0.0
    u = (p - duty) / (1 - duty)
    return hx + S * (-0.5 + _ease3(u)), -lift * math.sin(math.pi * u) ** 0.8, 0.0, u


def _planted_limb(pen, hx, hy, phase, l1, l2, w, col, amp=0.55, lift=0.7, claws=False):
    sp, st = _SK["sp"] or {}, _SK["st"] or {}
    moving = st.get("moving", True)
    duty = 0.58
    g_units = _GROUND_PX / max(pen.s, 1e-3)
    S = min(g_units * duty * 2 * math.pi / _PHASE_RATE, (l1 + l2) * 0.8)   # stride so that foot speed == ground speed
    fx, fy, planted, u = _foot_track(phase, hx, hy, l1, l2, duty, S, 45 + 70 * lift, moving)
    fy_ground = 0.0
    # the hip is above the ground by about l1+l2: aim the foot at the ground line
    (kx, ky), (fx, fy) = _ik2(hx, hy, fx, fy_ground + fy, l1, l2, 1.0)
    # flesh (dark, thin: the body wasted away) over a bone core with knuckles at hip, knee, ankle
    pen.line([(hx, hy), (kx, ky), (fx, fy)], w * 0.62, col)
    _bone_seg(pen, [(hx, hy), (kx, ky), (fx, fy)], max(5.0, w * 0.22))
    # the foot: a long bony toe-bone flat on the ground and claws
    ft = [(fx - 6, fy - 4), (fx + 30, fy - 2), (fx + 62, fy + 2)]
    _bone_seg(pen, ft, max(4.0, w * 0.17), knobs=False)
    if claws or sp.get("horror"):
        for k in range(3):
            pen.poly([(fx + 54 + k * 5, fy - 6 + k * 3), (fx + 84 + k * 7, fy + 3 + k * 2), (fx + 50 + k * 5, fy + 6 + k * 2)], (235, 230, 215), rim=False)
    if fy > -3.0:
        _contact(pen, fx + 24, 0.0, max(30.0, w * 0.7), dust=max(0.0, 1.0 - (((phase / (2 * math.pi)) % 1.0) / duty) * 5.0) if moving else 0.0)


def _ribcage(pen, x0, y0, x1, y1, n, hang, col_in=(18, 10, 14), bone=_BONE, w=6.0, curve=0.5):
    """An open ribcage seen from the side: a dark cavity, a spine, and curved ribs hanging from it."""
    pts = [(x0 + (x1 - x0) * i / (n - 1), y0 + (y1 - y0) * i / (n - 1)) for i in range(n)]
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2 + hang * 0.55
    pen.ell(cx, cy, abs(x1 - x0) / 2 + 8, hang * 0.62 + 8, col_in, rim=False)
    for (x, y) in pts:
        s = 1.0 - abs((x - cx) / (abs(x1 - x0) / 2 + 1e-3)) * 0.35
        h = hang * s
        _bone_seg(pen, [(x, y), (x + h * curve * 0.35, y + h * 0.55), (x + h * curve * 0.12, y + h)], w, bone, knobs=False)
    _bone_seg(pen, pts, w * 1.15, bone, knobs=True)


def _vertebrae(pen, pts, r, bone=_BONE):
    for i, (x, y) in enumerate(pts):
        rr = r * (1.0 - 0.45 * i / max(1, len(pts) - 1))
        pen.dot(x, y, rr * 1.2, dark(bone, 0.5))
        pen.dot(x, y, rr, bone)
        pen.poly([(x - rr * 0.6, y - rr * 0.4), (x + rr * 0.6, y - rr * 0.4), (x, y - rr * 2.2)], dark(bone, 0.85), rim=False)


def _skull_marks(pen, hx, hy, r, sp, st):
    """Exposed bone and cracks on a face."""
    pen.poly([(hx - r * 0.9, hy - r * 0.6), (hx - r * 0.2, hy - r * 1.05), (hx + r * 0.25, hy - r * 0.55), (hx - r * 0.35, hy - r * 0.35)], _BONE, rim=False)
    pen.line([(hx - r * 0.6, hy - r * 0.5), (hx - r * 0.3, hy - r * 0.1), (hx - r * 0.5, hy + r * 0.3)], 3, (24, 16, 20))
    pen.line([(hx + r * 0.1, hy - r * 0.6), (hx + r * 0.3, hy - r * 0.2)], 2.5, (24, 16, 20))


# ---------------------------------------------------------------- wrapper per body
def _skeletal(kind, orig, extra):
    def draw(pen, sp, ph, st):
        if not sp.get("horror") or sp.get("noskel"):
            return orig(pen, sp, ph, st)
        global limb
        _SK["pen"], _SK["sp"], _SK["st"] = pen, sp, st
        limb = _planted_limb
        try:
            orig(pen, sp, ph, st)
        finally:
            limb = _ORIG_LIMB
        extra(pen, sp, ph, st)
    return draw


def _sk_theropod(pen, sp, ph, st):
    moving, t = st.get("moving", True), st.get("t", 0.0)
    bob = (-abs(math.sin(ph)) * 14) if moving else math.sin(t * 2.2) * 5
    sway = math.sin(ph * 0.9) * 40 if moving else math.sin(t * 1.3) * 18
    _ribcage(pen, -90, -420 + bob, 150, -470 + bob, 6, 120)
    tp = [(-150 - i * 82, -335 + bob + 36 * i * (1 - i / 14.0) * 0.1 + sway * (i / 6.0) ** 1.4 + 6 * i) for i in range(7)]
    _vertebrae(pen, tp[1:], 14)
    _bone_seg(pen, [(255, -430 + bob), (295, -385 + bob), (330, -392 + bob)], 7)     # the tiny arm is only bone
    _skull_marks(pen, 440, -580 + bob, 60, sp, st)
    pen.line([(350, -530 + bob), (430, -520 + bob)], 3, (24, 16, 20))


def _sk_dragon(pen, sp, ph, st):
    moving, t = st.get("moving", True), st.get("t", 0.0)
    bob = (-abs(math.sin(ph)) * 10) if moving else math.sin(t * 2.0) * 6
    flap = math.sin(t * 3.0 + ph * 0.3) if (moving or sp.get("flying")) else 0.25 * math.sin(t * 1.4)
    _ribcage(pen, -110, -375 + bob, 120, -395 + bob, 6, 105)
    sx, sy = 40, -430 + bob
    base = 1.05 + 0.55 * flap
    prev = (sx, sy)
    for da, L in ((0.0, 560), (-0.42, 600), (-0.84, 540), (-1.24, 430)):      # the wing is a bat's arm: finger bones with knuckles
        a = base + da * 0.7
        tip = (sx - L * math.cos(a) * 0.82, sy - L * math.sin(a) * 0.95)
        mid = ((sx + tip[0]) / 2, (sy + tip[1]) / 2 - 18)
        _bone_seg(pen, [(sx, sy), mid, tip], 6)
    tp = [(-160 - i * 85, -320 + bob + 22 * i) for i in range(8)]
    _vertebrae(pen, tp[1:], 15)
    _skull_marks(pen, 380, -650 + bob, 55, sp, st)


def _sk_stalker(pen, sp, ph, st):
    moving, t = st.get("moving", True), st.get("t", 0.0)
    bob = (-abs(math.sin(ph)) * 8 if moving else math.sin(t * 2) * 3) + 110 * st.get("crouch", 0.0)
    _vertebrae(pen, [(-8 + 4 * i, -440 - i * 38 + bob) for i in range(6)], 9)
    _skull_marks(pen, 110, -760 + bob, 48, sp, st)


def _sk_hollow(pen, sp, ph, st):
    lean = 22 * math.sin(st.get("t", 0.0) * 0.9)
    _ribcage(pen, -16 + lean * 0.3, -650, 26 + lean * 0.4, -590, 5, 70, w=4.0, curve=0.3)


def _sk_owlman(pen, sp, ph, st):
    t, moving = st.get("t", 0.0), st.get("moving", True)
    amp = 1.0 if moving else 0.0
    bob = -abs(math.sin(ph)) * 8 * amp + 60 * st.get("crouch", 0.0)
    flap = math.sin(ph * 0.5 + t * 0.4) * 0.5 + 0.2 + (0.35 * math.sin(t * 5) if not moving else 0)
    wy = -440 + bob
    for k in range(4):         # wing struts: arm, wrist, long finger bones
        _bone_seg(pen, [(0, wy), (-120 - k * 30, wy - 120 - 60 * flap - k * 40), (-250 - k * 40, wy - 240 - 140 * flap - k * 60)], 5)
    _ribcage(pen, -40, -440 + bob, 40, -400 + bob, 5, 120, w=4.5)
    _skull_marks(pen, 40, -560 + bob, 70, sp, st)


def _sk_djinn(pen, sp, ph, st):
    t = st.get("t", 0.0)
    bob = -150 + 24 * math.sin(t * 1.8 + ph * 0.1)
    _ribcage(pen, -70, -500 + bob, 70, -470 + bob, 6, 130)
    for sgn in (1, -1):
        sway = 20 * math.sin(t * 2.3 + sgn)
        _bone_seg(pen, [(sgn * 110, -520 + bob), (sgn * 190 + 20, -380 + bob + sway), (sgn * 150 + 60, -230 + bob + sway * 1.5)], 8)


def _sk_crawler(pen, sp, ph, st):
    t = st.get("t", 0.0)
    sway = 10 * math.sin(t * 1.3)
    _vertebrae(pen, [(-70, -590 + i * 36 + sway * 0.3) for i in range(6)], 8)
    _bone_seg(pen, [(90, -520 + sway * 0.3), (170, -330), (150, -90 + 60)], 6)


def _sk_serpent(pen, sp, ph, st):
    """Bones along the whole body, and where the belly lies on the ground it presses a dark contact shadow."""
    t = st.get("t", 0.0)
    moving = st.get("moving", True)
    rise = 330 - 70 * st.get("crouch", 0.0) + (0 if moving else 14 * math.sin(t * 2.2))
    n = 18
    pts = []
    for i in range(n + 1):
        u = i / n
        sm = max(0.0, (u - 0.55) / 0.45)
        sm = sm * sm * (3 - 2 * sm)
        pts.append((-380 + u * 450 + (60 * sm), -34 + 26 * math.sin(ph * 1.6 - u * 11) * (1 - sm) - sm * rise))
    for i in range(n - 2):                       # the vertebral column with a pair of ribs at every segment
        x, y = pts[i]
        w = 82 * (0.35 + 0.65 * math.sin(min(1, (i / n) * 1.15) * math.pi / 2))
        if i % 2 == 0:
            _bone_seg(pen, [(x, y - 4), (x + 10, y + w * 0.28), (x + 5, y + w * 0.42)], 4.5, knobs=False)
        pen.dot(x, y - 6, max(5.0, w * 0.09), _BONE)
        if y > -62:                                # lying on the ground: a contact shadow under the belly
            pen.ell(x, 6, w * 0.7, 9, (6, 6, 12), rim=False)
    hx, hy = pts[-1]
    _skull_marks(pen, hx + 25, hy - 60, 48, sp, st)
    for k in range(2):                           # long bone fangs
        _bone_seg(pen, [(hx + 90 + k * 22, hy - 40), (hx + 96 + k * 22, hy - 10)], 5, knobs=False)


def _sk_wraith(pen, sp, ph, st):
    t, moving = st.get("t", 0.0), st.get("moving", True)
    bob = -60 + 20 * math.sin(t * 2.2 + ph * 0.1) - 40 * st.get("crouch", 0.0)
    _ribcage(pen, -30, -440 + bob, 60, -400 + bob, 5, 120, w=4.5, curve=0.3)
    for sgn, base in ((1, -450), (-1, -480)):    # radius and ulna, knuckles, finger joints
        p2 = (150, base + 20 + bob + 14 * math.sin(ph + sgn))
        p3 = (300, base + 60 + bob + 20 * math.sin(ph * 1.3 + sgn))
        _bone_seg(pen, [(40, base + bob), p2, p3], 6)
        _bone_seg(pen, [(40, base + 14 + bob), (p2[0], p2[1] + 12), (p3[0], p3[1] + 6)], 3.5, knobs=False)
        for k in range(4):
            a = (p3[0] + 30 + k * 4, p3[1] - 12 + k * 8)
            _bone_seg(pen, [p3, a, (a[0] + 30, a[1] + 10 + k * 3)], 3.5)
    _skull_marks(pen, 30, -560 + bob, 70, sp, st)


def _sk_arachnid_leg(pen, sp, ph, st, by, i, side):
    """One spider leg rebuilt: a jointed bone leg whose tip plants on the ground in alternating groups (the tetrapod
    gait real spiders use), with a contact shadow under every planted tip."""
    moving = st.get("moving", True)
    g_units = _GROUND_PX / max(pen.s, 1e-3)
    duty = 0.6
    S = min(g_units * duty * 2 * math.pi / _PHASE_RATE, 150.0)
    hx = 30 + (i - 1.5) * 52
    nominal = hx + (i - 1.5) * 105 + 45
    cyc = ph + (i % 2) * math.pi + side * 0.5 * math.pi
    fx, fy, planted, u = _foot_track(cyc, nominal, by, 250, 250, duty, S, 70, moving)
    (kx, ky), (fx, fy) = _ik2(hx, by, fx, fy, 270, 270, -1.0)
    ww = 10 if side else 7
    col = sp["color"] if side else dark(sp["color"], 0.6)
    pen.line([(hx, by), (kx, ky), (fx, fy)], ww * 1.3, col)
    _bone_seg(pen, [(hx, by), (kx, ky), (fx, fy)], ww * 0.55)
    pen.poly([(fx - 8, fy), (fx + 18, fy + 4), (fx + 2, fy - 22)], (235, 230, 215), rim=False)
    if fy > -3.0:
        _contact(pen, fx, 0.0, 26.0, dust=0.0)


def _sk_arachnid(pen, sp, ph, st):
    moving = st.get("moving", True)
    t = st.get("t", 0.0)
    by = -300 + 70 * st.get("crouch", 0.0) + (-abs(math.sin(ph * 2)) * 8 if moving else math.sin(t * 2.2) * 3)
    _ribcage(pen, -190, by - 10, -80, by - 10, 6, 90, w=4.5, curve=0.2)       # across the swollen abdomen
    _vertebrae(pen, [(-60 - i * 18, by - 50 + i * 4) for i in range(4)], 8)


def _install_skeletons():
    table = {"theropod": _sk_theropod, "dragon": _sk_dragon, "stalker": _sk_stalker, "hollow": _sk_hollow,
             "owlman": _sk_owlman, "djinn": _sk_djinn, "crawler": _sk_crawler, "serpent": _sk_serpent,
             "wraith": _sk_wraith}
    for kind, extra in table.items():
        KINDS[kind] = _skeletal(kind, KINDS[kind], extra)

    global _ARACH_LEG
    _ARACH_LEG = _sk_arachnid_leg
    orig_arach = KINDS["arachnid"]

    def draw_arachnid_skel(pen, sp, ph, st):
        orig_arach(pen, sp, ph, st)
        if sp.get("horror") and not sp.get("noskel"):
            _sk_arachnid(pen, sp, ph, st)
    KINDS["arachnid"] = draw_arachnid_skel


_install_skeletons()


# ================================================================ story event scenes
# One scene for each thing a disaster / folklore narration can actually SAY: the tide drains, somebody checks the logs
# in a control tower, a radio call fails, the harbour is swallowed, something rises out of the trench, eyes stare
# through the glass, a giant crests the ridge. The storyboard picks these by what the sentence says.
def eye_total(sp, default=2):
    for v in (sp.get("eyes"), (sp.get("g") or {}).get("head", {}).get("eye_count") if isinstance(sp.get("g"), dict) else None):
        try:
            if v:
                return max(2, min(6, int(v)))
        except Exception:
            pass
    return default


def _stars_bg(d, rnd_seed, n=90, ymax=900, t=0.0, hide=None):
    rnd = random.Random(rnd_seed)
    for _ in range(n):
        x, y, r = rnd.uniform(0, W), rnd.uniform(40, ymax), rnd.uniform(1.2, 3.0)
        if hide and hide(x, y):
            continue
        a = int(140 + 90 * math.sin(t * 2 + x))
        d.ellipse((x - r, y - r, x + r, y + r), fill=(235, 240, 255, max(60, min(255, a))))


def _figure(d, x, y, s, col=(8, 8, 14), raised=0.0, lean=0.0, rim=(120, 150, 190)):
    """A person seen from the side, feet at (x, y). `raised` lifts the near arm to the face (a radio, a phone)."""
    hx = x + lean * 18 * s
    for dx in (-1, 1):
        d.line([(x + dx * 10 * s, y - 80 * s), (x + dx * 14 * s, y)], fill=col + (255,), width=int(16 * s))
    d.polygon([(x - 26 * s, y - 85 * s), (x + 26 * s, y - 85 * s), (hx + 22 * s, y - 175 * s), (hx - 22 * s, y - 175 * s)], fill=col + (255,))
    d.ellipse((hx - 19 * s, y - 218 * s, hx + 19 * s, y - 178 * s), fill=col + (255,))
    hand = (hx + (20 + 12 * raised) * s, y - (110 + 70 * raised) * s)
    d.line([(hx + 6 * s, y - 165 * s), (hx + 34 * s, y - 130 * s), hand], fill=col + (255,), width=int(12 * s))
    d.line([(hx - 22 * s, y - 175 * s), (hx - 22 * s, y - 175 * s)], fill=rim + (255,), width=2)
    d.line([(hx + 19 * s, y - 214 * s), (hx + 19 * s, y - 184 * s)], fill=rim + (170,), width=max(2, int(3 * s)))   # rim light on the face edge
    return hand


def _glow_eyes(d, cx, cy, n, spread, r, col, t, seed=0, appear=1.0):
    """n glowing eyes in a row (or two rows), blinking one after another."""
    if appear <= 0.01:
        return
    rows = 1 if n <= 3 else 2
    per = [n] if rows == 1 else [n // 2 + n % 2, n // 2]
    k = 0
    for ri, cnt in enumerate(per):
        for j in range(cnt):
            x = cx + (j - (cnt - 1) / 2) * spread
            y = cy + ri * spread * 0.55 - (spread * 0.2 if ri == 0 and rows == 2 else 0)
            ph = (t * 0.6 + k * 0.17 + seed) % 1.0
            lid = 1.0 - min(1.0, abs(ph - 0.93) / 0.03) if ph > 0.9 else 0.0
            a = appear * (1 - lid)
            for rr, al in ((r * 4.2, 22), (r * 2.6, 46), (r * 1.7, 90)):
                d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=col + (int(al * a),))
            d.ellipse((x - r, y - r * (1 - 0.6 * lid), x + r, y + r * (1 - 0.6 * lid)), fill=tuple(min(255, c + 40) for c in col) + (int(255 * a),))
            d.ellipse((x - r * 0.28, y - r * 0.8, x + r * 0.28, y + r * 0.8), fill=(10, 8, 12, int(255 * a)))
            k += 1


def _sea_strip(d, y0, y1, t, col=(10, 36, 66), foam=True):
    xs = list(range(0, W + 20, 20))
    pts = [(x, y0 + 8 * math.sin(x * 0.02 + t * 1.6) + 4 * math.sin(x * 0.07 + t * 3)) for x in xs]
    d.polygon(pts + [(W, y1), (0, y1)], fill=col + (255,))
    if foam:
        d.line(pts, fill=(215, 230, 240, 200), width=6)
    return pts


# ---------------------------------------------------------------- the tide drains and leaves fish on the mud
def retreat_scene(out, dur, world, sp, cwd=None):
    sky = _gradient((6, 10, 28), (38, 58, 96), 900)
    rnd = random.Random(5)
    fish = [(rnd.uniform(60, W - 60), rnd.uniform(1280, 1800), rnd.uniform(0.7, 1.3), rnd.uniform(0, 6)) for _ in range(16)]
    hits = [dur * 0.4, dur * 0.7]
    world.push, world.shake = 0.1, 2.0

    def paint(im, d, t, u, cam):
        im.paste(sky, (0, 0))
        d = ImageDraw.Draw(im, "RGBA")
        _stars_bg(d, 3, 80, 700, t)
        _moon(d, 780, 330, 58)
        draw = _smooth(u / 0.8)
        shore = 920 + 520 * draw                                   # where the water edge has drawn back to
        d.rectangle((0, 900, W, H), fill=(46, 40, 36, 255))          # bare mud
        for k in range(9):                                           # wet sheen on the mud
            yy = 960 + k * 110
            d.rectangle((0, yy, W, yy + 18), fill=(80, 90, 104, 60 + 6 * k))
        d.rectangle((0, 900, W, shore), fill=(10, 34, 62, 255))
        _sea_strip(d, shore, shore + 80, t)
        d.polygon([(110, 1330), (420, 1330), (380, 1400), (150, 1400)], fill=(20, 14, 10, 255))      # a boat left leaning on the mud
        d.line((260, 1330, 300, 1170), fill=(20, 14, 10, 255), width=9)
        for fx, fy, fs, ph in fish:                                  # gasping fish
            if fy < shore + 30:
                continue
            fl = math.sin(t * 9 + ph) * (0.6 if (t * 1.3 + ph) % 2.0 < 0.5 else 0.12)
            x, y = fx + 6 * fl, fy - 5 * abs(fl)
            d.ellipse((x - 24 * fs, y - 8 * fs, x + 24 * fs, y + 8 * fs), fill=(150, 170, 182, 255))
            d.polygon([(x + 20 * fs, y), (x + 40 * fs, y - 12 * fs + 10 * fl), (x + 40 * fs, y + 12 * fs + 10 * fl)], fill=(120, 140, 152, 255))
            d.ellipse((x - 14 * fs, y - 3 * fs, x - 9 * fs, y + 2 * fs), fill=(10, 10, 14, 255))
        _figure(d, 860, 1250, 1.1, raised=0.0, lean=0.4)              # someone standing at the edge, watching it go
        dim(im, 12)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


# ---------------------------------------------------------------- the control tower: someone checks the logs
def tidelog_scene(out, dur, world, sp, cwd=None):
    hits = [dur * 0.5]
    world.push, world.shake = 0.08, 0.0

    def paint(im, d, t, u, cam):
        d.rectangle((0, 0, W, H), fill=(20, 22, 30))
        d = ImageDraw.Draw(im, "RGBA")
        sky = _gradient((6, 10, 26), (30, 48, 80), 600)               # the window: night sea outside, the water line retreating
        im.paste(sky, (560, 260))
        d = ImageDraw.Draw(im, "RGBA")
        d.rectangle((560, 600, 1020, 860), fill=(12, 36, 62, 255))
        sea_y = 700 + 120 * _smooth(u / 0.9)
        d.rectangle((560, 640, 1020, 860), fill=(44, 38, 34, 255))
        d.rectangle((560, 640, 1020, sea_y - 40), fill=(12, 36, 62, 255))
        _stars_bg(d, 8, 25, 560, t)
        d.rectangle((560, 260, 1020, 860), outline=(60, 66, 80, 255), width=12)
        d.line((790, 260, 790, 860), fill=(60, 66, 80, 255), width=8)
        d.line((560, 560, 1020, 560), fill=(60, 66, 80, 255), width=8)
        d.rectangle((0, 1180, W, H), fill=(34, 26, 22, 255))             # the desk
        glow = Image.new("RGBA", im.size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.ellipse((60, 900, 760, 1500), fill=(255, 190, 90, 70 + int(8 * math.sin(t * 7))))
        im.paste(Image.alpha_composite(im.convert("RGBA"), glow.filter(ImageFilter.GaussianBlur(60))).convert("RGB"))
        d = ImageDraw.Draw(im, "RGBA")
        d.rectangle((140, 1120, 520, 1300), fill=(232, 224, 200, 255))   # the log book
        for k in range(7):
            ln = 150 + k * 22
            d.line((170, ln + 1000, 170 + 280 - (k % 3) * 40, ln + 1000), fill=(60, 50, 40, 255), width=3)
        pen_x = 170 + 240 * ((t * 0.4) % 1.0)
        d.line((pen_x, 1180, pen_x + 40, 1100), fill=(20, 20, 24, 255), width=6)    # a pen moving down the page
        d.polygon([(250, 1000), (200, 1180), (300, 1180)], fill=(70, 60, 50, 255))   # lamp
        d.polygon([(230, 1000), (330, 1000), (300, 1100), (200, 1100)], fill=(255, 210, 120, 255))
        _figure(d, 380, 1300, 1.9, col=(6, 6, 10), lean=-0.6, rim=(255, 190, 110))     # the man at the desk, bent over the log
        d.rectangle((60, 340, 260, 520), fill=(14, 16, 22, 255))         # a dark dial panel with a blinking light
        d.ellipse((140, 420, 172, 452), fill=(255, 60, 40, 255 if int(t * 2) % 2 == 0 else 60))
        dim(im, 16)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


# ---------------------------------------------------------------- the radio call that fails
def radio_scene(out, dur, world, sp, cwd=None):
    sky = _gradient((6, 10, 26), (28, 40, 70), 1000)
    hits = [dur * 0.45, dur * 0.85]
    world.push, world.shake = 0.12, 1.5

    def paint(im, d, t, u, cam):
        im.paste(sky, (0, 0))
        d = ImageDraw.Draw(im, "RGBA")
        _stars_bg(d, 12, 60, 800, t)
        d.polygon([(0, 1000), (380, 960), (760, 1080), (W, 1040), (W, H), (0, H)], fill=(14, 16, 22, 255))      # the cliff road
        d.polygon([(0, 1500), (W, 1380), (W, 1420), (0, 1560)], fill=(40, 42, 50, 255))
        # the harbour far below: lights that go out one by one as the signal dies
        alive = max(0.0, 1.0 - _smooth((u - 0.5) / 0.4))
        rnd = random.Random(4)
        for k in range(26):
            x, y = 120 + k * 32 + rnd.uniform(-8, 8), 1100 + rnd.uniform(-20, 40)
            if (k / 26.0) < alive + 0.02:
                d.ellipse((x - 5, y - 5, x + 5, y + 5), fill=(255, 210, 120, 230))
                d.ellipse((x - 14, y - 14, x + 14, y + 14), fill=(255, 190, 90, 40))
        hand = _figure(d, 330, 1500, 2.2, raised=min(1.0, u / 0.2), lean=0.5, rim=(120, 150, 200))     # the deputy with the radio
        d.rectangle((hand[0] - 14, hand[1] - 34, hand[0] + 14, hand[1] + 24), fill=(30, 30, 36, 255))
        d.line((hand[0] + 6, hand[1] - 34, hand[0] + 6, hand[1] - 120), fill=(30, 30, 36, 255), width=4)  # the aerial
        # the signal bars falling, with static creeping in
        sig = max(0.0, 1.0 - u * 1.25)
        for b in range(4):
            on = sig * 4 > b
            d.rectangle((820 + b * 40, 300 - b * 28, 846 + b * 40, 340), fill=(120, 230, 150, 255) if on else (70, 70, 80, 180))
        rn = random.Random(int(t * 24))
        for _ in range(int(20 + 340 * (1 - sig) ** 1.5)):
            y = rn.randint(0, H)
            d.line((rn.randint(0, W), y, rn.randint(0, W), y), fill=(200, 205, 215, rn.randint(20, 90)), width=rn.choice((2, 3, 5)))
        if u > 0.9:
            dim(im, int(180 * (u - 0.9) / 0.1))
        dim(im, 10)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


# ---------------------------------------------------------------- the harbour is swallowed
def harbor_scene(out, dur, world, sp, cwd=None):
    sky = _gradient((8, 12, 32), (40, 60, 96), 780)
    hits = [dur * 0.5, dur * 0.8]
    world.push, world.shake = 0.1, 3.5

    def paint(im, d, t, u, cam):
        im.paste(sky, (0, 0))
        d = ImageDraw.Draw(im, "RGBA")
        _stars_bg(d, 21, 70, 700, t)
        wave = _smooth((u - 0.15) / 0.6)
        d.rectangle((0, 780, W, H), fill=(12, 34, 60, 255))
        d.rectangle((0, 1250, W, 1290), fill=(58, 48, 40, 255))               # the quay
        rnd = random.Random(2)
        for k in range(9):                                                     # warehouses and houses along the front
            x = 40 + k * 120
            h = rnd.randint(110, 220)
            d.rectangle((x, 1250 - h, x + 100, 1250), fill=(16, 18, 26, 255))
            lit = (k / 9.0) > wave * 1.1
            for wy in range(1250 - h + 20, 1230, 50):
                d.rectangle((x + 20, wy, x + 36, wy + 22), fill=(255, 200, 110, 240) if lit else (30, 30, 40, 255))
        for bx, by_ in ((220, 1330), (620, 1350), (880, 1320)):               # boats at their moorings, tipping as it arrives
            tilt = 24 * wave * math.sin(t * 3 + bx)
            d.polygon([(bx - 70, by_ - tilt), (bx + 70, by_ + tilt), (bx + 50, by_ + 34 + tilt), (bx - 50, by_ + 34 - tilt)], fill=(24, 18, 14, 255))
        top = 800 - 120 * wave - 1300 * _smooth((u - 0.7) / 0.28)               # the water coming over the front
        if wave > 0:
            _sea_strip(d, top, H + 200, t, col=(8, 30, 56))
            for k in range(1, 6):
                _sea_strip(d, top + 120 * k, H + 200, t + k, col=(8 - k, 28 - 2 * k, 54 - 3 * k), foam=False)
        if u > 0.9:
            dim(im, int(220 * (u - 0.9) / 0.1))
        dim(im, 8)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


# ---------------------------------------------------------------- something rises out of the dark trench
def rise_scene(out, dur, world, sp, cwd=None):
    sky = _gradient((4, 8, 20), (24, 36, 62), 900)
    hits = [dur * 0.35, dur * 0.7]
    n_eyes = eye_total(sp, 4)
    ec = tuple(sp.get("eye", (60, 255, 150))[:3])
    world.push, world.shake = 0.12, 3.0

    def paint(im, d, t, u, cam):
        im.paste(sky, (0, 0))
        d = ImageDraw.Draw(im, "RGBA")
        _stars_bg(d, 31, 70, 600, t)
        q = _smooth(u / 0.8)
        d.rectangle((0, 1000, W, H), fill=(6, 18, 34, 255))
        top = 1000 - 900 * q                                                    # the black mountain of water
        pts = [(0, 1000)]
        for x in range(0, W + 40, 40):
            k = 1.0 - abs(x - 540) / 640.0
            pts.append((x, 1000 - max(0.0, k) ** 1.4 * 900 * q + 16 * math.sin(x * 0.05 + t * 3)))
        pts += [(W, 1000)]
        d.polygon(pts + [(W, 1100), (0, 1100)], fill=(4, 14, 26, 255))
        if q > 0.35:                                                            # weed and bone along the crest
            for x, y in pts[2:-2:2]:
                if y < 990:
                    d.line([(x, y), (x + 12 * math.sin(t * 2 + x), y + 50)], fill=(18, 60, 40, 230), width=5)
            for x, y in pts[3:-3:5]:
                if y < 960:
                    d.line([(x, y + 6), (x + 22, y + 30)], fill=(220, 214, 196, 255), width=7)
                    d.ellipse((x - 5, y + 2, x + 8, y + 14), fill=(220, 214, 196, 255))
        _glow_eyes(d, 540, 1000 - 520 * q, n_eyes, 120, 20, ec, t, appear=_smooth((u - 0.55) / 0.2))
        for k in range(40):                                                     # water streaming down its flanks
            x = 140 + (k * 97) % 800
            y0 = 1000 - 700 * q * max(0.1, 1 - abs(x - 540) / 600.0)
            d.line((x, y0 + (t * 220 + k * 40) % 300, x, y0 + 40 + (t * 220 + k * 40) % 300), fill=(150, 180, 200, 70), width=3)
        d.rectangle((0, 1000, W, H), fill=(6, 18, 34, 255))
        _sea_strip(d, 1000, H, t, col=(6, 18, 34), foam=False)
        for k in range(10):
            d.ellipse((60 + k * 100 + 20 * math.sin(t + k), 1020 + (k * 53) % 200, 100 + k * 100, 1040 + (k * 53) % 200), fill=(120, 150, 170, 50))
        _BOXES.append((100, 150, 980, 1000))
        dim(im, 6)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


# ---------------------------------------------------------------- eyes staring through the reinforced glass
def glass_scene(out, dur, world, sp, cwd=None):
    hits = [dur * 0.4, dur * 0.85]
    n_eyes = eye_total(sp, 4)
    ec = tuple(sp.get("eye", (60, 255, 150))[:3])
    world.push, world.shake = 0.05, 2.0

    def paint(im, d, t, u, cam):
        d.rectangle((0, 0, W, H), fill=(4, 10, 20))
        d = ImageDraw.Draw(im, "RGBA")
        for k in range(30):                                                     # the churning wall of water behind the glass
            y = (k * 70 + t * 60) % H
            x = 80 + (k * 131) % 900
            d.ellipse((x - 120, y - 30, x + 120, y + 30), fill=(14, 44, 70, 120))
        _glow_eyes(d, 540, 820, n_eyes, 190, 34, ec, t, appear=_smooth(u / 0.35))
        d = ImageDraw.Draw(im, "RGBA")
        for k in range(34):                                                     # rain and spray running down the pane
            x = (k * 37 + 11) % W
            y = (t * (260 + k * 6) + k * 120) % (H + 200) - 100
            d.line((x, y, x + 2, y + 90), fill=(180, 210, 230, 60), width=3)
        for x in range(0, W + 1, 270):                                          # the wire-reinforced glass grid
            d.line((x, 0, x, H), fill=(40, 52, 66, 200), width=6)
        for y in range(0, H + 1, 270):
            d.line((0, y, W, y), fill=(40, 52, 66, 200), width=6)
        if u > 0.6:                                                             # the glass starts to crack
            c = _smooth((u - 0.6) / 0.35)
            for a, L in ((0.3, 460), (1.9, 380), (3.5, 520), (5.0, 410)):
                x1, y1 = 540 + math.cos(a) * L * c, 830 + math.sin(a) * L * c
                d.line([(540, 830), ((540 + x1) / 2 + 20, (830 + y1) / 2 - 14), (x1, y1)], fill=(230, 240, 250, 220), width=3)
        d.rectangle((0, 1500, W, H), fill=(18, 20, 26, 255))                    # the tower's console below the window
        for k in range(7):
            d.ellipse((80 + k * 140, 1580, 110 + k * 140, 1610), fill=(255, 70, 50, 255) if int(t * 3 + k) % 2 else (60, 20, 20, 255))
        _BOXES.append((80, 400, 1000, 1250))
        dim(im, 6)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


# ---------------------------------------------------------------- the creature crests the ridge, blotting out the stars
def ridge_scene(out, dur, world, sp, cwd=None):
    sky = _gradient((4, 6, 18), (22, 30, 56), 1300)
    hits = [dur * 0.3, dur * 0.65]
    world.push, world.shake = 0.0, 3.0

    def paint(im, d, t, u, cam):
        im.paste(sky, (0, 0))
        d = ImageDraw.Draw(im, "RGBA")
        k = _smooth(u / 0.7)
        base = 1750 - 760 * k                                                    # how far up behind the ridge it has climbed
        ax_, ay_ = head_anchor(sp)
        _stars_bg(d, 41, 130, 1300, t, hide=lambda x, y: abs(x - 540) < 330 * k and y > 1250 - 1000 * k)
        draw_creature(d, sp, 0, 0, t * 1.6, 1, 4.1, {"t": t, "roar": 0.2 + 0.6 * _smooth((u - 0.5) / 0.4), "moving": False},
                      focus=(ax_, ay_, 690 + 4 * math.sin(t * 40), base - 140))
        d = ImageDraw.Draw(im, "RGBA")
        d.polygon([(0, 1700), (200, 1640), (420, 1700), (680, 1620), (900, 1690), (W, 1650), (W, H), (0, H)], fill=(3, 4, 9, 255))
        for x, y in ((180, 1820), (250, 1830), (760, 1840), (860, 1810)):          # tiny lights far below show the scale
            d.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(255, 205, 110, 255))
        _BOXES.append((100, 200, 980, 1700))
        dim(im, 6)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


STORY_FUNCS = {"retreat": retreat_scene, "tidelog": tidelog_scene, "radio": radio_scene, "harbor": harbor_scene,
               "rise": rise_scene, "glass": glass_scene, "ridge": ridge_scene}


# ================================================================ cinematic scenes (layered backgrounds, emergence, light change)
# What makes the reference videos feel produced: something huge watches from the background, things rise SLOWLY out of the
# ground or water with dust / rings around them, and the light itself changes when the story changes mood.
def _layer_creature(im, sp, scale, cx, cy, alpha, tint=None, t=0.0):
    """Draw the creature on its own layer and blend it in: a huge ghostly presence behind the action."""
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(lay, "RGBA")
    ax_, ay_ = head_anchor(sp)
    try:
        draw_creature(ld, sp, 0, 0, t * 0.8, 1, scale, {"t": t, "roar": 0.0, "moving": False}, focus=(ax_, ay_, cx, cy))
    except Exception:
        return
    if tint:
        px = np.asarray(lay).astype(np.float32)
        lum = px[..., :3].mean(axis=2, keepdims=True) / 255.0
        px[..., :3] = lum * np.array(tint, np.float32) * 1.2
        lay = Image.fromarray(px.clip(0, 255).astype(np.uint8), "RGBA")
    a = np.asarray(lay).copy()
    a[..., 3] = (a[..., 3] * alpha).astype(np.uint8)
    im.paste(Image.fromarray(a, "RGBA"), (0, 0), Image.fromarray(a, "RGBA"))


def _rise_dust(d, x, y, q, t, n=26, col=(120, 100, 80), spread=220, seed=3):
    rnd = random.Random(seed)
    for k in range(n):
        ang = rnd.uniform(-math.pi, 0)
        sp_ = rnd.uniform(0.3, 1.0)
        r = (14 + 30 * sp_) * (0.4 + q)
        px = x + math.cos(ang) * spread * sp_ * q + rnd.uniform(-40, 40)
        py = y + math.sin(ang) * spread * 0.7 * sp_ * q - 30 * q
        d.ellipse((px - r, py - r, px + r, py + r), fill=col + (int(60 * (1 - q * 0.6)),))


# ---------------------------------------------------------------- something drags itself up out of the ground
def emerge_scene(out, dur, world, sp, cwd=None):
    sky = _gradient((10, 8, 24), (70, 46, 60), 1200)
    hits = [dur * 0.25, dur * 0.6]
    world.push, world.shake = 0.08, 2.0
    ec = tuple(sp.get("eye", (255, 120, 60))[:3])

    def paint(im, d, t, u, cam):
        im.paste(sky, (0, 0))
        d = ImageDraw.Draw(im, "RGBA")
        _stars_bg(d, 52, 60, 700, t)
        _layer_creature(im, sp, 6.0, 540, 420, 0.22 * _smooth(u / 0.5), tint=(90, 90, 170), t=t)   # the huge watcher in the sky
        d = ImageDraw.Draw(im, "RGBA")
        _land(im, 1250, "emerge", t, 7)
        d = ImageDraw.Draw(im, "RGBA")
        q = _smooth((u - 0.1) / 0.75)                                            # slow, eased rise
        gy = 1500 + (1 - q) * 520
        ax_, ay_ = head_anchor(sp)
        draw_creature(d, sp, 0, 0, t * 1.2, 1, 1.5, {"t": t, "roar": 0.4 * q, "moving": False}, focus=(ax_, ay_, 560, gy - 300))
        d = ImageDraw.Draw(im, "RGBA")
        d.polygon([(0, 1500), (260, 1470), (420, 1500 + 24 * (1 - q)), (540, 1520 + 40 * (1 - q)), (660, 1500 + 24 * (1 - q)), (860, 1470), (W, 1500), (W, H), (0, H)], fill=(20, 14, 18, 255))
        for k in range(14):                                                      # cracks spread out from where it breaks through
            x = 540 + (k - 7) * 70 * q
            d.line([(540, 1520), (x, 1520 + 30 + (k % 3) * 28)], fill=(255, 140, 70, int(120 * q)), width=3)
        _rise_dust(d, 540, 1500, q, t)
        _BOXES.append((120, 500, 960, 1500))
        dim(im, 6)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


# ---------------------------------------------------------------- the water rings, foams, swirls; a shape slowly surfaces
def swirl_scene(out, dur, world, sp, cwd=None):
    sky = _gradient((20, 24, 52), (150, 120, 130), 900)
    hits = [dur * 0.45]
    world.push, world.shake = 0.1, 1.0

    def paint(im, d, t, u, cam):
        im.paste(sky, (0, 0))
        d = ImageDraw.Draw(im, "RGBA")
        _moon(d, 780, 330, 90)
        d.rectangle((0, 900, W, H), fill=(34, 52, 86, 255))
        for k in range(12):                                                      # distant ripples
            y = 930 + k * 40
            d.line([(x, y + 4 * math.sin(x * 0.03 + t * 2 + k)) for x in range(0, W + 30, 30)], fill=(130, 160, 200, 50), width=2)
        q = _smooth((u - 0.25) / 0.6)
        cx, cy = 540, 1240
        ax_, ay_ = head_anchor(sp)
        if q > 0.02:                                                            # the shape surfaces from below the waterline
            lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ld = ImageDraw.Draw(lay, "RGBA")
            draw_creature(ld, sp, 0, 0, t * 1.0, 1, 1.4, {"t": t, "roar": 0.0, "moving": False}, focus=(ax_, ay_, 560, cy + 420 * (1 - q) - 120))
            a = np.asarray(lay).copy()
            a[int(round((cy + 40) * (RSF[0] if _is_hr(lay) else 1.0))):, :, 3] = 0                # everything below the water line stays hidden
            lay = Image.fromarray(a, "RGBA")
            im.paste(lay, (0, 0), lay)
            d = ImageDraw.Draw(im, "RGBA")
        for k in range(5):                                                       # foam rings spreading, then the swirl
            r = 60 + ((t * 120 + k * 90) % 450)
            al = int(200 * (1 - (r - 60) / 450.0))
            d.ellipse((cx - r, cy + 40 - r * 0.28, cx + r, cy + 40 + r * 0.28), outline=(235, 245, 250, max(0, al)), width=5)
        for k in range(26):
            ang = t * 1.4 + k * 0.45
            rr = 20 + k * 9 * (1 - 0.5 * q)
            x, y = cx + math.cos(ang) * rr, cy + 40 + math.sin(ang) * rr * 0.28
            d.ellipse((x - 5, y - 3, x + 5, y + 3), fill=(240, 248, 252, int(190 * (1 - k / 30.0))))
        d.polygon([(0, 1290), (W, 1290), (W, H), (0, H)], fill=(26, 40, 70, 150))
        _BOXES.append((200, 600, 880, 1300))
        dim(im, 6)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


# ---------------------------------------------------------------- cast down into the pit: a long red fall
def abyss_scene(out, dur, world, sp, cwd=None):
    hits = [dur * 0.5]
    world.push, world.shake = 0.0, 2.5

    def paint(im, d, t, u, cam):
        d = ImageDraw.Draw(im, "RGBA")
        im.paste(_gradient((16, 2, 4), (120, 28, 16), H), (0, 0))
        d = ImageDraw.Draw(im, "RGBA")
        for side in (0, 1):                                                      # rock walls rushing upward past the camera
            for k in range(9):
                y = ((k * 260 - t * 700) % (H + 260)) - 130
                x = 0 if side == 0 else W
                w = 120 + (k * 53) % 90
                d.polygon([(x, y), (x + (w if side == 0 else -w), y + 70), (x, y + 200)], fill=(30, 8, 8, 255))
        rnd = random.Random(8)
        for k in range(9):                                                       # small figures tumbling down with it
            x = rnd.uniform(220, 860)
            y = ((rnd.uniform(0, H) + t * (380 + k * 40)) % (H + 200)) - 100
            _figure(d, x + 18 * math.sin(t * 2 + k), y, 0.5, col=(10, 4, 4), lean=math.sin(t + k))
        g = 0.5 + 0.5 * math.sin(t * 3)
        d.ellipse((140, H - 360, W - 140, H + 260), fill=(255, 90, 30, int(70 + 50 * g)))
        _BOXES.append((200, 300, 880, 1500))
        dim(im, 8)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


# ---------------------------------------------------------------- the light changes: cold night to warm first light
def dawn_scene(out, dur, world, sp, cwd=None):
    night, day = _gradient((6, 10, 30), (40, 50, 96), 1400), _gradient((90, 120, 190), (255, 196, 130), 1400)
    hits = []
    world.push, world.shake = 0.08, 0.0

    def paint(im, d, t, u, cam):
        k = _smooth((u - 0.2) / 0.6)                                             # the grade shifts mid-scene, not at the cut
        mix = Image.blend(night, day, k)
        im.paste(mix, (0, 0))
        d = ImageDraw.Draw(im, "RGBA")
        _stars_bg(d, 61, 80, 700, t)
        sx, sy = 540, 1250 - 420 * k
        for r, a in ((520, 28), (340, 50), (200, 90)):
            d.ellipse((sx - r, sy - r, sx + r, sy + r), fill=(255, 214, 150, int(a * k)))
        d.ellipse((sx - 90, sy - 90, sx + 90, sy + 90), fill=(255, 236, 190, int(255 * k)))
        for i, (c, y0) in enumerate((((60, 66, 112), 1330), ((34, 36, 70), 1420), ((14, 14, 30), 1520))):
            col = tuple(int(c[j] * (1 - k) + ((150, 100, 90), (110, 70, 70), (50, 30, 36))[i][j] * k) for j in range(3))
            pts = [(0, y0)] + [(x, y0 - 60 - 40 * math.sin(x * 0.006 + i * 2)) for x in range(0, W + 60, 60)] + [(W, y0), (W, H), (0, H)]
            d.polygon(pts, fill=col + (255,))
        _BOXES.append((100, 700, 980, 1300))
        dim(im, 4)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": hits}


STORY_FUNCS.update({"emerge": emerge_scene, "swirl": swirl_scene, "abyss": abyss_scene, "dawn": dawn_scene})


# ---------------------------------------------------------------- the two recurring hosts react to what was just said
_HS = 2                                                      # host sprites are drawn at 2x and shrunk for clean edges
_OUT = (48, 28, 30)


def _shade(c, k):
    return tuple(max(0, min(255, int(v * k))) for v in c)


def _pl(d, pts, col, w, S):
    """A smooth, round-capped thick line through points (arms, brows, hair strands)."""
    w = int(w * S)
    d.line(pts, fill=col, width=w, joint="curve")
    for x, y in (pts[0], pts[-1]):
        d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=col)


def _soft(im, S, clip_fn, shade_fn, blur, col, alpha):
    """Soft shading: paint a shape, blur it, keep it only inside clip_fn's region."""
    from PIL import ImageChops, ImageFilter
    m = Image.new("L", im.size, 0)
    clip_fn(ImageDraw.Draw(m))
    sh = Image.new("L", im.size, 0)
    shade_fn(ImageDraw.Draw(sh))
    sh = ImageChops.multiply(sh.filter(ImageFilter.GaussianBlur(blur * S)), m)
    lay = Image.new("RGBA", im.size, tuple(col) + (0,))
    lay.putalpha(sh.point(lambda v: int(v * alpha)))
    im.alpha_composite(lay)


def _blob(im, box, col, alpha, blur, S):
    """A soft translucent highlight that really blends (ImageDraw alpha fills would punch holes in an RGBA layer)."""
    from PIL import ImageFilter
    m = Image.new("L", im.size, 0)
    ImageDraw.Draw(m).ellipse(box, fill=255)
    m = m.filter(ImageFilter.GaussianBlur(blur * S))
    lay = Image.new("RGBA", im.size, tuple(col) + (0,))
    lay.putalpha(m.point(lambda v: int(v * alpha)))
    im.alpha_composite(lay)


def _hand(d, x, y, s, skin, ang, S):
    """A clean cartoon hand: fanned rounded fingers, a thumb and a palm, one outline around the whole shape."""
    parts = []
    for k, da in enumerate((-0.6, -0.2, 0.2, 0.6)):
        a = ang + da
        l = (54, 64, 64, 54)[k] * s
        parts.append(((x, y), (x + math.sin(a) * l, y - math.cos(a) * l), 15 * s))
    ta = ang - 1.15
    parts.append(((x, y + 6 * s), (x + math.sin(ta) * 36 * s, y + 6 * s - math.cos(ta) * 36 * s), 16 * s))
    for col, grow in ((_OUT, 6), (skin, 0)):
        for p0, p1, w in parts:
            _pl(d, [(p0[0] * 1, p0[1] * 1), p1], col + (255,), w + grow, S)
        r = 25 * s + grow / 2
        d.ellipse(((x - r), (y - r * 0.9), (x + r), (y + r * 1.1)), fill=col + (255,))
    for da in (-0.4, 0.0, 0.4):                                              # gaps between the fingers
        a = ang + da
        d.line([(x + math.sin(a) * 18 * s, y - math.cos(a) * 18 * s), (x + math.sin(a) * 62 * s, y - math.cos(a) * 62 * s)], fill=_shade(skin, 0.55) + (255,), width=int(3 * S))
    for p0, p1, w in parts[:4]:                                              # finger creases
        d.line([((p0[0] + p1[0]) / 2 - 4 * s, (p0[1] + p1[1]) / 2 + 4 * s), ((p0[0] + p1[0]) / 2 + 4 * s, (p0[1] + p1[1]) / 2 + 4 * s)], fill=_shade(skin, 0.8) + (255,), width=int(2 * S))


def _eye(d, x, y, s, look, blink, iris, S, lash=False, lid=True):
    w, h = 32 * s, 26 * s * (1 - 0.93 * blink)
    d.ellipse((x - w, y - h, x + w, y + h), fill=(252, 250, 246, 255))
    if lid:
        d.pieslice((x - w, y - h, x + w, y + h), 205, 335, fill=(214, 206, 204, 255))                  # lid shadow on the white
    if blink < 0.65:
        ix, iy = x + look[0] * 8 * s, y + look[1] * 5 * s
        r = 18 * s
        k = 1 - 0.8 * blink
        d.ellipse((ix - r, iy - r * k, ix + r, iy + r * k), fill=iris + (255,))
        d.ellipse((ix - r * 0.82, iy - r * 0.2, ix + r * 0.82, iy + r * 0.9 * k), fill=tuple(int(v * 0.6 + w_ * 0.4) for v, w_ in zip(_shade(iris, 1.35), iris)) + (255,))
        d.ellipse((ix - r * 0.52, iy - r * 0.52 * k, ix + r * 0.52, iy + r * 0.52 * k), fill=(16, 10, 12, 255))
        d.ellipse((ix - r * 0.55, iy - r * 0.78 * k, ix - r * 0.08, iy - r * 0.3 * k), fill=(255, 255, 255, 255))
        d.ellipse((ix + r * 0.25, iy + r * 0.2, ix + r * 0.5, iy + r * 0.45), fill=(238, 238, 238, 255))
    d.arc((x - w, y - h - 1, x + w, y + h), 188, 352, fill=_OUT + (255,), width=int(6.5 * S * s))
    d.arc((x - w, y - h, x + w, y + h), 18, 162, fill=(120, 86, 80, 255), width=int(2.5 * S))


def _brow(d, x, y, s, rise, tilt, col, thick, S, outline=True):
    pts = [(x - 36 * s, y + 6 * s + tilt * 6 * s), (x - 12 * s, y - 8 * s - rise * 12 * s), (x + 14 * s, y - 9 * s - rise * 14 * s), (x + 36 * s, y + 2 * s - tilt * 6 * s)]
    if outline:
        _pl(d, pts, _OUT + (255,), thick + 4, S)
    _pl(d, pts, col + (255,), thick, S)


def _mouth(d, x, y, s, open_, smile, S):
    w = 38 * s
    lip = (196, 98, 100)
    if open_ < 0.12:
        d.arc((x - w, y - 24 * s, x + w, y + 20 * s * (0.5 + smile)), 22, 158, fill=_OUT + (255,), width=int(5 * S * s))
        d.ellipse((x - 14 * s, y + 8 * s, x + 14 * s, y + 20 * s), fill=lip + (170,))
        return
    o = open_ * 52 * s
    d.chord((x - w, y - 12 * s, x + w, y + o + 10 * s), 0, 180, fill=(84, 20, 30, 255), outline=_OUT + (255,), width=int(4 * S))
    d.chord((x - w * 0.78, y - 10 * s, x + w * 0.78, y + 14 * s), 180, 360, fill=(252, 250, 244, 255))
    d.chord((x - w * 0.6, y + o * 0.35, x + w * 0.6, y + o + 8 * s), 0, 180, fill=(222, 100, 108, 255))
    d.arc((x - w, y - 12 * s, x + w, y + o + 10 * s), 0, 180, fill=lip + (255,), width=int(5 * S * s))


def _host_sprite(kind, t, speaking, ph):
    """kind 0: the storyteller (older, bearded, glasses, teal robe). kind 1: the reactor (young, curly hair, mustard jacket)."""
    S = _HS
    im = Image.new("RGBA", (600 * S, 900 * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im, "RGBA")
    sc = lambda v: v * S
    bob = 5 * math.sin(t * 3.0 + ph)
    cx, hy = 300, 300 + bob
    rx, ry = 104, 120
    ph_ = (t + ph) % 3.4
    blink = max(0.0, 1 - abs(ph_ - 3.2) / 0.1) if ph_ > 3.1 else 0.0
    talk = (0.3 + 0.7 * abs(math.sin(t * 8.5 + ph) * math.sin(t * 3.1 + 1))) if speaking else 0.0
    if kind == 0:
        skin, cloth, trim, hair, iris = (232, 184, 146), (36, 98, 118), (236, 196, 100), (236, 234, 230), (62, 112, 92)
        rise, tilt, look, smile = 0.15 + 0.15 * math.sin(t * 2), -0.3, (0.8, -0.4), 0.7
    else:
        skin, cloth, trim, hair, iris = (180, 120, 88), (226, 152, 50), (120, 56, 38), (46, 28, 26), (96, 62, 34)
        rise, tilt, look, smile = 1.3, 0.9, (-0.8, -0.5), 0.0
    # ---- torso (rounded shoulders), soft shading
    body = lambda dd, fill, col=None, wd=0: dd.rounded_rectangle((sc(cx - 205), sc(490 + bob), sc(cx + 205), sc(1000)), radius=sc(95), fill=fill, outline=col, width=wd)
    body(d, cloth + (255,), _OUT + (255,), sc(4))
    _soft(im, S, lambda m: body(m, 255), lambda m: m.ellipse((sc(cx + 40), sc(430), sc(cx + 420), sc(1100)), fill=255), 40, _shade(cloth, 0.5), 0.75)
    _soft(im, S, lambda m: body(m, 255), lambda m: m.ellipse((sc(cx - 330), sc(500), sc(cx - 120), sc(900)), fill=255), 35, _shade(cloth, 1.5), 0.3)
    d = ImageDraw.Draw(im, "RGBA")
    d.rectangle((sc(cx - 36), sc(400 + bob), sc(cx + 36), sc(520 + bob)), fill=_shade(skin, 0.93) + (255,))
    _soft(im, S, lambda m: m.rectangle((sc(cx - 36), sc(400), sc(cx + 36), sc(520)), fill=255), lambda m: m.ellipse((sc(cx - 90), sc(380), sc(cx + 90), sc(450)), fill=255), 10, (90, 40, 30), 0.55)
    d = ImageDraw.Draw(im, "RGBA")
    if kind == 0:                                                              # high collar, gold clasp, trim
        d.polygon([(sc(cx - 78), sc(492 + bob)), (sc(cx), sc(610 + bob)), (sc(cx + 78), sc(492 + bob)), (sc(cx + 50), sc(478 + bob)), (sc(cx), sc(560 + bob)), (sc(cx - 50), sc(478 + bob))], fill=trim + (255,), outline=_OUT + (255,))
        d.ellipse((sc(cx - 22), sc(600 + bob), sc(cx + 22), sc(644 + bob)), fill=(240, 206, 110, 255), outline=_OUT + (255,), width=sc(3))
        d.ellipse((sc(cx - 9), sc(613 + bob), sc(cx + 9), sc(631 + bob)), fill=(60, 140, 120, 255))
        for k in range(3):
            d.arc((sc(cx - 150 + k * 100), sc(690 + k * 20), sc(cx - 40 + k * 100), sc(820 + k * 20)), 210, 320, fill=_shade(cloth, 0.7) + (255,), width=sc(4))
        for k in range(9):
            d.ellipse((sc(cx - 176 + k * 44), sc(868), sc(cx - 162 + k * 44), sc(882)), fill=trim + (255,))
    else:                                                                      # open jacket over a cream top, lapels, pocket
        d.polygon([(sc(cx - 70), sc(492 + bob)), (sc(cx), sc(640 + bob)), (sc(cx + 70), sc(492 + bob)), (sc(cx + 46), sc(482 + bob)), (sc(cx), sc(560 + bob)), (sc(cx - 46), sc(482 + bob))], fill=(248, 236, 210, 255), outline=_OUT + (255,))
        d.polygon([(sc(cx - 70), sc(492 + bob)), (sc(cx - 96), sc(700)), (sc(cx - 4), sc(640 + bob))], fill=_shade(cloth, 0.82) + (255,), outline=_OUT + (255,))
        d.polygon([(sc(cx + 70), sc(492 + bob)), (sc(cx + 96), sc(700)), (sc(cx + 4), sc(640 + bob))], fill=_shade(cloth, 0.7) + (255,), outline=_OUT + (255,))
        d.line([(sc(cx), sc(640 + bob)), (sc(cx), sc(1000))], fill=_shade(cloth, 0.6) + (255,), width=sc(5))
        d.rounded_rectangle((sc(cx + 70), sc(740), sc(cx + 160), sc(800)), radius=sc(10), outline=_shade(cloth, 0.6) + (255,), width=sc(4))
        d.ellipse((sc(cx - 36), sc(590 + bob), sc(cx + 36), sc(650 + bob)), fill=(196, 64, 54, 255), outline=_OUT + (255,), width=sc(3))   # scarf knot
    # ---- arms and hands (connected to the shoulders)
    up = 0.5 + 0.5 * math.sin(t * 2.4 + ph)
    if kind == 1:
        for sx in (-1, 1):
            pts = [(sc(cx + sx * 175), sc(570 + bob)), (sc(cx + sx * 245), sc(650)), (sc(cx + sx * 150), sc(hy + 160 - 12 * up))]
            _pl(d, pts, _OUT + (255,), 62, S)
            _pl(d, pts, cloth + (255,), 54, S)
            _pl(d, pts[1:], _shade(cloth, 0.8) + (255,), 10, S)
            _hand(d, sc(cx + sx * 140), sc(hy + 130 - 12 * up), 1.35 * S, skin, -sx * 0.28, S)
    else:
        pts = [(sc(cx + 175), sc(570 + bob)), (sc(cx + 250), sc(660)), (sc(cx + 215), sc(hy + 205 + 6 * math.sin(t * 1.7)))]
        _pl(d, pts, _OUT + (255,), 62, S)
        _pl(d, pts, cloth + (255,), 54, S)
        _hand(d, sc(cx + 205), sc(hy + 190 + 6 * math.sin(t * 1.7)), 1.35 * S, skin, -0.25, S)
    d = ImageDraw.Draw(im, "RGBA")
    # ---- hair behind head (reactor curls)
    if kind == 1:
        rnd = random.Random(5)
        for ring, (rr, n_, sz) in enumerate(((118, 12, 58), (96, 9, 54))):
            for k in range(n_):
                a_ = math.radians(180 + 180 * k / (n_ - 1) + rnd.uniform(-6, 6)) if ring == 0 else math.radians(200 + 140 * k / (n_ - 1))
                x0, y0 = cx + math.cos(a_) * (rr + 16), hy - 10 + math.sin(a_) * (rr + 4)
                if ring == 0 or True:
                    d.ellipse((sc(x0 - sz), sc(y0 - sz), sc(x0 + sz), sc(y0 + sz)), fill=hair + (255,), outline=_OUT + (255,), width=sc(3))
        for sx in (-1, 1):                                                       # side curls down past the ears
            for k in range(3):
                d.ellipse((sc(cx + sx * (128 + k * 6) - 36), sc(hy + 20 + k * 40 - 36), sc(cx + sx * (128 + k * 6) + 36), sc(hy + 20 + k * 40 + 36)), fill=hair + (255,), outline=_OUT + (255,), width=sc(3))
    # ---- ears + head
    for sx in (-1, 1):
        d.ellipse((sc(cx + sx * rx - 18), sc(hy - 4), sc(cx + sx * rx + 18), sc(hy + 56)), fill=skin + (255,), outline=_OUT + (255,), width=sc(3))
        d.ellipse((sc(cx + sx * rx - 8), sc(hy + 12), sc(cx + sx * rx + 8), sc(hy + 40)), fill=_shade(skin, 0.86) + (255,))
    head = lambda dd, fill, col=None, wd=0: dd.ellipse((sc(cx - rx), sc(hy - ry), sc(cx + rx), sc(hy + ry)), fill=fill, outline=col, width=wd)
    head(d, skin + (255,), _OUT + (255,), sc(4))
    _soft(im, S, lambda m: head(m, 255), lambda m: m.ellipse((sc(cx + 40), sc(hy - 200), sc(cx + 300), sc(hy + 220)), fill=255), 30, _shade(skin, 0.6), 0.8)
    _soft(im, S, lambda m: head(m, 255), lambda m: m.ellipse((sc(cx - 70), sc(hy + 80), sc(cx + 70), sc(hy + 200)), fill=255), 18, _shade(skin, 0.7), 0.35)
    for sx in (-1, 1):
        _soft(im, S, lambda m: head(m, 255), lambda m, sx=sx: m.ellipse((sc(cx + sx * 64 - 34), sc(hy + 34), sc(cx + sx * 64 + 34), sc(hy + 76)), fill=255), 14, (232, 96, 96), 0.32)
    d = ImageDraw.Draw(im, "RGBA")
    if kind == 1:
        for sx in (-1, 1):                                                       # freckles
            for fx, fy in ((-12, 0), (2, 10), (14, -2), (-2, -10), (10, 14)):
                d.ellipse((sc(cx + sx * 62 + fx - 2.5), sc(hy + 24 + fy - 2.5), sc(cx + sx * 62 + fx + 2.5), sc(hy + 24 + fy + 2.5)), fill=_shade(skin, 0.66) + (255,))
    # ---- hair on top / beard / headband
    if kind == 0:
        for sx in (-1, 1):                                                       # grey side hair above the ears, a bald dome with a shine
            d.ellipse((sc(cx + sx * 98 - 24), sc(hy - 40), sc(cx + sx * 98 + 24), sc(hy + 14)), fill=hair + (255,), outline=_OUT + (255,), width=sc(3))
            for k in range(3):
                _pl(d, [(sc(cx + sx * (112 - k * 12)), sc(hy - 50 + k * 12)), (sc(cx + sx * (80 - k * 10)), sc(hy - 62 + k * 14))], _shade(hair, 0.8) + (255,), 2.5, S)
        _blob(im, (sc(cx - 46), sc(hy - 98), sc(cx + 4), sc(hy - 72)), (255, 255, 255), 0.4, 5, S); d = ImageDraw.Draw(im, "RGBA")
        beard = [(cx - 108, hy + 6), (cx - 100, hy + 70), (cx - 62, hy + 134), (cx - 20, hy + 188), (cx, hy + 206), (cx + 20, hy + 188), (cx + 62, hy + 134), (cx + 100, hy + 70), (cx + 108, hy + 6),
                 (cx + 86, hy + 56), (cx + 44, hy + 76), (cx, hy + 66), (cx - 44, hy + 76), (cx - 86, hy + 56)]
        d.polygon([(sc(x), sc(y)) for x, y in beard], fill=hair + (255,), outline=_OUT + (255,))
        d.ellipse((sc(cx - 52), sc(hy + 78), sc(cx + 52), sc(hy + 128)), fill=skin + (255,))                   # the mouth patch inside the beard
        for k in range(7):
            _pl(d, [(sc(cx - 70 + k * 23), sc(hy + 96 + abs(k - 3) * 6)), (sc(cx - 64 + k * 21), sc(hy + 160 + (3 - abs(k - 3)) * 12))], _shade(hair, 0.8) + (255,), 2.5, S)
        mu = [(cx - 54, hy + 78), (cx - 20, hy + 70), (cx, hy + 80), (cx + 20, hy + 70), (cx + 54, hy + 78), (cx + 30, hy + 96), (cx, hy + 88), (cx - 30, hy + 96)]
        d.polygon([(sc(x), sc(y)) for x, y in mu], fill=hair + (255,), outline=_OUT + (255,))
    else:
        for k in range(9):                                                       # fringe curls over the forehead
            a_ = math.radians(196 + 148 * k / 8)
            x0, y0 = cx + math.cos(a_) * 96, hy - 40 + math.sin(a_) * 78
            d.ellipse((sc(x0 - 34), sc(y0 - 34), sc(x0 + 34), sc(y0 + 34)), fill=hair + (255,), outline=_OUT + (255,), width=sc(3))
            d.arc((sc(x0 - 22), sc(y0 - 24), sc(x0 + 10), sc(y0 + 6)), 200, 300, fill=_shade(hair, 2.2) + (255,), width=sc(3))
        hb = [(sc(cx - 106 + k * 7), sc(hy - 64 - 22 * math.sin(math.pi * k / 30) + 0)) for k in range(31)]
        _pl(d, hb, _OUT + (255,), 30, S)
        _pl(d, hb, (238, 112, 64, 255), 24, S)
        for k in range(1, 30, 3):
            d.ellipse((hb[k][0] - sc(4), hb[k][1] - sc(4), hb[k][0] + sc(4), hb[k][1] + sc(4)), fill=(252, 228, 150, 255))
        for sx in (-1, 1):                                                       # gold hoops
            d.ellipse((sc(cx + sx * rx - 10), sc(hy + 52), sc(cx + sx * rx + 10), sc(hy + 86)), outline=(244, 204, 90, 255), width=sc(4))
    # ---- face
    ey = hy + 6
    for sx in (-1, 1):
        _eye(d, sc(cx + sx * 46), sc(ey), S, (look[0], look[1]), blink, iris, S, lash=(kind == 1), lid=(kind == 0))
        _brow(d, sc(cx + sx * 46), sc(ey - 46), S, rise, tilt * sx, hair, 10 if kind == 0 else 9, S, outline=(kind == 0))
    d.arc((sc(cx - 18), sc(hy + 44), sc(cx + 18), sc(hy + 80)), 25, 155, fill=_shade(skin, 0.6) + (255,), width=6)
    d.line([(sc(cx + 10), sc(hy + 22)), (sc(cx + 14), sc(hy + 56))], fill=_shade(skin, 0.78) + (255,), width=5)
    _blob(im, (sc(cx - 12), sc(hy + 52), sc(cx + 12), sc(hy + 70)), (255, 255, 255), 0.35, 5, S)
    d = ImageDraw.Draw(im, "RGBA")
    if kind == 0:                                                              # round gold glasses
        for sx in (-1, 1):
            d.ellipse((sc(cx + sx * 46 - 46), sc(ey - 40), sc(cx + sx * 46 + 46), sc(ey + 44)), outline=(212, 168, 70, 255), width=sc(5))
            d.arc((sc(cx + sx * 46 - 38), sc(ey - 34), sc(cx + sx * 46 + 2), sc(ey + 2)), 200, 270, fill=(250, 250, 250, 255), width=sc(3))
        d.arc((sc(cx - 14), sc(ey - 20), sc(cx + 14), sc(ey + 6)), 200, 340, fill=(212, 168, 70, 255), width=sc(4))
        for k in range(3):                                                     # smile lines
            d.arc((sc(cx + 78 + k * 4), sc(ey + 30 - k * 6), sc(cx + 96 + k * 4), sc(ey + 44 - k * 6)), 270, 90, fill=_shade(skin, 0.72) + (255,), width=5)
    mx, my = cx, hy + (84 if kind == 1 else 96)
    _mouth(d, sc(mx), sc(my), S, talk if speaking else 0.0, smile, S)
    return im.resize((600, 900), Image.LANCZOS)


# ================================================================ the two recurring hosts, as moody silhouettes
# Dark figures lit only by a cold rim light from the moon, with glowing eyes. They suit every dark theme and always look the same:
# kind 0 = the storyteller (broad shoulders, man bun, full beard), kind 1 = the reactor (long hair, slimmer, a loose strand on top).
HOST_SPEAKER = 1          # which host is talking in the current host scene: 0 the storyteller, 1 the reactor (set per shot)


def _cr(pts, n=10, closed=False):
    """Catmull-Rom spline through points: smooth organic outlines."""
    P = list(pts)
    P = ([P[-1]] + P + [P[0], P[1]]) if closed else ([P[0]] + P + [P[-1]])
    out = []
    for i in range(1, len(P) - 2):
        p0, p1, p2, p3 = P[i - 1], P[i], P[i + 1], P[i + 2]
        for k in range(n):
            u = k / n
            out.append(tuple(0.5 * ((2 * p1[j]) + (-p0[j] + p2[j]) * u + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * u * u + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * u ** 3) for j in (0, 1)))
    out.append(P[-2])
    return out


def _sil_sprite(kind, t, speaking, ph):
    from PIL import ImageChops, ImageFilter
    S = 2
    mask = Image.new("L", (600 * S, 900 * S), 0)
    m = ImageDraw.Draw(mask)
    pt = lambda L: [(x * S, y * S) for x, y in L]
    breath = 3 * math.sin(t * 1.6 + ph)
    tilt = (5 * math.sin(t * 7 + ph) if speaking else 2 * math.sin(t * 1.1 + ph))
    cx, hy = 300, 300 + breath
    # shoulders / torso
    if kind == 0:
        L = [(cx - 40, 498 + breath), (cx - 110, 510 + breath), (cx - 176, 532 + breath), (cx - 222, 580), (cx - 238, 660), (cx - 242, 900)]
    else:
        L = [(cx - 36, 498 + breath), (cx - 96, 512 + breath), (cx - 150, 536 + breath), (cx - 186, 590), (cx - 200, 680), (cx - 204, 900)]
    sh = _cr(list(reversed(L)) + [(2 * cx - x, y) for x, y in L], 6)
    m.polygon(pt(sh), fill=255)
    m.rectangle((pt([(cx - 36, hy + 90)])[0][0], (hy + 90) * S, (cx + 36) * S, 520 * S), fill=255)         # neck
    # head (rotated slightly with a tilt)
    def head_pts(rx, ry, top_flat=0.0):
        pts = []
        for k in range(48):
            a = 2 * math.pi * k / 48
            x, y = math.cos(a) * rx, math.sin(a) * ry
            if y > 0:
                x *= 1 - 0.18 * (y / ry) ** 2                                  # tapering jaw
            pts.append((cx + x + tilt * (-y / ry) * 0.5, hy + y))
        return pts
    m.polygon(pt(head_pts(100, 118)), fill=255)
    for sx in (-1, 1):                                                         # ears
        m.ellipse(((cx + sx * 104 - 14) * S, (hy - 6) * S, (cx + sx * 104 + 14) * S, (hy + 44) * S), fill=255)
    if kind == 0:
        m.ellipse(((cx - 40 + tilt * 0.4) * S, (hy - 214) * S, (cx + 48 + tilt * 0.4) * S, (hy - 128) * S), fill=255)                 # the man bun
        m.polygon(pt(_cr([(cx - 100, hy - 20), (cx - 96, hy - 100), (cx - 40, hy - 148), (cx + 10, hy - 156), (cx + 70, hy - 140), (cx + 100, hy - 90), (cx + 100, hy - 20)], 6)), fill=255)
        m.polygon(pt(_cr([(cx - 98, hy + 30), (cx - 100, hy + 90), (cx - 66, hy + 150), (cx - 20, hy + 186), (cx + 20, hy + 186), (cx + 66, hy + 150), (cx + 100, hy + 90), (cx + 98, hy + 30), (cx + 60, hy + 80), (cx - 60, hy + 80)], 6, closed=True)), fill=255)   # full beard
    else:
        m.polygon(pt(_cr([(cx - 112, hy - 20), (cx - 120, hy + 120), (cx - 150, hy + 320), (cx - 90, hy + 330), (cx - 84, hy + 140), (cx - 70, hy + 20)], 6, closed=True)), fill=255)
        m.polygon(pt(_cr([(cx + 112, hy - 20), (cx + 120, hy + 120), (cx + 150, hy + 320), (cx + 90, hy + 330), (cx + 84, hy + 140), (cx + 70, hy + 20)], 6, closed=True)), fill=255)
        m.polygon(pt(_cr([(cx - 112, hy), (cx - 106, hy - 100), (cx, hy - 152), (cx + 106, hy - 100), (cx + 112, hy)], 6)), fill=255)
        for k, (dx, dy) in enumerate(((-16, -150), (-34, -190), (-8, -222))):                                                       # a stray strand on top
            m.ellipse(((cx + dx - 5) * S, (hy + dy - 5) * S, (cx + dx + 5) * S, (hy + dy + 5) * S), fill=255)
    # light: dark body, bright cold rim on the edges facing the moon (upper right), a faint top-down gradient
    mask = mask.filter(ImageFilter.GaussianBlur(1.2))
    rim = ImageChops.subtract(mask, ImageChops.offset(mask, -9 * S, 7 * S)).filter(ImageFilter.GaussianBlur(2.2))
    rim = rim.point(lambda v: min(255, v * 3))
    im = Image.new("RGBA", mask.size, (0, 0, 0, 0))
    body = Image.new("RGBA", mask.size, (20, 20, 34, 0))
    body.putalpha(mask)
    im.alpha_composite(body)
    glow = Image.new("RGBA", mask.size, (150, 176, 230, 0))
    glow.putalpha(ImageChops.multiply(rim, mask).point(lambda v: int(v * 0.95)))
    im.alpha_composite(glow)
    # glowing eyes (pulse harder while speaking)
    d = ImageDraw.Draw(im, "RGBA")
    pulse = (0.65 + 0.35 * math.sin(t * 9 + ph)) if speaking else (0.5 + 0.12 * math.sin(t * 1.5 + ph))
    blink = 0.0
    ph_ = (t + ph) % 3.8
    if ph_ > 3.68:
        blink = 1 - abs(ph_ - 3.74) / 0.06
    col = (255, 196, 110) if kind == 0 else (150, 190, 255)
    for sx in (-1, 1):
        ex, ey = (cx + sx * 44 + tilt * 0.2) * S, (hy + 8) * S
        h = (6 if kind == 0 else 9) * S * max(0.12, 1 - 0.9 * blink)
        w = (26 if kind == 0 else 24) * S
        _blob(im, (ex - w * 1.8, ey - h * 3.2, ex + w * 1.8, ey + h * 3.2), col, 0.45 * pulse, 14, S)
        d = ImageDraw.Draw(im, "RGBA")
        d.polygon([(ex - w * sx * -1 * 0 - w, ey + h * 0.4), (ex, ey - h), (ex + w, ey + h * 0.2 - (4 * S if kind == 0 else 0)), (ex, ey + h)], fill=tuple(int(c * (0.7 + 0.3 * pulse)) for c in col) + (255,))
    return im.resize((600, 900), Image.LANCZOS)


def hosts_scene(out, dur, world, sp, cwd=None):
    sky = _gradient((22, 28, 66), (120, 92, 138), 1400)
    world.push, world.shake = 0.06, 0.0

    def paint(im, d, t, u, cam):
        im.paste(sky, (0, 0))
        d = ImageDraw.Draw(im, "RGBA")
        _stars_bg(d, 71, 70, 800, t)
        _moon(d, 840, 330, 90)
        for x, r, a in ((840, 520, 26), (840, 340, 30)):                         # cold halo behind the figures
            d.ellipse((x - r, 330 - r * 0.9, x + r, 330 + r * 0.9), fill=(150, 176, 230, a))
        d.polygon([(0, 1580), (300, 1520), (700, 1580), (W, 1530), (W, H), (0, H)], fill=(10, 10, 20, 255))
        k = _smooth(u / 0.2)                                                    # they step out of the dark into the frame
        for kind, x, ph in ((0, 235 - 120 * (1 - k), 0.0), (1, 770 + 120 * (1 - k), 1.7)):
            spr = _sil_sprite(kind, t, kind == HOST_SPEAKER, ph)
            if k < 1:
                a = spr.getchannel("A").point(lambda v, k=k: int(v * k))
                spr.putalpha(a)
            spr = spr.resize((750, 1125), Image.LANCZOS)
            im.paste(spr, (int(x - 375), 860), spr)
        d = ImageDraw.Draw(im, "RGBA")
        fl = 0.8 + 0.2 * math.sin(t * 13) * math.sin(t * 7.3)                      # a small fire between them: they are telling this story to someone
        d.ellipse((540 - 460 * fl, 1700 - 300 * fl, 540 + 460 * fl, 1700 + 300 * fl), fill=(255, 150, 60, 34))
        d.ellipse((540 - 260 * fl, 1700 - 170 * fl, 540 + 260 * fl, 1700 + 170 * fl), fill=(255, 170, 70, 46))
        for fx, fh in ((-26, 90), (0, 130), (26, 84)):
            hh = fh * (0.85 + 0.25 * math.sin(t * 11 + fx))
            d.polygon([(540 + fx - 22, 1730), (540 + fx + 4 * math.sin(t * 9 + fx), 1730 - hh), (540 + fx + 22, 1730)], fill=(255, 176, 64, 235))
            d.polygon([(540 + fx - 11, 1730), (540 + fx, 1730 - hh * 0.62), (540 + fx + 11, 1730)], fill=(255, 236, 160, 245))
        for k2 in range(5):                                                     # mist drifting across their feet
            y = 1640 + k2 * 40
            xo = (t * (20 + k2 * 8) + k2 * 190) % (W + 600) - 300
            d.ellipse((xo - 380, y - 30, xo + 380, y + 30), fill=(150, 160, 200, 26))
        _BOXES.append((100, 900, 980, 1900))
        dim(im, 4)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": []}


STORY_FUNCS.update({"hosts": hosts_scene})


# ================================================================ the close-up kill (claws and fangs entering skin)
# A macro shot of the prey's hide. Claws hover, strike (hit-stop + flash), the skin dimples and PUNCTURES, then the four claws
# rake and four wounds open layer by layer (cut skin edge, red flesh, dark depth, muscle fibre, wet shine) while blood wells,
# runs and sprays. Then the jaws close from above and below and the fangs sink in. Everything is drawn at 2x and reduced,
# so every edge stays sharp.
_MAUL_CACHE = {}


def _sm(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def _hide_base(col, kind):
    key = (tuple(int(c) for c in col[:3]), kind)
    if key in _MAUL_CACHE:
        return _MAUL_CACHE[key]
    rs = np.random.RandomState(7)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    light = 1.18 - 0.55 * np.clip(((xx / W) * 0.55 + (yy / H) * 0.65), 0, 1)           # lit from the upper left
    light *= 1 - 0.28 * np.clip(((xx - W / 2) ** 2 + (yy - H * 0.5) ** 2) / (W * 0.95) ** 2, 0, 1)
    base = np.array(col[:3], dtype=np.float32)[None, None, :] * light[..., None]
    img = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(img, "RGBA")
    rnd = random.Random(5)
    if kind == "fur":
        for i in range(17000):
            x, y = rnd.uniform(-20, W), rnd.uniform(-20, H)
            a = math.radians(rnd.uniform(70, 112))
            L = rnd.uniform(14, 36)
            k = rnd.uniform(0.55, 1.4)
            c = tuple(int(max(0, min(255, v * k))) for v in col[:3])
            d.line((x, y, x + math.cos(a) * L, y + math.sin(a) * L), fill=c + (rnd.randint(90, 200),), width=rnd.choice((2, 2, 3)))
    else:                                            # cloth: weave and soft folds
        for x in range(0, W, 5):
            d.line((x, 0, x, H), fill=(0, 0, 0, 22), width=1)
        for y in range(0, H, 5):
            d.line((0, y, W, y), fill=(255, 255, 255, 12), width=1)
        for i in range(9):
            x0 = rnd.uniform(-200, W)
            d.line((x0, 0, x0 + rnd.uniform(-260, 260), H), fill=(0, 0, 0, 34), width=rnd.randint(30, 90))
    img = img.filter(ImageFilter.GaussianBlur(0.7))
    _MAUL_CACHE[key] = img
    return img


class _D2:
    """Draws at twice the size so edges are crisp after reduction."""
    def __init__(self, size):
        self.im = Image.new("RGBA", (size[0] * 2, size[1] * 2), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.im, "RGBA")

    def poly(self, pts, fill):
        if len(pts) >= 3:
            self.d.polygon([(x * 2, y * 2) for x, y in pts], fill=fill)

    def line(self, pts, fill, width=2):
        self.d.line([(x * 2, y * 2) for x, y in pts], fill=fill, width=max(1, int(width * 2)), joint="curve")

    def ell(self, box, fill, outline=None, width=1):
        self.d.ellipse([c * 2 for c in box], fill=fill, outline=outline, width=int(width * 2))

    def done(self):
        return self.im.reduce(2)


def _strip(path, half):
    """Left and right edges of a ribbon around a centre path with a half width at each point."""
    L, R = [], []
    for i, ((x, y), h) in enumerate(zip(path, half)):
        a, b = path[max(i - 1, 0)], path[min(i + 1, len(path) - 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        n = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / n, dx / n
        L.append((x + nx * h, y + ny * h))
        R.append((x - nx * h, y - ny * h))
    return L + R[::-1]


def _claw_poly(tip, dirv, length, thick, bend, q0=0.0):
    """A hooked, tapering claw: sharp tip, thick root, curving away to one side."""
    nx, ny = -dirv[1], dirv[0]
    pts = [(tip[0] - dirv[0] * length * q + nx * bend * length * q * q,
            tip[1] - dirv[1] * length * q + ny * bend * length * q * q) for q in [i / 18 for i in range(19)]]
    half = [1.0 + thick * (q ** 0.85) for q in [i / 18 for i in range(19)]]
    return pts, half


def _sucker(V, x, y, r, a, col):
    """A round sucker with a ring of inward-pointing teeth, seen from above."""
    dk = tuple(int(c * 0.35 + 14) for c in col[:3])
    fl = tuple(min(255, int(c * 0.6 + 90)) for c in col[:3])
    V.ell((x - r * 1.18, y - r * 1.18 * 0.82, x + r * 1.18, y + r * 1.18 * 0.82), dk + (a,))
    V.ell((x - r, y - r * 0.82, x + r, y + r * 0.82), fl + (a,))
    for j in range(16):
        an = j * 6.2832 / 16
        c1, s1, c2, s2 = math.cos(an), math.sin(an), math.cos(an + 0.2), math.sin(an + 0.2)
        V.poly([(x + c1 * r * 0.94, y + s1 * r * 0.78), (x + c2 * r * 0.94, y + s2 * r * 0.78), (x + math.cos(an + 0.1) * r * 0.5, y + math.sin(an + 0.1) * r * 0.41)], (240, 232, 210, a))
    V.ell((x - r * 0.42, y - r * 0.34, x + r * 0.42, y + r * 0.34), (36, 3, 6, a))


def maul_frame(im, t, u, sp, prey_col=(200, 170, 130), kind="fur", seed=1):
    """One frame of the close-up kill. u runs 0..1 over the whole close-up. Returns a new RGB image."""
    _CLOSEUP[0] = True
    rnd = random.Random(seed)
    # hit-stop: time freezes for a moment at the instant of contact
    U_IMP, HOLD = 0.30, 0.03
    ue = u if u < U_IMP else (U_IMP if u < U_IMP + HOLD else u - HOLD)
    ue = ue / (1 - HOLD)
    U_IMP_E = U_IMP / (1 - HOLD)
    base = _hide_base(prey_col, kind).copy()
    pc = tuple(int(c) for c in (sp.get("color") or (70, 58, 50))[:3])
    claw_dark = tuple(int(c * 0.35 + 18) for c in pc)
    horn = (226, 210, 176)
    dirv = (0.30, 0.954)
    nrm = (-dirv[1], dirv[0])                              # across the claws
    P0 = (470, 430)                                        # where the first claw lands
    gap = 86.0
    LEN = 1000.0
    n_claw = 4
    strike = strike_of(sp)
    wscale, pz_, n_sp = 1.0, 1.0, 26
    claw_len, claw_thick, claw_bend = 520.0, 34.0, 0.22
    starts = [(P0[0] - nrm[0] * (i - 1.5) * gap, P0[1] - nrm[1] * (i - 1.5) * gap + (i % 2) * 14) for i in range(n_claw)]
    if strike != "claws":                                  # a body without claws leaves its own kind of wound
        rs_ = random.Random(seed * 7 + 3)
        n_sp = 6
        if strike == "swarm":                              # a cloud of tiny stings
            n_claw, LEN, wscale, pz_ = 34, 50.0, 0.42, 0.6
            starts = [(540 + rs_.uniform(-1, 1) * 330, 700 + rs_.uniform(-1, 1) * 300) for _ in range(n_claw)]
            n_sp = 2
        elif strike == "suckers":                          # round rings of teeth pressed on the skin
            n_claw, LEN, wscale, pz_ = 7, 34.0, 0.95, 1.3
            starts = [(540 + math.cos(a_) * (250 if i % 2 else 120) , 700 + math.sin(a_) * (230 if i % 2 else 100)) for i, a_ in ((i, i * 6.2832 / 7) for i in range(7))]
        elif strike == "bite":                             # two curved rows of tooth punctures
            n_claw, LEN, wscale, pz_ = 12, 170.0, 0.95, 2.1
            n_sp = 12
            starts = [(540 + (i % 6 - 2.5) * 140, (520 + 30 * abs(i % 6 - 2.5)) if i < 6 else (890 - 30 * abs(i % 6 - 2.5))) for i in range(12)]
        else:                                              # thorns driven in and dragged
            n_claw, LEN, wscale, pz_ = 9, 150.0, 0.8, 1.1
            starts = [(300 + rs_.uniform(0, 1) * 480, 440 + (i * 72) % 560 + rs_.uniform(-18, 18)) for i in range(9)]
            claw_len, claw_thick, claw_bend = 270.0, 17.0, 0.04
    t_drag0, t_drag1 = U_IMP_E, 0.62
    g = _sm((ue - t_drag0) / (t_drag1 - t_drag0))
    s_now = g * LEN * (1 if ue >= t_drag0 else 0)
    lip = (214, 160, 136) if kind == "fur" else (222, 168, 140)
    V = _D2((W, H))
    breathe = 1 + 0.012 * math.sin(t * 5.0)

    # ---- wounds -------------------------------------------------------------------------------------------------
    def age_open(s):
        pa = t_drag0 + (t_drag1 - t_drag0) * (s / LEN)      # when the claw passed this point
        a = ue - pa
        if a <= 0:
            return 0.0, 0.0
        return min(1.0, _sm(a / 0.09) * 0.8 + min(0.2, a * 0.8)), a
    wound_paths = []
    stain_k = 1.9 if strike == "bite" else (1.3 if strike in ("suckers", "thorns") else 1.0)
    if ue >= U_IMP_E:
        for i, (sx, sy) in enumerate(starts):
            path, half, ages = [], [], []
            n = max(2, int(s_now / 10) + 1)
            for k in range(n):
                s = min(s_now, k * 10.0)
                wob = 9 * math.sin(s * 0.021 + i * 1.7) + 4 * math.sin(s * 0.057 + i)
                x, y = sx + dirv[0] * s + nrm[0] * wob, sy + dirv[1] * s + nrm[1] * wob
                o, a = age_open(s)
                tp0_, tp1_ = min(60.0, LEN * 0.4), min(90.0, LEN * 0.6)
                taper = min(1.0, s / tp0_) * min(1.0, (LEN - s + 40) / tp1_)
                w = (3 + 56 * wscale * o * (0.78 + 0.22 * math.sin(s * 0.05 + i))) * taper
                path.append((x, y)); half.append(w); ages.append((o, a))
            wound_paths.append((path, half, ages))
    # stain: the hide around a wound darkens and reddens
    for path, half, ages in wound_paths:
        if len(path) > 2:
            V.line(path, (110, 8, 10, 46), 150 * stain_k)
            V.line(path, (110, 8, 10, 60), 84 * stain_k)
    # the cut edges, the flesh, the depth, the fibres and the wet shine
    for path, half, ages in wound_paths:
        if len(path) < 3:
            continue
        V.poly(_strip(path, [h + 9 for h in half]), lip + (255,))
        V.poly(_strip(path, [h + 4 for h in half]), (232, 188, 168, 255))
        V.poly(_strip(path, half), (152, 20, 28, 255))
        V.poly(_strip(path, [h * 0.62 for h in half]), (96, 8, 14, 255))
        V.poly(_strip(path, [h * 0.28 for h in half]), (42, 3, 6, 255))
        for k in range(2, len(path) - 2, 3):                 # muscle fibres crossing the wound
            x, y = path[k]
            h = half[k]
            if h > 8:
                for j in (-0.5, 0.0, 0.5):
                    V.line([(x + nrm[0] * h * j * 1.1 - nrm[0] * h * 0.4, y + nrm[1] * h * j * 1.1 - nrm[1] * h * 0.4),
                            (x + nrm[0] * h * j * 1.1 + nrm[0] * h * 0.4 + dirv[0] * 8, y + nrm[1] * h * j * 1.1 + nrm[1] * h * 0.4 + dirv[1] * 8)],
                           (196, 58, 56, 150), 2)
        edge = [(x + nrm[0] * h * 0.72, y + nrm[1] * h * 0.72) for (x, y), h in zip(path, half)]
        V.line(edge, (255, 190, 180, 150), 3)                # wet shine along the lip
        # blood welling over the lip
        V.poly(_strip(path, [h * 1.12 + 2 for h in half]), (170, 12, 18, 120))
    # rivulets running down from the wounds: a few thick, straight runs with a bead of blood at the end
    for wi, (path, half, ages) in enumerate(wound_paths):
        for k in (range(14 + wi * 3, len(path), 21) if strike == "claws" else range(2 + wi % 3, len(path), 6)):
            o, a = ages[k]
            if o < 0.4:
                continue
            x, y = path[k]
            Ld = 70 + 300 * _sm(a / 0.34) * (0.55 + 0.45 * ((k * 7 + wi * 3) % 5) / 4)
            pts = [(x + 5 * math.sin(j * 0.03 + k), y + j) for j in range(0, int(Ld), 14)]
            if len(pts) > 1:
                V.line(pts, (88, 4, 8, 240), 15)
                V.line(pts, (170, 12, 18, 255), 10)
                V.line([(px - 3, py) for px, py in pts], (255, 130, 120, 140), 3)
                px, py = pts[-1]
                V.ell((px - 11, py - 8, px + 11, py + 18), (150, 8, 14, 255))
                V.ell((px - 5, py - 4, px - 1, py + 4), (255, 170, 160, 190))
    # claw punctures at the instant of contact: tented skin, then a ragged hole with flaps
    if ue >= U_IMP_E - 0.05:
        for i, (sx, sy) in enumerate(starts):
            tent = _sm((ue - (U_IMP_E - 0.05)) / 0.05)
            if ue < U_IMP_E:
                V.ell((sx - 34 * tent * pz_, sy - 26 * tent * pz_, sx + 34 * tent * pz_, sy + 26 * tent * pz_), (0, 0, 0, int(80 * tent)))
                V.ell((sx - 18 * tent * pz_, sy - 14 * tent * pz_, sx + 18 * tent * pz_, sy + 14 * tent * pz_), (255, 230, 210, int(60 * tent)))
            else:
                if strike != "claws":                         # the skin around a bite swells and bruises; the teeth leave a ring of dents
                    bru = _sm((ue - U_IMP_E) / 0.22)
                    for rad, col_ in ((130, (92, 28, 74, 64)), (92, (138, 24, 44, 92)), (60, (176, 16, 26, 126))):
                        V.ell((sx - rad * pz_ * bru * 0.6, sy - rad * 0.72 * pz_ * bru * 0.6, sx + rad * pz_ * bru * 0.6, sy + rad * 0.72 * pz_ * bru * 0.6), col_)
                    for j in range(9 if strike == "bite" else 5):     # tears that run out from the hole through the skin
                        aj = j * 6.2832 / (9 if strike == "bite" else 5) + i * 0.7
                        Lj = (34 + 26 * ((j * 5 + i) % 3)) * pz_ * bru
                        V.line([(sx, sy), (sx + math.cos(aj) * Lj * 0.5 + 3, sy + math.sin(aj) * Lj * 0.4), (sx + math.cos(aj) * Lj, sy + math.sin(aj) * Lj * 0.8)], (96, 6, 12, 230), 5)
                for j in range(7):                            # flaps of skin pushed inwards
                    a = j * math.tau / 7 + i if hasattr(math, "tau") else j * 6.2832 / 7 + i
                    V.poly([(sx, sy), (sx + math.cos(a) * 26 * pz_, sy + math.sin(a) * 20 * pz_), (sx + math.cos(a + 0.5) * 17 * pz_, sy + math.sin(a + 0.5) * 13 * pz_)], lip + (255,))
                V.ell((sx - 13 * pz_, sy - 10 * pz_, sx + 13 * pz_, sy + 10 * pz_), (70, 4, 8, 255))
    # ---- blood jets that burst from the holes at the moment of the bite ---------------------------------------------------
    if ue >= U_IMP_E and strike in ("bite", "suckers", "thorns"):
        ka_ = (ue - U_IMP_E) / 0.22
        if ka_ < 1.0:
            for i, (sx, sy) in enumerate(starts):
                rj = random.Random(seed * 131 + i)
                for j in range(9 if strike == "bite" else 5):
                    aj = rj.uniform(-3.05, -0.1)
                    Lr = rj.uniform(120, 340) * min(pz_, 1.8) * (ka_ ** 0.55)
                    x2, y2 = sx + math.cos(aj) * Lr, sy + math.sin(aj) * Lr + 260 * ka_ * ka_
                    a_ = int(240 * (1 - ka_))
                    V.line([(sx, sy), ((sx + x2) / 2, (sy + y2) / 2 - 20 * ka_), (x2, y2)], (150, 8, 14, a_), max(2.0, 11 * (1 - ka_)))
                    V.ell((x2 - 8, y2 - 8, x2 + 8, y2 + 8), (176, 12, 18, a_))
    # ---- spray from the claw tips -----------------------------------------------------------------------------------
    if ue >= U_IMP_E:
        for i, (sx, sy) in enumerate(starts):
            for pidx in range(n_sp):
                tb = t_drag0 + (t_drag1 - t_drag0) * (pidx / float(n_sp)) + 0.004 * i
                if ue < tb:
                    continue
                age = (ue - tb) / 0.16
                if age > 1.0:
                    continue
                s = (pidx / float(n_sp)) * LEN
                bx, by = sx + dirv[0] * s, sy + dirv[1] * s
                rr = random.Random(seed * 1000 + i * 50 + pidx)
                vx, vy = rr.uniform(-520, 520) + dirv[0] * 380, rr.uniform(-760, -120)
                px, py = bx + vx * age * 0.45, by + vy * age * 0.45 + 1500 * (age * 0.45) ** 2
                qx, qy = bx + vx * max(0, age - 0.05) * 0.45, by + vy * max(0, age - 0.05) * 0.45 + 1500 * (max(0, age - 0.05) * 0.45) ** 2
                r = rr.uniform(3.5, 9) * (1 - 0.4 * age) * (1.5 if strike == "bite" else 1.0)
                V.line([(qx, qy), (px, py)], (130, 6, 10, 220), r * 1.2)
                V.ell((px - r, py - r, px + r, py + r), (176, 12, 18, 255))
    # ---- the claws ------------------------------------------------------------------------------------------------
    t_hover = 0.0
    if ue < t_drag1 + 0.09 and strike in ("claws", "thorns"):
        for i, (sx, sy) in enumerate(starts):
            if ue < U_IMP_E:
                k = _sm((ue - 0.14) / (U_IMP_E - 0.14))            # the strike: from high above down into the skin
                hover = 330 * (1 - k) + 9 * math.sin(t * 22 + i) * (1 - k) + (0 if ue > 0.12 else 40 * (1 - ue / 0.12))
                tip = (sx - dirv[0] * hover, sy - dirv[1] * hover)
            elif ue <= t_drag1:
                tip = (sx + dirv[0] * s_now + nrm[0] * 9 * math.sin(s_now * 0.021 + i * 1.7), sy + dirv[1] * s_now + nrm[1] * 9 * math.sin(s_now * 0.021 + i * 1.7))
            else:
                lift = _sm((ue - t_drag1) / 0.09)
                tip = (sx + dirv[0] * LEN - dirv[0] * 380 * lift, sy + dirv[1] * LEN - dirv[1] * 380 * lift - 140 * lift)
            height = 330 * (1 - _sm((ue - 0.14) / (U_IMP_E - 0.14))) if ue < U_IMP_E else (0 if ue <= t_drag1 else 200 * _sm((ue - t_drag1) / 0.09))
            lenc = claw_len + (40 if strike == "claws" else 25) * (i % 2)
            pts, half = _claw_poly(tip, dirv, lenc, claw_thick, claw_bend)
            for off, al in ((10, 44), (6, 32), (3, 22)):                          # cast shadow: closer to the skin when the claw is down
                sh = off + height * 0.10
                V.poly(_strip([(x + sh * 1.2, y + sh * 1.6) for x, y in pts], [h * 1.1 for h in half]), (0, 0, 0, al))
            V.poly(_strip(pts, half), claw_dark + (255,))
            V.poly(_strip([(x - nrm[0] * h * 0.18, y - nrm[1] * h * 0.18) for (x, y), h in zip(pts, half)], [h * 0.62 for h in half]), horn + (255,))
            V.poly(_strip([(x - nrm[0] * h * 0.30, y - nrm[1] * h * 0.30) for (x, y), h in zip(pts, half)], [h * 0.16 for h in half]), (255, 252, 240, 235))
            if ue >= U_IMP_E:                                                    # the blood on the tip and along the claw
                cut = [(x, y) for (x, y) in pts[:5]]
                V.poly(_strip(cut, [h * 1.05 for h in half[:5]]), (150, 8, 14, 235))
                V.line(cut, (255, 120, 110, 140), 2)
            else:
                V.line(pts[:4], (255, 255, 255, 140), 2)
        # the toe the claws grow from: it sits BEHIND the claw roots, up and away from the skin
        tipc = [(0.0, 0.0)]
        if strike == "claws":
            if ue < U_IMP_E:
                k = _sm((ue - 0.14) / (U_IMP_E - 0.14)); hv = 330 * (1 - k)
            elif ue <= t_drag1:
                hv = -s_now
            else:
                hv = -LEN + 380 * _sm((ue - t_drag1) / 0.09)
            tx = sum(s_[0] for s_ in starts) / n_claw
            ty = sum(s_[1] for s_ in starts) / n_claw
            if ue > t_drag1:
                ty -= 140 * _sm((ue - t_drag1) / 0.09)
            bx_, by_ = tx - dirv[0] * (hv + 500), ty - dirv[1] * (hv + 500)         # roots of the claws
            axis = [(bx_ - dirv[0] * 760 * q, by_ - dirv[1] * 760 * q) for q in [i / 10 for i in range(11)]]
            wid = [190 + 70 * q for q in [i / 10 for i in range(11)]]
            V.poly(_strip(axis, [w + 14 for w in wid]), tuple(min(255, c + 70) for c in pc) + (255,))      # rim light
            V.poly(_strip(axis, wid), pc + (255,))
            V.poly(_strip([(x + nrm[0] * 40, y + nrm[1] * 40) for x, y in axis], [w * 0.55 for w in wid]), tuple(int(c * 0.7) for c in pc) + (255,))
            for q in range(1, 9):                                                    # scales / knuckle creases
                x, y = axis[q]
                V.line([(x - nrm[0] * wid[q], y - nrm[1] * wid[q]), (x + nrm[0] * wid[q], y + nrm[1] * wid[q])], tuple(int(c * 0.5) for c in pc) + (190,), 5)
    if ue < t_drag1 + 0.09 and strike in ("suckers", "swarm"):                   # rings of teeth pressed on, or a cloud of tiny pincers
        for i, (sx, sy) in enumerate(starts):
            if ue < U_IMP_E:
                k = _sm((ue - 0.14) / (U_IMP_E - 0.14)); sc_ = 1.0 + 1.1 * (1 - k); al = int(255 * min(1.0, k * 2 + 0.15))
            elif ue <= t_drag1:
                sc_, al = 1.0, 255
            else:
                lift = _sm((ue - t_drag1) / 0.09); sc_, al = 1.0 + 0.9 * lift, int(255 * (1 - lift))
            if al <= 4:
                continue
            if strike == "suckers":
                _sucker(V, sx, sy, (52 + 14 * (i % 2)) * sc_, al, pc)
            else:
                for sg_ in (-1, 1):
                    V.line([(sx + sg_ * 14 * sc_, sy - 34 * sc_), (sx + sg_ * 5 * sc_, sy - 10 * sc_), (sx, sy + 4)], claw_dark + (al,), 5)
    # ---- the jaws ---------------------------------------------------------------------------------------------------
    j0, j1 = 0.60, 0.80
    if ue >= j0 - 0.02 and strike in ("claws", "bite"):
        close = _sm((ue - j0) / (j1 - j0))
        bite = _sm((ue - j1) / 0.07)
        top_y = -320 + 1250 * close - 14 * bite           # the tooth line of the upper jaw
        bot_y = 2300 - 1210 * close + 14 * bite           # and of the lower jaw
        gum_c = (146, 46, 58)
        for ri, (yb, sg) in enumerate(((top_y, -1), (bot_y, 1))):
            gum_y = yb + sg * 200                          # the gum line, set back from the tooth tips
            wav = [(x, gum_y + 16 * math.sin(x * 0.02 + ri * 2)) for x in range(-40, W + 60, 40)]
            V.poly(wav + [(W + 40, gum_y + sg * 900), (-40, gum_y + sg * 900)], gum_c + (255,))
            V.poly([(x, y + sg * 40) for x, y in wav] + [(W + 40, gum_y + sg * 900), (-40, gum_y + sg * 900)], (112, 30, 42, 255))
            V.line([(x, y + sg * 8) for x, y in wav], (255, 176, 176, 160), 5)             # wet shine on the gum
            xs = [60, 190, 300, 420, 540, 660, 780, 890, 1010]
            for ti, x in enumerate(xs):
                big = ti in (1, 7)
                extra = 70 if big else 8 + 22 * ((ti * 5) % 3)
                bw = 76 if big else 56
                base_y = gum_y + sg * 6
                tipy = yb + (-sg) * extra
                tipx = x + (ti - 4) * 7 + (24 if ri else -24)
                hook = 20 * (1 if ti % 2 else -1)
                qs = [i / 10 for i in range(11)]
                left = [(x - bw * (1 - q) + (tipx - x) * q - hook * q * q, base_y + (tipy - base_y) * q) for q in qs]
                right = [(x + bw * (1 - q) + (tipx - x) * q - hook * q * q, base_y + (tipy - base_y) * q) for q in qs]
                V.poly(left + right[::-1], (238, 228, 200, 255))
                V.poly([((a_[0] + b_[0]) / 2 + 4, a_[1]) for a_, b_ in zip(left, right)] + right[::-1], (200, 182, 142, 255))        # shaded side
                V.line([(a_[0] + (b_[0] - a_[0]) * 0.18, a_[1]) for a_, b_ in zip(left[:9], right[:9])], (255, 253, 242, 230), 6)   # highlight
                V.poly([(x - bw - 6, base_y), (x + bw + 6, base_y), (x + bw * 0.8, base_y + sg * 30), (x - bw * 0.8, base_y + sg * 30)], (170, 70, 82, 255))   # gum cuff
                if bite > 0:                                                       # blood on the tips that sank in
                    V.poly(left[-4:] + right[-4:][::-1], (158, 10, 16, 245))
            for k_ in range(7):                                                    # strands of saliva
                x = 140 + k_ * 135
                sag = 40 + 30 * math.sin(t * 3 + k_) + 60 * close
                V.line([(x, gum_y + sg * 10), (x + 6, gum_y + sg * (10 + sag)), (x - 4, gum_y + sg * (10 + sag * 1.8))], (255, 255, 255, 110), 3)
        if bite > 0:                                                               # punctures where the fangs went in
            for ti, x in enumerate([190, 420, 660, 890]):
                cy = 1010 + (ti % 2) * 14
                for rad, al in ((190, 52), (140, 86), (98, 124), (64, 168)):
                    V.ell((x - rad * bite, cy - rad * 0.58 * bite, x + rad * bite, cy + rad * 0.58 * bite), (120, 6, 10, al))
                for jf in range(9):                                                # torn skin around each hole
                    af = jf * 6.2832 / 9 + ti
                    V.poly([(x, cy), (x + math.cos(af) * 78 * bite, cy + math.sin(af) * 46 * bite), (x + math.cos(af + 0.5) * 50 * bite, cy + math.sin(af + 0.5) * 30 * bite)], lip + (255,))
                V.ell((x - 42 * bite, cy - 25 * bite, x + 42 * bite, cy + 25 * bite), (40, 2, 4, 255))
                V.ell((x - 18 * bite, cy - 10 * bite, x - 6 * bite, cy - 3 * bite), (255, 150, 140, 170))
                k = _sm((ue - j1) / 0.18)
                for j in range(8):
                    rr = random.Random(ti * 9 + j)
                    xx = x + rr.uniform(-36, 36)
                    pts = [(xx + rr.uniform(-4, 4), cy + y_) for y_ in range(0, int(60 + rr.uniform(80, 420) * k), 10)]
                    if len(pts) > 1:
                        V.line(pts, (150, 8, 14, 235), 14)
                        V.line(pts, (210, 30, 34, 200), 5)
    over = V.done()
    out = base.convert("RGBA")
    out.alpha_composite(over)
    out = out.convert("RGB")
    # ---- grade: impact flash, creeping red, vignette, camera shake and a slow push
    arr = out
    if U_IMP_E - 0.002 <= ue < U_IMP_E + 0.045:
        k = 1 - (ue - U_IMP_E) / 0.045
        arr = Image.blend(arr, Image.new("RGB", arr.size, (255, 244, 235)), 0.36 * max(0, k) ** 2.2)
    red = 0.05 + 0.20 * _sm((ue - U_IMP_E) / 0.6) if ue > U_IMP_E else 0.0
    if red:
        arr = Image.blend(arr, Image.new("RGB", arr.size, (110, 0, 0)), red)
    shake = 0.0
    if ue >= U_IMP_E:
        shake = (16 + (10 if strike == "bite" else 0)) * max(0.0, 1 - (ue - U_IMP_E) / 0.4) + (10 if j0 <= ue <= j1 + 0.1 else 0) * 0.7
    z = 1.0 + 0.07 * u
    cw, chh = W / z, H / z
    cx = W / 2 + shake * math.sin(t * 83)
    cy = H / 2 + shake * math.cos(t * 71)
    arr = arr.crop((int(cx - cw / 2), int(cy - chh / 2), int(cx + cw / 2), int(cy + chh / 2))).resize((W, H), Image.BILINEAR)
    vig = _MAUL_CACHE.get("vig")
    if vig is None:
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        r = np.sqrt(((xx - W / 2) / (W * 0.6)) ** 2 + ((yy - H / 2) / (H * 0.6)) ** 2)
        v = np.zeros((H, W, 4), dtype=np.uint8)
        v[..., 0] = 20
        v[..., 3] = (np.clip((r - 0.55) / 0.6, 0, 1) ** 1.6 * 190).astype(np.uint8)
        vig = _MAUL_CACHE["vig"] = Image.fromarray(v, "RGBA")
    arr = arr.convert("RGBA")
    arr.alpha_composite(vig)
    return arr.convert("RGB")


_maul_frame_impl = maul_frame


def maul_frame(*a, **k):
    """This close-up is built (and supersampled) as one 1080-based picture, so it is made the normal way and enlarged afterwards."""
    return _raw(_maul_frame_impl, *a, **k)


def maul_scene(out, dur, world, sp, prey_text="", cwd=None):
    """The close-up kill as its own scene (an insert for hunt / attack sentences)."""
    prey, _ = prey_spec(prey_text)
    pcol = tuple(int(c) for c in prey["color"][:3])
    world.push, world.shake = 0.0, 0.0
    world.no_cinema = True

    def paint(im, d, t, u, cam):
        _BOXES.append((100, 540, 980, 1240))
        return maul_frame(im, t, u, sp, pcol, "fur", seed=3)
    write_video(out, dur, paint, world, cwd, cam_dir=0)
    return {"hits": [dur * 0.30]}


INSERT_FUNCS["maul"] = maul_scene


# ================================================================ the opening hook (first 1.7 seconds of every video)
# A flash of fangs on the scream, then glowing eyes pushing in out of the dark, then the story's first picture dissolves in.
HOOK_LEN = 1.7


def hook_frame(t, eye=(255, 170, 40), seed=3):
    im = Image.new("RGB", (W, H), (4, 3, 6))
    cache = _MAUL_CACHE.get("hookfog")
    if cache is None:
        rs = np.random.RandomState(4)
        n = Image.fromarray((rs.rand(48, 27) * 255).astype(np.uint8)).resize((W + 400, H), Image.BICUBIC).filter(ImageFilter.GaussianBlur(60))
        cache = _MAUL_CACHE["hookfog"] = np.asarray(n, dtype=np.float32) / 255.0
    off = int(t * 90) % 380
    fog = cache[:, off:off + W]
    base = np.zeros((H, W, 3), dtype=np.float32)
    base += np.array([10, 8, 14], dtype=np.float32)
    base += fog[..., None] * np.array([26, 18, 30], dtype=np.float32)
    # a low red glow behind the eyes
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    k = _sm((t - 0.3) / 0.5)
    r2 = ((xx - W / 2) / 520) ** 2 + ((yy - 900) / 420) ** 2
    base += (np.exp(-r2 * 1.6) * 38 * k)[..., None] * np.array([1.0, 0.25, 0.12], dtype=np.float32)
    im = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(im, "RGBA")
    if 0.08 <= t < 0.30:                                              # the flash: a mouth of fangs, on the scream
        f = 1 - (t - 0.08) / 0.22
        cx, cy = W / 2, 930
        spread = 1.0 + 0.5 * (1 - f)
        d.ellipse((cx - 400 * spread, cy - 250, cx + 400 * spread, cy + 250), fill=(60, 4, 8, 255))
        d.ellipse((cx - 300 * spread, cy - 170, cx + 300 * spread, cy + 170), fill=(150, 18, 22, 255))
        for ri, sg in enumerate((-1, 1)):
            for j in range(9):
                x = cx + (j - 4) * 84 * spread
                L = 210 if j in (1, 7) else 130 + 20 * ((j * 5) % 3)
                y0 = cy + sg * 236
                d.polygon([(x - 38, y0), (x + 38, y0), (x + (j - 4) * 4, y0 - sg * L)], fill=(238, 228, 200, 255))
                d.polygon([(x, y0), (x + 38, y0), (x + (j - 4) * 4, y0 - sg * L)], fill=(196, 178, 140, 255))
        im = Image.blend(im, Image.new("RGB", im.size, (255, 236, 220)), 0.45 * f ** 1.5)
        return im
    a = _sm((t - 0.30) / 0.25) * (1 - _sm((t - 1.45) / 0.25))
    s = 1.0 + 1.2 * _sm((t - 0.45) / 1.1)                             # the eyes push in
    cx, cy = W / 2, 900 - 40 * _sm((t - 0.45) / 1.1)
    spread = 190 * s
    r = 15 * s
    blink = 1.0 - (1.0 - min(1.0, abs(t - 0.78) / 0.07)) * 0.95 if abs(t - 0.78) < 0.07 else 1.0
    for sgn in (-1, 1):
        x = cx + sgn * spread / 2
        for rr, al in ((r * 5.0, 26), (r * 3.0, 56), (r * 1.9, 110)):
            d.ellipse((x - rr, cy - rr * blink, x + rr, cy + rr * blink), fill=tuple(eye) + (int(al * a),))
        d.ellipse((x - r * 1.15, cy - r * 0.8 * blink, x + r * 1.15, cy + r * 0.8 * blink), fill=tuple(min(255, c + 40) for c in eye) + (int(255 * a),))
        d.ellipse((x - r * 0.28, cy - r * 0.8 * blink, x + r * 0.28, cy + r * 0.8 * blink), fill=(8, 6, 10, int(255 * a)))
    return im


_hook_frame_impl = hook_frame


def hook_frame(*a, **k):
    return _raw(_hook_frame_impl, *a, **k)


def hook_clip(out, eye=(255, 170, 40), cwd=None):
    """The hook as its own clip. The final video lays it over the first 1.7 seconds."""
    w = World("a dark forest", seed=1)
    w.push, w.shake = 0.0, 0.0
    w.no_cinema = True

    def paint(im, d, t, u, cam):
        _CLOSEUP[0] = True
        return hook_frame(t, eye)
    write_video(out, HOOK_LEN, paint, w, cwd, cam_dir=0)


# ================================================================ abilities shown on screen, and the non-graphic cinematic hunt
def _apply_tint(C, A, x0, y0, tn):
    """Skin colour change sweeping along the body. tn = {"color": (r,g,b), "front": screen x of the wavefront, "dir": +1 or -1
    (the way the wave travels), "soft": width of the soft edge, "t": time}. Pixels behind the front already have the new colour; at the
    front a bright shimmer and a few pale spots show the change happening. Brightness (the drawn shading) is kept."""
    h, w = A.shape
    if tn.get("base") is not None:       # a body that already wears an earlier colour: start from that
        g0 = C.mean(axis=2, keepdims=True) / 255.0
        C = np.array(tn["base"], dtype=np.float32)[None, None, :] * (0.45 + 0.95 * g0) * 0.93 + C * 0.07
    xs = (np.arange(w, dtype=np.float32) + x0)[None, :]
    soft = float(tn.get("soft", 150))
    m = np.clip((float(tn["front"]) - xs) * float(tn.get("dir", 1)) / soft + 0.5, 0.0, 1.0)
    m = np.repeat(m, h, axis=0)
    grey = C.mean(axis=2, keepdims=True) / 255.0
    col = np.array(tn["color"], dtype=np.float32)
    target = col[None, None, :] * (0.45 + 0.95 * grey)
    yy = np.arange(h, dtype=np.float32)[:, None]
    spots = (np.sin((xs + x0) * 0.071 + yy * 0.043 + tn.get("t", 0.0) * 2.0) * np.sin(yy * 0.057 - xs * 0.05)) > 0.55
    band = (m * (1 - m) * 4.0)[..., None]
    out = C * (1 - m[..., None] * 0.93) + target * (m[..., None] * 0.93)
    out = out + band * 70.0 + (band[..., 0] * spots * 55.0)[..., None]
    return np.clip(out, 0, 255)


def _place_tone(name):
    st = PLACES.get(name, DEFAULT_PLACE)
    c = mix(st["far"], st["near"], 0.45)
    return tuple(int(min(255, v * 1.35 + 14)) for v in c[:3])


def _bubbles(d, x, y, t, n=9, seed=2):
    rnd = random.Random(seed)
    for i in range(n):
        ph = (t * rnd.uniform(0.25, 0.6) + rnd.random()) % 1.0
        bx = x + rnd.uniform(-120, 120) + 12 * math.sin(t * 3 + i)
        by = y - 380 * ph
        r = rnd.uniform(5, 14) * (0.6 + 0.4 * ph)
        d.ellipse((bx - r, by - r, bx + r, by + r), outline=(210, 235, 255, int(150 * (1 - ph))), width=2)


def _fit_scale(sp, span=640):
    s0 = 1.4 * sp.get("scale", 1.0) * KIND_SCALE.get("chimera", 1.0)
    bb = sp.get("bbox")
    if bb is None:        # a folklore monster has no measured outline: estimate it from where its head is
        bb = (-260.0, -800.0, HEAD_AT.get(sp.get("kind"), (200, -400))[0] + 160.0, 0.0)
    front, back_ = max(bb[2], 100.0), max(-bb[0], 100.0)
    return _clip(span / ((front + back_) * s0 + 1.0), 0.45, 1.0), front, back_, s0


def ability_scene(out, dur, world, sp, kind, place_a=None, place_b=None, cwd=None):
    """The narration names an ability, so the picture DEMONSTRATES it.
    camouflage: the skin changes colour in a wave from head to tail to match the background; the background changes and it changes again.
    swim: it glides through water with bubbles. fly: it soars high above the ground. defend: it rears, flares and holds its ground."""
    zc, front, back_, s0 = _fit_scale(sp)
    world.push, world.shake = 0.06, 0.0
    info = {"ability": kind, "steps": []}
    if kind == "camouflage":
        wa = world
        wb = World(place_b or "the Sahara desert", 5)
        wb.focus_y = getattr(world, "focus_y", 0.7)
        tone_a, tone_b = _place_tone(place_a or "a dark forest"), _place_tone(place_b or "the Sahara desert")
        L = back_ * s0 * zc

        def paint(im, d, t, u, cam):
            bgk = _smooth((u - 0.42) / 0.12)                       # the scenery changes around it
            if bgk > 0.0:
                im2 = wb.frame(cam * 0.4, t)
                im = Image.blend(im, im2, bgk) if bgk < 1 else im2
                d = ImageDraw.Draw(im, "RGBA")
            x = 330 + 150 * u
            tn = None
            a1 = _smooth((u - 0.12) / 0.30)                       # first change: toward the first background, head to tail
            b1 = _smooth((u - 0.58) / 0.30)                       # second change: toward the second background
            head_x, tail_x = x + 330 * zc, x - 330 * zc
            if b1 > 0:
                tn = {"color": tone_b, "front": head_x + 150 - b1 * (head_x - tail_x + 360), "dir": -1, "soft": 160, "t": t}
                tn["base"] = tone_a              # the second wave passes over a body that already wears the first colour
            elif a1 > 0:
                tn = {"color": tone_a, "front": head_x + 150 - a1 * (head_x - tail_x + 360), "dir": -1, "soft": 160, "t": t}
            draw_creature(d, sp, x, GROUND + 20, t * STEP * 0.18, 1, zc,
                          {"t": t, "moving": False, "tint": tn, "headup": 0.2 + 0.15 * math.sin(t * 1.3)})
            return im
        write_video(out, dur, paint, world, cwd, cam_dir=0.2)
        info["tones"] = (tone_a, tone_b)
        return info
    if kind == "swim":
        def paint(im, d, t, u, cam):
            x = 280 + 460 * _smooth(u)
            y = GROUND - 330 + 46 * math.sin(t * 1.9)
            _bubbles(d, x - 60, y - 120, t, 10, 3)
            draw_creature(d, sp, x, y, t * STEP * 0.35, 1, zc * 0.95, {"t": t, "moving": True, "noshadow": True})
            _bubbles(d, x + 140, y - 60, t + 3, 6, 8)
        write_video(out, dur, paint, world, cwd, cam_dir=0.6)
        return info
    if kind == "fly":
        def paint(im, d, t, u, cam):
            x = 200 + 640 * _smooth(u)
            y = GROUND - 560 + 70 * math.sin(t * 2.2) - 80 * u
            d.ellipse((x - 150, GROUND + 6, x + 150, GROUND + 36), fill=(6, 8, 14, 90))        # its shadow slides over the ground far below
            for k in range(6):                                                                    # wind streaks rushing past
                sx = (W + 200 - ((t * (700 + 90 * k) + k * 211) % (W + 400)))
                sy = 420 + k * 170
                d.line((sx, sy, sx + 170, sy), fill=(255, 255, 255, 60), width=3)
            draw_creature(d, sp, x, y, t * STEP * 0.5, 1, zc * 0.9, {"t": t, "moving": True, "noshadow": True})
        write_video(out, dur, paint, world, cwd, cam_dir=2.0)
        return info
    # defend: rear up, flare, hold ground against something unseen at the edge of the dark
    def paint(im, d, t, u, cam):
        k = _smooth(u / 0.35)
        px = 520
        for i in range(15):                                                      # a fan of spines / frill flaring behind it
            a = math.radians(-165 + i * 11.5)
            r0, r1 = 170 * zc, (320 + 90 * math.sin(i * 1.7 + t * 9) * 0.3) * zc * (0.4 + 0.8 * k)
            cx, cy = px, GROUND - 330 * zc
            d.polygon([(cx + math.cos(a - 0.05) * r0, cy + math.sin(a) * r0), (cx + math.cos(a) * r1, cy + math.sin(a) * r1),
                       (cx + math.cos(a + 0.05) * r0, cy + math.sin(a) * r0)], fill=sp["color"][:3] + (int(170 * k),) if len(sp["color"]) >= 3 else (200, 90, 60, int(170 * k)))
        for ex in (1010, 1050):                                                  # the threat: eyes at the edge of the frame
            d.ellipse((ex - 14 + 8 * math.sin(t * 5), 1180, ex + 14, 1210), fill=(255, 40, 30, int(220 * k)))
        draw_creature(d, sp, px - 40 + 6 * math.sin(t * 40) * (u > 0.4), GROUND + 20, t * STEP * 0.25, 1, zc * (1.0 + 0.16 * k),
                      {"t": t, "moving": False, "roar": k * (0.6 + 0.4 * math.sin(t * 7) ** 2), "crouch": 0.2 * (1 - k), "headup": 0.7 * k})
        if 0.35 < u < 0.7:
            _dust(d, px - 280 * zc, GROUND + 14, (u - 0.35) / 0.35, seed=6, size=1.1)
            _dust(d, px + 180 * zc, GROUND + 14, (u - 0.35) / 0.35, seed=7, size=1.1)
    write_video(out, dur, paint, world, cwd, cam_dir=0, cam_fn=lambda t: 0.0)
    return info


def chase_scene(out, dur, world, sp, prey_text="", cwd=None):
    """A non-graphic hunt in six shots, both animals always fully visible: 1 wide (prey grazing, hybrid stalking, slow zoom-out),
    2 close on the prey as it notices, 3 tracking shot of the chase with the prey swerving, 4 the prey turns and stands its ground while
    the hybrid skids to a stop, 5 the leap into a cloud of dust, 6 the hybrid standing in the settling dust. No blood."""
    prey, pscale = prey_spec(prey_text)
    zc, front, back_, s0 = _fit_scale(sp, 560)
    world.push, world.shake = 0.0, 0.0
    c = [dur * f for f in (0.0, 0.24, 0.36, 0.60, 0.74, 0.90, 1.0)]
    GY = GROUND + 20
    leap_t = c[4] + 0.12 * dur

    def prey_at(d, x, t, scale_mul=1.0, dip=0.0, hop=0.0, run=False, headup=0.0):
        draw_creature(d, prey, x, GY - hop, t * STEP * (1.6 if run else 0.2), 1, pscale * scale_mul,
                      {"t": t, "moving": run, "dip": dip, "headup": headup})

    def paint(im, d, t, u, cam):
        if t < c[1]:                                                         # 1 wide establishing, slow zoom-out
            k = (t - c[0]) / (c[1] - c[0])
            z = zc * (1.12 - 0.12 * k)
            prey_at(d, 760, t, 1.0 - 0.0 * k, dip=0.55 + 0.45 * math.sin(t * 2.2))
            draw_creature(d, sp, 250 + 70 * k, GY, t * STEP * 0.30, 1, z, {"t": t, "moving": True, "headup": 0.25})
        elif t < c[2]:                                                       # 2 close on the prey: it freezes, head up, ears alert
            k = (t - c[1]) / (c[2] - c[1])
            dim(im, 60)
            prey_at(d, 540 + 3 * math.sin(t * 50), t, 2.3 - 0.15 * k, headup=1.0)
            d.polygon([(540 - 11, 760), (540 + 11, 760), (540 + 6, 900), (540 - 6, 900)], fill=(255, 240, 200, int(230 * min(1, k * 5))))
            d.ellipse((540 - 9, 920, 540 + 9, 938), fill=(255, 240, 200, int(230 * min(1, k * 5))))
        elif t < c[3]:                                                       # 3 tracking shot: the chase, the prey swerves
            k = (t - c[2]) / (c[3] - c[2])
            gap = 650 - 210 * _smooth(k)
            px = 800 + 50 * math.sin(k * 9)
            prey_at(d, px, t, run=True, hop=12 * abs(math.sin(t * 9)))
            draw_creature(d, sp, px - gap, GY, t * STEP * 1.2, 1, zc, {"t": t, "moving": True, "headup": 0.0, "ground": chim_ground(sp) * 2.2})
            _dust(d, px - gap - 150 * zc, GY - 4, (t * 3) % 1.0, seed=int(t * 3) + 1, size=0.7)
        elif t < c[4]:                                                       # 4 the prey turns and holds its ground; the hybrid skids and crouches
            k = (t - c[3]) / (c[4] - c[3])
            prey_at(d, 830, t, hop=60 * abs(math.sin(k * 7)) * (1 - k * 0.3), headup=0.8)
            draw_creature(d, sp, 400 - 60 * (1 - _smooth(min(1, k * 2))), GY, t * STEP * 0.5, 1, zc,
                          {"t": t, "moving": k < 0.5, "crouch": 0.6 * _smooth(k), "anticipate": _smooth(k)})
            if k < 0.6:
                _dust(d, 330, GY - 4, k / 0.6, seed=3, size=1.3)
        elif t < c[5]:                                                       # 5 the leap: a cloud of dust swallows the meeting point
            k = (t - c[4]) / (c[5] - c[4])
            lk = _clip((t - leap_t) / max(c[5] - leap_t, 0.1), 0.0, 1.0)
            hx = 400 + (570 - 400) * _smooth(lk) if t > leap_t else 400
            lift = -190 * math.sin(math.pi * lk) if t > leap_t else 0.0
            if lk < 0.7:
                prey_at(d, 830, t, hop=20 * abs(math.sin(t * 12)), headup=0.6)
            draw_creature(d, sp, hx, GY + lift, t * STEP * 0.5, 1, zc,
                          {"t": t, "moving": t > leap_t, "lunge": math.sin(math.pi * lk), "crouch": 0.5 * (1 - lk) if t <= leap_t else 0.0,
                           "anticipate": 1.0 if t <= leap_t else 0.0})
            if lk > 0.55:
                kk = (lk - 0.55) / 0.45
                for i in range(14):
                    ang = i * 0.9
                    rr = (70 + 140 * kk) * (0.7 + 0.3 * math.sin(i * 2.1))
                    d.ellipse((830 + 120 * math.cos(ang) * kk - rr, GY - 90 - 80 * kk * abs(math.sin(ang)) - rr * 0.7,
                               830 + 120 * math.cos(ang) * kk + rr, GY - 90 - 80 * kk * abs(math.sin(ang)) + rr * 0.7),
                              fill=(156, 138, 112, int(210 * (1 - 0.4 * kk))))
        else:                                                                # 6 it stands in the settling dust
            k = (t - c[5]) / (c[6] - c[5])
            draw_creature(d, sp, 520, GY, t * STEP * 0.2, 1, zc,
                          {"t": t, "moving": False, "roar": min(1, k * 3) * (1 if k < 0.9 else 0.3), "headup": 0.6})
            for i in range(10):
                rr = 150 * (0.7 + 0.3 * math.sin(i * 2.7))
                d.ellipse((800 + 70 * math.cos(i * 1.3) - rr, GY - 60 - rr * 0.5, 800 + 70 * math.cos(i * 1.3) + rr, GY - 60 + rr * 0.6),
                          fill=(156, 138, 112, int(150 * (1 - k))))

    steps = [c[2] + 0.3 * i for i in range(int((c[3] - c[2]) / 0.3))]
    write_video(out, dur, paint, world, cwd, cam_dir=1, cam_fn=lambda t: (t - c[2]) * 520 if c[2] < t < c[3] else (0.0 if t <= c[2] else (c[3] - c[2]) * 520))
    return {"zoom_at": c[1], "lunge_at": leap_t, "land_at": c[4] + 0.8 * (c[5] - c[4]), "steps": steps, "impact_at": c[4] + 0.8 * (c[5] - c[4])}


# ================================================================ universal skeleton / anatomy view (any creature, any body plan)
ANATOMY_RX = r"skeleton|\bbones?\b|anatom|rib ?cage|\bskull|\bspine\b|\bvertebra|inside (its|the) body|x-ray|beneath (its|the) (skin|scales|fur)"


# ================================================================ universal skeleton / anatomy view (works on ANY creature: hybrids, monsters, prey, people-sized figures)


def _smooth1d(a, n=9):
    k = np.ones(n) / n
    pad = np.pad(a, (n // 2, n // 2), mode="edge")
    return np.convolve(pad, k, mode="valid")


def anatomy_bones(solid, x0, y0, sp, cx, gy, s, facing):
    """Works out the skeleton from the creature's drawn outline (so it always lines up with the animated body) and from its genes
    (leg count, wings, shell, tail, skull size). Returns a dict of polylines in screen coordinates."""
    g = sp.get("g") if isinstance(sp.get("g"), dict) else {}
    h, w = solid.shape
    cols = np.where(solid.max(axis=0) > 0.5)[0]
    if len(cols) < 20:
        return None
    c0, c1 = int(cols[0]), int(cols[-1])
    top = np.full(w, np.nan)
    bot = np.full(w, np.nan)
    for x in range(c0, c1 + 1):
        ys = np.where(solid[:, x] > 0.5)[0]
        if len(ys):
            top[x], bot[x] = ys[0], ys[-1]
    xs = np.arange(c0, c1 + 1)
    tp, bt = top[c0:c1 + 1], bot[c0:c1 + 1]
    ok = ~np.isnan(tp)
    tp = np.interp(xs, xs[ok], tp[ok])
    bt = np.interp(xs, xs[ok], bt[ok])
    torso = g.get("torso")
    Ht = (float(torso[2]) * s) if torso else 0.30 * float(np.nanmax(bt) - np.nanmin(tp))
    thick = bt - tp
    spine = np.where(thick > 1.25 * Ht, tp + 0.45 * Ht, tp + 0.5 * thick)        # legs and wings do not drag the spine around
    spine = _smooth1d(spine, 15)
    head_dir = 1 if (sp.get("head0", (1, 0))[0] * facing) > 0 else -1
    hx = cx + facing * sp.get("head0", (150, -150))[0] * s - x0
    hy = gy + sp.get("head0", (150, -150))[1] * s - y0
    ratio = lambda x: (x - c0) / max(c1 - c0, 1) if head_dir > 0 else (c1 - x) / max(c1 - c0, 1)     # 0 at the tail tip, 1 at the nose
    bones = {"spine": [], "ribs": [], "legs": [], "wings": [], "tail": [], "skull": None, "neck": [], "dorsal": [], "shell": []}
    # the backbone from the tail tip to the base of the skull; the neck joins it to the head
    for i, x in enumerate(xs):
        u = ratio(x)
        if u <= 0.97 and abs(x - hx) > 12:
            bones["spine"].append((x0 + x, y0 + float(spine[i])))
    if head_dir > 0:
        bones["spine"].sort()
    else:
        bones["spine"].sort(reverse=True)
    sp_pts = bones["spine"]
    # vertebrae are small dots along it, ribs hang from the middle of the trunk
    thick_cols = [i for i, x in enumerate(xs) if thick[i] > 0.7 * Ht and 0.2 < ratio(x) < 0.75]
    if thick_cols:
        a, b = xs[thick_cols[0]], xs[thick_cols[-1]]
        n = int(np.clip((b - a) / (0.11 * Ht + 6), 5, 11))
        for r_ in range(n):
            x = a + (b - a) * (r_ + 0.5) / n
            i = int(np.clip(x - c0, 0, len(xs) - 1))
            ys0 = float(spine[i])
            depth = min(0.62 * Ht, max(float(bt[i] - ys0) - 8, 10)) * (1 - 0.35 * abs((r_ / max(n - 1, 1)) - 0.35))
            sway = head_dir * 0.10 * Ht
            bones["ribs"].append([(x0 + x, y0 + ys0 + 2), (x0 + x + sway, y0 + ys0 + depth * 0.55), (x0 + x - sway * 0.3, y0 + ys0 + depth)])
    # limbs: every place where the body reaches down to the ground is a leg
    reach = bt > (np.nanmax(bt) - 0.18 * (np.nanmax(bt) - np.nanmin(tp)))
    low_cols = [i for i in range(len(xs)) if reach[i] and bt[i] > spine[i] + 0.55 * Ht]
    groups, cur = [], []
    for i in low_cols:
        if cur and i - cur[-1] > 6:
            groups.append(cur)
            cur = []
        cur.append(i)
    if cur:
        groups.append(cur)
    nl = int(g.get("legs", (None, 4))[1]) if g.get("legs") else len(groups)
    for gi, grp in enumerate(groups[:max(nl, 2)]):
        i = grp[len(grp) // 2]
        foot = (x0 + xs[i], y0 + float(bt[i]))
        hip = (x0 + xs[i], y0 + float(spine[i]) + 0.35 * Ht)
        L = foot[1] - hip[1]
        bend = (-1 if gi % 2 else 1) * head_dir * 0.18 * L
        knee = (hip[0] + bend, hip[1] + 0.52 * L)
        toe = (foot[0] + head_dir * 0.22 * Ht * 0.6, foot[1])
        bones["legs"].append([hip, knee, foot, toe])
    # wings: the topmost part of the outline above the body is the wing tip
    if g.get("wings"):
        j = int(np.argmin(tp))
        if tp[j] < spine[j] - 0.6 * Ht:
            tip = (x0 + xs[j], y0 + float(tp[j]))
            sh = (x0 + xs[int(np.clip((c0 + (c1 - c0) * 0.55) - c0, 0, len(xs) - 1))], y0 + float(spine[int(np.clip((c1 - c0) * 0.55, 0, len(xs) - 1))]))
            elbow = (sh[0] + (tip[0] - sh[0]) * 0.45 - head_dir * 0.12 * Ht, sh[1] + (tip[1] - sh[1]) * 0.55)
            bones["wings"].append([sh, elbow, tip])
            for f in range(4):
                a = (f - 1.5) * 0.22
                end = (tip[0] - head_dir * (0.2 + 0.12 * f) * Ht * 1.6, tip[1] + (0.7 + 0.35 * f) * Ht * 0.9)
                bones["wings"].append([elbow if f < 2 else (elbow[0] + (tip[0] - elbow[0]) * 0.5, elbow[1] + (tip[1] - elbow[1]) * 0.5), end])
    if g.get("shell"):
        i0, i1 = int(len(xs) * 0.25), int(len(xs) * 0.7)
        bones["shell"] = [(x0 + xs[i], y0 + float(tp[i]) + 4) for i in range(i0, i1, 4)]
    if g.get("dorsal"):
        for i in range(int(len(xs) * 0.2), int(len(xs) * 0.85), max(len(xs) // 14, 6)):
            bones["dorsal"].append([(x0 + xs[i], y0 + float(spine[i]) - 4), (x0 + xs[i] - head_dir * 6, y0 + float(tp[i]) + 2)])
    # skull: sized from the creature's own head genes
    hd = g.get("head") or {}
    sw, sh_ = (hd.get("skull") or (46, 40))
    snout = hd.get("snout")
    sl = float(snout[1]) if snout else 50.0
    bones["skull"] = {"c": (x0 + hx, y0 + hy), "w": float(sw) * s * 1.1, "h": float(sh_) * s * 1.1, "snout": sl * s, "dir": head_dir}
    # the neck: dots from the end of the trunk to the skull
    if sp_pts:
        endp = sp_pts[-1]
        for q in range(1, 6):
            bones["neck"].append((endp[0] + (x0 + hx - endp[0]) * q / 6, endp[1] + (y0 + hy - endp[1]) * q / 6))
    return bones


def _xray_overlay(base, solid, x0, y0, sp, cx, gy, s, facing, xr):
    """xr = {"k": 0..1 how strongly the x-ray shows, "front": screen x the scan line has reached, "dir": scan direction, "labels": bool}"""
    k = float(xr.get("k", 1.0))
    if k <= 0.01:
        return
    bones = anatomy_bones(solid, x0, y0, sp, cx, gy, s, facing)
    if not bones:
        return
    # the body dims and cools toward a scan look
    h, w = solid.shape
    reg = base.crop((x0, y0, x0 + w, y0 + h))
    xs = (np.arange(w, dtype=np.float32) + x0)[None, :]
    front = xr.get("front")
    scan = np.ones((h, w), dtype=np.float32) if front is None else np.clip((float(front) - xs) * float(xr.get("dir", 1)) / 40.0 + 0.5, 0, 1).repeat(h, axis=0)
    a = (solid * scan * 0.62 * k)[..., None]
    arr = np.asarray(reg, dtype=np.float32)
    cool = np.array((14, 34, 52), dtype=np.float32)
    arr = arr * (1 - a) + cool * a
    reg = Image.fromarray(arr.astype(np.uint8))
    base.paste(reg, (x0, y0))
    # bones on a transparent layer, then a soft glow
    L = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(L, "RGBA")
    bw = max(3, int(6 * s))
    col = (238, 242, 232, 255)
    sp_ = bones["spine"]
    if len(sp_) > 2:
        d.line(sp_, fill=col, width=bw + 2, joint="curve")
        for i in range(0, len(sp_), max(len(sp_) // 26, 3)):
            q = sp_[i]
            d.ellipse((q[0] - bw, q[1] - bw, q[0] + bw, q[1] + bw), fill=col)
    for rb in bones["ribs"]:
        d.line(rb, fill=col, width=bw, joint="curve")
    for lg in bones["legs"]:
        d.line(lg, fill=col, width=bw + 1, joint="curve")
        for p_ in (lg[0], lg[1]):
            d.ellipse((p_[0] - bw * 1.4, p_[1] - bw * 1.4, p_[0] + bw * 1.4, p_[1] + bw * 1.4), fill=col)
    for wn in bones["wings"]:
        d.line(wn, fill=col, width=bw, joint="curve")
    if len(bones["shell"]) > 2:
        d.line(bones["shell"], fill=col, width=bw, joint="curve")
    for ds in bones["dorsal"]:
        d.line(ds, fill=col, width=max(2, bw - 1))
    for q in bones["neck"]:
        d.ellipse((q[0] - bw, q[1] - bw, q[0] + bw, q[1] + bw), fill=col)
    sk = bones["skull"]
    if sk:
        (kx, ky), sw, shh, sn, dr = sk["c"], sk["w"], sk["h"], sk["snout"], sk["dir"]
        d.ellipse((kx - sw * 0.6, ky - shh * 0.6, kx + sw * 0.6, ky + shh * 0.6), outline=col, width=bw)           # braincase
        d.polygon([(kx + dr * sw * 0.4, ky - shh * 0.25), (kx + dr * (sw * 0.4 + sn), ky - shh * 0.05), (kx + dr * (sw * 0.4 + sn * 0.9), ky + shh * 0.3),
                   (kx + dr * sw * 0.3, ky + shh * 0.45)], outline=col, width=bw)                                     # snout / jaw
        d.ellipse((kx - sw * 0.1, ky - shh * 0.28, kx + sw * 0.2, ky + shh * 0.04), fill=(8, 18, 28, 255), outline=col, width=max(2, bw - 2))  # eye socket
    alpha = np.asarray(L.getchannel("A"), dtype=np.float32) / 255.0
    if front is not None:
        full = np.arange(base.width, dtype=np.float32)[None, :]
        keep = np.clip((float(front) - full) * float(xr.get("dir", 1)) / 40.0 + 0.5, 0, 1)
        alpha = alpha * keep
    L.putalpha(Image.fromarray((alpha * 255 * k).astype(np.uint8), "L"))
    glow = L.filter(ImageFilter.GaussianBlur(9 * max(s, 0.6)))
    base.paste(glow.convert("RGB"), (0, 0), Image.fromarray((np.asarray(glow.getchannel("A")) * 0.8).astype(np.uint8), "L"))
    base.paste(L.convert("RGB"), (0, 0), L.getchannel("A"))
    if front is not None and 0 < k:                                   # the bright scan line itself
        sd = ImageDraw.Draw(base, "RGBA")
        fx = float(front)
        sd.rectangle((fx - 3, y0, fx + 3, y0 + h), fill=(150, 235, 255, int(150 * k)))
    if xr.get("labels") and k > 0.6:
        names = [("SKULL", sk["c"] if sk else None), ("SPINE", sp_[len(sp_) // 2] if sp_ else None)]
        if bones["ribs"]:
            names.append(("RIBS", bones["ribs"][len(bones["ribs"]) // 2][1]))
        if bones["wings"]:
            names.append(("WING BONES", bones["wings"][0][1]))
        elif bones["legs"]:
            names.append(("LIMBS", bones["legs"][0][1]))
        elif bones["shell"]:
            names.append(("SHELL", bones["shell"][len(bones["shell"]) // 2]))
        ld = ImageDraw.Draw(base, "RGBA")
        f = _font(34)
        ytop = y0 - 20
        for i, (nm, pt) in enumerate(names):
            if pt is None:
                continue
            tx = 80 + i * 245 if i < 4 else 80
            ty = max(300, int(ytop) - 70 - (i % 2) * 54)
            ld.line((tx + 60, ty + 40, pt[0], pt[1]), fill=(160, 220, 240, int(190 * k)), width=2)
            ld.ellipse((pt[0] - 6, pt[1] - 6, pt[0] + 6, pt[1] + 6), outline=(200, 245, 255, int(230 * k)), width=2)
            ld.text((tx, ty), nm, font=f, fill=(210, 240, 250, int(240 * k)))
            _BOXES.append((tx, ty, tx + 28 * len(nm), ty + 44))


def anatomy_scene(out, dur, world, sp, cwd=None, standing=True):
    """The x-ray view: the creature stands, a scan line sweeps nose to tail, the skeleton appears inside the body with a few clean labels, holds,
    then dissolves back to the normal picture. Clean and educational, no injuries. Works for any creature spec (hybrid, monster, prey...)."""
    zc, front, back_, s0 = _fit_scale(sp, 620)
    world.push, world.shake = 0.05, 0.0

    def paint(im, d, t, u, cam):
        k_in = _smooth(u / 0.16)
        k_out = 1 - _smooth((u - 0.86) / 0.14)
        k = min(k_in, k_out)
        x = 540
        head_dir = 1
        # the scan line travels from the head end to the tail end between 12% and 40% of the scene
        span = 460
        sweep = _smooth((u - 0.10) / 0.30)
        fr = (x + span) - sweep * 2 * span
        draw_creature(d, sp, x, GROUND + 20, t * STEP * 0.12, 1, zc,
                      {"t": t, "moving": False, "headup": 0.15 + 0.1 * math.sin(t), "xray": {"k": k, "front": fr if u < 0.45 else None, "dir": -1, "labels": True}})
    write_video(out, dur, paint, world, cwd, cam_dir=0.0)
    return {"ability": "anatomy"}


# ================================================================ depth objects, reactions and wrecks
# 1) Independent objects (trees, rocks, grass, stones) scattered at different depths. Each one has its own size, haze and scroll speed
#    (mid-ground ones sit behind the creature and scroll a little slower than the ground; a few big dark ones sit in front and scroll faster).
# 2) The scenery answers the action: a footfall, a roar or a crash starts a shock; trees bend and spring back, grass whips, rocks jump
#    and shake loose dust, small stones hop.
# 3) smash_scene: a creature wrecks a vehicle: contact, then the metal crumples, glass cracks and flies, sparks, the ground cracks,
#    a wheel rolls off, smoke rises and the wreck stays there.
import zlib

REACT = {"t": -99.0, "power": 0.0, "x": 540.0}
SMASH_VERB_RX = r"\b(crush|crushed|crushes|smash|smashed|smashes|flip|flipped|flips|tear|tore|torn|rip|ripped|slam|slammed|wreck|wrecked|flatten|flattened|ram|rammed|hurl|hurled|throw|threw|crumple|crumpled|destroy|destroyed|stomp|stomped|overturn|overturned|mangled|bash|bashed|batter|battered)\b"
VEHICLE_RX = r"\b(cars?|trucks?|buses|bus|vans?|jeeps?|taxis?|vehicles?|lorry|lorries|tractors?|wagons?|carriages?|pickup)\b"


def react(power=1.0, x=540.0):
    """Something heavy happened at screen position x: nearby scenery will answer it."""
    t = EMERGE.get("t", 0.0)
    if t - REACT["t"] > 0.3 or power > REACT["power"] * 0.6:
        REACT.update(t=t, power=min(1.8, power), x=x)


def _shock(t, x):
    """(sway, hop) at screen position x: a decaying wobble and a decaying jump."""
    dt = t - REACT["t"]
    if dt < 0 or dt > 2.4:
        return 0.0, 0.0
    fall = 1.0 / (1.0 + abs(x - REACT["x"]) / 420.0)
    p = REACT["power"] * fall
    return p * math.exp(-dt * 1.9) * math.sin(dt * 12.0 + 0.5), p * math.exp(-dt * 3.2) * abs(math.sin(dt * 10.5))


def _scatter_kind(st):
    s = st.get("sil")
    if s in ("trees", "pines", "dead"):
        return "tree"
    if s in ("reeds", "wheat"):
        return "grass"
    if s == "graves":
        return "grave"
    if s == "clouds":
        return None
    return "rock"


def _scatter_objs(world):
    if hasattr(world, "_scat"):
        return world._scat
    st = world.st
    rnd = random.Random(zlib.crc32(repr((st.get("sky"), st.get("ground"), st.get("sil"))).encode()))
    prim = _scatter_kind(st)
    objs = []
    if prim is None:
        world._scat = objs
        return objs
    g = rgb(st["ground"]) if isinstance(st.get("ground"), str) else (60, 50, 45)
    hz = rgb(st["sky"][1])
    for i in range(9):                                  # mid-ground, behind the creature: hazy and small
        kind = prim if (i % 3 != 2 or prim == "rock") else "rock"
        k = rnd.uniform(0.78, 0.92)
        col = mix(tuple(int(c * 0.55) for c in g), hz, 0.30)
        objs.append({"kind": kind, "ox": i * 160 + rnd.uniform(-50, 50), "k": k, "sc": rnd.uniform(0.5, 0.85), "y": GROUND - rnd.uniform(36, 4),
                     "col": col, "seed": rnd.randint(1, 9999), "front": False})
    for i in range(3):                                  # in front of the creature: big, dark, low, they scroll faster
        kind = "grass" if prim in ("tree", "grass") else "rock"
        col = tuple(int(c * 0.30) for c in g)
        objs.append({"kind": kind, "ox": i * 520 + rnd.uniform(0, 160), "k": rnd.uniform(1.18, 1.34), "sc": rnd.uniform(1.4, 1.9), "y": GROUND + rnd.uniform(400, 470),
                     "col": col, "seed": rnd.randint(1, 9999), "front": True})
    world._scat = objs
    return objs


def _draw_scatter_obj(d, o, x, t):
    y, sc, col, kind = o["y"], o["sc"], tuple(int(c) for c in o["col"]), o["kind"]
    rnd = random.Random(o["seed"])
    sway, hop = _shock(t, x)
    wind = 1.2 * math.sin(t * 1.3 + o["ox"] * 0.01)
    if kind == "tree":
        H = (230 + rnd.uniform(-30, 40)) * sc
        bend = (sway * 46 + wind * 6) * sc
        pts = [(x + bend * (i / 8) ** 2, y - H * i / 8) for i in range(9)]
        w0, w1 = 11 * sc, 4 * sc
        left = [(px - (w0 + (w1 - w0) * i / 8), py) for i, (px, py) in enumerate(pts)]
        right = [(px + (w0 + (w1 - w0) * i / 8), py) for i, (px, py) in enumerate(pts)]
        d.polygon(left + right[::-1], fill=col + (255,))
        tx, ty = pts[-1]
        if rnd.random() < 0.5:                                           # leafy crown
            for dx, dy, r in ((-34, 8, 46), (30, 4, 50), (0, -34, 54), (-6, 26, 42)):
                bx, by = tx + (dx + sway * 14) * sc * 0.9, ty + dy * sc
                d.ellipse((bx - r * sc, by - r * sc * 0.8, bx + r * sc, by + r * sc * 0.8), fill=col + (255,))
        else:                                                            # bare branches that whip with the shock
            for s_ in (-1, 1):
                for j in range(3):
                    by = y - H * (0.55 + 0.14 * j)
                    bx0 = x + bend * (0.55 + 0.14 * j) ** 2
                    ex = bx0 + s_ * (70 - 14 * j) * sc + sway * 26 * sc * s_
                    d.line((bx0, by, ex, by - 55 * sc), fill=col + (255,), width=max(2, int(5 * sc - j)))
    elif kind == "grass":
        for b in range(11):
            bx = x + (b - 5) * 9 * sc
            hh = rnd.uniform(60, 130) * sc
            tipx = bx + rnd.uniform(-10, 10) * sc + (sway * 34 + wind * 7) * sc * (hh / (130 * sc))
            d.polygon([(bx - 3 * sc, y), (tipx, y - hh), (bx + 3 * sc, y)], fill=col + (255,))
    elif kind == "grave":
        gx = x + sway * 3
        hh = rnd.uniform(70, 110) * sc
        d.rounded_rectangle((gx - 24 * sc, y - hh, gx + 24 * sc, y + 4), radius=int(22 * sc), fill=col + (255,))
    else:                                                                # rock with a lit facet and a few stones around it
        shift = sway * 5 * sc
        w, h = 64 * sc, 46 * sc
        rr = [rnd.uniform(0.75, 1.1) for _ in range(6)]
        pts = [(x + shift - w * rr[0], y + 4), (x + shift - w * 0.62 * rr[1], y - h * rr[2]), (x + shift + w * 0.1, y - h * 1.12 * rr[3]),
               (x + shift + w * 0.7 * rr[4], y - h * 0.7 * rr[5]), (x + shift + w * rr[0], y + 4)]
        d.polygon(pts, fill=col + (255,))
        lt = tuple(min(255, int(c * 1.35 + 10)) for c in col)
        d.polygon([pts[1], pts[2], (pts[2][0] - 8 * sc, pts[2][1] + 24 * sc), (pts[1][0] + 10 * sc, pts[1][1] + 22 * sc)], fill=lt + (255,))
        for j in range(3):
            px = x + (j - 1) * 46 * sc + rnd.uniform(-8, 8)
            ph = hop * 30 * (0.6 + 0.4 * j) * sc
            d.ellipse((px - 6 * sc, y - 6 * sc - ph, px + 6 * sc, y - ph), fill=col + (255,))
        if hop > 0.05:                                                   # dust shaken off the rock
            d.ellipse((x - w * 0.8, y - h * 0.6 - hop * 14, x + w * 0.8, y + 6), fill=(150, 135, 115, int(70 * min(1, hop))))


def _scatter_draw(world, im, cam, t, front):
    if getattr(world, "no_scatter", False):
        return
    objs = [o for o in _scatter_objs(world) if o["front"] == front]
    if not objs:
        return
    d = ImageDraw.Draw(im, "RGBA")
    P = 1500.0
    for o in sorted(objs, key=lambda o: o["y"]):
        x = (o["ox"] - cam * o["k"]) % P - 220
        if -200 < x < W + 200:
            _draw_scatter_obj(d, o, x, t)


_wf_depth0 = World.frame


def _wf_depth(self, cam, t):
    im = _wf_depth0(self, cam, t)
    _scatter_draw(self, im, cam, t, False)
    return im


World.frame = _wf_depth
_cf_depth0 = World.cinema_front


def _cf_depth(self, im, cam, t):
    im = _cf_depth0(self, im, cam, t)
    _scatter_draw(self, im, cam, t, True)
    return im


World.cinema_front = _cf_depth

_dc_prev = draw_creature


def draw_creature(img_draw, sp, cx, gy, phase, facing=1, scale=None, st=None, focus=None):
    if st and st.get("roar", 0) > 0.5:
        react(0.8, cx)                                  # a roar shakes the scenery
    return _dc_prev(img_draw, sp, cx, gy, phase, facing, scale, st, focus)


_step_fx_prev = _step_fx


def _step_fx(d, world, x, k, seed=1, size=1.0):
    if 0 < k < 0.08:
        react(0.55 * size, x)                           # a footfall makes the ground answer
    return _step_fx_prev(d, world, x, k, seed, size)


# ---- the wreck
_CAR_COLS = ((165, 45, 45), (48, 92, 150), (200, 200, 206), (186, 148, 40))


def _draw_car(d, x, gy, sq, col, shake=0.0, glass=1.0, lights=1.0, cracks=0.0, wheel=None, seed=3):
    """A side-on car facing right. sq = 0 whole .. 1 flattened. wheel = (x, y) of the front wheel once it has come off."""
    rnd = random.Random(seed)
    x += shake
    wdt = 340 * (1 + 0.16 * sq)
    R = 36
    bb = gy - 40 + 34 * sq                                  # body bottom: it sinks to the ground as it is crushed
    bt = bb - 78 * (1 - 0.5 * sq)                           # body top
    ct = bt - 86 * (1 - 0.86 * sq)                          # roof
    dark = tuple(int(c * 0.55) for c in col)
    lite = tuple(min(255, int(c * 1.25 + 25)) for c in col)
    spread = 0.31 + 0.10 * sq
    wl = (x - wdt * spread, gy - R)
    wr = (x + wdt * spread, gy - R) if wheel is None else wheel
    for wx, wy in (wl, wr):                                 # wheels (the near ones, under the body)
        d.ellipse((wx - R, wy - R, wx + R, wy + R), fill=(18, 18, 20, 255))
        d.ellipse((wx - R * 0.45, wy - R * 0.45, wx + R * 0.45, wy + R * 0.45), fill=(120, 124, 130, 255))
    sag = 22 * sq
    body = [(x - wdt / 2, bb), (x - wdt / 2, bt + 14), (x - wdt / 2 + 18, bt), (x + wdt * 0.12, bt),
            (x + wdt * 0.3, bt + sag * 0.6), (x + wdt / 2 - 24, bt + 4 + sag * 0.5), (x + wdt / 2, bt + 20), (x + wdt / 2, bb)]
    d.polygon(body, fill=col + (255,))
    d.rectangle((x - wdt / 2, bb - 14, x + wdt / 2, bb), fill=dark + (255,))
    d.line((x - wdt / 2 + 20, bt + 3, x + wdt * 0.1, bt + 3), fill=lite + (255,), width=3)
    cab = [(x - wdt / 2 + 78, bt), (x - wdt / 2 + 128, ct + sag), (x + 12, ct + sag * 1.4), (x + wdt * 0.2 + 20 * sq, bt)]
    d.polygon(cab, fill=col + (255,))
    win = [(x - wdt / 2 + 92, bt - 4), (x - wdt / 2 + 132, ct + 10 + sag), (x + 6, ct + 10 + sag * 1.4), (x + wdt * 0.2 + 6, bt - 4)]
    if glass > 0.02:
        d.polygon(win, fill=(150, 190, 210, int(215 * glass)))
        d.line((x - 30, ct + 12 + sag, x - 36, bt - 4), fill=col + (255,), width=6)
    else:
        d.polygon(win, fill=(14, 18, 22, 255))
    if cracks > 0.02:                                       # the glass cracks outwards from the point of impact
        cx0, cy0 = x - 20, ct + 14 + sag
        for _ in range(int(3 + 9 * cracks)):
            a = rnd.uniform(0, 2 * math.pi)
            L = rnd.uniform(26, 82) * cracks
            d.line((cx0, cy0, cx0 + math.cos(a) * L, cy0 + math.sin(a) * L * 0.7), fill=(238, 246, 250, 235), width=2)
    for _ in range(int(7 * sq)):                            # crumple lines across the body
        px = rnd.uniform(x - wdt * 0.4, x + wdt * 0.4)
        d.line((px, bt + 6, px + rnd.uniform(-26, 26), bb - 12), fill=dark + (230,), width=3)
    d.ellipse((x + wdt / 2 - 22, bt + 22, x + wdt / 2 - 4, bt + 40), fill=(255, 235, 150, int(255 * lights)))
    d.ellipse((x - wdt / 2 + 2, bt + 22, x - wdt / 2 + 16, bt + 38), fill=(225, 40, 40, 255))
    d.rectangle((x - wdt / 2 - 4, bb - 22, x - wdt / 2 + 4, bb - 4), fill=(40, 40, 44, 255))
    d.rectangle((x + wdt / 2 - 4, bb - 22, x + wdt / 2 + 8, bb - 4), fill=(40, 40, 44, 255))
    return {"box": (x - wdt / 2 - 10, ct - 6, x + wdt / 2 + 12, gy + 4), "roof": (x - 20, ct), "engine": (x + wdt * 0.25, bt)}


def smash_scene(out, dur, world, sp, obj="car", cwd=None):
    """The creature walks up to a car and wrecks it. Contact, deformation, aftermath: the roof folds, the glass cracks and showers out,
    sparks, the ground cracks, dust and shaken scenery, a wheel rolls away, smoke rises and the crushed car stays."""
    zc, front, back_, s0 = _fit_scale(sp, 520)
    F, B = front * s0 * zc, back_ * s0 * zc
    GY = GROUND + 20
    cx1 = 60 + B + 40
    cx0 = cx1 - 150
    car_x = cx1 + F + 75
    t_hit = 0.44
    th = dur * t_hit
    col = _CAR_COLS[zlib.crc32(str(sp.get("name", "")).encode()) % len(_CAR_COLS)]
    rnd = random.Random(11)
    shards = [(rnd.uniform(-330, 330), rnd.uniform(-640, -200), rnd.uniform(5, 12), rnd.uniform(0, 6)) for _ in range(26)]
    sparks = [(rnd.uniform(-260, 260), rnd.uniform(-420, -120)) for _ in range(16)]
    cracks_ = [[(rnd.uniform(-1, 1), rnd.uniform(0.25, 0.9)) for _ in range(4)] for _ in range(7)]
    rest = [rnd.uniform(-4, 22) for _ in shards]
    world.push, world.shake = 0.04, 0.0
    done = {"react": False}

    def paint(im, d, t, u, cam):
        age = t - th
        sq = 0.0 if age < 0 else _smooth(age / (0.10 * dur)) * 1.0
        if age > 0:
            sq = min(1.0, sq) - 0.035 * math.exp(-max(0.0, age - 0.3) * 4) * math.sin(max(0.0, age - 0.3) * 18) * (1 if age > 0.3 else 0)
        pre = _smooth(u / 0.34)
        cx = cx0 + (cx1 - cx0) * pre + (50 * math.sin(math.pi * min(1, max(0, (age + 0.1) / 0.45))) if age > -0.1 else 0)
        # tremor in the car as the creature closes in (the headlights flicker too)
        trem = (1.5 * math.sin(t * 60) * pre) if age < 0 else 0.0
        fl = 0.6 + 0.4 * math.sin(t * 25) if age < 0 and u > 0.2 else (1.0 if age < 0 else max(0.0, 1.0 - age * 3.0) * (0.5 + 0.5 * math.sin(t * 40)))
        glass = 1.0 if age < 0 else max(0.0, 1.0 - age / 0.18)
        crk = 0.0 if age < 0 else min(1.0, age / 0.12)
        wheel = None
        if age > 0.04:
            a_ = age - 0.04
            wx = car_x + 118 + 200 * (1 - math.exp(-a_ * 1.4)) / 1.4
            wy = GY - 36 - abs(math.sin(a_ * 7)) * 42 * math.exp(-a_ * 1.5)
            wheel = (wx, wy)
        if age > 0:
            world.shake = 8.0 * math.exp(-age * 5.0)
            if not done["react"]:
                done["react"] = True
            react(1.6, car_x)
        # cracks in the ground, growing out from the car and staying
        if age > 0:
            kk = min(1.0, age / 0.5)
            for ci, pts in enumerate(cracks_):
                px, py = car_x + (ci - 3) * 18, GY + 10
                line = [(px, py)]
                for (dx, dy) in pts:
                    px += dx * 70 * kk
                    py += dy * 22 * kk
                    line.append((px, py))
                d.line(line, fill=(28, 22, 18, 215), width=3)
        info = _draw_car(d, car_x, GY - 24, sq, col, trem, glass, fl, crk, wheel, seed=5)
        _BOXES.append(tuple(int(v) for v in info["box"]))
        strike = math.sin(math.pi * min(1, max(0, (age + 0.18) / 0.5))) if age > -0.18 else 0.0
        crouch = 0.6 * _smooth((u - 0.26) / 0.12) * (1 - _smooth((age + 0.05) / 0.1)) if age < 0.05 else 0.0
        roar = 0.7 * _smooth((age - 0.5) / 0.2) * (1 - _smooth((age - 1.6) / 0.4)) if age > 0.5 else 0.0
        draw_creature(d, sp, cx, GY, t * STEP * 0.5, 1, zc,
                      {"t": t, "moving": age < 0.0, "lunge": strike * 0.9, "crouch": crouch, "roar": roar, "headup": 0.0 if age < 0.5 else 0.2})
        if age > 0:                                                  # what the impact throws out
            rx, ry = info["roof"]
            for i, (vx, vy, sz, ph) in enumerate(shards):
                a_ = age - 0.01 * (i % 5)
                if a_ < 0:
                    continue
                sx, sy = rx + vx * a_, ry + vy * a_ + 900 * a_ * a_ / 2
                if sy > GY + rest[i]:
                    sy = GY + rest[i]
                    sx = rx + vx * math.sqrt(2 * (GY + rest[i] - ry + 600) / 900) * 0.6 + (vx * a_ * 0.1)
                al = 235 if sy >= GY - 1 else 255
                d.polygon([(sx, sy), (sx + sz, sy + sz * 0.4), (sx + sz * 0.3, sy + sz)], fill=(190, 225, 240, al))
            for (vx, vy) in sparks:
                if age < 0.5:
                    sx, sy = rx + vx * age * 0.9, ry + 40 + vy * age + 700 * age * age / 2
                    d.line((sx, sy, sx - vx * 0.03, sy - vy * 0.03 * 0.6), fill=(255, 205, 90, int(255 * (1 - age / 0.5))), width=3)
            for k_, (off, sz, sd) in enumerate(((0.0, 1.9, 1), (0.22, 1.3, 2))):
                kk = (age - off) / 1.3
                if 0 < kk < 1:
                    _dust(d, car_x + (k_ * 2 - 1) * 70, GY + 10, kk, seed=sd, size=sz, up=1.4)
            ex, ey = info["engine"]
            for i in range(8):                                        # smoke from the engine, drifting up and thickening
                a_ = age - 0.3 - i * 0.16
                if 0 < a_ < 2.6:
                    r = 16 + a_ * 28
                    d.ellipse((ex + 14 * math.sin(a_ * 2 + i) - r, ey - a_ * 78 - r, ex + 14 * math.sin(a_ * 2 + i) + r, ey - a_ * 78 + r),
                              fill=(70, 70, 74, int(125 * (1 - a_ / 2.6))))
    write_video(out, dur, paint, world, cwd, cam_dir=0.0)
    return {"hits": [dur * t_hit, dur * t_hit + 0.22], "impact_at": dur * t_hit}


# ---- weight and posture (applies to every creature that is drawn, so all series get it)
# The whole body leans and squats around the point where its feet touch the ground, driven by a spring so that it has momentum:
# it leans into a walk, rocks back as it roars, throws its weight forward in a lunge, sinks into a crouch and settles (with a small
# overshoot) when it stops. Heavy creatures move slowly, light ones quickly. The feet never leave the ground.
BODY = {}
BODY_LOG = []          # (lean degrees, squash) of every drawn frame, so a test can check the posture really changes


def _body_targets(sp, st, phase):
    moving = bool(st.get("moving", True))
    roar, lunge, crouch = st.get("roar", 0.0) or 0.0, st.get("lunge", 0.0) or 0.0, st.get("crouch", 0.0) or 0.0
    ant, dip, up = st.get("anticipate", 0.0) or 0.0, st.get("dip", 0.0) or 0.0, st.get("headup", 0.0) or 0.0
    lean = (2.4 if moving else 0.0) - 5.0 * roar + 10.0 * lunge + 4.0 * crouch - 3.0 * ant + 3.0 * dip - 2.5 * up
    sq = 0.10 * crouch + 0.05 * ant + 0.035 * lunge - 0.03 * roar + 0.03 * dip
    return lean, sq


def _body_mechanics(layer, sp, cx, gy, facing, st, phase):
    kind = sp.get("kind")
    if st.get("nobody") or kind in ("swim", "float", "wraith") or (kind == "bird" and "flying" in sp.get("traits", ())):
        return layer
    t = st.get("t")
    if t is None:
        return layer
    mass = _clip(sp.get("scale", 1.0) * KIND_SCALE.get(kind, 1.0) * 1.6, 0.7, 1.6)
    lean_t, sq_t = _body_targets(sp, st, phase)
    key = (id(sp), facing)
    S = BODY.get(key)
    if S is None or not (0.0 <= t - S["t"] < 0.2):          # a new scene (or a jump in time): start in the target posture
        S = BODY[key] = {"t": t, "a": lean_t, "va": 0.0, "q": sq_t, "vq": 0.0}
    dt = t - S["t"]
    if dt > 0:
        w, z = 11.0 / math.sqrt(mass), 0.5
        for _ in range(3):                                   # three small steps per frame keeps the spring stable
            h = dt / 3
            S["va"] += (w * w * (lean_t - S["a"]) - 2 * z * w * S["va"]) * h
            S["a"] += S["va"] * h
            S["vq"] += (w * w * (sq_t - S["q"]) - 2 * z * w * S["vq"]) * h
            S["q"] += S["vq"] * h
        S["t"] = t
    lean, sq = S["a"], S["q"]
    if kind != "chimera" and st.get("moving", True):         # footfall: the body sinks as each foot takes the weight (chimeras have this in their gait)
        sq += 0.012 * mass * (0.5 + 0.5 * math.cos(2 * (phase - math.pi / 2)))
    sq = _clip(sq, -0.06, 0.16)
    lean = _clip(lean, -9.0, 14.0)
    BODY_LOG.append((round(lean, 2), round(sq, 4)))
    if abs(lean) < 0.15 and abs(sq) < 0.003:
        return layer
    bb = layer.getchannel("A").point(lambda v: 255 if v > 150 else 0).getbbox()
    if not bb:
        return layer
    padx, pady = 220, 90
    box = (max(int(bb[0]) - padx, 0), max(int(bb[1]) - pady, 0), min(int(bb[2]) + padx, layer.width), min(int(bb[3]) + 60, layer.height))
    crop = layer.crop(box).convert("RGBa")                   # premultiplied, so the edges do not get dark fringes
    ax, ay = cx - box[0], gy - box[1] + 14                    # the feet's contact line
    T = math.tan(math.radians(lean)) * facing
    wx = 1.0 + 0.55 * sq
    hy = 1.0 - sq
    coeffs = (1 / wx, T / wx, ax - (ax + T * ay) / wx, 0.0, 1 / hy, ay - ay / hy)
    tr = crop.transform(crop.size, Image.AFFINE, coeffs, Image.BILINEAR).convert("RGBA")
    out = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    out.paste(tr, (box[0], box[1]))
    return out


# ---- movement log: how far each creature moves between two frames (a "teleport" or a weightless flick shows up as a huge step)
MOTION_LOG = []        # one row per scene and creature: frames, largest one-frame move in px, number of jumps over MOTION_JUMP_PX
_MOT, _SCENE = {}, [""]
MOTION_JUMP_PX = 200


def _motion_note(sp, st, bb):
    t = st.get("t")
    if t is None:
        return
    key = id(sp)
    x, y = (bb[0] + bb[2]) / 2.0, float(bb[3])
    w_, h_ = float(bb[2] - bb[0]), float(bb[3] - bb[1])
    edge = bb[0] <= 2 or bb[2] >= W - 2                     # partly off screen: its outline is clipped, so the centre is not reliable
    R = _MOT.get(key)
    if R is None or not (0.0 <= t - R["t"] < 0.1):
        R = _MOT[key] = {"t": t, "x": x, "y": y, "w": w_, "h": h_, "edge": edge, "max": 0.0, "jumps": 0, "n": 0, "cuts": 0, "scene": _SCENE[0], "t0": t}
        MOTION_LOG.append(R)
    elif t > R["t"]:
        step = math.hypot(x - R["x"], y - R["y"])
        cut = abs(h_ - R["h"]) > 0.2 * max(R["h"], 1) or abs(w_ - R["w"]) > 0.2 * max(R["w"], 1)    # a different shot size: a cut, not a move
        if cut:
            R["cuts"] += 1
        elif not (edge or R["edge"]):
            R["max"] = max(R["max"], step)
            R["jumps"] += step > MOTION_JUMP_PX
        R["n"] += 1
        R["t"], R["x"], R["y"], R["w"], R["h"], R["edge"] = t, x, y, w_, h_, edge


# ================================================================ four monsters with no legs, hands or wings
# Each has a clearly drawn face (or a clear main body), big enough to read on a phone, with texture and detail.
def _rng_pts(n, seed, fn):
    r = random.Random(seed)
    out = []
    while len(out) < n:
        x, y = r.uniform(-1, 1), r.uniform(-1, 1)
        if fn(x, y):
            out.append((x, y, r.random()))
    return out


_SWARM_PTS = {}


def draw_swarm(pen, sp, ph, st):
    """A person-shaped swarm of hundreds of black beetles and moths. No limbs: it hangs together as one body that tapers into a whirl.
    The face is a clear hollow: glowing eyes, a dark open mouth lined with pale insects for teeth."""
    t = st.get("t", 0.0)
    col, dk = sp["color"], dark(sp["color"], 0.5)
    roar = max(st.get("roar", 0.0), 0.35)
    if "body" not in _SWARM_PTS:
        def shape(x, y):          # x,y in -1..1: tall head, broad shoulders, arms hanging, tapering to a funnel
            yy = (y + 1) / 2        # 0 top .. 1 bottom
            if yy < 0.22:
                return (x * 1.0) ** 2 + ((yy - 0.11) / 0.11) ** 2 < 1
            if yy < 0.78:
                half = 0.62 - 0.22 * (yy - 0.22) / 0.56
                return abs(x) < half
            return abs(x) < 0.4 * (1 - (yy - 0.78) / 0.22) + 0.04
        _SWARM_PTS["body"] = _rng_pts(430, 7, shape)
    bob = 14 * math.sin(t * 1.7)
    H, Wd = 640, 210
    top = -300 - H + bob + 150
    # a dark solid core so the figure reads as one shape (and the face has something to sit on)
    pen.poly([(-Wd * 0.55, top + H * 0.26), (Wd * 0.55, top + H * 0.26), (Wd * 0.42, top + H * 0.78), (0, top + H), (-Wd * 0.42, top + H * 0.78)], dk, rim=False)
    pen.ell(0, top + H * 0.11, Wd * 0.46, H * 0.115, dk, rim=False)
    for i, (bx, by, rr) in enumerate(_SWARM_PTS["body"]):
        yy = (by + 1) / 2
        x = bx * Wd + 16 * math.sin(t * 4.6 + i) * (0.4 + yy) + (30 * math.sin(t * 2.2 + i * 0.3) * (yy ** 3))
        y = top + yy * H + 11 * math.cos(t * 5.1 + i * 1.3)
        face = yy < 0.2 and abs(bx) < 0.62                 # keep the face clear of insects
        if face and ((abs(bx - 0.28) < 0.24 and abs(yy - 0.085) < 0.045) or (abs(bx - 0.28) < 0.34 and 0.12 < yy < 0.19)):
            continue
        s = 7 + 7 * rr
        c = mix(col, (18, 16, 24), 0.4 + 0.5 * rr)
        pen.ell(x, y, s, s * 0.62, c, rim=False)
        if i % 3 == 0:                                      # wings flicker on a third of them
            fl = 0.6 + 0.4 * math.sin(t * 28 + i)
            pen.ell(x - s * 0.5, y - s * 0.6, s * 0.9 * fl, s * 0.35, mix(col, (150, 140, 130), 0.35), rim=False)
    # the face (towards the front: +x)
    fx, fy = 0.28 * Wd * 0.5 + 10, top + H * 0.085
    pen.ell(fx, fy + 6, 70, 48, (6, 4, 8), rim=False)
    for sgn in (-1, 1):
        eye(pen, fx + sgn * 28 + 10, fy, 13, sp, st)
    ml = top + H * 0.155
    pen.ell(fx + 8, ml + 12, 52, 8 + 34 * roar, (30, 4, 8), rim=False)
    for k in range(9):                                      # teeth made of pale insects
        tx = fx - 36 + k * 9.5
        pen.ell(tx + 8, ml + 2, 4.5, 8, (225, 220, 205), rim=False)
        pen.ell(tx + 8, ml + 22 + 30 * roar, 4.5, 8, (225, 220, 205), rim=False)
    for i in range(26):                                     # a loose cloud peeling away behind it
        a = t * 3 + i * 0.7
        pen.ell(-Wd * 0.6 - 30 - 18 * (i % 6) + 20 * math.sin(a), top + H * 0.35 + (i * 37) % 300, 5, 3, dk, rim=False)


def draw_tendril(pen, sp, ph, st):
    """A bloated body of wet flesh that hangs in the air and moves on a dozen long tendrils lined with suckers. One huge eye, a ring-toothed
    lamprey mouth beneath it and two smaller eyes. Nothing in it is a leg, an arm or a wing."""
    t = st.get("t", 0.0)
    col, dk = sp["color"], dark(sp["color"], 0.55)
    flesh = mix(col, (170, 110, 110), 0.25)
    roar = max(st.get("roar", 0.0), 0.3)
    moving = st.get("moving", True)
    bob = -250 + 22 * math.sin(t * 1.9)
    N = 13
    for i in range(N):                                       # tendrils first, so the body sits over their roots
        k = (i - (N - 1) / 2) / ((N - 1) / 2)
        L = 9
        sx, sy = k * 150, bob + 120 + abs(k) * 20
        pts, ws = [(sx, sy)], []
        for j in range(1, L + 1):
            u = j / L
            wave = math.sin(t * (2.4 if moving else 1.2) - u * 4.0 + i * 0.9 + ph * 0.5)
            x = sx + k * 120 * u + 38 * wave * u + (50 if moving else 0) * -u * 0.4
            y = min(sy + (230 - bob * 0.35) * u + 20 * math.cos(t * 2 + i + u * 3) * u, 4)
            pts.append((x, y))
        for j in range(len(pts) - 1):
            w = 34 * (1 - j / L) + 7
            pen.line([pts[j], pts[j + 1]], w, mix(flesh, dk, 0.25 + 0.4 * (j / L)))
            if j % 2 == 1:                                    # suckers
                mx, my = (pts[j][0] + pts[j + 1][0]) / 2, (pts[j][1] + pts[j + 1][1]) / 2
                pen.ell(mx, my + 3, w * 0.28, w * 0.18, (235, 190, 190), rim=False)
                pen.ell(mx, my + 3, w * 0.12, w * 0.08, (80, 20, 30), rim=False)
    pen.ell(0, bob, 190, 175, flesh)                         # the body
    pen.ell(-30, bob + 70, 150, 90, mix(flesh, dk, 0.35), rim=False)
    for k in range(14):                                       # veins and pustules
        a = k * 0.9 + 0.3
        vx, vy = math.cos(a) * 140, bob + math.sin(a) * 120
        pen.line([(vx, vy), (vx * 0.7 + 20, vy * 0.6 + bob * 0.4 + 30), (vx * 0.4, bob + 10)], 4, mix(flesh, (110, 20, 40), 0.6))
        if k % 3 == 0:
            pen.ell(vx * 0.9, vy, 13, 11, mix(flesh, (210, 190, 120), 0.5), rim=False)
    ex, ey = 58, bob - 38                                     # the huge eye
    pen.ell(ex, ey, 92, 82, (232, 224, 206), rim=False)
    for k in range(9):
        a = k * 0.7
        pen.line([(ex + math.cos(a) * 88, ey + math.sin(a) * 78), (ex + math.cos(a) * 40, ey + math.sin(a) * 34)], 3, (170, 40, 50))
    ec = sp.get("eye", (255, 190, 40))
    look = 10 * math.sin(t * 0.8)
    pen.ell(ex + 14 + look, ey, 50, 50, mix(ec, (30, 10, 10), 0.35), rim=False)
    pen.ell(ex + 14 + look, ey, 36, 36, ec, rim=False)
    pen.ell(ex + 14 + look, ey, 11, 38, (8, 4, 6), rim=False)
    pen.ell(ex + 2 + look, ey - 22, 11, 9, (255, 255, 255), rim=False)
    pen.poly([(ex - 96, ey - 8), (ex - 40, ey - 100), (ex + 60, ey - 104), (ex + 100, ey - 20), (ex + 40, ey - 62), (ex - 40, ey - 58)], dk, rim=False)   # heavy lid
    for sgn in (-1, 1):                                       # two small eyes
        eye(pen, ex - 30 + sgn * 78, bob + 70 - (0 if sgn < 0 else 6), 9, sp, st)
    my = bob + 112                                            # lamprey mouth: rings of teeth
    for r_, c_ in ((52, (70, 12, 22)), (38, (120, 20, 34)), (22, (30, 4, 10))):
        pen.ell(ex - 6, my, r_ * (1 + 0.35 * roar), r_ * 0.7 * (1 + 0.4 * roar), c_, rim=False)
    for ring, R in ((0, 46), (1, 31)):
        for k in range(14 - ring * 4):
            a = k * (2 * math.pi / (14 - ring * 4)) + ring
            tx, ty = ex - 6 + math.cos(a) * R, my + math.sin(a) * R * 0.7
            pen.poly([(tx - 3, ty - 2), (tx + 3, ty - 2), (tx - math.cos(a) * 11, ty - math.sin(a) * 8)], (240, 232, 214), rim=False)


def draw_orb(pen, sp, ph, st):
    """A pale sphere that floats and rolls: the whole body is one giant eye with a vein-lined white, a huge iris, and a toothed split that
    opens along its side. Dozens of long optic nerves drag behind it along the ground."""
    t = st.get("t", 0.0)
    col, dk = sp["color"], dark(sp["color"], 0.55)
    roar = max(st.get("roar", 0.0), 0.3)
    moving = st.get("moving", True)
    R = 215
    bob = -330 + 26 * math.sin(t * 1.4)
    for i in range(11):                                       # trailing nerves
        k = i / 10
        pts = [(-R * 0.5, bob + 40 + k * 80 - 40)]
        for j in range(1, 10):
            u = j / 9
            pts.append((-R * 0.5 - 60 * j - 14 * math.sin(t * 2.5 + i + u * 3) * u, min(bob + 60 + 250 * u + 12 * math.sin(t * 3 + i * 1.7 + j), 2)))
        for j in range(len(pts) - 1):
            pen.line([pts[j], pts[j + 1]], 16 * (1 - j / 10) + 3, mix(col, (150, 60, 70), 0.4 + 0.3 * k))
    spin = t * (1.1 if moving else 0.3)
    pen.ell(0, bob, R, R, (226, 218, 205))                    # the white
    pen.ell(-30, bob + 40, R * 0.82, R * 0.8, (206, 194, 182), rim=False)
    for k in range(18):
        a = k * 0.75 + spin * 0.2
        r0 = R * 0.95
        pen.line([(math.cos(a) * r0, bob + math.sin(a) * r0), (math.cos(a + 0.2) * R * 0.5, bob + math.sin(a + 0.2) * R * 0.5), (math.cos(a + 0.35) * R * 0.32, bob + math.sin(a + 0.35) * R * 0.32)], 3, (176, 44, 56))
    ec = sp.get("eye", (255, 190, 40))
    ix, iy = R * 0.42 + 8 * math.sin(t * 0.9), bob - R * 0.12
    for r_, c_ in ((112, mix(ec, (20, 8, 8), 0.6)), (96, mix(ec, (20, 8, 8), 0.3)), (78, ec)):
        pen.ell(ix, iy, r_, r_, c_, rim=False)
    for k in range(24):                                       # iris fibres
        a = k * math.pi / 12
        pen.line([(ix + math.cos(a) * 30, iy + math.sin(a) * 30), (ix + math.cos(a) * 92, iy + math.sin(a) * 92)], 3, mix(ec, (255, 255, 230), 0.4) if k % 2 else mix(ec, (60, 20, 10), 0.4))
    pen.ell(ix + 4, iy, 22 * (1.3 - 0.4 * roar), 62, (6, 4, 8), rim=False)
    pen.ell(ix - 22, iy - 34, 16, 12, (255, 255, 255), rim=False)
    my = bob + R * 0.55                                       # the split mouth along the lower side
    op = 12 + 70 * roar
    pen.poly([(-R * 0.45, my - 6), (R * 0.8, my - 28), (R * 0.9, my + op), (-R * 0.4, my + op * 0.8)], (70, 8, 16), rim=False)
    for k in range(12):
        tx = -R * 0.4 + k * (R * 1.2 / 12)
        pen.poly([(tx, my - 10 - k), (tx + 14, my - 16 - k), (tx + 7, my + 14 + 0.3 * op)], (242, 236, 220), rim=False)
        pen.poly([(tx, my + op - 4), (tx + 14, my + op - 4), (tx + 7, my + op * 0.2 - 8)], (242, 236, 220), rim=False)


def draw_bloom(pen, sp, ph, st):
    """A flower the size of a house: a thick, swaying stem with barbed leaves, a ring of petals and in the middle a face -- two compound
    eyes and a round mouth with spiralling rows of teeth. It drags itself on root-vines that trail behind it."""
    t = st.get("t", 0.0)
    col, dk = sp["color"], dark(sp["color"], 0.5)
    leaf = mix(col, (40, 90, 50), 0.5)
    petal = mix(col, (170, 60, 90), 0.5)
    roar = max(st.get("roar", 0.0), 0.35)
    moving = st.get("moving", True)
    for i in range(7):                                        # root-vines dragging behind
        pts = [(-30, -20)]
        for j in range(1, 9):
            u = j / 8
            pts.append((-30 - 70 * j - 12 * math.sin(t * 2.2 + i + u * 3), -8 + 8 * math.sin(t * 3 + i * 1.4 + j) - 6 * i * 0.3))
        pen.line(pts, 12 - i * 0.8, mix(dk, (60, 40, 30), 0.4))
    sway = 36 * math.sin(t * 1.3) + (24 if moving else 0)
    stem = [(0, 0), (-20, -150), (10 + sway * 0.3, -300), (30 + sway * 0.7, -450), (70 + sway, -570)]
    for j in range(len(stem) - 1):
        pen.line([stem[j], stem[j + 1]], 62 - 9 * j, mix(leaf, dk, 0.2 + 0.1 * j))
    for j, (lx, ly) in enumerate(stem[1:4]):                  # barbed leaves
        for sgn in (-1, 1):
            if sgn == -1 and j == 0:
                continue
            tipx, tipy = lx + sgn * (150 - 20 * j), ly - 30 - 26 * math.sin(t * 2 + j)
            pen.poly([(lx, ly), (lx + sgn * 60, ly - 62), (tipx, tipy), (lx + sgn * 70, ly + 4)], leaf)
            pen.line([(lx, ly - 4), (tipx - sgn * 10, tipy + 6)], 4, dark(leaf, 0.6))
            for b in range(3):
                pen.poly([(lx + sgn * (40 + b * 26), ly - 30 - b * 8), (lx + sgn * (52 + b * 26), ly - 52 - b * 8), (lx + sgn * (60 + b * 26), ly - 28 - b * 8)], (225, 220, 200), rim=False)
    hx, hy = stem[-1]
    for k in range(9):                                        # petals
        a = k * (2 * math.pi / 9) + math.sin(t * 0.8) * 0.08
        px, py = hx + math.cos(a) * 112, hy + math.sin(a) * 112
        ox, oy = -math.sin(a) * 46, math.cos(a) * 46
        pen.poly([(hx + math.cos(a) * 40 + ox * 0.5, hy + math.sin(a) * 40 + oy * 0.5), (px + ox, py + oy), (hx + math.cos(a) * 190, hy + math.sin(a) * 190),
                  (px - ox, py - oy), (hx + math.cos(a) * 40 - ox * 0.5, hy + math.sin(a) * 40 - oy * 0.5)], mix(petal, dk, 0.12 * (k % 3)))
    pen.ell(hx, hy, 96, 96, mix(col, (230, 190, 90), 0.35))   # the face disc
    pen.ell(hx, hy, 82, 82, mix(col, (60, 30, 30), 0.45), rim=False)
    for sgn in (-1, 1):                                       # compound eyes: a cluster of lenses
        ex, ey = hx + 14 + sgn * 36, hy - 34
        pen.ell(ex, ey, 26, 26, (18, 10, 16), rim=False)
        for a in range(6):
            pen.ell(ex + math.cos(a * 1.05) * 12, ey + math.sin(a * 1.05) * 12, 8, 8, mix(sp.get("eye", (255, 190, 40)), (40, 20, 10), 0.25 * (a % 2)), rim=False)
        pen.ell(ex, ey, 9, 9, sp.get("eye", (255, 190, 40)), rim=False)
    my = hy + 28
    for r_, c_ in ((44, (60, 8, 16)), (30, (110, 16, 30)), (16 + 6 * roar, (20, 2, 6))):
        pen.ell(hx + 10, my, r_ * (1 + 0.25 * roar), r_ * 0.8 * (1 + 0.3 * roar), c_, rim=False)
    for ring, R_ in ((0, 40), (1, 26)):
        for k in range(12 - ring * 4):
            a = k * (2 * math.pi / (12 - ring * 4)) + t * 0.6 * (1 if ring else -1)
            tx, ty = hx + 10 + math.cos(a) * R_, my + math.sin(a) * R_ * 0.8
            pen.poly([(tx - 3, ty - 2), (tx + 3, ty - 2), (tx - math.cos(a) * 11, ty - math.sin(a) * 9)], (242, 236, 220), rim=False)


KINDS.update({"swarm": draw_swarm, "tendril": draw_tendril, "orb": draw_orb, "bloom": draw_bloom})
KIND_SCALE.update({"swarm": 0.85, "tendril": 1.0, "orb": 1.0, "bloom": 0.9})
HEAD_AT.update({"swarm": (20, -820), "tendril": (60, -300), "orb": (90, -350), "bloom": (90, -520)})
PRINT_OF.update({"swarm": "dots", "tendril": "slither", "orb": "frost", "bloom": "slither"})
BODIES = BODIES + ("swarm", "tendril", "orb", "bloom")

# ====================================================================================================== VISTA
# A far landscape seen in FIRST PERSON. The camera zooms into one far landmark (a waterfall, a mountain with a cave, a
# ruined tower); something comes out of it and down the land toward you, and the land answers it: water splashes, birds
# scatter, trees sway, rocks fall, mist swirls. The whole landscape is painted ONCE at 3x ("plates") so the zoom stays sharp.
VS = 3                                                       # plate scale: 3 pixels for every drawing unit
VISTA_KINDS = ("falls", "peak", "tower")


class _VC:
    """A big picture painted in drawing units (1080 x 1920) but with VS times the pixels."""

    def __init__(self, mode="RGB", fill=None):
        if fill is None:
            fill = (0, 0, 0, 0) if mode == "RGBA" else (0, 0, 0)       # overlay layers start clear, not black
        self.im = Image.new(mode, (W * VS, H * VS), fill)
        self.d = ImageDraw.Draw(self.im, "RGBA")

    def P(self, pts, fill):
        self.d.polygon([(x * VS, y * VS) for x, y in pts], fill=fill)

    def L(self, pts, fill, w=1.0):
        self.d.line([(x * VS, y * VS) for x, y in pts], fill=fill, width=max(1, int(round(w * VS))))

    def E(self, box, fill):
        self.d.ellipse([box[0] * VS, box[1] * VS, box[2] * VS, box[3] * VS], fill=fill)

    def R(self, box, fill):
        self.d.rectangle([box[0] * VS, box[1] * VS, box[2] * VS, box[3] * VS], fill=fill)

    def masked(self, poly, fn):
        """fn(canvas) draws freely; only what falls inside the polygon is kept (used for rock facets, snow, strata)."""
        xs, ys = [p[0] for p in poly], [p[1] for p in poly]
        x0, y0, x1, y1 = max(0, min(xs)), max(0, min(ys)), min(W, max(xs)), min(H, max(ys))
        if x1 <= x0 or y1 <= y0:
            return
        bw, bh = int((x1 - x0) * VS) + 2, int((y1 - y0) * VS) + 2
        tile = _VC.__new__(_VC)
        tile.im = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        tile.d = ImageDraw.Draw(tile.im, "RGBA")
        tile.ox, tile.oy = x0, y0
        tile.lit_side = getattr(self, "lit_side", -1)
        # in a tile, drawing units are shifted: wrap the drawing calls
        sh = lambda pts: [(x - x0, y - y0) for x, y in pts]
        tile.P = lambda pts, fill: tile.d.polygon([((x - x0) * VS, (y - y0) * VS) for x, y in pts], fill=fill)
        tile.L = lambda pts, fill, w=1.0: tile.d.line([((x - x0) * VS, (y - y0) * VS) for x, y in pts], fill=fill, width=max(1, int(round(w * VS))))
        tile.E = lambda box, fill: tile.d.ellipse([(box[0] - x0) * VS, (box[1] - y0) * VS, (box[2] - x0) * VS, (box[3] - y0) * VS], fill=fill)
        fn(tile)
        mask = Image.new("L", (bw, bh), 0)
        ImageDraw.Draw(mask).polygon([((x - x0) * VS, (y - y0) * VS) for x, y in poly], fill=255)
        a = ImageChops.multiply(tile.im.getchannel("A"), mask)
        tile.im.putalpha(a)
        if self.im.mode == "RGBA":
            layer = Image.new("RGBA", self.im.size, (0, 0, 0, 0))
            layer.paste(tile.im, (int(x0 * VS), int(y0 * VS)))
            self.im.alpha_composite(layer)
        else:
            self.im.paste(tile.im.convert("RGB"), (int(x0 * VS), int(y0 * VS)), tile.im.getchannel("A"))


def _v_pal(world):
    """Colours for the landscape: taken from the place the story is in, so the vista belongs to the same world."""
    st = world.st
    s0, s1 = rgb(st["sky"][0]), rgb(st["sky"][1])
    night = (_lum01(st["sky"][0]) + _lum01(st["sky"][1])) / 2 < 0.34
    far, near, grd = rgb(st["far"]), rgb(st["near"]), rgb(st["ground"])
    pal = dict(s0=s0, s1=s1, night=night)
    pal["haze"] = light(mix(s1, (200, 205, 225), 0.35), 0.1)
    pal["rock_l"] = light(mix(far, s1, 0.25), 0.22)             # the lit side of rock
    pal["rock_d"] = dark(mix(near, s0, 0.35), 0.62)             # the shadow side
    pal["rock_m"] = mix(pal["rock_l"], pal["rock_d"], 0.5)
    pal["tree"] = dark(mix(near, (20, 48, 40), 0.45), 0.6)
    pal["tree_l"] = light(mix(near, (40, 80, 60), 0.4), 0.18)
    pal["grass"] = mix(grd, (34, 58, 36), 0.55)
    pal["water"] = mix(s1, (30, 70, 105), 0.45)
    pal["snow"] = (232, 238, 248) if not st["ground"].lower().startswith("#e") else (255, 255, 255)
    pal["moon"] = rgb(st["sun"]) if isinstance(st.get("sun"), str) and st["sun"].startswith("#") else ((236, 238, 214) if night else (255, 240, 205))
    pal["glow"] = (255, 70, 56)
    return pal


def _v_ridge(rnd, y0, amp, step=7.0, rough=0.55, x0=-20, x1=W + 20, peaks=3):
    """A ragged ridge line: points (x, y) from left to right."""
    n = int((x1 - x0) / step) + 1
    ph = [rnd.uniform(0, 6.28) for _ in range(peaks + 3)]
    pts = []
    walk = 0.0
    for i in range(n):
        x = x0 + i * step
        v = 0.0
        for k in range(peaks):
            v += math.sin(x / (170 + 140 * k) * (1 + 0.6 * k) + ph[k]) * (1.0 / (1 + 0.55 * k))
        walk += rnd.uniform(-1, 1) * rough * 5
        walk *= 0.93
        pts.append((x, y0 - amp * (0.5 + 0.5 * v) - walk))
    return pts


def _v_pine(c, x, y, h, col, lit, rnd, lean=0.0):
    """One pine: a stack of drooping, ragged skirts with a lit side (the side facing the moon) and a trunk."""
    w = h * rnd.uniform(0.26, 0.38)
    tiers = max(3, min(9, int(h / 13)))
    big = h > 55
    ls = getattr(c, "lit_side", -1)
    for t in range(tiers):
        k0, k1 = t / tiers, (t + 1) / tiers
        yb = y - h * k0 * 0.88
        yt = y - h * (k1 + 0.10) * 0.98
        ww = w * (1.0 - k0 * 0.80)
        xl = x + lean * (1 - k0) * h * 0.04
        if big:
            nseg = 7
            edge = [(xl - ww, yb)]
            for i in range(1, nseg):
                px = xl - ww + 2 * ww * i / nseg
                py = yb + ww * 0.13 * (1.0 if i % 2 else -0.25) * rnd.uniform(0.6, 1.3)
                edge.append((px, py))
            edge.append((xl + ww, yb))
            c.P([(xl, yt)] + edge, col)
            # a lighter wedge on the moon side, and dark drooping shadow lines under the skirt
            if ls < 0:
                c.P([(xl, yt), (xl - ww, yb), (xl - ww * 0.35, yb + ww * 0.05), (xl - ww * 0.02, yt + (yb - yt) * 0.15)], lit)
            else:
                c.P([(xl, yt), (xl + ww, yb), (xl + ww * 0.35, yb + ww * 0.05), (xl + ww * 0.02, yt + (yb - yt) * 0.15)], lit)
            for i in range(2, nseg - 1, 2):
                px = xl - ww + 2 * ww * i / nseg
                c.L([(px, yb - ww * 0.02), (px + rnd.uniform(-2, 2), yb + ww * 0.17)], (0, 0, 0, 110), max(0.8, h * 0.006))
        else:
            c.P([(xl - ww, yb), (xl, yt), (xl + ww, yb)], col)
            if h > 20:
                if ls < 0:
                    c.P([(xl - ww, yb), (xl, yt), (xl - ww * 0.06, yb)], lit)
                else:
                    c.P([(xl + ww, yb), (xl, yt), (xl + ww * 0.06, yb)], lit)
    tw = max(h * 0.025, 0.4)
    c.P([(x - tw, y), (x - tw, y + h * 0.1), (x + tw, y + h * 0.1), (x + tw, y)], col)


def _v_forest(c, rnd, pal, x0, x1, y0, y1, n, h0, h1, fog=0.0, zone=None):
    """Thousands of tiny pines, small and hazy far up, bigger and darker near the bottom of the band."""
    trees = []
    for _ in range(n):
        x = rnd.uniform(x0, x1)
        y = rnd.uniform(y0, y1)
        if zone and not zone(x, y):
            continue
        trees.append((y, x))
    trees.sort()
    for y, x in trees:
        k = (y - y0) / max(1.0, (y1 - y0))
        h = h0 + (h1 - h0) * k * rnd.uniform(0.8, 1.2)
        col = mix(pal["tree"], pal["haze"], fog * (1 - k)) + (255,)
        lit = mix(pal["tree_l"], pal["haze"], fog * (1 - k)) + (255,)
        _v_pine(c, x, y, h, col, lit, rnd, lean=rnd.uniform(-1, 1))


def _v_sky(pal, rnd, kind):
    """The sky at 1x: gradient, a big soft moon or sun with a halo, thin drifting cloud banks."""
    ys = np.linspace(0, 1, H, dtype=np.float32)[:, None]
    top, bot = np.array(pal["s0"], np.float32), np.array(pal["s1"], np.float32)
    k = np.clip((ys / 0.62), 0, 1) ** 0.9
    g = top[None, None, :] * (1 - k[..., None]) + bot[None, None, :] * k[..., None]
    g = np.broadcast_to(g, (H, W, 3)).copy()
    mx, my = (300 if kind != "peak" else 760), 330
    yy, xx = np.mgrid[0:H:2, 0:W:2].astype(np.float32)
    dist = np.sqrt((xx - mx) ** 2 + (yy - my) ** 2)
    halo = np.exp(-(dist / 330.0) ** 1.4)[..., None] * np.array(pal["moon"], np.float32)[None, None, :] * 0.34
    halo = np.asarray(Image.fromarray(np.clip(halo, 0, 255).astype(np.uint8)).resize((W, H), Image.BICUBIC), np.float32)
    g = g + halo
    # thin cloud banks
    nz = _fractal_noise(H // 8, W // 8, rnd.randint(1, 99999))
    nz = np.asarray(Image.fromarray((np.clip(nz, 0, 1) * 255).astype(np.uint8)).resize((W, H), Image.BICUBIC), np.float32) / 255.0
    band = np.exp(-((ys - 0.26) / 0.12) ** 2) + 0.6 * np.exp(-((ys - 0.46) / 0.08) ** 2)
    cl = np.clip((nz - 0.52) * 3.4, 0, 1) * band * 0.55
    ccol = mix(pal["haze"], pal["moon"], 0.3)
    g = g * (1 - cl[..., None]) + np.array(ccol, np.float32)[None, None, :] * cl[..., None]
    return Image.fromarray(np.clip(g, 0, 255).astype(np.uint8)), (mx, my)


def _fractal_noise(h, w, seed):
    r = np.random.RandomState(seed)
    out = np.zeros((h, w), np.float32)
    amp, tot = 1.0, 0.0
    for o in range(5):
        sh, sw = max(2, h // (2 ** (4 - o)) + 2), max(2, w // (2 ** (4 - o)) + 2)
        base = r.rand(sh, sw).astype(np.float32)
        up = np.asarray(Image.fromarray((base * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC), np.float32) / 255.0
        out += up * amp
        tot += amp
        amp *= 0.55
    return out / tot


class Vista:
    """One painted landscape: back / front / near plates (3x), the path the creature will take, and where things happen."""

    def __init__(self, world, kind=None, seed=1, side=None):
        self.rnd = random.Random(seed * 7919 + 13)
        rnd = self.rnd
        self.pal = pal = _v_pal(world)
        self.kind = kind if kind in VISTA_KINDS else rnd.choice(VISTA_KINDS)
        self.side = side if side in (-1, 1) else rnd.choice((-1, 1))
        side = self.side
        # the far landmark stands at one side, about a third of the way up the picture
        self.lx = 540 + side * 215
        self.base_y = 1010                                         # where the landmark's foot meets the land
        self.horizon = 900
        sky, (mx, my) = _v_sky(pal, rnd, self.kind)
        back = _VC("RGB")
        back.im = sky.resize((W * VS, H * VS), Image.BICUBIC)
        back.d = ImageDraw.Draw(back.im, "RGBA")
        self.ls = back.lit_side = -1 if mx < 540 else 1
        self._stars_moon(back, mx, my)
        # three far mountain ranges, hazier the further they are
        for i, (yb, amp, fogk) in enumerate(((880, 250, 0.72), (940, 190, 0.52), (985, 130, 0.34))):
            col = mix(pal["rock_m"], pal["haze"], fogk)
            pts = _v_ridge(rnd, yb, amp, peaks=3 + i)
            back.P(pts + [(W + 20, yb + 140), (-20, yb + 140)], col + (255,))
            # lit rims and snow flecks on the highest ridges
            for j in range(0, len(pts) - 1, 1):
                if rnd.random() < 0.55:
                    back.L([pts[j], pts[j + 1]], mix(pal["haze"], (255, 255, 255), 0.4) + (150,), 1.1)
            for _ in range(10 + 4 * i):
                x = rnd.uniform(0, W)
                yk = min(pts, key=lambda p: abs(p[0] - x))
                ss = rnd.uniform(14, 40)
                back.P([(x - ss, yk[1] + ss * 1.1), (x, yk[1] - 2), (x + ss * 0.8, yk[1] + ss * 1.0)], pal["snow"] + (170 - 30 * i,))
        # forests on the far hills: so many tiny trees that they read as texture, and as detail when you zoom
        _v_forest(back, rnd, pal, -10, W + 10, 940, 1060, 1400, 8, 20, fog=0.55)
        getattr(self, "_" + self.kind)(back, pal, rnd)
        self._land(back, pal, rnd)
        self.back = back.im
        self.front = self._front_plate(pal, rnd)
        self.near = self._near_plate(pal, rnd)
        self.heroes = self._heroes(pal, rnd)

    # ---------------------------------------------------------------- sky details
    def _stars_moon(self, c, mx, my):
        rnd, pal = self.rnd, self.pal
        if pal["night"]:
            for _ in range(240):
                x, y = rnd.uniform(0, W), rnd.uniform(0, 760)
                r = rnd.choice((0.6, 0.7, 0.8, 0.9, 1.1, 1.5))
                a = int(110 + 140 * rnd.random())
                c.E((x - r * 2.6, y - r * 2.6, x + r * 2.6, y + r * 2.6), (210, 220, 255, 14))
                c.E((x - r, y - r, x + r, y + r), (235, 240, 255, a))
        R_ = 62
        c.E((mx - R_ * 1.55, my - R_ * 1.55, mx + R_ * 1.55, my + R_ * 1.55), pal["moon"] + (28,))
        c.E((mx - R_ * 1.22, my - R_ * 1.22, mx + R_ * 1.22, my + R_ * 1.22), pal["moon"] + (46,))
        c.E((mx - R_, my - R_, mx + R_, my + R_), pal["moon"] + (255,))
        for _ in range(11):                                                      # craters
            a = rnd.uniform(0, 6.28)
            rr = rnd.uniform(0, R_ * 0.72)
            cx_, cy_ = mx + math.cos(a) * rr, my + math.sin(a) * rr
            cr = rnd.uniform(4, 11)
            c.E((cx_ - cr, cy_ - cr, cx_ + cr, cy_ + cr), dark(pal["moon"], 0.86) + (120,))

    # ---------------------------------------------------------------- the three landmarks
    def _peak(self, c, pal, rnd):
        """A tall snow-capped mountain, lit on one side, with a dark cave mouth low on its face and scree below it."""
        s = self.side
        lx, by = self.lx, self.base_y
        top = (lx - s * 20, 430)
        # outline: from the left foot up a jagged face to the summit and down the right side
        left, right = lx - 330, lx + 330
        L = []
        n = 26
        for i in range(n + 1):
            k = i / n
            x = left + (top[0] - left) * k
            y = by + (top[1] - by) * (k ** 0.86) + rnd.uniform(-14, 14) * math.sin(k * 3.14)
            L.append((x + rnd.uniform(-7, 7), y))
        Rr = []
        for i in range(n + 1):
            k = i / n
            x = top[0] + (right - top[0]) * k
            y = top[1] + (by - top[1]) * (k ** 0.9) + rnd.uniform(-14, 14) * math.sin(k * 3.14)
            Rr.append((x + rnd.uniform(-7, 7), y))
        outline = L + Rr + [(right + 40, by + 90), (left - 40, by + 90)]
        lit_side, shade_side = (L, Rr) if s > 0 else (Rr, L)
        # the lit and the shadowed halves: split by the summit ridge line that runs from the top down the middle
        ridge = [(top[0], top[1])]
        for i in range(1, 12):
            k = i / 11
            ridge.append((top[0] + rnd.uniform(-34, 34) * math.sin(k * 3.14) + s * 18 * k, top[1] + (by - top[1]) * k))
        half_l = L + ridge[::-1] if True else None
        half_l = [p for p in L] + [(top[0], top[1])] + ridge[1:][::-1][::-1]
        pl = L + [r for r in ridge[::-1]]
        pr = ridge + Rr[::-1][::-1]
        back_lit = pal["rock_l"] + (255,)
        back_shd = pal["rock_d"] + (255,)
        c.P(outline, pal["rock_m"] + (255,))
        c.masked(outline, lambda t: (t.P(pl, back_lit if s < 0 else back_shd), t.P(pr + [(right + 60, by + 100), (ridge[-1][0], by + 100)], back_shd if s < 0 else back_lit)))
        # facets: dozens of triangular planes, lighter facing the moon and darker away, long gullies running down
        def facets(t):
            for _ in range(190):
                x = rnd.uniform(left, right)
                y = rnd.uniform(top[1], by)
                w_ = rnd.uniform(14, 62)
                h_ = rnd.uniform(26, 110)
                lit_f = (x < top[0]) == (s > 0)
                a = rnd.randint(40, 110)
                col = (255, 255, 255, a // 3) if lit_f else (0, 0, 20, a)
                t.P([(x, y), (x + w_ * rnd.uniform(0.3, 1), y + h_ * 0.35), (x + w_ * rnd.uniform(-0.5, 0.4), y + h_)], col)
            for _ in range(46):
                x = rnd.uniform(left + 40, right - 40)
                y0 = rnd.uniform(top[1] + 40, by - 200)
                pts = [(x, y0)]
                for j in range(1, 7):
                    pts.append((pts[-1][0] + rnd.uniform(-9, 9), pts[-1][1] + rnd.uniform(22, 50)))
                t.L(pts, (8, 10, 24, rnd.randint(80, 150)), rnd.uniform(1.2, 3.2))
        c.masked(outline, facets)
        # the snow cap: an irregular lower edge, drifts down the gullies, blue shadow under it
        edge_y = top[1] + 245
        sn = [(left + (top[0] - left) * 0.45, edge_y - 12)]
        for i in range(0, 17):
            k = i / 16
            x = (left + (top[0] - left) * 0.45) + (right - (right - top[0]) * 0.45 - (left + (top[0] - left) * 0.45)) * k
            sn.append((x, edge_y + rnd.uniform(-26, 70) + 38 * math.sin(k * 9)))
        snow_poly = [(top[0], top[1] - 4)] + sn
        c.masked(outline, lambda t: (t.P(snow_poly + [(top[0], top[1] - 4)], pal["snow"] + (245,)),
                                     t.P([(x, y) for x, y in sn[len(sn) // 2:]] + [(top[0], top[1] + 20)], (90, 110, 170, 90))))
        def runs(t):
            for _ in range(80):                                                  # snow runs down the gullies
                x = rnd.uniform(left + 80, right - 80)
                y = edge_y + rnd.uniform(-10, 50)
                if abs(x - top[0]) < 200:
                    t.L([(x, y), (x + rnd.uniform(-6, 6), y + rnd.uniform(24, 80))], pal["snow"] + (190,), rnd.uniform(1.5, 3.6))
        c.masked(outline, runs)
        # the rim light along the lit edge
        for pts in (lit_side,):
            for i in range(len(pts) - 1):
                c.L([pts[i], pts[i + 1]], mix(pal["moon"], (255, 255, 255), 0.4) + (200,), 1.6)
        # forest climbing the lower slopes
        zone = lambda x, y: (abs(x - lx) < 330 - (by - y) * 0.2) and y > 760
        _v_forest(c, rnd, pal, left, right, 760, by + 10, 2200, 7, 24, fog=0.5, zone=zone)
        # the cave mouth, low on the face: a black arch with a faint red glow deep inside, and a scree fan under it
        self.cx, self.cy = lx + s * 14, by - 26
        c.P([(self.cx - 150, by + 20), (self.cx - 50, self.cy - 46), (self.cx + 70, self.cy - 50), (self.cx + 170, by + 24)], pal["rock_d"] + (255,))     # overhang
        arch = [(self.cx - 46, by + 30), (self.cx - 46, self.cy - 6)] + [(self.cx + 46 * math.cos(math.pi * (1 - j / 10)), self.cy - 6 - 52 * math.sin(math.pi * (1 - j / 10))) for j in range(1, 10)] + [(self.cx + 46, self.cy - 6), (self.cx + 46, by + 30)]
        c.P(arch, (2, 2, 8, 255))
        c.E((self.cx - 18, self.cy - 8, self.cx + 18, self.cy + 24), (70, 8, 10, 120))
        for _ in range(120):                                                    # scree
            x = self.cx + rnd.uniform(-160, 160)
            y = by + rnd.uniform(-6, 40)
            if abs(x - self.cx) < 40 + (y - by) * 0.4 + 10:
                continue
            sz = rnd.uniform(2, 7)
            c.P([(x, y), (x + sz, y - sz * 0.6), (x + sz * 1.6, y + sz * 0.2)], mix(pal["rock_m"], pal["rock_d"], rnd.random()) + (255,))
        self.emerge = (self.cx, by + 22)
        self.size_hint = (330, 640)
        self.c_h0 = 82

    def _falls(self, c, pal, rnd):
        """A tall dark cliff with a river pouring over a notch (the water itself is animated), a pool and boulders at the foot,
        pines on the ledges, and a cave hidden behind the curtain."""
        s = self.side
        ls = self.ls
        lx, by = self.lx, self.base_y
        topy = 540
        fx = lx - s * 10
        # outline: a trapezoid with ragged, stepped shoulders and a notch where the river comes over
        pts = [(lx - 340, by + 90)]
        for i in range(1, 9):                                    # left flank, climbing in steps
            k = i / 8
            pts.append((lx - 340 + 80 * k + rnd.uniform(-14, 14), by + 90 - (by + 90 - topy - 30) * k ** 0.8 + rnd.uniform(-10, 10)))
        x = lx - 262
        while x < lx + 262:
            x += rnd.uniform(16, 38)
            dip = 20 if abs(x - fx) < 40 else 0
            pts.append((min(x, lx + 262), topy + rnd.uniform(-22, 18) + dip))
        for i in range(1, 9):                                    # right flank, stepping down
            k = i / 8
            pts.append((lx + 262 + 80 * k + rnd.uniform(-14, 14), topy + 30 + (by + 90 - topy - 30) * k ** 1.2 + rnd.uniform(-10, 10)))
        cliff = pts
        c.P(cliff, pal["rock_m"] + (255,))
        def face(t):
            xs = lx - 360
            while xs < lx + 360:                                 # tall rock columns: moon side light, far side dark
                w_ = rnd.uniform(22, 70)
                lit_ = ((xs + w_ / 2 - lx) * ls < 0) if ls else True
                col = mix(pal["rock_l"] if lit_ else pal["rock_d"], pal["rock_m"], rnd.uniform(0.05, 0.5)) + (255,)
                t.P([(xs, topy - 50), (xs + w_, topy - 50), (xs + w_ + rnd.uniform(-10, 10), by + 100), (xs + rnd.uniform(-10, 10), by + 100)], col)
                xs += w_
            for _ in range(110):                                 # broken facets
                x0, y0 = rnd.uniform(lx - 340, lx + 340), rnd.uniform(topy, by)
                w_, h_ = rnd.uniform(14, 54), rnd.uniform(22, 90)
                lit_f = (x0 - lx) * ls < 0
                t.P([(x0, y0), (x0 + w_, y0 + h_ * 0.3), (x0 + w_ * rnd.uniform(-0.4, 0.6), y0 + h_)], (255, 255, 255, 30) if lit_f else (0, 0, 16, rnd.randint(50, 120)))
            for _ in range(70):                                  # strata
                y = rnd.uniform(topy + 20, by)
                x0 = rnd.uniform(lx - 340, lx + 260)
                t.L([(x0, y), (x0 + rnd.uniform(40, 170), y + rnd.uniform(-6, 6))], (0, 0, 12, rnd.randint(50, 110)), rnd.uniform(1, 2.4))
            for _ in range(60):                                  # cracks
                x0, y0 = rnd.uniform(lx - 340, lx + 340), rnd.uniform(topy + 20, by - 40)
                p2 = [(x0, y0)]
                for j in range(6):
                    p2.append((p2[-1][0] + rnd.uniform(-7, 7), p2[-1][1] + rnd.uniform(14, 38)))
                t.L(p2, (4, 6, 16, rnd.randint(90, 170)), rnd.uniform(1, 2.6))
            for _ in range(46):                                  # pale ledges
                x0, y0 = rnd.uniform(lx - 340, lx + 340), rnd.uniform(topy + 30, by - 20)
                t.L([(x0, y0), (x0 + rnd.uniform(20, 80), y0 + rnd.uniform(-3, 3))], mix(pal["haze"], (255, 255, 255), 0.4) + (rnd.randint(50, 130),), rnd.uniform(1, 2.2))
            for _ in range(40):                                  # moss and wet streaks running down from the top
                x0, y0 = rnd.uniform(lx - 330, lx + 330), rnd.uniform(topy, topy + 160)
                t.L([(x0, y0), (x0 + rnd.uniform(-5, 5), y0 + rnd.uniform(40, 200))], mix(pal["tree"], pal["rock_d"], 0.3) + (90,), rnd.uniform(1.5, 4))
        c.masked(cliff, face)
        # pines clinging to ledges halfway down, and a rim of them along the top
        for _ in range(26):
            x = rnd.uniform(lx - 320, lx + 320)
            y = rnd.uniform(topy + 150, by - 40)
            if abs(x - fx) < 70:
                continue
            _v_pine(c, x, y, rnd.uniform(16, 32), mix(pal["tree"], pal["rock_d"], 0.2) + (255,), pal["tree_l"] + (255,), rnd, rnd.uniform(-1, 1))
        _v_forest(c, rnd, pal, lx - 270, lx + 270, topy - 30, topy + 26, 320, 16, 36, fog=0.25,
                  zone=lambda x, y: abs(x - fx) > 34)
        # the river channel and the cave behind where the curtain will hang
        self.fall = dict(x=fx, top=topy + 14, bot=by + 14, w_top=50, w_bot=70)
        c.P([(fx - 34, topy + 18), (fx + 34, topy + 18), (fx + 50, by + 14), (fx - 50, by + 14)], mix(pal["rock_d"], (0, 0, 0), 0.45) + (255,))
        cy0, cy1 = by - 150, by + 10
        c.P([(fx - 40, cy0 + 30), (fx - 24, cy0), (fx + 24, cy0), (fx + 40, cy0 + 30), (fx + 44, cy1), (fx - 44, cy1)], (3, 4, 10, 255))
        c.E((fx - 17, cy0 + 48, fx + 17, cy0 + 86), (70, 8, 10, 90))
        # the pool at the foot with boulders round its rim
        pool = (fx - 200, by - 26, fx + 200, by + 80)
        c.E(pool, dark(pal["water"], 0.7) + (255,))
        c.E((pool[0] + 12, pool[1] + 6, pool[2] - 12, pool[3] - 8), mix(pal["water"], pal["s1"], 0.3) + (255,))
        c.E((pool[0] + 34, pool[1] + 16, pool[2] - 34, pool[3] - 20), mix(pal["water"], pal["haze"], 0.3) + (255,))
        self.pool = pool
        for _ in range(60):
            ang = rnd.uniform(0, 6.28)
            x = (pool[0] + pool[2]) / 2 + math.cos(ang) * (pool[2] - pool[0]) / 2 * rnd.uniform(0.96, 1.08)
            y = (pool[1] + pool[3]) / 2 + math.sin(ang) * (pool[3] - pool[1]) / 2 * rnd.uniform(0.96, 1.1)
            sz = rnd.uniform(5, 20)
            col = mix(pal["rock_d"], pal["rock_m"], rnd.random())
            c.P([(x - sz, y + sz * 0.3), (x - sz * 0.4, y - sz * 0.7), (x + sz * 0.5, y - sz * 0.8), (x + sz, y + sz * 0.3)], col + (255,))
            c.P([(x - sz * 0.4, y - sz * 0.7), (x + sz * 0.5, y - sz * 0.8), (x + sz * 0.1, y - sz * 0.3)], pal["rock_l"] + (140,))
        _v_forest(c, rnd, pal, lx - 380, lx + 380, by + 22, by + 100, 360, 18, 40, fog=0.3,
                  zone=lambda x, y: abs(x - fx) > 225 or y > by + 88)
        self.emerge = (fx, cy1 - 8)
        self.size_hint = (720, 540)
        self.c_h0 = 80

    def _tower(self, c, pal, rnd):
        """A crag with a ruined stone tower on it: broken battlements, arched windows (one lit), a stair switch-backing
        down the rock to a dark archway at the foot."""
        s = self.side
        ls = self.ls
        lx, by = self.lx, self.base_y
        topy = 540
        # the crag
        crag = [(lx - 320, by + 80)]
        for i in range(1, 13):
            k = i / 12
            crag.append((lx - 320 + (210) * k + rnd.uniform(-12, 12), by + 80 - (by + 80 - topy - 110) * (k ** 0.75) + rnd.uniform(-14, 14)))
        crag += [(lx - 60, topy + 100), (lx + 40, topy + 104), (lx + 116, topy + 108)]
        for i in range(1, 13):
            k = i / 12
            crag.append((lx + 116 + 214 * k + rnd.uniform(-12, 12), topy + 108 + (by + 80 - topy - 108) * (k ** 1.15) + rnd.uniform(-14, 14)))
        c.P(crag, pal["rock_m"] + (255,))
        def planes(t):
            for xs in range(int(lx - 340), int(lx + 340), 46):           # big lit / shadow blocks
                lit_ = (xs + 23 - lx) * ls < 0
                t.P([(xs, topy + 90), (xs + 46, topy + 90), (xs + 46 + rnd.uniform(-10, 10), by + 100), (xs + rnd.uniform(-10, 10), by + 100)],
                    mix(pal["rock_l"] if lit_ else pal["rock_d"], pal["rock_m"], rnd.uniform(0.1, 0.55)) + (255,))
            for _ in range(180):
                x = rnd.uniform(lx - 330, lx + 330)
                y = rnd.uniform(topy + 90, by + 20)
                w_, h_ = rnd.uniform(14, 56), rnd.uniform(22, 100)
                t.P([(x, y), (x + w_, y + h_ * 0.3), (x + w_ * rnd.uniform(-0.4, 0.5), y + h_)], (255, 255, 255, 30) if (x - lx) * ls < 0 else (0, 0, 16, rnd.randint(60, 120)))
            for _ in range(60):
                x0, y0 = rnd.uniform(lx - 330, lx + 330), rnd.uniform(topy + 100, by - 20)
                p2 = [(x0, y0)]
                for j in range(6):
                    p2.append((p2[-1][0] + rnd.uniform(-8, 8), p2[-1][1] + rnd.uniform(16, 42)))
                t.L(p2, (4, 6, 18, rnd.randint(90, 160)), rnd.uniform(1, 2.4))
        c.masked(crag, planes)
        # the tower
        tx0, tx1 = lx - 92, lx + 92
        ty0, ty1 = topy - 280, topy + 112
        lit_l = mix(pal["rock_l"], pal["haze"], 0.35) + (255,)
        shd = mix(pal["rock_d"], pal["rock_m"], 0.35) + (255,)
        mid = mix(pal["rock_m"], pal["haze"], 0.15) + (255,)
        # broken crown: merlons on most of the top, a missing bite, a slumped section
        crown = [(tx0, ty0 + 36)]
        x = tx0
        i = 0
        while x < tx1:
            w_ = 20
            drop = 0 if i % 2 == 0 else 22
            if 0.55 < (x - tx0) / (tx1 - tx0) < 0.78:
                drop += 48
            crown += [(x, ty0 + drop), (x + w_, ty0 + drop)]
            x += w_
            i += 1
        crown += [(tx1, ty0 + 40), (tx1, ty1), (tx0, ty1)]
        c.P(crown, mid)
        half = [(tx0, ty0 + 36), (lx, ty0 + 36), (lx, ty1), (tx0, ty1)] if ls < 0 else [(lx, ty0 + 36), (tx1, ty0 + 36), (tx1, ty1), (lx, ty1)]
        other = [(lx, ty0 + 36), (tx1, ty0 + 36), (tx1, ty1), (lx, ty1)] if ls < 0 else [(tx0, ty0 + 36), (lx, ty0 + 36), (lx, ty1), (tx0, ty1)]
        def bricks(t):
            t.P(half, lit_l)
            t.P(other, shd)
            for y in range(int(ty0), int(ty1), 16):
                t.L([(tx0, y), (tx1, y)], (0, 0, 12, 80), 0.9)
                for xx in range(int(tx0) + (8 if (y // 16) % 2 else 0), int(tx1), 26):
                    t.L([(xx, y), (xx, y + 16)], (0, 0, 12, 60), 0.8)
            for _ in range(70):                                      # chipped stones, lighter and darker
                x_, y_ = rnd.uniform(tx0, tx1), rnd.uniform(ty0 + 30, ty1)
                t.P([(x_, y_), (x_ + rnd.uniform(6, 14), y_), (x_ + rnd.uniform(6, 14), y_ + rnd.uniform(5, 10)), (x_, y_ + 8)], (255, 255, 255, 20) if rnd.random() < 0.5 else (0, 0, 12, 40))
            for _ in range(16):                                      # ivy dripping from the top
                x_ = rnd.uniform(tx0, tx1)
                t.L([(x_, ty0 + 40), (x_ + rnd.uniform(-5, 5), ty0 + rnd.uniform(80, 210))], mix(pal["tree"], (20, 50, 30), 0.4) + (190,), rnd.uniform(1.4, 3.2))
        c.masked(crown, bricks)
        # windows: arched; one is lit warm with a halo
        wins = ((lx - 26, ty0 + 90, False), (lx + 26, ty0 + 150, True), (lx - 24, ty0 + 220, False), (lx + 22, ty0 + 290, False))
        for (wx, wy, lamp) in wins:
            c.P([(wx - 11, wy + 44), (wx - 11, wy + 14), (wx - 7, wy + 4), (wx, wy), (wx + 7, wy + 4), (wx + 11, wy + 14), (wx + 11, wy + 44)], (255, 186, 84, 255) if lamp else (2, 3, 8, 255))
            c.L([(wx - 14, wy + 45), (wx + 14, wy + 45)], mix(pal["haze"], pal["rock_l"], 0.5) + (255,), 2.2)       # sill
            if lamp:
                for r_, a in ((70, 14), (46, 22), (28, 34)):
                    c.E((wx - r_, wy + 22 - r_, wx + r_, wy + 22 + r_), (255, 170, 70, a))
                c.L([(wx, wy + 8), (wx, wy + 44)], (110, 60, 20, 255), 1.6)
                c.L([(wx - 11, wy + 26), (wx + 11, wy + 26)], (110, 60, 20, 255), 1.6)
                self.lamp = (wx, wy + 22)
        # a ruined wing and a broken second turret
        wing = [(tx1, ty1 - 170), (tx1 + 40, ty1 - 150), (tx1 + 64, ty1 - 100), (tx1 + 92, ty1 - 92), (tx1 + 100, ty1), (tx1, ty1)]
        if ls < 0:
            c.P(wing, shd)
        else:
            c.P(wing, mid)
        c.P([(tx0 - 84, ty1 - 110), (tx0 - 60, ty1 - 124), (tx0 - 44, ty1 - 108), (tx0 - 30, ty1 - 120), (tx0 - 24, ty1 - 60), (tx0, ty1 - 40), (tx0, ty1), (tx0 - 84, ty1)], lit_l if ls < 0 else mid)
        for xx in (tx1 + 14, tx1 + 52, tx1 + 90):                       # arches in the wing
            c.P([(xx - 10, ty1 - 6), (xx - 10, ty1 - 50), (xx, ty1 - 62), (xx + 10, ty1 - 50), (xx + 10, ty1 - 6)], (2, 3, 8, 255))
        # the stair: diagonal switchbacks cut into the rock face, with a landing at each turn
        sx0, sx1 = lx - 96, lx + 96
        n = 9
        y0_, y1_ = topy + 122, by - 14
        pts = [(lx + s * 8, y0_)]
        for j in range(1, n + 1):
            pts.append((sx1 if j % 2 == 1 else sx0, y0_ + (y1_ - y0_) * j / n))
        pts.append((lx + s * 6, by - 10))
        c.L([(x_, y_ + 5) for x_, y_ in pts], (0, 0, 10, 190), 5.5)
        c.L(pts, mix(pal["haze"], pal["rock_l"], 0.45) + (240,), 4.2)
        for k in range(len(pts) - 1):                                      # step ticks and a landing at each turn
            (xa, ya), (xb, yb_) = pts[k], pts[k + 1]
            for u in (0.12, 0.24, 0.36, 0.48, 0.6, 0.72, 0.84):
                px, py = xa + (xb - xa) * u, ya + (yb_ - ya) * u
                c.L([(px, py - 3), (px, py + 3)], (0, 0, 10, 120), 0.9)
            c.E((xb - 9, yb_ - 4, xb + 9, yb_ + 4), mix(pal["haze"], pal["rock_l"], 0.5) + (255,))
        # the dark archway at the foot
        ax, ay = lx + s * 6, by + 6
        c.P([(ax - 56, ay + 44), (ax - 50, ay - 40), (ax - 30, ay - 66), (ax, ay - 78), (ax + 30, ay - 66), (ax + 50, ay - 40), (ax + 56, ay + 44)], pal["rock_d"] + (255,))
        c.P([(ax - 36, ay + 44), (ax - 34, ay - 34), (ax, ay - 54), (ax + 34, ay - 34), (ax + 36, ay + 44)], (2, 2, 8, 255))
        c.E((ax - 12, ay - 16, ax + 12, ay + 12), (70, 8, 10, 100))
        # gnarled dead trees and pines on the crag
        for _ in range(34):
            x = rnd.uniform(lx - 310, lx + 310)
            y = rnd.uniform(topy + 190, by + 40)
            if abs(x - lx) < 125 and y < by - 40:
                continue
            _v_pine(c, x, y, rnd.uniform(18, 48), mix(pal["tree"], pal["haze"], 0.12) + (255,), pal["tree_l"] + (255,), rnd, rnd.uniform(-1, 1))
        self.emerge = (ax, ay + 34)
        self.size_hint = (500, 800)
        self.c_h0 = 74

    # ---------------------------------------------------------------- the land in front: hills, the river, the path
    def _land(self, c, pal, rnd):
        """Rolling slopes down to the viewer, with a river to cross, the path winding from the landmark, grass, stones."""
        s = self.side
        ex, ey = self.emerge
        # the ground: a few overlapping slopes, each darker and nearer
        for i, (yb, amp, k) in enumerate(((1060, 70, 0.30), (1140, 90, 0.16), (1260, 110, 0.06))):
            col = mix(pal["grass"], pal["haze"], k)
            pts = _v_ridge(rnd, yb, amp, step=9, rough=0.3, peaks=2)
            c.P(pts + [(W + 20, H + 20), (-20, H + 20)], col + (255,))
            for _ in range(60 + 40 * i):                                          # grass strokes and little bushes catching light
                x, y = rnd.uniform(0, W), rnd.uniform(yb, yb + 260)
                c.L([(x, y), (x + rnd.uniform(-5, 5), y - rnd.uniform(5, 16))], mix(col, pal["haze"], 0.3) + (130,), 1.1)
            _v_forest(c, rnd, pal, -20, W + 20, yb - 70, yb + 50, 150 + 60 * i, 18 + 10 * i, 42 + 20 * i, fog=0.35 - 0.1 * i,
                      zone=lambda x, y: abs(x - (540 + s * (1 - i * 0.2) * 150 * (1 - (y - 1000) / 600.0))) > 110)
        # the path first (the river is placed so that the path fords it): a pale thread from the landmark's door to the viewer
        path = []
        xt = 540 - s * 40
        for j in range(0, 61):
            k = j / 60
            y = ey + (1900 - ey) * k ** 1.35
            g = k - 0.62 * math.sin(2 * math.pi * k) / (2 * math.pi)       # an S: it hurries sideways in the middle and eases at both ends, never turning back
            path.append((ex + (xt - ex) * g, y))
        self.path = path
        # the river crosses the whole land, a winding stream that the creature has to wade across at the ford
        ph = rnd.uniform(0, 6.28)
        ry = lambda x: 1236 + 30 * math.sin(x / 190.0 + ph) + 0.03 * (x - 540) * s
        rw = lambda x: 40 + 8 * math.sin(x / 120.0 + ph * 2)
        xs_ = [-40 + i * 12 for i in range(0, 95)]
        up = [(x, ry(x) - rw(x)) for x in xs_]
        dn = [(x, ry(x) + rw(x) * 1.15) for x in xs_]
        self.river_pts = [(x, ry(x)) for x in xs_]
        self.river_w = rw
        self.river_y = ry
        c.P([(x, y - 6) for x, y in up] + [(x, y + 6) for x, y in dn[::-1]], dark(pal["water"], 0.55) + (255,))          # banks
        c.P(up + dn[::-1], mix(pal["water"], pal["s1"], 0.55) + (255,))
        c.P([(x, y + (y2 - y) * 0.45) for (x, y), (_, y2) in zip(up, dn)] + dn[::-1], mix(pal["water"], pal["s1"], 0.30) + (255,))   # deeper near bank
        for _ in range(520):                                                     # ripples and sparkles
            x = rnd.uniform(0, W)
            yy = ry(x) + rnd.uniform(-rw(x), rw(x) * 1.1) * 0.9
            ln = rnd.uniform(8, 34)
            c.L([(x, yy), (x + ln, yy + rnd.uniform(-1, 1))], mix(pal["haze"], (255, 255, 255), 0.5) + (rnd.randint(40, 130),), rnd.uniform(0.8, 1.9))
        # reeds along the banks
        for _ in range(180):
            x = rnd.uniform(0, W)
            yy = (ry(x) - rw(x) - 4) if rnd.random() < 0.5 else (ry(x) + rw(x) * 1.15 + 4)
            h = rnd.uniform(10, 30)
            c.L([(x, yy), (x + rnd.uniform(-5, 5), yy - h)], dark(pal["grass"], 0.55) + (230,), 1.3)
        # a short stream from the landmark's foot down to the river
        sp = []
        for j in range(0, 21):
            k = j / 20
            sp.append((ex + s * 46 * min(1, k * 4) + math.sin(k * 6) * 12, ey + 8 + (ry(ex) - ey - 10) * k))
        for j in range(len(sp) - 1):
            wd = 6 + 12 * (j / 20)
            c.P([(sp[j][0] - wd, sp[j][1]), (sp[j][0] + wd, sp[j][1]), (sp[j + 1][0] + wd, sp[j + 1][1]), (sp[j + 1][0] - wd, sp[j + 1][1])], mix(pal["water"], pal["s1"], 0.5) + (235,))
        # the path over everything, then the ford: stepping stones and a lighter shallows
        for j in range(len(path) - 1):
            k = j / 60
            wd = 3 + 150 * k ** 1.7
            (x0, y0), (x1, y1) = path[j], path[j + 1]
            c.P([(x0 - wd, y0), (x0 + wd, y0), (x1 + wd * 1.03, y1), (x1 - wd * 1.03, y1)], mix(pal["grass"], (150, 140, 120), 0.5 - 0.2 * k) + (210,))
            if j % 2 == 0:
                c.L([(x0 - wd * 0.9, y0), (x1 - wd * 0.9, y1)], (0, 0, 0, 40), 1 + 3 * k)
        fj = min(range(len(path)), key=lambda j: abs(path[j][1] - ry(path[j][0])))
        fxp, fyp = path[fj]
        self.ford = (fxp, ry(fxp) + rw(fxp) * 0.07)
        self.ford_k = fj / 60.0
        wd = 3 + 150 * (fj / 60.0) ** 1.7
        c.E((fxp - wd * 0.9, ry(fxp) - rw(fxp) * 0.6, fxp + wd * 0.9, ry(fxp) + rw(fxp) * 0.8), mix(pal["water"], pal["haze"], 0.45) + (200,))
        for _ in range(16):
            sx_ = fxp + rnd.uniform(-wd * 0.8, wd * 0.8)
            sy_ = ry(fxp) + rnd.uniform(-rw(fxp) * 0.4, rw(fxp) * 0.6)
            sz = rnd.uniform(5, 11)
            c.E((sx_ - sz, sy_ - sz * 0.5, sx_ + sz, sy_ + sz * 0.5), mix(pal["rock_m"], pal["rock_l"], rnd.random()) + (255,))
        # stones and tufts on the land, bigger near the viewer
        for _ in range(240):
            k = rnd.random() ** 0.8
            y = 1040 + 860 * k
            x = rnd.uniform(0, W)
            sz = 2 + 30 * k * k
            cx_ = min(path, key=lambda p: abs(p[1] - y))[0]
            if abs(x - cx_) < 40 + 140 * k:
                continue
            c.P([(x - sz, y), (x - sz * 0.4, y - sz * 0.7), (x + sz * 0.6, y - sz * 0.6), (x + sz, y)], mix(pal["rock_d"], pal["rock_m"], rnd.random()) + (255,))
            c.P([(x - sz * 0.4, y - sz * 0.7), (x + sz * 0.6, y - sz * 0.6), (x + sz * 0.2, y - sz * 0.3)], pal["rock_l"] + (120,))
        # light haze lying in the valley (soft bands)
        hz = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        hd = ImageDraw.Draw(hz)
        for yb, a in ((1000, 80), (1070, 54), (1160, 34)):
            hd.rectangle((0, yb, W, yb + 34), fill=pal["haze"] + (a,))
        hz = hz.filter(ImageFilter.GaussianBlur(26)).resize((W * VS, H * VS), Image.BICUBIC)
        base = c.im.convert("RGBA")
        base.alpha_composite(hz)
        c.im = base.convert("RGB")
        c.d = ImageDraw.Draw(c.im, "RGBA")

    def path_at(self, k):
        """Where the path is at fraction k (0 at the landmark's door, 1 at your feet): (x, y)."""
        k = min(1.0, max(0.0, k)) * (len(self.path) - 1)
        i = min(int(k), len(self.path) - 2)
        f = k - i
        (x0, y0), (x1, y1) = self.path[i], self.path[i + 1]
        return x0 + (x1 - x0) * f, y0 + (y1 - y0) * f

    def creature_h(self, y):
        """How tall (in drawing units) the creature is when its feet are at height y: things shrink toward the horizon."""
        return self.c_h0 * (y - self.horizon) / max(1.0, (self.emerge[1] - self.horizon))

    def _heroes(self, pal, rnd):
        """Big pines standing beside the path, each as its own sprite so it can sway and shed leaves when the creature slips past.
        They are tall enough to hide it: the creature goes behind them, then steps out again."""
        kf = self.ford_k
        ks = [0.40 * kf, 0.82 * kf, kf + 0.30 * (1 - kf), kf + 0.62 * (1 - kf)]
        out = []
        for i, kk in enumerate(ks):
            x, y = self.path_at(kk)
            h = max(100.0, self.creature_h(y) * rnd.uniform(0.95, 1.2) + 30)
            tw, th = h * 0.92, h * 1.18
            c = _VC.__new__(_VC)
            c.im = Image.new("RGBA", (int(tw * VS), int(th * VS)), (0, 0, 0, 0))
            c.d = ImageDraw.Draw(c.im, "RGBA")
            c.lit_side = self.ls
            _v_pine(c, tw / 2, th - h * 0.12, h, mix(pal["tree"], pal["haze"], 0.08) + (255,), pal["tree_l"] + (255,), rnd, rnd.uniform(-0.6, 0.6))
            ox = (1 if i % 2 else -1) * rnd.uniform(0.30, 0.42) * h * (1.0 + 0.0 * i)
            out.append(dict(k=kk, x=x + ox, y=y + 26 + 0.05 * h, h=h, spr=c.im, tw=tw, th=th, foot=th - h * 0.12, seed=rnd.random() * 6.28))
        return out

    # ---------------------------------------------------------------- plates in front of the creature
    def _front_plate(self, pal, rnd):
        """Trees and rocks standing between the viewer and the creature's path: it is hidden behind them while it stalks."""
        c = _VC("RGBA")
        c.lit_side = self.ls
        s = self.side
        pth = self.path
        for (yy, n, hh0, hh1, dx) in ((1130, 10, 70, 120, 130), (1250, 8, 110, 170, 170), (1390, 6, 170, 250, 230)):
            for _ in range(n):
                x = rnd.uniform(0, W)
                cx_ = min(pth, key=lambda p: abs(p[1] - yy))[0]
                if abs(x - cx_) < dx * 0.6:
                    continue
                y = yy + rnd.uniform(-18, 18)
                h = rnd.uniform(hh0, hh1)
                _v_pine(c, x, y, h, mix(pal["tree"], pal["haze"], 0.1) + (255,), pal["tree_l"] + (255,), rnd, rnd.uniform(-1, 1))
        return c.im

    def _near_plate(self, pal, rnd):
        """Your own foreground: dark slope, long grass, a rock ledge, huge pines framing the left and right."""
        c = _VC("RGBA")
        c.lit_side = self.ls
        col = dark(pal["grass"], 0.4) + (255,)
        pts = _v_ridge(rnd, 1860, 70, step=10, rough=0.4, peaks=2)
        c.P(pts + [(W + 20, H + 20), (-20, H + 20)], col)
        for _ in range(520):                                                       # long grass
            x = rnd.uniform(-10, W + 10)
            y = rnd.uniform(1800, 1930)
            h = rnd.uniform(26, 120)
            bend = rnd.uniform(-22, 22)
            c.P([(x - 3.2, y), (x + bend, y - h), (x + 3.2, y)], mix(dark(pal["grass"], 0.35), pal["tree_l"], rnd.random() * 0.5) + (255,))
        for sgn, x0 in ((-1, -40), (1, W + 40)):                                     # framing pines
            for _ in range(4):
                x = x0 + sgn * rnd.uniform(-20, 70)
                _v_pine(c, x - sgn * rnd.uniform(0, 70), rnd.uniform(1500, 1900), rnd.uniform(520, 760), dark(pal["tree"], 0.5) + (255,), dark(pal["tree_l"], 0.6) + (255,), rnd, sgn * 0.5)
        return c.im

# ====================================================================================================== VISTA (animation)
# First-person camera, far away: one landmark in the distance (a waterfall with a cave behind it, a mountain with a cave, a ruined tower
# with a dark door). The camera zooms into it, the creature comes out, then it stalks down the land towards you -- it fords the river,
# slips behind trees, birds burst up, rocks fall, the water and mist keep moving -- and ambushes you.


def _vz_crop(plate, x0, y0, cw, ch):
    """A window (1080-based numbers) of a 3x plate, as a picture of the frame size (hi-res when the scene is drawn big)."""
    box = (x0 * VS, y0 * VS, (x0 + cw) * VS, (y0 + ch) * VS)
    big = _big()
    res = _raw(plate.resize, big, Image.LANCZOS if cw * VS > big[0] else Image.BICUBIC, box)
    return _hr_reg(res) if RSF[0] != 1.0 else res


def _vz_height(sp):
    """The creature's drawn height (in drawing units) at scale 1, measured."""
    if "_vzh" in sp:
        return sp["_vzh"]
    old = RSF[0]
    RSF[0] = 1.0
    nb = len(_BOXES)
    try:
        lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(lay, "RGBA")
        draw_creature(ld, sp, 540, 1500, 0.3, 1, 1.0, {"t": 0.5, "moving": False, "noemerge": True, "noshadow": True})
        bb = lay.getchannel("A").point(lambda v: 255 if v > 120 else 0).getbbox()
    except Exception:
        bb = None
    finally:
        RSF[0] = old
        del _BOXES[nb:]
    hb = float(bb[3] - bb[1]) if bb else 500.0
    sp["_vzh"] = max(hb, 120.0)
    return sp["_vzh"]


def _vz_mist(seed=5):
    rnd = random.Random(seed)
    lay = Image.new("RGBA", (W * 2, 420), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay, "RGBA")
    for _ in range(46):
        x, y = rnd.uniform(0, W * 2), rnd.uniform(120, 300)
        rx, ry = rnd.uniform(120, 330), rnd.uniform(24, 60)
        d.ellipse((x - rx, y - ry, x + rx, y + ry), fill=(220, 228, 240, rnd.randint(18, 40)))
    lay = lay.filter(ImageFilter.GaussianBlur(34))
    # make it tile: the right half repeats the left
    left = lay.crop((0, 0, W, 420))
    lay.paste(left, (W, 0))
    return lay


def _vz_bird(d, x, y, sz, flap, col):
    f = math.sin(flap)
    d.line([(x - sz, y - sz * 0.5 * f), (x, y), (x + sz, y - sz * 0.5 * f)], fill=col, width=max(2, int(sz * 0.28)))


def _vz_cam(V, u, T, zL):
    """Camera window (x0, y0, zoom) for scene time u: wide, zoom to the landmark, hold, then ease out as the creature comes."""
    ex, ey = V.emerge
    z_in = _smooth(u / 0.26)
    z = 1.0 + (zL - 1.0) * z_in
    w = _smooth((u - 0.30) / 0.42)                       # after the creature appears the camera relaxes out
    z = z + (1.0 - z) * w
    return z


def vista_scene(out, dur, world, sp, kind=None, seed=None, cwd=None, finale="face", kinds=None):
    """See the block comment above. Returns the same timing info as the other attack scenes."""
    seed = seed if seed is not None else (zlib.crc32(str(sp.get("name", "")).encode()) % 997) + 1
    if kind is None and kinds:
        kind = kinds[seed % len(kinds)]
    V = Vista(world, kind=kind, seed=seed)
    s = V.side
    ex, ey = V.emerge
    air = sp.get("kind") in AIR_KINDS
    hb = _vz_height(sp)
    G = chim_ground(sp) if sp.get("kind") == "chimera" else WALK_PX
    T = 0.92
    if PAUSE_REMAIN[0]:
        T = min(0.95, max(0.78, (dur - PAUSE_REMAIN[0]) / dur))
    u_go, u_arr, u_lunge = 0.34, T - 0.09, T - 0.09
    sp3 = dict(sp)
    sp3["bloody"] = True
    info = {"hits": [dur * T], "impact_at": dur * T, "stalk_at": dur * u_go, "maul_at": dur * (T + (1 - T) * 0.07), "maul_dur": dur * (1 - T) * 0.57}
    K_END = 0.93
    face = -s                                            # it comes down from the landmark towards the middle: that way is its facing

    def kof(u):
        v = min(1.0, max(0.0, (u - u_go) / max(u_arr - u_go, 0.05)))
        return K_END * (v ** 1.12)

    def sc_at(k):
        x, y = V.path_at(k)
        return x, y, max(0.05, 1.3 * V.creature_h(y) / hb)

    # distance walked (in creature units) as a function of u, so the feet keep to the ground
    nS = 240
    PT = [0.0]
    pk = kof(u_go)
    for i in range(1, nS + 1):
        uu = u_go + (u_arr - u_go) * i / nS
        kk = kof(uu)
        (xa, ya, sa), (xb, yb, sb) = sc_at(pk), sc_at(kk)
        PT.append(PT[-1] + math.hypot(xb - xa, yb - ya) / (0.5 * (sa + sb)) / G)
        pk = kk

    def P_at(u):
        q = min(1.0, max(0.0, (u - u_go) / max(u_arr - u_go, 0.05))) * nS
        i = min(int(q), nS - 1)
        return PT[i] + (PT[i + 1] - PT[i]) * (q - i)

    fo = {"falls": 150, "peak": 170, "tower": 330}.get(V.kind, 160)
    zL = {"falls": 2.4, "peak": 2.2, "tower": 1.9}.get(V.kind, 2.2)
    mist = _vz_mist(seed)
    rnd = random.Random(seed + 5)
    kf = V.ford_k
    fx, fy = V.ford
    u_ford = u_go + (u_arr - u_go) * ((kf / K_END) ** (1 / 1.12)) if kf < K_END else 0.6
    # birds: a few drift far off from the start; a flock bursts out of the trees as the creature passes the ford
    far_birds = [(rnd.uniform(0.1, 0.9) * W, rnd.uniform(560, 760), rnd.uniform(0.6, 1.2), rnd.uniform(0, 6)) for _ in range(5)]
    flock = []
    bx, by_ = V.path_at(min(0.92, kf + 0.18 * (1 - kf)))
    for i in range(9):
        flock.append((bx + rnd.uniform(-70, 70), by_ - rnd.uniform(20, 120), rnd.uniform(-1, 1), rnd.uniform(0.6, 1.3), rnd.uniform(0, 6)))
    u_flock = u_go + (u_arr - u_go) * (((kf + 0.12 * (1 - kf)) / K_END) ** (1 / 1.12)) if kf + 0.12 * (1 - kf) < K_END else 0.62
    rocks = [(rnd.uniform(-60, 60), rnd.uniform(-170, -60), rnd.uniform(3, 8), rnd.uniform(0, 0.25)) for _ in range(9)]
    world.push, world.shake = 0.0, 0.0
    world.no_cinema = True
    st_run = {"moving": True, "ground": G}

    def to_screen(px, py, c):
        return (px - c[0]) * c[2], (py - c[1]) * c[2]

    def paint(im0, d0, t, u, cam):
        R = RSF[0]
        # ---------------------------------------------------------------- camera
        k = kof(u) if u >= u_go else 0.0
        cxp, cyp, scl = sc_at(k)
        z = _vz_cam(V, u, T, zL)
        w = _smooth((u - 0.30) / 0.42)
        tgt_x = ex + (cxp - ex) * w * 0.9 + (0.0 if w > 0 else 0.0)
        mid_y = ey - 0.40 * V.creature_h(ey) + (cyp - 0.5 * V.creature_h(cyp) - (ey - 0.40 * V.creature_h(ey))) * w
        focus_x = ex * (1 - w) + 540 * w * 0.35 + cxp * w * 0.65 if w > 0 else ex
        focus_y = (ey - fo) * (1 - w) + mid_y * w if w > 0 else ey - fo
        if u < 0.26:
            q = _smooth(u / 0.26)
            focus_x, focus_y = 540 + (ex - 540) * q, 960 + (ey - fo - 960) * q
        cw, ch = W / z, H / z
        shk = 0.0 if u < 0.6 else 2.4 * (u - 0.6) / 0.4
        bx_ = 4.0 * math.sin(t * 1.3) + shk * math.sin(t * 31)
        by__ = 3.0 * math.sin(t * 1.9 + 1.0) + shk * math.cos(t * 27)
        x0 = _clip(focus_x - cw / 2 + bx_ / z, 0, W - cw)
        y0 = _clip(focus_y - ch / 2 + by__ / z, 0, H - ch)
        c = (x0, y0, z)
        im = _vz_crop(V.back, x0, y0, cw, ch)
        d = ImageDraw.Draw(im, "RGBA")
        # ---------------------------------------------------------------- far things that move
        for (bx0, byy, sp_, ph) in far_birds:
            bxx = (bx0 + t * 22 * sp_ * 1.0) % (W + 120) - 60
            sx_, sy_ = to_screen(bxx, byy + 12 * math.sin(t * 0.7 + ph), c)
            _vz_bird(d, sx_, sy_, 7 * z, t * 9 + ph, (14, 16, 26, 220))
        if V.kind == "falls":
            f = V.fall
            top_, bot_ = f["top"], f["bot"]
            cur = [to_screen(f["x"] - 34, top_, c), to_screen(f["x"] + 34, top_, c), to_screen(f["x"] + 50, bot_, c), to_screen(f["x"] - 50, bot_, c)]
            d.polygon(cur, fill=(150, 180, 205, 120))                # the sheet of water itself, a little see-through
            n = 34
            for i in range(n):                                   # streaks running down, brighter and wider towards the bottom
                xr = (i + 0.5) / n * 2 - 1
                ph = (i * 0.6180339) % 1.0
                for seg in range(4):
                    yy0 = top_ + ((t * (0.7 + 0.5 * ph) + ph + seg / 4.0) % 1.0) * (bot_ - top_)
                    yy1 = min(bot_, yy0 + (bot_ - top_) * (0.05 + 0.08 * ph))
                    hw = lambda y: 34 + 16 * (y - top_) / max(1.0, bot_ - top_)
                    p0, p1 = to_screen(f["x"] + xr * hw(yy0), yy0, c), to_screen(f["x"] + xr * hw(yy1), yy1, c)
                    d.line([p0, p1], fill=(226, 238, 248, 70 + int(60 * ph)), width=max(1, int(1.6 * z)))
            for a_, wd in ((60, 1.0), (40, 0.7)):
                x1, y1 = to_screen(f["x"] - 54, bot_ - 14, c)
                x2, y2 = to_screen(f["x"] + 54, bot_ + 14, c)
                d.ellipse((x1, y1, x2, y2), fill=(235, 242, 250, a_))
            for i in range(10):                                  # spray at the foot
                ang = (t * 1.3 + i * 0.63) % 1.0
                sxx, syy = to_screen(f["x"] + (i - 4.5) * 9, bot_ - 18 * ang - 6, c)
                r_ = (3 + 8 * ang) * z
                d.ellipse((sxx - r_, syy - r_, sxx + r_, syy + r_), fill=(240, 246, 252, int(80 * (1 - ang))))
        if V.kind == "tower" and hasattr(V, "lamp"):
            lx_, ly_ = to_screen(V.lamp[0], V.lamp[1], c)
            fl = 0.72 + 0.28 * math.sin(t * 13) * math.sin(t * 5.3 + 1.0)
            for r_, a_ in ((80, 20), (48, 34), (26, 55)):
                rr = r_ * z * fl
                d.ellipse((lx_ - rr, ly_ - rr, lx_ + rr, ly_ + rr), fill=(255, 170, 70, int(a_ * fl)))
        # eyes in the dark before it comes out
        if 0.17 < u < u_go + 0.03:
            aeye = _smooth((u - 0.17) / 0.08) * (0.6 + 0.4 * math.sin(t * 5)) * (1 - _smooth((u - u_go) / 0.03))
            exx, eyy = to_screen(ex, ey - 0.45 * V.creature_h(ey), c)
            for sg in (-1, 1):
                rr = 5.5 * z
                d.ellipse((exx + sg * 7 * z - rr, eyy - rr, exx + sg * 7 * z + rr, eyy + rr), fill=(255, 60, 40, int(240 * aeye)))
                rr *= 2.4
                d.ellipse((exx + sg * 7 * z - rr, eyy - rr, exx + sg * 7 * z + rr, eyy + rr), fill=(255, 40, 30, int(50 * aeye)))
        # rocks and dust shaken loose as it comes out
        ua = u - (u_go - 0.05)
        if 0 < ua < 0.22:
            for (rx0, ry0, rs, rd) in rocks:
                a_ = ua - rd * 0.2
                if a_ <= 0:
                    continue
                tt_ = a_ * dur
                rx_ = ex + rx0 + 6 * tt_
                ry_ = ey + ry0 + 220 * tt_ * tt_
                if ry_ > ey + 40:
                    continue
                sx_, sy_ = to_screen(rx_, ry_, c)
                r_ = rs * z
                d.polygon([(sx_ - r_, sy_), (sx_, sy_ - r_ * 0.9), (sx_ + r_, sy_ + r_ * 0.2)], fill=(52, 50, 56, 255))
            for j in range(5):
                dq = min(1.0, ua / 0.2)
                sx_, sy_ = to_screen(ex + (j - 2) * 30, ey + 10 - dq * 40, c)
                r_ = (16 + 40 * dq) * z
                d.ellipse((sx_ - r_, sy_ - r_ * 0.6, sx_ + r_, sy_ + r_ * 0.6), fill=(150, 142, 130, int(70 * (1 - dq))))
        # ---------------------------------------------------------------- creature and the things it passes behind
        show = u >= u_go - 0.005
        heroes = V.heroes
        before = [h for h in heroes if not show or h["y"] <= cyp]
        after = [h for h in heroes if show and h["y"] > cyp]

        def put_hero(h):
            near_k = math.exp(-((h["k"] - k) / 0.06) ** 2) if show else 0.0
            ang = 0.012 * math.sin(t * 1.7 + h["seed"]) + 0.05 * near_k * math.sin(t * 9 + h["seed"])
            spr = h["spr"]
            tw, th = h["tw"], h["th"]
            lx_, ty_ = to_screen(h["x"] - tw / 2, h["y"] - h["foot"], c)
            sw_, sh_ = tw * z, th * z
            if lx_ > W or ty_ > H or lx_ + sw_ < 0 or ty_ + sh_ < 0:
                return
            pic = _raw(spr.resize, (max(2, int(round(sw_ * R))), max(2, int(round(sh_ * R)))), Image.BICUBIC)
            if abs(ang) > 1e-4:                                  # sway: turn it about its foot (done at the small size: much faster)
                pic = _raw(pic.rotate, math.degrees(ang), Image.BICUBIC, 0, (pic.width / 2, h["foot"] / th * pic.height))
            if R != 1.0:
                _hr_reg(pic)
            im.paste(pic, (int(round(lx_)), int(round(ty_))), pic)

        for h in before:
            put_hero(h)
        if show:
            q_l = _smooth((u - u_lunge) / max(T - u_lunge, 0.02)) if u > u_lunge else 0.0
            sx, sy = to_screen(cxp, cyp, c)
            sc_draw = scl * z
            if q_l > 0:
                sx = sx + (540 - sx) * q_l ** 1.5
                sy = sy + (1790 - sy) * q_l ** 1.5
                sc_draw = sc_draw * (1 + 1.25 * q_l ** 2)
            vis = _smooth((u - u_go) / 0.05)
            dyfl = -90 * scl * z if air else 0.0
            stt = dict(st_run, t=t, moving=(q_l < 0.6), roar=0.9 * q_l, lunge=q_l, crouch=0.0 if q_l == 0 else 0.3 * (1 - q_l))
            phase = P_at(min(u, u_arr)) * STEP * 0.5
            if vis < 0.98:
                lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                ld = ImageDraw.Draw(lay, "RGBA")
                draw_creature(ld, sp, sx, sy + dyfl, phase, face, sc_draw, dict(stt, noemerge=True, noshadow=True))
                al = lay.getchannel("A").point(lambda v, a=vis: int(v * a))
                im.paste(lay, (0, 0), al)
            else:
                draw_creature(d, sp, sx, sy + dyfl, phase, face, sc_draw, stt)
            # the ford: it wades through, the water answers
            ag = u - u_ford
            if -0.02 < ag < 0.20 and V.kind is not None:
                for j in range(4):
                    rr = (ag + 0.02 - j * 0.03) * dur * 90
                    if rr <= 0 or rr > 140:
                        continue
                    ax_, ay_ = to_screen(fx + (cxp - fx) * 0.0, fy, c)
                    ax_ += (j - 1.5) * 14 * z
                    d.ellipse((ax_ - rr * z, ay_ - rr * 0.28 * z, ax_ + rr * z, ay_ + rr * 0.28 * z), outline=(228, 238, 248, int(150 * (1 - rr / 140))), width=max(2, int(2.2 * z)))
                if ag < 0.09:
                    for j in range(14):
                        a_ = (ag * dur * 2.2 + j * 0.13) % 1.0
                        px_ = fx + (j - 7) * 6 + 28 * (a_ - 0.5) * (1 if j % 2 else -1)
                        py_ = fy - 60 * math.sin(math.pi * a_) * (0.6 + 0.4 * ((j * 7) % 5) / 5)
                        sxx, syy = to_screen(px_, py_, c)
                        r_ = 2.6 * z
                        d.ellipse((sxx - r_, syy - r_, sxx + r_, syy + r_), fill=(240, 246, 252, int(210 * (1 - a_))))
            if show and u > u_flock - 0.01:
                af = (u - u_flock) * dur
                for (x_, y_, vx, sz_, ph) in flock:
                    if af < 0:
                        continue
                    px_ = x_ + vx * 140 * af
                    py_ = y_ - 210 * af - 30 * af * af
                    sxx, syy = to_screen(px_, py_, c)
                    _vz_bird(d, sxx, syy, 8 * z * sz_, t * 16 + ph, (12, 12, 20, 235))
        for h in after:
            put_hero(h)
        # ---------------------------------------------------------------- front trees, mist, foreground, point of view
        fr = _vz_crop(V.front, x0, y0, cw, ch)
        im.paste(fr, (0, 0), fr)
        mo = int(t * 16) % W
        for yy, a in ((1030, 0.9), (1210, 0.7)):
            m = mist.crop((mo, 0, mo + W, 420))
            m = m.point(lambda v, a=a: int(v * a)) if a < 0.99 else m
            im.paste(m, (0, int((yy - 0.5 * 420 - y0) * z)), m)
        nr = _vz_crop(V.near, x0, y0, cw, ch)
        im.paste(nr, (0, 0), nr)
        # ---------------------------------------------------------------- the ambush
        if u < T:
            _vignette(im, 150 + int(70 * _smooth((u - 0.5) / 0.4)))
            if u > u_lunge:
                red_ = 0.30 * _smooth((u - u_lunge) / (T - u_lunge))
                im = red_wash(im, red_)
            return im
        kk = (u - T) / (1 - T)
        dd = ImageDraw.Draw(im, "RGBA")
        if finale != "maul":                             # no killing here: the face fills the lens, then the dark
            closeup(im, dd, sp, 0.0, 0.3, 2.3, 2.7, roar=1.0, shake=12 * max(0.0, 1 - kk), tt=t)
            _vignette(im, 200)
            return red_wash(im, 0.28)
        if kk < 0.07:
            closeup(im, dd, sp3, 0.0, 0.3, 2.3, 2.7, roar=1.0, shake=12, tt=t)
            return red_wash(im, 0.4)
        if kk < 0.64:
            _BOXES.append((100, 540, 980, 1240))
            return maul_frame(im, t, (kk - 0.07) / (0.64 - 0.07), sp, (44, 50, 82), "cloth", seed=5)
        k2 = 0.64 + (kk - 0.64) * 0.55
        dim(im, 150)
        draw_creature(dd, sp3, 540 + 16 * math.sin(t * 90) * max(0.0, 1 - k2 * 4), 1700, t * 0.8, face, 1.5, {"t": t, "moving": False, "roar": min(1, k2 * 3)})
        _vignette(im, 230)
        return red_wash(im, 0.5 * max(0.0, 1 - k2 * 1.2) + 0.12)

    write_video(out, dur, paint, world, cwd, cam_dir=0.0)
    return info

STORY_FUNCS["vista"] = vista_scene
