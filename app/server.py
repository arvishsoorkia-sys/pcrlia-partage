# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
"""Local MVP server with an optional, explicitly bound HTTPS listener for VPN trials."""
from __future__ import annotations

import argparse
import base64
import hmac
import ipaddress
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import threading
import time
import urllib.parse
import webbrowser

from .storage import Conflict, Store, encode
from . import accounts
from .pcr import PCRService


def installed_version():
    """Version written by the update tool beside app/ and web/; empty in a source checkout."""
    try:
        value = json.loads((Path(__file__).resolve().parents[1]/'release.json').read_text(encoding='utf-8'))['version']
        return value if isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}', value) else ''
    except (OSError, ValueError, KeyError):
        return ''

STATIC = Path(__file__).resolve().parent.parent / 'web'
# Files served from web/. Add here any logo referenced in web/laboratoire.js.
STATIC_FILES = ('/', '/app.js', '/laboratoire.js', '/storage.js', '/workspaces.js', '/pcr.js', '/pcr.css',
                '/style.css', '/brand.svg')


def start_background(store):
    """MCA order receiver (when enabled) and scheduled NAS backup."""
    from .pcr_mca import receiver_for
    from .nas_storage import archive_for
    receiver_for(store).start()
    archive_for(store).tick()


def guide_folder():
    """<installation>/Guide-technicien on the host; PCRLIA_GUIDE_DIR for a source checkout (preview)."""
    installation = Path(__file__).resolve().parents[2]
    if (installation / 'installation.json').is_file():
        return installation / 'Guide-technicien'
    return Path(os.environ.get('PCRLIA_GUIDE_DIR') or (Path(__file__).resolve().parents[1] / 'test-output' / 'guide'))


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, store, tls_context=None, public_names=()):
        self.store, self.setup_attempts = store, {}
        self.tls_context = tls_context
        self.secure = tls_context is not None
        self.public_names = tuple(public_names)
        if self.public_names and not self.secure:
            raise ValueError('Un nom réseau exige HTTPS.')
        for name in self.public_names:
            if (not isinstance(name, str) or len(name) > 253 or name != name.lower()
                    or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                               for label in name.split('.'))):
                raise ValueError('Nom réseau invalide ; indiquer un nom exact, sans URL ni caractère générique.')
        self.setup_attempts_lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(32)
        if not ipaddress.ip_address(address[0]).is_loopback and not self.secure:
            raise ValueError('Un accès réseau exige HTTPS ; HTTP reste limité à ce PC.')
        self.pcr = PCRService(store)
        super().__init__(address, Handler)
        with store.lock:
            store.identity_server_count = getattr(store, 'identity_server_count', 0) + 1

    def service_actions(self):
        # Older installed hosts only run serve_forever: keep the enabled MCA
        # links alive without requiring someone to open the application.
        # The updater also serves a disposable database during its probe. Only
        # the real installed data folder may start the connections, and only
        # once its update transaction has finished. The local server owns this
        # work; a second HTTPS server must not run the same maintenance loop.
        if self.secure or not ipaddress.ip_address(self.server_address[0]).is_loopback:
            return
        installation = Path(__file__).resolve().parents[2]
        if (Path(__file__).resolve().parents[1] != installation / 'application'
                or self.store.root != installation / 'data'
                or not (installation / 'installation.json').is_file()
                or (installation / 'update-transaction.json').exists()):
            return
        start_background(self.store)

    def server_close(self):
        super().server_close()
        with self.store.lock:
            self.store.identity_server_count = max(0, getattr(self.store, 'identity_server_count', 1) - 1)
            last = self.store.identity_server_count == 0
        if last and hasattr(self.store, 'pcr_receiver'):
            self.store.pcr_receiver.stop()
        if last and hasattr(self.store, 'nas_archive'):
            self.store.nas_archive.stop()

    def get_request(self):
        conn, address = super().get_request()
        conn.settimeout(15)
        if self.secure:
            # The handshake happens in the bounded request thread, not the accept loop.
            conn = self.tls_context.wrap_socket(conn, server_side=True, do_handshake_on_connect=False)
        return conn, address

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        pass  # No request content or patient data in unprotected console logs.


