"""
Makes one YouTube Short: an AI-animated story about an imaginary animal hybrid
surviving a harsh place.

How the video is built
  - Pictures: AI images from Cloudflare Workers AI (free). Creatures are made on a plain
    colored backdrop, cut out, and walked across an AI background ("puppet" animation).
  - Voice: Gemini text-to-speech (free models first), basic free voice as backup.
  - Sound: animal calls from Freesound (CC0 only, optional key) plus synthesized
    whooshes, riser, boom and a layered monster scream for the hybrid.
  - Effects: camera shake, flash, vignette, film grain.
  - Every step has a fallback so a video is still made when one service fails.
"""
import asyncio
import base64
import json
import math
import os
import pathlib
import random
import re
import shutil
import subprocess
import sys
import time
import wave

import edge_tts
import requests

ROOT = pathlib.Path(__file__).parent
HISTORY_FILE = ROOT / "history.json"
DESIGNS_FILE = ROOT / "hybrids.json"  # every hybrid ever designed, so a new one is never a copy of an old one
WORK = ROOT / "work"
OUT = ROOT / "output"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY", "")
FREESOUND_API_KEY = os.environ.get("FREESOUND_API_KEY", "")
CF_ACCOUNT = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
CF_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
CF_MODEL = os.environ.get("CF_IMAGE_MODEL", "@cf/black-forest-labs/flux-1-schnell")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
def _load_narrator():
    """The locked narrator identity (narrator.json). A story can never change it; only the owner can."""
    try:
        return json.loads((ROOT / "narrator.json").read_text())
    except Exception:
        return {"voice": "Charon", "edge_fallback_voice": "en-US-AndrewNeural", "pitch_shift_semitones": 0}


NARRATOR = _load_narrator()
GEMINI_VOICE = os.environ.get("GEMINI_VOICE", NARRATOR.get("voice", "Charon"))
# "classic" = the original single-take narrator voice (the one you approved); "acted" = a new mood for every sentence
VOICE_MODE = os.environ.get("VOICE_MODE", "classic").strip().lower()
EDGE_VOICE = os.environ.get("TTS_VOICE", NARRATOR.get("edge_fallback_voice", "en-US-AndrewNeural"))   # fallback voice: the natural one, no artificial lowering
PRIVACY = os.environ.get("PRIVACY", "private")  # private = you review first
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"  # 1 = make video, do not upload

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
TTS_MODELS = ["gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts",
              "gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts"]

VINTAGE = ("vintage 1970s wildlife documentary film photograph, shot on 35mm Kodak film, fine film grain, "
           "faded muted colors, soft natural light, photorealistic, real photograph, not a cartoon, "
           "not an illustration, not CGI")
STYLE = f"{VINTAGE}, vertical composition, no text, no watermark, no people."
# The cut-out creature is photographed in clean, natural colors (so the backdrop is easy to remove);
# the vintage film look is added afterwards by the color grade, so it matches the background.
STYLE_SPRITE = ("Photorealistic wildlife photograph, telephoto lens, realistic fur, skin and scale "
                "texture, sharp focus, not a cartoon, not an illustration, no text, no watermark.")
FIERCE = ("a fierce, terrifying, dominant apex predator, menacing snarling expression, bared fangs, "
          "glowing eyes, scarred battle-worn hide, huge muscular body, intimidating horror-creature look")

ANIMALS = [
    "camel", "octopus", "penguin", "axolotl", "bat", "cheetah", "crocodile",
    "polar bear", "jellyfish", "owl", "chameleon", "elephant", "shark",
    "scorpion", "frog", "eagle", "wolf", "snake", "turtle", "spider",
    "whale", "gecko", "dragonfly", "lion", "gorilla", "fox", "seal",
    "ant", "crab", "hummingbird", "lizard", "bear",
]

# (place, background description, ambience sound search, light on the creature, color grade)
PLACES = [
    ("the Sahara desert", "endless sand dunes under a blazing sun", "desert wind",
     "harsh golden afternoon sunlight with long shadows", "warm"),
    ("Antarctica", "a frozen white ice plain under a stormy sky", "arctic wind blizzard",
     "flat cold blue overcast light", "cool"),
    ("the deep ocean", "the dark deep sea floor with faint blue light", "underwater ambience",
     "dim murky blue light from above", "teal"),
    ("a land of active volcanoes", "black rock and glowing red lava rivers", "volcano rumble",
     "glowing orange light from lava and smoke", "red"),
    ("Mars", "a rusty red desert of rocks and dust under a pink sky", "alien wind",
     "dusty pinkish-orange hazy light", "red"),
    ("the clouds of Venus", "thick orange storm clouds with lightning", "thunder storm",
     "sickly orange light through thick haze", "warm"),
    ("an icy moon like Europa", "cracked blue ice with a huge planet in the sky", "ice cracking wind",
     "cold pale blue light", "cool"),
    ("a rainforest after a flood", "a flooded jungle with heavy rain and mist", "rainforest rain",
     "dim green light filtered through mist", "green"),
    ("a mountain peak above the clouds", "a snowy rocky summit above a sea of clouds", "mountain wind",
     "crisp cold sunlight", "cool"),
    ("a planet with two suns", "an alien plain under two glowing suns", "sci-fi ambience drone",
     "warm light from two suns", "warm"),
    ("a swamp full of toxic mud", "a foggy swamp with bubbling green mud", "swamp night ambience",
     "murky greenish dim light and fog", "green"),
    ("the edge of a black hole", "a glowing black hole warping stars in deep space", "deep space drone",
     "eerie dim bluish glow", "cool"),
]

# One color grade applied to the whole frame (and the creature) so everything shares one look.
GRADES = {
    "warm": "colorbalance=rs=0.05:bs=-0.06:rm=0.08:bm=-0.07:rh=0.06:bh=-0.05",
    "cool": "colorbalance=rs=-0.04:bs=0.07:rm=-0.05:bm=0.09:rh=-0.03:bh=0.06",
    "red": "colorbalance=rs=0.08:gs=-0.02:bs=-0.05:rm=0.12:gm=-0.03:bm=-0.08",
    "green": "colorbalance=gs=0.05:rs=-0.03:gm=0.07:rm=-0.04:bm=-0.03",
    "teal": "colorbalance=rs=-0.06:bs=0.05:gs=0.03:rm=-0.08:bm=0.06:gm=0.04",
}

# Backdrop color used to cut creatures out; pick the one least like the animals' own colors.
KEYS = {
    "green": ("0x00FF00", "bright pure green", "green"),
    "magenta": ("0xFF00FF", "bright pure magenta", None),
    "blue": ("0x0000FF", "bright pure blue", "blue"),
}
KEY_CONFLICTS = {
    "green": {"frog", "gecko", "chameleon", "lizard", "snake", "turtle", "crocodile",
              "dragonfly", "hummingbird"},
    "magenta": {"axolotl", "jellyfish"},
    "blue": {"shark", "whale", "seal", "penguin"},
}

# What to search for in the free stock footage library for each place
PLACE_STOCK = {
    "the Sahara desert": "desert sand dunes", "Antarctica": "antarctica snow ice storm",
    "the deep ocean": "deep sea underwater dark", "a land of active volcanoes": "volcano lava eruption",
    "Mars": "red desert rocks dust", "the clouds of Venus": "storm clouds lightning",
    "an icy moon like Europa": "frozen ice glacier", "a rainforest after a flood": "flooded jungle rain",
    "a mountain peak above the clouds": "mountain summit clouds", "a planet with two suns": "alien landscape sunset",
    "a swamp full of toxic mud": "swamp fog", "the edge of a black hole": "space galaxy nebula",
}

FPS = 30
FADE = 0.6          # seconds of cross-dissolve in the opening morph
MAX_SPRITE_W = 640  # biggest a parent animal is drawn, in pixels (the hybrid is drawn bigger)
MAX_SPRITE_H = 780
GROUND_Y = 1320     # where the creature's feet are
STEP_RATE = 1.9     # steps per second
HYBRID_SCALE = 1.3  # the hybrid is drawn bigger so it dominates the frame
MOTIONS = ["in", "left", "out", "right"]


# ---------------------------------------------------------------- helpers
def log(msg):
    print(msg, flush=True)


def run(cmd, cwd=None):
    if cmd and cmd[0] == "ffmpeg":  # show only real errors, not the long banner and progress lines
        cmd = [cmd[0], "-hide_banner", "-loglevel", "error"] + list(cmd[1:])
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        log(result.stderr[-2000:])
        crash = " - ffmpeg crashed" if result.returncode < 0 else ""
        raise RuntimeError(f"Command failed (code {result.returncode}{crash}): "
                           + " ".join(str(c) for c in cmd[:12]))
    return result.stdout


def run_capture(cmd, cwd=None):
    """Run ffmpeg and return everything it printed (used to read measurements)."""
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return result.stdout + result.stderr


def load_history():
    if HISTORY_FILE.exists():
        return json.loads(HISTORY_FILE.read_text())
    return []


def load_designs():
    if DESIGNS_FILE.exists():
        try:
            return json.loads(DESIGNS_FILE.read_text())
        except Exception:
            return []
    return []


def pick_idea(history):
    for _ in range(500):
        a, b = sorted(random.sample(ANIMALS, 2))
        place = random.choice(PLACES)
        key = f"{a}+{b}@{place[0]}"
        if key not in history:
            return key, a, b, place
    raise RuntimeError("Ran out of unused ideas. Add more animals/places.")


def pick_key(animals):
    best, best_score = "green", 99
    for name in ("green", "magenta", "blue"):
        score = sum(1 for a in animals if a in KEY_CONFLICTS[name])
        if score < best_score:
            best, best_score = name, score
    return best


def find_media(obj, kind):
    """Find the dict holding base64 data of the given kind ('audio'/'image') in a JSON reply."""
    if isinstance(obj, dict):
        data = obj.get("data")
        if isinstance(data, str) and len(data) > 100:
            mime = str(obj.get("mime_type") or obj.get("mimeType") or "")
            if obj.get("type") == kind or mime.startswith(kind + "/"):
                return {"data": data, "mime": mime}
        for v in obj.values():
            found = find_media(v, kind)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_media(v, kind)
            if found:
                return found
    return None


def longest_b64(obj):
    best = ""
    if isinstance(obj, dict):
        for v in obj.values():
            c = longest_b64(v)
            if len(c) > len(best):
                best = c
    elif isinstance(obj, list):
        for v in obj:
            c = longest_b64(v)
            if len(c) > len(best):
                best = c
    elif isinstance(obj, str) and len(obj) > 2000:
        best = obj
    return best


# ---------------------------------------------------------------- writing
HYB_ACTIONS_WILD = ("walk", "walkback", "feed", "turn", "face", "hunt", "footprints")
HYB_ACTIONS_ENC = ("footprints", "pov", "loom", "shoulder", "chase", "face", "walk", "hunt")
HYB_FORMATS = (
    ("Survival documentary", "wild", "A nature-documentary look at the creature surviving in {place}.",
     "each one a specific danger of {place} and the clever trait that solves it."),
    ("Rival showdown", "wild", "A famous predator of {place} decides this is its territory, and the hybrid will not leave.",
     "build the stand-off: the rival's size and reputation, the first challenge, then the moment ONE trait from the {a} beats the rival and ONE from the {b} finishes it."),
    ("The great escape", "wild", "The hybrid is hunted by something far bigger. It must escape.",
     "the pursuer closes in, the hybrid tries a trick from the {a}, it fails, then a trick from the {b} saves it; keep the tension rising."),
    ("A day in its life", "wild", "Follow the creature from dawn to night in {place}.",
     "dawn, the heat or cold of midday, the hunt at dusk, and the night when it has to defend its territory, each tied to a trait from the {a} or the {b}."),
    ("Cryptid encounter", "enc", "A ranger in {place} finds strange tracks, hears something at night, and finally sees it.",
     "tracks that match no known animal, a sound in the dark, the creature glimpsed at the edge of the light, the ranger's escape; the creature is never cruel, only huge and impossible."),
    ("When the habitat changes", "wild", "A drought, flood, wildfire or freeze hits {place}, and the hybrid has to adapt.",
     "what disaster arrives, how the {a} half of the creature copes, how the {b} half copes, and why the hybrid survives better than either parent would."),
    ("Guardian of the nest", "wild", "The hybrid guards its young in {place}, and a threat comes close.",
     "the nest, the threat creeping closer, how parenting habits from the {a} and the {b} combine into a defence, and how the threat backs off."),
    ("Which parent wins?", "wild", "Inside the hybrid, the {a} and the {b} abilities compete: speed against strength, senses against armour.",
     "each sentence shows one contest between a {a} ability and a {b} ability in {place}, escalating, with a surprising winner at the end."),
)


def ask_gemini(a, b, place_name, design_text="", fmt=None):
    fmt = fmt or random.choice(HYB_FORMATS)
    fname, fam, brief, middle = fmt
    brief, middle = brief.format(place=place_name, a=a, b=b), middle.format(place=place_name, a=a, b=b)
    acts = HYB_ACTIONS_ENC if fam == "enc" else HYB_ACTIONS_WILD
    prompt = f"""You write narration for 40-second YouTube Shorts about IMAGINARY animal hybrids.

Idea: a hybrid of a {a} and a {b}, living in {place_name}.
STORY FORMAT: "{fname}". {brief} Tell THIS story, with a beginning, a rising problem and an ending, instead of just listing facts.
{("The creature has been designed. Describe EXACTLY this anatomy and movement, do not invent spikes, armour or other features it does not have: " + design_text) if design_text else ""}

Return ONLY valid JSON, no markdown, in exactly this shape:
{{
  "title": "catchy title under 70 characters, no hashtags",
  "creature_name": "a fun invented name for the hybrid",
  "prey": "a specific animal that lives in {place_name} and that the hybrid hunts",
  "signature_move": "one sentence: the hybrid's signature move on that prey, using abilities taken from the {a} and the {b} (an action, not gore)",
  "hybrid_look": "one detailed sentence describing how the creature LOOKS, clearly showing specific features taken from the {a} and from the {b}, matching the design given above if there is one (its body shape, face, limbs, wings or shell, colours and how it moves)",
  "sentences": [
    {{"text": "one spoken sentence", "image": "the SETTING shown while this sentence is spoken", "action": "what the creature is physically doing while this sentence is spoken: one of {', '.join(acts)}"}}
  ]
}}

Rules for "sentences" (7 to 9 items, 65 to 85 words in total (the voice reads slowly, about 1.6 words per second, and a Short must stay under 50 seconds, so NEVER go over 90 words), plain spoken English, no emojis):
- Sentence 1: a short, shocking hook (under 14 words) about the most terrifying thing this creature does.
- Sentence 2: names the creature, says it is imaginary, and says what it inherits from the {a} and from the {b}.
- One middle sentence describes the signature move on its prey.
- Middle sentences: {middle}
- Give each middle sentence a DIFFERENT "action", and make it the action the sentence really describes (hunt = it pounces on prey, feed = it eats, walk = it travels, turn = it reacts to a sound, face = a close-up of its face, footprints = its tracks are found). Use "hunt" at most once.
- Storyteller frame: in ONE place (never sentence 1, 2 or the last) add a short first-person line from the narrator ("I saw it myself.",
  "We should have turned back.") with "frame": true. It must not describe a new event, only the narrator recalling it.
- Host reactions: in ONE or TWO places add one short sentence with "speaker": "host", the narrator's curious friend reacting out loud
  in 2 to 6 words to the sentence just before it ("Wait, WHAT?!", "It did that?!"). A host line is never sentence 1, 2 or the last, and
  never sits next to another host line. Host and frame lines do not count towards the word total.
- Last sentence: a surprising weakness as a twist, then end with exactly these words: What should I combine next? Tell me in the comments.
- Use real science about the parent animals where possible. Do not present false facts as real.
- "image" is a vivid 10 to 20 word description of the BACKGROUND ONLY for that sentence: the place
  and the danger (for example "a howling sandstorm swallowing red dunes"). Never mention any animal
  or creature in "image", the creature is added separately. No text or words inside pictures.
"""
    url = f"{API_BASE}/models/{GEMINI_MODEL}:generateContent"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 1.0, "responseMimeType": "application/json"},
    }
    last_err = None
    for attempt in range(3):
        try:
            r = requests.post(url, params={"key": GEMINI_API_KEY}, json=body, timeout=90)
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
            data = json.loads(text)
            sents = [s for s in data["sentences"]
                     if isinstance(s, dict) and str(s.get("text", "")).strip()]
            assert data["title"] and data["hybrid_look"] and len(sents) >= 5
            for s in sents:
                s["text"] = s["text"].strip()
                s["image"] = str(s.get("image") or f"a dramatic view of {place_name}").strip()
                s["action"] = str(s.get("action") or "").strip().lower() if str(s.get("action") or "").strip().lower() in acts else ""
                s["speaker"] = "host" if str(s.get("speaker") or "").lower() == "host" else "narrator"
                s["frame"] = bool(s.get("frame")) and s["speaker"] == "narrator"
            # the hosts appear only where they make sense: not in the first two or the last sentence, never side by side, at most two
            sents = sents[:11]
            hosts_seen = 0
            for k_, s in enumerate(sents):
                if s["speaker"] == "host" and (k_ < 2 or k_ >= len(sents) - 1 or hosts_seen >= 2
                                               or (k_ and sents[k_ - 1]["speaker"] == "host")):
                    s["speaker"] = "narrator"
                    s["text"] = s["text"]
                    s["_drop"] = True
                elif s["speaker"] == "host":
                    hosts_seen += 1
                if s["frame"] and (k_ < 2 or k_ >= len(sents) - 1):
                    s["frame"] = False
                    s["_drop"] = True
            sents = [s for s in sents if not s.get("_drop")]          # a misplaced host/frame line is removed, not read in the wrong voice
            data["sentences"] = sents
            data["format"] = fname
            data["prey"] = str(data.get("prey") or "a small desert animal").strip()
            data["signature_move"] = str(data.get("signature_move") or "it pounces with lightning speed").strip()
            _word_guard(data, attempt)
            return data
        except Exception as e:
            last_err = e
            log(f"Gemini attempt {attempt + 1} failed: {e}")
    raise RuntimeError(f"Gemini failed 3 times: {last_err}")


def facing_direction(img_name):
    """Ask Gemini which way the creature faces. Returns 1 (right) or -1 (left)."""
    try:
        path = WORK / img_name
        mime = "image/png" if img_name.endswith("png") else (
            "image/webp" if img_name.endswith("webp") else "image/jpeg")
        body = {"contents": [{"parts": [
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(path.read_bytes()).decode()}},
            {"text": "Which way is the animal's head pointing in this picture, toward the LEFT "
                     "or the RIGHT side of the picture? Answer with exactly one word: LEFT or RIGHT."},
        ]}]}
        r = requests.post(f"{API_BASE}/models/{GEMINI_MODEL}:generateContent",
                          params={"key": GEMINI_API_KEY}, json=body, timeout=60)
        r.raise_for_status()
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"].upper()
        if "LEFT" in text and "RIGHT" not in text:
            return -1
    except Exception as e:
        log(f"Could not check which way the creature faces ({e}); assuming right.")
    return 1


# ---------------------------------------------------------------- AI pictures
def cloudflare_image(prompt, dest, steps=6):
    """One AI picture from Cloudflare Workers AI (FLUX). Returns True on success.
    This model accepts only a prompt and a step count, and always makes a 1024x1024 square."""
    if not (CF_ACCOUNT and CF_TOKEN):
        return False
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/ai/run/{CF_MODEL}"
    headers = {"Authorization": f"Bearer {CF_TOKEN}"}
    for attempt in range(3):
        body = {"prompt": prompt[:1800], "steps": steps}
        try:
            r = requests.post(url, headers=headers, json=body, timeout=180)
        except Exception as e:
            log(f"Cloudflare image request failed: {e}")
            time.sleep(5)
            continue
        if r.status_code == 200:
            ctype = r.headers.get("content-type", "")
            if ctype.startswith("image/"):
                pathlib.Path(dest).write_bytes(r.content)
                return True
            try:
                b64 = longest_b64(r.json())
                if b64:
                    pathlib.Path(dest).write_bytes(base64.b64decode(b64))
                    return True
            except Exception as e:
                log(f"Could not read Cloudflare reply: {e}")
            log(f"Cloudflare reply had no picture: {r.text[:200]}")
            time.sleep(3)
            continue
        log(f"Cloudflare image -> {r.status_code}: {r.text[:300]}")
        if r.status_code in (401, 403):
            return False  # wrong token or account, retrying will not help
        time.sleep(15 if r.status_code == 429 else 3)
    return False


def fetch_ai_image(name, prompt, steps=6):
    """Download an AI picture into WORK with the right file ending. Returns the file name or None."""
    tmp = WORK / f"{name}.download"
    if not cloudflare_image(prompt, str(tmp), steps):
        return None
    head = tmp.read_bytes()[:16]
    if head[:4] == b"\x89PNG":
        ext = "png"
    elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        ext = "webp"
    else:
        ext = "jpg"
    raw = f"{name}_raw.{ext}"
    tmp.replace(WORK / raw)  # ffmpeg needs the right file ending to read it
    return raw


def compose_vertical(raw, out_png, cover=True):
    """Put a picture on a 1080x1920 canvas. cover=True fills the frame (crops the sides);
    cover=False keeps the whole picture on top of a blurred copy of itself."""
    if cover:
        fc = "[0:v]scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:1920,setsar=1"
    else:
        fc = ("[0:v]split[a][b];"
              "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=40:5[bg];"
              "[b]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
              "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1")
    run(["ffmpeg", "-y", "-i", raw, "-filter_complex", fc, "-frames:v", "1", out_png], cwd=WORK)


def make_picture(name, prompt):
    """Full-frame AI picture saved as work/<name>.png. Returns True on success."""
    raw = fetch_ai_image(name, prompt)
    if not raw:
        return False
    try:
        compose_vertical(raw, f"{name}.png")
        log(f"Picture '{name}': AI")
        return True
    except RuntimeError:
        log(f"Picture '{name}': AI image could not be read")
        return False


def gradient_plate(name, c0="0x203a43", c1="0x0f2027"):
    run(["ffmpeg", "-y", "-f", "lavfi", "-i",
         f"gradients=s=1080x1920:c0={c0}:c1={c1}:x0=0:y0=0:x1=0:y1=1920:d=1",
         "-frames:v", "1", f"{name}.png"], cwd=WORK)


def make_plate(name, desc):
    """Background picture (no creature). Falls back to a plain gradient so a scene always has one."""
    prompt = (f"{STYLE} Wide empty landscape, {desc}. Open ground in the lower half, "
              "no animals, no creatures, no people, nothing in the center.")
    if make_picture(name, prompt):
        return True
    log(f"Background '{name}': AI failed, using a plain gradient.")
    gradient_plate(name)
    return False


# ---------------------------------------------------------------- creature cutouts
def image_size(path):
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
               "stream=width,height", "-of", "csv=p=0", str(WORK / path)])
    w, h = out.strip().split(",")[:2]
    return int(w), int(h)


def backdrop_color(raw):
    """Average color of the four corners, as (hex, (r, g, b)). None if the corners do not match,
    which means the picture has no plain backdrop to cut out."""
    w, h = image_size(raw)
    cols = []
    for x, y in ((0, 0), (w - 24, 0), (0, h - 24), (w - 24, h - 24)):
        r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", raw, "-vf",
                            f"crop=24:24:{x}:{y},scale=1:1", "-frames:v", "1", "-f", "rawvideo",
                            "-pix_fmt", "rgb24", "-"], cwd=WORK, capture_output=True)
        if r.returncode != 0 or len(r.stdout) < 3:
            return None
        cols.append(tuple(r.stdout[:3]))
    spread = max(max(c[i] for c in cols) - min(c[i] for c in cols) for i in range(3))
    if spread > 70:
        return None
    rgb = tuple(sum(c[i] for c in cols) // 4 for i in range(3))
    return f"0x{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}", rgb


def key_sprite(raw, color_hex, despill, out_png, similarity=0.30):
    """Remove the plain backdrop. Returns how much of the picture is creature (percent)."""
    chain = f"[0:v]format=rgba,colorkey={color_hex}:{similarity}:0.12"
    if despill:
        chain += f",despill=type={despill}"
    # erode one pixel more than we grow back, so no colored fringe from the backdrop is left on the edge
    chain += (",split[c][d];[d]alphaextract,erosion,erosion,erosion,dilation,dilation[m];"
              "[c][m]alphamerge,format=rgba")
    run(["ffmpeg", "-y", "-i", raw, "-filter_complex", chain, "-frames:v", "1", out_png], cwd=WORK)
    text = run_capture(["ffmpeg", "-i", out_png, "-vf",
                        "alphaextract,signalstats,metadata=print:file=-", "-f", "null", "-"], cwd=WORK)
    m = re.search(r"lavfi\.signalstats\.YAVG=([\d.]+)", text)
    return float(m.group(1)) / 255 * 100 if m else 0.0


def sprite_bbox(png):
    text = run_capture(["ffmpeg", "-i", png, "-vf",
                        "alphaextract,bbox=min_val=60,metadata=print:file=-", "-f", "null", "-"], cwd=WORK)
    vals = {}
    for k in ("x1", "y1", "w", "h"):
        m = re.search(rf"lavfi\.bbox\.{k}=(\d+)", text)
        if not m:
            return None
        vals[k] = int(m.group(1))
    return vals


def make_sprite(name, desc, key, light, fierce=False):
    """A cut-out creature. Returns {'png','w','h','face'} or None if it did not work out."""
    color_words = KEYS[key][1]
    mood = f" It is {FIERCE}." if fierce else ""
    prompt = (f"{STYLE_SPRITE} {desc}.{mood} Lit by {light}. Full-body side view, walking pose, "
              f"facing right, whole body in frame, standing in front of a perfectly flat, evenly lit "
              f"{color_words} chroma key backdrop: one plain solid color, no gradient, no texture, "
              "no ground, no shadow, no other objects.")
    for attempt in range(2):
        tag = f"Sprite '{name}' attempt {attempt + 1}"
        raw = fetch_ai_image(f"{name}{attempt}", prompt, steps=8)
        if not raw:
            continue
        try:
            bd = backdrop_color(raw)
            if not bd:
                log(f"{tag}: the picture has no plain backdrop, rejected")
                continue
            color_hex, (r, g, b) = bd
            despill = "green" if g > r + 30 and g > b + 30 else ("blue" if b > r + 30 and b > g + 30 else None)
            cover = key_sprite(raw, color_hex, despill, f"{name}_k.png")
            if not 4 <= cover <= 70:
                log(f"{tag}: cut-out covers {cover:.0f}% of the picture (backdrop {color_hex}), rejected")
                continue
            bb = sprite_bbox(f"{name}_k.png")
            if not bb or bb["w"] < 120 or bb["h"] < 120:
                log(f"{tag}: could not find the creature outline")
                continue
            iw, ih = image_size(f"{name}_k.png")
            if bb["w"] > 0.97 * iw and bb["h"] > 0.97 * ih:
                log(f"{tag}: cut-out reaches every edge of the picture, so backdrop is left in, rejected")
                continue
            run(["ffmpeg", "-y", "-i", f"{name}_k.png", "-vf",
                 f"crop={bb['w']}:{bb['h']}:{bb['x1']}:{bb['y1']}", "-frames:v", "1", f"{name}_c.png"],
                cwd=WORK)
            face = facing_direction(raw)
            log(f"Sprite '{name}': AI cut-out {bb['w']}x{bb['h']}, backdrop {color_hex}, "
                f"faces {'right' if face == 1 else 'left'}")
            return {"png": f"{name}_c.png", "w": bb["w"], "h": bb["h"], "face": face}
        except RuntimeError:
            log(f"{tag}: cut-out failed")
    return None


# ---------------------------------------------------------------- backup footage (Pixabay)
def pixabay_clip(query, dest, exclude=None):
    """Free stock footage. `exclude` is a set of clip ids already used; the chosen id is added to it."""
    if not PIXABAY_API_KEY:
        return False
    exclude = exclude if exclude is not None else set()
    try:
        r = requests.get("https://pixabay.com/api/videos/",
                         params={"key": PIXABAY_API_KEY, "q": query[:90], "per_page": 20,
                                 "safesearch": "true"}, timeout=30)
        r.raise_for_status()
        hits = r.json().get("hits", [])
        random.shuffle(hits)
        for h in hits:
            if h.get("id") in exclude:
                continue
            for size in ("medium", "large", "small"):
                url = h.get("videos", {}).get(size, {}).get("url")
                if url:
                    with requests.get(url, stream=True, timeout=120) as resp:
                        resp.raise_for_status()
                        with open(dest, "wb") as f:
                            for chunk in resp.iter_content(1 << 20):
                                f.write(chunk)
                    exclude.add(h.get("id"))
                    return True
    except Exception as e:
        log(f"Pixabay backup for '{query}' failed: {e}")
    return False


def frame_from_clip(clip, out_png):
    for ss in ("0.5", "0"):
        try:
            run(["ffmpeg", "-y", "-ss", ss, "-i", clip, "-frames:v", "1", out_png], cwd=WORK)
            if (WORK / out_png).exists() and (WORK / out_png).stat().st_size > 0:
                return True
        except RuntimeError:
            pass
    return False


def get_animal(animal, name, key, light):
    """Everything needed to show one parent animal: a background plate and a walking sprite."""
    sprite = make_sprite(f"spr_{name}", f"A real {animal}", key, light)
    plate = f"plate_{name}.png"
    if sprite:
        make_plate(f"plate_{name}", f"the natural habitat of a {animal}")
        return {"plate": plate, "sprite": sprite}
    # Backup 1: a full-frame AI portrait, shown as a still
    if make_picture(f"port_{name}", f"{STYLE} A close-up portrait of a {animal} in its natural environment."):
        return {"plate": f"port_{name}.png", "sprite": None}
    # Backup 2: a frame of real stock footage
    if pixabay_clip(animal, str(WORK / f"{name}.mp4")) and frame_from_clip(f"{name}.mp4", f"{name}_f.png"):
        compose_vertical(f"{name}_f.png", f"port_{name}.png")
        return {"plate": f"port_{name}.png", "sprite": None}
    sys.exit(f"Could not get any picture of a {animal}.")


# ---------------------------------------------------------------- voice
def _save_audio(b64, mime, dest_wav):
    raw = base64.b64decode(b64)
    if raw[:4] == b"RIFF":
        pathlib.Path(dest_wav).write_bytes(raw)
        return
    rate = 24000
    m = re.search(r"rate=(\d+)", mime or "")
    if m:
        rate = int(m.group(1))
    with wave.open(str(dest_wav), "wb") as w:  # raw 16-bit mono PCM -> add a WAV header
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(raw)


_TTS_NO_META = set()


def gemini_tts(text, dest_wav, style=None, voice=None):
    """More natural voice from Gemini. Returns True on success."""
    headers = {"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}
    style = style or "an excited, curious nature-documentary narrator, warm and dramatic, natural pacing"
    attempts = []
    for model in TTS_MODELS:
        if (model, "meta") not in _TTS_NO_META:
            attempts.append((model, "interactions", True))
        attempts.append((model, "classic", True))                 # keeps the narrator style as plain text when speech metadata is refused
        attempts.append((model, "interactions", False))
    dead = set()  # (model, endpoint type) pairs that answered 404
    for model, style_kind, with_style in attempts:
        if (model, style_kind) in dead:
            continue
        if style_kind == "interactions":
            content = {"type": "text", "text": text}
            if with_style:
                content["annotations"] = [{"type": "speech_metadata", "style": style}]
            body = {
                "model": model,
                "input": [{"type": "user_input", "content": [content]}],
                "response_format": {"type": "audio"},
                "generation_config": {"speech_config": [{"voice": voice or GEMINI_VOICE}]},
            }
            url = f"{API_BASE}/interactions"
        else:
            body = {
                "contents": [{"parts": [{"text": f"Say as {style}: {text}"}]}],
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice or GEMINI_VOICE}}},
                },
            }
            url = f"{API_BASE}/models/{model}:generateContent"
        try:
            r = requests.post(url, headers=headers, json=body, timeout=180)
            if r.status_code != 200:
                log(f"Gemini voice {model} [{style_kind}] -> {r.status_code}: {r.text[:250]}")
                if r.status_code == 404:
                    dead.add((model, style_kind))
                if r.status_code == 400 and "metadata" in r.text.lower():
                    _TTS_NO_META.add((model, "meta"))             # remember: do not waste a request on this next time
                if r.status_code in (401, 403):
                    return False
                continue
            item = find_media(r.json(), "audio")
            if not item:
                log(f"Gemini voice {model} [{style_kind}]: no audio in reply")
                continue
            _save_audio(item["data"], item["mime"], dest_wav)
            log(f"Voice: Gemini {model} ({voice or GEMINI_VOICE})")
            return True
        except Exception as e:
            log(f"Gemini voice {model} [{style_kind}] failed: {e}")
    return False


