"""Checks for the strangeness picker, the non-animal bodies and how they are used by every series."""
import sys, pathlib, tempfile, random, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import make_short as ms, toon
from PIL import Image
ms.WORK = pathlib.Path(tempfile.mkdtemp())
ok = True
def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name); ok &= bool(cond)

# 1. the deal: every option once per round, new order each round, never the same twice in a row
opts = list("abcdefg")
seq = [ms.cycle_pick(opts, n, 1) for n in range(7 * 12)]
check("every option appears once per round", all(sorted(seq[r * 7:(r + 1) * 7]) == sorted(opts) for r in range(12)))
check("never the same twice in a row (also across rounds)", all(seq[i] != seq[i + 1] for i in range(len(seq) - 1)))
check("each round is in a different order", len({tuple(seq[r * 7:(r + 1) * 7]) for r in range(12)}) >= 10)
check("the deal is the same when asked again (no hidden state)", seq == [ms.cycle_pick(opts, n, 1) for n in range(7 * 12)])
packs = [ms.strange_pack(n) for n in range(300)]
for f in ("rule", "move", "shape", "tone"):
    check(f"{f}: never repeats back to back over 300 episodes", all(packs[i][f] != packs[i + 1][f] for i in range(299)))
check("rules: the same rule never comes back within 4 episodes", all(len({p["rule"] for p in packs[i:i + 5]}) == 5 for i in range(0, 290)))
check("moves: the same movement never comes back within 3 episodes", all(len({p["move"] for p in packs[i:i + 4]}) == 4 for i in range(0, 290)))
check("the four choices do not move in lock-step", len({(p["rule"], p["move"]) for p in packs[:120]}) > 100)

# 2. the body choice: most episodes get a non-animal body, and it suits the setting
fmts = {}
random.seed(5)
hist, got = [], []
for i in range(120):
    v = ms.folk_variety(hist)
    b = v["bodies"][0]
    got.append((v["format"], v["type"], b, v["strange_body"]))
    key = f"folk:wolf:Legend{i}|{b}|{v['type']}|{v['palettes'][0]}|{v['region']}|{v['origin']}"
    hist.append(key)
strange = [g for g in got if g[3]]
check("about half to two thirds of episodes get a non-animal body", 0.4 <= len(strange) / len(got) <= 0.8)
check("all four strange bodies show up", {g[2] for g in strange} == set(ms.STRANGE_BODIES))
check("a strange body never repeats back to back", all(not (got[i][3] and got[i + 1][3] and got[i][2] == got[i + 1][2]) for i in range(len(got) - 1)))
check("nature stories only get bodies that fit the water or sky", all(g[2] in ms.NATURE_STRANGE.get(g[1], ()) for g in strange if g[1] in ms.NATURE_BODIES))
check("prehistoric: only the meteor story gets a creature from another world", all(g[1] == "meteor" for g in strange if g[0] == "prehistoric") and any(g[0] == "prehistoric" and g[3] for g in got))
check("the lamp story is always the djinn", all(g[2] == "djinn" for g in got if g[1] == "lamp"))
check("every body that can be picked is a body the drawing engine knows", all(g[2] in toon.BODIES for g in got))

# 3. the prompts carry the strangeness and describe the body, for every series
seen = {}
class Stop(Exception): pass
def fake_post(url, params=None, json=None, timeout=0):
    seen["prompt"] = json["contents"][0]["parts"][0]["text"]
    raise Stop()
ms.requests.post = fake_post
ms.GEMINI_API_KEY = "x"
ms.time.sleep = lambda *_: None
def prompt_of(fn, *a, **k):
    seen.clear()
    try: fn(*a, **k)
    except BaseException: pass
    return seen.get("prompt", "")
def var_for(fmt, want_type=None, want_strange=True):
    hh = []
    for i in range(400):
        v = ms.folk_variety(hh)
        if v["format"] == fmt and v["strange_body"] == want_strange and (want_type is None or v["type"] == want_type):
            return v
        hh.append(f"folk:wolf:L{i}|{v['bodies'][0]}|{v['type']}|ash|x|y")
    return None
