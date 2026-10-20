"""Publish only aggregate snapshot files to stats via GitHub's Git data API."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.request import Request, build_opener
from xml.etree import ElementTree

from scripts.cloudflare.shop_stats.snapshot import HISTORY_DAYS, NoRedirect, snapshot_date, validate_stats


def publication_day(names: object) -> date:
    if (not isinstance(names, list) or len(names) != 4
            or any(not isinstance(name, str) for name in names) or len(set(names)) != 4):
        raise ValueError('invalid snapshot file manifest')
    history = set(names) - {'latest.json', 'latest.svg', 'README.md'}
    if len(history) != 1:
        raise ValueError('missing latest snapshot files')
    name = history.pop()
    match = re.fullmatch(r'history/(\d{4}-\d{2}-\d{2})\.json', name)
    if not match:
        raise ValueError('snapshot publication cannot include other files')
    return snapshot_date(match[1])


def validate_files(files: dict[str, str]) -> date:
    day = publication_day(list(files))
    if any(not isinstance(content, str) or len(content.encode('utf-8')) > 65536
           for content in files.values()):
        raise ValueError('invalid snapshot file')
    validate_stats(json.loads(files['latest.json']))
    if files['latest.json'] != files[f'history/{day.isoformat()}.json']:
        raise ValueError('latest and dated aggregate must match')
    try:
        svg = ElementTree.fromstring(files['latest.svg'])
    except ElementTree.ParseError as error:
        raise ValueError('invalid snapshot SVG') from error
    if svg.tag != '{http://www.w3.org/2000/svg}svg':
        raise ValueError('invalid snapshot SVG')
    return day


def read_files(directory: Path) -> dict[str,str]:
    root = directory.resolve()
    names = json.loads((root/'snapshot-files.json').read_text(encoding='utf-8'))
    publication_day(names)
    files = {}
    for name in names:
        declared = root/name
        path = declared.resolve()
        if not path.is_relative_to(root) or declared.is_symlink() or path.stat().st_size > 65536:
            raise ValueError('invalid snapshot file')
        files[name] = path.read_text(encoding='utf-8')
    validate_files(files)
    return files


def history_deletions(tree: dict, day: date) -> list[dict]:
    if tree.get('truncated') is not False or not isinstance(tree.get('tree'), list):
        raise ValueError('complete stats tree is required before retention cleanup')
    cutoff = day - timedelta(days=HISTORY_DAYS - 1)
    deletions = []
    for entry in tree['tree']:
        if (not isinstance(entry, dict) or not isinstance(entry.get('path'), str)
                or entry.get('type') != 'blob' or entry.get('mode') != '100644'):
            continue
        match = re.fullmatch(r'history/(\d{4}-\d{2}-\d{2})\.(json|svg)', entry['path'])
        if not match:
            continue
        try:
            recorded = snapshot_date(match[1])
        except ValueError:
            continue
        if match[2] == 'svg' or recorded < cutoff:
            deletions.append({'path': entry['path'], 'mode': '100644', 'type': 'blob', 'sha': None})
    return sorted(deletions, key=lambda entry: entry['path'])


def publish(request, files: dict[str,str]) -> str:
    day = validate_files(files)
    old = request('GET','/git/ref/heads/stats',None,missing=True)
    tree_payload = {'tree':[{'path':name,'mode':'100644','type':'blob','content':content}
                            for name,content in sorted(files.items())]}
    parents = []
    if old is not None:
        parents = [old['object']['sha']]
        tree_payload['base_tree'] = request('GET',f'/git/commits/{parents[0]}',None)['tree']['sha']
        previous = request('GET', f"/git/trees/{tree_payload['base_tree']}?recursive=1", None)
        tree_payload['tree'].extend(history_deletions(previous, day))
    tree = request('POST','/git/trees',tree_payload)['sha']
    message = (f'chore(stats): archive shop statistics for {day}\n\n'
               'Refresh the aggregate and chart, retain daily JSON for the latest 365 calendar days, '
               'and remove expired JSON and daily charts while preserving Git history.')
    commit = request('POST','/git/commits',{'message':message,'tree':tree,'parents':parents})['sha']
    if parents:
        request('PATCH','/git/refs/heads/stats',{'sha':commit,'force':False})
    else:
        request('POST','/git/refs',{'ref':'refs/heads/stats','sha':commit})
    actual = request('GET','/git/ref/heads/stats',None)['object']['sha']
    if actual != commit:
        raise RuntimeError('stats branch reference did not match the published commit')
    return commit


def github_request(repository: str, token: str):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repository):
        raise ValueError('invalid GitHub repository')
    opener = build_opener(NoRedirect())
    def request(method, path, body=None, *, missing=False):
        payload = json.dumps(body).encode('utf-8') if body is not None else None
        query = Request(f'https://api.github.com/repos/{repository}{path}',data=payload,method=method,headers={
            'Accept':'application/vnd.github+json','Authorization':f'Bearer {token}',
            'Content-Type':'application/json','X-GitHub-Api-Version':'2026-03-10',
            'User-Agent':'E7auto-statistics-snapshot'})
        try:
            with opener.open(query,timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            if missing and error.code == 404:
                return None
            raise RuntimeError(f'GitHub publication failed with HTTP {error.code}') from None
    return request


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot-dir',required=True)
    parser.add_argument('--repository',default=os.environ.get('GITHUB_REPOSITORY'))
    parser.add_argument('--publish',action='store_true',help='explicitly authorize remote stats branch publication')
    args = parser.parse_args()
    files = read_files(Path(args.snapshot_dir))
    if not args.publish:
        print(json.dumps({'branch':'stats','files':sorted(files)},ensure_ascii=False))
        return
    token = os.environ.get('GITHUB_TOKEN')
    if not token or not args.repository:
        parser.error('GitHub Actions token and repository are required for publication')
    print(publish(github_request(args.repository,token),files))


if __name__ == '__main__':
    main()
