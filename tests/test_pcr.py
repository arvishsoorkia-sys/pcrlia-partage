# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
"""Synthetic receipts only: never contact MCA or use an installed data directory."""
import csv
import http.client
import io
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from app import accounts
from app.pcr import COLUMNS, PATIENT_COLUMNS, PCRService, csv_rows
from app.server import AppServer
from app.storage import Conflict, Store, password_hash


def contents(rows):
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(COLUMNS)
    for sid, code, *extra in rows:
        state = extra[0] if extra else 'attendu'
        writer.writerow(['DEMO001', sid, code, 'Libellé source', 'Prélèvement fictif', '2026-11-12', '2026-11-11', 'SITE TEST', state])
    return output.getvalue()


def configure(pcr):
    for code, label in [('IST_A', 'Panel IST fictif A'), ('IST_B', 'Panel IST fictif B')]:
        pcr.configure_code({'code': code, 'label': label, 'group_key': 'ist', 'active': True, 'revision': 0}, 'admin-test')


def imported(pcr, rows):
    data = {'content': contents(rows), 'source': 'Liste synthétique'}
    preview = pcr.preview(data)
    return pcr.import_list({**data, **{k: preview[k] for k in ('digest', 'revision', 'catalog_digest')}, 'confirmed': True}, 'admin-test')


class PCRTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pcrlia-domain-')
        self.store = Store(self.temp.name)
        self.pcr = PCRService(self.store)
        configure(self.pcr)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def scan(self, sid, code='IST_A', token='scan-request-00000001'):
        return self.pcr.scan({'sample_id': sid, 'request_code': code, 'request_id': token}, 'technicien-test')

    def tube_scan(self, sid, token='tube-scan-request-000001'):
        return self.pcr.scan({'sample_id': sid, 'request_id': token}, 'technicien-test')

    def test_barcode_only_receives_all_codes_on_exact_tube(self):
        imported(self.pcr, [('00000186','IST_A'),('00000186','IST_B'),('00000187','IST_A')])
        result = self.tube_scan('00000186')
        self.assertEqual(result['matched_count'], 2)
        states = {(p['sample_id'],p['request_code']):p['status'] for p in self.pcr.listing()['pairs']}
        self.assertEqual(states, {('00000186','IST_A'):'received',('00000186','IST_B'):'received',('00000187','IST_A'):'expected'})
        self.assertEqual(result['pairs'][0]['receipt'], result['pairs'][1]['receipt'])

    def test_unknown_barcode_is_stored_without_inventing_analysis_then_reconciled(self):
        first = self.tube_scan('00000186')['pairs'][0]['receipt']
        pair = self.pcr.listing()['pairs'][0]
        self.assertEqual(pair['request_code'], '')
        self.assertEqual(pair['group'], 'ist')
        self.assertEqual(self.pcr.sample('00000186')['orders'], {})
        imported(self.pcr, [('00000187','IST_A'),('00000186','IST_A')])
        imported(self.pcr, [('00000186','IST_B')])
        rows = self.pcr.listing()['pairs']
        self.assertEqual(len(rows), 3)
        for p in rows:
            if p['sample_id'] == '00000186':
                self.assertEqual(p['receipt'], first)
                self.assertIsNotNone(p['scanned_at'])
            else:
                self.assertIsNone(p['receipt'])
        self.tube_scan('186', 'tube-scan-request-000002')
        self.assertEqual(self.pcr.listing()['pairs'][-1]['request_code'], '')

    def test_barcode_retries_and_late_import_do_not_undo_receipt_correction(self):
        imported(self.pcr, [('00000186','IST_A'),('00000186','IST_B','annule')])
        first = self.tube_scan('00000186')
        revision = self.pcr.revision()
        self.assertTrue(self.tube_scan('00000186')['replayed'])
        self.assertEqual(self.pcr.revision(), revision)
        self.assertEqual(first['cancelled_count'], 1)
        pair = next(p for p in first['pairs'] if p['request_code']=='IST_A')
        self.pcr.cancel_receipt({'sample_id':'00000186','request_code':'IST_A','revision':pair['revision'],
                                'confirmed':True,'reason':'Erreur de pointage'}, 'test')
        data={'source':'Réimport actualisé','content':contents([('00000186','IST_A')]).replace('Libellé source','Libellé source modifié')}
        preview=self.pcr.preview(data)
        self.pcr.import_list({**data,**preview,'confirmed':True}, 'test')
        self.assertIsNone(self.pcr.history('00000186','IST_A')['pair']['receipt'])
        self.assertTrue(self.tube_scan('00000186')['replayed'])
        self.assertIsNone(self.pcr.history('00000186','IST_A')['pair']['receipt'])
        with self.assertRaises(Conflict):
            self.scan('00000186', token='tube-scan-request-000001')
        self.tube_scan('00000186','tube-scan-request-000002')
        self.assertEqual(self.pcr.history('00000186','IST_A')['pair']['status'],'received')
        self.assertEqual(self.pcr.history('00000186','IST_B')['pair']['status'],'cancelled')

    def test_barcode_scan_transaction_is_atomic_and_survives_restart(self):
        imported(self.pcr, [('00000186','IST_A'),('00000186','IST_B')])
        with patch.object(self.store,'audit',side_effect=RuntimeError('disk test')):
            with self.assertRaises(RuntimeError):
                self.tube_scan('00000186')
        self.assertTrue(all(p['receipt'] is None for p in self.pcr.listing()['pairs']))
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda n:self.tube_scan('00000186',f'tube-concurrent-{n:08d}'),range(2)))
        self.assertEqual(sum(r['duplicate'] for r in results),1)
        self.assertEqual(results[0]['pairs'][0]['receipt'],results[1]['pairs'][1]['receipt'])
        self.store.db.close();self.store=Store(self.temp.name);self.pcr=PCRService(self.store)
        self.assertTrue(all(p['status']=='received' for p in self.pcr.listing()['pairs']))

    def patient_import(self, dossier='', pid='P001', patient='Patient fictif'):
        output = io.StringIO()
        writer = csv.writer(output, delimiter=';')
        writer.writerow(COLUMNS + PATIENT_COLUMNS)
        writer.writerow([dossier,'00123486','IST_A','','','2026-11-14','','LAB TEST','attendu',pid,patient])
        data = {'content': output.getvalue(), 'source': 'Essai patient'}
        preview = self.pcr.preview(data)
        return self.pcr.import_list({**data, **preview, 'confirmed': True}, 'test')

    def test_patient_identity_and_optional_dossier_survive_partial_import(self):
        self.patient_import()
        pair = self.pcr.listing()['pairs'][0]
        self.assertEqual((pair['patient_id'], pair['patient'], pair['dossier']), ('P001','Patient fictif',''))
        self.patient_import('DOS001', '', '')
        pair = self.pcr.listing()['pairs'][0]
        self.assertEqual((pair['patient_id'], pair['patient'], pair['dossier']), ('P001','Patient fictif','DOS001'))
        with self.assertRaisesRegex(Conflict, 'patient'):
            self.patient_import('DOS001', 'P002', 'Même nom fictif')
        self.assertEqual(self.pcr.listing()['pairs'][0]['patient_id'], 'P001')

    def test_configurable_suffixes_classify_without_changing_exact_pair(self):
        imported(self.pcr, [(f'000001{s}', 'IST_A') for s in ('86','87','88','44','85','99')])
        self.assertEqual([p['group'] for p in self.pcr.listing()['pairs']], ['parasito','covid','ist','ist','ist','ist'])
        config = self.pcr.config()
        for r in config['suffixes']:
            if r['suffix'] == '85': r['group'] = 'other'
        self.pcr.configure(config, 'test')
        self.assertEqual(self.pcr.history('00000185', 'IST_A')['pair']['group'], 'other')
        self.scan('00000185')
        self.assertEqual(self.pcr.history('00000186', 'IST_A')['pair']['status'], 'expected')
        with self.assertRaises(Conflict):
            self.pcr.configure(config, 'test')
        config = self.pcr.config()
        config['suffixes'].append(config['suffixes'][0])
        with self.assertRaises(ValueError):
            self.pcr.configure(config, 'test')

    def test_dossier_transcoding_is_opt_in_and_never_overwrites_source(self):
        self.patient_import()
        self.assertFalse(self.pcr.listing()['pairs'][0]['dossier_derived'])
        config = self.pcr.config()
        self.pcr.configure({**config, 'dossier_strip': 2}, 'test')
        pair = self.pcr.listing()['pairs'][0]
        self.assertEqual((pair['dossier'], pair['dossier_derived']), ('001234', True))
        self.assertEqual(self.pcr.sample('00123486')['dossier'], '')
        self.patient_import('MCA_DOSSIER')
        pair = self.pcr.listing()['pairs'][0]
        self.assertEqual((pair['dossier'], pair['dossier_derived']), ('MCA_DOSSIER', False))
        self.assertTrue(self.store.verify_audit())

    def test_scan_register_retains_unmatched_cancelled_and_corrected_receipts(self):
        first = self.scan('00000185')['pair']
        self.pcr.cancel_receipt({'sample_id':'00000185','request_code':'IST_A','revision':first['revision'],
                                'reason':'Erreur fictive','confirmed':True}, 'test')
        pair = self.pcr.listing()['pairs'][0]
        self.assertIsNone(pair['receipt'])
        self.assertIsNotNone(pair['scanned_at'])
        self.scan('00000185', token='another-scan-request-00002')
        imported(self.pcr, [('00000185','IST_A','annule')])
        pair = self.pcr.listing()['pairs'][0]
        self.assertIsNotNone(pair['scanned_at'])
        self.assertEqual(pair['status'], 'cancelled')

    def test_complete_sample_and_request_code_are_both_required(self):
        imported(self.pcr, [('00000185', 'IST_A'), ('00000185', 'IST_B'), ('00000186', 'IST_A'), ('00000187', 'IST_A')])
        self.scan('00000185')
        states = {(p['sample_id'], p['request_code']): p['status'] for p in self.pcr.listing()['pairs']}
        self.assertEqual(states, {('00000185', 'IST_A'): 'received', ('00000185', 'IST_B'): 'expected',
                                  ('00000186', 'IST_A'): 'expected', ('00000187', 'IST_A'): 'expected'})
        self.assertEqual(self.scan('185', token='scan-request-00000002')['pair']['status'], 'unknown')

    def test_unknown_pair_merges_only_with_exact_later_request(self):
        initial = self.scan('00000185')['pair']['receipt']
        imported(self.pcr, [('00000185', 'IST_B')])
        states = {p['request_code']: p['status'] for p in self.pcr.listing()['pairs']}
        self.assertEqual(states, {'IST_A': 'unknown', 'IST_B': 'expected'})
        imported(self.pcr, [('00000185', 'IST_A')])
        self.assertEqual(self.pcr.history('00000185', 'IST_A')['pair']['receipt'], initial)

    def test_repeat_and_network_retry_do_not_create_another_expected_pair(self):
        imported(self.pcr, [('00000185', 'IST_A')])
        first = self.scan('00000185')
        revision = self.pcr.revision()
        self.assertEqual(self.scan('00000185'), first)
        self.assertEqual(self.pcr.revision(), revision)
        repeated = self.scan('00000185', token='scan-request-00000002')
        self.assertTrue(repeated['duplicate'])
        self.assertEqual(first['pair']['receipt'], repeated['pair']['receipt'])
        self.assertEqual(len(self.pcr.listing()['pairs']), 1)
        with self.assertRaises(Conflict):
            self.scan('00000185', 'IST_B')

    def test_concurrent_same_pair_scans_are_serialized(self):
        imported(self.pcr, [('00000185', 'IST_A')])
        with ThreadPoolExecutor(2) as pool:
            result = list(pool.map(lambda n: self.scan('00000185', token=f'scan-request-{n:08d}'), range(2)))
        self.assertEqual(sum(r['duplicate'] for r in result), 1)
        self.assertEqual(result[0]['pair']['receipt'], result[1]['pair']['receipt'])

    def test_incremental_import_never_removes_omitted_pairs(self):
        imported(self.pcr, [('00000185', 'IST_A'), ('00000186', 'IST_A')])
        self.scan('00000185')
        imported(self.pcr, [('00000185', 'IST_A', 'annule')])
        pairs = {p['sample_id']: p for p in self.pcr.listing()['pairs']}
        self.assertEqual(pairs['00000185']['status'], 'cancelled')
        self.assertIsNotNone(pairs['00000185']['receipt'])
        self.assertEqual(pairs['00000186']['status'], 'expected')

    def test_receipt_correction_is_versioned_and_pair_scoped(self):
        imported(self.pcr, [('00000185', 'IST_A'), ('00000185', 'IST_B')])
        p = self.scan('00000185')['pair']
        self.scan('00000185', 'IST_B', token='scan-request-00000002')
        data = {'sample_id': p['sample_id'], 'request_code': 'IST_A', 'revision': p['revision'], 'confirmed': True, 'reason': 'Erreur de pointage fictive'}
        with self.assertRaises(Conflict):
            self.pcr.cancel_receipt(data, 'technicien-test')
        data['revision'] = self.pcr.history(p['sample_id'], 'IST_A')['pair']['revision']
        self.pcr.cancel_receipt(data, 'technicien-test')
        self.assertEqual(self.pcr.history(p['sample_id'], 'IST_A')['pair']['status'], 'expected')
        self.assertEqual(self.pcr.history(p['sample_id'], 'IST_B')['pair']['status'], 'received')
        self.assertEqual(self.pcr.history(p['sample_id'], 'IST_A')['events'][0]['action'], 'receipt_cancelled')
        # A late retry of the first HTTP scan must not claim the cancelled receipt
        # is still active, or create it again.
        revision = self.pcr.revision()
        retried = self.scan('00000185')
        self.assertTrue(retried['replayed'])
        self.assertEqual(retried['pair']['status'], 'expected')
        self.assertEqual(self.pcr.revision(), revision)
        self.assertTrue(self.store.verify_audit())

    def test_preview_is_read_only_and_commit_rejects_stale_content_and_catalog(self):
        data = {'content': contents([('00000185', 'IST_A')]), 'source': 'Essai'}
        p = self.pcr.preview(data)
        self.assertFalse(self.pcr.listing()['pairs'])
        with self.assertRaises(Conflict):
            self.pcr.import_list({**data, **p, 'digest': 'wrong', 'confirmed': True}, 'test')
        self.pcr.configure_code({'code': 'IST_A', 'label': 'Nouveau libellé', 'active': True, 'revision': 1, 'group_key': 'ist'}, 'admin-test')
        with self.assertRaises(Conflict):
            self.pcr.import_list({**data, **p, 'confirmed': True}, 'test')
        p = self.pcr.preview(data)
        self.scan('00000187')
        with self.assertRaises(Conflict):
            self.pcr.import_list({**data, **p, 'confirmed': True}, 'test')

    def test_unknown_code_and_dossier_conflict_block_whole_import(self):
        with self.assertRaisesRegex(ValueError, 'paramétrer'):
            imported(self.pcr, [('00000185', 'REAL_CODE_NOT_CONFIGURED')])
        self.assertFalse(self.pcr.listing()['pairs'])
        imported(self.pcr, [('00000185', 'IST_A')])
        data = {'content': contents([('00000186', 'IST_A'), ('00000185', 'IST_A')]).replace('DEMO001;00000185', 'OTHER;00000185'), 'source': 'Conflit'}
        with self.assertRaises(Conflict):
            self.pcr.preview(data)
        self.assertEqual(len(self.pcr.listing()['pairs']), 1)

    def test_configured_label_wins_and_disabled_codes_are_retained(self):
        imported(self.pcr, [('00000185', 'IST_A')])
        self.assertEqual(self.pcr.listing()['pairs'][0]['label'], 'Panel IST fictif A')
        self.pcr.configure_code({'code': 'IST_A', 'label': 'Autre libellé', 'group_key': 'ist', 'active': False, 'revision': 1}, 'admin-test')
        with self.assertRaisesRegex(ValueError, 'désactivé'):
            self.scan('00000185')
        self.assertFalse(self.pcr.listing()['pairs'][0]['code_active'])

    def test_duplicate_file_replay_does_not_undo_cancellation(self):
        original = [('00000185', 'IST_A')]
        imported(self.pcr, original)
        imported(self.pcr, [('00000185', 'IST_A', 'annule')])
        self.assertTrue(imported(self.pcr, original)['duplicate'])
        self.assertEqual(self.pcr.listing()['pairs'][0]['status'], 'cancelled')

    def test_validation_and_atomicity(self):
        bad = contents([('00000185', 'IST_A')]).replace('2026-11-12', '2026-02-30')
        with self.assertRaises(ValueError):
            csv_rows(bad)
        with self.assertRaises(ValueError):
            csv_rows('"unterminated')
        with self.assertRaises(ValueError):
            csv_rows(contents([('00000185', 'IST_A'), ('00000185', 'IST_A', 'annule')]))
        self.assertFalse(self.pcr.listing()['pairs'])
        with patch.object(self.store, 'audit', side_effect=RuntimeError('disk test')):
            with self.assertRaises(RuntimeError):
                self.scan('00000185')
        self.assertFalse(self.pcr.listing()['pairs'])
        self.assertEqual(self.pcr.revision(), 0)





class PCRHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='pcrlia-http-');cls.store=Store(cls.temp.name)
        cls.store.create_user('admin', 'test', 'admin', 'test', ['pcrlia'])
        cls.store.create_user('pcr', 'test', 'technician', 'admin', ['pcrlia'], 'pcrlia')
        cls.store.create_user('sero', 'test', 'technician', 'admin')
        cls.store.create_user('reader', 'test', 'viewer', 'admin', ['pcrlia'], 'pcrlia')
        cls.store.create_user('admin-pcr', 'test', 'admin', 'admin', ['pcrlia'], 'pcrlia')
        cls.server=AppServer(('127.0.0.1',0),cls.store);configure(cls.server.pcr)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.store.db.close();cls.temp.cleanup()

    def request(self,path,data=None,headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
        conn.request('GET' if data is None else 'POST',path,None if data is None else json.dumps(data),{'Content-Type':'application/json',**(headers or {})})
        res=conn.getresponse();body=res.read();result=(res.status,body,dict(res.getheaders()));conn.close();return result

    def login(self,name):
        status,body,h=self.request('/api/login',{'username':name,'password':'test'});self.assertEqual(status,200)
        return {'Cookie':h['Set-Cookie'].split(';')[0],'X-CSRF-Token':json.loads(body)['csrf']}


    def test_scans_require_scope_writer_and_csrf_and_configuration_requires_admin(self):
        pcr=self.login('pcr');reader=self.login('reader')
        data={'sample_id':'00000185','request_code':'IST_A','request_id':'http-scan-request-001'}
        for headers in [None,reader,{'Cookie':pcr['Cookie']}]:
            self.assertEqual(self.request('/api/pcr/scan',data,headers)[0],403)
        self.assertEqual(self.request('/api/pcr/scan',data,pcr)[0],200)
        plain={'sample_id':'00000188','request_id':'http-barcode-only-001'}
        self.assertEqual(self.request('/api/pcr/scan',plain,reader)[0],403)
        status,body,_=self.request('/api/pcr/scan',plain,pcr)
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['kind'],'tube')
        self.assertEqual(self.request('/api/pcr/import-preview',{},pcr)[0],403)
        self.assertEqual(self.request('/api/pcr/codes',{},pcr)[0],403)
        self.assertEqual(self.request('/api/pcr/config',{},pcr)[0],403)
        self.assertEqual(self.request('/api/pcr/config',headers=pcr)[0],403)
        self.assertEqual(self.request('/api/users',headers=pcr)[0],403)
        self.assertEqual(self.request('/api/pcr/samples',headers=reader)[0],200)

    def test_handling_requires_assignment_writer_csrf_and_records_session_actor(self):
        pcr = self.login('pcr')
        _, body, _ = self.request('/api/pcr/scan', {'sample_id':'1199000399','request_id':'http-handling-scan-001'}, pcr)
        revision = json.loads(body)['revision']
        data = {'sample_id':'1199000399','revision':revision,'confirmed':True,'action':'removed',
                'actor':'forged-user','at':'1900-01-01'}
        for headers in [None, self.login('reader'), {'Cookie':pcr['Cookie']}]:
            self.assertEqual(self.request('/api/pcr/handle',data,headers)[0],403)
        status, body, _ = self.request('/api/pcr/handle',data,pcr)
        self.assertEqual(status,200)
        pair = json.loads(body)['pairs'][0]
        self.assertFalse(pair['pending_error'])
        self.assertEqual(pair['last_handling']['actor'],'pcr')
        self.assertNotEqual(pair['last_handling']['at'],'1900-01-01')
        self.assertEqual(self.request('/api/pcr/handle',data,pcr)[0],409)
        self.assertEqual(self.request('/api/pcr/history?sample_id=1199000399',headers=self.login('reader'))[0],200)



    def test_admin_creates_assigned_users_and_can_import_after_preview(self):
        h=self.login('admin')
        status,_,_=self.request('/api/users',{'username':'new-pcr','password':'test','role':'technician','apps':['pcrlia'],'default_app':'pcrlia'},h)
        self.assertEqual(status,200)
        data={'source':'Fictif HTTP','content':contents([('00000186','IST_A')])}
        status,body,_=self.request('/api/pcr/import-preview',data,h);self.assertEqual(status,200)
        p=json.loads(body);self.assertNotIn('_changed',p)
        self.assertEqual(self.request('/api/pcr/import',{**data,**p,'confirmed':True},h)[0],200)


if __name__ == '__main__':
    unittest.main()
