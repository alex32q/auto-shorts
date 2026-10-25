"""
Auto Instagram Reel bot: shayari reels (sad, love, romantic, judai, yaadein, motivation)
Topic -> 3 scripts -> best script -> images -> voice + subtitles -> Instagram
"""
import asyncio
import io
import json
import os
import pathlib
import random
import re
import textwrap
import time
from urllib.parse import quote

import edge_tts
import numpy as np
import requests
from google import genai
from google.genai import types
from PIL import Image, ImageDraw, ImageFont

# ---------------- Settings ----------------
TEXT_MODEL = os.getenv("TEXT_MODEL", "gemini-3.5-flash-lite")
VEO_MODEL = os.getenv("VEO_MODEL", "veo-3.1-fast-generate-preview")
USE_VEO = os.getenv("USE_VEO", "0") == "1"       # 0 = free motion effects (default)
SEGMENTS = int(os.getenv("SEGMENTS", "3"))        # 3 segments = 4 images
VOICE = os.getenv("VOICE", "hi-IN-SwaraNeural")   # male: hi-IN-MadhurNeural
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"        # 1 = upload nahi, sirf video banao
GRAPH = "https://graph.facebook.com/v21.0"

W, H = 1080, 1920
OUT = pathlib.Path("output")
OUT.mkdir(exist_ok=True)
HISTORY = pathlib.Path("data/history.json")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

STYLE = (
    "cute 2D cartoon illustration, soft warm colors, clean outlines, vertical 9:16, "
    "emotional mood, no text or letters in the image"
)

SHAYARI_CATEGORIES = [
    "Sad shayari (dard aur tanhai)",
    "Love shayari (ishq aur pyaar)",
    "Romantic shayari (nazdeeki aur chhoti chhoti baatein)",
    "Judai shayari (breakup aur alag hona)",
    "Intezaar shayari (kisi ka intezaar)",
    "Yaadein shayari (purani yaadein)",
    "Bewafai shayari (dhoka aur bharosa toot na)",
    "Dosti shayari (purane dost)",
    "Maa-papa shayari (ghar ki yaad)",
    "Zindagi aur motivation shayari (himmat aur umeed)",
    "Raat aur akelapan shayari (raat 2 baje ka mood)",
]
SHAYARI_ANGLES = [
    "Ek sher jo seedha dil mein lage",
    "Do line ka dard, phir ek umeed",
    "Ek chhoti kahani jo shayari mein khatam ho",
    "Wo baat jo kehni thi par keh nahi paye",
]
FOOTER = "Aapka dil kya kehta hai? Comment karo"

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def log(*a):
    print(*a, flush=True)


# ---------------- Gemini helpers ----------------
def ask_raw(prompt, as_json=False, temp=0.9):
    kwargs = {"temperature": temp}
    if as_json:
        kwargs["response_mime_type"] = "application/json"
    for attempt in range(3):
        try:
            return client.models.generate_content(
                model=TEXT_MODEL, contents=prompt,
                config=types.GenerateContentConfig(**kwargs))
        except Exception as e:  # noqa
            log("Gemini retry:", e)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("Gemini fail")


def ask_json(prompt, temp=0.9):
    text = ask_raw(prompt, as_json=True, temp=temp).text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    return json.loads(text)


# ---------------- History ----------------
def load_history():
    return json.loads(HISTORY.read_text()) if HISTORY.exists() else []


def save_history(h):
    HISTORY.parent.mkdir(exist_ok=True)
    HISTORY.write_text(json.dumps(h, ensure_ascii=False, indent=1))


# ---------------- Step 1: topic ----------------
def pick_topic(history):
    past = [h["topic"] for h in history[-80:]]
    cats = "; ".join(SHAYARI_CATEGORIES)
    return ask_json(
        f"Instagram Reel ke liye ek naya shayari topic chuno. Category ho: {cats}. "
        f"Ye topics repeat mat karo: {past}. Topic aisa ho jo log real life mein mehsoos karte "
        "hain (jaise 'woh message jo seen hokar bhi reply nahi aaya', 'mummy ki yaad mein', "
        "'raat ko purani chat dekhna').\n"
        'JSON: {"topic": "..."}',
        temp=1.0)


