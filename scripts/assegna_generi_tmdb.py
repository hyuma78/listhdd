#!/usr/bin/env python3
"""Assegna i generi ai film con l'aiuto di TMDB: lui SUGGERISCE, tu controlli e confermi.

Cosa fa
  * Legge i JSON in data/ e trova i FILM (extra, interviste, serie TV e anime esclusi:
    stessa logica di app.js, blocco CLASSIFICAZIONE).
  * Per ogni film cerca su TMDB in italiano e mostra titolo trovato, anno, voto e regista.
    L'abbinamento viene segnalato come sicuro o da verificare; i generi suggeriti sono
    evidenziati nella griglia delle categorie.
  * Interfaccia a schermata singola, con colori e comandi principali a tasto singolo.
  * Con M puoi cercare un film già classificato e correggerne i generi.
  * I film che stai guardando vengono cercati in anticipo mentre rispondi, quindi non aspetti.
  * Lo stesso film su più dischi (stesso titolo e anno) viene chiesto una volta sola.
  * Ogni risposta viene salvata SUBITO: se chiudi lo script (q, Ctrl+C, finestra chiusa)
    alla volta dopo riparte dal primo film senza genere.
  * Le schede già presenti con un genere non vengono mai riproposte né sovrascritte.
  * Con  y  su un suggerimento TMDB la scheda salva anche titolo, voto e regista (li mostra il sito);
    con una scelta manuale salva solo anno e generi.

Chiave TMDB gratuita: themoviedb.org > Impostazioni > API (va bene la "API Key" v3 o il token v4).

Uso, dalla radice del repo (nessun pacchetto da installare):
    set TMDB_API_KEY=la_tua_chiave                  (PowerShell:  $env:TMDB_API_KEY="la_tua_chiave")
    python scripts\\assegna_generi_tmdb.py
Opzioni:
    --key CHIAVE       alternativa alla variabile TMDB_API_KEY
    --senza-tmdb       non usa TMDB (suggerisce solo dalle cartelle, come assegna_generi.py)
    --disco NOME       propone solo i film di quel disco (nome del JSON senza .json)
    --rivedi-saltati   ripropone anche i film che avevi saltato con "s"
    --stato            mostra a che punto sei e quanti film ci sono per genere, poi esce

Comandi (un solo tasto, senza Invio)
    y                  accetta i generi suggeriti
    n                  apre il selettore visuale dei generi
    1-9                apre il selettore con quel genere già selezionato
    s                  salta il film per ora
    x                  esclude il film dalla procedura
    m                  modifica i generi di un film già classificato
    a                  corregge l'anno
    t                  cerca un titolo manualmente; anno facoltativo e alternative TMDB
    v                  mostra le alternative della ricerca TMDB corrente
    b                  annulla l'ultima operazione
    ?                  mostra l'aiuto
    q                  salva ed esce

Nel selettore: frecce per muoversi, spazio per selezionare/deselezionare,
 y per salvare, t per inserire testo (es. 1 5 oppure horror comm),
 n o Esc per annullare.
Alla fine: git add data/film_metadata.json, commit e push.
"""
import argparse
from difflib import SequenceMatcher
import json
import os
import queue
import re
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
import shutil

RADICE = Path(__file__).resolve().parent.parent
DATA = RADICE / "data"
FILE_SCHEDE = DATA / "film_metadata.json"
FILE_PROGRESSO = Path(__file__).resolve().parent / ".progresso_generi.json"   # locale, non va su GitHub
ESCLUSI = {"manifest.json", "film_metadata.json", "tv_metadata.json"}
ANNO_CORRENTE = 2026
STORICO_MAX = 100

# (etichetta mostrata, valore salvato: deve coincidere con GENRES di app.js)
GENERI = [
    ("Horror", "Horror"), ("Commedia", "Commedia"), ("Avventura", "Avventura"), ("Poliziesco", "Crime"),
    ("Azione", "Azione"), ("Drammatico", "Drammatico"), ("Fantascienza", "Fantascienza"), ("Fantasy", "Fantasy"),
    ("Thriller", "Thriller"), ("Animazione", "Animazione"), ("Famiglia", "Famiglia"), ("Romantico", "Romantico"),
    ("Guerra", "Guerra"), ("Western", "Western"), ("Storico", "Storico"), ("Biografico", "Biografico"),
    ("Musicale", "Musicale"), ("Mistero", "Mistero"), ("Documentario", "Documentario"),
]
ETICHETTA = {valore: etichetta for etichetta, valore in GENERI}


# ------------------------------------------------ classificazione (come app.js: tenere allineata)
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
RE_ANNO = r"(?:^|[^0-9])(19[0-9]{2}|20[0-2][0-9])(?=$|[^0-9])"


def parti_percorso(percorso):
    parti = [p for p in percorso.replace("/", "\\").split("\\") if p]
    if parti and re.match(r"^[A-Za-z]:$", parti[0]):
        parti.pop(0)
    return parti


def primo_anno(testo):
    anni = [int(m.group(1)) for m in re.finditer(RE_ANNO, testo)]
    anni = [a for a in anni if 1900 <= a <= ANNO_CORRENTE]
    return anni[0] if anni else None


def anno_come_app(nome, percorso):
    """Stesso anno che mostra il sito: quello nel nome file, altrimenti quello della cartella che lo contiene."""
    own = primo_anno(nome)
    if own:
        return own
    cartelle = percorso.replace("\\", "/").split("/")[1:-1]
    return primo_anno(cartelle[-1]) if cartelle else None


def e_extra(nome, parti):
    return any(EXTRA_DIR.match(normalizza(p)) for p in parti[:-1]) or bool(EXTRA_FILE.search(normalizza(nome)))


def e_film(percorso, nome, parti):
    cat = categoria(percorso)
    if cat == "Altro" and len(parti) <= 2 and primo_anno(nome) and not EPISODIO.search(" ".join(parti)):
        cat = "Film"
    return cat == "Film" and not e_extra(nome, parti)


def generi_da_cartelle(percorso):
    """Suggerimento: stesse regole di inferGenres in app.js (cartelle organizzate per genere)."""
    trovati = []
    def agg(g):
        if g not in trovati:
            trovati.append(g)
    for d in (normalizza(x) for x in percorso.replace("\\", "/").split("/")[1:-1]):
        if re.match(r"^(film )?horror( thriller| thiller)?$", d) or d == "horror thriller":
            agg("Horror")
            if "thriller" in d or "thiller" in d:
                agg("Thriller")
        if re.match(r"^(film )?thriller$", d):
            agg("Thriller")
        if re.match(r"^(sci fi fantasy|fantascienza fantasy|fantascienza|sci fi)$", d):
            agg("Fantascienza")
            if "fantasy" in d:
                agg("Fantasy")
        if re.match(r"^(film )?fantasy$", d):
            agg("Fantasy")
        if re.match(r"^(film )?(commedia|commedie)$", d):
            agg("Commedia")
        if re.match(r"^(film )?western$", d):
            agg("Western")
        if re.match(r"^(film )?drammatici?$", d):
            agg("Drammatico")
        if re.match(r"^(film )?avventura$", d):
            agg("Avventura")
    return trovati


# ------------------------------------------------ titolo dal nome del file (serve per mostrare e raggruppare)
TAG_RILASCIO = re.compile(
    r"(?<![A-Za-z0-9])(2160p|1080[pi]?|720p?|576p|480p|4k|uhd|blu-?ray|bdrip|bdmux|brrip|bdremux|remux|dvdrip|dvdmux|dvd|webrip|web-?dl|webmux|hdtv|satrip|hdmux|hdrip|mux|"
    r"x264|x265|h ?26[45]|hevc|avc|xvid|divx|ac3|eac3|dts|aac|ita|italian|eng|english|multi|sub|subs)(?![A-Za-z0-9])", re.I)