async def edge_voice(text, mp3_path):
    comm = edge_tts.Communicate(text, EDGE_VOICE, rate="+0%", boundary="WordBoundary")
    words = []
    with open(mp3_path, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 1e7
                end = (chunk["offset"] + chunk["duration"]) / 1e7
                words.append((start, end, chunk["text"]))
    return words


def audio_seconds(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", str(path)])
    return float(out.strip())


# ---------------------------------------------------------------- sound effects
def freesound_fetch(queries, dest_base, min_d, max_d):
    """Download one CC0 sound from Freesound. Returns the saved file name or None."""
    if not FREESOUND_API_KEY:
        return None
    for q in queries:
        for flt in (f'license:"Creative Commons 0" duration:[{min_d} TO {max_d}]',
                    'license:"Creative Commons 0"'):  # never drop the license filter
            try:
                r = requests.get("https://freesound.org/apiv2/search/text/", params={
                    "query": q, "filter": flt, "fields": "id,name,duration,previews,license",
                    "page_size": 15, "token": FREESOUND_API_KEY}, timeout=30)
            except Exception as e:
                log(f"Freesound search '{q}' failed: {e}")
                break
            if r.status_code in (401, 403):
                log(f"Freesound refused the key ({r.status_code}); using synthetic sounds.")
                return None
            if r.status_code != 200:
                log(f"Freesound '{q}' -> {r.status_code}: {r.text[:200]}")
                continue
            results = [x for x in r.json().get("results", [])
                       if "zero" in str(x.get("license", "")).lower() or "cc0" in str(x.get("license", "")).lower()]
            random.shuffle(results)
            for x in results[:5]:
                previews = x.get("previews") or {}
                url = previews.get("preview-hq-mp3") or next(
                    (v for v in previews.values() if str(v).endswith(".mp3")), None)
                if not url:
                    continue
                try:
                    data = requests.get(url, timeout=60)
                    data.raise_for_status()
                except Exception as e:
                    log(f"Freesound download failed: {e}")
                    continue
                dest = f"{dest_base}.mp3"
                (WORK / dest).write_bytes(data.content)
                log(f"Sound '{q}': Freesound CC0 #{x.get('id')}")
                return dest
            break  # got a valid answer with no usable results, try the next query
    return None


def synth(kind, dest):
    """Generate a sound effect with ffmpeg. All of these are made from scratch."""
    L = "aevalsrc"
    if kind == "growl":  # a big animal's throat: low, rough, uneven, with a rattle and a hard-clipped edge
        fc = (f"{L}='st(0,2*PI*(58*t-0.9*t*t)+2.4*sin(2*PI*3.1*t)+0.7*sin(2*PI*17*t));"
              "(0.25+0.75*pow(sin(PI*t/1.9),0.6))*(0.8+0.2*sin(2*PI*14*t))*(0.5*sin(ld(0))+0.42*sin(2*ld(0))+0.36*sin(3*ld(0))"
              "+0.3*sin(4*ld(0))+0.25*sin(5*ld(0))+0.2*sin(6*ld(0))+0.14*sin(8*ld(0)))':d=1.9:s=44100[a];"
              "anoisesrc=d=1.9:c=brown:a=0.6,bandpass=f=420:width_type=h:w=700,tremolo=f=15:d=0.8[n];"
              "[a][n]amix=inputs=2:weights='1 0.5':normalize=0,volume=2.4,asoftclip=type=tanh,"
              "equalizer=f=500:t=q:w=1:g=4,equalizer=f=1100:t=q:w=1.2:g=3,lowpass=f=3800,"
              "aecho=0.8:0.85:60|130:0.3|0.18,afade=in:d=0.06,afade=out:st=1.4:d=0.5,alimiter=limit=0.9:level=0")
    elif kind == "roar":  # a long, falling roar with a ragged edge
        fc = (f"{L}='st(0,2*PI*(98*t-17*t*t)+3.0*sin(2*PI*5*t)+0.9*sin(2*PI*23*t));"
              "min(1,t/0.12)*pow(max(0,1-t/2.5),0.7)*(0.75+0.25*sin(2*PI*19*t))*(0.5*sin(ld(0))+0.5*sin(2*ld(0))+0.42*sin(3*ld(0))"
              "+0.36*sin(4*ld(0))+0.3*sin(5*ld(0))+0.26*sin(6*ld(0))+0.2*sin(7*ld(0))+0.16*sin(9*ld(0)))':d=2.5:s=44100[a];"
              "anoisesrc=d=2.5:c=pink:a=0.5,bandpass=f=1300:width_type=h:w=1800,tremolo=f=21:d=0.7[n];"
              "[a][n]amix=inputs=2:weights='1 0.45':normalize=0,volume=2.6,asoftclip=type=tanh,"
              "equalizer=f=420:t=q:w=1:g=4,equalizer=f=1500:t=q:w=1.2:g=3,lowpass=f=5200,"
              "aecho=0.8:0.85:90|210|380:0.35|0.22|0.12,afade=out:st=1.9:d=0.6,alimiter=limit=0.9:level=0")
    elif kind == "screech":  # something between a shriek and a tearing of metal
        fc = (f"{L}='st(0,2*PI*(820*t+420*t*t)+30*sin(2*PI*8*t)+8*sin(2*PI*41*t));"
              "min(1,t/0.05)*pow(max(0,1-t/1.7),0.6)*(0.55*sin(ld(0))+0.4*sin(2*ld(0))+0.3*sin(3*ld(0))+0.18*sin(5*ld(0)))':d=1.7:s=44100[a];"
              "anoisesrc=d=1.7:c=white:a=0.3,highpass=f=1800,tremolo=f=30:d=0.8[n];"
              "[a][n]amix=inputs=2:weights='1 0.5':normalize=0,volume=1.8,asoftclip=type=tanh,lowpass=f=9000,"
              "aecho=0.8:0.85:110|240:0.35|0.2,afade=out:st=1.2:d=0.5,alimiter=limit=0.9:level=0")
    elif kind == "rumble":  # the earth or the sea complaining
        fc = (f"{L}='0.7*sin(2*PI*36*t+3*sin(2*PI*0.7*t))*(0.4+0.6*sin(2*PI*5*t)*sin(2*PI*5*t))':d=3.0:s=44100[a];"
              "anoisesrc=d=3.0:c=brown:a=0.9,lowpass=f=110[n];[a][n]amix=inputs=2:normalize=0,volume=2.0,"
              "afade=in:d=0.8,afade=out:st=2.0:d=1.0,alimiter=limit=0.9:level=0")
    elif kind == "static":  # radio static with crackle and a falling carrier tone
        fc = ("anoisesrc=d=2.6:c=white:a=0.7,bandpass=f=2600:width_type=h:w=3800,tremolo=f=17:d=0.9,"
              "volume=8dB,afade=in:d=0.1,afade=out:st=1.8:d=0.8,alimiter=limit=0.9:level=0")
    elif kind == "splash":
        fc = ("anoisesrc=d=1.1:c=pink:a=0.8,bandpass=f=1400:width_type=h:w=2600,afade=in:d=0.02,afade=out:st=0.1:d=1.0,"
              "volume=16dB,aecho=0.7:0.6:70:0.25,alimiter=limit=0.9:level=0")
    elif kind == "gust":
        fc = ("anoisesrc=d=2.4:c=pink:a=0.9,bandpass=f=520:width_type=h:w=1000,tremolo=f=0.9:d=0.7,afade=in:d=0.9,afade=out:st=1.5:d=0.9,"
              "volume=13dB,alimiter=limit=0.9:level=0")
    elif kind == "crash":  # a wall of water arriving
        fc = ("anoisesrc=d=2.6:c=brown:a=1.0,lowpass=f=1800[a];anoisesrc=d=2.6:c=pink:a=0.6,highpass=f=400,lowpass=f=6000[b];"
              "[a][b]amix=inputs=2:normalize=0,volume=1.6,afade=in:d=0.7,afade=out:st=1.0:d=1.6,alimiter=limit=0.9:level=0")
    elif kind == "victim":
        # a human-like shriek: a rising voice with vibrato, harmonics and breath, cut off abruptly
        fc = (f"{L}='0.55*sin(2*PI*(520*t+380*t*t+14*sin(2*PI*7*t)))+0.3*sin(2*PI*(1040*t+760*t*t+28*sin(2*PI*7*t)))"
              f"+0.18*sin(2*PI*(1560*t+1140*t*t))':d=1.0:s=44100[a];"
              "anoisesrc=d=1.0:c=white:a=0.22,bandpass=f=2600:w=1500[n];[a][n]amix=inputs=2:normalize=0,"
              "acrusher=bits=7:mode=log:mix=0.2,afade=in:d=0.06,afade=out:st=0.86:d=0.04,alimiter=limit=0.9:level=0")
    elif kind == "distant":
        fc = (f"{L}='0.5*sin(2*PI*(430*t+300*t*t+10*sin(2*PI*6*t)))+0.25*sin(2*PI*(860*t+600*t*t))':d=1.6:s=44100[a];"
              "anoisesrc=d=1.6:c=pink:a=0.15[n];[a][n]amix=inputs=2:normalize=0,lowpass=f=2200,"
              "aecho=0.8:0.8:180|320:0.5|0.35,afade=in:d=0.1,afade=out:st=0.9:d=0.7,alimiter=limit=0.8:level=0")
    elif kind == "heart":
        fc = (f"{L}='0.9*sin(2*PI*52*t)*exp(-14*t)+0.6*sin(2*PI*48*(t-0.16))*exp(-16*(t-0.16))*gt(t,0.16)':d=0.5:s=44100[a];"
              "[a]lowpass=f=140,alimiter=limit=0.9:level=0")
    elif kind == "whoosh":
        fc = ("anoisesrc=d=0.9:c=pink:a=0.7,highpass=f=300,lowpass=f=5000,"
              "afade=in:d=0.4,afade=out:st=0.4:d=0.5,alimiter=limit=0.9:level=0")
    elif kind == "boom":
        fc = (f"{L}='0.9*sin(2*PI*55*t)*exp(-3.5*t)':d=1.3:s=44100[a];"
              "anoisesrc=d=1.3:c=brown:a=0.8,lowpass=f=300,afade=out:st=0.0:d=0.5[n];"
              "[a][n]amix=inputs=2:normalize=0,alimiter=limit=0.9:level=0")
    elif kind == "riser":
        fc = (f"{L}='0.35*sin(2*PI*(220*t+500*t*t))':d=1.4:s=44100[a];"
              "anoisesrc=d=1.4:c=pink:a=0.5,highpass=f=800[n];[a][n]amix=inputs=2:normalize=0,"
              "afade=in:d=1.2,afade=out:st=1.3:d=0.1,alimiter=limit=0.9:level=0")
    elif kind == "eerie":
        fc = (f"{L}='0.32*sin(2*PI*(430*t-60*t*t))+0.3*sin(2*PI*(455*t-65*t*t))+0.22*sin(2*PI*95*t)':d=1.8:s=44100[a];"
              "anoisesrc=d=1.8:c=pink:a=0.25,bandpass=f=1800:w=700[n];[a][n]amix=inputs=2:normalize=0,"
              "tremolo=f=6:d=0.5,aecho=0.8:0.8:90:0.4,afade=in:d=0.25,afade=out:st=1.1:d=0.7,"
              "alimiter=limit=0.9:level=0")
    elif kind == "tap":  # a knife-tip knocking on a wooden floor
        fc = (f"{L}='0.9*sin(2*PI*190*t)*exp(-38*t)+0.45*sin(2*PI*430*t)*exp(-70*t)+0.25*sin(2*PI*900*t)*exp(-110*t)':d=0.4:s=44100[a];"
              "[a]lowpass=f=2400,aecho=0.7:0.6:45:0.3,alimiter=limit=0.9:level=0")
    elif kind == "scrape":  # claws tearing into earth
        fc = ("anoisesrc=d=0.6:c=brown:a=0.9:s=44100[a];[a]highpass=f=180,lowpass=f=2600,tremolo=f=22:d=0.7,"
              "afade=in:d=0.04,afade=out:st=0.25:d=0.35,alimiter=limit=0.9:level=0")
    elif kind == "flap":  # one heavy wing beat
        fc = (f"{L}='0.9*sin(2*PI*58*t)*exp(-9*t)+0.35*sin(2*PI*110*t)*exp(-14*t)':d=0.55:s=44100[a];"
              "[a]lowpass=f=900,alimiter=limit=0.9:level=0")
    elif kind == "stab":  # a claw or fang punching through skin: a wet thud with a short tearing hiss
        fc = (f"{L}='0.95*sin(2*PI*88*t)*exp(-26*t)+0.5*sin(2*PI*205*t)*exp(-42*t)':d=0.4:s=44100[a];"
              "anoisesrc=d=0.4:c=pink:a=0.8:s=44100[n];[n]highpass=f=450,lowpass=f=4800,afade=out:st=0:d=0.22[nn];"
              "[a][nn]amix=inputs=2:weights='1 0.55':normalize=0,lowpass=f=5200,alimiter=limit=0.9:level=0")
    elif kind == "rip":  # flesh and hide tearing under a dragged claw
        fc = ("anoisesrc=d=1.0:c=pink:a=0.9:s=44100[a];[a]highpass=f=260,lowpass=f=3600,tremolo=f=38:d=0.85,"
              "equalizer=f=1200:t=q:w=1:g=4,afade=in:d=0.04,afade=out:st=0.55:d=0.45,alimiter=limit=0.9:level=0")
    elif kind == "crunch":  # fangs closing through gristle and bone: a few hard clicks and a low squeeze
        fc = (f"{L}='0.9*sin(2*PI*150*t)*exp(-34*t)+0.8*sin(2*PI*210*(t-0.11))*exp(-60*(t-0.11))*gte(t,0.11)"
              "+0.7*sin(2*PI*130*(t-0.23))*exp(-55*(t-0.23))*gte(t,0.23)+0.4*sin(2*PI*60*t)*exp(-6*t)':d=0.7:s=44100[a];"
              "[a]lowpass=f=2600,aecho=0.7:0.5:40:0.25,alimiter=limit=0.9:level=0")
    elif kind == "creak":  # an old stair taking weight
        fc = (f"{L}='0.35*sin(2*PI*(230-90*t)*t)+0.22*sin(2*PI*(470-170*t)*t)+0.12*sin(2*PI*(910-300*t)*t)':d=0.7:s=44100[a];"
              "[a]tremolo=f=34:d=0.85,lowpass=f=1800,afade=in:d=0.05,afade=out:st=0.4:d=0.3,alimiter=limit=0.9:level=0")
    elif kind == "ambient":
        fc = ("anoisesrc=d=12:c=brown:a=0.6,lowpass=f=700,tremolo=f=0.25:d=0.6,"
              "afade=in:d=1,afade=out:st=11:d=1")
    else:
        raise ValueError(kind)
    # Every chain starts with a source filter, so label the end of it as the output.
    run(["ffmpeg", "-y", "-filter_complex", fc + "[out]", "-map", "[out]", "-ac", "2", dest], cwd=WORK)


def make_scream(call_a, call_b, dest):
    """Layer both parents' calls (one pitched down, one up) with a screech, then distort."""
    synth("screech", "scr_synth.wav")
    ins = ["-i", "scr_synth.wav"]
    if not call_a:
        synth("roar", "g_a.wav")
    if not call_b:
        synth("growl", "g_b.wav")
    ca = call_a or "g_a.wav"
    cb = call_b or "g_b.wav"
    ins += ["-i", ca, "-i", cb]
    fc = ("[0:a]aresample=44100,aformat=channel_layouts=stereo[s];"
          "[1:a]aresample=44100,asetrate=38000,aresample=44100,atrim=0:2.4,aformat=channel_layouts=stereo[ca];"
          "[2:a]aresample=44100,asetrate=48000,aresample=44100,atrim=0:1.8,aformat=channel_layouts=stereo[cb];"
          "[s][ca][cb]amix=inputs=3:normalize=0:weights='0.8 1 0.6',asoftclip=type=tanh,"
          "aecho=0.8:0.85:100|230:0.35|0.2,alimiter=limit=0.95:level=0[out]")
    run(["ffmpeg", "-y"] + ins + ["-filter_complex", fc, "-map", "[out]", dest], cwd=WORK)


def prepare_sounds(a, b, ambient_query):
    """Returns a dict of sound file names inside WORK."""
    s = {}
    for kind in ("whoosh", "boom", "riser", "growl", "eerie", "victim", "distant", "heart", "tap", "creak", "scrape", "flap", "roar", "rumble", "splash", "gust", "crash", "static", "stab", "rip", "crunch"):
        synth(kind, f"{kind}.wav")
        s[kind] = f"{kind}.wav"
    queries = lambda x: [f"{x} sound", f"{x} call", f"{x} roar"]
    ca = freesound_fetch(queries(a), "call_a", 0.5, 8)
    cb = freesound_fetch(queries(b), "call_b", 0.5, 8)
    if not ca:
        synth("growl", "call_a.wav")
    if not cb:
        synth("growl", "call_b.wav")
    s["call_a"] = ca or "call_a.wav"
    s["call_b"] = cb or "call_b.wav"
    make_scream(ca, cb, "scream.wav")
    s["scream"] = "scream.wav"
    amb = freesound_fetch([ambient_query], "ambient", 8, 90)
    if not amb:
        synth("ambient", "ambient.wav")
    s["ambient"] = amb or "ambient.wav"
    s["real_calls"] = bool(ca) + bool(cb)
    return s


def find_music_track():
    """Find an optional user-supplied, licensed soundtrack. No track is bundled."""
    configured = os.environ.get("MUSIC_FILE", "").strip()
    candidates = [pathlib.Path(configured)] if configured else []
    music_dir = ROOT / "music"
    if music_dir.exists():
        candidates.extend(sorted(
            p for p in music_dir.iterdir()
            if p.is_file() and p.suffix.lower() in {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac"}
        ))
    for candidate in candidates:
        path = candidate if candidate.is_absolute() else ROOT / candidate
        if path.is_file():
            return str(path.resolve())
    return None


# ---------------------------------------------------------------- original cinematic score (synthesized, so no licence problems)
SCORE_MOODS = {  # root note (Hz), chord (semitones above root), pulse speed (beats per minute at medium intensity), percussion style
    "legend": dict(root=73.42, chord=(0, 3, 7, 8), bpm=52, drums="heart"),        # D minor with a cold flat sixth
    "modern": dict(root=55.0, chord=(0, 1, 7), bpm=58, drums="heart"),            # A with a minor second: uneasy, close, quiet
    "prehistoric": dict(root=65.41, chord=(0, 3, 7, 10), bpm=68, drums="tribal"),  # C minor, heavy low drums
    "nature": dict(root=46.25, chord=(0, 7, 12), bpm=44, drums="swell"),          # a very deep drone that breathes like the sea
}
BEAT_INTENSITY = {
    "omen": .30, "walk": .35, "home": .30, "village": .35, "valley": .30, "hide": .40, "footprints": .35, "bedroom": .40,
    "stairs": .50, "basement": .55, "cctv": .50, "neighbour": .45, "woods": .50, "road": .55, "stalk": .55, "lamp": .55,
    "pov": .50, "mpov": .50, "dig": .45, "fly": .50, "ocean": .50, "lake": .45, "erupt": .75, "hallway": .65, "curtain": .60,
    "window": .60, "loom": .70, "shoulder": .60, "mirror": .65, "birth": .80, "tsunami": .85, "storm": .80, "lava": .70,
    "giant": .85, "jump": .90, "attack": .90, "chase": .90, "clash": .95, "transform": .80, "mob": .70, "aftermath": .40,
    "reign": .45, "hook": .45, "door": .50,
    "eyes": .60, "notice": .55, "lantern": .45, "establish": .32}
REVEAL_AT = {  # how far into the scene the monster is shown (fraction of the scene)
    "hallway": .72, "woods": .78, "road": .75, "jump": .72, "birth": .45, "loom": .70, "curtain": .70, "window": .70,
    "giant": .40, "tsunami": .55, "ocean": .56, "lake": .56, "lava": .50, "erupt": .55, "clash": .30, "mirror": .60,
    "lamp": .70, "transform": .50}


def make_score(total, plan, out_path):
    """Builds a dark, original film score for this exact story. plan = {"mood", "scenes": [(start, dur, intensity)],
    "reveals": [(time, strength)]}. The music swells with the scene intensity, rises into every reveal, drops almost to
    silence for a moment, hits, and then settles; the last seconds resolve into a low fading chord."""
    import numpy as np
    sr = 44100
    n = int(total * sr)
    t = np.arange(n) / sr
    mood = SCORE_MOODS.get(plan.get("mood"), SCORE_MOODS["legend"])
    rng = np.random.default_rng(7)

    def smooth(x, sec):
        k = max(int(sec * sr), 1)
        pad = np.concatenate([np.full(k // 2, x[0]), x, np.full(k - k // 2, x[-1])])
        c = np.cumsum(np.insert(pad, 0, 0.0))
        return (c[k:k + n] - c[:n]) / k

    def lowpass(x, fc):
        f = np.fft.rfft(x)
        fr = np.fft.rfftfreq(len(x), 1 / sr)
        return np.fft.irfft(f / (1 + (fr / fc) ** 4), len(x))

    # intensity curve through the scene centres, rising slowly over the video
    cen = [s + d / 2 for s, d, _ in plan["scenes"]]
    val = [i for _, _, i in plan["scenes"]]
    inten = np.interp(t, cen, val)
    inten = smooth(inten, 1.6) * (0.82 + 0.18 * np.clip(t / max(total * 0.7, 1), 0, 1))
    end_t = total - 2.4  # the last seconds resolve
    inten = inten * np.where(t > end_t, 1 - 0.55 * np.clip((t - end_t) / 2.4, 0, 1), 1.0)
    root = mood["root"]

    # 1) drone: three slightly detuned voices, brighter when the intensity is higher
    cut = 160 + 900 * inten
    drone = np.zeros(n)
    for f0, amp in ((root, 1.0), (root * 1.5, 0.5), (root * 2 * 1.004, 0.35)):
        for k in range(1, 11):
            drone += amp * np.sin(2 * np.pi * k * f0 * t + k) / k * np.clip(1 - k * f0 / cut, 0, 1) ** 1.5
    drone *= 0.22 + 0.78 * inten
    if mood["drums"] == "swell":
        drone *= 0.75 + 0.25 * np.sin(2 * np.pi * 0.09 * t)

    # 2) pad: the chord, a little wider left and right
    padL, padR = np.zeros(n), np.zeros(n)
    for j, iv in enumerate(mood["chord"]):
        f0 = root * 2 * 2 ** (iv / 12)
        trem = 0.75 + 0.25 * np.sin(2 * np.pi * (0.11 + 0.03 * j) * t + j)
        padL += (np.sin(2 * np.pi * f0 * 1.003 * t) + 0.4 * np.sin(4 * np.pi * f0 * t)) * trem
        padR += (np.sin(2 * np.pi * f0 / 1.003 * t + 1) + 0.4 * np.sin(4 * np.pi * f0 * t + 2)) * trem
    gain = (0.05 + 0.34 * inten ** 1.3) / max(len(mood["chord"]), 1)
    padL, padR = padL * gain, padR * gain

    # 3) percussion: a pulse that speeds up and hardens with the intensity; nothing at all while it is quiet
    perc = np.zeros(n)
    soft_noise = lowpass(rng.standard_normal(int(0.9 * sr)), 700) * 3.0  # the 'skin' of a drum: dull, never a sharp tick
    def thump(at, amp, f_hi=95, f_lo=42, decay=5.0, click=0.25):
        i0 = int(at * sr)
        m = int(0.9 * sr)
        if i0 >= n:
            return
        tau = np.arange(min(m, n - i0)) / sr
        ph = 2 * np.pi * np.cumsum(f_lo + (f_hi - f_lo) * np.exp(-tau * 22)) / sr
        s = np.sin(ph) * np.exp(-tau * decay) + click * soft_noise[:len(tau)] * np.exp(-tau * 60)
        perc[i0:i0 + len(tau)] += amp * s
    tt = 0.6
    while tt < total - 2.0:
        iv = float(np.interp(tt, t, inten))
        if iv > 0.38:
            a = (iv ** 1.6) * 0.9
            if mood["drums"] == "tribal":
                thump(tt, a * 1.1, 110, 48, 4.0)
                if iv > 0.7:
                    thump(tt + 0.5 * 60 / (mood["bpm"] * (0.8 + 0.7 * iv)), a * 0.55, 90, 40, 6.0)
            elif mood["drums"] == "swell":
                thump(tt, a * 0.8, 70, 32, 2.6, 0.0)
            else:  # a heartbeat: two beats close together
                thump(tt, a, 90, 42, 6.0)
                thump(tt + 0.27, a * 0.6, 80, 40, 7.0)
            if iv > 0.78:
                thump(tt + 30 / (mood["bpm"] * (0.8 + 0.7 * iv)), a * 0.35, 120, 60, 9.0)
        tt += 60.0 / (mood["bpm"] * (0.8 + 0.7 * iv))

    # 4) reveals: riser -> a moment of near silence -> impact with a long tail
    hit = np.zeros(n)
    dip = np.ones(n)
    for r, strength in plan["reveals"]:
        if r < 1.0 or r > total - 0.5:
            continue
        rl = min(2.2, r - 0.2)
        i0 = int((r - rl) * sr)
        i1 = int(r * sr)
        tau = np.arange(i1 - i0) / sr
        ramp = (tau / rl) ** 2
        sweep = np.sin(2 * np.pi * np.cumsum(80 + 650 * (tau / rl) ** 1.6) / sr)
        nz = lowpass(rng.standard_normal(i1 - i0), 2500)
        hit[i0:i1] += strength * ramp * (0.30 * sweep + 0.22 * nz)
        dm = np.clip((t - (r - 0.32)) / 0.1, 0, 1) * (1 - np.clip((t - r) / 0.05, 0, 1))
        dip *= 1 - 0.85 * dm  # the whole bed drops away for a heartbeat before the hit
        m = min(int(3.2 * sr), n - i1)
        if m > 100:
            tau = np.arange(m) / sr
            sub = np.sin(2 * np.pi * np.cumsum(28 + 40 * np.exp(-tau * 6)) / sr) * np.exp(-tau * 1.5)
            burst = lowpass(rng.standard_normal(m), 1400) * np.exp(-tau * 7)
            ring = sum(np.sin(2 * np.pi * f * tau) * np.exp(-tau * d_) for f, d_ in ((181, 1.6), (273, 2.0), (409, 2.6)))
            hit[i1:i1 + m] += strength * (1.0 * sub + 0.65 * burst + 0.07 * ring)

    # a little room around the hits and the pad
    ir_len = int(1.9 * sr)
    tau = np.arange(ir_len) / sr
    size = 1 << int(np.ceil(np.log2(n + ir_len)))
    def reverb(x, seed):
        ir = np.random.default_rng(seed).standard_normal(ir_len) * np.exp(-tau * 2.6)
        return np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir / np.sqrt(np.sum(ir ** 2)), size), size)[:n]
    wet = reverb(hit, 3)
    L = (drone + padL + perc) * dip + hit * 0.8 + wet * 0.35
    R = (drone * 0.97 + padR + perc) * dip + hit * 0.8 + reverb(hit, 5) * 0.35
    # the very end: a low chord that fades away
    tail = np.clip((t - (total - 3.0)) / 1.0, 0, 1) * np.clip((total - t) / 2.6, 0, 1)
    low = 0.10 * (np.sin(2 * np.pi * root * t) + 0.5 * np.sin(2 * np.pi * root * 1.5 * t)) * tail
    L, R = L + low, R + low
    st = np.stack([L, R], 1)
    peak = np.max(np.abs(st)) or 1.0
    st = np.tanh(st / peak * 1.4) / np.tanh(1.4) * 0.8
    pcm = (st * 32767).astype("<i2")
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return str(out_path)


def ramp_score(total):
    """A gentle score for the formats without timed reveals: it just grows as the video goes on."""
    return {"mood": "nature", "reveals": [], "scenes": [(0, total * 0.3, 0.30), (total * 0.3, total * 0.4, 0.55), (total * 0.7, total * 0.3, 0.75)]}


def score_plan(story, beats, durs, impacts):
    """The score's map of this story: a mood for the genre, an intensity per scene and the moments of the reveals."""
    stype = story.get("story_type")
    if stype in NATURE_TYPES:
        mood = "nature"
    else:
        mood = {"modern": "modern", "prehistoric": "prehistoric"}.get(story.get("format"), "legend")
    scenes, reveals, at = [], [], 0.0
    for i, (b, d) in enumerate(zip(beats, durs)):
        iv = BEAT_INTENSITY.get(b, 0.45)
        if i == 0:
            iv = min(iv, 0.35)  # the hook is quiet and curious
        if i == len(beats) - 1:
            iv *= 0.7
        scenes.append((at, d, iv))
        if b == "attack":
            reveals.append((at + impacts.get(i, d * 0.6), 1.0))
        elif b in REVEAL_AT and i > 0:
            reveals.append((at + d * REVEAL_AT[b], 0.6 + 0.4 * iv))
        at += d
    reveals = sorted(reveals, key=lambda x: -x[1])[:4]  # only the strongest reveals get a hit, so they still mean something
    kept = []
    for r in sorted(reveals):
        if not kept or r[0] - kept[-1][0] > 5.0:
            kept.append(r)
    return {"mood": mood, "scenes": scenes, "reveals": kept}


_STAT_CACHE = {}


def sound_stats(path):
    """(mean dB, peak dB) of a sound file, measured once. Used to set every effect to a fixed distance below the voice."""
    key = str(path)
    if key not in _STAT_CACHE:
        out = run_capture(["ffmpeg", "-hide_banner", "-nostats", "-i", key, "-af", "volumedetect", "-f", "null", "-"], cwd=WORK)
        mean = re.search(r"mean_volume:\s*(-?[\d.]+) dB", out)
        peak = re.search(r"max_volume:\s*(-?[\d.]+) dB", out)
        _STAT_CACHE[key] = (float(mean.group(1)) if mean else -30.0, float(peak.group(1)) if peak else -6.0)
    return _STAT_CACHE[key]


# Where everything sits, measured against the narrator (about -16 LUFS, speech peaks near -3 dBFS):
#   ordinary effects peak near -20 dBFS (12-18 dB under the voice), big impacts near -13 dBFS (8-12 dB under),
#   ambience about -38 dB RMS, music about -28 dB RMS (and ducked a further ~8 dB whenever the narrator speaks).
SFX_PEAK_DB, SFX_MEAN_DB = -24.0, -31.0          # ordinary effects: about 12-18 dB under the voice
MAJOR_PEAK_DB, MAJOR_MEAN_DB = -13.0, -28.0      # big impacts and screams: about 8-12 dB under, and only briefly
MAJOR_SOUNDS = ("boom", "scream", "victim", "crash", "roar", "stab", "crunch")
AMBIENT_MEAN_DB = -38.0
MUSIC_MEAN_DB = -31.0


DEEPEN = ""   # no artificial pitch change: the natural voice is kept as it is


def voice_chain():
    """The one filter chain the narrator goes through: gentle compression so every word is clear, levelled to a steady -16 LUFS."""
    fmt = "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo"
    return (f"aresample=44100,{DEEPEN}{fmt},highpass=f=70,bass=g=2.5:f=140,"
            "acompressor=threshold=0.125:ratio=2.2:attack=10:release=160:makeup=1.5,"
            f"loudnorm=I=-16:TP=-2.5:LRA=7,aresample=44100,{fmt}")


def _build_audio_once(voice_name, total, events, ambient, out_wav, quiet=None, score=None, trim_db=0.0, only=None):
    """Narration-first mix. The voice is levelled, then effects, ambience and music are each set to a measured distance
    below it and ducked while it speaks. Music is the user's own track if supplied, otherwise the generated score."""
    music = find_music_track()
    generated = False
    if not music and score:
        try:
            music = make_score(total, score, WORK / "score.wav")
            generated = True
        except Exception as e:  # music is a bonus: never lose the video because of it
            log(f"Score failed ({e}); continuing without it.")
            music = None
    cmd = ["ffmpeg", "-y", "-i", voice_name, "-stream_loop", "-1", "-i", ambient]
    fmt = "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo"
    def gate_expr(depth):   # smooth: fades down over 0.25 s into each held silence and snaps back as the reveal lands
        return "".join(f",volume='1-{depth}*clip(min((t-{a:.2f})/0.25,({b:.2f}-t)/0.06),0,1)':eval=frame" for a, b in (quiet or []))
    gate = gate_expr(1.0)
    # one copy of the voice for each thing that must duck under it (ambience always; effects and music only if present)
    key_names = ["vk0"] + (["vk1"] if events else []) + (["vk2"] if music else [])
    nkeys = len(key_names)
    keys = "".join(f"[{k}]" for k in key_names)
    # the voice: gentle compression so every word is clear, levelled to a steady -16 LUFS, no clipping
    parts = [f"[0:a]{voice_chain()},asplit={1 + nkeys}[v]{keys}"]
    a_mean, _ = sound_stats(ambient)
    amb_db = AMBIENT_MEAN_DB - a_mean
    parts.append(f"[1:a]aresample=44100,{fmt},atrim=0:{total + 1:.2f},volume={amb_db:.1f}dB{gate}[ambraw]")
    parts.append("[ambraw][vk0]sidechaincompress=threshold=0.02:ratio=2.5:attack=30:release=500[amb]")
    labels = ["[v]", "[amb]"]
    ev_labels = []
    for i, ev in enumerate(events):
        cmd += ["-i", ev["file"]]
        ms = max(int(ev["at"] * 1000), 0)
        cut = f",atrim=0:{ev['max']}" if ev.get("max") else ""
        mean, peak = sound_stats(ev["file"])
        major = pathlib.Path(str(ev["file"])).stem.split("_")[0] in MAJOR_SOUNDS
        rel = 20 * math.log10(max(min(ev["vol"], 1.0), 0.05))
        # dense sounds (screams, growls) are far louder than their peaks suggest, so use the quieter of two rules
        gain = min((MAJOR_PEAK_DB if major else SFX_PEAK_DB) + rel - peak,
                   (MAJOR_MEAN_DB if major else SFX_MEAN_DB) + rel - mean)
        gain = max(min(gain, 24.0), -45.0)
        parts.append(f"[{i + 2}:a]aresample=44100,{fmt}{cut},asetpts=PTS-STARTPTS,"
                     f"volume={gain:.1f}dB,adelay={ms}|{ms}[e{i}]")
        ev_labels.append(f"[e{i}]")
    if ev_labels:
        parts.append("".join(ev_labels) + f"amix=inputs={len(ev_labels)}:duration=longest:normalize=0,"
                     "alimiter=limit=0.35:level=0[sfxraw]")
        parts.append("[sfxraw][vk1]sidechaincompress=threshold=0.02:ratio=3:attack=15:release=350[sfx]")
        labels.append("[sfx]")
    if music:
        music_idx = len(events) + 2
        cmd += ["-stream_loop", "-1", "-i", music]
        m_mean, _ = sound_stats(music)
        base_db = MUSIC_MEAN_DB - m_mean
        intro_end = max(total * 0.20, 0.5)
        build_end = max(total * 0.65, intro_end + 0.5)
        peak_end = max(total * 0.85, build_end + 0.25)
        if generated:  # the score already follows the story; it only needs a level, a dip at the silent moments and fades
            music_filter = (f"aresample=44100,{fmt},atrim=0:{total + 0.25:.2f},volume={base_db:.1f}dB"
                            + gate_expr(0.88)
                            + f",afade=t=in:st=0:d=1.5,afade=t=out:st={max(total - 2.2, 0):.2f}:d=2.2[musicraw]")
        else:
            music_filter = (f"aresample=44100,{fmt},atrim=0:{total + 0.25:.2f},volume={base_db:.1f}dB,"
                            f"volume='if(lt(t,{intro_end:.2f}),0.6,if(lt(t,{build_end:.2f}),0.85,if(lt(t,{peak_end:.2f}),1.15,0.7)))':eval=frame,"
                            f"afade=t=in:st=0:d=1,afade=t=out:st={max(total - 2.0, 0):.2f}:d=2[musicraw]")
        parts.append(f"[{music_idx}:a]{music_filter}")
        # ducking: down about 8 dB under speech, back up smoothly in the pauses
        parts.append("[musicraw][vk2]sidechaincompress=threshold=0.015:ratio=4:attack=60:release=900:makeup=1[duckedmusic]")
        labels.append("[duckedmusic]")
    if trim_db:                     # everything except the voice, turned down together (the voice-dominance guard)
        tl = ["[v]"]
        for i_, l_ in enumerate(labels[1:]):
            parts.append(f"{l_}volume={trim_db:.1f}dB[tb{i_}]")
            tl.append(f"[tb{i_}]")
        labels = tl
    if only == "bed":
        parts.append("[v]anullsink")
        labels = labels[1:]
    parts.append("".join(labels) + f"amix=inputs={len(labels)}:duration=first:normalize=0:"
                 "dropout_transition=0,alimiter=limit=0.89:level=0[mix]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[mix]", out_wav]
    if music:
        log("Background score: original score generated for this story" if generated else f"Background score: adaptive mix enabled ({pathlib.Path(music).name})")
    else:
        log("No background score found; using narration, ambience, and sound effects only. "
            "Add a licensed track to music/ or set MUSIC_FILE.")
    run(cmd, cwd=WORK)


VOICE_MEDIAN_DB, VOICE_P10_DB = 12.0, 7.0        # while the narrator speaks, the voice must stand at least this far above everything else
AUDIO_REPORT = {}


def _pcm(path, chain=""):
    import numpy as np
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-af", (chain + "," if chain else "") + "aresample=22050,aformat=channel_layouts=mono",
                        "-f", "f32le", "-"], capture_output=True, cwd=WORK)
    return np.frombuffer(r.stdout, dtype=np.float32)


def voice_dominance(voice_wav, bed_wav):
    """How far the narrator stands above the music, ambience and effects while the narrator is speaking, measured from the
    two separate tracks. Returns (median dB, 10th percentile dB, speech windows measured)."""
    import numpy as np
    v, x = _pcm(pathlib.Path(WORK) / voice_wav), _pcm(pathlib.Path(WORK) / bed_wav)
    n = min(len(v), len(x))
    win = 22050 // 4
    vp = np.array([float(np.mean(v[i:i + win] ** 2)) for i in range(0, n - win, win)])
    bp = np.array([float(np.mean(x[i:i + win] ** 2)) for i in range(0, n - win, win)])
    if not len(vp):
        return 99.0, 99.0, 0
    speech = vp > vp.max() * 10 ** (-2.5)                  # within 25 dB of the loudest word
    if not speech.any():
        return 99.0, 99.0, 0
    d = 10 * np.log10(vp[speech] / np.maximum(bp[speech], 1e-12))
    return float(np.median(d)), float(np.percentile(d, 10)), int(speech.sum())


def build_audio(voice_name, total, events, ambient, out_wav, quiet=None, score=None):
    """The mix, followed by a measured check that the narrator is never overwhelmed. If the music, ambience and effects
    stand too close to the voice while it speaks, they are turned down together and the mix is made again."""
    trim = 0.0
    try:
        run(["ffmpeg", "-y", "-i", voice_name, "-af", voice_chain(), "stem_voice.wav"], cwd=WORK)
    except Exception as e:
        log(f"Voice-dominance check could not run ({e}); making the mix without it.")
        _build_audio_once(voice_name, total, events, ambient, out_wav, quiet, score, 0.0)
        return
    for attempt in range(3):
        _build_audio_once(voice_name, total, events, ambient, out_wav, list(quiet) if quiet else quiet, score, trim)
        try:
            _build_audio_once(voice_name, total, events, ambient, "stem_bed.wav", list(quiet) if quiet else quiet, score, trim, only="bed")
            med, p10, n = voice_dominance("stem_voice.wav", "stem_bed.wav")
        except Exception as e:
            log(f"Voice-dominance check could not run ({e}); keeping the mix as it is.")
            return
        AUDIO_REPORT.update({"median_db": round(med, 1), "p10_db": round(p10, 1), "trim_db": trim, "windows": n})
        if med >= VOICE_MEDIAN_DB and p10 >= VOICE_P10_DB:
            log(f"Voice dominance OK: the narrator is {med:.1f} dB above everything else (weakest moments {p10:.1f} dB), bed trimmed {trim:.1f} dB.")
            return
        need = max(VOICE_MEDIAN_DB - med, VOICE_P10_DB - p10)
        trim -= min(max(need + 0.5, 1.5), 9.0)
        log(f"Voice dominance too thin (median {med:.1f} dB, weakest {p10:.1f} dB): turning music/ambience/effects down to {trim:.1f} dB and mixing again.")
    _build_audio_once(voice_name, total, events, ambient, out_wav, list(quiet) if quiet else quiet, score, trim)
    log("Voice dominance: still low after 3 mixes; using the quietest one.")


# ---------------------------------------------------------------- captions
def ass_time(t):
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def even_words(text, total):
    """Estimated word timings (used when the voice gives none). Longer words get more time."""
    toks = text.split()
    weights = [len(t) + 2 for t in toks]
    scale = total / max(sum(weights), 1)
    out, t = [], 0.0
    for tok, w in zip(toks, weights):
        out.append((t, t + w * scale, tok))
        t += w * scale
    return out


CAPTION_SPOTS = (1400, 1540, 1575, 480, 400, 640, 880, 1120)   # where the bottom edge of a caption can sit, in order of preference
CAPTION_X0, CAPTION_X1 = 150, 930                    # the caption never leaves this band (clear of the right-hand Shorts buttons)
SAFE_TOP, SAFE_BOTTOM = 220, 1580                    # mobile interface safe zone for caption boxes
CAPTION_OK = 0.02
CAPTION_TOLERATED = 0.30                                    # a caption may touch at most 2% of its own box with the creature
CAPTION_REPORT = []


def _cap_dims(text_len, long_line):
    """How big a caption really is on screen: (width, height) in pixels."""
    cw = text_len * (41 if long_line else 50) + 30
    if cw <= CAPTION_X1 - CAPTION_X0:
        return cw, (118 if long_line else 140)
    return CAPTION_X1 - CAPTION_X0, (230 if long_line else 270)


def _cap_overlap(track, t0, t1, bottom, dims=None):
    """How much of the caption's box the creature covers at its worst moment in [t0, t1] (0..1)."""
    worst = 0.0
    cw, ch = dims or (CAPTION_X1 - CAPTION_X0, 190)
    top = bottom - ch
    cx0, cx1 = 540 - cw / 2 - 12, 540 + cw / 2 + 12
    area = max((cx1 - cx0) * ch, 1)
    for t, box in track:
        if t < t0 - 0.1 or t > t1 + 0.1 or not box:
            continue
        tot = 0.0
        for bx in [box]:      # the union of everything protected in the frame (the separate parts are also kept in box.parts, but using them was stricter than the tuned rules)
            # the video is zoomed 5% and shifted a little before the subtitles are drawn
            bh = bx[3] - bx[1]
            if bh > 650:      # a subject this big fills the frame: its protected part is the head, chest and action; the lowest 28% (legs, feet, ground contact) is not
                bx = (bx[0], bx[1], bx[2], bx[1] + bh * (0.5 if bh > 1000 else 0.72))      # a herd of giants: only the heads and chests are protected
            x0, y0, x1, y1 = bx[0] * 1.05 - 27, bx[1] * 1.05 - 48, bx[2] * 1.05 - 27, bx[3] * 1.05 - 48
            w = min(x1, cx1) - max(x0, cx0)
            h = min(y1, bottom + 10) - max(y0, top - 10)
            if w > 0 and h > 0:
                tot = max(tot, w * h / area)         # parts often overlap each other (outline + nominal box), so they are not added up
        worst = max(worst, min(tot, 1.0))
    return worst


def caption_in_safe_zone(bottom, dims):
    cw, ch = dims
    return bottom <= SAFE_BOTTOM and bottom - ch >= SAFE_TOP and 540 - cw / 2 >= CAPTION_X0 - 20 and 540 + cw / 2 <= CAPTION_X1 + 20


def _place_caption(track, start, end, dims, prev_y):
    """The first safe spot (the previous one is kept if it is still clear). Returns (y, overlap), y None if nowhere is safe."""
    cands = [prev_y] + [c for c in CAPTION_SPOTS if c != prev_y]
    best = (9.0, None)
    for c in cands:
        if not caption_in_safe_zone(c, dims):
            continue
        if TITLECARD and start < CARD_OFF and end > CARD_ON and (c - dims[1] < CARD_Y1 + 20 and c > CARD_Y0 - 20):
            continue            # the title card is on screen there
        ov = _cap_overlap(track, start, end, c, dims)
        if ov <= CAPTION_OK:
            return c, ov
        best = min(best, (ov, c), key=lambda p: p[0])
    if best[1] is not None and best[1] >= 1100 and best[0] <= CAPTION_TOLERATED:
        return best[1], best[0]        # a giant subject fills the frame: the caption rides the bottom edge, over at most the creature's legs, rather than vanish
    return None, best[0]


# ---------------------------------------------------------------- the title card (designed image, not subtitle text)
FONT_URLS = {
    "script": ("DancingScript.ttf", "https://raw.githubusercontent.com/google/fonts/main/ofl/dancingscript/DancingScript%5Bwght%5D.ttf"),
    "caps": ("Cinzel.ttf", "https://raw.githubusercontent.com/google/fonts/main/ofl/cinzel/Cinzel%5Bwght%5D.ttf"),
}
TITLECARD = {}                       # set by write_captions: what the card says and when it is on screen
CARD_ON, CARD_OFF = 1.2, 4.6
CARD_Y0, CARD_Y1 = 250, 1000          # the band of the frame the card uses (captions keep out of it while it shows)
CARD_ACCENTS = {"DARK FOLKLORE": (232, 185, 80), "MODERN": (150, 190, 235), "LOST WORLD": (240, 130, 70),
                "IMPOSSIBLE ANIMALS": (140, 220, 150), "NATURE": (110, 200, 200)}


def _card_font(kind, size):
    from PIL import ImageFont
    name, url = FONT_URLS[kind]
    path = WORK / "fonts" / name
    try:
        if not path.exists():
            (WORK / "fonts").mkdir(exist_ok=True)
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            path.write_bytes(r.content)
        f = ImageFont.truetype(str(path), size)
        try:
            f.set_variation_by_axes([700 if kind == "script" else 600])
        except Exception:
            pass
        return f
    except Exception:
        for fb in ("/usr/share/fonts/truetype/liberation/LiberationSerif-BoldItalic.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSerif-BoldItalic.ttf"):
            if os.path.exists(fb):
                return ImageFont.truetype(fb, size)
        return ImageFont.load_default()


def _spaced(draw, xy_center, text, font, fill, spacing):
    """Letter-spaced text centred on x. Returns its box."""
    widths = [draw.textlength(ch, font=font) for ch in text]
    total = sum(widths) + spacing * (len(text) - 1)
    x = xy_center[0] - total / 2
    top = None
    for ch, w in zip(text, widths):
        draw.text((x, xy_center[1]), ch, font=font, fill=fill)
        x += w + spacing
    bb = draw.textbbox((xy_center[0] - total / 2, xy_center[1]), text, font=font)
    return (int(xy_center[0] - total / 2), bb[1], int(xy_center[0] + total / 2), bb[3])


def title_card_png(series, episode, title, place, path, accent=None):
    """series (small), episode (discreet), title (cursive, the focus), place (small). Returns the layout report.
    Every box is measured; the card is refused if two boxes touch, anything leaves the safe width, or text is too small for a phone."""
    from PIL import Image, ImageDraw, ImageFilter
    accent = accent or next((c for k, c in CARD_ACCENTS.items() if k in series.upper()), (232, 185, 80))
    W_, H_ = 1080, 1920
    im = Image.new("RGBA", (W_, H_), (0, 0, 0, 0))
    glow = Image.new("RGBA", (W_, H_), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-120, CARD_Y0 - 60, W_ + 120, CARD_Y1 + 120), fill=(4, 6, 12, 150))          # a dark soft pool so the text reads on any scene
    gd.ellipse((200, 380, 880, 700), fill=accent + (28,))                                      # a faint coloured light behind the title
    im = Image.alpha_composite(im, glow.filter(ImageFilter.GaussianBlur(70)))
    d = ImageDraw.Draw(im)
    boxes = {}
    cx = W_ // 2
    f_series = _card_font("caps", 46)
    boxes["series"] = _spaced(d, (cx, 300), series.upper(), f_series, (225, 222, 214, 255), 9)
    y = boxes["series"][3] + 26
    d.line((cx - 150, y, cx - 18, y), fill=accent + (200,), width=2)                             # a thin rule with a small diamond
    d.line((cx + 18, y, cx + 150, y), fill=accent + (200,), width=2)
    d.polygon([(cx, y - 8), (cx + 8, y), (cx, y + 8), (cx - 8, y)], fill=accent + (255,))
    boxes["rule"] = (cx - 150, y - 8, cx + 150, y + 8)
    if episode:
        f_ep = _card_font("caps", 42)
        boxes["episode"] = _spaced(d, (cx, y + 34), f"EPISODE {episode}", f_ep, accent + (255,), 8)
        y = boxes["episode"][3]
    # the title: as large as fits in at most two lines inside the safe width
    t = str(title).strip()
    size, lines = 150, [t]
    while size >= 84:
        f_t = _card_font("script", size)
        words, lines, cur = t.split(), [], ""
        for w_ in words:
            trial = (cur + " " + w_).strip()
            if d.textlength(trial, font=f_t) <= 900 or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w_
        lines.append(cur)
        if len(lines) <= 2 and all(d.textlength(l, font=f_t) <= 900 for l in lines):
            break
        size -= 8
    f_t = _card_font("script", size)
    ty = y + 50
    tb = None
    for ln in lines:
        w_ = d.textlength(ln, font=f_t)
        for off, a in ((4, 140),):                                                              # soft shadow, then the text
            d.text((cx - w_ / 2 + off, ty + off), ln, font=f_t, fill=(0, 0, 0, a))
        d.text((cx - w_ / 2, ty), ln, font=f_t, fill=(250, 244, 230, 255))
        bb = d.textbbox((cx - w_ / 2, ty), ln, font=f_t)
        tb = bb if tb is None else (min(tb[0], bb[0]), min(tb[1], bb[1]), max(tb[2], bb[2]), max(tb[3], bb[3]))
        ty = bb[3] + 6
    boxes["title"] = tb
    if place:
        psz = 40
        while psz > 39 and sum(d.textlength(ch, font=_card_font("caps", psz)) for ch in place) + 8 * len(place) > 900:
            psz -= 2
        ptxt = place.upper()
        if "," in ptxt and sum(d.textlength(ch, font=_card_font("caps", psz)) for ch in ptxt) + 8 * len(ptxt) > 900:
            ptxt = ptxt.split(",")[0]          # a very long place line is cut back to its first part rather than run off the screen
        boxes["place"] = _spaced(d, (cx, tb[3] + 40), ptxt, _card_font("caps", psz), (200, 198, 190, 255), 8)
    im.save(path)
    # ---- validation
    problems, names = [], list(boxes)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = boxes[names[i]], boxes[names[j]]
            gap = max(b[1] - a[3], a[1] - b[3], b[0] - a[2], a[0] - b[2])
            if gap < 12:
                problems.append(f"'{names[i]}' and '{names[j]}' are too close or overlap (gap {gap}px)")
    for n_, b in boxes.items():
        if b[0] < 60 or b[2] > W_ - 60:
            problems.append(f"'{n_}' leaves the safe width ({b[0]}..{b[2]})")
        if b[3] > CARD_Y1 or b[1] < CARD_Y0 - 40:
            problems.append(f"'{n_}' leaves the card band ({b[1]}..{b[3]})")
    if size < 84:
        problems.append("the title does not fit in two lines at a readable size")
    for n_, px in (("series", 46), ("episode", 42), ("place", 39)):
        if n_ in boxes and px * 360 / 1080 < 13:
            problems.append(f"'{n_}' is too small on a phone")
    return {"boxes": boxes, "title_size": size, "problems": problems, "lines": lines}


def make_title_card():
    """Renders the card from what write_captions stored. Returns the png name, or None when there is no card or it failed its check."""
    tc = TITLECARD
    if not tc:
        return None
    try:
        rep = title_card_png(tc["series"], tc["episode"], tc["title"], tc["place"], WORK / "titlecard.png")
    except Exception as e:
        log(f"Title card could not be drawn ({e}).")
        return None
    try:
        (WORK / "titlecard_report.json").write_text(json.dumps(rep, indent=1, default=str))
    except Exception:
        pass
    if rep["problems"]:
        for p in rep["problems"]:
            log(f"Title card check FAILED: {p}")
        TITLECARD_STATUS.append(False)
        return None
    TITLECARD_BOX.append(rep["boxes"]["title"])
    log(f"Title card check passed: {len(rep['boxes'])} elements, title {rep['title_size']}px, no overlaps.")
    TITLECARD_STATUS.append(True)
    return "titlecard.png"


TITLECARD_STATUS = []
TITLECARD_BOX = []
LOOP_INFO = {}


def write_captions(words, total, path, card=None, track=None, card_big=None):
    if not words:
        return False
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Liberation Sans Narrow,112,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,"
        "1,0,0,0,100,100,1,0,1,8,3,2,150,150,520,1\n"
        "Style: Card,Liberation Sans Narrow,62,&H00D9D9D9,&H000000FF,&H00000000,&H00000000,"
        "1,0,0,0,100,100,10,0,1,3,0,8,60,60,330,1\n"
        "Style: Card2,Liberation Sans Narrow,52,&H00C8C8C8,&H000000FF,&H00000000,&H00000000,"
        "1,0,0,0,100,100,9,0,1,3,0,8,60,60,545,1\n"
        "Style: Title,Liberation Serif,124,&H0050B9E8,&H000000FF,&H00071018,&H96000000,"
        "1,0,0,0,100,100,2,0,1,5,4,8,60,60,395,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    EMITTED = []
    prev_y, prev_end = CAPTION_SPOTS[0], -9.0
    groups = caption_groups(words) if len(words) > 6 else [words[i:i + 2] for i in range(0, len(words), 2)]
    rep = {"captions": 0, "moved": 0, "split": 0, "omitted": 0, "omitted_text": [], "collisions": 0, "worst_overlap": 0.0}

    def emit(chunk, start, end, y):
        nonlocal prev_y, prev_end
        toks = [w[2].upper().replace("{", "").replace("}", "") for w in chunk]
        long_line = len(" ".join(toks)) > 17
        key = max(range(len(toks)), key=lambda j: len(toks[j]))  # the longest word is shown in orange
        toks[key] = "{\\c&H001E9BFF&}" + toks[key] + "{\\c&H00FFFFFF&}"
        text = ("{\\fs92}" if long_line else "") + " ".join(toks)
        if y != prev_y and start - prev_end < 0.6:  # a quick, smooth slide from where the last caption was
            tag = f"{{\\an2\\move(540,{prev_y},540,{y},0,170)}}"
        else:
            tag = f"{{\\an2\\pos(540,{y})}}"
        prev_y, prev_end = y, end
        EMITTED.append((start, end, y, dims_of(chunk), " ".join(w[2] for w in chunk)))
        pop = "{\\fscx88\\fscy88\\t(0,120,\\fscx100\\fscy100)}"     # each phrase lands with a small punch
        lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{tag}{pop}{text}")
        rep["captions"] += 1

    def dims_of(chunk):
        plain = " ".join(w[2] for w in chunk)
        return _cap_dims(len(plain), len(plain) > 17)

    for gi, chunk in enumerate(groups):
        start = chunk[0][0]
        nxt = groups[gi + 1][0][0] if gi + 1 < len(groups) else total
        end = max(chunk[-1][1], min(nxt, chunk[-1][1] + 0.4))
        if not track:
            emit(chunk, start, end, CAPTION_SPOTS[0])
            continue
        y, ov = _place_caption(track, start, end, dims_of(chunk), prev_y)
        if y is not None:
            if y != prev_y:
                rep["moved"] += 1
            rep["worst_overlap"] = max(rep["worst_overlap"], ov)
            emit(chunk, start, end, y)
            continue
        # nowhere is safe for the whole phrase: split it into two shorter captions, each with its own timing
        if len(chunk) >= 2:
            h = len(chunk) // 2
            halves = [(chunk[:h], start, chunk[h - 1][1] + 0.05), (chunk[h:], chunk[h][0], end)]
            placed = [(c, s0, e0, *_place_caption(track, s0, e0, dims_of(c), prev_y)) for c, s0, e0 in halves]
            if all(p[3] is not None for p in placed):
                rep["split"] += 1
                for c, s0, e0, yy, ov2 in placed:
                    rep["worst_overlap"] = max(rep["worst_overlap"], ov2)
                    emit(c, s0, e0, yy)
                continue
            # one half may fit even if the other does not: show only the half that is clear
            for c, s0, e0, yy, ov2 in placed:
                if yy is not None:
                    emit(c, s0, e0, yy)
                else:
                    rep["omitted"] += 1
                    rep["omitted_text"].append(" ".join(w[2] for w in c))
                    rep.setdefault("omitted_detail", []).append({"text": " ".join(w[2] for w in c), "from": round(s0, 2), "to": round(e0, 2),
                                                                 "overlap_by_spot": {sp_: round(_cap_overlap(track, s0, e0, sp_, dims_of(c)), 2) for sp_ in CAPTION_SPOTS}})
            rep["split"] += 1
            continue
        rep["omitted"] += 1                                   # better no caption than a caption over the creature
        rep["omitted_text"].append(" ".join(w[2] for w in chunk))
        rep.setdefault("omitted_detail", []).append({"text": " ".join(w[2] for w in chunk), "from": round(start, 2), "to": round(end, 2),
                                                     "overlap_by_spot": {c: round(_cap_overlap(track, start, end, c, dims_of(chunk)), 2) for c in CAPTION_SPOTS},
                                                     "box": next(([round(v) for v in b] for t_, b in track if t_ >= start and b), None)})
    # --- the mandatory check: every caption that was written is re-tested against every sampled frame
    if track:
        for st_, en_, yb, dims, txt in EMITTED:
            ov = _cap_overlap(track, st_ + 0.17, en_, yb, dims)
            if ov > CAPTION_OK + 1e-9:
                rep["tolerated"] = rep.get("tolerated", 0) + 1
            if ov > CAPTION_TOLERATED + 1e-9 or not caption_in_safe_zone(yb, dims):
                rep["collisions"] += 1
                rep.setdefault("collision_details", []).append({"text": txt, "from": round(st_, 2), "to": round(en_, 2), "y": yb, "overlap": round(ov, 3)})
                log(f"Caption collision: '{txt}' {st_:.1f}-{en_:.1f}s at y={yb} covers {ov:.0%} of its box")
    if track:
        rep["track_tail"] = [(round(t_, 2), [round(v) for v in b] if b else None) for t_, b in track[-60::15]]
    CAPTION_REPORT.append(rep)
    try:
        (path.parent / "caption_report.json").write_text(json.dumps(rep, indent=1))
    except Exception:
        pass
    log(f"Caption check: {rep['captions']} captions, {rep['moved']} moved out of the way, {rep['split']} split, "
        f"{rep['omitted']} left out rather than cover the creature, {rep['collisions']} collisions left")
    TITLECARD.clear()
    if card:
        top, low = card, None
        if "\u00b7" in card:       # "PLACE  ·  SERIES #N": series and episode are separate elements, the place is a small line
            region_part, series_part = [x.strip() for x in card.split("\u00b7", 1)]
            top, low = series_part, region_part
        mm = re.match(r"(.*?)\s*#\s*(\d+)\s*$", top)
        ser, ep = (mm.group(1).strip(), mm.group(2)) if mm else (top, "")
        TITLECARD.update({"series": ser, "episode": ep, "title": str(card_big or ser), "place": low or ""})
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return True


# ---------------------------------------------------------------- video: shots
LOOK = {"grade": "null"}  # color grade of the current video, set in main()


def ensure_shadow():
    if (WORK / "shadow.png").exists():
        return
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=600x90,format=rgba,"
         "geq=r=0:g=0:b=0:a='255*0.85*pow(max(0,1-hypot((X-W/2)/(W/2),(Y-H/2)/(H/2))),1.4)'",
         "-frames:v", "1", "shadow.png"], cwd=WORK)


