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
WORK = ROOT / "work"
OUT = ROOT / "output"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY", "")
FREESOUND_API_KEY = os.environ.get("FREESOUND_API_KEY", "")
CF_ACCOUNT = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
CF_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
CF_MODEL = os.environ.get("CF_IMAGE_MODEL", "@cf/black-forest-labs/flux-1-schnell")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
GEMINI_VOICE = os.environ.get("GEMINI_VOICE", "Charon")
# "classic" = the original single-take narrator voice (the one you approved); "acted" = a new mood for every sentence
VOICE_MODE = os.environ.get("VOICE_MODE", "classic").strip().lower()
EDGE_VOICE = os.environ.get("TTS_VOICE", "en-US-AndrewNeural")
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
def ask_gemini(a, b, place_name):
    prompt = f"""You write narration for 40-second YouTube Shorts about IMAGINARY animal hybrids.

Idea: a hybrid of a {a} and a {b}, living in {place_name}.

Return ONLY valid JSON, no markdown, in exactly this shape:
{{
  "title": "catchy title under 70 characters, no hashtags",
  "creature_name": "a fun invented name for the hybrid",
  "prey": "a specific animal that lives in {place_name} and that the hybrid hunts",
  "signature_move": "one sentence: the hybrid's signature move on that prey, using abilities taken from the {a} and the {b} (an action, not gore)",
  "hybrid_look": "one detailed sentence describing how the creature LOOKS: a fierce, scary, dominant apex predator (menacing face, glowing eyes, scars, fangs or claws or spikes, huge muscular body), clearly showing specific features taken from the {a} and from the {b}",
  "sentences": [
    {{"text": "one spoken sentence", "image": "the SETTING shown while this sentence is spoken"}}
  ]
}}

Rules for "sentences" (7 to 9 items, 85 to 105 words in total, plain spoken English, no emojis):
- Sentence 1: a short, shocking hook (under 14 words) about the most terrifying thing this creature does.
- Sentence 2: names the creature, says it is imaginary, and says what it inherits from the {a} and from the {b}.
- One middle sentence describes the signature move on its prey.
- Middle sentences: each one a specific danger of {place_name} and the clever trait that solves it.
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
            data["sentences"] = sents[:9]
            data["prey"] = str(data.get("prey") or "a small desert animal").strip()
            data["signature_move"] = str(data.get("signature_move") or "it pounces with lightning speed").strip()
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


def gemini_tts(text, dest_wav, style=None, voice=None):
    """More natural voice from Gemini. Returns True on success."""
    headers = {"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}
    style = style or "an excited, curious nature-documentary narrator, warm and dramatic, natural pacing"
    attempts = []
    for model in TTS_MODELS:
        attempts.append((model, "interactions", True))
        attempts.append((model, "interactions", False))
        attempts.append((model, "classic", True))
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
    for kind in ("whoosh", "boom", "riser", "growl", "eerie", "victim", "distant", "heart", "tap", "creak", "scrape", "flap", "roar", "rumble", "splash", "gust", "crash"):
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


def build_audio(voice_name, total, events, ambient, out_wav, quiet=None):
    """Mix the narration with timed sound effects and a quiet ambient bed."""
    cmd = ["ffmpeg", "-y", "-i", voice_name, "-stream_loop", "-1", "-i", ambient]
    fmt = "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo"
    parts = [f"[0:a]aresample=44100,{fmt}[v]",
             f"[1:a]aresample=44100,{fmt},atrim=0:{total + 1:.2f},volume=0.14"
             + "".join(f",volume='if(between(t,{a:.2f},{b:.2f}),0,1)':eval=frame" for a, b in (quiet or [])) + "[amb]"]
    labels = ["[v]", "[amb]"]
    for i, ev in enumerate(events):
        cmd += ["-i", ev["file"]]
        ms = max(int(ev["at"] * 1000), 0)
        cut = f",atrim=0:{ev['max']}" if ev.get("max") else ""
        parts.append(f"[{i + 2}:a]aresample=44100,{fmt}{cut},asetpts=PTS-STARTPTS,"
                     f"volume={ev['vol']},adelay={ms}|{ms}[e{i}]")
        labels.append(f"[e{i}]")
    parts.append("".join(labels) + f"amix=inputs={len(labels)}:duration=first:normalize=0:"
                 "dropout_transition=0,alimiter=limit=0.95:level=0[mix]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[mix]", out_wav]
    run(cmd, cwd=WORK)


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


CAPTION_SPOTS = (1400, 1540, 640, 880)   # where the bottom edge of the caption can sit, in order of preference
CAPTION_H, CAPTION_X0, CAPTION_X1 = 190, 200, 880


def _cap_overlap(track, t0, t1, bottom):
    """How much of the caption's box the creature covers at its worst moment in [t0, t1] (0..1)."""
    worst = 0.0
    top = bottom - CAPTION_H
    area = (CAPTION_X1 - CAPTION_X0) * CAPTION_H
    for t, box in track:
        if t < t0 - 0.1 or t > t1 + 0.1 or not box:
            continue
        # the video is zoomed 5% and shifted a little before the subtitles are drawn
        x0, y0, x1, y1 = box[0] * 1.05 - 27, box[1] * 1.05 - 48, box[2] * 1.05 - 27, box[3] * 1.05 - 48
        w = min(x1, CAPTION_X1) - max(x0, CAPTION_X0)
        h = min(y1, bottom) - max(y0, top)
        if w > 0 and h > 0:
            worst = max(worst, w * h / area)
    return worst


