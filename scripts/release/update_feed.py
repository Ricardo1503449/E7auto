"""Generate a signed feed and explicitly publish it to the independent updates branch."""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import subprocess

from e7auto.core.update_feed import REPOSITORY, PROJECT_URL, decode_feed, encode_feed, version_parts
from e7auto.platform.update_signing import SIGNER_SHA256, verify_signature
from scripts.common.paths import PROJECT_ROOT, development_tool_path
from scripts.release import workflow


def signing_identity(*, initialize=False, payload: Path | None = None) -> dict:
    directory = development_tool_path('release_signing', kind='states')
    if initialize:
        directory.mkdir(parents=True, exist_ok=True)
    key = directory / 'update-signing.pfx.dpapi'
    if not initialize and not key.is_file():
        raise ValueError('Local signing identity is missing; do not replace the pinned production key')
    if key.is_symlink() or key.is_junction():
        raise ValueError('Linked signing identity is not allowed')
    args = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            str(PROJECT_ROOT / 'scripts/release/update-signing.ps1'), '-Mode', 'init' if initialize else 'sign',
            '-KeyFile', str(key)]
    if payload is not None:
        args += ['-PayloadFile', str(payload)]
    result = subprocess.run(args, capture_output=True, encoding='utf-8', errors='replace')
    if result.returncode:
        raise RuntimeError('Local update signing failed: ' + result.stderr.strip())
    public = json.loads(result.stdout)
    if not initialize and public['fingerprint'] != SIGNER_SHA256:
        raise ValueError('Local signing identity differs from the client trust anchor')
    return public


def github(method: str, path: str, body=None, *, missing=False):
    args = ['gh', 'api', '--method', method, f'repos/{REPOSITORY}{path}']
    if body is not None:
        args += ['--input', '-']
    result = subprocess.run(args, input=json.dumps(body) if body is not None else None,
                            capture_output=True, encoding='utf-8')
    if result.returncode:
        if missing and 'HTTP 404' in result.stderr:
            return None
        raise RuntimeError('GitHub feed operation failed: ' + result.stderr.strip())
    return json.loads(result.stdout)


def release_payload(check: dict, release: dict, tag_commit: str) -> dict:
    version = 'v' + check['version']
    name = f'E7auto_{version}_Setup_x64.exe'
    if (release.get('tag_name') != version or release.get('draft') is not False
            or release.get('prerelease') is not False or tag_commit != check['commit']):
        raise ValueError('A published stable release on the verified commit is required')
    assets = release.get('assets')
    if not isinstance(assets, list) or len(assets) != 1:
        raise ValueError('Release must contain exactly the verified Setup EXE')
    asset = assets[0]
    url = f'{PROJECT_URL}/releases/download/{version}/{name}'
    if (asset.get('name') != name or asset.get('browser_download_url') != url
            or asset.get('state') != 'uploaded' or type(asset.get('size')) is not int
            or asset['size'] != check['installer_size']
            or asset.get('digest') != 'sha256:' + check['installer_sha256']):
        raise ValueError('GitHub asset differs from the sealed installer')
    return {'repository': REPOSITORY, 'version': version, 'published_at': release['published_at'],
            'commit': check['commit'], 'notes': release.get('body') or '',
            'asset': {'name': name, 'url': url, 'size': asset['size'], 'sha256': check['installer_sha256']}}


def tag_commit(request, version: str) -> str:
    obj = request('GET', '/git/ref/tags/' + version)['object']
    for _ in range(5):
        if obj['type'] == 'commit':
            return obj['sha']
        if obj['type'] != 'tag':
            break
        obj = request('GET', '/git/tags/' + obj['sha'])['object']
    raise ValueError('Tag does not resolve to a commit')


