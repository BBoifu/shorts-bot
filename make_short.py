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

STYLE = ("Cinematic photorealistic wildlife photograph, dramatic lighting, rich detail, "
         "sharp focus, vertical composition, no text, no watermark, no people.")
STYLE_SPRITE = "Photorealistic wildlife photograph, sharp focus, rich detail, no text, no watermark."

ANIMALS = [
    "camel", "octopus", "penguin", "axolotl", "bat", "cheetah", "crocodile",
    "polar bear", "jellyfish", "owl", "chameleon", "elephant", "shark",
    "scorpion", "frog", "eagle", "wolf", "snake", "turtle", "spider",
    "whale", "gecko", "dragonfly", "lion", "gorilla", "fox", "seal",
    "ant", "crab", "hummingbird", "lizard", "bear",
]

# (place, what the background should show, sound-effect search for the ambience)
PLACES = [
    ("the Sahara desert", "endless sand dunes under a blazing sun", "desert wind"),
    ("Antarctica", "a frozen white ice plain under a stormy sky", "arctic wind blizzard"),
    ("the deep ocean", "the dark deep sea floor with faint blue light", "underwater ambience"),
    ("a land of active volcanoes", "black rock and glowing red lava rivers", "volcano rumble"),
    ("Mars", "a rusty red desert of rocks and dust under a pink sky", "alien wind"),
    ("the clouds of Venus", "thick orange storm clouds with lightning", "thunder storm"),
    ("an icy moon like Europa", "cracked blue ice with a huge planet in the sky", "ice cracking wind"),
    ("a rainforest after a flood", "a flooded jungle with heavy rain and mist", "rainforest rain"),
    ("a mountain peak above the clouds", "a snowy rocky summit above a sea of clouds", "mountain wind"),
    ("a planet with two suns", "an alien plain under two glowing suns", "sci-fi ambience drone"),
    ("a swamp full of toxic mud", "a foggy swamp with bubbling green mud", "swamp night ambience"),
    ("the edge of a black hole", "a glowing black hole warping stars in deep space", "deep space drone"),
]

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

FPS = 30
FADE = 0.6          # seconds of cross-dissolve in the opening morph
MAX_SPRITE_W = 780  # biggest the creature is drawn, in pixels
MAX_SPRITE_H = 900
GROUND_Y = 1320     # where the creature's feet are
STEP_RATE = 2.2     # steps per second
MOTIONS = ["in", "left", "out", "right"]


# ---------------------------------------------------------------- helpers
def log(msg):
    print(msg, flush=True)


def run(cmd, cwd=None):
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        log(result.stderr[-2000:])
        raise RuntimeError("Command failed: " + " ".join(str(c) for c in cmd[:4]))
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
  "hybrid_look": "one detailed sentence describing how the creature LOOKS (body shape, skin or fur, colors), naming specific features taken from the {a} and from the {b}",
  "sentences": [
    {{"text": "one spoken sentence", "image": "the SETTING shown while this sentence is spoken"}}
  ]
}}

Rules for "sentences" (7 to 9 items, 85 to 105 words in total, plain spoken English, no emojis):
- Sentence 1: a gripping hook question that makes clear the creature is imaginary.
- Sentence 2: names the creature and says what it inherits from the {a} and from the {b}.
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
def cloudflare_image(prompt, dest):
    """One AI picture from Cloudflare Workers AI (FLUX). Returns True on success."""
    if not (CF_ACCOUNT and CF_TOKEN):
        return False
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/ai/run/{CF_MODEL}"
    headers = {"Authorization": f"Bearer {CF_TOKEN}"}
    for attempt in range(3):
        for with_size in (True, False):
            body = {"prompt": prompt[:1800], "steps": 6}
            if with_size:
                body.update({"width": 768, "height": 1344})
            try:
                r = requests.post(url, headers=headers, json=body, timeout=180)
            except Exception as e:
                log(f"Cloudflare image request failed: {e}")
                time.sleep(5)
                break
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
                break
            log(f"Cloudflare image -> {r.status_code}: {r.text[:300]}")
            if r.status_code in (401, 403):
                return False
            if r.status_code == 400 and with_size:
                continue
            if r.status_code == 429:
                time.sleep(15)
            break
        time.sleep(3)
    return False


def fetch_ai_image(name, prompt):
    """Download an AI picture into WORK with the right file ending. Returns the file name or None."""
    tmp = WORK / f"{name}.download"
    if not cloudflare_image(prompt, str(tmp)):
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


