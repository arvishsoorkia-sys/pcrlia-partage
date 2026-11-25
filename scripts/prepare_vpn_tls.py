# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
"""Generate a local trial certificate without installing trust or sending anything online."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def generate(address, folder):
    ip = ipaddress.ip_address(address)
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = folder/'server-cert.pem', folder/'server-key.pem'
    if cert_path.exists() or key_path.exists():
        if not cert_path.exists() or not key_path.exists():
            raise ValueError('Certificat ou clé manquant : ne pas remplacer silencieusement les fichiers existants.')
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        if ip not in cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.IPAddress):
            raise ValueError('Ce certificat ne couvre pas l’adresse VPN actuelle. Préparer un nouveau dossier de certificat pour cette adresse.')
        if cert.not_valid_after_utc < datetime.now(timezone.utc)+timedelta(days=1):
            raise ValueError('Certificat expiré ou proche de l’expiration ; son renouvellement doit être explicite.')
        key = serialization.load_pem_private_key(key_path.read_bytes(),password=None)
        if key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo) != cert.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo):
            raise ValueError('La clé et le certificat ne correspondent pas.')
    else:
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f'CHLAS VPN {address}')])
        stamp = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(stamp-timedelta(minutes=5))
                .not_valid_after(stamp+timedelta(days=30))
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip)]),critical=False)
                .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),critical=False)
                .add_extension(x509.KeyUsage(digital_signature=True,content_commitment=False,key_encipherment=False,
                    data_encipherment=False,key_agreement=False,key_cert_sign=False,crl_sign=False,encipher_only=False,decipher_only=False),critical=True)
                .sign(key,hashes.SHA256()))
        # This folder must be in the local user profile, never the synchronised source project.
        with key_path.open('xb') as f:
            f.write(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        with cert_path.open('xb') as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
    public = folder/'CHLAS-VPN-public.cer'
    public.write_bytes(cert.public_bytes(serialization.Encoding.DER))
    info = {'address':address,'sha256':cert.fingerprint(hashes.SHA256()).hex().upper(),
            'valid_until_utc':cert.not_valid_after_utc.isoformat(),'cert_file':str(cert_path),
            'key_file':str(key_path),'public_certificate':str(public),'trust_installed':False}
    (folder/'certificate-info.json').write_text(json.dumps(info,indent=2),encoding='utf-8')
    return info


def main():
    p=argparse.ArgumentParser();p.add_argument('--address',required=True);p.add_argument('--folder',required=True);args=p.parse_args()
    folder=Path(args.folder).resolve()
    if any(x.lower().startswith(('onedrive','dropbox','google drive')) for x in folder.parts):
        p.error('Le dossier des clés doit être local et non synchronisé.')
    try:
        print(json.dumps(generate(args.address,folder),indent=2))
    except ValueError as exc:
        p.error(str(exc))


if __name__=='__main__':main()
