"""
Makes one YouTube Short: an AI-illustrated story about an imaginary animal
hybrid surviving a harsh place.

Video plan (every picture is an AI image with camera motion):
  1. Hook: animal A
  2. Morph: animal A -> animal B -> blend of both -> the hybrid
  3. Survival scenes: the hybrid facing each danger, one picture per sentence
  4. Twist ending: the hybrid again, slow zoom

Free services used: Gemini (story + voice), Cloudflare Workers AI (pictures),
ffmpeg (video), Pixabay (optional backup when a picture fails).
"""
import asyncio
import base64
import json
import os
import pathlib
import random
import re
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
CF_ACCOUNT = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
CF_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
CF_MODEL = os.environ.get("CF_IMAGE_MODEL", "@cf/black-forest-labs/flux-1-schnell")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
GEMINI_VOICE = os.environ.get("GEMINI_VOICE", "Charon")
EDGE_VOICE = os.environ.get("TTS_VOICE", "en-US-AndrewNeural")
PRIVACY = os.environ.get("PRIVACY", "private")  # private = you review first
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"  # 1 = make video, do not upload

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
# Tried in order; the first one that works for your key is used.
TTS_MODELS = ["gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts",
              "gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts"]

STYLE = ("Cinematic photorealistic wildlife photograph, dramatic lighting, rich detail, "
         "sharp focus, vertical composition, no text, no watermark, no people.")

ANIMALS = [
    "camel", "octopus", "penguin", "axolotl", "bat", "cheetah", "crocodile",
    "polar bear", "jellyfish", "owl", "chameleon", "elephant", "shark",
    "scorpion", "frog", "eagle", "wolf", "snake", "turtle", "spider",
    "whale", "gecko", "dragonfly", "lion", "gorilla", "fox", "seal",
    "ant", "crab", "hummingbird", "lizard", "bear",
]

PLACES = [
    ("the Sahara desert", ["desert dunes", "sand storm"]),
    ("Antarctica", ["antarctica ice", "snow storm"]),
    ("the deep ocean", ["deep ocean", "underwater dark"]),
    ("a land of active volcanoes", ["volcano lava", "lava flow"]),
    ("Mars", ["red desert", "dust storm"]),
    ("the clouds of Venus", ["storm clouds", "lightning"]),
    ("an icy moon like Europa", ["ice cracks", "frozen lake"]),
    ("a rainforest after a flood", ["flooded forest", "heavy rain jungle"]),
    ("a mountain peak above the clouds", ["mountain snow", "blizzard"]),
    ("a planet with two suns", ["sunset planet", "space nebula"]),
    ("a swamp full of toxic mud", ["swamp fog", "mud bubbles"]),
    ("the edge of a black hole", ["black hole", "galaxy space"]),
]

MOTIONS = ["in", "left", "out", "right"]


# ---------------------------------------------------------------- helpers
def log(msg):
    print(msg, flush=True)


def run(cmd, cwd=None):
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        log(result.stderr[-2000:])
        raise RuntimeError("Command failed: " + " ".join(cmd[:4]))
    return result.stdout


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
    """Find the longest long string in a JSON reply (the picture, when we do not know the field)."""
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
    {{"text": "one spoken sentence", "image": "what the viewer SEES while this sentence is spoken"}}
  ]
}}

Rules for "sentences" (7 to 9 items, 85 to 105 words in total, plain spoken English, no emojis):
- Sentence 1: a gripping hook question that makes clear the creature is imaginary.
- Sentence 2: names the creature and says what it inherits from the {a} and from the {b}.
- Middle sentences: each one a specific danger of {place_name} and the clever trait that solves it.
- Last sentence: a surprising weakness as a twist, then ask viewers one question to answer in the comments.
- Use real science about the parent animals where possible. Do not present false facts as real.
- "image" is a vivid 15 to 25 word description of ONE still picture showing the creature in the
  setting doing what the sentence says (for the middle sentences). Describe the scene only: the
  creature will be described separately, so call it "the creature". No text or words inside pictures.
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
                s["image"] = str(s.get("image") or "the creature in its habitat").strip()
            data["sentences"] = sents[:9]
            return data
        except Exception as e:
            last_err = e
            log(f"Gemini attempt {attempt + 1} failed: {e}")
    raise RuntimeError(f"Gemini failed 3 times: {last_err}")


