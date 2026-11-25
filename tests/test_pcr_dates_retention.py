# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from app.storage import Store, encode
from app.pcr import PCRService
from app.pcr_mca import receiver_for,apply_orders,validate_config
from app.phadia_orders import decode_orders
from tests.test_pcr_mca import order


class DatesRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='pcr-retention-')
        self.store=Store(self.temp.name);self.pcr=PCRService(self.store);self.receiver=receiver_for(self.store)

    def tearDown(self):
        self.receiver.stop();self.store.db.close();self.temp.cleanup()

    def apply(self,stamp,code='CTNG1',action='N'):
        with self.store.lock,self.store.db:apply_orders(self.pcr,decode_orders(order(stamp,code=code,action=action)))

    def test_first_transmission_date_survives_changes_cancellation_and_reactivation(self):
        self.apply('20261115151334')
        for stamp,action in [('20261116080000','N'),('20261117090000','C'),('20261118090000','N'),('20261114090000','N')]:
            self.apply(stamp,action=action)
            p=self.pcr.listing()['pairs'][0]
            self.assertEqual(p['date_demande'],'2026-11-15')
            self.assertEqual(p['date_prelevement'],'')
        self.apply('20261118100000',code='CTNG2')
        self.assertEqual({p['request_code']:p['date_demande'] for p in self.pcr.listing()['pairs']},{'CTNG1':'2026-11-15','CTNG2':'2026-11-18'})

    def test_legacy_first_date_is_recovered_from_events_without_writing_on_read(self):
        self.apply('20261115151334');self.apply('20261117090000',action='C')
        sample=self.pcr.sample('1199000188');sample['orders']['CTNG1'].pop('date_remontee_mca')
        with self.store.db:
            self.store.db.execute('UPDATE pcr_samples SET payload=? WHERE sample_id=?',(encode(sample),sample['sample_id']))
            for row in self.store.db.execute("SELECT seq,detail FROM pcr_events WHERE action='orders_received_mca'").fetchall():
                detail=json.loads(row['detail'])
                for group in ('before','after'):
                    for o in detail.get(group,{}).values():o.pop('date_remontee_mca',None)
                self.store.db.execute('UPDATE pcr_events SET detail=? WHERE seq=?',(encode(detail),row['seq']))
        before=list(self.store.db.iterdump())
        self.assertEqual(self.pcr.listing()['pairs'][0]['date_demande'],'2026-11-15')
        self.assertEqual(list(self.store.db.iterdump()),before)
        self.apply('20261118090000',action='N')
        self.assertEqual(self.pcr.sample('1199000188')['orders']['CTNG1']['date_remontee_mca'],'2026-11-15')

    def test_purge_only_old_integrated_payloads_keeps_dates_orders_scans_and_errors(self):
        self.apply('20261115151334')
        self.pcr.scan_tube({'sample_id':'1199000188','request_id':'physical-receipt-retained'},'test-tech')
        before=copy.deepcopy(self.pcr.listing()['pairs'])
        clock=datetime(2027,3,1,tzinfo=timezone.utc)
        with self.store.db:
            self.store.db.execute('CREATE TABLE pcr_mca_inbox(id TEXT PRIMARY KEY,at TEXT,peer TEXT,payload BLOB,encoding TEXT,state TEXT,error TEXT)')
            for rid,age,state in [('old',91,'applied'),('boundary',90,'applied'),('recent',1,'applied'),('error',120,'quarantined'),('partial',120,'interrupted'),('ready',120,'ready')]:
                self.store.db.execute('INSERT INTO pcr_mca_inbox VALUES(?,?,?,?,?,?,?)',(rid,(clock-timedelta(days=age)).isoformat(),'127.0.0.1',b'FICTITIOUS RAW MESSAGE','cp1252',state,''))
        self.assertEqual(self.receiver.purge_received(clock),1)
        rows={r['id']:dict(r) for r in self.store.db.execute('SELECT * FROM pcr_mca_inbox')}
        self.assertEqual((rows['old']['state'],rows['old']['payload']),('purged',b''))
        self.assertTrue(all(rows[k]['payload'] for k in rows if k!='old'))
        self.assertEqual(self.pcr.listing()['pairs'],before)
        self.assertEqual(self.receiver.purge_received(clock),0)
        self.assertEqual(self.receiver.status()['purged_messages'],1)
        self.assertTrue(self.store.verify_audit())

    def test_invalid_retention_rejected(self):
        for value in (0,-1,3651,True,'90'):
            with self.assertRaises(ValueError):validate_config({'raw_retention_days':value})
        self.assertEqual(validate_config({})['raw_retention_days'],90)
