# listhdd

Catalogo personale dei video distribuiti su più dischi. Il sito è statico: HTML, CSS e JavaScript vanilla, senza framework o dipendenze lato browser.

## Avvio in locale

Dalla cartella principale del repository:

```powershell
python -m http.server 8000
```

Aprire poi [http://localhost:8000](http://localhost:8000). È necessario usare un server HTTP locale perché il sito carica i JSON con `fetch`.

## Struttura

- `index.html`, `styles.css`, `app.js`: interfaccia del catalogo.
- `data/<disco>.json`: cataloghi generati dalla scansione dei dischi; ciascuno contiene `catalogo_video` e `informazioni_disco`.
- `data/manifest.json`: associa il nome visualizzato al relativo file JSON. Il nome del file (senza estensione) è l'identificativo stabile usato per le schede e i preferiti.
- `data/film_metadata.json`: generi e schede dei film.
- `data/tv_metadata.json`: metadati di serie TV e anime; il sito carica entrambi i database, mentre lo script di classificazione modifica soltanto quello dei film.
- `scripts/assegna_generi_tmdb.py`: classificazione interattiva dei film con suggerimenti TMDB.
- `scripts/aggiorna_manifest.py`: rigenera il manifest a partire dai cataloghi validi in `data/`.

## Classificazione dei film

Impostare la chiave TMDB nell'ambiente PowerShell:

```powershell
$env:TMDB_API_KEY="LA_TUA_CHIAVE"
python scripts/assegna_generi_tmdb.py
```

Alcune opzioni:

```powershell
python scripts/assegna_generi_tmdb.py --stato
python scripts/assegna_generi_tmdb.py --disco Seagate1TB
python scripts/assegna_generi_tmdb.py --senza-tmdb
```

Ogni classificazione viene salvata immediatamente in `data/film_metadata.json`. Lo stato della sessione, i film saltati/ignorati e le correzioni di titolo restano in `scripts/.progresso_generi.json`: è un file locale escluso da Git e **non va pubblicato**.

## Aggiornamento dei dischi

Quando si aggiunge, rimuove o rinomina un catalogo JSON, rigenerare il manifest:

```powershell
python scripts/aggiorna_manifest.py
```

Il workflow GitHub Actions aggiorna automaticamente `data/manifest.json` quando cambia un catalogo in `data/`. Le modifiche ai due database dei metadati non attivano quel workflow.

## Pubblicazione

Per condividere le classificazioni, pubblicare `data/film_metadata.json` e, quando modificato dal sito, `data/tv_metadata.json`. Non pubblicare `scripts/.progresso_generi.json`. I cataloghi dei dischi vanno aggiornati soltanto quando cambia la scansione corrispondente.
