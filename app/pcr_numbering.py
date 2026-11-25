# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
"""PCRLia numbering: barcode -> laboratory family, site and relaunch address.

Identifiers remain text. These rules enrich the display, never the stored
specimen/request key, patient identity, source dossier, or request dates.
"""
from copy import deepcopy
import os
from datetime import date
import re


# Three numbering families recognized from the scanned barcode (see docs/ADAPTER.md):
#   family A : 8-digit dossier, the first two digits identify the site (each site listed separately);
#   family B : 10-digit dossier (one-digit year + month + day + ...), digits 6-7 identify the site;
#   family C : two-digit barcode prefix standing for one or two letters, then `c_digits` digits.
# A real installation starts EMPTY: sites are entered in « Paramètres PCRLia · Laboratoires et dossiers ».
# The EXAMPLE values below are fictitious and used only when PCRLIA_DEMO=1 (preview and tests).
FAMILY_LABELS = {'reseau_a': 'Réseau A', 'reseau_b': 'Réseau B', 'reseau_c': 'Réseau C'}
RESEAU_A = {'11': 'Site 11', '12': 'Site 12', '13': 'Site 13', '14': 'Site 14', '15': 'Site 15', '16': 'Site 16', '17': 'Site 17', '18': 'Site 18', '19': 'Site 19', '20': 'LABO TEST', '21': 'Site 21', '29': 'Z-LABO'}
RESEAU_B = {'99': 'Site B99', '98': 'Site B98', '97': 'Site B97', '96': 'Site B96', '95': 'Site B95', '94': 'Site B94', '93': 'Site B93', '92': 'Site B92', '91': 'Site B91', '90': 'Site B90', '89': 'Site B89', '88': 'Site B88', '87': 'Site B87'}
RESEAU_C = {'DA': '18', 'DC': '19', 'DD': '20', 'A': '01', 'B': '02', 'C': '03', 'D': '04', 'E': '05', 'F': '06', 'G': '07', 'H': '08', 'I': '09', 'J': '10', 'K': '11', 'L': '12', 'M': '13', 'N': '14', 'O': '15', 'P': '16', 'Q': '17', 'R': '21', 'S': '31', 'U': '25', 'V': '26', 'W': '27', 'X': '28', 'Y': '29', 'Z': '30'}
RESEAU_C_NAMES = {'B': 'Site C-B', 'C': 'Site C-C', 'D': 'Site C-D', 'E': 'Site C-E', 'F': 'Site C-F', 'G': 'Site C-G', 'H': 'Site C-H', 'I': 'Site C-I', 'J': 'Site C-J', 'K': 'Site C-K', 'L': 'Site C-L', 'M': 'Site C-M', 'N': 'Site C-N', 'O': 'Site C-O', 'P': 'Site C-P', 'Q': 'Site C-Q', 'R': 'Site C-R', 'S': 'Site C-S', 'U': 'Site C-U', 'V': 'Site C-V', 'W': 'Site C-W', 'X': 'Site C-X', 'Z': 'Site C-Z', 'DA': 'Site C-DA', 'DC': 'Site C-DC', 'DD': 'Site C-DD'}
RESEAU_A_EMAILS = {'11': 'site11@reseau-a.exemple.invalid', '12': 'site12@reseau-a.exemple.invalid', '13': 'site13@reseau-a.exemple.invalid', '14': 'site14@reseau-a.exemple.invalid', '15': 'site15@reseau-a.exemple.invalid', '16': 'site16@reseau-a.exemple.invalid', '17': 'site17@reseau-a.exemple.invalid', '18': 'site18@reseau-a.exemple.invalid', '19': 'site19@reseau-a.exemple.invalid', '21': 'site21@reseau-a.exemple.invalid'}
RESEAU_B_EMAILS = {'99': 'secretariat99@reseau-b.exemple.invalid', '98': 'secretariat98@reseau-b.exemple.invalid', '97': 'secretariat97@reseau-b.exemple.invalid', '96': 'secretariat96@reseau-b.exemple.invalid', '95': 'secretariat95@reseau-b.exemple.invalid', '94': 'secretariat94@reseau-b.exemple.invalid', '93': 'secretariat93@reseau-b.exemple.invalid', '92': 'secretariat92@reseau-b.exemple.invalid', '91': 'secretariat91@reseau-b.exemple.invalid', '90': 'secretariat90@reseau-b.exemple.invalid', '89': 'secretariat89@reseau-b.exemple.invalid', '88': 'secretariat88@reseau-b.exemple.invalid', '87': 'secretariat87@reseau-b.exemple.invalid'}
RESEAU_C_EMAILS = {'B': 'site-b@reseau-c.exemple.invalid', 'C': 'site-c@reseau-c.exemple.invalid', 'D': 'site-d@reseau-c.exemple.invalid', 'E': 'site-e@reseau-c.exemple.invalid', 'F': 'site-f@reseau-c.exemple.invalid', 'G': 'site-g@reseau-c.exemple.invalid', 'H': 'site-h@reseau-c.exemple.invalid', 'I': 'site-i@reseau-c.exemple.invalid', 'J': 'site-j@reseau-c.exemple.invalid', 'L': 'site-l@reseau-c.exemple.invalid', 'M': 'site-m@reseau-c.exemple.invalid', 'N': 'site-n@reseau-c.exemple.invalid', 'O': 'site-o@reseau-c.exemple.invalid', 'P': 'site-p@reseau-c.exemple.invalid', 'Q': 'site-q@reseau-c.exemple.invalid', 'R': 'site-r@reseau-c.exemple.invalid', 'S': 'site-s@reseau-c.exemple.invalid', 'U': 'site-u@reseau-c.exemple.invalid', 'V': 'site-v@reseau-c.exemple.invalid', 'W': 'site-w@reseau-c.exemple.invalid', 'X': 'site-x@reseau-c.exemple.invalid', 'Z': 'site-z@reseau-c.exemple.invalid', 'DA': 'site-da@reseau-c.exemple.invalid'}


