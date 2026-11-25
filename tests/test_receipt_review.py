# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
"""Independent review cases: no sockets, no installed data, no MCA access."""
import copy
import json
import tempfile
import unittest

from app.pcr import PCRService
from app.pcr_mca import receiver_for
from app.storage import Store
from tests.test_phadia_orders import message


class ReceiptReviewTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='pcr-receipt-review-')
        self.store = Store(self.folder.name)
        self.pcr = PCRService(self.store)
        self.receiver = receiver_for(self.store)
        self.receiver.service = self.pcr
        self.sequence = 0
        with self.store.lock, self.store.db:
            self.store.db.execute('''CREATE TABLE pcr_mca_inbox(
                id TEXT PRIMARY KEY,at TEXT NOT NULL,peer TEXT NOT NULL,
                payload BLOB NOT NULL,encoding TEXT NOT NULL,state TEXT NOT NULL,error TEXT NOT NULL)''')

    def tearDown(self):
        self.receiver.stop()
        self.store.db.close()
        self.folder.cleanup()

    def ignore(self, codes):
        self.pcr.configure({**self.pcr.config(), 'ignored_mca_codes': codes}, 'review-admin')
        self.receiver.recheck_ignored(self.pcr)

    def add(self, code):
        self.pcr.configure_code({'code': code, 'label': 'Analyse fictive ' + code,
                                'group_key': 'ist', 'active': True,
                                'revision': 0, 'suffix': '88'}, 'review-admin')

    def receive(self, orders, stamp='20261115120000'):
        self.sequence += 1
        rid = 'review-' + str(self.sequence)
        payload = message(stamp, orders)
        with self.store.lock, self.store.db:
            self.store.db.execute('INSERT INTO pcr_mca_inbox VALUES(?,?,?,?,?,?,?)',
                                 (rid, '2026-11-15T12:00:00+00:00', '127.0.0.1',
                                  payload, 'ascii', 'ready', ''))
        self.receiver._apply(rid, payload, 'ascii')
        return rid

    def test_unrelated_unknown_codes_do_not_starve_the_new_known_code(self):
        for number in range(100):
            self.receive([(f'1199{number:04d}88', 'UNRELATED', 'N')])
        self.receive([('1199999988', 'NEW_PANEL', 'N')])
        self.add('NEW_PANEL')
        result = self.receiver.retry('review-admin', reinclude_only=True)
        self.assertEqual(result['included_analyses'], 1)
        self.assertIn('NEW_PANEL', self.pcr.sample('1199999988')['orders'])
        self.assertEqual(self.receiver.status()['pending'], 100)

    def test_more_than_one_hundred_ignored_messages_are_all_reincluded_or_report_remaining(self):
        self.ignore(['NEW_PANEL'])
        for number in range(101):
            self.receive([(f'1199{number:04d}88', 'NEW_PANEL', 'N')])
        self.ignore([])
        self.add('NEW_PANEL')
        result = self.receiver.retry('review-admin', reinclude_only=True)
        # A partial batch must not silently report zero outstanding work.
        self.assertEqual(result['included_analyses'] + result.get('remaining_reinclude', 0), 101)
        if result.get('remaining_reinclude'):
            self.assertEqual(result['remaining_reinclude'], 101-result['included_analyses'])
            tail = self.receiver.retry('review-admin', reinclude_only=True)
            self.assertEqual(result['included_analyses'] + tail['included_analyses'], 101)
            self.assertEqual(tail.get('remaining_reinclude',0), 0)
            self.assertEqual(len(self.pcr.listing()['pairs']), 101)
        else:
            self.assertEqual(len(self.pcr.listing()['pairs']), 101)

    def test_old_ignored_addition_does_not_undo_newer_cancellation_or_existing_scan(self):
        sid = '1199000188'
        self.ignore(['NEW_PANEL'])
        self.receive([(sid, 'CTNG1', 'N'), (sid, 'NEW_PANEL', 'N')], '20261114120000')
        self.pcr.scan_tube({'sample_id':sid,'request_id':'review-physical-scan-001'},'review-tech')
        self.ignore([])
        self.add('NEW_PANEL')
        self.receive([(sid, 'NEW_PANEL', 'C')], '20261116120000')
        before = copy.deepcopy(self.pcr.sample(sid))
        self.receiver.retry('review-admin', reinclude_only=True)
        self.assertEqual(self.pcr.sample(sid), before)
        self.assertEqual(before['orders']['NEW_PANEL']['state'], 'annule')
        self.assertEqual(before['orders']['CTNG1']['date_remontee_mca'], '2026-11-14')
        self.assertTrue(before['tube_receipt'])

    def test_mixed_message_can_recover_two_ignored_codes_in_separate_steps(self):
        sid='1199000188'
        self.ignore(['NEW_A','NEW_B'])
        rid=self.receive([(sid,'CTNG1','N'),(sid,'NEW_A','N'),(sid,'NEW_B','N')])
        original=copy.deepcopy(self.pcr.sample(sid)['orders']['CTNG1'])
        self.ignore(['NEW_B']);self.add('NEW_A')
        self.assertEqual(self.receiver.retry('review-admin', reinclude_only=True)['included_analyses'],1)
        self.assertEqual(set(self.pcr.sample(sid)['orders']), {'CTNG1','NEW_A'})
        self.assertEqual(json.loads(self.store.db.execute('SELECT summary FROM pcr_imports').fetchone()[0])['ignored_code_values'],['NEW_B'])
        self.ignore([]);self.add('NEW_B')
        self.assertEqual(self.receiver.retry('review-admin', reinclude_only=True)['included_analyses'],1)
        self.assertEqual(set(self.pcr.sample(sid)['orders']), {'CTNG1','NEW_A','NEW_B'})
        self.assertEqual(self.pcr.sample(sid)['orders']['CTNG1'], original)
        before=copy.deepcopy(self.pcr.sample(sid))
        self.assertEqual(self.receiver.retry('review-admin', reinclude_only=True)['included_analyses'],0)
        self.assertEqual(self.pcr.sample(sid), before)
        self.assertEqual(self.store.db.execute('SELECT state FROM pcr_mca_inbox WHERE id=?',(rid,)).fetchone()[0], 'applied')
        self.assertTrue(self.store.verify_audit())

    def test_conflicting_ignored_events_do_not_partially_change_known_orders(self):
        sid='1199000188'
        self.ignore(['NEW_PANEL'])
        self.receive([(sid,'CTNG1','N'),(sid,'NEW_PANEL','N'),(sid,'NEW_PANEL','C')])
        before=copy.deepcopy(self.pcr.sample(sid))
        self.ignore([]);self.add('NEW_PANEL')
        result=self.receiver.retry('review-admin', reinclude_only=True)
        self.assertEqual(result['included_analyses'],0)
        self.assertEqual(self.pcr.sample(sid),before)
        self.assertEqual(self.receiver.status()['pending'],1)
        self.assertIn('contradictoires',self.receiver.status()['issues'][0]['error'])

    def test_ignore_is_exact_case_and_does_not_hide_other_unknown_codes(self):
        self.ignore(['SC2FabR'])
        self.receive([('1199000188','SC2FabR','N')])
        self.receive([('1199000288','SC2FABR','N')])
        self.assertEqual(self.receiver.status()['counts'].get('ignored'),1)
        self.assertEqual(self.receiver.status()['pending'],1)
        self.assertEqual(self.pcr.listing()['pairs'],[])


if __name__ == '__main__':
    unittest.main()
