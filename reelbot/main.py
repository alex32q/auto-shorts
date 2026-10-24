"""
Auto Instagram Reel bot
Flow: topic -> PubMed research (papers, best chuno) -> 3 scripts -> best script
      -> images (Nano Banana) -> Veo (photo1 -> photo2 ...) -> voice -> edit -> Instagram
"""
import asyncio
import io
import json
import os
import pathlib
import re
import textwrap
import time
import xml.etree.ElementTree as ET

import edge_tts
import numpy as np
import requests
from google import genai
from google.genai import types
from PIL import Image, ImageDraw, ImageFont

# ---------------- Settings (env se badal sakte ho) ----------------
TEXT_MODEL = os.getenv("TEXT_MODEL", "gemini-2.5-flash")
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gemini-2.5-flash-image")
VEO_MODEL = os.getenv("VEO_MODEL", "veo-3.1-fast-generate-preview")
USE_VEO = os.getenv("USE_VEO", "0") == "1"      # 0 = free MoviePy motion (default), 1 = paid Veo
IMAGE_BACKEND = os.getenv("IMAGE_BACKEND", "pollinations")  # pollinations = free, gemini = paid
SEGMENTS = int(os.getenv("SEGMENTS", "3"))       # 3 segments = 4 photos = 3 Veo clips
VOICE = os.getenv("VOICE", "hi-IN-SwaraNeural")  # male: hi-IN-MadhurNeural
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"       # 1 = upload nahi, sirf video banao
NICHE = os.getenv("NICHE", "ghar ke nuskhe aur health myths (Indian audience)")
GRAPH = "https://graph.facebook.com/v21.0"

W, H = 1080, 1920
OUT = pathlib.Path("output")
OUT.mkdir(exist_ok=True)
HISTORY = pathlib.Path("data/history.json")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

STYLE = (
    "cute 2D cartoon illustration, soft warm colors, clean outlines, vertical 9:16, "
    "main character: friendly Indian cartoon grandmother (dadi) with white hair bun, "
    "round glasses, orange saree. No text or letters in the image"
)
ANGLES = [
    "Myth vs Fact (sach ya myth?)",
    "Log ye galti karte hain",
    "Dadi ka nuskha: kya science maanta hai?",
]

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def log(*a):
    print(*a, flush=True)


# ---------------- Gemini helpers ----------------
def ask_raw(prompt, search=False, as_json=False, temp=0.4):
    kwargs = {"temperature": temp}
    if search:
        kwargs["tools"] = [types.Tool(google_search=types.GoogleSearch())]
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


def ask_json(prompt, temp=0.4):
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
    past = [h["topic"] for h in history[-60:]]
    return ask_json(
        f"Niche: {NICHE}. Instagram Reel ke liye ek naya topic chuno jo log search "
        f"karte hain (jaise 'tulsi ke patte aur pathri'). Ye topics repeat mat karo: {past}.\n"
        'JSON: {"topic": "...", "claim": "wo popular daava jise jaanchna hai", '
        '"pubmed_query": "English mein 3-5 keywords, PubMed pe research dhundhne ke liye"}',
        temp=1.0)


# ---------------- Step 2: research (PubMed, bilkul free) ----------------
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def pubmed_search(query, n=6):
    ids = requests.get(f"{EUTILS}/esearch.fcgi", params={
        "db": "pubmed", "term": query, "retmax": n, "retmode": "json",
        "sort": "relevance"}, timeout=40).json()["esearchresult"]["idlist"]
    if not ids:
        return []
    raw = requests.get(f"{EUTILS}/efetch.fcgi", params={
        "db": "pubmed", "id": ",".join(ids), "retmode": "xml"}, timeout=40).text
    out = []
    for art in ET.fromstring(raw).iter("PubmedArticle"):
        t = art.find(".//ArticleTitle")
        title = "".join(t.itertext()) if t is not None else ""
        abstract = " ".join("".join(a.itertext()) for a in art.findall(".//AbstractText"))
        if title and abstract:
            out.append({
                "title": title,
                "journal": art.findtext(".//Journal/Title") or "",
                "year": art.findtext(".//PubDate/Year") or "",
                "types": [p.text for p in art.findall(".//PublicationType") if p.text],
                "abstract": abstract[:700],
            })
    return out


