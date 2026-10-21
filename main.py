"""Facts Short auto-maker v2:
Gemini script -> AI images -> ONE voice (word timings) -> zoom clips + word-by-word captions + music -> YouTube upload.
"""
import os, json, random, subprocess, asyncio, pathlib, urllib.parse
import requests
import edge_tts
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

W, H, FPS = 1080, 1920, 30
WORK = pathlib.Path("work")
WORK.mkdir(exist_ok=True)
USED = pathlib.Path("used_topics.txt")
MUSIC_DIR = pathlib.Path("music")  # yahan 3-5 royalty-free .mp3 rakho (YouTube Audio Library)

GEMINI_KEY = os.environ["GEMINI_API_KEY"]
PEXELS_KEY = os.environ.get("PEXELS_API_KEY", "")  # sirf image fail hone par backup
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
VOICE = os.getenv("VOICE", "en-US-GuyNeural")
VOICE_RATE = os.getenv("VOICE_RATE", "+8%")
PRIVACY = os.getenv("PRIVACY", "private")  # private / unlisted / public
NICHE = os.getenv("NICHE", "psychology or space")
STYLE = os.getenv("IMAGE_STYLE", "dark cinematic style, moody lighting, highly detailed, vertical 9:16, no text, no watermark")
MUSIC_VOLUME = float(os.getenv("MUSIC_VOLUME", "0.12"))
TAIL = 0.6  # video ke ant me extra seconds


def run(cmd, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if p.returncode:
        raise RuntimeError("Command failed:\n" + p.stderr[-1500:])


def duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


# ---------- 1. Script ----------
def make_script():
    used = USED.read_text().splitlines()[-60:] if USED.exists() else []
    prompt = f"""You are a YouTube Shorts scriptwriter for a US audience. Pick ONE surprising, TRUE, verifiable fact about {NICHE}.
Do not repeat these topics: {used}

Rules:
- Casual American English, like a friend sharing a secret. Short sentences, simple words.
- Sentence 1 is a hook: under 12 words, creates curiosity. NEVER start with "Hello", "Did you know" or any intro.
- Narration 90-115 words total. Every 10-15 seconds add a twist or a new question. Reveal the answer at the very end.
- The last sentence should make people want to replay the video (tie back to the hook).
- Only include facts you are highly confident are correct. No made-up statistics.
- 10 to 12 scenes. Each scene has 'image_prompt' (a vivid visual description: no text, no logos, no real people) and 'keyword' (2-3 English words for a stock photo search).
- Title: max 60 characters, curiosity-driven, no lies. Description: 2 lines plus 3-5 hashtags. Tags: English.

Return ONLY JSON in this format:
{{"topic": "...", "title": "...", "description": "...", "tags": ["..."],
 "narration": "the full narration as one string",
 "scenes": [{{"image_prompt": "...", "keyword": "..."}}]}}"""
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        headers={"x-goog-api-key": GEMINI_KEY},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 1.0},
        },
        timeout=120,
    )
    r.raise_for_status()
    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


# ---------- 2. Voice (one file + word timings) ----------
async def _tts(text, path):
    try:
        comm = edge_tts.Communicate(text, VOICE, rate=VOICE_RATE, boundary="WordBoundary")
    except TypeError:  # purani edge-tts version
        comm = edge_tts.Communicate(text, VOICE, rate=VOICE_RATE)
    words = []
    with open(path, "wb") as f:
        async for ch in comm.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                start = ch["offset"] / 1e7
                words.append((start, start + ch["duration"] / 1e7, ch["text"]))
    return words


def make_voice(text, path):
    return asyncio.run(_tts(text, str(path)))


def even_words(text, dur):
    ws = text.split()
    step = dur / max(len(ws), 1)
    return [(i * step, (i + 1) * step, w) for i, w in enumerate(ws)]


# ---------- 3. Images ----------
def pexels_photo(keyword, path):
    if not PEXELS_KEY:
        return False
    try:
        r = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_KEY},
            params={"query": keyword, "orientation": "portrait", "per_page": 10},
            timeout=60,
        )
        r.raise_for_status()
        photos = r.json().get("photos", [])
        if not photos:
            return False
        url = random.choice(photos)["src"]["large2x"]
        path.write_bytes(requests.get(url, timeout=120).content)
        return True
    except Exception as e:
        print("Pexels fail:", e)
        return False