EDIZIONE = re.compile(r"\b(director'?s cut|extended( cut| edition)?|versione (estesa|integrale|restaurata)|special edition|edizione speciale|ultimate [a-z ]*edition|[a-z]+ edition|remaster(ed)?|restaurat[ao]|theatrical( cut)?)\b.*$", re.I)
DURATA_INIZIO = re.compile(
    r"^\s*(?:durata\s*)?(?:\d{1,3}\s*(?:min(?:uti)?|minutes?)\b|"
    r"\d{1,3}\s*m\b|\d{1,2}\s*h(?:\s*\d{1,2}\s*m)?\b)", re.I)


def pulisci(t):
    if t.count("(") > t.count(")"):
        t = t[: t.rfind("(")]
    t = re.sub(r"\([^)]*\)", " ", t)
    t = re.sub(r"\[[^\]]*\]", " ", t)
    return re.sub(r"\s+", " ", t).strip(" -–—+,:;()[]")


def titolo_e_anno(nome):
    """Estrae il titolo ignorando i metadati tecnici e le durate dopo l'anno."""
    base = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", nome)
    base = re.sub(r"[._]+", " ", base)
    tag = TAG_RILASCIO.search(base)
    testa = base[: tag.start()] if tag and tag.start() > 2 else base
    testa = re.sub(r"\[(?!\s*(?:19|20)\d\d\s*\])[^\]]*\]", " ", testa)
    if testa.count("[") > testa.count("]"):
        testa = testa[: testa.rfind("[")]
    cand = [(int(m.group(1)), m.start(1)) for m in re.finditer(RE_ANNO, testa)
            if 1900 <= int(m.group(1)) <= ANNO_CORRENTE]
    if len(cand) > 1 and cand[0][1] == 0:
        cand = cand[1:]

    titolo = None
    if cand:
        inizio = cand[-1][1]
        prima = pulisci(testa[:inizio])
        dopo = pulisci(testa[inizio + 4:])
        # Una durata subito dopo l'anno (es. "2006 - 94 min") è un metadato,
        # non un titolo: in questo caso prevale sempre la parte precedente all'anno.
        dopo_e_durata = bool(DURATA_INIZIO.match(dopo))
        dopo_trattino = bool(re.search(r"[-–—]\s*$", testa[:inizio]))
        if dopo_e_durata:
            titolo = prima
        else:
            titolo = dopo if (len(prima) < 2 or (dopo_trattino and len(dopo) >= 3)) else prima
    return titolo or pulisci(testa) or nome


# ------------------------------------------------ TMDB
MAPPA_GENERI_TMDB = {"Dramma": "Drammatico", "Romance": "Romantico", "Storia": "Storico", "Musica": "Musicale"}
GENERI_SITO = {valore for _, valore in GENERI}


class Tmdb:
    def __init__(self, chiave):
        self.v4 = chiave.startswith("eyJ")
        self.chiave = chiave

    def get(self, percorso, **params):
        params.setdefault("language", "it-IT")
        if not self.v4:
            params["api_key"] = self.chiave
        url = "https://api.themoviedb.org/3" + percorso + "?" + urllib.parse.urlencode(params)
        richiesta = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "vault-listhdd/1.0"})
        if self.v4:
            richiesta.add_header("Authorization", "Bearer " + self.chiave)
        for tentativo in range(4):
            try:
                with urllib.request.urlopen(richiesta, timeout=15) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code in (401, 404):
                    return None
                if e.code == 429:
                    time.sleep(int(e.headers.get("Retry-After", "2")) + 1)
                    continue
                time.sleep(1 + tentativo)
            except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError):
                time.sleep(1 + tentativo)
        return None


def similarita_titolo(titolo, trovato):
    """Restituisce la migliore similarità tra titolo cercato e titoli TMDB."""
    cercato = normalizza(titolo)
    if not cercato:
        return 0.0
    nomi = [normalizza(trovato.get("title", "")), normalizza(trovato.get("original_title", ""))]
    punteggi = []
    parole_cercate = set(cercato.split())
    for nome in nomi:
        if not nome:
            continue
        if nome == cercato:
            return 1.0
        rapporto = SequenceMatcher(None, cercato, nome).ratio()
        parole_nome = set(nome.split())
        copertura = len(parole_cercate & parole_nome) / max(1, len(parole_cercate | parole_nome))
        punteggi.append(max(rapporto, copertura))
    return max(punteggi, default=0.0)


def affidabile(titolo, anno, trovato):
    """True solo con titolo uguale/molto simile e anno compatibile."""
    titolo_ok = similarita_titolo(titolo, trovato) >= 0.88
    data = str(trovato.get("release_date") or "")
    try:
        anno_trovato = int(data[:4])
    except (TypeError, ValueError):
        anno_trovato = 0
    anno_ok = anno is None or (anno_trovato > 0 and abs(anno_trovato - anno) <= 1)
    return titolo_ok and anno_ok


def cerca_candidati_tmdb(tmdb, titolo, anno=None):
    """Recupera candidati multipli da TMDB, anche senza anno, eliminando i duplicati."""
    titolo = str(titolo or "").strip()
    if not titolo:
        return []

    tentativi = []
    def aggiungi(query, anno_query=None):
        query = str(query or "").strip()
        voce = (query, anno_query)
        if len(query) >= 2 and voce not in tentativi:
            tentativi.append(voce)

    # Prima la ricerca più precisa; se l'anno è noto, cerca comunque anche senza filtro.
    aggiungi(titolo, anno)
    if anno:
        aggiungi(titolo, None)
    corto = re.split(r"\s+[-–—:]\s+", titolo)[0]
    senza_edizione = EDIZIONE.sub("", titolo).strip(" -–—:")
    for variante in (corto, senza_edizione):
        if variante != titolo:
            aggiungi(variante, anno)
            if anno:
                aggiungi(variante, None)

    candidati = {}
    for query, anno_query in tentativi:
        parametri = {"query": query, "include_adult": "false"}
        if anno_query:
            parametri["year"] = anno_query
        ris = tmdb.get("/search/movie", page=1, **parametri)
        if not ris:
            continue
        pagine = [ris]
        # Per la query completa carica anche la seconda pagina quando esiste:
        # il selettore può così mostrare più alternative senza altre azioni.
        try:
            altre_pagine = int(ris.get("total_pages") or 1)
        except (TypeError, ValueError):
            altre_pagine = 1
        if query == titolo and altre_pagine > 1:
            seconda = tmdb.get("/search/movie", page=2, **parametri)
            if seconda:
                pagine.append(seconda)
        for pagina_risultati in pagine:
            for risultato in pagina_risultati.get("results", [])[:20]:
                if not risultato.get("id"):
                    continue
                candidato = dict(risultato)
                candidato["_similarita"] = similarita_titolo(titolo, candidato)
                data = str(candidato.get("release_date") or "")
                try:
                    anno_ris = int(data[:4])
                except (TypeError, ValueError):
                    anno_ris = 0
                candidato["_anno_punti"] = (
                    2 if anno and anno_ris == anno else
                    1 if anno and anno_ris and abs(anno_ris - anno) <= 1 else
                    0 if not anno else -1
                )
                precedente = candidati.get(candidato["id"])
                chiave = (candidato["_similarita"], candidato["_anno_punti"],
                          float(candidato.get("popularity") or 0))
                chiave_precedente = ((precedente["_similarita"], precedente["_anno_punti"],
                                      float(precedente.get("popularity") or 0)) if precedente else None)
                if precedente is None or chiave > chiave_precedente:
                    candidati[candidato["id"]] = candidato

    ordinati = sorted(
        candidati.values(),
        key=lambda x: (x["_similarita"], x["_anno_punti"],
                       float(x.get("popularity") or 0), int(x.get("vote_count") or 0)),
        reverse=True,
    )
    return ordinati[:60]


