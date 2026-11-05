#!/usr/bin/env python3
"""Aggiunge a data/film_metadata.json le schede TMDB (generi, anno, voto, regista, trama)
dei FILM presenti nei cataloghi di data/.

Cosa considera "film" (stessa logica di app.js, blocco CLASSIFICAZIONE):
  - categoria Film (cartelle FILM, HORROR - THRILLER, ...) o file video nella radice del
    disco con anno nel nome e nessuna traccia di episodi;
  - esclusi gli extra (cartelle Featurettes, EXTRA, BONUS, Deleted Scenes, Interviews...
    e nomi file con trailer, intervista, making of...).
Serie TV e anime non vengono toccati.

Chiave TMDB gratuita: https://www.themoviedb.org/settings/api
(vanno bene sia la "API Key" v3 sia il "Token di lettura API" v4)

Uso, dalla radice del repo:
  set TMDB_API_KEY=la_tua_chiave          (PowerShell:  $env:TMDB_API_KEY="la_tua_chiave")
  python scripts/arricchisci_tmdb.py --dry-run     # solo anteprima, non contatta TMDB
  python scripts/arricchisci_tmdb.py               # crea le schede mancanti
Opzioni: --solo-disco NOME  --limite N  --rifai  --key CHIAVE

Le schede già presenti non vengono toccate (nemmeno quelle corrette a mano), a meno di --rifai,
che comunque non sovrascrive mai le schede con source "Manuale".
Gli abbinamenti incerti vengono salvati con "review": true (il sito mostra "Da verificare")
e elencati in scripts/revisione_tmdb.txt.
"""
import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent
DATA = RADICE / "data"
FILE_SCHEDE = DATA / "film_metadata.json"
FILE_REVISIONE = Path(__file__).resolve().parent / "revisione_tmdb.txt"
ESCLUSI = {"manifest.json", "film_metadata.json"}
ANNO_CORRENTE = 2026

# TMDB (italiano) -> nomi usati dal sito (GENRES in app.js)
MAPPA_GENERI = {"Dramma": "Drammatico", "Romance": "Romantico", "Storia": "Storico", "Musica": "Musicale"}
GENERI_SITO = {"Azione", "Avventura", "Animazione", "Biografico", "Commedia", "Crime", "Documentario",
               "Drammatico", "Famiglia", "Fantasy", "Fantascienza", "Guerra", "Horror", "Musicale",
               "Mistero", "Romantico", "Storico", "Thriller", "Western"}


# ---------------------------------------------------------------- classificazione (come app.js)
def normalizza(testo):
    t = unicodedata.normalize("NFD", str(testo))
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", re.sub(r"[\W_]+", " ", t)).strip()


def categoria(percorso):
    s = normalizza(percorso)
    if re.search(r"\b(anime|animazione|cartoni|naruto|one piece|bleach)\b", s):
        return "Anime e cartoni"
    if re.search(r"\b(serie tv|serie|series|stagione|season)\b", s) or re.search(r"\bs\d{1,2}e\d{1,3}\b", percorso, re.I):
        return "Serie TV"
    if re.search(r"\b(documentari|documentary|documentario|documentaries)\b", s):
        return "Documentari"
    if re.search(r"\b(film|cinema|movies|movie)\b", s) or re.search(r"\b(horror thriller|horror thiller|sci fi fantasy|fantascienza fantasy)\b", s):
        return "Film"
    return "Altro"


EXTRA_DIR = re.compile(r"^(extras?|extra features?|bonus( features| disc| extra)?|special features?|featurettes?|deleted scenes?|scene eliminate|behind the scenes|making of|interviste?|interviews?|contenuti speciali|contenuti extra|extra (contenuti|speciali)|trailers?|backstage|inediti|speciali)( \d+)?$")
EXTRA_FILE = re.compile(r"\b(trailers?|sample|intervist[ae]|interviews?|featurettes?|making of|behind the scenes|deleted scenes?|scene eliminate|backstage)\b")
EPISODIO = re.compile(r"\bs\d{1,3}e\d{1,3}\b|\b\d{1,2}x\d{2,3}\b|\bs\d{1,3}\b|\bstagione\b|\bseason\b", re.I)


