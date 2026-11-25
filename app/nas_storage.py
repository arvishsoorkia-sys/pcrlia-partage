# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
"""Verified document offloading and consistent SQLite snapshots, explicitly enabled.

No work at import/construction or upgrade probe. NAS versions are immutable.
Small local pointers keep existing document URLs valid after local offloading.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid

DEFAULTS={'enabled':False,'directory':'','interval_hours':24,'local_days':90}
POINTER='.nas.json'


def long_path(path):
    value=os.path.abspath(path)
    if os.name=='nt' and not value.startswith('\\\\?\\'):
        value='\\\\?\\UNC\\'+value[2:] if value.startswith('\\\\') else '\\\\?\\'+value
    return Path(value)


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def write_json(path,value):
    path=Path(path);temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as stream:
            json.dump(value,stream,ensure_ascii=False,sort_keys=True)
            stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def local_folder(root,kind,identifier):
    if kind not in ('documents','examples') or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',identifier):
        raise ValueError('Référence de document invalide.')
    root=Path(root).resolve();folder=root/kind/identifier
    if (root/kind).resolve().parent!=root or folder.resolve().parent!=(root/kind).resolve() or folder.is_symlink():
        raise ValueError('Emplacement de document invalide.')
    return folder


def pointer(folder):
    path=folder/POINTER
    if not path.exists():return None
    data=json.loads(path.read_text(encoding='utf-8'))
    if data.get('version')!=1 or not isinstance(data.get('files'),dict) or not Path(data['directory']).is_absolute():
        raise ValueError('Référence NAS invalide.')
    return data


def read_file(root,kind,identifier,name):
    if not isinstance(name,str) or Path(name).name!=name or name in ('.','..',POINTER):
        raise ValueError('Nom de document invalide.')
    folder=local_folder(root,kind,identifier)
    try:return (folder/name).read_bytes()
    except FileNotFoundError:
        archive=pointer(folder)
        if not archive or name not in archive['files']:raise
    try:
        content=(Path(archive['directory'])/name).read_bytes()
        if hashlib.sha256(content).hexdigest()!=archive['files'][name]:
            raise ValueError('Le document archivé a changé. Vérifier sa copie sur le NAS.')
        return content
    except OSError as exc:
        raise ValueError('Document conservé sur le NAS, actuellement inaccessible. Les résultats restent consultables ; rétablir l’accès au NAS pour ouvrir le document.') from exc


def materialize(root,kind,identifier):
    """Restore missing analysis inputs locally; re-reading never writes into the NAS."""
    folder=local_folder(root,kind,identifier);archive=pointer(folder)
    if archive:
        for name in archive['files']:
            if not (folder/name).exists():
                content=read_file(root,kind,identifier,name)
                temporary=folder/('.copie-'+uuid.uuid4().hex[:8])
                try:
                    temporary.write_bytes(content);os.replace(temporary,folder/name)
                finally:temporary.unlink(missing_ok=True)
    return folder


def validate(data,root):
    if not isinstance(data,dict) or set(data)-set(DEFAULTS):raise ValueError('Paramètres NAS invalides.')
    config={**DEFAULTS,**data}
    if type(config['enabled']) is not bool:raise ValueError('Activation NAS invalide.')
    for key,maximum in (('interval_hours',720),('local_days',3650)):
        if type(config[key]) is not int or not 1<=config[key]<=maximum:raise ValueError('Fréquence ou durée de conservation invalide.')
    location=config['directory']
    if not isinstance(location,str) or len(location)>1000:raise ValueError('Chemin NAS invalide.')
    if location:
        path=Path(location)
        if not path.is_absolute() or str(path)==path.anchor:raise ValueError('Indiquer un dossier précis du NAS, par exemple \\\\NAS\\Sauvegardes\\PCRLia.')
        # Do not resolve an unavailable network path while merely viewing settings.
        local=Path(os.path.abspath(root));dest=Path(os.path.abspath(path))
        if local==dest or local in dest.parents or dest in local.parents:
            raise ValueError('Le dossier de sauvegarde doit être distinct du dossier de données.')
    elif config['enabled']:raise ValueError('Renseigner le dossier NAS avant l’activation.')
    return config


class Archive:
    def __init__(self,store):
        self.store=store;self.control=threading.RLock();self.thread=None;self.stopping=threading.Event()
        self.last_attempt=0;self.running=False;self.error=''

    def config(self):return {**DEFAULTS,**self.store.settings().get('nas_backup',{})}

    def status(self):
        with self.store.lock:
            state=self.store.settings().get('nas_backup_state',{})
        config=self.config();last=state.get('at')
        next_at=(datetime.fromisoformat(last)+timedelta(hours=config['interval_hours'])).isoformat() if last else None
        return {'config':config,'running':self.running,'error':self.error,'last':state,'next':next_at}

    def configure(self,data,actor):
        config=validate(data,self.store.root)
        with self.control:
            if self.running:raise ValueError('Attendre la fin de la sauvegarde avant de changer ces paramètres.')
            if config['enabled']:self.probe(config)
            with self.store.lock,self.store.db:
                self.store.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('nas_backup',json.dumps(config)))
                self.store.audit(actor,'nas_backup_configured',config)
            self.last_attempt=0;self.error=''
        self.tick()
        return self.status()

    def probe(self,config=None):
        config=config or self.config();validate(config,self.store.root)
        if not config['directory']:raise ValueError('Renseigner le dossier NAS.')
        folder=Path(config['directory'])
        if not folder.is_dir():raise ValueError('Dossier NAS inaccessible pour le compte Windows qui exécute PCRLia.')
        test=folder/('.pcrlia-verification-'+uuid.uuid4().hex)
        try:
            value=os.urandom(64);test.write_bytes(value)
            if test.read_bytes()!=value:raise ValueError('La vérification de la copie sur le NAS a échoué.')
        finally:test.unlink(missing_ok=True)
        return {'ok':True,'message':'Accès en lecture et en écriture vérifié.'}

    def tick(self,force=False):
        with self.control:
            config=self.config()
            if not config['enabled'] or self.running or self.stopping.is_set():return
            status=self.status();last=status['last'].get('at')
            if not force and ((self.last_attempt and time.monotonic()-self.last_attempt<3600) or
                    (last and datetime.now(timezone.utc)<datetime.fromisoformat(last)+timedelta(hours=config['interval_hours']))):return
            self.last_attempt=time.monotonic();self.running=True
            self.thread=threading.Thread(target=self._work,daemon=True,name='PCRLia-NAS');self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.thread:self.thread.join()  # Never close SQLite while the worker still uses it.

    def _work(self):
        try:self.run_once()
        except (OSError,ValueError,sqlite3.Error):self.error='Sauvegarde interrompue ou NAS indisponible. Les fichiers non archivés restent sur ce PC. Vérifiez l’accès au dossier NAS.'
        finally:self.running=False

    def run_once(self,at=None):
        config=validate(self.config(),self.store.root)
        if not config['enabled']:return None
        if not self.store.analysis_lock.acquire(blocking=False):
            self.last_attempt=0
            return None
        try:return self._snapshot(config,at or datetime.now(timezone.utc))
        finally:self.store.analysis_lock.release()

    def _snapshot(self,config,clock):
        self.probe(config)
        with self.store.lock,self.store.db:
            instance=self.store.settings().get('nas_instance_id')
            if not instance:
                instance=uuid.uuid4().hex
                self.store.db.execute('INSERT INTO settings VALUES(?,?)',('nas_instance_id',json.dumps(instance)))
        home=long_path(config['directory'])/('PCRLia-'+instance);home.mkdir(exist_ok=True)
        snapshot_id=clock.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
        pending=home/('.en-cours-'+snapshot_id);pending.mkdir()
        refs=[];cutoff=clock-timedelta(days=config['local_days'])
        with tempfile.TemporaryDirectory(prefix='PCRLia-sauvegarde-') as temp:
            database=Path(temp)/'chlas.sqlite3';copy=sqlite3.connect(database)
            try:
                with self.store.lock:self.store.db.backup(copy)
                if copy.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                    raise ValueError('Intégrité de la sauvegarde à vérifier.')
                documents=[]  # PCRLia keeps no scanned documents: the database is the whole backup.
                imports={}
                for stamp,detail in copy.execute("SELECT at,detail FROM audit WHERE action IN ('document_imported','example_imported') ORDER BY seq"):
                    value=json.loads(detail);identifier=value.get('id') or value.get('document_id')
                    if identifier:imports.setdefault(identifier,stamp)
                for kind,doc,fallback in documents:
                    if self.stopping.is_set():raise ValueError('Sauvegarde interrompue.')
                    folder=local_folder(self.store.root,kind,doc['id'])
                    names=[doc['original'],*(p['image'] for p in doc['pages'])]
                    files={}
                    stage=Path(temp)/uuid.uuid4().hex;stage.mkdir()
                    for name in names:
                        content=read_file(self.store.root,kind,doc['id'],name)
                        (stage/name).write_bytes(content);files[name]=hashlib.sha256(content).hexdigest()
                    if files[doc['original']]!=doc['sha256']:raise ValueError('Original modifié : archivage interrompu.')
                    version=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()
                    destination=home/kind/doc['id']/version
                    destination.mkdir(parents=True,exist_ok=True)
                    for name,expected in files.items():
                        target=destination/name
                        if not target.exists():
                            temporary=destination/('.copie-'+uuid.uuid4().hex[:8])
                            shutil.copyfile(stage/name,temporary)
                            if sha(temporary)!=expected:raise ValueError('Copie NAS incomplète.')
                            os.replace(temporary,target)
                        if sha(target)!=expected:raise ValueError('Copie NAS altérée ; fichiers locaux conservés.')
                    stamp=doc.get('created_at') or imports.get(doc['id']) or fallback
                    try:eligible=datetime.fromisoformat(stamp.replace('Z','+00:00'))<cutoff
                    except (ValueError,TypeError):eligible=False
                    refs.append({'version':1,'kind':kind,'id':doc['id'],'directory':str(destination),
                                 'files':files,'offload':eligible,'imported_at':stamp})
            finally:copy.close()
            shutil.copyfile(database,pending/'chlas.sqlite3')
            if sha(database)!=sha(pending/'chlas.sqlite3'):raise ValueError('Sauvegarde des données incomplète.')
            write_json(pending/'documents.json',refs)
            if json.loads((pending/'documents.json').read_text(encoding='utf-8'))!=refs:raise ValueError('Index de sauvegarde incomplet.')
            write_json(pending/'verification.json',{'database_sha256':sha(database),'documents_sha256':sha(pending/'documents.json'),'at':clock.isoformat()})
        snapshots=home/'sauvegardes';snapshots.mkdir(exist_ok=True)
        final=snapshots/snapshot_id;pending.rename(final)
        # Only a completed, verified snapshot permits removal of exact local copies.
        released=0;count=0
        for ref in refs:
            if not ref['offload'] or self.stopping.is_set():continue
            folder=local_folder(self.store.root,ref['kind'],ref['id'])
            if any(sha(Path(ref['directory'])/name)!=expected for name,expected in ref['files'].items()):
                raise ValueError('Copie NAS devenue indisponible ; fichiers locaux conservés.')
            write_json(folder/POINTER,ref)
            removed=False
            for name,expected in ref['files'].items():
                target=folder/name
                if target.is_file() and not target.is_symlink() and sha(target)==expected:
                    size=target.stat().st_size;target.unlink();released+=size;removed=True
            count+=int(removed)
        state={'at':clock.isoformat(),'snapshot':str(final),'documents':len(refs),'offloaded':count,'released_bytes':released}
        with self.store.lock,self.store.db:
            self.store.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('nas_backup_state',json.dumps(state)))
            self.store.audit('Sauvegarde NAS','nas_backup_completed',state)
        self.error=''
        return state


def archive_for(store):
    with store.lock:
        if not hasattr(store,'nas_archive'):store.nas_archive=Archive(store)
        return store.nas_archive
