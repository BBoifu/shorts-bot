"""Nightreel reference-image library.

refs/<category>/<file> are real reference pictures; refs/manifest.json says what each one shows (category, subject, view, tags).
Nothing here pastes a reference into a scene.  Each picture is measured once (colour palette with weights, accent colours, brightness,
colour strength, texture detail, dark-gap share, top-to-bottom light) and the generators in toon.py read those measurements to build
their own original pictures.  Which references an episode uses is chosen from its story (begin_episode) and then stays fixed, so a
location or creature does not change between scenes.   Run:  python refs.py report   |   python refs.py build"""
import json, hashlib, pathlib, random, sys
import numpy as np
from PIL import Image, ImageFilter

HERE = pathlib.Path(__file__).resolve().parent
DIR = HERE / "refs"
MANIFEST = DIR / "manifest.json"
PROFILES = DIR / "profiles.json"

# category -> the views a complete collection should have (report() lists what is still missing)
WANTED = {
    "mountain": ["wide_range", "close_rock", "ridgeline", "haze"],
    "forest": ["aerial_wide", "close_vegetation", "single_tree", "riverbank", "lighting"],
    "river": ["aerial_river", "bank", "current", "lake"],
    "wetland": ["swamp_wide", "reeds", "mist"],
    "volcanic": ["volcano_wide", "lava", "ash_sky", "rock"],
    "dinosaur_anatomy": ["full_body", "head", "sail", "overhead", "skin"],
    "dinosaur_movement": ["walk", "run", "swim", "attack"],
    "monster_anatomy": ["full_body", "head", "limbs", "skin"],
    "water": ["surface", "wake", "underwater", "shadow_below"],
    "lighting": ["golden", "overcast", "storm", "night", "mist"],
}
MIN_SIDE = 600        # smaller pictures are too low-resolution to measure
MIN_SHARP = 15.0      # blurrier pictures (variance of the Laplacian) are refused
ACTIVE = {}           # category -> profile, set per episode by begin_episode


def _load(path):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except Exception:
        return None


def manifest():
    m = _load(MANIFEST)
    return m if isinstance(m, list) else []


def _ahash(im):
    g = np.asarray(im.convert("L").resize((16, 16)), np.float32)
    return "".join("1" if v else "0" for v in (g > g.mean()).ravel())


def measure(path):
    """The measurements of one picture, or {'error': why} if it is unusable."""
    im = Image.open(path).convert("RGB")
    w, h = im.size
    if min(w, h) < MIN_SIDE:
        return {"error": f"too small ({w}x{h})"}
    sm = im.copy(); sm.thumbnail((640, 640))
    a = np.asarray(sm, np.float32)
    g = a.mean(2)
    lap = (np.abs(4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:])).var()
    if lap < MIN_SHARP:
        return {"error": f"too blurry ({lap:.1f})"}
    q = sm.quantize(colors=14, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    pal = q.getpalette()[:14 * 3]
    cnt = np.bincount(np.asarray(q).ravel(), minlength=14)[:14]
    items = sorted(((int(c), tuple(int(v) for v in pal[3 * i:3 * i + 3])) for i, c in enumerate(cnt) if c), reverse=True)
    tot = float(sum(c for c, _ in items))
    palette = [{"rgb": list(rgb), "w": round(c / tot, 4)} for c, rgb in items]
    mx, mn = a.max(2), a.min(2)
    hsv = np.asarray(sm.convert("HSV"), np.float32); hh = hsv[..., 0] * 360 / 255; ss = hsv[..., 1] / 255; vv = hsv[..., 2] / 255
    classes = {"pink_purple": (((hh > 285) | (hh < 20)) & (ss > 0.16) & (vv > 0.25)), "orange_yellow": ((hh >= 20) & (hh < 60) & (ss > 0.4) & (vv > 0.4)),
               "cream_pale": ((vv > 0.68) & (ss < 0.14))}
    accents = {}
    for name, m in classes.items():
        if m.sum() > 0.002 * m.size:
            accents[name] = {"rgb": [int(v) for v in a[m].mean(0)], "share": round(float(m.mean()), 4)}
    sat = float(((mx - mn) / np.maximum(mx, 1)).mean())
    lum = (0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]) / 255.0
    gx = np.abs(np.diff(g, axis=1)).mean(); gy = np.abs(np.diff(g, axis=0)).mean()
    third = lum.shape[0] // 3
    return {"size": [w, h], "palette": palette, "lum_mean": round(float(lum.mean()), 4), "lum_std": round(float(lum.std()), 4),
            "sat": round(sat, 4), "accents": accents, "texture": round(float(gx + gy) / 2, 3), "dark_share": round(float((lum < 0.16).mean()), 4),
            "light_top_bottom": round(float(lum[:third].mean() - lum[-third:].mean()), 4), "sharp": round(float(lap), 1),
            "hash": _ahash(im), "file_sha": hashlib.sha1(pathlib.Path(path).read_bytes()).hexdigest()[:12]}


