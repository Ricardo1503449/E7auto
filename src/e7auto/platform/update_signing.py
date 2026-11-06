"""Verify the publisher's CMS signature with Windows CryptoAPI and a pinned key."""
import base64
import hashlib
import hmac

import win32crypt
import pywintypes

SIGNER_SHA256 = '9c421bb8ffc94bba30116a6638a378814f0ba24d18fbeb627334b67366ca3346'
SELF_TEST_DATA = b'E7auto update feed signature self-test v1'
SELF_TEST_SIGNATURE = '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'


def verify_signature(data: bytes, signature: bytes) -> None:
    certificate = None
    try:
        certificate = win32crypt.CryptVerifyDetachedMessageSignature(0, signature, (data,), None)
        actual = hashlib.sha256(certificate.CertEncoded).hexdigest()
        if not SIGNER_SHA256 or not hmac.compare_digest(actual, SIGNER_SHA256):
            raise ValueError('更新清单签名者不受信任')
    except pywintypes.error as exc:
        raise ValueError('更新清单签名验证失败') from exc
    finally:
        if certificate is not None:
            certificate.CertFreeCertificateContext()


def self_check() -> bool:
    verify_signature(SELF_TEST_DATA, base64.b64decode(SELF_TEST_SIGNATURE, validate=True))
    return True
