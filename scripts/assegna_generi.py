#!/usr/bin/env python3
"""Assegna a mano i generi ai film dei cataloghi e li salva in data/film_metadata.json.

Cosa fa
  * Legge i JSON in data/ e trova i FILM (extra, interviste, serie TV e anime esclusi:
    stessa logica di app.js, blocco CLASSIFICAZIONE).
  * Per ogni film ti mostra titolo, anno, disco e percorso e ti chiede il genere.
    Lo stesso film su più dischi (stesso titolo e anno) viene chiesto una volta sola
    e la risposta vale per tutte le copie.
  * Ogni risposta viene salvata SUBITO: se chiudi lo script (q, Ctrl+C, finestra chiusa)
    alla volta dopo riparte dal primo film senza genere.
  * Le schede già presenti con un genere (anche quelle fatte a mano dal sito) non vengono
    mai riproposte né sovrascritte.

Uso (dalla radice del repo, nessun pacchetto da installare):
    python scripts\\assegna_generi.py
Opzioni:
    --disco NOME       propone solo i film di quel disco (nome del JSON senza .json)
    --rivedi-saltati   ripropone anche i film che avevi saltato con "s"
    --stato            mostra a che punto sei e quanti film ci sono per genere, poi esce

Risposte possibili quando ti chiede il genere
    1 5  oppure  1,5   uno o più numeri della lista (es. Horror + Azione)
    horror comm        va bene anche scrivere l'inizio del nome del genere
    Invio              accetta il suggerimento (se c'è: viene dalla cartella, es. HORROR - THRILLER)
    s                  salta il film per ora (lo ritrovi con --rivedi-saltati)
    x                  non è un film / da ignorare per sempre
    a                  correggi l'anno
    b                  torna indietro e rifai il film precedente
    ?                  mostra questo aiuto
    q                  salva ed esci

Alla fine: git add data/film_metadata.json, commit e push.
"""
import argparse
import json
import os
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent
DATA = RADICE / "data"
FILE_SCHEDE = DATA / "film_metadata.json"
FILE_PROGRESSO = Path(__file__).resolve().parent / ".progresso_generi.json"   # locale, non va su GitHub
ESCLUSI = {"manifest.json", "film_metadata.json"}
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


def pulisci(t):
    if t.count("(") > t.count(")"):
        t = t[: t.rfind("(")]
    t = re.sub(r"\([^)]*\)", " ", t)
    t = re.sub(r"\[[^\]]*\]", " ", t)
    return re.sub(r"\s+", " ", t).strip(" -–—+,:;()[]")


def titolo_e_anno(nome):
    base = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", nome)
    base = re.sub(r"[._]+", " ", base)
    tag = TAG_RILASCIO.search(base)
    testa = base[: tag.start()] if tag and tag.start() > 2 else base
    testa = re.sub(r"\[(?!\s*(?:19|20)\d\d\s*\])[^\]]*\]", " ", testa)
    if testa.count("[") > testa.count("]"):
        testa = testa[: testa.rfind("[")]
    cand = [(int(m.group(1)), m.start(1)) for m in re.finditer(RE_ANNO, testa) if 1900 <= int(m.group(1)) <= ANNO_CORRENTE]
    if len(cand) > 1 and cand[0][1] == 0:
        cand = cand[1:]
    titolo = None
    if cand:
        inizio = cand[-1][1]
        prima, dopo = pulisci(testa[:inizio]), pulisci(testa[inizio + 4:])
        dopo_trattino = bool(re.search(r"[-–—]\s*$", testa[:inizio]))
        titolo = dopo if (len(prima) < 2 or (dopo_trattino and len(dopo) >= 3)) else prima
    return titolo or pulisci(testa) or nome


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
            if nome and perc and e_film(perc, nome, parti_percorso(perc)):
                film.append({"key": f"{p.stem}::{perc}", "nome": nome, "percorso": perc, "disco": p.stem,
                             "dim": str(f.get("dimensione", "")), "titolo": titolo_e_anno(nome)})
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


