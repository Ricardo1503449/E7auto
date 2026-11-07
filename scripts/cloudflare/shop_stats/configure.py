"""Fill verified deployment inputs without rewriting game configuration."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
from uuid import UUID

from scripts.common.paths import PROJECT_ROOT
from urllib.parse import urlsplit

def endpoint_base(value: str) -> str:
    if any(char.isspace() or char in '\"\'<>\\' for char in value):
        raise ValueError('invalid HTTPS service root URL')
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ('','/') or parsed.port not in (None,443)):
        raise ValueError('a real HTTPS service root URL without credentials is required')
    return value.rstrip('/')


def configure(*, database_id: str | None = None, endpoint: str | None = None,
              root: Path = PROJECT_ROOT) -> list[Path]:
    updates = {}
    if database_id is not None:
        if str(UUID(database_id)) != database_id.lower():
            raise ValueError('use the real canonical D1 database UUID')
        path = root/'scripts/cloudflare/shop_stats/wrangler.jsonc'
        text = path.read_bytes().decode('utf-8')
        text,count = re.subn(r'("database_id"\s*:\s*")[^"]*(")',
                             lambda match: match[1]+database_id.lower()+match[2],text)
        if count != 1: raise ValueError('expected one D1 database binding')
        updates[path] = text
    if endpoint is not None:
        endpoint = endpoint_base(endpoint)
        path = root/'config/internal.yaml'
        text = path.read_bytes().decode('utf-8')
        pattern = r'(?m)(^statistics:\r?\n(?:[ \t]+[^\r\n]*(?:\r?\n))*?)(^  api_base_url:[^\r\n]*)'
        text,count = re.subn(pattern,lambda match: match[1]+f'  api_base_url: "{endpoint}"',text)
        if count != 1: raise ValueError('expected one statistics API setting')
        updates[path] = text
    if not updates: raise ValueError('provide a database ID or service endpoint')
    for path,text in updates.items():
        temporary = path.with_suffix(path.suffix+'.tmp')
        temporary.write_bytes(text.encode('utf-8'))
        temporary.replace(path)
    return list(updates)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-id')
    parser.add_argument('--endpoint')
    args = parser.parse_args()
    for path in configure(database_id=args.database_id,endpoint=args.endpoint):
        print(path)


if __name__ == '__main__':
    main()
