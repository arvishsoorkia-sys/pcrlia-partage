# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
"""Only synthetic patients and loopback sockets; no laboratory network access."""
import http.client
import json
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from app import astm, accounts
from app.server import start_background
from app.pcr import PCRService
from app.pcr_mca import receiver_for, validate_config
from app.server import AppServer
from app.storage import Store
from tests.test_phadia_orders import message


def order(stamp='20261115120000', code='CTNG1', action='N', sid='1199000188'):
    return message(stamp, [(sid, code, action)]).replace(b'\r\n', b'\r') + b'\r'


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pcr-wire-')
        self.store = Store(self.temp.name)
        self.pcr = PCRService(self.store)
        self.receiver = receiver_for(self.store)
        self.config = {'enabled': True, 'listen_address': '127.0.0.1', 'port': free_port(),
                       'mca_address': '127.0.0.1', 'encoding': 'cp1252'}
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.receiver.stop()
        if hasattr(self.store, 'mca_identity_link'):
            self.store.mca_identity_link.stop()
        self.store.db.close()
        self.temp.cleanup()

    def connect(self):
        if not self.receiver.listener:
            self.receiver.configure(self.config, 'fictitious-admin')
        client = socket.create_connection(('127.0.0.1', self.config['port']), timeout=3)
        client.settimeout(3)
        self.clients.append(client)
        return client

    def begin(self, client):
        client.sendall(astm.ENQ)
        self.assertEqual(client.recv(1), astm.ACK)

    def transmit(self, payload, client=None):
        client = client or self.connect()
        self.begin(client)
        for packet in astm.frames(payload):
            client.sendall(packet)
            self.assertEqual(client.recv(1), astm.ACK)
        client.sendall(astm.EOT)
        return client

    def wait_for(self, predicate):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.02)
        self.fail('Receiver condition not reached')

    def test_disabled_start_and_status_do_not_change_database(self):
        before = list(self.store.db.iterdump())
        self.receiver.start()
        self.assertFalse(self.pcr.listing()['source']['connected'])
        self.assertFalse(self.receiver.listener)
        self.assertEqual(before, list(self.store.db.iterdump()))

    def test_exact_order_identity_and_durable_receipt_before_final_ack(self):
        client = self.connect()
        self.begin(client)
        for packet in astm.frames(order()):
            # Deliberately fragment a TCP packet: framing must not depend on recv boundaries.
            client.sendall(packet[:3]); client.sendall(packet[3:])
            self.assertEqual(client.recv(1), astm.ACK)
        with self.store.lock:
            pairs = self.pcr.listing()['pairs']
            self.assertEqual(len(pairs), 1)
            p = pairs[0]
            self.assertEqual((p['sample_id'], p['request_code']), ('1199000188', 'CTNG1'))
            self.assertEqual(p['status'], 'expected')
            self.assertIsNone(p['receipt'])
            self.assertEqual(p['patient'], 'PATIENT FICTIF EXEMPLE')
            self.assertEqual(p['patient_id'], '')
            self.assertEqual(p['date_demande'], '2026-11-15')
            self.assertEqual(self.receiver.status()['counts']['applied'], 1)
            self.assertEqual(self.store.db.execute('PRAGMA synchronous').fetchone()[0], 2)
        client.sendall(astm.EOT)

    def test_retries_bad_checksum_and_duplicate_frame_no_double_order(self):
        client = self.connect(); self.begin(client)
        packets = list(astm.frames(order()))
        bad = packets[0][:-4] + b'XX\r\n'
        client.sendall(bad); self.assertEqual(client.recv(1), astm.NAK)
        for packet in packets:
            client.sendall(packet); self.assertEqual(client.recv(1), astm.ACK)
            client.sendall(packet); self.assertEqual(client.recv(1), astm.ACK)
        rev = self.pcr.revision()
        client.sendall(astm.EOT)
        self.transmit(order(), client)
        self.assertEqual(rev, self.pcr.revision())
        self.assertEqual(len(self.pcr.listing()['pairs']), 1)

    def test_cancel_old_replay_and_reactivation_preserve_scan(self):
        client = self.transmit(order())
        self.pcr.scan_tube({'sample_id': '1199000188', 'request_id': 'fictitious-scan-001'}, 'tech-test')
        before = self.pcr.sample('1199000188')['tube_receipt']
        self.transmit(order('20261115120100', action='C'), client)
        self.assertEqual(self.pcr.listing()['pairs'][0]['state'], 'annule')
        self.transmit(order('20261115115900'), client)
        self.assertEqual(self.pcr.listing()['pairs'][0]['state'], 'annule')
        self.transmit(order('20261115120200'), client)
        pair = self.pcr.listing()['pairs'][0]
        self.assertEqual(pair['receipt'], before)
        self.assertEqual(pair['state'], 'attendu')
        self.assertTrue(pair['reactivated_request'])
        self.assertTrue(self.store.verify_audit())

    def test_conflicting_equal_timestamp_kept_for_review(self):
        client = self.transmit(order())
        self.transmit(order(action='C'), client)
        self.assertEqual(self.pcr.listing()['pairs'][0]['state'], 'attendu')
        self.assertEqual(self.receiver.status()['pending'], 1)

    def test_unknown_code_atomic_quarantine_and_explicit_retry(self):
        payload = message(orders=[('1199000188','CTNG1','N'),('1199000288','UNKNOWN','N')]).replace(b'\r\n',b'\r')+b'\r'
        self.transmit(payload)
        self.assertEqual(self.pcr.listing()['pairs'], [])
        self.assertEqual(self.receiver.status()['counts']['quarantined'], 1)
        self.pcr.configure_code({'code':'UNKNOWN','label':'Test fictif','group_key':'ist','active':True,'revision':0,'suffix':'88'}, 'admin')
        self.receiver.retry('admin')
        self.assertEqual(len(self.pcr.listing()['pairs']), 2)
        self.assertEqual(self.receiver.status()['pending'], 0)

    def test_bare_phadia_code_add_cancel_and_unknown_code_guard(self):
        payload=order(code='PARASITO',sid='1199000144').replace(b'^^^PARASITO^1',b'^^^PARASITO')
        client=self.transmit(payload)
        pair=self.pcr.listing()['pairs'][0]
        self.assertEqual((pair['sample_id'],pair['request_code'],pair['status']),('1199000144','PARASITO','expected'))
        self.assertIsNone(pair['receipt'])
        cancelled=order('20261115120100',code='PARASITO',action='C',sid='1199000144').replace(b'^^^PARASITO^1',b'^^^PARASITO')
        self.transmit(cancelled,client)
        self.assertEqual(self.pcr.listing()['pairs'][0]['state'],'annule')
        self.assertFalse(self.pcr.listing()['pairs'][0]['pending_cancelled'])
        self.assertFalse(self.pcr.listing()['pairs'][0]['has_physical_scan'])
        self.pcr.scan_tube({'sample_id':'1199000144','request_id':'first-physical-scan-after-cancellation'},'tech-test')
        self.assertTrue(self.pcr.listing()['pairs'][0]['pending_cancelled'])
        self.transmit(payload.replace(b'^^^PARASITO',b'^^^UNCONFIGURED'),client)
        self.assertEqual(self.receiver.status()['pending'],1)
        self.assertEqual(len(self.pcr.listing()['pairs']),1)

    def test_previously_quarantined_bare_code_can_be_reexamined_once(self):
        payload=order(code='PARASITO',sid='1199000144').replace(b'^^^PARASITO^1',b'^^^PARASITO')
        self.receiver.configure(self.config,'admin');self.receiver.stop()
        with self.store.db:
            self.store.db.execute('INSERT INTO pcr_mca_inbox VALUES(?,?,?,?,?,?,?)',
                ('previous-version','2026-11-15','127.0.0.1',payload,'cp1252','quarantined','Composition du code de demande à qualifier.'))
        self.receiver.start()
        self.assertEqual(self.receiver.status()['pending'],1)
        self.assertEqual(self.pcr.listing()['pairs'],[])
        self.receiver.retry('test-admin')
        self.assertEqual(self.receiver.status()['pending'],0)
        pair=self.pcr.listing()['pairs'][0]
        self.assertEqual((pair['sample_id'],pair['request_code'],pair['status']),('1199000144','PARASITO','expected'))
        self.assertIsNone(pair['receipt'])
        revision=self.pcr.revision()
        self.receiver.retry('test-admin')
        self.assertEqual(self.pcr.revision(),revision)
        self.assertEqual(len(self.pcr.listing()['pairs']),1)
        self.assertEqual(self.store.db.execute('SELECT payload FROM pcr_mca_inbox').fetchone()[0],payload)

    def test_incomplete_transfer_keeps_frames_but_no_orders(self):
        client = self.connect(); self.begin(client)
        client.sendall(next(astm.frames(order())))
        self.assertEqual(client.recv(1), astm.ACK)
        client.sendall(astm.EOT)
        self.wait_for(lambda: self.receiver.status()['counts'].get('interrupted') == 1)
        self.assertEqual(self.pcr.listing()['pairs'], [])
        with self.store.lock:
            self.assertTrue(self.store.db.execute('SELECT payload FROM pcr_mca_inbox').fetchone()[0])
        self.transmit(order())
        self.assertEqual(len(self.pcr.listing()['pairs']), 1)

    def test_result_and_unqualified_payload_are_not_orders(self):
        self.transmit(order().replace(b'L|1|F', b'R|1|^^^CHLAS|12.34\rL|1|F'))
        self.assertEqual(self.pcr.listing()['pairs'], [])
        self.assertEqual(self.receiver.status()['pending'], 1)

    def test_encoding_and_leading_zero_specimen(self):
        self.transmit(order(sid='0000000188').replace(b'EXEMPLE', 'ÉLÈVE'.encode('cp1252')))
        pair = self.pcr.listing()['pairs'][0]
        self.assertEqual(pair['sample_id'], '0000000188')
        self.assertIn('ÉLÈVE', pair['patient'])

    def test_different_source_identity_never_reassigns_known_tube(self):
        client = self.transmit(order())
        self.transmit(order('20261115120200').replace(b'TEST001', b'TEST999'), client)
        self.assertEqual(self.receiver.status()['pending'], 1)
        self.assertEqual(self.pcr.sample('1199000188')['mca_patient']['source_identifier'], 'TEST001')

    def test_peer_allowlist_and_reserved_port(self):
        for port in (5510, 8765, 9443):
            with self.assertRaises(ValueError): validate_config({**self.config,'port':port})
        with self.assertRaises(ValueError): validate_config({**self.config,'listen_address':'0.0.0.0'})
        self.config['mca_address'] = '127.0.0.2'
        client = self.connect()
        self.wait_for(lambda: 'refusée' in self.receiver.error)
        try:
            self.assertEqual(client.recv(1), b'')
        except ConnectionResetError:
            pass
        self.assertEqual(self.receiver.status()['counts'], {})

    def test_occupied_port_reports_failure_without_touching_other_listener(self):
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1',self.config['port'])); occupied.listen()
            self.receiver.configure(self.config, 'admin')
            self.assertFalse(self.receiver.listener)
            self.assertIn('impossible', self.receiver.status()['error'])

    def test_complete_saved_message_recovered_after_restart(self):
        self.receiver.configure(self.config, 'admin'); self.receiver.stop()
        with self.store.db:
            self.store.db.execute('INSERT INTO pcr_mca_inbox VALUES(?,?,?,?,?,?,?)',
                ('crash-test','2026-11-15','127.0.0.1',order(),'cp1252','ready',''))
        self.receiver.start()
        self.assertEqual(len(self.pcr.listing()['pairs']), 1)
        self.assertEqual(self.receiver.status()['counts']['applied'], 1)
        self.receiver.stop(); self.receiver.start()
        self.assertEqual(len(self.pcr.listing()['pairs']), 1)

    def test_end_record_without_final_frame_is_not_recovered_as_complete(self):
        client = self.connect(); self.begin(client)
        packets = list(astm.frames(order()))
        for packet in packets[:-1]:
            client.sendall(packet); self.assertEqual(client.recv(1), astm.ACK)
        number, data, _ = astm.decode_frame(packets[-1])
        client.sendall(astm.frame(number, data, final=False)); self.assertEqual(client.recv(1), astm.ACK)
        client.close(); self.receiver.stop(); self.receiver.start()
        self.assertEqual(self.pcr.listing()['pairs'], [])
        self.assertEqual(self.receiver.status()['counts']['interrupted'], 1)

    def test_storage_failure_does_not_acknowledge_final_frame(self):
        client = self.connect(); self.begin(client)
        packets = list(astm.frames(order()))
        for packet in packets[:-1]:
            client.sendall(packet); self.assertEqual(client.recv(1), astm.ACK)
        with patch('app.pcr_mca.apply_orders', side_effect=sqlite3.OperationalError('test failure')):
            client.sendall(packets[-1])
            self.assertEqual(client.recv(1), b'')
        self.assertEqual(self.pcr.listing()['pairs'], [])

    def test_standalone_receiver_stops_when_last_http_server_closes(self):
        self.receiver.configure(self.config, 'admin'); self.receiver.stop()
        start_background(self.store)
        self.assertTrue(self.receiver.listener)
        self.transmit(order())
        server = AppServer(('127.0.0.1',0), self.store)
        server.server_close()
        self.assertIsNone(self.receiver.listener)


