#!/usr/bin/env python3
"""Cerca su TMDB i FILM dei cataloghi e salva anno e generi in data/film_metadata.json.

Si lancia a mano dal PC, dopo aver aggiornato i JSON dei dischi:

    # PowerShell (la chiave gratuita si crea su themoviedb.org > Impostazioni > API)
    $env:TMDB_API_KEY = "la-tua-chiave"
    python scripts/aggiorna_generi.py

Cosa fa
  * Considera solo i film: salta le serie/gli episodi e le cartelle extra
    (Featurettes, Deleted Scenes, EXTRA, Bonus, Interviews, ...).
  * Ricava titolo e anno dal nome del file, cerca su TMDB (in italiano) e accetta
    solo corrispondenze sicure. Le altre finiscono in scripts/tmdb_dubbi.json.
  * È incrementale: interroga TMDB solo per i film senza scheda.
  * Toglie da film_metadata.json le schede dei file che non esistono più.
  * Le schede inserite a mano ("source": "Manuale") non vengono mai sovrascritte.

Risolvere un dubbio: apri scripts/tmdb_dubbi.json, copia l'id TMDB giusto tra i
"candidati" (o dal link themoviedb.org/movie/<id>) e scrivilo in
scripts/tmdb_id_manuali.json come  {"<chiave del dubbio>": <id>}.
Alla prossima esecuzione viene usato quell'id.

Opzioni utili
  --dry-run     mostra cosa cercherebbe, senza rete e senza scrivere nulla
  --disco NOME  lavora solo sui film di quel disco
  --limite N    massimo di richieste a TMDB (default 3000)
  --riprova     ripete anche i dubbi e i "non trovato" controllati di recente
  --non-potare  non toglie le schede dei file spariti dai cataloghi
"""
import argparse
import json
import os
import re
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

QUI = Path(__file__).resolve().parent
DATA = QUI.parent / "data"
FILE_SCHEDE = DATA / "film_metadata.json"
FILE_MANIFEST = DATA / "manifest.json"
FILE_DUBBI = QUI / "tmdb_dubbi.json"
FILE_MANUALI = QUI / "tmdb_id_manuali.json"

BASE = "https://api.themoviedb.org/3"
OGGI = date.today()
GIORNI_RICONTROLLO = 60

# Generi TMDB (film) -> nomi usati dal sito. "Film TV" (10770) non è un genere utile.
GENERI = {
    28: "Azione", 12: "Avventura", 16: "Animazione", 35: "Commedia", 80: "Crime",
    99: "Documentario", 18: "Drammatico", 10751: "Famiglia", 14: "Fantasy",
    36: "Storico", 27: "Horror", 10402: "Musicale", 9648: "Mistero",
    10749: "Romantico", 878: "Fantascienza", 53: "Thriller", 10752: "Guerra",
    37: "Western",
}

ESTENSIONI = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".m4v", ".mpg", ".mpeg", ".ts",
              ".m2ts", ".webm", ".flv", ".vob", ".divx", ".iso", ".3gp"}
EXTRA = re.compile(r"(?i)^(extras?|extra disc|bonus|special features?|speciali|featurettes?|"
                   r"deleted scenes?|interviews?|interviste|extended interviews?|making[ ._-]?of|"
                   r"behind the scenes|trailers?|teasers?|contenuti speciali|samples?)$")
NOME_EXTRA = re.compile(r"(?i)\b(sample|trailer|featurette|making[ ._-]?of|deleted[ ._-]?scenes?|behind the scenes)\b")
EPISODIO = re.compile(r"(?i)(\bS\s*\d{1,3}[ ._-]*E\s*\d{1,3}\b|\b\d{1,2}x\d{2,3}\b|"
                      r"\b(?:ep|episodio|episode)[ ._-]*\d{1,4}\b|\b(?:stagione|season)[ ._-]*\d{1,3}\b|\bS\d{2}\b)")