def write_captions(words, total, path, card=None, track=None):
    if not words:
        return False
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Liberation Sans Narrow,112,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,"
        "1,0,0,0,100,100,1,0,1,8,3,2,60,60,520,1\n"
        "Style: Card,Liberation Sans Narrow,62,&H00D9D9D9,&H000000FF,&H00000000,&H00000000,"
        "1,0,0,0,100,100,10,0,1,3,0,8,60,60,330,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    group = 2
    prev_y, prev_end = CAPTION_SPOTS[0], -9.0
    for i in range(0, len(words), group):
        chunk = words[i:i + group]
        start = chunk[0][0]
        nxt = words[i + group][0] if i + group < len(words) else total
        end = max(chunk[-1][1], min(nxt, chunk[-1][1] + 0.4))
        toks = [w[2].upper().replace("{", "").replace("}", "") for w in chunk]
        key = max(range(len(toks)), key=lambda j: len(toks[j]))  # the longest word is shown in orange
        toks[key] = "{\\c&H001E9BFF&}" + toks[key] + "{\\c&H00FFFFFF&}"
        text = " ".join(toks)
        y = CAPTION_SPOTS[0]
        if track:
            # keep the last spot if it is still clear, otherwise take the first free one (or the least covered one)
            cands = [prev_y] + [c for c in CAPTION_SPOTS if c != prev_y]
            scores = [(_cap_overlap(track, start, end, c), k) for k, c in enumerate(cands)]
            clear = [c for (sc, k), c in zip(scores, cands) if sc < 0.04]
            y = clear[0] if clear else cands[min(scores)[1]]
        if y != prev_y and start - prev_end < 0.6:  # a quick, smooth slide from where the last caption was
            tag = f"{{\\an2\\move(540,{prev_y},540,{y},0,170)}}"
        else:
            tag = f"{{\\an2\\pos(540,{y})}}"
        prev_y, prev_end = y, end
        lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{tag}{text}")
    if card:
        lines.append(f"Dialogue: 1,{ass_time(1.5)},{ass_time(3.9)},Card,,0,0,0,,{{\\fad(300,450)}}{card}")
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


def plan_scenes(sentences, total):
    """Split the audio time across sentences, then decide what to show for each."""
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
        scenes.append({"kind": "scene", "dur": ds[i], "image": sentences[i]["image"]})
        i += 1
    scenes.append({"kind": "hold", "dur": ds[n - 1] + 0.3})
    return scenes


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
          f"eq=brightness='{flash}':saturation=0.92:contrast=1.06:eval=frame,"
          "vignette=PI/4.5,noise=alls=16:allf=t+u")
    if captions_name:
        vf += f",subtitles={captions_name}"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt", "-i", audio_name,
         "-vf", vf, "-map", "0:v", "-map", "1:a",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "160k", "-shortest", "-t", "59", str(out_path)], cwd=WORK)


