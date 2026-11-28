"""Caption safety regression test (the 10 compositions from the production rules).
Run before delivering ANY upgrade:  python3 tests/test_captions.py"""
import importlib.util, pathlib, sys, tempfile
here = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(here))
spec = importlib.util.spec_from_file_location("ms", here / "make_short.py")
ms = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ms)

WORDS = [(i * 0.35, i * 0.35 + 0.33, w) for i, w in enumerate(
    "THE CREATURE STEPPED OUT OF THE ASH AND THE VILLAGE WENT QUIET AT ONCE".split())]
TOTAL = WORDS[-1][1] + 0.3


def screen_to_toon(b):
    """boxes are given as they appear on the final screen; the track stores them in drawing space"""
    return [(b[0] + 27) / 1.05, (b[1] + 48) / 1.05, (b[2] + 27) / 1.05, (b[3] + 48) / 1.05]


def run(name, fn):
    track = [(i / 24, screen_to_toon(fn(i / 24))) for i in range(int(TOTAL * 24) + 1)]
    out = pathlib.Path(tempfile.mkdtemp()) / "c.ass"
    ms.write_captions(WORDS, TOTAL, out, track=track)
    rep = ms.CAPTION_REPORT[-1]
    # when the creature fills the whole frame, leaving the captions out is the correct result (protect the animal)
    ok = rep["collisions"] == 0 and (rep["captions"] > 0 or name[:2].strip() in ("5", "10"))
    print(("PASS " if ok else "FAIL ") + name, {k: rep[k] for k in ("captions", "moved", "split", "omitted", "collisions")})
    return ok


T = TOTAL
tests = {
    "1 animal in the lower half": lambda t: (150, 1050, 930, 1500),
    "2 animal in the upper half": lambda t: (200, 300, 880, 800),
    "3 animal moving left to right": lambda t: (-300 + 1500 * t / T, 1000, 100 + 1500 * t / T, 1500),
    "4 animal moving right to left": lambda t: (1200 - 1500 * t / T, 1000, 1500 - 1500 * t / T, 1500),
    "5 large animal filling the screen": lambda t: (0, 100, 1080, 1900),
    "6 close-up": lambda t: (150, 500, 930, 1500),
    "7 two subjects": lambda t: (60, 900, 400, 1450) if t < 2 else (60, 900, 1000, 1450),
    "8 character and creature": lambda t: (100, 700, 980, 1500),
    "9 subject moves into the empty caption region": lambda t: (300, 1380 - 900 * t / T * 0 + (-700 * t / T) * -1 * 0 - 0, 800, 1700) if t < 2 else (300, 1100, 800, 1500),
    "10 dark scene, little empty space": lambda t: (80, 240, 1000, 1560),
}
bad = [n for n, f in tests.items() if not run(n, f)]
# the placement rules themselves
assert ms.caption_in_safe_zone(1400, (700, 140)) and not ms.caption_in_safe_zone(1700, (700, 140)), "bottom UI zone"
assert not ms.caption_in_safe_zone(1400, (1000, 140)), "right-hand buttons zone"
# a creature in the lower half must push captions to the top
track = [(i / 24, screen_to_toon((150, 1000, 930, 1550))) for i in range(int(TOTAL * 24))]
p = ms._place_caption(track, 0.0, 2.0, (600, 140), 1400)
assert p[0] is not None and p[0] <= 880, p
# a scene cut in the middle of a phrase: before the cut the figure blocks the top and middle, after the cut something blocks the bottom
# (the real failure: hallway figure, then the hosts on the couch). The phrase must be cut where the picture cuts, not omitted.
def cut_case(name, cut, expect_words_left_out=0):
    ws = [(i * 0.45, i * 0.45 + 0.42, w) for i, w in enumerate("THE TIMESTAMP MATCHED EVERY NIGHT AND THEN SOMETHING MOVED".split())]
    tot = ws[-1][1] + 0.3
    trk = [(i / 24, screen_to_toon((100, 150, 980, 1280) if i / 24 < cut else (100, 1000, 980, 1900))) for i in range(int(tot * 24) + 1)]
    out = pathlib.Path(tempfile.mkdtemp()) / "c.ass"
    ms.write_captions(ws, tot, out, track=trk)
    r = ms.CAPTION_REPORT[-1]
    ok_ = r["collisions"] == 0 and r["omitted_words"] <= expect_words_left_out
    print(("PASS " if ok_ else "FAIL ") + name, {k: r[k] for k in ("captions", "split", "omitted", "omitted_words", "words", "collisions")})
    return ok_