class Handler(BaseHTTPRequestHandler):
    server_version = 'ChlasLocal/0.1'
    sys_version = ''

    def log_message(self, *_args):
        pass  # No URLs, identities, passwords or source text in console/access logs.

    def headers_common(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'self'")

    def send(self, status, value, mime='application/json; charset=utf-8', extra=None):
        content = encode(value).encode() if not isinstance(value, bytes) else value
        self.send_response(status)
        self.headers_common()
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(content)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(content)

    def body(self):
        n = int(self.headers.get('Content-Length', '0'))
        if n <= 0 or n > 36_000_000:
            raise ValueError('Taille de requête invalide (maximum 36 Mo).')
        if not self.headers.get('Content-Type', '').startswith('application/json'):
            raise ValueError('Requête JSON requise.')
        value = json.loads(self.rfile.read(n))
        if not isinstance(value, dict):
            raise ValueError('Objet JSON requis.')
        return value

    def cookie(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
            return cookie['chlas_session'].value if 'chlas_session' in cookie else ''
        except Exception:
            return ''

    def user(self, write=False, admin=False):
        u = self.server.store.session(self.cookie())
        if not u:
            raise PermissionError('Connexion requise.')
        if write and not hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), u['csrf']):
            raise PermissionError('Session ou jeton de formulaire invalide ; reconnectez-vous.')
        if (write and u['role'] == 'viewer') or (admin and u['role'] != 'admin'):
            raise PermissionError('Droits insuffisants.')
        return u

    def origin_check(self):
        address = self.server.server_address[0]
        allowed = {f'{address}:{self.server.server_port}'}
        allowed.update(f'{name}:{self.server.server_port}' for name in self.server.public_names)
        if ipaddress.ip_address(address).is_loopback:
            allowed.add(f'localhost:{self.server.server_port}')
        if self.headers.get('Host') not in allowed:
            raise PermissionError('Hôte non autorisé.')
        origin = self.headers.get('Origin')
        scheme = 'https://' if self.server.secure else 'http://'
        if origin and origin not in {scheme + h for h in allowed}:
            raise PermissionError('Origine non autorisée.')
        public_entry = (self.command == 'GET' and urllib.parse.urlsplit(self.path).path == '/'
                        and self.headers.get('Sec-Fetch-Mode') == 'navigate'
                        and self.headers.get('Sec-Fetch-Dest') == 'document')
        if self.headers.get('Sec-Fetch-Site') == 'cross-site' and not public_entry:
            raise PermissionError('Requête intersite refusée.')

    def send_guide(self):
        """Technician guide, deposited apart from the program in <installation>/Guide-technicien."""
        def page(status, message):
            body = ('<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Guide du technicien</title></head>'
                    '<body style="font:18px Arial,sans-serif;margin:48px;color:#14263A">'
                    f'<h1 style="font-size:24px">Guide du technicien</h1><p>{message}</p></body></html>')
            return self.send(status, body.encode('utf-8'), 'text/html; charset=utf-8')
        if not self.server.store.session(self.cookie()):
            return page(401, 'Se connecter à PCRLia, puis rouvrir le guide depuis le menu.')
        file = guide_folder() / 'Guide-technicien.html'
        try:
            if file.is_file() and file.stat().st_size <= 60_000_000:
                return self.send(200, file.read_bytes(), 'text/html; charset=utf-8')
        except OSError:
            pass
        return page(404, 'Le guide n’a pas encore été déposé sur le PC hébergeur (dossier Guide-technicien).')

    def do_GET(self):
        self.dispatch(False)

    def do_POST(self):
        self.dispatch(True)

    def dispatch(self, post):
        try:
            self.connection.settimeout(120)
            self.origin_check()
            path = urllib.parse.urlsplit(self.path).path
            if not post and path in STATIC_FILES:
                file = STATIC / ('index.html' if path == '/' else path[1:])
                mime = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png'}[file.suffix]
                return self.send(200, file.read_bytes(), mime)
            if not post and path == '/api/status':
                return self.send(200, {'application': 'chlas-mca', 'setup_required': bool(self.server.store.setup_token),
                                      'setup_allowed': not self.server.secure, 'access': 'vpn_https' if self.server.secure else 'local',
                                      'user': self.server.store.session(self.cookie()),                                       'version': installed_version()})
            if post and path in ('/api/login', '/api/setup'):
                if path == '/api/setup' and self.server.secure:
                    raise PermissionError('Le premier compte doit être créé sur le PC hébergeant l’application, via son accès local.')
                data = self.body()
                if path == '/api/setup':
                    # Initial provisioning has its own limiter; it must never block login.
                    peer, clock = self.client_address[0], time.monotonic()
                    with self.server.setup_attempts_lock:
                        for key in list(self.server.setup_attempts):
                            self.server.setup_attempts[key] = [t for t in self.server.setup_attempts[key] if clock-t < 300]
                            if not self.server.setup_attempts[key]:
                                del self.server.setup_attempts[key]
                        attempts = self.server.setup_attempts.setdefault(peer, [])
                        if len(attempts) >= 8 or sum(map(len, self.server.setup_attempts.values())) >= 500:
                            return self.send(429, {'error': 'Trop de tentatives de première ouverture. Réessayer dans cinq minutes.'})
                        attempts.append(clock)
                    self.server.store.setup(data.get('token'), data.get('username'), data.get('password'))
                token, u = self.server.store.login(data.get('username'), data.get('password'))
                cookie_secure = '; Secure' if self.server.secure else ''
                return self.send(200, u, extra={'Set-Cookie': f'chlas_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800{cookie_secure}'})
            if not post and path == '/guide':
                return self.send_guide()
            u = self.user(write=post)
            actor = u['username']
            if path in ('/api/storage', '/api/storage/probe', '/api/storage/run'):
                self.user(write=post,admin=True)
                from .nas_storage import archive_for
                archive=archive_for(self.server.store)
                if path.endswith('/probe'):
                    if not post:raise ValueError('Utiliser le bouton Vérifier l’accès.')
                    return self.send(200,archive.probe(self.body()))
                if path.endswith('/run'):
                    if not post:raise ValueError('Utiliser le bouton Sauvegarder maintenant.')
                    self.body();archive.tick(force=True)
                    return self.send(200,archive.status())
                return self.send(200,archive.configure(self.body(),actor) if post else archive.status())
            # « Nouveautés » window: what changed since this user last closed it.
            if path in ('/api/news', '/api/news/seen'):
                from . import news
                if path.endswith('/seen'):
                    if not post:
                        raise ValueError('Utiliser le bouton J’ai compris.')
                    self.user(write=True)
                    self.body()
                    return self.send(200, news.mark_seen(self.server.store, u, installed_version()))
                return self.send(200, news.status(self.server.store, u, installed_version()))
            # Signed update already deposited in « INSTAL MAJ », installed by an administrator.
            if path in ('/api/updates', '/api/updates/install'):
                from . import remote_update
                self.user(write=post, admin=True)
                if path.endswith('/install'):
                    if not post:
                        raise ValueError('Utiliser le bouton Installer.')
                    started = remote_update.start(self.body(), installed_version(), actor)
                    self.server.store.audit(actor, 'remote_update_started', started)
                    return self.send(200, started)
                return self.send(200, remote_update.status(installed_version()))
            # Scope every data endpoint on the server, including source files and
            # exports. Administrators manage accounts but do not bypass assignments.
            if path.startswith('/api/pcr/'):
                if 'pcrlia' not in u['apps']:
                    raise PermissionError('Ce compte n’est pas affecté à PCRLia.')
                pcr = self.server.pcr
                if path in ('/api/pcr/mca', '/api/pcr/mca/retry', '/api/pcr/mca/reinclude'):
                    from .pcr_mca import receiver_for
                    self.user(write=post, admin=True)
                    receiver = receiver_for(self.server.store)
                    if path.endswith(('/retry', '/reinclude')):
                        if not post:
                            raise ValueError('Utiliser le bouton Réexaminer.')
                        self.body()
                        return self.send(200, receiver.retry(actor, reinclude_only=path.endswith('/reinclude')))
                    return self.send(200, receiver.configure(self.body(), actor) if post else
                                     {'config': receiver.config, **receiver.status()})
                if path == '/api/pcr/samples' and not post:
                    return self.send(200, pcr.listing())
                if path == '/api/pcr/config':
                    self.user(write=post, admin=True)
                    if post:
                        result = pcr.configure(self.body(), actor)
                        from .pcr_mca import receiver_for
                        receiver_for(self.server.store).recheck_ignored(pcr)
                        return self.send(200, result)
                    return self.send(200, pcr.config())
                if path == '/api/pcr/codes':
                    if post:
                        self.user(write=True, admin=True)
                        return self.send(200, pcr.configure_code(self.body(), actor))
                    return self.send(200, pcr.codes())
                if path == '/api/pcr/codes/delete' and post:
                    self.user(write=True, admin=True)
                    return self.send(200, pcr.delete_code(self.body(), actor))
                if path == '/api/pcr/history' and not post:
                    query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                    return self.send(200, pcr.history(query.get('sample_id', [''])[0], query.get('request_code', [''])[0]))
                if path == '/api/pcr/scan' and post:
                    return self.send(200, pcr.scan(self.body(), actor))
                if path == '/api/pcr/handle' and post:
                    return self.send(200, pcr.handle(self.body(), actor))
                if path == '/api/pcr/comment' and post:
                    self.user(write=True)
                    return self.send(200, pcr.add_comment(self.body(), actor))
                if path == '/api/pcr/comment/change' and post:
                    self.user(write=True)
                    return self.send(200, pcr.change_comment(self.body(), actor))
                if path == '/api/pcr/comment/ack' and post:
                    self.user(write=True)
                    return self.send(200, pcr.ack_comment(self.body(), actor))
                if path == '/api/pcr/mail-log' and post:
                    self.user(write=True)
                    return self.send(200, pcr.log_mail(self.body(), actor))
                if path == '/api/pcr/cancel-receipt' and post:
                    return self.send(200, pcr.cancel_receipt(self.body(), actor))
                if path == '/api/pcr/template.csv' and not post:
                    self.user(admin=True)
                    return self.send(200, pcr.template(), 'text/csv; charset=utf-8',
                        extra={'Content-Disposition': 'attachment; filename="PCRLia-modele-demandes.csv"'})
                if path in ('/api/pcr/import-preview', '/api/pcr/import') and post:
                    self.user(write=True, admin=True)
                    data = self.body()
                    if path.endswith('import-preview'):
                        result = pcr.preview(data)
                        result.pop('_changed')
                    else:
                        result = pcr.import_list(data, actor)
                    return self.send(200, result)
                return self.send(404, {'error': 'Ressource PCRLia introuvable.'})
            if post and path == '/api/logout':
                self.server.store.logout(self.cookie())
                cookie_secure = '; Secure' if self.server.secure else ''
                return self.send(200, {}, extra={'Set-Cookie': 'chlas_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0' + cookie_secure})
            if path == '/api/users':
                self.user(write=post, admin=True)
                if post:
                    data = self.body()
                    if data.get('action') == 'update':
                        accounts.update(self.server.store, data, actor)
                    elif data.get('action', 'create') == 'create':
                        self.server.store.create_user(data.get('username'), data.get('password'), data.get('role'), actor,
                                                      data.get('apps'), data.get('default_app'),
                                                      data.get('first_name', ''), data.get('last_name', ''))
                    else:
                        raise ValueError('Action de gestion des comptes inconnue.')
                return self.send(200, accounts.listing(self.server.store))
            return self.send(404, {'error': 'Ressource introuvable.'})
        except PermissionError as exc:
            return self.send(403, {'error': str(exc)})
        except Conflict as exc:
            return self.send(409, {'error': str(exc)})
        except (ValueError, TypeError) as exc:
            return self.send(400, {'error': str(exc)})
        except KeyError:
            return self.send(404, {'error': 'Ressource introuvable.'})
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            return
        except Exception:
            return self.send(500, {'error': 'Opération interrompue. Aucune réussite ne doit être déduite de cette erreur ; actualisez la page.'})


