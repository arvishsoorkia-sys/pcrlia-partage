# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
"""Synthetic laboratory numbering; no MCA or real patient data."""
import csv
import io
import tempfile
import unittest
from copy import deepcopy

from app import pcr_numbering as numbering
from app.pcr import COLUMNS, PATIENT_COLUMNS, PCRService, ERROR_SITE
from app.storage import Store, Conflict
from tests.test_pcr import configure


class NumberingTests(unittest.TestCase):
    def setUp(self):
        self.rules = numbering.defaults()

    def test_confirmed_examples_and_leading_zero_round_trip(self):
        for dossier, barcode, site, detail in [
            ('29005797', '2900579788', 'Z-LABO', 'Z-LABO'),
            ('6111495378', '611149537888', 'Réseau B', 'Site B95'),
            ('M261114204', '1326111420488', 'Réseau C', 'Site C-M'),
            ('C261114901', '0326111490188', 'Réseau C', 'Site C-C'),
            ('DA261114902', '1826111490288', 'Réseau C', 'Site C-DA'),
            ('S261114903', '3126111490388', 'Réseau C', 'Site C-S'),
        ]:
            with self.subTest(dossier=dossier):
                self.assertEqual(numbering.sample_from_dossier(dossier, '88'), barcode)
                actual = numbering.classify(barcode, self.rules)
                self.assertEqual((actual['dossier'], actual['site'], actual['lab_detail']), (dossier, site, detail))

    def test_every_prefix_round_trips_without_ambiguity(self):
        for prefix, label in numbering.RESEAU_A.items():
            dossier = prefix + '990001'
            self.assertEqual(numbering.classify(dossier+'86', self.rules)['site'], label)
        for prefix, label in numbering.RESEAU_B.items():
            dossier = '61114'+prefix+'901'
            self.assertEqual(numbering.classify(dossier+'87', self.rules)['lab_detail'], label)
        for prefix, barcode in numbering.RESEAU_C.items():
            sample = numbering.sample_from_dossier(prefix+'261114901', '44')
            self.assertEqual(sample, barcode+'26111490144')
            self.assertEqual(numbering.classify(sample, self.rules)['dossier'], prefix+'261114901')

    def test_same_prefix_different_lengths_never_conflates_networks(self):
        self.assertEqual(numbering.classify('1399000188', self.rules)['site'], 'Site 13')
        self.assertEqual(numbering.classify('1326111490188', self.rules)['site'], 'Réseau C')
        self.assertEqual(numbering.classify('611149590188', self.rules)['site'], 'Réseau B')
        for invalid in ['326111490188', '032611149018', '611349590188', '6111490901', '9999000188', 'M26111490188', 3299000188]:
            self.assertIsNone(numbering.classify(invalid, self.rules), invalid)

    def test_mapping_validation_rejects_ambiguous_reverse_codes_and_numeric_ids(self):
        for kind in ['duplicate_letters', 'duplicate_barcode', 'zero_lost', 'numeric_code', 'colliding_length']:
            rules = deepcopy(self.rules)
            if kind == 'duplicate_letters': rules['reseau_c'].append(deepcopy(rules['reseau_c'][0]))
            elif kind == 'duplicate_barcode': rules['reseau_c'][1]['barcode'] = rules['reseau_c'][0]['barcode']
            elif kind == 'zero_lost': rules['reseau_c'][3]['barcode'] = '1'
            elif kind == 'numeric_code': rules['reseau_c'][3]['barcode'] = 1
            else: rules['reseau_c_digits'] = 8
            with self.subTest(kind=kind), self.assertRaises(ValueError): numbering.validate(rules)
        self.assertEqual(numbering.validate(self.rules), self.rules)

    def test_unknown_or_disabled_formats_do_not_invent_information(self):
        self.rules['enabled'] = False
        self.assertIsNone(numbering.classify('2900579788', self.rules))
        with self.assertRaises(ValueError): numbering.sample_from_dossier('DB261114901', '88')
        with self.assertRaises(ValueError): numbering.sample_from_dossier('C261114901', 88)


class NumberingServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pcr-labs-')
        self.store = Store(self.temp.name)
        self.pcr = PCRService(self.store)
        configure(self.pcr)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def import_rows(self, rows):
        out = io.StringIO(); writer = csv.writer(out, delimiter=';')
        writer.writerow(COLUMNS + PATIENT_COLUMNS)
        for sid, code, dossier, site in rows:
            writer.writerow([dossier, sid, code, '', '', '2026-11-14', '', site, 'attendu', 'FICTIF001', 'Patient fictif'])
        data = {'source': 'Fictif laboratoires', 'content': out.getvalue()}
        preview = self.pcr.preview(data)
        self.pcr.import_list({**data, **preview, 'confirmed': True}, 'test')
        return preview

    def test_receipt_pair_keys_and_source_metadata_are_preserved(self):
        sid = '0326111490188'
        preview = self.import_rows([(sid,'IST_A','','Site source'),(sid,'IST_B','','Site source'),('0326111490187','IST_A','','')])
        self.assertEqual(preview['pairs'][0]['dossier'], 'C261114901')
        result = self.pcr.scan({'sample_id':sid,'request_id':'laboratory-scan-0001'}, 'test')
        self.assertEqual(len(result['pairs']), 2)
        pair = self.pcr.history(sid, 'IST_A')['pair']
        self.assertEqual((pair['dossier'],pair['site'],pair['site_source']), ('C261114901','Réseau C','Site source'))
        self.assertEqual(pair['date_demande'], '2026-11-14')
        self.assertEqual(self.pcr.sample(sid)['dossier'], '')
        self.assertEqual(self.pcr.sample(sid)['site'], 'Site source')
        self.assertEqual(self.pcr.history('0326111490187','IST_A')['pair']['status'], 'expected')

    def test_transmitted_dossier_remains_authoritative_and_settings_recompute_views(self):
        sid = '1326111490188'
        self.import_rows([(sid,'IST_A','DOS_SOURCE','Site source')])
        pair = self.pcr.history(sid,'IST_A')['pair']
        self.assertEqual((pair['dossier'],pair['dossier_derived']), ('DOS_SOURCE',False))
        config = self.pcr.config()
        next(r for r in config['numbering']['reseau_c'] if r['prefix']=='M')['label']='Site C-M modifié'
        self.pcr.configure(config,'test')
        self.assertEqual(self.pcr.history(sid,'IST_A')['pair']['lab_detail'], 'Site C-M modifié')
        with self.assertRaises(Conflict): self.pcr.configure(config,'test')
        legacy = self.pcr.config(); del legacy['numbering']
        self.pcr.configure(legacy,'test')
        self.assertEqual(self.pcr.history(sid,'IST_A')['pair']['lab_detail'], 'Site C-M modifié')
        self.assertTrue(self.store.verify_audit())

    def test_unknown_scan_reconciles_without_losing_zero_and_disable_is_reversible(self):
        sid='0326111490188'
        first=self.pcr.scan({'sample_id':sid,'request_id':'laboratory-before-001'},'test')['pairs'][0]
        self.assertEqual((first['dossier'],first['site'],first['site_resolved']),('C261114901',ERROR_SITE,'Réseau C'))
        self.assertEqual(first['error_reasons'], ['Absent des demandes'])
        self.import_rows([(sid,'IST_A','','')])
        pair=self.pcr.history(sid,'IST_A')['pair']
        self.assertEqual(pair['receipt'],first['receipt'])
        c=self.pcr.config();c['numbering']['enabled']=False;self.pcr.configure(c,'test')
        pair=self.pcr.history(sid,'IST_A')['pair']
        self.assertEqual((pair['dossier'],pair['site'],pair['site_resolved']),('',ERROR_SITE,''))
        self.assertEqual(pair['receipt'],first['receipt'])
        c=self.pcr.config();c['numbering']['enabled']=True;self.pcr.configure(c,'test')
        self.store.db.close();self.store=Store(self.temp.name);self.pcr=PCRService(self.store)
        self.assertEqual(self.pcr.history(sid,'IST_A')['pair']['dossier'],'C261114901')

    def test_add_and_remove_laboratory_changes_display_without_removing_scan(self):
        sid='2299000188'
        received=self.pcr.scan({'sample_id':sid,'request_id':'new-lab-scan-00001'},'test')['pairs'][0]
        self.assertEqual(received['site'],ERROR_SITE)
        c=self.pcr.config();c['numbering']['reseau_a'].append({'prefix':'22','label':'Nouveau laboratoire'})
        self.pcr.configure(c,'test')
        row=self.pcr.listing()['pairs'][0]
        self.assertEqual((row['dossier'],row['site_resolved']),('22990001','Nouveau laboratoire'))
        self.assertEqual(row['error_reasons'],['Absent des demandes'])
        c=self.pcr.config();c['numbering']['reseau_a']=[r for r in c['numbering']['reseau_a'] if r['prefix']!='22']
        self.pcr.configure(c,'test')
        row=self.pcr.listing()['pairs'][0]
        self.assertEqual((row['sample_id'],row['site'],row['receipt']),(sid,ERROR_SITE,received['receipt']))

    def test_error_reasons_clear_independently_when_requests_and_settings_arrive(self):
        sid='9999000199'
        first=self.pcr.scan({'sample_id':sid,'request_id':'error-scan-request-001'},'test')['pairs'][0]
        self.assertEqual(first['site'],ERROR_SITE)
        self.assertEqual(first['error_reasons'],['Laboratoire non reconnu','Suffixe inconnu','Absent des demandes'])
        receipt=first['receipt']
        self.import_rows([(sid,'IST_A','',''),(sid,'IST_B','','')])
        pairs=self.pcr.listing()['pairs']
        self.assertEqual(len(pairs),2)
        self.assertTrue(all(p['error_reasons']==['Laboratoire non reconnu','Suffixe inconnu'] for p in pairs))
        c=self.pcr.config();c['numbering']['reseau_a'].append({'prefix':'99','label':'Laboratoire ajouté'})
        self.pcr.configure(c,'test')
        self.assertEqual(self.pcr.history(sid,'IST_A')['pair']['error_reasons'],['Suffixe inconnu'])
        c=self.pcr.config();c['suffixes'].append({'suffix':'99','group':'ist','active':True})
        self.pcr.configure(c,'test')
        pair=self.pcr.history(sid,'IST_A')['pair']
        self.assertEqual((pair['site'],pair['error_reasons'],pair['receipt']),('Laboratoire ajouté',[],receipt))
        repeated=self.pcr.scan({'sample_id':sid,'request_id':'error-scan-request-001'},'test')
        self.assertEqual(repeated['pairs'][0]['error_reasons'],[])
        self.assertTrue(repeated['replayed'])
        self.assertTrue(self.store.verify_audit())

    def test_missing_tube_request_is_not_satisfied_by_another_tube_in_same_dossier(self):
        self.import_rows([('1199000188','IST_A','','')])
        first=self.pcr.scan({'sample_id':'1199000187','request_id':'wrong-tube-scan-001'},'test')['pairs'][0]
        self.assertEqual(first['error_reasons'],['Absent des demandes'])
        self.assertEqual(self.pcr.history('1199000188','IST_A')['pair']['status'],'expected')
        self.import_rows([('1199000187','IST_A','','')])
        pair=self.pcr.history('1199000187','IST_A')['pair']
        self.assertEqual((pair['site'],pair['error_reasons'],pair['receipt']),('Site 11',[],first['receipt']))

    def test_error_group_is_for_scanned_tubes_and_transmitted_lab_can_resolve_origin(self):
        sid='9999000188'
        self.import_rows([(sid,'IST_A','','')])
        self.assertEqual(self.pcr.history(sid,'IST_A')['pair']['error_reasons'],[])
        self.pcr.scan({'sample_id':sid,'request_id':'origin-scan-request-001'},'test')
        self.assertEqual(self.pcr.history(sid,'IST_A')['pair']['error_reasons'],['Laboratoire non reconnu'])
        self.import_rows([(sid,'IST_A','','Laboratoire transmis')])
        pair=self.pcr.history(sid,'IST_A')['pair']
        self.assertEqual((pair['site'],pair['error_reasons']),('Laboratoire transmis',[]))

    def test_scanned_tube_with_any_cancelled_request_requires_physical_separation(self):
        sid='1199000188'
        self.import_rows([(sid,'IST_A','',''),(sid,'IST_B','','')])
        # A cancellation changes request state, not the other analysis on the tube.
        sample=self.pcr.sample(sid);sample['orders']['IST_B']['state']='annule'
        with self.store.db:self.pcr.event(sample,'test','orders_imported',{})
        self.assertFalse(self.pcr.history(sid,'IST_A')['pair']['isolation_required'])
        result=self.pcr.scan({'sample_id':sid,'request_id':'cancelled-tube-scan-001'},'test')
        self.assertTrue(all(p['isolation_required'] for p in result['pairs']))
        self.assertTrue(all(p['has_cancelled_requests'] for p in result['pairs']))
        self.assertTrue(all(p['site']=='Site 11' and not p['error_reasons'] for p in result['pairs']))
        self.assertEqual(self.pcr.history(sid,'IST_A')['pair']['state'],'attendu')
        self.assertEqual(self.pcr.history(sid,'IST_B')['pair']['state'],'annule')


if __name__ == '__main__':
    unittest.main()