v = var_for("legend")
ms_fv = ms.folk_variety
ms.folk_variety = lambda h: v
p = prompt_of(ms.ask_folklore, [])
ms.folk_variety = ms_fv
check("folklore prompt says the creature is NOT an animal and describes it", "NOT an animal" in p and ms.BODY_DESC[v["bodies"][0]] in p)
check("folklore prompt carries the strangeness block", "THIS EPISODE'S STRANGENESS" in p and "THE STRANGE RULE" in p and "TONE OF VOICE" in p)
vm = var_for("modern")
pm = prompt_of(ms.ask_modern, vm)
check("modern prompt: strangeness + the body described", "THIS EPISODE'S STRANGENESS" in pm and ms.BODY_DESC[vm["bodies"][0]] in pm)
vp = var_for("prehistoric", "meteor")
pp = prompt_of(ms.ask_prehistoric, vp)
check("prehistoric (meteor) prompt: creature from another world is described and is not a dinosaur", ms.BODY_DESC[vp["bodies"][0]] in pp and "NOT a dinosaur" in pp)
vp2 = var_for("prehistoric", "volcano", want_strange=False)
pp2 = prompt_of(ms.ask_prehistoric, vp2)
check("prehistoric (volcano) keeps its dinosaur and still gets a rule and a tone", "NOT a dinosaur" not in pp2 and "THE STRANGE RULE" in pp2)
ph = prompt_of(ms.ask_gemini, "lion", "spider", "the clouds of Venus", "", None, ms.strange_pack(3))
check("hybrid prompt carries a rule and a tone", "THE STRANGE RULE" in ph and "TONE OF VOICE" in ph)
check("hybrid prompt without a pack still works", "STRANGENESS" not in prompt_of(ms.ask_gemini, "lion", "spider", "the clouds of Venus"))

# 4. each new body can be drawn as a creature spec and has its own way of killing
for b in ms.STRANGE_BODIES:
    sp = toon.folk_creature("wolf", {"body": b, "palette": "ash", "eyes": "red"})
    check(f"{b}: has a creature spec, no claws and no bones", toon.strike_of(sp) != "claws" and not toon.has_bones(sp))
check("a normal creature still claws and has bones", toon.strike_of(toon.folk_creature("wolf", {"body": "beast"})) == "claws" and toon.has_bones(toon.folk_creature("wolf", {"body": "beast"})))
sp = toon.folk_creature("wolf", {"body": "orb"})
check("anatomy scene geometry works for a creature with no measured outline", len(toon._fit_scale(dict(sp), 620)) == 4)
# 5. a folklore "chase" sentence shows a person running, not the hybrid animal hunt that has the same function name
import inspect
check("folk chase beat is routed to the person-chase scene", ms.SCENE_NAME.get("chase") == "person_chase" and hasattr(toon, "person_chase_scene"))
check("the hybrid animal hunt keeps its own chase_scene (it takes prey text)", "prey_text" in inspect.signature(toon.chase_scene).parameters and toon.chase_scene is not toon.person_chase_scene)

# 5. a voice clip padded with silence is trimmed (a padded clip once made a 73-word story look 74 seconds long)
import wave, numpy as np
f = ms.WORK / "pad.wav"; sr = 24000
tone = (np.sin(2 * np.pi * 200 * np.arange(sr) / sr) * 12000).astype(np.int16)
with wave.open(str(f), "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
    w.writeframes(np.concatenate([np.zeros(2 * sr, np.int16), tone, np.zeros(3 * sr, np.int16)]).tobytes())
ms._trim_voice_edges(f, "one two three four")
with wave.open(str(f)) as w:
    dur = w.getnframes() / w.getframerate()
check("a clip padded with 5 seconds of silence is trimmed to its speech plus a little air", 1.2 <= dur <= 1.7)

# 6. folklore "chase" shows a person running from the monster, not the animal hunt of the hybrid series
check("folk chase beat uses the person-running scene", ms.SCENE_NAME.get("chase") == "person_chase" and hasattr(toon, "person_chase_scene"))
check("hybrid chase_scene is still the animal hunt", "prey_text" in toon.chase_scene.__code__.co_varnames)

# 7. a long place name on the title card wraps instead of running off the screen (a hybrid episode was refused for "a mountain peak above the clouds")
import shutil as _sh
_fonts = pathlib.Path(__file__).resolve().parent.parent.parent / "fonts"
if (_fonts / "DancingScript[wght].ttf").exists():
    (ms.WORK / "fonts").mkdir(exist_ok=True)
    _sh.copy(_fonts / "DancingScript[wght].ttf", ms.WORK / "fonts" / "DancingScript.ttf")
    _sh.copy(_fonts / "Cinzel[wght].ttf", ms.WORK / "fonts" / "Cinzel.ttf")
for _title, _place in (("Penguinopus", "A MOUNTAIN PEAK ABOVE THE CLOUDS"), ("Octo-Penguin Hybrid", "AN ABANDONED RESEARCH STATION BENEATH THE ANTARCTIC ICE SHELF"),
                       ("The Lion That Keeps Its Own Shadow Tied Up", "A FLOODED CATHEDRAL AT THE BOTTOM OF THE SEA"), ("The Ash King", "LATE CRETACEOUS"),
                       ("The camera alert at three", "A HOUSE IN LEEDS, 3:07 AM")):
    _r = ms.title_card_png("IMPOSSIBLE ANIMALS", 7, _title, _place, ms.WORK / "tc.png")
    check(f"title card fits: {_place[:34]}", not _r["problems"])
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
