"""Create an app-scoped local CA and SAN certificate; never install a system root."""
import argparse
import csv
import subprocess
import ipaddress
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID


def provision(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        # Protect keys before creating them; Windows chmod alone does not set an ACL.
        identity = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], text=True)
        sid = next(csv.reader(identity.strip().splitlines()))[1]
        if not sid.startswith('S-1-'):
            raise ValueError('Cannot determine the current Windows user SID.')
        subprocess.run(['icacls', str(folder), '/inheritance:r', '/grant:r',
                        f'*{sid}:(OI)(CI)F', '*S-1-5-18:(OI)(CI)F'],
                       stdout=subprocess.DEVNULL, check=True)
    names = ['ca.pem', 'ca-key.pem', 'server.pem', 'server-key.pem']
    if any((folder/name).exists() for name in names):
        if not all((folder/name).is_file() for name in names):
            raise ValueError('Incomplete existing TLS material; refusing to overwrite it.')
        return
    now = datetime.now(timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Kaspi local catalogue CA')])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=3650))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
              key_encipherment=False, data_encipherment=False, key_agreement=False,
              key_cert_sign=True, crl_sign=True, encipher_only=None, decipher_only=None), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    certificate = (x509.CertificateBuilder()
          .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')]))
          .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=365))
          .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
          .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),
              x509.IPAddress(ipaddress.ip_address('127.0.0.1')),
              x509.IPAddress(ipaddress.ip_address('::1'))]), critical=False)
          .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
          .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    for name, value in [('ca.pem', ca.public_bytes(serialization.Encoding.PEM)),
                        ('server.pem', certificate.public_bytes(serialization.Encoding.PEM)),
                        ('ca-key.pem', ca_key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8, serialization.NoEncryption())),
                        ('server-key.pem', key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))]:
        with (folder/name).open('xb') as output:
            output.write(value)
        os.chmod(folder/name, 0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export-saved-catalog', action='store_true',
                        help='Recover the latest real XML snapshot already stored in the working database.')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    folder = root/'.local-catalog'
    provision(folder)
    if args.export_saved_catalog:
        # Read configuration without constructing an app or triggering any migrations/network calls.
        import sys
        sys.path.insert(0, str(root))
        from kaspibot.settings import local_settings
        settings = local_settings(root)
        database = Path(os.environ.get('KASPIBOT_DATABASE') or settings.get('KASPIBOT_DATABASE') or root/'app.db')
        if not database.is_absolute():
            database = root/database
        with sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True) as connection:
            row = connection.execute('SELECT raw_xml FROM seller_catalog_imports ORDER BY source_date DESC, id DESC LIMIT 1').fetchone()
        if row is None:
            raise ValueError('No saved real catalogue is available. Supply your own catalog.xml.')
        with (folder/'catalog.xml').open('xb') as output:
            output.write(row[0])
    print('Local TLS files prepared. Configure KASPI_CATALOG_CA_BUNDLE=.local-catalog/ca.pem.')
    print('No Windows trust-store or startup settings were changed.')


if __name__ == '__main__':
    main()