def ensure_fog():
    """A soft, drifting band of dust/mist that sits in front of the creature's feet."""
    if (WORK / "fog.png").exists():
        return
    # small random noise stretched up gives soft cloud shapes (no streaks), faded out top and bottom
    fc = ("color=c=0x808080:s=54x13,format=gray,noise=alls=100:allf=u,"
          "scale=2160:520:flags=bicubic,gblur=sigma=42,eq=contrast=2.0:brightness=0.04,"
          "geq=lum='min(255,lum(X,Y)*1.15)*(1-pow(abs(Y-H/2)/(H/2),1.4))'[g];"
          "color=c=0xd9cfbf:s=2160x520,format=rgba[c];[c][g]alphamerge")
    run(["ffmpeg", "-y", "-filter_complex", fc, "-frames:v", "1", "fog.png"], cwd=WORK)


def desaturate(s):
    """colorchannelmixer settings that pull colors toward gray (s=1 keeps them, 0 is black and white)."""
    k = 1 - s
    return (f"colorchannelmixer=rr={s + k * 0.299:.3f}:rg={k * 0.587:.3f}:rb={k * 0.114:.3f}:"
            f"gr={k * 0.299:.3f}:gg={s + k * 0.587:.3f}:gb={k * 0.114:.3f}:"
            f"br={k * 0.299:.3f}:bg={k * 0.587:.3f}:bb={s + k * 0.114:.3f}")


def still_clip(img, dur, out, mode="in"):
    """Turn a picture into a video with camera motion: in, out, left or right."""
    n = max(int(dur * FPS), 2)
    if mode == "out":
        z, x = f"1.15-0.15*on/{n}", "(iw-iw/zoom)/2"
    elif mode == "left":
        z, x = "1.15", f"(iw-iw/zoom)*on/{n}"
    elif mode == "right":
        z, x = "1.15", f"(iw-iw/zoom)*(1-on/{n})"
    else:
        z, x = f"1+0.15*on/{n}", "(iw-iw/zoom)/2"
    vf = ("scale=1296:2304:force_original_aspect_ratio=increase,crop=1296:2304,"
          f"zoompan=z='{z}':x='{x}':y='(ih-ih/zoom)/2':d={n}:s=1080x1920:fps={FPS},setsar=1")
    run(["ffmpeg", "-y", "-i", img, "-vf", vf, "-t", f"{dur:.3f}", "-an",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", out], cwd=WORK)


def sprite_geometry(spr, factor=1.0):
    scale = min(MAX_SPRITE_W * factor / spr["w"], MAX_SPRITE_H * factor / spr["h"])
    tw, th = int(spr["w"] * scale) // 2 * 2, int(spr["h"] * scale) // 2 * 2
    ang = 0.045
    rw = int(tw * math.cos(ang) + th * math.sin(ang)) // 2 * 2 + 4
    rh = int(th * math.cos(ang) + tw * math.sin(ang)) // 2 * 2 + 4
    return tw, th, rw, rh, ang


def composite_shot(bg, sprites, dur, out, simple):
    """Put walking creatures on a background clip. simple=True drops the fancy effects."""
    ensure_shadow()
    cmd = ["ffmpeg", "-y", "-i", bg]
    for sp in sprites:
        cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", sp["spec"]["png"]]
    cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", "shadow.png"]
    shadow_idx = len(sprites) + 1
    fog_idx = len(sprites) + 2
    if not simple:
        ensure_fog()
        cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", "fog.png"]
    parts, last = [], "[0:v]"
    for i, sp in enumerate(sprites):
        spec = sp["spec"]
        tw, th, rw, rh, ang = sprite_geometry(spec, sp.get("scale", 1.0))
        # walk a few steps, but never so far that the creature leaves the screen
        dist = max(0.0, min(420.0, 70 * dur, 1080 - rw - 80 - 2 * abs(sp.get("dx", 0))))
        flip = "hflip," if spec["face"] != sp["move"] else ""
        alpha = f",colorchannelmixer=aa={sp['alpha']}" if sp.get("alpha", 1) < 1 else ""
        lean = 0.02 * sp["move"]  # lean slightly into the walk
        # soften edges and match the scene's colors, then sway with each step
        look = "" if simple else f"gblur=sigma=0.8,{desaturate(0.82)},{LOOK['grade']},"
        parts.append(
            f"[{i + 1}:v]format=rgba,{flip}{look}scale={tw}:{th},"
            f"rotate=a='{ang}*sin(PI*{STEP_RATE}*t)+{lean}':c=none:ow={rw}:oh={rh}{alpha}[s{i}]")
        parts.append(f"[{shadow_idx}:v]format=rgba,scale={int(tw * 0.8)}:46{alpha}[sh{i}]")
        cx = 540 + sp.get("dx", 0)
        xexpr = f"{cx - rw / 2:.1f}+{sp['move']}*{dist:.1f}*(t/{dur:.3f}-0.5)"
        # feet sink a few pixels into the ground so the creature does not float
        yexpr = f"{GROUND_Y + 14 - rh}-18*abs(sin(PI*{STEP_RATE}*t))"
        sh_x = f"{cx - tw * 0.4:.1f}+{sp['move']}*{dist:.1f}*(t/{dur:.3f}-0.5)"
        parts.append(f"{last}[sh{i}]overlay=x='{sh_x}':y='{GROUND_Y - 22}':eval=frame:format=auto[b{i}]")
        parts.append(f"[b{i}][s{i}]overlay=x='{xexpr}':y='{yexpr}':eval=frame:format=auto[o{i}]")
        last = f"[o{i}]"
    if simple:
        parts.append(f"{last}format=yuv420p[v]")
    else:
        # drifting dust/mist in front of the feet ties the creature into the ground
        parts.append(f"[{fog_idx}:v]format=rgba,colorchannelmixer=aa=0.55[fogl]")
        parts.append(f"{last}[fogl]overlay=x='-100-t*35':y='{GROUND_Y - 320}':eval=frame:format=auto,"
                     "format=yuv420p[v]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[v]", "-t", f"{dur:.3f}", "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", out]
    run(cmd, cwd=WORK)


def shot(plate, sprites, dur, out, bg_mode="in"):
    """A background with walking creatures on top.
    sprites: list of {'spec': sprite, 'move': +1/-1, 'alpha': 0-1, 'dx': pixels offset}.
    If the full effects fail, a simpler version is tried, and last of all the background alone."""
    bg = f"bg_{abs(hash(out)) % 10**8}.mp4"
    still_clip(plate, dur, bg, bg_mode)
    if sprites:
        for simple in (False, True):
            try:
                composite_shot(bg, sprites, dur, out, simple)
                return
            except RuntimeError as e:
                log(f"Shot '{out}': {'simple ' if simple else 'full '}compositing failed ({e})")
        log(f"Shot '{out}': showing the background only.")
    (WORK / bg).replace(WORK / out)


def clip_seconds(path):
    try:
        path = pathlib.Path(path)
        if not path.is_absolute():
            path = WORK / path
        out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
        return float(out.strip())
    except Exception:
        return 0.0


def clip_shot(clip, dur, out):
    """Use an AI-made video clip for `dur` seconds: fill the tall frame, trim it, or slow it down a
    little (at most 1.5x) and hold the last frame if the clip is shorter than needed."""
    d = clip_seconds(clip)
    if d < 0.5:
        raise RuntimeError("clip unreadable")
    f = 1.0 if d >= dur else min(dur / d, 1.5)
    extra = max(dur - d * f, 0.0)
    vf = (f"scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:1920,setsar=1,"
          f"fps={FPS},setpts={f:.4f}*PTS,tpad=stop_mode=clone:stop_duration={extra + 0.2:.3f}")
    run(["ffmpeg", "-y", "-i", clip, "-vf", vf, "-t", f"{dur:.3f}", "-an",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", out], cwd=WORK)


def shot_or_clip(plate, clip, dur, out, mode="in"):
    """The AI video clip if there is one (and it works), otherwise the still picture with camera motion."""
    if not plate:
        if not (WORK / "grad.png").exists():
            gradient_plate("grad")
        plate = "grad.png"
    if clip:
        try:
            clip_shot(clip, dur, out)
            log(f"Used video clip {pathlib.Path(clip).name} for {dur:.1f}s")
            return
        except RuntimeError as e:
            log(f"Clip {clip} could not be used ({e}); using a still picture instead.")
    shot(plate, [], dur, out, mode)


def open_times(dh, dm):
    """Timing of the opening block: hook + morph. Times are seconds from the start of the video."""
    la, lb, lm = 1.3, 1.3, 0.9
    big_la = dh + la
    total = dh + dm
    lh = total - (big_la + lb + lm) + 3 * FADE
    return {"la": big_la, "lb": lb, "lm": lm, "lh": lh, "total": total,
            "b": big_la - FADE, "mix": big_la + lb - 2 * FADE, "reveal": big_la + lb + lm - 3 * FADE}


def open_block(dh, dm, assets, out):
    """Animal A walks -> animal B walks -> both overlap -> the hybrid appears."""
    t = open_times(dh, dm)
    if t["lh"] < 1.0:
        raise RuntimeError("Opening too short")
    a, b, h = assets["a"], assets["b"], assets["h"]
    shot_or_clip(a["plate"], a.get("clip"), t["la"], "o_a.mp4", "right")
    shot_or_clip(b["plate"], b.get("clip"), t["lb"], "o_b.mp4", "left")
    mixed = False
    if assets.get("mix"):
        shot_or_clip(None, assets["mix"], t["lm"], "o_m.mp4", "in")
        mixed = True
    elif a.get("clip") and b.get("clip"):
        try:
            clip_shot(a["clip"], t["lm"], "m_a.mp4")
            clip_shot(b["clip"], t["lm"], "m_b.mp4")
            run(["ffmpeg", "-y", "-i", "m_a.mp4", "-i", "m_b.mp4", "-filter_complex",
                 "[0:v][1:v]blend=all_mode=average,format=yuv420p", "-t", f"{t['lm']:.3f}", "-an",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "o_m.mp4"], cwd=WORK)
            mixed = True
        except RuntimeError:
            log("Footage overlap failed; using a simple fade instead.")
    if not mixed:
        pa, pb = a["plate"], b["plate"]
        if pa and pb:
            run(["ffmpeg", "-y", "-i", pa, "-i", pb, "-filter_complex",
                 "[0:v][1:v]blend=all_mode=average", "-frames:v", "1", "mix.png"], cwd=WORK)
            still_clip("mix.png", t["lm"], "o_m.mp4", "in")
        else:
            shot_or_clip(pa or pb, a.get("clip") or b.get("clip"), t["lm"], "o_m.mp4", "in")
    shot_or_clip(h["plate"], h.get("clip"), t["lh"], "o_h.mp4", "in")
    o1, o2, o3 = t["b"], t["mix"], t["reveal"]
    fc = (f"[0][1]xfade=transition=fade:duration={FADE}:offset={o1:.3f}[v1];"
          f"[v1][2]xfade=transition=dissolve:duration={FADE}:offset={o2:.3f}[v2];"
          f"[v2][3]xfade=transition=dissolve:duration={FADE}:offset={o3:.3f}[v]")
    run(["ffmpeg", "-y", "-i", "o_a.mp4", "-i", "o_b.mp4", "-i", "o_m.mp4", "-i", "o_h.mp4",
         "-filter_complex", fc, "-map", "[v]", "-t", f"{t['total']:.3f}", "-an",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", out], cwd=WORK)
    return t


# ---------------------------------------------------------------- the Impossible series: abilities, places, walking only when it means something
ABILITY_WORDS = (
    ("camouflage", r"colou?r|camoufl|blend|chameleon|vanish|invisible|disappear|shimmer|ripple of"),
    ("swim", r"\bswim|\bswims|\bdive|\bdives|underwater|beneath the (water|waves|surface)|\bgills?\b"),
    ("fly", r"\bfl(y|ies|ew|ying)\b|\bsoar|\bglid|airborne|wings? (spread|beat|open)|takes? off|\bleaps? into the (air|sky)"),
    ("defend", r"\bdefen[cs]|\bspikes?\b|\bspines?\b|\bvenom|\bpoison|\bspits?\b|\bfrill|\bhiss|\barmou?r|\bshell\b|\bpuff|\bflares?\b"),
)
WALK_WORDS = (r"\bsize\b|\bhuge|\bgiant|\bmassive|\bheavy|\bweigh|\btower|\bfootstep|\bstomp|\bapproach|\bwalk|\bmarch|\bstalk|"
              r"\bcreep|\bcrept|\btoward|\btracks?\b|\btremble|\bground shakes?|\bshak(es|ing) the ground|\bpace|\bpaces\b")
BIOMES = (
    (r"shark|whale|fish|octopus|squid|eel|dolphin|ray|jelly|orca|seahorse|crab|lobster", "the deep ocean"),
    (r"camel|scorpion|lizard|snake|fennec|meerkat|vulture|gecko|chameleon", "the Sahara desert"),
    (r"penguin|polar|seal|walrus|arctic|reindeer|yeti|husky", "Antarctica"),
    (r"frog|croc|alligator|hippo|toad|newt|turtle|heron|salamander|axolotl", "a swamp full of toxic mud"),
    (r"monkey|parrot|jaguar|tiger|toucan|sloth|gorilla|python|panther|tapir|orangutan", "a rainforest after a flood"),
    (r"eagle|goat|yak|falcon|condor|ibex|snow leopard|llama|hawk", "a mountain peak above the clouds"),
    (r"lion|zebra|elephant|giraffe|rhino|cheetah|buffalo|antelope|horse|cow|deer|wolf|dog|cat|bear", "open grasslands"),
    (r"bat|spider|mole|badger|owl|centipede|scorpion", "a deep cave"),
    (r"fox|moose|lynx|boar|squirrel|raccoon|hedgehog", "a snowy forest"),
)
TEXT_PLACES = (
    (r"ocean|\bsea\b|underwater|reef|\bwaves?\b|\bdeep water", "the deep ocean"),
    (r"\bice\b|snow|frozen|glacier|arctic|blizzard", "Antarctica"),
    (r"desert|\bsand\b|dunes?\b", "the Sahara desert"),
    (r"swamp|\bmud\b|marsh|wetland|\bbog\b", "a swamp full of toxic mud"),
    (r"jungle|rainforest|canopy|vines", "a rainforest after a flood"),
    (r"volcan|\blava\b|magma|\bash\b", "a land of active volcanoes"),
    (r"\bcave|tunnel|underground|burrow", "a deep cave"),
    (r"mountain|cliff|\bpeak|\bridge\b|summit", "a mountain peak above the clouds"),
    (r"grassland|savanna|meadow|\bplains?\b|\bfield\b", "open grasslands"),
)


# places that really sit next to each other in nature: a creature that lives in one is believable in the next, so no hybrid is stuck with one backdrop
NEIGHBOR_PLACES = {
    "a rainforest after a flood": ("a misty river bank", "a swamp full of toxic mud", "a dark forest"),
    "a swamp full of toxic mud": ("a misty river bank", "a rainforest after a flood", "a dark forest"),
    "open grasslands": ("a moonlit field", "a stormy hillside", "a misty river bank"),
    "the Sahara desert": ("open grasslands", "a stormy hillside", "a deep cave"),
    "Antarctica": ("a snowy forest", "a mountain peak above the clouds"),
    "a mountain peak above the clouds": ("a stormy hillside", "a snowy forest", "a deep cave"),
    "a deep cave": ("a dark forest", "a stormy hillside"),
    "a snowy forest": ("a dark forest", "a stormy hillside", "a mountain peak above the clouds"),
    "a land of active volcanoes": ("a stormy hillside", "a deep cave"),
    "a dark forest": ("a misty river bank", "a stormy hillside"),
}


LOCATIONS_FILE = ROOT / "locations.json"      # which places the last episodes used, so the next one looks different


def recent_locations(n=10):
    try:
        return list(json.loads(LOCATIONS_FILE.read_text()))[-n:]
    except Exception:
        return []


def remember_locations(places):
    try:
        old = recent_locations(40)
        LOCATIONS_FILE.write_text(json.dumps((old + [p for p in places if p])[-40:], indent=1))
    except Exception as e:
        log(f"Could not save the places used ({e}).")


def detect_ability(text):
    low = (text or "").lower()
    for name, rx in ABILITY_WORDS:
        if re.search(rx, low):
            return name
    return None


def needs_walk(text):
    return bool(re.search(WALK_WORDS, (text or "").lower()))


def creature_biomes(*names):
    out = []
    for nm in names:
        for rx, place in BIOMES:
            if re.search(rx, str(nm).lower()) and place in toon_places() and place not in out:
                out.append(place)
    return out


def toon_places():
    import toon
    return toon.PLACES


def choose_scene_place(i, text, base, a, b, used):
    """A different, believable place for each stretch of the story: what the sentence says first, otherwise the places the two parent
    animals really live in. The first and last shots use the episode's own place. Never the same place twice in a row when another one fits."""
    import toon
    low = (text or "").lower()
    named = next((pl for rx, pl in TEXT_PLACES if re.search(rx, low) and pl in toon.PLACES), None)
    pool = [base] + [p for p in creature_biomes(a, b) if p != base]
    for p_ in list(pool):                                   # add the neighbouring habitats so there are always several believable places
        for q_ in NEIGHBOR_PLACES.get(p_, ()):
            if q_ not in pool and q_ in toon.PLACES:
                pool.append(q_)
    if named and named not in pool:
        pool.insert(1, named)
    pick = named if named else None
    if pick is None:
        cands = [p for p in pool if not used or p != used[-1]]
        counts = {p: used.count(p) for p in pool}
        recent = recent_locations()
        try:
            ep_no = len(load_history())          # history.json is always saved by the workflow, so this works even without locations.json
        except Exception:
            ep_no = 0
        # unused here first, then least used in the last episodes, then a rotation that moves one step every episode
        cands.sort(key=lambda p: (counts[p], recent.count(p), (pool.index(p) + ep_no + i) % len(pool)))
        pick = cands[0] if cands else base
    if used and pick == used[-1] and len(pool) > 1:
        pick = next((p for p in pool if p != used[-1]), pick)
    return pick


def plan_scenes(sentences, total, ds=None):
    """Split the audio time across sentences, then decide what to show for each."""
    if ds is None:
        chars = [max(len(s["text"]), 1) for s in sentences]
        tot = sum(chars)
        ds = [total * c / tot for c in chars]
    n = len(sentences)
    dh = ds[0]
    d, i = ds[1], 2
    while d < 4.8 and i < n - 1:
        d += ds[i]
        i += 1
    scenes = [{"kind": "open", "dur": dh + d, "hook": dh, "morph": d}]
    while i < n - 1:
        scenes.append({"kind": "scene", "dur": ds[i], "image": sentences[i].get("image", ""), "si": i,
                       "host": sentences[i].get("speaker") == "host" or bool(sentences[i].get("frame"))})
        i += 1
    scenes.append({"kind": "hold", "dur": ds[n - 1] + 0.3})
    return scenes


HOOK_EYE = (255, 170, 40)     # the colour of the creature's eyes in the opening hook; each story sets its own


# ---------------------------------------------------------------- the quality gate: nothing is approved (or uploaded) unless every mandatory check passes
ANGLES_USED, PLACES_USED, ABILITY_EXPECTED, PAUSE_EXPECTED = [], [], [], []
SMASH_AT = {}                      # shot number -> seconds into the shot when a creature wrecks a vehicle (for the crash sound)


def _gray_frames(path, times, size=(54, 96)):
    """Grabs small grey frames from the finished video (for the final inspection)."""
    import numpy as np
    out = []
    for t in times:
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(t, 0):.2f}", "-i", str(path), "-frames:v", "1", "-vf", f"scale={size[0]}:{size[1]},format=gray",
                            "-f", "rawvideo", "-"], capture_output=True)
        a = np.frombuffer(r.stdout, dtype=np.uint8)
        out.append(a.reshape(size[1], size[0]).astype(np.float32) if a.size == size[0] * size[1] else None)
    return out


def run_quality_checks(video_path, words, kind):
    """Every mandatory stage, as real measurements. Returns a list of (stage, passed, message)."""
    import numpy as np
    res = []

    def add(stage, ok, msg):
        res.append((stage, bool(ok), msg))
    add("Voice approved", voice_approved(), "the narrator voice is approved" if voice_approved() else "the narrator voice has not been approved")
    if TITLECARD:
        okc = bool(TITLECARD_STATUS) and TITLECARD_STATUS[-1]
        add("Title card", okc, "no overlaps, readable at phone size" if okc else "the card failed its layout check")
    cr = CAPTION_REPORT[-1] if CAPTION_REPORT else None
    if cr is None:
        add("Captions", False, "no caption report")
    else:
        add("Captions", cr.get("collisions", 1) == 0 and cr.get("omitted", 0) <= max(2, cr.get("captions", 1) // 8) and cr.get("tolerated", 0) <= max(2, cr.get("captions", 1) * 3 // 10),
            f"no caption covers a face ({cr.get('omitted', 0)} left out, {cr.get('tolerated', 0)} ride over a giant creature's legs)")
    add("Sound and narration", SYNC_REPORT.get("overlaps", 0) == SYNC_REPORT.get("turned_down", 0) and len(SOUND_SYNC) >= len(PAUSE_EXPECTED),
        f"{len(SOUND_SYNC)} narrator pause(s), {SYNC_REPORT.get('overlaps', 0)} overlap(s) fixed")
    wins = [e for e in SOUND_SYNC if "final_at" in e]
    if wins and (WORK / "voice_gap.wav").exists() and (WORK / "mix.wav").exists():
        import numpy as np
        def seg(name, a, b):
            r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{a:.2f}", "-t", f"{b - a:.2f}", "-i", str(WORK / name), "-ac", "1", "-ar", "16000", "-f", "s16le", "-"], capture_output=True)
            x = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0
            return 20 * math.log10(float(np.sqrt(np.mean(x ** 2))) + 1e-9) if x.size else -120.0
        bad = []
        for e in wins:
            a, b = e["final_at"] + 0.15, e["final_at"] + e["final_len"] - 0.15
            v, mx = seg("voice_gap.wav", a, b), seg("mix.wav", a, b)
            e["voice_db"], e["mix_db"] = round(v, 1), round(mx, 1)
            if v > -50 or mx < -45:
                bad.append(f"sentence {e['sentence'] + 1}: narrator {v:.0f} dB, mix {mx:.0f} dB")
        add("Pause windows", not bad, "the narrator is silent and the sound effect is heard in every pause window" if not bad else "; ".join(bad))
    if AUDIO_REPORT:
        add("Narrator louder than music", AUDIO_REPORT.get("p10_db", 99) >= 5.0,
            f"narrator is {AUDIO_REPORT.get('median_db')} dB above the rest (weakest moments {AUDIO_REPORT.get('p10_db')} dB)")
    if ABILITY_EXPECTED:
        shown = {i for _, i in ABILITY_LOG} | set(ANATOMY_LOG)
        miss = [i for i in ABILITY_EXPECTED if i not in shown]
        add("Abilities shown", not miss, "every ability the narrator describes is demonstrated" if not miss else f"scene(s) {miss} describe an ability that is not shown")
    if kind == "hybrid" and len(PLACES_USED) >= 3:
        add("Scene variety", len(set(PLACES_USED)) >= 2 and len(set(ANGLES_USED)) >= 2, f"{len(set(PLACES_USED))} places, {len(set(ANGLES_USED))} camera angles")
    elif ANGLES_USED:
        add("Scene variety", len(set(ANGLES_USED)) >= 3 or len(ANGLES_USED) < 5, f"{len(set(ANGLES_USED))} camera angles")
    try:
        import toon as _t
        rows = [r for r in _t.MOTION_LOG if r["n"] >= 5]
        jumpy = [r for r in rows if r["jumps"] > 0]
        if rows:       # a creature must move like something with weight: no teleporting from one spot to another between two frames
            add("Movement", not jumpy, (f"{len(rows)} creature shots, the biggest move between two frames is {max(r['max'] for r in rows):.0f}px (limit {_t.MOTION_JUMP_PX}px)"
                                        if not jumpy else "a creature jumps too far between frames in " + ", ".join(pathlib.Path(r['scene']).name for r in jumpy)))
    except Exception as e:
        add("Movement", False, f"could not be measured ({e})")
    try:
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video_path)], capture_output=True, text=True).stdout.strip())
    except Exception:
        dur = 0.0
    add("File", 8 < dur <= 60.0, f"{dur:.1f}s long")
    last_word = max((w_[1] for w_ in words), default=0.0)
    add("Ending", dur - last_word >= 0.8, f"{dur - last_word:.1f}s between the last word and the end")
    ts = [0.05, 0.8, 2.6, 6.0] + [dur * f for f in (0.2, 0.35, 0.5, 0.65, 0.8, 0.9)] + [dur - 0.1]
    fr = _gray_frames(video_path, ts)
    if all(f is not None for f in fr):
        dead = sum(1 for f in fr if f.mean() < 2.0)
        add("Picture", dead <= 1, f"{dead} blank frame(s) in {len(fr)} samples")
        if TITLECARD and TITLECARD_STATUS and TITLECARD_STATUS[-1]:
            tb = TITLECARD_BOX[-1] if TITLECARD_BOX else None
            if tb:
                big = _gray_frames(video_path, [0.7, 2.6], size=(270, 480))
                if all(f is not None for f in big):
                    x0_, y0_, x1_, y1_ = [int(v / 4) for v in tb]
                    on, off = [float((f[y0_:y1_, x0_:x1_] > 235).mean()) for f in (big[1], big[0])]
                    add("Title card visible", on >= 0.01 and on > off + 0.005, f"title text on screen at 2.6s ({on:.3f} bright vs {off:.3f} before it appears)")
        if LOOP_INFO:
            try:
                from PIL import Image as _I
                fog = np.asarray(_I.open(WORK / LOOP_INFO["fog_png"]).convert("L").resize((54, 96)), dtype=np.float32)
            except Exception:
                fog = fr[0]
            diff = float(np.abs(fr[-1] - fog).mean())
            add("Loop", diff < 40.0 and fr[-1].mean() < 70, f"the last frame is the opening's dark fog (differs by {diff:.0f}/255)")
        else:
            add("Loop", False, "the loop ending was not applied")
    else:
        add("Picture", False, "could not read frames from the video")
    return res


