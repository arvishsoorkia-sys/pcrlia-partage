/* PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt */
/* Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0 */
'use strict';
const Workspaces = (() => {
  const names = {pcrlia:'PCRLia'};
  let users = [];
  function apps(){return S.user?.apps || ['pcrlia'];}
  function switcher(){return apps().length>1 ? `<div class="workspace-switch" aria-label="Changer d’espace">${apps().map(a=>`<button data-action="workspace" data-workspace="${a}" class="${S.workspace===a?'active':''}">${names[a]}</button>`).join('')}</div>` : '';}
  async function enter(app){
    if(!apps().includes(app))throw new Error('Cet espace n’est pas affecté à votre compte.');
    if(PCR.pendingScans())throw new Error('Attendre la fin des scans avant de changer d’espace.');
    PCR.stop();S.workspace=app;
    document.title=names[app]+' · '+LAB.short;
    await PCR.load();
  }
  async function start(){await enter(S.user.default_app || apps()[0]);}
  function assignmentFields(user){
    const selected=user?.apps||['pcrlia'],preferred=user?.default_app||selected[0];
    return `<fieldset class="account-assignments"><legend>Interfaces autorisées</legend>${Object.entries(names).map(([id,label])=>`<label><input type="checkbox" name="apps" value="${id}" ${selected.includes(id)?'checked':''}> ${label}</label>`).join('')}</fieldset><label>Espace à l’ouverture<select name="default_app">${Object.entries(names).map(([id,label])=>`<option value="${id}" ${preferred===id?'selected':''} ${selected.includes(id)?'':'disabled'}>${label}</option>`).join('')}</select></label>`;
  }
  function fields(user){return `${field('last_name','Nom',user?.last_name||'','text','maxlength="80" autocomplete="family-name"')}${field('first_name','Prénom',user?.first_name||'','text','maxlength="80" autocomplete="given-name"')}<label>Droits<select name="role">${Object.entries({technician:'Utilisateur',admin:'Administrateur',...(user?.role==='viewer'?{viewer:'Consultation · ancien compte'}:{})}).map(([id,label])=>`<option value="${id}" ${user?.role===id?'selected':''}>${label}</option>`).join('')}</select></label>${assignmentFields(user)}`;}
  async function manage(){
    users=await api('/users');
    showModal('Utilisateurs et affectations',`<div class="account-manager"><p>Cocher uniquement les interfaces autorisées pour chaque compte.</p><div class="pcr-table-wrap"><table><thead><tr><th>Compte</th><th>Droits</th><th>Interfaces autorisées</th><th>Ouverture</th><th></th></tr></thead><tbody>${users.map(u=>`<tr><td><strong>${esc(u.display_name||u.username)}</strong>${u.first_name||u.last_name?`<br><small>${esc(u.username)}</small>`:''}${u.active?'':' <span class="badge amber">Désactivé</span>'}</td><td>${esc({admin:'Administrateur',technician:'Utilisateur',viewer:'Consultation'}[u.role])}</td><td>${u.apps.map(a=>names[a]).join(' · ')}</td><td>${names[u.default_app]}</td><td><button data-action="account-edit" data-id="${esc(u.id)}">Modifier</button></td></tr>`).join('')}</tbody></table></div><h3>Créer un compte</h3><form id="workspace-user-form" class="account-form">${field('username','Identifiant','','text','required maxlength="80" autocomplete="off"')}${field('password','Mot de passe initial','','password','required minlength="2" autocomplete="new-password"')}${fields()}<button class="primary">Créer le compte</button></form></div>`);
  }
  function edit(id){const u=users.find(u=>u.id===id);if(!u)return;showModal('Modifier le compte '+u.username,`<form id="workspace-access-form" data-id="${esc(id)}" data-revision="${u.revision}">${fields(u)}<label class="checks"><input type="checkbox" name="active" ${u.active?'checked':''}>Compte actif</label><p class="muted">La modification ferme les sessions de ce compte. Les actions déjà enregistrées restent attribuées à leur auteur.</p><button class="primary">Enregistrer le compte</button></form>`,true);}
  function click(a,el){
    if(a==='workspace'){run(()=>enter(el.dataset.workspace));return true;}
    if(a==='accounts'){run(manage);return true;}
    if(a==='account-edit'){edit(el.dataset.id);return true;}
    return false;
  }
  async function submit(f,data){
    if(!['workspace-user-form','workspace-access-form','user-form'].includes(f.id))return false;
    const payload={...data,apps:new FormData(f).getAll('apps')};
    if(f.id==='workspace-access-form')Object.assign(payload,{action:'update',id:f.dataset.id,revision:Number(f.dataset.revision),active:data.active==='on'});
    await api('/users',payload);
    if(payload.id===S.user.id){PCR.stop();S.user=null;closeModal();authScreen(await api('/status'));toast('Accès modifiés. Reconnectez-vous.');}
    else {await manage();toast(f.id==='workspace-access-form'?'Compte enregistré.':'Compte créé.');}
    return true;
  }
  document.addEventListener('change',e=>{if(e.target.name!=='apps')return;const f=e.target.closest('form');if(!f?.elements.default_app)return;const selected=new FormData(f).getAll('apps'),select=f.elements.default_app;for(const o of select.options)o.disabled=!selected.includes(o.value);if(!selected.includes(select.value))select.value=selected[0]||'';});
  return {start,enter,switcher,assignmentFields,click,submit};
})();