# ------------------------------------------------ interazione
def righe_generi():
    celle = [f"{i:>2} {etichetta}" for i, (etichetta, _) in enumerate(GENERI, 1)]
    larghezza = max(len(c) for c in celle) + 2
    return "\n".join("  " + "".join(c.ljust(larghezza) for c in celle[i:i + 5]).rstrip() for i in range(0, len(celle), 5))


def interpreta(testo):
    """Numeri e/o inizi di nome -> lista di valori genere. Solleva ValueError con il messaggio da mostrare."""
    scelti = []
    for tok in re.split(r"[\s,;+/]+", testo.strip()):
        if not tok:
            continue
        if tok.isdigit():
            n = int(tok)
            if not 1 <= n <= len(GENERI):
                raise ValueError(f"'{tok}' non è nella lista (1-{len(GENERI)})")
            valore = GENERI[n - 1][1]
        else:
            trovati = [v for e, v in GENERI if normalizza(e).startswith(normalizza(tok))]
            if len(trovati) != 1:
                raise ValueError(f"'{tok}' non è chiaro: scrivi il numero o più lettere del genere" if trovati else f"'{tok}' non è un genere")
            valore = trovati[0]
        if valore not in scelti:
            scelti.append(valore)
    if not scelti:
        raise ValueError("scrivi almeno un numero")
    return scelti


def mostra_gruppo(g, n, totale, suggerimento, progresso):
    print("\n" + "-" * 78)
    print(f"[{n}/{totale}]  saltati: {len(progresso['saltati'])}   (? aiuto · q esci)")
    print(f"  {g['titolo']}   ({g['anno'] or 'anno ?'})")
    for c in g["copie"][:4]:
        print(f"    {c['disco']:<14} {c['dim']:>9}   {c['percorso']}")
    if len(g["copie"]) > 4:
        print(f"    … e altre {len(g['copie']) - 4} copie")
    print(righe_generi())
    if suggerimento:
        print("  Suggerimento: " + ", ".join(ETICHETTA.get(s, s) for s in suggerimento) + "   (Invio per accettare)")


AIUTO = __doc__.split("Risposte possibili quando ti chiede il genere")[1].split("Alla fine:")[0]