# ---------------- Step 2: scripts ----------------
def write_script(topic, angle):
    try:
        s = ask_json(
            f"Topic: {topic}\nAngle: {angle}\n"
            f"Ek {SEGMENTS * 8} second ki Instagram Reel script likho jisme shayari ho.\n"
            "NIYAM:\n"
            "- Shayari bilkul original honi chahiye. Kisi famous shayar ya gaane ki line copy mat karo.\n"
            "- Bhasha real life jaisi ho: Hindi/Hinglish, simple aur dil se.\n"
            "- Pehla segment strong hook ho jo dil ko chhu jaye.\n"
            "- Aakhri segment mein ek sawaal ya comment ka call-to-action ho.\n"
            "- character: ek short fixed description (jaise 'a young Indian woman in a yellow kurti, "
            "long black hair'). Ye har image mein same rahega.\n"
            f"- images: exactly {SEGMENTS + 1} scene prompts (English). Har prompt mein character "
            "ka description repeat ho, aur mood topic ke hisaab se ho. Koi text na ho.\n"
            f"- segments: exactly {SEGMENTS}; har ek mein narration_hi (Devanagari Hindi, 18-25 "
            "shabd, shayari ke 1-2 sher), caption_hinglish (Roman script, max 8 shabd, emoji nahi), "
            "motion_prompt (English, photo i se photo i+1 tak kya movement ho)\n"
            "- caption: Instagram caption Hinglish mein (2 line), hashtags: 8 hashtags "
            "(jaise #shayari #sadshayari #lovequotes #hindishayari)\n"
            'JSON: {"character":"","images":["..."],"segments":[{"narration_hi":"","caption_hinglish":"",'
            '"motion_prompt":""}],"caption":"","hashtags":["#..."]}',
            temp=1.0)
        assert len(s["images"]) == SEGMENTS + 1 and len(s["segments"]) == SEGMENTS
        return s
    except Exception as e:  # noqa
        log("Script fail:", angle, e)
        return None


# ---------------- Step 3: judge ----------------
def pick_best(scripts):
    brief = [{
        "i": i,
        "hook": s["segments"][0]["caption_hinglish"],
        "narration": " ".join(x["narration_hi"] for x in s["segments"]),
        "caption": s["caption"],
    } for i, s in enumerate(scripts)]
    res = ask_json(
        f"Shayari scripts: {json.dumps(brief, ensure_ascii=False)}\n"
        "Har script ko score do (1-10): hook (scroll rokne wala), quality (shayari kitni dil se "
        "nikli hui aur asli lagti hai, ghisi-pitti line nahi). safe=false karo agar script mein "
        "gaali, nafrat, kisi asli insaan ka naam ya kisi ko nuksan pahunchane wali baat ho.\n"
        'JSON object mein do: {"scores":[{"i":0,"hook":0,"quality":0,"safe":true}]}')
    log("Judge:", res)
    items = res.get("scores", res) if isinstance(res, dict) else res
    ok = []
    for x in items:
        if not isinstance(x, dict):
            continue
        idx = x.get("i")
        if isinstance(idx, int) and 0 <= idx < len(scripts) and x.get("safe") and x.get("quality", 0) >= 7:
            ok.append(x)
    if not ok:
        return None
    best = max(ok, key=lambda x: x.get("hook", 0) + x.get("quality", 0))
    return scripts[best["i"]]


