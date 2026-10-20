"""Install and invoke registered Cloudflare tools with private external runtime paths."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

from scripts.common.paths import (
    PROJECT_ROOT, create_artifact_run, managed_task_directory,
    development_tool_path, development_tool_entry,
)
from scripts.common.dev_tools import development_run_directory, external_node

SOURCE = Path(__file__).resolve().parent


def task_directory(value: str | None) -> Path:
    if value is None:
        return create_artifact_run('tasks','shop-statistics-tools',feature='shop')
    return managed_task_directory(Path(value),feature='shop')


def log_directory(value: str | None) -> Path:
    if value is None:
        return create_artifact_run('maintenance','cloudflare-tools')
    return development_run_directory(Path(value))


def stage(task: Path) -> Path:
    destination = task/'scratch/worker'
    destination.mkdir(parents=True,exist_ok=True)
    for name in ('worker.mjs','views.mjs','render.mjs','wrangler.jsonc'):
        shutil.copyfile(SOURCE/name,destination/name)
    (destination/'migrations').mkdir(exist_ok=True)
    for source in (SOURCE/'migrations').glob('*.sql'):
        shutil.copyfile(source,destination/'migrations'/source.name)
    return destination


def tool_environment(node: Path, task: Path) -> dict[str,str]:
    environment = os.environ.copy()
    environment['PATH'] = str(node.parent)+os.pathsep+environment.get('PATH','')
    environment['XDG_CONFIG_HOME'] = str(development_tool_path('cloudflare',kind='states'))
    environment['WRANGLER_LOG_PATH'] = str(task/'scratch/wrangler.log')
    environment['WRANGLER_SEND_METRICS'] = 'false'
    account = development_tool_path('cloudflare',kind='states')/'account.json'
    if account.is_file():
        value = json.loads(account.read_text(encoding='utf-8'))
        environment['CLOUDFLARE_ACCOUNT_ID'] = value['account_id']
    return environment


def install(node: Path, npm: Path, task: Path) -> Path:
    manifest, lock = SOURCE/'package.json',SOURCE/'package-lock.json'
    if not lock.is_file():
        raise ValueError('A committed package-lock.json is required; no npm install fallback')
    declared = json.loads(manifest.read_text(encoding='utf-8'))
    locked = json.loads(lock.read_text(encoding='utf-8'))
    if locked.get('packages',{}).get('',{}).get('devDependencies') != declared.get('devDependencies'):
        raise ValueError('Dependency manifest and lock file disagree')
    directory = development_tool_path('cloudflare_shop_stats')
    existing = directory/'package.json'
    if existing.exists() and json.loads(existing.read_text(encoding='utf-8')).get('name') != declared['name']:
        raise ValueError('Preserve different contents at the registered installation')
    directory.mkdir(parents=True,exist_ok=True)
    cache = development_tool_path('npm',kind='caches')
    cache.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(manifest,directory/manifest.name)
    shutil.copyfile(lock,directory/lock.name)
    environment = tool_environment(node,task)
    result = subprocess.run([str(node),str(npm),'ci','--include=dev','--engine-strict',
        '--cache',str(cache),'--prefer-offline','--no-audit','--no-fund'],
        cwd=directory,env=environment,capture_output=True,text=True,encoding='utf-8',errors='replace')
    (task/'scratch/npm-install.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
    if result.returncode:
        raise RuntimeError(f'npm ci failed ({result.returncode}); inspect the managed installation log')
    print(result.stdout,end='')
    return directory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['stage','install','locate','run'])
    parser.add_argument('--run-dir')
    parser.add_argument('--node',help='Explicit external node.exe; otherwise private configuration, then PATH')
    parser.add_argument('--npm-cli',help='Explicit external npm/bin/npm-cli.js')
    args, remaining = parser.parse_known_args()
    if args.command == 'stage':
        if remaining: parser.error('Unexpected stage arguments')
        print(stage(task_directory(args.run_dir)))
        return
    if args.command == 'locate':
        if remaining: parser.error('Unexpected locate arguments')
        node,npm = external_node(args.node,args.npm_cli)
        print(json.dumps({'node':str(node),'npm_cli':str(npm),
            'toolchain':str(development_tool_path('cloudflare_shop_stats')),
            'wrangler':str(development_tool_entry('cloudflare_shop_stats')),
            'cache':str(development_tool_path('npm',kind='caches'))},ensure_ascii=False))
        return
    node,npm = external_node(args.node,args.npm_cli)
    task = log_directory(args.run_dir)
    if args.command == 'install':
        if remaining: parser.error('Unexpected install arguments')
        print(install(node,npm,task))
        return
    arguments = remaining[1:] if remaining[:1] == ['--'] else remaining
    if not arguments: parser.error('run requires Wrangler arguments after --')
    cli = development_tool_entry('cloudflare_shop_stats')
    if not cli.is_file(): parser.error('Registered Wrangler is missing; run install first')
    directory = stage(task)
    result = subprocess.run([str(node),str(cli),*arguments],cwd=directory,env=tool_environment(node,task))
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
