"""Checks for: real sentence timing found in the audio, brighter / clearer / smoother backgrounds, the 1440x2560 film
filter chain, and the lively narration pace."""
import os, sys, wave, random, subprocess, importlib.util, pathlib
os.environ["DRY_RUN"] = "1"
import numpy as np
HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location("ms", HERE / "make_short.py"); ms = importlib.util.module_from_spec(spec); spec.loader.exec_module(ms)
import toon
ok = True
def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name); ok &= bool(cond)

# ---- 1. sentence timing found in the audio itself
sr = 44100
def burst(d, f):
    t = np.arange(int(d * sr)) / sr
    return np.sin(2 * np.pi * f * t) * (np.abs(np.sin(np.pi * 7 * t)) ** 0.5 * 0.5 + 0.25) * 0.4
def make(sents, gaps, comma_gap=0.14, lead=0.2, wps=0.33, seed=1):
    rs = random.Random(seed); out = [np.zeros(int(lead * sr))]; truth = []; t = lead
    for i, tx in enumerate(sents):
        s0 = t
        parts = tx.split(",")
        for j, pt in enumerate(parts):
            d = len(pt.split()) * wps * rs.uniform(0.8, 1.25)
            out.append(burst(d, 110 + rs.random() * 40)); t += d
            if j < len(parts) - 1:
                out.append(np.zeros(int(comma_gap * sr))); t += comma_gap
        truth.append((s0, t))
        if i < len(sents) - 1:
            out.append(np.zeros(int(gaps[i] * sr))); t += gaps[i]
    out.append(np.zeros(int(0.3 * sr)))
    return np.concatenate(out), truth
def write(a, p):
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes((a * 32000).astype(np.int16).tobytes())
cases = [
    (["A traveller walked the old forest road alone at night.", "Behind him, steps matched his own, one for one.", "He stopped, and the steps stopped too.",
      "In the mud beside him, enormous footprints appeared.", "It leapt, and its teeth closed upon his shoulder.", "By morning only the lantern remained."], [0.35, 0.45, 0.9, 0.3, 0.5]),
    (["Short one.", "This sentence is quite a lot longer than the other, with several words in it, and a comma.", "Tiny.",
      "Another fairly long sentence that goes on for a while and then ends, finally.", "Done."], [0.5, 0.3, 1.2, 0.4]),
    (["One two three four five six seven.", "One two three four five six seven.", "One two three four five six seven."], [0.25, 0.25]),
]
import tempfile
TD = pathlib.Path(tempfile.mkdtemp(prefix="nightreel_q_"))
tmp = TD / "al_test.wav"
worst, worst_old = 0.0, 0.0
for ci, (s_, g_) in enumerate(cases):
    a, truth = make(s_, g_, seed=ci + 3); write(a, tmp)
    r = ms.align_sentences(tmp, s_)
    errs = [abs(r[i][0] - truth[i][0]) for i in range(len(s_))] + [abs(r[i][1] - truth[i][1]) for i in range(len(s_))]
    chars = [max(len(x), 1) for x in s_]; L = len(a) / sr; cum = 0; old = []
    for c in chars:
        old.append(cum / sum(chars) * L); cum += c
    worst = max(worst, max(errs)); worst_old = max(worst_old, max(abs(old[i] - truth[i][0]) for i in range(len(s_))))
check(f"sentence starts/ends found within 120 ms (worst {worst * 1000:.0f} ms)", worst < 0.12)
check(f"...where the old character estimate was off by up to {worst_old * 1000:.0f} ms", worst_old > 0.3)
check("one sentence, silence, or unreadable file is handled", ms.align_sentences(tmp, ["only one sentence here."]) is not None and ms.align_sentences(HERE / "nope.wav", ["a", "b"]) is None)

# ---- 2. backgrounds: brighter, anti-aliased, same transparency rules
w = toon.World("a village at night", 3, {})
fr = np.asarray(w.frame(400, 1.5), dtype=np.float32)
lum = (fr @ np.array([0.299, 0.587, 0.114], dtype=np.float32))
check(f"a night background is no longer murky (mean brightness {lum.mean():.0f}/255, ground {lum[1450:].mean():.0f})", lum.mean() > 52 and lum[1450:].mean() > 28)
al = np.asarray(w.near)[..., 3]
check("hill and tree edges are anti-aliased (soft alpha at the edges)", ((al > 0) & (al < 255)).sum() > 2000)
check("every layer still knows where it sits", all(hasattr(getattr(w, n), "top") for n in ("hz", "far", "mid", "near")))
wb = toon.World("a mountain peak above the clouds", 2, {})
lb = (np.asarray(wb.frame(300, 1.0), dtype=np.float32) @ np.array([0.299, 0.587, 0.114], dtype=np.float32))
check(f"a bright place is not blown out (mean {lb.mean():.0f}, clipped {100 * (lb > 250).mean():.1f}%)", (lb > 250).mean() < 0.12)
sky = np.asarray(w.sky, dtype=np.float32)[300:700, 100:140, 0]
check("the sky has no hard bands (a whisper of noise dithers it)", sky.std(axis=1).mean() > 0.4)

# ---- 3. the finished film: bigger frame, the filter chain is valid
check("output is 1440x2560 by default", ms.out_size() == (1440, 2560))
K = 1440 / 1080
vf = (f"scale={round(1134 * K)}:{round(2016 * K)}:flags=lanczos,crop=1440:2560:x='(27)*{K:.5f}':y='(48)*{K:.5f}',{ms.FILM},{ms.GRADES['cool']},"
      "eq=brightness='0+0.06':saturation=1.12:contrast=1.25:gamma=1.35:eval=frame,unsharp=5:5:0.9:5:5:0.0,unsharp=13:13:0.45:13:13:0.0,"
      "vignette=PI/5.5,noise=alls=2:allf=t+u")
r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=1080x1920:d=0.2:r=10", "-vf", vf, "-frames:v", "1", str(TD / "q.png")], capture_output=True, text=True)
check("the film filter chain is accepted by ffmpeg", r.returncode == 0 and (TD / "q.png").exists())
from PIL import Image
check("...and makes a 1440x2560 picture", Image.open(TD / "q.png").size == (1440, 2560))
# the look must be bright AND crisp: a night scene keeps its brightness, but the deepest darks stay really dark (not a hazy grey)
Image.fromarray(np.asarray(w.frame(400, 1.5)).astype(np.uint8)).save(TD / "night_in.png")
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(TD / "night_in.png"), "-vf", vf.replace(ms.GRADES['cool'], "null"), "-frames:v", "1", str(TD / "night_out.png")], capture_output=True, text=True)
g = np.asarray(Image.open(TD / "night_out.png").convert("L"), dtype=np.float32)
check(f"the finished night picture is bright (mean {g.mean():.0f}) yet has deep darks (2% darkest <= {np.percentile(g, 2):.0f})", g.mean() >= 55 and np.percentile(g, 2) <= 24)

# ---- 4. the voice
check("narration plays at a steady, slightly lively pace (x1.0 to x1.10; the owner set it to 1.03 on 26 Nov)", 1.0 <= ms.NARR_RATE <= 1.10)
check("the narrator style asks for energy, not slowness", "brisk" in ms.NARRATOR_STYLE and "Never slow" in ms.NARRATOR_STYLE and "Slow, deliberate" not in ms.NARRATOR_STYLE)
print("ALL QUALITY CHECKS PASSED" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
