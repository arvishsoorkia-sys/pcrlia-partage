# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
"""Synthetic messages only: optional MCA Â« analyse faite Â» codes shown as a green dot."""
import unittest

from tests import test_pcr_mca as fixtures
from tests.test_pcr_mca import order
from tests.test_phadia_orders import message


class AnalysisDoneTests(unittest.TestCase):
    # Same isolated loopback fixture, without inheriting its test methods.
    setUp = fixtures.ReceiverTests.setUp
    tearDown = fixtures.ReceiverTests.tearDown
    connect = fixtures.ReceiverTests.connect
    begin = fixtures.ReceiverTests.begin
    transmit = fixtures.ReceiverTests.transmit

    def set_done(self, code='CTNG1', done='CTNG1_F'):
        row = next(c for c in self.pcr.codes() if c['code'] == code)
        return self.pcr.configure_code({**row, 'original_code': code, 'done_code': done}, 'admin-test')

    def pairs(self):
        return self.pcr.listing()['pairs']

    def test_nothing_configured_changes_nothing(self):
        self.assertTrue(all(c['done_code'] == '' for c in self.pcr.codes()))
        self.transmit(order('20261115084219'))
        pair, = self.pairs()
        self.assertEqual((pair['done_code'], pair['analysis_done']), ('', None))
        # An unconfigured announcement stays an unknown code to examine, as before.
        self.transmit(order('20261115090000', code='CTNG1_F'), self.clients[0])
        self.assertEqual(self.receiver.status()['pending'], 1)
        self.assertEqual(len(self.pairs()), 1)

    def test_done_code_turns_the_exact_pair_green_without_creating_an_expected_tube(self):
        self.set_done()
        client = self.transmit(order('20261115084219'))
        self.assertEqual(self.pairs()[0]['done_code'], 'CTNG1_F')
        self.assertIsNone(self.pairs()[0]['analysis_done'])
        self.transmit(order('20261115153000', code='CTNG1_F'), client)
        pair, = self.pairs()
        self.assertEqual(pair['request_code'], 'CTNG1')
        self.assertEqual(pair['analysis_done'], {'at': '2026-11-15T15:30:00', 'code': 'CTNG1_F'})
        self.assertEqual(pair['status'], 'expected')
        self.assertEqual((pair['date_demande'], pair['heure_demande']), ('2026-11-15', '08:42'))
        self.assertEqual(self.receiver.status()['pending'], 0)
        events = [r['action'] for r in self.store.db.execute('SELECT action FROM pcr_events ORDER BY seq')]
        self.assertEqual(events[-1], 'analysis_done_mca')

    def test_withdrawn_announcement_and_older_message(self):
        self.set_done()
        client = self.transmit(order('20261115084219'))
        self.transmit(order('20261115153000', code='CTNG1_F'), client)
        self.transmit(order('20261115160000', code='CTNG1_F', action='C'), client)
        self.assertIsNone(self.pairs()[0]['analysis_done'])
        # A late, older message never overwrites the latest known state.
        self.transmit(order('20261115155900', code='CTNG1_F'), client)
        self.assertIsNone(self.pairs()[0]['analysis_done'])
        self.transmit(order('20261115170000', code='CTNG1_F'), client)
        self.assertEqual(self.pairs()[0]['analysis_done']['at'], '2026-11-15T17:00:00')

    def test_announcement_before_the_order_and_only_on_the_same_sample(self):
        self.set_done()
        client = self.transmit(order('20261115080000', code='CTNG1_F'))
        self.assertEqual(self.pairs(), [])
        payload = message('20261115084219', [('1199000188', 'CTNG1', 'N'), ('1299000188', 'CTNG1', 'N')])
        self.transmit(payload.replace(b'\r\n', b'\r') + b'\r', client)
        done = {p['sample_id']: p['analysis_done'] for p in self.pairs()}
        self.assertEqual(done['1199000188']['at'], '2026-11-15T08:00:00')
        self.assertIsNone(done['1299000188'])

    def test_scan_reception_and_cancellation_are_untouched(self):
        self.set_done()
        client = self.transmit(order('20261115084219'))
        self.pcr.scan_tube({'sample_id': '1199000188', 'request_id': 'fictitious-done-scan'}, 'tech-test')
        before = self.pairs()[0]
        self.transmit(order('20261115153000', code='CTNG1_F'), client)
        after = self.pairs()[0]
        for key in ('status', 'state', 'receipt', 'scanned_at', 'issues', 'pending_issues'):
            self.assertEqual(after.get(key), before.get(key), key)
        self.assertIsNotNone(after['analysis_done'])

    def test_catalog_rules_are_optional_and_unambiguous(self):
        self.set_done()
        with self.assertRaises(ValueError):
            self.set_done('CTNG2', 'CTNG1_F')       # already used by another analysis
        with self.assertRaises(ValueError):
            self.set_done('CTNG2', 'CTNG3')         # an analysis code
        with self.assertRaises(ValueError):
            self.set_done('CTNG2', 'CTNG2')         # itself
        with self.assertRaises(ValueError):
            self.pcr.configure({**self.pcr.config(), 'ignored_mca_codes': ['CTNG1_F']}, 'admin-test')
        with self.assertRaises(ValueError):
            self.pcr.configure_code({'code': 'CTNG1_F', 'label': 'Fictive', 'group_key': 'ist',
                                     'active': True, 'revision': 0, 'suffix': '88'}, 'admin-test')
        # Saving an analysis without the field keeps the value; an empty value removes it.
        row = next(c for c in self.pcr.codes() if c['code'] == 'CTNG1')
        row.pop('done_code')
        self.pcr.configure_code({**row, 'original_code': 'CTNG1', 'label': 'CT/NG 1'}, 'admin-test')
        self.assertEqual(next(c for c in self.pcr.codes() if c['code'] == 'CTNG1')['done_code'], 'CTNG1_F')
        self.set_done('CTNG1', '')
        self.assertEqual(self.pcr.config()['done_codes'], {})

    def test_renamed_analysis_keeps_its_done_code(self):
        self.set_done()
        row = next(c for c in self.pcr.codes() if c['code'] == 'CTNG1')
        self.pcr.configure_code({**row, 'code': 'CTNG1B', 'original_code': 'CTNG1'}, 'admin-test')
        self.assertEqual(self.pcr.config()['done_codes'], {'CTNG1B': 'CTNG1_F'})


if __name__ == '__main__':
    unittest.main()
