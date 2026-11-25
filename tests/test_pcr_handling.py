# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
"""Named physical handling, persistent pending queues and genuine repeat scans."""
import tempfile
import unittest
from uuid import uuid4
from unittest.mock import patch

from app.pcr import PCRService
from app.storage import Store, Conflict, encode
from tests.test_pcr import configure, imported, contents


class HandlingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pcrlia-handling-')
        self.store = Store(self.temp.name)
        self.pcr = PCRService(self.store)
        configure(self.pcr)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def scan(self, sid='1199000188', actor='alice', token=None):
        return self.pcr.scan({'sample_id': sid, 'request_id': token or str(uuid4())}, actor)

    def handle(self, sid='1199000188', actor='bruno', **extra):
        return self.pcr.handle({'sample_id': sid, 'revision': self.pcr.sample(sid)['revision'],
                               'action': 'removed', 'confirmed': True, **extra}, actor)

    def counts(self):
        pairs = self.pcr.listing()['pairs']
        return tuple(len({p['sample_id'] for p in pairs if p[field]})
                     for field in ('pending_error', 'pending_cancelled'))

    def test_handling_clears_only_pending_tube_preserves_receipts_and_named_history(self):
        imported(self.pcr, [('1199000188', 'IST_A', 'annule'), ('1199000188', 'IST_B', 'annule')])
        first = self.scan()
        self.scan('1199000299')
        self.assertEqual(self.counts(), (1, 1))  # Two cancelled requests, one tube.
        result = self.handle()
        self.assertEqual(self.counts(), (1, 0))
        self.assertEqual(result['pairs'][0]['receipt'], first['pairs'][0]['receipt'])
        self.assertEqual(result['pairs'][0]['receipt']['actor'], 'alice')
        self.assertEqual(result['pairs'][0]['last_handling']['actor'], 'bruno')
        self.assertEqual(result['pairs'][0]['last_handling']['label'], 'Retiré du lot')
        self.assertFalse(result['pairs'][0]['isolation_required'])
        self.handle('1199000299', action='verified', note='Vérification fictive')
        self.assertEqual(self.counts(), (0, 0))
        self.store.db.close()
        self.store = Store(self.temp.name)
        self.pcr = PCRService(self.store)
        self.assertEqual(self.counts(), (0, 0))
        h = self.pcr.history('1199000299')
        self.assertEqual(h['pair']['last_handling']['note'], 'Vérification fictive')
        self.assertTrue(any(e['actor'] == 'alice' and e['action'] == 'tube_scanned' for e in h['events']))
        self.assertTrue(any(e['actor'] == 'bruno' and e['action'] == 'tube_handled' for e in h['events']))
        self.assertTrue(self.store.verify_audit())

    def test_network_retry_is_not_duplicate_but_new_scan_reopens_handled_condition(self):
        token = str(uuid4())
        self.scan(token=token)
        self.handle()
        revision = self.pcr.revision()
        replay = self.scan(token=token)
        self.assertTrue(replay['replayed'])
        self.assertEqual(self.pcr.revision(), revision)
        self.assertEqual(self.counts(), (0, 0))
        self.assertFalse(replay['pairs'][0]['pending_duplicate'])
        real = self.scan(actor='charlotte')
        self.assertTrue(real['duplicate'])
        self.assertTrue(real['pairs'][0]['pending_duplicate'])
        self.assertEqual(self.counts(), (1, 0))
        with self.assertRaises(ValueError):
            self.handle()  # Must also resolve the duplicate explicitly.
        self.handle(duplicate_action='same_tube', action='verified')
        self.assertEqual(self.counts(), (0, 0))
        self.scan()
        handled = self.handle(duplicate_action='two_tubes_removed')
        self.assertIn('Deux tubes', handled['pairs'][0]['last_handling']['label'])
        self.assertEqual(self.counts(), (0, 0))
        self.assertEqual(sum(e['action'] == 'tube_handled' for e in self.pcr.history('1199000188')['events']), 3)

    def test_two_technicians_scanning_same_valid_barcode_keep_first_receipt(self):
        imported(self.pcr, [('1199000188', 'IST_A')])
        first = self.scan()
        self.assertEqual(self.counts(), (0, 0))
        repeated = self.scan(actor='bruno')
        self.assertTrue(repeated['pairs'][0]['pending_duplicate'])
        self.assertEqual(repeated['pairs'][0]['receipt'], first['pairs'][0]['receipt'])
        self.assertEqual(self.counts(), (1, 0))
        self.handle(duplicate_action='same_tube')
        self.assertEqual(self.counts(), (0, 0))

    def test_late_cancellation_and_new_cancellation_require_fresh_handling(self):
        imported(self.pcr, [('1199000188', 'IST_A'), ('1199000188', 'IST_B')])
        self.scan()
        imported(self.pcr, [('1199000188', 'IST_A', 'annule')])
        self.assertEqual(self.counts(), (0, 1))
        self.handle()
        # A changed label in another active request does not reopen an acknowledged cancellation.
        data = {'source': 'Autre actualisation', 'content': contents([('1199000188', 'IST_B')]).replace('Libellé source', 'Libellé mis à jour')}
        preview = self.pcr.preview(data)
        self.pcr.import_list({**data, **preview, 'confirmed': True}, 'importateur')
        self.assertEqual(self.counts(), (0, 0))
        imported(self.pcr, [('1199000188', 'IST_B', 'annule')])
        self.assertEqual(self.counts(), (0, 1))
        self.assertEqual(len(self.pcr.history('1199000188')['pair']['pending_issues']), 1)

    def test_stale_confirmation_and_failed_transaction_never_hide_new_scan(self):
        self.scan()
        old = self.pcr.sample('1199000188')['revision']
        self.scan()
        with self.assertRaises(Conflict):
            self.handle(revision=old, duplicate_action='same_tube')
        with patch.object(self.store, 'audit', side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError):
                self.handle(duplicate_action='same_tube')
        self.assertEqual(self.counts(), (1, 0))
        self.assertIsNone(self.pcr.history('1199000188')['pair']['last_handling'])

    def test_configuration_reopens_new_condition_without_erasing_past_confirmation(self):
        self.scan()
        self.handle()
        # A suffix also remains recognized while an active analysis uses it.
        code = next(c for c in self.pcr.codes() if c['code'] == 'CTNG1')
        self.pcr.configure_code({**code, 'active': False}, 'admin')
        config = self.pcr.config()
        config['suffixes'] = [r for r in config['suffixes'] if r['suffix'] != '88']
        self.pcr.configure(config, 'admin')
        self.assertEqual(self.counts(), (1, 0))
        pair = self.pcr.history('1199000188')['pair']
        self.assertEqual(pair['pending_reasons'], ['Suffixe inconnu'])
        self.assertEqual(pair['last_handling']['actor'], 'bruno')

    def test_upgrade_existing_untracked_tube_adds_pending_actions_only_once(self):
        self.scan()
        sample = self.pcr.sample('1199000188')
        del sample['issues']
        with self.store.db:
            self.store.db.execute('UPDATE pcr_samples SET payload=? WHERE sample_id=?', (encode(sample), sample['sample_id']))
        PCRService(self.store)
        self.assertEqual(self.counts(), (1, 0))
        revision = self.pcr.revision()
        PCRService(self.store)
        self.assertEqual(self.pcr.revision(), revision)
        self.assertIsNone(self.pcr.history('1199000188')['pair']['last_handling'])

    def reactivate(self, sid='1199000188', code='IST_A'):
        # A new source update, not a replay of a previously imported file.
        data = {'source': 'Nouvelle demande fictive',
                'content': contents([(sid, code)]).replace('2026-11-12', '2026-11-15')}
        preview = self.pcr.preview(data)
        return self.pcr.import_list({**data, **preview, 'confirmed': True}, 'source-test')

    def test_reactivated_request_leaves_cancellations_and_receives_normally(self):
        imported(self.pcr, [('1199000188', 'IST_A', 'annule'), ('1199000187', 'IST_A', 'annule')])
        self.assertEqual(self.counts(), (0, 0))
        self.reactivate()
        pair = self.pcr.history('1199000188')['pair']
        self.assertEqual(pair['state'], 'attendu')
        self.assertEqual(pair['status'], 'expected')
        self.assertFalse(pair['pending_cancelled'])
        self.assertEqual(self.counts(), (0, 0))  # The other cancellation has no physical scan.
        scan = self.scan()
        self.assertEqual(scan['matched_count'], 1)
        self.assertEqual(scan['cancelled_count'], 0)
        self.assertFalse(scan['pairs'][0]['pending_cancelled'])
        self.assertFalse(scan['pairs'][0]['isolation_required'])
        events = self.pcr.history('1199000188')['events']
        self.assertTrue(any(e['detail'].get('after', {}).get('orders', {}).get('IST_A', {}).get('state') == 'annule' for e in events))

    def test_reactivation_keeps_named_handling_and_repeat_scan_is_only_a_duplicate(self):
        imported(self.pcr, [('1199000188', 'IST_A', 'annule')])
        self.scan()
        self.handle(action='managed')
        self.assertEqual(self.pcr.history('1199000188')['pair']['last_handling']['label'], 'Prise en charge confirmée')
        self.reactivate()
        pair = self.pcr.history('1199000188')['pair']
        self.assertEqual(pair['state'], 'attendu')
        self.assertEqual(pair['last_handling']['actor'], 'bruno')
        self.assertFalse(pair['pending_cancelled'])
        scan = self.scan()
        self.assertEqual(scan['cancelled_count'], 0)
        self.assertTrue(scan['pairs'][0]['pending_duplicate'])
        self.assertEqual([i['kind'] for i in scan['pairs'][0]['pending_issues']], ['duplicate'])

    def test_partial_reactivation_does_not_hide_other_cancelled_analysis(self):
        imported(self.pcr, [('1199000188', 'IST_A', 'annule'), ('1199000188', 'IST_B', 'annule')])
        old_revision = self.pcr.sample('1199000188')['revision']
        self.reactivate()
        pairs = {p['request_code']: p for p in self.pcr.listing()['pairs']}
        self.assertEqual(pairs['IST_A']['state'], 'attendu')
        self.assertEqual(pairs['IST_B']['state'], 'annule')
        self.assertEqual(pairs['IST_A']['pending_issues'], [])
        with self.assertRaises(Conflict):
            self.handle(revision=old_revision, action='managed')
        scan = self.scan()
        self.assertEqual(scan['matched_count'], 1)
        self.assertEqual(scan['cancelled_count'], 1)
        self.assertEqual([i['key'] for i in scan['pairs'][0]['pending_issues']], ['cancelled:IST_B'])

    def test_unscanned_cancellation_needs_no_action_until_physical_scan(self):
        imported(self.pcr, [('1199000188', 'IST_A', 'annule')])
        pair=self.pcr.listing()['pairs'][0]
        self.assertEqual(pair['state'],'annule')
        self.assertFalse(pair['has_physical_scan'])
        self.assertEqual(pair['pending_issues'],[])
        self.assertEqual(self.counts(),(0,0))
        self.assertIsNone(pair['last_handling'])
        self.assertFalse(pair['isolation_required'])
        with self.assertRaises(Conflict):self.handle()
        scanned=self.scan()['pairs'][0]
        self.assertTrue(scanned['has_physical_scan'])
        self.assertTrue(scanned['pending_cancelled'])
        self.assertTrue(scanned['isolation_required'])
        self.assertEqual(self.counts(),(0,1))

    def test_old_unscanned_alert_is_not_pending_and_history_is_preserved_on_read(self):
        imported(self.pcr, [('1199000188', 'IST_A', 'annule')])
        sample=self.pcr.sample('1199000188')
        legacy={'key':'cancelled:IST_A','kind':'cancelled','label':'Demande annulée',
                'id':'legacy-unscanned','at':'2026-11-15T12:00:00+00:00','active':True,'handling':None}
        sample['issues']=[legacy]
        with self.store.db:self.store.db.execute('UPDATE pcr_samples SET payload=? WHERE sample_id=?',(encode(sample),sample['sample_id']))
        before=list(self.store.db.iterdump())
        self.pcr=PCRService(self.store)
        self.assertEqual(self.counts(),(0,0))
        pair=self.pcr.history(sample['sample_id'])['pair']
        self.assertEqual(pair['issues'],[legacy])
        self.assertEqual(pair['pending_issues'],[])
        self.assertIsNone(pair['last_handling'])
        with self.assertRaises(Conflict):self.handle()
        self.assertEqual(list(self.store.db.iterdump()),before)
        scanned=self.scan()['pairs'][0]
        self.assertTrue(scanned['pending_cancelled'])
        self.assertEqual(self.counts(),(0,1))

    def test_voided_receipt_does_not_erase_the_fact_that_tube_was_scanned(self):
        imported(self.pcr, [('1199000188','IST_A')])
        first=self.scan()['pairs'][0]
        self.pcr.cancel_receipt({'sample_id':first['sample_id'],'request_code':'IST_A',
            'revision':first['revision'],'confirmed':True,'reason':'Correction fictive du pointage'},'alice')
        imported(self.pcr, [('1199000188','IST_A','annule')])
        pair=self.pcr.listing()['pairs'][0]
        self.assertIsNone(pair['receipt'])
        self.assertTrue(pair['has_physical_scan'])
        self.assertTrue(pair['pending_cancelled'])

    @patch('app.pcr.now', return_value='2026-11-15T01:00:00+00:00')
    def test_final_disposition_is_explicit_and_reactivation_does_not_return_tube_automatically(self, _clock):
        imported(self.pcr, [('1199000188', 'IST_A', 'annule')])
        first = self.scan()
        removed = self.handle(action='removed')['pairs'][0]
        self.assertEqual(removed['tube_disposition']['state'], 'removed')
        self.assertEqual(removed['tube_disposition']['label'], 'Retiré du lot PCR')
        self.assertEqual(removed['tube_disposition']['actor'], 'bruno')
        self.assertEqual(removed['receipt'], first['pairs'][0]['receipt'])
        self.reactivate()
        active = self.pcr.history('1199000188')['pair']
        self.assertTrue(active['reactivated_request'])
        self.assertEqual(active['tube_disposition'], removed['tube_disposition'])
        rescanned = self.scan()['pairs'][0]
        self.assertEqual(rescanned['tube_disposition']['state'], 'removed')
        self.assertTrue(rescanned['reactivated_request'])
        kept = self.handle(action='kept', duplicate_action='same_tube', actor='charlotte')['pairs'][0]
        self.assertEqual(kept['tube_disposition']['state'], 'kept')
        self.assertEqual(kept['tube_disposition']['label'], 'À faire en PCR')
        self.assertEqual(kept['tube_disposition']['actor'], 'charlotte')
        self.assertFalse(kept['reactivated_request'])
        self.assertEqual(self.counts(), (0, 0))
        self.store.db.close()
        self.store = Store(self.temp.name)
        self.pcr = PCRService(self.store)
        self.assertEqual(self.pcr.history('1199000188')['pair']['tube_disposition'], kept['tube_disposition'])
        decisions = [e['detail']['disposition']['state'] for e in self.pcr.history('1199000188')['events'] if e['action']=='tube_handled']
        self.assertEqual(decisions, ['kept', 'removed'])
        self.scan()
        withdrawn = self.handle(duplicate_action='two_tubes_removed')['pairs'][0]
        self.assertEqual(withdrawn['tube_disposition']['state'], 'removed')
        self.assertEqual(withdrawn['tube_disposition']['tube_count'], 2)
        self.assertTrue(self.store.verify_audit())

    def test_generic_legacy_confirmation_never_invents_the_final_disposition(self):
        self.scan()
        pair = self.handle(action='managed')['pairs'][0]
        self.assertIsNone(pair['tube_disposition'])
        self.assertEqual(pair['last_handling']['label'], 'Prise en charge confirmée')
        self.assertFalse(pair['pending_error'])
        self.assertTrue(any(i['kind']=='error' and i['handling'] for i in pair['issues']))
