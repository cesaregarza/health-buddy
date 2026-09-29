/* Canonical API envelopes and browser-owned durable retry state. */
const HealthAPI = (() => {
  const identityKeys = ['installationId', 'datasetId', 'restoreEpoch'];
  function metadata(value) {
    const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
    return value && value.apiVersion === 1 && Number.isSafeInteger(value.dataRevision) &&
      value.dataRevision >= 0 && identityKeys.every(key => uuid.test(value[key] || ''));
  }
  function sameIdentity(left, right) {
    return identityKeys.every(key => left?.[key] === right?.[key]);
  }
  function pendingRequest(value, sessionId) {
    if(!value||value.version!==1||value.method!=='POST'||value.path!=='/v1/workouts'||!metadata(value.identity)||typeof value.body!=='string'||new TextEncoder().encode(value.body).length>65536)return false;
    const headers=value.headers,keys=['Content-Type','X-Installation-ID','X-Dataset-ID','X-Restore-Epoch','If-Match','Idempotency-Key'];
    if(!headers||Object.keys(headers).sort().join('|')!==keys.sort().join('|')||headers['Content-Type']!=='application/json'||headers['X-Installation-ID']!==value.identity.installationId||headers['X-Dataset-ID']!==value.identity.datasetId||headers['X-Restore-Epoch']!==value.identity.restoreEpoch||headers['If-Match']!==`"rev-${value.identity.dataRevision}"`||!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(headers['Idempotency-Key']||''))return false;
    try{const payload=JSON.parse(value.body);return payload?.schema_version===1&&payload.session_id===sessionId;}catch(e){return false;}
  }
  async function envelope(response, expected = null) {
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      const code = /^[a-z_]{1,80}$/.test(body?.error?.code || '') ? body.error.code : 'request_failed';
      const error = new Error(`Request could not be confirmed (${code}).`);
      error.code = code;
      throw error;
    }
    if (!body || !Object.hasOwn(body, 'data') || !metadata(body.meta)) {
      throw new Error('The response could not be verified. Keep the original request for retry.');
    }
    if (expected && !sameIdentity(expected, body.meta)) {
      throw new Error('The workspace identity changed. Keep this request and explicitly resolve it before a new save.');
    }
    return body;
  }
  return {metadata, sameIdentity, pendingRequest, envelope};
})();