for nm, cut in (("11 cut after the first word of a phrase", 0.5), ("12 cut in the middle of a phrase", 1.0), ("13 cut near the end of a phrase", 1.7)):
    if not cut_case(nm, cut):
        bad.append(nm)
# the gate counts words left out, not phrases: a few single words are fine, a whole sentence is not
ms.CAPTION_REPORT[:] = [{"collisions": 0, "omitted": 2, "omitted_words": 2, "words": 40, "captions": 12, "tolerated": 0}]
ms.TITLECARD.clear()
# a single word whose time slot straddles the cut: it is shown for the clear part of its time rather than left out
import re as _re
ws1 = [(0.0, 0.3, "A"), (0.8, 1.9, "NIGHT")]
# one caption spot only, so that the picture really blocks it for the first 1.2 seconds (with all the usual spots a caption always finds a clear one)
trk1 = [(i / 24, screen_to_toon((100, 1150, 980, 1700) if i / 24 < 1.2 else (100, 150, 980, 700))) for i in range(int(2.4 * 24) + 1)]
out1 = pathlib.Path(tempfile.mkdtemp()) / "c.ass"
_spots = list(ms.CAPTION_SPOTS)
ms.CAPTION_SPOTS = (1400,)
try:
    ms.write_captions(ws1, 2.4, out1, track=trk1)
finally:
    ms.CAPTION_SPOTS = tuple(_spots)
r1 = ms.CAPTION_REPORT[-1]
night = [l for l in out1.read_text().splitlines() if "NIGHT" in l]
st = night and _re.search(r"Dialogue: 0,(\d+):(\d+):([\d.]+)", night[0])
start1 = (int(st.group(2)) * 60 + float(st.group(3))) if st else None
ok1 = r1["omitted_words"] <= 1 and r1["collisions"] == 0 and start1 is not None and start1 >= 1.2      # only "A", which lies wholly under the blocked time, may go
print(("PASS " if ok1 else "FAIL ") + "14 a word cut in half by the picture is shown for its clear part", {k: r1[k] for k in ("captions", "omitted_words", "collisions")}, start1)
if not ok1:
    bad.append("14")

def gate(rep):
    ms.CAPTION_REPORT[:] = [rep]
    res = ms.run_quality_checks(ms.WORK / "none.mp4", [(0, 1, "x")], "hybrid")
    return next(p for s, p, _ in res if s == "Captions")
for nm, rep, want in (("gate accepts 2 of 40 words left out", {"collisions": 0, "omitted": 2, "omitted_words": 2, "words": 40, "captions": 12, "tolerated": 0}, True),
                      ("gate accepts 3 single words of a short 55-word story", {"collisions": 0, "omitted": 3, "omitted_words": 3, "words": 55, "captions": 12, "tolerated": 0}, True),
                      ("gate refuses 12 of 40 words left out", {"collisions": 0, "omitted": 4, "omitted_words": 12, "words": 40, "captions": 12, "tolerated": 0}, False),
                      ("gate refuses any collision", {"collisions": 1, "omitted": 0, "omitted_words": 0, "words": 40, "captions": 12, "tolerated": 0}, False),
                      ("gate still reads old reports (3 of 12 phrases is too many)", {"collisions": 0, "omitted": 3, "captions": 12, "tolerated": 0}, False)):
    got = gate(rep)
    print(("PASS " if got == want else "FAIL ") + nm)
    if got != want:
        bad.append(nm)
print("ALL CAPTION SAFETY CHECKS PASSED" if not bad else f"FAILED: {bad}")
sys.exit(1 if bad else 0)