def quality_gate(video_path, words, kind, rebuild=None):
    """Stage 7. One automatic repair attempt (re-render) if something fails, then STOP: no approval, no upload, a clear report."""
    failed, res = [], []
    for attempt in (1, 2):
        res = run_quality_checks(video_path, words, kind)
        failed = [r for r in res if not r[1]]
        for stage, ok, msg in res:
            log(f"QC {'PASS' if ok else 'FAIL'} - {stage}: {msg}")
        if not failed:
            break
        if attempt == 1 and rebuild is not None:
            log("QC: re-rendering once to try to repair: " + ", ".join(r[0] for r in failed))
            try:
                import toon as _t0
                _t0.MOTION_LOG.clear()
                rebuild()
            except Exception as e:
                log(f"QC: the re-render failed ({e})")
                break
    ready = not failed
    try:
        OUT.mkdir(exist_ok=True)
        (OUT / "quality_report.json").write_text(json.dumps({"production_ready": ready, "checks": [{"stage": s, "passed": ok, "detail": m} for s, ok, m in res]}, indent=1))
    except Exception:
        pass
    if not ready and os.environ.get("QC_ENFORCE", "1") != "0":
        try:
            pathlib.Path(video_path).rename(OUT / "short_NOT_APPROVED.mp4")
        except Exception:
            pass
        sys.exit("STOPPED - NOT APPROVED, nothing uploaded. Failed checks: " + "; ".join(f"{s} ({m})" for s, ok, m in res if not ok) +
                 ". See output/quality_report.json. (Set the variable QC_ENFORCE to 0 only if you want to publish anyway.)")
    return ready


def build_video(scenes, assets, audio_name, captions_name, out_path):
    segs, reveal = [], 0.0
    k = 0
    h = assets["h"]
    for i, sc in enumerate(scenes):
        seg = f"seg{i}.mp4"
        if sc["kind"] == "open":
            t = open_block(sc["hook"], sc["morph"], assets, seg)
            reveal = t["reveal"]
        elif sc["kind"] == "hold":
            shot_or_clip(h["plate"], sc.get("clip"), sc["dur"], seg, "in")
        else:
            shot_or_clip(sc["plate"], sc.get("clip"), sc["dur"], seg, MOTIONS[k % len(MOTIONS)])
            k += 1
        segs.append(seg)
    (WORK / "list.txt").write_text("".join(f"file '{s}'\n" for s in segs))
    T = reveal
    shake = (f"between(t,{T:.2f},{T + 0.9:.2f})")
    # gentle handheld wobble all the time, a hard shake when the hybrid is revealed
    xe = f"27+3*sin(t*2.1)+2*sin(t*5.3)+{shake}*24*sin(t*65)*(1-(t-{T:.2f})/0.9)"
    ye = f"48+3*cos(t*1.7)+2*cos(t*4.9)+{shake}*30*cos(t*57)*(1-(t-{T:.2f})/0.9)"
    flash = f"if(between(t,{T:.2f},{T + 0.35:.2f}),0.6*(1-(t-{T:.2f})/0.35),0)"
    # one shared "old film" look over everything: faded colors, scene color grade, heavy grain, vignette
    vf = (f"scale=1134:2016,crop=1080:1920:x='{xe}':y='{ye}',"
          f"curves=preset=vintage,{LOOK['grade']},"
          f"eq=brightness='{flash}+0.06':saturation=1.0:contrast=1.05:gamma=1.2:eval=frame,"
          "vignette=PI/5.5,noise=alls=11:allf=t+u")
    if captions_name:
        vf += f",subtitles={captions_name}"
    hook_in, hook_fc = [], None
    card_png = make_title_card() if os.environ.get("TITLE_CARD", "1") != "0" else None
    head, tail = vf.split("curves=preset=vintage", 1)
    graph, cur, n_in = [f"[0:v]{head.rstrip(',')}[b]"], "b", 2
    if os.environ.get("HOOK", "1") != "0":
        try:     # the opening hook: fangs on the scream, glowing eyes pushing in, then the story dissolves in
            import toon
            toon.hook_clip(str(WORK / "hook.mp4"), tuple(HOOK_EYE), cwd=WORK)
            graph.append(f"[{n_in}:v]format=rgba,fade=t=out:st={toon.HOOK_LEN - 0.3:.2f}:d=0.3:alpha=1[hk];"
                         f"[{cur}][hk]overlay=0:0:eof_action=pass:enable='lt(t,{toon.HOOK_LEN:.2f})'[h1]")
            cur, n_in = "h1", n_in + 1
            hook_in = ["-i", "hook.mp4"]
        except Exception as e:
            log(f"Opening hook skipped ({e}).")
    if card_png:    # the title card eases in with a small upward drift and fades out
        hook_in += ["-loop", "1", "-t", f"{CARD_OFF + 0.2:.1f}", "-i", card_png]
        graph.append(f"[{n_in}:v]format=rgba,fade=t=in:st={CARD_ON:.2f}:d=0.8:alpha=1,fade=t=out:st={CARD_OFF - 0.7:.2f}:d=0.7:alpha=1[tc];"
                     f"[{cur}][tc]overlay=x=0:y='20*(1-min(1,max(0,(t-{CARD_ON:.2f})/1.4)))':eof_action=pass:enable='between(t,{CARD_ON:.2f},{CARD_OFF:.2f})'[h2]")
        cur, n_in = "h2", n_in + 1
    T_end = min(59.0, audio_seconds(WORK / audio_name))
    if os.environ.get("LOOP", "1") != "0" and T_end > 8:
        try:     # the loop: the last second sinks into the same dark fog the opening starts in, a pair of eyes watches, then it all restarts
            import toon
            toon.hook_frame(0.0, tuple(HOOK_EYE)).save(WORK / "loop_fog.png")
            toon.hook_frame(0.62, tuple(HOOK_EYE)).save(WORK / "loop_eyes.png")
            LOOP_LEN = 1.3
            hook_in += ["-loop", "1", "-t", f"{LOOP_LEN + 1.5:.2f}", "-i", "loop_fog.png", "-loop", "1", "-t", f"{LOOP_LEN + 1.5:.2f}", "-i", "loop_eyes.png"]
            t0_ = T_end - LOOP_LEN
            graph.append(f"[{n_in}:v]format=rgba,fade=t=in:st=0:d=1.0:alpha=1,setpts=PTS+{t0_:.3f}/TB[lf];"
                         f"[{cur}][lf]overlay=0:0:eof_action=pass:enable='gte(t,{t0_:.3f})'[l1]")
            graph.append(f"[{n_in + 1}:v]format=rgba,fade=t=in:st=0.35:d=0.5:alpha=1,fade=t=out:st=1.1:d=0.2:alpha=1,setpts=PTS+{t0_:.3f}/TB[le];"
                         f"[l1][le]overlay=0:0:eof_action=pass:enable='gte(t,{t0_ + 0.35:.3f})'[l2]")
            cur, n_in = "l2", n_in + 2
            LOOP_INFO.update({"fog_png": "loop_fog.png", "from": round(t0_, 2), "to": round(T_end, 2)})
        except Exception as e:
            log(f"Loop ending skipped ({e}).")
    if len(graph) > 1:
        hook_fc = ";".join(graph) + f";[{cur}]curves=preset=vintage{tail}[vout]"
    vmap = ["-map", "0:v"] if not hook_fc else ["-filter_complex", hook_fc, "-map", "[vout]"]
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt", "-i", audio_name] + hook_in +
        ([] if hook_fc else ["-vf", vf]) + vmap + ["-map", "1:a",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
         "-af", f"afade=t=out:st={max(0.0, T_end - 0.35):.2f}:d=0.35,loudnorm=I=-14:TP=-1.5:LRA=11", "-ac", "2", "-ar", "44100",
         "-c:a", "aac", "-b:a", "192k", "-shortest", "-t", "59", str(out_path)], cwd=WORK)


def make_steps(times, mass, out_name, total):
    """One quiet footfall track: a soft low thud at every moment a foot lands (heavier bodies: deeper, longer)."""
    import numpy as np
    sr = 44100
    buf = np.zeros(int((total + 1.0) * sr), dtype=np.float32)
    rnd = random.Random(7)
    f0 = 150.0 / (0.7 + mass)
    n = int(sr * (0.07 + 0.07 * mass))
    tt = np.arange(n) / sr
    for t in times:
        i = int(t * sr)
        if i + n >= len(buf):
            continue
        f = f0 * rnd.uniform(0.92, 1.08)
        thud = np.sin(2 * math.pi * f * tt * (1.0 - 0.35 * tt / tt[-1])) * np.exp(-tt * (38 / (0.7 + mass)))
        tick = np.random.default_rng(int(t * 1000)).standard_normal(n) * np.exp(-tt * 260) * 0.25
        buf[i:i + n] += ((thud + tick) * rnd.uniform(0.8, 1.0)).astype(np.float32)
    buf = np.clip(buf, -1, 1)
    with wave.open(str(WORK / out_name), "wb") as w_:
        w_.setnchannels(1)
        w_.setsampwidth(2)
        w_.setframerate(sr)
        w_.writeframes((buf * 32000).astype(np.int16).tobytes())
    return out_name


def sound_events(scenes, sounds, hunt=None, steps_file=None):
    """When each sound effect plays, in seconds from the start of the video."""
    first = scenes[0]
    t = open_times(first["hook"], first["morph"])
    ev = [
        {"file": sounds["scream"], "at": 0.05, "vol": 0.8, "max": 1.4},      # scary opening flash
        {"file": sounds["call_a"], "at": 1.5, "vol": 0.8, "max": 3.0},
        {"file": sounds["whoosh"], "at": t["b"] - 0.1, "vol": 0.45},
        {"file": sounds["call_b"], "at": t["b"] + 0.15, "vol": 0.8, "max": 3.0},
        {"file": sounds["whoosh"], "at": t["mix"] - 0.1, "vol": 0.45},
        {"file": sounds["riser"], "at": max(t["reveal"] - 1.3, 0), "vol": 0.55},
        {"file": sounds["boom"], "at": t["reveal"], "vol": 0.9},
        {"file": sounds["scream"], "at": t["reveal"] + 0.05, "vol": 0.95, "max": 2.6},
    ]
    at = first["dur"]
    for sc in scenes[1:]:
        ev.append({"file": sounds["whoosh"], "at": at - 0.1, "vol": 0.3})
        if sc.get("maul"):
            fe, _q = maul_sound_events(sounds, at, sc["dur"])
            ev += fe
        if sc.get("smash_at") is not None:            # a vehicle is wrecked: the crunch lands exactly on the contact frame
            sh_ = at + sc["smash_at"]
            ev += [{"file": sounds["crash"], "at": sh_, "vol": 0.9}, {"file": sounds["boom"], "at": sh_, "vol": 0.7},
                   {"file": sounds["crunch"], "at": sh_ + 0.04, "vol": 0.7}, {"file": sounds["tap"], "at": sh_ + 0.14, "vol": 0.6},
                   {"file": sounds["scrape"], "at": sh_ + 0.3, "vol": 0.45}]
        if sc.get("hunt") and hunt:
            ev.append({"file": sounds["eerie"], "at": at + hunt["zoom_at"] - 0.05, "vol": 0.9})
            ev.append({"file": sounds["boom"], "at": at + hunt["zoom_at"], "vol": 0.6})
            ev.append({"file": sounds["growl"], "at": at + hunt["lunge_at"] - 0.1, "vol": 0.8})
            ev.append({"file": sounds["boom"], "at": at + hunt["lunge_at"] + 0.25, "vol": 0.7})
            if "maul_at" in hunt:
                fe, _q = maul_sound_events(sounds, at + hunt["maul_at"], sc["dur"] - hunt["maul_at"])
                ev += fe
        at += sc["dur"]
    if steps_file:
        ev.append({"file": steps_file, "at": 0.0, "vol": 0.9})
    # closing close-up of the face: a last growl and a quiet eerie tone, then the video loops
    ev.append({"file": sounds["growl"], "at": max(at - scenes[-1]["dur"] + 0.3, 0), "vol": 0.6})
    ev.append({"file": sounds["eerie"], "at": max(at - 1.4, 0), "vol": 0.5})
    return ev


