# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
"""Synthetic data only: comments, delays, alert switches, duplicate scans, relaunch mails, lab addresses."""
import json
import tempfile
import unittest

from app.pcr import PCRService
from app.storage import Conflict, Store
from app import pcr_numbering
from tests.test_pcr import configure, imported


class FollowUpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pcrlia-followup-')
        self.store = Store(self.temp.name)
        self.pcr = PCRService(self.store)
        configure(self.pcr)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def pair(self, sid, code='IST_A'):
        return next(p for p in self.pcr.listing()['pairs'] if p['sample_id'] == sid and p['request_code'] == code)

    def tube_scan(self, sid, token):
        return self.pcr.scan({'sample_id': sid, 'request_id': token}, 'technicien-test')

    def test_comments_are_append_only_with_author_and_time(self):
        imported(self.pcr, [('00000186', 'IST_A'), ('00000186', 'IST_B')])
        self.pcr.add_comment({'sample_id': '00000186', 'request_code': 'IST_A', 'text': 'Premier'}, 'tech-a')
        self.pcr.add_comment({'sample_id': '00000186', 'request_code': 'IST_A', 'text': 'Second'}, 'tech-b')
        self.pcr.add_comment({'sample_id': '00000186', 'request_code': '', 'text': 'Tube entier'}, 'tech-c')
        a, b = self.pair('00000186'), self.pair('00000186', 'IST_B')
        self.assertEqual([(c['text'], c['actor']) for c in a['comments']],
                         [('Premier', 'tech-a'), ('Second', 'tech-b'), ('Tube entier', 'tech-c')])
        self.assertTrue(all(c['at'] for c in a['comments']))
        # A comment on one analysis stays on it; tube-level comments show on every line.
        self.assertEqual([c['text'] for c in b['comments']], ['Tube entier'])
        with self.assertRaises(ValueError):
            self.pcr.add_comment({'sample_id': '00000186', 'request_code': 'IST_A', 'text': '  '}, 'tech-a')
        with self.assertRaises(KeyError):
            self.pcr.add_comment({'sample_id': '99999999', 'text': 'inconnu'}, 'tech-a')

    def test_comment_to_acknowledge_by_a_colleague(self):
        imported(self.pcr, [('00000186', 'IST_A')])
        self.pcr.add_comment({'sample_id': '00000186', 'request_code': 'IST_A', 'text': 'Info'}, 'tech-a')
        self.pcr.add_comment({'sample_id': '00000186', 'request_code': 'IST_A', 'text': 'À lire', 'to_ack': True}, 'tech-a')
        plain, flagged = self.pair('00000186')['comments']
        self.assertNotIn('to_ack', plain)
        self.assertEqual((flagged['to_ack'], flagged['ack']), (True, None))
        with self.assertRaises(KeyError):  # an ordinary comment has nothing to acknowledge
            self.pcr.ack_comment({'sample_id': '00000186', 'comment_id': plain['id']}, 'tech-b')
        self.pcr.ack_comment({'sample_id': '00000186', 'comment_id': flagged['id']}, 'tech-a')  # author allowed
        acked = self.pair('00000186')['comments'][1]
        self.assertEqual((acked['text'], acked['ack']['actor']), ('À lire', 'tech-a'))
        with self.assertRaises(Conflict):
            self.pcr.ack_comment({'sample_id': '00000186', 'comment_id': flagged['id']}, 'tech-c')

    def test_only_the_author_changes_the_comment_just_written(self):
        imported(self.pcr, [('00000186', 'IST_A')])
        first = self.pcr.add_comment({'sample_id': '00000186', 'request_code': 'IST_A', 'text': 'Premier'}, 'tech-a')[0]
        change = lambda cid, actor, **kw: self.pcr.change_comment({'sample_id': '00000186', 'comment_id': cid, **kw}, actor)
        with self.assertRaises(PermissionError):
            change(first['id'], 'tech-b', action='edit', text='Pas à moi')
        change(first['id'], 'tech-a', action='edit', text='Premier corrigé')
        change(first['id'], 'tech-a', action='to_ack')
        comment, = self.pair('00000186')['comments']
        self.assertEqual((comment['text'], comment['to_ack'], comment['history'][0]['text']), ('Premier corrigé', True, 'Premier'))
        second = self.pcr.add_comment({'sample_id': '00000186', 'request_code': 'IST_A', 'text': 'Second'}, 'tech-b')[-1]
        with self.assertRaises(Conflict):  # an earlier comment is final
            change(first['id'], 'tech-a', action='delete')
        change(second['id'], 'tech-b', action='delete')
        self.assertEqual([c['text'] for c in self.pair('00000186')['comments']], ['Premier corrigé'])
        events = [r['action'] for r in self.store.db.execute("SELECT action FROM pcr_events WHERE sample_id='00000186'")]
        self.assertEqual(events.count('comment_changed'), 3)

    def test_delays_are_optional_kept_on_rename_and_never_block(self):
        row = {k: v for k, v in next(c for c in self.pcr.codes() if c['code'] == 'IST_A').items() if k != 'suffix'}
        self.assertIsNone(row['receipt_delay_hours'])
        self.pcr.configure_code({**row, 'original_code': 'IST_A', 'receipt_delay_hours': '48', 'result_delay_hours': 24}, 'admin')
        row = {k: v for k, v in next(c for c in self.pcr.codes() if c['code'] == 'IST_A').items() if k != 'suffix'}
        self.assertEqual((row['receipt_delay_hours'], row['result_delay_hours'], row['label']), (48, 24, 'Panel IST fictif A'))
        # Absent fields keep the delays; empty values remove them.
        self.pcr.configure_code({**row, 'original_code': 'IST_A', 'label': 'Renommé'}, 'admin')
        row = {k: v for k, v in next(c for c in self.pcr.codes() if c['code'] == 'IST_A').items() if k != 'suffix'}
        self.assertEqual((row['receipt_delay_hours'], row['label']), (48, 'Renommé'))
        self.pcr.configure_code({**row, 'original_code': 'IST_A', 'receipt_delay_hours': '', 'result_delay_hours': ''}, 'admin')
        row = {k: v for k, v in next(c for c in self.pcr.codes() if c['code'] == 'IST_A').items() if k != 'suffix'}
        self.assertEqual((row['receipt_delay_hours'], row['result_delay_hours']), (None, None))
        with self.assertRaises(ValueError):
            self.pcr.configure_code({**row, 'original_code': 'IST_A', 'receipt_delay_hours': 0}, 'admin')
        imported(self.pcr, [('00000186', 'IST_A')])
        pair = self.pair('00000186')
        self.assertEqual(pair['requested_at'], '2026-11-12T00:00:00')

    def test_alert_switches_default_and_validation(self):
        config = self.pcr.config()
        # Result alert off at installation: it depends on MCA rules not yet created.
        self.assertEqual(config['delay_alerts'], {'receipt': True, 'result': False})
        after = self.pcr.configure({**config, 'delay_alerts': {'receipt': False, 'result': True}}, 'admin')
        self.assertEqual(after['delay_alerts'], {'receipt': False, 'result': True})
        with self.assertRaises(ValueError):
            self.pcr.configure({**self.pcr.config(), 'delay_alerts': {'receipt': 'oui', 'result': True}}, 'admin')

    def test_closed_days_default_and_validation(self):
        config = self.pcr.config()
        self.assertEqual(config['closed_days'], {'weekdays': [6], 'holidays_off': [], 'extra_dates': []})
        after = self.pcr.configure({**config, 'closed_days': {'weekdays': [6, 5, 6], 'holidays_off': ['pentecost_mon'],
                                                              'extra_dates': ['2026-12-26']}}, 'admin')
        self.assertEqual(after['closed_days'], {'weekdays': [5, 6], 'holidays_off': ['pentecost_mon'],
                                                'extra_dates': ['2026-12-26']})
        for bad in ({'weekdays': list(range(7)), 'holidays_off': [], 'extra_dates': []},
                    {'weekdays': [6], 'holidays_off': ['noel'], 'extra_dates': []},
                    {'weekdays': [6], 'holidays_off': [], 'extra_dates': ['2026-02-30']}):
            with self.assertRaises(ValueError):
                self.pcr.configure({**self.pcr.config(), 'closed_days': bad}, 'admin')

    def test_duplicate_scan_history_and_regularized_answer_keeps_tube(self):
        imported(self.pcr, [('00000186', 'IST_A')])
        self.tube_scan('00000186', 'scan-premier-000000001')
        result = self.tube_scan('00000186', 'scan-second-0000000001')
        self.assertTrue(result['duplicate'])
        self.assertEqual([s['actor'] for s in result['scans']], ['technicien-test', 'technicien-test'])
        self.assertEqual(len(self.pair('00000186')['scans']), 2)
        sample = self.pcr.sample('00000186')
        handled = self.pcr.handle({'sample_id': '00000186', 'revision': sample['revision'], 'confirmed': True,
                                   'duplicate_action': 'two_tubes_regularized', 'action': 'kept'}, 'tech-b')
        pair = handled['pairs'][0]
        self.assertEqual(pair['tube_disposition']['label'], 'À faire en PCR')
        self.assertFalse(pair['pending_issues'])

    def test_cancelled_request_real_or_by_mistake(self):
        imported(self.pcr, [('00000186', 'IST_A'), ('00000187', 'IST_A')])
        self.tube_scan('00000186', 'scan-annule-reel-00001')
        self.tube_scan('00000187', 'scan-annule-erreur-001')
        imported(self.pcr, [('00000186', 'IST_A', 'annule'), ('00000187', 'IST_A', 'annule')])
        handle = lambda sid, action: self.pcr.handle({'sample_id': sid, 'revision': self.pcr.sample(sid)['revision'],
                                                      'confirmed': True, 'action': action}, 'tech-b')
        real = handle('00000186', 'cancel_confirmed')['pairs'][0]
        mistake = handle('00000187', 'cancel_error')['pairs'][0]
        self.assertEqual(real['tube_disposition']['label'], 'Retiré du lot PCR')
        self.assertEqual(mistake['tube_disposition']['label'], 'À faire en PCR')
        self.assertIn('prescription', mistake['last_handling']['label'])
        self.assertFalse(real['pending_issues'] or mistake['pending_issues'])

    def test_duplicate_with_cancelled_analysis_is_still_removed(self):
        imported(self.pcr, [('00000186', 'IST_A')])
        self.tube_scan('00000186', 'scan-premier-000000001')
        imported(self.pcr, [('00000186', 'IST_A', 'annule')])
        self.tube_scan('00000186', 'scan-second-0000000001')
        sample = self.pcr.sample('00000186')
        handled = self.pcr.handle({'sample_id': '00000186', 'revision': sample['revision'], 'confirmed': True,
                                   'duplicate_action': 'two_tubes_regularized', 'action': 'removed'}, 'tech-b')
        self.assertEqual(handled['pairs'][0]['tube_disposition']['label'], 'Retiré du lot PCR')

    def test_mail_generation_is_traced_without_comment(self):
        imported(self.pcr, [('00000186', 'IST_A')])
        self.pcr.log_mail({'to': 'labo@exemple.fr', 'pairs': [{'sample_id': '00000186', 'request_code': 'IST_A'}]}, 'tech-a')
        self.pcr.log_mail({'to': 'labo@exemple.fr', 'pairs': [{'sample_id': '00000186', 'request_code': 'IST_A'}]}, 'tech-b')
        pair = self.pair('00000186')
        self.assertEqual([m['actor'] for m in pair['mails']], ['tech-a', 'tech-b'])
        self.assertEqual(pair['comments'], [])
        events = [r['action'] for r in self.store.db.execute("SELECT action FROM pcr_events WHERE sample_id='00000186'")]
        self.assertEqual(events.count('mail_generated'), 2)
        with self.assertRaises(ValueError):
            self.pcr.log_mail({'to': 'pas-une-adresse', 'pairs': [{'sample_id': '00000186', 'request_code': 'IST_A'}]}, 'tech-a')

    def test_lab_addresses_default_and_never_override_saved_ones(self):
        numbering = self.pcr.config()['numbering']
        emails = {f: {r['prefix']: r['email'] for r in numbering[f]} for f in ('reseau_a', 'reseau_b', 'reseau_c')}
        self.assertEqual(emails['reseau_a']['11'], 'site11@reseau-a.exemple.invalid')
        self.assertEqual(emails['reseau_a']['20'], '')
        self.assertEqual(emails['reseau_b']['95'], 'secretariat95@reseau-b.exemple.invalid')
        self.assertEqual(emails['reseau_c']['C'], 'site-c@reseau-c.exemple.invalid')
        self.assertEqual(emails['reseau_c']['K'], '')
        # Rows saved by an older version (no address key) get the directory address at read time only.
        old = pcr_numbering.defaults()
        for family in ('reseau_a', 'reseau_b', 'reseau_c'):
            for row in old[family]:
                row.pop('email')
        old['reseau_a'][0]['email'] = ''
        raw = json.loads(self.store.db.execute('SELECT payload FROM pcr_config WHERE id=1').fetchone()[0])
        self.store.db.execute('UPDATE pcr_config SET payload=? WHERE id=1', (json.dumps({**raw, 'numbering': old}),))
        self.store.db.commit()
        numbering = self.pcr.config()['numbering']
        self.assertEqual(numbering['reseau_a'][0]['email'], '')  # explicitly emptied: kept empty
        self.assertEqual(numbering['reseau_a'][1]['email'], 'site12@reseau-a.exemple.invalid')
        stored = json.loads(self.store.db.execute('SELECT payload FROM pcr_config WHERE id=1').fetchone()[0])
        self.assertNotIn('email', stored['numbering']['reseau_a'][1])
        with self.assertRaises(ValueError):
            bad = pcr_numbering.defaults()
            bad['reseau_c'][0]['email'] = 'invalide'
            pcr_numbering.validate(bad)
        # The barcode carries its laboratory address for relaunch mails.
        imported(self.pcr, [('1299000188', 'IST_A')])
        self.assertEqual(self.pair('1299000188', 'IST_A')['lab_email'], 'site12@reseau-a.exemple.invalid')


if __name__ == '__main__':
    unittest.main()
