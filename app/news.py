# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
"""« Nouveautés » shown once per user after an update, limited to the user's spaces.

Each delivered update adds one entry below. The entry « PROCHAINE » collects what is
done but not yet delivered; it is renamed to the real version number when the update
is built (see notes/A-LIVRER-PROCHAINE-MAJ.txt). Seen versions are kept per user in the
existing settings table, written only when the user closes the window.
"""
import re

from .storage import encode

NEXT = 'PROCHAINE'
VERSION = re.compile(r'\d{4}\.\d{2}\.\d{2}\.\d{1,3}')
SETTING = 'news_seen'

# (version, [(space, text)]) — space: 'pcrlia', 'commun' or 'admin'.
# Written for technicians: what they see and do, never settings.
NEWS = [
    (NEXT, []),
]


def key(version):
    return (1, ()) if version == NEXT else (0, tuple(int(p) for p in version.split('.')))


def entries(user, installed, last_seen):
    """News newer than what this user has seen, up to the installed version."""
    installed_key = key(installed) if VERSION.fullmatch(installed or '') else key(NEXT)
    seen_key = key(last_seen) if last_seen and (last_seen == NEXT or VERSION.fullmatch(last_seen)) else None
    spaces = set(user.get('apps') or []) | {'commun'} | ({'admin'} if user.get('role') == 'admin' else set())
    result = []
    for version, items in NEWS:
        if key(version) > installed_key or (seen_key is not None and key(version) <= seen_key):
            continue
        shown = [text for space, text in items if space in spaces]
        if shown:
            result.append({'version': 'en préparation' if version == NEXT else version, 'items': shown})
    return result


def current(installed):
    return installed if VERSION.fullmatch(installed or '') else NEXT


def status(store, user, installed):
    seen = store.settings().get(SETTING, {})
    last = seen.get(user['username']) if isinstance(seen, dict) else None
    return {'installed': installed, 'entries': entries(user, installed, last)}


def mark_seen(store, user, installed):
    with store.lock, store.db:
        seen = store.settings().get(SETTING, {})
        seen = dict(seen) if isinstance(seen, dict) else {}
        seen[user['username']] = current(installed)
        store.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (SETTING, encode(seen)))
    return {'seen': seen[user['username']]}