def research(claim, query):
    try:
        papers = pubmed_search(query)
    except Exception as e:  # noqa
        log("PubMed fail:", e)
        return None
    log("PubMed papers:", [p["title"] for p in papers])
    if len(papers) < 2:
        return None
    info = ask_json(
        f"Claim: {claim}\nResearch papers (PubMed): {json.dumps(papers, ensure_ascii=False)}\n"
        "In papers se jaancho ki claim kitna saabit hai. Har paper ko quality score (1-10) do: "
        "insaanon pe trial / meta-analysis / systematic review ko zyada, animal ya lab study ko kam. "
        "Sabse relevant aur bharosemand paper chuno (best_idx). Agar papers claim ko saabit "
        "nahi karte, to imandaari se wahi batao (claim myth bhi ho sakta hai). safe_to_publish "
        "false karo agar papers claim se match nahi karte ya claim khatarnak hai "
        "(jaise doctor ka ilaaj chhodna).\n"
        'JSON: {"sources":[{"idx":0,"quality":0,"evidence_level":"human_trials|animal_or_lab|'
        'traditional_only|unclear","summary":""}],"best_idx":0,"safe_to_publish":true,'
        '"safe_framing":"claim ko imandaari se kaise bolna hai"}')
    idx = info.get("best_idx", 0)
    if not isinstance(idx, int) or idx >= len(papers):
        idx = 0
    p = papers[idx]
    info["best_source"] = f'PubMed: {p["journal"]} {p["year"]}'.strip()
    log("Research verdict:", json.dumps(info, ensure_ascii=False))
    return info


# ---------------- Step 3: 3 scripts ----------------
def write_script(topic, info, angle):
    try:
        s = ask_json(
            f"Topic: {topic}\nAngle: {angle}\nEvidence: {json.dumps(info, ensure_ascii=False)}\n"
            f"Ek {SEGMENTS * 8} second ki Instagram Reel script likho.\n"
            "NIYAM: 'ilaaj', 'theek ho jayega', 'saaf kar dega', 'guarantee' jaise daave bilkul nahi. "
            "Evidence ke hisaab se bolo: 'kuch studies', 'paramparik roop se maana jata hai', "
            "'madad kar sakta hai'. Aakhri segment mein doctor se salah ka zikr karo. Pehla "
            "segment ek strong hook ho.\n"
            f"- images: exactly {SEGMENTS + 1} scene prompts (English, character same rahe, "
            "har scene pichle se thoda aage badhe)\n"
            f"- segments: exactly {SEGMENTS}; har ek mein narration_hi (Devanagari Hindi, 18-22 "
            "shabd), caption_hinglish (Roman script, max 8 shabd, emoji nahi), motion_prompt "
            "(English, photo i se photo i+1 tak kya movement ho)\n"
            "- caption: Instagram caption Hinglish mein (hook + 2 line), hashtags: 8 hashtags\n"
            'JSON: {"images":["..."],"segments":[{"narration_hi":"","caption_hinglish":"",'
            '"motion_prompt":""}],"caption":"","hashtags":["#..."]}',
            temp=1.0)
        assert len(s["images"]) == SEGMENTS + 1 and len(s["segments"]) == SEGMENTS
        return s
    except Exception as e:  # noqa
        log("Script fail:", angle, e)
        return None


# ---------------- Step 4: judge ----------------
def pick_best(scripts, info):
    brief = [{
        "i": i,
        "hook": s["segments"][0]["caption_hinglish"],
        "narration": " ".join(x["narration_hi"] for x in s["segments"]),
        "caption": s["caption"],
    } for i, s in enumerate(scripts)]
    res = ask_json(
        f"Evidence: {json.dumps(info, ensure_ascii=False)}\nScripts: {json.dumps(brief, ensure_ascii=False)}\n"
        "Har script ko score do (1-10): hook (scroll rokne wala), accuracy (evidence se match). "
        "safe=false karo agar cure/ilaaj/guarantee ka daava hai, ya doctor se bachne ko kehta hai.\n"
        'JSON: {"scores":[{"i":0,"hook":0,"accuracy":0,"safe":true}]}')
    log("Judge:", res)
    ok = [x for x in res["scores"] if x["safe"] and x["accuracy"] >= 7]
    if not ok:
        return None
    best = max(ok, key=lambda x: x["hook"] + x["accuracy"])
    return scripts[best["i"]]


# ---------------- Step 5: images ----------------
def gen_image_free(prompt, seed, path):
    """Pollinations.ai: free, bina key. Same seed = character thoda consistent."""
    from urllib.parse import quote
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
            log("Free image retry:", e)
            time.sleep(15 * (attempt + 1))
    raise RuntimeError("Free image generation fail")


