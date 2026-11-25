# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
"""Names and settings lifecycle, isolated fictitious accounts and orders."""
import tempfile
import unittest
from app import accounts
from app.pcr import PCRService
from app.storage import Store, Conflict
from app.pcr_mca import apply_orders
from app.phadia_orders import decode_orders
from tests.test_pcr_mca import order


class SettingsAccountsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pcr-settings-')
        self.store = Store(self.temp.name)
        self.store.setup(self.store.setup_token, 'admin-test', 'xx')
        self.pcr = PCRService(self.store)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_user_names_shared_preserve_login_and_audit_identifiers(self):
        self.store.create_user('tech-test','ab','technician','admin-test',apps=['pcrlia'],
                               first_name='Élodie',last_name='Durand-Martin')
        token, user = self.store.login('tech-test','ab')
        self.assertEqual(user['display_name'],'Élodie Durand-Martin')
        row = tuple(self.store.db.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone())
        accounts.update(self.store,{**user,'first_name':'Anaïs','last_name':'Martin'},'admin-test')
        self.assertIsNone(self.store.session(token))
        _, updated = self.store.login('tech-test','ab')
        self.assertEqual(updated['display_name'],'Anaïs Martin')
        self.assertEqual(updated['apps'],['pcrlia'])
        self.assertEqual(tuple(self.store.db.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone()),row)
        self.assertTrue(self.store.verify_audit())

    def test_legacy_names_and_omitted_update_fields(self):
        _, user = self.store.login('admin-test','xx')
        self.assertEqual(user['display_name'],'admin-test')
        accounts.update(self.store,{**user,'first_name':'Camille','last_name':'Exemple'},'admin-test')
        _, user = self.store.login('admin-test','xx')
        user.pop('first_name');user.pop('last_name')
        accounts.update(self.store,user,'admin-test')
        self.assertEqual(self.store.login('admin-test','xx')[1]['display_name'],'Camille Exemple')

    def test_invalid_names_atomic_and_revision_guard(self):
        before = list(self.store.db.iterdump())
        for value in (None, 123, 'X'*81, 'Nom\nAutre'):
            with self.assertRaises(ValueError):
                self.store.create_user('invalid','ab','technician','admin-test',first_name=value)
        self.assertEqual(list(self.store.db.iterdump()),before)
        _, user = self.store.login('admin-test','xx')
        with self.assertRaises(Conflict):
            accounts.update(self.store,{**user,'revision':0},'admin-test')

    def test_unused_code_deleted_used_code_preserved(self):
        self.pcr.configure_code({'code':'UNUSED','label':'À retirer','group_key':'ist','active':True,'revision':0,'suffix':'88'},'admin-test')
        with self.assertRaises(Conflict):
            self.pcr.delete_code({'code':'UNUSED','revision':0},'admin-test')
        self.pcr.delete_code({'code':'UNUSED','revision':1},'admin-test')
        self.assertNotIn('UNUSED',[c['code'] for c in self.pcr.codes()])
        with self.store.lock,self.store.db:
            apply_orders(self.pcr,decode_orders(order()))
        before=self.pcr.sample('1199000188')
        with self.assertRaisesRegex(ValueError,'historique'):
            self.pcr.delete_code({'code':'CTNG1','revision':1},'admin-test')
        self.assertEqual(self.pcr.sample('1199000188'),before)
        self.assertTrue(self.store.verify_audit())