CARTELLE_SERIE = re.compile(r"(?i)\b(serie[ ._-]*tv|tv[ ._-]*series|tv[ ._-]*show|telefilm|series|"
                            r"stagion[ei]|seasons?|anime|cartoni|animazione)\b")
CARTELLE_FILM = re.compile(r"(?i)\b(film|movies?|cinema|horror|thriller|fantascienza|fantasy|sci[ ._-]?fi|commedie|azione)\b")
TECNICO = re.compile(r"(?i)\b(?:2160p|1080[pi]?|720p|576p|480p|4k|uhd|blu[ ._-]?ray|bdrip|bdmux|brrip|dvdrip|dvdmux|"
                     r"dvdscr|webrip|web[ ._-]?dl|webmux|remux|repack|x264|x265|h[ ._-]?26[45]|hevc|xvid|divx|aac|"
                     r"ac-?3|eac3|dts|hdtv|satrip|hdrip|hdr10|dovi|proper|ita|eng|jpn|jap|subs?|subita|dual|"
                     r"10bit|8bit|rm4k|rip)\b")
EDIZIONE = re.compile(r"(?i)\s*[-–+(]?\s*\b(director'?s cut|extended( edition| cut)?|uncut|unrated|remastered|"
                      r"restored|theatrical( cut)?|final cut|special edition|collector'?s edition|"
                      r"edizione \w+|bonus|extras?|trilogia)\b.*$")
ANNO = re.compile(r"(?<!\d)((?:19|20)\d\d)(?!\d)")
SENZA_ANNO_DENTRO_PARENTESI = re.compile(r"[\[(]((?!(?:19|20)\d\d)[^\])])*[\])]")


