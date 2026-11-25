# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
"""Disposable, loopback-only PCRLia demonstration. No installed data or MCA."""
import argparse
import os
os.environ['PCRLIA_DEMO'] = '1'  # fictitious example configuration
import copy
from pathlib import Path
import sys
import tempfile
import threading
import webbrowser

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import accounts
from scripts.pcr_demo_data import seed
from app.server import AppServer
from app.storage import Store


def seed_followup(server, store):
    """Preview only: exercise comments, delays, result series, duplicate scans and mails."""
    from datetime import datetime, timedelta, timezone
    pcr = server.pcr
    # Several non-received samples per network B / C laboratory, to exercise grouped mails.
    import csv, io
    from app.pcr import COLUMNS, PATIENT_COLUMNS
    from app.pcr_numbering import sample_from_dossier
    extra = [('6111495902', '88', 'DUPONT Marie', '2026-11-14'), ('6111495902', '87', 'DUPONT Marie', '2026-11-14'),
             ('6111495903', '88', 'MARTIN Paul', '2026-11-15'), ('6111895904', '85', 'BERNARD Lucie', '2026-11-18'),
             ('6111488902', '88', 'PETIT Julien', '2026-11-14'), ('6111788903', '86', 'ROBERT Chloé', '2026-11-17'),
             ('C261114902', '88', 'RICHARD Hugo', '2026-11-14'), ('C261117903', '87', 'DURAND Emma', '2026-11-17'),
             ('C261118904', '88', 'LEROY Nathan', '2026-11-18'), ('C261118904', '86', 'LEROY Nathan', '2026-11-18'),
             ('M261116905', '88', 'MOREAU Léa', '2026-11-16'), ('M261118906', '85', 'SIMON Louis', '2026-11-18'),
             ('DA261117907', '88', 'LAURENT Inès', '2026-11-17')]
    codes = {'88': 'CTNG1', '87': 'CTNG2', '86': 'CTNG3', '85': 'COVID', '44': 'PARASITO'}
    out = io.StringIO()
    writer = csv.writer(out, delimiter=';')
    writer.writerow(COLUMNS + PATIENT_COLUMNS)
    for dossier, suffix, name, day in extra:
        writer.writerow(['', sample_from_dossier(dossier, suffix), codes[suffix], '', 'Prélèvement fictif',
                         day, '', '', 'attendu', 'FICTIF-' + dossier, name + ' (fictif)'])
    data = {'content': out.getvalue(), 'source': 'DÉMONSTRATION — relances groupées — données fictives'}
    pcr.import_list({**data, **pcr.preview(data), 'confirmed': True}, 'demo-admin')
    delays ={'CTNG1': (48, 24), 'CTNG2': (48, 24), 'CTNG3': (48, None), 'COVID': (24, 6), 'PARASITO': (72, 48)}
    for row in pcr.codes():
        if row['code'] in delays:
            receipt, result = delays[row['code']]
            pcr.configure_code({**row, 'original_code': row['code'], 'done_code': row['code'] + '_F',
                                'receipt_delay_hours': receipt, 'result_delay_hours': result}, 'demo-admin')
    # Preview only: show both alerts (the result alert is off by default at installation).
    config = pcr.config()
    pcr.configure({**config, 'delay_alerts': {'receipt': True, 'result': True}}, 'demo-admin')
    local = datetime.now().replace(microsecond=0)
    utc = datetime.now(timezone.utc).replace(microsecond=0)
    ids = sorted(row[0] for row in store.db.execute('SELECT sample_id FROM pcr_samples'))
    with store.lock, store.db:
        for index, sid in enumerate(ids):
            sample = pcr.sample(sid)
            sample['mca_patient'] = {'source_identifier': f'FICTIF{index:03d}', 'name_field': sample.get('patient', '').replace(' ', '^', 1),
                                     'additional_name_field': '', 'birth_date_field': f'19{60 + index % 40:02d}{1 + index % 12:02d}{1 + index % 27:02d}',
                                     'sex_field': ''}
            # Recent requests for a third of the tubes, so that not everything is late.
            if index % 3 == 1:
                for order in sample['orders'].values():
                    stamp = (local - timedelta(hours=2 + index)).isoformat()
                    order.update(mca_first_at=stamp, mca_at=stamp, date_remontee_mca=stamp[:10])
            pcr.event(sample, 'mca-démo', 'orders_received_mca', {'preview_only': True})
    # Physical receipts: some old (late results), some recent, some with results in two hourly series.
    plan = [(sid, i) for i, sid in enumerate(ids) if pcr.sample(sid)['orders']][3:13]
    for n, (sid, _) in enumerate(plan):
        pcr.scan({'sample_id': sid, 'request_id': f'demo-suivi-reception-{n:04d}'}, 'demo-pcr')
    with store.lock, store.db:
        for n, (sid, _) in enumerate(plan):
            sample = pcr.sample(sid)
            received = (utc - timedelta(hours=[30, 3, 50, 1, 8, 26, 5, 2, 40, 12][n])).isoformat()
            sample['tube_receipt'] = sample['tube_last_scan'] = sample['last_scan'] = {'at': received, 'actor': 'demo-pcr'}
            for order in sample['orders'].values():
                order['receipt'] = order['last_scan'] = {'at': received, 'actor': 'demo-pcr'}
            if n in (1, 4, 6, 7):
                hour = local - timedelta(hours=1 if n in (1, 4) else 3)
                sample['done'] = {code: {'state': 'fait', 'at': hour.replace(minute=10 + n * 5).isoformat(), 'first_at': '',
                                         'code': code + '_F', 'mca_digest': 'preview-only'} for code in sample['orders']}
            else:
                sample['done'] = {}
            pcr.event(sample, 'demo-pcr', 'preview_receipt_dated', {'preview_only': True})
            store.db.execute("UPDATE pcr_events SET at=? WHERE sample_id=? AND action='tube_scanned'", (received, sid))
    # Duplicate scan on one tube, and a few comments.
    dup = plan[2][0]
    pcr.scan({'sample_id': dup, 'request_id': 'demo-suivi-double-0001'}, 'demo-admin')
    for sid, code, text, actor in [(plan[0][0], '', 'Tube arrivé sans étiquette de transport, recollée.', 'demo-pcr'),
                                   (plan[0][0], '', 'Vu avec le biologiste : OK pour passer.', 'demo-admin'),
                                   (ids[0], next(iter(pcr.sample(ids[0])['orders']), ''), 'Labo appelé à 10 h : tube parti hier soir.', 'demo-pcr')]:
        pcr.add_comment({'sample_id': sid, 'request_code': code, 'text': text}, actor)