def build():
    """Measure every manifest entry (skipping watermarked, unusable or duplicate pictures) and write refs/profiles.json."""
    out, seen, notes = {}, [], []
    for e in manifest():
        pid = e.get("id")
        if e.get("watermark"):
            notes.append(f"{pid}: skipped (watermarked)"); continue
        f = DIR / e["category"] / e["file"]
        if not f.exists():
            notes.append(f"{pid}: file missing"); continue
        m = measure(f)
        if "error" in m:
            notes.append(f"{pid}: skipped ({m['error']})"); continue
        if any(sum(x != y for x, y in zip(m["hash"], s)) < 8 for s in seen):
            notes.append(f"{pid}: skipped (duplicate of another reference)"); continue
        seen.append(m["hash"])
        out[pid] = dict(e, **m)
    PROFILES.write_text(json.dumps(out, indent=1))
    return out, notes


def profiles():
    p = _load(PROFILES)
    if not isinstance(p, dict):
        try:
            p, _ = build()
        except Exception:
            p = {}
    return p


def collection(category, subject=None, view=None):
    return [p for p in profiles().values() if p["category"] == category and (subject is None or p.get("subject") == subject)
            and (view is None or p.get("view") == view)]


def report():
    prof = profiles()
    lines = []
    for cat, views in WANTED.items():
        have = {p.get("view") for p in prof.values() if p["category"] == cat}
        n = sum(1 for p in prof.values() if p["category"] == cat)
        miss = [v for v in views if v not in have]
        lines.append(f"{cat:18s} {n} reference(s)" + (f"   still wanted: {', '.join(miss)}" if miss else "   complete"))
    return "\n".join(lines)


# ---------------------------------------------------------------- choosing references from the story
TAG_RULES = (   # words in the episode's story/setting -> the categories that belong in it
    (("river", "stream", "rapids", "waterfall"), ("river", "water", "forest")),
    (("lake", "pond"), ("river", "water")),
    (("swamp", "wetland", "marsh", "bog"), ("wetland", "water", "forest")),
    (("forest", "jungle", "canopy", "woods", "rainforest"), ("forest",)),
    (("volcano", "lava", "eruption", "magma", "ash"), ("volcanic", "mountain")),
    (("mountain", "peak", "ridge", "range", "cliff"), ("mountain",)),
    (("spinosaurus",), ("dinosaur_anatomy", "dinosaur_movement", "water")),
    (("tyrannosaurus", "rex", "raptor", "triceratops", "dinosaur"), ("dinosaur_anatomy", "dinosaur_movement")),
    (("monster", "beast", "tentacle", "creature"), ("monster_anatomy",)),
)


def categories_for(text):
    t = (text or "").lower()
    cats = []
    for words, cs in TAG_RULES:
        if any(w in t for w in words):
            cats += [c for c in cs if c not in cats]
    if cats and "lighting" not in cats:
        cats.append("lighting")
    return cats


def begin_episode(seed, text="", extra=()):
    """Pick the references for this episode from its story.  Called once; the choice then stays fixed for every scene."""
    ACTIVE.clear()
    cats = categories_for(text) + [c for c in extra if c not in categories_for(text)]
    rnd = random.Random(f"refs|{seed}")
    for c in cats:
        pool = sorted(collection(c), key=lambda p: p["id"])
        if pool:
            ACTIVE[c] = rnd.choice(pool)
    return dict(ACTIVE)


def active(category):
    return ACTIVE.get(category)


def active_key():
    return "|".join(sorted(f"{c}:{p['id']}" for c, p in ACTIVE.items()))


def palette(category, accents=True):
    """The active reference's colours as [(rgb, weight)] (None if this episode has no reference for the category)."""
    p = ACTIVE.get(category)
    if not p:
        return None
    return [(tuple(c["rgb"]), c["w"]) for c in p["palette"]]


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        o, n = build(); print(len(o), "usable reference(s)"); print("\n".join(n))
    else:
        print(report())
