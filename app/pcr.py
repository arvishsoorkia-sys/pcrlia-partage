# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
"""PCRLia: exact specimen receipt, delays, comments and follow-up of PCR tubes.

Manual CSV imports are incremental, explicitly labelled incomplete. The optional
MCA receiver lives in pcr_mca; this service never opens a socket.
"""
import csv
from datetime import date
import hashlib
import io
import json
import re
from uuid import uuid4

from .domain import now
from .storage import Conflict, encode
from . import pcr_numbering


COLUMNS = ['dossier', 'echantillon', 'analyse', 'libelle', 'prelevement',
           'date_demande', 'date_prelevement', 'site', 'etat']
PATIENT_COLUMNS = ['patient_id', 'patient']
GROUPS = [{'id': 'ist', 'label': 'PCR IST'}, {'id': 'covid', 'label': 'PCR COVID'},
          {'id': 'parasito', 'label': 'PCR PARASITO'}, {'id': 'other', 'label': 'Autres PCR'}]
GROUP_IDS = {g['id'] for g in GROUPS}
# Example catalogue, created only in demonstration mode (PCRLIA_DEMO=1); a real installation starts empty.
EXAMPLE_ANALYSES = [('CTNG1', 'ist', '88'), ('CTNG2', 'ist', '87'), ('CTNG3', 'ist', '86'),
                    ('COVID', 'covid', '85'), ('PARASITO', 'parasito', '44')]
ERROR_SITE = 'TUBES EN ERREUR'
HANDLING_LABELS = {'verified': 'Vérifié', 'removed': 'Retiré du lot',
                   'managed': 'Prise en charge confirmée',
                   'kept': 'À faire en PCR',
                   'same_tube': 'Un seul tube, scanné deux fois par inattention — vérifié',
                   'two_tubes_removed': 'Deux tubes avec ce code-barres — retirés du lot',
                   'two_tubes_regularized': 'Deux échantillons avec ce code-barres — régularisé, échantillon conservé',
                   'cancel_confirmed': 'Demande réellement annulée — tube retiré du lot',
                   'cancel_error': 'Demande annulée par erreur (toujours sur la prescription) — tube gardé pour l’analyse, demande à rétablir dans le SIL'}
REMOVE_ACTIONS = ('removed', 'cancel_confirmed')
KEEP_ACTIONS = ('kept', 'cancel_error')
DUPLICATE_ACTIONS = ('same_tube', 'two_tubes_regularized', 'two_tubes_removed')
DELAY_KEYS = ('receipt', 'result')
# French public holidays (computed each year in the browser, Easter included).
HOLIDAYS = ('jan1', 'easter_mon', 'may1', 'may8', 'ascension', 'pentecost_mon',
            'jul14', 'aug15', 'nov1', 'nov11', 'dec25')


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', value.strip()):
        raise ValueError('Scanner le numéro d’échantillon complet, sans espace (80 caractères maximum).')
    return value.strip()


def csv_rows(content):
    if not isinstance(content, str) or len(content.encode('utf-8')) > 2_000_000:
        raise ValueError('Liste CSV trop volumineuse (2 Mo maximum).')
    content = content.lstrip('\ufeff')
    reader = csv.DictReader(io.StringIO(content), delimiter=';', strict=True)
    try:
        header = reader.fieldnames
    except csv.Error:
        raise ValueError('En-tête CSV illisible.') from None
    if not header or len(set(header)) != len(header):
        raise ValueError('En-tête CSV absent ou dupliqué.')
    if not set(COLUMNS).issubset(header) or set(header) - set(COLUMNS + PATIENT_COLUMNS):
        raise ValueError('Colonnes attendues : ' + ';'.join(COLUMNS))
    rows, keys, specimens = [], {}, {}
    try:
        for number, raw in enumerate(reader, 2):
            if len(rows) >= 5000:
                raise ValueError('Limiter chaque import à 5 000 lignes.')
            if None in raw or any(v is None for v in raw.values()):
                raise ValueError(f'Ligne {number} : nombre de colonnes incorrect.')
            row = {k: v.strip() for k, v in raw.items()}
            for key in PATIENT_COLUMNS:
                row.setdefault(key, '')
            if any(len(v) > 200 or any(ord(c) < 32 for c in v) for v in row.values()):
                raise ValueError(f'Ligne {number} : champ trop long ou caractère de contrôle.')
            for key in ('echantillon', 'analyse'):
                row[key] = identifier(row[key])
            for key in ('dossier', 'patient_id'):
                if row[key]:
                    row[key] = identifier(row[key])
            if row['etat'] not in ('attendu', 'annule'):
                raise ValueError(f'Ligne {number} : état attendu ou annule requis.')
            for key in ('date_demande', 'date_prelevement'):
                value = row[key]
                if not value and key == 'date_prelevement':
                    continue
                try:
                    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                        raise ValueError()
                    date.fromisoformat(value)
                except ValueError:
                    raise ValueError(f'Ligne {number} : {key} doit être une date AAAA-MM-JJ valide.') from None
            identity = tuple(row[k] for k in ('dossier', 'patient_id', 'patient', 'prelevement', 'date_prelevement', 'site'))
            if row['echantillon'] in specimens and specimens[row['echantillon']] != identity:
                raise ValueError(f'Ligne {number} : informations contradictoires pour le même échantillon.')
            specimens[row['echantillon']] = identity
            key = (row['echantillon'], row['analyse'])
            if key in keys:
                if keys[key] != row:
                    raise ValueError(f'Ligne {number} : demandes contradictoires pour le même échantillon et la même analyse.')
                continue
            keys[key] = row
            rows.append(row)
    except csv.Error:
        raise ValueError('CSV illisible : vérifier les guillemets et séparateurs.') from None
    if not rows:
        raise ValueError('La liste ne contient aucune demande.')
    return rows


def empty_sample(sample_id):
    return {'sample_id': sample_id, 'dossier': '', 'prelevement': '', 'date_prelevement': '',
            'patient_id': '', 'patient': '', 'site': '', 'orders': {}, 'last_scan': None, 'revision': 0}