def main():
    ap = argparse.ArgumentParser(description="Assegna a mano i generi ai film.")
    ap.add_argument("--disco")
    ap.add_argument("--rivedi-saltati", action="store_true")
    ap.add_argument("--stato", action="store_true")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    schede = leggi_json(FILE_SCHEDE, {}).get("files", {})
    prog = leggi_json(FILE_PROGRESSO, {})
    prog = {"saltati": list(prog.get("saltati", [])), "ignorati": list(prog.get("ignorati", [])), "storico": list(prog.get("storico", []))}

    gruppi = raggruppa(carica_film(args.disco))
    if not gruppi:
        sys.exit("Nessun film trovato nei JSON di data/.")
    per_id = {g["id"]: g for g in gruppi}

    saltati_ora = set()    # saltati in questa sessione (con --rivedi-saltati non vanno richiesti di nuovo subito)

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
              f"saltati {len(prog['saltati'])} · ignorati {conta['ignorato']}")
        per_genere = Counter(x for g in gruppi if stato(g) == "fatto" for x in schede[g['copie'][0]['key']]['genres'])
        for genere, n in per_genere.most_common():
            print(f"  {ETICHETTA.get(genere, genere):<14} {n}")
        return

    totale = len(gruppi)
    ripeti = None          # gruppo da rifare dopo "b"
    fatti_sessione = 0
    print("Assegnazione generi: ogni risposta viene salvata subito, puoi uscire quando vuoi (q) e riprendere dopo.")
    try:
        while True:
            if ripeti and ripeti in per_id:
                g, ripeti = per_id[ripeti], None
            else:
                da_fare = [x for x in gruppi if stato(x) == "da fare"]
                if not da_fare:
                    break
                g = da_fare[0]
            n = sum(1 for x in gruppi if stato(x) in ("fatto", "ignorato")) + 1
            esistenti = next((schede[c["key"]]["genres"] for c in g["copie"] if ha_genere(c["key"])), None)
            suggerimento = esistenti or generi_da_cartelle(g["copie"][0]["percorso"])
            while True:     # risposta valida per questo gruppo
                mostra_gruppo(g, n, totale, suggerimento, prog)
                try:
                    risposta = input("Genere > ").strip()
                except (EOFError, KeyboardInterrupt):
                    risposta = "q"
                r = risposta.lower()
                if r in ("q", "esci"):
                    salva()
                    print(f"\nSalvato. Fatti in questa sessione: {fatti_sessione}. Rilancia lo script per continuare.")
                    return
                if r in ("?", "h", "aiuto"):
                    print(AIUTO)
                    continue
                if r == "a":
                    try:
                        nuovo = input(f"Anno (Invio per lasciare {g['anno'] or 'vuoto'}): ").strip()
                    except (EOFError, KeyboardInterrupt):
                        continue
                    if nuovo:
                        if nuovo.isdigit() and 1900 <= int(nuovo) <= ANNO_CORRENTE:
                            g["anno"] = int(nuovo)
                        else:
                            print(f"  Anno non valido (1900-{ANNO_CORRENTE}).")
                    continue
                if r == "b":
                    if not prog["storico"]:
                        print("  Non c'è nulla da annullare.")
                        continue
                    ultimo = prog["storico"].pop()
                    for k, vecchia in ultimo["prima"].items():
                        if vecchia is None:
                            schede.pop(k, None)
                        else:
                            schede[k] = vecchia
                    for lista in (prog["saltati"], prog["ignorati"]):
                        if ultimo["gid"] in lista:
                            lista.remove(ultimo["gid"])
                    saltati_ora.discard(ultimo["gid"])
                    ripeti = ultimo["gid"]
                    if ripeti in per_id:
                        per_id[ripeti]["anno"] = per_id[ripeti]["anno_originale"]    # l'anno corretto con "a" torna quello di partenza
                    salva()
                    print(f"  Annullato: {per_id[ultimo['gid']]['titolo'] if ultimo['gid'] in per_id else ultimo['gid']}")
                    break
                voce = {"gid": g["id"], "prima": {c["key"]: schede.get(c["key"]) for c in g["copie"]}}
                if r == "s":
                    if g["id"] not in prog["saltati"]:
                        prog["saltati"].append(g["id"])
                    saltati_ora.add(g["id"])
                    prog["storico"].append(voce)
                    salva()
                    break
                if r == "x":
                    prog["ignorati"].append(g["id"])
                    prog["storico"].append(voce)
                    salva()
                    print("  Ignorato: non verrà più proposto.")
                    break
                if r == "":
                    if not suggerimento:
                        print("  Nessun suggerimento per questo film: scrivi i numeri, oppure s per saltare.")
                        continue
                    generi = list(suggerimento)
                else:
                    try:
                        generi = interpreta(risposta)
                    except ValueError as e:
                        print(f"  {e}")
                        continue
                for c in g["copie"]:
                    vecchia = schede.get(c["key"], {})
                    nuova = dict(vecchia)
                    nuova.update({"year": g["anno"], "genres": generi, "source": "Manuale"})
                    nuova.pop("review", None)
                    schede[c["key"]] = nuova
                if g["id"] in prog["saltati"]:
                    prog["saltati"].remove(g["id"])
                prog["storico"].append(voce)
                fatti_sessione += 1
                salva()
                print("  -> " + ", ".join(ETICHETTA.get(x, x) for x in generi) + (f"  (su {len(g['copie'])} dischi)" if len(g['copie']) > 1 else ""))
                break
    except KeyboardInterrupt:
        salva()
        print("\nInterrotto. Quello che hai fatto è già salvato.")
        return
    salva()
    saltati = [x for x in gruppi if stato(x) == "saltato"]
    print(f"\nFinito! Fatti in questa sessione: {fatti_sessione}." + (f" Film saltati: {len(saltati)} (rivedili con --rivedi-saltati)." if saltati else ""))
    print("Ora: git add data/film_metadata.json  ·  git commit -m \"Generi film\"  ·  git push")


if __name__ == "__main__":
    main()