def sound_events(scenes, sounds, hunt=None):
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
        if sc.get("hunt") and hunt:
            ev.append({"file": sounds["eerie"], "at": at + hunt["zoom_at"] - 0.05, "vol": 0.9})
            ev.append({"file": sounds["boom"], "at": at + hunt["zoom_at"], "vol": 0.6})
            ev.append({"file": sounds["growl"], "at": at + hunt["lunge_at"] - 0.1, "vol": 0.8})
            ev.append({"file": sounds["boom"], "at": at + hunt["lunge_at"] + 0.25, "vol": 0.7})
        at += sc["dur"]
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
    if not gemini_tts(narration, WORK / "voice.wav"):
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
    build_audio(audio_name, total, events, sounds["ambient"], "mix.wav")

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
NATURE_BODIES = {"sea": ("serpent", "beast"), "lake": ("serpent", "hollow", "stalker"), "lava": ("beast", "stalker"),
                 "storm": ("wraith", "stalker", "owlman"), "tsunami": ("beast", "serpent"), "titan": ("beast", "stalker", "hollow")}
MODERN_BODIES = ("crawler", "stalker", "wraith", "owlman", "beast", "arachnid", "hollow")
BODY_HINT = ("beast = a four-legged hunting predator (dog, wolf, bear or bull-like); stalker = a tall thin antlered forest spirit; "
             "arachnid = a huge spider-like weaver or trapper; serpent = a giant snake or river creature; "
             "wraith = a floating ghost, banshee or cloaked spirit; owlman = a winged night bird or bat-thing; "
             "crawler = an upside-down man-dog thing that hangs from ceilings and crawls on walls; "
             "hollow = a far too tall, faceless pale man in a dark suit with a thin grin; "
             "djinn = a smoke genie with horns and a burning grin (only for lamp stories)")


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


def folk_variety(history):
    """Decide the plot shape, hook, ending, region and monster look for the next legend, avoiding recent ones."""
    past = [p for p in (_parse_folk_key(k) for k in history) if p]
    def recent(field, n):
        return [p[field] for p in past[-n:] if p[field]]
    # modern, indoor first-person stories take turns with the old legends (never three of the same kind in a row)
    kinds = ["modern" if p["type"] in MODERN_TYPES else "legend" for p in past[-2:]]
    if len(kinds) == 2 and kinds[0] == kinds[1]:
        fmt = "legend" if kinds[0] == "modern" else "modern"
    else:
        fmt = "modern" if random.random() < 0.6 else "legend"
    pool = MODERN_TYPES if fmt == "modern" else STORY_TYPES
    types = [k for k in pool if k not in recent("type", len(pool) // 2 + 1)] or list(pool)
    allowed = MODERN_BODIES if fmt == "modern" else ("beast", "stalker", "arachnid", "serpent", "wraith", "owlman")
    bodies = [b for b in allowed if b not in recent("body", 3)] or [allowed[0]]
    palettes = [c for c in PALETTE_NAMES if c not in recent("palette", 4)] or list(PALETTE_NAMES)
    regions = [r for r in FOLK_REGIONS if r not in recent("region", 6)] or FOLK_REGIONS
    origins = [k for k in ORIGINS if k not in recent("origin", 6)] or list(ORIGINS)
    stype = random.choice(types)
    if stype in NATURE_BODIES:
        bodies = [b for b in NATURE_BODIES[stype] if b in bodies] or list(NATURE_BODIES[stype])
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
  "sentences": [{{"text": "one spoken sentence", "beat": "one scene word from the list below", "setting": "one place word from the list below", "delivery": "one voice mood from the list below", "speaker": "narrator, person, creature or mimic", "voice": "only for person and mimic lines: man, woman, boy, girl, old_man or old_woman"}}]
}}

Rules for "sentences" (7 to 9 items, 85 to 105 words in total, plain spoken English, no emojis):
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
            data["sentences"] = sents[:9]
            for k, x in enumerate(data["sentences"]):
                sp_ = str(x.get("speaker", "narrator")).lower()
                x["speaker"] = sp_ if sp_ in ("person", "creature", "mimic") else "narrator"
                vv = str(x.get("voice", "")).lower().replace(" ", "_")
                x["voice"] = vv if vv in ("man", "woman", "boy", "girl", "old_man", "old_woman") else ""
                prev2 = [data["sentences"][j]["speaker"] for j in (k - 1, k - 2) if j >= 0]
                if k in (0, len(data["sentences"]) - 1) or (len(prev2) == 2 and all(p != "narrator" for p in prev2)):
                    x["speaker"] = "narrator"
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
            return data
        except Exception as e:
            last = e
            log(f"Gemini folklore attempt {attempt + 1} failed: {e}")
    raise RuntimeError(f"Gemini failed 3 times: {last}")


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
  "sentences": [{{"text": "one spoken sentence", "beat": "one scene word from the list below", "delivery": "one voice mood from the list below", "speaker": "narrator, person, creature or mimic", "voice": "only for person and mimic lines: man, woman, boy, girl, old_man or old_woman"}}]
}}