# ---------------- Step 4: images (free) ----------------
def gen_image_free(prompt, seed, path):
    """Pollinations.ai: free, bina key. Same seed se character thoda consistent rehta hai."""
    url = (f"https://image.pollinations.ai/prompt/{quote(prompt[:900])}"
           f"?width=720&height=1280&seed={seed}&model=flux&nologo=true")
    for attempt in range(4):
        try:
            r = requests.get(url, timeout=180)
            if r.ok and r.headers.get("content-type", "").startswith("image"):
                Image.open(io.BytesIO(r.content)).convert("RGB").save(path)
                return
            raise RuntimeError(f"status {r.status_code}")
        except Exception as e:  # noqa
            log("Image retry:", e)
            time.sleep(15 * (attempt + 1))
    raise RuntimeError("Image generation fail")


def make_images(script):
    paths, seed = [], int(time.time()) % 100000
    for i, p in enumerate(script["images"]):
        path = OUT / f"scene_{i}.png"
        gen_image_free(f"{STYLE}. Character: {script['character']}. Scene: {p}", seed, path)
        paths.append(path)
        time.sleep(5)
    return paths


# ---------------- Step 5: Veo (optional, paid) ----------------
def veo_clip(a, b, motion, out):
    def img(p):
        return types.Image(image_bytes=pathlib.Path(p).read_bytes(), mime_type="image/png")
    op = client.models.generate_videos(
        model=VEO_MODEL,
        prompt=f"{motion}. Smooth cartoon animation, same character, no text, no subtitles.",
        image=img(a),
        config=types.GenerateVideosConfig(last_frame=img(b), aspect_ratio="9:16"))
    t0 = time.time()
    while not op.done:
        if time.time() - t0 > 900:
            raise TimeoutError("Veo timeout")
        time.sleep(10)
        op = client.operations.get(op)
    v = op.response.generated_videos[0]
    client.files.download(file=v.video)
    v.video.save(str(out))


# ---------------- Step 6: edit ----------------
def text_image(text, size, fontsize=62):
    try:
        font = ImageFont.truetype(FONT, fontsize)
    except Exception:  # noqa
        font = ImageFont.load_default()
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    lines = textwrap.wrap(text, width=20)
    lh = fontsize + 14
    y = (size[1] - lh * len(lines)) // 2
    for line in lines:
        w = d.textlength(line, font=font)
        d.text(((size[0] - w) / 2, y), line, font=font, fill="white",
               stroke_width=6, stroke_fill="black")
        y += lh
    return np.array(img)


def build_video(script, imgs, clips):
    from moviepy.editor import (AudioFileClip, CompositeAudioClip, CompositeVideoClip,
                                ImageClip, VideoFileClip, afx, concatenate_videoclips, vfx)

    def fit(c):
        s = max(W / c.w, H / c.h)
        c = c.resize(s)
        return c.crop(x_center=c.w / 2, y_center=c.h / 2, width=W, height=H)

    segs, voices, t = [], [], 0.0
    for i, s in enumerate(script["segments"]):
        ap = OUT / f"voice_{i}.mp3"
        asyncio.run(edge_tts.Communicate(s["narration_hi"], VOICE).save(str(ap)))
        a = AudioFileClip(str(ap))
        d = a.duration + 0.3
        voices.append(a.set_start(t))
        t += d

        if clips[i] and pathlib.Path(clips[i]).exists():
            v = fit(VideoFileClip(str(clips[i])).without_audio().fx(vfx.speedx, final_duration=d))
        else:  # free motion: zoom in / zoom out / pan (har scene alag)
            base = fit(ImageClip(str(imgs[i])).set_duration(d))
            mode = i % 3
            if mode == 0:
                moved = base.resize(lambda x, d=d: 1 + 0.10 * x / d).set_position("center")
            elif mode == 1:
                moved = base.resize(lambda x, d=d: 1.10 - 0.10 * x / d).set_position("center")
            else:
                big = base.resize(1.12)
                moved = big.set_position(lambda x, d=d: (-0.12 * W * x / d, -0.06 * H))
            v = CompositeVideoClip([moved], size=(W, H)).set_duration(d)
        sub = (ImageClip(text_image(s["caption_hinglish"], (W - 120, 420)))
               .set_duration(d).set_position(("center", 1250)))
        segs.append(CompositeVideoClip([v, sub], size=(W, H)).set_duration(d))

    # end card
    end_d = 2.5
    end_base = fit(ImageClip(str(imgs[-1])).set_duration(end_d)).fx(vfx.colorx, 0.45)
    end_txt = ImageClip(text_image(FOOTER, (W - 120, 600), 56)).set_duration(end_d).set_position("center")
    segs.append(CompositeVideoClip([end_base, end_txt], size=(W, H)).set_duration(end_d))

    final = concatenate_videoclips(segs)
    tracks = voices
    music = pathlib.Path("assets/music.mp3")
    if music.exists():
        m = AudioFileClip(str(music)).volumex(0.12)
        tracks = voices + [afx.audio_loop(m, duration=final.duration)]
    final = final.set_audio(CompositeAudioClip(tracks).set_duration(final.duration))
    out = OUT / "reel.mp4"
    final.write_videofile(str(out), fps=24, codec="libx264", audio_codec="aac",
                          preset="medium", threads=2, logger=None)
    return out