def gen_image(prompt, ref, path):
    parts = []
    if ref:
        parts.append(types.Part.from_bytes(data=pathlib.Path(ref).read_bytes(), mime_type="image/png"))
        prompt = "Keep the exact same cartoon character and art style as the reference image. " + prompt
    parts.append(prompt)
    for attempt in range(3):
        try:
            r = client.models.generate_content(
                model=IMAGE_MODEL, contents=parts,
                config=types.GenerateContentConfig(
                    response_modalities=["TEXT", "IMAGE"],
                    image_config=types.ImageConfig(aspect_ratio="9:16")))
            for p in r.candidates[0].content.parts:
                if p.inline_data and p.inline_data.data:
                    Image.open(io.BytesIO(p.inline_data.data)).convert("RGB").save(path)
                    return
            raise RuntimeError("image nahi mili")
        except Exception as e:  # noqa
            log("Image retry:", e)
            time.sleep(8 * (attempt + 1))
    raise RuntimeError("Image generation fail")


def make_images(prompts):
    paths, seed = [], int(time.time()) % 100000
    for i, p in enumerate(prompts):
        path = OUT / f"scene_{i}.png"
        if IMAGE_BACKEND == "gemini":
            gen_image(f"{STYLE}. Scene: {p}", paths[0] if paths else None, path)
        else:
            gen_image_free(f"{STYLE}. Scene: {p}", seed, path)
            time.sleep(5)  # free server pe halka load
        paths.append(path)
    return paths


# ---------------- Step 6: Veo (photo i -> photo i+1) ----------------
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


# ---------------- Step 7: edit ----------------
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
            v = VideoFileClip(str(clips[i])).without_audio().fx(vfx.speedx, final_duration=d)
            v = fit(v)
        else:  # free motion: zoom-in / zoom-out / pan (har scene alag)
            base = fit(ImageClip(str(imgs[i])).set_duration(d))
            mode = i % 3
            if mode == 0:    # zoom in
                moved = base.resize(lambda x, d=d: 1 + 0.10 * x / d).set_position("center")
            elif mode == 1:  # zoom out
                moved = base.resize(lambda x, d=d: 1.10 - 0.10 * x / d).set_position("center")
            else:            # pan left
                big = base.resize(1.12)
                moved = big.set_position(lambda x, d=d: (-0.12 * W * x / d, -0.06 * H))
            v = CompositeVideoClip([moved], size=(W, H)).set_duration(d)
        sub = (ImageClip(text_image(s["caption_hinglish"], (W - 120, 420)))
               .set_duration(d).set_position(("center", 1250)))
        segs.append(CompositeVideoClip([v, sub], size=(W, H)).set_duration(d))

    # end card: disclaimer
    end_d = 2.5
    end_base = fit(ImageClip(str(imgs[-1])).set_duration(end_d)).fx(vfx.colorx, 0.45)
    end_txt = ImageClip(text_image("Ye sirf jankari ke liye hai. Bimari mein doctor se salah lein.",
                                   (W - 120, 600), 56)).set_duration(end_d).set_position("center")
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


# ---------------- Step 8: upload ----------------
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
    t = pick_topic(history)
    topic, claim = t["topic"], t["claim"]
    log("Topic:", topic, "| Claim:", claim)

    info = research(claim, t.get("pubmed_query") or claim)
    if not info or not info.get("safe_to_publish"):
        log("SKIP: bharosemand source nahi mila ya claim safe nahi.")
        history.append({"topic": topic, "status": "skipped"})
        save_history(history)
        return

    scripts = [s for s in (write_script(topic, info, a) for a in ANGLES) if s]
    if not scripts:
        raise RuntimeError("Koi script nahi bani")
    best = pick_best(scripts, info)
    if not best:
        log("SKIP: koi script safe/accurate nahi thi.")
        history.append({"topic": topic, "status": "skipped"})
        save_history(history)
        return

    imgs = make_images(best["images"])

    clips = []
    for i in range(SEGMENTS):
        out = OUT / f"clip_{i}.mp4"
        if USE_VEO:
            try:
                veo_clip(imgs[i], imgs[i + 1], best["segments"][i]["motion_prompt"], out)
                clips.append(out)
                continue
            except Exception as e:  # noqa
                log(f"Veo fail clip {i}, fallback zoom:", e)
        clips.append(None)

    video = build_video(best, imgs, clips)
    tags = " ".join(h if h.startswith("#") else f"#{h}" for h in best["hashtags"][:10])
    caption = (f'{best["caption"]}\n\n⚠️ Ye sirf jankari ke liye hai. Bimari mein doctor se '
               f'salah lein.\nSource: {info["best_source"]}\n\n{tags}')[:2200]
    log("Caption:\n", caption)

    if DRY_RUN:
        log("DRY_RUN: upload nahi kiya. Video:", video)
        return

    ig_publish(upload_cloudinary(video), caption)
    history.append({"topic": topic, "status": "posted", "date": time.strftime("%Y-%m-%d")})
    save_history(history)


if __name__ == "__main__":
    main()