def dettaglio_candidato_tmdb(tmdb, trovato, titolo_cercato, anno_cercato=None,
                              confermato_manualmente=False, candidati=None):
    """Carica la scheda completa di un candidato TMDB selezionato."""
    det = tmdb.get(f"/movie/{trovato['id']}", append_to_response="credits")
    if not det:
        return None

    sicuro = bool(confermato_manualmente or affidabile(titolo_cercato, anno_cercato, det))
    generi = []
    # Una selezione esplicita dell'utente vale come conferma dell'identità del film.
    # I risultati automatici, invece, devono superare il controllo di titolo/anno.
    if sicuro:
        for genere in det.get("genres", []):
            nome = MAPPA_GENERI_TMDB.get(genere.get("name"), genere.get("name"))
            if nome in GENERI_SITO and nome not in generi:
                generi.append(nome)

    crediti = det.get("credits") or {}
    registi = [c["name"] for c in crediti.get("crew", []) if c.get("job") == "Director"][:3]
    try:
        anno_tmdb = int((det.get("release_date") or "0")[:4] or 0)
    except (TypeError, ValueError):
        anno_tmdb = 0
    scheda = {
        "year": anno_tmdb if 1900 <= anno_tmdb <= ANNO_CORRENTE else None,
        "genres": generi,
        "source": "TMDB",
        "kind": "movie",
        "tmdb_id": det["id"],
        "title": det.get("title") or det.get("original_title") or "",
        "overview": (det.get("overview") or "").strip(),
        "poster_path": det.get("poster_path") or "",
        "vote_average": round(float(det.get("vote_average") or 0), 1),
        "directors": registi,
        "cast": [c["name"] for c in crediti.get("cast", [])][:6],
        "tmdb_url": f"https://www.themoviedb.org/movie/{det['id']}",
    }
    return {
        "titolo": scheda["title"],
        "anno": scheda["year"],
        "generi": generi,
        "registi": registi,
        "voto": scheda["vote_average"],
        "sicuro": sicuro,
        "scheda": scheda,
        "candidati": candidati or [],
        "query_titolo": titolo_cercato,
        "query_anno": anno_cercato,
        "scelta_manuale": bool(confermato_manualmente),
    }


def cerca_su_tmdb(tmdb, titolo, anno):
    """Cerca candidati multipli e prepara il primo; le alternative restano selezionabili."""
    candidati = cerca_candidati_tmdb(tmdb, titolo, anno)
    if not candidati:
        return None
    return dettaglio_candidato_tmdb(tmdb, candidati[0], titolo, anno, False, candidati)


