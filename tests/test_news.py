# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
"""« Nouveautés » window: per user, per space, up to the installed version."""
import tempfile
import unittest
from unittest import mock

from app import news
from app.storage import Store

SAMPLE = [
    ('2026.01.10.1', [('pcrlia', 'Première nouveauté.')]),
    ('2026.01.20.1', [('pcrlia', 'Deuxième nouveauté.'), ('admin', 'Ligne réservée aux administrateurs.')]),
    (news.NEXT, [('commun', 'En préparation.')]),
]


class NewsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='news-')
        self.store = Store(self.temp.name)
        self.patch = mock.patch.object(news, 'NEWS', SAMPLE)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.store.db.close()
        self.temp.cleanup()

    def test_never_seen_shows_installed_and_earlier_only_for_own_spaces(self):
        tech = {'username': 'tech', 'role': 'technician', 'apps': ['pcrlia']}
        self.assertEqual([e['version'] for e in news.entries(tech, '2026.01.10.1', None)], ['2026.01.10.1'])
        entries = news.entries(tech, '2026.01.20.1', None)
        self.assertEqual([e['version'] for e in entries], ['2026.01.10.1', '2026.01.20.1'])
        self.assertNotIn('Ligne réservée aux administrateurs.', entries[-1]['items'])
        admin = {'username': 'admin', 'role': 'admin', 'apps': ['pcrlia']}
        self.assertIn('Ligne réservée aux administrateurs.', news.entries(admin, '2026.01.20.1', None)[-1]['items'])

    def test_seen_once_then_hidden_until_next_version(self):
        user = {'username': 'tech', 'role': 'technician', 'apps': ['pcrlia']}
        self.assertTrue(news.status(self.store, user, '2026.01.20.1')['entries'])
        news.mark_seen(self.store, user, '2026.01.20.1')
        self.assertEqual(news.status(self.store, user, '2026.01.20.1')['entries'], [])
        other = {'username': 'autre', 'role': 'technician', 'apps': ['pcrlia']}
        self.assertTrue(news.status(self.store, other, '2026.01.20.1')['entries'])


if __name__ == '__main__':
    unittest.main()
