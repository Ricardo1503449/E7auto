"""Publish only aggregate snapshot files to stats via GitHub's Git data API."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
from urllib.error import HTTPError
from urllib.request import Request, build_opener

from scripts.cloudflare.shop_stats.snapshot import NoRedirect


def read_files(directory: Path) -> dict[str,str]:
    root = directory.resolve()
    names = json.loads((root/'snapshot-files.json').read_text(encoding='utf-8'))
    if not isinstance(names,list) or len(names) != 5 or len(set(names)) != 5:
        raise ValueError('invalid snapshot file manifest')
    files = {}
    for name in names:
        if not isinstance(name,str) or not (name in ('latest.json','latest.svg','README.md')
                or re.fullmatch(r'history/\d{4}-\d{2}-\d{2}\.(json|svg)',name)):
            raise ValueError('snapshot publication cannot include other files')
        declared = root/name
        path = declared.resolve()
        if not path.is_relative_to(root) or declared.is_symlink() or path.stat().st_size > 65536:
            raise ValueError('invalid snapshot file')
        files[name] = path.read_text(encoding='utf-8')
    if not {'latest.json','latest.svg','README.md'} <= files.keys():
        raise ValueError('missing latest snapshot files')
    dates = {PurePosixPath(name).stem for name in files if name.startswith('history/')}
    if len(dates) != 1:
        raise ValueError('snapshot files must share one date')
    return files


def publish(request, files: dict[str,str]) -> str:
    old = request('GET','/git/ref/heads/stats',None,missing=True)
    tree_payload = {'tree':[{'path':name,'mode':'100644','type':'blob','content':content}
                            for name,content in sorted(files.items())]}
    parents = []
    if old is not None:
        parents = [old['object']['sha']]
        tree_payload['base_tree'] = request('GET',f'/git/commits/{parents[0]}',None)['tree']['sha']
    tree = request('POST','/git/trees',tree_payload)['sha']
    day = next(PurePosixPath(name).stem for name in files if name.startswith('history/'))
    message = (f'chore(stats): archive shop statistics for {day}\n\n'
               'Save the aggregated strategy totals and matching chart as a dated community snapshot while preserving earlier snapshots.')
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
