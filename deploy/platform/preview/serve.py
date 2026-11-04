"""Bounded static-only demo server. No API, uploads, credentials or model calls.

Python's http.server is used only for this disposable interface review, not the
production platform. The enterprise deployment uses its existing HTTP gateway.
"""
import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import BoundedSemaphore
from urllib.parse import urlsplit


class PreviewServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, root):
        self.slots = BoundedSemaphore(24)
        self.files = {}
        for file in root.rglob('*'):
            if file.is_symlink():
                raise ValueError('Preview release must not contain links')
            if file.is_file():
                relative = file.relative_to(root).as_posix()
                if any(part.startswith('.') for part in file.relative_to(root).parts):
                    raise ValueError('Hidden files are not public assets')
                self.files['/' + relative] = file
        if '/index.html' not in self.files:
            raise ValueError('Missing preview index')
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = 'OpenAgentPreview'
    sys_version = ''
    timeout = 10

    def log_message(self, format, *args):
        # Do not log submitted paths, query strings, headers or user input.
        pass

    def end_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        super().end_headers()

    def do_HEAD(self):
        self.serve_asset(head=True)

    def do_GET(self):
        self.serve_asset(head=False)

    def serve_asset(self, head):
        path = urlsplit(self.path).path
        if path == '/healthz':
            payload = json.dumps({'status': 'ok', 'mode': 'interface-preview', 'model_connected': False}).encode()
            mime = 'application/json'
        else:
            file = self.server.files.get('/index.html' if path == '/' else path)
            if file is None:
                self.send_error(404, 'Not found')
                return
            payload = file.read_bytes()
            mime = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
            if file.suffix == '.js':
                mime = 'text/javascript'
        self.send_response(200)
        self.send_header('Content-Type', mime + '; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        if not head:
            self.wfile.write(payload)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=18080)
    args = parser.parse_args()
    PreviewServer((args.host, args.port), args.root.resolve(strict=True)).serve_forever()