# ---------------------------------------------------------------- pictures
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
                return False  # wrong token or account, retrying will not help
            if r.status_code == 400 and with_size:
                continue  # model may not accept a size, retry without it
            if r.status_code == 429:
                time.sleep(15)
            break
        time.sleep(3)
    return False


def compose_vertical(raw, out_png):
    """Fit any picture onto a 1080x1920 canvas with a blurred copy behind it."""
    fc = ("[0:v]split[a][b];"
          "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=40:5[bg];"
          "[b]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
          "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1")
    run(["ffmpeg", "-y", "-i", raw, "-filter_complex", fc, "-frames:v", "1", out_png], cwd=WORK)


def make_picture(name, prompt):
    """AI picture saved as work/<name>.png on a vertical canvas. Returns True on success."""
    tmp = WORK / f"{name}.download"
    if cloudflare_image(prompt, str(tmp)):
        try:
            head = tmp.read_bytes()[:16]
            if head[:2] == b"\xff\xd8":
                ext = "jpg"
            elif head[:4] == b"\x89PNG":
                ext = "png"
            elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                ext = "webp"
            else:
                ext = "jpg"
            raw = f"{name}_raw.{ext}"
            tmp.replace(WORK / raw)  # ffmpeg needs the right file ending to read it
            compose_vertical(raw, f"{name}.png")
            log(f"Picture '{name}': AI")
            return True
        except RuntimeError:
            log(f"Picture '{name}': AI image could not be read")
    return False


