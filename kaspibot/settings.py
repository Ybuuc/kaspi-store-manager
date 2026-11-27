"""Local configuration without publishing credentials or overriding the environment."""
import os
from pathlib import Path


def local_settings(root):
    values = {}
    path = Path(root) / '.env'
    allowed = {'KASPI_API_TOKEN', 'KSM_SECRET_KEY', 'SECRET_KEY', 'KASPIBOT_DATABASE',
               'KASPI_API_BASE', 'KASPI_HISTORY_DAYS', 'KSM_DEMO_MODE',
               'KASPI_AUTO_SYNC', 'KASPI_SYNC_INTERVAL_SECONDS', 'KASPI_CATALOG_URL',
               'KASPI_ALLOW_LOCAL_CATALOG', 'KASPI_CATALOG_CA_BUNDLE'}
    if path.is_file():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            key, value = key.strip(), value.strip()
            if key in allowed:
                if len(value) >= 2 and value[0] == value[-1] and value[0] in '\"\'':
                    value = value[1:-1]
                values[key] = value
    return {key: os.environ.get(key, value) for key, value in values.items()}