# ---------------------------------------------------------------- upload
def upload_to_youtube(video_path, title, description, tags):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = Credentials(
        None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    body = {
        "snippet": {"title": title, "description": description, "tags": tags, "categoryId": "15"},
        "status": {"privacyStatus": PRIVACY, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True, chunksize=-1)
    request = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        _, response = request.next_chunk()
    return response["id"]


# ---------------------------------------------------------------- main
def main():
    if not GEMINI_API_KEY:
        sys.exit("Missing secret: GEMINI_API_KEY")
    if not (CF_ACCOUNT and CF_TOKEN):
        sys.exit("Missing secret: CLOUDFLARE_ACCOUNT_ID and/or CLOUDFLARE_API_TOKEN")

    WORK.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    history = load_history()
    key, a, b, (place_name, place_desc, ambient_query, place_light, place_grade) = pick_idea(history)
    LOOK["grade"] = GRADES.get(place_grade, "null")
    log(f"Idea: {a} + {b} in {place_name}")

    story = ask_gemini(a, b, place_name)
    sentences = story["sentences"]
    narration = " ".join(s["text"] for s in sentences)
    look = story["hybrid_look"]
    log(f"Title: {story['title']}")

    # --- voice
    words = None
    audio_name = "voice.wav"
    if not gemini_tts(narration, WORK / "voice.wav", style=NARRATOR_STYLE):
        voice_fallback_check()
        log("Falling back to the basic free voice.")
        audio_name = "voice.mp3"
        words = asyncio.run(edge_voice(narration, str(WORK / "voice.mp3")))
    total = audio_seconds(WORK / audio_name)
    if not words:
        words = even_words(narration, total)
    has_caps = write_captions(words, total, WORK / "captions.ass")

    # --- creatures and backgrounds
    keycolor = pick_key([a, b])
    log(f"Cut-out backdrop color: {keycolor}")
    assets = {"a": get_animal(a, "a", pick_key([a]), place_light),
              "b": get_animal(b, "b", pick_key([b]), place_light)}
    h_sprite = make_sprite("spr_h", f"An imaginary hybrid creature: {look}", keycolor,
                           place_light, fierce=True)
    plate_ok = make_plate("plate_h", place_desc)
    if h_sprite:
        assets["h"] = {"plate": "plate_h.png", "sprite": h_sprite}
    else:
        log("Hybrid cut-out failed. Trying a full-frame hybrid picture.")
        prompt = (f"{STYLE} An imaginary hybrid creature: {look} It is {FIERCE}. "
                  f"It stands in {place_name}, shown clearly and dominantly.")
        if not make_picture("port_h", prompt):
            log("Hybrid picture failed. Using a blend of the two animals instead.")
            run(["ffmpeg", "-y", "-i", assets["a"]["plate"], "-i", assets["b"]["plate"], "-filter_complex",
                 "[0:v][1:v]blend=all_mode=average", "-frames:v", "1", "port_h.png"], cwd=WORK)
        assets["h"] = {"plate": "port_h.png", "sprite": None}

    scenes = plan_scenes(sentences, total)
    for i, sc in enumerate(scenes):
        if sc["kind"] != "scene":
            continue
        name = f"plate_s{i}"
        if assets["h"]["sprite"]:
            make_plate(name, sc["image"])  # the walking creature is added on top
        else:
            # no cut-out creature available: the picture itself must show the creature
            prompt = f"{STYLE} The creature: {look} Scene in {place_name}: {sc['image']}"
            if not make_picture(name, prompt):
                shutil.copy(WORK / assets["h"]["plate"], WORK / f"{name}.png")
        sc["plate"] = f"{name}.png"

    # --- sound
    sounds = prepare_sounds(a, b, ambient_query)
    events = sound_events(scenes, sounds)
    build_audio(audio_name, total, events, sounds["ambient"], "mix.wav", score=ramp_score(total))

    out_path = OUT / "short.mp4"
    build_video(scenes, assets, "mix.wav", "captions.ass" if has_caps else None, out_path)
    sprites = sum(1 for k in ("a", "b", "h") if assets[k]["sprite"])
    log(f"Video ready: {out_path} ({total:.1f}s of speech, animated creatures: {sprites}/3, "
        f"real animal calls: {sounds['real_calls']}/2)")

    title = story["title"][:80].rstrip() + " #Shorts"
    description = (
        f"Impossible Animals #{plan.get('episode', 1)}: {story['creature_name']}, an imaginary animal hybrid ({a} + {b}) in {place_name}.\n"
        "This is a fictional story made for fun and the creature is not real. "
        "The images and animation are AI-generated, the narration uses a synthetic voice, "
        "and the sound effects are synthesized or CC0.\n\n"
        "Comment which two animals I should combine next!\n\n"
        "#shorts #animals #whatif #speculativebiology"
    )
    tags = ["shorts", "animals", "what if", "hybrid animals", "speculative biology", a, b]
    (OUT / "meta.json").write_text(json.dumps(
        {"title": title, "description": description, "narration": narration,
         "animated_creatures": sprites, "real_animal_calls": sounds["real_calls"]}, indent=2))

    if DRY_RUN:
        log("DRY RUN: not uploading. Download the video from the run's Artifacts.")
        return
    video_id = upload_to_youtube(out_path, title, description, tags)
    log(f"Uploaded as '{PRIVACY}': https://youtube.com/shorts/{video_id}")
    history.append(key)
    HISTORY_FILE.write_text(json.dumps(history, indent=1))


# ---------------------------------------------------------------- two-step mode: plan, then build
PLAN_DIR = ROOT / "plan"
CLIPS_DIR = ROOT / "clips"
CLIP_EXT = (".mp4", ".mov", ".webm", ".m4v", ".gif")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")


def clip_prompts(look, place_name, place_desc, prey="a small animal", move="it pounces with lightning speed"):
    """Three prompts to paste into an AI video website."""
    base = ("Photorealistic cinematic footage, handheld camera, natural lighting, realistic motion, "
            "real fur, skin and scale texture, vertical 9:16, no text, no people.")
    return [
        ("Clip 1 - the reveal (plays when the hybrid first appears)",
         f"{base} A massive imaginary hybrid creature steps out and walks toward the camera in "
         f"{place_name}: {place_desc}. {look} It is menacing and dominant."),
        ("Clip 2 - the signature move on its prey (plays in the middle of the story)",
         f"{base} In {place_desc}, the same hybrid creature goes after {prey}. {move}. "
         f"Fast, powerful movement, camera follows the action. {look}"),
        ("Clip 3 - the close-up (plays at the end)",
         f"{base} Extreme close-up of the same hybrid creature's face, glowing eyes, snarling and "
         f"breathing heavily, wind and dust moving around it. {look}"),
    ]



# ---------------------------------------------------------------- series 2: dark folklore
FOLK_ANIMALS = ["bull", "wolf", "bear", "fox", "snake", "owl", "lion", "cheetah", "crocodile", "bat"]
FOLK_REGIONS = ["Kerala, India", "Scotland", "Japan", "Mexico", "Romania", "Nigeria", "the Philippines",
                "Iceland", "Brazil", "Russia", "Ireland", "Turkey", "Indonesia", "Greece", "Korea"]
FOLK_PLACE = ("a village at night", "a dark village path under a full moon", "night crickets ambience",
              "cold blue moonlight with a warm lantern glow", "cool")


# Every legend follows a different plot shape, opens differently and ends differently, so no two videos feel alike.
STORY_TYPES = {
    "mimic": ("The creature learns the voice of someone the hero loves (a son, a wife, a friend, the village sheriff) and calls out "
              "with it from the dark, luring the hero away from safety. The real person is somewhere else, or already gone.",
              "pathway, forest, home, door, window, stalk, attack"),
    "follower": ("A traveller walking alone at night realises something is following. Its steps match theirs.",
                 "pathway, forest, footprints, hide, stalk, attack"),
    "guest": ("A stranger knocks at night and politely asks to be let in. The household has an old rule about this.",
              "village, home, door, window, attack"),
    "return": ("Someone comes home after years away and slowly realises a relative at home is no longer what they were.",
               "pathway, village, home, window, transform"),
    "bargain": ("Long ago a village made a deal with the creature. Tonight the price falls due.",
                "village, mob, graveyard, door, attack"),
    "neighbour": ("A kind neighbour is the creature by night. Small clues pile up, one by one, until the truth lands.",
                  "village, home, footprints, window, transform"),
    "hunter": ("A hunter tracks the creature through the wild, until the tracks quietly start to circle behind him.",
               "forest, footprints, hide, stalk, attack"),
    "festival": ("At a festival or wedding a guest is missing, or a guest has no shadow. The party goes quiet.",
                 "village, mob, door, chase, attack"),
    "child": ("A child says they saw a figure at the window. The adults laugh, until the night proves the child right.",
              "home, window, door, hide, attack"),
    "crossing": ("A river crossing or ferry with a toll. Something older than the village waits under the water.",
                 "river, pathway, stalk, chase, attack"),
    "rule": ("A strict old rule (never answer a voice that calls your name, never whistle after dark...) and the story of the one who broke it.",
             "pathway, field, hide, chase, attack"),
    "bloodline": ("A family carries the change from one generation to the next, and tonight it is someone's turn.",
                  "home, village, transform, door, aftermath"),
    "caravan": ("A group of travellers goes missing and a lone survivor tells what followed them.",
                "pathway, hills, chase, hide, attack"),
    "mimic": ("The creature copies the voice of someone you love, calling to you from the dark outside.",
              "home, door, window, forest, attack"),
    "harvest": ("Something moves through the fields at night while the crops sway, and the scarecrows are in the wrong place at dawn.",
                "field, hide, footprints, chase, attack"),
    "reflection": ("In still water or a mirror the creature appears first, long before it is really there.",
                   "river, home, window, transform, attack"),
    "blizzard": ("Lost in snow or fog, a stranger offers warmth and shelter. Their tracks do not match their feet.",
                 "hills, footprints, hide, stalk, attack"),
}
HOOKS = ("Open with a rule: \"Never <do something> after midnight in <place>.\"",
         "Open with a sound: a sound the listener should dread, repeated, for example \"Three knocks. Always three.\"",
         "Open with a question: \"Have you ever felt someone <doing something> behind you?\"",
         "Open with the old people's warning, for example \"In <place>, the elders say...\"",
         "Open with a scene already under way, one calm detail that feels wrong.",
         "Open in the classic form \"If <something> follows you at night in <place>... don't look back.\"")
ENDINGS = ("a tragic ending: the victim is simply never seen again",
           "a narrow escape: someone survives, but is changed by what they saw",
           "a twist: it turns out the narrator, or the one who was saved, is the creature",
           "an unresolved ending: the sound at the door is still there tonight",
           "a warning ending: the rule that would have saved them")
PALETTE_NAMES = ("ash", "bone", "rust", "green", "blue", "violet", "charcoal", "yellow", "crimson", "teal")
# Modern, first-person horror that happens indoors. Each one: (premise, the scenes that tend to fit it)
MODERN_TYPES = {
    "mimic": ("The narrator hears a loved one (a mother, a husband, a child, a friend) calling from outside or from another room, but "
              "that person is not home. The voice is perfect, except for one wrong word. The creature is copying them.",
              "bedroom, door, window, hallway, woods, jump"),
    "basement": ("Since a break-in two weeks ago that killed the narrator's security guard and his pet dog, a slow tapping starts under the "
                 "floor at the same minute every night. Curiosity wins, the narrator goes down and sees the creature, which holds the very "
                 "weapon the investigators found at the murder scene.", "bedroom, stairs, basement, jump"),
    "camera": ("The narrator's home camera sends a motion alert at the same minute every night. The footage shows something standing "
               "in the empty living room, a little nearer to the lens each night.", "bedroom, cctv, hallway, jump"),
    "nightshift": ("The narrator works a lonely night shift in a building that should be empty (a warehouse, a hospital wing, a car park). "
                   "The security camera shows something walking the aisles that is not on any staff list.", "cctv, hallway, door, jump"),
    "monitor": ("A baby monitor crackles at the same minute every night and a voice that is not the parent's is whispering to the child. "
                "The narrator follows the sound.", "bedroom, hallway, door, jump"),
    "corridor": ("An apartment or hotel corridor where the lights die one by one, and each time they come back something at the "
                 "far end is nearer. The narrator has to walk it to reach their own door.", "hallway, door, window, jump"),
    "ceiling": ("The narrator hears scratching from the attic or ceiling at the same minute every night, and one night looks up.",
                "bedroom, hallway, stairs, jump"),
    "knock": ("Someone knocks three times on the narrator's front door at the same minute every night. The peephole shows nobody. "
              "Tonight the knocks come from the inside of the house.", "door, window, curtain, hallway, jump"),
    "woods": ("Lost in a dense, dark wood at night (a camping trip, a shortcut, a search for someone). Something pale keeps pace between "
              "the trees, and it knows the narrator's name, or sounds exactly like a friend calling for help.", "woods, hallway, jump"),
    "nextdoor": ("The new neighbour next door never leaves, never turns lights on, and yet someone stands at their window at the same minute "
                 "every night. The narrator finally looks closer, or goes over.", "neighbour, bedroom, door, jump"),
    "lamp": ("The narrator buys an old brass lamp at a flea market or an estate sale. It grants wishes, and every wish takes something "
             "back, until the last wish and what comes out of the smoke.", "lamp, bedroom, mirror, jump"),
    "road": ("A late drive home on an empty road. A figure stands in the headlights, and each time the road bends it is closer, "
             "although the car never stops.", "road, woods, mirror, jump"),
    "mirror": ("An antique mirror in the house shows the room a little wrong every night: one more thing in the reflection that "
               "is not in the room.", "mirror, bedroom, hallway, jump"),
    "sea": ("A fishing boat, a research ship or a small yacht far from land at night. Something vast moves under the hull "
            "and the sea itself starts to behave wrongly. The narrator survived and tells what rose up out of the water.",
            "ocean, tsunami, giant"),
    "lake": ("A calm freshwater lake with a dock, a cabin and thick mist. The water is too still, ripples move against the wind, "
             "and the old locals warn about what sleeps in the deep part.", "lake, woods, giant"),
    "lava": ("A volcano that should be dormant wakes. Villagers, geologists or hikers see something stand up out of the "
             "molten rock, and the eruption is only its first breath.", "lava, fly, giant"),
    "storm": ("Storm chasers or a farm family watch a tornado cross open land. Inside the funnel something huge is looking at "
              "them, and the storm turns to follow.", "tornado, road, giant"),
    "tsunami": ("A creature that has slept under the sea for centuries is woken (a drilling rig, a blast, a ship's anchor). The "
                "sea pulls back from the coast, and a wall of water rises with the creature behind it.", "tsunami, ocean, giant"),
    "titan": ("A titan five hundred feet tall walks out of the cloud bank over a mountain range. The narrator is far below, "
              "and the whole sky seems to turn its head. Clouds drift across its face as it looks down.", "giant, woods, road"),
}
NATURE_TYPES = ("sea", "lake", "lava", "storm", "tsunami", "titan")
NATURE_BODIES = {"sea": ("serpent", "beast"), "lake": ("serpent", "hollow", "stalker"), "lava": ("dragon", "beast", "stalker"),
                 "storm": ("dragon", "wraith", "stalker", "owlman"), "tsunami": ("beast", "serpent"),
                 "titan": ("theropod", "dragon", "beast", "stalker", "hollow")}

# Prehistoric horror: the age of dinosaurs, told as a story about a new apex predator. Each type: (plot, what each of the 8 sentences does).
# The scene for every sentence is fixed by the type, so the pictures always follow the story in order.
PRE_SCENES = ("valley", "erupt", "birth", "clash", "skies", "reign")
PRE_TYPES = {
    "volcano": ("For millions of years the tyrant lizard (T-rex) has been the undisputed apex predator of its valley. A volcano that had "
                "slept for ages erupts, and in the molten crater a NEW apex predator is born from fire and ash: bigger, hungrier, with "
                "glowing cracks of lava across its body. It challenges the old king and ends his reign.",
                ["valley", "valley", "erupt", "erupt", "birth", "birth", "clash", "reign"],
                ["Hook: open in the age of the dinosaurs and say, in one vivid line, that one animal, the T-rex, ruled this valley and nothing dared hunt it.",
                 "Show the valley: herds, long-necked giants and flying reptiles, all of them keeping away from the king. Name the king's roar.",
                 "A volcano that had slept for ages begins to shake. The ground cracks, the animals fall silent.",
                 "It erupts. Ash, fire, thunder. Every animal in the valley runs, and for the first time even the king runs.",
                 "In the molten crater something moves. Heat and ash gather into a body: describe a creature being born from the fire, with glowing cracks across it.",
                 "It opens its eyes and takes its first breath. It is already bigger than the king, and the ground trembles under it.",
                 "The old king, proud, goes to face it. One strike. Imply, do not describe, what happens to the king.",
                 "The valley has a new ruler. End with a question for viewers that fits this story."]),
    "meteor": ("It is the age of the dinosaurs. A meteor shower rains fire across the valley. In the crater left by the biggest fragment "
               "something crawls out that was not born on Earth: a new predator that hunts even the old king.",
               ["valley", "valley", "erupt", "erupt", "birth", "birth", "clash", "reign"],
               ["Hook: open in the age of the dinosaurs with one vivid line about the old king, the T-rex, and a night sky that looked ordinary.",
                "Show the valley: herds, long-necked giants, flying reptiles, and a strange extra star in the sky.",
                "The star splits into many. The first burning rocks fall and the animals look up.",
                "The sky burns. Fireballs crash into the valley and every animal runs, even the king.",
                "In the biggest crater something stirs under the glowing rock. Describe the first sign of it, not from this world.",
                "It climbs out of the crater and turns its head. It is already bigger than the king and it is looking for prey.",
                "The old king roars and goes to face it. One strike. Imply, do not describe, what happens to the king.",
                "The valley has a new ruler. End with a question for viewers that fits this story."]),
    "dragon": ("The skies of the dinosaur age belong to the flying reptiles, and the ground belongs to the T-rex. A volcano erupts, and "
               "out of its smoke something is born that is neither: the first dragon. It scatters the flying reptiles and burns the old king.",
               ["valley", "skies", "erupt", "erupt", "birth", "skies", "clash", "reign"],
               ["Hook: open in the age of the dinosaurs: the sky belongs to the flying reptiles and the ground to the T-rex, and nothing has ever challenged either.",
                "Show the sky full of flying reptiles at sunset, and say they have never feared anything above them.",
                "A volcano wakes under the valley. The ground trembles and the horizon turns red.",
                "It erupts and the black smoke covers the sky. The herds run, and the king runs with them.",
                "In the burning crater something moves: wings, horns, fire. Describe the first dragon being born out of the volcano.",
                "It opens its wings and rises. The flying reptiles scatter, and for the first time they are the prey.",
                "The old king roars up at it. The dragon answers with fire. Imply, do not describe, what happens to the king.",
                "The sky and the ground have a new ruler. End with a question for viewers that fits this story."]),
}
PRE_BODIES = {"volcano": ("theropod",), "meteor": ("theropod",), "dragon": ("dragon",)}
PRE_CRACKS = {"volcano": True, "meteor": False, "dragon": True}
MODERN_BODIES = ("crawler", "stalker", "wraith", "owlman", "beast", "arachnid", "hollow")
BODY_HINT = ("beast = a four-legged hunting predator (dog, wolf, bear or bull-like); stalker = a tall thin antlered forest spirit; "
             "arachnid = a huge spider-like weaver or trapper; serpent = a giant snake or river creature; "
             "wraith = a floating ghost, banshee or cloaked spirit; owlman = a winged night bird or bat-thing; "
             "crawler = an upside-down man-dog thing that hangs from ceilings and crawls on walls; "
             "hollow = a far too tall, faceless pale man in a dark suit with a thin grin; "
             "djinn = a smoke genie with horns and a burning grin (only for lamp stories); "
             "dragon = a winged dragon with horns, a long neck and tail, breathing fire (fits volcano, storm, mountain and legend stories); "
             "theropod = a hulking T-rex-like dinosaur monster with a huge head and tiny arms (fits titan and prehistoric stories)")


# How the monster comes into the world. The key is also the kind of sign drawn in the sky.
ORIGINS = {
    "fullmoon": ("A FULL MOON. Only under its light does the creature walk; by day it is an ordinary person or animal.", "shepherd, hunter, girl"),
    "bluemoon": ("A rare BLUE MOON, seen once in years, opens a door that is normally shut.", "priest, girl, traveller"),
    "eclipse": ("A LUNAR ECLIPSE. The moon goes dark and blood red, and the thing is set free.", "priest, farmer, doctor"),
    "thunder": ("A violent THUNDERSTORM. A bolt of lightning strikes the earth and the creature rises where it hit.", "hunter, farmer, soldier"),
    "asteroid": ("A burning rock, an ASTEROID, crashes near the village. Whatever crawled out of the crater is not from here.", "shepherd, soldier, farmer"),
    "aliens": ("BEINGS FROM THE SKY descend in lights, looking for blood. Livestock is found drained by morning.", "farmer, soldier, girl"),
    "virus": ("A deadly VIRUS that mutates. One sick person or animal changes, and the sickness spreads at night.", "doctor, worker, soldier"),
    "shadow": ("The monster lives among the villagers in HUMAN FORM, a normal neighbour by day. Only its shadow does not match.", "traveller, priest, doctor"),
    "comet": ("A COMET returns every few decades. Each time it crosses the sky, the creature wakes.", "shepherd, priest, farmer"),
    "aurora": ("Strange LIGHTS ripple across the sky, the animals fall silent, and something walks in under them.", "hunter, shepherd, traveller"),
    "redsky": ("The sky turns BLOOD RED at dusk and hundreds of crows gather. It is the old warning.", "farmer, priest, girl"),
    "tomb": ("Workers break open something BURIED long ago. The ground cracks, and what lay there climbs out.", "worker, soldier, traveller"),
}


def _parse_folk_key(k):
    """folk:<animal>:<legend>|<body>|<type>|<palette>|<region>  ->  dict (older keys have fewer parts)."""
    if not k.startswith("folk:"):
        return None
    rest = k.split(":", 2)
    if len(rest) < 3:
        return None
    parts = rest[2].split("|")
    parts += [""] * (6 - len(parts))
    return {"animal": rest[1], "legend": parts[0], "body": parts[1], "type": parts[2], "palette": parts[3], "region": parts[4],
            "origin": parts[5]}


def _format_of(p):
    return "modern" if p["type"] in MODERN_TYPES else "prehistoric" if p["type"] in PRE_TYPES else "legend"


def folk_variety(history):
    """Decide the plot shape, hook, ending, region and monster look for the next legend, avoiding recent ones."""
    past = [p for p in (_parse_folk_key(k) for k in history) if p]
    def recent(field, n):
        return [p[field] for p in past[-n:] if p[field]]
    # three kinds of story take turns: old legends, modern first-person horror and prehistoric horror (never the same kind three times in a row)
    fmts = [_format_of(p) for p in past]
    weights = {"modern": 0.4, "legend": 0.3, "prehistoric": 0.3}
    if "prehistoric" not in fmts[-6:]:
        weights["prehistoric"] = 0.6  # make sure the prehistoric stories keep showing up
    if len(fmts) >= 2 and fmts[-1] == fmts[-2]:
        weights[fmts[-1]] = 0.0
    fmt = random.choices(list(weights), weights=list(weights.values()))[0]
    pool = {"modern": MODERN_TYPES, "legend": STORY_TYPES, "prehistoric": PRE_TYPES}[fmt]
    types = [k for k in pool if k not in recent("type", len(pool) // 2 + 1)] or list(pool)
    allowed = MODERN_BODIES if fmt == "modern" else ("beast", "stalker", "arachnid", "serpent", "wraith", "owlman", "dragon")
    bodies = [b for b in allowed if b not in recent("body", 3)] or [allowed[0]]
    palettes = [c for c in PALETTE_NAMES if c not in recent("palette", 4)] or list(PALETTE_NAMES)
    regions = [r for r in FOLK_REGIONS if r not in recent("region", 6)] or FOLK_REGIONS
    origins = [k for k in ORIGINS if k not in recent("origin", 6)] or list(ORIGINS)
    stype = random.choice(types)
    if fmt == "prehistoric":
        bodies = list(PRE_BODIES[stype])
    elif stype in NATURE_BODIES:
        bodies = [b for b in NATURE_BODIES[stype] if b not in recent("body", 3)] or list(NATURE_BODIES[stype])
    if stype == "lamp":
        bodies = ["djinn"]
    elif "djinn" in bodies:
        bodies = [b for b in bodies if b != "djinn"] or ["hollow"]
    return {"format": fmt, "origin": random.choice(origins), "type": stype, "hook": random.choice(HOOKS), "ending": random.choice(ENDINGS),
            "bodies": bodies, "palettes": palettes, "region": random.choice(regions),
            "legends": [p["legend"] for p in past if p["legend"]]}


def ask_folklore(history):
    var = folk_variety(history)
    region = var["region"]
    used = var["legends"]
    modern = var["format"] == "modern"
    if var["format"] == "prehistoric":
        return ask_prehistoric(var)
    if modern:
        return ask_modern(var)
    plot, scene_hint = STORY_TYPES[var["type"]]
    origin_text, origin_roles = ORIGINS[var["origin"]]
    prompt = f"""You write narration for 40-second YouTube Shorts that retell DARK FOLKLORE: old legends about
people who turn into animals or creatures that follow travellers at night.

Pick one real, well-known legend of shapeshifting or a night creature from: {region}.
Do not reuse these legends: {used or "none yet"}.
The story must follow this plot shape: {plot}
HOW THE MONSTER ENTERS THE WORLD (the sign, and the heart of this story): {origin_text}
Characters who fit: {origin_roles}. Show this sign in the story as its own moment, early (sentence 2 or 3).
Scenes that tend to fit this shape (use your own judgement): {scene_hint}.
Ending: {var["ending"]}.

Return ONLY valid JSON, no markdown, in exactly this shape:
{{
  "legend_name": "the name of the legend or creature, 1 to 3 words",
  "region": "short place name, for example Kerala, India",
  "animal": "ONE word from this list that best matches the creature: {", ".join(FOLK_ANIMALS)}",
  "title": "catchy title under 70 characters, no hashtags",
  "narrator": {{"gender": "male or female", "persona": "who is telling this, for example an old villager, a frightened witness, a grandmother"}},
  "hero": "the main person, ONE of: traveller, farmer, hunter, priest, shepherd, doctor, soldier, girl, worker",
  "people": "who walks with the hero: one of alone, child, elder, pair, dog",
  "monster": {{"body": "ONE of: {", ".join(var["bodies"])}", "palette": "ONE of: {", ".join(var["palettes"])}",
              "eyes": "one of: red, gold, green, white, violet, orange, blue", "size": "small, medium or huge",
              "horn": "none, antlers, ram, twin or bull (only matters for body beast)", "spine": "bone, long or none",
              "eye_count": "a number from 2 to 6"}},
  "sentences": [{{"text": "one spoken sentence", "beat": "one scene word from the list below", "setting": "one place word from the list below", "delivery": "one voice mood from the list below", "speaker": "narrator, person, creature, mimic or host", "voice": "only for person and mimic lines: man, woman, boy, girl, old_man or old_woman"}}]
}}

Rules for "sentences" (7 to 9 items, 65 to 85 words in total (the voice reads slowly, about 1.6 words per second, and a Short must stay under 50 seconds, so NEVER go over 90 words), plain spoken English, no emojis):
- Sentence 1: a gripping hook. {var["hook"]}
- Retell the legend as "the old tales say". Never present it as true or as something that really happens.
- Be respectful of the culture it comes from. Do not mock any people, religion or community.
- Horror is welcome: dread, screams, attacks and their terrifying aftermath. Show harm through implication and
  aftermath (a scream cut off, a dropped lantern, blood on the path, a smear where someone was dragged away).
  Never describe injuries or body parts in explicit detail, and never name real people as victims.
- Every sentence gets a "beat": the ONE scene word that best shows what that sentence says. Pick from:
  "walk" (a person walks through the night), "footprints" (footsteps are mentioned: a person's prints appear and huge
  clawed prints follow inside them), "home" (a person goes to or reaches their house), "village" (the sleeping village,
  lights going out), "window" (something watches or scratches at a window), "door" (something pounds at a locked door),
  "transform" (a person changes into the creature), "chase" (someone runs, the creature gives chase), "hide" (someone
  hides while it passes), "mob" (villagers with torches hunt it), "stalk" (the creature closes in), "dig" (the creature digs in the earth, dirt flying: use it whenever the narration says
  it or anyone is digging, burying or unearthing something), "fly" (the creature unfolds wings and takes off into the
  sky: use it whenever the narration says it flies, soars or takes off), "attack" (it strikes),
  "aftermath" (the horrible quiet after), "omen" (a wide shot of the sign in the sky or ground: use it ONCE, for the
  sentence that shows how the monster enters the world), and five CAMERA ANGLES: "pov" (first person through the hero's
  eyes, turning to see it coming), "mpov" (first person through the monster's eyes as it hunts), "curtain" (seen from
  inside a house through half-closed curtains while someone is chased outside), "shoulder" (over the hero's shoulder as
  it approaches), "loom" (a low tilted angle, the monster towering over the hero). Use at least 2 camera angles.
  Use at least 6 different scene words, never the same one twice in a row, and use "attack" once, followed by "aftermath".
  The first sentence must be "walk"; the last should be "stalk" or "aftermath".
- Every sentence also gets a "setting", the place where that sentence happens: "pathway" (an eerie road), "forest" (a dark
  forest), "graveyard", "river" (a misty river bank), "village" (houses and a street), "field" (a moonlit field),
  "hills" (a stormy hillside). Follow the story's journey, for example pathway, then forest, then village, and use at
  least 3 different settings. Put "home" and "village" scenes in "village".
- Spoken lines: in 1 to 3 sentences someone SPEAKS inside the story. For those sentences set "speaker" to "person" (another
  human: a friend, an officer, a child), "creature" (the monster talking in its own voice) or "mimic" (the creature COPYING
  the voice of someone the victim loves or trusts, such as a son, a mother, a friend, a sheriff). Also set "voice" for
  person and mimic lines: man, woman, boy, girl, old_man or old_woman, whoever is speaking. A spoken sentence contains ONLY
  the spoken words (2 to 12 words, no quotation marks, no "he said") and the sentence just before it is a narrator sentence
  that introduces the speaker. IMPORTANT: whenever the narration says that someone calls out, shouts, whispers or says
  something, the NEXT sentence must be that spoken line, so the audience hears it in another voice, and a mimic line
  can be followed by a short second one that fits ("Wait for me, son!", "It's me! The sheriff!", "Open the door, it's cold").
  All other sentences have "speaker": "narrator". The first and last sentence are always the narrator, and never more than
  two spoken lines in a row.
- Storyteller frame: tell the story as something that happened to the narrator and a friend ("my cousin and I"). In 2 or 3 places
  (never the first or last sentence) add a short first-person line from the narrator ("I was there that night.", "We should have
  turned back.", "I still hear it.") with "frame": true. Those lines must not describe a new event, only the narrator recalling it.
- Host reactions: in 1 or 2 places add one short sentence with "speaker": "host" and "voice": "host", the narrator's curious
  friend reacting out loud in 2 to 6 words to the sentence just before it ("Wait, WHAT?!", "It did what?!", "Who was that?").
  A host line is never the first or last sentence and never sits next to a person, mimic or creature line.
- Every sentence also gets a "delivery", how the narrator's VOICE should sound on that line. Pick from: calm, hushed,
  whisper, urgent (fast and frightened), trembling, shout (a sudden terrified high shout, for the attack), low (slow and
  deep, ominous), sorrow, awe, dread. Vary them so the voice swings between quiet and sudden, high and deep.
- "people": "alone" if the hero is by themselves, otherwise who walks with them (child, elder, pair, dog).
- The second to last sentence builds to the moment of danger.
- Last sentence: a question for viewers that fits THIS story (not always "Would you turn around?").
- "monster": design the creature so it matches THIS legend. Body guide: {BODY_HINT}. Choose only from the allowed bodies
  and colours listed above, because the other ones were used recently.
"""
    url = f"{API_BASE}/models/{GEMINI_MODEL}:generateContent"
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 1.0, "responseMimeType": "application/json"}}
    last = None
    for attempt in range(3):
        try:
            r = requests.post(url, params={"key": GEMINI_API_KEY}, json=body, timeout=90)
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
            data = json.loads(text)
            sents = [s for s in data["sentences"] if isinstance(s, dict) and str(s.get("text", "")).strip()]
            assert data["title"] and data["legend_name"] and len(sents) >= 5
            for s in sents:
                s["text"] = s["text"].strip()
            data["sentences"] = sents[:11]
            for k, x in enumerate(data["sentences"]):
                sp_ = str(x.get("speaker", "narrator")).lower()
                x["frame"] = bool(x.get("frame"))
                x["speaker"] = sp_ if sp_ in ("person", "creature", "mimic", "host") else "narrator"
                vv = str(x.get("voice", "")).lower().replace(" ", "_")
                x["voice"] = vv if vv in ("man", "woman", "boy", "girl", "old_man", "old_woman", "host") else ""
                if x["speaker"] == "host":
                    x["voice"] = "host"
                prev2 = [data["sentences"][j]["speaker"] for j in (k - 1, k - 2) if j >= 0]
                if k in (0, len(data["sentences"]) - 1) or (len(prev2) == 2 and all(p != "narrator" for p in prev2)):
                    x["speaker"] = "narrator"
            data["sentences"] = _enforce_voices(data["sentences"])
            if data.get("animal") not in FOLK_ANIMALS:
                data["animal"] = "wolf"
            data["region"] = str(data.get("region") or region).strip()
            m = data.get("monster") if isinstance(data.get("monster"), dict) else {}
            if m.get("body") not in var["bodies"]:
                m["body"] = random.choice(var["bodies"])
            if m.get("palette") not in var["palettes"]:
                m["palette"] = random.choice(var["palettes"])
            if m.get("eyes") not in ("red", "gold", "green", "white", "violet", "orange", "blue"):
                m["eyes"] = random.choice(["red", "gold", "green", "white", "violet", "orange"])
            m["horn"] = m.get("horn") if m.get("horn") in ("antlers", "ram", "twin", "bull") else random.choice([None, None, "antlers", "ram", "twin", "bull"])
            m["size"] = m.get("size") if m.get("size") in ("small", "medium", "huge") else "medium"
            data["monster"] = m
            data["story_type"] = var["type"]
            data["origin"] = var["origin"]
            if data.get("hero") not in ("traveller", "farmer", "hunter", "priest", "shepherd", "doctor", "soldier", "girl", "worker"):
                data["hero"] = random.choice([r.strip() for r in origin_roles.split(",")])
            if data.get("people") not in ("alone", "child", "elder", "pair", "dog"):
                data["people"] = "alone"
            if not isinstance(data.get("narrator"), dict):
                data["narrator"] = {}
            _word_guard(data, attempt)
            return data
        except Exception as e:
            last = e
            log(f"Gemini folklore attempt {attempt + 1} failed: {e}")
    raise RuntimeError(f"Gemini failed 3 times: {last}")


_CALL_RE = re.compile(r"\b(call(?:ed|s|ing)?|shout(?:ed|s|ing)?|yell(?:ed|s|ing)?|whisper(?:ed|s|ing)?|cr(?:ied|ies)|hear(?:d|s)?)\b[^.]{0,30}\b(name|voice|out)\b", re.I)
_MIMIC_FOLLOW = {"boy": ["Mommy, wait for me!", "Daddy, I'm scared out here!"], "girl": ["Wait for me! Please!", "Mama, open the door!"],
                 "woman": ["It's me. Please, open the door.", "Come here, it's cold out here."],
                 "man": ["It's me! Open the door!", "Wait for me, son!"],
                 "old_man": ["Wait for me, I can't walk fast.", "It's me. It's your father."],
                 "old_woman": ["Wait for me, dear.", "It's me, child. Open up."]}


def _enforce_voices(sents):
    """Guarantee what the story promised: when the narration says somebody called out, a DIFFERENT voice is heard calling;
    when a creature copies a loved one's voice, it follows up with a second line."""
    out = []
    for idx, x in enumerate(sents):
        out.append(x)
        last = idx == len(sents) - 1
        nxt = sents[idx + 1] if not last else None
        gap = nxt is not None and nxt.get("speaker", "narrator") == "narrator"
        if x.get("speaker") == "narrator" and idx > 0 and gap and _CALL_RE.search(x["text"]) and len(out) < 12:
            skip = ("i", "the", "he", "she", "it", "they", "his", "her", "then", "but", "and", "from", "in", "a", "officer", "sheriff", "deputy")
            caps = [w.strip(",.;:!?'\"") for w in x["text"].split()[1:] if w[:1].isupper()]
            caps = [c for c in caps if c.lower() not in skip and len(c) > 1]
            nm = re.sub(r"'s$", "", caps[0]) if caps else ""
            txt = (f"{nm}! {nm}, over here!" if nm else "Hey! Over here! Come quick!")
            who = "old_man" if re.search(r"grand|old man|elder", x["text"], re.I) else ("woman" if re.search(r"\b(wife|mother|mama|woman)\b", x["text"], re.I) else "man")
            out.append({"text": txt, "beat": nxt.get("beat", x.get("beat")), "setting": nxt.get("setting", x.get("setting")),
                        "delivery": "urgent", "speaker": "person", "voice": who})
        elif x.get("speaker") == "mimic" and not last and gap and len(out) < 12:
            vo = x.get("voice") or "man"
            out.append({"text": random.choice(_MIMIC_FOLLOW.get(vo, _MIMIC_FOLLOW["man"])), "beat": x.get("beat"),
                        "setting": x.get("setting"), "delivery": "hushed", "speaker": "mimic", "voice": vo})
    return out




def ask_modern(var):
    used = var["legends"]
    plot, scene_hint = MODERN_TYPES[var["type"]]
    times = ["2:48", "3:07", "3:33", "1:14", "4:04", "2:19", "3:51", "12:37"]
    time_hint = random.choice(times)
    nature = var["type"] in NATURE_TYPES
    where = ("OUTDOORS in nature on a vast scale (open sea, a lake, a volcano, a storm, a coastline, mountains), where the "
             "danger is as big as the landscape" if nature else
             "in a normal present-day home or building, or somewhere close to one")
    last_scene = "giant" if nature else "jump"
    prompt = f"""You write narration for 40-second YouTube Shorts: original, modern, FIRST-PERSON horror stories told by someone
who lived through it, set {where}. They are fiction.

Story shape: {plot}
Use the time {time_hint} as the exact minute when it happens (you may pick another time if it fits better).
Do not reuse these names or ideas: {used or "none yet"}.
Ending: {var["ending"]}.

Make it concrete and specific, not generic: a believable narrator (age, job), real small details (what was said by the
police, the day it started, the sound, an object that does not belong), and one detail that turns cold in the last line.

Return ONLY valid JSON, no markdown, in exactly this shape:
{{
  "legend_name": "what the thing is called, 1 to 3 words, for example The Hanging Man",
  "region": "short place and time, for example A house in Ohio, 2:48 AM",
  "time": "the minute it happens, like 2:48",
  "animal": "ONE word from this list that best matches the creature: {", ".join(FOLK_ANIMALS)}",
  "title": "catchy title under 70 characters, no hashtags",
  "narrator": {{"gender": "male or female", "persona": "the narrator, for example a night-shift nurse telling it a year later"}},
  "hero": "traveller",
  "people": "alone",
  "monster": {{"body": "ONE of: {", ".join(var["bodies"])}", "palette": "ONE of: {", ".join(var["palettes"])}",
              "eyes": "one of: red, gold, green, white, violet, orange, blue", "size": "small, medium or huge",
              "horn": "none", "spine": "bone, long or none", "eye_count": "a number from 2 to 6"}},
  "sentences": [{{"text": "one spoken sentence", "beat": "one scene word from the list below", "delivery": "one voice mood from the list below", "speaker": "narrator, person, creature, mimic or host", "voice": "only for person and mimic lines: man, woman, boy, girl, old_man or old_woman"}}]
}}

Rules for "sentences" (7 to 9 items, 65 to 85 words in total (the voice reads slowly, about 1.6 words per second, and a Short must stay under 50 seconds, so NEVER go over 90 words), plain spoken English, first person "I", no emojis):
- Sentence 1: a gripping hook. {var["hook"]}
- Horror is welcome: dread, noises, something wrong. Show harm only through implication (a police detail, a stain, a silence).
  Never describe injuries in explicit detail, and never name real people.
- Every sentence gets a "beat": the ONE scene that best shows it. Pick from: "bedroom" (lying awake in bed, the red clock
  showing the time, a tapping from below), "stairs" (creeping down creaking stairs with a flashlight), "basement" (first
  person, flashlight sweeping a dark basement until it catches the creature), "hallway" (a long corridor where the lights
  die one by one and a figure comes nearer), "cctv" (night-vision camera footage with a timestamp), "door" (something
  knocks or pounds at a door), "window" (something watches at a window), "curtain" (seen from behind half-closed
  curtains), "woods" (first person through a dense, misty, dark wood with trunks crowding in and something pale walking
  between them), "neighbour" (a night street, the neighbour's house, a figure at the lit window), "lamp" (a glowing old
  brass lamp on a table, smoke rising and forming the creature), "road" (driving at night, a figure in the headlights
  nearer every time), "mirror" (an oval antique mirror in a dark room that shows something wrong, then cracks),
  "ocean" (a small boat with a lantern on a black sea, a vast shadow sliding under it, then the creature surfaces), "lake"
  (a still foggy lake, ripples against the wind, a head rising slowly), "lava" (a volcano and a lake of lava, the creature
  rising out of the molten rock), "tornado" (a black funnel over farmland with lightning and something looking out of it),
  "tsunami" (the sea draws back, a huge wall of water climbs the horizon with the creature's face rising behind it),
  "giant" (a creature five hundred feet tall, its face emerging from the clouds above mountains: the finale for nature
  stories), "retreat" (night coast: the tide drains away, bare mud, gasping fish; nature stories), "tidelog" (inside a control
  tower or lighthouse: a person checks logbooks while the sea outside draws back), "radio" (a person on a cliff road speaks into a
  radio, the signal bars fall and the static takes over), "harbor" (a harbour with lit houses and boats; the water swallows it,
  nature stories), "rise" (a black mountain of water rises out of a trench, weed and bone on its crest, eyes opening; nature
  stories), "glass" (eyes glowing behind rain-streaked reinforced glass that begins to crack), "emerge" (something drags itself out of the ground with a huge ghostly shape watching from the sky), "swirl" (water rings and foams into a swirl and a shape slowly surfaces), "abyss" (cast down into a red pit), "dawn" (cold night turns to warm first light), "ridge" (the giant creature
  crests a ridge and blots out the stars; nature stories). IMPORTANT: choose the beat that shows what THIS sentence says, and use
  a different beat for every sentence whenever you can, "dig" (the creature digs in the earth, dirt flying, only if the story really has digging), "fly" (the creature unfolds
  wings and takes off, only if the story really has flying), "jump" (a dark room, two eyes open, then the face is
  suddenly in the lens, indoor stories only). Whenever the narration describes the creature doing something that can be drawn (digging,
  flying, climbing), pick the beat that shows it. Use at least 4 different scene
  words and never the same one twice in a row. Scenes that tend to fit this story: {scene_hint}.
  The LAST sentence should be "{last_scene}". The first sentence should open in the story's main place (for example "woods", "road", "neighbour", "lamp",
  "mirror", "bedroom", "cctv" or "door"), whichever fits.
- Spoken lines: in 1 to 3 sentences someone SPEAKS inside the story. For those sentences set "speaker" to "person" (another
  human: a friend, an officer, a child), "creature" (the monster talking in its own voice) or "mimic" (the creature COPYING
  the voice of someone the victim loves or trusts, such as a son, a mother, a friend, a sheriff). Also set "voice" for
  person and mimic lines: man, woman, boy, girl, old_man or old_woman, whoever is speaking. A spoken sentence contains ONLY
  the spoken words (2 to 12 words, no quotation marks, no "he said") and the sentence just before it is a narrator sentence
  that introduces the speaker. IMPORTANT: whenever the narration says that someone calls out, shouts, whispers or says
  something, the NEXT sentence must be that spoken line, so the audience hears it in another voice, and a mimic line
  can be followed by a short second one that fits ("Wait for me, son!", "It's me! The sheriff!", "Open the door, it's cold").
  All other sentences have "speaker": "narrator". The first and last sentence are always the narrator, and never more than
  two spoken lines in a row.
- Storyteller frame: tell the story as something that happened to the narrator and a friend ("my cousin and I"). In 2 or 3 places
  (never the first or last sentence) add a short first-person line from the narrator ("I was there that night.", "We should have
  turned back.", "I still hear it.") with "frame": true. Those lines must not describe a new event, only the narrator recalling it.
- Host reactions: in 1 or 2 places add one short sentence with "speaker": "host" and "voice": "host", the narrator's curious
  friend reacting out loud in 2 to 6 words to the sentence just before it ("Wait, WHAT?!", "It did what?!", "Who was that?").
  A host line is never the first or last sentence and never sits next to a person, mimic or creature line.
- Every sentence also gets a "delivery": calm, hushed, whisper, urgent, trembling, shout, low, sorrow, awe or dread.
- The second to last sentence builds to the moment of seeing it.
- Last sentence: a question for viewers that fits THIS story (for example "Would you have gone down there?").
- "monster": design the creature so it matches the story and the place. Body guide: {BODY_HINT}. Choose only from the
  allowed bodies and colours listed above. Do NOT make it a man-dog unless the story truly calls for it.
- Keep the narrator specific and varied: not always a homeowner. It may be a camper, a student, a driver, a nurse, a
  collector, a new tenant. The story is allowed to take place outdoors or in another person's house, as the story shape says.
"""
    url = f"{API_BASE}/models/{GEMINI_MODEL}:generateContent"
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 1.0, "responseMimeType": "application/json"}}
    last = None
    for attempt in range(3):
        try:
            r = requests.post(url, params={"key": GEMINI_API_KEY}, json=body, timeout=90)
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
            data = json.loads(text)
            sents = [x for x in data["sentences"] if isinstance(x, dict) and str(x.get("text", "")).strip()]
            assert data["title"] and data["legend_name"] and len(sents) >= 5
            for x in sents:
                x["text"] = x["text"].strip()
            data["sentences"] = sents[:11]
            for k, x in enumerate(data["sentences"]):
                sp_ = str(x.get("speaker", "narrator")).lower()
                x["frame"] = bool(x.get("frame"))
                x["speaker"] = sp_ if sp_ in ("person", "creature", "mimic", "host") else "narrator"
                vv = str(x.get("voice", "")).lower().replace(" ", "_")
                x["voice"] = vv if vv in ("man", "woman", "boy", "girl", "old_man", "old_woman", "host") else ""
                if x["speaker"] == "host":
                    x["voice"] = "host"
                prev2 = [data["sentences"][j]["speaker"] for j in (k - 1, k - 2) if j >= 0]
                if k in (0, len(data["sentences"]) - 1) or (len(prev2) == 2 and all(p != "narrator" for p in prev2)):
                    x["speaker"] = "narrator"
            data["sentences"] = _enforce_voices(data["sentences"])
            if data.get("animal") not in FOLK_ANIMALS:
                data["animal"] = "wolf"
            data["region"] = str(data.get("region") or "A house at night").strip()
            tm = str(data.get("time") or "").strip()
            data["time"] = tm if re.fullmatch(r"\d{1,2}:\d{2}", tm) else time_hint
            m = data.get("monster") if isinstance(data.get("monster"), dict) else {}
            if m.get("body") not in var["bodies"]:
                m["body"] = random.choice(var["bodies"])
            if m.get("palette") not in var["palettes"]:
                m["palette"] = random.choice(var["palettes"])
            if m.get("eyes") not in ("red", "gold", "green", "white", "violet", "orange", "blue"):
                m["eyes"] = random.choice(["red", "gold", "green", "white", "violet", "orange"])
            m["horn"] = None
            m["size"] = m.get("size") if m.get("size") in ("small", "medium", "huge") else "medium"
            data["monster"] = m
            data["story_type"] = var["type"]
            data["format"] = "modern"
            data["origin"] = ""
            data["hero"], data["people"] = "traveller", "alone"
            if not isinstance(data.get("narrator"), dict):
                data["narrator"] = {}
            _word_guard(data, attempt)
            return data
        except Exception as e:
            last = e
            log(f"Gemini modern story attempt {attempt + 1} failed: {e}")
    raise RuntimeError(f"Gemini failed 3 times: {last}")


def ask_prehistoric(var):
    used = var["legends"]
    plot, stages, steps = PRE_TYPES[var["type"]]
    body = var["bodies"][0]
    steps_text = "\n".join(f"  {i + 1}. {t}" for i, t in enumerate(steps))
    prompt = f"""You write narration for 40-second YouTube Shorts: original PREHISTORIC HORROR stories set in the age of the dinosaurs,
told by a calm storyteller in the third person, past tense. They are fiction, not science.

Story shape: {plot}
Do not reuse these names or ideas: {used or "none yet"}.

Write EXACTLY {len(steps)} sentences, one for each step below and in this order:
{steps_text}

Return ONLY valid JSON, no markdown, in exactly this shape:
{{
  "legend_name": "what the new predator is called, 1 to 3 words, a made-up creature name such as The Ashmaw or Emberfang",
  "region": "a short time and place, for example Late Cretaceous, 66 million years ago",
  "title": "catchy title under 70 characters, no hashtags",
  "narrator": {{"gender": "male or female", "persona": "a calm, warm storyteller, like a nature documentary narrator who is quietly afraid"}},
  "monster": {{"palette": "ONE of: {", ".join(var["palettes"])}", "eyes": "one of: red, gold, green, white, violet, orange, blue", "size": "medium or huge"}},
  "sentences": [{{"text": "one spoken sentence", "delivery": "one voice mood: calm, hushed, whisper, urgent, trembling, low, sorrow, awe or dread"}}],
  "host_lines": [{{"after": 3, "text": "Wait, WHAT?!"}}]
}}

"host_lines": ONE or TWO short reactions (2 to 6 words) from the storyteller's curious friend, said out loud right after the
sentence with that number (1 = first sentence). Never after sentence 1 or after the last sentence, never two in a row.

Rules for "sentences" (65 to 85 words in total, the voice reads slowly so NEVER go over 90 words, plain spoken English, no emojis, no quotation marks):
- Short, concrete, visual sentences: what can be SEEN and HEARD (ash, roars, glowing eyes, the ground shaking).
- Horror is welcome, but harm is only implied (a roar cut off, a silence, a trail). No explicit injuries.
- Do not use real people's names. Do not claim it is real science. The first sentence is a hook that makes people stay.
- The last sentence is a question for viewers that fits THIS story.
"""
    url = f"{API_BASE}/models/{GEMINI_MODEL}:generateContent"
    body_ = {"contents": [{"parts": [{"text": prompt}]}],
             "generationConfig": {"temperature": 1.0, "responseMimeType": "application/json"}}
    last = None
    for attempt in range(3):
        try:
            r = requests.post(url, params={"key": GEMINI_API_KEY}, json=body_, timeout=90)
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
            data = json.loads(text)
            sents = [x for x in data["sentences"] if isinstance(x, dict) and str(x.get("text", "")).strip()]
            assert data["title"] and data["legend_name"] and len(sents) >= 6
            for x in sents:
                x["text"] = x["text"].strip()
                x["speaker"], x["voice"] = "narrator", ""
            sents = sents[:11]
            hl, used_at = data.get("host_lines"), set()
            for h in (hl if isinstance(hl, list) else [])[:2]:      # the friend's reactions go in AFTER the story sentence they react to
                try:
                    at, tx = int(h["after"]), str(h["text"]).strip()
                except Exception:
                    continue
                if 1 < at < len(sents) and tx and len(tx.split()) <= 8 and at not in used_at and at - 1 not in used_at:
                    used_at.add(at)
            for at in sorted(used_at, reverse=True):
                tx = next(str(h["text"]).strip() for h in hl if int(h.get("after", -1)) == at)
                sents.insert(at, {"text": tx, "delivery": "urgent", "speaker": "host", "voice": "host"})
            data["sentences"] = sents
            data["region"] = str(data.get("region") or "Late Cretaceous, 66 million years ago").strip()
            m = data.get("monster") if isinstance(data.get("monster"), dict) else {}
            m["body"] = body
            if m.get("palette") not in var["palettes"]:
                m["palette"] = random.choice(var["palettes"])
            if m.get("eyes") not in ("red", "gold", "green", "white", "violet", "orange", "blue"):
                m["eyes"] = random.choice(["red", "gold", "orange"] if PRE_CRACKS[var["type"]] else ["green", "violet", "white"])
            m["size"] = "huge" if m.get("size") == "huge" else "medium"
            m["horn"] = None
            m["cracks"] = PRE_CRACKS[var["type"]]
            data["monster"] = m
            data["animal"] = "crocodile"
            data["story_type"] = var["type"]
            data["format"] = "prehistoric"
            data["origin"] = ""
            data["time"] = "2:48"
            data["hero"], data["people"] = "traveller", "alone"
            if not isinstance(data.get("narrator"), dict):
                data["narrator"] = {}
            _word_guard(data, attempt)
            return data
        except Exception as e:
            last = e
            log(f"Gemini prehistoric story attempt {attempt + 1} failed: {e}")
    raise RuntimeError(f"Gemini failed 3 times: {last}")


ATTACK_WORDS = ("attack", "lunge", "pounc", "tore", "torn", "bit ", "bite", "blood", "killed", "dragged", "devour",
                "scream", "slash", "mauled", "ripped", "snatched", "never seen again", "claws")


SCENE_FUNCS = ("dig", "fly", "bedroom", "stairs", "basement", "hallway", "cctv", "jump", "hook", "footprints", "home", "village", "window", "door", "transform", "chase", "hide", "mob",
               "omen", "pov", "mpov", "curtain", "shoulder", "loom", "emerge", "swirl", "abyss", "dawn", "hosts")
CAMERA_SCENES = ("pov", "mpov", "curtain", "shoulder", "loom")
MODERN_SCENES = ("bedroom", "stairs", "basement", "hallway", "cctv", "door", "window", "curtain", "jump",
                 "woods", "neighbour", "lamp", "road", "mirror", "dig", "fly",
                 "ocean", "lake", "lava", "tornado", "tsunami", "giant",
                 "retreat", "tidelog", "radio", "harbor", "rise", "glass", "ridge", "emerge", "swirl", "abyss", "dawn", "hosts")
SECOND_BEAT = {}   # sentence index -> a second story scene for the same sentence
STORY_SCENES = ("retreat", "tidelog", "radio", "harbor", "rise", "glass", "ridge", "emerge", "swirl", "abyss", "dawn", "hosts")   # scenes for one specific narrated event
STORY_NATURE = ("retreat", "harbor", "rise", "ridge")                                  # only for stories set in nature
MODERN_WORDS = (("emerge", ("out of the ground", "out of the earth", "from the soil", "clawed its way", "dug itself out", "from the grave", "burst from the ground", "broke through the ground")),
                ("swirl", ("foam", "whirlpool", "swirl", "ripples", "vortex", "bubbles rose")),
                ("abyss", ("hurled", "cast down", "plunged", "into the pit", "abyss", "bottomless", "fell into the dark")),
                ("dawn", ("sunrise", "first light", "morning came", "at daybreak", "the dawn", "as dawn")),
                ("radio", ("radio", "signal", "static", "transmission", "dispatch", "walkie")),
                ("tidelog", ("diesel", "logbook", "the logs", "my logs", "log book", "records", "gauge", "instrument panel", "control room", "checking the")),
                ("retreat", ("tide", "retreat", "receded", "drained", "bare mud", "seabed", "gasping fish", "water drew back", "water vanished")),
                ("harbor", ("harbor", "harbour", "the port", "quay", "wharf", "the pier")),
                ("glass", ("reinforced glass", "through the glass", "blinked open", "stared through", "staring right through", "pane")),
                ("ridge", ("ridge", "crested", "blotting", "blotted", "skyline", "over the hill", "over the crest")),
                ("rise", ("trench", "rose out", "rose from", "rose from the", "emerged from", "surfaced", "from the deep", "rising from", "mountain of")),
                ("tsunami", ("tsunami", "wall of water", "great wave")), ("tornado", ("tornado", "twister", "funnel")),
                ("lava", ("lava", "volcano", "magma", "erupt")), ("giant", ("giant", "titan", "towering", "colossal", "feet tall")),
                ("ocean", ("ship", "boat", "ocean", "the sea", "trawler")), ("lake", ("lake", "dock", "pond")),
("stairs", ("stairs", "steps down", "went down", "creak")), ("basement", ("basement", "cellar", "flashlight")),
                ("cctv", ("camera", "footage", "screen", "monitor", "recording")), ("hallway", ("hall", "corridor", "lights", "floor")),
                ("door", ("door", "knock", "peephole")), ("window", ("window", "glass")), ("curtain", ("curtain",)),
                ("dig", ("dig ", "digging", "dug ", "unearth", "shovel")), ("fly", ("flew", "flying", "wings", "soared", "took off")),
                ("woods", ("wood", "forest", "trees", "trail", "campsite")), ("neighbour", ("neighbor", "neighbour", "next door")),
                ("lamp", ("lamp", "wish", "genie", "smoke")), ("road", ("drive", "driving", "headlights", "highway", "car ")),
                ("mirror", ("mirror", "reflection")),
                ("bedroom", ("bed", "clock", "awake", "sleep", "night", "tapping")))
MODERN_FILL = ("bedroom", "hallway", "cctv", "door", "stairs", "basement", "window", "curtain")
# the places a story may use are limited to the ones that fit its type (the fill scenes are only used to repair repeats)
MODERN_PLACE_SCENES = {"woods": ("woods", "road", "dig", "fly"), "nextdoor": ("neighbour", "bedroom", "window", "door", "dig"),
                       "lamp": ("lamp", "bedroom", "mirror", "hallway"), "road": ("road", "woods", "mirror"),
                       "mirror": ("mirror", "bedroom", "hallway", "door"),
                       "sea": ("ocean", "tsunami", "giant", "retreat", "harbor", "rise", "ridge"), "lake": ("lake", "woods", "giant", "rise", "ridge"),
                       "lava": ("lava", "fly", "giant", "ridge", "harbor"),
                       "storm": ("tornado", "road", "giant", "ridge", "harbor"), "tsunami": ("tsunami", "ocean", "giant", "retreat", "harbor", "rise", "ridge"),
                       "titan": ("giant", "woods", "road", "ridge", "harbor")}