def compose_vertical(raw, out_png):
    """Fit any picture onto a 1080x1920 canvas with a blurred copy behind it."""
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
def key_sprite(raw, color_hex, despill, out_png):
    """Remove the plain backdrop. Returns how much of the picture is creature (percent)."""
    chain = f"[0:v]format=rgba,colorkey={color_hex}:0.34:0.12"
    if despill:
        chain += f",despill=type={despill}"
    chain += (",split[c][d];[d]alphaextract,erosion,erosion,dilation,dilation[m];"
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


def make_sprite(name, desc, key):
    """A cut-out creature. Returns {'png','w','h','face'} or None if it did not work out."""
    color_hex, color_words, despill = KEYS[key]
    prompt = (f"{STYLE_SPRITE} {desc}. Full-body side view, walking pose, facing right, whole body "
              f"in frame, isolated on a solid flat {color_words} background, even lighting, "
              "no ground, no shadow, no other objects.")
    for attempt in range(2):
        raw = fetch_ai_image(f"{name}{attempt}", prompt)
        if not raw:
            continue
        try:
            cover = key_sprite(raw, color_hex, despill, f"{name}_k.png")
            if not 4 <= cover <= 70:
                log(f"Sprite '{name}' attempt {attempt + 1}: cut-out covers {cover:.0f}% of the picture, rejected")
                continue
            bb = sprite_bbox(f"{name}_k.png")
            if not bb or bb["w"] < 120 or bb["h"] < 120:
                log(f"Sprite '{name}' attempt {attempt + 1}: could not find the creature outline")
                continue
            run(["ffmpeg", "-y", "-i", f"{name}_k.png", "-vf",
                 f"crop={bb['w']}:{bb['h']}:{bb['x1']}:{bb['y1']}", "-frames:v", "1", f"{name}_c.png"],
                cwd=WORK)
            face = facing_direction(raw)
            log(f"Sprite '{name}': AI cut-out {bb['w']}x{bb['h']}, faces {'right' if face == 1 else 'left'}")
            return {"png": f"{name}_c.png", "w": bb["w"], "h": bb["h"], "face": face}
        except RuntimeError:
            log(f"Sprite '{name}' attempt {attempt + 1}: cut-out failed")
    return None


# ---------------------------------------------------------------- backup footage (Pixabay)
def pixabay_clip(query, dest):
    if not PIXABAY_API_KEY:
        return False
    try:
        r = requests.get("https://pixabay.com/api/videos/",
                         params={"key": PIXABAY_API_KEY, "q": query[:90], "per_page": 20,
                                 "safesearch": "true"}, timeout=30)
        r.raise_for_status()
        hits = r.json().get("hits", [])
        random.shuffle(hits)
        for h in hits:
            for size in ("medium", "large", "small"):
                url = h.get("videos", {}).get(size, {}).get("url")
                if url:
                    with requests.get(url, stream=True, timeout=120) as resp:
                        resp.raise_for_status()
                        with open(dest, "wb") as f:
                            for chunk in resp.iter_content(1 << 20):
                                f.write(chunk)
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


def get_animal(animal, name, key):
    """Everything needed to show one parent animal: a background plate and a walking sprite."""
    sprite = make_sprite(f"spr_{name}", f"A {animal}", key)
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
        "Style: Default,DejaVu Sans,86,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,"
        "1,0,0,0,100,100,0,0,1,7,2,2,60,60,520,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    group = 3
    for i in range(0, len(words), group):
        chunk = words[i:i + group]
        start = chunk[0][0]
        nxt = words[i + group][0] if i + group < len(words) else total
        end = max(chunk[-1][1], min(nxt, chunk[-1][1] + 0.4))
        text = " ".join(w[2] for w in chunk).upper().replace("{", "").replace("}", "")
        lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{text}")
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return True


# ---------------------------------------------------------------- video: shots
def ensure_shadow():
    if (WORK / "shadow.png").exists():
        return
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=600x90,format=rgba,"
         "geq=r=0:g=0:b=0:a='255*0.55*max(0,1-hypot((X-W/2)/(W/2),(Y-H/2)/(H/2)))'",
         "-frames:v", "1", "shadow.png"], cwd=WORK)


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


def sprite_geometry(spr):
    scale = min(MAX_SPRITE_W / spr["w"], MAX_SPRITE_H / spr["h"])
    tw, th = int(spr["w"] * scale) // 2 * 2, int(spr["h"] * scale) // 2 * 2
    ang = 0.045
    rw = int(tw * math.cos(ang) + th * math.sin(ang)) // 2 * 2 + 4
    rh = int(th * math.cos(ang) + tw * math.sin(ang)) // 2 * 2 + 4
    return tw, th, rw, rh, ang


