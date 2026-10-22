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
- HOOK (most important): scene 1 narration is max 12 words and must stop the scroll in 2 seconds. Start with a shocking claim, a number, or a "you've been lied to" style contradiction. NEVER start with greetings, "Did you know", "Today", or "In this video". Tease a payoff that is revealed only near the end (open loop).
- Pacing: total narration 95-115 words (about 35-45 seconds), split into 9 to 11 SHORT scenes of 8-14 words, ONE idea per scene. No filler, every line must add a new fact or twist.
- Ending: last scene gives the payoff and ends in a way that links back to the hook so it loops naturally. Do not say "like and subscribe".
- Only include facts you are highly confident are correct. No doubtful claims, no made-up statistics.
- Each scene has 'image_prompt': a vivid English description (15-25 words) of ONE concrete, photorealistic, cinematic vertical image that literally shows what that scene says (subject, setting, lighting). No text, no logos, no real named people, no celebrity faces.
- Each scene also has 'keywords': 3 stock-video search phrases (1-3 words, concrete things, specific to generic) as backup.
- Title: max 70 characters, curiosity-driven, no clickbait lies. Description: 2-3 lines plus 3-5 hashtags. Tags: English.

Return ONLY JSON in this format:
{{"topic": "...", "title": "...", "description": "...", "tags": ["..."],
 "scenes": [{{"narration": "...", "image_prompt": "...", "keywords": ["...", "...", "..."]}}]}}"""
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
            await edge_tts.Communicate(text, VOICE, rate="+8%").save(str(path))

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
def get_image(prompt, idx):
    """Free AI image (Pollinations). Fail ho to None -> stock clip backup."""
    if not prompt:
        return None
    style = ", photorealistic, cinematic lighting, sharp focus, vertical 9:16, no text, no watermark"
    for attempt in range(2):
        try:
            url = ("https://image.pollinations.ai/prompt/" + requests.utils.quote(prompt + style) +
                   f"?width=720&height=1280&model=flux&nologo=true&seed={random.randint(1, 10**6)}")
            r = requests.get(url, timeout=120)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image") and len(r.content) > 20000:
                path = WORK / f"img{idx}.jpg"
                path.write_bytes(r.content)
                return path
            print(f"  image try {attempt + 1} fail: HTTP {r.status_code}")
        except Exception as e:
            print(f"  image try {attempt + 1} error: {e}")
    return None


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


def get_clip(keywords, idx):
    if isinstance(keywords, str):
        keywords = [keywords]
    for kw in keywords:
        clip = get_clip_pixabay(kw, idx) if PIXABAY_KEY else None
        if not clip and PEXELS_KEY:
            clip = get_clip_pexels(kw, idx)
        if clip:
            print(f"  scene {idx + 1} clip keyword: {kw}")
            return clip
    return None


# ---------- 4. Scene render ----------
FONT = find_font()


def caption_filters(idx, narration, dur):
    """2-word UPPERCASE captions, bold outline, awaaz ke time par badalte hain."""
    words = narration.upper().replace("\n", " ").split()
    chunks = [" ".join(words[i:i + 2]) for i in range(0, len(words), 2)]
    total = sum(len(c) for c in chunks) or 1
    font_opt = f"fontfile={FONT}:" if FONT else ""
    color = "yellow" if idx == 0 else "white"  # hook scene yellow
    parts, t = [], 0.0
    for j, c in enumerate(chunks):
        d = dur * len(c) / total
        f = WORK / f"cap{idx}_{j}.txt"
        f.write_text(textwrap.fill(c, 13), encoding="utf-8")
        parts.append(
            f"drawtext={font_opt}textfile={f}:fontsize=64:fontcolor={color}:"
            "borderw=7:bordercolor=black:shadowx=3:shadowy=3:shadowcolor=black@0.6:line_spacing=6:"
            f"x=(w-text_w)/2:y=h*0.58:enable='between(t,{t:.2f},{t + d:.2f})'"
        )
        t += d
    return ",".join(parts)


def render_scene(idx, narration, clip, audio, img=None):
    dur = duration(audio)
    caps = caption_filters(idx, narration, dur)
    if img:
        n = int(dur * 30) + 6
        if idx % 2 == 0:
            z = "min(zoom+0.0009,1.30)"  # zoom in
        else:
            z = "if(eq(on,1),1.30,max(zoom-0.0009,1.0))"  # zoom out
        vf = (f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
              f"zoompan=z='{z}':d={n}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps=30,"
              f"{caps},format=yuv420p")
        vin = ["-i", str(img)]
    else:
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps=30,"
              f"{caps},format=yuv420p")
        vin = (["-stream_loop", "-1", "-i", str(clip)] if clip
               else ["-f", "lavfi", "-i", f"color=c=0x1a1a2e:s={W}x{H}:r=30"])
    out = WORK / f"scene{idx}.mp4"
    run(["ffmpeg", "-y", *vin, "-i", str(audio), "-vf", vf, "-map", "0:v", "-map", "1:a",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
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
        img = get_image(s.get("image_prompt"), i)
        clip = None if img else get_clip(s.get("keywords") or s.get("keyword") or ["nature"], i)
        print(f"  scene {i + 1} visual:", "AI image" if img else ("stock clip" if clip else "plain bg"))
        scenes.append(render_scene(i, s["narration"], clip, audio, img))
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
