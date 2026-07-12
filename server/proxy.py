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
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse
from dotenv import load_dotenv

# Load server/.env so PROXY_*/BACKEND_URL/MCP_*/CORS_* take effect from the file
# (this process is separate from Flask's config.py, which loads it for the app).
load_dotenv(Path(__file__).resolve().parent / '.env', override=False)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PROXY_PORT = int(os.environ.get('PROXY_PORT', 8080))
PROXY_HOST = os.environ.get('PROXY_HOST', '0.0.0.0')
BACKEND_URL = os.environ.get('BACKEND_URL', 'http://127.0.0.1:3001')
DIST_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'dist'))

# AI chat dependencies (see CLAUDE.md "AI Agent Chat"):
#   - MCP server: standalone service started here, has its OWN venv.
#   - OpenCode: external agent runtime (`opencode serve`), NOT started here
#     because it holds provider API keys in its own global config.
MCP_HEALTH_URL = os.environ.get('MCP_HEALTH_URL', 'http://127.0.0.1:3003/health')
OPENCODE_BASE_URL = os.environ.get('OPENCODE_BASE_URL', 'http://127.0.0.1:4096')

# Managed subprocesses log here (NOT /dev/null) so a failed start leaves a
# diagnosable trace. .gitignore already excludes *.log.
BACKEND_LOG = os.path.join(os.path.dirname(__file__), 'proxy-backend.log')
MCP_LOG = os.path.join(os.path.dirname(__file__), 'proxy-mcp.log')

# Ensure mimetypes are correct on Windows
mimetypes.add_type('application/javascript', '.js')
mimetypes.add_type('text/css', '.css')
mimetypes.add_type('image/svg+xml', '.svg')


def _backend_port():
    """Port the proxy launches the backend on = the port it proxies to (from
    BACKEND_URL), so they can never drift apart."""
    return urlparse(BACKEND_URL).port or 3001


def _allowed_origins():
    return [o.strip() for o in os.environ.get('CORS_ALLOWED_ORIGINS', '').split(',') if o.strip()]


def _cors_origin(request_origin):
    """Resolve the Access-Control-Allow-Origin value for a preflight: echo the
    request Origin if it's in CORS_ALLOWED_ORIGINS; if the list is empty, fall
    back to '*' (unchanged default); otherwise return '' (not allowed)."""
    allowed = _allowed_origins()
    if not allowed:
        return '*'
    return request_origin if request_origin in allowed else ''


def _safe_upstream_path(path):
    """BaseHTTPRequestHandler decodes the raw request line as latin-1, so a
    client that sends a non-percent-encoded UTF-8 byte in the path/query (e.g.
    a raw Chinese character) leaves it in `self.path` as a char in the
    0x80-0xFF range. http.client's request line must be pure ASCII, so passing
    that straight to urllib.request.Request crashes with UnicodeEncodeError
    (proxy-backend 500, connection reset for the client) before the request
    ever reaches the backend. Percent-encode only the non-ASCII bytes here;
    ASCII bytes (including any %XX a well-behaved client already sent) pass
    through untouched, so normal percent-encoded URLs are unaffected."""
    raw = path.encode('latin-1', errors='surrogateescape')
    return ''.join(chr(b) if b < 0x80 else '%{:02X}'.format(b) for b in raw)


