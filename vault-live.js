/* VAULT live metadata: runs only when a genre is selected, no credentials in this file. */
(() => {
  'use strict';
  const norm = s=>String(s||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^\p{L}\p{N}]+/gu,' ').trim().replace(/\s+/g,' ');
  const escapeHTML = s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const EP = /\b(?:s\s*\d{1,3}[ ._-]*e\s*\d{1,3}|\d{1,2}x\d{2,3}|(?:episodio|episode|ep)[ ._-]*\d{1,4}|(?:stagione|season)[ ._-]*\d{1,3}|stage[ ._-]*\d{1,3}|(?:puntata|part)[ ._-]*\d{1,3})\b/i;
  const TECH = /\b(?:2160p|1080p|720p|576p|480p|4k|uhd|blu[ ._-]?ray|bdrip|bdmux|brrip|dvdrip|webrip|web[ ._-]?dl|webmux|remux|x264|x265|h[ ._-]?264|hevc|aac|ac3|dts|hdtv|hdr10|dovi|dual[ ._-]?audio|proper|repack)\b/i;
  const EXTRAS = /^(?:extras?|extra disc|bonus|special features?|speciali|featurettes?|deleted scenes?|interviews?|making[ ._-]?of|behind the scenes|trailers?|teasers?|sample|campioni|menu)$/i;
  const GENERIC = /^(?:film|movie|movies|video|anime|cartoni|animazione|serie|serie tv|series|stagione ?\d+|season ?\d+|s\d+|disco ?\d+|disc ?\d+|cd ?\d+|episodi|episodes|completa|complete)$/i;
  const YEAR = /(?:^|[^0-9])(19\d{2}|20\d{2})(?=$|[^0-9])/;
  const getYear = s=>{const m=String(s).match(YEAR);const y=m?Number(m[1]):null;return y&&y<=new Date().getFullYear()+1?y:null;};
  function clean(s){s=String(s||'').replace(/\.[A-Za-z0-9]{2,5}$/,'').replace(/[._]+/g,' ');s=s.replace(/^\s*\[[^\]]{1,25}\]\s*/,'');const yr=s.match(YEAR);if(yr)s=s.slice(0,yr.index||0);const tech=s.match(TECH);if(tech)s=s.slice(0,tech.index||0);return s.replace(/\[[^\]]*\]/g,' ').replace(/\([^)]*(?:italiano|eng|jap|dvdrip|bluray)[^)]*\)/gi,' ').replace(/\s+(?:s\d{1,3}|r\d{1,2})$/i,'').replace(/\s+\((?:us|uk|usa)\)$/i,'').replace(/\s+/g,' ').replace(/^[ .\-_()[\]]+|[ .\-_()[\]]+$/g,'');}
  const validTitle=(s,year)=>{const n=norm(s);return n.length>=(year?2:4) && n.split(' ').length<=14 && !/^\d+$/.test(n) && !/^(?:extra|sample|film|video|episodio|episode|trailer|bdrip|bdmux|dvdrip|1080p|720p|stagione|season)$/.test(n);};
  function candidate(file){
    const paths=String(file.path||'').split(/[\\/]/).filter(Boolean),dirs=paths.slice(1,-1);
    if(dirs.some(d=>EXTRAS.test(d))||/\b(?:sample|trailer|featurette|making[ ._-]?of|deleted[ ._-]?scene)\b/i.test(file.name))return null;
    const folder=dirs.join(' '),tvContext=/(?:serie[ ._-]*tv|tv[ ._-]*series|telefilm|season|stagione|anime|cartoni|animazione)/i.test(folder),movieContext=/(?:film|movies?|cinema|horror|thriller|fantascienza|fantasy|sci[ ._-]*fi|commedia)/i.test(folder);
    const stem=String(file.name||'').replace(/\.[^.]+$/,'');
    const tv=EP.test(file.name)||EP.test(file.path)||(tvContext&&!movieContext&&!getYear(stem));
    let title='',year=null,kind=tv?'tv':'movie';
    if(tv){const before=clean(stem.split(EP)[0]);if(validTitle(before,false)&&!GENERIC.test(before))title=before;
      else for(const d of [...dirs].reverse()){if(GENERIC.test(d.trim())||EP.test(d)||EXTRAS.test(d))continue;const tmp=clean(d);if(validTitle(tmp,false)){title=tmp;break;}}
      year=getYear(stem)||null;
      // Episode years are usually episode release dates, not the series' initial year.
      if(EP.test(stem))year=null;
    }else{title=clean(stem);year=getYear(stem);if(!year&&dirs.length)year=getYear(dirs.at(-1));if(!validTitle(title,!!year)&&year)title=clean(dirs.at(-1));if(!year&&!movieContext)return null;}
    if(!validTitle(title,!!year)||dirs.length>9)return null;
    return {key:`${kind}|${norm(title)}|${year||'?'}`,kind,title,year};
  }
  window.createVaultLiveScan=function({state,render,enrichFile,notify}){
    const api=String(window.VAULT_TMDB_API||'').trim().replace(/\/$/,'');
    let adminKey='',serial=0,paused=false,scanQueue=[],groups=new Map();
    const progress={phase:'idle',genre:'',total:0,done:0,found:0,unmatched:0,cached:0,errors:0,message:''};
    const isConfigured=()=>/^https:\/\/[a-z0-9.-]+(?::\d+)?$/i.test(api);
    const redraw=()=>{if(state.page==='search')render();};
    function build(){const mapped=new Map();for(const f of state.files){if(state.diskFilter&&f.disk!==state.diskFilter)continue;if(state.categoryFilter&&state.categoryFilter!==f.category)continue;const c=candidate(f);if(!c)continue;let g=mapped.get(c.key);if(!g){g={...c,files:[]};mapped.set(c.key,g);}g.files.push(f);}return mapped;}
    function apply(group,metadata){if(!metadata)return;for(const f of group.files){if(state.localMetadata[f.key])continue;state.publishedMetadata[f.key]=metadata;enrichFile(f);}}
    async function request(endpoint,body,auth=false){const headers={'Content-Type':'application/json'};if(auth)headers['X-Vault-Admin']=adminKey;const ctl=new AbortController();const timeout=setTimeout(()=>ctl.abort(),25000);try{const resp=await fetch(api+endpoint,{method:'POST',headers,body:JSON.stringify(body),signal:ctl.signal});const data=await resp.json().catch(()=>({error:'RISPOSTA_NON_VALIDA'}));if(!resp.ok){const error=new Error(data.error||`HTTP_${resp.status}`);error.status=resp.status;error.retryAfter=data.retryAfter;error.partial=data.results||[];throw error;}return data;}finally{clearTimeout(timeout);}}
    function consume(result){const group=groups.get(result.key);if(!group)return;progress.done++;if(result.cached)progress.cached++;if(result.status==='ok'&&result.metadata){progress.found++;apply(group,result.metadata);}else progress.unmatched++;}
    async function begin(genre){serial++;const mine=serial;paused=false;scanQueue=[];groups=new Map();progress.phase='idle';progress.genre=genre||'';progress.done=progress.total=progress.found=progress.unmatched=progress.cached=progress.errors=0;progress.message='';if(!genre||genre==='unknown'){redraw();return;}
      if(!isConfigured()){progress.phase='setup';redraw();return;}
      groups=build();progress.total=groups.size;progress.phase='lookup';progress.message='Controllo i titoli già classificati…';redraw();
      const all=[...groups.values()];for(let i=0;i<all.length;i+=80){if(mine!==serial)return;const chunk=all.slice(i,i+80);const knownFromFiles=[];const absent=[];for(const g of chunk){const f=g.files.find(f=>f.tmdb?.source==='TMDB');if(f){knownFromFiles.push({key:g.key,status:'ok',metadata:f.tmdb,cached:true});}else absent.push(g);}
        for(const value of knownFromFiles)consume(value);
        if(absent.length){try{const data=await request('/api/lookup',{keys:absent.map(x=>x.key)});if(mine!==serial)return;for(const g of absent){const old=data.results?.[g.key];if(old)consume({key:g.key,...old,cached:true});else scanQueue.push(g);}}
          catch(e){if(mine!==serial)return;progress.phase='error';progress.message='Cache non raggiungibile: '+e.message;redraw();return;}}
        if((i/80)%5===0||i+80>=all.length)redraw();}
      if(mine!==serial)return;
      if(!scanQueue.length){progress.phase='complete';progress.message='Tutti i titoli candidati hanno già una classificazione o sono stati verificati.';redraw();return;}
      progress.phase=adminKey?'scanning':'auth';progress.message=adminKey?'Sto cercando i titoli mancanti su TMDB…':`Restano ${scanQueue.length.toLocaleString('it-IT')} titoli da verificare. Inserisci il codice personale di scansione.`;redraw();if(adminKey)scan(mine);
    }
    async function scan(mine=serial){if(mine!==serial||paused||!adminKey)return;progress.phase='scanning';redraw();while(scanQueue.length&&mine===serial&&!paused){const batch=scanQueue.slice(0,2);try{const data=await request('/api/scan',{candidates:batch},true);if(mine!==serial)return;for(const result of data.results||[])consume(result);scanQueue.splice(0,batch.length);progress.message=`Analisi in corso: ${scanQueue.length.toLocaleString('it-IT')} titoli ancora da cercare.`;redraw();}
        catch(e){if(mine!==serial)return;for(const result of e.partial||[]){consume(result);scanQueue.shift();}
          if(e.status===401){adminKey='';progress.phase='auth';progress.message='Codice di scansione non corretto.';redraw();return;}
          if(e.status===429){progress.phase='paused';paused=true;progress.message=`TMDB ha chiesto di rallentare. Riprova fra circa ${e.retryAfter||20} secondi.`;redraw();return;}
          progress.phase='paused';paused=true;progress.errors++;progress.message=`La scansione è in pausa (${e.message}). Puoi riprenderla.`;redraw();return;}}
      if(mine!==serial)return;progress.phase=paused?'paused':'complete';progress.message=paused?'La scansione è in pausa.':'Analisi terminata. I risultati sono conservati nel database Cloudflare.';redraw();}
    function setAdmin(code){if(!code||code.length<24){notify('Il codice amministratore deve contenere almeno 24 caratteri.',true);return;}adminKey=code;if(progress.phase==='auth'||progress.phase==='paused'){paused=false;scan(serial);}else begin(progress.genre);}
    function pause(){paused=true;if(progress.phase==='scanning'){progress.phase='paused';progress.message='In pausa; potrai riprendere da dove eri arrivato.';}redraw();}
    function resume(){if(progress.phase==='paused'&&scanQueue.length){paused=false;scan(serial);}else if(progress.genre&&progress.phase==='error')begin(progress.genre);}
    function panel(){if(!progress.genre)return '';const pc=progress.total?Math.min(100,Math.round(progress.done*100/progress.total)):0;const active=['lookup','scanning'].includes(progress.phase);const names={setup:'Configurazione necessaria',lookup:'Controllo cache',auth:'Autorizza la scansione',scanning:'Analisi TMDB in corso',paused:'Analisi in pausa',complete:'Analisi completata',error:'Errore connessione',idle:'In attesa'};
      return `<section class="vault-scan-panel" aria-label="Analisi TMDB" aria-live="polite"><div class="vault-scan-top"><span class="vault-scan-dot ${active?'spin':''}">${active?'◌':'✦'}</span><div><strong>${escapeHTML(names[progress.phase]||'Analisi archivio')} · ${escapeHTML(progress.genre)}</strong><p>${escapeHTML(progress.message||'Analizzo solo quando seleziono un genere.')}</p></div><span class="vault-scan-pct">${pc}%</span></div><div class="vault-scan-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pc}"><div style="width:${pc}%"></div></div><div class="vault-scan-metrics"><span><b>${progress.done.toLocaleString('it-IT')}</b> / ${progress.total.toLocaleString('it-IT')} titoli controllati</span><span><b>${progress.found.toLocaleString('it-IT')}</b> riconosciuti</span><span><b>${progress.unmatched.toLocaleString('it-IT')}</b> dubbi o assenti</span><span><b>${progress.cached.toLocaleString('it-IT')}</b> dalla cache</span></div>
      ${progress.phase==='setup'?`<div class="vault-scan-message">Configura <code>vault-config.js</code> con l'URL del Worker Cloudflare. Intanto i filtri usano i metadati già presenti su GitHub.</div>`:''}
      ${progress.phase==='auth'?`<div class="vault-scan-auth"><label for="vaultAdminCode">Codice privato di scansione (non è il token TMDB)</label><div><input type="password" id="vaultAdminCode" placeholder="Codice amministratore Cloudflare" autocomplete="off"><button class="btn btn-primary" data-action="scan-auth">Avvia ricerca</button></div><small>Il codice viene tenuto solo nella memoria di questa scheda, non salvato su GitHub o nel browser.</small></div>`:''}
      <div class="vault-scan-controls">${progress.phase==='scanning'?'<button class="btn btn-soft" data-action="scan-pause">Metti in pausa</button>':''}${progress.phase==='paused'?'<button class="btn btn-primary" data-action="scan-resume">Riprendi analisi</button>':''}${progress.phase==='error'?'<button class="btn btn-soft" data-action="scan-resume">Riprova</button>':''}${progress.phase==='complete'?'<span class="vault-scan-message">Per gli altri generi userò anche i metadati già recuperati.</span>':''}</div></section>`;
    }
    return {begin,panel,setAdmin,pause,resume,get progress(){return {...progress};}};
  };
})();
