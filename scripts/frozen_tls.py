"""Provide HTTPS roots when the build interpreter's certificate paths are absent."""
import os
from pathlib import Path
import ssl
import sys

if not os.environ.get("SSL_CERT_FILE") and not os.environ.get("SSL_CERT_DIR"):
    defaults = ssl.get_default_verify_paths()
    if not defaults.cafile and not defaults.capath:
        candidates = [
            Path("/etc/ssl/certs/ca-certificates.crt"),
            Path("/etc/pki/tls/certs/ca-bundle.crt"),
            Path(sys._MEIPASS) / "certs" / "cacert.pem",
        ]
        for candidate in candidates:
            if candidate.is_file():
                os.environ["SSL_CERT_FILE"] = str(candidate)
                break
