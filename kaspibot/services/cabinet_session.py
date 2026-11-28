"""Checkpoint only the dedicated cabinet context after its merchant is verified."""
import json


def save_session(context,path):
    # IndexedDB support is available in the pinned Playwright 1.51; no new dependency.
    from kaspibot.services.catalog_source import atomic_write
    state=context.storage_state(indexed_db=True)
    atomic_write(path,json.dumps(state,ensure_ascii=False).encode('utf-8'))
