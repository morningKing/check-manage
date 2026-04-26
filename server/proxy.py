"""
Reverse proxy server for production deployment.

- Serves compiled frontend assets from ../dist/
- Proxies /api/* requests to the Flask backend (strip /api prefix)
- Handles SPA history fallback (serves index.html for non-file routes)
"""

import os
import sys
import mimetypes
import urllib.request
import urllib.error
import subprocess
import time
import signal
from http.server import HTTPServer, SimpleHTTPRequestHandler

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PROXY_PORT = int(os.environ.get('PROXY_PORT', 8080))
BACKEND_URL = os.environ.get('BACKEND_URL', 'http://127.0.0.1:3001')
DIST_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'dist'))

# Ensure mimetypes are correct on Windows
mimetypes.add_type('application/javascript', '.js')
mimetypes.add_type('text/css', '.css')
mimetypes.add_type('image/svg+xml', '.svg')


class ProxyHandler(SimpleHTTPRequestHandler):
    """Handle static files from dist/ and proxy /api to Flask backend."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIST_DIR, **kwargs)

    # -- Proxy /api/* to backend ------------------------------------------

    def _proxy_to_backend(self):
        # Strip /api prefix: /api/auth/login -> /auth/login
        backend_path = self.path[4:]  # remove leading "/api"
        if not backend_path:
            backend_path = '/'
        url = BACKEND_URL + backend_path

        # Read request body
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length > 0 else None

        # Build upstream request, forward relevant headers
        req = urllib.request.Request(url, data=body, method=self.command)
        for header in ('Content-Type', 'Authorization', 'X-API-Key', 'Accept'):
            value = self.headers.get(header)
            if value:
                req.add_header(header, value)

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                resp_body = resp.read()
                self.send_response(resp.status)
                for key, val in resp.getheaders():
                    if key.lower() in ('content-type', 'content-disposition',
                                       'x-total-count', 'cache-control'):
                        self.send_header(key, val)
                self.send_header('Content-Length', len(resp_body))
                self.end_headers()
                self.wfile.write(resp_body)
        except urllib.error.HTTPError as e:
            resp_body = e.read()
            self.send_response(e.code)
            content_type = e.headers.get('Content-Type', 'application/json')
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', len(resp_body))
            self.end_headers()
            self.wfile.write(resp_body)
        except urllib.error.URLError as e:
            msg = f'{{"error":"Backend unavailable: {e.reason}"}}'.encode()
            self.send_response(502)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', len(msg))
            self.end_headers()
            self.wfile.write(msg)

    # -- Static file serving with SPA fallback ----------------------------

    def _serve_static_or_fallback(self):
        # Determine the file path relative to dist/
        path = self.path.split('?')[0].split('#')[0]
        if path == '/':
            path = '/index.html'

        file_path = os.path.normpath(os.path.join(DIST_DIR, path.lstrip('/')))

        # Security: prevent directory traversal
        if not file_path.startswith(DIST_DIR):
            self.send_error(403, 'Forbidden')
            return

        if os.path.isfile(file_path):
            self._send_file(file_path)
        else:
            # SPA fallback: serve index.html for any non-file route
            self._send_file(os.path.join(DIST_DIR, 'index.html'))

    def _send_file(self, file_path):
        content_type, _ = mimetypes.guess_type(file_path)
        if content_type is None:
            content_type = 'application/octet-stream'

        try:
            with open(file_path, 'rb') as f:
                data = f.read()
        except OSError:
            self.send_error(404, 'File not found')
            return

        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', len(data))

        # Cache static assets with hashed filenames
        if '/assets/' in file_path:
            self.send_header('Cache-Control', 'public, max-age=31536000, immutable')
        else:
            self.send_header('Cache-Control', 'no-cache')

        self.end_headers()
        self.wfile.write(data)

    # -- Route dispatch ---------------------------------------------------

    def do_GET(self):
        if self.path.startswith('/api/') or self.path == '/api':
            self._proxy_to_backend()
        else:
            self._serve_static_or_fallback()

    def do_POST(self):
        self._proxy_to_backend() if self.path.startswith('/api') else self.send_error(405)

    def do_PUT(self):
        self._proxy_to_backend() if self.path.startswith('/api') else self.send_error(405)

    def do_DELETE(self):
        self._proxy_to_backend() if self.path.startswith('/api') else self.send_error(405)

    def do_PATCH(self):
        self._proxy_to_backend() if self.path.startswith('/api') else self.send_error(405)

    def do_OPTIONS(self):
        # Handle CORS preflight
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, PATCH, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-API-Key')
        self.send_header('Access-Control-Max-Age', '86400')
        self.end_headers()

    # Suppress default request logging clutter
    def log_message(self, format, *args):
        status = args[1] if len(args) > 1 else ''
        print(f'  {self.command:7s} {self.path} -> {status}')


def start_backend():
    """Start the Flask backend as a subprocess (non-debug for production)."""
    server_dir = os.path.dirname(__file__)
    env = os.environ.copy()
    # Force non-debug mode to avoid Werkzeug reloader issues
    env['FLASK_DEBUG'] = '0'
    proc = subprocess.Popen(
        [sys.executable, '-c',
         'import app; app.app.run(host="0.0.0.0", port=3001, debug=False)'],
        cwd=server_dir,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc


def wait_for_backend(url, timeout=15):
    """Wait until the backend responds."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            urllib.request.urlopen(url + '/auth/login', timeout=2)
        except urllib.error.HTTPError:
            return True  # got a response (405 etc.), server is up
        except Exception:
            time.sleep(0.5)
    return False


