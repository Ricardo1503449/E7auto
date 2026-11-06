import base64
import pytest
from e7auto.platform import update_signing


def test_pinned_native_signature_and_qt_wgc_import_share_one_process():
    from e7auto.app import validate_wgc_import
    assert update_signing.self_check()
    assert validate_wgc_import() == (True, '')


@pytest.mark.parametrize('kind', ['payload', 'signature', 'signer'])
def test_native_verifier_rejects_modified_content_or_untrusted_signer(monkeypatch, kind):
    data = update_signing.SELF_TEST_DATA
    signature = base64.b64decode(update_signing.SELF_TEST_SIGNATURE)
    if kind == 'payload': data += b'changed'
    if kind == 'signature': signature = signature[:-1] + bytes([signature[-1] ^ 1])
    if kind == 'signer': monkeypatch.setattr(update_signing, 'SIGNER_SHA256', '0' * 64)
    with pytest.raises(ValueError):
        update_signing.verify_signature(data, signature)
