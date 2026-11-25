# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
"""Synthetic application messages only: no socket, no production data or writes."""
import unittest

from app.phadia_orders import PhadiaMessageError, decode_orders, simulate_downloads


def message(stamp='20261114223509', orders=(('0000123486', 'IST_A', 'N'),)):
    lines = [f'H|\\^&|||MCA|||||||P|1|{stamp}', 'P|1|TEST001|||PATIENT FICTIF|EXEMPLE|20000101|M']
    for n, (sid, code, action) in enumerate(orders, 1):
        lines.append(f'O|{n}|{sid}||^^^{code}^1|||||||{action}||1||||||||||||O')
    return ('\r\n'.join(lines + ['L|1|F'])).encode('ascii')


class PhadiaOrdersTests(unittest.TestCase):
    def test_add_cancel_and_mixed_message_match_exact_pairs(self):
        inputs = [message(), message('20261114223611', [('0000123486','IST_A','C')]),
                  message('20261114224015', [('0000123486','IST_B','N')]),
                  message('20261114224117', [('0000123486','IST_B','C'),('0000123486','IST_C','N')])]
        result = simulate_downloads(inputs)
        self.assertEqual(len(result['timeline']), 5)
        self.assertEqual([(r['request_code'],r['state']) for r in result['orders']],
                         [('IST_A','annule'),('IST_B','annule'),('IST_C','attendu')])
        self.assertTrue(all(r['sample_id']=='0000123486' for r in result['orders']))

    def test_complete_identifiers_and_case_are_not_transcoded(self):
        result = decode_orders(message(orders=[('0000123486','e1','N'),('0000123487','e1','C'),('0000123486','E1','N')]))
        self.assertEqual([(e['sample_id'],e['request_code'],e['action']) for e in result['events']],
                         [('0000123486','e1','N'),('0000123487','e1','C'),('0000123486','E1','N')])

    def test_no_fabricated_dossier_lab_or_request_date(self):
        result = decode_orders(message())
        self.assertEqual(result['message_at'],'2026-11-14T22:35:09')
        event = result['events'][0]
        self.assertEqual(event['patient']['source_identifier'],'TEST001')
        self.assertTrue(all(event[key] is None for key in ('dossier','site','date_demande','date_prelevement')))
        self.assertIsNone(result['timezone'])
        self.assertFalse(result['transport_qualified'])
        self.assertFalse(result['complete_download'])

    def test_export_line_endings_and_duplicates(self):
        data = message()
        self.assertEqual(decode_orders(data),decode_orders(data.replace(b'\r\n',b'\r')+b'\r'))
        result = simulate_downloads([data,data.replace(b'\r\n',b'\n')])
        self.assertEqual((result['messages'],result['duplicates'],len(result['orders'])),(1,1,1))

    def test_sequence_and_truncation_are_rejected(self):
        for data in (message()[:-5],message().replace(b'O|1|',b'O|2|'),message().replace(b'L|1|F',b'L|1|I'),
                     message().replace(b'P|1|',b'P|2|'),message()+b'\r\nO|2|extra',b'\x02'+message(),
                     message().replace(b'20261114223509',b'20260230223509')):
            with self.subTest(data=data),self.assertRaises(PhadiaMessageError):
                decode_orders(data)

    def test_unknown_action_and_extra_fields_require_qualification(self):
        for data in (message(orders=[('0000123486','IST_A','D')]),
                     message().replace(b'^^^IST_A^1',b'^^^IST_A^2'),
                     message().replace(b'^^^IST_A^1',b'^^^IST_A^'),
                     message().replace(b'^^^IST_A^1',b'^^^IST_A^1^EXTRA'),
                     message().replace(b'^^^IST_A^1',b'^^^'),
                     message().replace(b'^^^IST_A^1',b'^^IST_A'),
                     message().replace(b'^^^IST_A^1',b'X^^IST_A'),
                     message().replace(b'|||||||N',b'|UNEXPECTED||||||N')):
            with self.subTest(data=data),self.assertRaises(PhadiaMessageError):
                decode_orders(data)

    def test_observed_codes_with_or_without_suffix_preserve_exact_evidence(self):
        for code in ('PARASITO', 'CTNG1'):
            for suffix in ('', '^1'):
                for action in ('N', 'C'):
                    with self.subTest(code=code,suffix=suffix,action=action):
                        token=('^^^'+code+suffix).encode('ascii')
                        payload=message(orders=[('0000123444',code,action)]).replace(('^^^'+code+'^1').encode(),token)
                        event=decode_orders(payload)['events'][0]
                        self.assertEqual((event['sample_id'],event['request_code'],event['action']),('0000123444',code,action))
                        self.assertEqual(event['source_test'],token.decode())

    def test_multiple_codes_with_mixed_observed_compositions(self):
        payload=message().replace(b'^^^IST_A^1',b'^^^CTNG1\\^^^CTNG2^1\\^^^PARASITO')
        events=decode_orders(payload)['events']
        self.assertEqual([e['request_code'] for e in events],['CTNG1','CTNG2','PARASITO'])
        self.assertEqual(len({e['sample_id'] for e in events}),1)

    def test_non_ascii_requires_explicit_encoding(self):
        data = message().replace(b'EXEMPLE', 'ÉLÈVE'.encode('cp1252'))
        with self.assertRaises(PhadiaMessageError):
            decode_orders(data)
        self.assertEqual(decode_orders(data,'cp1252')['patients'][0]['additional_name_field'],'ÉLÈVE')

    def test_out_of_order_files_and_same_time_conflicts(self):
        added, cancelled = message(), message('20261114223611',[('0000123486','IST_A','C')])
        self.assertEqual(simulate_downloads([cancelled,added])['orders'][0]['state'],'annule')
        with self.assertRaises(PhadiaMessageError):
            simulate_downloads([added,message(orders=[('0000123486','IST_A','C')])])


if __name__ == '__main__':
    unittest.main()
