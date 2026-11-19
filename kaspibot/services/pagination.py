"""Read complete API collections before applying catalogue deactivation."""
import json


def collect_pages(fetch, **filters):
    rows = []
    fingerprints = set()
    for number in range(1000):
        payload = fetch(page_number=number, page_size=100, **filters)
        if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
            raise ValueError('Kaspi returned an invalid collection; no catalogue changes applied')
        page = payload['data']
        if not page:
            return rows
        fingerprint = json.dumps(page, sort_keys=True)
        if fingerprint in fingerprints:
            raise ValueError('Kaspi repeated a page; refusing an incomplete collection')
        fingerprints.add(fingerprint)
        rows.extend(page)
        page_count = (payload.get('meta') or {}).get('pageCount')
        if page_count is not None:
            if int(page_count) < 1:
                raise ValueError('Invalid Kaspi page count')
            if number + 1 >= int(page_count):
                return rows
        elif len(page) < 100:
            return rows
    raise ValueError('Kaspi pagination limit reached; refusing an incomplete collection')