def parti_percorso(percorso):
    parti = [p for p in percorso.replace("/", "\\").split("\\") if p]
    if parti and re.match(r"^[A-Za-z]:$", parti[0]):
        parti.pop(0)
    return parti


def anno_nel_nome(nome):
    """Primo anno plausibile nel nome del file (come inferYear di app.js)."""
    anni = [int(m.group(1)) for m in re.finditer(r"(?:^|[^0-9])(19[0-9]{2}|20[0-2][0-9])(?=$|[^0-9])", nome)]
    anni = [a for a in anni if 1900 <= a <= ANNO_CORRENTE]
    return anni[0] if anni else None


def e_extra(nome, parti):
    return any(EXTRA_DIR.match(normalizza(p)) for p in parti[:-1]) or bool(EXTRA_FILE.search(normalizza(nome)))


def e_film(percorso, nome, parti):
    cat = categoria(percorso)
    if cat == "Altro" and len(parti) <= 2 and anno_nel_nome(nome) and not EPISODIO.search(" ".join(parti)):
        cat = "Film"
    return cat == "Film" and not e_extra(nome, parti)


# ---------------------------------------------------------------- titolo e anno dal nome file
TAG_RILASCIO = re.compile(
    r"(?<![A-Za-z0-9])(2160p|1080[pi]?|720p?|576p|480p|4k|uhd|blu-?ray|bdrip|bdmux|brrip|bdremux|remux|dvdrip|dvdmux|dvd|webrip|web-?dl|webmux|hdtv|satrip|hdmux|hdrip|mux|"
    r"x264|x265|h ?26[45]|hevc|avc|xvid|divx|ac3|eac3|dts|aac|ita|eng|multi|sub|subs)(?![A-Za-z0-9])", re.I)
EDIZIONE = re.compile(r"\b(director'?s cut|extended( cut| edition)?|versione (estesa|integrale|restaurata)|special edition|edizione speciale|ultimate [a-z ]*edition|[a-z]+ edition|remaster(ed)?|restaurat[ao]|theatrical( cut)?)\b.*$", re.I)


