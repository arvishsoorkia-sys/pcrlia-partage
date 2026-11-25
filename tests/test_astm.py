# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
import socket
import threading
import time
import unittest

from app import astm


def response(tube='0000000001', name='EXEMPLE^ALICE', tests='CHLAS', encoding='cp1252'):
    return (f'H|\\^&||||||||LIS||P|1|20261113090001\r'
            f'P|1|FAKE|||{name}|NAISSANCE|19900102|F\r'
            f'O|1|{tube}||^^^{tests}|A||||||A||||Serum\rL|1|F\r').encode(encoding)


def sockets():
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0)); listener.listen(1)
    a = socket.create_connection(listener.getsockname(), timeout=3)
    b, _ = listener.accept(); listener.close()
    return a, b


class ASTMTests(unittest.TestCase):
    def test_documented_checksum_and_exact_sample_query(self):
        data = b'Q|1|^Samp45||ALL||||||||O\r'
        # C03112-AF's example prints 15; summing the actual bytes gives 14.
        # Anchor the checksum rule with an independently hand-computed vector.
        self.assertEqual(astm.frame(1, b'A\r'), b'\x021A\r\x0382\r\n')
        self.assertEqual(astm.frame(2, data), b'\x022' + data + b'\x0314\r\n')
        self.assertIn(b'Q|1|^0000000001||ALL||||||||O\r', astm.query('0000000001'))
        for bad in ('123|P', '123\r', '123 01', '1'*16, '', 123):
            with self.assertRaises(astm.ProtocolError): astm.query(bad)

    def test_sequence_wrap_and_split_records(self):
        message = b'H|\\^&\r' + (b'P|' + b'A'*600 + b'\r')*4 + b'L|1|F\r'
        packets = list(astm.frames(message))
        self.assertTrue(all(len(p) <= 247 for p in packets))
        self.assertEqual([astm.decode_frame(p)[0] for p in packets], [(i+1)%8 for i in range(len(packets))])
        self.assertEqual(b''.join(astm.decode_frame(p)[1] for p in packets), message)
        self.assertIn(False, [astm.decode_frame(p)[2] for p in packets])

    def test_identity_fields_and_exact_match(self):
        value = astm.identity_response(response(name='ÉCHANTILLON^ALICE'), '0000000001')
        self.assertEqual(value['identity'], {'last_name':'ÉCHANTILLON','first_name':'ALICE',
            'patient_id':'FAKE','birth_date':'1990-01-02','sex':'F'})
        self.assertEqual(value['tests'], ['CHLAS'])
        self.assertEqual(value['status'], 'found')
        with self.assertRaises(astm.ProtocolError):
            astm.identity_response(response(), '0000000002')
        with self.assertRaises(astm.ProtocolError):
            astm.identity_response(response(), '000000001')  # Never normalize zeros.

    def test_missing_no_order_and_ambiguous_records(self):
        self.assertEqual(astm.identity_response(b'H|\\^&||||||||LIS||P|1|20261113090001\rL|1|F\r', '0000000001')['status'], 'not_found')
        self.assertEqual(astm.identity_response(response(tests='OTHER'), '0000000001')['status'], 'not_requested')
        for payload in (response()[:-1], response().replace(b'19900102', b'19900231'),
                        response().replace(b'L|1|F', b'R|1|^^^CHLAS|1.2\rL|1|F'),
                        response().replace(b'O|1|', b'P|2|OTHER|||OTHER^NAME||19900102|M\rO|1|'),
                        response().replace(b'EXEMPLE^ALICE', b''),
                        response().replace(b'||||||A||||', b'||||||C||||')):
            with self.assertRaises(astm.ProtocolError): astm.identity_response(payload, '0000000001')

    def test_fragmentation_bad_checksum_and_duplicate_retransmission(self):
        a, b = sockets(); outcome = []; failures = []
        def receive():
            try: outcome.append(astm.Wire(b).receive(time.monotonic()+5))
            except Exception as exc: failures.append(exc)
        thread = threading.Thread(target=receive); thread.start()
        try:
            a.sendall(astm.ENQ); self.assertEqual(a.recv(1), astm.ACK)
            packets = list(astm.frames(response()))
            damaged = bytearray(packets[0]); damaged[-4] = ord('Z')
            a.sendall(damaged); self.assertEqual(a.recv(1), astm.NAK)
            for packet in packets:
                for byte in packet: a.sendall(bytes([byte]))
                self.assertEqual(a.recv(1), astm.ACK)
                a.sendall(packet); self.assertEqual(a.recv(1), astm.ACK)
            a.sendall(astm.EOT); thread.join(5)
            self.assertEqual(failures, []); self.assertEqual(outcome, [response()])
        finally: a.close(); b.close(); thread.join(5)

    def test_full_query_reply_over_tcp(self):
        a, b = sockets(); requests = []; failures = []
        def peer():
            try:
                wire = astm.Wire(b); deadline=time.monotonic()+5
                requests.append(wire.receive(deadline)); wire.transmit(response(), deadline)
            except Exception as exc: failures.append(exc)
        thread=threading.Thread(target=peer);thread.start()
        try:
            wire=astm.Wire(a); deadline=time.monotonic()+5
            wire.transmit(astm.query('0000000001'), deadline)
            value=astm.identity_response(wire.receive(deadline),'0000000001')
            self.assertEqual(value['status'],'found');thread.join(5)
            self.assertEqual(failures,[]);self.assertEqual(len(requests),1)
            self.assertIn(b'Q|1|^0000000001|',requests[0])
        finally:a.close();b.close();thread.join(5)

    def test_truncated_transfer_and_timeout(self):
        a,b=sockets(); failures=[]
        def peer():
            try:
                a.sendall(astm.ENQ);a.recv(1)
                a.sendall(astm.frame(1,b'H|\\^&',False));a.recv(1);a.sendall(astm.EOT)
            except Exception as exc:failures.append(exc)
        t=threading.Thread(target=peer);t.start()
        try:
            with self.assertRaises(astm.ProtocolError):astm.Wire(b).receive(time.monotonic()+2)
            t.join(2);self.assertEqual(failures,[])
            with self.assertRaises(TimeoutError):astm.Wire(b).receive(time.monotonic()+.05)
        finally:a.close();b.close();t.join(2)

    def test_self_test_never_opens_network(self):
        from unittest.mock import patch
        with patch('socket.socket',side_effect=AssertionError('network forbidden')):
            self.assertTrue(astm.self_test()['ok'])