def seleziona_candidato_tmdb(candidati, titolo, anno=None):
    """Mostra candidati TMDB in pagine da otto; restituisce il candidato scelto o un comando."""
    if not candidati:
        return None
    per_pagina = 8
    pagina = 0
    totale_pagine = max(1, (len(candidati) + per_pagina - 1) // per_pagina)
    while True:
        pulisci_schermo()
        print(colora("╭─ SCEGLI IL FILM CORRETTO SU TMDB", "cyan", "bold"))
        print(colora("─" * _larghezza_terminale(), "cyan"))
        print("Ricerca: " + colora(titolo, "white", "bold") +
              (f"  |  anno filtro: {anno}" if anno else "  |  tutti gli anni"))
        print(f"Risultati: {len(candidati)}   |   pagina {pagina + 1}/{totale_pagine}")
        print()
        inizio = pagina * per_pagina
        fine = min(len(candidati), inizio + per_pagina)
        for pos in range(inizio, fine):
            c = candidati[pos]
            nome = c.get("title") or c.get("original_title") or "Titolo sconosciuto"
            originale = c.get("original_title") or ""
            try:
                anno_c = int(str(c.get("release_date") or "")[:4])
            except (TypeError, ValueError):
                anno_c = None
            voto = float(c.get("vote_average") or 0)
            similarita = int(round(float(c.get("_similarita", 0)) * 100))
            riga = f"{pos - inizio + 1:>2}. {nome} ({anno_c or '?'})"
            if originale and normalizza(originale) != normalizza(nome):
                riga += f"  [orig. {originale}]"
            riga += f"   voto {voto:.1f}   titolo {similarita}%"
            print("  " + colora(riga, "green", "bold") if pos == inizio else "  " + riga)
            overview = re.sub(r"\s+", " ", str(c.get("overview") or "")).strip()
            if overview:
                print("      " + colora(_accorcia_percorso(overview, max(35, _larghezza_terminale() - 10)), "gray"))
        print()
        print(colora("[1-8] scegli   [0] altri risultati   [9] pagina precedente", "yellow", "bold"))
        print(colora("[T] nuova ricerca   [ESC] torna al film", "yellow", "bold"))
        tasto = leggi_tasto()
        if tasto == "CTRL_C":
            raise KeyboardInterrupt
        if tasto == "ESC" or tasto.lower() == "q":
            return None
        if tasto.lower() == "t":
            return "NUOVA_RICERCA"
        if tasto == "0" and pagina + 1 < totale_pagine:
            pagina += 1
            continue
        if tasto == "9" and pagina > 0:
            pagina -= 1
            continue
        if tasto.isdigit() and "1" <= tasto <= "8":
            indice = inizio + int(tasto) - 1
            if indice < fine:
                return candidati[indice]


def ricerca_interattiva_tmdb(tmdb, titolo, anno, candidati=None):
    """Permette di scegliere un candidato o rifare la ricerca con titolo/anno facoltativo."""
    while True:
        if candidati is None:
            pulisci_schermo()
            print(colora(f"RICERCA TMDB: {titolo}", "cyan", "bold"))
            print("Anno facoltativo: " + (str(anno) if anno else "nessun filtro"))
            print(colora("Ricerca in corso…", "gray"))
            candidati = cerca_candidati_tmdb(tmdb, titolo, anno)
        if not candidati:
            pulisci_schermo()
            print(colora(f"Nessun risultato TMDB per: {titolo}", "yellow", "bold"))
            print("Premi T per provare un altro titolo oppure ESC per tornare al film.")
            tasto = leggi_tasto()
            if tasto == "CTRL_C":
                raise KeyboardInterrupt
            if tasto.lower() != "t":
                return None
            nuovo_titolo = input("Nuovo titolo (Invio = annulla): ").strip()
            if not nuovo_titolo:
                return None
            nuovo_anno = input("Anno facoltativo (Invio = cerca in tutti gli anni): ").strip()
            if nuovo_anno:
                if not (nuovo_anno.isdigit() and 1900 <= int(nuovo_anno) <= ANNO_CORRENTE):
                    print(colora("Anno non valido.", "red"))
                    aspetta_tasto()
                    continue
                anno = int(nuovo_anno)
            else:
                anno = None
            titolo = nuovo_titolo
            candidati = None
        else:
            scelto = seleziona_candidato_tmdb(candidati, titolo, anno)
            if scelto is None:
                return None
            if scelto == "NUOVA_RICERCA":
                pulisci_schermo()
                print(colora("NUOVA RICERCA TMDB", "cyan", "bold"))
                nuovo_titolo = input("Titolo (Invio = annulla): ").strip()
                if not nuovo_titolo:
                    return None
                nuovo_anno = input("Anno facoltativo (Invio = cerca in tutti gli anni): ").strip()
                if nuovo_anno:
                    if not (nuovo_anno.isdigit() and 1900 <= int(nuovo_anno) <= ANNO_CORRENTE):
                        print(colora("Anno non valido.", "red"))
                        aspetta_tasto()
                        continue
                    anno = int(nuovo_anno)
                else:
                    anno = None
                titolo = nuovo_titolo
                candidati = None
                continue
            risultato = dettaglio_candidato_tmdb(
                tmdb, scelto, titolo, anno, confermato_manualmente=True, candidati=candidati
            )
            if risultato is None:
                pulisci_schermo()
                print(colora("TMDB non ha restituito la scheda completa del film selezionato.", "red"))
                aspetta_tasto()
                return None
            return risultato


class Suggeritore:
    """Cerca su TMDB in background i film che stanno per arrivare, così non aspetti dopo ogni risposta."""
    def __init__(self, tmdb, lavoratori=2):
        self.tmdb, self.coda, self.esiti, self.lock = tmdb, queue.Queue(), {}, threading.Lock()
        for _ in range(lavoratori):
            threading.Thread(target=self._lavora, daemon=True).start()

    def _lavora(self):
        while True:
            g = self.coda.get()
            try:
                dati = cerca_su_tmdb(self.tmdb, g["titolo"], g["anno_originale"])
            except Exception:
                dati = None
            self.esiti[g["id"]]["dati"] = dati
            self.esiti[g["id"]]["pronto"].set()

    def richiedi(self, g):
        with self.lock:
            if g["id"] in self.esiti:
                return
            self.esiti[g["id"]] = {"dati": None, "pronto": threading.Event()}
        self.coda.put(g)

    def ottieni(self, g):
        self.richiedi(g)
        e = self.esiti[g["id"]]
        if not e["pronto"].is_set():
            print("  (cerco su TMDB…)", flush=True)
            while not e["pronto"].wait(0.3):      # a piccoli passi: Ctrl+C funziona anche su Windows
                pass
        return e["dati"]


def costruisci_suggerimento(esistenti, esito, g, usa_tmdb):
    cartelle = generi_da_cartelle(g["copie"][0]["percorso"])
    if esistenti:
        return {"generi": esistenti, "origine": "scheda esistente", "tmdb": esito, "cerca": usa_tmdb}
    if esito and esito.get("sicuro") and esito.get("generi"):
        return {"generi": esito["generi"], "origine": "TMDB", "tmdb": esito, "cerca": usa_tmdb}
    # Un candidato TMDB non verificato si mostra, ma non può fornire generi da accettare.
    return {"generi": cartelle, "origine": "cartella", "tmdb": esito, "cerca": usa_tmdb}


# ------------------------------------------------ dati su disco
def leggi_json(percorso, predefinito):
    try:
        return json.loads(percorso.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return predefinito


def scrivi_json(percorso, dati):
    tmp = percorso.with_name(percorso.name + ".tmp")
    tmp.write_text(json.dumps(dati, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, percorso)


def carica_film(solo_disco):
    """Lista di film: {key, nome, percorso, disco, dim}."""
    film = []
    for p in sorted(DATA.glob("*.json")):
        if p.name in ESCLUSI:
            continue
        dati = leggi_json(p, {})
        if not isinstance(dati.get("catalogo_video"), list):
            continue
        if solo_disco and p.stem.lower() != solo_disco.lower():
            continue
        for f in dati["catalogo_video"]:
            nome, perc = str(f.get("nome", "")), str(f.get("percorso_completo", ""))
            parti = parti_percorso(perc)
            if nome and perc and e_film(perc, nome, parti):
                # Il campo "nome" del catalogo può essere già abbreviato/alterato
                # (es. "94 min (2006)"). Il basename del percorso completo conserva
                # normalmente il nome reale del file ed è quindi la fonte primaria.
                nome_file = parti[-1] if parti else nome
                titolo_file = titolo_e_anno(nome_file)
                titolo_catalogo = titolo_e_anno(nome)
                titolo = titolo_file if titolo_file and len(normalizza(titolo_file)) >= 2 else titolo_catalogo
                film.append({"key": f"{p.stem}::{perc}", "nome": nome, "percorso": perc, "disco": p.stem,
                             "dim": str(f.get("dimensione", "")), "titolo": titolo})
    return film


def raggruppa(film):
    """Stesso titolo e stesso anno = stesso film su più dischi."""
    gruppi = {}
    for f in film:
        anno = anno_come_app(f["nome"], f["percorso"])
        gid = f"{normalizza(EDIZIONE.sub('', f['titolo']))}|{anno or ''}"
        g = gruppi.setdefault(gid, {"id": gid, "titolo": f["titolo"], "anno": anno, "anno_originale": anno, "copie": []})
        g["copie"].append(f)
    return sorted(gruppi.values(), key=lambda g: (normalizza(g["titolo"]), g["anno"] or 0))


# ------------------------------------------------ interazione: terminale a tasti singoli
COLORI = {}


def attiva_colori():
    """Abilita ANSI, compreso il terminale Windows, senza dipendenze esterne."""
    global COLORI
    if os.environ.get("NO_COLOR") or not getattr(sys.stdout, "isatty", lambda: False)():
        COLORI = {}
        return
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            modo = ctypes.c_uint()
            if kernel32.GetConsoleMode(handle, ctypes.byref(modo)):
                kernel32.SetConsoleMode(handle, modo.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        except Exception:
            pass
    COLORI = {
        "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
        "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m",
        "blue": "\033[34m", "magenta": "\033[35m", "cyan": "\033[36m",
        "white": "\033[97m", "black": "\033[30m", "gray": "\033[90m", "bg_blue": "\033[44m",
        "bg_green": "\033[42m", "bg_yellow": "\033[43m", "bg_cyan": "\033[46m",
    }


def colora(testo, *stili):
    codici = [COLORI[s] for s in stili if s in COLORI]
    if not codici:
        return str(testo)
    return "".join(codici) + str(testo) + COLORI["reset"]


def pulisci_schermo():
    if getattr(sys.stdout, "isatty", lambda: False)():
        print("\033[2J\033[H", end="", flush=True)
    else:
        print("\n" + "=" * 72)


def leggi_tasto():
    """Legge un singolo tasto senza attendere Invio. Restituisce nomi per i tasti speciali."""
    if not getattr(sys.stdin, "isatty", lambda: False)():
        riga = sys.stdin.readline()
        return riga[:1] if riga else "CTRL_C"
    if os.name == "nt":
        import msvcrt
        tasto = msvcrt.getwch()
        if tasto in ("\x00", "\xe0"):
            speciale = msvcrt.getwch()
            return {"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT",
                    "I": "PAGEUP", "Q": "PAGEDOWN", "G": "HOME", "O": "END"}.get(speciale, "")
        if tasto == "\x03":
            return "CTRL_C"
        if tasto == "\r":
            return "ENTER"
        if tasto == "\x1b":
            return "ESC"
        return tasto

    import select
    import termios
    import tty
    fd = sys.stdin.fileno()
    precedente = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        tasto = os.read(fd, 1).decode(sys.stdin.encoding or "utf-8", errors="replace")
        if tasto == "\x03":
            return "CTRL_C"
        if tasto in ("\r", "\n"):
            return "ENTER"
        if tasto == "\x1b":
            sequenza = ""
            # Le sequenze freccia arrivano subito dopo ESC; ESC da solo resta un comando.
            while select.select([fd], [], [], 0.025)[0] and len(sequenza) < 4:
                sequenza += os.read(fd, 1).decode(sys.stdin.encoding or "utf-8", errors="replace")
            return {"[A": "UP", "[B": "DOWN", "[D": "LEFT", "[C": "RIGHT",
                    "[5~": "PAGEUP", "[6~": "PAGEDOWN", "[H": "HOME", "[F": "END"}.get(sequenza, "ESC")
        return tasto
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, precedente)


def aspetta_tasto(messaggio="Premi un tasto per continuare…"):
    print("\n" + colora(messaggio, "dim"), end="", flush=True)
    try:
        leggi_tasto()
    except (EOFError, OSError):
        pass


def interpreta(testo):
    """Converte numeri, nomi completi o prefissi non ambigui in valori genere.

    Esempi: '1 5', '1,5', 'horror comm', 'Crime'. I duplicati vengono eliminati.
    Un prefisso ambiguo (per es. 'fant') viene rifiutato invece di scegliere a caso.
    """
    testo = str(testo or "").strip()
    if not testo:
        raise ValueError("Seleziona almeno un genere.")

    # Si accettano separatori comuni; le etichette dei generi sono singole parole.
    token = [x for x in re.split(r"[\s,;+/|]+", testo) if x]
    scelti = []
    for tok in token:
        normalizzato = normalizza(tok)
        if not normalizzato:
            continue
        if tok.isdigit():
            n = int(tok)
            if not 1 <= n <= len(GENERI):
                raise ValueError(f"Il numero {tok} non è valido: usa un valore da 1 a {len(GENERI)}.")
            valore = GENERI[n - 1][1]
        else:
            # Prima corrispondenza esatta, includendo sia l'etichetta mostrata sia il valore salvato.
            esatti = {v for e, v in GENERI if normalizzato in (normalizza(e), normalizza(v))}
            if len(esatti) == 1:
                valore = next(iter(esatti))
            else:
                trovati = {v for e, v in GENERI
                           if normalizza(e).startswith(normalizzato) or normalizza(v).startswith(normalizzato)}
                if len(trovati) != 1:
                    if trovati:
                        nomi = ", ".join(ETICHETTA.get(v, v) for v in sorted(trovati))
                        raise ValueError(f"'{tok}' è ambiguo ({nomi}). Scrivi più lettere o il numero.")
                    raise ValueError(f"'{tok}' non corrisponde a nessun genere.")
                valore = next(iter(trovati))
        if valore not in scelti:
            scelti.append(valore)

    if not scelti:
        raise ValueError("Seleziona almeno un genere.")
    return scelti


def _nome_generi(generi):
    return ", ".join(ETICHETTA.get(x, x) for x in generi) if generi else "Nessun genere"


def _larghezza_terminale():
    """Larghezza utile del terminale, limitata per evitare schermate troppo larghe."""
    try:
        colonne = shutil.get_terminal_size((100, 28)).columns
    except OSError:
        colonne = 100
    return max(64, min(colonne, 120))


def _barra_progresso(numero, totale, completati, saltati):
    """Riga superiore con posizione corrente, avanzamento effettivo e saltati."""
    totale = max(1, totale)
    percentuale = int(round(100 * completati / totale))
    lunghezza = max(18, min(36, _larghezza_terminale() - 62))
    pieni = min(lunghezza, int(round(lunghezza * percentuale / 100)))
    barra = colora("█" * pieni, "green", "bold") + colora("·" * (lunghezza - pieni), "gray")
    etichetta = colora(f" FILM {numero} / {totale} ", "bg_cyan", "black", "bold")
    print(f"{etichetta} {barra} {percentuale:>3}%    {colora(f'Saltati: {saltati}', 'gray')}")


def _accorcia_percorso(percorso, limite):
    percorso = str(percorso)
    if len(percorso) <= limite:
        return percorso
    if limite <= 5:
        return percorso[:limite]
    return "…" + percorso[-(limite - 1):]


def _stampa_lista_generi(suggeriti=(), selezionati=(), cursore=None):
    """Stampa i 19 generi in quattro colonne; i suggeriti sono evidenziati in verde."""
    suggeriti, selezionati = set(suggeriti or []), set(selezionati or [])
    colonne = 4
    larghezza = max(15, (_larghezza_terminale() - 6) // colonne)
    for r in range((len(GENERI) + colonne - 1) // colonne):
        celle = []
        for c in range(colonne):
            i = r * colonne + c
            if i >= len(GENERI):
                celle.append("".ljust(larghezza))
                continue
            etichetta, valore = GENERI[i]
            testo = f"{i + 1:>2} {etichetta}".ljust(larghezza)
            if cursore == i:
                testo = colora("› " + f"{i + 1:>2} {etichetta}".ljust(larghezza - 2), "bg_blue", "white", "bold")
            elif valore in selezionati:
                testo = colora("✓ " + f"{i + 1:>2} {etichetta}".ljust(larghezza - 2), "green", "bold")
            elif valore in suggeriti:
                testo = colora(testo, "bg_green", "black", "bold")
            celle.append(testo)
        print("  " + "".join(celle).rstrip())


def mostra_gruppo(g, n, totale, completati, saltati, sugg, ultimo_status=""):
    """Schermata principale: barra in alto, ultimo salvataggio, film, TMDB e generi."""
    pulisci_schermo()
    larghezza = _larghezza_terminale()
    _barra_progresso(n, totale, completati, saltati)
    if ultimo_status:
        colore_status = "green" if ultimo_status.startswith("OK") else "yellow"
        print(colora(ultimo_status, colore_status, "bold"))
    print(colora("─" * larghezza, "gray"))

    titolo = g["titolo"]
    anno = g["anno"] or "anno ?"
    copie = g["copie"]
    copie_label = colora(f"su {len(copie)} dischi", "magenta", "bold") if len(copie) > 1 else ""
    print("  " + colora(titolo, "white", "bold") + "  " + colora(f"({anno})", "cyan", "bold") +
          ("   " + copie_label if copie_label else "") +
          ("   " + colora("titolo corretto", "yellow") if g.get("titolo_manuale") else ""))

    limite_percorso = max(32, larghezza - 8)
    for copia in copie[:4]:
        riga = "  " + colora(copia["disco"], "magenta")
        if copia.get("dim"):
            riga += "  " + colora(copia["dim"], "gray")
        print(riga)
        print("  " + colora(_accorcia_percorso(copia["percorso"], limite_percorso), "gray"))
    if len(copie) > 4:
        print(colora(f"  … altre {len(copie) - 4} copie", "dim"))

    print()
    t = sugg.get("tmdb")
    if t:
        stato_tmdb = colora(" OK ", "bg_green", "black", "bold") if t["sicuro"] else colora(" VERIFICA ", "bg_yellow", "black", "bold")
        dati = f"  {colora('TMDB', 'cyan', 'bold')}  «{t['titolo']}» ({t['anno'] or '?'})  {stato_tmdb}"
        if t.get("voto"):
            dati += colora(f"  voto {t['voto']}", "gray")
        if t.get("registi"):
            dati += colora(f"  regia {', '.join(t['registi'][:2])}", "gray")
        numero_alternative = len(t.get("candidati") or [])
        if numero_alternative > 1:
            dati += colora(f"  |  {numero_alternative} risultati [V] alternative", "yellow", "bold")
        print(dati)
    elif sugg.get("cerca"):
        print("  " + colora("TMDB: nessun risultato per questo titolo", "yellow"))
    else:
        print("  " + colora("TMDB: ricerca disattivata", "dim"))

    generi_suggeriti = sugg.get("generi", ())
    if generi_suggeriti:
        origine = sugg.get("origine", "TMDB")
        print("  " + colora("Suggerito", "green", "bold") + "  " +
              colora(" + ".join(ETICHETTA.get(x, x) for x in generi_suggeriti), "green", "bold") +
              colora(f"  ({origine})", "gray"))
    else:
        print("  " + colora("Nessun genere suggerito", "yellow", "bold"))

    print()
    _stampa_lista_generi(suggeriti=generi_suggeriti)
    print(colora("─" * larghezza, "gray"))
    print(colora("[Y]", "green", "bold") + " accetta   " +
          colora("[N]", "yellow", "bold") + " scelgo io   " +
          colora("[1-9]", "cyan", "bold") + " avvia scelta col genere selezionato")
    print(colora("[S]", "white", "bold") + " salta   " +
          colora("[X]", "red", "bold") + " ignora   " +
          colora("[A]", "white", "bold") + " anno   " +
          colora("[T]", "yellow", "bold") + " correggi titolo / TMDB")
    print(colora("[B]", "white", "bold") + " annulla ultimo   " +
          colora("[M]", "magenta", "bold") + " modifica classificato   " +
          colora("[V]", "cyan", "bold") + " alternative TMDB   " +
          colora("[?]", "white", "bold") + " aiuto   " +
          colora("[Q]", "white", "bold") + " esci")


def seleziona_generi(g, iniziali=None, intestazione="SCELTA GENERI"):
    """Selettore visuale a tasti: frecce, spazio, Y conferma, N/ESC annulla."""
    selezionati = set(iniziali or [])
    indice = 0
    while True:
        pulisci_schermo()
        print(colora(f"╭─ {intestazione}", "cyan", "bold"))
        print(colora("─" * 72, "cyan"))
        print("Film: " + colora(g["titolo"], "white", "bold") + f" ({g['anno'] or '?'})")
        print("\nSelezione corrente: " + colora(_nome_generi([v for _, v in GENERI if v in selezionati]), "green", "bold"))
        print("\n" + colora("FRECCE", "cyan", "bold") + " sposta   " +
              colora("SPAZIO", "cyan", "bold") + " seleziona/deseleziona   " +
              colora("Y", "green", "bold") + " salva   " +
              colora("T", "yellow", "bold") + " inserisci testo   " +
              colora("N / ESC", "red", "bold") + " annulla")
        print(colora("─" * 72, "cyan"))
        _stampa_lista_generi(selezionati=selezionati, cursore=indice)
        print()
        tasto = leggi_tasto()
        if tasto == "CTRL_C":
            raise KeyboardInterrupt
        if tasto == "UP":
            indice = (indice - 4) % len(GENERI)
        elif tasto == "DOWN":
            indice = (indice + 4) % len(GENERI)
        elif tasto == "LEFT":
            indice = (indice - 1) % len(GENERI)
        elif tasto == "RIGHT":
            indice = (indice + 1) % len(GENERI)
        elif tasto == "HOME":
            indice = 0
        elif tasto == "END":
            indice = len(GENERI) - 1
        elif tasto == " ":
            valore = GENERI[indice][1]
            if valore in selezionati:
                selezionati.remove(valore)
            else:
                selezionati.add(valore)
        elif tasto.lower() == "c":
            selezionati.clear()
        elif tasto.lower() == "t":
            print("\nGeneri (es. 1 5 oppure horror comm): ", end="", flush=True)
            try:
                testo = input().strip()
                return interpreta(testo)
            except ValueError as e:
                print(colora(f"\n{e}", "red"))
                aspetta_tasto()
        elif tasto.lower() == "y":
            if selezionati:
                return [v for _, v in GENERI if v in selezionati]
            print(colora("\nSeleziona almeno un genere.", "red"))
            aspetta_tasto()
        elif tasto.lower() == "n" or tasto == "ESC":
            return None


def mostra_aiuto():
    pulisci_schermo()
    print(colora("COMANDI", "cyan", "bold"))
    print("Y       Accetta i generi suggeriti da TMDB o dalle cartelle.")
    print("N       Apre il selettore manuale dei generi.")
    print("1-9     Apre il selettore con quel genere già selezionato.")
    print("S       Salta il film; sarà riproposto con --rivedi-saltati.")
    print("X       Esclude il film in modo permanente dalla procedura.")
    print("M       Cerca un film già classificato e modifica i suoi generi.")
    print("A       Corregge l'anno associato al film.")
    print("T       Cerca un titolo a mano; l'anno è facoltativo e puoi lasciarlo vuoto.")
    print("V       Mostra le alternative restituite da TMDB per la ricerca corrente.")
    print("        Puoi scegliere il candidato corretto anche senza conoscere l'anno.")
    print("B       Annulla l'ultima classificazione/modifica/azione.")
    print("?       Mostra questo aiuto.")
    print("Q       Salva ed esce.")
    print("\nNel selettore: frecce per muoversi, spazio per selezionare, Y per salvare,")
    print("T per inserire numeri o nomi (es. '1 5' o 'horror comm'), C per azzerare, N/ESC per annullare.")
    aspetta_tasto()


def _registra_storico(prog, g, schede):
    prog["storico"].append({
        "gid": g["id"],
        "prima": {c["key"]: schede.get(c["key"]) for c in g["copie"]},
        "saltato_prima": g["id"] in prog["saltati"],
        "ignorato_prima": g["id"] in prog["ignorati"],
    })


def modifica_film_classificato(gruppi, schede, prog, salva):
    """Mostra le schede già classificate; permette di selezionarne una e cambiare i generi."""
    candidati = [g for g in gruppi if any(schede.get(c["key"], {}).get("genres") for c in g["copie"])]
    if not candidati:
        pulisci_schermo()
        print(colora("Non ci sono film già classificati da modificare.", "yellow"))
        aspetta_tasto()
        return

    filtrati = candidati[:]
    indice = 0
    inizio = 0
    altezza = 12
    while True:
        pulisci_schermo()
        print(colora("╭─ MODIFICA FILM GIÀ CLASSIFICATI", "magenta", "bold"))
        print(colora("─" * 72, "magenta"))
        print("Film classificati: " + str(len(candidati)) + "   |   " +
              colora("↑/↓", "cyan", "bold") + " scorri   " +
              colora("Y", "green", "bold") + " scegli   " +
              colora("/", "yellow", "bold") + " cerca   " +
              colora("ESC", "red", "bold") + " torna indietro")
        if not filtrati:
            print("\nNessun risultato per il filtro corrente.")
        else:
            if indice < inizio:
                inizio = indice
            if indice >= inizio + altezza:
                inizio = indice - altezza + 1
            for pos in range(inizio, min(len(filtrati), inizio + altezza)):
                g = filtrati[pos]
                generi = next((schede.get(c["key"], {}).get("genres", []) for c in g["copie"]
                               if schede.get(c["key"], {}).get("genres")), [])
                riga = f"{g['titolo']} ({g['anno'] or '?'})  —  {_nome_generi(generi)}"
                if len(g["copie"]) > 1:
                    riga += f"  [{len(g['copie'])} copie]"
                if pos == indice:
                    print(colora("› " + riga, "bg_blue", "white", "bold"))
                else:
                    print("  " + riga)
            print(colora(f"\nRiga {indice + 1} di {len(filtrati)}", "dim"))

        tasto = leggi_tasto()
        if tasto == "CTRL_C" or tasto == "ESC" or tasto.lower() == "q":
            return
        if tasto == "UP" and filtrati:
            indice = max(0, indice - 1)
        elif tasto == "DOWN" and filtrati:
            indice = min(len(filtrati) - 1, indice + 1)
        elif tasto == "PAGEUP" and filtrati:
            indice = max(0, indice - altezza)
        elif tasto == "PAGEDOWN" and filtrati:
            indice = min(len(filtrati) - 1, indice + altezza)
        elif tasto == "HOME" and filtrati:
            indice = 0
        elif tasto == "END" and filtrati:
            indice = len(filtrati) - 1
        elif tasto == "/":
            print("\nCerca titolo o anno (Invio per filtrare, vuoto per tutti): ", end="", flush=True)
            filtro = input().strip()
            if filtro:
                nf = normalizza(filtro)
                filtrati = [g for g in candidati if nf in normalizza(g["titolo"]) or filtro in str(g["anno"] or "")]
            else:
                filtrati = candidati[:]
            indice = inizio = 0
        elif tasto.lower() == "y" and filtrati:
            g = filtrati[indice]
            correnti = next((schede.get(c["key"], {}).get("genres", []) for c in g["copie"]
                             if schede.get(c["key"], {}).get("genres")), [])
            nuovi = seleziona_generi(g, correnti, "MODIFICA GENERI")
            if nuovi is None:
                continue
            _registra_storico(prog, g, schede)
            for copia in g["copie"]:
                nuova = dict(schede.get(copia["key"], {}))
                nuova.update({"year": g["anno"] or nuova.get("year"), "genres": nuovi, "source": "Manuale"})
                nuova.pop("review", None)
                schede[copia["key"]] = nuova
            if g["id"] in prog["saltati"]:
                prog["saltati"].remove(g["id"])
            if g["id"] in prog["ignorati"]:
                prog["ignorati"].remove(g["id"])
            salva()
            return f"OK modificato: {g['titolo']} -> {_nome_generi(nuovi)}"


def annulla_ultima(prog, schede, per_id, saltati_ora, salva):
    if not prog["storico"]:
        pulisci_schermo()
        print(colora("Non ci sono operazioni da annullare.", "yellow"))
        aspetta_tasto()
        return None, ""
    ultimo = prog["storico"].pop()
    for k, vecchia in ultimo.get("prima", {}).items():
        if vecchia is None:
            schede.pop(k, None)
        else:
            schede[k] = vecchia
    gid = ultimo.get("gid")
    for nome_lista, chiave_storico in (("saltati", "saltato_prima"), ("ignorati", "ignorato_prima")):
        lista = prog[nome_lista]
        while gid in lista:
            lista.remove(gid)
        # Le voci dello storico create dalle versioni precedenti non contengono questi flag.
        precedente = bool(ultimo.get(chiave_storico, False))
        if precedente:
            lista.append(gid)
    saltati_ora.discard(gid)
    salva()
    titolo = per_id[gid]["titolo"] if gid in per_id else gid
    pulisci_schermo()
    messaggio = f"ANNULLATO: {titolo}"
    print(colora(messaggio, "yellow", "bold"))
    aspetta_tasto()
    # Se era una nuova classificazione ora il film è di nuovo da fare; se era una modifica,
    # la scheda precedente resta intatta e non va rimessa in coda.
    if gid in per_id and not any(schede.get(c["key"], {}).get("genres") for c in per_id[gid]["copie"]):
        return gid, messaggio
    return None, messaggio


def main():
    ap = argparse.ArgumentParser(description="Assegna a mano i generi ai film.")
    ap.add_argument("--disco")
    ap.add_argument("--rivedi-saltati", action="store_true")
    ap.add_argument("--stato", action="store_true")
    ap.add_argument("--key", default=os.environ.get("TMDB_API_KEY", ""))
    ap.add_argument("--senza-tmdb", action="store_true")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    attiva_colori()
    schede_file = leggi_json(FILE_SCHEDE, {})
    schede = schede_file.get("files", {}) if isinstance(schede_file, dict) else {}
    if not isinstance(schede, dict):
        schede = {}
    prog_file = leggi_json(FILE_PROGRESSO, {})
    prog = {
        "saltati": list(prog_file.get("saltati", [])) if isinstance(prog_file, dict) else [],
        "ignorati": list(prog_file.get("ignorati", [])) if isinstance(prog_file, dict) else [],
        "storico": list(prog_file.get("storico", [])) if isinstance(prog_file, dict) else [],
        "titoli": dict(prog_file.get("titoli", {})) if isinstance(prog_file, dict) and isinstance(prog_file.get("titoli", {}), dict) else {},
    }

    gruppi = raggruppa(carica_film(args.disco))
    if not gruppi:
        sys.exit("Nessun film trovato nei JSON di data/.")
    for gruppo in gruppi:
        gruppo["titolo_auto"] = gruppo["titolo"]
        if gruppo["id"] in prog["titoli"]:
            gruppo["titolo"] = str(prog["titoli"][gruppo["id"]])
            gruppo["titolo_manuale"] = True
        else:
            gruppo["titolo_manuale"] = False
    per_id = {g["id"]: g for g in gruppi}
    saltati_ora = set()

    def ha_genere(k):
        return bool(schede.get(k, {}).get("genres"))

    def stato(g):
        if g["id"] in prog["ignorati"]:
            return "ignorato"
        if all(ha_genere(c["key"]) for c in g["copie"]):
            return "fatto"
        if g["id"] in prog["saltati"] and (not args.rivedi_saltati or g["id"] in saltati_ora):
            return "saltato"
        return "da fare"

    def salva():
        scrivi_json(FILE_SCHEDE, {"version": 1, "files": schede})
        prog["storico"] = prog["storico"][-STORICO_MAX:]
        scrivi_json(FILE_PROGRESSO, prog)

    if args.stato:
        conta = Counter(stato(g) for g in gruppi)
        print(f"Film (gruppi di copie): {len(gruppi)} · fatti {conta['fatto']} · da fare {conta['da fare']} · "
              f"saltati {conta['saltato']} · ignorati {conta['ignorato']}")
        per_genere = Counter(x for g in gruppi if stato(g) == "fatto"
                             for x in schede[g["copie"][0]["key"]]["genres"])
        for genere, quanti in per_genere.most_common():
            print(f"  {ETICHETTA.get(genere, genere):<14} {quanti}")
        return

    suggeritore = None
    if not args.senza_tmdb:
        if not args.key:
            print(colora("TMDB non configurato: uso solo i suggerimenti delle cartelle.", "yellow"))
        else:
            tmdb = Tmdb(args.key)
            if tmdb.get("/configuration", language="en-US"):
                suggeritore = Suggeritore(tmdb)
            else:
                print(colora("TMDB non disponibile (chiave o rete): procedo senza suggerimenti TMDB.", "yellow"))

    totale = len(gruppi)
    ripeti = None
    fatti_sessione = 0
    ultimo_status = ""
    while True:
        try:
            if ripeti and ripeti in per_id:
                g, ripeti = per_id[ripeti], None
            else:
                da_fare = [x for x in gruppi if stato(x) == "da fare"]
                if not da_fare:
                    break
                g = da_fare[0]

            completati = sum(1 for x in gruppi if stato(x) in ("fatto", "ignorato"))
            saltati_count = sum(1 for x in gruppi if stato(x) == "saltato")
            numero = gruppi.index(g) + 1
            esistenti = next((schede[c["key"]]["genres"] for c in g["copie"] if ha_genere(c["key"])), None)
            if suggeritore:
                for x in [y for y in gruppi if stato(y) == "da fare"][:7]:
                    suggeritore.richiedi(x)
                esito = suggeritore.ottieni(g)
            else:
                esito = None
            sugg = costruisci_suggerimento(esistenti, esito, g, bool(suggeritore))

            while True:
                mostra_gruppo(g, numero, totale, completati, saltati_count, sugg, ultimo_status)
                tasto = leggi_tasto()
                if tasto == "CTRL_C":
                    salva()
                    pulisci_schermo()
                    print("Salvato. Puoi riprendere in seguito.")
                    return
                if not tasto:
                    continue
                comando = tasto.lower()

                if comando == "q":
                    salva()
                    pulisci_schermo()
                    print(f"Salvato. Film classificati in questa sessione: {fatti_sessione}.")
                    return
                if comando in ("?", "h"):
                    mostra_aiuto()
                    continue
                if comando == "m":
                    messaggio_modifica = modifica_film_classificato(gruppi, schede, prog, salva)
                    if messaggio_modifica:
                        ultimo_status = messaggio_modifica
                    continue
                if comando == "t":
                    if not suggeritore:
                        pulisci_schermo()
                        print(colora("TMDB non è attivo: imposta TMDB_API_KEY per cercare titoli.", "yellow"))
                        aspetta_tasto()
                        continue
                    pulisci_schermo()
                    print(colora("RICERCA TMDB MANUALE", "cyan", "bold"))
                    print("Titolo attuale: " + colora(g["titolo"], "white", "bold"))
                    nuovo_titolo = input("Titolo da cercare (Invio = annulla): ").strip()
                    if not nuovo_titolo:
                        continue
                    anno_testo = input("Anno facoltativo (Invio = cerca in tutti gli anni): ").strip()
                    if anno_testo:
                        if not (anno_testo.isdigit() and 1900 <= int(anno_testo) <= ANNO_CORRENTE):
                            pulisci_schermo()
                            print(colora(f"Anno non valido: usa un valore tra 1900 e {ANNO_CORRENTE}.", "red"))
                            aspetta_tasto()
                            continue
                        anno_ricerca = int(anno_testo)
                    else:
                        anno_ricerca = None
                    nuovo_esito = ricerca_interattiva_tmdb(
                        suggeritore.tmdb, nuovo_titolo, anno_ricerca
                    )
                    if nuovo_esito:
                        g["titolo"] = nuovo_esito.get("query_titolo") or nuovo_titolo
                        g["titolo_manuale"] = True
                        prog["titoli"][g["id"]] = g["titolo"]
                        sugg = costruisci_suggerimento(None, nuovo_esito, g, True)
                        ultimo_status = (f"OK candidato scelto: {nuovo_esito['titolo']} "
                                         f"({nuovo_esito['anno'] or '?'})")
                        salva()
                    else:
                        ultimo_status = f"Ricerca TMDB annullata: {nuovo_titolo}"
                    continue
                if comando == "v":
                    if not suggeritore or not sugg.get("tmdb"):
                        pulisci_schermo()
                        print(colora("Non ci sono alternative TMDB disponibili per questo film.", "yellow"))
                        aspetta_tasto()
                        continue
                    dati_tmdb = sugg["tmdb"]
                    candidati_tmdb = dati_tmdb.get("candidati") or []
                    if len(candidati_tmdb) <= 1:
                        pulisci_schermo()
                        print(colora("TMDB non ha restituito alternative aggiuntive.", "yellow"))
                        aspetta_tasto()
                        continue
                    scelto = seleziona_candidato_tmdb(
                        candidati_tmdb,
                        dati_tmdb.get("query_titolo") or g["titolo"],
                        dati_tmdb.get("query_anno"),
                    )
                    if scelto == "NUOVA_RICERCA":
                        pulisci_schermo()
                        print("Titolo da cercare (Invio = annulla): ", end="", flush=True)
                        nuovo_titolo = input().strip()
                        if not nuovo_titolo:
                            continue
                        anno_testo = input("Anno facoltativo (Invio = tutti gli anni): ").strip()
                        if anno_testo and anno_testo.isdigit() and 1900 <= int(anno_testo) <= ANNO_CORRENTE:
                            anno_ricerca = int(anno_testo)
                        elif not anno_testo:
                            anno_ricerca = None
                        else:
                            print(colora("Anno non valido.", "red"))
                            aspetta_tasto()
                            continue
                        nuovo_esito = ricerca_interattiva_tmdb(
                            suggeritore.tmdb, nuovo_titolo, anno_ricerca
                        )
                        if nuovo_esito:
                            g["titolo"] = nuovo_esito.get("query_titolo") or nuovo_titolo
                            g["titolo_manuale"] = True
                            prog["titoli"][g["id"]] = g["titolo"]
                            sugg = costruisci_suggerimento(None, nuovo_esito, g, True)
                            ultimo_status = f"OK candidato scelto: {nuovo_esito['titolo']} ({nuovo_esito['anno'] or '?'})"
                            salva()
                        continue
                    if scelto is None:
                        continue
                    nuovo_esito = dettaglio_candidato_tmdb(
                        suggeritore.tmdb, scelto,
                        dati_tmdb.get("query_titolo") or g["titolo"],
                        dati_tmdb.get("query_anno"),
                        confermato_manualmente=True,
                        candidati=candidati_tmdb,
                    )
                    if nuovo_esito:
                        sugg = costruisci_suggerimento(None, nuovo_esito, g, True)
                        ultimo_status = f"OK candidato scelto: {nuovo_esito['titolo']} ({nuovo_esito['anno'] or '?'})"
                    else:
                        ultimo_status = "TMDB: impossibile caricare la scheda completa del candidato."
                    continue
                if comando == "a":
                    pulisci_schermo()
                    print(f"Film: {g['titolo']}\nAnno attuale: {g['anno'] or 'non indicato'}")
                    print("Nuovo anno (Invio per mantenere quello attuale): ", end="", flush=True)
                    nuovo = input().strip()
                    if nuovo:
                        if nuovo.isdigit() and 1900 <= int(nuovo) <= ANNO_CORRENTE:
                            g["anno"] = int(nuovo)
                            ultimo_status = f"OK anno aggiornato: {g['titolo']} ({g['anno']})"
                            pulisci_schermo()
                            print(colora(f"Anno aggiornato a {g['anno']}.", "green"))
                            aspetta_tasto()
                            # La ricerca TMDB usa l'anno iniziale; per evitare un falso suggerimento,
                            # la schermata resta aggiornata ma non viene rilanciata automaticamente.
                        else:
                            pulisci_schermo()
                            print(colora(f"Anno non valido: inserisci un valore tra 1900 e {ANNO_CORRENTE}.", "red"))
                            aspetta_tasto()
                    continue
                if comando == "b":
                    ripeti, ultimo_status = annulla_ultima(prog, schede, per_id, saltati_ora, salva)
                    break

                if comando == "s":
                    _registra_storico(prog, g, schede)
                    if g["id"] not in prog["saltati"]:
                        prog["saltati"].append(g["id"])
                    saltati_ora.add(g["id"])
                    salva()
                    ultimo_status = f"SALTATO: {g['titolo']}"
                    break
                if comando == "x":
                    _registra_storico(prog, g, schede)
                    if g["id"] not in prog["ignorati"]:
                        prog["ignorati"].append(g["id"])
                    salva()
                    ultimo_status = f"IGNORATO: {g['titolo']}"
                    break
                if comando == "n":
                    generi = seleziona_generi(g)
                    if generi is None:
                        continue
                    da_tmdb = False
                elif comando.isdigit() and 1 <= int(comando) <= 9:
                    genere_iniziale = GENERI[int(comando) - 1][1]
                    generi = seleziona_generi(g, [genere_iniziale], "SCELTA GENERI")
                    if generi is None:
                        continue
                    da_tmdb = False
                elif comando == "y":
                    if not sugg["generi"]:
                        pulisci_schermo()
                        print(colora("Non c'è un suggerimento da accettare. Premi N per scegliere i generi.", "yellow"))
                        aspetta_tasto()
                        continue
                    generi = list(sugg["generi"])
                    da_tmdb = sugg["origine"] == "TMDB"
                else:
                    continue

                _registra_storico(prog, g, schede)
                for copia in g["copie"]:
                    if da_tmdb:
                        nuova = dict(sugg["tmdb"]["scheda"])
                        anno_tmdb = nuova.get("year")
                        anno_da_salvare = (anno_tmdb if sugg["tmdb"].get("scelta_manuale") else (g["anno"] or anno_tmdb))
                        nuova.update({"year": anno_da_salvare, "genres": generi, "source": "TMDB"})
                    else:
                        nuova = dict(schede.get(copia["key"], {}))
                        nuova.update({"year": g["anno"], "genres": generi, "source": "Manuale"})
                    nuova.pop("review", None)
                    schede[copia["key"]] = nuova
                if g["id"] in prog["saltati"]:
                    prog["saltati"].remove(g["id"])
                fatti_sessione += 1
                salva()
                ultimo_status = f"OK salvato: {g['titolo']} -> {_nome_generi(generi)}"
                break
        except KeyboardInterrupt:
            salva()
            pulisci_schermo()
            print("Interrotto. Le modifiche sono state salvate.")
            return
        except EOFError:
            salva()
            return

    salva()
    saltati = [x for x in gruppi if stato(x) == "saltato"]
    pulisci_schermo()
    print(colora(f"Classificazione completata. Film classificati in questa sessione: {fatti_sessione}.", "green", "bold"))
    if saltati:
        print(f"Film saltati: {len(saltati)} (per riproporli usa --rivedi-saltati).")
    print("\nComando Git: git add data/film_metadata.json  ·  git commit -m \"Generi film\"  ·  git push")


if __name__ == "__main__":
    main()