def canon(testo):
    s = unicodedata.normalize("NFKD", str(testo or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", s.casefold()))


def anno_plausibile(valore):
    return 1900 <= int(valore) <= OGGI.year + 1


def analizza_testo(testo):
    """Da un nome di file o cartella ricava (titolo, anno). Titolo vuoto se non plausibile."""
    s = str(testo).replace("_", " ")
    if s.count(".") > s.count(" "):       # "Blade.Runner.1982" -> parole separate da punti
        s = s.replace(".", " ")
    s = SENZA_ANNO_DENTRO_PARENTESI.sub(" ", s)   # toglie [ITA], (by xyz), [1080p x265 ...]
    tecnico = TECNICO.search(s)
    if tecnico:
        s = s[:tecnico.start()]
    anni = [m for m in ANNO.finditer(s) if anno_plausibile(m.group(1))]
    tra_parentesi = [m for m in anni if m.start() > 0 and s[m.start() - 1] in "([" ]
    scelto = (tra_parentesi or anni or [None])[-1]
    anno = int(scelto.group(1)) if scelto else None
    if scelto:
        prima = s[:scelto.start()]
        dopo = s[scelto.end():]
        titolo = prima if canon(prima) else dopo
        if not canon(prima) and len(anni) == 1 and not canon(dopo):
            titolo, anno = scelto.group(1), None     # il "titolo" è proprio un numero (es. 1917)
    else:
        titolo = s
    titolo = EDIZIONE.sub("", titolo)
    titolo = re.sub(r"\s+", " ", titolo).strip(" .-–_+[]()")
    return titolo, anno


def titolo_plausibile(titolo, con_anno):
    c = canon(titolo)
    if len(c) < (2 if con_anno else 4) or len(c.split()) > 14 or c.isdigit() and not con_anno:
        return False
    return c not in {"extra", "trailer", "special", "video", "sample", "episodio", "episode", "film", "animazione"}


def analizza_file(nome, percorso):
    """Restituisce (titolo, anno) se il file sembra un film, altrimenti None."""
    stem, estensione = os.path.splitext(nome)
    if estensione.lower() not in ESTENSIONI:
        return None
    segmenti = [x for x in str(percorso).replace("/", "\\").split("\\") if x]
    cartelle = [x for x in segmenti[:-1] if not re.fullmatch(r"[A-Za-z]:", x)]
    if any(EXTRA.fullmatch(c.strip()) for c in cartelle):
        return None
    if EXTRA.fullmatch(stem.strip()) or NOME_EXTRA.search(nome):
        return None
    if EPISODIO.search(nome) or any(EPISODIO.search(c) for c in cartelle):
        return None
    titolo, anno = analizza_testo(stem)
    contesto = " ".join(cartelle)
    if CARTELLE_SERIE.search(contesto) and not CARTELLE_FILM.search(contesto) and not anno:
        return None                        # file senza anno dentro cartelle di serie/anime
    if not anno and cartelle:              # anno e, se serve, titolo dalla cartella del film
        titolo_cartella, anno_cartella = analizza_testo(cartelle[-1])
        anno = anno_cartella
        if not titolo_plausibile(titolo, False) and titolo_plausibile(titolo_cartella, bool(anno)):
            titolo = titolo_cartella
    if not titolo_plausibile(titolo, bool(anno)):
        return None
    return titolo, anno


def chiave_gruppo(titolo, anno):
    return f"{canon(titolo)}|{anno or ''}"


class ClienteTMDB:
    def __init__(self, token="", chiave="", limite=3000, rps=5.0):
        self.token, self.chiave, self.limite, self.rps = token, chiave, limite, rps
        self.richieste = 0
        self._ultima = 0.0

    def get(self, percorso, parametri=None):
        if self.richieste >= self.limite:
            raise LimiteRaggiunto()
        parametri = {"language": "it-IT", **(parametri or {})}
        intestazioni = {"Accept": "application/json", "User-Agent": "listhdd-generi/1.0"}
        if self.token:
            intestazioni["Authorization"] = "Bearer " + self.token
        else:
            parametri["api_key"] = self.chiave
        url = f"{BASE}{percorso}?{urlencode(parametri)}"
        for tentativo in range(4):
            attesa = self._ultima + 1.0 / self.rps - time.monotonic()
            if attesa > 0:
                time.sleep(attesa)
            self._ultima = time.monotonic()
            self.richieste += 1
            try:
                with urlopen(Request(url, headers=intestazioni), timeout=25) as r:
                    return json.loads(r.read().decode("utf-8"))
            except HTTPError as e:
                if e.code in (401, 403):
                    raise SystemExit("TMDB ha rifiutato la chiave (errore %d): controllala." % e.code)
                if e.code == 404:
                    return None
                if e.code == 429:
                    time.sleep(float(e.headers.get("Retry-After", 5)) + 1)
                    continue
                if e.code >= 500:
                    time.sleep(2 * (tentativo + 1))
                    continue
                raise SystemExit(f"Errore TMDB {e.code} su {percorso}")
            except (URLError, TimeoutError, OSError):
                time.sleep(2 * (tentativo + 1))
        raise RetiNonDisponibile()


class LimiteRaggiunto(Exception):
    pass


class RetiNonDisponibile(Exception):
    pass


def anno_di(data_uscita):
    m = re.match(r"(\d{4})", str(data_uscita or ""))
    return int(m.group(1)) if m else None


def somiglianza(titolo, risultato):
    nomi = [n for n in (risultato.get("title"), risultato.get("original_title")) if n]
    return max(SequenceMatcher(None, canon(titolo), canon(n)).ratio() for n in nomi) if nomi else 0.0


def scegli(titolo, anno, risultati):
    """Restituisce (risultato, None) se la corrispondenza è sicura, altrimenti (None, motivo)."""
    punti = sorted(((somiglianza(titolo, r), r) for r in risultati), key=lambda x: -x[0])
    if anno:
        buoni = [(p, r) for p, r in punti if p >= 0.88 and anno_di(r.get("release_date")) and
                 abs(anno_di(r["release_date"]) - anno) <= 1]
        if not buoni:
            return None, "non_trovato" if not punti or punti[0][0] < 0.7 else "dubbio"
        if len(buoni) > 1 and buoni[0][0] - buoni[1][0] < 0.03 and \
                abs(anno_di(buoni[0][1]["release_date"]) - anno_di(buoni[1][1]["release_date"])) <= 1:
            return None, "dubbio"
        return buoni[0][1], None
    buoni = [(p, r) for p, r in punti if p >= 0.97]
    if not buoni:
        return None, "non_trovato" if not punti or punti[0][0] < 0.7 else "dubbio"
    if len(buoni) > 1 or int(buoni[0][1].get("vote_count") or 0) < 100:
        return None, "dubbio"
    return buoni[0][1], None


def scheda_da_tmdb(r):
    ids = r.get("genre_ids") or [g.get("id") for g in r.get("genres", [])]
    generi = []
    for i in ids:
        nome = GENERI.get(i)
        if nome and nome not in generi:
            generi.append(nome)
    return {
        "year": anno_di(r.get("release_date")), "genres": generi, "source": "TMDB", "kind": "movie",
        "tmdb_id": r["id"], "title": r.get("title") or r.get("original_title"),
        "overview": r.get("overview") or "", "poster_path": r.get("poster_path"),
        "vote_average": r.get("vote_average"),
        "tmdb_url": f"https://www.themoviedb.org/movie/{r['id']}",
    }


def leggi_json(percorso, predefinito):
    try:
        return json.loads(percorso.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return predefinito
    except ValueError as err:
        raise SystemExit(f"JSON non valido: {percorso}: {err}")


def scrivi_json(percorso, oggetto, a_capo=True):
    testo = json.dumps(oggetto, ensure_ascii=False, indent=2) + ("\n" if a_capo else "")
    if percorso.exists() and percorso.read_text(encoding="utf-8-sig") == testo:
        return False
    tmp = percorso.with_suffix(percorso.suffix + ".tmp")
    tmp.write_text(testo, encoding="utf-8", newline="\n")
    tmp.replace(percorso)
    return True


def raccogli(solo_disco=None):
    """Legge i cataloghi: restituisce ({chiave: (titolo, anno)} dei film, insieme di tutte le chiavi)."""
    dischi = leggi_json(FILE_MANIFEST, {}).get("dischi", [])
    if not dischi:
        raise SystemExit("data/manifest.json mancante o vuoto: fai prima git pull (o lancia scripts/aggiorna_manifest.py).")
    film, tutte = {}, set()
    for e in dischi:
        catalogo = leggi_json(DATA / e["file"], {}).get("catalogo_video", [])
        for x in catalogo:
            if not isinstance(x, dict) or "nome" not in x or "percorso_completo" not in x:
                continue
            chiave = f"{e['nome']}::{x['percorso_completo']}"
            tutte.add(chiave)
            if solo_disco and e["nome"].casefold() != solo_disco.casefold():
                continue
            r = analizza_file(x["nome"], x["percorso_completo"])
            if r:
                film[chiave] = r
    return film, tutte


def da_rifare(scheda):
    """Una scheda TMDB di tipo 'serie' su un file che ora risulta un film va rifatta."""
    return scheda.get("source") == "TMDB" and scheda.get("kind") == "tv"


def main():
    p = argparse.ArgumentParser(description="Aggiunge anno e generi TMDB ai film dei cataloghi.")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--disco")
    p.add_argument("--limite", type=int, default=3000)
    p.add_argument("--rps", type=float, default=5.0)
    p.add_argument("--riprova", action="store_true")
    p.add_argument("--non-potare", action="store_true")
    a = p.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    film, tutte = raccogli(a.disco)
    schede_file = leggi_json(FILE_SCHEDE, {"version": 1, "files": {}})
    schede = schede_file.setdefault("files", {})
    dubbi = leggi_json(FILE_DUBBI, {})
    manuali = leggi_json(FILE_MANUALI, {})

    gruppi = defaultdict(list)
    gia_presenti = 0
    for chiave, (titolo, anno) in film.items():
        if chiave in schede and not da_rifare(schede[chiave]):
            gia_presenti += 1
            continue
        gruppi[chiave_gruppo(titolo, anno)].append((chiave, titolo, anno))
    print(f"Film nei cataloghi: {len(film)} | già con scheda: {gia_presenti} | "
          f"titoli distinti da cercare: {len(gruppi)}")

    if a.dry_run:
        for k, membri in sorted(gruppi.items()):
            print(f"  {membri[0][1]!r} ({membri[0][2] or 'anno?'})  x{len(membri)}")
        return

    token = os.environ.get("TMDB_READ_ACCESS_TOKEN", "").strip()
    chiave_api = os.environ.get("TMDB_API_KEY", "").strip()
    if not (token or chiave_api):
        p.error("Imposta la variabile TMDB_API_KEY (oppure TMDB_READ_ACCESS_TOKEN).")
    cliente = ClienteTMDB(token, chiave_api, a.limite, a.rps)

    riconosciuti = nuovi_dubbi = saltati = 0
    interrotto = None

    def salva():
        scrivi_json(FILE_SCHEDE, schede_file, a_capo=False)
        scrivi_json(FILE_DUBBI, dict(sorted(dubbi.items())))

    try:
        for n, (k, membri) in enumerate(sorted(gruppi.items()), 1):
            titolo, anno = membri[0][1], membri[0][2]
            vecchio = dubbi.get(k)
            if vecchio and not a.riprova and k not in manuali and \
                    (OGGI - date.fromisoformat(vecchio["controllato"])).days < GIORNI_RICONTROLLO:
                saltati += 1
                continue
            if k in manuali:
                risultato, motivo = cliente.get(f"/movie/{int(manuali[k])}"), None
                if not risultato:
                    risultato, motivo = None, "non_trovato"
            else:
                risultato, motivo = scegli(titolo, anno, (cliente.get("/search/movie", {"query": titolo}) or {}).get("results", []))
            if risultato:
                scheda = scheda_da_tmdb(risultato)
                for chiave, _, _ in membri:
                    schede[chiave] = scheda
                dubbi.pop(k, None)
                riconosciuti += 1
            else:
                candidati = []
                if motivo == "dubbio":
                    trovati = (cliente.get("/search/movie", {"query": titolo}) or {}).get("results", []) if False else []
                dubbi[k] = {"stato": motivo, "titolo": titolo, "anno": anno,
                            "file": [c for c, _, _ in membri][:5], "candidati": candidati,
                            "controllato": OGGI.isoformat()}
                nuovi_dubbi += 1
            if n % 25 == 0:
                salva()
                print(f"  {n}/{len(gruppi)} | richieste {cliente.richieste} | riconosciuti {riconosciuti} | dubbi {nuovi_dubbi}", flush=True)
    except LimiteRaggiunto:
        interrotto = "Limite di richieste raggiunto: rilancia per continuare da dove si è fermato."
    except RetiNonDisponibile:
        interrotto = "Rete o TMDB non raggiungibili: rilancia più tardi, il lavoro fatto è salvato."
    except KeyboardInterrupt:
        interrotto = "Interrotto: il lavoro fatto è salvato."

    potate = 0
    if not a.non_potare and not a.disco:
        for chiave in [c for c in schede if c not in tutte]:
            del schede[chiave]
            potate += 1
    salva()
    print(f"Richieste TMDB: {cliente.richieste} | riconosciuti: {riconosciuti} | da verificare: {nuovi_dubbi} | "
          f"saltati (già controllati): {saltati} | schede di file spariti tolte: {potate}")
    if nuovi_dubbi:
        print(f"Dubbi e non trovati in {FILE_DUBBI.relative_to(QUI.parent)}")
    if interrotto:
        print(interrotto)


if __name__ == "__main__":
    main()