class ProxyHandler(SimpleHTTPRequestHandler):
    """Handle static files from dist/ and proxy /api to Flask backend."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIST_DIR, **kwargs)

    # -- Proxy /api/* to backend ------------------------------------------

    def _is_sse(self):
        """SSE event streams that must be relayed chunk-by-chunk (never buffered):
        AI chat  /api/ai/chat/sessions/<id>/events
        kefu     /api/kefu/sessions/<sid>/events        (visitor OpenCode proxy)
                 /api/kefu/sessions/<sid>/human-events  (visitor human-takeover, C1)
                 /api/admin/kefu/events                 (admin console, C2)

        Match by suffix (`/events` OR `/human-events`) AND an AI-chat/kefu prefix.
        NOTE: any NEW SSE route must be added here or the proxy will buffer it to
        completion and the stream will hang in production (dev via Vite masks this
        — Vite proxies SSE natively). See CLAUDE.md "proxy.py verification"."""
        path = self.path.split('?')[0]
        return path.endswith(('/events', '/human-events')) and \
            ('/ai/chat/' in path or '/kefu/' in path)

    def _proxy_to_backend(self, stream=False):
        # Strip /api prefix: /api/auth/login -> /auth/login
        backend_path = self.path[4:]  # remove leading "/api"
        if not backend_path:
            backend_path = '/'
        url = BACKEND_URL + _safe_upstream_path(backend_path)

        # Read request body
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length > 0 else None

        # Build upstream request, forward relevant headers
        req = urllib.request.Request(url, data=body, method=self.command)
        for header in ('Content-Type', 'Authorization', 'X-API-Key', 'X-Visitor-Id', 'Accept'):
            value = self.headers.get(header)
            if value:
                req.add_header(header, value)

        # SSE streams are long-lived and must never be read to completion or
        # buffered; disable the read timeout and relay chunk-by-chunk.
        timeout = None if stream else 120
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if stream:
                    self._relay_stream(resp)
                else:
                    self._relay_buffered(resp)
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
        except (BrokenPipeError, ConnectionResetError):
            pass  # client disconnected

    def _relay_buffered(self, resp):
        """Read the whole upstream response, then forward it (normal API calls)."""
        resp_body = resp.read()
        self.send_response(resp.status)
        for key, val in resp.getheaders():
            if key.lower() in ('content-type', 'content-disposition',
                               'x-total-count', 'cache-control'):
                self.send_header(key, val)
        self.send_header('Content-Length', len(resp_body))
        self.end_headers()
        self.wfile.write(resp_body)

    def _relay_stream(self, resp):
        """Forward a streaming (SSE) response line-by-line without buffering.

        No Content-Length is sent: the connection stays open for the life of
        the stream and closes when the upstream generator ends or the client
        disconnects. Each chunk is flushed so events reach the browser live.
        """
        self.send_response(resp.status)
        self.send_header('Content-Type',
                         resp.headers.get('Content-Type', 'text/event-stream'))
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('X-Accel-Buffering', 'no')
        self.end_headers()
        try:
            for chunk in resp:
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # client went away; context manager closes the upstream

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
            self._proxy_to_backend(stream=self._is_sse())
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
        # Handle CORS preflight (origin gated by CORS_ALLOWED_ORIGINS; empty list -> '*')
        origin = _cors_origin(self.headers.get('Origin', ''))
        self.send_response(204)
        if origin:
            self.send_header('Access-Control-Allow-Origin', origin)
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, PATCH, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-API-Key')
        self.send_header('Access-Control-Max-Age', '86400')
        self.end_headers()

    # Suppress default request logging clutter
    def log_message(self, format, *args):
        status = args[1] if len(args) > 1 else ''
        msg = f'  {self.command:7s} {self.path} -> {status}'
        # self.path may hold raw non-ASCII bytes (see _safe_upstream_path) that
        # the console's codec (e.g. GBK on zh-CN Windows) cannot represent;
        # never let a log line crash the request it's logging.
        encoding = sys.stdout.encoding or 'utf-8'
        print(msg.encode(encoding, errors='backslashreplace').decode(encoding, errors='replace'))


def start_backend():
    """Start the Flask backend as a subprocess (production WSGI server).

    Uses waitress with a *bounded* thread pool instead of Werkzeug's unbounded
    threaded dev server. The pool size (BACKEND_THREADS, default 8) is kept
    below the DB pool max (ThreadedConnectionPool maxconn=20) so concurrent
    requests can't exhaust DB connections.
    """
    server_dir = os.path.dirname(__file__)
    env = os.environ.copy()
    env['FLASK_DEBUG'] = '0'
    threads = max(1, int(os.environ.get('BACKEND_THREADS', '8')))
    # Send stdout+stderr to a log file, NOT DEVNULL: a failed start (missing
    # 'waitress', DB down, port in use, import error) must be diagnosable rather
    # than vanishing silently. _report_dead_subprocess() surfaces this on failure.
    log = open(BACKEND_LOG, 'w', encoding='utf-8', errors='replace')
    proc = subprocess.Popen(
        [sys.executable, '-c',
         'import app; from waitress import serve; '
         f'serve(app.app, host="0.0.0.0", port={_backend_port()}, threads={threads})'],
        cwd=server_dir,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    return proc


def _read_tail(path, n=40):
    """Return the last `n` lines of a text file (empty string if unreadable)."""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            return ''.join(f.readlines()[-n:])
    except OSError:
        return ''


def _report_dead_subprocess(name, proc, log_path):
    """If `proc` has already exited, print a clear diagnostic (exit code + log
    tail + the most common fix) and return True. If it's still running, print
    nothing and return False.

    This is the fix for "proxy.py can't bring up the backend with no error":
    the child's output now lands in a log instead of /dev/null, and a child
    that dies on startup is reported loudly instead of swallowed."""
    if proc.poll() is None:
        return False
    print(f'       [ERROR] {name} exited immediately (exit code {proc.returncode}).',
          flush=True)
    tail = _read_tail(log_path)
    if tail.strip():
        print(f'       ----- {os.path.basename(log_path)} (last lines) -----', flush=True)
        for line in tail.rstrip().splitlines():
            print(f'       | {line}', flush=True)
    print('       Likely cause: backend dependencies are not installed in this '
          "Python. Run:  pip install -r requirements.txt", flush=True)
    print(f'       Full log: {log_path}', flush=True)
    return True


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


def _mcp_python():
    """Interpreter for the MCP server. It has its OWN venv (fastapi/uvicorn/mcp),
    so we must not assume the production interpreter has those deps. Override
    with MCP_PYTHON; otherwise probe mcp-server/.venv, else fall back."""
    override = os.environ.get('MCP_PYTHON')
    if override:
        return override
    mcp_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'mcp-server'))
    if sys.platform == 'win32':
        cand = os.path.join(mcp_dir, '.venv', 'Scripts', 'python.exe')
    else:
        cand = os.path.join(mcp_dir, '.venv', 'bin', 'python')
    return cand if os.path.isfile(cand) else sys.executable


def start_mcp():
    """Start the standalone MCP server. Returns the process, or None if it can't
    be located (the rest of the app still serves; only AI chat is degraded)."""
    mcp_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'mcp-server'))
    if not os.path.isfile(os.path.join(mcp_dir, 'main.py')):
        print(f'       [WARN] mcp-server not found at {mcp_dir}; AI chat disabled', flush=True)
        return None
    log = open(MCP_LOG, 'w', encoding='utf-8', errors='replace')
    proc = subprocess.Popen(
        [_mcp_python(), 'main.py'],
        cwd=mcp_dir,
        env=os.environ.copy(),
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    return proc


def wait_for_mcp(url, timeout=15):
    """Wait until the MCP server's /health responds."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except urllib.error.HTTPError:
            return True  # responded
        except Exception:
            time.sleep(0.5)
    return False


