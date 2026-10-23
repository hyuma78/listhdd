#!/usr/bin/env python3
"""VAULT 3: metadati TMDB film / serie / anime, con credenziali solo server-side.

API gratuita v3; supporta TMDB_READ_ACCESS_TOKEN o TMDB_API_KEY come segreti Actions.
Dati video MAI caricati: invia a TMDB soltanto titoli/anni dei candidati.
"""
import argparse
from collections import defaultdict
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
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

BASE = 'https://api.themoviedb.org/3'
TODAY = datetime.now(timezone.utc).date()
EXT = {'.mkv','.mp4','.avi','.mov','.wmv','.m4v','.mpg','.mpeg','.ts','.m2ts','.webm','.flv','.vob','.divx','.iso','.3gp'}
GENRES = {28:'Azione',12:'Avventura',16:'Animazione',35:'Commedia',80:'Crime',99:'Documentario',18:'Drammatico',14:'Fantasy',878:'Fantascienza',10752:'Guerra',27:'Horror',10402:'Musicale',10749:'Romantico',36:'Storico',53:'Thriller',37:'Western',9648:'Thriller',10759:'Azione',10765:'Fantascienza',10762:'Animazione',10763:'Documentario',10764:'Documentario',10766:'Drammatico',10767:'Musicale',10768:'Guerra'}
EXTRAS = re.compile(r'(?i)^(extras?|extra disc|bonus|special features?|speciali|featurettes?|deleted scenes?|interviews?|making[ ._-]?of|behind the scenes|trailers?|teasers?|menu|sample|campioni|galleria fotografica|contenuti speciali)$')
EPISODE = re.compile(r'(?i)(?:\bS\s*\d{1,3}[ ._-]*E\s*\d{1,3}\b|\b\d{1,2}x\d{2,3}\b|\b(?:ep|episodio|episode)[ ._-]*\d{1,4}\b|\b(?:stagione|season)[ ._-]*\d{1,3}\b)')
YEAR = re.compile(r'(?<!\d)((?:19|20)\d\d)(?!\d)')
TECH = re.compile(r'(?i)\b(?:2160p|1080p|720p|576p|480p|4k|uhd|blu[ ._-]?ray|bdrip|bdmux|brrip|dvdrip|dvdscr|webrip|web[ ._-]?dl|webmux|remux|repack|x264|x265|h[ ._-]?264|h[ ._-]?265|hevc|aac|ac3|eac3|dts|hdtv|satrip|hdrip|hdr10|dovi|proper|repack|multi|ita|eng|jpn|jap|sub|subs|dual|audio|10bit|8bit)\b')
TV_FOLDERS = re.compile(r'(?i)\b(serie[ ._-]*tv|tv[ ._-]*series|tv[ ._-]*show|telefilm|series|stagion[ei]|seasons?|anime|cartoni|animazione)\b')
MOV_FOLDERS = re.compile(r'(?i)\b(film|movies?|cinema|cinematografia|horror|thriller|fantascienza|fantasy|sci[ ._-]?fi|commedie|azione)\b')
GENERIC = re.compile(r'(?i)^(?:film|movies?|cinema|serie[ ._-]*tv|series|telefilm|anime|cartoni|animazione|stagion[ei][ ._-]*\d+|season[ ._-]*\d+|s\d+|complete|completa|vari|varie|video|download|da[ ._-]*vedere|horror[ ._-]*thriller|sci[ ._-]*fi|fantascienza|commedia|commedie)$')

class Budget(Exception): pass
class NetworkPause(Exception): pass