class ReceiverHTTPTests(unittest.TestCase):
    def test_admin_and_workspace_and_csrf_required(self):
        with tempfile.TemporaryDirectory(prefix='pcr-api-') as folder:
            store = Store(folder); store.setup(store.setup_token, 'admin-test', 'xx')
            store.create_user('pcr-admin','xx','admin','admin-test',apps=['pcrlia'],default_app='pcrlia')
            store.create_user('tech-test','xx','technician','admin-test',apps=['pcrlia'],default_app='pcrlia')
            server = AppServer(('127.0.0.1',0), store)
            thread = threading.Thread(target=server.serve_forever); thread.start()
            def request(path, data=None, headers=None):
                c = http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
                c.request('GET' if data is None else 'POST',path,None if data is None else json.dumps(data),
                          {'Content-Type':'application/json',**(headers or {})})
                r=c.getresponse(); status=r.status; body=json.loads(r.read()); h=dict(r.getheaders()); c.close()
                return status,body,h
            try:
                for user, expected in [('admin-test',200),('tech-test',403),('pcr-admin',200)]:
                    _,u,h=request('/api/login',{'username':user,'password':'xx'})
                    headers={'Cookie':h['Set-Cookie'].split(';')[0],'X-CSRF-Token':u['csrf']}
                    self.assertEqual(request('/api/pcr/mca',headers=headers)[0], expected)
                    self.assertEqual(request('/api/pcr/mca',{'enabled':False},headers)[0], expected)
                self.assertEqual(request('/api/pcr/mca',{'enabled':False},{'Cookie':headers['Cookie']})[0],403)
                self.assertEqual(request('/api/pcr/mca/retry',{},headers)[0],200)
            finally:
                server.shutdown(); server.server_close(); thread.join(); store.db.close()


if __name__ == '__main__': unittest.main()
