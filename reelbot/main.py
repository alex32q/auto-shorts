def _gtts(text, path):
    """Google TTS (free): edge-tts fail hone par fallback."""
    from gtts import gTTS
    gTTS(text=text, lang="hi", slow=False).save(str(path))
    if not pathlib.Path(path).exists() or pathlib.Path(path).stat().st_size < 1000:
        raise RuntimeError("gTTS ne khali audio diya")


async def _tts(text, path):
    """Pehle edge-tts (3 awaazein, retry), phir gTTS. Emoji/symbol hata ke."""
    clean = re.sub(r"[^\w\s।,.!?'\"-]", " ", text)
    clean = re.sub(r"\s+", " ", clean).strip() or "shayari"

    for voice in (VOICE, "hi-IN-MadhurNeural", "hi-IN-SwaraNeural"):
        for attempt in range(2):
            try:
                await edge_tts.Communicate(clean, voice).save(str(path))
                if pathlib.Path(path).exists() and pathlib.Path(path).stat().st_size > 1000:
                    return
            except Exception as e:  # noqa
                log("Edge TTS retry:", voice, e)
            await asyncio.sleep(2 * (attempt + 1))

    for attempt in range(2):
        try:
            _gtts(clean, path)
            log("gTTS fallback use hua")
            return
        except Exception as e:  # noqa
            log("gTTS retry:", e)
            await asyncio.sleep(3 * (attempt + 1))

    raise RuntimeError("TTS fail: edge-tts aur gTTS dono fail")
