"""
Makes one YouTube Short: a short story about an imaginary animal hybrid
surviving a harsh place, told over real stock footage from Pixabay.

Steps: pick idea -> Gemini writes story -> Pixabay clips -> free voice
-> captions -> ffmpeg builds the video -> (optional) upload to YouTube.
"""
import asyncio
import json
import os
import pathlib
import random
import re
import subprocess
import sys

import edge_tts
import requests

ROOT = pathlib.Path(__file__).parent
HISTORY_FILE = ROOT / "history.json"
WORK = ROOT / "work"
OUT = ROOT / "output"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
VOICE = os.environ.get("TTS_VOICE", "en-US-AndrewNeural")
PRIVACY = os.environ.get("PRIVACY", "private")  # private = you review first
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"  # 1 = make video, do not upload

ANIMALS = [
    "camel", "octopus", "penguin", "axolotl", "bat", "cheetah", "crocodile",
    "polar bear", "jellyfish", "owl", "chameleon", "elephant", "shark",
    "scorpion", "frog", "eagle", "wolf", "snake", "turtle", "spider",
    "whale", "gecko", "dragonfly", "lion", "gorilla", "fox", "seal",
    "ant", "crab", "hummingbird", "lizard", "bear",
]

# (place name, footage search words that usually work on Pixabay)
PLACES = [
    ("the Sahara desert", ["desert dunes", "sand storm", "sun heat"]),
    ("Antarctica", ["antarctica ice", "snow storm", "glacier"]),
    ("the deep ocean", ["deep ocean", "underwater dark", "ocean waves"]),
    ("inside an active volcano zone", ["volcano lava", "lava flow", "volcano eruption"]),
    ("Mars", ["red desert", "dust storm", "planet mars"]),
    ("the clouds of Venus", ["storm clouds", "lightning", "orange sky"]),
    ("an icy moon like Europa", ["ice cracks", "frozen lake", "space planet"]),
    ("a rainforest after a flood", ["flooded forest", "heavy rain jungle", "river flood"]),
    ("a mountain peak above the clouds", ["mountain snow", "blizzard", "mountain clouds"]),
    ("a planet with two suns", ["sunset planet", "space nebula", "sun flare"]),
    ("a swamp full of toxic mud", ["swamp fog", "mud bubbles", "dark swamp"]),
    ("the edge of a black hole", ["black hole", "galaxy space", "stars space"]),
]


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


# ---------------------------------------------------------------- writing
def ask_gemini(a, b, place_name):
    prompt = f"""You write narration for 40-second YouTube Shorts about IMAGINARY animal hybrids.

Idea: a hybrid of a {a} and a {b}, living in {place_name}.

Return ONLY valid JSON, no markdown, in exactly this shape:
{{
  "title": "catchy title under 70 characters, no hashtags",
  "creature_name": "a fun invented name for the hybrid",
  "narration": "the full spoken script",
  "scenes": ["search 1", "search 2", "search 3", "search 4", "search 5", "search 6", "search 7"]
}}

Narration rules:
- 85 to 105 words, plain spoken English, no emojis, no stage directions.
- Structure: (1) a gripping hook question in the first sentence that says the creature is imaginary,
  (2) name the creature and what it inherits from each parent,
  (3) two or three clever survival tricks, each solving a specific danger of that place,
  (4) a twist ending with one surprising weakness, then ask viewers one question to answer in the comments.
- Use scientific ideas that are real about the parent animals where possible. Do not state false facts as real.

Scenes rules:
- Exactly 7 items. Each is a 1 to 2 word search for REAL stock video footage, common words only.
- First two must be "{a}" and "{b}". The other five show the place and the action (e.g. "sand storm").
"""
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent"
    )
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
            assert data["title"] and data["narration"]
            scenes = [s for s in data["scenes"] if isinstance(s, str) and s.strip()]
            assert len(scenes) >= 4
            data["scenes"] = scenes
            return data
        except Exception as e:  # retry on any problem
            last_err = e
            log(f"Gemini attempt {attempt + 1} failed: {e}")
    raise RuntimeError(f"Gemini failed 3 times: {last_err}")


