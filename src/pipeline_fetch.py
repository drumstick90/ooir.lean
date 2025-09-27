#!/usr/bin/env python3
"""
MVP Pipeline Fetch - scarica e pulisce pagina web
"""
import argparse
import subprocess
import re
import html as html_lib
import datetime
import os
from utils import log_info, log_error, PROJECT_ROOT, log_step_start, log_step_end

OOIR_TRENDING_URL = "https://ooir.org/trending.php?field=Clinical+Medicine&category=Psychiatry&days=7"

def get_default_ooir_url() -> str:
    return OOIR_TRENDING_URL

def _strip_scripts_styles(html: str) -> str:
    """Rimuove contenuti <script> e <style> dal markup HTML."""
    without_scripts = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    without_styles = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", without_scripts)
    return without_styles


def _html_to_text_basic(html: str) -> str:
    """Converte HTML in testo semplice con euristiche minime, senza dipendenze esterne."""
    cleaned = _strip_scripts_styles(html)
    # Sostituisci i tag con spazi per preservare separatori di parole
    no_tags = re.sub(r"(?is)<[^>]+>", " ", cleaned)
    unescaped = html_lib.unescape(no_tags)
    # Normalizza whitespace e rimuove righe vuote/non informative
    lines = [re.sub(r"\s+", " ", line).strip() for line in unescaped.splitlines()]
    # Tieni righe che hanno almeno una lettera e non sono solo simboli
    informative = [ln for ln in lines if ln and re.search(r"[A-Za-zÀ-ÿ0-9]", ln)]
    return "\n".join(informative)


def fetch(url=None, out_clean=None):
    """Scarica pagina e prova a estrarre testo leggibile.

    Strategia:
    1) curl con redirect (-L) e user-agent realistico
    2) prova conversione HTML->testo
    3) fallback al contenuto grezzo se il testo è troppo corto
    """
    try:
        overall_start = log_step_start("fetch")
        # Normalizza URL: se non fornito o prefissato da '@', usa OOIR di default
        if not url:
            url = OOIR_TRENDING_URL
        if isinstance(url, str) and url.startswith("@"):
            url = url[1:]
        log_info(f"fetch: target={url}")
        
        # Scarica con curl: follow redirects, UA browser, accept-language
        dl_start = log_step_start("fetch.http")
        result = subprocess.run(
            [
                "curl",
                "-sL",
                "-H",
                "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                "-H",
                "Accept-Language: en-US,en;q=0.8,it-IT,it;q=0.6",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode != 0:
            raise RuntimeError(f"curl failed: {result.stderr}")
        log_step_end("fetch.http", dl_start, f"rc={result.returncode} bytes={len(result.stdout)}")

        html_content = result.stdout

        # Prova a convertire in testo semplice
        text_content = _html_to_text_basic(html_content)

        # Se troppo corto, fai un fallback: prova a prendere righe con <p>/<h*/<li>/<a>
        if len(text_content) < 400:
            grep_result = subprocess.run(
                ["grep", "-E", "<(p|h[1-6]|li|a)"],
                input=html_content,
                capture_output=True,
                text=True,
            )
            candidate = grep_result.stdout.strip()
            if len(candidate) > len(text_content):
                text_content = candidate

        # Speciale OOIR: se URL è la pagina trending, estrai (titolo, journal) e salva come TSV
        content_to_save = None
        used_pairs = False
        pairs = []
        if "ooir.org" in url and "trending.php" in url:
            # Parsing heuristico su testo normalizzato: cerca linee data YYYY-MM-DD
            date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
            lines = [ln.strip() for ln in text_content.splitlines() if ln.strip()]
            context = []
            for ln in lines:
                if date_re.match(ln):
                    if len(context) >= 2:
                        title = context[-2].strip()
                        journal = context[-1].strip()
                        if len(title) > 3 and len(journal) > 2:
                            pairs.append((title, journal))
                    # non aggiungere la data al contesto
                else:
                    context.append(ln)

            if pairs:
                # Aggiungi rank (1-based) come prima colonna
                content_to_save = "\n".join([f"{rank+1}\t{t}\t{j}" for rank, (t, j) in enumerate(pairs)])
                used_pairs = True
        log_info(f"fetch: parsed pairs={len(pairs)}")

        # Se non speciale OOIR o nessuna coppia trovata, default
        if content_to_save is None:
            # Se ancora troppo corto, salva HTML grezzo
            content_to_save = text_content if len(text_content) >= 400 else html_content

        # Aggiungi timestamp al nome file per accumulo nel tempo
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        # Se out_clean non è fornito, usa il base di default e forza .tsv
        if not out_clean:
            out_clean = os.path.join(PROJECT_ROOT, "data", "ooir_psy_trending.tsv")
        # Se è relativo, rendilo assoluto rispetto alla root progetto
        if not os.path.isabs(out_clean):
            out_clean = os.path.join(PROJECT_ROOT, out_clean)
        # Base senza estensione + estensione forzata .tsv
        base, _ = os.path.splitext(out_clean)
        ext = ".tsv"
        timestamped_filename = f"{base}_{timestamp}{ext}"
        # Assicura che la directory esista
        os.makedirs(os.path.dirname(timestamped_filename), exist_ok=True)
        
        # Salva risultato
        with open(timestamped_filename, "w", encoding='utf-8') as f:
            f.write(content_to_save)
        
        log_info(f"fetch: saving chars={len(content_to_save)} -> {timestamped_filename}")
        log_step_end("fetch", overall_start, f"output={timestamped_filename}")
        return True
        
    except Exception as e:
        log_error(f"Errore fetch: {e}")
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Scarica e pulisce pagina web (default: OOIR Psychiatry 7d → TSV timestamped)')
    parser.add_argument("--url", required=False, default=None, help="URL da scaricare (default OOIR Psychiatry 7d)")
    parser.add_argument("--out", required=False, default=None, help="Base file output (default data/ooir_psy_trending.tsv)")
    args = parser.parse_args()
    success = fetch(args.url, args.out)
    exit(0 if success else 1)