SCENE_INFO = {
    "retreat": ("The night coast: the tide drains away, leaving bare mud and gasping fish", "wide, slow push-in", "surf falling away, a low rumble"),
    "tidelog": ("Inside the control tower: a person checks the logbook while the sea outside draws back", "medium, lamp-lit", "pen scratch, a quiet hum"),
    "radio": ("A person on the cliff road speaks into a radio; the signal bars fall and the static takes over", "low, wind", "radio static, a dying signal"),
    "harbor": ("The harbour: lit houses and moored boats, then the water arrives and the lights go out", "wide, shaking", "surge, wood and glass breaking"),
    "rise": ("A black mountain of water rises out of the trench, weed and bone on its crest, eyes opening", "wide tilt up", "deep rumble, rushing water"),
    "glass": ("Eyes glowing behind rain-streaked reinforced glass that begins to crack", "locked, close", "glass creak, rain, a low heartbeat"),
    "ridge": ("The giant creature crests the ridge and blots out the stars", "low angle, slow", "ground tremor, heavy breath"),
    "hosts": ("The two hosts react to what was just said: a wide-eyed look, a gasp, a question", "medium two-shot, slight bob", "a soft sting"),
    "emerge": ("Something drags itself out of the ground while a huge ghostly shape watches from the sky", "slow tilt up, eased rise", "earth cracking, dust, a low groan"),
    "swirl": ("The water rings and foams into a swirl and a shape slowly surfaces", "slow push-in", "water ripple, a deep hum"),
    "abyss": ("Cast down into a red pit, rock walls rushing past, figures tumbling", "falling, shaking", "a long falling rumble"),
    "dawn": ("The light changes from cold night to warm first light as the sun climbs", "slow, still", "birdsong, soft wind"),
    "tsunami": ("The sea draws back, then a wall of water with the creature rising behind it", "wide", "rush, rumble"),
    "giant": ("The titan's face comes out of the clouds above the mountains", "low angle", "wind, a roar"),
}


def write_storyboard(sentences, beats, durs, settings, path):
    """The storyboard of the video: for each narration sentence, the picture that is made for it."""
    rows = []
    for k, sn in enumerate(sentences[:len(beats)]):
        info = SCENE_INFO.get(beats[k], ("the scene named '%s'" % beats[k], "", ""))
        rows.append({"narration": sn["text"], "speaker": sn.get("speaker", "narrator"), "scene": beats[k], "what_you_see": info[0],
                     "camera": info[1], "sound": info[2], "setting": settings[k] if k < len(settings) else "",
                     "seconds": round(durs[k], 2) if k < len(durs) else None})
    try:
        pathlib.Path(path).write_text(json.dumps(rows, indent=2))
    except Exception as e:
        log(f"Storyboard not saved ({e})")


SCENES = ("walk", "dig", "fly", "footprints", "home", "village", "window", "door", "transform", "chase", "hide", "mob",
          "stalk", "attack", "aftermath", "omen", "pov", "mpov", "curtain", "shoulder", "loom", "emerge", "swirl", "abyss", "dawn", "hosts")
SCENE_WORDS = (("dig", ("dig ", "digging", "dug ", "unearth", "shovel", "burying", "buried it", "claw at the earth", "clawing at the ground")),
               ("fly", ("flew", "flying", "fly ", "wings", "soared", "soaring", "took off", "takes off", "into the sky", "swooped")),
               ("footprints", ("footstep", "footprint", "steps behind", "tracks")),
               ("home", ("home", "house", "hut", "cottage", "doorstep")),
               ("village", ("village", "townsfolk", "town ")),
               ("window", ("window", "glass", "shutter")),
               ("door", ("door", "knock", "locked")),
               ("transform", ("turn into", "turns into", "transform", "shape", "becomes a", "changes into")),
               ("chase", ("run", "ran ", "flee", "chase", "race")),
               ("hide", ("hide", "hid ", "hiding", "silent", "holds his breath", "holds her breath")),
               ("mob", ("torch", "villagers", "hunt")),
               ("omen", ("moon", "eclipse", "comet", "lightning", "thunder", "meteor", "asteroid", "crash", "sky", "lights",
                         "virus", "sick", "plague", "buried", "tomb", "crows", "aurora")),
               ("curtain", ("curtain", "from inside", "peered out", "through the glass")),
               ("pov", ("looked back", "turned around", "turned to look", "glanced behind")))


SETTING_WORDS = (("forest", ("forest", "wood", "trees", "jungle")),
                 ("graveyard", ("grave", "cemetery", "tomb", "buried")),
                 ("river", ("river", "lake", "water", "ford", "stream", "mist")),
                 ("village", ("village", "home", "house", "hut", "street", "door", "window")),
                 ("field", ("field", "farm", "paddy", "crop", "meadow")),
                 ("hills", ("hill", "mountain", "storm", "ridge", "cliff", "thunder")))
SETTING_CYCLE = ("pathway", "forest", "village", "graveyard", "field", "river", "hills")


def assign_settings(sentences, beats):
    out = []
    for i, s in enumerate(sentences):
        st = s.get("setting")
        if st not in SETTING_CYCLE:
            st = None
            low = s["text"].lower()
            for name, words in SETTING_WORDS:
                if any(w in low for w in words):
                    st = name
                    break
            if st is None:
                st = out[-1] if out else "pathway"
        if beats[i] in ("home", "village", "mob"):
            st = "village"
        elif beats[i] == "dig":
            st = "graveyard" if any(w in s["text"].lower() for w in ("grave", "bur", "tomb", "coffin", "dead")) else "field"
        elif beats[i] == "fly":
            st = "field"
        out.append(st)
    out[0] = out[0] if out[0] != "village" else "pathway"
    # make sure the story actually travels: at least 3 places, and never more than 3 scenes in the same one
    run = 1
    for i in range(1, len(out)):
        run = run + 1 if out[i] == out[i - 1] else 1
        if run > 2 and beats[i] not in ("home", "village", "mob"):
            out[i] = next(c for c in SETTING_CYCLE if c != out[i - 1])
            run = 1
    if len(set(out)) < 3:
        for i in range(2, len(out) - 1, 2):
            if beats[i] not in ("home", "village", "mob"):
                out[i] = next(c for c in SETTING_CYCLE if c not in out)
                break
    return out


def assign_modern_beats(sentences, stype=None):
    n = len(sentences)
    beats = []
    for i, s in enumerate(sentences):
        b = s.get("beat")
        low = s["text"].lower()
        hits_ = sorted((min(low.find(w) for w in words if w in low), name) for name, words in MODERN_WORDS[:11] if any(w in low for w in words))
        hits_ = [(pos, nm) for pos, nm in hits_ if nm not in STORY_NATURE or stype in NATURE_TYPES]
        said = hits_[0][1] if hits_ else None
        SECOND_BEAT.pop(i, None)
        if len(hits_) >= 2 and hits_[1][1] != said and s.get("speaker", "narrator") == "narrator":
            SECOND_BEAT[i] = hits_[1][1]                # a sentence that says two things gets two shots, in the order it says them
        if said and (said not in STORY_NATURE or stype in NATURE_TYPES) and s.get("speaker", "narrator") == "narrator":
            b = said                                    # what the sentence SAYS decides the picture, not a guess
        if b not in MODERN_SCENES and s.get("speaker") != "host":
            b = next((name for name, words in MODERN_WORDS if any(w in low for w in words)), MODERN_FILL[i % len(MODERN_FILL)])
        if s.get("speaker") == "host" and 0 < i < n - 1:
            b = "hosts"                                 # the two hosts react to what was just narrated
        elif not hits_ and frame_line(s) and 0 < i < n - 1 and beats[-1:] != ["hosts"] and beats.count("hosts") < 4 and sentences[i + 1].get("speaker") != "host":
            b = "hosts"                                 # the storyteller recounting, only where the line names no scene of its own
        beats.append(b)
    place = MODERN_PLACE_SCENES.get(stype or "")
    fill = tuple(place) if place else MODERN_FILL
    beats[-1] = "jump"
    special = ("woods", "neighbour", "lamp", "road", "mirror", "dig", "fly", "ocean", "lake", "lava", "tornado", "tsunami", "giant")
    final = "giant" if stype in NATURE_TYPES else "jump"
    for i, b in enumerate(beats):  # the special places only appear in stories built around them
        if b in STORY_SCENES and b not in STORY_NATURE:
            continue                                   # radio / tower log / eyes through glass fit any story that says so
        if b in special and (not place or b not in place) or (b in STORY_NATURE and (not place or b not in place)):
            beats[i] = fill[i % len(fill)]
    if place and place[0] not in beats:
        beats[0] = place[0]
    for i in range(n - 1):  # the final face is only for the end
        if beats[i] in ("jump", final):
            beats[i] = fill[i % len(fill)] if place and fill[i % len(fill)] != final else ("hallway" if not place else fill[0])
    beats[-1] = final
    for _ in range(3):  # neighbours with the same scene are merged into one longer scene later, but never 3 in a row
        for i in range(2, n - 1):
            if beats[i] in STORY_SCENES:
                continue
            if (beats[i] == beats[i - 1] == beats[i - 2]) or beats.count(beats[i]) > 2:
                opts = [c for c in fill if c not in (beats[i - 1], beats[i + 1], final)] or [c for c in fill if c != final]
                if opts:
                    beats[i] = next((c for c in opts if beats.count(c) < 2), opts[0])
    if beats[-2] == final or beats[-1] != final:
        beats[-1] = final
    if n >= 6 and "cctv" in beats and beats.count("cctv") > 1:
        j = [k for k, b in enumerate(beats) if b == "cctv"][-1]
        beats[j] = "hallway" if beats[j - 1] != "hallway" else "door"
    return beats


def assign_pre_beats(n, stype):
    """Prehistoric stories follow a fixed order of scenes, spread evenly over however many sentences came back."""
    stages = PRE_TYPES[stype][1]
    beats = [stages[min(len(stages) - 1, int(i * len(stages) / n))] for i in range(n)]
    beats[-1] = "reign"
    return beats


LEGEND_SAID = (("emerge", r"(out of the (ground|earth)|from the (soil|grave)|clawed its way|dug itself out|burst from the ground)"),
               ("swirl", r"\b(foam\w*|whirlpool|swirl\w*|ripples?|vortex)\b"),
               ("abyss", r"\b(hurled|cast down|plunged|abyss|bottomless)\b|into the pit"),
               ("dawn", r"\b(sunrise|first light|daybreak)\b|morning came|\bat dawn\b|\bthe dawn\b"),
               ("dig", r"\b(dig|digging|dug|unearth\w*|shovel\w*)\b"),
               ("fly", r"\b(flew|flying|soared|soaring|swooped|took off|takes off)\b"),
               ("footprints", r"\b(footprints?|footsteps?|tracks)\b"),
               ("window", r"\b(window|shutters?)\b"),
               ("door", r"\b(door|knock\w*|latch)\b"),
               ("transform", r"\b(transform\w*|turns? into|turned into|changes? into|changed into)\b"),
               ("hide", r"\b(hid|hide|hiding|hidden)\b"),
               ("chase", r"\b(ran|run|running|fled|flee|fleeing|chased|chasing)\b"),
               ("village", r"\b(village|villagers|townsfolk)\b"))


def legend_said(low):
    """The scene a folklore sentence actually describes, if it names one of the drawable events (first one mentioned wins)."""
    best = None
    for name, rx in LEGEND_SAID:
        m = re.search(rx, low)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), name)
    return best[1] if best else None


FRAME_RE = re.compile(r"^(i|we|my|our|us|me)\b")


def frame_line(s):
    """A first-person line from the storyteller ("I was there that night.") frames the story as something that happened to the hosts."""
    return s.get("speaker", "narrator") == "narrator" and (bool(s.get("frame")) or bool(FRAME_RE.match(s["text"].strip().lower())))


def assign_beats(sentences, modern=False, stype=None):
    if stype in PRE_TYPES:
        return assign_pre_beats(len(sentences), stype)
    if modern:
        return assign_modern_beats(sentences, stype)
    n = len(sentences)
    beats, said_idx = [], set()
    for i, s in enumerate(sentences):
        b = s.get("beat")
        low = s["text"].lower()
        said = legend_said(low) if s.get("speaker", "narrator") == "narrator" and 0 < i < n - 1 else None
        if s.get("speaker") == "host" and 0 < i < n - 1:
            said = "hosts"
        elif said is None and frame_line(s) and 0 < i < n - 1 and beats[-1:] != ["hosts"] and beats.count("hosts") < 4 and sentences[i + 1].get("speaker") != "host":
            said = "hosts"                                  # the storyteller recounting: only where the line names no scene of its own
        if said:
            said_idx.add(i)
            b = said                                    # what the sentence SAYS decides the picture
        if b not in SCENES:
            b = None
            if any(w in low for w in ATTACK_WORDS) and i >= 2:
                b = "attack"
            else:
                for name, words in SCENE_WORDS:
                    if any(w in low for w in words):
                        b = name
                        break
            if b is None:
                b = "stalk" if i >= n - 4 else "walk"
        beats.append(b)
    beats[0] = "walk"
    if n >= 5 and "attack" not in beats[2:n - 1]:
        free = [j for j in range(n - 3, 1, -1) if j not in said_idx and any(w in sentences[j]["text"].lower() for w in ATTACK_WORDS)]
        if free:                                                         # only on a sentence that really is violent: never invent an attack
            beats[free[0]] = "attack"
    if beats[-1] in ("attack",) or beats[-1] not in ("stalk", "aftermath", "walk"):
        beats[-1] = "aftermath" if beats[-2] == "attack" else "stalk"
    for i in range(1, n):
        if beats[i] == "attack" and beats[i - 1] == "attack":
            beats[i] = "aftermath"  # one attack at a time
    for i in range(n - 1):
        if beats[i] == "attack" and beats[i + 1] != "aftermath" and (i + 1) not in said_idx:
            beats[i + 1] = "aftermath"
    om = [j for j, b in enumerate(beats) if b == "omen"]
    for j in om[1:]:  # only one omen
        beats[j] = "walk"
    if n >= 5 and (not om or om[0] > 3):  # the sign that brings the monster into the world, early on
        if om:
            beats[om[0]] = "walk"
        for j in (1, 2):
            if beats[j] in ("walk", "stalk", "footprints"):
                beats[j] = "omen"
                break
    if n >= 6 and sum(b in CAMERA_SCENES for b in beats) < 2:  # make sure the camera angles get used
        pool = [c for c in CAMERA_SCENES if c not in beats] or list(CAMERA_SCENES)
        cands = [j for j in range(3, n - 1) if beats[j] in ("walk", "stalk", "footprints", "hide", "home") and j not in said_idx]
        cands.sort(key=lambda j: (beats[j + 1] != "attack", -j))
        for j in cands:
            beats[j] = pool.pop(0) if pool else "pov"
            if sum(b in CAMERA_SCENES for b in beats) >= 2:
                break
    for i in range(1, n - 1):  # never the same scene twice in a row
        if beats[i] == beats[i - 1] and beats[i] not in ("attack",) and i not in said_idx:
            beats[i] = "stalk" if beats[i] != "stalk" else "walk"
    return beats


FOLK_STYLE = ("a warm, soft-spoken person telling a story out loud, naturally and conversationally, the way you would tell "
              "a close friend something that really happened to you and still gives you chills. Relaxed, believable, gentle "
              "tones with an uneven human rhythm, small pauses and quiet breaths, a little smile in the voice at the start that "
              "slowly turns quiet and serious when it gets frightening. Soft and close to the microphone, never robotic, never "
              "announcer-like, not reading from a page, no theatrical movie-trailer voice")
DRAMATIC_STYLE = ("a deep, calm, dramatic cinematic storyteller telling an intense story. Slow, deliberate pacing with real "
                  "pauses at full stops and at ellipses. Hushed and tense during the build-up, growing in weight and intensity "
                  "toward the frightening reveal, strongest on the key word, then a quiet, calm, final line. Low and controlled, "
                  "never shouting, never excited, not a nature documentary, no announcer sing-song, no smile in the voice")
NARRATOR_STYLE = DRAMATIC_STYLE if os.environ.get("VOICE_TONE", "dramatic") != "warm" else FOLK_STYLE
REVEAL_BEATS = ("hallway", "woods", "road", "jump", "birth", "loom", "curtain", "window", "giant", "tsunami", "ocean", "lake",
                "lava", "erupt", "clash", "mirror", "lamp", "transform", "attack")


def paced_text(sentences, idx, beats=None):
    """The narration with deliberate pauses: a held breath before the sentence that shows the monster."""
    out = []
    for x in idx:
        txt = sentences[x]["text"].strip()
        if beats and x > 0 and beats[x] in REVEAL_BEATS and (x == 0 or beats[x - 1] != beats[x]):
            txt = "... " + txt
            if out and not out[-1].endswith("..."):
                out[-1] = out[-1].rstrip(".!?") + "..."
        out.append(txt)
    return " ".join(out)


# a light touch only: a bit of warmth in the low-mids, a softer top end, gentle levelling. No pitch change, no echo.
SCARY_CHAIN = ("aresample=44100,highpass=f=60,equalizer=f=140:t=q:w=1:g=3.5,equalizer=f=190:t=q:w=1:g=1.5,equalizer=f=3800:t=q:w=1.2:g=-1.5,"
               "acompressor=threshold=-22dB:ratio=2:attack=15:release=250:makeup=1.5,loudnorm=I=-19:TP=-2:LRA=11,"
               "aformat=sample_fmts=s16:channel_layouts=mono")


# ---------------------------------------------------------------- an acted, emotional voice
# (style words for the voice, pitch change in semitones, tempo, loudness change in dB, tremble)
DELIVERY = {
    "calm": ("quiet, steady and natural, like a person remembering something", 0.0, 1.0, 0.0, 0),
    "hushed": ("hushed and close to the microphone, nervous", -0.5, 0.95, -1.5, 0),
    "whisper": ("a trembling whisper, afraid to be overheard", -1.0, 0.93, -2.5, 0),
    "urgent": ("fast, breathless and frightened, words tumbling out", 1.0, 1.10, 1.0, 0),
    "trembling": ("voice shaking with fear, catching on some words", 0.5, 0.97, 0.0, 1),
    "shout": ("a sudden terrified shout, high-pitched and cracking", 3.0, 1.04, 2.0, 0),
    "low": ("slow, deep and ominous, every word heavy", -3.0, 0.90, 0.5, 0),
    "sorrow": ("heavy with grief, the voice breaking softly", -1.0, 0.90, -1.0, 1),
    "awe": ("wide-eyed wonder tipping into dread", 1.0, 0.96, 0.0, 0),
    "dread": ("a cold, sinking dread, almost a gasp", -0.5, 0.94, -1.0, 0),
}
MALE_VOICES = ["Charon", "Orus", "Algenib", "Enceladus", "Fenrir"]
FEMALE_VOICES = ["Kore", "Gacrux", "Leda", "Aoede", "Sulafat"]
EDGE_MALE = ["en-GB-RyanNeural", "en-US-GuyNeural", "en-US-AndrewNeural"]
EDGE_FEMALE = ["en-US-AriaNeural", "en-US-JennyNeural", "en-GB-SoniaNeural"]
PERSONAS = ("an old villager telling it quietly, still afraid of it",
            "a frightened witness telling what they saw, years later",
            "a grandmother or grandfather passing down a warning",
            "a hushed storyteller by a fire at the dead of night")


def pick_delivery(i, n, text, beat):
    t = text.strip()
    if beat == "attack":
        return "shout"
    if beat == "aftermath":
        return "sorrow"
    if i == 0:
        return "hushed"
    if i == n - 1:
        return "low" if t.endswith("?") else "dread"
    if t.endswith("!"):
        return "urgent"
    if beat in ("chase", "hide"):
        return "urgent"
    if beat in ("stalk", "window", "door"):
        return "whisper"
    if beat in ("omen", "transform"):
        return "awe"
    return ("calm", "hushed", "dread", "trembling")[i % 4]


def fx_clip(src, dst, semis, tempo, db, tremble):
    """Bend a spoken sentence: higher or deeper, faster or slower, louder or softer, shaky."""
    r = 2 ** (semis / 12.0)
    tp = max(0.55, min(1.9, tempo / r))
    chain = [f"aresample=44100,asetrate={int(44100 * r)},aresample=44100,atempo={tp:.4f}"]
    if tremble:
        chain.append("vibrato=f=5.2:d=0.22")
    chain.append("loudnorm=I=-19:TP=-2:LRA=9")
    if abs(db) > 0.01:
        chain.append(f"volume={db}dB")
    chain.append("aformat=sample_fmts=s16:channel_layouts=mono")
    run(["ffmpeg", "-y", "-i", src, "-af", ",".join(chain), "-ar", "44100", "-ac", "1", dst], cwd=WORK)


async def edge_clip(text, mp3_path, voice, rate, pitch):
    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    with open(mp3_path, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])


# ---------------------------------------------------------------- the voice audition (nothing is rendered before the owner approves the voice)
def voice_approved():
    return bool(NARRATOR.get("approved_by_listening")) or os.environ.get("VOICE_APPROVED", "").strip().lower() in ("1", "yes", "true")


def voice_fallback_check():
    """The approved voice may not be swapped for a different one without the owner knowing."""
    if os.environ.get("ALLOW_VOICE_FALLBACK", "0") == "1":
        return
    sys.exit("STOPPED: the approved narrator voice (" + str(GEMINI_VOICE) + ") could not be produced right now, and the rules say a different "
             "voice must never be used silently. Run the workflow again in a few minutes. (To allow the basic free voice anyway, set the "
             "variable ALLOW_VOICE_FALLBACK to 1.)")


def audition_text(sentences, words=26):
    out, n = [], 0
    for s in sentences:
        if s.get("speaker", "narrator") not in ("narrator",):
            continue
        out.append(s["text"])
        n += len(s["text"].split())
        if n >= words:
            break
    return " ".join(out)


def voice_audition_gate(sentences):
    """Stage 2. Unless the voice is approved, make a 10-second sample of THIS script in the exact voice setup and stop.
    Approve: set narrator.json 'approved_by_listening' to true (or the variable VOICE_APPROVED to yes). Reject: run again with
    GEMINI_VOICE set to another voice (for example Orus, Algenib or Enceladus) to hear that one instead."""
    force = os.environ.get("VOICE_AUDITION", "0") == "1"
    if voice_approved() and not force:
        return
    OUT.mkdir(exist_ok=True)
    WORK.mkdir(exist_ok=True)
    text = audition_text(sentences)
    voice = os.environ.get("GEMINI_VOICE") or GEMINI_VOICE
    ok = GEMINI_API_KEY and gemini_tts(text, WORK / "audition_raw.wav", style=NARRATOR_STYLE, voice=voice)
    if not ok:
        sys.exit("STOPPED: the voice audition could not be made (Gemini voice not available right now). Nothing was rendered. Try again later.")
    run(["ffmpeg", "-y", "-i", "audition_raw.wav", "-af", SCARY_CHAIN, "-t", "10", "-ar", "44100", "-ac", "1",
         str((OUT / "voice_audition.mp3").resolve())], cwd=WORK)
    (OUT / "voice_audition.json").write_text(json.dumps({"voice": voice, "style": NARRATOR_STYLE, "text": text,
                                                         "approved": False, "how_to_approve": "narrator.json: approved_by_listening true, voice = this voice"}, indent=1))
    sys.exit(f"VOICE AUDITION READY (voice {voice}): listen to output/voice_audition.mp3. Nothing else was rendered. If it is good, set "
             "approved_by_listening to true in narrator.json (and put this voice name in 'voice'); if not, run again with GEMINI_VOICE "
             "set to another voice. The full video is only made after approval.")


# ---------------------------------------------------------------- the narrator stops for a loud sound
SOUND_PAUSE = {"attack": 2.2}        # silence after the narrator's sentence while the kill sound plays on its own (seconds)
JUMP_FRAC = {}                      # shot index -> where the jump-scare lands (a fraction of the shot)
SOUND_SYNC = []                      # what was done, for the report and the final check


def insert_sound_pauses(audio_name, words, durs, pauses, search=0.5):
    """Cuts the narration at the quietest moment next to the end of each given sentence and puts real silence there.
    pauses: {sentence index: seconds}. Returns (new wav name, words, durs) with every later time shifted by the silence."""
    import numpy as np
    run(["ffmpeg", "-y", "-i", audio_name, "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", "pz_in.wav"], cwd=WORK)
    with wave.open(str(WORK / "pz_in.wav")) as w_:
        sr = w_.getframerate()
        x = np.frombuffer(w_.readframes(w_.getnframes()), dtype=np.int16)
    ends = list(np.cumsum(durs))
    pieces, pos = [], 0
    new_durs, new_words = list(durs), [list(w) for w in words]
    last_cut = 0.0
    for i in sorted(pauses):
        gap = float(pauses[i])
        te = float(ends[i])
        lo, hi = max(last_cut + 0.4, te - search), min(len(x) / sr - 0.2, te + search)
        tc = te
        qdb = None
        if hi > lo:
            hop, win = int(sr * 0.01), int(sr * 0.03)
            best = None
            for s in range(int(lo * sr), int(hi * sr) - win, hop):
                r = float(np.sqrt(np.mean((x[s:s + win].astype(np.float32) / 32768.0) ** 2)) + 1e-9)
                if best is None or r < best[0]:
                    best = (r, s + win // 2)
            tc, qdb = best[1] / sr, 20 * math.log10(best[0])
        c = int(tc * sr)
        pieces += [x[pos:c], np.zeros(int(gap * sr), dtype=np.int16)]
        pos = c
        new_durs[i] += (tc - te) + gap
        if i + 1 < len(new_durs):
            new_durs[i + 1] -= (tc - te)
        for w in new_words:
            if w[0] >= tc:
                w[0] += gap
                w[1] += gap
        last_cut = tc
        SOUND_SYNC.append({"sentence": i, "final_at": round(tc, 2), "final_len": gap, "cut_at": round(tc, 2), "silence": gap, "cut_level_db": None if qdb is None else round(qdb, 1)})
        log(f"Narrator pauses {gap:.1f}s after sentence {i + 1} (cut at {tc:.2f}s, level {qdb if qdb is None else round(qdb)} dB) so the sound effect plays alone.")
    pieces.append(x[pos:])
    out = np.concatenate(pieces)
    with wave.open(str(WORK / "voice_gap.wav"), "wb") as o:
        o.setnchannels(1)
        o.setsampwidth(2)
        o.setframerate(sr)
        o.writeframes(out.tobytes())
    return "voice_gap.wav", [tuple(w) for w in new_words], new_durs


def make_story_voice(sentences, beats, narrator):
    """One acted clip per sentence (with its own mood, pitch and pace) joined with natural pauses.
    Returns (wav name, per-sentence starts, clip lengths, gaps, deliveries) or raises."""
    n = len(sentences)
    gender = narrator.get("gender") if narrator.get("gender") in ("male", "female") else random.choice(["male", "female"])
    persona = str(narrator.get("persona") or random.choice(PERSONAS))[:140]
    voice = os.environ.get("GEMINI_VOICE") or random.choice(MALE_VOICES if gender == "male" else FEMALE_VOICES)
    edge_voice_name = os.environ.get("TTS_VOICE") or random.choice(EDGE_MALE if gender == "male" else EDGE_FEMALE)
    delivs = []
    for i, sn in enumerate(sentences):
        d = sn.get("delivery")
        delivs.append(d if d in DELIVERY else pick_delivery(i, n, sn["text"], beats[i]))
    log(f"Narrator: {gender}, {persona}. Voice {voice} / {edge_voice_name}")
    log("Deliveries: " + ", ".join(delivs))
    engine = "gemini" if GEMINI_API_KEY else "edge"
    raw = []
    for i, sn in enumerate(sentences):
        dest = WORK / f"vs_{i}.wav"
        look, st_, tp_, db_, tr_ = DELIVERY[delivs[i]]
        style = (f"{persona}. Speaking in this way: {look}. Show real human emotion with natural breaths and small "
                 "hesitations. Sound like a person living through it, never like someone reading a page aloud.")
        ok = False
        if engine == "gemini":
            for attempt in range(2):
                if gemini_tts(sn["text"], dest, style=style, voice=voice):
                    ok = True
                    break
                time.sleep(4)
            if not ok:
                log("Gemini voice did not work for every sentence; switching the whole story to the backup voice.")
                engine = "edge"
        if engine == "edge":
            break
        raw.append((dest, 0.5))
        time.sleep(1.0)
    if engine == "edge":
        raw = []
        for i, sn in enumerate(sentences):
            _, st_, tp_, db_, tr_ = DELIVERY[delivs[i]]
            mp3 = WORK / f"vs_{i}.mp3"
            asyncio.run(edge_clip(sn["text"], str(mp3), edge_voice_name, f"{int(round((tp_ - 1) * 100)):+d}%", f"{int(round(st_ * 7)):+d}Hz"))
            raw.append((mp3, 0.0))  # the pitch and pace were already set in the voice itself
    clips, durs = [], []
    for i, (src, k) in enumerate(raw):
        _, st_, tp_, db_, tr_ = DELIVERY[delivs[i]]
        dst = f"vc_{i}.wav"
        fx_clip(str(src), dst, st_ * (k if k else 0.0), 1 + (tp_ - 1) * k, db_, tr_)
        clips.append(dst)
        durs.append(audio_seconds(WORK / dst))
    gaps = []
    for i in range(n):
        g = 0.26
        if i + 1 < n and beats[i + 1] == "attack":
            g = 0.55
        if beats[i] == "attack":
            g = 0.5
        if delivs[i] in ("whisper", "dread", "low"):
            g += 0.12
        gaps.append(g if i < n - 1 else 0.45)
    lines, starts, at = [], [], 0.0
    for i in range(n):
        starts.append(at)
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", f"{gaps[i]:.3f}", "-c:a", "pcm_s16le", f"gap_{i}.wav"], cwd=WORK)
        lines += [f"file '{clips[i]}'", f"file 'gap_{i}.wav'"]
        at += durs[i] + gaps[i]
    (WORK / "voice_list.txt").write_text("\n".join(lines) + "\n")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "voice_list.txt", "-c:a", "pcm_s16le", "voice.wav"], cwd=WORK)
    return "voice.wav", starts, durs, gaps, delivs


def make_mixed_voice(sentences, narrator, beats=None):
    """The narrator reads the story in one calm voice (as before). Spoken lines inside the story get their own voice:
    a different human voice for 'person', and a deep, slowed, echoing voice for the 'creature'.
    Returns (wav name, per-sentence starts, per-sentence lengths) or raises, so the caller can fall back."""
    n = len(sentences)
    runs, k = [], 0
    while k < n:  # consecutive narrator sentences are read in one go
        j = k
        if sentences[k].get("speaker", "narrator") == "narrator":
            while j + 1 < n and sentences[j + 1].get("speaker", "narrator") == "narrator":
                j += 1
        runs.append((k, j))
        k = j + 1
    if len(runs) > 9:
        raise RuntimeError("too many separate voice parts")
    narr_voice = os.environ.get("GEMINI_VOICE") or GEMINI_VOICE
    creature_voice = random.choice([v for v in ("Algenib", "Enceladus", "Fenrir") if v != narr_voice])
    # who can speak: (gemini voice, pitch factor). A child is the same kind of voice, only higher.
    host_voice = os.environ.get("HOST_VOICE") or ("Aoede" if narr_voice in MALE_VOICES else "Fenrir")
    creature_voice = creature_voice if creature_voice != host_voice else "Enceladus"
    pool_m = [v for v in MALE_VOICES if v not in (narr_voice, creature_voice, host_voice)] or ["Orus"]
    pool_f = [v for v in FEMALE_VOICES if v != host_voice] or FEMALE_VOICES
    vmap = {"man": (random.choice(pool_m), 1.0), "woman": (random.choice(pool_f), 1.0),
            "boy": (random.choice(pool_m), 1.22), "girl": (random.choice(pool_f), 1.12),
            "old_man": (random.choice(pool_m), 0.92), "old_woman": (random.choice(pool_f), 0.94)}
    vmap["host"] = (host_voice, 1.06)                      # the same bright voice every time: the second recurring character
    narr_gender = narrator.get("gender") if narrator.get("gender") in ("male", "female") else "male"
    default_person = "woman" if narr_gender == "male" else "man"
    log(f"Voices: narrator {narr_voice}, creature {creature_voice}, people {vmap} ({len(runs)} parts)")
    parts, starts, lens, at = [], [0.0] * n, [0.0] * n, 0.0
    for r, (a, b) in enumerate(runs):
        spk = sentences[a].get("speaker", "narrator")
        text = paced_text(sentences, range(a, b + 1), beats) if spk == "narrator" else " ".join(sentences[x]["text"] for x in range(a, b + 1))
        raw, dst = WORK / f"mv_raw{r}.wav", f"mv_{r}.wav"
        if spk == "narrator":
            ok = gemini_tts(text, raw, style=NARRATOR_STYLE, voice=narr_voice)
            chain = SCARY_CHAIN
        elif spk == "creature":
            ok = gemini_tts(text, raw, style="a low, inhuman, slow whisper that does not sound alive, cold and patient, "
                            "each word drawn out", voice=creature_voice)
            chain = ("aresample=44100,asplit=2[x][y];[x]asetrate=33000,aresample=44100[x1];[y]asetrate=24000,aresample=44100[y1];"
                     "[x1][y1]amix=inputs=2:weights='1 0.55':normalize=0,asoftclip=type=tanh,equalizer=f=250:t=q:w=1:g=3,"
                     "lowpass=f=4500,aecho=0.8:0.88:80|190:0.35|0.2,loudnorm=I=-17:TP=-2:LRA=8,"
                     "aformat=sample_fmts=s16:channel_layouts=mono")
        else:
            who = sentences[a].get("voice") or default_person
            gv, pf = vmap.get(who, vmap[default_person])
            if spk == "host":
                ok = gemini_tts(text, raw, style="a bright, quick, curious young voice, startled and a little thrilled, "
                                "as if reacting to something unbelievable", voice=gv)
                chain = (f"aresample=44100,asetrate={int(44100 * pf)},aresample=44100,loudnorm=I=-18:TP=-2:LRA=9,"
                         "aformat=sample_fmts=s16:channel_layouts=mono")
            elif spk == "mimic":  # the creature copying a loved one: right voice, wrong in a way you feel more than hear
                ok = gemini_tts(text, raw, style="a warm, loving, familiar voice calling out gently, but strangely flat and "
                                "slightly too calm, as if copied", voice=gv)
                chain = (f"aresample=44100,asetrate={int(44100 * pf)},aresample=44100,vibrato=f=6.5:d=0.06,"
                         "aecho=0.75:0.7:130|260|420:0.26|0.16|0.09,lowpass=f=7500,loudnorm=I=-19:TP=-2:LRA=9,"
                         "aformat=sample_fmts=s16:channel_layouts=mono")
            else:
                ok = gemini_tts(text, raw, style="a frightened, human, slightly breathless voice, real and unsteady", voice=gv)
                chain = (f"aresample=44100,asetrate={int(44100 * pf)},aresample=44100,loudnorm=I=-19:TP=-2:LRA=9,"
                         "aformat=sample_fmts=s16:channel_layouts=mono")
        if not ok:
            raise RuntimeError(f"voice part {r} failed")
        if "asplit" in chain:  # a layered chain needs a filter graph
            run(["ffmpeg", "-y", "-i", str(raw), "-filter_complex", "[0:a]" + chain + "[o]", "-map", "[o]", "-ar", "44100", "-ac", "1", dst], cwd=WORK)
        else:
            run(["ffmpeg", "-y", "-i", str(raw), "-af", chain, "-ar", "44100", "-ac", "1", dst], cwd=WORK)
        d = audio_seconds(WORK / dst)
        chars = [max(len(sentences[x]["text"]), 1) for x in range(a, b + 1)]
        pos = at
        for x, c in zip(range(a, b + 1), chars):
            starts[x], lens[x] = pos, d * c / sum(chars)
            pos += lens[x]
        gap = 0.3 if r < len(runs) - 1 else 0.0
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", f"{gap:.3f}", "-c:a", "pcm_s16le", f"mvgap_{r}.wav"], cwd=WORK)
        parts += [f"file '{dst}'", f"file 'mvgap_{r}.wav'"]
        at += d + gap
        time.sleep(1.0)
    (WORK / "mv_list.txt").write_text("\n".join(parts) + "\n")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "mv_list.txt", "-c:a", "pcm_s16le", "voice.wav"], cwd=WORK)
    return "voice.wav", starts, lens


def humanize_voice(name):
    """Make a synthetic voice sound a little more like a real person speaking in a real room."""
    out = "voice_fx.wav"
    fc = ("highpass=f=75,equalizer=f=170:t=q:w=1:g=2.5,equalizer=f=3800:t=q:w=1.2:g=-2.5,lowpass=f=11000,"
          "acompressor=threshold=-21dB:ratio=3:attack=12:release=180:makeup=2,"
          "aecho=0.88:0.9:38|95:0.16|0.08,alimiter=limit=0.95:level=0")
    try:
        run(["ffmpeg", "-y", "-i", name, "-af", fc, "-ar", "44100", out], cwd=WORK)
        return out
    except Exception as e:
        log(f"Voice polish skipped: {e}")
        return name



MAULS = {}      # scene index -> (seconds into the scene the close-up starts, how long it lasts), filled when the scene is made


def maul_sound_events(sounds, t0, dur):
    """Sound for the close-up kill, timed to what is on screen: claws touch (0.30), the rake (0.31-0.62), the jaws close
    (0.60-0.80) and the fangs sink in (0.80). t0 is the absolute time the close-up starts."""
    claw = t0 + 0.30 * dur
    ev = [{"file": sounds["stab"], "at": claw + k * 0.035, "vol": 0.9 - 0.08 * k} for k in range(4)]
    ev.append({"file": sounds["rip"], "at": claw + 0.06, "vol": 0.8, "max": max(0.5, dur * 0.36)})
    ev.append({"file": sounds["crunch"], "at": t0 + 0.81 * dur, "vol": 1.0})
    ev.append({"file": sounds["stab"], "at": t0 + 0.81 * dur + 0.02, "vol": 0.8})
    ev.append({"file": sounds["growl"], "at": t0 + 0.62 * dur, "vol": 0.5})
    return ev, (claw - 0.38, claw - 0.03)


