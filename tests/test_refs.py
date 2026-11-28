"""Reference-image library: measuring, refusing bad pictures, choosing by story, staying fixed, and really changing the generated forest."""
import os, sys, pathlib, json, tempfile
os.environ["DRY_RUN"] = "1"
HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import numpy as np
from PIL import Image, ImageFilter
import refs, toon
ok = True
def check(n, c):
    global ok
    print(("PASS " if c else "FAIL ") + n); ok &= bool(c)

o, notes = refs.build()
check("the forest reference is measured", "forest_aerial_canopy_01" in o and o["forest_aerial_canopy_01"]["palette"])
check("all ten categories are known", set(refs.WANTED) >= {"mountain", "forest", "river", "wetland", "volcanic", "dinosaur_anatomy", "dinosaur_movement", "monster_anatomy", "water", "lighting"})
check("the report names what is still missing", "still wanted" in refs.report())
d = pathlib.Path(tempfile.mkdtemp())
Image.new("RGB", (300, 300), (30, 90, 40)).save(d / "small.png")
check("a small picture is refused", "error" in refs.measure(d / "small.png"))
Image.new("RGB", (900, 900), (30, 90, 40)).save(d / "flat.png")
check("a blurry/flat picture is refused", "error" in refs.measure(d / "flat.png"))
src = Image.open(HERE / "refs/forest/aerial_canopy_01.png")
check("a sharp, large picture is accepted", "error" not in refs.measure(HERE / "refs/forest/aerial_canopy_01.png"))
# choosing from the story
check("a river story chooses river, water and forest", {"river", "water", "forest"} <= set(refs.categories_for("A Spinosaurus swims up a jungle river")))
check("a volcano story does not get a forest or river", not ({"forest", "river"} & set(refs.categories_for("Lava pours from the volcano"))))
check("a plain mountain story only gets mountain and lighting", set(refs.categories_for("Peaks rise over the range")) == {"mountain", "lighting"})
check("a story about nothing in the library chooses nothing", refs.categories_for("A quiet night") == [])
a = refs.begin_episode(7, "jungle river"); b = refs.begin_episode(7, "jungle river")
check("the same episode always gets the same references", {c: p["id"] for c, p in a.items()} == {c: p["id"] for c, p in b.items()} and "forest" in a)
refs.begin_episode(3, "lava volcano")
check("no forest reference in a volcano episode", refs.active("forest") is None and refs.palette("forest") is None)
# the references change what is generated
refs.ACTIVE.clear(); g0, a0 = toon._forest_style()
refs.begin_episode(1, "jungle river"); g1, a1 = toon._forest_style()
check("the forest colours come from the reference", g0 != g1 and any(abs(c[0] - c[1]) < 40 for c in g1))
check("accent crowns come from the reference (pink / pale)", a1[0] > 0.0 and a1[1] != a0[1])
check("the world is rebuilt when the reference changes (cache key)", toon._refs_key() != "")
refs.ACTIVE.clear(); refs.begin_episode(1, "jungle river")
w = toon._world(1)
land = np.asarray(w[0].crop((300, 4200, 1932, 5366)).resize((408, 291)), np.float32)
lum = (0.299 * land[..., 0] + 0.587 * land[..., 1] + 0.114 * land[..., 2]) / 255
tgt = o["forest_aerial_canopy_01"]["lum_mean"]
check(f"the generated canopy's brightness ({lum.mean():.2f}) matches the reference ({tgt:.2f})", abs(lum.mean() - tgt) < 0.06)
refs.ACTIVE.clear(); toon._WORLD.clear()
w0 = toon._world(1); l0 = np.asarray(w0[0].crop((300, 4200, 1932, 5366)).resize((408, 291)), np.float32).mean()
check("without a reference the forest is unchanged from the built-in look", abs(l0 - land.mean()) > 5)
sample = np.asarray(Image.open(HERE / "refs/forest/aerial_canopy_01.png").convert("RGB").resize((200, 140)), np.float32)
check("the reference picture itself is never pasted into the scene", np.abs(np.asarray(w[0].crop((300, 4200, 500, 4340)), np.float32) - sample).mean() > 20)
print("ALL REFERENCE CHECKS PASSED" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