def pulisci(t):
    if t.count("(") > t.count(")"):                      # "(Castellari," senza chiusura
        t = t[: t.rfind("(")]
    t = re.sub(r"\([^)]*\)", " ", t)
    t = re.sub(r"\[[^\]]*\]", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -–—+,:;()[]")
    return t


def titolo_e_anno(nome):
    base = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", nome)
    base = re.sub(r"[._]+", " ", base)
    tag = TAG_RILASCIO.search(base)                                     # dal primo tag di rilascio in poi è rumore
    testa = base[: tag.start()] if tag and tag.start() > 2 else base
    testa = re.sub(r"\[(?!\s*(?:19|20)\d\d\s*\])[^\]]*\]", " ", testa)  # toglie [gruppo], tiene [1989]
    if testa.count("[") > testa.count("]"):                             # parentesi quadra mai chiusa
        testa = testa[: testa.rfind("[")]
    candidati = [(int(m.group(1)), m.start(1)) for m in
                 re.finditer(r"(?:^|[^0-9])(19[0-9]{2}|20[0-2][0-9])(?=$|[^0-9])", testa)
                 if 1900 <= int(m.group(1)) <= ANNO_CORRENTE]
    if len(candidati) > 1 and candidati[0][1] == 0:                     # "2001 Odissea nello spazio (1968)"
        candidati = candidati[1:]
    anno, titolo = None, None
    if candidati:
        anno, inizio = candidati[-1]
        prima, dopo = pulisci(testa[:inizio]), pulisci(testa[inizio + 4:])
        dopo_trattino = bool(re.search(r"[-–—]\s*$", testa[:inizio]))
        # "Bud Spencer - 1973 - Titolo del film": il titolo è DOPO l'anno
        titolo = dopo if (len(prima) < 2 or (dopo_trattino and len(dopo) >= 3)) else prima
    if not titolo:
        titolo = pulisci(testa)
    return titolo, anno


# ---------------------------------------------------------------- TMDB
class Tmdb:
    def __init__(self, chiave):
        self.v4 = chiave.startswith("eyJ")
        self.chiave = chiave
        self.richieste = 0

    def get(self, percorso, **params):
        params.setdefault("language", "it-IT")
        if not self.v4:
            params["api_key"] = self.chiave
        url = "https://api.themoviedb.org/3" + percorso + "?" + urllib.parse.urlencode(params)
        richiesta = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "vault-listhdd/1.0"})
        if self.v4:
            richiesta.add_header("Authorization", "Bearer " + self.chiave)
        for tentativo in range(5):
            try:
                with urllib.request.urlopen(richiesta, timeout=20) as r:
                    self.richieste += 1
                    time.sleep(0.03)
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    sys.exit("TMDB ha rifiutato la chiave (401): controlla TMDB_API_KEY.")
                if e.code == 429:
                    time.sleep(int(e.headers.get("Retry-After", "2")) + 1)
                    continue
                if e.code == 404:
                    return None
                time.sleep(2 * (tentativo + 1))
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                time.sleep(2 * (tentativo + 1))
        return None

    def cerca(self, titolo, anno):
        tentativi = [(titolo, anno), (titolo, None)]
        corto = re.split(r"\s+[-–—:]\s+", titolo)[0]                 # "Titolo - Altro titolo" -> "Titolo"
        senza_edizione = EDIZIONE.sub("", titolo).strip(" -–—:")     # "Predator 2 Ultimate Hunter Edition" -> "Predator 2"
        for variante in (corto, senza_edizione):
            if len(variante) >= 2 and variante != titolo and (variante, anno) not in tentativi:
                tentativi.append((variante, anno))
        for t, a in tentativi:
            params = {"query": t, "include_adult": "false"}
            if a:
                params["year"] = a
            ris = self.get("/search/movie", **params)
            if ris and ris.get("results"):
                return ris["results"][0]
        return None

    def dettagli(self, tmdb_id):
        return self.get(f"/movie/{tmdb_id}", append_to_response="credits")


def affidabile(titolo, anno, trovato):
    """True se titolo e anno cercati coincidono con quelli del risultato."""
    cercato = normalizza(titolo)
    nomi = {normalizza(trovato.get("title", "")), normalizza(trovato.get("original_title", ""))}
    titolo_ok = cercato in nomi or any(n and (n in cercato or cercato in n) and min(len(n), len(cercato)) >= 5 for n in nomi)
    anno_trovato = int((trovato.get("release_date") or "0000")[:4] or 0)
    anno_ok = anno is None or abs(anno_trovato - anno) <= 1
    return titolo_ok and anno_ok


def scheda_da_tmdb(det):
    generi = []
    for g in det.get("genres", []):
        nome = MAPPA_GENERI.get(g.get("name"), g.get("name"))
        if nome in GENERI_SITO and nome not in generi:
            generi.append(nome)
    crediti = det.get("credits") or {}
    registi = [c["name"] for c in crediti.get("crew", []) if c.get("job") == "Director"][:3]
    cast = [c["name"] for c in crediti.get("cast", [])][:6]
    anno = int((det.get("release_date") or "0")[:4] or 0)
    return {
        "year": anno if 1900 <= anno <= ANNO_CORRENTE else None,
        "genres": generi,
        "source": "TMDB",
        "kind": "movie",
        "tmdb_id": det["id"],
        "title": det.get("title") or det.get("original_title") or "",
        "overview": (det.get("overview") or "").strip(),
        "poster_path": det.get("poster_path") or "",
        "vote_average": round(float(det.get("vote_average") or 0), 1),
        "directors": registi,
        "cast": cast,
        "tmdb_url": f"https://www.themoviedb.org/movie/{det['id']}",
    }


# ---------------------------------------------------------------- main
def carica_film(solo_disco):
    film = []
    for p in sorted(DATA.glob("*.json")):
        if p.name in ESCLUSI:
            continue
        try:
            dati = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not isinstance(dati.get("catalogo_video"), list):
            continue
        disco = p.stem
        if solo_disco and disco.lower() != solo_disco.lower():
            continue
        for f in dati["catalogo_video"]:
            nome, perc = str(f.get("nome", "")), str(f.get("percorso_completo", ""))
            if nome and perc and e_film(perc, nome, parti_percorso(perc)):
                film.append((f"{disco}::{perc}", nome))
    return film