def shot(plate, sprites, dur, out, bg_mode="in"):
    """A background with walking creatures on top.
    sprites: list of {'spec': sprite, 'move': +1/-1, 'alpha': 0-1, 'dx': pixels offset}."""
    bg = f"bg_{abs(hash(out)) % 10**8}.mp4"
    still_clip(plate, dur, bg, bg_mode)
    if not sprites:
        (WORK / bg).replace(WORK / out)
        return
    ensure_shadow()
    cmd = ["ffmpeg", "-y", "-i", bg]
    for sp in sprites:
        cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", sp["spec"]["png"]]
    cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", "shadow.png"]
    shadow_idx = len(sprites) + 1
    parts, last = [], "[0:v]"
    for i, sp in enumerate(sprites):
        spec = sp["spec"]
        tw, th, rw, rh, ang = sprite_geometry(spec)
        # walk a few steps, but never so far that the creature leaves the screen
        dist = max(0.0, min(420.0, 70 * dur, 1080 - rw - 80 - 2 * abs(sp.get("dx", 0))))
        flip = "hflip," if spec["face"] != sp["move"] else ""
        alpha = f",colorchannelmixer=aa={sp['alpha']}" if sp.get("alpha", 1) < 1 else ""
        parts.append(f"[{i + 1}:v]format=rgba,{flip}scale={tw}:{th},"
                     f"rotate=a='{ang}*sin(PI*{STEP_RATE}*t)':c=none:ow={rw}:oh={rh}{alpha}[s{i}]")
        parts.append(f"[{shadow_idx}:v]format=rgba,scale={int(tw * 0.9)}:60"
                     f"{alpha}[sh{i}]")
        cx = 540 + sp.get("dx", 0)
        xexpr = f"{cx - rw / 2:.1f}+{sp['move']}*{dist:.1f}*(t/{dur:.3f}-0.5)"
        yexpr = f"{GROUND_Y - rh}-26*abs(sin(PI*{STEP_RATE}*t))"
        sh_x = f"{cx - tw * 0.45:.1f}+{sp['move']}*{dist:.1f}*(t/{dur:.3f}-0.5)"
        parts.append(f"{last}[sh{i}]overlay=x='{sh_x}':y='{GROUND_Y - 38}':eval=frame:format=auto[b{i}]")
        parts.append(f"[b{i}][s{i}]overlay=x='{xexpr}':y='{yexpr}':eval=frame:format=auto[o{i}]")
        last = f"[o{i}]"
    parts.append(f"{last}format=yuv420p[v]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[v]", "-t", f"{dur:.3f}", "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", out]
    run(cmd, cwd=WORK)


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
    shot(a["plate"], [{"spec": a["sprite"], "move": 1}] if a["sprite"] else [], t["la"], "o_a.mp4", "right")
    shot(b["plate"], [{"spec": b["sprite"], "move": -1}] if b["sprite"] else [], t["lb"], "o_b.mp4", "left")
    if a["sprite"] and b["sprite"]:
        shot(h["plate"], [{"spec": a["sprite"], "move": 1, "alpha": 0.6, "dx": -130},
                          {"spec": b["sprite"], "move": -1, "alpha": 0.6, "dx": 130}],
             t["lm"], "o_m.mp4", "in")
    else:
        run(["ffmpeg", "-y", "-i", a["plate"], "-i", b["plate"], "-filter_complex",
             "[0:v][1:v]blend=all_mode=average", "-frames:v", "1", "mix.png"], cwd=WORK)
        still_clip("mix.png", t["lm"], "o_m.mp4", "in")
    shot(h["plate"], [{"spec": h["sprite"], "move": 1}] if h["sprite"] else [], t["lh"], "o_h.mp4", "in")
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
            shot(h["plate"], [{"spec": h["sprite"], "move": 1}] if h["sprite"] else [], sc["dur"], seg, "in")
        else:
            move = 1 if k % 2 == 0 else -1
            shot(sc["plate"], [{"spec": h["sprite"], "move": move}] if h["sprite"] else [],
                 sc["dur"], seg, MOTIONS[k % len(MOTIONS)])
            k += 1
        segs.append(seg)
    (WORK / "list.txt").write_text("".join(f"file '{s}'\n" for s in segs))
    T = reveal
    shake = (f"between(t,{T:.2f},{T + 0.9:.2f})")
    xe = f"27+{shake}*24*sin(t*65)*(1-(t-{T:.2f})/0.9)"
    ye = f"48+{shake}*30*cos(t*57)*(1-(t-{T:.2f})/0.9)"
    flash = f"if(between(t,{T:.2f},{T + 0.35:.2f}),0.6*(1-(t-{T:.2f})/0.35),0)"
    vf = (f"scale=1134:2016,crop=1080:1920:x='{xe}':y='{ye}',"
          f"eq=brightness='{flash}':saturation=1.12:contrast=1.06:eval=frame,"
          "vignette=PI/5,noise=alls=5:allf=t")
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
    key, a, b, (place_name, place_desc, ambient_query) = pick_idea(history)
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
    assets = {"a": get_animal(a, "a", pick_key([a])), "b": get_animal(b, "b", pick_key([b]))}
    h_sprite = make_sprite("spr_h", f"An imaginary hybrid creature: {look}", keycolor)
    plate_ok = make_plate("plate_h", place_desc)
    if h_sprite:
        assets["h"] = {"plate": "plate_h.png", "sprite": h_sprite}
    else:
        log("Hybrid cut-out failed. Trying a full-frame hybrid picture.")
        prompt = (f"{STYLE} An imaginary hybrid creature: {look} It stands in {place_name}, "
                  "shown clearly and heroically.")
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


if __name__ == "__main__":
    main()
