"""Serve exactly one configured XML over loopback TLS; no directory or project access."""
import argparse
import ssl
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


LIMIT = 16 * 1024 * 1024


def make_server(host, port, catalog, cert, key):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Neither paths nor client-controlled input belong in logs.

        def do_GET(self):
            if self.path != '/catalog.xml':
                self.send_error(404)
                return
            try:
                with Path(catalog).open('rb') as source:
                    data = source.read(LIMIT + 1)
                if len(data) > LIMIT:
                    self.send_error(413)
                    return
            except OSError:
                self.send_error(503)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/xml; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(data)

        do_HEAD = do_GET

    class Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6 if ':' in host else socket.AF_INET
        daemon_threads = True
        allow_reuse_address = True

        def server_bind(self):
            if self.address_family == socket.AF_INET6:
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            super().server_bind()

        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(5)
            try:
                return context.wrap_socket(connection, server_side=True), address
            except Exception:
                connection.close()
                raise

    return Server((host, port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1] / '.local-catalog'
    parser.add_argument('--catalog', type=Path, default=root/'catalog.xml')
    parser.add_argument('--cert', type=Path, default=root/'server.pem')
    parser.add_argument('--key', type=Path, default=root/'server-key.pem')
    args = parser.parse_args()
    servers = []
    try:
        # Fail rather than silently exposing a partially configured listener.
        for host in ('127.0.0.1', '::1'):
            servers.append(make_server(host, 8443, args.catalog, args.cert, args.key))
        for server in servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
        print('Local catalogue HTTPS listening on loopback port 8443.', flush=True)
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    main()