SYNC_REPORT = {}


def sound_sync_check(events, words, loud=0.6):
    """Stage 4: no prominent sound effect may play over a spoken word. Overlapping ones are turned down (the narrator stays clear)
    and counted; the report says how many overlaps there were and how many were fixed."""
    spans = []
    for a, b, _w in sorted(words):
        if spans and a - spans[-1][1] < 0.25:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    found = fixed = 0
    for ev in events:
        stem = pathlib.Path(str(ev["file"])).stem.split("_")[0]
        if stem not in MAJOR_SOUNDS or ev.get("vol", 1.0) < loud:
            continue
        fp = pathlib.Path(str(ev["file"]))
        if not fp.is_absolute():
            fp = WORK / fp                      # sound names are relative to the work folder (measuring them from anywhere else failed silently)
        try:
            ln = min(audio_seconds(fp), ev.get("max", 99.0))
        except Exception as e:
            ln = 1.0
            log(f"Could not measure {fp.name} ({e}); assuming 1 second.")
        s, e = ev["at"], ev["at"] + ln
        ov = sum(max(0.0, min(e, b) - max(s, a)) for a, b in spans)
        if ov > 0.15:
            found += 1
            ev["vol"] = min(ev.get("vol", 1.0), 0.4)
            fixed += 1
    SYNC_REPORT.update({"overlaps": found, "turned_down": fixed, "pauses": list(SOUND_SYNC)})
    log(f"Sound sync check: {len(SOUND_SYNC)} narrator pause(s), {found} loud sound(s) overlapped speech, {fixed} turned down.")
    return events


def folk_sound_events(durs, beats, impacts, sounds, hits=None, quiet=None, origin=None):
    hits = hits or {}
    quiet = quiet if quiet is not None else []
    ev = [{"file": sounds["eerie"], "at": 0.05, "vol": 0.9},
          {"file": sounds["scream"], "at": 0.1, "vol": 0.6, "max": 1.2}]
    at = 0.0
    for i, d in enumerate(durs):
        if i:
            ev.append({"file": sounds["whoosh"], "at": at - 0.1, "vol": 0.3})
        if i in SMASH_AT:                           # a vehicle is wrecked: the crunch lands exactly on the contact frame
            sh_ = at + SMASH_AT[i]
            ev += [{"file": sounds["crash"], "at": sh_, "vol": 0.9}, {"file": sounds["boom"], "at": sh_, "vol": 0.7},
                   {"file": sounds["crunch"], "at": sh_ + 0.04, "vol": 0.7}, {"file": sounds["tap"], "at": sh_ + 0.14, "vol": 0.6},
                   {"file": sounds["scrape"], "at": sh_ + 0.3, "vol": 0.45}]
            quiet.append((sh_ - 0.45, sh_ - 0.03))
        if beats[i] == "attack":
            hit = at + impacts.get(i, d * 0.6)
            # a heartbeat that speeds up as the creature closes in, then total silence just before the strike
            tt, gapb = at + d * 0.25, 0.85
            while tt < hit - 0.9:
                ev.append({"file": sounds["heart"], "at": tt, "vol": 0.9})
                tt += gapb
                gapb = max(0.42, gapb * 0.88)
            quiet.append((hit - 0.8, hit - 0.05))
            ev.append({"file": sounds["eerie"], "at": at + d * 0.3, "vol": 0.7})
            ev.append({"file": sounds["boom"], "at": hit, "vol": 0.9})
            ev.append({"file": sounds["victim"], "at": max(hit - 0.45, at), "vol": 0.95, "max": 0.6})  # the victim screams, cut off at impact
            ev.append({"file": sounds["scream"], "at": hit, "vol": 0.7, "max": 1.4})
            ev.append({"file": sounds["growl"], "at": hit + 0.4, "vol": 0.6})
            if i in MAULS:                      # the close-up: claws through skin, the rake, the bite
                fe, qw = maul_sound_events(sounds, at + MAULS[i][0], MAULS[i][1])
                ev += fe
                quiet.append(qw)
        elif beats[i] == "bedroom":
            for h in hits.get(i, [])[:10]:
                ev.append({"file": sounds["tap"], "at": at + h, "vol": 0.85})
            ev.append({"file": sounds["riser"], "at": at + d * 0.55, "vol": 0.45, "max": d * 0.45})
        elif beats[i] == "stairs":
            for h in hits.get(i, [])[:10]:
                ev.append({"file": sounds["creak"], "at": at + h, "vol": 0.8})
            ev.append({"file": sounds["heart"], "at": at + d * 0.7, "vol": 0.9})
            ev.append({"file": sounds["heart"], "at": at + d * 0.9, "vol": 1.0})
        elif beats[i] == "basement":
            for h in hits.get(i, [])[:6]:
                ev.append({"file": sounds["tap"], "at": at + h, "vol": 1.0})
            tt = at + d * 0.55
            while tt < at + d - 0.3:
                ev.append({"file": sounds["heart"], "at": tt, "vol": 0.9})
                tt += 0.6
            ev.append({"file": sounds["growl"], "at": at + d * 0.8, "vol": 0.55})
        elif beats[i] == "hallway":
            for h in hits.get(i, [])[:5]:
                ev.append({"file": sounds["boom"], "at": at + h, "vol": 0.4})
            ev.append({"file": sounds["riser"], "at": at + 0.2, "vol": 0.6})
            ev.append({"file": sounds["heart"], "at": at + d * 0.8, "vol": 0.9})
        elif beats[i] == "cctv":
            ev.append({"file": sounds["eerie"], "at": at + 0.1, "vol": 0.6})
            ev.append({"file": sounds["whoosh"], "at": at + d * 0.3, "vol": 0.35})
            ev.append({"file": sounds["boom"], "at": at + d * 0.9, "vol": 0.7})
        elif beats[i] == "jump":
            cut = at + d * JUMP_FRAC.get(i, 0.72)
            quiet.append((cut - 1.0, cut - 0.05))
            ev.append({"file": sounds["riser"], "at": max(at, cut - 1.3), "vol": 0.5, "max": 1.2})
            ev.append({"file": sounds["boom"], "at": cut, "vol": 1.0})
            ev.append({"file": sounds["scream"], "at": cut, "vol": 0.85, "max": 1.4})
            ev.append({"file": sounds["growl"], "at": cut + 0.1, "vol": 0.7})
        elif beats[i] == "woods":
            for h in hits.get(i, [])[:5]:
                ev.append({"file": sounds["tap"], "at": at + h, "vol": 0.7})  # twigs snapping in the dark
            ev.append({"file": sounds["whoosh"], "at": at + 0.2, "vol": 0.4})
            ev.append({"file": sounds["distant"], "at": at + d * 0.45, "vol": 0.6})
            ev.append({"file": sounds["heart"], "at": at + d * 0.8, "vol": 0.9})
        elif beats[i] == "neighbour":
            ev.append({"file": sounds["eerie"], "at": at + d * 0.2, "vol": 0.6})
            for h in hits.get(i, [])[:3]:
                ev.append({"file": sounds["boom"], "at": at + h, "vol": 0.45})
            ev.append({"file": sounds["growl"], "at": at + d * 0.75, "vol": 0.45})
        elif beats[i] == "lamp":
            ev.append({"file": sounds["riser"], "at": at + 0.1, "vol": 0.55})
            ev.append({"file": sounds["eerie"], "at": at + d * 0.35, "vol": 0.8})
            ev.append({"file": sounds["growl"], "at": at + d * 0.7, "vol": 0.75})
        elif beats[i] == "road":
            tt = at + 0.2
            while tt < at + d - 0.3:
                ev.append({"file": sounds["heart"], "at": tt, "vol": 0.8})
                tt += 0.7
            for h in hits.get(i, [])[:4]:
                ev.append({"file": sounds["boom"], "at": at + h, "vol": 0.55})
        elif beats[i] == "mirror":
            ev.append({"file": sounds["eerie"], "at": at + 0.2, "vol": 0.6})
            for h in hits.get(i, [])[:2]:
                ev.append({"file": sounds["boom"], "at": at + h, "vol": 0.9})
                ev.append({"file": sounds["victim"], "at": at + h, "vol": 0.7, "max": 0.5})
        elif beats[i] == "dig":
            for h in hits.get(i, [])[:8]:
                ev.append({"file": sounds["scrape"], "at": at + h - 0.15, "vol": 0.9})
            ev.append({"file": sounds["growl"], "at": at + d * 0.5, "vol": 0.4})
        elif beats[i] == "fly":
            for h in hits.get(i, [])[:6]:
                ev.append({"file": sounds["flap"], "at": at + h, "vol": 0.9})
            ev.append({"file": sounds["whoosh"], "at": at + d * 0.35, "vol": 0.6})
            ev.append({"file": sounds["growl"], "at": at + d * 0.3, "vol": 0.6})
        elif beats[i] == "eyes":
            ev.append({"file": sounds["eerie"], "at": at + 0.1, "vol": 0.55})
            ev.append({"file": sounds["heart"], "at": at + d * 0.55, "vol": 0.8})
        elif beats[i] == "lantern":
            for h in hits.get(i, [])[:2]:
                ev.append({"file": sounds["creak"], "at": at + h, "vol": 0.6})
            if len(hits.get(i, [])) == 1:  # the drop: a thud and the glass
                ev.append({"file": sounds["boom"], "at": at + hits[i][0], "vol": 0.5})
                ev.append({"file": sounds["tap"], "at": at + hits[i][0] + 0.05, "vol": 0.9})
                quiet.append((at + hits[i][0] + 0.2, at + hits[i][0] + 0.9))  # then a held silence
        elif beats[i] == "notice":
            ev.append({"file": sounds["tap"], "at": at + d * 0.2, "vol": 0.6})
            ev.append({"file": sounds["heart"], "at": at + d * 0.62, "vol": 0.85})
        elif beats[i] == "establish":
            ev.append({"file": sounds["gust"], "at": at + 0.1, "vol": 0.55})
        elif beats[i] in ("ocean", "lake"):
            h = hits.get(i, [d * 0.28, d * 0.56, d * 0.82])
            ev.append({"file": sounds["rumble"], "at": at + h[0] - 0.3, "vol": 0.8})
            ev.append({"file": sounds["splash"], "at": at + h[1], "vol": 0.7})
            ev.append({"file": sounds["roar"], "at": at + h[1] + 0.15, "vol": 0.8})
            ev.append({"file": sounds["growl"], "at": at + h[2], "vol": 0.55})
        elif beats[i] == "lava":
            h = hits.get(i, [d * 0.25, d * 0.55, d * 0.8])
            ev.append({"file": sounds["rumble"], "at": at + h[0] - 0.3, "vol": 0.9})
            ev.append({"file": sounds["roar"], "at": at + h[1], "vol": 0.9})
            ev.append({"file": sounds["boom"], "at": at + h[2], "vol": 0.6})
            ev.append({"file": sounds["growl"], "at": at + h[2] + 0.2, "vol": 0.5})
        elif beats[i] == "tornado":
            ev.append({"file": sounds["gust"], "at": at + 0.05, "vol": 0.9})
            ev.append({"file": sounds["gust"], "at": at + d * 0.5, "vol": 0.8})
            for h in hits.get(i, [])[:4]:
                ev.append({"file": sounds["boom"], "at": at + h, "vol": 0.7})
            ev.append({"file": sounds["growl"], "at": at + d * 0.62, "vol": 0.6})
        elif beats[i] == "tsunami":
            h = hits.get(i, [d * 0.2, d * 0.45, d * 0.88])
            ev.append({"file": sounds["rumble"], "at": at + h[0] - 0.3, "vol": 0.9})
            ev.append({"file": sounds["roar"], "at": at + h[1], "vol": 0.9})
            quiet.append((at + h[2] - 0.9, at + h[2] - 0.05))
            ev.append({"file": sounds["crash"], "at": at + h[2], "vol": 1.0})
            ev.append({"file": sounds["boom"], "at": at + h[2], "vol": 0.8})
        elif beats[i] == "retreat":
            ev.append({"file": sounds["rumble"], "at": at + 0.1, "vol": 0.5})
            ev.append({"file": sounds["gust"], "at": at + d * 0.3, "vol": 0.4})
            ev.append({"file": sounds["distant"], "at": at + d * 0.6, "vol": 0.35})
        elif beats[i] == "tidelog":
            ev.append({"file": sounds["scrape"], "at": at + 0.3, "vol": 0.3})
            ev.append({"file": sounds["creak"], "at": at + d * 0.45, "vol": 0.4})
            ev.append({"file": sounds["rumble"], "at": at + d * 0.6, "vol": 0.4})
        elif beats[i] == "radio":
            ev.append({"file": sounds["static"], "at": at + 0.2, "vol": 0.55})
            ev.append({"file": sounds["static"], "at": at + d * 0.5, "vol": 0.8})
            quiet.append((at + d - 0.8, at + d - 0.05))        # the signal dies: a held silence
        elif beats[i] == "harbor":
            ev.append({"file": sounds["rumble"], "at": at + 0.05, "vol": 0.8})
            ev.append({"file": sounds["crash"], "at": at + d * 0.55, "vol": 0.95})
            ev.append({"file": sounds["boom"], "at": at + d * 0.62, "vol": 0.7})
            ev.append({"file": sounds["splash"], "at": at + d * 0.72, "vol": 0.6})
        elif beats[i] == "rise":
            ev.append({"file": sounds["rumble"], "at": at + 0.05, "vol": 0.9})
            ev.append({"file": sounds["splash"], "at": at + d * 0.35, "vol": 0.75})
            ev.append({"file": sounds["growl"], "at": at + d * 0.75, "vol": 0.55})
        elif beats[i] == "glass":
            tt = at + 0.2
            while tt < at + d * 0.7:
                ev.append({"file": sounds["heart"], "at": tt, "vol": 0.7})
                tt += 0.8
            ev.append({"file": sounds["creak"], "at": at + d * 0.62, "vol": 0.8})
            ev.append({"file": sounds["growl"], "at": at + d * 0.85, "vol": 0.5})
        elif beats[i] in ("emerge", "abyss"):
            h = hits.get(i, [d * 0.25, d * 0.6])
            ev.append({"file": sounds["rumble"], "at": at + 0.05, "vol": 0.9})
            ev.append({"file": sounds["boom"], "at": at + h[0], "vol": 0.5})
        elif beats[i] == "swirl":
            ev.append({"file": sounds["rumble"], "at": at + d * 0.3, "vol": 0.5})
        elif beats[i] == "ridge":
            h = hits.get(i, [d * 0.3, d * 0.65])
            ev.append({"file": sounds["rumble"], "at": at + 0.05, "vol": 0.9})
            ev.append({"file": sounds["boom"], "at": at + h[0], "vol": 0.6})
            ev.append({"file": sounds["roar"], "at": at + h[1], "vol": 0.9})
        elif beats[i] == "giant":
            h = hits.get(i, [d * 0.25, d * 0.6])
            ev.append({"file": sounds["rumble"], "at": at + max(0, h[0] - 0.3), "vol": 0.9})
            ev.append({"file": sounds["roar"], "at": at + h[1], "vol": 1.0})
            ev.append({"file": sounds["boom"], "at": at + h[1], "vol": 0.7})
        elif beats[i] == "valley":
            h = hits.get(i, [d * 0.5])
            ev.append({"file": sounds["distant"], "at": at + 0.3, "vol": 0.5})
            ev.append({"file": sounds["rumble"], "at": at + h[0] - 0.2, "vol": 0.45})
            ev.append({"file": sounds["roar"], "at": at + h[0], "vol": 0.75})
        elif beats[i] == "erupt":
            h = hits.get(i, [d * 0.22, d * 0.5, d * 0.78])
            ev.append({"file": sounds["rumble"], "at": at + 0.05, "vol": 1.0})
            for hh in h[:3]:
                ev.append({"file": sounds["boom"], "at": at + hh, "vol": 0.85})
            ev.append({"file": sounds["gust"], "at": at + d * 0.35, "vol": 0.6})
            ev.append({"file": sounds["roar"], "at": at + h[1] + 0.2, "vol": 0.7})
        elif beats[i] == "birth":
            h = hits.get(i, [d * 0.3, d * 0.58, d * 0.82])
            ev.append({"file": sounds["rumble"], "at": at + max(0.0, h[0] - 0.4), "vol": 0.9})
            ev.append({"file": sounds["growl"], "at": at + h[1], "vol": 0.8})
            ev.append({"file": sounds["roar"], "at": at + h[2], "vol": 1.0})
            ev.append({"file": sounds["boom"], "at": at + h[2], "vol": 0.6})
        elif beats[i] == "clash":
            hit = at + d * 0.54
            ev.append({"file": sounds["roar"], "at": at + d * 0.1, "vol": 0.7})
            ev.append({"file": sounds["growl"], "at": at + d * 0.26, "vol": 0.9})
            quiet.append((hit - 0.7, hit - 0.04))
            ev.append({"file": sounds["boom"], "at": hit, "vol": 1.0})
            ev.append({"file": sounds["scream"], "at": hit, "vol": 0.85, "max": 1.4})
            ev.append({"file": sounds["crash"], "at": hit + 0.1, "vol": 0.6})
            ev.append({"file": sounds["roar"], "at": hit + 0.5, "vol": 0.95})
        elif beats[i] == "skies":
            for hh in (0.12, 0.3):
                ev.append({"file": sounds["scream"], "at": at + d * hh, "vol": 0.45, "max": 0.9})
            ev.append({"file": sounds["rumble"], "at": at + d * 0.35, "vol": 0.7})
            for q in (0.45, 0.58, 0.7):
                ev.append({"file": sounds["flap"], "at": at + d * q, "vol": 0.9})
            ev.append({"file": sounds["roar"], "at": at + d * 0.55, "vol": 0.9})
        elif beats[i] == "reign":
            h = hits.get(i, [d * 0.3, d * 0.62])
            ev.append({"file": sounds["rumble"], "at": at + 0.1, "vol": 0.8})
            ev.append({"file": sounds["roar"], "at": at + h[0], "vol": 1.0})
            ev.append({"file": sounds["boom"], "at": at + h[0], "vol": 0.7})
            ev.append({"file": sounds["roar"], "at": at + h[1], "vol": 1.0})
        elif beats[i] == "hook":
            ev.append({"file": sounds["tap"], "at": at + 0.9, "vol": 0.8})
            ev.append({"file": sounds["tap"], "at": at + 1.5, "vol": 0.8})
        elif beats[i] == "stalk":
            ev.append({"file": sounds["growl"], "at": at + 0.3, "vol": 0.4})
        elif beats[i] == "aftermath":
            ev.append({"file": sounds["distant"], "at": at + 0.8, "vol": 0.55})
        elif beats[i] in ("footprints", "door", "village", "mob", "window", "hide"):
            for h in hits.get(i, [])[:6]:
                ev.append({"file": sounds["boom"], "at": at + h, "vol": 0.45})
            if beats[i] in ("window", "door"):
                ev.append({"file": sounds["growl"], "at": at + d * 0.3, "vol": 0.45})
                ev.append({"file": sounds["victim"], "at": at + d * 0.85, "vol": 0.7, "max": 0.7})
            if beats[i] == "village":
                ev.append({"file": sounds["distant"], "at": at + d * 0.78, "vol": 0.7})
        elif beats[i] == "omen":
            ev.append({"file": sounds["riser"], "at": at + 0.1, "vol": 0.7})
            for h in hits.get(i, [])[:4]:
                ev.append({"file": sounds["boom"], "at": at + h, "vol": 1.0 if origin in ("asteroid", "thunder", "tomb") else 0.6})
            if origin in ("fullmoon", "bluemoon", "shadow"):
                ev.append({"file": sounds["growl"], "at": at + d * 0.7, "vol": 0.55})
            if origin in ("aliens", "comet", "aurora", "eclipse"):
                ev.append({"file": sounds["eerie"], "at": at + d * 0.2, "vol": 0.8})
            if origin == "asteroid":
                ev.append({"file": sounds["whoosh"], "at": at + d * 0.25, "vol": 0.8, "max": 1.6})
            if origin == "virus":
                ev.append({"file": sounds["distant"], "at": at + d * 0.5, "vol": 0.5})
        elif beats[i] == "pov":
            ev.append({"file": sounds["heart"], "at": at + 0.2, "vol": 1.0})
            ev.append({"file": sounds["heart"], "at": at + 0.9, "vol": 1.0})
            ev.append({"file": sounds["growl"], "at": at + d * 0.5, "vol": 0.7})
            ev.append({"file": sounds["riser"], "at": at + d * 0.4, "vol": 0.5, "max": d * 0.6})
        elif beats[i] == "mpov":
            tt = at + 0.1
            while tt < at + d - 0.3:
                ev.append({"file": sounds["heart"], "at": tt, "vol": 1.0})
                tt += 0.55
            ev.append({"file": sounds["growl"], "at": at + 0.3, "vol": 0.8})
        elif beats[i] == "curtain":
            ev.append({"file": sounds["growl"], "at": at + d * 0.45, "vol": 0.55})
            ev.append({"file": sounds["victim"], "at": at + d * 0.3, "vol": 0.85, "max": 0.9})
            ev.append({"file": sounds["whoosh"], "at": at + d * 0.5, "vol": 0.4})
        elif beats[i] == "shoulder":
            ev.append({"file": sounds["heart"], "at": at + 0.3, "vol": 0.9})
            ev.append({"file": sounds["heart"], "at": at + 1.0, "vol": 0.9})
            ev.append({"file": sounds["growl"], "at": at + d * 0.6, "vol": 0.5})
        elif beats[i] == "loom":
            ev.append({"file": sounds["boom"], "at": at + d * 0.35, "vol": 0.9})
            ev.append({"file": sounds["growl"], "at": at + d * 0.3, "vol": 0.9})
            ev.append({"file": sounds["scream"], "at": at + d * 0.35, "vol": 0.6, "max": 1.2})
        elif beats[i] == "transform":
            ev.append({"file": sounds["growl"], "at": at + d * 0.45, "vol": 0.8})
            ev.append({"file": sounds["scream"], "at": at + d * 0.5, "vol": 0.6, "max": 1.4})
            ev.append({"file": sounds["victim"], "at": at + d * 0.3, "vol": 0.8, "max": 0.9})
        elif beats[i] == "chase":
            ev.append({"file": sounds["growl"], "at": at + 0.2, "vol": 0.5})
            ev.append({"file": sounds["whoosh"], "at": at + d * 0.5, "vol": 0.4})
            ev.append({"file": sounds["victim"], "at": at + 0.15, "vol": 0.85, "max": 0.9})
            ev.append({"file": sounds["victim"], "at": at + d * 0.55, "vol": 0.85, "max": 0.7})
        at += d
    ev.append({"file": sounds["eerie"], "at": max(at - 1.6, 0), "vol": 0.8})
    ev.append({"file": sounds["scream"], "at": max(at - 1.5, 0), "vol": 0.8, "max": 1.6})
    return ev


INSERT_POOL = {"legend": ["establish", "tower", "notice", "lantern", "eyes"], "modern": ["eyes", "tower", "lantern"],
               "prehistoric": ["tower", "eyes"], "nature": ["tower", "eyes"]}
ESTABLISH_VARIANTS = ["pan", "push", "pull", "storm", "low"]
MIN_SHOT = 4.4  # a scene shorter than this is not split


ABILITY_LOG, WALK_LOG, ANATOMY_LOG = [], [], []
ANGLE_CYCLE = ("wide", "low", "drift", "close", "high", "dutch")
CREATURE_BEATS = ("stalk", "walk", "chase", "omen", "village", "footprints", "aftermath")


def shot_angle(j, beat, sh, modern, n, i):
    """A different camera angle for neighbouring shots, so one location never looks like one static view.
    Only shots where a creature (or the open scene) is the subject move; rooms, inserts, host scenes and the kill keep their own framing."""
    if modern and not (sh.get("insert") or beat in ("attack", "hosts", "frame") or i == 0 or i == n - 1):
        return ("dutch", "drift", "high")[j % 3]        # rooms and streets: framing that never depends on the creature being in view
    if sh.get("insert") or modern or beat in ("attack", "hosts", "frame", "emerge", "swirl", "abyss", "dawn") or i == 0 or i == n - 1:
        return "wide"
    a = ANGLE_CYCLE[j % len(ANGLE_CYCLE)]
    if a in ("low", "close") and beat not in CREATURE_BEATS:
        a = "drift"
    return a


def plan_shots(beats, durs, fmt, stype, rnd=None):
    """Turns each scene into two shots: a short cutaway first (wide establishing shot, a person noticing something, a lantern
    close-up, eyes in the dark), then the main scene. The first and last scenes stay whole. No cutaway type is used twice in
    a row, and the establishing shots rotate through different camera moves."""
    rnd = rnd or random.Random(len(beats) * 31 + int(sum(durs)))
    kind = "nature" if stype in NATURE_TYPES else {"modern": "modern", "prehistoric": "prehistoric"}.get(fmt, "legend")
    pool = INSERT_POOL[kind]
    shots, last_ins, var_i, prev_ins = [], None, rnd.randrange(len(ESTABLISH_VARIANTS)), False
    used_ins = {}
    n = len(beats)
    for i, (b, d) in enumerate(zip(beats, durs)):
        ins = None
        gap_ok = not (len(pool) < 2 and prev_ins)  # a one-type pool needs a plain scene between cutaways
        if b in STORY_SCENES:                           # the shot that shows this very sentence: no random cutaway in front of it
            sec = SECOND_BEAT.get(i)
            if sec and d >= 3.4:
                d1 = d * 0.45
                shots.append({"i": i, "beat": b, "dur": d1, "insert": False, "variant": None})
                shots.append({"i": i, "beat": sec, "dur": d - d1, "insert": False, "variant": None})
            else:
                shots.append({"i": i, "beat": b, "dur": d, "insert": False, "variant": None})
            prev_ins = False
            continue
        if 0 < i < n - 1 and d >= MIN_SHOT and b not in ("attack", "aftermath") and gap_ok:
            opts = [p for p in pool if p != last_ins] or pool
            if i + 1 < n and beats[i + 1] == "attack":
                opts = [p for p in opts if p != "notice"] or opts
            least = min(used_ins.get(p, 0) for p in opts)          # use every cutaway once before any is used again
            opts = [p for p in opts if used_ins.get(p, 0) == least]
            ins = rnd.choice(opts)
            used_ins[ins] = used_ins.get(ins, 0) + 1
        if ins:
            var = None
            if ins == "establish":
                var = ESTABLISH_VARIANTS[var_i % len(ESTABLISH_VARIANTS)]
                var_i += 1
            elif ins == "lantern":
                var = "drop" if rnd.random() < 0.4 else "swing"
            elif ins == "valley":
                var = "pull" if rnd.random() < 0.5 else "push"
            di = min(2.5, max(1.6, d * 0.34))
            shots.append({"i": i, "beat": ins, "dur": di, "insert": True, "variant": var})
            shots.append({"i": i, "beat": b, "dur": d - di, "insert": False, "variant": None})
            last_ins = ins
            prev_ins = True
        else:
            prev_ins = False
            shots.append({"i": i, "beat": b, "dur": d, "insert": False, "variant": None})
    prev_main = None
    for sh in shots:  # the same scene twice in a row gets a different camera the second time
        if not sh["insert"]:
            sh["repeat"] = sh["beat"] == prev_main
            prev_main = sh["beat"]
    return shots


CAP_FILLER = {"the", "a", "an", "of", "to", "in", "on", "at", "across", "from", "with", "by", "for", "and", "but", "or", "as", "that", "when",
              "where", "which", "his", "her", "their", "its", "my", "your", "our", "into", "onto", "near", "over", "under", "through", "than",
              "then", "so", "if", "while", "after", "before", "until", "toward", "towards", "behind", "beyond", "around", "like", "about", "out"}


def caption_groups(words):
    """Caption phrases of 2 to 5 words that end at natural breaks: never on a filler word ("across the"), never a stranded word,
    and a sentence (a host's reaction, say) always starts its own caption."""
    END = (".", ",", "!", "?", ";", ":", "\u2014", "\u2026")
    clean = lambda w: w[2].rstrip("\"'\u201d)")
    sent_end = [clean(w).endswith((".", "!", "?", "\u2026")) for w in words]
    n = len(words)
    to_end = [0] * n                       # words still to come in this sentence
    for k in range(n - 1, -1, -1):
        to_end[k] = 0 if sent_end[k] or k == n - 1 else to_end[k + 1] + 1
    groups, cur = [], []
    for k, w in enumerate(words):
        cur.append(w)
        low = clean(w).lower().strip(".,!?;:\u2014\u2026")
        if k == n - 1 or sent_end[k]:
            groups.append(cur)
            cur = []
            continue
        want = len(cur) >= 4 or (clean(w).endswith(END) and len(cur) >= 3)
        if want and (low in CAP_FILLER or to_end[k] == 1) and len(cur) < 6:
            continue                       # extend so the phrase finishes properly
        if want:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    # a one-word leftover joins the group before it (same sentence only)
    merged = []
    for g in groups:
        if merged and len(g) == 1 and not clean(merged[-1][-1]).endswith((".", "!", "?", "\u2026")):
            merged[-1].extend(g)
        else:
            merged.append(g)
    return merged


