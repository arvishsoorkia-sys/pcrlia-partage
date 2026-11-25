# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
"""Catalogue upgrades and clean installation; synthetic data stay in preview only."""
import tempfile
import unittest

from app.pcr import PCRService
from app.storage import Conflict, Store, encode
from scripts.pcr_demo_data import requests, seed


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pcrlia-catalog-')
        self.store = Store(self.temp.name)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_clean_install_has_five_mappings_and_no_test_dossiers(self):
        pcr = PCRService(self.store)
        actual = [(c['code'], c['label'], c['group_key'], c['suffix'], c['active']) for c in pcr.codes()]
        self.assertEqual(actual, [
            ('COVID', 'COVID', 'covid', '85', True),
            ('CTNG1', 'CTNG1', 'ist', '88', True),
            ('CTNG2', 'CTNG2', 'ist', '87', True),
            ('CTNG3', 'CTNG3', 'ist', '86', True),
            ('PARASITO', 'PARASITO', 'parasito', '44', True)])
        self.assertEqual(pcr.listing()['pairs'], [])
        self.assertIsNone(pcr.listing()['source']['last_import'])
        self.assertEqual(pcr.revision(), 0)
        for table in ('pcr_samples', 'pcr_events', 'pcr_scan_requests', 'pcr_imports'):
            self.assertEqual(self.store.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)

    def test_upgrade_keeps_legacy_codes_and_existing_custom_mapping(self):
        with self.store.db:
            self.store.db.execute('''CREATE TABLE pcr_codes(code TEXT PRIMARY KEY, label TEXT NOT NULL,
                group_key TEXT NOT NULL, active INTEGER NOT NULL, revision INTEGER NOT NULL)''')
            self.store.db.executemany('INSERT INTO pcr_codes VALUES(?,?,?,?,?)', [
                ('CTNG1', 'Libellé personnalisé', 'other', 0, 7), ('IST_A', 'Ancien code', 'ist', 1, 3)])
            self.store.db.execute('CREATE TABLE pcr_config(id INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
            self.store.db.execute('INSERT INTO pcr_config VALUES(1,?)', (encode({
                'revision': 12, 'suffixes': [{'suffix': '09', 'group': 'other', 'active': True}],
                'dossier_strip': 3}),))
        pcr = PCRService(self.store)
        self.assertEqual(pcr.code('CTNG1', active=False), {
            'code': 'CTNG1', 'label': 'Libellé personnalisé', 'group_key': 'other',
            'active': 0, 'revision': 7, 'suffix': ''})
        self.assertEqual(pcr.code('IST_A')['revision'], 3)
        self.assertEqual(pcr.code('CTNG2')['suffix'], '87')
        self.assertEqual(pcr.config()['suffixes'][0]['suffix'], '09')
        self.assertEqual(pcr.config()['dossier_strip'], 3)
        self.assertTrue(self.store.verify_audit())

    def test_edits_deactivation_and_zero_suffix_survive_restart(self):
        pcr = PCRService(self.store)
        original = next(c for c in pcr.codes() if c['code'] == 'CTNG1')
        pcr.configure_code({**original, 'label': 'Nouveau libellé', 'suffix': '03', 'active': False}, 'admin-test')
        pcr.configure({**pcr.config(), 'dossier_strip': 2}, 'admin-test')
        before = pcr.codes(), pcr.config(), self.store.db.execute('SELECT count(*) FROM audit').fetchone()[0]
        self.store.db.close()
        self.store = Store(self.temp.name)
        reloaded = PCRService(self.store)
        after = reloaded.codes(), reloaded.config(), self.store.db.execute('SELECT count(*) FROM audit').fetchone()[0]
        self.assertEqual(before, after)
        self.assertEqual(reloaded.code('CTNG1', active=False)['suffix'], '03')
        self.assertTrue(self.store.verify_audit())

    def test_suffix_validation_revision_and_old_client_compatibility(self):
        pcr = PCRService(self.store)
        original = next(c for c in pcr.codes() if c['code'] == 'CTNG2')
        for suffix in (87, '7', '087', 'AB', ' 87', None):
            with self.assertRaises(ValueError):
                pcr.configure_code({**original, 'suffix': suffix}, 'admin-test')
        old_client = {k: v for k, v in original.items() if k != 'suffix'}
        pcr.configure_code({**old_client, 'label': 'Modifié sans nouveau champ'}, 'admin-test')
        self.assertEqual(pcr.code('CTNG2')['suffix'], '87')
        with self.assertRaises(Conflict):
            pcr.configure_code(original, 'other-admin')

    def test_new_preview_uses_real_codes_and_scan_keeps_exact_pairs(self):
        pcr = PCRService(self.store)
        seed(pcr)
        pairs = pcr.listing()['pairs']
        self.assertEqual(len(pairs), 23)
        codes = {c['code']: c['suffix'] for c in pcr.codes()}
        self.assertEqual(set(codes), {'CTNG1', 'CTNG2', 'CTNG3', 'COVID', 'PARASITO'})
        for pair in pairs:
            self.assertTrue(pair['sample_id'].endswith(codes[pair['request_code']]))
        received = pcr.scan({'sample_id': '1199000187', 'request_id': 'catalog-test-scan-001'}, 'tech-test')
        self.assertEqual([p['request_code'] for p in received['pairs']], ['CTNG2'])
        self.assertIsNone(pcr.sample('1199000186')['orders']['CTNG3']['receipt'])

    def test_catalog_edit_invalidates_pending_import_without_rewriting_history(self):
        pcr = PCRService(self.store)
        data = {'content': requests(), 'source': 'DÉMONSTRATION — catalogue'}
        preview = pcr.preview(data)
        code = next(c for c in pcr.codes() if c['code'] == 'CTNG3')
        pcr.configure_code({**code, 'label': 'CTNG3 modifié'}, 'admin-test')
        with self.assertRaises(Conflict):
            pcr.import_list({**data, **preview, 'confirmed': True}, 'admin-test')
        seed(pcr)
        before = self.store.db.execute('SELECT * FROM pcr_samples ORDER BY sample_id').fetchall()
        pcr.configure_code({**next(c for c in pcr.codes() if c['code'] == 'CTNG3'), 'active': False}, 'admin-test')
        self.assertEqual(before, self.store.db.execute('SELECT * FROM pcr_samples ORDER BY sample_id').fetchall())

    def test_rename_deactivates_old_code_and_preserves_received_order_history(self):
        pcr = PCRService(self.store)
        seed(pcr)
        original = next(c for c in pcr.codes() if c['code'] == 'CTNG1')
        sample_before = pcr.sample('1199000188')
        events_before = pcr.history('1199000188')['events']
        pcr.configure_code({**original, 'original_code': 'CTNG1', 'code': 'CTNG_NOUVEAU'}, 'admin-test')
        self.assertFalse(pcr.code('CTNG1', active=False)['active'])
        self.assertEqual(pcr.code('CTNG_NOUVEAU')['suffix'], '88')
        self.assertEqual(pcr.sample('1199000188'), sample_before)
        self.assertEqual(pcr.history('1199000188')['events'], events_before)
        self.assertTrue(self.store.verify_audit())
        with self.assertRaises(Conflict):
            pcr.configure_code({**next(c for c in pcr.codes() if c['code'] == 'CTNG2'),
                                'original_code': 'CTNG2', 'code': 'CTNG3'}, 'admin-test')
        self.assertTrue(pcr.code('CTNG2')['active'])

    def test_known_suffix_without_order_is_error_and_never_invents_ctng1(self):
        pcr = PCRService(self.store)
        seed(pcr)
        result = pcr.scan({'sample_id': '1199999988', 'request_id': 'catalog-without-order-001'}, 'tech-test')
        pair = result['pairs'][0]
        self.assertTrue(pair['pending_error'])
        self.assertEqual(pair['error_reasons'], ['Absent des demandes'])
        self.assertEqual(pair['request_code'], '')
        self.assertEqual(pcr.sample('1199999988')['orders'], {})
        self.assertEqual(result['matched_count'], 0)

    def test_wrong_suffix_for_received_request_is_error_and_setting_fix_is_traced(self):
        pcr = PCRService(self.store)
        # The correct tube exists, but its only request is CTNG2, expected on 87.
        data = {'content': requests().replace(';CTNG1;', ';CTNG2;'), 'source': 'DÉMONSTRATION — suffixe incompatible'}
        pcr.import_list({**data, **pcr.preview(data), 'confirmed': True}, 'admin-test')
        result = pcr.scan({'sample_id': '1199000188', 'request_id': 'catalog-wrong-suffix-001'}, 'tech-test')
        pair = result['pairs'][0]
        self.assertTrue(pair['pending_error'])
        self.assertEqual(pair['error_reasons'], ['Suffixe incompatible : CTNG2 attend 87'])
        self.assertEqual(pair['request_code'], 'CTNG2')
        self.assertEqual(set(pcr.sample('1199000188')['orders']), {'CTNG2'})
        original = next(c for c in pcr.codes() if c['code'] == 'CTNG2')
        pcr.configure_code({**original, 'suffix': '88'}, 'admin-test')
        corrected = pcr.history('1199000188')['pair']
        self.assertFalse(corrected['pending_error'])
        self.assertEqual(corrected['receipt'], pair['receipt'])
        self.assertTrue(any(i['kind'] == 'error' and not i['active'] for i in corrected['issues']))
        self.assertTrue(self.store.verify_audit())


if __name__ == '__main__':
    unittest.main()
