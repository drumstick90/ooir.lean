#!/usr/bin/env python3
"""
MVP Pipeline Summarize - riassunto con Gemini (auto-pick latest TSV, no CLI args)
Con gestione quota: prova più modelli (fallback) in caso di 429/RESOURCE_EXHAUSTED.
"""
import json
import subprocess
import sys
import os
import re
import datetime
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import log_info, log_error, get_env, log_step_start, log_step_end, PROJECT_ROOT

def _read_gemini_api_key() -> str:
    """Legge GEMINI_API_KEY usando utils.get_env con path robusto."""
    return get_env('GEMINI_API_KEY')


def _latest_tsv_in_data() -> str:
    """Trova l'ultimo TSV generato in data/ (preferendo ooir_psy_trending_*.tsv)."""
    data_dir = os.path.join(PROJECT_ROOT, 'data')
    if not os.path.isdir(data_dir):
        raise ValueError(f"Directory dati non trovata: {data_dir}")
    candidates = []
    for name in os.listdir(data_dir):
        if not name.endswith('.tsv'):
            continue
        # preferisci i file che iniziano con ooir_psy_trending_
        pref = name.startswith('ooir_psy_trending_')
        full = os.path.join(data_dir, name)
        try:
            mtime = os.path.getmtime(full)
        except OSError:
            continue
        candidates.append((pref, mtime, full))
    if not candidates:
        raise ValueError('Nessun file TSV trovato in data/')
    # ordina: preferiti prima (pref=True), poi per mtime decrescente
    candidates.sort(key=lambda t: (t[0], t[1]))
    pref, mtime, path = candidates[-1]
    return path


