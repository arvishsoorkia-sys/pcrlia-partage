# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
"""Dedicated, opt-in Phadia order receiver. Never sends results or tube receipts.

H/P/O/L fields follow the locally supplied MCA exports. The ASTM E1381 wire
profile is exercised against a simulator; the first live MCA trial must still
qualify it. ACK means durable transport receipt, not clinical acceptance.
"""
import ipaddress
import json
import select
import socket
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from . import astm
from .domain import now
from .phadia_orders import decode_orders
from .storage import encode, Conflict

DEFAULTS = {'enabled': False, 'listen_address': '127.0.0.1', 'port': 5511,
            'mca_address': '127.0.0.1', 'encoding': 'cp1252', 'raw_retention_days': 90}
NETWORKS = tuple(ipaddress.ip_network(n) for n in
                 ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8'))
ACTOR = 'MCA / PCRLia'
MAX_MESSAGE = 2_000_000


def catalog_error(error):
    """Retained messages blocked solely by a missing or inactive catalog code."""
    return ((error.startswith('Code de demande ') and ' inconnu :' in error)
            or (error.startswith('Le code de demande ') and error.endswith(' est désactivé.')))


def validate_config(data):
    if not isinstance(data, dict) or set(data) - set(DEFAULTS):
        raise ValueError('Paramètres de réception PCRLia invalides.')
    c = {**DEFAULTS, **data}
    if type(c['enabled']) is not bool:
        raise ValueError('Activation de réception invalide.')
    for key in ('listen_address', 'mca_address'):
        try:
            ip = ipaddress.ip_address(c[key])
        except (ValueError, TypeError):
            raise ValueError('Indiquer deux adresses IPv4 précises.') from None
        if ip.version != 4 or not any(ip in n for n in NETWORKS):
            raise ValueError('Indiquer une adresse du réseau privé, sans masque ni numéro de port.')
        c[key] = str(ip)
    if type(c['port']) is not int or not 1024 <= c['port'] <= 65535 or c['port'] in (5510, 8765, 9443):
        raise ValueError('Choisir un port dédié à PCRLia (5511), distinct du site.')
    if c['encoding'] not in ('ascii', 'cp1252', 'utf-8'):
        raise ValueError('Encodage non pris en charge.')
    if type(c['raw_retention_days']) is not int or not 1 <= c['raw_retention_days'] <= 3650:
        raise ValueError('Conservation des messages bruts : de 1 à 3650 jours.')
    return c


def apply_orders(pcr, decoded):
    """Caller owns the DB transaction: orders, inbox and audit commit together."""
    store = pcr.store
    digest = decoded.get('profile', 'phadia') + ':' + decoded['digest']
    imported = store.db.execute('SELECT summary FROM pcr_imports WHERE digest=?', (digest,)).fetchone()
    previous_summary = json.loads(imported[0]) if imported else None
    # Only explicitly excluded codes are ignored. An unlisted unknown test
    # remains visible for review instead of silently discarding a new analysis.
    ignored_codes = set(pcr.config().get('ignored_mca_codes', []))
    events = [event for event in decoded['events'] if event['request_code'] not in ignored_codes]
    remaining = ({event['request_code'] for event in decoded['events'] if event['request_code'] in ignored_codes}
                 if previous_summary is None else set(previous_summary.get('ignored_code_values', [])))
    if previous_summary is not None:
        events = [event for event in events if event['request_code'] in remaining]
        if not events:
            return {'duplicate': True, 'changed_count': 0}
    if not events:
        return {'ignored_only': True, 'changed_count': 0, 'line_count': 0,
                'ignored_codes': len(decoded['events'])}
    staged, before, ignored, keys = {}, {}, 0, {}
    # Optional « analyse faite » codes: they mark an analysis as performed and
    # never create an expected tube. Unconfigured, nothing changes.
    done_targets = pcr.done_targets()
    for event in events:
        sid, code, stamp = event['sample_id'], event['request_code'], event['message_at']
        target = done_targets.get(code)
        if not target:
            pcr.code(code, active=event['action'] == 'N')
        key = (sid, code)
        if key in keys and keys[key] != event['state']:
            raise Conflict('Ajout et annulation contradictoires dans le même message.')
        keys[key] = event['state']
        if sid not in staged:
            staged[sid], before[sid] = pcr.sample(sid), pcr.sample(sid)
        sample = staged[sid]
        if target:
            done_state = 'fait' if event['state'] == 'attendu' else 'retire'
            old = sample.get('done', {}).get(target, {})
            if old.get('at', '') > stamp:
                ignored += 1
                continue
            if old.get('at') == stamp and old['state'] != done_state:
                raise Conflict('Messages opposés au même horodatage : contrôle SIL/middleware nécessaire.')
        else:
            old = sample['orders'].get(code, {})
            if old.get('mca_at', '') > stamp:
                ignored += 1
                continue
            if old.get('mca_at') == stamp and old['state'] != event['state']:
                raise Conflict('Messages opposés au même horodatage : contrôle SIL/middleware nécessaire.')
        source = event['patient']
        previous_identity = sample.get('mca_patient', {})
        if previous_identity and previous_identity['source_identifier'] != source['source_identifier']:
            raise Conflict('Identité source différente pour un échantillon déjà connu : contrôle SIL/middleware nécessaire.')
        if stamp >= sample.get('mca_identity_at', ''):
            if stamp == sample.get('mca_identity_at') and source != previous_identity:
                raise Conflict('Identités différentes au même horodatage : contrôle SIL/middleware nécessaire.')
            name = ' '.join(v for v in (source['name_field'], source['additional_name_field']) if v)
            if not previous_identity and sample.get('patient') and sample['patient'] != name:
                raise Conflict('Identité importée manuellement différente : contrôle SIL/middleware nécessaire.')
            sample.update(patient=name, mca_patient=source, mca_identity_at=stamp)
            # P[2] is retained as source evidence, never used to group patients or infer a dossier.
        if target:
            # Kept on the exact sample even before its order arrives; history stays in events.
            sample['done'] = {**sample.get('done', {}), target: {
                'state': done_state, 'at': stamp, 'code': code, 'mca_digest': digest,
                'first_at': old.get('first_at') or (stamp if done_state == 'fait' else '')}}
            continue
        receipt = old.get('receipt') or (None if old.get('receipt_voided') else sample.get('tube_receipt'))
        reactivated = old.get('state') == 'annule' and event['state'] == 'attendu'
        sample['orders'][code] = {**old, 'code': code, 'source_label': pcr.code(code, active=False)['label'],
            'date_demande': old.get('date_demande', ''), 'state': event['state'],
            'date_remontee_mca': old.get('date_remontee_mca') or pcr.mca_request_date(sample,code) or stamp[:10],
            'receipt': receipt, 'last_scan': old.get('last_scan') or sample.get('tube_last_scan'),
            'receipt_voided': old.get('receipt_voided', False),
            'reactivated_at': now() if reactivated else old.get('reactivated_at', ''),
            'reactivated_revision': sample['revision'] + 1 if reactivated else old.get('reactivated_revision', 0),
            'mca_first_at': pcr.mca_first_stamp(sample, code) or stamp,
            'mca_at': stamp, 'mca_digest': digest}
    changed = 0
    for sid, sample in staged.items():
        if sample != before[sid]:
            pcr.sync_issues(sample)
            detail = {'digest': digest, 'message_at': decoded['message_at'],
                      'before': before[sid]['orders'], 'after': sample['orders']}
            orders_changed = sample['orders'] != before[sid]['orders']
            if sample.get('done', {}) != before[sid].get('done', {}):
                detail.update(done_before=before[sid].get('done', {}), done_after=sample['done'])
            pcr.event(sample, ACTOR, 'orders_received_mca' if orders_changed or 'done_after' not in detail
                      else 'analysis_done_mca', detail)
            changed += 1
    remaining.difference_update(event['request_code'] for event in events)
    summary = {'changed_count': changed, 'ignored_older': ignored,
               'line_count': (previous_summary or {}).get('line_count', 0) + len(events),
               'ignored_codes': sum(event['request_code'] in remaining for event in decoded['events']),
               'ignored_code_values': sorted(remaining)}
    if previous_summary is None:
        store.db.execute('INSERT INTO pcr_imports VALUES(?,?,?,?,?)',
                         (digest, now(), ACTOR, decoded.get('source', 'SIL/middleware / Phadia'), encode(summary)))
    else:
        store.db.execute('UPDATE pcr_imports SET summary=? WHERE digest=?', (encode(summary), digest))
    store.audit(ACTOR, 'pcr_mca_orders_received', {'digest': digest, **summary})
    return summary


class Receiver:
    def __init__(self, store):
        self.store = store
        self.config = validate_config(store.settings().get('pcr_mca', {}))
        self.control = threading.RLock()
        self.stopping = threading.Event()
        self.listener = self.connection = self.thread = None
        self.started = self.connected = False
        self.error = ''
        self.last_maintenance = None
        self.retention_error = ''

    def _exists(self):
        return bool(self.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pcr_mca_inbox'").fetchone())

    def status(self):
        with self.store.lock:
            counts, last, issues = {}, None, []
            if self._exists():
                counts = dict(self.store.db.execute('SELECT state,count(*) FROM pcr_mca_inbox GROUP BY state'))
                row = self.store.db.execute("SELECT at FROM pcr_mca_inbox WHERE state IN ('applied','purged') ORDER BY rowid DESC LIMIT 1").fetchone()
                last = row[0] if row else None
                issues = [dict(r) for r in self.store.db.execute("SELECT id,at,state,error FROM pcr_mca_inbox WHERE state IN ('quarantined','interrupted','ready') ORDER BY rowid DESC LIMIT 10")]
        pending = sum(counts.get(s, 0) for s in ('quarantined', 'interrupted', 'ready'))
        message = self.error or ('SIL/middleware connecté · réception active.' if self.connected else
                   'En attente de la connexion SIL/middleware.' if self.listener else 'Connexion SIL/middleware désactivée.')
        return {'enabled': self.config['enabled'], 'connected': self.connected, 'listening': bool(self.listener),
                'complete': False, 'mode': 'phadia' if self.config['enabled'] else 'manual',
                'message': message, 'error': self.error, 'pending': pending, 'counts': counts,
                'last_received': last, 'issues': issues, 'raw_retention_days': self.config['raw_retention_days'],
                'purged_messages': counts.get('purged',0) + counts.get('ignored_purged',0),
                'retention_error': self.retention_error}

    def purge_received(self, at=None):
        """Release old applied/ignored payloads; retain metadata, orders and errors."""
        cutoff=((at or datetime.now(timezone.utc))-timedelta(days=self.config['raw_retention_days'])).isoformat()
        with self.store.lock, self.store.db:
            if not self._exists():return 0
            rows=list(self.store.db.execute("SELECT id,state FROM pcr_mca_inbox WHERE state IN ('applied','ignored') AND datetime(at)<datetime(?) ORDER BY rowid LIMIT 1000",(cutoff,)))
            if not rows:return 0
            self.store.db.execute('PRAGMA secure_delete=ON')
            self.store.db.executemany("UPDATE pcr_mca_inbox SET payload=?,state=? WHERE id=? AND state=?",
                [(b'', 'purged' if r['state']=='applied' else 'ignored_purged', r['id'], r['state']) for r in rows])
            self.store.audit(ACTOR,'pcr_mca_raw_purged',{'count':len(rows),'retention_days':self.config['raw_retention_days'],'before':cutoff})
            return len(rows)

    def configure(self, data, actor):
        config = validate_config(data)
        existing = self.store.settings().get('mca_identity', {})
        if existing.get('port', 5510) == config['port']:
            raise ValueError('Ce port est réservé à une autre liaison.')
        with self.control:
            self.stop()
            with self.store.lock, self.store.db:
                self.store.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('pcr_mca', encode(config)))
                self.store.audit(actor, 'pcr_mca_configured', config)
            self.config = config
            self.last_maintenance = None
            self.start()
        return {'config': self.config, **self.status()}

    def start(self):
        with self.control:
            tick=time.monotonic()
            if self.last_maintenance is None or tick-self.last_maintenance>=3600:
                self.last_maintenance=tick
                try:
                    self.purge_received()
                    self.retention_error=''
                except sqlite3.Error:
                    self.retention_error='Nettoyage des messages bruts interrompu ; nouvelle tentative dans une heure.'
            if self.started:
                return
            self.started = True
            if not self.config['enabled']:
                return
            # Created only on explicit activation, never by updater health checks.
            from .pcr import PCRService
            self.service = PCRService(self.store)
            with self.store.lock, self.store.db:
                self.store.db.execute('''CREATE TABLE IF NOT EXISTS pcr_mca_inbox(
                    id TEXT PRIMARY KEY, at TEXT NOT NULL, peer TEXT NOT NULL,
                    payload BLOB NOT NULL, encoding TEXT NOT NULL, state TEXT NOT NULL, error TEXT NOT NULL)''')
                interrupted = list(self.store.db.execute("SELECT id,payload,encoding FROM pcr_mca_inbox WHERE state='ready'"))
            # A complete, checksummed message may have been durably saved just
            # before a process interruption. Finish it idempotently on restart.
            for row in interrupted:
                self._apply(row['id'], row['payload'], row['encoding'])
            self.recheck_ignored(self.service)
            with self.store.lock, self.store.db:
                self.store.db.execute("UPDATE pcr_mca_inbox SET state='interrupted',error=? WHERE state='receiving'",
                                      ('Transmission interrompue ; demander un nouvel envoi dans le SIL/middleware.',))
            listener = None
            try:
                listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                listener.bind((self.config['listen_address'], self.config['port']))
                listener.listen(2)
                listener.settimeout(.3)
            except OSError:
                if listener:
                    listener.close()
                self.error = 'Écoute PCRLia impossible : vérifier l’adresse de ce PC et le port disponible.'
                return
            self.listener, self.error = listener, ''
            self.stopping.clear()
            self.thread = threading.Thread(target=self._serve, name='pcrlia-mca-receiver', daemon=True)
            self.thread.start()

    def stop(self):
        with self.control:
            self.stopping.set()
            for conn in (self.connection, self.listener):
                if conn:
                    try:
                        conn.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    conn.close()
            if self.thread:
                self.thread.join(timeout=35)
                if self.thread.is_alive():
                    raise RuntimeError('La réception PCRLia ne s’est pas arrêtée. Réessayer avant de quitter.')
            self.listener = self.connection = self.thread = None
            self.started = self.connected = False

    def _apply(self, rid, payload, encoding):
        try:
            decoded = decode_orders(payload, encoding)
            with self.store.lock, self.store.db:
                previous = self.store.db.execute('SELECT state FROM pcr_mca_inbox WHERE id=?', (rid,)).fetchone()
                result = apply_orders(self.service, decoded)
                state = 'ignored' if result.get('ignored_only') else 'applied'
                self.store.db.execute("UPDATE pcr_mca_inbox SET state=?,error='' WHERE id=?", (state, rid))
                if state == 'ignored' and previous['state'] != 'ignored':
                    self.store.audit(ACTOR, 'pcr_mca_message_ignored',
                                     {'id': rid, 'ignored_codes': result['ignored_codes']})
        except ValueError as exc:
            # Persist unsupported application content, do not pretend it became orders.
            with self.store.lock, self.store.db:
                self.store.db.execute("UPDATE pcr_mca_inbox SET state='quarantined',error=? WHERE id=?", (str(exc), rid))
                self.store.audit(ACTOR, 'pcr_mca_message_quarantined', {'id': rid, 'reason': str(exc)})

    def recheck_ignored(self, service):
        """Resolve only retained complete messages affected by an exclusion."""
        with self.control, self.store.lock:
            ignored = set(service.config().get('ignored_mca_codes', []))
            if not ignored or not self._exists():
                return
            self.service = service
            rows = list(self.store.db.execute(
                "SELECT id,payload,encoding,error FROM pcr_mca_inbox WHERE state='quarantined' ORDER BY rowid"))
            for row in rows:
                if not catalog_error(row['error']):
                    continue
                try:
                    events = decode_orders(row['payload'], row['encoding'])['events']
                except ValueError:
                    continue
                if any(event['request_code'] in ignored for event in events):
                    self._apply(row['id'], row['payload'], row['encoding'])

    def retry(self, actor, reinclude_only=False):
        from .pcr import PCRService
        self.service = PCRService(self.store)
        with self.control, self.store.lock:
            included_before = sum(len(json.loads(r[0]).get('orders', {})) for r in self.store.db.execute('SELECT payload FROM pcr_samples'))
            rows = []
            remaining_reinclude = 0
            if self._exists():
                ignored = set(self.service.config().get('ignored_mca_codes', []))
                catalog = {c['code']: c for c in self.service.codes()}
                for row in self.store.db.execute("SELECT id,payload,state,error,encoding FROM pcr_mca_inbox WHERE state IN ('quarantined','ready','ignored','applied') ORDER BY rowid"):
                    if reinclude_only and (row['state'] == 'ready' or
                            (row['state'] == 'quarantined' and not catalog_error(row['error']))):
                        continue
                    if reinclude_only or row['state'] in ('ignored', 'applied'):
                        try:
                            decoded = decode_orders(row['payload'], row['encoding'])
                        except ValueError:
                            if reinclude_only:
                                continue
                            raise
                        candidates = {e['request_code'] for e in decoded['events']} - ignored
                        if row['state'] == 'applied':
                            digest = decoded.get('profile', 'phadia') + ':' + decoded['digest']
                            done = self.store.db.execute('SELECT summary FROM pcr_imports WHERE digest=?', (digest,)).fetchone()
                            candidates.intersection_update(json.loads(done[0]).get('ignored_code_values', []) if done else [])
                        if not candidates:
                            continue
                        if reinclude_only and any(
                                e['request_code'] in candidates and
                                (e['request_code'] not in catalog or
                                 (e['action']=='N' and not catalog[e['request_code']]['active']))
                                for e in decoded['events']):
                            continue
                    if len(rows) < 100:
                        rows.append(row)
                    else:
                        remaining_reinclude += 1
                for row in rows:
                    self._apply(row['id'], row['payload'], row['encoding'] if reinclude_only else self.config['encoding'])
                with self.store.db:
                    self.store.audit(actor, 'pcr_mca_reinclude' if reinclude_only else 'pcr_mca_retry', {'count': len(rows)})
            included_after = sum(len(json.loads(r[0]).get('orders', {})) for r in self.store.db.execute('SELECT payload FROM pcr_samples'))
        return {**self.status(), 'reexamined_messages': len(rows),
                'included_analyses': included_after - included_before,
                'remaining_reinclude': remaining_reinclude}

    def _receive(self, wire):
        rid, payload, previous, expected, errors, complete = uuid.uuid4().hex, b'', None, 1, 0, False
        with self.store.lock, self.store.db:
            self.store.db.execute('INSERT INTO pcr_mca_inbox VALUES(?,?,?,?,?,?,?)',
                (rid, now(), self.config['mca_address'], b'', self.config['encoding'], 'receiving', ''))
        wire.send(astm.ACK, time.monotonic() + 15)
        try:
            while not self.stopping.is_set():
                packet = wire.token(time.monotonic() + 30)
                if packet == astm.EOT:
                    if not complete:
                        raise astm.ProtocolError('Message SIL/middleware incomplet ; nouvel envoi nécessaire.')
                    return
                if packet == previous:
                    wire.send(astm.ACK, time.monotonic() + 15)
                    continue
                try:
                    number, part, final = astm.decode_frame(packet)
                    if number != expected or (final and not (payload + part).endswith(astm.CR)):
                        raise astm.BadFrame('Ordre ou fin de trame incorrect.')
                except astm.BadFrame:
                    errors += 1
                    wire.send(astm.NAK, time.monotonic() + 15)
                    if errors >= 6:
                        raise astm.ProtocolError('Six trames refusées : vérifier le profil ASTM dans le SIL/middleware.')
                    continue
                if complete:
                    raise astm.ProtocolError('Plusieurs messages dans une transmission : profil à qualifier.')
                if len(payload) + len(part) > MAX_MESSAGE:
                    raise astm.ProtocolError('Transmission PCRLia trop volumineuse.')
                payload += part
                complete = final and payload.rstrip(b'\r').split(b'\r')[-1].startswith(b'L|')
                # Every positively acknowledged frame survives process restart.
                with self.store.lock, self.store.db:
                    self.store.db.execute('UPDATE pcr_mca_inbox SET payload=?,state=? WHERE id=?',
                        (payload, 'ready' if complete else 'receiving', rid))
                if complete:
                    self._apply(rid, payload, self.config['encoding'])
                previous, expected, errors = packet, (expected + 1) % 8, 0
                wire.send(astm.ACK, time.monotonic() + 15)
        finally:
            with self.store.lock, self.store.db:
                self.store.db.execute("UPDATE pcr_mca_inbox SET state='interrupted',error=? WHERE id=? AND state='receiving'",
                    ('Transmission interrompue ; demander un nouvel envoi dans le SIL/middleware.', rid))

    def _serve(self):
        while not self.stopping.is_set():
            try:
                conn, peer = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if peer[0] != self.config['mca_address']:
                conn.close()
                self.error = 'Connexion refusée : adresse source différente du SIL/middleware autorisé.'
                continue
            self.connection, self.connected, self.error = conn, True, ''
            wire = astm.Wire(conn)
            try:
                while not self.stopping.is_set():
                    if not wire.buffer and not select.select([conn], [], [], .3)[0]:
                        continue
                    if wire.token(time.monotonic() + 30) != astm.ENQ:
                        raise astm.ProtocolError('Début ASTM non reconnu : vérifier le format ImmunoCAP PHADIA.')
                    self._receive(wire)
            except (OSError, ValueError, sqlite3.Error) as exc:
                self.error = ('' if isinstance(exc, ConnectionError) and str(exc) == 'Connexion MCA interrompue.' else
                              str(exc) if isinstance(exc, astm.ProtocolError) else 'Connexion interrompue ; les demandes déjà reçues sont conservées.')
            finally:
                conn.close()
                self.connection, self.connected = None, False


def receiver_for(store):
    with store.lock:
        if not hasattr(store, 'pcr_receiver'):
            store.pcr_receiver = Receiver(store)
        return store.pcr_receiver
