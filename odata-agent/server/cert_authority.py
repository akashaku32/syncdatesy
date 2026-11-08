"""
ca_authority.py

Server-side certificate authority helper.
Loads the CA key/cert once at startup and signs client CSRs on demand.

All cryptographic work here is synchronous/CPU-bound by nature (RSA signing).
Callers MUST invoke sign_csr() via asyncio.to_thread(...) so it doesn't block
the event loop that's also servicing every other connected agent's stream.
"""

from datetime import datetime, timedelta

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID


class InvalidCSRError(Exception):
    """Raised when a submitted CSR is malformed, unsigned, or doesn't match
    the identity the caller claims to be."""


class CertificateAuthority:
    def __init__(self, ca_key_path: str, ca_cert_path: str, validity_days: int = 90):
        with open(ca_key_path, "rb") as f:
            self.ca_key = serialization.load_pem_private_key(
                f.read(), password=None, backend=default_backend()
            )
        with open(ca_cert_path, "rb") as f:
            ca_cert_bytes = f.read()
        self.ca_cert = x509.load_pem_x509_certificate(ca_cert_bytes, default_backend())
        self.ca_cert_pem = ca_cert_bytes.decode("utf-8")
        self.validity_days = validity_days

    def sign_csr(self, csr_pem: str, expected_cn: str) -> str:
        """
        Validate and sign a CSR. Synchronous / CPU-bound - call via
        asyncio.to_thread from the servicer, never directly in a coroutine.

        Returns the new certificate as a PEM string.
        Raises InvalidCSRError if the CSR is malformed or doesn't match expected_cn.
        """
        try:
            csr = x509.load_pem_x509_csr(csr_pem.encode("utf-8"), default_backend())
        except ValueError as e:
            raise InvalidCSRError(f"Malformed CSR: {e}")

        if not csr.is_signature_valid:
            raise InvalidCSRError("CSR signature does not verify against its own public key")

        cn_attrs = csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        if not cn_attrs or cn_attrs[0].value != expected_cn:
            raise InvalidCSRError(
                f"CSR common name does not match expected identity '{expected_cn}'"
            )

        now = datetime.utcnow()

        cert = (
            x509.CertificateBuilder()
            .subject_name(csr.subject)
            .issuer_name(self.ca_cert.subject)
            .public_key(csr.public_key())
            .serial_number(x509.random_serial_number())
            # small backdate to tolerate clock skew between server and agent
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=self.validity_days))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    key_encipherment=True,
                    content_commitment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH]),
                critical=False,
            )
            .sign(self.ca_key, hashes.SHA256(), default_backend())
        )

        return cert.public_bytes(serialization.Encoding.PEM).decode("utf-8")


def get_peer_common_name(context) -> str | None:
    """
    Extract the CN of the client certificate that authenticated the current
    (already-mTLS-secured) RPC call. Used to confirm the caller requesting
    renewal for `cust_id` is actually the agent holding that identity's
    current certificate - not some other authenticated client.
    """
    auth_ctx = context.auth_context()
    cn_values = auth_ctx.get("x509_common_name")
    if not cn_values:
        return None
    return cn_values[0].decode("utf-8")