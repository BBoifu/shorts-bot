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
- Sentence 1: a gripping hook question that makes clear the creature is imaginary.
- Sentence 2: names the creature and says what it inherits from the {a} and from the {b}.
- One middle sentence describes the signature move on its prey.
- Middle sentences: each one a specific danger of {place_name} and the clever trait that solves it.
- Last sentence: a surprising weakness as a twist, then ask viewers one question to answer in the comments.
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


def gemini_tts(text, dest_wav):
    """More natural voice from Gemini. Returns True on success."""
    headers = {"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}
    style = "an excited, curious nature-documentary narrator, warm and dramatic, natural pacing"
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
                "generation_config": {"speech_config": [{"voice": GEMINI_VOICE}]},
            }
            url = f"{API_BASE}/interactions"
        else:
            body = {
                "contents": [{"parts": [{"text": f"Say as {style}: {text}"}]}],
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": GEMINI_VOICE}}},
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
            log(f"Voice: Gemini {model} ({GEMINI_VOICE})")
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
    if kind == "growl":
        fc = (f"{L}='0.6*sin(2*PI*(70+8*sin(2*PI*9*t))*t)+0.3*sin(2*PI*140*t)':d=1.3:s=44100[a];"
              "anoisesrc=d=1.3:c=brown:a=0.3[n];[a][n]amix=inputs=2:normalize=0,tremolo=f=22:d=0.6,"
              "acrusher=bits=6:mode=log:mix=0.3,afade=in:d=0.05,afade=out:st=0.9:d=0.4,alimiter=limit=0.9:level=0")
    elif kind == "screech":
        fc = (f"{L}='0.5*sin(2*PI*(700*t+900*t*t))+0.3*sin(2*PI*(1400*t+1800*t*t))':d=1.6:s=44100[a];"
              "anoisesrc=d=1.6:c=white:a=0.25,highpass=f=2000[n];[a][n]amix=inputs=2:normalize=0,"
              "tremolo=f=35:d=0.7,acrusher=bits=5:mode=log:mix=0.25,aecho=0.8:0.85:70:0.35,"
              "afade=in:d=0.03,afade=out:st=1.1:d=0.5,alimiter=limit=0.9:level=0")
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
        synth("growl", "g_a.wav")
    if not call_b:
        synth("growl", "g_b.wav")
    ca = call_a or "g_a.wav"
    cb = call_b or "g_b.wav"
    ins += ["-i", ca, "-i", cb]
    fc = ("[0:a]aresample=44100,aformat=channel_layouts=stereo[s];"
          "[1:a]aresample=44100,asetrate=33075,aresample=44100,atrim=0:2.2,aformat=channel_layouts=stereo[ca];"
          "[2:a]aresample=44100,asetrate=55125,aresample=44100,atrim=0:1.8,aformat=channel_layouts=stereo[cb];"
          "[s][ca][cb]amix=inputs=3:normalize=0:weights='1 0.8 0.6',acrusher=bits=6:mode=log:mix=0.3,"
          "aecho=0.8:0.8:80:0.3,alimiter=limit=0.95:level=0[out]")
    run(["ffmpeg", "-y"] + ins + ["-filter_complex", fc, "-map", "[out]", dest], cwd=WORK)


def prepare_sounds(a, b, ambient_query):
    """Returns a dict of sound file names inside WORK."""
    s = {}
    for kind in ("whoosh", "boom", "riser", "growl"):
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


def build_audio(voice_name, total, events, ambient, out_wav):
    """Mix the narration with timed sound effects and a quiet ambient bed."""
    cmd = ["ffmpeg", "-y", "-i", voice_name, "-stream_loop", "-1", "-i", ambient]
    fmt = "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo"
    parts = [f"[0:a]aresample=44100,{fmt}[v]",
             f"[1:a]aresample=44100,{fmt},atrim=0:{total + 1:.2f},volume=0.14[amb]"]
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


def write_captions(words, total, path):
    if not words:
        return False
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Liberation Sans Narrow,112,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,"
        "1,0,0,0,100,100,1,0,1,8,3,2,60,60,520,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    group = 2
    for i in range(0, len(words), group):
        chunk = words[i:i + group]
        start = chunk[0][0]
        nxt = words[i + group][0] if i + group < len(words) else total
        end = max(chunk[-1][1], min(nxt, chunk[-1][1] + 0.4))
        toks = [w[2].upper().replace("{", "").replace("}", "") for w in chunk]
        key = max(range(len(toks)), key=lambda j: len(toks[j]))  # the longest word is shown in orange
        toks[key] = "{\\c&H001E9BFF&}" + toks[key] + "{\\c&H00FFFFFF&}"
        text = " ".join(toks)
        lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{text}")
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


def sound_events(scenes, sounds):
    """When each sound effect plays, in seconds from the start of the video."""
    first = scenes[0]
    t = open_times(first["hook"], first["morph"])
    ev = [
        {"file": sounds["call_a"], "at": 0.4, "vol": 0.8, "max": 3.0},
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
        at += sc["dur"]
    # a last growl on the twist ending
    ev.append({"file": sounds["growl"], "at": max(at - scenes[-1]["dur"] + 0.3, 0), "vol": 0.6})
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
        f"{story['creature_name']}: an imaginary animal hybrid ({a} + {b}) in {place_name}.\n"
        "This is a fictional story made for fun and the creature is not real. "
        "The images and animation are AI-generated, the narration uses a synthetic voice, "
        "and the sound effects are synthesized or CC0.\n\n"
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


def plan_main():
    if not GEMINI_API_KEY:
        sys.exit("Missing secret: GEMINI_API_KEY")
    OUT.mkdir(exist_ok=True)
    PLAN_DIR.mkdir(exist_ok=True)
    history = load_history()
    key, a, b, place = pick_idea(history)
    place_name, place_desc = place[0], place[1]
    log(f"Idea: {a} + {b} in {place_name}")
    story = ask_gemini(a, b, place_name)
    plan = {"key": key, "a": a, "b": b, "place": list(place), "story": story,
            "clips": [{"title": t, "prompt": p} for t, p in
                      clip_prompts(story["hybrid_look"], place_name, place_desc,
                         story.get("prey", "a small animal"), story.get("signature_move", "it pounces"))]}
    (PLAN_DIR / "plan.json").write_text(json.dumps(plan, indent=1))
    history.append(key)
    HISTORY_FILE.write_text(json.dumps(history, indent=1))

    lines = [f"# {story['title']}", "",
             f"**Hybrid:** {story['creature_name']} ({a} + {b}) in {place_name}", "",
             "## Your job: make 3 short videos (5 seconds each), then upload them", "",
             "Use PixVerse or Kling (text-to-video). Pick **9:16 vertical**, **5 seconds**, **720p or better**.",
             "Paste each prompt below (2 to 5 seconds is plenty). Save the results as `clip1.mp4`, `clip2.mp4`, `clip3.mp4`.", ""]
    for i, c in enumerate(plan["clips"], 1):
        lines += [f"### {c['title']}", "", "```", c["prompt"], "```", ""]
    lines += ["## The story that will be spoken", "", " ".join(x["text"] for x in story["sentences"]), ""]
    md = "\n".join(lines)
    (OUT / "PROMPTS.md").write_text(md)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(md)
    log("Plan saved. Open the run page and read the prompts in the Summary.")


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
    has_caps = write_captions(words, total, WORK / "captions.ass")

    # --- the animation: every creature is drawn and animated by the bot (flat, stylized look)
    import toon
    sa, sb = toon.animal_spec(a), toon.animal_spec(b)
    sh = toon.hybrid_spec(a, b)
    scenes = plan_scenes(sentences, total)
    first = scenes[0]
    t = open_times(first["hook"], first["morph"])
    world = toon.World(place_name, 1)
    log(f"Animating: {a} ({sa['kind']}), {b} ({sb['kind']}), hybrid ({sh['kind']}, traits: {', '.join(sorted(sh['traits']))})")
    toon.walk_scene(str(WORK / "toon_a.mp4"), t["la"], world, sa, 1, (1.0, 1.1), cwd=WORK)
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
    k = 0
    for i, sc in enumerate(scenes):
        if sc["kind"] == "open":
            continue
        w = toon.World(place_name, i + 2)
        out = str(WORK / f"toon_s{i}.mp4")
        if sc["kind"] == "hold":
            toon.walk_scene(out, sc["dur"], w, sh, 1, (1.0, 1.25), hybrid_roar_end=True, cwd=WORK)
        elif i == hunt_at:
            toon.hunt_scene(out, sc["dur"], w, sh, cwd=WORK)
        else:
            face = 1 if k % 2 == 0 else -1
            toon.walk_scene(out, sc["dur"], w, sh, face, (1.0, 1.2 if k % 2 == 0 else 0.9),
                            hybrid_roar_end=(k % 2 == 1), cwd=WORK)
            k += 1
        sc["clip"] = f"toon_s{i}.mp4"
        sc["plate"] = None
    if len(clips) >= 2 and middle:
        scenes[hunt_at]["clip"] = clips[1]
    if len(clips) >= 3:
        scenes[-1]["clip"] = clips[2]

    # --- sound
    sounds = prepare_sounds(a, b, ambient_query)
    events = sound_events(scenes, sounds)
    build_audio(audio_name, total, events, sounds["ambient"], "mix.wav")

    out_path = OUT / "short.mp4"
    build_video(scenes, assets, "mix.wav", "captions.ass" if has_caps else None, out_path)
    log(f"Video ready: {out_path} ({total:.1f}s of speech, AI video clips used: {len(clips)}/3, "
        f"real animal calls: {sounds['real_calls']}/2)")

    title = story["title"][:80].rstrip() + " #Shorts"
    description = (
        f"{story['creature_name']}: an imaginary animal hybrid ({a} + {b}) in {place_name}.\n"
        "This is a fictional story made for fun and the creature is not real. "
        "The images and animation are AI-generated, the narration uses a synthetic voice, "
        "and the sound effects are synthesized or CC0.\n\n"
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