def summarize(input_file: str | None = None, output_file: str | None = None, model: str | None = None):
    """Riassume testo usando Gemini API via curl"""
    try:
        overall_start = log_step_start("summarize")
        api_key = _read_gemini_api_key()

        # Leggi file input
        if not input_file:
            input_file = _latest_tsv_in_data()
        with open(input_file, 'r', encoding='utf-8') as f:
            content = f.read()

        log_info(f"summarize: input chars={len(content)}")

        # Prompt Template B: assistente human-like, scorrevole ma conciso, TTS-friendly
        prompt = (
            "Sei un assistente italiano human-like. Trasforma l'elenco TSV (rank<TAB>title<TAB>journal) "
            "in un copione scorrevole e conciso, ottimizzato per TTS. Regole:\n"
            "- Apertura breve e chiara: 'Ciao Pier, queste sono le notizie della settimana.'\n"
            "- UNA frase ciascuno al presente; soggetto esplicito (es. 'uno studio', 'una review', 'un trial'), "
            "  verbo chiaro (es. 'identifica', 'analizza', 'valuta'). Cita SEMPRE il journal (es. JAMA Psychiatry).\n"
            "- Aggiungi micro-transizioni leggere tra alcuni punti. Esempi NON vincolanti: "
            "  'Poi', 'Intanto', 'Da segnalare', 'Passiamo ora a', 'Nel frattempo', 'In parallelo', 'Guardiamo a', "
            "  'Chiudiamo con', 'Infine, uno sguardo a'. Varia le transizioni (usa 3–4 diverse), evita ripetizioni fisse settimana su settimana.\n"
            "- Frasi brevi, punteggiatura che aiuta la lettura, tono professionale ma umano (non enfatico, non colloquiale).\n"
            "- Chiudi con una sola riga di wrap-up: 'Questa settimana i temi caldi erano: X, Y, Z. Buona giornata.'\n"
            "- Non inventare informazioni non presenti nei titoli; niente link, niente numeri aggiuntivi, niente caratteri che possano confendere un modello text-to-speech.\n"
            "Non devono esserci righe vuote.\n\n"
            "Input (TSV) da usare come unica fonte: rank<TAB>title<TAB>journal\n\n"
            "Ecco i dati:\n" + content
        )

        # Stampa integrale del prompt che verrà inviato a Gemini (senza chiavi/API nel log)
        log_info(f"summarize: PROMPT SENT ->\n{prompt}")

        # Seleziona modelli da provare: ENV -> arg -> default consigliato, poi fallback
        env_model = os.environ.get("GEMINI_MODEL")
        candidates = [
            model or env_model or "gemini-2.5-pro-preview-03-25",
            "gemini-1.5-flash-latest",
            "gemini-1.5-pro-latest",
            "gemini-2.0-pro-exp",
        ]
        # Deduplica preservando ordine
        seen, models_to_try = set(), []
        for m in candidates:
            if m and m not in seen:
                seen.add(m)
                models_to_try.append(m)

        summary = None
        used_model = None
        last_err = None
        for current_model in models_to_try:
            log_info(f"summarize: trying model={current_model}")
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                + current_model + ":generateContent?key=" + api_key
            )
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": prompt}
                        ]
                    }
                ]
            }
            payload_json = json.dumps(payload)

            http_start = log_step_start("summarize.http")
            result = subprocess.run(
                [
                    "curl",
                    "-s",
                    "-X", "POST",
                    "-H", "Content-Type: application/json",
                    url,
                    "-d", payload_json,
                ],
                capture_output=True,
                text=True,
                timeout=45,
            )
            if result.returncode != 0:
                last_err = RuntimeError(f"curl failed: {result.stderr}")
                log_step_end("summarize.http", http_start, f"rc={result.returncode} bytes=0")
                break
            log_step_end("summarize.http", http_start, f"rc={result.returncode} bytes={len(result.stdout)}")

            try:
                resp = json.loads(result.stdout)
            except json.JSONDecodeError as je:
                last_err = RuntimeError(f"JSON non valido: {je}\nOutput: {result.stdout[:500]}")
                continue

            if isinstance(resp, dict) and "error" in resp:
                err = resp["error"]
                code = err.get("code")
                message = err.get("message", "")
                status = err.get("status", "")
                # 429/RESOURCE_EXHAUSTED / quota → prova prossimo modello
                if code == 429 or "quota" in message.lower() or "RESOURCE_EXHAUSTED" in status:
                    last_err = RuntimeError(f"Quota/429 su {current_model}: {message}")
                    continue
                last_err = RuntimeError(f"Errore API su {current_model}: {message}")
                break

            try:
                summary = resp['candidates'][0]['content']['parts'][0]['text']
                used_model = current_model
                break
            except Exception:
                last_err = RuntimeError(f"Risposta inattesa: {json.dumps(resp)[:500]}")
                continue

        if summary is None:
            raise last_err or RuntimeError("Sintesi non riuscita con i modelli disponibili")
        log_info(f"summarize: used model={used_model}")

        # Costruisci filename timestamped: riusa timestamp dell'input se presente, altrimenti ora
        input_ts_match = re.search(r"_(\d{8}_\d{6})(?:\.[^.]+)?$", os.path.basename(input_file))
        ts = input_ts_match.group(1) if input_ts_match else datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        if not output_file:
            output_file = os.path.join(PROJECT_ROOT, 'data', 'ooir_psy_trending.txt')
        base, ext = os.path.splitext(output_file)
        if not ext:
            ext = ".txt"
        timestamped_output = f"{base}_{ts}{ext}"

        # Salva risultato
        os.makedirs(os.path.dirname(timestamped_output), exist_ok=True)
        with open(timestamped_output, 'w', encoding='utf-8') as f:
            f.write(summary)

        log_info(f"summarize: saved chars={len(summary)} -> {timestamped_output}")
        log_step_end("summarize", overall_start, f"output={timestamped_output}")
        return True

    except Exception as e:
        log_error(f"Errore summarize: {e}")
        return False

if __name__ == "__main__":
    success = summarize()
    exit(0 if success else 1)