/* Browser-only drafts and change history. Bundled into the static page. */
const HealthFeatures = (() => {
  'use strict';
  const DRAFT_KEY='health-workout-draft-v1', CHECK_KEY='health-workout-checks-v1', SEEN_KEY='health-seen-v1';
  let data, navigate, today, changes, storageWarning='', draft, revision=0, blocked=false;
  let draftLock=false,lockRequested=false,corruptDraft=false,identityChanged=false;
  const el=(tag,attrs={},parent)=>{const node=document.createElement(tag);for(const [k,v] of Object.entries(attrs)){if(k==='text')node.textContent=v;else node.setAttribute(k,v);}if(parent)parent.append(node);return node;};
  const canonical=value=>JSON.stringify(value,(_,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.entries(v).sort(([a],[b])=>a.localeCompare(b))):v);
  function fingerprint(value){let a=2166136261,b=5381;for(const c of canonical(value)){a=Math.imul(a^c.charCodeAt(0),16777619);b=Math.imul(b,33)^c.charCodeAt(0);}return (a>>>0).toString(16)+(b>>>0).toString(16);}
  function read(key){try{const raw=localStorage.getItem(key);return raw?JSON.parse(raw):null;}catch(e){storageWarning='Local storage is unavailable or a saved record could not be read. Keep this page open until you save.';return null;}}
  function write(key,value){try{localStorage.setItem(key,JSON.stringify(value));return true;}catch(e){storageWarning='This browser could not retain the draft. Keep this page open until the central save succeeds.';return false;}}
  function cacheKey(base){return HealthAPI.metadata(data.meta)?[base,data.meta.installationId,data.meta.datasetId,data.meta.restoreEpoch].join(':'):null;}
  function snapshot(){
    const records={};
    const add=(category,key,value,date,tab,target)=>{records[category+':'+key]={hash:fingerprint(value),date,tab,target,category};};
    for(const [key,tab,target] of [['weight','overview','weight'],['bp','overview','bp'],['sleep','overview','sleep'],['rhr','overview','rhr'],['hrv','overview','hrv'],['steps','overview','steps'],['intake','overview','intake']]){
      const duplicate=new Map();for(const row of data[key]||[]){const base=row.t||row.d,n=duplicate.get(base)||0;duplicate.set(base,n+1);add('Readings',key+':'+base+':'+n,row,row.d,tab,target);}
    }
    for(const day of data.training_detail?.days||[])for(const s of day.sessions||[])if(s.duration_source!=='watch-only'&&s.duration_source!=='watch-unmatched')add('Workouts',s.id,s,day.d,'training','training-history');
    for(const a of data.labs?.analytes||[]){const duplicate=new Map();for(const p of a.points||[]){const n=duplicate.get(p.d)||0;duplicate.set(p.d,n+1);add('Lab results',a.key+':'+p.d+':'+n,p,p.d,'labs','labs-figs');}}
    for(const q of data.visit?.prepared_questions||[])add('Questions',q.question_id,q,q.recorded_on||data.visit?.note_through,'notes','visit-questions');
    return {version:1,opened:new Date().toISOString(),records};
  }
  function init(D,options){
    data=D;navigate=options.navigate;today=options.today;
    const current=snapshot(),key=cacheKey(SEEN_KEY),previous=key?read(key):null;
    const valid=previous?.version===1&&previous.records&&typeof previous.records==='object'&&!Array.isArray(previous.records);
    const groups=new Map();
    if(valid)for(const [key,value]of Object.entries(current.records)){
      const old=previous.records[key];if(old?.hash===value.hash)continue;
      const group=groups.get(value.category)||{category:value.category,added:0,updated:0,links:new Map(),latest:null};
      group[old?'updated':'added']++;group.links.set(value.tab+':'+value.target,value);
      if(value.date&&(!group.latest||value.date>group.latest))group.latest=value.date;groups.set(value.category,group);
    }
    changes={first:!valid,since:valid?previous.opened:null,groups:[...groups.values()]};if(key)write(key,current);
  }
  function renderChanges(host){
    host.textContent='';const groups=changes?.groups||[];
    const details=el('details',{class:'changes-summary'},host);el('summary',{text:groups.length?'New since you last opened this':'What changed'},details);
    const body=el('div',{class:'changes-body'},details);
    if(!groups.length)el('p',{class:'prov',text:changes?.first?'This device’s baseline is set. New readings, workouts, results and questions will appear here on your next visit.':'No new or changed records since this device last opened the dashboard.'},body);
    else{
      details.open=true;
      const stamp=new Date(changes.since).toLocaleString(undefined,{timeZone:data.meta.tz||'UTC',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
      el('p',{class:'prov',text:`Compared with ${stamp}. Dates below are record dates; a newly imported record can be older.`},body);
      for(const group of groups){const row=el('div',{class:'change-row'},body);el('b',{text:group.category},row);el('span',{text:[group.added?`${group.added} new`:null,group.updated?`${group.updated} updated`:null,group.latest?'latest '+group.latest:null].filter(Boolean).join(' · ')},row);
        for(const item of group.links.values()){const button=el('button',{type:'button',class:'action',text:item.category==='Readings'?item.target.replace('rhr','Resting heart rate').replace('hrv','HRV'):'View'},row);button.addEventListener('click',()=>navigate(item.tab,item.target));}}
      const dismiss=el('button',{type:'button',class:'action',text:'Mark reviewed'},body);dismiss.onclick=()=>{changes.groups=[];renderChanges(host);};
    }
    if(storageWarning)el('p',{class:'prov',text:storageWarning},body);
  }
  function checks(){const key=cacheKey(CHECK_KEY),value=key?read(key):null;return value?.version===1&&value.values&&typeof value.values==='object'?value.values:{};}
  function checked(key){return !!checks()[key];}
  function setChecked(key,value){const storageKey=cacheKey(CHECK_KEY);if(!storageKey)return false;const values=checks();values[key]=value;const cutoff=today().slice(0,7);for(const old of Object.keys(values))if(old.startsWith('rx-')&&old.slice(3,10)<cutoff)delete values[old];return write(storageKey,{version:1,values});}
  function newDraft(){return {version:1,revision:0,identity:data.meta,session_id:'dashboard-'+crypto.randomUUID(),date:today(),workout_type:data.config?'strength':'upper_body',status:'complete',duration_min:'',notes:'',sets:[],receipt:null,updated:null};}
  function restore(){
    let saved=null,exists=false;corruptDraft=false;identityChanged=false;
    try{const raw=localStorage.getItem(DRAFT_KEY);exists=raw!==null;saved=exists?JSON.parse(raw):null;}
    catch(e){exists=true;corruptDraft=true;}
    if(saved?.version===1&&HealthAPI.metadata(saved.identity)&&Array.isArray(saved.sets)&&saved.sets.length<=80&&saved.sets.every(s=>s&&typeof s==='object'&&!Array.isArray(s))&&typeof saved.session_id==='string'&&typeof saved.date==='string'&&Number.isInteger(saved.revision)){draft=saved;revision=saved.revision;identityChanged=!HealthAPI.sameIdentity(saved.identity,data.meta);}
    else{draft=newDraft();revision=0;corruptDraft=exists;if(exists)storageWarning='The saved draft could not be restored. An earlier request may already have saved. Keep its state until you explicitly resolve it.';}
  }
  function persist(){
    if(!draftLock||corruptDraft||identityChanged)return false;
    const other=read(DRAFT_KEY);
    if(other&&(Number.isInteger(other.revision)?other.revision:0)!==revision){blocked=true;return false;}
    const nextRevision=revision+1;
    draft.revision=nextRevision;draft.updated=new Date().toISOString();
    if(write(DRAFT_KEY,draft)){revision=nextRevision;return true;}
    draft.revision=revision;return false;
  }
  function renderWorkout(host,prescription){
    if(host.dataset.mounted)return;host.dataset.mounted='true';restore();
    const detail=el('details',{class:'workout-editor',id:'workout-editor'},host);
    const summary=el('summary',{text:'Record workout · resume a draft'},detail);
    const body=el('div',{class:'workout-editor-body'},detail);
    const status=el('p',{class:'prov',role:'status','aria-live':'polite',id:'draft-status'},body);
    const form=el('form',{id:'workout-form'},body);const fields=el('fieldset',{},form);const grid=el('div',{class:'workout-fields'},fields);
    function field(parent,label,key,options={}){
      const wrap=el('label',{class:'control-block'},parent);el('span',{text:label,class:'control-label'},wrap);
      const input=el(options.choices?'select':options.multiline?'textarea':'input',{class:'select-control','data-field':key,...(!options.choices&&!options.multiline?{type:options.type||'text'}:{}),...(options.attrs||{})},wrap);
      if(options.choices)for(const [value,text] of options.choices)el('option',{value,text},input);
      return input;
    }
    const dateInput=field(grid,'Workout date','date',{type:'date',attrs:{required:'',max:today()}});
    const typeInput=field(grid,'Session','workout_type',{choices:[['upper_body','Upper body'],['lower_body','Lower body'],['strength','Other strength'],['cardio','Cardio'],['racquet','Racquet'],['shuffle','Shuffle']]}), completion=field(grid,'Outcome','status',{choices:[['complete','Completed'],['partial','Partial session']]}),duration=field(grid,'Duration in minutes (optional)','duration_min',{type:'number',attrs:{min:0,max:1440,step:'any'}}), notes=field(grid,'Session notes','notes',{multiline:true,attrs:{maxlength:2000}});
    for(const input of [dateInput,typeInput,completion,duration,notes]){input.value=draft[input.dataset.field]??'';input.addEventListener('input',()=>{draft[input.dataset.field]=input.value;changed();});}
    const setHost=el('div',{class:'entered-sets',id:'entered-sets'},fields);
    const source=(prescription?.recommendation?.template?.exercises||[]);
    if(!draft.updated&&!draft.sets.length&&/lower/i.test(prescription?.recommendation?.template?.label||'')){draft.workout_type='lower_body';typeInput.value='lower_body';}
    const picker=field(fields,'Add an exercise from the plan','exercise-choice',{choices:[['','Choose an exercise'],...source.map((x,i)=>[String(i),x.exercise]),['custom','Other exercise or machine']]}),add=el('button',{type:'button',class:'action',text:'Add completed set',id:'add-workout-set'},fields);
    el('p',{class:'prov',text:'Enter what you completed. Blank load, RIR, or form stays unknown. Check the actual machine and load basis; targets are not entered as results.'},fields);
    const preview=el('div',{class:'workout-review',id:'workout-review',hidden:''},form);
    const review=el('button',{type:'submit',class:'action',text:'Review workout',id:'review-workout'},form);
    const save=el('button',{type:'button',class:'action',text:'Save workout to central log',id:'save-workout',hidden:''},form);
    const next=el('button',{type:'button',class:'action',text:'Start another workout',id:'new-workout',hidden:''},body);
    const discard=el('button',{type:'button',class:'action',text:'Discard this draft'},body);
    const resolve=el('button',{type:'button',class:'action',text:'Resolve saved retry request',hidden:''},body);
    let reviewed=null,saving=false;
    function showStatus(message){status.textContent=message|| (draft.receipt?'Saved to the central log.':blocked?'This draft changed in another tab. Reload before editing or saving.':storageWarning|| (draft.updated?'Draft retained on this device. Nothing is logged until you save.':'Start entering your completed workout. Drafts stay on this device until saved.'));}
    function changed(){reviewed=null;save.hidden=true;preview.hidden=true;persist();showStatus();if(blocked){fields.disabled=true;review.disabled=true;}}
    function drawSets(){
      setHost.textContent='';draft.sets.forEach((set,index)=>{
        const card=el('section',{class:'entered-set'},setHost);el('h3',{text:`Completed set ${index+1}`},card);
        const row=el('div',{class:'workout-fields'},card);
        const definitions=[['Exercise','exercise',{}],['Actual machine / equipment','equipment',{}],['Set number','set_number',{type:'number',attrs:{min:1,max:1000,step:1,required:''}}],['Load (lb, optional)','load_lb',{type:'number',attrs:{min:0,max:2000,step:'any'}}],['Load basis','load_basis',{choices:[['not_reported','Not recorded'],['per_hand','Per hand'],['total_stack','Total stack'],['machine_stack','Machine stack'],['total','Total load'],['bodyweight','Bodyweight']]}],['Completed reps','reps',{type:'number',attrs:{min:1,max:1000,step:1,required:''}}],['RIR (optional)','rir',{type:'number',attrs:{min:0,max:10,step:1}}],['Reported form','form_quality',{choices:[['not_reported','Not reported'],['controlled','Controlled'],['clean','Clean'],['breakdown','Form breakdown']]}],['Set notes','notes',{multiline:true,attrs:{maxlength:2000}}]];
        for(const [label,key,options]of definitions){const input=field(row,label,key,options);input.value=set[key]??'';if(['exercise','equipment'].includes(key)){input.required=true;input.maxLength=100;}input.oninput=()=>{set[key]=input.value;changed();};
          if(key==='equipment' && data.config?.equipment?.length){
            const list=el('datalist',{id:`equipment-options-${index}`},row); input.setAttribute('list',list.id);
            for(const item of data.config.equipment)el('option',{value:item.id,text:item.label},list);
            input.onchange=()=>{const item=data.config.equipment.find(item=>item.id===input.value);if(item){set.exercise=item.exercise;set.load_basis=item.loadBasis;changed();drawSets();}};
          }
        }
        const remove=el('button',{type:'button',class:'action',text:'Remove set'},card);remove.onclick=()=>{draft.sets.splice(index,1);changed();drawSets();};
      });
    }
    add.onclick=()=>{
      if(draft.sets.length>=80){showStatus('At most 80 sets can be submitted together.');return;}
      if(!picker.value){showStatus('Choose an exercise first.');picker.focus();return;}
      const x=picker.value==='custom'?{}:source[Number(picker.value)],p=x.progression||{};
      const exercise=p.exercise_id||'',equipment=p.equipment_id||'';
      const ordinal=Math.max(0,...draft.sets.filter(s=>s.exercise===exercise&&s.equipment===equipment).map(s=>Number(s.set_number)||0))+1;
      draft.sets.push({exercise,equipment,set_number:String(ordinal),load_lb:'',load_basis:p.load_basis||'not_reported',reps:'',rir:'',form_quality:'not_reported',notes:''});changed();drawSets();setHost.lastElementChild.querySelector('[data-field=reps]').focus();
    };
    function payload(){return {schema_version:1,session_id:draft.session_id,date:draft.date,workout_type:draft.workout_type,status:draft.status,duration_min:draft.duration_min===''?null:Number(draft.duration_min),notes:draft.notes,sets:draft.sets.map(s=>({...s,set_number:Number(s.set_number),load_lb:s.load_lb===''?null:Number(s.load_lb),reps:Number(s.reps),rir:s.rir===''?null:Number(s.rir)}))};}
    form.onsubmit=event=>{event.preventDefault();if(blocked||corruptDraft||identityChanged||!draftLock||draft.receipt)return;if(!form.reportValidity())return;
      if(draft.date>today()){showStatus('Choose today or an earlier workout date.');return;}
      if(!draft.sets.length&&(['upper_body','lower_body','strength'].includes(draft.workout_type)||draft.duration_min==='')){showStatus('Enter completed sets, or a recorded cardio duration.');return;}
      reviewed=payload();preview.textContent='';preview.hidden=false;
      el('b',{text:`${draft.date} · ${typeInput.selectedOptions[0].textContent} · ${completion.selectedOptions[0].textContent}`},preview);
      el('p',{text:`${draft.sets.length} completed sets · ${draft.duration_min===''?'duration unknown':draft.duration_min+' minutes'}`},preview);
      const list=el('ul',{},preview);for(const s of reviewed.sets)el('li',{text:`${s.exercise} · ${s.equipment} · set ${s.set_number}: ${s.load_lb===null?'load unknown':s.load_lb+' lb'} (${s.load_basis}) × ${s.reps} reps · ${s.rir===null?'RIR unknown':s.rir+' RIR'} · ${s.form_quality}${s.notes?' · '+s.notes:''}`},list);
      if(reviewed.notes)el('p',{text:reviewed.notes},preview);save.hidden=false;showStatus('Review these actual results, then save them to the central health log.');save.focus();
    };
    save.onclick=async()=>{if(saving||!reviewed||blocked||corruptDraft||identityChanged||!draftLock||draft.receipt)return;saving=true;fields.disabled=true;review.disabled=true;save.disabled=true;discard.disabled=true;showStatus('Saving to the central log…');
      const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),30000);
      try{
        if(!draft.pending){
          const capabilities=await HealthAPI.envelope(await fetch('/v1/capabilities',{cache:'no-store',signal:controller.signal}),data.meta);
          if(capabilities.data.writable!==true)throw new Error('This client cannot save health records.');
          const meta=capabilities.meta;
          draft.pending={version:1,method:'POST',path:'/v1/workouts',body:JSON.stringify(reviewed),identity:meta,headers:{
            'Content-Type':'application/json','X-Installation-ID':meta.installationId,
            'X-Dataset-ID':meta.datasetId,'X-Restore-Epoch':meta.restoreEpoch,
            'If-Match':`"rev-${meta.dataRevision}"`,'Idempotency-Key':crypto.randomUUID()
          }};
          if(!persist())throw new Error('Saving is paused until this browser can retain the original retry request.');
        }
        const pending=draft.pending;
        if(!HealthAPI.pendingRequest(pending,draft.session_id)){
          throw new Error('The saved retry request is invalid. Resolve it explicitly before creating another.');
        }
        // A changed epoch is never silently rebound. The same original body,
        // identity, revision and key survive reload, timeout and manual retry.
        if(!HealthAPI.sameIdentity(data.meta,pending.identity))throw new Error('This saved request belongs to a different workspace identity. Resolve it explicitly.');
        if(!persist())throw new Error('Saving is paused until this browser can retain the original retry request.');
        const response=await fetch(pending.path,{method:pending.method,signal:controller.signal,headers:pending.headers,body:pending.body});
        const result=await HealthAPI.envelope(response,pending.identity);
        const intent=JSON.parse(pending.body);
        if(result.data.saved!==true||result.data.sessionId!==intent.session_id||result.meta.dataRevision!==pending.identity.dataRevision+1||response.headers.get('etag')!==`"rev-${result.meta.dataRevision}"`){
          throw new Error('The save receipt did not match this request. Retry the unchanged request.');
        }
        draft.receipt=result;
        if(!persist()){draft.receipt=null;throw new Error('A save response arrived but its receipt could not be retained. Retry the unchanged request.');}
        summary.textContent='Workout saved · '+draft.date;save.hidden=true;review.hidden=true;next.hidden=false;discard.hidden=true;resolve.hidden=true;
        showStatus('Saved to the central health log. Reload to see the current dashboard.');
        const reload=el('button',{type:'button',class:'action',text:'Reload dashboard'},body);reload.onclick=()=>location.reload();
      }catch(error){fields.disabled=!!draft.pending;review.disabled=!!draft.pending;save.disabled=false;discard.disabled=false;resolve.hidden=!draft.pending;showStatus(`${error.message} ${draft.pending?'The original request is retained. Retry unchanged, or resolve it explicitly; a lost response may already have saved.':'The draft is retained.'}`);}
      finally{clearTimeout(timeout);saving=false;}
    };
    function reset(){
      if(!draftLock)return;
      const fresh=newDraft();
      if(!write(DRAFT_KEY,fresh)){showStatus('Local retry state could not be updated. Keep the original request.');return;}
      draft=fresh;revision=0;blocked=false;corruptDraft=false;identityChanged=false;storageWarning='';
      host.textContent='';delete host.dataset.mounted;renderWorkout(host,prescription);host.querySelector('details').open=true;
    }
    next.onclick=reset;
    discard.onclick=()=>{if(confirm(corruptDraft||(draft.pending&&!draft.receipt)?'An earlier request may already have saved. Discarding local state does not undo it. Discard this draft and its retry request?':'Discard this device’s unsaved workout draft?'))reset();};
    resolve.onclick=()=>{
      if(corruptDraft){if(confirm('The unreadable saved draft may contain a request that already saved. Clearing it cannot undo that outcome. Explicitly discard this local state and start a new draft?'))reset();return;}
      if(!confirm('The original request may already have saved, including in a previous workspace epoch. Clearing its retry state does not undo it. Clear only the retry request, retain this draft, and reload for a new explicit review?'))return;
      const original=draft.pending,receipt=draft.receipt,identity=draft.identity,changed=identityChanged;
      draft.pending=null;draft.receipt=null;draft.identity=data.meta;identityChanged=false;
      if(persist())location.reload();else{draft.pending=original;draft.receipt=receipt;draft.identity=identity;identityChanged=changed;showStatus('Retry state could not be updated. Keep the original request.');}
    };
    drawSets();showStatus();if(corruptDraft){fields.disabled=true;review.disabled=true;resolve.hidden=false;showStatus(storageWarning);}
    else if(identityChanged){fields.disabled=true;review.disabled=true;save.disabled=true;resolve.hidden=false;showStatus('This draft belongs to a different workspace identity. Its original state is retained; explicitly resolve it before preparing a new save.');}
    else if(draft.receipt){fields.disabled=true;review.hidden=true;next.hidden=false;discard.hidden=true;summary.textContent='Workout saved · '+draft.date;}
    else if(draft.pending){
      try{reviewed=JSON.parse(draft.pending.body);}catch(e){reviewed=null;}
      fields.disabled=true;review.hidden=true;save.hidden=false;save.textContent='Retry unchanged save';resolve.hidden=false;
      if(!HealthAPI.pendingRequest(draft.pending,draft.session_id)){save.disabled=true;showStatus('The saved retry request is invalid. Its original state is retained; resolve its possible earlier outcome explicitly.');}
      else showStatus('A save request is retained from this browser. Retry it unchanged or resolve its possible earlier outcome explicitly.');
    }
    else if(draft.sets.length)summary.textContent='Resume workout draft · '+draft.date;
    window.addEventListener('storage',e=>{if(e.key===DRAFT_KEY){blocked=true;fields.disabled=true;review.disabled=true;save.disabled=true;showStatus();}});
    if(!draftLock){
      fields.disabled=true;review.disabled=true;save.disabled=true;discard.disabled=true;resolve.disabled=true;next.disabled=true;
      showStatus('Waiting for exclusive access to this browser’s workout draft…');
      if(!navigator.locks){showStatus('This browser cannot safely coordinate saved retries. Use a browser with Web Locks or the native client.');return;}
      if(!lockRequested){
        lockRequested=true;
        navigator.locks.request(DRAFT_KEY,{mode:'exclusive',ifAvailable:true},async lock=>{
          if(!lock){showStatus('Another tab owns this workout draft. Close that tab and reload this page before editing or saving.');return;}
          draftLock=true;blocked=false;
          host.textContent='';delete host.dataset.mounted;renderWorkout(host,prescription);
          await new Promise(release=>window.addEventListener('pagehide',release,{once:true}));
          draftLock=false;
        }).catch(()=>showStatus('The browser draft lock is unavailable. The saved request has been retained.'));
        window.addEventListener('pageshow',event=>{if(event.persisted)location.reload();});
      }
    }
  }
  return {init,renderChanges,renderWorkout,checked,setChecked};
})();
