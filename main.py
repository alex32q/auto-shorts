"""US-audience facts Shorts auto-maker: script -> voice -> video -> YouTube upload (private)."""
import os, sys, json, random, subprocess, textwrap, asyncio, pathlib
import requests

W, H = 720, 1280
WORK = pathlib.Path("work")
WORK.mkdir(exist_ok=True)
USED = pathlib.Path("used_topics.txt")

GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()
PEXELS_KEY = os.getenv("PEXELS_API_KEY", "").strip()
PIXABAY_KEY = os.getenv("PIXABAY_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "").strip() or "gemini-flash-latest"
VOICE = os.getenv("VOICE", "").strip() or "en-US-AriaNeural"
PRIVACY = os.getenv("PRIVACY", "").strip() or "private"


def die(msg):
    print("\nERROR:", msg)
    sys.exit(1)


def check_secrets():
    missing = [n for n in ["GEMINI_API_KEY", "YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"]
               if not os.getenv(n, "").strip()]
    if missing:
        die(f"Ye GitHub secrets missing/khali hain: {', '.join(missing)}. "
            "Settings > Secrets and variables > Actions mein naam bilkul same rakho.")
    if not PIXABAY_KEY and not PEXELS_KEY:
        print("WARNING: Pixabay/Pexels key nahi hai, plain background use hoga.")


def find_font():
    for p in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
              "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
              "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"]:
        if os.path.exists(p):
            return p
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}", "sans:bold"], capture_output=True, text=True)
        if out.stdout.strip() and os.path.exists(out.stdout.strip()):
            return out.stdout.strip()
    except Exception:
        pass
    return None


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("COMMAND FAILED:", " ".join(cmd[:8]), "...")
        print(r.stderr[-1800:])
        die("ffmpeg/ffprobe step fail hua (upar ka log dekho).")
    return r


# ---------- 1. Script ----------
def make_script():
    used = USED.read_text().splitlines()[-60:] if USED.exists() else []
    prompt = f"""You are a YouTube Shorts scriptwriter for a US audience. Pick one surprising, TRUE, verifiable fact/knowledge topic (science or history).
Do not repeat these topics: {used}

Rules:
- Language: casual, conversational American English. Use US units (miles, Fahrenheit, dollars) when natural.
- First sentence must be a strong hook (a surprising question or claim).
- Total narration 110-140 words, 6 to 8 scenes.
- Only include facts you are highly confident are correct. No doubtful claims, no made-up statistics.
- Each scene's 'keyword' is 2-3 English words for a stock video search (e.g. "ocean waves").
- Title: max 70 characters, curiosity-driven, no clickbait lies. Description: 2-3 lines plus 3-5 hashtags. Tags: English.

Return ONLY JSON in this format:
{{"topic": "...", "title": "...", "description": "...", "tags": ["..."],
 "scenes": [{{"narration": "...", "keyword": "..."}}]}}"""
    models = list(dict.fromkeys([GEMINI_MODEL, "gemini-flash-latest", "gemini-3.1-flash-lite",
                                 "gemini-3-flash-preview", "gemini-2.5-flash"]))
    last = ""
    for m in models:
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent",
                headers={"x-goog-api-key": GEMINI_KEY},
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"responseMimeType": "application/json", "temperature": 1.0}},
                timeout=180,
            )
        except Exception as e:
            last = f"{m}: network error {e}"
            print(last)
            continue
        if r.status_code != 200:
            last = f"{m}: HTTP {r.status_code} {r.text[:300]}"
            print("Gemini model fail ->", last)
            continue
        try:
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            text = text.replace("```json", "").replace("```", "").strip()
            data = json.loads(text)
            if isinstance(data, list):
                data = data[0]
            assert data["scenes"] and data["title"]
            print("Gemini model used:", m)
            return data
        except Exception as e:
            last = f"{m}: response parse fail {e}"
            print(last)
    die("Gemini se script nahi mili. Aakhri error: " + last +
        "  (Agar 'API key not valid' ya 403 hai to GEMINI_API_KEY secret dobara check karo.)")


# ---------- 2. Voice ----------
def make_voice(text, path):
    try:
        import edge_tts

        async def go():
            await edge_tts.Communicate(text, VOICE).save(str(path))

        asyncio.run(go())
        if path.stat().st_size < 1000:
            raise RuntimeError("audio khali aaya")
    except Exception as e:
        print("edge-tts fail, gTTS backup use ho raha hai:", e)
        try:
            from gtts import gTTS
            gTTS(text, lang="en", tld="us").save(str(path))
        except Exception as e2:
            die(f"Voice dono tareeke se fail: {e2}")


