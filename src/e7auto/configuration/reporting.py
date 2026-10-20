"""Read optional reporting separately from game calibration and startup gates."""
from pathlib import Path
from urllib.parse import urlsplit

import yaml


def load_reporting_endpoint(path: Path) -> str:
    try:
        document = yaml.safe_load(path.read_text(encoding='utf-8'))
        section = document.get('statistics', {}) if isinstance(document, dict) else {}
        value = section.get('api_base_url', '') if isinstance(section, dict) else ''
        if not value:
            return ''
        parsed = (urlsplit(value) if isinstance(value, str)
                  and not any(char.isspace() or char in '\"\'<>\\' for char in value) else None)
        if (parsed is None or parsed.scheme != 'https' or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or parsed.path not in ('','/')
                or parsed.port not in (None, 443)):
            return ''
        return value.rstrip('/')
    except (OSError, ValueError, yaml.YAMLError):
        return ''
