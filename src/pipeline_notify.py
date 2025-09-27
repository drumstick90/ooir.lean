import requests, toml, os, re
from utils import log_info, log_error, get_env, log_step_start, log_step_end, PROJECT_ROOT


def _latest_file_with_ext(data_dir: str, ext: str, prefer_prefix: str | None = None) -> str:
    candidates = []
    for name in os.listdir(data_dir):
        if not name.endswith(ext):
            continue
        pref = name.startswith(prefer_prefix) if prefer_prefix else False
        full = os.path.join(data_dir, name)
        try:
            mtime = os.path.getmtime(full)
        except OSError:
            continue
        candidates.append((pref, mtime, full))
    if not candidates:
        raise ValueError(f"Nessun file *{ext} trovato in {data_dir}")
    candidates.sort(key=lambda t: (t[0], t[1]))
    return candidates[-1][2]


def notify(voice_file=None, text_file=None, conf_file=None, debug: bool = False):
    try:
        overall_start = log_step_start("notify")

        data_dir = os.path.join(PROJECT_ROOT, 'data')
        if not os.path.isdir(data_dir):
            raise ValueError(f"Directory dati non trovata: {data_dir}")

        # Default config path
        if conf_file is None:
            conf_file = os.path.join(PROJECT_ROOT, 'config', 'telegram.toml')
        conf = toml.load(conf_file)

        # Pick latest OGG if not provided
        if voice_file is None:
            voice_file = _latest_file_with_ext(data_dir, '.ogg', prefer_prefix='ooir_psy_trending_')
        size_voice = os.path.getsize(voice_file)
        log_info(f"notify: selected voice={voice_file} size={size_voice}B")

        # Pick matching TXT by timestamp if not provided
        if text_file is None:
            m = re.search(r"_(\d{8}_\d{6})(?:\.[^.]+)?$", os.path.basename(voice_file))
            candidate_txt = None
            if m:
                ts = m.group(1)
                maybe = os.path.join(data_dir, f"ooir_psy_trending_{ts}.txt")
                if os.path.exists(maybe):
                    candidate_txt = maybe
            if candidate_txt is None:
                candidate_txt = _latest_file_with_ext(data_dir, '.txt', prefer_prefix='ooir_psy_trending_')
            text_file = candidate_txt
        log_info(f"notify: selected text={text_file}")

        with open(text_file) as f:
            first_line = f.readline().strip()
        caption = conf["caption_prefix"] + first_line
        if debug:
            preview = caption[:120].replace("\n", " ")
            log_info(f"notify: caption_preview='{preview}'")
        log_info(f"notify: caption_len={len(caption)}")

        url = f"https://api.telegram.org/bot{get_env('TELEGRAM_TOKEN')}/sendVoice"
        files = {"voice": open(voice_file, "rb")}
        data = {"chat_id": conf["chat_id"], "caption": caption}

        http_start = log_step_start("notify.http")
        r = requests.post(url, data=data, files=files, timeout=30)
        try:
            r.raise_for_status()
        except requests.HTTPError as he:
            log_step_end("notify.http", http_start, f"status={getattr(he.response,'status_code', 'ERR')}")
            if debug:
                try:
                    log_error(f"notify: HTTP body={he.response.text[:400]}")
                except Exception:
                    pass
            raise
        log_step_end("notify.http", http_start, f"status={r.status_code}")
        log_info(f"notify: inviato voice {voice_file}")
        log_step_end("notify", overall_start, f"voice={voice_file}")
    except Exception as e:
        log_error(f"notify: errore {e}")
        raise


if __name__ == "__main__":
    debug_env = os.environ.get('NOTIFY_DEBUG', '') or os.environ.get('TTS_DEBUG', '')
    debug = str(debug_env).lower() in ('1', 'true', 'yes')
    notify(debug=debug)
