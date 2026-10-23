#!/usr/bin/env python3
"""VAULT: enrich conservative film matches via TMDB, without exposing credentials.

Reads the existing data/manifest.json and data/*.json, creates/updates
film_metadata.json, tmdb_state.json and tmdb_review.json.
Only searches plausible standalone movies with a year; episodic content,
extras and uncertain matches are excluded or sent to review.
Uses the Python standard library. Does not upload video files.
"""
import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
import json
import os
from pathlib import Path
import re
import sys
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_BASE = 'https://api.themoviedb.org/3/search/movie'
GENRE_MAP = {
    28: 'Azione', 12: 'Avventura', 16: 'Animazione', 35: 'Commedia',
    80: 'Crime', 99: 'Documentario', 18: 'Drammatico', 14: 'Fantasy',
    878: 'Fantascienza', 10752: 'Guerra', 27: 'Horror', 10402: 'Musicale',
    10749: 'Romantico', 36: 'Storico', 53: 'Thriller', 37: 'Western'
}
VIDEO_EXT = {'.mkv','.mp4','.avi','.mov','.wmv','.m4v','.mpg','.mpeg',
             '.ts','.m2ts','.webm','.flv','.vob','.divx','.iso'}
EXTRA_DIR = re.compile(r'^(?:bonus|bonus disc|extras?|special features?|featurettes?|deleted scenes?|interviews?|making of|behind the scenes|trailers?|galleria fotografica|materiali promozionali|menu|contenuti speciali)$', re.I)
EPISODE = re.compile(r'(?i)(?:\bS\s?\d{1,2}\s?[.\- ]?E\s?\d{1,3}\b|\b\d{1,2}x\d{2,3}\b|\bseason\s+\d+\b|\bstagione\s+\d+\b|\bepisod(?:e|io)[.\-_ ]*\d+\b|\be\d{2}\b)')
MOVIE_CONTEXT = re.compile(r'(?i)(?:^|\s)(film|movies|cinema|horror|thriller|sci fi|fantascienza|fantasy)(?:$|\s)')
TV_CONTEXT = re.compile(r'(?i)(?:^|\s)(serie tv|serietv|serie|series|cartoni animati|anime|stagion[ie]|season)(?:$|\s)')
YEAR_RE = re.compile(r'(?<!\d)(19\d{2}|20[0-2]\d)(?!\d)')
TECH_RE = re.compile(r'(?i)(?:\b(?:2160p|1080p|720p|576p|480p|bluray|blu ray|bdrip|bdmux|brrip|dvdrip|dvdmux|webrip|web dl|webmux|remux|repack|x264|x265|h264|h265|hevc|aac|ac3|dts|dvmux|satrip|sub ita|sub eng|ita eng|hdtv)\b)')
CLEAN_PREFIX = re.compile(r'(?i)^(?:\s*[\[\(][^\]\)]{1,40}[\]\)]\s*)?\s*(?:\d{1,2}\s*[-–]\s*)?')
DATE_FMT = '%Y-%m-%d'
NOW = datetime.now(timezone.utc)
TODAY = NOW.date()


