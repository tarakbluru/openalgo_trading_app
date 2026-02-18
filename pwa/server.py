#!/usr/bin/env python3
"""
PWA Proxy Server — zero external dependencies (Python 3.8+ stdlib only).

Sits in front of standalone_server.py and:
  1. Serves /manifest.json, /sw.js, /icon.svg from this folder
  2. Injects PWA <link>/<meta> tags into every HTML response
  3. Proxies everything else transparently to the backend

Usage:
  1. Make sure standalone_server.py is running on :5003 (unchanged)
  2. python pwa/server.py
  3. Open http://127.0.0.1:5004/ in Chrome or Edge
  4. Look for the install icon (⊕) in the browser address bar

Environment variables (can also be set in ../.env):
  PWA_PORT    Port this proxy listens on  (default: 5004)
  BACKEND_URL URL of standalone_server.py (default: http://127.0.0.1:5003)
"""

import os, json, urllib.request, urllib.error, socketserver
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

# ── Config ────────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_env():
    """Load ../.env so we share the same env file as standalone_server."""
    path = os.path.join(BASE_DIR, '..', '.env')
    try:
        with open(path, encoding='utf-8') as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, val = line.partition('=')
                os.environ.setdefault(key.strip(), val.strip())
    except FileNotFoundError:
        pass


_load_env()

PORT        = int(os.getenv('PWA_PORT', 5004))
BACKEND_URL = os.getenv('BACKEND_URL', 'http://127.0.0.1:5003').rstrip('/')

# ── Static PWA files ──────────────────────────────────────────────────────

_MIME = {
    'manifest.json': 'application/manifest+json',
    'sw.js':         'application/javascript; charset=utf-8',
    'icon.svg':      'image/svg+xml',
}

_STATIC: dict[str, bytes] = {}
for _fname in _MIME:
    _fpath = os.path.join(BASE_DIR, _fname)
    try:
        with open(_fpath, encoding='utf-8') as _f:
            _STATIC[f'/{_fname}'] = _f.read().encode('utf-8')
    except FileNotFoundError:
        print(f"  Warning: pwa/{_fname} not found — will be skipped")

# ── PWA injection snippets ────────────────────────────────────────────────

_PWA_HEAD = (
    '<link rel="manifest" href="/manifest.json">'
    '<meta name="theme-color" content="#0f172a">'
    '<meta name="mobile-web-app-capable" content="yes">'
    '<meta name="apple-mobile-web-app-capable" content="yes">'
    '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">'
    '<meta name="apple-mobile-web-app-title" content="Trading App">'
    '<link rel="apple-touch-icon" href="/icon.svg">'
)

_PWA_BODY = (
    '<script>'
    'if("serviceWorker"in navigator){'
    'navigator.serviceWorker.register("/sw.js")'
    '.then(()=>console.log("[PWA] Service worker registered"))'
    '.catch(e=>console.error("[PWA] SW error:",e));'
    '}'
    '</script>'
)


def _inject_pwa(html: str) -> str:
    """Inject manifest link + SW registration into HTML."""
    html = html.replace('</head>', _PWA_HEAD + '</head>', 1)
    html = html.replace('</body>', _PWA_BODY + '</body>', 1)
    return html


# ── Handler ───────────────────────────────────────────────────────────────

class ProxyHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"  [{self.address_string()}] {fmt % args}")

    # ── helpers ──

    def _serve_static(self, path: str) -> bool:
        """Serve a PWA static file. Returns True if handled."""
        data = _STATIC.get(path)
        if data is None:
            return False
        fname = path.lstrip('/')
        ctype = _MIME.get(fname, 'application/octet-stream')
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(data)
        return True

    def _proxy(self, method: str, body: bytes | None = None):
        """Forward the request to BACKEND_URL, inject PWA tags if HTML."""
        url = BACKEND_URL + self.path
        # Copy headers, skip hop-by-hop ones
        headers = {
            k: v for k, v in self.headers.items()
            if k.lower() not in ('host', 'content-length', 'transfer-encoding')
        }
        if body:
            headers['Content-Length'] = str(len(body))

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                ctype  = resp.headers.get('Content-Type', '')
                raw    = resp.read()
                status = resp.status

                # Inject PWA tags into HTML pages
                if 'text/html' in ctype:
                    html = raw.decode('utf-8', errors='replace')
                    html = _inject_pwa(html)
                    raw  = html.encode('utf-8')

                self.send_response(status)
                for k, v in resp.headers.items():
                    # Skip headers we're managing ourselves
                    if k.lower() in ('content-length', 'transfer-encoding',
                                     'content-encoding'):
                        continue
                    self.send_header(k, v)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        except urllib.error.HTTPError as e:
            raw = e.read()
            self.send_response(e.code)
            self.send_header('Content-Type', e.headers.get('Content-Type', 'text/plain'))
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        except Exception as exc:
            msg = f'<h3>Proxy Error</h3><pre>{exc}</pre>\n<p>Is standalone_server.py running at {BACKEND_URL}?</p>'
            body = msg.encode('utf-8')
            self.send_response(502)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    # ── routes ──

    def do_GET(self):
        path = urlparse(self.path).path
        if not self._serve_static(path):
            self._proxy('GET')

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body   = self.rfile.read(length) if length else None
        self._proxy('POST', body)


class _ThreadingServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True


# ── Entry point ───────────────────────────────────────────────────────────

if __name__ == '__main__':
    missing = [f for f in _MIME if f'/{f}' not in _STATIC]
    if missing:
        print(f"  Warning: missing PWA files: {missing}")

    print(f"PWA Proxy  →  http://127.0.0.1:{PORT}")
    print(f"Backend    →  {BACKEND_URL}")
    print(f"PWA files  →  {BASE_DIR}\n")

    server = _ThreadingServer(('0.0.0.0', PORT), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