# ---------------- Step 7: upload ----------------
def upload_cloudinary(path):
    import cloudinary.uploader  # CLOUDINARY_URL env se config
    r = cloudinary.uploader.upload(str(path), resource_type="video")
    return r["secure_url"]


def ig_publish(video_url, caption):
    uid, token = os.environ["IG_USER_ID"], os.environ["IG_ACCESS_TOKEN"]
    r = requests.post(f"{GRAPH}/{uid}/media", data={
        "media_type": "REELS", "video_url": video_url, "caption": caption,
        "share_to_feed": "true", "access_token": token}, timeout=60)
    if not r.ok:
        raise RuntimeError(f"IG container error: {r.text}")
    cid = r.json()["id"]
    for _ in range(40):
        st = requests.get(f"{GRAPH}/{cid}", params={
            "fields": "status_code,status", "access_token": token}, timeout=60).json()
        log("IG status:", st)
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"IG processing fail: {st}")
        time.sleep(15)
    else:
        raise TimeoutError("IG processing timeout")
    r = requests.post(f"{GRAPH}/{uid}/media_publish", data={
        "creation_id": cid, "access_token": token}, timeout=60)
    if not r.ok:
        raise RuntimeError(f"IG publish error: {r.text}")
    log("Published! media id:", r.json().get("id"))


# ---------------- Main ----------------
def main():
    history = load_history()
    topic = pick_topic(history)["topic"]
    log("Topic:", topic)

    angles = random.sample(SHAYARI_ANGLES, 3)
    scripts = [s for s in (write_script(topic, a) for a in angles) if s]
    if not scripts:
        raise RuntimeError("Koi script nahi bani")
    best = pick_best(scripts)
    if not best:
        log("SKIP: koi script quality ke saath safe nahi thi.")
        history.append({"topic": topic, "status": "skipped"})
        save_history(history)
        return

    imgs = make_images(best)

    clips = []
    for i in range(SEGMENTS):
        out = OUT / f"clip_{i}.mp4"
        if USE_VEO:
            try:
                veo_clip(imgs[i], imgs[i + 1], best["segments"][i]["motion_prompt"], out)
                clips.append(out)
                continue
            except Exception as e:  # noqa
                log(f"Veo fail clip {i}, fallback:", e)
        clips.append(None)

    video = build_video(best, imgs, clips)
    tags = " ".join(h if h.startswith("#") else f"#{h}" for h in best["hashtags"][:10])
    caption = "\n".join([best["caption"], tags])[:2200]
    log("Caption:\n", caption)

    if DRY_RUN:
        log("DRY_RUN: upload nahi kiya. Video:", video)
        return

    ig_publish(upload_cloudinary(video), caption)
    history.append({"topic": topic, "status": "posted", "date": time.strftime("%Y-%m-%d")})
    save_history(history)


if __name__ == "__main__":
    main()
