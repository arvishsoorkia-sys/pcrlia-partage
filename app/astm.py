# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
"""Bounded ASTM E1381 / DxI exchange for queries and explicit result trials.

Reference: Beckman C03112-AF, chapters 2–4. The MCA query/response record
layout was also observed on 2026-11-13. Raw TCP framing still needs a lab trial.
Only synthetic messages belong in tests; never log received patient records.
"""
from __future__ import annotations

from datetime import date, datetime
import re
import socket
import time

STX, ETX, EOT, ENQ, ACK, LF, CR, NAK, ETB = (bytes([n]) for n in (2, 3, 4, 5, 6, 10, 13, 21, 23))
MAX_MESSAGE = 65536


class ProtocolError(ValueError):
    pass


class BadFrame(ProtocolError):
    pass


def sample_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,14}', value):
        raise ProtocolError('Identifiant ASTM : 1 à 15 caractères, sans espace ; le code-barres reste inchangé.')
    return value


def query(value, stamp=None):
    value = sample_id(value)
    stamp = stamp or datetime.now().strftime('%Y%m%d%H%M%S')
    # Same Q field positions as the actual ACCESS query observed in MCA.
    return (f'H|\\^&|||ACCESS^PCRLIA|||||LIS||P|1|{stamp}\r'
            f'Q|1|^{value}||ALL||||||||O\rL|1|F\r').encode('ascii')


def frame(number, data, final=True):
    if not 0 <= number <= 7 or not 1 <= len(data) <= 240:
        raise ProtocolError('Taille ou numéro de trame ASTM invalide.')
    body = str(number).encode() + data + (ETX if final else ETB)
    return STX + body + f'{sum(body) % 256:02X}'.encode() + CR + LF


def frames(message):
    if not message.endswith(CR) or len(message) > MAX_MESSAGE:
        raise ProtocolError('Message ASTM incomplet ou trop long.')
    number = 1
    for record in message.split(CR)[:-1]:
        record += CR
        for start in range(0, len(record), 240):
            yield frame(number, record[start:start + 240], start + 240 >= len(record))
            number = (number + 1) % 8


def decode_frame(packet):
    if (len(packet) < 8 or len(packet) > 247 or packet[:1] != STX or
            packet[-2:] != CR + LF or packet[-5:-4] not in (ETX, ETB) or
            packet[1:2] not in tuple(str(n).encode() for n in range(8))):
        raise BadFrame('Structure de trame ASTM invalide.')
    if packet[-4:-2] != f'{sum(packet[1:-4]) % 256:02X}'.encode():
        raise BadFrame('Somme de contrôle ASTM incorrecte.')
    data = packet[2:-5]
    if any(n < 32 and n != 13 for n in data):
        raise BadFrame('Caractère de contrôle dans les données ASTM.')
    return int(packet[1:2]), data, packet[-5:-4] == ETX


