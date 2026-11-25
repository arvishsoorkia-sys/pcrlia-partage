# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
"""Install, from the browser, a signed update already deposited in « INSTAL MAJ ».

The deposited package's own launcher runs on the host, exactly as when an
administrator double-clicks the update script: the installed verifier
checks every signature, the host is stopped gracefully (never forced), a backup
is taken and a failure restores the previous version. Nothing is downloaded.
"""
import ctypes
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess

from .domain import now

NAME = re.compile(r'Patch-[A-Za-z0-9-]{0,80}?-(\d{4}\.\d{2}\.\d{2}\.\d{1,3})')
VERSION = re.compile(r'\d{4}\.\d{2}\.\d{2}\.\d{1,3}')
LOCK = 'maj-distance.json'


def installation_root():
    """Installed layout: <root>/application/app/remote_update.py; None elsewhere (preview, development)."""
    root = Path(__file__).resolve().parents[2]
    if (root / 'installation.json').is_file() and (root / 'tools' / 'common.ps1').is_file():
        return root
    return None


def key(version):
    return tuple(int(part) for part in version.split('.'))


def alive(pid):
    """True while the launched update process runs (Windows only, read-only query)."""
    if type(pid) is not int or pid <= 0:
        return False
    kernel = ctypes.windll.kernel32
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
    finally:
        kernel.CloseHandle(handle)


def read_text(path, limit=20000):
    try:
        raw = path.read_bytes()[-limit * 2:]
    except OSError:
        return ''
    for encoding in ('utf-16', 'utf-8-sig', 'cp1252'):
        if encoding == 'utf-16' and not raw.startswith((b'\xff\xfe', b'\xfe\xff')):
            continue
        try:
            return raw.decode(encoding)[-limit:]
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')[-limit:]


def running(root):
    try:
        lock = json.loads((root / 'data' / LOCK).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return lock if alive(lock.get('pid')) else None


def status(installed):
    root = installation_root()
    if root is None:
        return {'available': False, 'installed': installed, 'packages': [],
                'reason': 'Installation à distance disponible uniquement sur le PC hébergeur installé.'}
    packages = []
    folder = root / 'INSTAL MAJ'
    for item in sorted(folder.iterdir()) if folder.is_dir() else []:
        match = NAME.fullmatch(item.name)
        if not match or not item.is_dir():
            continue
        version = match[1]
        complete = ((item / 'mise-a-jour-pcrlia.ps1').is_file()
                    and not (item / 'PREPARATION-INCOMPLETE.txt').exists()
                    and not (item / 'COPIE-INCOMPLETE.txt').exists())
        compatible = (item / 'versions' / ('depuis-' + installed)).is_dir() if VERSION.fullmatch(installed or '') else False
        newer = VERSION.fullmatch(installed or '') is not None and key(version) > key(installed)
        packages.append({'name': item.name, 'version': version, 'newer': newer,
                         'installable': complete and compatible and newer,
                         'reason': '' if complete and compatible and newer else
                                   'Déjà installée ou plus ancienne.' if not newer else
                                   'Dépôt incomplet.' if not complete else
                                   'Ne s’applique pas à la version installée.',
                         'guide': read_text(item / 'LIRE-AVANT.txt', 6000) if newer else ''})
    packages.sort(key=lambda p: key(p['version']), reverse=True)
    logs = sorted((root / 'logs').glob('maj-distance-*.log')) if (root / 'logs').is_dir() else []
    current = running(root)
    return {'available': True, 'installed': installed, 'packages': packages,
            'running': bool(current), 'started': current,
            'interrupted': (root / 'update-transaction.json').exists(),
            'last_result': read_text(root / 'DERNIERE-MISE-A-JOUR.txt', 2000),
            'last_log': read_text(logs[-1], 4000) if logs else '', 'last_log_name': logs[-1].name if logs else ''}


def start(data, installed, actor):
    root = installation_root()
    if root is None:
        raise ValueError('Installation à distance disponible uniquement sur le PC hébergeur installé.')
    if data.get('confirm') != 'OUI':
        raise ValueError('Taper OUI pour confirmer l’installation.')
    state = status(installed)
    if state['running']:
        raise ValueError('Une installation est déjà en cours.')
    if state['interrupted']:
        raise ValueError('Une mise à jour précédente est interrompue : la relancer sur le PC hébergeur.')
    package = next((p for p in state['packages'] if p['name'] == data.get('name')), None)
    if package is None or not package['installable']:
        raise ValueError('Mise à jour introuvable ou non applicable à la version installée.')
    folder = root / 'INSTAL MAJ' / package['name']
    (root / 'logs').mkdir(exist_ok=True)
    log = root / 'logs' / ('maj-distance-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.log')
    command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
               '-File', str(folder / 'mise-a-jour-pcrlia.ps1'), '-Installation', str(root), '-Yes']
    base = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    with log.open('wb') as output:
        output.write(f'Installation lancée depuis le navigateur par {actor} le {now()} : {package["name"]}\r\n'.encode('utf-8'))
        output.flush()
        # Independent of the host it is about to stop; leave the job if one exists.
        for flags in (base | subprocess.CREATE_BREAKAWAY_FROM_JOB, base):
            try:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                           cwd=str(folder), creationflags=flags, close_fds=True)
                break
            except OSError:
                if flags == base:
                    raise
    lock = {'pid': process.pid, 'name': package['name'], 'version': package['version'],
            'actor': actor, 'at': now(), 'log': log.name}
    (root / 'data' / LOCK).write_text(json.dumps(lock), encoding='utf-8')
    return lock
