/* PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt */
/* Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0 */
'use strict';
// Noyau commun : connexion, fenêtres, messages, Nouveautés et mises à jour. L'espace métier est PCRLia (pcr.js).
const $ = (s, root = document) => root.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const S = {access:'local', user:null, workspace:'pcrlia'};
let busy = false, toastTimer;
const modal = $('#modal');
const dateLabel = d => d ? new Date(d.length === 10 ? d+'T12:00:00' : d).toLocaleString('fr-FR', d.length === 10 ? {dateStyle:'medium'} : {dateStyle:'short',timeStyle:'short'}) : '—';
function toast(message, error=false){const el=$('#toast');el.textContent=message;el.className=error?'error':'';el.style.display='block';clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.style.display='none',error?9000:4500);}
async function api(path, data){const res=await fetch('/api'+path,{method:data===undefined?'GET':'POST',headers:{'Content-Type':'application/json',...(S.user?{'X-CSRF-Token':S.user.csrf}:{})},...(data===undefined?{}:{body:JSON.stringify(data)})});let body;try{body=await res.json();}catch{throw new Error('Réponse illisible. Actualisez avant de poursuivre.');}if(!res.ok){if(res.status===403&&body.error==='Connexion requise.'&&S.user){PCR.reset();S.user=null;closeModal();authScreen({setup_required:false,access:S.access});}throw new Error(body.error||'Opération interrompue.');}return body;}
async function run(fn){if(busy)return;busy=true;document.body.classList.add('busy');try{await fn();}catch(e){toast(e.message,true);}finally{busy=false;document.body.classList.remove('busy');}}
function showModal(title, body, small=false){modal.className=small?'small':'';modal.innerHTML=`<div class="modal-head"><h2>${esc(title)}</h2><button class="close" data-action="close" aria-label="Fermer">×</button></div><div class="modal-body">${body}</div>`;if(!modal.open)modal.showModal();}
function closeModal(){for(const f of modal.querySelectorAll('[data-dirty]'))delete f.dataset.dirty;modal.close();}
function field(name,label,value='',type='text',extra=''){return `<label>${esc(label)}<input name="${name}" type="${type}" value="${esc(value)}" ${extra}></label>`;}
function note(label='Motif / observation'){return `<label>${esc(label)}<textarea name="reason" minlength="5" required placeholder="Indiquer ce qui a été vérifié ou la raison de la correction"></textarea></label>`;}
function brandSignature(){return '<span class="brand-signature"><span class="signature-ai">I</span>.<span class="signature-ai">A</span><sup>rvish</sup></span>';}
function labLogo(width=220){return LAB.logo?`<img src="${esc(LAB.logo)}" alt="${esc(LAB.name)}" width="${width}">`:`<strong class="lab-name">${esc(LAB.name)}</strong>`;}
function authScreen(status){
 S.access=status.access||'local';
 if(status.setup_required&&status.setup_allowed===false){$('#app').innerHTML=`<div class="auth-form" style="min-height:100vh"><div><div class="eyebrow">${esc(LAB.short)} · PCRLia · Accès réseau chiffré</div><h2>Créer d’abord le compte sur le PC hébergeur</h2><p>La liaison HTTPS répond. Le premier compte administrateur doit être créé via l’accès local sur ce PC.</p><p>Recharger cette page une fois le compte créé, puis se connecter.</p></div></div>`;return;}
 const setup=status.setup_required;let token='';if(location.hash.startsWith('#setup=')){token=location.hash.substring(7);history.replaceState(null,'',location.pathname);}
 $('#app').innerHTML=`<div class="auth"><div class="auth-intro"><div class="auth-brand"><div class="auth-brand-names"><img src="/brand.svg" width="92" height="92" alt=""><div><span>PCRLia</span></div></div><div class="auth-brand-signatures"><small>UNE INTERFACE ${brandSignature()}</small>${labLogo()}</div></div><h1>Réception des tubes PCR</h1><div class="auth-workspaces"><p><strong>PCRLia</strong>Réceptionner les tubes PCR et retrouver les échantillons non réceptionnés.</p></div></div><div class="auth-form"><form id="auth-form" data-setup="${setup}"><div class="eyebrow">PCRLia · ${esc(LAB.name)}</div><h2>${setup?'Première ouverture':'Connexion'}</h2><p class="muted">${setup?'Créer le compte administrateur de ce poste.':'Connectez-vous à votre espace.'}</p>${setup?field('token','Clé de première ouverture',token,'password','required autocomplete="off"'):''}${field('username','Identifiant','','text','required autocomplete="username"')}${field('password','Mot de passe','','password',`required ${setup?'minlength="2"':''} autocomplete="${setup?'new-password':'current-password'}"`)}<div id="auth-error" class="form-error"></div><button class="primary" type="submit">${setup?'Créer mon compte':'Se connecter'}</button>${setup?'<p class="small-note">La clé se trouve dans premiere-ouverture.txt, dans le dossier local de données. Mot de passe : 2 caractères minimum.</p>':''}</form></div></div>`;
}
function render(){return PCR.render();}

document.addEventListener('click',e=>{
 const el=e.target.closest('[data-action]');if(!el)return;const a=el.dataset.action;
 if(a!=='manual-cancel'&&document.querySelector('form[data-dirty="true"]')&&!el.closest('form[data-dirty="true"]')){toast('Enregistrez ou annulez la saisie en cours.',true);return;}
 if(a==='close')return closeModal();
 if(a==='pcr-home')e.preventDefault();
 if(a==='logout'&&PCR.pendingScans()){toast('Attendre la fin des scans avant la déconnexion.',true);return;}
 if(Workspaces.click(a,el)||PCR.click(a,el)||StorageSettings.click(a,el))return;
 if(a==='logout')run(async()=>{await api('/logout',{});PCR.reset();S.user=null;authScreen(await api('/status'));});
});
document.addEventListener('submit',e=>{e.preventDefault();const f=e.target;const data=Object.fromEntries(new FormData(f));if(f.id==='pcr-scan-form'){PCR.enqueue(data);return;}run(async()=>{
 if(await StorageSettings.submit(f)||await Workspaces.submit(f,data)||await PCR.submit(f,data))return;
 if(f.id==='auth-form'){try{S.user=await api(f.dataset.setup==='true'?'/setup':'/login',data);await Workspaces.start();newsCheck();}catch(err){$('#auth-error').textContent=err.message;}return;}
});});
run(async()=>{const status=await api('/status');S.access=status.access||'local';S.version=status.version||'';if(status.user){S.user=status.user;await Workspaces.start();newsCheck();}else authScreen(status);});

// « Nouveautés » after an update, once per user; marked as read when the window is closed.
async function newsCheck(){
  try{
    const n=await api('/news');if(!n.entries?.length||modal.open)return;
    showModal('Nouveautés',`<div class="news">${n.entries.map(e=>`<section><h3>Version ${esc(e.version)}</h3><ul>${e.items.map(i=>`<li>${esc(i)}</li>`).join('')}</ul></section>`).join('')}</div><div class="actions"><button class="primary" data-action="close">J’ai compris</button></div>`,true);
    modal.addEventListener('close',()=>{api('/news/seen',{}).catch(()=>{});},{once:true});
  }catch{}
}
function versionTag(){return S.version?(S.user?.role==='admin'?`<button class="app-version app-version-button" data-action="updates" title="Version installée · installer une mise à jour déposée">Version ${esc(S.version)} · Mises à jour</button>`:`<small class="app-version" title="Version installée de PCRLia">Version ${esc(S.version)}</small>`):'';}
