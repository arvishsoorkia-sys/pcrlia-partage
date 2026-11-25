# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
"""Synthetic messages only; configured exclusions and recovery over loopback."""
import copy
import json
from datetime import datetime, timedelta, timezone
from app.phadia_orders import decode_orders
from app.pcr_mca import apply_orders
from tests import test_pcr_mca as fixtures
from tests.test_pcr_mca import order
from tests.test_phadia_orders import message


class IgnoredCodesTests(fixtures.ReceiverTests):
    # Reuse the isolated fixture without inheriting all its test methods below.
    def ignore(self, codes):
        self.pcr.configure({**self.pcr.config(), 'ignored_mca_codes': codes}, 'admin-test')
        self.receiver.recheck_ignored(self.pcr)

    def add_code(self, code):
        self.pcr.configure_code({'code': code, 'label': 'Analyse fictive', 'group_key': 'covid',
                                 'active': True, 'revision': 0, 'suffix': '85'}, 'admin-test')

    def test_only_configured_codes_are_ignored_without_samples(self):
        self.ignore(['SC2FabR'])
        client = self.transmit(order(code='SC2FabR', sid='1199000185'))
        self.transmit(order('20261115120100', code='SC2FabR', action='C', sid='1199000185'), client)
        self.assertEqual(self.pcr.listing()['pairs'], [])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM pcr_samples').fetchone()[0], 0)
        self.assertEqual(self.receiver.status()['pending'], 0)
        self.assertEqual(self.receiver.status()['counts']['ignored'], 2)
        self.assertIsNone(self.receiver.status()['last_received'])
        self.transmit(order(code='NEW_UNKNOWN'), client)
        self.assertEqual(self.receiver.status()['pending'], 1)

    def test_existing_alert_clears_when_code_is_explicitly_ignored(self):
        self.transmit(order(code='SC2FabR', sid='1199000185'))
        self.assertEqual(self.receiver.status()['pending'], 1)
        self.ignore(['SC2FabR'])
        self.assertEqual(self.receiver.status()['pending'], 0)
        self.assertEqual(self.pcr.listing()['pairs'], [])
        self.assertEqual(self.receiver.status()['issues'], [])

    def test_mixed_message_keeps_known_analysis_then_recovers_ignored_without_duplicates(self):
        self.ignore(['SC2FabR'])
        payload = message(orders=[('1199000188','CTNG1','N'),('1199000185','SC2FabR','N')]).replace(b'\r\n', b'\r') + b'\r'
        self.transmit(payload)
        self.pcr.scan_tube({'sample_id':'1199000188','request_id':'known-tube-preserved'},'tech-test')
        before = copy.deepcopy(self.pcr.sample('1199000188'))
        self.assertEqual(len(self.pcr.listing()['pairs']), 1)
        self.ignore([])
        self.add_code('SC2FabR')
        result = self.receiver.retry('admin-test', reinclude_only=True)
        self.assertEqual(result['included_analyses'], 1)
        self.assertEqual(self.pcr.sample('1199000188'), before)
        restored = self.pcr.sample('1199000185')
        self.assertEqual(restored['orders']['SC2FabR']['date_remontee_mca'], '2026-11-14')
        self.assertIsNone(restored['orders']['SC2FabR']['receipt'])
        self.assertEqual(len(self.pcr.listing()['pairs']), 2)
        revision = self.pcr.revision()
        self.assertEqual(self.receiver.retry('admin-test', reinclude_only=True)['included_analyses'], 0)
        self.assertEqual(self.pcr.revision(), revision)
        self.assertTrue(self.store.verify_audit())

    def test_reinclude_previously_ignored_messages_preserves_cancellation(self):
        self.ignore(['SC2FabR'])
        client = self.transmit(order('20261115120000',code='SC2FabR',sid='1199000185'))
        self.transmit(order('20261116120000',code='SC2FabR',action='C',sid='1199000185'),client)
        self.ignore([])
        self.add_code('SC2FabR')
        self.assertEqual(self.receiver.retry('admin-test', reinclude_only=True)['included_analyses'], 1)
        pair = self.pcr.listing()['pairs'][0]
        self.assertEqual((pair['state'],pair['date_demande'],pair['pending_cancelled']), ('annule','2026-11-15',False))

    def test_active_analysis_cannot_be_ignored_or_created_as_ignored(self):
        with self.assertRaisesRegex(ValueError, 'active'):
            self.ignore(['CTNG1'])
        self.ignore(['SC2FabR'])
        with self.assertRaisesRegex(ValueError, 'ignorer'):
            self.add_code('SC2FabR')

    def test_unrelated_protocol_error_remains_pending(self):
        self.transmit(order().replace(b'L|1|F', b'R|1|^^^CHLAS|12.34\rL|1|F'))
        self.ignore(['SC2FabR'])
        self.assertEqual(self.receiver.retry('admin-test', reinclude_only=True)['reexamined_messages'], 0)
        self.assertEqual(self.receiver.status()['pending'], 1)

    def test_ignored_payload_retention_and_recovery_limit(self):
        self.ignore(['SC2FabR'])
        self.transmit(order(code='SC2FabR'))
        clock = datetime.now(timezone.utc)
        with self.store.lock, self.store.db:
            self.store.db.execute('UPDATE pcr_mca_inbox SET at=?', ((clock-timedelta(days=91)).isoformat(),))
        self.assertEqual(self.receiver.purge_received(clock), 1)
        row = self.store.db.execute('SELECT payload,state FROM pcr_mca_inbox').fetchone()
        self.assertEqual(tuple(row), (b'', 'ignored_purged'))
        self.ignore([])
        self.add_code('SC2FabR')
        self.assertEqual(self.receiver.retry('admin-test', reinclude_only=True)['included_analyses'], 0)
        self.assertIsNone(self.receiver.status()['last_received'])


# Fixture reuse should not duplicate the existing receiver suite.
for _name in dir(fixtures.ReceiverTests):
    if _name.startswith('test_') and _name not in IgnoredCodesTests.__dict__:
        setattr(IgnoredCodesTests, _name, None)