def canon(value):
    s = unicodedata.normalize('NFKD',str(value or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[a-z0-9]+',s.casefold()))

def read_json(p, default):
    if not p.exists(): return default
    try: return json.loads(p.read_text(encoding='utf-8-sig'))
    except (ValueError,UnicodeError) as exc: raise ValueError(f'JSON non valido: {p}: {exc}') from exc

def write_json(p,obj):
    content=json.dumps(obj,ensure_ascii=False,indent=2,sort_keys=True)+'\n'
    if p.exists() and p.read_text(encoding='utf-8-sig')==content: return False
    p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_suffix(p.suffix+'.tmp')
    tmp.write_text(content,encoding='utf-8')
    tmp.replace(p)
    return True

def extract_year(s):
    return next((int(m[1]) for m in YEAR.finditer(s) if 1900<=int(m[1])<=TODAY.year),None)

def clean_title(raw):
    s=str(raw).replace('_',' ').replace('.', ' ')
    # Rimuovi prefissi estranei (tag release e numeri lista), non gli articoli.
    s=re.sub(r'^\s*\[[^\]]{1,25}\]\s*', '',s)
    s=re.sub(r'(?i)\b(?:19\d\d|20\d\d)\b.*$', '',s) if YEAR.search(s) else s
    m=TECH.search(s)
    if m:s=s[:m.start()]
    s=re.sub(r'\s*\[[^\]]+\]\s*$', '',s)
    s=re.sub(r'\s*\([^)]*(?:italiano|english|ita|jap|eng|dvdrip)[^)]*\)\s*$', '',s,flags=re.I)
    s=s.strip(' .-–_[]()')
    s=re.sub(r'(?i)\s+S\d{1,3}(?:\s*[-–]\s*S?\d{1,3})?$', '',s)
    s=re.sub(r'(?i)\s*\((?:US|UK|USA)\)\s*$', '',s)
    s=re.sub(r'(?i)\s*\((?:US|UK|USA)\s*$', '',s)
    s=re.sub(r'(?i)\s*\((?:US|UK|USA)\)?\s*\(?$', '',s)
    s=re.sub(r'\s+', ' ',s).strip(' .-–_[]()')
    return s

def plausible_title(s,with_year=False):
    value=canon(s)
    if value=='24':return True
    if len(value)<(2 if with_year else 4) or len(value.split())>14 or value.isdigit():return False
    if value in {'extra','trailer','special','video','sample','episodio','episode','film','animazione'}:return False
    if re.match(r'(?i)^(?:bdrip|bdmux|dvdrip|bluray|rip|1080p|720p|season|stagione)', s):return False
    return True

def categorize(f):
    name=str(f.get('nome') or '').strip()
    path=str(f.get('percorso_completo') or '').strip()
    if not name or not path or Path(name).suffix.lower() not in EXT:return None
    segments=path.replace('/','\\').split('\\')
    dirs=segments[1:-1]
    if any(EXTRAS.fullmatch(d.strip()) for d in dirs):return None
    if re.search(r'(?i)\b(?:sample|trailer|featurette|making[ ._-]?of|deleted[ ._-]?scene)\b',name):return None
    folder=' '.join(dirs)
    tv_context=bool(TV_FOLDERS.search(folder))
    film_context=bool(MOV_FOLDERS.search(folder))
    episodic=bool(EPISODE.search(name) or EPISODE.search(path))
    stem=Path(name).stem
    is_tv=episodic or (tv_context and not film_context and not extract_year(stem))
    if is_tv:
        # Episodi: il nome della serie si trova spesso nel contenitore, non nel titolo della puntata.
        before=EPISODE.split(stem,maxsplit=1)[0]
        before=clean_title(before)
        if plausible_title(before) and not GENERIC.fullmatch(before):
            title=before
        else:
            title=''
            for d in reversed(dirs):
                if GENERIC.fullmatch(d.strip()) or EPISODE.search(d) or EXTRAS.fullmatch(d.strip()): continue
                candidate=clean_title(d)
                if plausible_title(candidate): title=candidate;break
        if not title or not plausible_title(title):return None
        # Date dalle cartelle stagione poco affidabili; usiamo il titolo serie stesso.
        year=extract_year(stem) or next((extract_year(d) for d in reversed(dirs) if not GENERIC.fullmatch(d.strip()) and extract_year(d)),None)
        if ' stagione ' in canon(title):title=title.split(' Stagione ')[0]
        return 'tv',title,year
    # Film (inclusi film anime), non i singoli episodi.
    year=extract_year(stem)
    title=clean_title(stem)
    if not year and dirs:
        near=dirs[-1]
        year=extract_year(near)
        if (not plausible_title(title) or canon(title) in {'movie','film','video'}) and year:
            title=clean_title(near)
    if not plausible_title(title, bool(year)):return None
    # Senza anno ammettiamo solo un contesto cinematografico esplicito.
    if not year and not (film_context or (tv_context and 'anime' in canon(folder))):return None
    # Con anno, ammettiamo anche collezioni anime (lungometraggi) e altre cartelle.
    if len(dirs)>6:return None
    return 'movie',title,year

def build_groups(root):
    manifest=read_json(root/'data'/'manifest.json',{})
    disks=manifest.get('dischi',[])
    if not isinstance(disks,list) or not disks:raise ValueError('Manca data/manifest.json o non contiene dischi')
    groups=defaultdict(list)
    total=0; omitted=0
    for disk in disks:
        diskname=str(disk.get('nome') or '')
        filename=str(disk.get('file') or '')
        if not diskname or not re.fullmatch(r'[\w.\-]+\.json',filename) or filename=='manifest.json':raise ValueError(f'Manifest: disco non valido: {disk!r}')
        p=root/'data'/filename
        if not p.is_file():raise FileNotFoundError(f'{p} non trovato: verifica maiuscole/minuscole su GitHub')
        catalog=read_json(p,{})
        if not isinstance(catalog.get('catalogo_video'),list):raise ValueError(f'{p}: catalogo_video mancante')
        for f in catalog['catalogo_video']:
            if not isinstance(f,dict):continue
            total+=1
            c=categorize(f)
            if not c:omitted+=1;continue
            kind,title,year=c
            key=f'{kind}|{canon(title)}|{year or "?"}'
            groups[key].append({'file_key':diskname+'::'+str(f['percorso_completo']),'kind':kind,'title':title,'year':year})
    return groups,total,omitted

class TMDB:
    def __init__(self,token='',api_key='',limit=3000,rps=5.0,transport=None):
        self.token=token;self.api_key=api_key;self.limit=limit;self.rps=rps
        self.requests=0;self.last=0;self.transport=transport
        if not token and not api_key and transport is None:pass  # main() impone credenziali solo nelle esecuzioni reali
    def get(self,path,params=None):
        opts={'language':'it-IT',**(params or {})}
        if self.api_key and not self.token:opts['api_key']=self.api_key
        headers={'Accept':'application/json','User-Agent':'VaultPersonalMediaCatalog/3.0'}
        if self.token:headers['Authorization']='Bearer '+self.token
        url=BASE+path+'?'+urlencode(opts)
        for attempt in range(7):
            if self.requests>=self.limit:raise Budget()
            seconds=max(0,1/self.rps-(time.monotonic()-self.last))
            if seconds and self.transport is None:time.sleep(seconds)
            self.last=time.monotonic();self.requests+=1
            try:
                if self.transport is not None:return self.transport(path,opts)
                with urlopen(Request(url,headers=headers),timeout=25) as response:
                    return json.load(response)
            except HTTPError as e:
                if e.code in (401,403):raise ValueError(f'TMDB HTTP {e.code}: credenziale non valida o non autorizzata. Controlla i Secrets.') from None
                if e.code not in (429,500,502,503,504):raise NetworkPause(f'Errore TMDB HTTP {e.code}') from None
                if attempt==6:raise NetworkPause(f'TMDB HTTP {e.code} ripetuto, riproverò nella prossima esecuzione') from None
                try:
                    wait=float(e.headers.get('Retry-After') or 0)
                except (ValueError,TypeError):
                    try:wait=(parsedate_to_datetime(e.headers.get('Retry-After'))-datetime.now(timezone.utc)).total_seconds()
                    except Exception:wait=0
                wait=max(1,min(90,wait if wait>0 else 2**attempt))
                print(f'  TMDB HTTP {e.code}: pausa di {wait:.0f}s, tentativo {attempt+1}/7',flush=True)
                time.sleep(wait)
            except (TimeoutError,URLError) as e:
                if attempt==6:raise NetworkPause(f'Rete TMDB temporaneamente indisponibile: {type(e).__name__}') from None
                time.sleep(min(30,2**attempt))
        raise NetworkPause('Numero eccessivo di tentativi')

def resolve_match(kind,title,year,results):
    options=[]
    for row in results[:20]:
        if not isinstance(row,dict) or not isinstance(row.get('id'),int):continue
        released=str(row.get('release_date' if kind=='movie' else 'first_air_date') or '')[:4]
        actual=int(released) if released.isdigit() else None
        if year and (actual is None or abs(actual-year)>1):continue
        names=[row.get('title'),row.get('original_title')] if kind=='movie' else [row.get('name'),row.get('original_name')]
        sim=max(SequenceMatcher(None,canon(title),canon(s)).ratio() for s in names)
        if year and actual!=year and sim < .985:continue
        options.append((sim, row, actual))
    options.sort(key=lambda x:x[0],reverse=True)
    if not options:return None,'non_trovato'
    best,row,actual=options[0]
    runner=options[1][0] if len(options)>1 else 0
    threshold=.94 if year else .985
    if best < threshold or (runner>=.89 and best-runner<.085):return None,'ambiguo'
    if year is None and int(row.get('vote_count') or 0)<3:return None,'ambiguo'
    return row,None

def record_from_tmdb(kind,search,detail=None):
    d=detail or search
    year_string=str(d.get('release_date' if kind=='movie' else 'first_air_date') or '')[:4]
    year=int(year_string) if year_string.isdigit() else None
    if detail and isinstance(detail.get('genres'),list): genre_ids=[x.get('id') for x in detail['genres'] if isinstance(x,dict)]
    else:genre_ids=search.get('genre_ids') or []
    genre_set={GENRES[x] for x in genre_ids if x in GENRES}
    if 10759 in genre_ids: genre_set.add('Avventura')
    if 10765 in genre_ids: genre_set.add('Fantasy')
    genres=sorted(genre_set)
    cast=(d.get('credits') or {}).get('cast') or []
    crew=(d.get('credits') or {}).get('crew') or []
    creators=d.get('created_by') or []
    directors=([c.get('name') for c in crew if c.get('job')=='Director'] if kind=='movie' else [c.get('name') for c in creators])
    return {'year':year,'genres':genres,'source':'TMDB','type':kind,
      'tmdb_id':search['id'],'tmdb_title':str(d.get('title' if kind=='movie' else 'name') or ''),
      'tmdb_url':f'https://www.themoviedb.org/{kind}/{search["id"]}',
      'overview':str(d.get('overview') or '')[:1800],
      'poster_path':str(d.get('poster_path') or ''),
      'rating':round(float(d.get('vote_average') or 0),1),
      'votes':int(d.get('vote_count') or 0),
      'director':', '.join(str(s) for s in directors[:3] if s),
      'cast':[str(c.get('name')) for c in cast[:6] if c.get('name')],
      'runtime': d.get('runtime') if kind=='movie' else None,
      'seasons':d.get('number_of_seasons') if kind=='tv' else None,
      'episodes':d.get('number_of_episodes') if kind=='tv' else None,
      'checked_at':TODAY.isoformat(),'details_complete':bool(detail)}

def recent(s,days):
    try:return 0 <= (TODAY-date.fromisoformat(s)).days < days
    except (TypeError,ValueError):return False

def run(root,client,dry_run=False,refresh_days=90):
    groups,total,ignored=build_groups(root)
    meta_p=root/'data'/'film_metadata.json';state_p=root/'data'/'tmdb_state.json';review_p=root/'data'/'tmdb_review.json'
    meta=read_json(meta_p,{'version':1,'files':{}})
    state=read_json(state_p,{'version':2,'queries':{}})
    if not isinstance(meta.get('files'),dict) or not isinstance(state.get('queries'),dict):raise ValueError('Metadati o state non validi')
    files=meta['files']; queries=state['queries']
    types=defaultdict(int)
    for name,items in groups.items(): types[items[0]['kind']]+=1
    print(f'Archivio: {total} file, {len(groups)} titoli candidati unici ({types["movie"]} film, {types["tv"]} serie/anime); esclusi/extra: {ignored}',flush=True)
    if dry_run:
        for name,members in list(sorted(groups.items(),key=lambda kv:-len(kv[1])))[:16]:
            print(f'  [anteprima] {members[0]["kind"]}: {members[0]["title"]} ({members[0]["year"] or "anno ?"}) — {len(members)} file')
        print('Anteprima: nessuna richiesta a TMDB e nessun file modificato.')
        return
    matches=ambiguous=updated=skipped=0
    def propagate(members,entry):
        nonlocal updated
        for m in members:
            key=m['file_key']
            existing=files.get(key,{})
            if existing and str(existing.get('source','')).lower() not in ('tmdb',):continue
            if existing!=entry:files[key]=dict(entry);updated+=1
    def persist():
        # Non cancellare i record precedenti se un disco è stato temporaneamente scollegato.
        write_json(meta_p,{'version':1,'files':files})
        write_json(state_p,{'version':2,'queries':queries})
        review=[]
        for query,members in groups.items():
            r=queries.get(query,{})
            if r.get('status') in ('ambiguo','non_trovato','senza_generi'):
                review.append({'titolo':members[0]['title'],'anno':members[0]['year'],
                               'tipo':members[0]['kind'],'motivo':r['status'],'copie':len(members)})
        write_json(review_p,{'version':2,'da_verificare':sorted(review,key=lambda x:(x['motivo'],x['titolo']))[:3000]})
    ordered=sorted(groups.items(),key=lambda kv:(-len(kv[1]),0 if kv[1][0]['year'] else 1,kv[0]))
    try:
        for query,members in ordered:
            exemplar=members[0]
            keys=[f['file_key'] for f in members]
            old=queries.get(query,{})
            # Migrazione della versione precedente: se una copia possiede già
            # l'ID TMDB verificato, recupera direttamente il dettaglio senza
            # ripetere la ricerca. Non modificare nessuna scheda manuale.
            if not old:
                existing_tmdb=next((files[k] for k in keys if isinstance(files.get(k),dict)
                                    and str(files[k].get('source','')).lower()=='tmdb'
                                    and isinstance(files[k].get('tmdb_id'),int)
                                    and files[k]['tmdb_id']>0),None)
                if existing_tmdb and exemplar['kind']=='movie':
                    old={'status':'pending','kind':'movie','checked_at':TODAY.isoformat(),
                         'search':{'id':existing_tmdb['tmdb_id'],
                         'title':existing_tmdb.get('tmdb_title') or exemplar['title'],
                         'release_date':str(existing_tmdb.get('year') or exemplar['year'] or '')+'-01-01',
                         'genre_ids':[], 'vote_count':0}}
                    queries[query]=old
            current=old.get('data') if old.get('status')=='ok' else None
            # Cache: le copie extra dello stesso titolo ereditano i dati subito.
            if current and recent(current.get('checked_at'),refresh_days):
                propagate(members,current);skipped+=1;continue
            # Ripartenza di una ricerca confermata ma con dettaglio non ancora richiesto.
            pending=old.get('search') if old.get('status')=='pending' else None
            if old.get('status') in ('ambiguo','non_trovato','senza_generi') and recent(old.get('checked_at'),60):
                skipped+=1;continue
            kind,title,year=exemplar['kind'],exemplar['title'],exemplar['year']
            if pending and old.get('kind')!=kind:pending=None
            if pending:
                result=pending
            else:
                opts={'query':title,'include_adult':'false','page':1}
                if year:opts['year' if kind=='movie' else 'first_air_date_year']=year
                response=client.get('/search/'+kind,opts)
                result,why=resolve_match(kind,title,year,response.get('results') or [])
                if result is None:
                    queries[query]={'status':why,'checked_at':TODAY.isoformat()}
                    ambiguous+=1
                    continue
                queries[query]={'status':'pending','kind':kind,'search':result,'checked_at':TODAY.isoformat()}
            # Richiedi dettagli, cast e creatori solo per titolo riconosciuto.
            detail=client.get(f'/{kind}/{result["id"]}',{'append_to_response':'credits'})
            entry=record_from_tmdb(kind,result,detail)
            if not entry['genres']:
                queries[query]={'status':'senza_generi','checked_at':TODAY.isoformat()}
                ambiguous+=1;continue
            queries[query]={'status':'ok','checked_at':TODAY.isoformat(),'data':entry}
            propagate(members,entry)
            matches+=1
            if (matches+ambiguous)%50==0:
                persist();print(f'  API: {client.requests}; riconosciuti {matches}; dubbi {ambiguous}; file aggiornati {updated}',flush=True)
    except Budget:
        print('Budget richieste raggiunto: la prossima esecuzione riprenderà dalle ricerche ancora da fare.',flush=True)
    except NetworkPause as exc:
        print(f'Pausa per risposta TMDB/rete: {exc}. Salvo il lavoro e riprenderò nel prossimo workflow.',flush=True)
    finally:
        persist()
    print(f'Richieste TMDB (incluse eventuali ripetizioni): {client.requests}; titoli riconosciuti in questa esecuzione: {matches}; da verificare: {ambiguous}; schede aggiornate: {updated}; recuperate da cache: {skipped}',flush=True)

def main():
    p=argparse.ArgumentParser(description='VAULT: film, serie TV e anime via TMDB gratuito')
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    p.add_argument('--max-requests',type=int,default=3000)
    p.add_argument('--rps',type=float,default=5.0)
    p.add_argument('--dry-run',action='store_true')
    a=p.parse_args()
    if not 1<=a.max_requests<=10000:p.error('--max-requests deve essere tra 1 e 10000')
    if not .5<=a.rps<=10:p.error('--rps deve essere fra 0.5 e 10')
    token=os.environ.get('TMDB_READ_ACCESS_TOKEN','').strip()
    api_key=os.environ.get('TMDB_API_KEY','').strip()
    if not a.dry_run and not (token or api_key):p.error('Crea un GitHub Secret TMDB_READ_ACCESS_TOKEN oppure TMDB_API_KEY')
    try:run(a.root,TMDB(token,api_key,a.max_requests,a.rps),a.dry_run)
    except (ValueError,FileNotFoundError) as exc:print('ERRORE:',exc,file=sys.stderr);sys.exit(1)

if __name__=='__main__':main()
