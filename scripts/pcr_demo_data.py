# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
"""Fictitious PCR requests for the isolated preview only."""
import csv
import io
from app.pcr import COLUMNS, PATIENT_COLUMNS
from app.pcr_numbering import RESEAU_A, sample_from_dossier


def requests(legacy=False):
    rows = []
    def add(dossier, suffix, name, pid, code='IST_A', state='attendu', day='2026-11-14'):
        rows.append(['', sample_from_dossier(dossier,suffix), code, '', 'Prélèvement fictif',
                     day, '', '', state, pid, name+' (fictif)'])
    for i, (prefix, label) in enumerate(RESEAU_A.items(),1):
        suffix, code = ('44','PAR_A') if prefix=='15' else ('85','COV_A') if prefix=='19' else ('88','IST_A')
        add(prefix+'990001',suffix,'TEST '+label,f'FICTIF-ANA-{prefix}',code,day='2026-11-13' if i%3==0 else '2026-11-14')
    add('11990001','87','TEST Site 11','FICTIF-ANA-11')
    add('11990001','86','TEST Site 11','FICTIF-ANA-11')
    add('11990001','88','TEST Site 11','FICTIF-ANA-11','IST_B')
    for code, label in [('95','Site B95'),('99','Site B99'),('88','Site B88')]:
        add('61114'+code+'901','88','TEST '+label,'FICTIF-BIO-'+code)
    add('6111495901','87','TEST Site B95','FICTIF-BIO-95','IST_B')
    for prefix, label in [('C','Site C-C'),('M','Site C-M'),('DA','Site C-DA'),('S','Site C-S')]:
        add(prefix+'261114901','88','TEST '+label,'FICTIF-BLM-'+prefix,state='annule' if prefix=='S' else 'attendu')
    add('C261114901','86','TEST Site C-C','FICTIF-BLM-C','IST_B')
    if not legacy:
        # Current catalogue: one IST analysis per suffix. Legacy fixtures remain
        # available solely to exercise historical multi-request tube handling.
        rows = [row for row in rows if not (row[1] == '1199000188' and row[2] == 'IST_B')]
        for row in rows:
            row[2] = {'88': 'CTNG1', '87': 'CTNG2', '86': 'CTNG3',
                      '85': 'COVID', '44': 'PARASITO'}[row[1][-2:]]
    out=io.StringIO();writer=csv.writer(out,delimiter=';')
    writer.writerow(COLUMNS+PATIENT_COLUMNS);writer.writerows(rows)
    return out.getvalue()


def seed(pcr, legacy=False):
    for code,label,group in ([('IST_A','PCR Chlamydia / Gonocoque','ist'),('IST_B','PCR Mycoplasma genitalium','ist'),
                             ('PAR_A','PCR Parasites des selles','parasito'),('COV_A','PCR SARS-CoV-2','covid')]
                             if legacy else []):
        pcr.configure_code({'code':code,'label':label,'group_key':group,'active':True,'revision':0},'demo-admin')
    data={'content':requests(legacy),'source':'DÉMONSTRATION — réseaux A / B / C — données entièrement fictives'}
    pcr.import_list({**data,**pcr.preview(data),'confirmed':True},'demo-admin')
    for i,sid in enumerate(['1199000188','611149590188','0326111490188'],1):
        pcr.scan({'sample_id':sid,'request_id':f'demo-laboratoires-{i:04d}'},'demo-admin')