EMAIL = re.compile(r'[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,180}\.[A-Za-z]{2,24}')


def demo():
    return os.environ.get('PCRLIA_DEMO') == '1'


def defaults():
    if not demo():
        return {'enabled': True, 'reseau_c_digits': 9, 'reseau_a': [], 'reseau_b': [], 'reseau_c': []}
    return {'enabled': True, 'reseau_c_digits': 9,
            'reseau_a': [{'prefix': k, 'label': v, 'email': RESEAU_A_EMAILS.get(k, '')} for k, v in RESEAU_A.items()],
            'reseau_b': [{'prefix': k, 'label': v, 'email': RESEAU_B_EMAILS.get(k, '')} for k, v in RESEAU_B.items()],
            'reseau_c': [{'prefix': k, 'barcode': v, 'label': RESEAU_C_NAMES.get(k, ''), 'email': RESEAU_C_EMAILS.get(k, '')}
                       for k, v in RESEAU_C.items()]}


def add_default_emails(numbering):
    """Give rows saved before addresses existed (no 'email' key) their directory address."""
    added = []
    if not demo():
        return added
    for family, emails in (('reseau_a', RESEAU_A_EMAILS), ('reseau_b', RESEAU_B_EMAILS), ('reseau_c', RESEAU_C_EMAILS)):
        for row in numbering.get(family, []):
            if 'email' not in row:
                row['email'] = emails.get(row['prefix'], '')
                if row['email']:
                    added.append(f"{family}:{row['prefix']}")
    return added