# ---------------------------------------------------------------- backup footage (Pixabay)
def pixabay_search(query):
    r = requests.get(
        "https://pixabay.com/api/videos/",
        params={"key": PIXABAY_API_KEY, "q": query[:90], "per_page": 20, "safesearch": "true"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("hits", [])


def pixabay_clip(query, dest):
    if not PIXABAY_API_KEY:
        return False
    try:
        hits = pixabay_search(query)
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


def animal_picture(animal, name):
    """AI portrait of an animal; falls back to a frame of real Pixabay footage."""
    prompt = f"{STYLE} A close-up portrait of a {animal} in its natural environment."
    if make_picture(name, prompt):
        return
    log(f"Backup for {animal}: real footage frame")
    if pixabay_clip(animal, str(WORK / f"{name}.mp4")) and frame_from_clip(f"{name}.mp4", f"{name}_f.png"):
        compose_vertical(f"{name}_f.png", f"{name}.png")
        return
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


# ---------------------------------------------------------------- video pieces
FPS = 30
FADE = 0.6  # seconds of cross-dissolve in the morph


def blend_still(a_png, b_png, out_png):
    run(["ffmpeg", "-y", "-i", a_png, "-i", b_png, "-filter_complex",
         "[0:v][1:v]blend=all_mode=average", "-frames:v", "1", out_png], cwd=WORK)


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


def morph_clip(a_png, b_png, h_png, dur, out):
    """Animal A -> animal B -> blend of both -> the hybrid."""
    la, lb, lm = 1.3, 1.3, 0.9
    lh = dur - (la + lb + lm) + 3 * FADE
    if lh < 1.0:
        raise RuntimeError("Morph scene too short")
    blend_still(a_png, b_png, "mix.png")
    still_clip(a_png, la, "m_a.mp4", "in")
    still_clip(b_png, lb, "m_b.mp4", "out")
    still_clip("mix.png", lm, "m_m.mp4", "in")
    still_clip(h_png, lh, "m_h.mp4", "in")
    o1 = la - FADE
    o2 = la + lb - 2 * FADE
    o3 = la + lb + lm - 3 * FADE
    fc = (f"[0][1]xfade=transition=fade:duration={FADE}:offset={o1:.3f}[v1];"
          f"[v1][2]xfade=transition=dissolve:duration={FADE}:offset={o2:.3f}[v2];"
          f"[v2][3]xfade=transition=dissolve:duration={FADE}:offset={o3:.3f}[v]")
    run(["ffmpeg", "-y", "-i", "m_a.mp4", "-i", "m_b.mp4", "-i", "m_m.mp4", "-i", "m_h.mp4",
         "-filter_complex", fc, "-map", "[v]", "-t", f"{dur:.3f}", "-an",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", out], cwd=WORK)


def plan_scenes(sentences, total):
    """Split the audio time across sentences, then decide what to show for each."""
    chars = [max(len(s["text"]), 1) for s in sentences]
    tot = sum(chars)
    ds = [total * c / tot for c in chars]
    n = len(sentences)
    scenes = [{"kind": "hook", "dur": ds[0]}]
    d, i = ds[1], 2
    while d < 4.8 and i < n - 1:
        d += ds[i]
        i += 1
    scenes.append({"kind": "morph", "dur": d})
    while i < n - 1:
        scenes.append({"kind": "scene", "dur": ds[i], "image": sentences[i]["image"]})
        i += 1
    scenes.append({"kind": "hold", "dur": ds[n - 1] + 0.3})
    return scenes


def build_video(scenes, audio_name, captions_name, out_path):
    segs = []
    k = 0
    for i, sc in enumerate(scenes):
        seg = f"seg{i}.mp4"
        if sc["kind"] == "morph":
            morph_clip("a.png", "b.png", "hybrid.png", sc["dur"], seg)
        elif sc["kind"] == "hook":
            still_clip("a.png", sc["dur"], seg, "out")  # ends at zoom 1.0, matching the morph start
        elif sc["kind"] == "hold":
            still_clip("hybrid.png", sc["dur"], seg, "out")
        else:
            still_clip(sc["pic"], sc["dur"], seg, MOTIONS[k % len(MOTIONS)])
            k += 1
        segs.append(seg)
    (WORK / "list.txt").write_text("".join(f"file '{s}'\n" for s in segs))
    vf = f"subtitles={captions_name}" if captions_name else "null"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt", "-i", audio_name,
         "-vf", vf, "-map", "0:v", "-map", "1:a",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "160k", "-shortest", "-t", "59", str(out_path)], cwd=WORK)


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
    key, a, b, (place_name, _place_queries) = pick_idea(history)
    log(f"Idea: {a} + {b} in {place_name}")

    story = ask_gemini(a, b, place_name)
    sentences = story["sentences"]
    narration = " ".join(s["text"] for s in sentences)
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

    # --- pictures
    animal_picture(a, "a")
    animal_picture(b, "b")
    look = story["hybrid_look"]
    hybrid_prompt = (f"{STYLE} An imaginary creature that is a hybrid: {look} "
                     f"It stands in {place_name}, shown clearly and heroically.")
    ai_hybrid = make_picture("hybrid", hybrid_prompt)
    if not ai_hybrid:
        log("Hybrid picture failed. Using a blend of the two animals instead.")
        blend_still("a.png", "b.png", "hybrid.png")

    scenes = plan_scenes(sentences, total)
    ai_count = 0
    for i, sc in enumerate(scenes):
        if sc["kind"] != "scene":
            continue
        prompt = f"{STYLE} The creature: {look} Scene in {place_name}: {sc['image']}"
        if make_picture(f"s{i}", prompt):
            sc["pic"] = f"s{i}.png"
            ai_count += 1
        else:
            log(f"Scene {i}: AI picture failed, reusing the hybrid picture.")
            sc["pic"] = "hybrid.png"

    out_path = OUT / "short.mp4"
    build_video(scenes, audio_name, "captions.ass" if has_caps else None, out_path)
    log(f"Video ready: {out_path} ({total:.1f}s of speech, "
        f"AI scene pictures: {ai_count}, AI hybrid: {ai_hybrid})")

    title = story["title"][:80].rstrip() + " #Shorts"
    description = (
        f"{story['creature_name']}: an imaginary animal hybrid ({a} + {b}) in {place_name}.\n"
        "This is a fictional story made for fun and the creature is not real. "
        "The images are AI-generated and the narration uses a synthetic voice.\n\n"
        "#shorts #animals #whatif #speculativebiology"
    )
    tags = ["shorts", "animals", "what if", "hybrid animals", "speculative biology", a, b]
    (OUT / "meta.json").write_text(json.dumps(
        {"title": title, "description": description, "narration": narration,
         "ai_scene_pictures": ai_count, "ai_hybrid": ai_hybrid}, indent=2))

    if DRY_RUN:
        log("DRY RUN: not uploading. Download the video from the run's Artifacts.")
        return
    video_id = upload_to_youtube(out_path, title, description, tags)
    log(f"Uploaded as '{PRIVACY}': https://youtube.com/shorts/{video_id}")
    history.append(key)
    HISTORY_FILE.write_text(json.dumps(history, indent=1))


if __name__ == "__main__":
    main()
