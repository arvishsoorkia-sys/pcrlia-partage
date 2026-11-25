# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
"""Complete messages retained across explicit catalog changes."""
import unittest

from tests import test_receipt_review as fixture


class ReceiverReconfigurationReviewTests(unittest.TestCase):
    setUp = fixture.ReceiptReviewTests.setUp
    tearDown = fixture.ReceiptReviewTests.tearDown
    add = fixture.ReceiptReviewTests.add
    ignore = fixture.ReceiptReviewTests.ignore
    receive = fixture.ReceiptReviewTests.receive

    def active(self, code, value):
        item = self.pcr.code(code, active=False)
        self.pcr.configure_code({**item, 'active': value}, 'review-admin')

    def test_explicitly_ignoring_a_disabled_catalog_code_resolves_its_retained_message(self):
        self.add('SC2FabR')
        self.active('SC2FabR', False)
        self.receive([('1199000188', 'SC2FabR', 'N')])
        self.assertEqual(self.receiver.status()['pending'], 1)
        self.assertIn('désactivé', self.receiver.status()['issues'][0]['error'])
        self.ignore(['SC2FabR'])
        self.assertEqual(self.receiver.status()['pending'], 0)
        self.assertEqual(self.receiver.status()['counts'].get('ignored'), 1)
        self.assertEqual(self.pcr.listing()['pairs'], [])

    def test_reactivating_a_disabled_code_allows_explicit_reinclusion(self):
        self.add('SC2FabR')
        self.active('SC2FabR', False)
        self.receive([('1199000188', 'SC2FabR', 'N')])
        self.active('SC2FabR', True)
        result = self.receiver.retry('review-admin', reinclude_only=True)
        self.assertEqual(result['included_analyses'], 1)
        self.assertEqual(result['pending'], 0)
        self.assertIn('SC2FabR', self.pcr.sample('1199000188')['orders'])


if __name__ == '__main__':
    unittest.main()
