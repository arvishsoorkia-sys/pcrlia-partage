# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
"""Offline decoder for the MCA Phadia H/P/O/L exports observed on 2026-11-14/15.

No network listener, acknowledgements, database writes or result transmission.
Field positions are qualified against exported application messages only; these
files do not establish the transport framing or the completeness of a download.
"""
from datetime import datetime
import hashlib
import re


class PhadiaMessageError(ValueError):
    pass


def _identifier(value, label):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', value):
        raise PhadiaMessageError(f'{label} absent ou non reconnu.')
    return value


def _timestamp(value):
    if not re.fullmatch(r'\d{14}', value):
        raise PhadiaMessageError('Horodatage de message absent ou invalide.')
    try:
        return datetime.strptime(value, '%Y%m%d%H%M%S').isoformat()
    except ValueError:
        raise PhadiaMessageError('Horodatage de message invalide.') from None


def decode_orders(message, encoding='ascii'):
    """Decode N/C at the exact specimen/request-code level, without guessing dates.

    Encoding must be explicitly selected for non-ASCII messages. Source patient
    field meanings are retained for qualification, not converted to dossier IDs.
    """
    if not isinstance(message, bytes) or not 1 <= len(message) <= 2_000_000:
        raise PhadiaMessageError('Message vide ou trop volumineux (2 Mo maximum).')
    if encoding not in ('ascii', 'cp1252', 'utf-8'):
        raise PhadiaMessageError('Encodage non pris en charge.')
    try:
        text = message.decode(encoding, errors='strict')
    except UnicodeError:
        raise PhadiaMessageError('Encodage du message à préciser ; aucun caractère n’a été remplacé.') from None
    if any(ord(c) < 32 and c not in '\r\n' for c in text):
        raise PhadiaMessageError('Exporter le contenu H/P/O/L, sans trames ni caractères de contrôle.')
    normalized = text.replace('\r\n', '\r').replace('\n', '\r').rstrip('\r')
    lines = normalized.split('\r')
    if not 4 <= len(lines) <= 10002 or any(not line for line in lines):
        raise PhadiaMessageError('Message incomplet ou lignes vides inattendues.')
    records = [line.split('|') for line in lines]
    if any(len(field) > 500 for record in records for field in record):
        raise PhadiaMessageError('Champ trop long.')
    header = records[0]
    if (len(header) != 14 or header[:2] != ['H', '\\^&'] or header[4] != 'MCA'
            or header[11:13] != ['P', '1'] or records[-1] != ['L', '1', 'F']):
        raise PhadiaMessageError('En-tête ou fin hors du profil SIL/middleware / Phadia observé.')
    message_at = _timestamp(header[13])
    patients, events, specimens = [], [], {}
    patient = None
    order_sequence = 0
    for record in records[1:-1]:
        if record[0] == 'P':
            if patient is not None and order_sequence == 0:
                raise PhadiaMessageError('Bloc patient sans demande.')
            if len(record) != 9 or record[1] != str(len(patients) + 1) or record[3] or record[4]:
                raise PhadiaMessageError('Bloc patient hors du profil observé.')
            _identifier(record[2], 'Identifiant porté par P')
            if not record[5]:
                raise PhadiaMessageError('Identité source absente.')
            birth = record[7]
            if birth:
                if not re.fullmatch(r'\d{8}', birth):
                    raise PhadiaMessageError('Date de naissance source invalide.')
                try:
                    datetime.strptime(birth, '%Y%m%d')
                except ValueError:
                    raise PhadiaMessageError('Date de naissance source invalide.') from None
            patient = {'source_identifier': record[2], 'name_field': record[5],
                       'additional_name_field': record[6], 'birth_date_field': birth,
                       'sex_field': record[8]}
            patients.append(patient)
            order_sequence = 0
        elif record[0] == 'O':
            if patient is None or len(record) != 26 or record[1] != str(order_sequence + 1) or record[25] != 'O':
                raise PhadiaMessageError('Ligne de demande hors du profil observé.')
            order_sequence += 1
            sid = _identifier(record[2], 'Numéro complet d’échantillon')
            if sid in specimens and specimens[sid] != patient:
                raise PhadiaMessageError('Identités contradictoires pour le même échantillon.')
            specimens[sid] = patient
            action = record[11]
            if action not in ('N', 'C'):
                raise PhadiaMessageError('Action de demande non qualifiée : seules N et C sont prises en charge.')
            # Do not silently discard new fields whose meaning is not established.
            if any(value for i, value in enumerate(record) if i not in (0, 1, 2, 4, 11, 13, 25)) or record[13] != '1':
                raise PhadiaMessageError('Champ de demande supplémentaire à qualifier.')
            tests = record[4].split('\\')
            if len(tests) > 100:
                raise PhadiaMessageError('Trop de codes dans une ligne.')
            for test in tests:
                parts = test.split('^')
                # Both forms were observed in MCA exports: ^^^CODE^1 from the
                # original Phadia and ^^^CODE from PCRLia (2026-11-15). Keep
                # the exact source value; do not guess other component meanings.
                if (len(parts) not in (4, 5) or parts[:3] != ['', '', '']
                        or (len(parts) == 5 and parts[4] != '1')):
                    raise PhadiaMessageError('Composition du code de demande à qualifier.')
                code = _identifier(parts[3], 'Code de demande')
                events.append({'sample_id': sid, 'request_code': code, 'action': action,
                               'state': 'attendu' if action == 'N' else 'annule',
                               'message_at': message_at, 'patient': dict(patient),
                               'source_test': test, 'order_sequence': order_sequence,
                               'dossier': None, 'site': None, 'date_demande': None,
                               'date_prelevement': None})
        else:
            raise PhadiaMessageError('Type de ligne non qualifié dans le téléchargement Phadia.')
    if not events or order_sequence == 0:
        raise PhadiaMessageError('Aucune demande dans le message.')
    return {'source': 'MCA / Phadia — profil export observé', 'message_at': message_at,
            'timezone': None, 'digest': hashlib.sha256(normalized.encode('utf-8')).hexdigest(),
            'patients': patients, 'events': events, 'transport_qualified': False,
            'complete_download': False}


def simulate_downloads(messages, encoding='ascii'):
    """Chronological offline projection; retains cancelled pairs and ignores repeats.

    This is a qualification helper, not a live queue/reconnect implementation.
    Equal timestamps with opposing actions in separate messages are ambiguous.
    """
    decoded = sorted((decode_orders(m, encoding) for m in messages), key=lambda m: m['message_at'])
    orders, seen, timeline = {}, set(), []
    duplicates = 0
    for message in decoded:
        if message['digest'] in seen:
            duplicates += 1
            continue
        seen.add(message['digest'])
        for event in message['events']:
            key = (event['sample_id'], event['request_code'])
            previous = orders.get(key)
            if (previous and previous['message_at'] == event['message_at']
                    and previous['digest'] != message['digest'] and previous['state'] != event['state']):
                raise PhadiaMessageError('Messages contradictoires au même horodatage : ordre chronologique indéterminé.')
            orders[key] = {**event, 'digest': message['digest']}
            timeline.append(event)
    return {'messages': len(seen), 'duplicates': duplicates, 'timeline': timeline,
            'orders': [orders[key] for key in sorted(orders)]}