class Wire:
    """One serialized conversation per TCP connection, with buffered stream reads."""
    def __init__(self, connection):
        self.connection = connection
        self.buffer = bytearray()

    def send(self, data, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Délai de réponse MCA dépassé.')
        self.connection.settimeout(min(15, remaining))
        self.connection.sendall(data)

    def token(self, deadline):
        while True:
            if self.buffer:
                if self.buffer[0] != 2:
                    return bytes([self.buffer.pop(0)])
                end = next((i for i in range(2, len(self.buffer)) if self.buffer[i] in (3, 23)), None)
                if end is not None and len(self.buffer) >= end + 5:
                    packet = bytes(self.buffer[:end + 5])
                    del self.buffer[:end + 5]
                    return packet
                if len(self.buffer) > 247:
                    raise ProtocolError('Trame ASTM trop longue.')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Délai de réponse MCA dépassé.')
            self.connection.settimeout(remaining)
            chunk = self.connection.recv(4096)
            if not chunk:
                raise ConnectionError('Connexion MCA interrompue.')
            self.buffer.extend(chunk)
            if len(self.buffer) > MAX_MESSAGE:
                raise ProtocolError('Réception ASTM trop volumineuse.')

    def transmit(self, message, deadline):
        for attempt in range(6):
            self.send(ENQ, deadline)
            answer = self.token(min(deadline, time.monotonic() + 15))
            if answer == ACK:
                break
            pause = 1 if answer == ENQ else 10 if answer == NAK else None
            if pause is None or time.monotonic() + pause >= deadline:
                raise ProtocolError('MCA n’a pas accepté la demande ASTM.')
            time.sleep(pause)
        else:
            raise ProtocolError('MCA reste occupé.')
        try:
            for packet in frames(message):
                for attempt in range(6):
                    self.send(packet, deadline)
                    answer = self.token(min(deadline, time.monotonic() + 15))
                    if answer == ACK:
                        break
                    if answer == EOT:
                        raise ProtocolError('MCA a interrompu l’envoi.')
                    if answer != NAK:
                        raise ProtocolError('Acquittement ASTM inattendu.')
                else:
                    raise ProtocolError('Trame refusée six fois par MCA.')
        finally:
            self.send(EOT, deadline)

    def receive(self, deadline):
        if self.token(deadline) != ENQ:
            raise ProtocolError('Début de réponse ASTM inattendu.')
        self.send(ACK, deadline)
        message, partial = bytearray(), bytearray()
        expected, previous, errors = 1, None, 0
        while True:
            packet = self.token(min(deadline, time.monotonic() + 30))
            if packet == EOT:
                if partial or not message:
                    raise ProtocolError('Réponse MCA interrompue avant sa fin.')
                return bytes(message)
            try:
                number, data, final = decode_frame(packet)
                if packet == previous:
                    self.send(ACK, deadline)
                    continue  # Lost ACK: do not append the retransmitted frame twice.
                if number != expected:
                    raise BadFrame('Ordre des trames ASTM incorrect.')
                if len(message) + len(partial) + len(data) > MAX_MESSAGE:
                    raise ProtocolError('Message ASTM trop long.')
                if final and not (partial + data).endswith(CR):
                    raise BadFrame('Enregistrement ASTM incomplet.')
            except BadFrame:
                errors += 1
                self.send(NAK, deadline)
                if errors >= 6:
                    raise ProtocolError('Trop de trames ASTM invalides.')
                continue
            partial.extend(data)
            if final:
                message.extend(partial)
                partial.clear()
            previous, expected, errors = packet, (expected + 1) % 8, 0
            self.send(ACK, deadline)


def identity_response(message, expected_sample, encoding='cp1252', expected_test='CHLAS'):
    """Accept only complete H/P/O/L, with an exact specimen match. Never infer IDs."""
    sample_id(expected_sample)
    try:
        text = message.decode(encoding, errors='strict')
    except (UnicodeError, LookupError) as exc:
        raise ProtocolError('Encodage de la réponse MCA illisible.') from exc
    if len(message) > MAX_MESSAGE or not text.endswith('\r') or any(ord(c) < 32 and c != '\r' for c in text):
        raise ProtocolError('Réponse MCA mal formée.')
    records = [r.split('|') for r in text[:-1].split('\r')]
    if (len(records) < 2 or records[0][0] != 'H' or records[0][1:2] != ['\\^&'] or
            records[-1][0] != 'L' or records[-1][1:3] != ['1', 'F']):
        raise ProtocolError('En-tête ou fin de réponse MCA invalide.')
    if len(records) == 2:
        return {'status': 'not_found', 'message': 'MCA n’a renvoyé aucune analyse pour ce tube.'}
    patient, orders = None, []
    for record in records[1:-1]:
        if record[0] == 'P' and patient is None and not orders:
            patient = record
        elif record[0] == 'O' and patient is not None:
            if len(record) < 5 or record[2] != expected_sample:
                raise ProtocolError('Échantillon différent dans la réponse MCA : identité non attribuée.')
            if len(record) > 11 and record[11] not in ('', 'A', 'N'):
                raise ProtocolError('Ordre MCA inattendu : identité non attribuée.')
            for test in record[4].split('\\'):
                parts = test.split('^')
                if len(parts) < 4 or not parts[3] or len(parts[3]) > 80:
                    raise ProtocolError('Code d’analyse MCA invalide.')
                orders.append(parts[3])
        else:
            raise ProtocolError('Réponse MCA ambiguë ou non attendue : identité non attribuée.')
    if patient is None or not orders or len(patient) < 9:
        raise ProtocolError('Identité ou demande incomplète dans la réponse MCA.')
    names = patient[5].split('^')
    last_name, first_name = names[0].strip(), names[1].strip() if len(names) > 1 else ''
    birth = patient[7]
    if birth:
        try:
            if not re.fullmatch(r'\d{8}', birth):
                raise ValueError()
            birth = date(int(birth[:4]), int(birth[4:6]), int(birth[6:8])).isoformat()
        except ValueError as exc:
            raise ProtocolError('Date de naissance MCA invalide.') from exc
    if not last_name or any(len(v) > 200 for v in (last_name, first_name, patient[2])):
        raise ProtocolError('Identité absente ou trop longue dans la réponse MCA.')
    return {'status': 'found' if expected_test in orders else 'not_requested',
            'sample_id': expected_sample,
            'identity': {'last_name': last_name, 'first_name': first_name,
                         'birth_date': birth, 'sex': patient[8], 'patient_id': patient[2]},
            'tests': list(dict.fromkeys(orders)),
            'message': 'Identité reçue de MCA.' if expected_test in orders else
                       f'Identité reçue ; {expected_test} absent des analyses renvoyées par MCA.'}


def self_test():
    """Deterministic, synthetic codec check; no socket, no real barcode, no MCA."""
    request = query('0000000001', '20261113090000')
    response = (b'H|\\^&||||||||LIS||P|1|20261113090001\r'
                b'P|1|TEST|||EXEMPLE^ALICE||19900102|F\r'
                b'O|1|0000000001||^^^CHLAS|A||||||A||||Serum\rL|1|F\r')
    decoded = b''.join(decode_frame(p)[1] for p in frames(response))
    result = identity_response(decoded, '0000000001')
    return {'ok': result['status'] == 'found' and b'Q|1|^0000000001|' in request,
            'message': 'Essai local réussi : trames et identité fictive vérifiées. Aucun échange avec MCA.',
            'identity': result['identity'], 'sample_id': result['sample_id']}
