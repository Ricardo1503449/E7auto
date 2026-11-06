"""Signed release identity, shared by the client and release publisher."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
import json
import re
from typing import Callable

REPOSITORY = 'Ricardo1503449/E7auto'
PROJECT_URL = f'https://github.com/{REPOSITORY}'
OFFICIAL_FEED_URL = f'https://raw.githubusercontent.com/{REPOSITORY}/updates/latest.json'
MIRROR_PREFIX = 'https://gh-proxy.org/'
FEED_URLS = (MIRROR_PREFIX + OFFICIAL_FEED_URL, OFFICIAL_FEED_URL)
MAX_FEED_BYTES = 256 * 1024
MAX_INSTALLER_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True)
class ReleaseAsset:
    version: str
    name: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True)
class UpdateRelease:
    version: str
    notes: str
    asset: ReleaseAsset
    commit: str
    published_at: str


def version_parts(tag: str) -> tuple[int, int, int]:
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', tag) if isinstance(tag, str) else None
    if match is None:
        raise ValueError('无法识别发布版本号')
    return tuple(map(int, match.groups()))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('更新清单含重复字段')
        result[key] = value
    return result


def load_json(data: bytes):
    try:
        return json.loads(data, object_pairs_hook=_unique_object)
    except RecursionError as exc:
        raise ValueError('更新清单JSON嵌套过深') from exc


def validate_payload(value: dict) -> UpdateRelease:
    if not isinstance(value, dict) or set(value) != {
            'repository', 'version', 'published_at', 'notes', 'commit', 'asset'}:
        raise ValueError('更新清单字段无效')
    version = value['version']
    if value['repository'] != REPOSITORY or version != 'v' + '.'.join(map(str, version_parts(version))):
        raise ValueError('更新清单仓库或版本无效')
    if not isinstance(value['notes'], str) or not isinstance(value['commit'], str) or not re.fullmatch(r'[0-9a-f]{40}', value['commit']):
        raise ValueError('更新清单说明或提交无效')
    date = value['published_at']
    if not isinstance(date, str) or not date.endswith('Z'):
        raise ValueError('更新清单发布时间无效')
    datetime.fromisoformat(date.replace('Z', '+00:00'))
    asset = value['asset']
    name = f'E7auto_{version}_Setup_x64.exe'
    if (not isinstance(asset, dict) or set(asset) != {'name', 'url', 'size', 'sha256'}
            or asset['name'] != name or asset['url'] != f'{PROJECT_URL}/releases/download/{version}/{name}'
            or type(asset['size']) is not int or not 0 < asset['size'] <= MAX_INSTALLER_BYTES
            or not isinstance(asset['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', asset['sha256'])):
        raise ValueError('更新清单安装包信息无效')
    return UpdateRelease(version, value['notes'].strip(), ReleaseAsset(version, **asset), value['commit'], date)


def decode_feed(data: bytes, verify: Callable[[bytes, bytes], None]) -> UpdateRelease:
    if len(data) > MAX_FEED_BYTES:
        raise ValueError('更新清单过大')
    envelope = load_json(data)
    if (not isinstance(envelope, dict) or set(envelope) != {'schema_version', 'payload', 'signature'}
            or type(envelope['schema_version']) is not int or envelope['schema_version'] != 1
            or not isinstance(envelope['payload'], str) or not isinstance(envelope['signature'], str)):
        raise ValueError('更新清单格式无效')
    payload = base64.b64decode(envelope['payload'], validate=True)
    signature = base64.b64decode(envelope['signature'], validate=True)
    verify(payload, signature)
    return validate_payload(load_json(payload))


def encode_feed(payload: dict, sign: Callable[[bytes], bytes]) -> bytes:
    validate_payload(payload)
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    result = json.dumps({'schema_version': 1, 'payload': base64.b64encode(data).decode('ascii'),
                         'signature': base64.b64encode(sign(data)).decode('ascii')}, separators=(',', ':')).encode('ascii')
    if len(result) > MAX_FEED_BYTES:
        raise ValueError('更新清单过大')
    return result + b'\n'


def download_urls(asset: ReleaseAsset) -> tuple[str, str]:
    return MIRROR_PREFIX + asset.url, asset.url