def canon(s):
    s = unicodedata.normalize('NFKD', str(s or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[a-z0-9]+', s.casefold()))


def load_json(path, fallback):
    if not path.exists(): return fallback
    try: return json.loads(path.read_text(encoding='utf-8-sig'))
    except (ValueError, UnicodeError) as e:
        raise ValueError(f'JSON non valido: {path}: {e}') from e


def save_json_if_different(path, obj):
    content = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
    if path.exists() and path.read_text(encoding='utf-8-sig') == content: return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    return True


def extract_candidate(file):
    name = str(file.get('nome', '')).strip()
    path = str(file.get('percorso_completo', '')).strip()
    if not name or not path or Path(name).suffix.lower() not in VIDEO_EXT: return None
    dirs = path.replace('/', '\\').split('\\')[1:-1]
    if any(EXTRA_DIR.match(x.strip()) for x in dirs): return None
    full = ' '.join(canon(d) for d in dirs)
    if TV_CONTEXT.search(full) or EPISODE.search(name) or EPISODE.search(path): return None
    # Avoid assuming that any episode or OVA under ANIME is a standalone movie.
    if re.search(r'\b(anime|cartoni)\b', full): return None
    if not MOVIE_CONTEXT.search(full):
        # Outside explicit movie folders, only films with a year and a fairly
        # clear filename are admitted; excludes most miscellaneous videos.
        if not YEAR_RE.search(name) or len(dirs) > 3: return None
    stem = Path(name).stem
    years = list(YEAR_RE.finditer(stem))
    if not years: return None  # No title/year certainty: send to no API.
    year_match = years[0]
    year = int(year_match.group(1))
    if not (1900 <= year <= TODAY.year): return None
    title = stem[:year_match.start()]
    # Bracketed release group and rip metadata preceding year.
    title = re.sub(r'[._]+', ' ', title).replace('_', ' ')
    title = re.sub(r'\([^)]*(?:JPN|ITA|ENG|PROPER|INTERNAL)[^)]*\)', ' ', title, flags=re.I)
    title = CLEAN_PREFIX.sub('', title).strip(' .-_;()[]')
    tech = TECH_RE.search(title)
    if tech: title = title[:tech.start()].strip(' .-_;()[]')
    title = re.sub(r'\s+', ' ', title).strip()
    normalized = canon(title)
    # Reject titles looking like placeholders, numbers, or too much release noise.
    if len(normalized) < 5 or len(normalized.split()) > 14: return None
    if normalized.isdigit() or normalized in {'extra','trailer','special','featurette'}: return None
    return title, year, normalized


def candidate_groups(root):
    manifest = load_json(root/'data'/'manifest.json', {})
    disks = manifest.get('dischi')
    if not isinstance(disks, list) or not disks: raise ValueError('data/manifest.json mancante o senza dischi')
    groups = defaultdict(list)
    files = 0
    skipped = 0
    for disk in disks:
        name = str(disk.get('nome') or '').strip()
        filename = str(disk.get('file') or '')
        if not name or not re.fullmatch(r'[\w.\-]+\.json', filename) or filename == 'manifest.json':
            raise ValueError(f'Voce manifest non valida: {disk!r}')
        disk_file = root/'data'/filename
        if not disk_file.is_file():
            raise FileNotFoundError(f'{disk_file} non trovato. Controlla maiuscole/minuscole nel manifest GitHub.')
        catalog = load_json(disk_file, {})
        if not isinstance(catalog.get('catalogo_video'), list):
            raise ValueError(f'{disk_file} privo di catalogo_video')
        for obj in catalog['catalogo_video']:
            if not isinstance(obj, dict): continue
            files += 1
            movie = extract_candidate(obj)
            if not movie:
                skipped += 1
                continue
            title, year, normalized = movie
            path = str(obj['percorso_completo'])
            groups[f'{normalized}|{year}'].append({'key': f'{name}::{path}', 'title': title, 'year':year})
    return groups, files, skipped


def tmdb_search(token, title, year, session=None):
    params = urlencode({'query':title,'language':'it-IT','include_adult':'false','year':year,'page':1})
    req = Request(API_BASE+'?'+params, headers={
        'Authorization':'Bearer '+token,
        'Accept':'application/json',
        'User-Agent':'VaultPersonalCatalog/1.0 (GitHub Actions; TMDB metadata enrichment)'
    })
    for attempt in range(5):
        try:
            if session is None:
                with urlopen(req, timeout=20) as response:
                    return json.load(response).get('results', [])
            return session(title, year)
        except HTTPError as exc:
            if exc.code == 401 or exc.code == 403:
                raise RuntimeError('Credenziale TMDB non valida o non autorizzata. Controlla il Secret.') from None
            if exc.code in {429, 500, 502, 503, 504} and attempt < 4:
                retry_after = exc.headers.get('Retry-After')
                try: wait = min(20, max(1, float(retry_after or 2 ** attempt)))
                except ValueError: wait = min(20, 2 ** attempt)
                time.sleep(wait)
                continue
            raise RuntimeError(f'TMDB HTTP {exc.code}; interruzione senza salvare risultati incompleti.') from None
        except (URLError, TimeoutError) as exc:
            if attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f'Problema di connessione a TMDB: {exc}') from exc
    return []


def match_result(title, year, results):
    ref = canon(title)
    candidates = []
    for row in results[:20]:
        if not isinstance(row, dict) or not isinstance(row.get('id'), int): continue
        released = str(row.get('release_date') or '')[:4]
        if not released.isdigit() or abs(int(released) - year) > 1: continue
        similarity = max(SequenceMatcher(None, ref, canon(row.get(k))).ratio()
                         for k in ('title', 'original_title'))
        if abs(int(released) - year) == 1 and similarity < .98: continue
        candidates.append((similarity, row))
    candidates.sort(key=lambda x: x[0], reverse=True)
    if not candidates: return None, 'non_trovato'
    best_score, best = candidates[0]
    runner = candidates[1][0] if len(candidates) > 1 else 0
    if best_score < .94 or (runner >= .90 and best_score - runner < .08):
        return None, 'ambiguo'
    genres = [GENRE_MAP[g] for g in best.get('genre_ids', []) if g in GENRE_MAP]
    if not genres: return None, 'senza_generi'
    year_res = int(str(best.get('release_date'))[:4])
    result = {'year': year_res, 'genres': sorted(set(genres)), 'source': 'TMDB',
              'tmdb_id': best['id'], 'tmdb_title': best.get('title',''),
              'tmdb_url': f"https://www.themoviedb.org/movie/{best['id']}",
              'checked_at': TODAY.strftime(DATE_FMT)}
    return result, None


def fresh(timestamp, days):
    try: return (TODAY-date.fromisoformat(str(timestamp))) < timedelta(days=days)
    except (TypeError, ValueError): return False


def run(root, limit, dry_run=False, token=None, session=None):
    groups, total, skipped = candidate_groups(root)
    meta_path = root/'data'/'film_metadata.json'
    states_path = root/'data'/'tmdb_state.json'
    review_path = root/'data'/'tmdb_review.json'
    meta = load_json(meta_path, {'version':1,'files':{}})
    state = load_json(states_path, {'version':1,'queries':{}})
    if not isinstance(meta.get('files'), dict):raise ValueError('film_metadata.json: files non è un oggetto')
    if not isinstance(state.get('queries'), dict): raise ValueError('tmdb_state.json: queries non è un oggetto')
    entries = meta['files']; lookups=state['queries']
    attempts = accepted = uncertain = new_files = 0
    review = []
    for query, group in sorted(groups.items(), key=lambda item:(-len(item[1]),item[0])):
        keys = [g['key'] for g in group]
        # Never replace manually edited metadata. Propagate a recent confident
        # match across same movie releases/copies before querying again.
        reusable = next((entries[k] for k in keys if k in entries and entries[k].get('source') == 'TMDB'
                         and fresh(entries[k].get('checked_at'),120)), None)
        if reusable:
            for key in keys:
                if key not in entries:
                    entries[key] = dict(reusable)
                    new_files += 1
            continue
        prior = lookups.get(query, {})
        if fresh(prior.get('checked_at'), 35) and prior.get('status') in {'ambiguo','non_trovato','senza_generi'}:
            continue
        if attempts >= limit: continue
        title, year = group[0]['title'], group[0]['year']
        if dry_run:
            attempts += 1
            if attempts <= 8: print(f'  [anteprima] {title} ({year}), copie: {len(keys)}')
            continue
        results=tmdb_search(token, title, year, session)
        attempts += 1
        data, why=match_result(title, year, results)
        if data:
            accepted += 1
            for key in keys:
                if entries.get(key,{}).get('source') not in {'Manuale','Manual', 'Utente'}:
                    if entries.get(key) != data:
                        entries[key]=dict(data)
                        new_files += 1
            lookups[query]={'status':'ok','checked_at':TODAY.strftime(DATE_FMT),'id':data['tmdb_id']}
        else:
            uncertain += 1
            lookups[query]={'status':why,'checked_at':TODAY.strftime(DATE_FMT)}
            if len(review)<200:review.append({'titolo':title,'anno':year,'motivo':why,'copie':len(keys)})
        if attempts % 25 == 0: print(f'  Richieste: {attempts}, film riconosciuti: {accepted}, da verificare: {uncertain}')
        if session is None: time.sleep(.28)
    print(f'File nel catalogo: {total}; esclusi o non candidati: {skipped}; gruppi di film con anno: {len(groups)}')
    print(f'Richieste TMDB: {attempts}; corrispondenze sicure: {accepted}; gruppi dubbi: {uncertain}; schede nuove/aggiornate: {new_files}')
    if dry_run:
        print('Anteprima: nessun file scritto e nessuna richiesta a TMDB.')
        return
    # Don't retain orphaned stale lookups indefinitely.
    lookups={k:v for k,v in lookups.items() if k in groups}
    # Reconstruct the current review list across runs rather than forgetting
    # cases considered in earlier batches. Only public title/year are stored.
    review = []
    for q, members in groups.items():
        rec = lookups.get(q, {})
        if rec.get('status') in {'ambiguo','non_trovato','senza_generi'}:
            review.append({'titolo':members[0]['title'], 'anno':members[0]['year'],
                           'motivo':rec['status'], 'copie':len(members)})
    review = sorted(review, key=lambda x:(x['motivo'], x['titolo']))[:500]
    updated = [save_json_if_different(meta_path, {'version':1,'files':entries}),
               save_json_if_different(states_path, {'version':1,'queries':lookups}),
               save_json_if_different(review_path, {'version':1,'da_verificare':review})]
    print('JSON aggiornati.' if any(updated) else 'Nessuna modifica ai JSON.')


def main():
    parser=argparse.ArgumentParser(description='Aggiorna in modo prudente i generi VAULT tramite TMDB.')
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--limit',type=int,default=120,help='Massimo richieste TMDB per esecuzione (default 120)')
    parser.add_argument('--dry-run',action='store_true',help='Mostra i film candidati senza interrogare TMDB')
    args=parser.parse_args()
    if not 1<=args.limit<=1000: parser.error('--limit deve essere fra 1 e 1000')
    token=os.environ.get('TMDB_READ_ACCESS_TOKEN','').strip()
    if not args.dry_run and not token:
        parser.error('Manca TMDB_READ_ACCESS_TOKEN. Configuralo come GitHub Actions Secret, non nel repository.')
    try: run(args.root, args.limit,args.dry_run,token)
    except (ValueError,FileNotFoundError,RuntimeError) as ex:
        print(f'ERRORE: {ex}',file=sys.stderr);sys.exit(1)


if __name__=='__main__':main()
