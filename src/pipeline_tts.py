import argparse, base64, requests, toml, os, re, datetime
from utils import log_info, log_error, get_env, log_step_start, log_step_end, PROJECT_ROOT

def _timestamp_from_filename(path: str) -> str | None:
    m = re.search(r"_(\d{8}_\d{6})(?:\.[^.]+)?$", os.path.basename(path))
    return m.group(1) if m else None


def _latest_txt_in_data() -> str:
    data_dir = os.path.join(PROJECT_ROOT, 'data')
    if not os.path.isdir(data_dir):
        raise ValueError(f"Directory dati non trovata: {data_dir}")
    candidates = []
    for name in os.listdir(data_dir):
        if not name.endswith('.txt'):
            continue
        pref = name.startswith('ooir_psy_trending_')
        full = os.path.join(data_dir, name)
        try:
            mtime = os.path.getmtime(full)
        except OSError:
            continue
        candidates.append((pref, mtime, full))
    if not candidates:
        raise ValueError('Nessun file TXT trovato in data/')
    candidates.sort(key=lambda t: (t[0], t[1]))
    return candidates[-1][2]


def _sanitize_text(text: str) -> str:
    # Remove non-BMP characters (e.g., musical symbols) that can break TTS
    text = re.sub(r"[^\u0000-\uFFFF]", "", text)
    # Normalize whitespace (keep newlines for sentence splitting)
    text = re.sub(r"[\t\x0b\x0c\r]", " ", text)
    text = re.sub(r"[ ]+", " ", text)
    return text.strip()


def _split_long_sentences(text: str, max_len: int = 200) -> str:
    # Treat newlines as sentence boundaries
    text = re.sub(r"\n+", ". ", text)
    # Split by sentence-ending punctuation while keeping delimiters
    parts = re.split(r"([.!?])\s+", text)
    # Reassemble into sentences with delimiters
    sentences = []
    buf = ""
    for i in range(0, len(parts), 2):
        seg = parts[i]
        delim = parts[i + 1] if i + 1 < len(parts) else ""
        sentence = (seg + (delim or "")).strip()
        if not sentence:
            continue
        sentences.append(sentence)

    # Further split sentences longer than max_len using commas/semicolons/spaces
    final_segments: list[str] = []
    for s in sentences:
        cur = s
        while len(cur) > max_len:
            # Try to break near max_len at preferred separators
            window = cur[: max_len + 1]
            cut = max(window.rfind("."), window.rfind(";"), window.rfind(","))
            if cut < max_len * 0.6:
                # fallback to last space
                cut = window.rfind(" ")
            if cut <= 0:
                cut = max_len
            chunk = cur[:cut].strip()
            if chunk and chunk[-1] not in ".!?":
                chunk += "."
            final_segments.append(chunk)
            cur = cur[cut:].lstrip()
        if cur:
            if cur[-1] not in ".!?":
                cur += "."
            final_segments.append(cur)

    # Stats logging helper
    if final_segments:
        max_seg = max(len(x) for x in final_segments)
        log_info(f"tts: segments={len(final_segments)} max_seg_len={max_seg}")

    return " ".join(final_segments)


def tts(in_file: str | None = None, out_file: str | None = None, conf_file: str | None = None, debug: bool = False):
    try:
        overall_start = log_step_start("tts")
        if in_file is None:
            in_file = _latest_txt_in_data()
        with open(in_file) as f:
            text = f.read()
        # Sanitize and split long sentences to avoid 400 INVALID_ARGUMENT
        text = _sanitize_text(text)
        text = _split_long_sentences(text, max_len=200)
        if conf_file is None:
            conf_file = os.path.join(PROJECT_ROOT, 'config', 'tts.toml')
        conf = toml.load(conf_file) if os.path.exists(conf_file) else {
            "voice": "it-IT-Chirp3-HD-Vindemiatrix",
            "encoding": "OGG_OPUS",
            "speaking_rate": 1.0,
            "pitch": 0.0,
        }

        # Safe defaults if keys are missing in tts.toml
        voice = conf.get("voice", "it-IT-Chirp3-HD-Vindemiatrix")
        encoding = conf.get("encoding", "OGG_OPUS")
        speaking_rate = float(conf.get("speaking_rate", 1.0))
        pitch = float(conf.get("pitch", 0.0))

        url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={get_env('GCP_TTS_KEY')}"
        req = {
            "input": {"text": text},
            # Chirp 3 HD voices follow the naming like it-IT-Chirp3-HD-<Name>
            "voice": {"languageCode": "it-IT", "name": voice},
            "audioConfig": {
                "audioEncoding": encoding,
                "speakingRate": speaking_rate,
                "pitch": pitch,
            }
        }
        if debug:
            log_info(f"tts: text chars={len(text)} voice={voice} enc={encoding}")
        http_start = log_step_start("tts.http")
        r = requests.post(url, json=req, timeout=180)
        r.raise_for_status()
        log_step_end("tts.http", http_start, f"status={r.status_code}")
        audio_b64 = r.json()["audioContent"]

        # timestamped output: riusa timestamp input se presente, altrimenti ora
        ts = _timestamp_from_filename(in_file) or datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        if out_file is None:
            out_file = os.path.join(PROJECT_ROOT, 'data', 'ooir_psy_trending.ogg')
        base, ext = os.path.splitext(out_file)
        if not ext:
            ext = ".ogg"
        out_ts = f"{base}_{ts}{ext}"

        os.makedirs(os.path.dirname(out_ts), exist_ok=True)
        with open(out_ts, "wb") as f:
            f.write(base64.b64decode(audio_b64))
        size_bytes = os.path.getsize(out_ts)
        log_info(f"tts: generated {out_ts} size={size_bytes}B")
        log_step_end("tts", overall_start, f"output={out_ts}")
    except Exception as e:
        if debug and 'r' in locals():
            try:
                log_error(f"tts: HTTP {r.status_code}: {r.text[:400]}")
            except Exception:
                pass
        log_error(f"tts: errore {e}")
        raise

if __name__ == "__main__":
    debug_env = os.environ.get('TTS_DEBUG', '').lower() in ('1', 'true', 'yes')
    tts(debug=debug_env)
