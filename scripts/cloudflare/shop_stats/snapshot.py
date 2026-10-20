"""Fetch one validated aggregate snapshot; render its SVG from the same data."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from scripts.common.paths import PROJECT_ROOT, create_artifact_run, ensure_task_result_path

COUNTERS = ('skystone_spent','covenant_purchases','mystic_purchases')
STRATEGIES = ('transfer','continuous')
SOURCE = Path(__file__).resolve().parent
HISTORY_DAYS = 365


def snapshot_date(value: str) -> date:
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('invalid snapshot date')
    return date.fromisoformat(value)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        return None


def endpoint_base(value: str) -> str:
    if any(char.isspace() or char in '\"\'<>\\' for char in value):
        raise ValueError('invalid HTTPS service root URL')
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ('','/') or parsed.port not in (None,443)):
        raise ValueError('a real HTTPS service root URL without credentials is required')
    return value.rstrip('/')


def validate_stats(value: object) -> dict:
    if not isinstance(value,dict) or set(value) != {'pools','updated_at'}:
        raise ValueError('invalid aggregate document')
    pools = value['pools']
    if not isinstance(pools,dict) or set(pools) != set(STRATEGIES):
        raise ValueError('both strategy pools are required')
    stamp = value['updated_at']
    if stamp is not None:
        if not isinstance(stamp,str):
            raise ValueError('invalid aggregate timestamp')
        datetime.fromisoformat(stamp.replace('Z','+00:00'))
    for pool in pools.values():
        if not isinstance(pool,dict) or set(pool) != {*COUNTERS,'covenant_per_100','mystic_per_100'}:
            raise ValueError('invalid aggregate fields')
        if any(isinstance(pool[key],bool) or not isinstance(pool[key],int)
               or not 0 <= pool[key] <= 2**53-1 for key in COUNTERS):
            raise ValueError('invalid aggregate counters')
        for rate,count in [('covenant_per_100','covenant_purchases'),('mystic_per_100','mystic_purchases')]:
            expected = 100*pool[count]/pool['skystone_spent'] if pool['skystone_spent'] else None
            actual = pool[rate]
            if expected is None:
                if actual is not None:
                    raise ValueError('zero-consumption rate must be null')
            elif isinstance(actual,bool) or not isinstance(actual,(int,float)) or not math.isfinite(actual) or not math.isclose(actual,expected,rel_tol=1e-9,abs_tol=1e-9):
                raise ValueError('rate does not match aggregate totals')
    return value


def fetch_stats(endpoint: str) -> dict:
    request = Request(endpoint_base(endpoint)+'/v1/shop-stats', headers={
        'Accept':'application/json','User-Agent':'E7auto-statistics-snapshot'})
    with build_opener(NoRedirect()).open(request, timeout=30) as response:
        payload = response.read(32769)
    if len(payload) > 32768:
        raise ValueError('aggregate response is too large')
    return validate_stats(json.loads(payload))


def build_files(stats: dict, endpoint: str, *, node: str, day: str) -> dict[str,str]:
    snapshot_date(day)
    endpoint = endpoint_base(endpoint)
    validate_stats(stats)
    data = json.dumps(stats,ensure_ascii=False,indent=2)+'\n'
    result = subprocess.run([node,str(SOURCE/'render.mjs')], input=data.encode('utf-8'),
                            capture_output=True,timeout=10,check=True)
    svg = result.stdout.decode('utf-8')
    if not svg.startswith('<svg'):
        raise ValueError('renderer did not produce SVG')
    readme = (f'# 刷商店社区统计\n\n![两个策略池统计](latest.svg)\n\n'
              f'[查看近实时详情]({endpoint}/stats) · [汇总 JSON](latest.json) · [每日快照](history/)\n\n'
              f'快照日期：{day}（北京时间）。数据来自成功上报的匿名客户端，包含启动时已有商品的成功购买。'
              '每100天空石购买次数按累计总量计算；这些数据不能证明游戏结果真实，也不覆盖未成功上传的运行。\n\n'
              f'历史仅保留包含快照当天在内的最近{HISTORY_DAYS}个日历日的JSON，不保存每日SVG。'
              '成功更新快照时同时移除过期JSON及旧每日SVG；旧内容仍保留在Git提交历史中。\n')
    return {'latest.json':data,'latest.svg':svg,f'history/{day}.json':data,'README.md':readme}


def write_files(output: Path, files: dict[str,str]) -> None:
    output = output.resolve()
    if output.is_relative_to(PROJECT_ROOT.resolve()):
        ensure_task_result_path(output/'latest.json')
    # Fetching, validation and rendering have all succeeded before touching prior files.
    for name, content in files.items():
        path = output/name
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary = path.with_suffix(path.suffix+'.tmp')
        temporary.write_text(content,encoding='utf-8')
        temporary.replace(path)
    manifest = output/'snapshot-files.json'
    manifest.write_text(json.dumps(sorted(files)),encoding='utf-8')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--output-dir')
    parser.add_argument('--node', default=shutil.which('node'))
    args = parser.parse_args()
    if not args.node:
        parser.error('Node.js is required only to render deployment snapshots')
    stats = fetch_stats(args.endpoint)
    day = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    files = build_files(stats,args.endpoint,node=args.node,day=day)
    output = Path(args.output_dir) if args.output_dir else create_artifact_run(
        'tasks','shop-statistics-snapshot',feature='shop')/'results/snapshot'
    write_files(output,files)
    print(output)


if __name__ == '__main__':
    main()