def main():
    parser = argparse.ArgumentParser(description='Aperçu PCRLia sur données fictives uniquement')
    parser.add_argument('--open', action='store_true')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--legacy-demo-codes', action='store_true', help='Anciens codes fictifs, pour les tests de compatibilité uniquement')
    parser.add_argument('--with-mca-times', action='store_true', help='Ajoute des heures MCA fictives dans l’aperçu local')
    parser.add_argument('--with-done', action='store_true', help='Ajoute des codes « analyse faite » et des analyses faites fictives')
    parser.add_argument('--with-followup', action='store_true', help='Délais, retards, résultats, double scan, commentaires et DDN fictifs')
    args = parser.parse_args()
    folder = tempfile.mkdtemp(prefix='PCRLia-demo-')
    store = Store(folder)
    store.setup(store.setup_token, 'demo-admin', 'demo')
    admin = accounts.listing(store)[0]
    accounts.update(store, {**admin, 'apps': ['pcrlia'], 'default_app': 'pcrlia'}, 'installation-demo')
    store.create_user('demo-pcr', 'demo', 'technician', 'demo-admin', ['pcrlia'], 'pcrlia')
    server = AppServer(('127.0.0.1', args.port), store)
    seed(server.pcr, legacy=args.legacy_demo_codes)
    if args.with_mca_times:
        # These values exist only in the disposable preview and exercise the
        # display of the Phadia/MCA message time beside received dossiers.
        # One fictitious time per dossier, on its seeded date; one dossier is
        # left without time to show the "not transmitted" display.
        ids = sorted(row[0] for row in store.db.execute('SELECT sample_id FROM pcr_samples'))
        for index, sample_id in enumerate(ids):
            sample = server.pcr.sample(sample_id)
            if not sample['orders'] or index == 1:
                continue
            number = sum(ord(c) for c in sample.get('dossier') or sample_id)
            clock = f'{7 + number % 11:02d}:{number * 7 % 60:02d}:00'
            before = copy.deepcopy(sample['orders'])
            for order in sample['orders'].values():
                day = order.get('date_demande') or '2026-11-16'
                order.update(mca_at=f'{day}T{clock}', mca_first_at=f'{day}T{clock}',
                             date_remontee_mca=day, mca_digest='preview-only')
            server.pcr.event(sample, 'mca-démo', 'orders_received_mca',
                             {'preview_only': True, 'before': before, 'after': sample['orders']})
    if args.with_done:
        # Preview only: optional « analyse faite » codes on three analyses (two
        # left unconfigured on purpose) and a few fictitious MCA announcements.
        import app.server as server_module
        server_module.installed_version = lambda: 'aperçu local'
        for row in server.pcr.codes():
            if row['active'] and row['code'] in ('CTNG1', 'COVID', 'PARASITO'):
                server.pcr.configure_code({**row, 'original_code': row['code'], 'done_code': row['code'] + '_F'}, 'demo-admin')
        ids = sorted(row[0] for row in store.db.execute('SELECT sample_id FROM pcr_samples'))
        with store.lock, store.db:
            for index, sample_id in enumerate(ids):
                sample = server.pcr.sample(sample_id)
                codes = [code for code in sample['orders'] if code in ('CTNG1', 'COVID', 'PARASITO')]
                if index % 3 or not codes:
                    continue
                before = copy.deepcopy(sample.get('done', {}))
                sample['done'] = {code: {'state': 'fait', 'at': '2026-11-16T15:20:00', 'first_at': '2026-11-16T15:20:00',
                                         'code': code + '_F', 'mca_digest': 'preview-only'} for code in codes}
                server.pcr.event(sample, 'mca-démo', 'analysis_done_mca',
                                 {'preview_only': True, 'done_before': before, 'done_after': sample['done']})
    if args.with_followup:
        seed_followup(server, store)
    url = f'http://127.0.0.1:{server.server_port}'
    print(f'Aperçu PCRLia : {url}\nCompte : demo-admin / Mot de passe : demo\nAutre compte : demo-pcr / Mot de passe : demo\nDonnées fictives isolées : {folder}\nAucune connexion à MCA.', flush=True)
    if args.open:
        threading.Timer(.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.db.close()


if __name__ == '__main__':
    main()