def make_image(scene, idx):
    path = WORK / f"img{idx}.jpg"
    prompt = f"{scene['image_prompt']}, {STYLE}"
    url = (
        "https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt)
        + f"?width={W}&height={H}&nologo=true&seed={random.randint(1, 10**6)}"
    )
    for _ in range(3):
        try:
            r = requests.get(url, timeout=180)
            if r.ok and r.headers.get("content-type", "").startswith("image") and len(r.content) > 5000:
                path.write_bytes(r.content)
                return path
        except requests.RequestException as e:
            print("image retry:", e)
    print(f"Scene {idx}: AI image fail, backup use ho raha hai")
    if pexels_photo(scene.get("keyword", "dark abstract"), path):
        return path
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=0x1a1a2e:s={W}x{H}", "-frames:v", "1", str(path)])
    return path


# ---------- 4. Zoom clip ----------
def make_clip(img, dur, idx):
    out = WORK / f"clip{idx}.mp4"
    frames = max(int(dur * FPS), 1)
    z = "min(1+0.0015*on,1.3)" if idx % 2 == 0 else "max(1.3-0.0015*on,1.0)"
    vf = (
        f"scale={W*2}:{H*2}:force_original_aspect_ratio=increase,crop={W*2}:{H*2},"
        f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},"
        "format=yuv420p"
    )
    run(["ffmpeg", "-y", "-i", str(img), "-vf", vf, "-frames:v", str(frames),
         "-c:v", "libx264", "-crf", "20", "-preset", "medium", str(out)])
    return out


# ---------- 5. Captions (word by word) ----------
def ass_time(t):
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def make_ass(words, path):
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {W}\nPlayResY: {H}\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Cap,DejaVu Sans,96,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,"
        "-1,0,0,0,100,100,0,0,1,7,2,5,40,40,0,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    groups = [words[i:i + 2] for i in range(0, len(words), 2)]
    lines = []
    for k, g in enumerate(groups):
        start = g[0][0]
        end = groups[k + 1][0][0] if k + 1 < len(groups) else g[-1][1] + 0.3
        text = " ".join(w[2] for w in g).upper().replace("{", "").replace("}", "")
        lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Cap,,0,0,0,,{text}")
    pathlib.Path(path).write_text(header + "\n".join(lines) + "\n", encoding="utf-8")


# ---------- 6. Final video ----------
def build_final():
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", "video_raw.mp4"], cwd=WORK)
    tracks = list(MUSIC_DIR.glob("*.mp3")) if MUSIC_DIR.exists() else []
    cmd = ["ffmpeg", "-y", "-i", "video_raw.mp4", "-i", "voice.mp3"]
    if tracks:
        cmd += ["-stream_loop", "-1", "-i", str(random.choice(tracks).resolve())]
        fc = (
            "[0:v]subtitles=captions.ass[v];"
            f"[1:a]apad=pad_dur={TAIL}[va];"
            f"[2:a]volume={MUSIC_VOLUME}[m];"
            "[va][m]amix=inputs=2:duration=first:dropout_transition=0[a]"
        )
    else:
        print("music/ folder me mp3 nahi mila, bina music ke ban raha hai")
        fc = f"[0:v]subtitles=captions.ass[v];[1:a]apad=pad_dur={TAIL}[a]"
    cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-shortest", "final.mp4"]
    run(cmd, cwd=WORK)
    return WORK / "final.mp4"


# ---------- 7. Upload ----------
def upload(video, data):
    creds = Credentials(
        None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    yt = build("youtube", "v3", credentials=creds)
    body = {
        "snippet": {
            "title": data["title"][:100],
            "description": data["description"] + "\n\n#Shorts",
            "tags": data.get("tags", [])[:15],
            "categoryId": "27",  # Education
            "defaultLanguage": "en",
            "defaultAudioLanguage": "en",
        },
        "status": {"privacyStatus": PRIVACY, "selfDeclaredMadeForKids": False},
    }
    req = yt.videos().insert(
        part="snippet,status", body=body,
        media_body=MediaFileUpload(str(video), chunksize=-1, resumable=True),
    )
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    print("Uploaded! Video ID:", resp["id"])


def main():
    data = make_script()
    print("Topic:", data["topic"], "|", data["title"])

    words = make_voice(data["narration"], WORK / "voice.mp3")
    vdur = duration(WORK / "voice.mp3")
    if not words:
        words = even_words(data["narration"], vdur)
    total = vdur + TAIL

    scenes = data["scenes"]
    per = total / len(scenes)
    with (WORK / "list.txt").open("w") as lst:
        for i, s in enumerate(scenes):
            img = make_image(s, i)
            clip = make_clip(img, per, i)
            lst.write(f"file '{clip.name}'\n")

    make_ass(words, WORK / "captions.ass")
    final = build_final()
    print("Video length:", round(duration(final), 1), "sec")

    upload(final, data)
    with USED.open("a") as f:
        f.write(data["topic"].replace("\n", " ") + "\n")


if __name__ == "__main__":
    main()
