# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
"""SQLite persistence, authenticated sessions, transactional audit and revision checks."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from .domain import digest, now, uid
from . import accounts


class Conflict(ValueError):
    pass


def encode(v):
    return json.dumps(v, ensure_ascii=False, separators=(',', ':'))


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 310_000).hex()


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.documents = self.root / 'documents'
        self.documents.mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.root / 'chlas.sqlite3', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.analysis_lock = threading.Lock()
        self.analysis_progress = {}
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, salt TEXT NOT NULL, password TEXT NOT NULL, role TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), csrf TEXT NOT NULL, created REAL NOT NULL, touched REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT NOT NULL, batch_id TEXT, action TEXT NOT NULL, detail TEXT NOT NULL, previous_hash TEXT NOT NULL, hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')
        accounts.initialize(self)
        self.db.commit()
        self.setup_token = None
        if not self.db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            token_file = self.root / 'premiere-ouverture.txt'
            self.setup_token = token_file.read_text().strip() if token_file.exists() else secrets.token_urlsafe(32)
            token_file.write_text(self.setup_token, encoding='utf-8')

    def audit(self, actor, action, detail, batch_id=None):
        previous = self.db.execute('SELECT hash FROM audit ORDER BY seq DESC LIMIT 1').fetchone()
        previous_hash = previous[0] if previous else '0' * 64
        at = now()
        value = {'at': at, 'actor': actor, 'batch_id': batch_id, 'action': action, 'detail': detail, 'previous_hash': previous_hash}
        self.db.execute('INSERT INTO audit(at,actor,batch_id,action,detail,previous_hash,hash) VALUES(?,?,?,?,?,?,?)',
                        (at, actor, batch_id, action, encode(detail), previous_hash, digest(value)))

    def create_user(self, username, password, role, actor, apps=None, default_app=None, first_name='', last_name=''):
        if not isinstance(username, str) or not 2 <= len(username.strip()) <= 80:
            raise ValueError('Identifiant : 2 à 80 caractères.')
        if not isinstance(password, str) or not 2 <= len(password) <= 200:
            raise ValueError('Mot de passe : 2 à 200 caractères.')
        apps, default_app = accounts.validate(role, apps, default_app)
        names = accounts.clean_names(first_name, last_name)
        with self.lock, self.db:
            salt = secrets.token_hex(16)
            user_id = uid()
            try:
                self.db.execute('INSERT INTO users VALUES(?,?,?,?,?)', (user_id, username.strip(), salt, password_hash(password, salt), role))
            except sqlite3.IntegrityError as exc:
                raise ValueError('Cet identifiant existe déjà.') from exc
            self.db.execute('INSERT INTO user_access VALUES(?,?,?,1,1)', (user_id, encode(apps), default_app))
            if any(names.values()):
                accounts.save_names(self, user_id, names)
            self.audit(actor, 'user_created', {'username': username.strip(), 'role': role, 'apps': apps, **names})

    def setup(self, token, username, password):
        with self.lock:
            if not self.setup_token or not hmac.compare_digest(str(token), self.setup_token):
                raise PermissionError('Clé de première ouverture invalide.')
            self.create_user(username, password, 'admin', 'installation')
            self.setup_token = None
            (self.root / 'premiere-ouverture.txt').unlink(missing_ok=True)

    def login(self, username, password):
        with self.lock, self.db:
            row = self.db.execute('SELECT * FROM users WHERE username=?', (str(username),)).fetchone()
            salt = row['salt'] if row else '0' * 32
            candidate = password_hash(str(password), salt)
            if not row or not hmac.compare_digest(candidate, row['password']) or not accounts.profile(self, row)['active']:
                raise PermissionError('Identifiant ou mot de passe incorrect.')
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            self.db.execute('DELETE FROM sessions WHERE created<? OR touched<?', (time.time()-28800, time.time()-1800))
            self.db.execute('INSERT INTO sessions VALUES(?,?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), row['id'], csrf, time.time(), time.time()))
            self.audit(row['username'], 'login', {})
            return token, accounts.profile(self, row, csrf)

    def session(self, token):
        if not token:
            return None
        with self.lock, self.db:
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            row = self.db.execute('SELECT u.id,u.username,u.role,s.csrf,s.created,s.touched FROM sessions s JOIN users u ON u.id=s.user_id WHERE token_hash=?', (token_hash,)).fetchone()
            if not row or time.time() - row['created'] > 28800 or time.time() - row['touched'] > 1800:
                return None
            result = accounts.profile(self, row, row['csrf'])
            if not result['active']:
                return None
            self.db.execute('UPDATE sessions SET touched=? WHERE token_hash=?', (time.time(), token_hash))
            return result

    def logout(self, token):
        with self.lock, self.db:
            self.db.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))

    def verify_audit(self):
        previous = '0' * 64
        with self.lock:
            for r in self.db.execute('SELECT * FROM audit ORDER BY seq'):
                value = {k: r[k] for k in ('at', 'actor', 'batch_id', 'action', 'previous_hash')}
                value['detail'] = json.loads(r['detail'])
                if r['previous_hash'] != previous or r['hash'] != digest(value):
                    return False
                previous = r['hash']
        return True

    def settings(self):
        with self.lock:
            values = {r[0]: json.loads(r[1]) for r in self.db.execute('SELECT key,value FROM settings')}
        return {**values}
