"""Generate a SELF-SIGNED organisation certificate for electronic signatures
(GED Phase 7.4) - for tests, demos and a first installation only.

    python scripts/generate_signing_cert.py --org "My Organisation" --country XX \
        --out /secrets/sdai-signing.p12 --password '...' [--years 3]

Then set SIGNING_P12_PATH / SIGNING_P12_PASSWORD (see .env.example).

PDF readers show signatures made with a self-signed certificate as "validity
unknown" until the certificate is trusted on that computer: SDAI itself
verifies them (it trusts its own certificate), but for documents leaving the
organisation obtain a certificate from a recognised provider (see the
"Electronic signature" section of README.md). Keep the .p12 file and its
password secret: whoever holds them can sign in the organisation's name.
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def build(org: str, common_name: str, country: str, years: int, password: str) -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    attrs = [
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, org),
    ]
    if country:
        attrs.append(x509.NameAttribute(NameOID.COUNTRY_NAME, country))
    name = x509.Name(attrs)
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=365 * years))
        # Self-signed: it is its own CA, so verifiers can anchor trust in it.
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=True,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.EMAIL_PROTECTION]),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    return pkcs12.serialize_key_and_certificates(
        common_name.encode(),
        key,
        cert,
        None,
        serialization.BestAvailableEncryption(password.encode()),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--org", required=True, help="organisation name (O=)")
    parser.add_argument(
        "--cn", help="certificate name (CN=), default: '<org> - signature'"
    )
    parser.add_argument("--country", help="two-letter country code (C=), optional")
    parser.add_argument("--years", type=int, default=3)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--password", help="PKCS#12 password (default: $SIGNING_P12_PASSWORD)"
    )
    parser.add_argument(
        "--force", action="store_true", help="overwrite an existing file"
    )
    args = parser.parse_args(argv)
    password = args.password or os.getenv("SIGNING_P12_PASSWORD", "")
    if len(password) < 8:
        parser.error("a password of at least 8 characters is required")
    if args.out.exists() and not args.force:
        parser.error(f"{args.out} exists (use --force to replace it)")
    data = build(
        args.org,
        args.cn or f"{args.org} - signature",
        args.country or "",
        args.years,
        password,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    print(
        f"wrote {args.out} (self-signed, {args.years} years) - set SIGNING_P12_PATH to it"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