def main():
    if not os.path.isdir(DIST_DIR):
        print(f'[ERROR] dist/ not found at {DIST_DIR}')
        print('        Run "npm run build" first to compile frontend assets.')
        sys.exit(1)

    if not os.path.isfile(os.path.join(DIST_DIR, 'index.html')):
        print(f'[ERROR] index.html not found in {DIST_DIR}')
        sys.exit(1)

    # Start Flask backend
    print(f'[1/2] Starting backend (Flask) ...', flush=True)
    backend_proc = start_backend()

    if wait_for_backend(BACKEND_URL):
        print(f'       Backend ready at {BACKEND_URL}', flush=True)
    else:
        print(f'       [WARN] Backend may not be ready yet', flush=True)

    # Start reverse proxy
    print(f'[2/2] Starting reverse proxy ...', flush=True)
    server = HTTPServer(('0.0.0.0', PROXY_PORT), ProxyHandler)
    print(f'       Serving at http://localhost:{PROXY_PORT}', flush=True)
    print(f'       Static files: {DIST_DIR}', flush=True)
    print(f'       API proxy:    /api/* -> {BACKEND_URL}', flush=True)
    print(flush=True)

    def shutdown(sig=None, frame=None):
        print('\n[Shutdown] Stopping services...', flush=True)
        try:
            # Force kill backend process on Windows
            if sys.platform == 'win32':
                backend_proc.kill()
            else:
                backend_proc.terminate()

            # Shutdown HTTP server in a thread to avoid blocking
            import threading
            shutdown_thread = threading.Thread(target=server.shutdown)
            shutdown_thread.daemon = True
            shutdown_thread.start()

            # Wait for backend to terminate (with timeout)
            try:
                backend_proc.wait(timeout=3)
                print('[Shutdown] Backend stopped.', flush=True)
            except subprocess.TimeoutExpired:
                print('[Shutdown] Backend kill forced.', flush=True)
                backend_proc.kill()
                backend_proc.wait()

            print('[Shutdown] Done.', flush=True)
            sys.exit(0)
        except Exception as e:
            print(f'[Shutdown] Error: {e}', flush=True)
            sys.exit(1)

    # Register signal handlers
    signal.signal(signal.SIGINT, shutdown)
    if sys.platform != 'win32':
        signal.signal(signal.SIGTERM, shutdown)
    else:
        # On Windows, also handle SIGBREAK (Ctrl+Break)
        signal.signal(signal.SIGBREAK, shutdown)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        shutdown()
    except Exception as e:
        print(f'[Error] Server error: {e}', flush=True)
        shutdown()
    finally:
        # Cleanup
        if backend_proc.poll() is None:
            backend_proc.kill()
            backend_proc.wait()


if __name__ == '__main__':
    main()