class PCRService:
    def __init__(self, store):
        self.store = store
        self._first_mca = {}
        with store.lock, store.db:
            store.db.executescript('''
            CREATE TABLE IF NOT EXISTS pcr_samples(sample_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS pcr_events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
                sample_id TEXT NOT NULL, at TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS pcr_events_sample ON pcr_events(sample_id,seq);
            CREATE TABLE IF NOT EXISTS pcr_imports(digest TEXT PRIMARY KEY, at TEXT NOT NULL,
                actor TEXT NOT NULL, source TEXT NOT NULL, summary TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS pcr_scan_requests(request_id TEXT PRIMARY KEY,
                sample_id TEXT NOT NULL, actor TEXT NOT NULL, response TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS pcr_codes(code TEXT PRIMARY KEY, label TEXT NOT NULL,
                group_key TEXT NOT NULL, active INTEGER NOT NULL, revision INTEGER NOT NULL,
                suffix TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS pcr_config(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
            ''')
            if 'suffix' not in {row['name'] for row in store.db.execute('PRAGMA table_info(pcr_codes)')}:
                store.db.execute("ALTER TABLE pcr_codes ADD COLUMN suffix TEXT NOT NULL DEFAULT ''")
            defaults = {'revision': 1, 'suffixes': [
                {'suffix': suffix, 'group': group, 'active': active} for suffix, group, active in
                [('86', 'ist', True), ('87', 'ist', True), ('88', 'ist', True),
                 ('44', 'parasito', True), ('85', 'covid', True)]], 'dossier_strip': 0}
            store.db.execute('INSERT OR IGNORE INTO pcr_config VALUES(1,?)', (encode(defaults),))
            config = self.config()
            if not config.get('analysis_defaults_version'):
                # Install once, preserving existing codes and all later user changes.
                added = []
                for code, group, suffix in (EXAMPLE_ANALYSES if pcr_numbering.demo() else []):
                    inserted = store.db.execute('''INSERT OR IGNORE INTO pcr_codes
                        (code,label,group_key,active,revision,suffix) VALUES(?,?,?,1,1,?)''',
                        (code, code, group, suffix))
                    if inserted.rowcount:
                        added.append(code)
                config['analysis_defaults_version'] = 1
                config['revision'] += 1
                store.db.execute('UPDATE pcr_config SET payload=? WHERE id=1', (encode(config),))
                store.audit('system', 'pcr_catalog_initialized', {'version': 1, 'added_codes': added})
            # Upgrade existing samples once; never invent a technician's confirmation.
            for row in store.db.execute('SELECT payload FROM pcr_samples').fetchall():
                sample = json.loads(row[0])
                if 'issues' not in sample:
                    self.sync_issues(sample)
                    self.event(sample, 'system', 'tracking_initialized', {})

    def config(self):
        with self.store.lock:
            config = json.loads(self.store.db.execute('SELECT payload FROM pcr_config WHERE id=1').fetchone()[0])
            config.setdefault('numbering', pcr_numbering.defaults())
            # Read-time only (nothing rewritten at startup): directory addresses for
            # laboratories saved before addresses existed. A saved address, even
            # empty, always wins.
            pcr_numbering.add_default_emails(config['numbering'])
            config.setdefault('ignored_mca_codes', [])
            # Optional: analysis code -> MCA code announcing that the analysis was performed.
            config.setdefault('done_codes', {})
            # Optional per-analysis delays in hours: {'CTNG1': {'receipt': 48, 'result': 24}}.
            config.setdefault('delays', {})
            # Global switches for the two delay alerts and their visuals.
            # The result alert stays off at installation: it depends on MCA « analyse faite » rules
            # that the laboratory has not created yet (decision of 2026-11-20).
            config.setdefault('delay_alerts', {'receipt': True, 'result': False})
            # Days without reception, excluded from delays: weekdays (0 = Monday … 6 = Sunday),
            # French public holidays switched off by key, and exceptional closing dates.
            config.setdefault('closed_days', {'weekdays': [6], 'holidays_off': [], 'extra_dates': []})
            return config

    def done_targets(self):
        """SIL/middleware « analyse faite » code -> analysis code. Empty when nothing is configured."""
        return {done: code for code, done in self.config()['done_codes'].items()}

    def configure(self, data, actor):
        suffixes, strip = data.get('suffixes'), data.get('dossier_strip')
        if not isinstance(suffixes, list) or len(suffixes) > 100 or type(strip) is not int or not 0 <= strip <= 20:
            raise ValueError('Correspondances de tubes ou transcodage invalide.')
        seen, clean = set(), []
        for rule in suffixes:
            if (not isinstance(rule, dict) or not isinstance(rule.get('suffix'), str)
                    or not re.fullmatch(r'[0-9]{2}', rule['suffix']) or rule['suffix'] in seen
                    or rule.get('group') not in GROUP_IDS or type(rule.get('active')) is not bool):
                raise ValueError('Chaque terminaison doit contenir deux chiffres et un seul type de PCR.')
            seen.add(rule['suffix'])
            clean.append({k: rule[k] for k in ('suffix', 'group', 'active')})
        with self.store.lock, self.store.db:
            before = self.config()
            if type(data.get('revision')) is not int or data['revision'] != before['revision']:
                raise Conflict('Ce paramétrage a changé. Le rouvrir avant d’enregistrer.')
            numbering = pcr_numbering.validate(data.get('numbering', before['numbering']))
            ignored = data.get('ignored_mca_codes', before['ignored_mca_codes'])
            if not isinstance(ignored, list) or len(ignored) > 200:
                raise ValueError('Indiquer au maximum 200 codes SIL/middleware à ignorer.')
            ignored = sorted(set(identifier(code) for code in ignored))
            active_codes = {row[0] for row in self.store.db.execute('SELECT code FROM pcr_codes WHERE active=1')}
            if active_codes.intersection(ignored):
                raise ValueError('Une analyse active ne peut pas être ignorée. Désactivez-la d’abord dans le catalogue.')
            if set(before['done_codes'].values()).intersection(ignored):
                raise ValueError('Un code « analyse faite » paramétré ne peut pas être ignoré. Retirez-le d’abord de l’analyse.')
            alerts = data.get('delay_alerts', before['delay_alerts'])
            if (not isinstance(alerts, dict) or set(alerts) != set(DELAY_KEYS)
                    or any(type(v) is not bool for v in alerts.values())):
                raise ValueError('Activation des retards invalide.')
            closed = self.closed_days_value(data.get('closed_days', before['closed_days']))
            after = {**before, 'revision': before['revision'] + 1, 'suffixes': clean, 'dossier_strip': strip,
                     'numbering': numbering, 'ignored_mca_codes': ignored, 'delay_alerts': dict(alerts),
                     'closed_days': closed}
            self.store.db.execute('UPDATE pcr_config SET payload=? WHERE id=1', (encode(after),))
            self.store.audit(actor, 'pcr_configured', {'before': before, 'after': after})
            for row in self.store.db.execute('SELECT payload FROM pcr_samples').fetchall():
                sample = json.loads(row[0])
                if self.sync_issues(sample):
                    self.event(sample, actor, 'alerts_updated', {'config_revision': after['revision']})
            return after

    def codes(self):
        with self.store.lock:
            config = self.config()
            done, delays = config['done_codes'], config['delays']
            return [{**dict(r), 'active': bool(r['active']), 'done_code': done.get(r['code'], ''),
                     'receipt_delay_hours': delays.get(r['code'], {}).get('receipt'),
                     'result_delay_hours': delays.get(r['code'], {}).get('result')}
                    for r in self.store.db.execute('SELECT * FROM pcr_codes ORDER BY code')]

    @staticmethod
    def closed_days_value(value):
        """Validate the days without reception; holidays are computed in the browser."""
        if not isinstance(value, dict) or set(value) != {'weekdays', 'holidays_off', 'extra_dates'}:
            raise ValueError('Jours sans réception invalides.')
        weekdays, off, extra = value['weekdays'], value['holidays_off'], value['extra_dates']
        if (not isinstance(weekdays, list) or any(type(d) is not int or not 0 <= d <= 6 for d in weekdays)
                or len(set(weekdays)) == 7):
            raise ValueError('Choisir les jours de la semaine sans réception (au moins un jour ouvert).')
        if not isinstance(off, list) or any(k not in HOLIDAYS for k in off):
            raise ValueError('Jour férié inconnu.')
        if not isinstance(extra, list) or len(extra) > 200:
            raise ValueError('200 dates de fermeture au maximum.')
        for day in extra:
            try:
                if not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
                    raise ValueError()
                date.fromisoformat(day)
            except ValueError:
                raise ValueError(f'Date de fermeture invalide : {day}. Format JJ/MM/AAAA.') from None
        return {'weekdays': sorted(set(weekdays)), 'holidays_off': sorted(set(off)), 'extra_dates': sorted(set(extra))}

    @staticmethod
    def delay_value(value, label):
        """Optional whole number of hours; empty or absent means no delay."""
        if value in (None, ''):
            return None
        if isinstance(value, str) and re.fullmatch(r'[0-9]{1,4}', value.strip()):
            value = int(value.strip())
        if type(value) is not int or not 1 <= value <= 2000:
            raise ValueError(f'{label} : indiquer un nombre d’heures entre 1 et 2000, ou laisser vide.')
        return value

    def configure_code(self, data, actor):
        code = identifier(data.get('code'))
        original_code = identifier(data.get('original_code') or code)
        label = data.get('label', '')
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 160 or any(ord(c) < 32 for c in label):
            raise ValueError('Indiquer un libellé de 1 à 160 caractères.')
        if data.get('group_key') not in GROUP_IDS or type(data.get('active')) is not bool or type(data.get('revision')) is not int:
            raise ValueError('Groupe, activation ou version du code invalide.')
        with self.store.lock, self.store.db:
            config = self.config()
            if data['active'] and code in config['ignored_mca_codes']:
                raise ValueError('Retirez ce code de la liste des codes SIL/middleware à ignorer avant d’activer l’analyse.')
            done_codes = dict(config['done_codes'])
            if any(done == code for owner, done in done_codes.items() if owner != original_code):
                raise ValueError('Ce code est déjà utilisé comme code « analyse faite » d’une autre analyse.')
            # Optional and never required: an absent field keeps the current value.
            done_code = data.get('done_code', done_codes.get(original_code, ''))
            if not isinstance(done_code, str):
                raise ValueError('Code « analyse faite » invalide.')
            done_code = done_code.strip()
            if done_code:
                done_code = identifier(done_code)
                if done_code in (code, original_code):
                    raise ValueError('Le code « analyse faite » doit être différent du code automate de l’analyse.')
                if self.store.db.execute('SELECT 1 FROM pcr_codes WHERE code=?', (done_code,)).fetchone():
                    raise ValueError('Le code « analyse faite » est déjà le code automate d’une analyse.')
                if any(done == done_code for owner, done in done_codes.items() if owner != original_code):
                    raise ValueError('Ce code « analyse faite » est déjà associé à une autre analyse.')
                if done_code in config['ignored_mca_codes']:
                    raise ValueError('Retirez d’abord ce code de la liste des codes SIL/middleware à ignorer.')
            delays = dict(config['delays'])
            current_delays = delays.get(original_code, {})
            new_delays = {}
            for key, field, delay_label in (('receipt', 'receipt_delay_hours', 'Délai de réception'),
                                            ('result', 'result_delay_hours', 'Délai de résultat')):
                value = self.delay_value(data[field], delay_label) if field in data else current_delays.get(key)
                if value:
                    new_delays[key] = value
            previous = self.store.db.execute('SELECT * FROM pcr_codes WHERE code=?', (original_code,)).fetchone()
            revision = previous['revision'] if previous else 0
            if revision != data['revision']:
                raise Conflict('Ce code a changé. Actualiser le paramétrage.')
            replacing = original_code != code
            if replacing and (previous is None or self.store.db.execute('SELECT 1 FROM pcr_codes WHERE code=?', (code,)).fetchone()):
                raise Conflict('Le nouveau code existe déjà ou le code d’origine est absent. Choisir un code distinct.')
            suffix = data.get('suffix', previous['suffix'] if previous else '')
            if not isinstance(suffix, str) or (suffix and not re.fullmatch(r'[0-9]{2}', suffix)):
                raise ValueError('Le suffixe doit contenir deux chiffres, ou rester vide si non défini.')
            if 'suffix' in data and data['active'] and not suffix:
                raise ValueError('Indiquer le suffixe à deux chiffres de cette analyse active.')
            if replacing:
                self.store.db.execute('UPDATE pcr_codes SET active=0,revision=revision+1 WHERE code=?', (original_code,))
            self.store.db.execute('''INSERT OR REPLACE INTO pcr_codes
                (code,label,group_key,active,revision,suffix) VALUES(?,?,?,?,?,?)''',
                (code, label.strip(), data['group_key'], int(data['active']), 1 if replacing else revision + 1, suffix))
            done_codes.pop(original_code, None)
            if done_code:
                done_codes[code] = done_code
            delays.pop(original_code, None)
            if new_delays:
                delays[code] = new_delays
            if done_codes != config['done_codes'] or delays != config['delays']:
                self.store.db.execute('UPDATE pcr_config SET payload=? WHERE id=1',
                                      (encode({**config, 'done_codes': done_codes, 'delays': delays}),))
            self.store.audit(actor, 'pcr_code_configured', {'before': dict(previous) if previous else None,
                'replaced_code': original_code if replacing else None,
                'after': {'code': code, 'label': label.strip(), 'group_key': data['group_key'],
                          'active': data['active'], 'suffix': suffix, 'done_code': done_code,
                          'delays': new_delays}})
            for row in self.store.db.execute('SELECT payload FROM pcr_samples').fetchall():
                sample = json.loads(row[0])
                if self.sync_issues(sample):
                    self.event(sample, actor, 'alerts_updated', {'request_code': code})
            return self.codes()

    def catalog_digest(self):
        return hashlib.sha256(encode({'codes': self.codes(), 'config': self.config()}).encode()).hexdigest()

    def delete_code(self, data, actor):
        code = identifier(data.get('code'))
        with self.store.lock, self.store.db:
            previous = self.code(code, active=False)
            if type(data.get('revision')) is not int or data['revision'] != previous['revision']:
                raise Conflict('Ce code a changé. Actualiser le paramétrage.')
            if any(code in json.loads(row[0]).get('orders', {}) for row in
                   self.store.db.execute('SELECT payload FROM pcr_samples')):
                raise ValueError('Cette analyse est liée à des échantillons. Désactivez-la pour conserver leur historique.')
            self.store.db.execute('DELETE FROM pcr_codes WHERE code=?', (code,))
            config = self.config()
            if code in config['done_codes'] or code in config['delays']:
                config['done_codes'].pop(code, None)
                config['delays'].pop(code, None)
                self.store.db.execute('UPDATE pcr_config SET payload=? WHERE id=1', (encode(config),))
            self.store.audit(actor, 'pcr_code_deleted', {'before': previous})
            for row in self.store.db.execute('SELECT payload FROM pcr_samples').fetchall():
                sample = json.loads(row[0])
                if self.sync_issues(sample):
                    self.event(sample, actor, 'alerts_updated', {'deleted_code': code})
            return self.codes()

    def code(self, value, active=True):
        value = identifier(value)
        row = self.store.db.execute('SELECT * FROM pcr_codes WHERE code=?', (value,)).fetchone()
        if row is None:
            raise ValueError(f'Code de demande {value} inconnu : à paramétrer par un administrateur.')
        if active and not row['active']:
            raise ValueError(f'Le code de demande {value} est désactivé.')
        return dict(row)

    def revision(self):
        return self.store.db.execute('SELECT coalesce(max(seq),0) FROM pcr_events').fetchone()[0]

    def sample(self, sample_id):
        row = self.store.db.execute('SELECT payload FROM pcr_samples WHERE sample_id=?', (sample_id,)).fetchone()
        return {**empty_sample(sample_id), **json.loads(row[0])} if row else empty_sample(sample_id)

    def event(self, sample, actor, action, detail):
        # The caller owns the lock and transaction, including the chained audit.
        sample['revision'] += 1
        self.store.db.execute('INSERT OR REPLACE INTO pcr_samples VALUES(?,?)', (sample['sample_id'], encode(sample)))
        self.store.db.execute('INSERT INTO pcr_events(sample_id,at,actor,action,detail) VALUES(?,?,?,?,?)',
                              (sample['sample_id'], now(), actor, action, encode(detail)))
        self.store.audit(actor, 'pcr_' + action, {'sample_id': sample['sample_id'], **detail})

    def public(self, sample):
        active = [o for o in sample['orders'].values() if o['state'] == 'attendu']
        return {**sample, 'active_orders': active,
                'request_date': min((o['date_demande'] for o in active), default='')}

    def conditions(self, sample, config):
        result = pcr_numbering.display(sample, config)
        errors = []
        scanned = sample.get('last_scan') or sample.get('tube_last_scan') or any(
            o.get('receipt') or o.get('last_scan') for o in sample['orders'].values())
        if scanned:
            if not result['site']:
                errors.append('Laboratoire non reconnu')
            if not (any(r['active'] and sample['sample_id'].endswith(r['suffix']) for r in config['suffixes'])
                    or any(c['active'] and c['suffix'] and sample['sample_id'].endswith(c['suffix']) for c in self.codes())):
                errors.append('Suffixe inconnu')
            if not any(o['state'] in ('attendu', 'annule') for o in sample['orders'].values()):
                errors.append('Absent des demandes')
            for code, order in sample['orders'].items():
                configured = self.code(code, active=False)
                suffix = configured['suffix']
                if order['state'] == 'attendu' and suffix and not sample['sample_id'].endswith(suffix):
                    errors.append(f'Suffixe incompatible : {configured["label"]} attend {suffix}')
        conditions = [{'key': 'error:' + reason, 'kind': 'error', 'label': reason} for reason in errors]
        conditions += [{'key': 'cancelled:' + code, 'kind': 'cancelled',
                        'label': 'Demande annulée : ' + self.code(code, active=False)['label']}
                       for code, order in sample['orders'].items() if scanned and order['state'] == 'annule']
        return result, errors, bool(scanned), conditions

    def sync_issues(self, sample, new_scan=False, duplicate=False):
        """Track each occurrence, retaining handled and automatically ended alerts."""
        before = encode(sample.get('issues', []))
        issues = sample.setdefault('issues', [])
        _, _, _, conditions = self.conditions(sample, self.config())
        keys = {c['key'] for c in conditions}
        at = now()
        for issue in issues:
            if issue['active'] and issue['kind'] != 'duplicate' and (
                    issue['key'] not in keys or (new_scan and issue.get('handling'))):
                issue.update(active=False, ended_at=at,
                             ended_reason='Nouveau scan' if issue['key'] in keys else 'Demande ou paramétrage actualisé')
        active = {i['key'] for i in issues if i['active']}
        for condition in conditions:
            if condition['key'] not in active:
                issues.append({**condition, 'id': str(uuid4()), 'at': at, 'active': True, 'handling': None})
        if duplicate:
            issues.append({'id': str(uuid4()), 'key': 'duplicate', 'kind': 'duplicate',
                           'label': 'Code-barres scanné plusieurs fois', 'at': at,
                           'active': True, 'handling': None})
        return encode(issues) != before

    def display(self, sample, config):
        result, errors, scanned, _ = self.conditions(sample, config)
        issues = sample.get('issues', [])
        # Older versions stored cancellation alerts even for tubes never scanned.
        # Keep that history intact, but no physical action is due until a scan.
        pending = [i for i in issues if i['active'] and not i.get('handling')
                   and (i['kind'] != 'cancelled' or scanned)]
        pending_errors = [i for i in pending if i['kind'] in ('error', 'duplicate')]
        pending_cancelled = any(i['kind'] == 'cancelled' for i in pending)
        handlings = [i['handling'] for i in issues if i.get('handling')]
        last_handling = max(handlings, key=lambda h: (h.get('revision', 0), h['at'])) if handlings else None
        decisions = []
        for handling in handlings:
            decision = handling.get('disposition')
            if not decision and any(a in handling['actions'] for a in ('removed', 'two_tubes_removed')):
                # Old explicit removals are known; generic checks do not prove an outcome.
                decision = {'state': 'removed', 'label': 'Retiré du lot PCR',
                            'tube_count': 2 if 'two_tubes_removed' in handling['actions'] else 1}
            if decision:
                decisions.append({**decision, 'at': handling['at'], 'actor': handling['actor'],
                                  'revision': handling.get('revision', 0)})
        disposition = max(decisions, key=lambda d: (d['revision'], d['at'])) if decisions else None
        reactivated = scanned and any(o['state'] == 'attendu' and (
            o['reactivated_revision'] > (last_handling.get('revision', 0) if last_handling else 0)
            if o.get('reactivated_revision') else o.get('reactivated_at', '') >
            (last_handling['at'] if last_handling else '')) for o in sample['orders'].values())
        return {**result, 'site_resolved': result['site'],
                'site': ERROR_SITE if errors or pending_errors else result['site'],
                'error_reasons': errors, 'pending_reasons': list(dict.fromkeys(i['label'] for i in pending)),
                'pending_error': bool(pending_errors), 'pending_cancelled': pending_cancelled,
                'has_physical_scan': scanned,
                'pending_duplicate': any(i['kind'] == 'duplicate' for i in pending),
                'pending_issues': pending, 'last_handling': last_handling,
                'tube_disposition': disposition, 'reactivated_request': bool(reactivated),
                'has_cancelled_requests': scanned and any(o['state'] == 'annule' for o in sample['orders'].values()),
                'isolation_required': bool(pending_errors) or (scanned and pending_cancelled)}

    def pair(self, sample, request_code):
        order = sample['orders'][request_code]
        configured = self.code(request_code, active=False)
        config = self.config()
        group = next((r['group'] for r in config['suffixes']
                      if r['active'] and sample['sample_id'].endswith(r['suffix'])), configured['group_key'])
        if configured['suffix'] and sample['sample_id'].endswith(configured['suffix']):
            group = configured['group_key']
        status = ('received' if order.get('receipt') else 'expected') if order['state'] == 'attendu' else (
            'cancelled' if order['state'] == 'annule' else 'unknown')
        return {k: v for k, v in {**sample, **order, 'request_code': request_code,
            'label': configured['label'], 'code_active': bool(configured['active']), 'group': group,
            'patient_id': sample.get('patient_id', ''), 'patient': sample.get('patient', ''),
            **self.display(sample, config),
            'date_demande': self.mca_request_date(sample,request_code) or order.get('date_demande',''),
            'date_source': 'mca' if order.get('mca_at') else 'import',
            'heure_demande': self.mca_first_stamp(sample, request_code)[11:16],
            'requested_at': self.mca_first_stamp(sample, request_code) or (
                (self.mca_request_date(sample, request_code) or order.get('date_demande', '')) + 'T00:00:00'
                if (self.mca_request_date(sample, request_code) or order.get('date_demande')) else ''),
            'done_code': config['done_codes'].get(request_code, ''),
            'analysis_done': self.analysis_done(sample, request_code),
            'receipt_delay_hours': config['delays'].get(request_code, {}).get('receipt'),
            'result_delay_hours': config['delays'].get(request_code, {}).get('result'),
            **self.extras(sample, request_code),
            'status': status, 'receipt': order.get('receipt'), 'last_scan': order.get('last_scan')}.items() if k != 'orders'}

    def mca_request_date(self, sample, request_code):
        """First observed MCA transmission date, never the latest amendment date.

        Old orders are projected from retained events, without a startup migration
        or invented date when source history is absent.
        """
        order=sample['orders'].get(request_code,{})
        if order.get('date_remontee_mca'):
            return order['date_remontee_mca']
        if not order.get('mca_at'):
            return ''
        rows=self.store.db.execute("SELECT detail FROM pcr_events WHERE sample_id=? AND action='orders_received_mca' ORDER BY seq",(sample['sample_id'],))
        for row in rows:
            previous=json.loads(row['detail']).get('after',{}).get(request_code,{})
            if previous.get('mca_at'):
                return previous.get('date_remontee_mca') or previous['mca_at'][:10]
        return ''

    @staticmethod
    def extras(sample, request_code):
        """Comments for this line (tube-level ones included) and SIL/middleware birth date."""
        source = sample.get('mca_patient') or {}
        birth = source.get('birth_date_field', '')
        # Phadia P[5] may hold « NOM^PRÉNOM »; otherwise P[6] is taken as the first name.
        name, extra = source.get('name_field', ''), source.get('additional_name_field', '')
        last, _, first = name.partition('^') if '^' in name else (name, '', extra)
        if not source:
            last, first = sample.get('patient', ''), ''
        return {'comments': [c for c in sample.get('comments', [])
                             if c['request_code'] in ('', request_code) and not c.get('deleted')],
                'mails': [m for m in sample.get('mails', []) if m['request_code'] == request_code],
                'patient_last': last.replace('^', ' ').strip(), 'patient_first': first.replace('^', ' ').strip(),
                'birth_date': f'{birth[:4]}-{birth[4:6]}-{birth[6:8]}' if re.fullmatch(r'\d{8}', birth or '') else ''}

    def scan_history(self, sample_ids=None):
        """Every physical scan of each tube, oldest first, from retained events."""
        history = {}
        query = "SELECT sample_id,at,actor,action,detail FROM pcr_events WHERE action IN ('tube_scanned','received','scan_repeated')"
        args = ()
        if sample_ids is not None:
            ids = list(sample_ids)
            query += ' AND sample_id IN (%s)' % ','.join('?' * len(ids))
            args = tuple(ids)
        for row in self.store.db.execute(query + ' ORDER BY seq', args):
            history.setdefault(row['sample_id'], []).append({'at': row['at'], 'actor': row['actor']})
        return history

    def log_mail(self, data, actor):
        """Trace on each line that a relaunch mail draft was generated (append-only)."""
        to, pairs = data.get('to', ''), data.get('pairs')
        if not isinstance(to, str) or not pcr_numbering.EMAIL.fullmatch(to.strip()):
            raise ValueError('Adresse du laboratoire invalide.')
        if not isinstance(pairs, list) or not 1 <= len(pairs) <= 500 or not all(isinstance(p, dict) for p in pairs):
            raise ValueError('Indiquer de 1 à 500 échantillons.')
        # Kept apart from comments: only the blue mail marker is shown on the line.
        with self.store.lock, self.store.db:
            for item in pairs:
                sid = identifier(item.get('sample_id'))
                code = identifier(item.get('request_code'))
                sample = self.sample(sid)
                if code not in sample['orders']:
                    raise KeyError('Échantillon inconnu.')
                mail = {'at': now(), 'actor': actor, 'request_code': code, 'to': to.strip()}
                sample.setdefault('mails', []).append(mail)
                self.event(sample, actor, 'mail_generated', {'request_code': code, 'to': to.strip()})
        return {'logged': len(pairs)}

    def change_comment(self, data, actor):
        """The author edits, deletes or asks acknowledgement of the comment just written (latest of the line).

        Nothing is lost: earlier wording and deletions stay in the comment and the tube history.
        """
        sid = identifier(data.get('sample_id'))
        comment_id, action = data.get('comment_id'), data.get('action')
        if not isinstance(comment_id, str) or not re.fullmatch(r'[0-9a-f-]{36}', comment_id):
            raise ValueError('Commentaire inconnu.')
        if action not in ('edit', 'delete', 'to_ack'):
            raise ValueError('Action sur le commentaire inconnue.')
        with self.store.lock, self.store.db:
            sample = self.sample(sid)
            comment = next((c for c in sample.get('comments', []) if c['id'] == comment_id and not c.get('deleted')), None)
            if comment is None:
                raise KeyError('Commentaire inconnu ou supprimé.')
            if comment['actor'] != actor:
                raise PermissionError('Seul l’auteur peut modifier ce commentaire.')
            # Only the comment just written: once anyone has commented after it, it is final.
            comments = sample.get('comments', [])
            later = comments[comments.index(comment) + 1:]  # list order, not clock: same-instant comments stay ordered
            if any(not c.get('deleted')
                   and (comment['request_code'] == '' or c['request_code'] in ('', comment['request_code']))
                   for c in later):
                raise Conflict('Seul le dernier commentaire de la ligne peut être modifié.')
            stamp = {'at': now(), 'actor': actor}
            detail = {'request_code': comment['request_code'], 'comment_id': comment_id, 'action': action}
            if action == 'edit':
                text = data.get('text', '')
                if (not isinstance(text, str) or not 1 <= len(text.strip()) <= 1000
                        or any(ord(c) < 32 and c not in '\n\r' for c in text)):
                    raise ValueError('Commentaire de 1 à 1 000 caractères.')
                comment.setdefault('history', []).append({'text': comment['text'], **stamp})
                detail.update(before=comment['text'], after=text.strip())
                comment.update(text=text.strip(), edited=stamp)
            elif action == 'delete':
                comment['deleted'] = stamp
                detail['before'] = comment['text']
            else:
                if comment.get('to_ack'):
                    raise Conflict('Ce commentaire est déjà à acquitter.')
                comment.update(to_ack=True, ack=None)
            self.event(sample, actor, 'comment_changed', detail)
            return comment

    def ack_comment(self, data, actor):
        """Anyone allowed to write, author included, acknowledges a comment marked « à acquitter »."""
        sid = identifier(data.get('sample_id'))
        comment_id = data.get('comment_id')
        if not isinstance(comment_id, str) or not re.fullmatch(r'[0-9a-f-]{36}', comment_id):
            raise ValueError('Commentaire inconnu.')
        with self.store.lock, self.store.db:
            sample = self.sample(sid)
            comment = next((c for c in sample.get('comments', []) if c['id'] == comment_id and not c.get('deleted')), None)
            if comment is None or not comment.get('to_ack'):
                raise KeyError('Commentaire inconnu ou sans acquittement demandé.')
            if comment.get('ack'):
                raise Conflict(f'Déjà acquitté par {comment["ack"]["actor"]}.')
            comment['ack'] = {'at': now(), 'actor': actor}
            self.event(sample, actor, 'comment_acknowledged',
                       {'request_code': comment['request_code'], 'comment_id': comment_id})
            return comment

    def add_comment(self, data, actor, kind='note'):
        sid = identifier(data.get('sample_id'))
        request_code = data.get('request_code') or ''
        if request_code:
            request_code = identifier(request_code)
        text = data.get('text', '')
        if (not isinstance(text, str) or not 1 <= len(text.strip()) <= 1000
                or any(ord(c) < 32 and c not in '\n\r' for c in text)):
            raise ValueError('Commentaire de 1 à 1 000 caractères.')
        with self.store.lock, self.store.db:
            sample = self.sample(sid)
            if not sample['revision'] or (request_code and request_code not in sample['orders']):
                raise KeyError('Échantillon inconnu.')
            # Append-only: earlier comments are never edited or removed.
            comment = {'id': str(uuid4()), 'at': now(), 'actor': actor,
                       'request_code': request_code, 'text': text.strip(), 'kind': kind}
            if data.get('to_ack') is True:
                # To be read and acknowledged by a colleague; the acknowledgement is added later, never removed.
                comment.update(to_ack=True, ack=None)
            sample.setdefault('comments', []).append(comment)
            self.event(sample, actor, 'comment_added', {'request_code': request_code, 'comment': comment})
            return self.extras(sample, request_code)['comments']

    @staticmethod
    def analysis_done(sample, request_code):
        """SIL/middleware announcement that this analysis was performed; None while absent or withdrawn."""
        record = sample.get('done', {}).get(request_code)
        return {'at': record['at'], 'code': record['code']} if record and record['state'] == 'fait' else None

    def mca_first_stamp(self, sample, request_code):
        """Timestamp of the first MCA message for this pair, never a later amendment.

        Empty for manual imports: no time is invented. Orders received before
        ``mca_first_at`` existed are read once from retained events.
        """
        order = sample['orders'].get(request_code, {})
        if order.get('mca_first_at'):
            return order['mca_first_at']
        if not order.get('mca_at'):
            return ''
        key = (sample['sample_id'], request_code)
        if key not in self._first_mca:
            first = ''
            rows = self.store.db.execute("SELECT detail FROM pcr_events WHERE sample_id=? AND action='orders_received_mca' ORDER BY seq", (sample['sample_id'],))
            for row in rows:
                previous = json.loads(row['detail']).get('after', {}).get(request_code, {})
                if previous.get('mca_at'):
                    first = previous['mca_at']
                    break
            self._first_mca[key] = first or order['mca_at']
        return self._first_mca[key]

    def unassigned_pair(self, sample):
        """A physical tube seen before its orders: never invent a request code."""
        config = self.config()
        group = next((r['group'] for r in config['suffixes']
                      if r['active'] and sample['sample_id'].endswith(r['suffix'])), 'other')
        group = next((c['group_key'] for c in self.codes()
                      if c['active'] and c['suffix'] and sample['sample_id'].endswith(c['suffix'])), group)
        return {k: v for k, v in {**sample, 'request_code': '', 'label': 'Demande non transmise',
            'state': 'unmatched', 'status': 'unknown', 'group': group, 'code_active': False,
            'patient': sample.get('patient', ''), 'patient_id': sample.get('patient_id', ''),
            **self.display(sample, config), **self.extras(sample, ''),
            'date_demande': '', 'receipt': sample.get('tube_receipt'),
            'last_scan': sample.get('tube_last_scan'), 'scanned_at': sample.get('tube_last_scan')}.items() if k != 'orders'}

    def listing(self):
        from .pcr_mca import receiver_for
        with self.store.lock:
            samples = [json.loads(row[0]) for row in self.store.db.execute('SELECT payload FROM pcr_samples')]
            pairs = [self.pair(s, code) for s in samples for code in s['orders']]
            scans = {}
            for event in self.store.db.execute("SELECT sample_id,at,actor,detail FROM pcr_events WHERE action IN ('received','scan_repeated') ORDER BY seq"):
                code = json.loads(event['detail'])['request_code']
                scans[(event['sample_id'], code)] = {'at': event['at'], 'actor': event['actor']}
            for item in pairs:
                stamps = [s for s in (scans.get((item['sample_id'], item['request_code'])), item.get('last_scan')) if s]
                item['scanned_at'] = max(stamps, key=lambda s: s['at']) if stamps else None
            pairs.extend(self.unassigned_pair(s) for s in samples if not s['orders'] and s.get('tube_receipt'))
            history = self.scan_history()
            for item in pairs:
                item['scans'] = history.get(item['sample_id'], [])
            imports = self.store.db.execute('SELECT at,actor,source,summary FROM pcr_imports ORDER BY rowid DESC LIMIT 1').fetchone()
            source = receiver_for(self.store).status()
            return {'pairs': sorted(pairs, key=lambda s: (s['date_demande'] or '9999', s['dossier'], s['sample_id'], s['request_code'])),
                    'codes': self.codes(), 'config': self.config(),
                    'revision': self.revision(), 'groups': GROUPS,
                    'source': {**source,
                               'last_import': dict(imports) if imports else None}, 'at': now()}

    def preview(self, data):
        rows = csv_rows(data.get('content'))
        source = data.get('source', '')
        if not isinstance(source, str) or not source.strip() or len(source) > 160 or any(ord(c) < 32 for c in source):
            raise ValueError('Indiquer le nom ou la provenance de la liste (160 caractères maximum).')
        digest = hashlib.sha256(data['content'].encode('utf-8')).hexdigest()
        with self.store.lock:
            duplicate = bool(self.store.db.execute('SELECT 1 FROM pcr_imports WHERE digest=?', (digest,)).fetchone())
            staged, changed = {}, []
            for row in rows:
                self.code(row['analyse'], active=row['etat'] == 'attendu')
                sid = row['echantillon']
                sample = staged.setdefault(sid, self.sample(sid))
                if sample['dossier'] and row['dossier'] and sample['dossier'] != row['dossier']:
                    raise Conflict(f'Échantillon {sid} : déjà rattaché à un autre dossier. Import refusé.')
                if sample['patient_id'] and row['patient_id'] and sample['patient_id'] != row['patient_id']:
                    raise Conflict(f'Échantillon {sid} : déjà rattaché à un autre patient. Import refusé.')
                for key in ('dossier', 'patient_id', 'patient', 'prelevement', 'date_prelevement', 'site'):
                    if row[key]:
                        sample[key] = row[key]
                old_order = sample['orders'].get(row['analyse'], {})
                # A prior physical scan receives a newly arriving order on this
                # exact tube. An explicitly corrected receipt stays corrected.
                receipt = old_order.get('receipt') or (None if old_order.get('receipt_voided') else sample.get('tube_receipt'))
                sample['orders'][row['analyse']] = {**old_order, 'code': row['analyse'], 'source_label': row['libelle'],
                    'date_demande': row['date_demande'], 'state': row['etat'],
                    'receipt': receipt, 'last_scan': old_order.get('last_scan') or sample.get('tube_last_scan'),
                    'receipt_voided': old_order.get('receipt_voided', False),
                    'reactivated_at': now() if old_order.get('state') == 'annule' and row['etat'] == 'attendu'
                                      else old_order.get('reactivated_at', ''),
                    'reactivated_revision': sample['revision'] + 1 if old_order.get('state') == 'annule' and row['etat'] == 'attendu'
                                            else old_order.get('reactivated_revision', 0)}
            for sid, sample in staged.items():
                if sample != self.sample(sid):
                    changed.append(sample)
            return {'digest': digest, 'revision': self.revision(), 'catalog_digest': self.catalog_digest(), 'duplicate': duplicate,
                    'source': source.strip(), 'line_count': len(rows), 'sample_count': len(staged),
                    'changed_count': 0 if duplicate else len(changed),
                    'cancelled_orders': sum(r['etat'] == 'annule' for r in rows),
                    'pairs': [self.pair(staged[r['echantillon']], r['analyse']) for r in rows], '_changed': changed}

    def import_list(self, data, actor):
        if data.get('confirmed') is not True:
            raise ValueError('Vérifier l’aperçu puis confirmer l’import.')
        with self.store.lock, self.store.db:
            preview = self.preview(data)
            if preview['digest'] != data.get('digest'):
                raise Conflict('Le fichier a changé. Refaire l’aperçu.')
            if preview['duplicate']:
                return {'duplicate': True, 'changed_count': 0}
            if preview['catalog_digest'] != data.get('catalog_digest'):
                raise Conflict('Le paramétrage des codes a changé. Refaire l’aperçu.')
            if type(data.get('revision')) is not int or preview['revision'] != data['revision']:
                raise Conflict('Le registre a changé depuis l’aperçu. Refaire l’aperçu avant d’importer.')
            for sample in preview['_changed']:
                previous = self.sample(sample['sample_id'])
                self.sync_issues(sample)
                self.event(sample, actor, 'orders_imported', {'source': preview['source'], 'digest': preview['digest'],
                    'before': {k: previous[k] for k in ('dossier', 'patient_id', 'patient', 'orders', 'prelevement', 'date_prelevement', 'site')},
                    'after': {k: sample[k] for k in ('dossier', 'patient_id', 'patient', 'orders', 'prelevement', 'date_prelevement', 'site')}})
            summary = {k: preview[k] for k in ('line_count', 'sample_count', 'changed_count', 'cancelled_orders')}
            self.store.db.execute('INSERT INTO pcr_imports VALUES(?,?,?,?,?)',
                (preview['digest'], now(), actor, preview['source'], encode(summary)))
            self.store.audit(actor, 'pcr_list_imported', {'digest': preview['digest'], **summary})
            return summary

    def scan(self, data, actor):
        if 'request_code' not in data:
            return self.scan_tube(data, actor)
        sid = identifier(data.get('sample_id'))
        request_code = identifier(data.get('request_code'))
        rid = data.get('request_id')
        if not isinstance(rid, str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,80}', rid):
            raise ValueError('Identifiant de scan invalide. Actualiser la page.')
        with self.store.lock, self.store.db:
            previous = self.store.db.execute('SELECT * FROM pcr_scan_requests WHERE request_id=?', (rid,)).fetchone()
            if previous:
                saved = json.loads(previous['response'])
                if previous['sample_id'] != sid or previous['actor'] != actor or saved.get('kind') == 'tube' or saved['pair']['request_code'] != request_code:
                    raise Conflict('Cet identifiant de scan a déjà été utilisé.')
                current = self.sample(sid)
                if current['revision'] != saved['pair']['revision']:
                    return {'pair': self.pair(current, request_code), 'duplicate': True, 'replayed': True}
                return saved
            self.code(request_code)
            sample = self.sample(sid)
            repeated_scan = bool(sample.get('last_scan'))
            order = sample['orders'].setdefault(request_code, {'code': request_code, 'date_demande': '',
                'state': 'unmatched', 'source_label': '', 'receipt': None, 'last_scan': None})
            duplicate = order['receipt'] is not None
            stamp = {'at': now(), 'actor': actor}
            if not duplicate:
                order['receipt'] = stamp
            order['receipt_voided'] = False
            order['last_scan'] = sample['last_scan'] = stamp
            self.sync_issues(sample, new_scan=True, duplicate=repeated_scan)
            self.event(sample, actor, 'scan_repeated' if duplicate else 'received', {'duplicate': duplicate, 'request_code': request_code})
            response = {'pair': self.pair(sample, request_code), 'duplicate': duplicate}
            self.store.db.execute('INSERT INTO pcr_scan_requests VALUES(?,?,?,?)', (rid, sid, actor, encode(response)))
            return response

    def tube_response(self, sample, duplicate, replayed=False):
        pairs = [self.pair(sample, code) for code in sample['orders']]
        scans = self.scan_history([sample['sample_id']]).get(sample['sample_id'], [])
        for item in pairs:
            item['scans'] = scans
        return {'kind': 'tube', 'sample_id': sample['sample_id'], 'revision': sample['revision'], 'scans': scans,
                'pairs': pairs or [self.unassigned_pair(sample)],
                'matched_count': sum(p['state'] == 'attendu' for p in pairs),
                'cancelled_count': sum(p['state'] == 'annule' for p in pairs),
                'duplicate': duplicate, 'replayed': replayed}

    def scan_tube(self, data, actor):
        sid, rid = identifier(data.get('sample_id')), data.get('request_id')
        if not isinstance(rid, str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,80}', rid):
            raise ValueError('Identifiant de scan invalide. Actualiser la page.')
        with self.store.lock, self.store.db:
            previous = self.store.db.execute('SELECT * FROM pcr_scan_requests WHERE request_id=?', (rid,)).fetchone()
            sample = self.sample(sid)
            if previous:
                saved = json.loads(previous['response'])
                if previous['sample_id'] != sid or previous['actor'] != actor or saved.get('kind') != 'tube':
                    raise Conflict('Cet identifiant de scan a déjà été utilisé.')
                return self.tube_response(sample, True, True)
            stamp = {'at': now(), 'actor': actor}
            duplicate = bool(sample.get('last_scan') or sample.get('tube_last_scan'))
            sample['tube_receipt'] = sample.get('tube_receipt') or stamp
            sample['tube_last_scan'] = sample['last_scan'] = stamp
            for order in sample['orders'].values():
                order['receipt'] = order.get('receipt') or stamp
                order['last_scan'] = stamp
                order['receipt_voided'] = False
            self.sync_issues(sample, new_scan=True, duplicate=duplicate)
            self.event(sample, actor, 'tube_scanned', {'request_codes': sorted(sample['orders']), 'duplicate': duplicate})
            response = self.tube_response(sample, duplicate)
            self.store.db.execute('INSERT INTO pcr_scan_requests VALUES(?,?,?,?)', (rid, sid, actor, encode(response)))
            return response

    def cancel_receipt(self, data, actor):
        sid = identifier(data.get('sample_id'))
        request_code = identifier(data.get('request_code'))
        reason = data.get('reason', '')
        if data.get('confirmed') is not True or not isinstance(reason, str) or not 5 <= len(reason.strip()) <= 1000:
            raise ValueError('Confirmer l’annulation avec un motif de 5 à 1 000 caractères.')
        with self.store.lock, self.store.db:
            sample = self.sample(sid)
            if type(data.get('revision')) is not int or sample['revision'] != data['revision']:
                raise Conflict('Cet échantillon a changé. Actualiser son historique.')
            order = sample['orders'].get(request_code)
            if not order or not order.get('receipt'):
                raise Conflict('Cet échantillon n’a pas de réception active.')
            before = order['receipt']
            order['receipt'] = None
            order['receipt_voided'] = True
            self.event(sample, actor, 'receipt_cancelled', {'reason': reason.strip(), 'before': before, 'request_code': request_code})
            return self.pair(sample, request_code)

    def handle(self, data, actor):
        sid = identifier(data.get('sample_id'))
        note = data.get('note', '')
        if data.get('confirmed') is not True:
            raise ValueError('Confirmer que l’action a été effectuée physiquement.')
        if not isinstance(note, str) or len(note.strip()) > 1000 or any(ord(c) < 32 and c not in '\n\r' for c in note):
            raise ValueError('Commentaire invalide (1 000 caractères maximum).')
        with self.store.lock, self.store.db:
            sample = self.sample(sid)
            if type(data.get('revision')) is not int or data['revision'] != sample['revision']:
                raise Conflict('Ce tube a changé. Rouvrir son suivi avant de confirmer.')
            pending = self.display(sample, self.config())['pending_issues']
            if not pending:
                raise Conflict('Ce tube n’a plus d’action en attente. Actualiser son suivi.')
            ordinary = any(i['kind'] != 'duplicate' for i in pending)
            duplicate = any(i['kind'] == 'duplicate' for i in pending)
            action, duplicate_action = data.get('action'), data.get('duplicate_action')
            if ordinary and action not in ('verified', 'managed') + REMOVE_ACTIONS + KEEP_ACTIONS:
                raise ValueError('Indiquer si le tube a été vérifié ou retiré du lot.')
            if action in KEEP_ACTIONS and any(i['kind'] == 'error' for i in pending):
                raise ValueError('Tube en erreur : il doit être retiré du lot.')
            if duplicate and duplicate_action not in DUPLICATE_ACTIONS:
                raise ValueError('Préciser si le même tube a été rescanné ou si deux tubes portent ce code-barres.')
            if duplicate_action == 'two_tubes_removed' and ordinary:
                action = 'removed'
            actions = ([action] if ordinary else []) + ([duplicate_action] if duplicate else [])
            handling = {'at': now(), 'actor': actor, 'actions': actions, 'revision': sample['revision'] + 1,
                        'label': ' · '.join(HANDLING_LABELS[a] for a in actions), 'note': note.strip(),
                        'issue_ids': [i['id'] for i in pending]}
            removed = (ordinary and action in REMOVE_ACTIONS) or (duplicate and duplicate_action == 'two_tubes_removed')
            # A cancellation is either real (removed) or a mistake still on the prescription (kept);
            # an error always requires removal; a regularized duplicate alone keeps the tube.
            kept = not removed and ((ordinary and action in KEEP_ACTIONS) or
                                    (duplicate and duplicate_action in ('same_tube', 'two_tubes_regularized')))
            if removed or kept:
                handling['disposition'] = {'state': 'removed' if removed else 'kept',
                    'label': 'Retiré du lot PCR' if removed else 'À faire en PCR',
                    'tube_count': 2 if duplicate and duplicate_action == 'two_tubes_removed' else 1}
            for issue in pending:
                issue['handling'] = handling
            self.event(sample, actor, 'tube_handled', handling)
            return self.tube_response(sample, False)

    def history(self, sid, request_code=''):
        sid = identifier(sid)
        if request_code:
            request_code = identifier(request_code)
        with self.store.lock:
            sample = self.sample(sid)
            if not sample['revision'] or (request_code and request_code not in sample['orders']):
                raise KeyError('Échantillon inconnu.')
            events = [{**dict(row), 'detail': json.loads(row['detail'])} for row in self.store.db.execute(
                'SELECT * FROM pcr_events WHERE sample_id=? ORDER BY seq DESC', (sid,))]
            if request_code:
                events = [e for e in events if e['detail'].get('request_code', request_code) == request_code]
            pairs = [self.pair(sample, code) for code in sample['orders']] or [self.unassigned_pair(sample)]
            return {'pair': self.pair(sample, request_code) if request_code else pairs[0],
                    'pairs': pairs, 'events': events}

    def template(self):
        return ('\ufeff' + ';'.join(COLUMNS + PATIENT_COLUMNS) + '\r\n').encode('utf-8')
