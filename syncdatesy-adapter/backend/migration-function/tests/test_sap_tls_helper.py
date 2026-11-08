import os
import unittest
import datetime
from unittest.mock import patch

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from utils.sap_tls_helper import build_session_from_config, PinnedCertAdapter

# Generate valid test PEM
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SAP_TEST")])
now = datetime.datetime.now(datetime.timezone.utc)
cert_obj = (
    x509.CertificateBuilder()
    .subject_name(name)
    .issuer_name(name)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(now)
    .not_valid_after(now + datetime.timedelta(days=365))
    .sign(key, hashes.SHA256())
)
VALID_PEM = cert_obj.public_bytes(serialization.Encoding.PEM)


class TestSAPTLSHelper(unittest.TestCase):
    def test_build_session_ca_trusted(self):
        config = {"tls_verification_mode": "ca_trusted"}
        session = build_session_from_config(config)
        self.assertTrue(session.verify)

    @patch("utils.sap_tls_helper.fetch_secret_to_tempfile")
    def test_build_session_pinned_leaf_no_san(self, mock_fetch):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as f:
            f.write(VALID_PEM)
            tmp_path = f.name
        
        mock_fetch.return_value = tmp_path

        config = {
            "tls_verification_mode": "pinned_leaf_no_san",
            "tls_cert_secret_name": "sap-cert-1-test"
        }
        session = build_session_from_config(config)
        adapter = session.adapters.get("https://")
        self.assertIsInstance(adapter, PinnedCertAdapter)
        self.assertTrue(adapter.skip_hostname)

    @patch("os.path.exists", return_value=False)
    @patch("utils.sap_tls_helper.classify_cert")
    @patch("utils.sap_tls_helper.store_cert_secret")
    @patch("utils.sap_tls_helper.fetch_secret_to_tempfile")
    def test_build_session_inline_classification_during_test_connection(self, mock_fetch, mock_store, mock_classify, mock_exists):
        from utils.sap_tls_helper import _CERT_FILE_CACHE
        _CERT_FILE_CACHE.clear()
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as f:
            f.write(VALID_PEM)
            tmp_path = f.name
        
        mock_fetch.return_value = tmp_path
        mock_classify.return_value = ("pinned_leaf_no_san", {
            "pem_bytes": VALID_PEM,
            "fingerprint": "abc123testfp",
            "not_valid_after": "2027-12-31T23:59:59+00:00",
            "sans": [],
            "is_self_signed": True,
        })
        mock_store.return_value = "sap-cert-pending-ecxnet.ddns.net-44300"

        # Pass raw host/port before SourceConfig or tls_verification_mode exists
        config = {"host": "ecxnet.ddns.net", "port": 44300}
        session = build_session_from_config(config)

        # Assert inline classification ran and populated config
        self.assertEqual(config["tls_verification_mode"], "pinned_leaf_no_san")
        self.assertEqual(config["tls_cert_fingerprint"], "abc123testfp")
        self.assertIn("sap-cert-ecxnet-ddns-net-44300", config["tls_cert_secret_name"])
        
        adapter = session.adapters.get("https://")
        self.assertIsInstance(adapter, PinnedCertAdapter)
        self.assertTrue(adapter.skip_hostname)

if __name__ == "__main__":
    unittest.main()
