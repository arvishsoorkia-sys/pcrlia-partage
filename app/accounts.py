# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
"""Account profile: names, active flag and the PCRLia assignment."""
import json


APPS = ('pcrlia',)


def clean_names(first_name='', last_name=''):
    result = {}
    for key, value in (('first_name', first_name), ('last_name', last_name)):
        if not isinstance(value, str) or len(value) > 80 or any(ord(c) < 32 for c in value):
            raise ValueError('Nom et prénom : 80 caractères maximum, sur une seule ligne.')
        result[key] = value.strip()
    return result


def user_names(store, user_id):
    row = store.db.execute('SELECT value FROM settings WHERE key=?', ('account-name:' + user_id,)).fetchone()
    return json.loads(row[0]) if row else {'first_name': '', 'last_name': ''}


def save_names(store, user_id, names):
    # Additive metadata; existing credentials and attribution identifiers stay intact.
    store.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',
                     ('account-name:' + user_id, json.dumps(names, ensure_ascii=False)))


def defaults(role):
    # Accounts without a saved assignment get PCRLia. A role alone must
    # never grant access to another application, including for administrators.
    return ['pcrlia']


def validate(role, apps=None, default_app=None):
    if role not in ('admin', 'technician', 'viewer'):
        raise ValueError('Rôle invalide.')
    apps = defaults(role) if apps is None else apps
    if not isinstance(apps, list) or not apps or any(a not in APPS for a in apps):
        raise ValueError('Choisir l’affectation PCRLia.')
    apps = [a for a in APPS if a in apps]
    default_app = default_app or apps[0]
    if default_app not in apps:
        raise ValueError('L’espace de démarrage doit appartenir aux affectations du compte.')
    return apps, default_app


def initialize(store):
    store.db.execute('''CREATE TABLE IF NOT EXISTS user_access(
        user_id TEXT PRIMARY KEY REFERENCES users(id), apps TEXT NOT NULL,
        default_app TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
        revision INTEGER NOT NULL DEFAULT 1)''')
    for row in store.db.execute('SELECT id,role FROM users').fetchall():
        store.db.execute('INSERT OR IGNORE INTO user_access VALUES(?,?,?,1,1)',
                         (row['id'], json.dumps(defaults(row['role'])), 'pcrlia'))


def profile(store, row, csrf=None):
    access = store.db.execute('SELECT * FROM user_access WHERE user_id=?', (row['id'],)).fetchone()
    names = user_names(store, row['id'])
    value = {'id': row['id'], 'username': row['username'], 'role': row['role'], **names,
             'display_name': ' '.join(v for v in (names['first_name'], names['last_name']) if v) or row['username'],
             'apps': json.loads(access['apps']) if access else defaults(row['role']),
             'default_app': access['default_app'] if access else 'pcrlia',
             'active': bool(access['active']) if access else True,
             'revision': access['revision'] if access else 0}
    if csrf is not None:
        value['csrf'] = csrf
    return value


def listing(store):
    with store.lock:
        return [profile(store, row) for row in store.db.execute('SELECT id,username,role FROM users ORDER BY username').fetchall()]


def update(store, data, actor):
    from .storage import Conflict
    if type(data.get('active')) is not bool or type(data.get('revision')) is not int:
        raise ValueError('État du compte ou version invalide.')
    with store.lock, store.db:
        row = store.db.execute('SELECT id,username,role FROM users WHERE id=?', (data.get('id'),)).fetchone()
        if row is None:
            raise KeyError('Compte inconnu.')
        before = profile(store, row)
        names = clean_names(data.get('first_name', before['first_name']), data.get('last_name', before['last_name']))
        apps, default_app = validate(data.get('role'), data.get('apps', before['apps']),
                                     data.get('default_app', before['default_app']))
        if data['revision'] != before['revision']:
            raise Conflict('Ce compte a été modifié. Actualiser la liste des utilisateurs.')
        if before['role'] == 'admin' and before['active'] and (data['role'] != 'admin' or not data['active']):
            if sum(u['active'] and u['role'] == 'admin' for u in listing(store)) <= 1:
                raise ValueError('Conserver au moins un administrateur actif.')
        store.db.execute('UPDATE users SET role=? WHERE id=?', (data['role'], row['id']))
        store.db.execute('INSERT OR REPLACE INTO user_access VALUES(?,?,?,?,?)',
                         (row['id'], json.dumps(apps), default_app, int(data['active']), before['revision'] + 1))
        if names != {k: before[k] for k in names}:
            save_names(store, row['id'], names)
        store.db.execute('DELETE FROM sessions WHERE user_id=?', (row['id'],))
        store.audit(actor, 'user_access_changed', {'before': before, 'after': {
            'role': data['role'], 'apps': apps, 'default_app': default_app, 'active': data['active'], **names}})
