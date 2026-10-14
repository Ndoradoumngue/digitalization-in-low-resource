"""Electronic signature (GED Phase 7.4) - PAdES signing and verification.

SDAI signs with the ORGANISATION's certificate (SIGNING_P12_PATH /
SIGNING_P12_PASSWORD - a PKCS#12 file mounted read-only), after the person
signing has re-entered their password. What the signature proves:

  - the PDF has not changed since (PAdES-B-B, SHA-256, verifiable in any
    PDF reader that trusts the organisation's certificate);
  - who signed and when, from SDAI's evidence: the visible stamp (name,
    role, date, reason), sdai_signatures (signer, IP, certificate
    fingerprint, hash of the signed file) and the hash-chained audit log.

That is a simple - or, with a certificate from a recognised provider and
the evidence above, advanced - electronic signature. NEVER a "qualified"
one: that would need a qualified certificate on a qualified device held by
the signer personally.

  load_signer()            the organisation's signer (cached) or SigningUnavailable
  to_pdf(path)             an image original as PDF (PDF/A-2b when ocrmypdf
                           is installed, else plain PDF)
  sign_pdf(data, ...)      one more signature as an incremental update - the
                           earlier signatures stay valid
  verify_pdf(data)         every embedded signature: intact / trusted /
                           coverage / signer name / time / certificate

No router imports at module level.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

SIGNING_P12_PATH = os.getenv("SIGNING_P12_PATH", "").strip()
SIGNING_P12_PASSWORD = os.getenv("SIGNING_P12_PASSWORD", "")
# Extra trust anchors for verification (PEM/DER files, comma-separated) -
# the CA chain of a certificate bought from a provider. The organisation's
# own certificate is always trusted.
SIGNING_TRUST_ROOTS = os.getenv("SIGNING_TRUST_ROOTS", "").strip()
SIGNING_LOCATION = os.getenv("SIGNING_LOCATION", "").strip()

IMAGE_MIMES = ("image/png", "image/jpeg", "image/tiff")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".tif", ".tiff")

# Visible stamps, bottom-left of the last page, one above the other.
_STAMP_W, _STAMP_H, _STAMP_GAP, _STAMP_X, _STAMP_Y = 250, 62, 8, 40, 36


class SigningUnavailable(Exception):
    """No (usable) organisation certificate on this node."""


class SigningError(Exception):
    """The file can't be signed (encrypted, broken PDF ...)."""


@dataclass(frozen=True)
class CertInfo:
    subject: str
    issuer: str
    fingerprint: str  # sha256 of the DER certificate, hex
    not_before: datetime
    not_after: datetime
    self_signed: bool


def configured() -> bool:
    return bool(SIGNING_P12_PATH)


@lru_cache(maxsize=4)
def _load(path: str, password: str, mtime: float):
    from pyhanko.sign import signers

    try:
        signer = signers.SimpleSigner.load_pkcs12(
            path, passphrase=password.encode() or None
        )
    except Exception as exc:
        raise SigningUnavailable(f"cannot read {path}: {type(exc).__name__}") from None
    if signer is None:
        raise SigningUnavailable(f"cannot read {path}")
    return signer


def load_signer():
    if not configured():
        raise SigningUnavailable("SIGNING_P12_PATH is not set")
    path = Path(SIGNING_P12_PATH)
    if not path.is_file():
        raise SigningUnavailable(f"{path} not found")
    return _load(str(path), SIGNING_P12_PASSWORD, path.stat().st_mtime)


def cert_info(cert) -> CertInfo:
    """`cert`: an asn1crypto certificate (pyHanko's)."""
    return CertInfo(
        subject=cert.subject.human_friendly,
        issuer=cert.issuer.human_friendly,
        fingerprint=hashlib.sha256(cert.dump()).hexdigest(),
        not_before=cert.not_valid_before,
        not_after=cert.not_valid_after,
        self_signed=cert.self_signed != "no",
    )


def signer_info() -> CertInfo:
    return cert_info(load_signer().signing_cert)


def status() -> dict:
    """For the UI: is signing possible on this node, with which certificate."""
    try:
        info = signer_info()
    except SigningUnavailable as exc:
        return {"configured": False, "reason": str(exc) if configured() else None}
    now = datetime.now(timezone.utc)
    return {
        "configured": True,
        "subject": info.subject,
        "issuer": info.issuer,
        "fingerprint": info.fingerprint,
        "not_after": info.not_after.isoformat(),
        "expired": info.not_after < now,
        "self_signed": info.self_signed,
    }


# ── Conversion ────────────────────────────────────────────────────────────────


def is_pdf(path: str, mime: str | None) -> bool:
    return (mime or "") == "application/pdf" or path.lower().endswith(".pdf")


def is_image(path: str, mime: str | None) -> bool:
    return (mime or "") in IMAGE_MIMES or path.lower().endswith(IMAGE_EXTS)


