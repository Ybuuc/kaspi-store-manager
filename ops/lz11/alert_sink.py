"""Minimal internal webhook sink for *disposable CI* Uptime Kuma notifications.

Not authenticated, not intended to be accessible outside the Docker Compose
network. The CI workflow verifies real DOWN and UP delivery; this is not a
production paging system.
"""
import json
import os
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LOG = Path(os.environ.get("KSM_ALERT_LOG", "/app/alerts/notifications.jsonl"))
LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        if self.path != "/alert" or not (0 < length <= 65536):
            self.send_error(400)
            return
        try:
            body = json.loads(self.rfile.read(length))
            hb = body.get("heartbeat") or {}
            record = {
                "received_at": datetime.now(timezone.utc).isoformat(),
                "status": hb.get("status"),
                "message": body.get("msg", ""),
                "monitor_name": (body.get("monitor") or {}).get("name"),
            }
            with LOCK:
                with LOG.open("a", encoding="utf-8") as out:
                    out.write(json.dumps(record, ensure_ascii=False) + "\n")
            self.send_response(204)
            self.end_headers()
        except (ValueError, TypeError, KeyError):
            self.send_error(400)

    def log_message(self, fmt, *args):
        return


def main():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    print("Internal synthetic alert sink listening on 8765", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8765), Handler).serve_forever()


if __name__ == "__main__":
    main()