def publish(request, data: bytes, *, verify=verify_signature) -> str:
    release = decode_feed(data, verify)
    # Only the current official Latest may advance the feed.
    latest = request('GET', '/releases/latest')
    if latest['tag_name'] != release.version or latest['draft'] or latest['prerelease']:
        raise ValueError('Feed is not the latest stable release')
    if tag_commit(request, release.version) != release.commit:
        raise ValueError('Release tag changed before feed publication')
    expected = {'version': release.version.removeprefix('v'), 'commit': release.commit,
                'installer_size': release.asset.size, 'installer_sha256': release.asset.sha256}
    actual_payload = release_payload(expected, latest, release.commit)
    if actual_payload['notes'].strip() != release.notes or actual_payload['published_at'] != release.published_at:
        raise ValueError('Release metadata changed before feed publication')
    old = request('GET', '/git/ref/heads/updates', missing=True)
    parents = [] if old is None else [old['object']['sha']]
    tree_body = {'tree': [{'path': 'latest.json', 'type': 'blob', 'mode': '100644',
                           'content': data.decode('ascii')}]}
    if parents:
        commit = request('GET', '/git/commits/' + parents[0])
        tree_body['base_tree'] = commit['tree']['sha']
        tree = request('GET', '/git/trees/' + tree_body['base_tree'])
        if tree.get('truncated') is not False:
            raise ValueError('Complete updates branch tree is required')
        entries = [entry for entry in tree['tree'] if entry['path'] == 'latest.json']
        if entries:
            if entries[0]['type'] != 'blob' or entries[0]['mode'] != '100644':
                raise ValueError('Unexpected existing feed entry')
            blob = request('GET', '/git/blobs/' + entries[0]['sha'])
            previous = base64.b64decode(blob['content'])
            previous_release = decode_feed(previous, verify)
            if version_parts(previous_release.version) > version_parts(release.version):
                raise ValueError('Refusing to roll back the update feed')
            if previous_release.version == release.version and previous_release.asset != release.asset:
                raise ValueError('Same-version installer identity changed; use a new release version')
            if previous == data:
                current = request('GET', '/releases/latest')
                if release_payload(expected, current, tag_commit(request, release.version)) != actual_payload:
                    raise ValueError('Release changed before idempotent publication verification')
                if request('GET', '/git/ref/heads/updates')['object']['sha'] != parents[0]:
                    raise RuntimeError('Remote updates reference changed while checking the existing feed')
                return parents[0]
    tree_sha = request('POST', '/git/trees', tree_body)['sha']
    message = (f'chore(updates): publish signed feed for {release.version}\n\n'
               'Bind the released installer and notes to a signed feed so clients can query and download through file accelerators.')
    commit_sha = request('POST', '/git/commits', {'message': message, 'tree': tree_sha, 'parents': parents})['sha']
    # Recheck Latest immediately before changing the public branch reference.
    current_latest = request('GET', '/releases/latest')
    if current_latest['tag_name'] != release.version:
        raise ValueError('Latest changed during feed generation; leaving the branch unchanged')
    if release_payload(expected, current_latest, tag_commit(request, release.version)) != actual_payload:
        raise ValueError('Release changed during feed generation; leaving the branch unchanged')
    if parents:
        request('PATCH', '/git/refs/heads/updates', {'sha': commit_sha, 'force': False})
    else:
        request('POST', '/git/refs', {'ref': 'refs/heads/updates', 'sha': commit_sha})
    if request('GET', '/git/ref/heads/updates')['object']['sha'] != commit_sha:
        raise RuntimeError('Remote updates reference differs from the published commit; inspect before retrying')
    actual = request('GET', '/contents/latest.json?ref=' + commit_sha)
    if base64.b64decode(actual['content']) != data:
        raise RuntimeError('Remote feed content verification failed')
    return commit_sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--init-key', action='store_true')
    parser.add_argument('--run-dir', type=Path)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    if args.init_key:
        if args.run_dir or args.publish:
            parser.error('--init-key cannot publish or generate a feed')
        print(json.dumps(signing_identity(initialize=True)))
        return
    if args.run_dir is None:
        parser.error('--run-dir is required')
    run = workflow._run_directory(args.run_dir, PROJECT_ROOT)
    check = workflow.check(run)
    release = github('GET', '/releases/tags/v' + check['version'])
    payload = release_payload(check, release, tag_commit(github, 'v' + check['version']))
    if github('GET', '/releases/latest')['id'] != release['id']:
        raise ValueError('Only the current Latest can generate the feed')
    payload_path = run / 'update-feed-payload.json'
    def sign(data):
        payload_path.write_bytes(data)
        return base64.b64decode(signing_identity(payload=payload_path)['signature'], validate=True)
    data = encode_feed(payload, sign)
    decode_feed(data, verify_signature)
    (run / 'latest.json').write_bytes(data)
    result = {'branch': 'updates', 'version': payload['version'], 'signer_sha256': SIGNER_SHA256,
              'file': str(run / 'latest.json'), 'published': False}
    if args.publish:
        result.update(commit=publish(github, data), published=True)
    (run / 'update-feed-result.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