def to_pdf(path: Path) -> tuple[bytes, bool]:
    """An image file as PDF. Returns (pdf, is_pdfa): PDF/A-2b when the
    ocrmypdf command is available (no OCR - --skip-text), else the plain
    img2pdf output (lossless, the image is embedded unchanged)."""
    import img2pdf

    try:
        data = img2pdf.convert(str(path), rotation=img2pdf.Rotation.ifvalid)
    except Exception as exc:
        raise SigningError(f"cannot convert {path.name}: {exc}") from None
    if not shutil.which("ocrmypdf"):
        return data, False
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "in.pdf", Path(tmp) / "out.pdf"
        src.write_bytes(data)
        try:
            subprocess.run(
                [
                    "ocrmypdf",
                    "--skip-text",
                    "--output-type",
                    "pdfa-2",
                    "--quiet",
                    str(src),
                    str(dst),
                ],
                check=True,
                timeout=300,
                capture_output=True,
            )
            return dst.read_bytes(), True
        except Exception as exc:  # keep the lossless PDF rather than fail
            logger.warning("PDF/A conversion failed (%s) - keeping the plain PDF", exc)
            return data, False


# ── Signing ───────────────────────────────────────────────────────────────────


def existing_signatures(data: bytes) -> int:
    from pyhanko.pdf_utils.reader import PdfFileReader

    try:
        return len(PdfFileReader(io.BytesIO(data), strict=False).embedded_signatures)
    except Exception:
        return 0


def sign_pdf(
    data: bytes,
    *,
    signer_name: str,
    role: str,
    reason: str,
    signed_at: datetime,
    locale: str = "fr",
) -> bytes:
    """`data` with one more PAdES signature (incremental update) and its
    visible stamp. Raises SigningUnavailable / SigningError."""
    from pyhanko import stamp
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import fields, signers

    signer = load_signer()
    n = existing_signatures(data) + 1
    field = f"SDAI_Signature_{n}"
    y = _STAMP_Y + (n - 1) * (_STAMP_H + _STAMP_GAP)
    when = signed_at.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    if locale == "en":
        template = (
            "Electronically signed by %(who)s\n%(role)s - %(when)s\nReason: %(reason)s"
        )
    else:
        template = "Signé électroniquement par %(who)s\n%(role)s - %(when)s\nMotif : %(reason)s"
    try:
        writer = IncrementalPdfFileWriter(io.BytesIO(data), strict=False)
        if writer.prev.encrypted:
            raise SigningError("encrypted PDF")
        fields.append_signature_field(
            writer,
            fields.SigFieldSpec(
                field, on_page=-1, box=(_STAMP_X, y, _STAMP_X + _STAMP_W, y + _STAMP_H)
            ),
        )
        meta = signers.PdfSignatureMetadata(
            field_name=field,
            reason=reason,
            name=signer_name,
            location=SIGNING_LOCATION or None,
            subfilter=fields.SigSeedSubFilter.PADES,
            md_algorithm="sha256",
        )
        pdf_signer = signers.PdfSigner(
            meta,
            signer=signer,
            stamp_style=stamp.TextStampStyle(stamp_text=template, border_width=1),
        )
        out = io.BytesIO()
        pdf_signer.sign_pdf(
            writer,
            output=out,
            appearance_text_params={
                "who": signer_name,
                "role": role,
                "when": when,
                "reason": reason,
            },
        )
        return out.getvalue()
    except (SigningUnavailable, SigningError):
        raise
    except Exception as exc:
        raise SigningError(f"{type(exc).__name__}: {exc}") from None


# ── Verification ──────────────────────────────────────────────────────────────


def _trust_roots() -> list:
    from asn1crypto import pem
    from asn1crypto import x509 as ax509

    roots = []
    try:
        roots.append(load_signer().signing_cert)
    except SigningUnavailable:
        pass
    for item in filter(None, (p.strip() for p in SIGNING_TRUST_ROOTS.split(","))):
        try:
            raw = Path(item).read_bytes()
            if pem.detect(raw):
                roots += [
                    ax509.Certificate.load(der)
                    for _, _, der in pem.unarmor(raw, multiple=True)
                ]
            else:
                roots.append(ax509.Certificate.load(raw))
        except Exception:
            logger.warning("cannot read trust root %s", item)
    return roots


def verify_pdf(data: bytes) -> list[dict]:
    """Every signature embedded in the PDF, oldest first."""
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko_certvalidator import ValidationContext

    try:
        reader = PdfFileReader(io.BytesIO(data), strict=False)
        embedded = reader.embedded_signatures
    except Exception as exc:
        raise SigningError(f"{type(exc).__name__}: {exc}") from None
    roots = _trust_roots()
    results = []
    for sig in embedded:
        vc = ValidationContext(
            trust_roots=roots, allow_fetching=False, revocation_mode="soft-fail"
        )
        entry = {
            "field": sig.field_name,
            "signer_name": str(sig.sig_object.get("/Name") or "") or None,
            "reason": str(sig.sig_object.get("/Reason") or "") or None,
        }
        try:
            st = validate_pdf_signature(sig, vc)
            info = cert_info(st.signing_cert)
            entry.update(
                {
                    "intact": bool(st.intact),
                    "valid": bool(st.valid),
                    "trusted": bool(st.trusted),
                    "coverage": st.coverage.name if st.coverage else None,
                    "modification_level": st.modification_level.name
                    if st.modification_level
                    else None,
                    "signed_at": st.signer_reported_dt.isoformat()
                    if st.signer_reported_dt
                    else None,
                    "cert_subject": info.subject,
                    "cert_fingerprint": info.fingerprint,
                    "summary": st.summary(),
                }
            )
        except Exception as exc:
            entry.update(
                {
                    "intact": False,
                    "valid": False,
                    "trusted": False,
                    "error": str(exc)[:300],
                }
            )
        entry["ok"] = bool(
            entry.get("intact") and entry.get("valid") and entry.get("trusted")
        )
        results.append(entry)
    return results
