import base64
import json
import pytest

from e7auto.core.update_feed import decode_feed, MAX_FEED_BYTES, validate_payload
from tests.helpers.updates import feed, payload, verify_test_signature


@pytest.mark.parametrize('field,value', [('repository', 'other/repo'), ('version', 'v9.0.0-beta'),
    ('commit', 'bad'), ('published_at', 'yesterday'), ('notes', []), ('asset', {})])
def test_signed_feed_rejects_invalid_release_identity(field, value):
    data = payload()
    data[field] = value
    with pytest.raises((ValueError, TypeError)):
        validate_payload(data)


def test_feed_tampering_is_rejected_before_payload_is_trusted():
    envelope = json.loads(feed())
    envelope['payload'] = base64.b64encode(b'{"repository":"evil/repo"}').decode()
    with pytest.raises(ValueError, match='signature'):
        decode_feed(json.dumps(envelope).encode(), verify_test_signature)
    assert decode_feed(feed(), verify_test_signature).asset.sha256 == 'a' * 64


@pytest.mark.parametrize('raw', [b'{}', b'<html>proxy error</html>', b'x' * (MAX_FEED_BYTES + 1),
    b'{"schema_version":1,"schema_version":1,"payload":"","signature":""}', b'[' * 5000],
    ids=['missing', 'html', 'oversize', 'duplicate', 'nested'])
def test_invalid_or_excessive_feed_is_rejected(raw):
    with pytest.raises(ValueError):
        decode_feed(raw, verify_test_signature)


@pytest.mark.parametrize('field,value', [('size', True), ('size', 0), ('sha256', 'x' * 64),
    ('url', 'https://evil.test/setup.exe'), ('name', 'other.exe')])
def test_asset_fields_are_bound_to_the_signed_version(field, value):
    data = payload()
    data['asset'][field] = value
    with pytest.raises(ValueError):
        validate_payload(data)
