import base64
import hashlib
import json
import pytest

from e7auto.platform import update_signing
from scripts.release import update_feed
from tests.helpers.updates import feed, payload, verify_test_signature


def published_release():
    data = payload()
    asset = data['asset']
    return {'id': 1, 'tag_name': data['version'], 'draft': False, 'prerelease': False,
            'published_at': data['published_at'], 'body': data['notes'], 'assets': [{
                'name': asset['name'], 'browser_download_url': asset['url'], 'size': asset['size'],
                'state': 'uploaded', 'digest': 'sha256:' + asset['sha256']}]}


@pytest.mark.parametrize('kind', ['valid', 'draft', 'digest', 'commit', 'extra_asset'])
def test_feed_requires_the_published_verified_installer(kind):
    release = published_release()
    check = {'version': '9.0.0', 'commit': 'a' * 40, 'installer_size': 123, 'installer_sha256': 'a' * 64}
    commit = 'b' * 40 if kind == 'commit' else check['commit']
    if kind == 'draft': release['draft'] = True
    if kind == 'digest': release['assets'][0]['digest'] = 'sha256:' + 'b' * 64
    if kind == 'extra_asset': release['assets'].append(release['assets'][0].copy())
    if kind == 'valid':
        assert update_feed.release_payload(check, release, commit) == payload()
    else:
        with pytest.raises(ValueError): update_feed.release_payload(check, release, commit)


class Remote:
    def __init__(self, previous=None, *, conflict=False, change_latest=False):
        self.previous, self.conflict, self.change_latest = previous, conflict, change_latest
        self.ref = 'c' * 40 if previous else None
        self.mutations = []
        self.content = previous
        self.latest_reads = 0
    def __call__(self, method, path, body=None, *, missing=False):
        if path == '/releases/latest':
            self.latest_reads += 1
            result = published_release()
            if self.change_latest and self.latest_reads > 1: result['tag_name'] = 'v10.0.0'
            return result
        if path == '/git/ref/tags/v9.0.0': return {'object': {'type': 'commit', 'sha': 'a' * 40}}
        if path == '/git/ref/heads/updates': return {'object': {'sha': self.ref}} if self.ref else None
        if path == '/git/commits/' + 'c' * 40: return {'tree': {'sha': 'd' * 40}}
        if path == '/git/trees/' + 'd' * 40:
            return {'truncated': False, 'tree': [{'path': 'latest.json', 'type': 'blob', 'mode': '100644', 'sha': 'e' * 40}]}
        if path == '/git/blobs/' + 'e' * 40: return {'content': base64.b64encode(self.previous).decode()}
        if path.startswith('/contents/latest.json?ref='):
            return {'content': base64.b64encode(self.content).decode()}
        self.mutations.append((method, path, body))
        if method == 'POST' and path == '/git/trees':
            self.content = body['tree'][0]['content'].encode('ascii')
            return {'sha': 'f' * 40}
        if method == 'POST' and path == '/git/commits': return {'sha': 'b' * 40}
        if path in ('/git/refs', '/git/refs/heads/updates'):
            if self.conflict: raise RuntimeError('concurrent reference update')
            self.ref = body['sha']
            return {}
        raise AssertionError((method, path))


@pytest.mark.parametrize('existing', [False, True])
def test_publication_is_narrow_nonforced_and_verifies_the_remote(existing):
    remote = Remote(feed('v8.0.0') if existing else None)
    assert update_feed.publish(remote, feed(), verify=verify_test_signature) == 'b' * 40
    tree = next(body for _, path, body in remote.mutations if path == '/git/trees')
    assert [item['path'] for item in tree['tree']] == ['latest.json']
    assert ('base_tree' in tree) is existing
    reference = remote.mutations[-1]
    assert reference[2] == ({'sha': 'b' * 40, 'force': False} if existing else {'ref': 'refs/heads/updates', 'sha': 'b' * 40})


@pytest.mark.parametrize('kind', ['rollback', 'identity_conflict', 'concurrent', 'latest_changed'])
def test_publication_failure_does_not_overwrite_the_existing_branch(kind):
    previous = feed('v10.0.0') if kind == 'rollback' else feed(sha256='b' * 64) if kind == 'identity_conflict' else feed('v8.0.0')
    remote = Remote(previous, conflict=kind == 'concurrent', change_latest=kind == 'latest_changed')
    with pytest.raises((ValueError, RuntimeError)):
        update_feed.publish(remote, feed(), verify=verify_test_signature)
    assert remote.ref == 'c' * 40
    if kind in ('rollback', 'identity_conflict'):
        assert not remote.mutations


