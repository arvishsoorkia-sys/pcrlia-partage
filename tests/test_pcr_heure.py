# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
"""Synthetic messages only: time of the first MCA transmission shown in Non réceptionnés."""
import json
import unittest

from app.pcr import PCRService
from tests import test_pcr_mca as fixtures
from tests.test_pcr_mca import order


class FirstTransmissionTimeTests(unittest.TestCase):
    # Same isolated loopback fixture, without inheriting its test methods.
    setUp = fixtures.ReceiverTests.setUp
    tearDown = fixtures.ReceiverTests.tearDown
    connect = fixtures.ReceiverTests.connect
    begin = fixtures.ReceiverTests.begin
    transmit = fixtures.ReceiverTests.transmit

    def pair(self, service=None):
        return (service or self.pcr).listing()['pairs'][0]

    def test_time_of_first_message_is_listed(self):
        self.transmit(order('20261115084219'))
        pair = self.pair()
        self.assertEqual(pair['heure_demande'], '08:42')
        self.assertEqual(pair['date_demande'], '2026-11-15')
        self.assertEqual(pair['mca_first_at'], '2026-11-15T08:42:19')

    def test_cancellation_and_reactivation_keep_the_first_time(self):
        client = self.transmit(order('20261115084219'))
        self.transmit(order('20261115090500', action='C'), client)
        self.transmit(order('20261116143000'), client)
        pair = self.pair()
        self.assertEqual(pair['status'], 'expected')
        self.assertEqual(pair['mca_at'], '2026-11-16T14:30:00')
        self.assertEqual((pair['date_demande'], pair['heure_demande']), ('2026-11-15', '08:42'))

    def test_orders_received_before_this_version_use_retained_history(self):
        client = self.transmit(order('20261115084219'))
        self.transmit(order('20261115090500', action='C'), client)
        self.transmit(order('20261115101500'), client)
        # Same stored shape as 2026.11.16.2: the first time was not kept on the order.
        with self.store.lock, self.store.db:
            sid, payload = self.store.db.execute('SELECT sample_id,payload FROM pcr_samples').fetchone()
            sample = json.loads(payload)
            for stored in sample['orders'].values():
                del stored['mca_first_at']
            self.store.db.execute('UPDATE pcr_samples SET payload=? WHERE sample_id=?', (json.dumps(sample), sid))
        before = list(self.store.db.iterdump())
        pair = self.pair(PCRService(self.store))
        self.assertEqual(pair['heure_demande'], '08:42')
        self.assertNotIn('mca_first_at', pair)
        self.assertEqual(before, list(self.store.db.iterdump()))

    def test_manual_order_without_mca_message_has_no_invented_time(self):
        self.transmit(order('20261115084219'))
        with self.store.lock, self.store.db:
            sid, payload = self.store.db.execute('SELECT sample_id,payload FROM pcr_samples').fetchone()
            sample = json.loads(payload)
            for stored in sample['orders'].values():
                for key in ('mca_first_at', 'mca_at', 'date_remontee_mca'):
                    stored.pop(key, None)
            self.store.db.execute('UPDATE pcr_samples SET payload=? WHERE sample_id=?', (json.dumps(sample), sid))
        self.assertEqual(self.pair(PCRService(self.store))['heure_demande'], '')


if __name__ == '__main__':
    unittest.main()
