"""Checks for the cinematic upgrade: title card layout, narrator pauses, abilities, walking rule, anatomy, quality gate."""
import sys, pathlib, wave, struct, math, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import make_short as ms, toon, numpy as np
ms.WORK = pathlib.Path(tempfile.mkdtemp())
ok = True
def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name); ok &= bool(cond)

# title card: good layouts pass, no overlaps; a card with colliding text is rejected
r = ms.title_card_png("Impossible Animals", "12", "Chameleonic Stalkerwolf", "Deep Rainforest", ms.WORK / "c.png")
check("title card passes (no overlaps)", not r["problems"])
r = ms.title_card_png("Lost World", "2", "A Title That Is Far Too Long To Ever Fit In Two Lines On A Phone Screen At Any Readable Size", "x", ms.WORK / "c2.png")
check("a far-too-long title is cut at a word and the card still passes (a long title must never fail the whole video)",
      not r["problems"] and r["title_size"] >= 84 and len(r["lines"]) <= 3)
r = ms.title_card_png("Impossible Animals", "7", "The Penguin Octopus That Leaves A Coin Before It Takes You", "A MOUNTAIN PEAK ABOVE THE CLOUDS", ms.WORK / "c3.png")
check("the real long hybrid title (3 lines, 2-line place) passes", not r["problems"] and len(r["lines"]) == 3)
r = ms.title_card_png("Impossible Animals", "9", "The Penguin Octopus", "SUPERCALIFRAGILISTICEXPIALIDOCIOUSLYLONGPLACENAMEWITHNOSPACES", ms.WORK / "c4.png")
check("an absurdly long place name never leaves the safe width", not r["problems"])

# narrator pause: silence really inserted at the quiet point
sr = 44100
x = np.concatenate([np.sin(np.arange(sr * 2) * 0.05) * 9000, np.zeros(int(sr * 0.3)), np.sin(np.arange(sr * 2) * 0.05) * 9000]).astype(np.int16)
with wave.open(str(ms.WORK / "v.wav"), "wb") as o:
    o.setnchannels(1); o.setsampwidth(2); o.setframerate(sr); o.writeframes(x.tobytes())
ms.run = lambda cmd, cwd=None: __import__("subprocess").run(cmd, cwd=cwd, check=True, capture_output=True)
nm, words, durs = ms.insert_sound_pauses("v.wav", [(0.1, 0.5, "a"), (2.5, 3.0, "b")], [2.15, 2.15], {0: 2.0})
with wave.open(str(ms.WORK / nm)) as w_:
    n = w_.getnframes() / w_.getframerate()
check("2.0s of silence inserted", abs(n - (4.3 + 2.0)) < 0.1)
check("later words shifted by the silence", words[1][0] > 4.4)

# abilities and walking
check("colour change detected", ms.detect_ability("It changes colour to match the rock.") == "camouflage")
check("swimming detected", ms.detect_ability("It dives beneath the surface.") == "swim")
check("walking only for size/approach", ms.needs_walk("Each footstep shakes the ground.") and not ms.needs_walk("It smells the air."))

# anatomy works on a hybrid and a plain animal
for a, b in (("crocodile", "lion"), ("eagle", "wolf")):
    sp = toon.hybrid_spec_from(toon.design_hybrid(a, b))
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (1080, 1920), (30, 40, 40)); d = ImageDraw.Draw(im, "RGBA")
    toon.draw_creature(d, sp, 540, toon.GROUND + 20, 0.0, 1, 0.8, {"t": 0.0, "moving": False, "xray": {"k": 1.0, "front": None, "labels": True}})
    arr = np.asarray(im).astype(int)
    check(f"x-ray skeleton drawn for {a}-{b}", (arr.min(axis=2) > 220).sum() > 300)

# the gate refuses a failing video
ms.TITLECARD.clear(); ms.CAPTION_REPORT[:] = [{"collisions": 2, "omitted": 0, "captions": 10}]
res = ms.run_quality_checks(ms.WORK / "none.mp4", [(0, 1, "x")], "hybrid")
check("gate fails when a caption collision is left", any(s == "Captions" and not p for s, p, _ in res))
print("ALL UPGRADE CHECKS PASSED" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