def salva(schede):
    tmp = FILE_SCHEDE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"version": 1, "files": schede}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, FILE_SCHEDE)


def main():
    ap = argparse.ArgumentParser(description="Genera le schede TMDB dei film.")
    ap.add_argument("--key", default=os.environ.get("TMDB_API_KEY", ""))
    ap.add_argument("--dry-run", action="store_true", help="mostra titolo e anno estratti senza contattare TMDB")
    ap.add_argument("--solo-disco", help="elabora solo questo disco (nome del file JSON senza .json)")
    ap.add_argument("--limite", type=int, help="massimo di film da elaborare in questa esecuzione")
    ap.add_argument("--rifai", action="store_true", help="rifà anche i film che hanno già una scheda (tranne quelle Manuale)")
    args = ap.parse_args()

    schede = {}
    if FILE_SCHEDE.exists():
        schede = json.loads(FILE_SCHEDE.read_text(encoding="utf-8")).get("files", {})

    film = carica_film(args.solo_disco)
    da_fare = [(k, n) for k, n in film
               if k not in schede or (args.rifai and schede[k].get("source") != "Manuale")]
    print(f"Film riconosciuti: {len(film)} · già con scheda: {len(film) - len([1 for k, _ in film if k not in schede])} · da elaborare: {len(da_fare)}")
    if args.limite:
        da_fare = da_fare[: args.limite]

    if args.dry_run:
        for chiave, nome in da_fare[:60]:
            t, a = titolo_e_anno(nome)
            print(f"  {a or '----'}  {t!r:50}  <- {nome[:70]}")
        if len(da_fare) > 60:
            print(f"  … e altri {len(da_fare) - 60}")
        return
    if not args.key:
        sys.exit("Manca la chiave TMDB: imposta TMDB_API_KEY oppure usa --key (vedi l'intestazione dello script).")

    tmdb, cache, dubbi, non_trovati, nuovi = Tmdb(args.key), {}, [], [], 0
    try:
        for i, (chiave, nome) in enumerate(da_fare, 1):
            titolo, anno = titolo_e_anno(nome)
            if len(titolo) < 2:
                non_trovati.append(f"{chiave}  (titolo non estraibile)")
                continue
            ck = (normalizza(titolo), anno)
            if ck not in cache:
                trovato = tmdb.cerca(titolo, anno)
                det = tmdb.dettagli(trovato["id"]) if trovato else None
                cache[ck] = (scheda_da_tmdb(det), affidabile(titolo, anno, trovato)) if det else None
            if cache[ck] is None:
                non_trovati.append(f"{chiave}  (cercato: {titolo!r} {anno or ''})")
                continue
            scheda, ok = dict(cache[ck][0]), cache[ck][1]
            if not ok:
                scheda["review"] = True
                dubbi.append(f"{chiave}  ->  {scheda['title']} ({scheda['year']})  [cercato: {titolo!r} {anno or ''}]")
            schede[chiave] = scheda
            nuovi += 1
            if i % 25 == 0:
                salva(schede)
                print(f"  {i}/{len(da_fare)}…")
    except KeyboardInterrupt:
        print("\nInterrotto: salvo quanto fatto finora.")
    salva(schede)

    righe = ["# Abbinamenti incerti (salvati con review=true, il sito li segna 'Da verificare')"] + dubbi \
        + ["", "# Film non trovati su TMDB (nessuna scheda creata)"] + non_trovati
    FILE_REVISIONE.write_text("\n".join(righe) + "\n", encoding="utf-8")
    print(f"\nFatto: {nuovi} schede scritte ({len(dubbi)} da verificare), {len(non_trovati)} non trovati, {tmdb.richieste} richieste a TMDB.")
    print(f"Dettagli in {FILE_REVISIONE.relative_to(RADICE)}. Ora fai commit e push di data/film_metadata.json.")


if __name__ == "__main__":
    main()