def build_folk_main(plan):
    import toon
    story = plan["story"]
    sentences = story["sentences"]
    animal = story["animal"]
    region = story["region"]
    episode = plan.get("episode", 1)
    narration = " ".join(s["text"] for s in sentences)
    voice_audition_gate(sentences)
    place_name, place_desc, ambient_query, place_light, place_grade = FOLK_PLACE
    LOOK["grade"] = GRADES.get(place_grade, "null")
    WORK.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    log(f"Building folklore: {story['legend_name']} ({animal}) from {region}")
    log(f"Title: {story['title']}")

    fmt = story.get("format")
    modern = fmt in ("modern", "prehistoric")
    toon.EMERGE["seen"] = False  # the first time the monster shows up it fades in slowly, eyes first
    beats = assign_beats(sentences, modern, story.get("story_type"))
    log(("Modern indoor story. " if fmt == "modern" else "Prehistoric story. " if fmt == "prehistoric" else "") + "Beats: " + ", ".join(beats))
    n = len(sentences)
    words, durs = None, None
    audio_name = "voice.wav"
    mixed_ok = False
    try:
        if VOICE_MODE != "acted":
            raise RuntimeError("classic voice selected")
        audio_name, starts, clip_durs, gaps, delivs = make_story_voice(sentences, beats, story.get("narrator") or {})
        words = []
        for i, sn in enumerate(sentences):
            words += [(starts[i] + a, starts[i] + b, w) for a, b, w in even_words(sn["text"], clip_durs[i])]
        durs = [clip_durs[i] + gaps[i] for i in range(n)]
    except Exception as e:  # never lose the video because the acting failed
        if VOICE_MODE == "acted":
            log(f"Acted voice failed ({e}); using the classic narration instead.")
        words = None
        audio_name = "voice.wav"
        mixed_ok = False
        if VOICE_MODE != "acted" and GEMINI_API_KEY and any(sn.get("speaker", "narrator") != "narrator" for sn in sentences):
            try:  # the narrator plus separate voices for the spoken lines
                audio_name, m_starts, m_lens = make_mixed_voice(sentences, story.get("narrator") or {}, beats)
                words = []
                for i, sn in enumerate(sentences):
                    words += [(m_starts[i] + a, m_starts[i] + b, w) for a, b, w in even_words(sn["text"], m_lens[i])]
                durs = [(m_starts[i + 1] if i + 1 < n else audio_seconds(WORK / audio_name)) - m_starts[i] for i in range(n)]
                mixed_ok = True
            except Exception as e2:
                log(f"Separate character voices did not work ({e2}); reading the whole story in one voice.")
                words, durs, audio_name = None, None, "voice.wav"
        if mixed_ok:
            pass
        elif not gemini_tts(paced_text(sentences, range(len(sentences)), beats), WORK / "voice.wav", style=NARRATOR_STYLE,
                            voice=os.environ.get("GEMINI_VOICE") or GEMINI_VOICE):
            voice_fallback_check()
            log("Falling back to the basic free voice.")
            audio_name = "voice.mp3"
            words = asyncio.run(edge_voice(narration, str(WORK / "voice.mp3")))
    if VOICE_MODE == "acted":
        audio_name = humanize_voice(audio_name)
    elif audio_name == "voice.wav" and not mixed_ok:  # the single narrator voice gets the same cold, deeper treatment
        try:
            run(["ffmpeg", "-y", "-i", "voice.wav", "-af", SCARY_CHAIN, "-ar", "44100", "-ac", "1", "voice_dread.wav"], cwd=WORK)
            audio_name = "voice_dread.wav"
        except Exception as e3:
            log(f"Voice polish skipped: {e3}")
    lead = 0.0
    if VOICE_MODE != "acted":
        # breathing room: a short silence before the first word, a slightly slower voice, and a long hold at the end
        speech = audio_seconds(WORK / audio_name)
        lead, tail = 0.8, 1.9
        check_speech_fits(speech, lead, tail, 2.4)          # raises StoryTooLong (the room also leaves space for the silence before a kill sound)
        rate = 0.95 if speech / 0.95 + lead + tail < 57 else 1.0
        if speech / rate + lead + tail > 57.5:  # too long for a Short: speed the voice up just enough so the ending is never cut off
            rate = min(MAX_SPEEDUP, speech / (57.5 - lead - tail))
            log(f"Story is long ({speech:.1f}s of speech): voice sped up x{rate:.2f} so the ending fits.")
        run(["ffmpeg", "-y", "-i", audio_name, "-af",
             f"atempo={rate},adelay={int(lead * 1000)}|{int(lead * 1000)},apad=pad_dur={tail}", "-ar", "44100", "voice_paced.wav"], cwd=WORK)
        audio_name = "voice_paced.wav"
        if durs is not None:
            durs = [d_ / rate for d_ in durs]
            durs[0] += lead
            durs[-1] += tail
        if words:
            words = [(a / rate + lead, b / rate + lead, w) for a, b, w in words]
        else:
            words = [(a + lead, b + lead, w) for a, b, w in even_words(narration, speech / rate)]
    total = audio_seconds(WORK / audio_name)
    if not words:
        words = even_words(narration, total)

    creature = toon.folk_creature(animal, story.get("monster"))
    globals()["HOOK_EYE"] = tuple(creature.get("eye", (255, 170, 40)))[:3]      # the opening hook shows THIS creature's eyes
    if durs is None:
        chars = [max(len(s["text"]), 1) for s in sentences]
        speech_len = total - lead - (tail if lead else 0.0)
        durs = [speech_len * c / sum(chars) for c in chars]
        durs[0] += lead
        durs[-1] += (tail if lead else 0.3)
    PAUSES = {}
    SOUND_SYNC.clear()
    for i_, b_ in enumerate(beats):          # the kill sound gets the room to itself: the narrator finishes, then silence, then the sound
        if b_ in SOUND_PAUSE and not modern and i_ < n - 1 and beats[i_ + 1] != b_ and os.environ.get("SOUND_PAUSE", "1") != "0":
            PAUSES[i_] = SOUND_PAUSE[b_]
            PAUSE_EXPECTED.append(i_)
    if PAUSES:
        try:
            audio_name, words, durs = insert_sound_pauses(audio_name, words, durs, PAUSES)
            total = audio_seconds(WORK / audio_name)
        except Exception as e_:
            log(f"Narration pauses skipped ({e_}).")
            PAUSES = {}
            SOUND_SYNC.clear()
    settings = ["forest" if b == "dig" else "field" if b == "fly" else "village" for b in beats] if modern else assign_settings(sentences, beats)
    if modern:  # sentences that share a scene play as one longer scene, so its action is not restarted
        mb, md, ms = [], [], []
        for b_, d_, s_ in zip(beats, durs, settings):
            if mb and mb[-1] == b_ and not (fmt == "prehistoric" and b_ in ("valley", "erupt") and mb.count(b_) < 2):
                md[-1] += d_
            else:
                mb.append(b_)
                md.append(d_)
                ms.append(s_)
        beats, durs, settings, n = mb, md, ms, len(mb)
        log("Scenes: " + ", ".join(beats))
    log("Settings: " + ", ".join(settings))
    write_storyboard(sentences, beats, durs, settings, OUT / "storyboard.json")
    people = story.get("people", "alone")
    toon.CAST["kind"] = people if people in ("child", "elder", "pair", "dog") else None
    toon.CAST["role"] = story.get("hero") if story.get("hero") in toon.ROLES else None
    origin = story.get("origin") if story.get("origin") in ORIGINS else None
    log(f"Origin: {origin}. Hero: {toon.CAST['role']}, with: {toon.CAST['kind']}")
    WEATHER = {"stalk": dict(fog=0.55), "attack": dict(fog=0.6, redmoon=True), "aftermath": dict(fog=0.7, redmoon=True),
               "chase": dict(storm=True), "transform": dict(redmoon=True), "window": dict(storm=True)}
    PUSH = {"walk": 0.06, "stalk": 0.10, "home": 0.06, "village": 0.05, "hide": 0.06, "footprints": 0.0,
            "aftermath": 0.04, "mob": 0.05}   # calmer: slow, gentle push-ins
    SHAKE = {"chase": 3.5}
    VARY_SKY = ({}, {"sky": "fullmoon"}, {"fog": 0.5}, {"sky": "fullmoon", "fog": 0.3}, {"fog": 0.7}, {})
    FIRST_SIGN_AT = next((j for j, b in enumerate(beats) if b == "omen"), n)
    shots = plan_shots(beats, durs, fmt, story.get("story_type"))
    log(f"Shots ({len(shots)}): " + ", ".join(sh["beat"] + (f"[{sh['variant']}]" if sh["variant"] else "") for sh in shots))
    SMASH_AT.clear()
    scenes, impacts, blooded, hits = [], {}, False, {}
    cap_track, scene_at = [], 0.0
    for j, sh in enumerate(shots):
        i, dur, beat = sh["i"], sh["dur"], sh["beat"]
        wx = dict(WEATHER.get(beats[i]) or {})
        # the story's sign (red moon, eclipse...) is a motif, not wallpaper: it shows at the omen, the climax and the ending only
        key_moment = beats[i] in ("omen", "attack", "aftermath", "chase", "transform") or i == FIRST_SIGN_AT + 1 or i == n - 1
        if origin and i >= FIRST_SIGN_AT and "sky" not in wx and key_moment:
            wx["sky"] = origin
        if beats[i] == "omen":
            wx["sky"] = origin
        if not wx.get("sky"):                 # every other shot gets its own sky and mist so no two backdrops look alike
            for k_, v_ in VARY_SKY[j % len(VARY_SKY)].items():
                wx.setdefault(k_, v_)
        w = toon.World(toon.SETTINGS[settings[i]], 3 + j * 5, wx)
        w.mode = story.get("story_type")
        if sh.get("repeat"):
            w.pull = 0.22
        w.push = PUSH.get(beats[i], 0.0) if not sh["insert"] else 0.0
        w.shake = SHAKE.get(beats[i], 0.0) if not sh["insert"] else 0.0
        w.angle = shot_angle(j, beats[i], sh, modern, n, i)
        ANGLES_USED.append(w.angle)
        out = str(WORK / f"toon_s{j}.mp4")
        toon.HOST_SPEAKER = 1 if sentences[i].get("speaker") == "host" else 0   # who is talking in a host scene: reactor or storyteller
        w.clock = story.get("time") or "2:48"
        cur = dict(creature, bloody=True) if blooded else creature
        fpos = i / max(n - 1, 1)                 # where we are in the story: the creature is felt first, shown later
        veil = 0.92 if fpos < 0.30 else (0.55 if fpos < 0.50 else 0.0)
        cur = dict(cur, veil=veil)
        creature_v = dict(creature, veil=veil)
        g0 = 1300 - 740 * i / n
        g1 = 1300 - 740 * (i + 1) / n
        intro = (cur, min(1.4, dur * 0.5)) if i == 0 else None
        last = i == n - 1 and not sh["insert"]
        if re.search(toon.VEHICLE_RX, sentences[i].get("text", "").lower()) and re.search(toon.SMASH_VERB_RX, sentences[i].get("text", "").lower()):
            log(f"Vehicle-wreck sentence {i + 1}: shot {j}, beat {beats[i]}, {dur:.1f}s, creature veil {veil}, cutaway={sh['insert']}")
        if sh["insert"]:
            w.variant = sh["variant"]
            if beat == "valley":  # the wide view of the valley, used as an establishing cutaway
                w.push, w.pull = (0.0, 0.18) if sh["variant"] == "pull" else (0.18, 0.0)
            hits[j] = toon.INSERT_FUNCS[beat](out, dur, w, cur, cwd=WORK).get("hits", [])
        elif (not modern) and veil == 0.0 and beats[i] not in ("attack", "jump") and dur >= 3.0 and re.search(toon.ANATOMY_RX, sentences[i].get("text", "").lower()):
            _an = toon.anatomy_scene(out, dur, w, creature_v, cwd=WORK)           # the narration talks about the body: show what is inside it
            ANATOMY_LOG.append(i)
        elif veil == 0.0 and dur >= 2.8 and beats[i] not in ("attack", "jump") \
                and re.search(toon.VEHICLE_RX, sentences[i].get("text", "").lower()) and re.search(toon.SMASH_VERB_RX, sentences[i].get("text", "").lower()):
            if modern:                  # the modern stories are indoors: a wrecked car needs a road
                w = toon.World(toon.SETTINGS["pathway"], 3 + j * 5, {"fog": 0.5})
            _sm = toon.smash_scene(out, dur, w, creature_v, cwd=WORK)             # the narration wrecks a vehicle: show contact, crumple, debris, aftermath
            SMASH_AT[j] = _sm["impact_at"]
        elif modern:
            if beat == "jump":      # the scare lands a beat AFTER the last spoken word, in silence
                tail_ = (locals().get("tail", 0.45) if lead else 0.45)
                JUMP_FRAC[j] = min(0.88, max(0.45, (dur - tail_ + 0.35) / dur))
                toon.JUMP_CUT[0] = JUMP_FRAC[j]
            hits[j] = getattr(toon, beat + "_scene")(out, dur, w, cur, cwd=WORK).get("hits", [])
            toon.JUMP_CUT[0] = None
        elif beat == "attack":
            toon.PAUSE_REMAIN[0] = (PAUSES[i] + 0.1) if i in PAUSES and not sh.get("insert") else None
            _ai = toon.attack_scene(out, dur, w, creature_v, cwd=WORK)
            toon.PAUSE_REMAIN[0] = None
            impacts[j] = _ai["impact_at"]
            MAULS[j] = (_ai["maul_at"], _ai["maul_dur"])
            blooded = True
        elif beat == "aftermath" and not last:
            toon.ground_scene(out, dur, w, cur, cwd=WORK)
        elif beat == "aftermath":
            toon.aftermath_scene(out, dur, w, cur, cwd=WORK, outro=min(1.6, dur * 0.6))
        elif beat == "omen" and not (i == 0 or last):
            hits[j] = toon.omen_scene(out, dur, w, cur, cwd=WORK).get("hits", [])
        elif beat in SCENE_FUNCS and not (i == 0 or last):
            hits[j] = getattr(toon, beat + "_scene")(out, dur, w, cur, cwd=WORK).get("hits", [])
        else:
            outro = (cur, min(1.6, dur * 0.6)) if last else None
            toon.folk_scene(out, dur, w, cur, g0, g1, intro=intro, outro=outro,
                            roar_end=(beats[min(i + 1, n - 1)] == "attack"), cwd=WORK)
        cap_track += [(scene_at + tt, bb) for tt, bb in toon.LAST_TRACK]
        scene_at += dur
        scenes.append({"kind": "hold" if last else "scene", "dur": dur,
                       "clip": f"toon_s{j}.mp4", "plate": None})
    beats = [sh["beat"] for sh in shots]  # the sound and the score follow the shots, not the sentences
    durs = [sh["dur"] for sh in shots]
    has_caps = write_captions(words, total, WORK / "captions.ass",
                              card=(f"{region.upper()}  \u00b7  LOST WORLD #{episode}" if fmt == "prehistoric"
                                    else f"{region.upper()}  \u00b7  DARK FOLKLORE #{episode}"), track=cap_track,
                              card_big=animal)
    sounds = prepare_sounds(animal, animal, ambient_query)
    quiet = []
    events = folk_sound_events(durs, beats, impacts, sounds, hits, quiet, origin)
    events = sound_sync_check(events, words)
    try:
        splan = score_plan(story, beats, durs, impacts)
        log("Score: " + splan["mood"] + ", reveals at " + ", ".join(f"{r:.1f}s" for r, _ in splan["reveals"]))
    except Exception as e:
        log(f"Score plan failed ({e})")
        splan = None
    build_audio(audio_name, total, events, sounds["ambient"], "mix.wav", quiet, score=splan)
    out_path = OUT / "short.mp4"
    build_video(scenes, {"h": {"plate": None, "sprite": None, "clip": None}}, "mix.wav",
                "captions.ass" if has_caps else None, out_path)
    log(f"Video ready: {out_path} ({total:.1f}s of speech)")
    quality_gate(out_path, words, "folk", rebuild=lambda: build_video(scenes, {"h": {"plate": None, "sprite": None, "clip": None}}, "mix.wav",
                                                                      "captions.ass" if has_caps else None, out_path))

    title = story["title"][:80].rstrip() + " #Shorts"
    description = (
        (f"{story['legend_name']}: an original prehistoric horror story. Fiction, not real science.\n"
         "The animation is drawn by software, " if fmt == "prehistoric" else
         f"{story['legend_name']}: an original horror story. Fiction, not a true story.\n"
         "The animation is drawn by software, " if modern else
         f"{story['legend_name']}: a dark folk legend from {region}, retold for fun.\n"
         "The old tales say... this is folklore, not fact. The animation is drawn by software, ") +
        "the narration uses a synthetic voice, and the sound effects are synthesized or CC0.\n\n"
        "What would YOU have done? Tell me in the comments, and tell me which legend I should tell next!\n\n"
        "#shorts #folklore #darkfolklore #legend #scarystories"
    )
    tags = ["shorts", "folklore", "dark folklore", "legend", "scary stories", region, story["legend_name"]]
    (OUT / "meta.json").write_text(json.dumps(
        {"title": title, "description": description, "narration": narration, "series": "folklore"}, indent=2))
    if DRY_RUN or not os.environ.get("YT_REFRESH_TOKEN"):
        log("Not uploading (dry run, or YouTube is not set up yet). Download the video from the run's Artifacts.")
        return
    video_id = upload_to_youtube(out_path, title, description, tags)
    log(f"Uploaded as '{PRIVACY}': https://youtube.com/shorts/{video_id}")


# ---------------------------------------------------------------- the story must fit in a Short
# A real run produced 78.6 s of speech for a 59 s Short: the end of the story was cut off. Now the spoken length is MEASURED after the voice
# is made. A little too long: the voice is sped up (at most x1.2). Too long for that: the story is shortened (Gemini rewrites the same
# sentences shorter, plan.json is updated) and the whole build runs again.
MAX_SPEEDUP = 1.2
SHORT_LIMIT_S = 58.0
HYBRID_TAIL_S = 1.4          # silence after the last word in the hybrid series


class StoryTooLong(Exception):
    def __init__(self, speech, room):
        self.speech, self.room = speech, room
        super().__init__(f"{speech:.1f}s of speech cannot fit in {room:.1f}s even at x{MAX_SPEEDUP}")


def speech_room(lead, tail, extra=0.0):
    """Seconds of speech that can be fitted into a Short once the opening lead, the closing hold and any inserted silences are counted."""
    return SHORT_LIMIT_S - lead - tail - extra


def check_speech_fits(speech, lead, tail, extra=0.0):
    room = speech_room(lead, tail, extra)
    if speech / MAX_SPEEDUP > room:
        raise StoryTooLong(speech, room)
    return room


WEAK_END = {"the", "a", "an", "of", "and", "to", "in", "with", "that", "his", "her", "its", "their", "on", "at", "by", "for", "as", "but", "or", "from", "into", "than", "then", "was", "were", "is"}
STORY_WORD_CAP = 110


def _word_guard(data, attempt):
    """Called when Gemini returns a story: a story far over the word limit is asked for again (the last try is accepted, the build then shortens it)."""
    n = sum(len(str(s.get("text", "")).split()) for s in data.get("sentences", []))
    if n > STORY_WORD_CAP and attempt < 2:
        raise ValueError(f"the story has {n} words, the limit is {STORY_WORD_CAP}")


def trim_sentence(text, max_words):
    """Cut a sentence to at most max_words, at the last natural break (comma, dash) if there is one, else at a word that can end a sentence."""
    words = text.split()
    if len(words) <= max_words:
        return text
    lo = max(4, max_words // 2)
    cut = None
    for i in range(min(max_words, len(words)) - 1, lo - 2, -1):
        if re.search(r"[,;:\u2014\u2013-]$", words[i]):
            cut = i + 1
            break
    if cut is None:
        cut = max_words
        while cut > 4 and re.sub(r"\W", "", words[cut - 1]).lower() in WEAK_END:
            cut -= 1
    out = " ".join(words[:cut]).rstrip(" ,;:\u2014\u2013-")
    return out + "."


def trim_to_budget(sentences, target_w):
    """The no-fail way to shorten: drop the friend's reactions first, then cut the longest narrator sentences back to a natural break.
    The first sentence (the hook), the last (the closing question) and spoken lines of characters are never cut."""
    sents = [dict(s) for s in sentences]
    total = lambda: sum(len(s["text"].split()) for s in sents)
    for i in range(len(sents) - 2, 0, -1):
        if total() <= target_w:
            return sents
        if sents[i].get("speaker") == "host":
            del sents[i]
    guard = 0
    while total() > target_w and guard < 60:
        guard += 1
        cands = [i for i in range(1, len(sents) - 1) if sents[i].get("speaker", "narrator") == "narrator" and len(sents[i]["text"].split()) > 6]
        if not cands:
            break
        i = max(cands, key=lambda k: len(sents[k]["text"].split()))
        n = len(sents[i]["text"].split())
        sents[i]["text"] = trim_sentence(sents[i]["text"], max(5, int(n * 0.72)))
    return sents


def shorten_story_texts(sentences, speech, room):
    """Returns a shorter list of sentences that should fit: Gemini rewrites them shorter (up to three passes, each pass shown the last answer),
    then whatever is still too long is cut back deterministically, so this never fails to make progress."""
    target_s = room * 1.1                                  # a x1.1 voice then fits exactly
    words_now = sum(len(s["text"].split()) for s in sentences)
    target_w = max(35, int(words_now * target_s / max(speech, 1.0)))
    last = sentences[-1]["text"]
    cur = [s["text"] for s in sentences]
    best = None
    url = f"{API_BASE}/models/{GEMINI_MODEL}:generateContent"
    for attempt in range(3):
        w_cur = sum(len(x.split()) for x in cur)
        prompt = f"""Shorten this narration for a YouTube Short. There are {len(cur)} sentences, {w_cur} words now.
Rewrite EVERY sentence shorter so the whole thing is at most {int(target_w * 0.85)} words (count them!). Keep the same order and the same number of
sentences ({len(cur)}). Keep the shocking first sentence short and punchy, keep the twist, and keep the facts the pictures depend on (names, places,
what the creature does). Spoken lines and reactions stay very short. No sentence may become longer. Plain spoken English, no emojis.
The final sentence must still end with its question or call to action, using the same closing words as now: "{last[-60:]}".
Return JSON only: {{"sentences": ["...", "..."]}} with exactly {len(cur)} strings.

Current sentences:
""" + "\n".join(f"{i + 1}. {x}" for i, x in enumerate(cur))
        body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.5, "responseMimeType": "application/json"}}
        try:
            r = requests.post(url, params={"key": GEMINI_API_KEY}, json=body, timeout=90)
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            data = json.loads(re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip())
            new = [str(x).strip() for x in data["sentences"]]
            assert len(new) == len(cur) and all(new), "wrong number of sentences"
            w_new = sum(len(x.split()) for x in new)
            if w_new < w_cur:
                cur = new
                best = new
                if w_new <= target_w:
                    break
                log(f"Shortening pass {attempt + 1}: {w_cur} -> {w_new} words (wanted {target_w}); trying again.")
            else:
                log(f"Shortening pass {attempt + 1}: not shorter ({w_new} words).")
        except Exception as e:
            log(f"Shortening attempt {attempt + 1} failed: {e}")
    out = []
    for s_, t_ in zip(sentences, best or cur):
        s2 = dict(s_)
        if s_.get("speaker", "narrator") == "narrator" or best:
            s2["text"] = t_
        out.append(s2)
    out[-1]["text"] = out[-1]["text"] if out[-1]["text"].strip().endswith(("?", ".", "!")) else out[-1]["text"] + "."
    if sum(len(s["text"].split()) for s in out) > target_w:
        out = trim_to_budget(out, target_w)
        log(f"The rewrite was still too long, so the longest sentences were cut back to {sum(len(s['text'].split()) for s in out)} words.")
    return out


def build_with_story_fit(builder):
    """Run the build; if the story is too long to fit, shorten it once or twice and run the build again."""
    for attempt in range(3):
        try:
            return builder()
        except StoryTooLong as e:
            if attempt == 2:
                sys.exit(f"STOPPED - the story is still too long for a Short ({e}). Nothing uploaded.")
            log(f"The story is too long for a Short ({e}). Shortening it and building again.")
            plan_file = PLAN_DIR / "plan.json"
            plan = json.loads(plan_file.read_text())
            sents = plan["story"]["sentences"]
            new = shorten_story_texts(sents, e.speech, e.room)
            plan["story"]["sentences"] = new
            plan_file.write_text(json.dumps(plan, indent=1))
            log(f"Story shortened to {sum(len(s['text'].split()) for s in new)} words: " + " ".join(s["text"] for s in new))



def plan_main():
    if not GEMINI_API_KEY:
        sys.exit("Missing secret: GEMINI_API_KEY")
    OUT.mkdir(exist_ok=True)
    PLAN_DIR.mkdir(exist_ok=True)
    history = load_history()
    # the two series take turns: hybrid animals, then dark folklore, and so on
    series = "folklore" if len(history) % 2 == 1 else "hybrid"
    episode = 1 + sum(1 for k in history if k.startswith("folk:") == (series == "folklore"))
    plan, lines = None, []
    if series == "folklore":
        try:
            story = ask_folklore(history)
            mo = story.get("monster", {})
            key = (f"folk:{story['animal']}:{story['legend_name'].replace('|', ' ').replace(':', ' ')}"
                   f"|{mo.get('body', '')}|{story.get('story_type', '')}|{mo.get('palette', '')}|{story['region'].replace('|', ' ')}"
                   f"|{story.get('origin', '')}")
            plan = {"series": "folklore", "key": key, "episode": episode, "story": story}
            log(f"Folklore #{episode}: {story['legend_name']} ({story['region']})")
            lines = [f"# {story['title']}", "", f"**Dark Folklore #{episode}:** {story['legend_name']} ({story['region']})", "",
                     "No clips are needed for this one. The Build step draws the whole animation.", "",
                     "## The story that will be spoken", "", " ".join(x["text"] for x in story["sentences"]), ""]
        except RuntimeError as e:
            log(f"Folklore story failed ({e}); making a hybrid story instead.")
            series = "hybrid"
            episode = 1 + sum(1 for k in history if not k.startswith("folk:"))
    if series == "hybrid":
        key, a, b, place = pick_idea(history)
        place_name, place_desc = place[0], place[1]
        log(f"Hybrid #{episode}: {a} + {b} in {place_name}")
        import toon
        designs = load_designs()
        avoid = [d["fingerprint"] for d in designs if "fingerprint" in d]
        gd = toon.design_hybrid(a, b, avoid=avoid, seed=len(designs))
        prof = toon.design_profile(gd, f"{a}-{b}")
        log(f"Design: {prof['silhouette']} | {prof['locomotion']} | difference from earlier hybrids: {gd['fp_distance']}")
        design_text = "; ".join(toon.describe(gd)) + ". It moves: " + toon.describe_move(gd) + "."
        story = ask_gemini(a, b, place_name, design_text)
        designs.append(toon.designs_to_json(gd))
        DESIGNS_FILE.write_text(json.dumps(designs, indent=1))
        plan = {"series": "hybrid", "key": key, "a": a, "b": b, "place": list(place), "story": story,
                "episode": episode, "design": toon.designs_to_json(gd), "profile": prof,
                "clips": [{"title": t, "prompt": p} for t, p in
                          clip_prompts(story["hybrid_look"], place_name, place_desc,
                                       story.get("prey", "a small animal"), story.get("signature_move", "it pounces"))]}
        lines = [f"# {story['title']}", "",
                 f"**Impossible Animals #{episode}:** {story['creature_name']} ({a} + {b}) in {place_name}", "",
                 "## Optional: your own AI video clips (the video is made without them)", "",
                 "Use PixVerse or Kling (text-to-video), **9:16**, **5 seconds**. Save the results as "
                 "`clip1.mp4`, `clip2.mp4`, `clip3.mp4` and upload them to the `clips` folder.", ""]
        for i, c in enumerate(plan["clips"], 1):
            lines += [f"### {c['title']}", "", "```", c["prompt"], "```", ""]
        lines += ["## The story that will be spoken", "", " ".join(x["text"] for x in story["sentences"]), ""]
    (PLAN_DIR / "plan.json").write_text(json.dumps(plan, indent=1))
    history.append(plan["key"])
    HISTORY_FILE.write_text(json.dumps(history, indent=1))
    md = "\n".join(lines)
    (OUT / "PROMPTS.md").write_text(md)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(md)
    log("Plan saved. Open the run page and read the summary.")


def find_clips():
    files = sorted(p for p in CLIPS_DIR.glob("*") if p.suffix.lower() in CLIP_EXT) if CLIPS_DIR.exists() else []
    good = []
    for p in files:
        dst = WORK / f"in_{len(good) + 1}{p.suffix.lower()}"
        shutil.copy(p, dst)
        if clip_seconds(dst) >= 0.5:
            good.append(dst.name)
        else:
            log(f"Clip {p.name} cannot be read, skipping it.")
    log(f"Video clips found: {len(good)}")
    return good[:3]


def find_user_image():
    """A picture you made yourself (jpg/png/webp in the clips folder). Used as the hybrid's picture."""
    files = sorted(p for p in CLIPS_DIR.glob("*") if p.suffix.lower() in IMAGE_EXT) if CLIPS_DIR.exists() else []
    for p in files:
        dst = WORK / f"user_img{p.suffix.lower()}"
        shutil.copy(p, dst)
        try:
            compose_vertical(dst.name, "user_h.png")
            log(f"Using your picture {p.name} as the hybrid picture")
            return "user_h.png"
        except RuntimeError:
            log(f"Picture {p.name} cannot be read, skipping it.")
    return None


def build_main():
    if not GEMINI_API_KEY:
        sys.exit("Missing secret: GEMINI_API_KEY")
    plan_file = PLAN_DIR / "plan.json"
    if not plan_file.exists():
        sys.exit("No plan found. Run the 'Plan' workflow first.")
    plan = json.loads(plan_file.read_text())
    if plan.get("series") == "folklore":
        return build_folk_main(plan)
    WORK.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    key, a, b = plan["key"], plan["a"], plan["b"]
    place_name, place_desc, ambient_query, place_light, place_grade = plan["place"]
    story = plan["story"]
    sentences = story["sentences"]
    look = story["hybrid_look"]
    narration = " ".join(s["text"] for s in sentences)
    voice_audition_gate(sentences)
    LOOK["grade"] = GRADES.get(place_grade, "null")
    log(f"Building: {a} + {b} in {place_name}")
    log(f"Title: {story['title']}")
    clips = find_clips()

    # --- voice
    words = None
    audio_name = "voice.wav"
    sent_durs = None
    if any(s.get("speaker") == "host" for s in sentences):
        try:   # the narrator in his own voice, and the curious friend (the second host) in a different one
            audio_name, h_starts, h_lens = make_mixed_voice(sentences, story.get("narrator") or {}, None)
            words = []
            for i_, sn in enumerate(sentences):
                words += [(h_starts[i_] + a_, h_starts[i_] + b_, w_) for a_, b_, w_ in even_words(sn["text"], h_lens[i_])]
            sent_durs = [(h_starts[i_ + 1] if i_ + 1 < len(sentences) else audio_seconds(WORK / audio_name)) - h_starts[i_]
                         for i_ in range(len(sentences))]
        except Exception as e_:
            log(f"Host voices did not work ({e_}); the hosts are dropped and the narrator reads the story alone.")
            sentences[:] = [s for s in sentences if s.get("speaker") != "host"]
            narration = " ".join(s["text"] for s in sentences)
            words, audio_name, sent_durs = None, "voice.wav", None
    if sent_durs is None and not gemini_tts(narration, WORK / "voice.wav", style=NARRATOR_STYLE):
        voice_fallback_check()
        log("Falling back to the basic free voice.")
        audio_name = "voice.mp3"
        words = asyncio.run(edge_voice(narration, str(WORK / "voice.mp3")))
    total = audio_seconds(WORK / audio_name)
    room = check_speech_fits(total, HYBRID_TAIL_S, 0.4)            # raises StoryTooLong: the build is repeated with a shorter story
    if total > room:                                            # a little too long: speed the voice up just enough, never cut the ending
        rate_ = total / room
        log(f"Story is long ({total:.1f}s of speech): voice sped up x{rate_:.2f} so the ending fits.")
        run(["ffmpeg", "-y", "-i", audio_name, "-af", f"atempo={rate_:.4f}", "-ar", "44100", "-ac", "1", "voice_fit.wav"], cwd=WORK)
        audio_name = "voice_fit.wav"
        if words:
            words = [(a_ / rate_, b_ / rate_, w_) for a_, b_, w_ in words]
        if sent_durs:
            sent_durs = [d_ / rate_ for d_ in sent_durs]
        total = audio_seconds(WORK / audio_name)
    if not words:
        words = even_words(narration, total)
    has_caps = write_captions(words, total, WORK / "captions.ass",
                              card=f"{place_name.upper()}  \u00b7  IMPOSSIBLE ANIMALS #{plan.get('episode', 1)}",
                              card_big=(plan.get("story") or {}).get("creature_name"))

    # --- the animation: every creature is drawn and animated by the bot (flat, stylized look)
    import toon
    toon.CINEMA["fg"] = True      # dark foreground plants pass in front of the action (hybrid series)
    sa, sb = toon.pure_spec(a), toon.pure_spec(b)
    if plan.get("design"):
        sh = toon.hybrid_spec_from(plan["design"])  # the saved identity profile: same body in every scene
    else:
        sh = toon.hybrid_spec_from(toon.design_hybrid(a, b))
    scenes = plan_scenes(sentences, total, sent_durs)
    END_TAIL_S = HYBRID_TAIL_S          # a proper ending: after the last word the picture and the sound get time to land (and the loop begins)
    try:
        run(["ffmpeg", "-y", "-i", audio_name, "-af", f"apad=pad_dur={END_TAIL_S}", "-ar", "44100", "-ac", "1", "voice_end.wav"], cwd=WORK)
        audio_name = "voice_end.wav"
        total += END_TAIL_S
        scenes[-1]["dur"] += END_TAIL_S
    except Exception as e_:
        log(f"Ending tail could not be added ({e_}).")
    first = scenes[0]
    t = open_times(first["hook"], first["morph"])
    world = toon.World(place_name, 1)
    log(f"Animating: {a} ({sa['kind']}), {b} ({sb['kind']}), hybrid ({sh['kind']}: {'; '.join(toon.describe(sh['g']))})")
    world.focus_y = 0.70                     # keep the camera on the creature, not the empty sky
    world.push = 0.16
    toon.walk_scene(str(WORK / "toon_a.mp4"), t["la"], world, sa, 1, (1.0, 1.1), cwd=WORK, intro=(sh, 1.3))
    toon.walk_scene(str(WORK / "toon_b.mp4"), t["lb"], world, sb, -1, (1.0, 1.1), cwd=WORK)
    world.push = 0.08
    toon.meet_scene(str(WORK / "toon_m.mp4"), t["lm"], world, sa, sb, cwd=WORK)
    world.push = 0.22
    toon.walk_scene(str(WORK / "toon_h.mp4"), t["lh"], world, sh, 1, (0.95, 1.15), hybrid_roar_end=True, cwd=WORK)
    assets = {"a": {"plate": None, "sprite": None, "clip": "toon_a.mp4"},
              "b": {"plate": None, "sprite": None, "clip": "toon_b.mp4"},
              "mix": "toon_m.mp4",
              "h": {"plate": None, "sprite": None, "clip": "toon_h.mp4"}}
    if clips:  # a clip you made yourself replaces the drawn reveal
        assets["h"]["clip"] = clips[0]

    middle = [i for i, sc in enumerate(scenes) if sc["kind"] == "scene"]
    hunt_at = middle[len(middle) // 2] if middle else None
    named = [i for i in middle if sentences[scenes[i]["si"]].get("action") == "hunt"]
    if named:
        hunt_at = named[0]                      # the pounce is shown where the story says it pounces
    elif any(sentences[scenes[i]["si"]].get("action") for i in middle):
        hunt_at = None if story.get("format") == "Cryptid encounter" else hunt_at
    hunt_info = None
    k = 0
    prev_shot = None
    scene_tracks = {}
    used_places = []
    for i_, sc_ in enumerate(scenes):         # which scenes must DEMONSTRATE an ability the narration describes
        if sc_["kind"] == "scene" and i_ != hunt_at and not sc_.get("host") and sentences[sc_["si"]].get("action") != "hunt":
            sc_["ability"] = detect_ability(sentences[sc_["si"]].get("text", "")) if sc_["dur"] >= 2.6 else None
            sc_["anatomy"] = bool(re.search(toon.ANATOMY_RX, sentences[sc_["si"]].get("text", "").lower())) and sc_["dur"] >= 3.0
            if sc_["anatomy"]:
                sc_["ability"] = None
            if sc_["ability"] or sc_["anatomy"]:
                ABILITY_EXPECTED.append(i_)
    sh0 = sh
    for i, sc in enumerate(scenes):
        if sc["kind"] == "open":
            continue
        fp_ = sc.get("si", len(sentences) - 1) / max(len(sentences) - 1, 1)
        sh = dict(sh0, veil=(0.9 if fp_ < 0.4 else (0.5 if fp_ < 0.6 else 0.0))) if story.get("format") == "Cryptid encounter" else sh0
        if sc["kind"] == "scene" and 0 < i < len(scenes) - 1:
            loc = choose_scene_place(i, (sentences[sc["si"]].get("text", "") + " " + sentences[sc["si"]].get("image", "")), place_name, a, b, used_places)
        else:
            loc = place_name
        if sc.get("ability") == "swim":
            loc = "the deep ocean"
        used_places.append(loc)
        sc["place"] = loc
        w = toon.World(loc, i + 2)
        w.focus_y = 0.70
        w.push = 0.18 if i % 2 == 0 else 0.0     # a slow push-in on every other shot, otherwise a locked frame
        w.angle = "wide" if (sc.get("host") or i == hunt_at or sc["kind"] == "hold") else ANGLE_CYCLE[i % len(ANGLE_CYCLE)]
        ANGLES_USED.append(w.angle)
        PLACES_USED.append(sc.get("place", place_name))
        out = str(WORK / f"toon_s{i}.mp4")
        if sc["kind"] == "hold":
            toon.walk_scene(out, sc["dur"], w, sh, 1, (1.0, 1.15), cwd=WORK, outro=(sh, min(1.5, sc["dur"] * 0.6)))
        elif sentences[sc["si"]].get("action") == "hunt" and i != hunt_at:     # a second kill sentence: the close-up of claws and jaws
            if os.environ.get("HYBRID_GORE", "0") == "1":
                toon.maul_scene(out, sc["dur"], w, sh, story.get("prey", ""), cwd=WORK)
                sc["maul"] = True
            else:
                toon.chase_scene(out, sc["dur"], w, sh, story.get("prey", ""), cwd=WORK)
            sc["steps"] = []
        elif sc.get("host"):                      # the two hosts by the fire, recalling what happened to them
            toon.HOST_SPEAKER = 1 if sentences[sc["si"]].get("speaker") == "host" else 0
            toon.hosts_scene(out, sc["dur"], w, sh, cwd=WORK)
            sc["steps"] = []
            sc["hosts"] = True
        elif i == hunt_at:
            globals()["HOOK_EYE"] = tuple(sh.get("eye", (255, 170, 40)))[:3]
            if os.environ.get("HYBRID_GORE", "0") == "1":
                hunt_info = toon.pounce_scene(out, sc["dur"], w, sh, story.get("prey", ""), cwd=WORK)
            else:       # this series stays non-graphic: a multi-shot chase with the whole prey in view
                hunt_info = toon.chase_scene(out, sc["dur"], w, sh, story.get("prey", ""), cwd=WORK)
            sc["hunt"] = True
            sc["steps"] = hunt_info.get("steps", [])
        elif sc["dur"] >= 2.8 and re.search(toon.VEHICLE_RX, sentences[sc["si"]].get("text", "").lower()) \
                and re.search(toon.SMASH_VERB_RX, sentences[sc["si"]].get("text", "").lower()):
            sc["smash_at"] = toon.smash_scene(out, sc["dur"], w, sh, cwd=WORK)["impact_at"]      # the narration wrecks a vehicle
            sc["steps"] = []
        elif sc.get("anatomy"):                  # the narration talks about bones or anatomy
            toon.anatomy_scene(out, sc["dur"], w, sh, cwd=WORK)
            sc["steps"] = []
            ANATOMY_LOG.append(i)
        elif sc.get("ability"):                  # the narration names an ability: show it happening
            alt = next((p for p in ("the Sahara desert", "Antarctica", "a rainforest after a flood", "a swamp full of toxic mud") if p != sc["place"]), "the Sahara desert")
            toon.ability_scene(out, sc["dur"], w, sh, sc["ability"], sc["place"], alt, cwd=WORK)
            sc["steps"] = []
            ABILITY_LOG.append((sc["ability"], i))
        else:
            # a different physical action in each shot: walk, feed, turn, walk the other way, a close-up reaction
            kinds = ["face", "feed", "turn", "pov", "loom", "shoulder"]       # walking is NOT a filler: only when the story is about size, weight or approach
            shot_type = kinds[k % len(kinds)]
            act = sentences[sc["si"]].get("action") if "si" in sc else ""
            stext = sentences[sc["si"]].get("text", "") if "si" in sc else ""
            if act and act != "hunt":
                shot_type = act                  # the picture is what the sentence says the creature is doing
                if shot_type in ("walk", "walkback") and not needs_walk(stext):
                    shot_type = kinds[k % len(kinds)]
                    WALK_LOG.append(("walk replaced", i))
                if shot_type == prev_shot:
                    shot_type = {"walk": "walkback", "walkback": "walk"}.get(shot_type, shot_type)
            elif needs_walk(stext):
                shot_type = "walk" if shot_type != prev_shot else "walkback"
            if sc["dur"] < 3.6 and shot_type in ("feed", "turn"):
                shot_type = "walk"
            if sc["dur"] < 2.4 and shot_type in ("pov", "loom", "shoulder", "chase", "footprints"):
                shot_type = "walk"
            prev_shot = shot_type
            if shot_type in ("footprints", "pov", "loom", "shoulder", "chase"):
                getattr(toon, shot_type + "_scene")(out, sc["dur"], w, sh, cwd=WORK)
                sc["steps"] = []
            elif shot_type == "walk":
                toon.walk_scene(out, sc["dur"], w, sh, 1, (0.95, 1.1), cwd=WORK)
                sc["steps"] = toon.walk_steps(sh, sc["dur"])
            elif shot_type == "feed":
                sc["steps"] = toon.feed_scene(out, sc["dur"], w, sh, cwd=WORK, food_col=tuple(toon.pure_spec(b)["color"])[:3])["steps"]
            elif shot_type == "turn":
                sc["steps"] = toon.turn_scene(out, sc["dur"], w, sh, cwd=WORK)["steps"]
            elif shot_type == "walkback":
                toon.walk_scene(out, sc["dur"], w, sh, -1, (0.9, 1.0), cwd=WORK)
                sc["steps"] = toon.walk_steps(sh, sc["dur"])
            else:
                toon.face_scene(out, sc["dur"], w, sh, cwd=WORK)
            k += 1
        scene_tracks[i] = list(toon.LAST_TRACK)
        sc["clip"] = f"toon_s{i}.mp4"
        sc["plate"] = None
    ct, at0 = [], 0.0                       # subtitles step out of the creature's way in this series too
    for i, sc in enumerate(scenes):
        ct += [(at0 + tt, bb) for tt, bb in scene_tracks.get(i, [])]
        at0 += sc["dur"]
    has_caps = write_captions(words, total, WORK / "captions.ass",
                              card=f"{place_name.upper()}  \u00b7  IMPOSSIBLE ANIMALS #{plan.get('episode', 1)}",
                              card_big=(plan.get("story") or {}).get("creature_name"), track=ct)
    if len(clips) >= 2 and middle and hunt_at is not None:
        scenes[hunt_at]["clip"] = clips[1]
    if len(clips) >= 3:
        scenes[-1]["clip"] = clips[2]

    # --- sound
    sounds = prepare_sounds(a, b, ambient_query)
    step_abs, at_ = [], scenes[0]["dur"]
    for sc in scenes[1:]:
        step_abs += [at_ + x for x in sc.get("steps", [])]
        at_ += sc["dur"]
    steps_file = make_steps(step_abs, toon.chim_mass(sh), "steps.wav", total) if step_abs else None
    events = sound_events(scenes, sounds, hunt_info, steps_file)
    build_audio(audio_name, total, events, sounds["ambient"], "mix.wav", score=ramp_score(total))

    out_path = OUT / "short.mp4"
    build_video(scenes, assets, "mix.wav", "captions.ass" if has_caps else None, out_path)
    log(f"Video ready: {out_path} ({total:.1f}s of speech, AI video clips used: {len(clips)}/3, "
        f"real animal calls: {sounds['real_calls']}/2)")
    quality_gate(out_path, words, "hybrid", rebuild=lambda: build_video(scenes, assets, "mix.wav", "captions.ass" if has_caps else None, out_path))
    remember_locations(PLACES_USED)

    title = story["title"][:80].rstrip() + " #Shorts"
    description = (
        f"{story['creature_name']}: an imaginary animal hybrid ({a} + {b}) in {place_name}.\n"
        "This is a fictional story made for fun and the creature is not real. "
        "The animation is drawn by software, the narration uses a synthetic voice, "
        "and the sound effects are synthesized or CC0.\n\n"
        "Comment which two animals I should combine next!\n\n"
        "#shorts #animals #whatif #speculativebiology"
    )
    tags = ["shorts", "animals", "what if", "hybrid animals", "speculative biology", a, b]
    (OUT / "meta.json").write_text(json.dumps(
        {"title": title, "description": description, "narration": narration, "format": story.get("format"),
         "actions": [x.get("action", "") for x in sentences], "video_clips_used": len(clips), "real_animal_calls": sounds["real_calls"]}, indent=2))

    if DRY_RUN or not os.environ.get("YT_REFRESH_TOKEN"):
        log("Not uploading (dry run, or YouTube is not set up yet). Download the video from the run's Artifacts.")
        return
    video_id = upload_to_youtube(out_path, title, description, tags)
    log(f"Uploaded as '{PRIVACY}': https://youtube.com/shorts/{video_id}")



if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "build"
    if mode == "plan":
        plan_main()
    else:
        build_with_story_fit(build_main)