def validate(value):
    if (not isinstance(value, dict) or type(value.get('enabled')) is not bool
            or type(value.get('reseau_c_digits')) is not int or not 9 <= value['reseau_c_digits'] <= 20):
        raise ValueError('Numérotation des laboratoires invalide : Réseau C attend 9 à 20 chiffres après les lettres.')
    clean = {k: value[k] for k in ('enabled', 'reseau_c_digits')}
    for family in ('reseau_a', 'reseau_b', 'reseau_c'):
        rows, seen, barcodes = value.get(family), set(), set()
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError('Liste de correspondances de laboratoires invalide.')
        clean[family] = []
        for row in rows:
            pattern = r'[A-Z]{1,2}' if family == 'reseau_c' else r'[0-9]{2}'
            if (not isinstance(row, dict) or not isinstance(row.get('prefix'), str)
                    or not re.fullmatch(pattern, row['prefix']) or row['prefix'] in seen):
                raise ValueError('Chaque préfixe de laboratoire doit être valide et unique dans son groupe.')
            seen.add(row['prefix'])
            label = row.get('label', '')
            if (not isinstance(label, str) or len(label.strip()) > 100
                    or any(ord(c) < 32 for c in label) or (family != 'reseau_c' and not label.strip())):
                raise ValueError('Renseigner un nom de laboratoire de 100 caractères maximum.')
            item = {'prefix': row['prefix'], 'label': label.strip()}
            email = row.get('email', '')
            if not isinstance(email, str) or (email.strip() and not EMAIL.fullmatch(email.strip())):
                raise ValueError(f'Adresse mail invalide pour le laboratoire {row["prefix"]}.')
            item['email'] = email.strip()
            if family == 'reseau_c':
                barcode = row.get('barcode')
                if not isinstance(barcode, str) or not re.fullmatch(r'[0-9]{2}', barcode) or barcode in barcodes:
                    raise ValueError('Chaque code-barres Réseau C doit contenir deux chiffres, zéro initial inclus, et être unique.')
                barcodes.add(barcode)
                item['barcode'] = barcode
            clean[family].append(item)
    return clean


def classify(sample_id, rules):
    """Recognize complete numeric barcodes by length before interpreting prefixes."""
    if not rules['enabled'] or not isinstance(sample_id, str) or not re.fullmatch(r'[0-9]+', sample_id):
        return None
    base = sample_id[:-2]
    family, row, dossier = '', None, base
    if len(base) == 8:
        family = 'Réseau A'
        row = next((r for r in rules['reseau_a'] if r['prefix'] == base[:2]), None)
    elif len(base) == 10:
        # Check month/day without inventing the decade of the one-digit year.
        try:
            date(2000, int(base[1:3]), int(base[3:5]))
        except ValueError:
            return None
        family = 'Réseau B'
        row = next((r for r in rules['reseau_b'] if r['prefix'] == base[5:7]), None)
    elif len(base) == 2 + rules['reseau_c_digits']:
        family = 'Réseau C'
        row = next((r for r in rules['reseau_c'] if r['barcode'] == base[:2]), None)
        if row:
            dossier = row['prefix'] + base[2:]
    if not row:
        return None
    return {'dossier': dossier, 'site': row['label'] if family == 'Réseau A' else family,
            'lab_family': family, 'lab_code': row['prefix'], 'lab_detail': row['label'],
            'lab_email': row.get('email', '')}


def display(sample, config):
    """Apply reversible display rules; keep transmitted metadata available."""
    match = classify(sample['sample_id'], config['numbering'])
    result = {'dossier': sample.get('dossier', ''), 'dossier_derived': False,
              'site': sample.get('site', ''), 'site_source': sample.get('site', ''),
              'lab_family': '', 'lab_code': '', 'lab_detail': '', 'lab_email': ''}
    if match:
        result.update({k: v for k, v in match.items() if k != 'dossier'})
        if not result['dossier']:
            result.update(dossier=match['dossier'], dossier_derived=True)
    elif not result['dossier'] and 0 < config['dossier_strip'] < len(sample['sample_id']):
        result.update(dossier=sample['sample_id'][:-config['dossier_strip']], dossier_derived=True)
    return result


def sample_from_dossier(dossier, suffix, rules=None):
    """Explicit construction helper for previews; never normalizes scanned input."""
    rules = defaults() if rules is None else deepcopy(rules)
    if not isinstance(dossier, str) or not isinstance(suffix, str) or not re.fullmatch(r'[0-9]{2}', suffix):
        raise ValueError('Dossier et terminaison à fournir en texte, terminaison sur deux chiffres.')
    candidate = dossier + suffix
    match = re.fullmatch(r'([A-Z]{1,2})([0-9]+)', dossier)
    if match:
        row = next((r for r in rules['reseau_c'] if r['prefix'] == match[1]), None)
        if not row or len(match[2]) != rules['reseau_c_digits']:
            raise ValueError('Dossier Réseau C non reconnu.')
        candidate = row['barcode'] + match[2] + suffix
    resolved = classify(candidate, rules)
    if not resolved or resolved['dossier'] != dossier:
        raise ValueError('Dossier non reconnu par les correspondances des laboratoires.')
    return candidate