Rules for "sentences" (7 to 9 items, 85 to 105 words in total, plain spoken English, first person "I", no emojis):
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
  stories), "dig" (the creature digs in the earth, dirt flying, only if the story really has digging), "fly" (the creature unfolds
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
            data["sentences"] = sents[:9]
            for k, x in enumerate(data["sentences"]):
                sp_ = str(x.get("speaker", "narrator")).lower()
                x["speaker"] = sp_ if sp_ in ("person", "creature", "mimic") else "narrator"
                vv = str(x.get("voice", "")).lower().replace(" ", "_")
                x["voice"] = vv if vv in ("man", "woman", "boy", "girl", "old_man", "old_woman") else ""
                prev2 = [data["sentences"][j]["speaker"] for j in (k - 1, k - 2) if j >= 0]
                if k in (0, len(data["sentences"]) - 1) or (len(prev2) == 2 and all(p != "narrator" for p in prev2)):
                    x["speaker"] = "narrator"
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
            return data
        except Exception as e:
            last = e
            log(f"Gemini modern story attempt {attempt + 1} failed: {e}")
    raise RuntimeError(f"Gemini failed 3 times: {last}")


ATTACK_WORDS = ("attack", "lunge", "pounc", "tore", "torn", "bit ", "bite", "blood", "killed", "dragged", "devour",
                "scream", "slash", "mauled", "ripped", "snatched", "never seen again", "claws")


SCENE_FUNCS = ("dig", "fly", "bedroom", "stairs", "basement", "hallway", "cctv", "jump", "hook", "footprints", "home", "village", "window", "door", "transform", "chase", "hide", "mob",
               "omen", "pov", "mpov", "curtain", "shoulder", "loom")
CAMERA_SCENES = ("pov", "mpov", "curtain", "shoulder", "loom")
MODERN_SCENES = ("bedroom", "stairs", "basement", "hallway", "cctv", "door", "window", "curtain", "jump",
                 "woods", "neighbour", "lamp", "road", "mirror", "dig", "fly",
                 "ocean", "lake", "lava", "tornado", "tsunami", "giant")
MODERN_WORDS = (("tsunami", ("tsunami", "wall of water", "great wave")), ("tornado", ("tornado", "twister", "funnel")),
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
                       "sea": ("ocean", "tsunami", "giant"), "lake": ("lake", "woods", "giant"), "lava": ("lava", "fly", "giant"),
                       "storm": ("tornado", "road", "giant"), "tsunami": ("tsunami", "ocean", "giant"), "titan": ("giant", "woods", "road")}
SCENES = ("walk", "dig", "fly", "footprints", "home", "village", "window", "door", "transform", "chase", "hide", "mob",
          "stalk", "attack", "aftermath", "omen", "pov", "mpov", "curtain", "shoulder", "loom")
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
        elif beats[i] == "giant":
            h = hits.get(i, [d * 0.25, d * 0.6])
            ev.append({"file": sounds["rumble"], "at": at + max(0, h[0] - 0.3), "vol": 0.9})
            ev.append({"file": sounds["roar"], "at": at + h[1], "vol": 1.0})
            ev.append({"file": sounds["boom"], "at": at + h[1], "vol": 0.7})
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
        if run > 3 and beats[i] not in ("home", "village", "mob"):
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
        if b not in MODERN_SCENES:
            low = s["text"].lower()
            b = next((name for name, words in MODERN_WORDS if any(w in low for w in words)), MODERN_FILL[i % len(MODERN_FILL)])
        beats.append(b)
    place = MODERN_PLACE_SCENES.get(stype or "")
    fill = tuple(place) if place else MODERN_FILL
    beats[-1] = "jump"
    special = ("woods", "neighbour", "lamp", "road", "mirror", "dig", "fly", "ocean", "lake", "lava", "tornado", "tsunami", "giant")
    final = "giant" if stype in NATURE_TYPES else "jump"
    for i, b in enumerate(beats):  # the special places only appear in stories built around them
        if b in special and (not place or b not in place):
            beats[i] = fill[i % len(fill)]
    if place and place[0] not in beats:
        beats[0] = place[0]
    for i in range(n - 1):  # the final face is only for the end
        if beats[i] in ("jump", final):
            beats[i] = fill[i % len(fill)] if place and fill[i % len(fill)] != final else ("hallway" if not place else fill[0])
    beats[-1] = final
    for _ in range(3):  # neighbours with the same scene are merged into one longer scene later, but never 3 in a row
        for i in range(2, n - 1):
            if (beats[i] == beats[i - 1] == beats[i - 2]) or beats.count(beats[i]) > 4:
                opts = [c for c in fill if c not in (beats[i - 1], beats[i + 1], final)] or [c for c in fill if c != final]
                if opts:
                    beats[i] = next((c for c in opts if beats.count(c) < 2), opts[0])
    if beats[-2] == final or beats[-1] != final:
        beats[-1] = final
    if n >= 6 and "cctv" in beats and beats.count("cctv") > 1:
        j = [k for k, b in enumerate(beats) if b == "cctv"][-1]
        beats[j] = "hallway" if beats[j - 1] != "hallway" else "door"
    return beats


def assign_beats(sentences, modern=False, stype=None):
    if modern:
        return assign_modern_beats(sentences, stype)
    n = len(sentences)
    beats = []
    for i, s in enumerate(sentences):
        b = s.get("beat")
        low = s["text"].lower()
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
        beats[n - 3] = "attack"
    if beats[-1] in ("attack",) or beats[-1] not in ("stalk", "aftermath", "walk"):
        beats[-1] = "aftermath" if beats[-2] == "attack" else "stalk"
    for i in range(1, n):
        if beats[i] == "attack" and beats[i - 1] == "attack":
            beats[i] = "aftermath"  # one attack at a time
    for i in range(n - 1):
        if beats[i] == "attack" and beats[i + 1] != "aftermath":
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
        cands = [j for j in range(3, n - 1) if beats[j] in ("walk", "stalk", "footprints", "hide", "home")]
        cands.sort(key=lambda j: (beats[j + 1] != "attack", -j))
        for j in cands:
            beats[j] = pool.pop(0) if pool else "pov"
            if sum(b in CAMERA_SCENES for b in beats) >= 2:
                break
    for i in range(1, n - 1):  # never the same scene twice in a row
        if beats[i] == beats[i - 1] and beats[i] not in ("attack",):
            beats[i] = "stalk" if beats[i] != "stalk" else "walk"
    return beats


FOLK_STYLE = ("a warm, soft-spoken person telling a story out loud, naturally and conversationally, the way you would tell "
              "a close friend something that really happened to you and still gives you chills. Relaxed, believable, gentle "
              "tones with an uneven human rhythm, small pauses and quiet breaths, a little smile in the voice at the start that "
              "slowly turns quiet and serious when it gets frightening. Soft and close to the microphone, never robotic, never "
              "announcer-like, not reading from a page, no theatrical movie-trailer voice")
# a light touch only: a bit of warmth in the low-mids, a softer top end, gentle levelling. No pitch change, no echo.
SCARY_CHAIN = ("aresample=44100,highpass=f=60,equalizer=f=190:t=q:w=1:g=2.5,equalizer=f=3800:t=q:w=1.2:g=-1.5,"
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
EDGE_MALE = ["en-US-AndrewNeural", "en-US-GuyNeural", "en-GB-RyanNeural"]
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


def make_mixed_voice(sentences, narrator):
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
    pool_m = [v for v in MALE_VOICES if v not in (narr_voice, creature_voice)] or ["Orus"]
    pool_f = FEMALE_VOICES
    vmap = {"man": (random.choice(pool_m), 1.0), "woman": (random.choice(pool_f), 1.0),
            "boy": (random.choice(pool_m), 1.22), "girl": (random.choice(pool_f), 1.12),
            "old_man": (random.choice(pool_m), 0.92), "old_woman": (random.choice(pool_f), 0.94)}
    narr_gender = narrator.get("gender") if narrator.get("gender") in ("male", "female") else "male"
    default_person = "woman" if narr_gender == "male" else "man"
    log(f"Voices: narrator {narr_voice}, creature {creature_voice}, people {vmap} ({len(runs)} parts)")
    parts, starts, lens, at = [], [0.0] * n, [0.0] * n, 0.0
    for r, (a, b) in enumerate(runs):
        spk = sentences[a].get("speaker", "narrator")
        text = " ".join(sentences[x]["text"] for x in range(a, b + 1))
        raw, dst = WORK / f"mv_raw{r}.wav", f"mv_{r}.wav"
        if spk == "narrator":
            ok = gemini_tts(text, raw, style=FOLK_STYLE, voice=narr_voice)
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
            if spk == "mimic":  # the creature copying a loved one: right voice, wrong in a way you feel more than hear
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


def folk_sound_events(durs, beats, impacts, sounds, hits=None, quiet=None, origin=None):
    hits = hits or {}
    quiet = quiet if quiet is not None else []
    ev = [{"file": sounds["eerie"], "at": 0.05, "vol": 0.9},
          {"file": sounds["scream"], "at": 0.1, "vol": 0.6, "max": 1.2}]
    at = 0.0
    for i, d in enumerate(durs):
        if i:
            ev.append({"file": sounds["whoosh"], "at": at - 0.1, "vol": 0.3})
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
            cut = at + d * 0.72
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


def build_folk_main(plan):
    import toon
    story = plan["story"]
    sentences = story["sentences"]
    animal = story["animal"]
    region = story["region"]
    episode = plan.get("episode", 1)
    narration = " ".join(s["text"] for s in sentences)
    place_name, place_desc, ambient_query, place_light, place_grade = FOLK_PLACE
    LOOK["grade"] = GRADES.get(place_grade, "null")
    WORK.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    log(f"Building folklore: {story['legend_name']} ({animal}) from {region}")
    log(f"Title: {story['title']}")

    modern = story.get("format") == "modern"
    beats = assign_beats(sentences, modern, story.get("story_type"))
    log(("Modern indoor story. " if modern else "") + "Beats: " + ", ".join(beats))
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
                audio_name, m_starts, m_lens = make_mixed_voice(sentences, story.get("narrator") or {})
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
        elif not gemini_tts(narration, WORK / "voice.wav"):
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
        rate = 0.95 if speech / 0.95 + 0.8 + 1.9 < 57 else 1.0
        lead, tail = 0.8, 1.9
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
    if durs is None:
        chars = [max(len(s["text"]), 1) for s in sentences]
        speech_len = total - lead - (tail if lead else 0.0)
        durs = [speech_len * c / sum(chars) for c in chars]
        durs[0] += lead
        durs[-1] += (tail if lead else 0.3)
    settings = ["forest" if b == "dig" else "field" if b == "fly" else "village" for b in beats] if modern else assign_settings(sentences, beats)
    if modern:  # sentences that share a scene play as one longer scene, so its action is not restarted
        mb, md, ms = [], [], []
        for b_, d_, s_ in zip(beats, durs, settings):
            if mb and mb[-1] == b_:
                md[-1] += d_
            else:
                mb.append(b_)
                md.append(d_)
                ms.append(s_)
        beats, durs, settings, n = mb, md, ms, len(mb)
        log("Scenes: " + ", ".join(beats))
    log("Settings: " + ", ".join(settings))
    people = story.get("people", "alone")
    toon.CAST["kind"] = people if people in ("child", "elder", "pair", "dog") else None
    toon.CAST["role"] = story.get("hero") if story.get("hero") in toon.ROLES else None
    origin = story.get("origin") if story.get("origin") in ORIGINS else None
    log(f"Origin: {origin}. Hero: {toon.CAST['role']}, with: {toon.CAST['kind']}")
    WEATHER = {"stalk": dict(fog=0.55), "attack": dict(fog=0.6, redmoon=True), "aftermath": dict(fog=0.7, redmoon=True),
               "chase": dict(storm=True), "transform": dict(redmoon=True), "window": dict(storm=True)}
    PUSH = {"walk": 0.10, "stalk": 0.16, "home": 0.10, "village": 0.08, "hide": 0.10, "footprints": 0.0,
            "aftermath": 0.06, "mob": 0.08}
    SHAKE = {"chase": 7.0}
    FIRST_SIGN_AT = next((j for j, b in enumerate(beats) if b == "omen"), n)
    scenes, impacts, blooded, hits = [], {}, False, {}
    cap_track, scene_at = [], 0.0
    for i, dur in enumerate(durs):
        wx = dict(WEATHER.get(beats[i]) or {})
        if origin and i >= FIRST_SIGN_AT - 0 and "sky" not in wx:  # once the sign has appeared, the sky keeps it
            wx["sky"] = origin
        if beats[i] == "omen":
            wx["sky"] = origin
        w = toon.World(toon.SETTINGS[settings[i]], 5 + (i % 3), wx)
        w.push = PUSH.get(beats[i], 0.0)
        w.shake = SHAKE.get(beats[i], 0.0)
        out = str(WORK / f"toon_s{i}.mp4")
        w.clock = story.get("time") or "2:48"
        cur = dict(creature, bloody=True) if blooded else creature
        g0 = 1300 - 740 * i / n
        g1 = 1300 - 740 * (i + 1) / n
        intro = (cur, min(1.4, dur * 0.5)) if i == 0 else None
        last = i == n - 1
        if modern:
            hits[i] = getattr(toon, beats[i] + "_scene")(out, dur, w, cur, cwd=WORK).get("hits", [])
        elif beats[i] == "attack":
            impacts[i] = toon.attack_scene(out, dur, w, creature, cwd=WORK)["impact_at"]
            blooded = True
        elif beats[i] == "aftermath" and not last:
            toon.ground_scene(out, dur, w, cur, cwd=WORK)
        elif beats[i] == "aftermath":
            toon.aftermath_scene(out, dur, w, cur, cwd=WORK, outro=min(1.6, dur * 0.6))
        elif beats[i] == "omen" and not (i == 0 or last):
            hits[i] = toon.omen_scene(out, dur, w, cur, cwd=WORK).get("hits", [])
        elif beats[i] in SCENE_FUNCS and not (i == 0 or last):
            hits[i] = getattr(toon, beats[i] + "_scene")(out, dur, w, cur, cwd=WORK).get("hits", [])
        else:
            outro = (cur, min(1.6, dur * 0.6)) if last else None
            toon.folk_scene(out, dur, w, cur, g0, g1, intro=intro, outro=outro,
                            roar_end=(beats[min(i + 1, n - 1)] == "attack"), cwd=WORK)
        cap_track += [(scene_at + tt, bb) for tt, bb in toon.LAST_TRACK]
        scene_at += dur
        scenes.append({"kind": "hold" if last else "scene", "dur": dur,
                       "clip": f"toon_s{i}.mp4", "plate": None})
    has_caps = write_captions(words, total, WORK / "captions.ass",
                              card=f"{region.upper()}  \u00b7  DARK FOLKLORE #{episode}", track=cap_track)
    sounds = prepare_sounds(animal, animal, ambient_query)
    quiet = []
    events = folk_sound_events(durs, beats, impacts, sounds, hits, quiet, origin)
    build_audio(audio_name, total, events, sounds["ambient"], "mix.wav", quiet)
    out_path = OUT / "short.mp4"
    build_video(scenes, {"h": {"plate": None, "sprite": None, "clip": None}}, "mix.wav",
                "captions.ass" if has_caps else None, out_path)
    log(f"Video ready: {out_path} ({total:.1f}s of speech)")

    title = story["title"][:80].rstrip() + " #Shorts"
    description = (
        (f"{story['legend_name']}: an original horror story. Fiction, not a true story.\n"
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
        story = ask_gemini(a, b, place_name)
        plan = {"series": "hybrid", "key": key, "a": a, "b": b, "place": list(place), "story": story,
                "episode": episode,
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
    LOOK["grade"] = GRADES.get(place_grade, "null")
    log(f"Building: {a} + {b} in {place_name}")
    log(f"Title: {story['title']}")
    clips = find_clips()

    # --- voice
    words = None
    audio_name = "voice.wav"
    if not gemini_tts(narration, WORK / "voice.wav"):
        log("Falling back to the basic free voice.")
        audio_name = "voice.mp3"
        words = asyncio.run(edge_voice(narration, str(WORK / "voice.mp3")))
    total = audio_seconds(WORK / audio_name)
    if not words:
        words = even_words(narration, total)
    has_caps = write_captions(words, total, WORK / "captions.ass",
                              card=f"{place_name.upper()}  \u00b7  IMPOSSIBLE ANIMALS #{plan.get('episode', 1)}")

    # --- the animation: every creature is drawn and animated by the bot (flat, stylized look)
    import toon
    sa, sb = toon.animal_spec(a), toon.animal_spec(b)
    sh = toon.hybrid_spec(a, b)
    scenes = plan_scenes(sentences, total)
    first = scenes[0]
    t = open_times(first["hook"], first["morph"])
    world = toon.World(place_name, 1)
    log(f"Animating: {a} ({sa['kind']}), {b} ({sb['kind']}), hybrid ({sh['kind']}, traits: {', '.join(sorted(sh['traits']))})")
    toon.walk_scene(str(WORK / "toon_a.mp4"), t["la"], world, sa, 1, (1.0, 1.1), cwd=WORK, intro=(sh, 1.3))
    toon.walk_scene(str(WORK / "toon_b.mp4"), t["lb"], world, sb, -1, (1.0, 1.1), cwd=WORK)
    toon.meet_scene(str(WORK / "toon_m.mp4"), t["lm"], world, sa, sb, cwd=WORK)
    toon.walk_scene(str(WORK / "toon_h.mp4"), t["lh"], world, sh, 1, (0.95, 1.15), hybrid_roar_end=True, cwd=WORK)
    assets = {"a": {"plate": None, "sprite": None, "clip": "toon_a.mp4"},
              "b": {"plate": None, "sprite": None, "clip": "toon_b.mp4"},
              "mix": "toon_m.mp4",
              "h": {"plate": None, "sprite": None, "clip": "toon_h.mp4"}}
    if clips:  # a clip you made yourself replaces the drawn reveal
        assets["h"]["clip"] = clips[0]

    middle = [i for i, sc in enumerate(scenes) if sc["kind"] == "scene"]
    hunt_at = middle[len(middle) // 2] if middle else None
    hunt_info = None
    k = 0
    for i, sc in enumerate(scenes):
        if sc["kind"] == "open":
            continue
        w = toon.World(place_name, i + 2)
        out = str(WORK / f"toon_s{i}.mp4")
        if sc["kind"] == "hold":
            toon.walk_scene(out, sc["dur"], w, sh, 1, (1.0, 1.15), cwd=WORK, outro=(sh, min(1.5, sc["dur"] * 0.6)))
        elif i == hunt_at:
            hunt_info = toon.hunt_scene(out, sc["dur"], w, sh, story.get("prey", ""), cwd=WORK)
            sc["hunt"] = True
        else:
            shot_type = k % 3
            if shot_type == 0:
                toon.walk_scene(out, sc["dur"], w, sh, 1, (1.0, 1.2), cwd=WORK)
            elif shot_type == 1:
                toon.walk_scene(out, sc["dur"], w, sh, -1, (0.62, 0.7), hybrid_roar_end=True, cwd=WORK)
            else:
                toon.face_scene(out, sc["dur"], w, sh, cwd=WORK)
            k += 1
        sc["clip"] = f"toon_s{i}.mp4"
        sc["plate"] = None
    if len(clips) >= 2 and middle:
        scenes[hunt_at]["clip"] = clips[1]
    if len(clips) >= 3:
        scenes[-1]["clip"] = clips[2]

    # --- sound
    sounds = prepare_sounds(a, b, ambient_query)
    events = sound_events(scenes, sounds, hunt_info)
    build_audio(audio_name, total, events, sounds["ambient"], "mix.wav")

    out_path = OUT / "short.mp4"
    build_video(scenes, assets, "mix.wav", "captions.ass" if has_caps else None, out_path)
    log(f"Video ready: {out_path} ({total:.1f}s of speech, AI video clips used: {len(clips)}/3, "
        f"real animal calls: {sounds['real_calls']}/2)")

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
        {"title": title, "description": description, "narration": narration,
         "video_clips_used": len(clips), "real_animal_calls": sounds["real_calls"]}, indent=2))

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
        build_main()