def test_local_dpapi_signing_roundtrip_and_init_refuses_replacement(tmp_path, monkeypatch):
    key_area = tmp_path / 'private'
    monkeypatch.setattr(update_feed, 'development_tool_path', lambda *args, **kwargs: key_area)
    public = update_feed.signing_identity(initialize=True)
    key = key_area / 'update-signing.pfx.dpapi'
    original_hash = hashlib.sha256(key.read_bytes()).hexdigest()
    with pytest.raises(RuntimeError): update_feed.signing_identity(initialize=True)
    assert hashlib.sha256(key.read_bytes()).hexdigest() == original_hash
    monkeypatch.setattr(update_feed, 'SIGNER_SHA256', public['fingerprint'])
    monkeypatch.setattr(update_signing, 'SIGNER_SHA256', public['fingerprint'])
    raw = json.dumps(payload()).encode()
    input_path = tmp_path / 'payload.json'
    input_path.write_bytes(raw)
    signed = update_feed.signing_identity(payload=input_path)
    update_signing.verify_signature(raw, base64.b64decode(signed['signature']))
    from e7auto.core.update_feed import decode_feed
    envelope = json.dumps({'schema_version': 1, 'payload': base64.b64encode(raw).decode(),
                           'signature': signed['signature']}).encode()
    assert decode_feed(envelope, update_signing.verify_signature).asset.sha256 == 'a' * 64
    with pytest.raises(ValueError):
        update_signing.verify_signature(raw + b'changed', base64.b64decode(signed['signature']))


def test_release_rejects_missing_signature_runtime_extension(tmp_path):
    from scripts.release.verify_release import verify_required_release_files
    (tmp_path / 'winrt').mkdir()
    (tmp_path / 'winrt/_winrt_windows_foundation.pyd').write_bytes(b'fixture')
    assert verify_required_release_files(tmp_path) == ['missing required release file: win32crypt.pyd']
    (tmp_path / 'win32crypt.pyd').write_bytes(b'fixture')
    assert verify_required_release_files(tmp_path) == []


@pytest.mark.parametrize('kind', ['unchanged', 'latest_changed', 'reference_changed'])
def test_idempotent_publication_rechecks_live_references_without_new_commits(kind):
    remote = Remote(feed(), change_latest=kind == 'latest_changed')
    reads = [0]
    def request(method, path, body=None, **kwargs):
        if path == '/git/ref/heads/updates':
            reads[0] += 1
            if kind == 'reference_changed' and reads[0] > 1:
                return {'object': {'sha': 'f' * 40}}
        return remote(method, path, body, **kwargs)
    if kind == 'unchanged':
        assert update_feed.publish(request, feed(), verify=verify_test_signature) == 'c' * 40
    else:
        with pytest.raises((ValueError, RuntimeError)):
            update_feed.publish(request, feed(), verify=verify_test_signature)
    assert not remote.mutations


def test_default_cli_generates_a_verified_local_feed_without_remote_writes(tmp_path, monkeypatch):
    import sys
    from e7auto.core.update_feed import decode_feed
    check = {'version': '9.0.0', 'commit': 'a' * 40, 'installer_size': 123, 'installer_sha256': 'a' * 64}
    monkeypatch.setattr(update_feed.workflow, '_run_directory', lambda path, root: tmp_path)
    monkeypatch.setattr(update_feed.workflow, 'check', lambda run: check)
    requests = []
    def request(method, path, body=None, **kwargs):
        requests.append((method, path))
        assert method == 'GET', 'default invocation must never write to GitHub'
        if path.startswith('/git/ref/tags/'):
            return {'object': {'type': 'commit', 'sha': 'a' * 40}}
        return published_release()
    monkeypatch.setattr(update_feed, 'github', request)
    monkeypatch.setattr(update_feed, 'verify_signature', verify_test_signature)
    monkeypatch.setattr(update_feed, 'signing_identity', lambda **kwargs: {'signature': base64.b64encode(hashlib.sha256(kwargs['payload'].read_bytes()).digest()).decode()})
    monkeypatch.setattr(sys, 'argv', ['update_feed', '--run-dir', str(tmp_path)])
    update_feed.main()
    data = (tmp_path / 'latest.json').read_bytes()
    assert decode_feed(data, verify_test_signature).asset.sha256 == 'a' * 64
    assert json.loads((tmp_path / 'update-feed-result.json').read_text())['published'] is False
