/* VAULT live metadata: runs only when a genre is selected, no credentials in this file. */
(() => {
  'use strict';
  const norm = s=>String(s||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^\p{L}\p{N}]+/gu,' ').trim().replace(/\s+/g,' ');
  const escapeHTML = s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const EP = /(?:^|[\s._-])(?:s\d{1,3}[\s._-]*e\d{1,3}|\d{1,2}x\d{1,3}|(?:episodio|episode|ep|puntata|stage)[\s._-]*\d{1,4}|\d{1,2}[._-]\d{2}(?=[\s._-]))/i;
  const NUMBERED_EP = /(?:^|[\s._-])(?:\d{1,3})(?=[\s._-]+[A-Za-zÀ-ÿ]{2,})/;
  const TECH = /(?:^|[\s._-])(?:2160p|1080p|720p|576p|480p|4k|uhd|blu[ ._-]?ray|bdrip|bdmux|brrip|dvdrip|dvdmux|webrip|web[ ._-]?dl|webmux|remux|x264|x265|h[ ._-]?264|hevc|aac|ac3|eac3|dts|hdtv|hdr10|dovi|dual[ ._-]?audio|proper|repack|multi|subs?|ita(?:lian)?|eng(?:lish)?|jap(?:anese)?)(?=$|[\s._-])/i;
  const EXTRAS = /^(?:extras?(?:[ ._-]|$)|bonus(?:[ ._-]|$)|special features?|speciali|featurettes?|deleted scenes?|interviews?|making[ ._-]?of|behind the scenes|trailers?|teasers?|sample|campioni|menu)/i;
  const GENERIC = /^(?:film|movie|movies|video|anime|cartoni|cartoni animati|animazione|serie|serie tv|series|stagione ?\d+|season ?\d+|s\d+|disco ?\d+|disc ?\d+|cd ?\d+|episodi|episodes|completa|complete)$/i;
  const YEAR = /(?:^|[^0-9])(19\d{2}|20\d{2})(?=$|[^0-9])/;
  function getYear(s){
    // I tag tra parentesi quadre, ad es. [genio1985], sono release group, non anni.
    let raw=String(s||'').replace(/\[[^\]]*\]/g,' ');
    const tech=raw.match(TECH);if(tech)raw=raw.slice(0,tech.index);
    const m=raw.match(YEAR),y=m?Number(m[1]):null;
    return y&&y>=1900&&y<=new Date().getFullYear()+1?y:null;
  }
  function clean(s){
    s=String(s||'').replace(/\.(?:mkv|mp4|avi|m4v|mov|wmv|mpg|mpeg|ts|m2ts|webm|flv|vob|iso)$/i,'').replace(/[._]+/g,' ');
    s=s.replace(/^\s*\[[^\]]+\]\s*/, '');
    // Prima elimina i tag tecnici, poi tronca all'anno (non prima dei titoli).
    const tech=s.match(TECH);if(tech)s=s.slice(0,tech.index);
    const y=s.match(YEAR);if(y)s=s.slice(0,y.index);
    s=s.replace(/\[[^\]]*\]/g,' ').replace(/\((?:ita|eng|jap|us|uk|usa|sub[^)]*)\)/gi,' ')
      .replace(/\s+(?:s\d{1,3}|r\d{1,2})$/i,' ').replace(/\s+/g,' ')
      .replace(/^[ .\-_()[\]]+|[ .\-_()[\]]+$/g,'');
    return s;
  }
  const validTitle=(s,year)=>{const n=norm(s);return n.length>=(year?2:4)&&n.split(' ').length<=14&&!/^\d+$/.test(n)&&!/^(?:extra|sample|film|video|episodio|episode|trailer|bdrip|bdmux|dvdrip|1080p|720p|stagione|season|ita|eng|jap)$/.test(n);};
  function candidate(file){
    const path=String(file.path||''),parts=path.split(/[\\/]/).filter(Boolean),dirs=parts.slice(1,-1);
    if(dirs.some(d=>EXTRAS.test(d))||/\b(?:sample|trailer|featurette|making[ ._-]?of|deleted[ ._-]?scene)\b/i.test(file.name))return null;
    const folder=dirs.join(' '),stem=String(file.name||'').replace(/\.[^.]+$/,'');
    const tvContext=/(?:serie[ ._-]*tv|tv[ ._-]*series|telefilm|season|stagione)/i.test(folder);
    const animContext=/(?:anime|cartoni animati)/i.test(folder);
    const movieContext=/(?:film|movies?|cinema|horror|thriller|fantascienza|fantasy|sci[ ._-]*fi|commedia)/i.test(folder);
    const rawBeforeTech=stem.split(TECH)[0].replace(/\[[^\]]*\]/g,' ');
    const standardEpisode=EP.test(stem)||EP.test(folder);
    const numericEpisode=standardEpisode?null:NUMBERED_EP.exec(rawBeforeTech);
    const numericPrefix=numericEpisode?clean(rawBeforeTech.slice(0,numericEpisode.index)):'';
    const episode=standardEpisode||((tvContext||animContext)&&!!numericEpisode&&((numericPrefix.length>=4&&(!/\b(?:movie|film|special)\b/i.test(numericPrefix)))||dirs.length>1&&!numericPrefix));
    const isMovieSubfolder=dirs.some(d=>/^(?:film(?: vari)?|movies?|special films?)$/i.test(d));
    const tv=(episode&&!isMovieSubfolder)||(!isMovieSubfolder&&tvContext);
    let title='',year=null,kind=tv?'tv':'movie';
    if(tv){
      // Preferiamo il nome comune della serie, non il titolo di una singola puntata.
      const before=stem.split(EP)[0];
      let prefix=clean(before);
      if(numericEpisode && numericPrefix.length>=4)prefix=numericPrefix;
      if(validTitle(prefix,false)&&!GENERIC.test(prefix))title=prefix;
      else for(const d of [...dirs].reverse()){
        if(GENERIC.test(d.trim())||EP.test(d)||EXTRAS.test(d))continue;
        const tmp=clean(d);
        if(validTitle(tmp,false)){title=tmp;break;}
      }
      // Per i seriali non dedurre l'anno dal release group o dall'episodio.
      year=null;
    }else{
      title=clean(stem);year=getYear(stem);
      if(!year&&dirs.length)year=getYear(dirs.at(-1));
      if(!validTitle(title,!!year)&&year)title=clean(dirs.at(-1));
      if(!year&&!movieContext&&!animContext)return null;
    }
    if(!validTitle(title,!!year)||GENERIC.test(title)||dirs.length>9)return null;
    return {key:`${kind}|${norm(title)}|${year||'?'}`,kind,title,year};
  }
  // Una catalogazione globale, indipendente da genere e HDD selezionato.
  window.createVaultLiveScan=function({state,render,enrichFile,notify}){
    const api=String(window.VAULT_TMDB_API||'').trim().replace(/\/$/,'');
    const configured=/^https:\/\/[a-z0-9.-]+(?::\d+)?$/i.test(api);
    const progress={mode:'loading',total:0,done:0,recognized:0,reviewed:0,last_run:null,last_error:null,fingerprint:''};
    let groups=new Map(),fingerprint='',adminKey='',uploading=false,uploadDone=0,uploadTotal=0,cursor=0,started=false,pollBusy=false;
    const escape=escapeHTML;
    const format=n=>(Number(n)||0).toLocaleString('it-IT');
    const draw=()=>{if((state.page==='overview'||state.page==='search')&&document.activeElement?.id!=='vaultAdminCode')render();};
    const number=x=>Math.max(0,Number(x)||0);
    function build(){
      groups=new Map();
      for(const f of state.files){
        const c=candidate(f);if(!c)continue;
        let g=groups.get(c.key);
        if(!g){g={...c,files:[]};groups.set(c.key,g);}
        g.files.push(f);
      }
      const sorted=[...groups.keys()].sort();let hash=2166136261;
      for(const key of sorted){for(let i=0;i<key.length;i++){hash^=key.charCodeAt(i);hash=Math.imul(hash,16777619);}hash^=10;hash=Math.imul(hash,16777619);}
      fingerprint=(hash>>>0).toString(16).padStart(8,'0')+'-'+groups.size;
    }
    async function apiRequest(path,{method='GET',body,auth=false}={}){
      const ctl=new AbortController(),timer=setTimeout(()=>ctl.abort(),30000);
      const headers={'Accept':'application/json'};
      if(method!=='GET')headers['Content-Type']='application/json';
      if(auth)headers['X-Vault-Admin']=adminKey;
      try{
        const resp=await fetch(api+path,{method,headers,...(body===undefined?{}:{body:JSON.stringify(body)}),signal:ctl.signal,cache:'no-store'});
        const json=await resp.json().catch(()=>({error:'RISPOSTA_NON_VALIDA'}));
        if(!resp.ok)throw Object.assign(new Error(json.error||`HTTP_${resp.status}`),{status:resp.status});
        return json;
      }finally{clearTimeout(timer);}
    }
    function applyResult(item){
      if(item.status!=='ok'||!item.metadata)return false;
      const g=groups.get(item.key);if(!g)return false;
      let updated=false;
      for(const f of g.files){
        if(state.localMetadata[f.key])continue;
        state.publishedMetadata[f.key]=item.metadata;
        enrichFile(f);updated=true;
      }
      return updated;
    }
    async function getResults(){
      let changed=false;
      for(let i=0;i<15;i++){
        const data=await apiRequest(`/api/catalog/results?after=${cursor}&limit=120`);
        for(const item of data.items||[])changed=applyResult(item)||changed;
        cursor=number(data.cursor)||cursor;
        if(!data.has_more)break;
      }
      return changed;
    }
    async function poll(){
      if(!configured||!started||pollBusy||uploading||document.visibilityState==='hidden')return;
      pollBusy=true;
      try{
        const remote=await apiRequest('/api/catalog/status');
        const prev=JSON.stringify(progress);
        Object.assign(progress,remote);
        const changed=await getResults();
        if(changed||JSON.stringify(progress)!==prev)draw();
      }catch(e){
        progress.mode='offline';progress.last_error=e.message;draw();
      }finally{pollBusy=false;}
    }
    function init(){
      build();started=true;
      if(!configured){progress.mode='setup';draw();return;}
      poll();
      if(!window.__vaultCatalogTimer){window.__vaultCatalogTimer=setInterval(poll,20000);}
      if(!window.__vaultCatalogVisible){window.__vaultCatalogVisible=true;document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')poll();});}
    }
    async function startScan(){
      if(uploading)return;
      uploading=true;uploadTotal=groups.size;uploadDone=0;
      progress.mode='uploading';draw();
      try{
        await apiRequest('/api/catalog/start',{method:'POST',auth:true,body:{fingerprint}});
        const all=[...groups.values()].map(({key,kind,title,year})=>({key,kind,title,year}));
        for(let i=0;i<all.length;i+=50){
          const batch=all.slice(i,i+50);
          await apiRequest('/api/catalog/enqueue',{method:'POST',auth:true,body:{candidates:batch,fingerprint}});
          uploadDone=Math.min(all.length,i+batch.length);
          if((i/50)%3===0||uploadDone===all.length)draw();
        }
        const r=await apiRequest('/api/catalog/finish',{method:'POST',auth:true,body:{fingerprint}});
        progress.mode=r.remaining?'running':'complete';
        progress.total=r.total;progress.done=r.done;progress.fingerprint=fingerprint;
        notify(r.remaining?'Catalogazione avviata su Cloudflare: prosegue anche a pagina chiusa.':'Il mio archivio è già catalogato.');
      }catch(e){
        progress.mode='upload-error';progress.last_error=e.message;
        if(e.status===401)adminKey='';
        notify('Non riesco a inviare il catalogo: '+e.message,true);
      }finally{uploading=false;draw();poll();}
    }
    function setAdmin(code){
      if(!code||code.length<24){notify('Inserisci il codice privato di scansione (almeno 24 caratteri).',true);return;}
      adminKey=code;
      if(['paused','error','auth-resume'].includes(progress.mode))control('resume');else if(progress.mode==='auth-pause')control('pause');else startScan();
    }
    async function control(action){
      if(!adminKey){progress.mode=action==='resume'?'auth-resume':'auth-pause';draw();return;}
      try{
        const reply=await apiRequest('/api/catalog/control',{method:'POST',auth:true,body:{action}});
        Object.assign(progress,reply.state);
        draw();
      }catch(e){if(e.status===401)adminKey='';notify('Non riesco a gestire la scansione: '+e.message,true);draw();}
    }
    function pause(){control('pause');}
    function resume(){if(progress.mode==='auth-resume'){control('resume');return;}control('resume');}
    const begin=()=>{}; // I cambi di genere filtrano localmente: non avviano TMDB.
    function panel(){
      if(!started)return '';
      const changed=progress.fingerprint!==fingerprint;
      const total=progress.total||0,done=progress.done||0;
      const pc=uploading?Math.round(100*uploadDone/Math.max(1,uploadTotal)):total?Math.round(100*done/total):0;
      let title='',message='',showCode=false,buttonText='Avvia catalogazione',controls='';
      const mode=progress.mode;
      if(!configured||mode==='setup'){
        title='Backend Cloudflare da collegare';message='Controlla il file vault-config.js e la configurazione D1.';
      }else if(mode==='loading'){
        title='Controllo della catalogazione';message='Recupero lo stato dei film già riconosciuti da Cloudflare…';
      }else if(mode==='idle'||(mode==='complete'&&changed)){
        title=mode==='idle'?'Prima catalogazione TMDB':'Nuovi titoli da catalogare';
        message=`Ho trovato ${format(groups.size)} titoli candidati. Inserisco una sola volta i titoli nella coda Cloudflare; poi la scansione procede in autonomia.`;
        showCode=true;buttonText=mode==='idle'?'Avvia la prima catalogazione':'Aggiorna catalogo';
      }else if(mode==='uploading'&&uploading){
        title='Preparazione del catalogo';message=`Trasferimento titoli a Cloudflare: ${format(uploadDone)} di ${format(uploadTotal)}. Non chiudere la pagina durante questa prima fase.`;
      }else if(mode==='uploading'&&!uploading){
        title='Invio catalogo da riprendere';message='La preparazione è stata interrotta. Inserisci il codice e riprendi senza duplicare i titoli.';
        showCode=true;buttonText='Riprendi invio';
      }else if(mode==='auth-pause'){
        title='Autorizza la pausa';message='Inserisci il codice privato per mettere in pausa la scansione sul server.';
        showCode=true;buttonText='Metti in pausa';
      }else if(mode==='upload-error'){
        title='Invio catalogo interrotto';message='Puoi riprovare: i titoli già ricevuti non vengono duplicati. '+String(progress.last_error||'');
        showCode=true;buttonText='Riprendi invio';
      }else if(mode==='running'){
        title='Catalogazione TMDB in corso';message='Cloudflare continua automaticamente anche se chiudi questa pagina. I generi già riconosciuti sono filtrabili subito.';
        controls='<button type="button" class="btn btn-soft" data-action="scan-pause">Metti in pausa</button>';
      }else if(mode==='paused'){
        title='Catalogazione in pausa';message='I risultati sono salvati. Puoi riprendere dal punto raggiunto.';
        if(adminKey)controls='<button type="button" class="btn btn-soft" data-action="scan-resume">Riprendi</button>';
        else{showCode=true;buttonText='Riprendi scansione';}
      }else if(mode==='complete'&&!changed){
        title='Catalogazione completata';message='I filtri per genere utilizzano i metadati archiviati su Cloudflare. Nessuna nuova ricerca TMDB quando cambi filtro.';
      }else if(mode==='error'||mode==='auth-resume'){
        title='Scansione in attesa di ripresa';message='Controlla il codice TMDB su Cloudflare, poi riprendi senza perdere i dati già raccolti.';
        showCode=true;buttonText='Riprendi scansione';
      }else{
        title='Cloudflare non raggiungibile';message='Verifica la connessione, il Worker e il database D1. '+String(progress.last_error||'');
      }
      const active=['loading','uploading','running'].includes(mode);
      const safePct=Math.min(100,Math.max(0,pc));
      return `<section class="vault-scan-panel" aria-label="Catalogazione TMDB" aria-live="polite">
        <div class="vault-scan-top"><span class="vault-scan-dot ${active?'spin':''}">${active?'◌':'✦'}</span><div>
          <strong>${escape(title)}</strong><p>${escape(message)}</p></div><span class="vault-scan-pct">${safePct}%</span></div>
        <div class="vault-scan-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${safePct}"><div style="width:${safePct}%"></div></div>
        <div class="vault-scan-metrics"><span><b>${format(done)}</b> analizzati</span><span><b>${format(total)}</b> titoli in cloud</span><span><b>${format(progress.recognized)}</b> riconosciuti</span><span><b>${format(progress.reviewed)}</b> da verificare</span></div>
        ${progress.last_run?`<p class="vault-scan-message">Ultima attività: ${escape(progress.last_run)} UTC · I contenuti già riconosciuti sono disponibili nei filtri.</p>`:''}
        ${progress.last_error?`<p class="vault-scan-message">Ultimo avviso: ${escape(progress.last_error)}${progress.paused_until?' (ritento automaticamente)':''}</p>`:''}
        ${showCode?`<div class="vault-scan-auth"><label for="vaultAdminCode">Codice privato SCAN_ADMIN_KEY</label><div><input type="password" id="vaultAdminCode" autocomplete="off" placeholder="Inserisci il codice privato"><button type="button" class="btn btn-primary" data-action="scan-auth">${escape(buttonText)}</button></div><small>Non è il token TMDB. Il codice resta nella memoria di questa pagina e non viene pubblicato su GitHub.</small></div>`:''}
        ${controls?`<div class="vault-scan-controls">${controls}</div>`:''}
        </section>`;
    }
    return {init,panel,begin,setAdmin,pause,resume,poll};
  };
})();