def vpn_tls_context(address, cert_file, key_file):
    ip = ipaddress.ip_address(address)
    networks = [ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')]
    if ip.version != 4 or not any(ip in n for n in networks):
        raise ValueError('L’adresse VPN doit être une IPv4 privée précise, jamais 0.0.0.0 ni une adresse Internet.')
    if not cert_file or not key_file:
        raise ValueError('Le certificat et la clé HTTPS sont obligatoires pour l’accès VPN.')
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert_file, key_file)
    return context


def main():
    parser = argparse.ArgumentParser(description='PCRLia — réception des tubes PCR')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data-dir', default=os.environ.get('CHLAS_DATA_DIR', str(Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'PCRLia')))
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--vpn-address', help='IPv4 privée exacte attribuée à la connexion VPN')
    parser.add_argument('--https-port', type=int, default=8443)
    parser.add_argument('--cert-file')
    parser.add_argument('--key-file')
    args = parser.parse_args()
    root = Path(args.data_dir).resolve()
    # Avoid accidentally uploading health documents through the source project's OneDrive sync.
    if any(p.lower().startswith(('onedrive', 'dropbox', 'google drive')) for p in root.parts):
        parser.error('Le stockage de données doit être hors des dossiers synchronisés (OneDrive, Dropbox, Google Drive).')
    try:
        tls = vpn_tls_context(args.vpn_address, args.cert_file, args.key_file) if args.vpn_address else None
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    store = Store(root)
    server = AppServer(('127.0.0.1', args.port), store)
    vpn_server = None
    if tls:
        try:
            vpn_server = AppServer((args.vpn_address, args.https_port), store, tls_context=tls)
        except OSError:
            server.server_close()
            store.db.close()
            parser.error('Impossible d’ouvrir l’adresse VPN. Vérifier la connexion et le port ; aucun élargissement d’écoute effectué.')
        threading.Thread(target=vpn_server.serve_forever, daemon=True).start()
        print(f'Accès VPN de test : https://{args.vpn_address}:{args.https_port}', flush=True)
    url = f'http://127.0.0.1:{server.server_port}'
    print(f'Application locale : {url}\nDonnées : {root}\nMCA : aucun envoi activé.', flush=True)
    if store.setup_token:
        print('Première ouverture : la clé est dans premiere-ouverture.txt, dans le dossier de données.', flush=True)
    if not args.no_browser:
        bootstrap = '#setup=' + store.setup_token if store.setup_token else ''
        threading.Timer(.6, lambda: webbrowser.open(url + bootstrap)).start()
    try:
        start_background(store)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if vpn_server:
            vpn_server.shutdown()
            vpn_server.server_close()
        server.server_close()
        store.db.close()


if __name__ == '__main__':
    main()
