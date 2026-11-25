/* PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt */
/* Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0 */
'use strict';
const StorageSettings=(()=>{
 let timer;
 const size=n=>new Intl.NumberFormat('fr-FR',{maximumFractionDigits:1}).format((n||0)/1048576)+' Mo';
 async function open(){
  clearTimeout(timer);const s=await api('/storage'),c=s.config,last=s.last;
  showModal('Sauvegarde et documents',`<div class="storage-intro"><span class="storage-symbol" aria-hidden="true">▤</span><div><h3>PCRLia</h3><p>Configurez ici la sauvegarde de PCRLia.</p></div></div><div class="storage-status ${s.error?'warning':''}" role="status"><strong>${s.running?'Sauvegarde en cours…':s.error?'Sauvegarde à vérifier':c.enabled?'Sauvegarde automatique activée':'NAS non activé'}</strong><p>${esc(s.error|| (last.at?'Dernière sauvegarde réussie : '+dateLabel(last.at):'Aucune sauvegarde NAS effectuée.'))}</p>${last.at?`<div class="storage-metrics"><span><b>${last.documents}</b> documents sauvegardés</span><span><b>${size(last.released_bytes)}</b> libérés au dernier passage</span></div>`:''}${c.enabled&&s.next?`<small>Prochain passage : ${esc(dateLabel(s.next))}</small>`:''}</div><form id="storage-form"><label class="storage-enable"><input type="checkbox" name="enabled" ${c.enabled?'checked':''}> Activer la sauvegarde sur le NAS</label>${field('directory','Dossier du NAS',c.directory,'text','spellcheck="false" autocomplete="off" placeholder="Chemin du dossier partagé du laboratoire"')}<div class="storage-fields">${field('interval_hours','Sauvegarder toutes les (heures)',c.interval_hours,'number','required min="1" max="720"')}${field('local_days','Conserver les scans sur ce PC (jours)',c.local_days,'number','required min="1" max="3650"')}</div><div class="storage-explanation"><strong>Après ${c.local_days} jours</strong><p>Les originaux et images sont retirés de ce PC après une copie NAS vérifiée. Ils s’ouvrent toujours depuis l’application lorsque le NAS est accessible.</p><p>Les séries, résultats, identités, dossiers, pointages et historiques restent accessibles dans SéroLia et PCRLia. Les sauvegardes du NAS ne sont pas effacées automatiquement.</p></div><div class="actions"><button class="primary" ${s.running?'disabled':''}>Enregistrer</button><button type="button" data-action="storage-probe" ${s.running?'disabled':''}>Vérifier l’accès</button><button type="button" data-action="storage-run" ${!c.enabled||s.running?'disabled':''}>Sauvegarder maintenant</button></div><p class="small-note">Accès au NAS avec le compte Windows du poste hébergeur. Sans accès, aucun fichier local supplémentaire n’est retiré.</p></form><button type="button" data-action="storage-settings" class="storage-refresh">Actualiser l’état</button>`);
  if(s.running)timer=setTimeout(async()=>{if(modal.open&&$('#storage-form')&&!$('#storage-form').dataset.dirty)await open();},2500);
  const form=$('#storage-form'),runButton=$('[data-action="storage-run"]',form);
  const draftNotice=document.createElement('p');draftNotice.className='small-note';draftNotice.id='storage-draft-notice';draftNotice.setAttribute('role','status');draftNotice.hidden=true;draftNotice.textContent='Enregistrer d’abord les modifications avant de lancer une sauvegarde.';runButton.closest('.actions').after(draftNotice);runButton.setAttribute('aria-describedby',draftNotice.id);
  const markDirty=()=>{form.dataset.dirty='true';runButton.disabled=true;draftNotice.hidden=false;};
  form.addEventListener('input',markDirty);form.addEventListener('change',markDirty);
 }
 function data(f){return {enabled:f.elements.enabled.checked,directory:f.elements.directory.value.trim(),interval_hours:Number(f.elements.interval_hours.value),local_days:Number(f.elements.local_days.value)};}
 // Install, from the browser, a signed update already deposited in « INSTAL MAJ » (administrators).
 let updateTimer;
 async function updates(){
  clearTimeout(updateTimer);const s=await api('/updates');
  const items=s.packages.filter(p=>p.newer);
  const body=!s.available?`<p>${esc(s.reason)}</p>`:`<p>Version installée : <strong>${esc(s.installed||'inconnue')}</strong></p>${s.running?`<div class="banner warning" role="status">Installation de ${esc(s.started.version)} en cours, lancée par ${esc(s.started.actor)}. Le site s’arrête environ une minute puis redémarre : attendre, puis Ctrl+F5.</div>`:''}${s.interrupted?'<div class="banner warning">Une mise à jour précédente est interrompue : la relancer sur le PC hébergeur.</div>':''}${items.length?items.map(p=>`<section class="update-card"><h3>${esc(p.version)} <small>${esc(p.name)}</small></h3>${p.installable?'':`<p class="form-error">${esc(p.reason)}</p>`}<pre class="update-guide">${esc(p.guide)}</pre>${p.installable&&!s.running?`<form class="update-form" data-name="${esc(p.name)}"><p><strong>Avant d’installer :</strong> attendre la fin des lectures et envois, prévenir les utilisateurs. Le site s’arrête environ une minute ; sauvegarde automatique et retour à la version précédente en cas d’échec. Une opération en cours n’est jamais interrompue : l’installation est alors refusée.</p><label>Pour installer, taper OUI<input name="confirm" autocomplete="off" required pattern="OUI"></label><button class="primary">Installer la mise à jour ${esc(p.version)}</button></form>`:''}</section>`).join(''):'<p>Aucune nouvelle mise à jour déposée dans INSTAL MAJ.</p>'}${s.last_result?`<details><summary>Dernière mise à jour réussie</summary><pre class="update-guide">${esc(s.last_result)}</pre></details>`:''}${s.last_log?`<details ${s.running?'open':''}><summary>Journal de la dernière installation lancée depuis le navigateur</summary><pre class="update-guide">${esc(s.last_log)}</pre></details>`:''}<p class="small-note">La mise à jour peut toujours aussi être lancée sur le PC hébergeur avec le script de mise à jour.</p><button data-action="updates">Actualiser l’état</button>`;
  showModal('Mises à jour',body);
  if(s.running)updateTimer=setTimeout(()=>{if(modal.open&&$('.update-card,.banner'))updates().catch(()=>{});},5000);
 }
 async function installUpdate(f){
  const r=await api('/updates/install',{name:f.dataset.name,confirm:f.elements.confirm.value.trim()});
  toast('Installation de '+r.version+' lancée. Le site redémarre dans environ une minute : faire ensuite Ctrl+F5.');
  await updates();
 }
 function click(a){
  if(a==='updates'){run(updates);return true;}
  if(a==='storage-settings'){run(open);return true;}
  if(a==='storage-probe'){run(async()=>{const r=await api('/storage/probe',data($('#storage-form')));toast(r.message);});return true;}
  if(a==='storage-run'){if($('#storage-form')?.dataset.dirty){toast('Enregistrer d’abord les modifications avant de lancer une sauvegarde.',true);return true;}run(async()=>{await api('/storage/run',{});await open();});return true;}
  return false;
 }
 async function submit(f){if(f.classList.contains('update-form')){await installUpdate(f);return true;}if(f.id!=='storage-form')return false;await api('/storage',data(f));await open();toast('Paramètres de sauvegarde enregistrés.');return true;}
 return {open,click,submit};
})();
