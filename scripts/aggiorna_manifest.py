#!/usr/bin/env python3
"""Rigenera data/manifest.json a partire dai file JSON presenti in data/.

- Ogni data/<Nome>.json con un array "catalogo_video" è un disco.
- Per i dischi già nel manifest viene mantenuto il "nome" esistente
  (i preferiti e le schede film sono legati a quel nome).
- Per i dischi nuovi il nome è il nome del file senza .json.
- I dischi il cui file non esiste più vengono tolti dal manifest.

Uso (dalla radice del repo):  python scripts/aggiorna_manifest.py
"""
import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
MANIFEST = DATA / "manifest.json"
ESCLUSI = {"manifest.json", "film_metadata.json", "tv_metadata.json"}


def carica_manifest_esistente():
    try:
        dischi = json.loads(MANIFEST.read_text(encoding="utf-8")).get("dischi", [])
    except (OSError, ValueError):
        return {}
    return {str(e["file"]).casefold(): e["nome"] for e in dischi if isinstance(e, dict) and "file" in e and "nome" in e}


def e_catalogo_valido(percorso):
    try:
        dati = json.loads(percorso.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        print(f"ATTENZIONE: {percorso.name} ignorato (JSON non leggibile: {err})", file=sys.stderr)
        return False
    if not isinstance(dati, dict) or not isinstance(dati.get("catalogo_video"), list):
        print(f"ATTENZIONE: {percorso.name} ignorato (manca catalogo_video)", file=sys.stderr)
        return False
    return True


def main():
    nomi = carica_manifest_esistente()
    dischi = []
    for p in sorted(DATA.glob("*.json")):
        if p.name in ESCLUSI or not e_catalogo_valido(p):
            continue
        dischi.append({"nome": nomi.get(p.name.casefold(), p.stem), "file": p.name})
    dischi.sort(key=lambda e: e["nome"].casefold())

    testo = json.dumps({"version": 2, "dischi": dischi}, indent=2, ensure_ascii=False) + "\n"
    if MANIFEST.exists() and MANIFEST.read_text(encoding="utf-8") == testo:
        print(f"manifest.json già aggiornato ({len(dischi)} dischi)")
        return
    MANIFEST.write_text(testo, encoding="utf-8")
    print(f"manifest.json aggiornato ({len(dischi)} dischi)")


if __name__ == "__main__":
    main()