def check_opencode(base_url, timeout=3):
    """Probe whether OpenCode is reachable. It's an external prerequisite (holds
    provider API keys in its own global config); we only warn if it's down."""
    try:
        urllib.request.urlopen(base_url, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True  # any HTTP response means it's up
    except Exception:
        return False


def main():
    if not os.path.isdir(DIST_DIR):
        print(f'[ERROR] dist/ not found at {DIST_DIR}')
        print('        Run "npm run build" first to compile frontend assets.')
        sys.exit(1)

    if not os.path.isfile(os.path.join(DIST_DIR, 'index.html')):
        print(f'[ERROR] index.html not found in {DIST_DIR}')
        sys.exit(1)

    # Subprocesses we manage; killed on shutdown (OpenCode is NOT one of them).
    procs = []

    # Start Flask backend
    print('[1/3] Starting backend (Flask) ...', flush=True)
    backend_proc = start_backend()
    procs.append(backend_proc)
    if wait_for_backend(BACKEND_URL):
        print(f'       Backend ready at {BACKEND_URL}', flush=True)
    elif _report_dead_subprocess('Backend', backend_proc, BACKEND_LOG):
        # No backend = the proxy would only serve 502s. Fail fast instead of
        # starting a useless proxy (this is what made the failure look silent).
        for p in procs:
            if p is not None and p.poll() is None:
                p.kill()
        sys.exit(1)
    else:
        print(f'       [WARN] Backend slow to respond but still running; '
              f'see {BACKEND_LOG}', flush=True)

    # Start MCP server (AI chat capability provider)
    print('[2/3] Starting MCP server ...', flush=True)
    mcp_proc = start_mcp()
    if mcp_proc is not None:
        procs.append(mcp_proc)
        if wait_for_mcp(MCP_HEALTH_URL):
            print(f'       MCP ready ({MCP_HEALTH_URL})', flush=True)
        elif _report_dead_subprocess('MCP server', mcp_proc, MCP_LOG):
            # MCP only powers AI chat; the rest of the app still serves, so we
            # report the cause but don't abort.
            print('       [WARN] MCP server failed to start; AI chat disabled '
                  '(rest of the app still serves).', flush=True)
        else:
            print('       [WARN] MCP server may not be ready yet', flush=True)

    # OpenCode is an external prerequisite for AI chat: probe and warn only.
    if check_opencode(OPENCODE_BASE_URL):
        print(f'       OpenCode reachable ({OPENCODE_BASE_URL})', flush=True)
    else:
        print(f'       [WARN] OpenCode not reachable at {OPENCODE_BASE_URL}; '
              'AI chat needs it (run: opencode serve)', flush=True)

    # Start reverse proxy (threaded so long-lived SSE streams don't block others)
    print('[3/3] Starting reverse proxy ...', flush=True)
    server = ThreadingHTTPServer((PROXY_HOST, PROXY_PORT), ProxyHandler)
    print(f'       Serving at http://localhost:{PROXY_PORT}', flush=True)
    print(f'       Static files: {DIST_DIR}', flush=True)
    print(f'       API proxy:    /api/* -> {BACKEND_URL}', flush=True)
    print(flush=True)

    def shutdown(sig=None, frame=None):
        print('\n[Shutdown] Stopping services...', flush=True)
        try:
            for p in procs:
                if p is None or p.poll() is not None:
                    continue
                if sys.platform == 'win32':
                    p.kill()
                else:
                    p.terminate()

            # Shutdown HTTP server in a thread to avoid blocking
            import threading
            shutdown_thread = threading.Thread(target=server.shutdown)
            shutdown_thread.daemon = True
            shutdown_thread.start()

            # Wait for each managed process to terminate (with timeout)
            for p in procs:
                if p is None:
                    continue
                try:
                    p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()

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
        for p in procs:
            if p is not None and p.poll() is None:
                p.kill()
                p.wait()


if __name__ == '__main__':
    main()