# ---------------------------------------------------------------- footage
def pixabay_search(query):
    r = requests.get(
        "https://pixabay.com/api/videos/",
        params={"key": PIXABAY_API_KEY, "q": query[:90], "per_page": 20, "safesearch": "true"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("hits", [])


def get_clip(query, fallback_queries, used_ids, dest):
    for q in [query] + fallback_queries + ["nature landscape"]:
        try:
            hits = pixabay_search(q)
        except Exception as e:
            log(f"Pixabay search '{q}' failed: {e}")
            continue
        random.shuffle(hits)
        for h in hits:
            if h["id"] in used_ids:
                continue
            for size in ("large", "medium", "small"):
                url = h.get("videos", {}).get(size, {}).get("url")
                if url:
                    used_ids.add(h["id"])
                    with requests.get(url, stream=True, timeout=120) as resp:
                        resp.raise_for_status()
                        with open(dest, "wb") as f:
                            for chunk in resp.iter_content(1 << 20):
                                f.write(chunk)
                    log(f"Clip for '{q}': Pixabay video {h['id']}")
                    return True
    return False


# ---------------------------------------------------------------- voice + captions
async def make_voice(text, mp3_path):
    comm = edge_tts.Communicate(text, VOICE, rate="+5%", boundary="WordBoundary")
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


def ass_time(t):
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def write_captions(words, total, path):
    """Big 3-word captions in the lower-middle of the screen."""
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


def even_words(text, total):
    toks = text.split()
    step = total / max(len(toks), 1)
    return [(i * step, (i + 1) * step, t) for i, t in enumerate(toks)]


# ---------------------------------------------------------------- video
def build_video(clip_paths, audio_path, captions_name, out_path, total):
    """All files are inside WORK; commands run with cwd=WORK so names stay short."""
    per = (total + 0.4) / len(clip_paths)
    segs = []
    for i, clip in enumerate(clip_paths):
        seg = f"seg{i}.mp4"
        run([
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", clip, "-t", f"{per:.3f}",
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,setsar=1",
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", seg,
        ], cwd=WORK)
        segs.append(seg)
    (WORK / "list.txt").write_text("".join(f"file '{s}'\n" for s in segs))
    vf = f"subtitles={captions_name}" if captions_name else "null"
    run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt", "-i", audio_path,
        "-vf", vf, "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-shortest", "-t", "59", str(out_path),
    ], cwd=WORK)


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
    for name, val in (("GEMINI_API_KEY", GEMINI_API_KEY), ("PIXABAY_API_KEY", PIXABAY_API_KEY)):
        if not val:
            sys.exit(f"Missing secret: {name}")

    WORK.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    history = load_history()
    key, a, b, (place_name, place_queries) = pick_idea(history)
    log(f"Idea: {a} + {b} in {place_name}")

    story = ask_gemini(a, b, place_name)
    log(f"Title: {story['title']}")

    used = set()
    clips = []
    for i, q in enumerate(story["scenes"][:7]):
        dest = f"clip{i}.mp4"
        if get_clip(q, place_queries, used, str(WORK / dest)):
            clips.append(dest)
    if len(clips) < 3:
        sys.exit("Could not get enough footage from Pixabay.")

    words = asyncio.run(make_voice(story["narration"], str(WORK / "voice.mp3")))
    total = audio_seconds(WORK / "voice.mp3")
    if not words:
        log("No word timings from voice; spreading captions evenly.")
        words = even_words(story["narration"], total)
    has_caps = write_captions(words, total, WORK / "captions.ass")

    out_path = OUT / "short.mp4"
    build_video(clips, "voice.mp3", "captions.ass" if has_caps else None, out_path, total)
    log(f"Video ready: {out_path} ({total:.1f}s of speech)")

    title = story["title"][:80].rstrip() + " #Shorts"
    description = (
        f"{story['creature_name']}: an imaginary animal hybrid ({a} + {b}) in {place_name}.\n"
        "This is a fictional story made for fun. The creature is not real. "
        "Footage is stock video; narration uses a synthetic voice.\n\n"
        "#shorts #animals #whatif #speculativebiology"
    )
    tags = ["shorts", "animals", "what if", "hybrid animals", "speculative biology", a, b]
    (OUT / "meta.json").write_text(json.dumps(
        {"title": title, "description": description, "narration": story["narration"]}, indent=2))

    if DRY_RUN:
        log("DRY RUN: not uploading. Download the video from the run's Artifacts.")
        return
    video_id = upload_to_youtube(out_path, title, description, tags)
    log(f"Uploaded as '{PRIVACY}': https://youtube.com/shorts/{video_id}")
    history.append(key)
    HISTORY_FILE.write_text(json.dumps(history, indent=1))


if __name__ == "__main__":
    main()