def duration(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
    return float(out.stdout.strip())


# ---------- 3. Visuals ----------
def get_clip_pixabay(keyword, idx):
    try:
        r = requests.get("https://pixabay.com/api/videos/",
                         params={"key": PIXABAY_KEY, "q": keyword, "per_page": 20, "safesearch": "true"},
                         timeout=60)
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


def get_clip_pexels(keyword, idx):
    try:
        r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization": PEXELS_KEY},
                         params={"query": keyword, "orientation": "portrait", "per_page": 10}, timeout=60)
        r.raise_for_status()
        videos = r.json().get("videos", [])
        if not videos:
            return None
        v = random.choice(videos)
        files = [f for f in v["video_files"] if f.get("width") and 500 <= f["width"] <= 1100] or v["video_files"]
        f = sorted(files, key=lambda x: x.get("width", 0))[0]
        path = WORK / f"clip{idx}.mp4"
        path.write_bytes(requests.get(f["link"], timeout=120).content)
        return path
    except Exception as e:
        print("Pexels fail:", e)
        return None


def get_clip(keyword, idx):
    clip = get_clip_pixabay(keyword, idx) if PIXABAY_KEY else None
    if not clip and PEXELS_KEY:
        clip = get_clip_pexels(keyword, idx)
    return clip


# ---------- 4. Scene render ----------
FONT = find_font()


def render_scene(idx, narration, clip, audio):
    cap = WORK / f"cap{idx}.txt"
    cap.write_text(textwrap.fill(narration, 26), encoding="utf-8")
    font_opt = f"fontfile={FONT}:" if FONT else ""
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps=30,"
          f"drawtext={font_opt}textfile={cap}:fontsize=42:fontcolor=white:"
          "box=1:boxcolor=black@0.55:boxborderw=18:line_spacing=8:"
          "x=(w-text_w)/2:y=h*0.62,format=yuv420p")
    vin = ["-stream_loop", "-1", "-i", str(clip)] if clip else ["-f", "lavfi", "-i", f"color=c=0x1a1a2e:s={W}x{H}:r=30"]
    out = WORK / f"scene{idx}.mp4"
    run(["ffmpeg", "-y", *vin, "-i", str(audio), "-vf", vf, "-map", "0:v", "-map", "1:a",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
         "-c:a", "aac", "-ar", "44100", "-ac", "2", "-shortest", str(out)])
    return out


# ---------- 5. Upload ----------
def upload(video, data):
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        from googleapiclient.errors import HttpError
    except Exception as e:
        die(f"Google libraries import fail: {e}")
    creds = Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"].strip(),
                        token_uri="https://oauth2.googleapis.com/token",
                        client_id=os.environ["YT_CLIENT_ID"].strip(),
                        client_secret=os.environ["YT_CLIENT_SECRET"].strip(),
                        scopes=["https://www.googleapis.com/auth/youtube.upload"])
    body = {
        "snippet": {"title": data["title"][:100],
                    "description": data.get("description", "") + "\n\n#Shorts",
                    "tags": data.get("tags", [])[:15], "categoryId": "27",
                    "defaultLanguage": "en", "defaultAudioLanguage": "en"},
        "status": {"privacyStatus": PRIVACY, "selfDeclaredMadeForKids": False},
    }
    try:
        yt = build("youtube", "v3", credentials=creds)
        req = yt.videos().insert(part="snippet,status", body=body,
                                 media_body=MediaFileUpload(str(video), chunksize=-1, resumable=True))
        resp = None
        while resp is None:
            _, resp = req.next_chunk()
        print("UPLOADED! Video ID:", resp["id"])
    except HttpError as e:
        die(f"YouTube upload fail: HTTP {e.resp.status} {e.content[:600]}")
    except Exception as e:
        msg = str(e)
        hint = ""
        if "invalid_grant" in msg or "unauthorized_client" in msg or "invalid_client" in msg:
            hint = " -> YT_REFRESH_TOKEN / CLIENT_ID / CLIENT_SECRET galat ya expire hain. Token dobara lo."
        die(f"YouTube auth/upload fail: {msg}{hint}")


def main():
    check_secrets()
    data = make_script()
    print("Topic:", data.get("topic"), "|", data["title"])
    scenes = []
    for i, s in enumerate(data["scenes"]):
        audio = WORK / f"voice{i}.mp3"
        make_voice(s["narration"], audio)
        clip = get_clip(s.get("keyword", "nature"), i)
        scenes.append(render_scene(i, s["narration"], clip, audio))
        print(f"Scene {i + 1}/{len(data['scenes'])} done")
    lst = WORK / "list.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in scenes))
    final = WORK / "final.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(final)])
    print("Video length:", round(duration(final), 1), "sec")
    upload(final, data)
    with USED.open("a") as f:
        f.write(str(data.get("topic", data["title"])).replace("\n", " ") + "\n")


if __name__ == "__main__":
    main()
