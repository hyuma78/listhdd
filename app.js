/* Vault 2.0 — Vanilla JS, nessuna dipendenza e nessuna richiesta a servizi esterni. */
(() => {
  'use strict';
  const $ = (selector) => document.querySelector(selector);
  const $page = $('#pageContent');
  const nf = new Intl.NumberFormat('it-IT');
  const decimal = new Intl.NumberFormat('it-IT', {maximumFractionDigits: 2});
  const MAX_PAGE = 70;
  const DEFAULT_MANIFEST = ['Arietty.json','Chopper.json','Hikvision2TB.json','Maxtor15G.json','NASWare.json','SOA.json','Seagate0FM.json','Seagate1TB.json','Seagate878.json','SeagateGMF.json','SeagateR9H.json','TR3NTON.json','Usopp.json','WD207.json','WD659.json','WD_Sarah_2TB.json','franky.json','nami.json','rufy.json','zoro.json'];
  const VIDEO_TYPES = ['mkv','mp4','avi','mov','wmv','m4v','mpg','mpeg','ts','m2ts','webm','flv','vob','3gp','divx','iso'];
  const GENRES = ['Azione','Avventura','Animazione','Biografico','Commedia','Crime','Documentario','Drammatico','Fantasy','Fantascienza','Guerra','Horror','Musicale','Romantico','Storico','Thriller','Western'];
  const CURRENT_YEAR = 2026;
  const TITLES = {overview:'Panoramica',drives:'I miei dischi',search:'Esplora file',favorites:'Preferiti',duplicates:'Doppioni potenziali'};
  const state = {
    page: 'overview', disks: new Map(), files: [], favorites: new Set(), query: '',
    diskFilter: '', categoryFilter:'', extFilter:'', sizeFilter:'', genreFilter:'', yearFilter:'', qualityFilter:'', languageFilter:'', sort:'relevance', driveSort:'name',
    publishedMetadata:{}, localMetadata:{}, metadataFromSite:false,
    pageLimit: MAX_PAGE, openDisk:'', folderPath:[], folderLimit:120, trees:new Map(),
    duplicateGroups:[], failed:[], temporaryImports:0, loading:false
  };
  const store = {
    get(key,fallback){try{return JSON.parse(localStorage.getItem('vault-'+key)) ?? fallback;}catch{return fallback;}},
    set(key,val){try{localStorage.setItem('vault-'+key,JSON.stringify(val));}catch{/* Storage disabilitato */}}
  };
  state.favorites = new Set(store.get('favorites',[]));
  state.localMetadata = store.get('movie-metadata',{});
  const formatCount = n => nf.format(n);
  const fmtGB = n => (Number.isFinite(n) ? (Math.abs(n) >= 1000 ? decimal.format(n / 1000) + ' TB' : decimal.format(n) + ' GB') : '—');
  const icon = name => `<svg class="icon" aria-hidden="true"><use href="#ico-${name}"></use></svg>`;
  const html = v => String(v ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  const normalize = value => String(value ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase('it-IT').replace(/[^\p{L}\p{N}]+/gu,' ').trim().replace(/\s+/g,' ');
  const sizeGB = value => {
    if(typeof value === 'number')return Number.isFinite(value)?value:NaN;
    const found = String(value ?? '').trim().match(/([\d.,]+)\s*(T[BIO]?|G[BIO]?|M[BIO]?|K[BIO]?|B)?/i);
    if(!found)return NaN;
    const s = found[1].replace(/\s/g,'');
    const number = Number(s.includes(',') ? s.replace(/\./g,'').replace(',','.') : s);
    const unit = (found[2]||'GB').toUpperCase()[0];
    return number * ({T:1000,G:1,M:.001,K:.000001,B:.000000001}[unit]||1);
  };
  const clampPercent = (v) => Math.max(0,Math.min(100,Number.isFinite(v)?v:0));
  const pct = d => d.total > 0 ? clampPercent(100 * d.used/d.total) : 0;
  const status = d => pct(d)>=95 ? 'danger' : pct(d)>=90 ? 'warning' : 'good';
  const diskColor = d => status(d)==='danger'?'danger':status(d)==='warning'?'warning':'';
  const fileKey = f => f.disk+'::'+f.path;
  const fileCategory = path => {
    const s = normalize(path);
    if(/\b(anime|animazione|cartoni|naruto|one piece|bleach)\b/.test(s))return 'Anime e cartoni';
    if(/\b(serie tv|serie|series|stagione|season)\b/.test(s) || /\bs\d{1,2}e\d{1,3}\b/i.test(path))return 'Serie TV';
    if(/\b(documentari|documentary|documentario|documentaries)\b/.test(s))return 'Documentari';
    if(/\b(film|cinema|movies|movie)\b/.test(s)||/\b(horror thriller|horror thiller|sci fi fantasy|fantascienza fantasy)\b/.test(s))return 'Film';
    return 'Altro';
  };
  // I metadati dedotti dal nome del file NON sono una verifica del film.
  // Il genere viene dedotto SOLO da cartelle esplicitamente organizzate per genere.
  function inferYear(filename, path, category){
    const find = text => {
      const matches=[...String(text).matchAll(/(?:^|[^0-9])(19[0-9]{2}|20[0-2][0-9])(?=$|[^0-9])/g)];
      const plausible=matches.map(m=>Number(m[1])).filter(y=>y>=1900&&y<=CURRENT_YEAR);
      return plausible.length?plausible[0]:null;
    };
    const own=find(filename);
    if(own)return own;
    // In particolare nelle serie la data in cartella può non essere l'anno della singola puntata.
    if(category==='Serie TV')return null;
    const dirs=String(path).replace(/\\/g,'/').split('/').slice(1,-1);
    return dirs.length?find(dirs[dirs.length-1]):null;
  }
  function inferGenres(path){
    const dirs=String(path).replace(/\\/g,'/').split('/').slice(1,-1).map(d=>normalize(d));
    const found=new Set();
    for(const d of dirs){
      if(/^(film )?horror( thriller| thiller)?$/.test(d)||/^horror thriller$/.test(d)){
        found.add('Horror');if(d.includes('thriller')||d.includes('thiller'))found.add('Thriller');
      }
      if(/^(film )?thriller$/.test(d))found.add('Thriller');
      if(/^(sci fi fantasy|fantascienza fantasy|fantascienza|sci fi)$/.test(d)){
        found.add('Fantascienza');if(d.includes('fantasy'))found.add('Fantasy');
      }
      if(/^(film )?fantasy$/.test(d))found.add('Fantasy');
      if(/^(film )?(commedia|commedie)$/.test(d))found.add('Commedia');
      if(/^(film )?western$/.test(d))found.add('Western');
      if(/^(film )?drammatici?$/.test(d))found.add('Drammatico');
      if(/^(film )?avventura$/.test(d))found.add('Avventura');
    }
    return [...found];
  }
  function inferQuality(filename, path){
    // Nelle release i numeri in nome sono indizi del formato, non una misura tecnica del file.
    const val=String(filename)+' '+String(path).split(/[\\/]/).slice(-2,-1).join(' ');
    if(/(?:^|[^0-9])(?:2160p|4k|uhd)(?:$|[^0-9])/i.test(val))return '4K';
    if(/(?:^|[^0-9])1080[pi]?(?:$|[^0-9])/i.test(val))return '1080p';
    if(/(?:^|[^0-9])720[pi]?(?:$|[^0-9])/i.test(val))return '720p';
    if(/(?:^|[^0-9])(?:480p|576p|dvdrip|satrip|vhsrip)(?:$|[^0-9])/i.test(val))return 'SD';
    return '';
  }
  function inferLanguages(filename){
    const tokens=String(filename).replace(/[^a-zA-Z]+/g,' ').toUpperCase().split(/\s+/);
    const seen=new Set(tokens);
    return [['ITA',['ITA','ITALIAN','ITALIANO']],['ENG',['ENG','ENGLISH']],['JAP',['JAP','JPN','JAPANESE']]]
      .filter(([,v])=>v.some(t=>seen.has(t))).map(([code])=>code);
  }
  function validMetadata(m){
    if(!m||typeof m!=='object'||Array.isArray(m))return null;
    const year=m.year===''||m.year===null?null:Number(m.year);
    const genres=Array.isArray(m.genres)?m.genres.filter(g=>GENRES.includes(g)):[];
    return {year:Number.isInteger(year)&&year>=1900&&year<=CURRENT_YEAR?year:null,genres,source:typeof m.source==='string'?m.source.slice(0,40):'Manuale'};
  }
  function enrichFile(f){
    const remote=state.publishedMetadata[f.key];
    const local=state.localMetadata[f.key];
    const selected=validMetadata(local||remote);
    f.year=selected?selected.year:f.guessedYear;
    f.genres=selected?selected.genres:f.guessedGenres;
    f.metadataSource=selected?selected.source:(f.guessedGenres.length?'Cartella':'Nome file');
    return f;
  }
  function applyAllMetadata(){for(const f of state.files)enrichFile(f);}
  const renderProgress = (percent, color='') => `<div class="progress-track" role="meter" aria-label="Spazio occupato" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(percent)}"><div class="progress-fill ${color}" style="width:${percent.toFixed(2)}%"></div></div>`;
  const diskEntries = () => Array.from(state.disks.values()).sort((a,b)=>a.name.localeCompare(b.name,'it',{sensitivity:'base'}));
  function calculateStats(){
    const disks = diskEntries().filter(d=>Number.isFinite(d.total)&&d.total>0);
    const total = disks.reduce((a,d)=>a+d.total,0), free = disks.reduce((a,d)=>a+d.free,0);
    return {total,free,used:total-free,files:state.files.length,drives:state.disks.size,full:disks.filter(d=>pct(d)>=90).length};
  }
  function ensurePage(page){return Object.prototype.hasOwnProperty.call(TITLES,page)?page:'overview';}
  function toast(message,isError=false){
    const div = document.createElement('div');div.className='toast'+(isError?' error':'');
    div.innerHTML=icon(isError?'alert':'check');
    const span=document.createElement('span');span.textContent=message;div.append(span);$('#toasts').append(div);
    setTimeout(()=>div.remove(),4300);
  }
  function setTheme(mode){
    document.documentElement.classList.toggle('light',mode==='light');store.set('theme',mode);
    $('#themeButton').innerHTML=icon(mode==='light'?'moon':'sun');
    $('#themeButton').title=mode==='light'?'Passa al tema scuro':'Passa al tema chiaro';
  }
  function makeFile(disk, raw, index){
    const name=String(raw.nome??''),path=String(raw.percorso_completo??name),size=sizeGB(raw.dimensione);
    const ext=(name.match(/\.([^.\\/\s]+)$/)||[])[1]?.toLowerCase()||'—';
    const parts=path.replace(/\//g,'\\').split('\\').filter(Boolean);
    if(parts[0]&&/^[A-Za-z]:$/.test(parts[0]))parts.shift();
    const category=fileCategory(path);
    const guessedYear=inferYear(name,path,category),guessedGenres=inferGenres(path);
    const f={id:index,disk,name,path,formattedSize:String(raw.dimensione??'—'),size,ext,category,parts,
      key:disk+'::'+path,guessedYear,guessedGenres,year:guessedYear,genres:guessedGenres,
      quality:inferQuality(name,path),languages:inferLanguages(name),metadataSource:'',
      search:normalize(name+' '+path+' '+disk), nameSearch:normalize(name)};
    return enrichFile(f);
  }
  function reindex(){
    let counter=0;state.files=[];state.trees.clear();
    for(const d of diskEntries()){
      d.files = (d.rawFiles||[]).filter(f=>f&&typeof f==='object'&&typeof f.nome==='string'&&typeof f.percorso_completo==='string').map(f=>makeFile(d.name,f,counter++));
      state.files.push(...d.files);
    }
    const groups = new Map();
    for(const f of state.files){
      if(!f.name||!Number.isFinite(f.size))continue;
      const key=normalize(f.name)+'|'+f.size.toFixed(2);
      if(!groups.has(key))groups.set(key,[]);
      groups.get(key).push(f);
    }
    state.duplicateGroups=Array.from(groups.values()).filter(g=>g.length>1&&new Set(g.map(f=>f.disk)).size>1).sort((a,b)=>b[0].size-a[0].size);
    $('#navDriveCount').textContent=state.disks.size;
    updateFavoriteCount();
  }
  function updateFavoriteCount(){ $('#navFavCount').textContent=state.files.filter(f=>state.favorites.has(f.key)).length; }
  function mapDisk(name, json){
    if(!json || !Array.isArray(json.catalogo_video))throw Error('Manca catalogo_video (array)');
    const diskInfo = json.informazioni_disco || {};
    const total=sizeGB(diskInfo.totale),freeReported=sizeGB(diskInfo.libero);
    const free=Number.isFinite(freeReported)&&freeReported>=0 ? Math.min(freeReported, Number.isFinite(total)?total:Infinity) : NaN;
    const used = Number.isFinite(total)&&Number.isFinite(free) ? Math.max(0,total-free) : NaN;
    return {name,rawFiles:json.catalogo_video,total,free,used,reportedTotal:diskInfo.totale??'—',reportedFree:diskInfo.libero??'—'};
  }
  async function fetchJSON(url){
    const response=await fetch(url,{cache:'no-store'});
    if(!response.ok)throw Error(`HTTP ${response.status}`);
    return response.json();
  }
  async function loadAll(){
    if(state.loading)return;
    state.loading=true;$('#statusMessage').hidden=false;$('#statusMessage').innerHTML='<span class="spinner"></span> Caricamento dei cataloghi in corso…';
    $('#loadErrors').hidden=true;
    const loaded = new Map();const failures=[];
    let entries=[];
    try {
      const manifest=await fetchJSON('data/manifest.json');
      entries=Array.isArray(manifest.dischi)?manifest.dischi:[];
    } catch { /* Il fallback permette di utilizzare anche il formato originale. */ }
    if(!entries.length)entries=DEFAULT_MANIFEST.map(file=>({nome:file.slice(0,-5),file}));
    entries=entries.filter(e=>e&&typeof e.file==='string'&&/^[\w\-.]+\.json$/i.test(e.file)&&e.file.toLowerCase()!=='manifest.json');
    const results=await Promise.allSettled(entries.map(async item=>{
      const data=await fetchJSON('data/'+item.file);
      const name=String(item.nome||item.file.replace(/\.json$/i,''));
      return mapDisk(name,data);
    }));
    results.forEach((res,i)=>{
      if(res.status==='fulfilled'){const d=res.value;loaded.set(d.name,d);}
      else failures.push(entries[i].file+' ('+(res.reason?.message||'errore')+')');
    });
    if(loaded.size||!state.disks.size)state.disks=loaded;
    state.failed=failures;
    try{
      const saved=await fetchJSON('data/film_metadata.json');
      state.publishedMetadata=(saved&&saved.files&&typeof saved.files==='object'&&!Array.isArray(saved.files))?saved.files:{};
      state.metadataFromSite=true;
    }catch{
      // Il file metadati è opzionale: nessun errore se non ancora pubblicato.
      state.publishedMetadata={};state.metadataFromSite=false;
    }
    reindex();state.loading=false;
    $('#statusMessage').hidden=true;
    $('#loadErrors').hidden=!failures.length;
    if(failures.length)$('#loadErrors').innerHTML=`${icon('alert')} <strong>Attenzione: ${failures.length} cataloghi non caricati.</strong> ${html(failures.join(' · '))}. Controlla i nomi dei file e i percorsi nella cartella data/.`;
    $('#footerMeta').textContent=`${formatCount(state.files.length)} file · ${state.disks.size} dischi · dati da scansioni locali`;
    render();
    if(!loaded.size)toast('Non riesco a leggere i JSON: controlla data/manifest.json e i file della cartella data/.',true);
  }
  function navigate(page){
    state.page=ensurePage(page);state.pageLimit=MAX_PAGE;
    history.replaceState(null,'','#'+state.page);
    document.querySelectorAll('[data-nav]').forEach(el=>el.classList.toggle('active',el.dataset.nav===state.page));
    $('#crumbCurrent').textContent=TITLES[state.page];
    render();
    window.scrollTo({top:0,behavior:'instant'});
  }
  function render(){
    if(state.loading)return;
    const pages={overview:renderOverview,drives:renderDrives,search:()=>renderLibrary('search'),favorites:()=>renderLibrary('favorites'),duplicates:renderDuplicates};
    $page.innerHTML=pages[state.page]();
  }
  function renderStats(){
    const s=calculateStats(),usedPercent=s.total?clampPercent(100*s.used/s.total):0;
    const data=[
      {title:'I miei video',value:formatCount(s.files),sub:'File presenti nei cataloghi',symbol:'film',color:''},
      {title:'I miei HDD',value:formatCount(s.drives),sub:'Dischi catalogati',symbol:'drive',color:'cyan'},
      {title:'Spazio occupato',value:fmtGB(s.used),sub:decimal.format(usedPercent)+'% della capacità totale',symbol:'trending',color:'green'},
      {title:'Spazio disponibile',value:fmtGB(s.free),sub:s.full?s.full+' dischi oltre il 90%':'Capacità ancora libera',symbol:'layers',color:'orange'}
    ];
    return `<div class="stat-grid">${data.map(x=>`<div class="stat-card"><div class="stat-header"><span class="stat-title">${x.title}</span><span class="stat-icon ${x.color}">${icon(x.symbol)}</span></div><div class="stat-value">${x.value}</div><div class="stat-sub">${x.sub}</div></div>`).join('')}</div>`;
  }
  function renderOverview(){
    const s=calculateStats(), percent=s.total?pct({total:s.total,used:s.used}):0;
    const disks=diskEntries().filter(d=>Number.isFinite(d.total)).sort((a,b)=>pct(b)-pct(a));
    const topCategories = Object.entries(state.files.reduce((a,f)=>(a[f.category]=(a[f.category]||0)+1,a),{})).sort((a,b)=>b[1]-a[1]);
    const biggest=[...state.files].filter(f=>Number.isFinite(f.size)).sort((a,b)=>b.size-a.size).slice(0,5);
    const pressure=disks.filter(d=>pct(d)>=90).slice(0,5);
    return `${renderStats()}
      <div class="section-head"><div><h2>Il mio archivio in numeri</h2><p>File, spazio e contenuti dei miei dischi.</p></div><button class="link-btn" data-action="drives">Esplora dischi ${icon('arrow')}</button></div>
      <div class="dashboard-grid">
        <section class="panel"><div class="panel-head"><h3>Capacità di archiviazione</h3><small>Totale di tutti i dischi</small></div><div class="total-usage-top"><strong>${fmtGB(s.used)}</strong><small>su ${fmtGB(s.total)}</small></div>${renderProgress(percent,percent>=95?'danger':percent>=90?'warning':'')}
        <div class="usage-legend"><span><i class="legend-dot"></i>Occupato ${decimal.format(percent)}%</span><span><i class="legend-dot free"></i>Disponibile ${decimal.format(100-percent)}%</span></div>
        <div class="mini-drive-list">${disks.slice(0,6).map(d=>`<div class="mini-drive-row"><button data-open-disk="${html(d.name)}" title="Apri ${html(d.name)}">${html(d.name)}</button>${renderProgress(pct(d),diskColor(d))}<span class="percent">${decimal.format(pct(d))}%</span></div>`).join('')||'<p class="muted-modal">Nessuna capacità registrata.</p>'}</div></section>
        <section class="panel"><div class="panel-head"><h3>Cosa c'è nell'archivio</h3><small>Classificazione indicativa</small></div><div class="category-list">${topCategories.map(([cat,n])=>`<div class="category-row"><strong>${html(cat)}</strong><div class="category-bar"><span style="width:${Math.max(1,n/state.files.length*100)}%"></span></div><span class="number">${formatCount(n)}</span></div>`).join('')||'<p>Nessun file disponibile.</p>'}</div><div class="info-banner" style="margin-top:27px;margin-bottom:0">${icon('info')}<span>Le categorie sono dedotte dai nomi delle cartelle e dei file. Non sono metadati verificati.</span></div></section>
      </div>
      <div class="section-head"><div><h2>Da controllare</h2><p>File più grandi e dischi con poco spazio.</p></div></div>
      <div class="overview-bottom"><section class="panel"><div class="panel-head"><h3>I file più pesanti</h3><button class="link-btn" data-action="search">Vedi tutti ${icon('arrow')}</button></div><div class="simple-list">${biggest.map(f=>`<div class="simple-list-item"><div class="simple-item-left"><span class="simple-file-icon">${icon('film')}</span><div class="simple-item-text"><strong title="${html(f.name)}">${html(f.name)}</strong><span>${html(f.disk)}</span></div></div><strong class="size">${fmtGB(f.size)}</strong></div>`).join('')||'Nessun file'}</div></section>
      <section class="panel"><div class="panel-head"><h3>Dischi quasi pieni</h3><button class="link-btn" data-action="drives">Vedi tutti ${icon('arrow')}</button></div><div class="simple-list">${pressure.map(d=>`<div class="simple-list-item"><div class="simple-item-left"><span class="simple-file-icon">${icon('drive')}</span><div class="simple-item-text"><strong>${html(d.name)}</strong><span>${fmtGB(d.free)} liberi su ${fmtGB(d.total)}</span></div></div><span class="small-pill ${status(d)}">${decimal.format(pct(d))}%</span></div>`).join('')||'<div class="empty-state"><h3>Nessun disco critico</h3><p>Non risultano unità occupate oltre il 90%.</p></div>'}</div></section></div>`;
  }
  function sortedDisks(){
    const arr=diskEntries();switch(state.driveSort){
      case 'usage':return arr.sort((a,b)=>pct(b)-pct(a));
      case 'free':return arr.sort((a,b)=>(b.free||0)-(a.free||0));
      case 'files':return arr.sort((a,b)=>b.files.length-a.files.length);
      case 'capacity':return arr.sort((a,b)=>(b.total||0)-(a.total||0));
      default:return arr;
    }
  }
  function renderDrives(){
    const arr=sortedDisks();
    return `<div class="page-head"><div><span class="mini-label">DISCHI CATALOGATI</span><h2>I miei hard disk</h2><p>${formatCount(arr.length)} unità · seleziona un disco per vedere le sue cartelle.</p></div><button class="btn btn-soft" data-action="export">${icon('download')} Esporta CSV</button></div>
    <div id="driveInspector">${state.openDisk?renderInspector():''}</div>
    <div class="drive-toolbar"><span class="toolbar-label">Ordina per</span><select class="input-select" id="driveSort" aria-label="Ordina dischi">
    ${[['name','Nome (A–Z)'],['usage','Più pieni'],['free','Più spazio libero'],['capacity','Capacità maggiore'],['files','Più file']].map(([k,v])=>`<option value="${k}" ${state.driveSort===k?'selected':''}>${v}</option>`).join('')}</select><span class="toolbar-label" style="margin-left:auto">Occupazione basata sull'ultima scansione</span></div>
    <div class="drive-grid">${arr.map(d=>{
      const p=pct(d),label=status(d)==='danger'?'Quasi pieno':status(d)==='warning'?'Spazio in esaurimento':'Disponibile';
      return `<button class="drive-card ${state.openDisk===d.name?'selected':''}" data-open-disk="${html(d.name)}"><div class="drive-card-top"><span class="drive-glyph">${icon('drive')}</span><span class="drive-name-group"><strong>${html(d.name)}</strong><small>${fmtGB(d.total)} di capacità</small></span><span class="small-pill ${status(d)}">${decimal.format(p)}%</span></div><div class="drive-capacity"><strong>${fmtGB(d.used)}</strong><span>occupati</span></div>${renderProgress(p,diskColor(d))}<div class="drive-card-foot"><span>${formatCount(d.files.length)} video · ${fmtGB(d.free)} liberi</span><span class="open">Esplora ${icon('arrow')}</span></div><div class="drive-status ${status(d)}"><span></span>${label}</div></button>`;
    }).join('')}</div>`;
  }
  function getFolderRoot(disk){
    if(state.trees.has(disk))return state.trees.get(disk);
    const root={name:disk,folders:new Map(),files:[]};
    for(const f of state.disks.get(disk)?.files||[]){
      let node=root;
      for(const part of f.parts.slice(0,-1)){
        if(!node.folders.has(part))node.folders.set(part,{name:part,folders:new Map(),files:[]});
        node=node.folders.get(part);
      }
      node.files.push(f);
    }
    state.trees.set(disk,root);return root;
  }
  function getFolderNode(){
    let node=getFolderRoot(state.openDisk);
    for(const folder of state.folderPath){node=node?.folders.get(folder);if(!node)break;}
    return node;
  }
  function renderInspector(){
    const d=state.disks.get(state.openDisk);if(!d)return '';
    const node=getFolderNode()||getFolderRoot(d.name);
    const folders=Array.from(node.folders.values()).sort((a,b)=>a.name.localeCompare(b.name,'it',{numeric:true}));
    const files=node.files.slice().sort((a,b)=>a.name.localeCompare(b.name,'it',{numeric:true}));
    const entries=[...folders.map((dir,i)=>({dir,i})),...files.map(f=>({f}))];
    const visible=entries.slice(0,state.folderLimit);
    const pathPrefix=state.folderPath.join('\\');
    return `<section class="drive-inspector" aria-label="Esplora ${html(d.name)}" id="inspectorPanel">
      <div class="inspector-top"><div class="inspector-info"><span class="mini-label">CARTELLE DEL DISCO</span><h3>${html(d.name)}</h3><p>${formatCount(d.files.length)} video · ${fmtGB(d.free)} liberi · ${decimal.format(pct(d))}% occupato</p></div>
      <div class="inspector-action"><button class="btn btn-primary" data-search-disk="${html(d.name)}">${icon('search')} Cerca in questo disco</button><button class="icon-btn" data-close-disk="1" title="Chiudi esplora disco" aria-label="Chiudi esplora disco">${icon('x')}</button></div></div>
      <nav class="folder-toolbar" aria-label="Percorso cartelle"><button class="breadcrumb-button ${state.folderPath.length?'':'current'}" data-folder-level="0">${icon('drive')} ${html(d.name)}</button>${state.folderPath.map((part,i)=>`<span class="breadcrumb-separator">/</span><button class="breadcrumb-button ${i===state.folderPath.length-1?'current':''}" data-folder-level="${i+1}" title="${html(part)}">${html(part)}</button>`).join('')}</nav>
      <div class="folder-content"><div class="filter-summary"><span><strong>${formatCount(folders.length)}</strong> cartelle · <strong>${formatCount(files.length)}</strong> file in questa posizione</span>${pathPrefix?`<span class="active-scope">${icon('folder')} ${html(pathPrefix)}</span>`:''}</div>
      ${visible.map(entry=>entry.dir?
        `<div class="folder-table-row"><span class="folder-glyph">${icon('folder')}</span><button class="folder-entry-button" data-folder-open="${entry.i}">${html(entry.dir.name)}</button><span class="folder-meta">Cartella</span><div class="folder-actions"><button aria-label="Apri cartella" title="Apri cartella" data-folder-open="${entry.i}">${icon('chevron')}</button></div></div>`:
        `<div class="folder-table-row"><span class="folder-glyph file-glyph">${icon('film')}</span><div style="min-width:0;flex:1"><span class="folder-entry-button">${html(entry.f.name)}</span></div><span class="folder-meta">${fmtGB(entry.f.size)}</span><div class="folder-actions"><button data-copy="${entry.f.id}" title="Copia percorso" aria-label="Copia percorso">${icon('copy')}</button><button data-fav="${entry.f.id}" class="${state.favorites.has(entry.f.key)?'is-fav':''}" title="Preferito" aria-label="Aggiungi o togli preferito">${icon('heart')}</button></div></div>`
      ).join('')||`<div class="empty-state">${icon('folder')}<h3>Cartella vuota</h3><p>Nessun video catalogato in questa posizione.</p></div>`}
      ${entries.length>visible.length?`<button class="load-more" data-action="more-folder">Mostra altri ${Math.min(120,entries.length-visible.length)} elementi (${formatCount(visible.length)} di ${formatCount(entries.length)})</button>`:''}
      </div></section>`;
  }
  function filterFiles(){
    let list=state.page==='favorites'?state.files.filter(f=>state.favorites.has(f.key)):state.files;
    const terms=normalize(state.query).split(' ').filter(Boolean);
    if(terms.length)list=list.filter(f=>terms.every(t=>f.search.includes(t)));
    if(state.diskFilter)list=list.filter(f=>f.disk===state.diskFilter);
    if(state.categoryFilter)list=list.filter(f=>f.category===state.categoryFilter);
    if(state.extFilter)list=list.filter(f=>f.ext===state.extFilter);
    if(state.sizeFilter){list=list.filter(f=>Number.isFinite(f.size)&&(state.sizeFilter==='small'?f.size<1:state.sizeFilter==='medium'?f.size>=1&&f.size<4:state.sizeFilter==='large'?f.size>=4&&f.size<10:f.size>=10));}
    if(state.genreFilter)list=list.filter(f=>state.genreFilter==='unknown'?!f.genres.length:f.genres.includes(state.genreFilter));
    if(state.yearFilter){
      list=list.filter(f=>state.yearFilter==='unknown'?!f.year:state.yearFilter.startsWith('decade:')?f.year&&Math.floor(f.year/10)*10===Number(state.yearFilter.slice(7)):f.year===Number(state.yearFilter.slice(5)));
    }
    if(state.qualityFilter)list=list.filter(f=>state.qualityFilter==='unknown'?!f.quality:f.quality===state.qualityFilter);
    if(state.languageFilter)list=list.filter(f=>state.languageFilter==='unknown'?!f.languages.length:f.languages.includes(state.languageFilter));
    const arr=list.slice();
    if(state.sort==='size-desc')arr.sort((a,b)=>(b.size||0)-(a.size||0));
    else if(state.sort==='size-asc')arr.sort((a,b)=>(a.size||0)-(b.size||0));
    else if(state.sort==='name')arr.sort((a,b)=>a.name.localeCompare(b.name,'it',{sensitivity:'base',numeric:true}));
    else if(state.sort==='disk')arr.sort((a,b)=>a.disk.localeCompare(b.disk,'it')||a.name.localeCompare(b.name,'it'));
    else if(state.sort==='year-desc')arr.sort((a,b)=>(b.year||0)-(a.year||0)||a.name.localeCompare(b.name,'it'));
    else if(state.sort==='year-asc')arr.sort((a,b)=>(a.year||9999)-(b.year||9999)||a.name.localeCompare(b.name,'it'));
    else if(terms.length){
      const whole=normalize(state.query);
      arr.sort((a,b)=>Number(b.nameSearch.startsWith(whole))-Number(a.nameSearch.startsWith(whole))||Number(b.nameSearch.includes(whole))-Number(a.nameSearch.includes(whole))||a.name.localeCompare(b.name,'it'));
    }else arr.sort((a,b)=>a.name.localeCompare(b.name,'it',{sensitivity:'base',numeric:true}));
    return arr;
  }
  function selectHtml(id,value,options,aria){return `<select class="input-select" id="${id}" aria-label="${aria}">${options.map(([v,label])=>`<option value="${html(v)}" ${v===value?'selected':''}>${html(label)}</option>`).join('')}</select>`;}
  function renderLibrary(page){
    const fav=page==='favorites',list=filterFiles(),visible=list.slice(0,state.pageLimit);
    const catOpts=[['','Tutte le categorie'],...['Film','Serie TV','Anime e cartoni','Documentari','Altro'].map(v=>[v,v])];
    const exts=Array.from(new Set(state.files.map(f=>f.ext))).filter(x=>x!=='—').sort();
    const years=[...new Set(state.files.map(f=>f.year).filter(Boolean))].sort((a,b)=>b-a);
    const decades=[...new Set(years.map(y=>Math.floor(y/10)*10))].sort((a,b)=>b-a);
    const yearOpts=[['','Tutti gli anni'],['unknown','Anno non disponibile'],...decades.map(y=>['decade:'+y,'Anni '+y.toString().slice(2)]),...years.map(y=>['year:'+y,String(y)])];
    const classified=state.files.filter(f=>f.genres.length).length;
    return `<div class="page-head"><div><span class="mini-label">${fav?'I MIEI SEGNALIBRI':'RICERCA NEL MIO ARCHIVIO'}</span><h2>${fav?'I miei preferiti':'Cerca nei miei file'}</h2><p>${fav?'I file che ho contrassegnato con il cuore, salvati in questo browser.':'Cerco per nome, cartella o hard disk.'}</p></div><button class="btn btn-soft" data-action="export">${icon('download')} Esporta CSV</button></div>
    <div class="filter-panel">${selectHtml('filterDisk',state.diskFilter,[['','Tutti gli hard disk'],...diskEntries().map(d=>[d.name,d.name])],'Filtra per disco')}
    ${selectHtml('filterCategory',state.categoryFilter,catOpts,'Filtra per categoria')}
    ${selectHtml('filterGenre',state.genreFilter,[['','Tutti i generi'],...GENRES.map(v=>[v,v]),['unknown','Da classificare']],'Filtra per genere')}
    ${selectHtml('filterYear',state.yearFilter,yearOpts,'Filtra per anno o decennio')}
    ${selectHtml('filterQuality',state.qualityFilter,[['','Tutte le risoluzioni'],['4K','4K / 2160p'],['1080p','Full HD / 1080p'],['720p','HD / 720p'],['SD','SD'],['unknown','Non indicata']],'Filtra per risoluzione')}
    ${selectHtml('filterLanguage',state.languageFilter,[['','Tutte le sigle lingua'],['ITA','ITA indicato'],['ENG','ENG indicato'],['JAP','JAP indicato'],['unknown','Non indicata']],'Filtra per sigla lingua')}
    ${selectHtml('filterSize',state.sizeFilter,[['','Tutte le dimensioni'],['small','Meno di 1 GB'],['medium','Da 1 a 4 GB'],['large','Da 4 a 10 GB'],['huge','Più di 10 GB']],'Filtra per dimensione')}
    ${selectHtml('sortFiles',state.sort,[['relevance','Rilevanza'],['name','Nome A–Z'],['year-desc','Anno più recente'],['year-asc','Anno meno recente'],['size-desc','Più grandi'],['size-asc','Più piccoli'],['disk','Nome disco']],'Ordina i file')}
    ${selectHtml('filterExt',state.extFilter,[['','Tutti i formati'],...exts.map(x=>[x,x.toUpperCase()])],'Filtra per formato')}
    </div>
    <div class="metadata-toolbar"><span>${icon('info')} Genere classificato per <strong>${formatCount(classified)}</strong> file su ${formatCount(state.files.length)}. Gli altri sono da classificare.</span>
      <span class="metadata-actions"><button type="button" class="btn btn-soft" data-action="import-meta">Importa schede</button><button type="button" class="btn btn-soft" data-action="export-meta">Esporta schede JSON</button></span></div>
    <div class="filter-summary"><span><strong>${formatCount(list.length)}</strong> ${list.length===1?'file trovato':'file trovati'}${state.query?` per <strong>“${html(state.query)}”</strong>`:''}</span><span class="filter-summary-right">${state.diskFilter?`<span class="active-scope">${icon('drive')} ${html(state.diskFilter)} <button title="Rimuovi filtro disco" data-action="clear-disk">×</button></span>`:''}${(state.diskFilter||state.categoryFilter||state.extFilter||state.sizeFilter||state.genreFilter||state.yearFilter||state.qualityFilter||state.languageFilter||state.query)?`<button type="button" class="link-btn" data-action="clear-filters">Azzera filtri</button>`:''}</span></div>
    <div class="file-results">${visible.map(f=>renderFileCard(f)).join('')||emptyState(fav)}</div>
    ${visible.length<list.length?`<button class="load-more" data-action="more-files">Mostra altri ${formatCount(Math.min(MAX_PAGE,list.length-visible.length))} file (${formatCount(visible.length)} di ${formatCount(list.length)})</button>`:''}`;
  }
  function highlight(text,query){
    if(!query?.trim())return html(text);
    const terms=query.trim().split(/\s+/).map(x=>x.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).filter(Boolean);
    if(!terms.length)return html(text);
    const reg=new RegExp('('+terms.join('|')+')','ig');
    return String(text).split(reg).map((part,i)=>i%2===1?'<mark>'+html(part)+'</mark>':html(part)).join('');
  }
  function renderFileCard(f){
    const fav=state.favorites.has(f.key);
    return `<article class="file-card"><span class="file-icon-box">${icon('movie')}</span><div class="file-main"><strong>${highlight(f.name,state.query)}</strong><div class="file-meta-line"><span class="file-disk-badge">${html(f.disk)}</span><span>${fmtGB(f.size)}</span><span>·</span><span>${html(f.ext.toUpperCase())}</span><span>·</span><span>${html(f.category)}</span>${f.year?`<span class="file-detail-badge">${f.year}</span>`:''}${f.quality?`<span class="file-detail-badge">${html(f.quality)}</span>`:''}${f.genres.map(g=>`<span class="genre-badge" title="${html(f.metadataSource)}: genere indicativo">${html(g)}</span>`).join('')}</div><div class="file-path" title="${html(f.path)}">${html(f.path)}</div></div><div class="file-actions"><button type="button" data-meta="${f.id}" title="Modifica genere e anno" aria-label="Modifica genere e anno">${icon('info')}</button><button type="button" data-copy="${f.id}" title="Copia percorso del file" aria-label="Copia percorso">${icon('copy')}</button><button type="button" data-fav="${f.id}" class="${fav?'is-fav':''}" title="${fav?'Rimuovi dai preferiti':'Aggiungi ai preferiti'}" aria-label="${fav?'Rimuovi dai preferiti':'Aggiungi ai preferiti'}">${icon('heart')}</button></div></article>`;
  }
  function emptyState(fav=false){return `<div class="empty-state">${icon(fav?'heart':'search')}<h3>${fav&&!state.favorites.size?'Non hai ancora preferiti':'Nessun file trovato'}</h3><p>${fav&&!state.favorites.size?'Premi il cuore accanto a un video per ritrovarlo qui.':'Prova a cambiare parole chiave o a rimuovere uno dei filtri.'}</p><button type="button" data-action="clear-filters" class="btn btn-soft">Azzera i filtri</button></div>`;}
  function renderDuplicates(){
    const groups=state.duplicateGroups,excess=groups.reduce((a,g)=>a+(g.length-1)*g[0].size,0);
    return `<div class="page-head"><div><span class="mini-label">CONTROLLO DEI MIEI FILE</span><h2>Doppioni potenziali</h2><p>Stesso nome file e stessa dimensione arrotondata, su dischi diversi.</p></div><button class="btn btn-soft" data-action="export-dupes">${icon('download')} Esporta CSV</button></div>
    <div class="stat-grid" style="margin-bottom:20px"><div class="stat-card"><div class="stat-title">Gruppi sospetti</div><div class="stat-value">${formatCount(groups.length)}</div><div class="stat-sub">Corrispondenza nome e dimensione</div></div><div class="stat-card"><div class="stat-title">Copie oltre la prima</div><div class="stat-value">${formatCount(groups.reduce((a,g)=>a+g.length-1,0))}</div><div class="stat-sub">Presenti su dischi diversi</div></div><div class="stat-card"><div class="stat-title">Spazio teorico duplicato</div><div class="stat-value">${fmtGB(excess)}</div><div class="stat-sub">Solo stima indicativa</div></div></div>
    <div class="info-banner">${icon('info')}<span><strong>Non eliminare automaticamente nulla.</strong> I JSON riportano dimensioni arrotondate: queste sono soltanto possibili copie, non duplicati verificati con hash dei contenuti.</span></div>
    ${groups.map((g,i)=>`<section class="duplicate-group"><div class="duplicate-header"><strong>${html(g[0].name)}</strong><span class="small-pill warning">${g.length} copie · ${fmtGB(g[0].size)}</span></div><div class="duplicate-files">${g.map(f=>renderFileCard(f)).join('')}</div></section>`).join('')||`<div class="empty-state">${icon('check')}<h3>Nessun doppione individuato</h3><p>Nessun file su dischi diversi con identico nome e dimensione arrotondata.</p></div>`}`;
  }
  function openDisk(name){
    if(!state.disks.has(name))return;
    state.page='drives';state.openDisk=name;state.folderPath=[];state.folderLimit=120;
    navigate('drives');
    $('#inspectorPanel')?.scrollIntoView({block:'start',behavior:'smooth'});
  }
  function copyPath(id){
    const f=state.files[Number(id)];if(!f)return;
    if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(f.path).then(()=>toast('Percorso copiato negli appunti')).catch(()=>fallbackCopy(f.path));}
    else fallbackCopy(f.path);
  }
  function fallbackCopy(text){
    const field=document.createElement('textarea');field.value=text;field.style.position='fixed';field.style.opacity='0';document.body.append(field);field.select();
    try{if(document.execCommand('copy'))toast('Percorso copiato negli appunti');else throw Error('not copied');}
    catch{toast('Copia non disponibile: usa un browser con HTTPS o localhost.',true);}
    finally{field.remove();}
  }
  function toggleFavorite(id){
    const f=state.files[Number(id)];if(!f)return;
    if(state.favorites.has(f.key)){state.favorites.delete(f.key);toast('Rimosso dai preferiti');}
    else {state.favorites.add(f.key);toast('Aggiunto ai preferiti');}
    store.set('favorites',[...state.favorites]);updateFavoriteCount();render();
  }
  function quoteCSV(value){
    let text=String(value??'');
    if(/^[\s\t\r\n]*[=+@-]/.test(text))text="'"+text;
    return '"'+text.replace(/"/g,'""')+'"';
  }
  function exportCSV(items,filename){
    if(!items.length){toast('Non ci sono file da esportare.',true);return;}
    const lines=[['Disco','Nome','Dimensione (GB)','Formato','Categoria','Anno','Generi','Risoluzione indicata','Sigle lingua','Percorso completo'],...items.map(f=>[f.disk,f.name,Number.isFinite(f.size)?f.size.toFixed(2):'',f.ext,f.category,f.year||'',f.genres.join(', '),f.quality,f.languages.join(', '),f.path])];
    const csv='\ufeff'+lines.map(row=>row.map(quoteCSV).join(';')).join('\r\n');
    const blob=new Blob([csv],{type:'text/csv;charset=utf-8;'});const url=URL.createObjectURL(blob);
    const link=document.createElement('a');link.href=url;link.download=filename;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
    toast(`${formatCount(items.length)} righe esportate in CSV`);
  }
  function downloadJSON(obj,name){
    const blob=new Blob([JSON.stringify(obj,null,2)+'\n'],{type:'application/json;charset=utf-8'});
    const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1100);
  }
  function exportMetadata(){
    const files={...state.publishedMetadata,...state.localMetadata};
    const valid={};for(const [key,item] of Object.entries(files)){const m=validMetadata(item);if(m)valid[key]=m;}
    downloadJSON({version:1,files:valid},'film_metadata.json');
    toast(`${formatCount(Object.keys(valid).length)} schede esportate. Carica il file in data/ su GitHub per sincronizzarle.`);
  }
  async function importMetadata(input){
    const file=input.files?.[0];input.value='';if(!file)return;
    if(file.size>15*1024*1024){toast('Metadati troppo grandi (limite 15 MB).',true);return;}
    try{
      const obj=JSON.parse(await file.text());
      if(!obj||!obj.files||typeof obj.files!=='object'||Array.isArray(obj.files))throw Error('Struttura files mancante');
      let n=0;const newData={...state.localMetadata};
      for(const [key,item] of Object.entries(obj.files)){
        if(key.length>2048)continue;
        const m=validMetadata(item);if(!m)continue;
        newData[key]=m;n++;
      }
      state.localMetadata=newData;store.set('movie-metadata',newData);applyAllMetadata();render();
      toast(`${n} schede importate in questo browser. Per condividerle carica film_metadata.json su GitHub.`);
    }catch(err){toast('Importazione schede non riuscita: '+err.message,true);}
  }
  function editMetadata(id){
    const f=state.files[Number(id)];if(!f)return;
    $('#metaFileId').value=String(f.id);
    $('#metaFilename').textContent=f.name;
    $('#metaDisk').textContent=f.disk;
    $('#metaYear').value=f.year||'';
    $('#metaGenreList').innerHTML=GENRES.map(g=>`<label class="meta-option"><input type="checkbox" name="movieGenre" value="${html(g)}" ${f.genres.includes(g)?'checked':''}><span>${html(g)}</span></label>`).join('');
    $('#metaAllCopies').checked=false;
    $('#metadataDialog').showModal();
  }
  function saveMetadata(){
    const f=state.files[Number($('#metaFileId').value)];if(!f)return;
    const raw=$('#metaYear').value.trim();const year=raw?Number(raw):null;
    if(year!==null&&(!Number.isInteger(year)||year<1900||year>CURRENT_YEAR)){toast('Inserisci un anno compreso tra 1900 e '+CURRENT_YEAR,true);return;}
    const genres=[...$('#metaGenreList').querySelectorAll('input:checked')].map(x=>x.value).filter(g=>GENRES.includes(g));
    const item={year,genres,source:'Manuale'};
    const same=!!$('#metaAllCopies').checked;
    let edited=0;
    for(const candidate of state.files){
      if(candidate===f||(same&&normalize(candidate.name)===normalize(f.name))){
        state.localMetadata[candidate.key]=item;enrichFile(candidate);edited++;
      }
    }
    store.set('movie-metadata',state.localMetadata);
    $('#metadataDialog').close();render();toast(`${edited} ${edited===1?'scheda aggiornata':'schede aggiornate'}. Esporta il JSON per conservarle su GitHub.`);
  }
  function resetMetadata(){
    const f=state.files[Number($('#metaFileId').value)];if(!f)return;
    delete state.localMetadata[f.key];
    store.set('movie-metadata',state.localMetadata);enrichFile(f);
    $('#metadataDialog').close();render();toast('Ripristinati i metadati pubblicati o quelli rilevati automaticamente.');
  }
  async function importJSON(input){
    const files=[...input.files||[]];input.value='';if(!files.length)return;
    const errors=[];let added=0;
    for(const file of files){
      if(file.size>30*1024*1024){errors.push(file.name+' (oltre 30 MB)');continue;}
      if(!/\.json$/i.test(file.name)){errors.push(file.name+' (non è JSON)');continue;}
      try{
        const data=JSON.parse(await file.text());const name=file.name.replace(/\.json$/i,'');
        const mapped=mapDisk(name,data);state.disks.set(name,mapped);added++;
      }catch(err){errors.push(file.name+' ('+err.message+')');}
    }
    if(added){state.temporaryImports+=added;reindex();state.openDisk='';state.folderPath=[];state.diskFilter='';render();toast(`${added} cataloghi caricati temporaneamente`);}
    if(errors.length)toast('File non importati: '+errors.join(', '),true);
  }
  function handleClick(e){
    const target=e.target.closest('button,[data-nav]');if(!target)return;
    if(target.hasAttribute('data-nav')){e.preventDefault();navigate(target.dataset.nav);return;}
    if(target.hasAttribute('data-suggest')){state.query=target.dataset.suggest;$('#globalSearch').value=state.query;navigate('search');return;}
    if(target.hasAttribute('data-open-disk')){openDisk(target.dataset.openDisk);return;}
    if(target.hasAttribute('data-close-disk')){state.openDisk='';state.folderPath=[];render();return;}
    if(target.hasAttribute('data-folder-level')){state.folderPath=state.folderPath.slice(0,Number(target.dataset.folderLevel));state.folderLimit=120;render();return;}
    if(target.hasAttribute('data-folder-open')){
      const node=getFolderNode(),folders=[...node.folders.values()].sort((a,b)=>a.name.localeCompare(b.name,'it',{numeric:true}));
      const child=folders[Number(target.dataset.folderOpen)];if(child){state.folderPath.push(child.name);state.folderLimit=120;render();}
      return;
    }
    if(target.hasAttribute('data-meta')){editMetadata(target.dataset.meta);return;}
    if(target.hasAttribute('data-copy')){copyPath(target.dataset.copy);return;}
    if(target.hasAttribute('data-fav')){toggleFavorite(target.dataset.fav);return;}
    if(target.hasAttribute('data-search-disk')){state.diskFilter=target.dataset.searchDisk;state.query='';$('#globalSearch').value='';navigate('search');return;}
    const action=target.dataset.action;
    if(!action)return;
    if(TITLES[action]){navigate(action);return;}
    if(action==='more-files'){state.pageLimit+=MAX_PAGE;render();return;}
    if(action==='more-folder'){state.folderLimit+=120;render();return;}
    if(action==='clear-filters'){state.query='';state.diskFilter='';state.categoryFilter='';state.extFilter='';state.sizeFilter='';state.genreFilter='';state.yearFilter='';state.qualityFilter='';state.languageFilter='';state.pageLimit=MAX_PAGE;$('#globalSearch').value='';render();return;}
    if(action==='clear-disk'){state.diskFilter='';render();return;}
    if(action==='import-meta'){$('#metaImportInput').click();return;}
    if(action==='export-meta'){exportMetadata();return;}
    if(action==='export'){exportCSV(state.page==='search'||state.page==='favorites'?filterFiles():state.files,'vault-catalogo.csv');return;}
    if(action==='export-dupes'){exportCSV(state.duplicateGroups.flat(),'vault-possibili-doppioni.csv');return;}
  }
  function handleChange(e){
    const key={filterDisk:'diskFilter',filterCategory:'categoryFilter',filterExt:'extFilter',filterSize:'sizeFilter',filterGenre:'genreFilter',filterYear:'yearFilter',filterQuality:'qualityFilter',filterLanguage:'languageFilter',sortFiles:'sort',driveSort:'driveSort'}[e.target.id];
    if(!key)return;state[key]=e.target.value;state.pageLimit=MAX_PAGE;render();
  }
  let debounce=null;
  function bind(){
    document.body.addEventListener('click',handleClick);
    $page.addEventListener('change',handleChange);
    $('#globalSearch').addEventListener('input',e=>{
      state.query=e.target.value;state.pageLimit=MAX_PAGE;
      clearTimeout(debounce);
      debounce=setTimeout(()=>{if(state.query.trim())navigate('search');else if(state.page==='search'||state.page==='favorites')render();},180);
    });
    $('#searchGo').addEventListener('click',()=>navigate('search'));
    $('#globalSearch').addEventListener('keydown',e=>{if(e.key==='Enter'){clearTimeout(debounce);state.query=e.target.value;navigate('search');}});
    $('#refreshButton').addEventListener('click',()=>{state.temporaryImports=0;loadAll();});
    $('#importButton').addEventListener('click',()=>$('#importInput').click());
    $('#importInput').addEventListener('change',e=>importJSON(e.target));
    $('#metaImportInput').addEventListener('change',e=>importMetadata(e.target));
    $('#metaSave').addEventListener('click',saveMetadata);
    $('#metaReset').addEventListener('click',resetMetadata);
    $('#metaCancel').addEventListener('click',()=>$('#metadataDialog').close());
    $('#themeButton').addEventListener('click',()=>setTheme(document.documentElement.classList.contains('light')?'dark':'light'));
    $('#shortcutsButton').addEventListener('click',()=>$('#shortcutsDialog').showModal());
    $('#closeShortcuts').addEventListener('click',()=>$('#shortcutsDialog').close());
    document.addEventListener('keydown',e=>{
      if(e.key==='Escape'&&document.activeElement===$('#globalSearch')){$('#globalSearch').value='';state.query='';render();$('#globalSearch').blur();return;}
      if(e.key==='Escape')return;
      if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();$('#globalSearch').focus();$('#globalSearch').select();return;}
      if(e.target instanceof HTMLElement&&e.target.closest('input,textarea,select,[contenteditable="true"]'))return;
      if(e.key==='/'){e.preventDefault();$('#globalSearch').focus();return;}
      if(e.key==='?'){$('#shortcutsDialog').showModal();}
    });
    window.addEventListener('hashchange',()=>navigate(location.hash.slice(1)));
  }
  function init(){
    setTheme(store.get('theme','dark'));
    state.page=ensurePage(location.hash.slice(1));
    bind();navigate(state.page);loadAll();
  }
  init();
})();
