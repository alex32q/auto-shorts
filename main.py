"""Facts Short (Hinglish) auto-maker: script -> voice -> video -> YouTube upload (private by default)."""
import os, json, random, subprocess, textwrap, asyncio, pathlib
import requests
import edge_tts
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

W, H = 720, 1280
WORK = pathlib.Path("work")
WORK.mkdir(exist_ok=True)
USED = pathlib.Path("used_topics.txt")

GEMINI_KEY = os.environ["GEMINI_API_KEY"]
PEXELS_KEY = os.getenv("PEXELS_API_KEY", "")
PIXABAY_KEY = os.getenv("PIXABAY_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
VOICE = os.getenv("VOICE", "en-US-AriaNeural")  # other US voices: en-US-GuyNeural, en-US-AndrewNeural, en-US-JennyNeural
PRIVACY = os.getenv("PRIVACY", "private")  # private / unlisted / public
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


# ---------- 1. Script ----------
def make_script():
    used = USED.read_text().splitlines()[-60:] if USED.exists() else []
    prompt = f"""You are a YouTube Shorts scriptwriter for a US audience. Pick one surprising, TRUE, verifiable fact/knowledge topic.
Do not repeat these topics: {used}

Rules:
- Language: casual, conversational American English. Use US units (miles, Fahrenheit, dollars) and US-relatable examples when natural.
- First sentence must be a strong hook (a surprising question or claim).
- Total narration 110-140 words, 6 to 8 scenes.
- Only include facts you are highly confident are correct. No doubtful claims, no made-up statistics.
- Each scene's 'keyword' is 2-3 English words for a stock video search (e.g. "ocean waves").
- Title: max 70 characters, curiosity-driven, no clickbait lies. Description: 2-3 lines plus 3-5 hashtags. Tags: English, US-friendly.

Return ONLY JSON in this format:
{{"topic": "...", "title": "... (max 70 chars, catchy)", "description": "2-3 lines + hashtags", "tags": ["..."],
 "scenes": [{{"narration": "...", "keyword": "..."}}]}}"""
    models = [GEMINI_MODEL, "gemini-flash-latest", "gemini-3.1-flash-lite", "gemini-3-flash-preview"]
    last = None
    for m in dict.fromkeys(models):  # duplicates hata ke, order same
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent",
            headers={"x-goog-api-key": GEMINI_KEY},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 1.0},
            },
            timeout=180,
        )
        if r.status_code in (400, 403, 404, 429, 503):
            print(f"Model {m} failed: {r.status_code} {r.text[:300]}")
            last = r
            continue
        r.raise_for_status()
        print("Using model:", m)
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    last.raise_for_status()


# ---------- 2. Voice ----------
async def _tts(text, path):
    await edge_tts.Communicate(text, VOICE).save(str(path))


def make_voice(text, path):
    asyncio.run(_tts(text, path))


def duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


# ---------- 3. Visuals ----------
def get_clip_pixabay(keyword, idx):
    try:
        r = requests.get(
            "https://pixabay.com/api/videos/",
            params={"key": PIXABAY_KEY, "q": keyword, "per_page": 20, "safesearch": "true"},
            timeout=60,
        )
        r.raise_for_status()
        hits = r.json().get("hits", [])
        if not hits:
            return None
        v = random.choice(hits)["videos"]
        url = (v.get("medium") or v.get("small") or v.get("tiny") or {}).get("url")
        if not url:
            return None
        path = WORK / f"clip{idx}.mp4"
        path.write_bytes(requests.get(url, timeout=120).content)
        return path
    except Exception as e:
        print("Pixabay fail:", e)
        return None


def get_clip(keyword, idx):
    if PIXABAY_KEY:
        clip = get_clip_pixabay(keyword, idx)
        if clip:
            return clip
    if not PEXELS_KEY:
        return None
    try:
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": PEXELS_KEY},
            params={"query": keyword, "orientation": "portrait", "per_page": 10},
            timeout=60,
        )
        r.raise_for_status()
        videos = r.json().get("videos", [])
        if not videos:
            return None
        v = random.choice(videos)
        files = [f for f in v["video_files"] if f.get("width") and 500 <= f["width"] <= 1100]
        files = files or v["video_files"]
        f = sorted(files, key=lambda x: x.get("width", 0))[0]
        path = WORK / f"clip{idx}.mp4"
        path.write_bytes(requests.get(f["link"], timeout=120).content)
        return path
    except Exception as e:
        print("Pexels fail:", e)
        return None


# ---------- 4. Scene render ----------
def render_scene(idx, narration, clip, audio):
    cap = WORK / f"cap{idx}.txt"
    cap.write_text(textwrap.fill(narration, 26), encoding="utf-8")
    vf = (
        f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps=30,"
        f"drawtext=fontfile={FONT}:textfile={cap}:fontsize=42:fontcolor=white:"
        "box=1:boxcolor=black@0.55:boxborderw=18:line_spacing=8:"
        "x=(w-text_w)/2:y=h*0.62,format=yuv420p"
    )
    if clip:
        vin = ["-stream_loop", "-1", "-i", str(clip)]
    else:
        vin = ["-f", "lavfi", "-i", f"color=c=0x1a1a2e:s={W}x{H}:r=30"]
    out = WORK / f"scene{idx}.mp4"
    run(
        ["ffmpeg", "-y", *vin, "-i", str(audio), "-vf", vf, "-map", "0:v", "-map", "1:a",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
         "-c:a", "aac", "-ar", "44100", "-ac", "2", "-shortest", str(out)]
    )
    return out


# ---------- 5. Upload ----------
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
    scenes = []
    for i, s in enumerate(data["scenes"]):
        audio = WORK / f"voice{i}.mp3"
        make_voice(s["narration"], audio)
        clip = get_clip(s["keyword"], i)
        scenes.append(render_scene(i, s["narration"], clip, audio))

    lst = WORK / "list.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in scenes))
    final = WORK / "final.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(final)])
    print("Video length:", round(duration(final), 1), "sec")

    upload(final, data)
    with USED.open("a") as f:
        f.write(data["topic"].replace("\n", " ") + "\n")


if __name__ == "__main__":
    main()
