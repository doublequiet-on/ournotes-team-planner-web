"""Isolated loopback application for testing song-independent team candidates."""
from __future__ import annotations
import argparse
import hashlib
from collections import OrderedDict
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
import webbrowser
import team_candidates as teams

ROOT = Path(__file__).resolve().parent
WEB = ROOT / 'web'
TOKEN = secrets.token_urlsafe(32)
IDENTITY = 'ournotes-local-team-lab-v1'
LOCK = threading.Lock()


class Cache:
    def __init__(self, path):
        self.lock = threading.Lock()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS results (key TEXT PRIMARY KEY, body TEXT NOT NULL)')
        self.db.commit()

    def get(self, key):
        with self.lock:
            row = self.db.execute('SELECT body FROM results WHERE key=?', (key,)).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row[0])
            if not isinstance(value, dict) or not isinstance(value.get('rows'), list):
                return None
            return value
        except (ValueError, TypeError):
            return None

    def put(self, key, value):
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False)
        with self.lock:
            self.db.execute('INSERT OR REPLACE INTO results VALUES (?,?)', (key, raw))
            self.db.commit()


from sample import sample


class Server(ThreadingHTTPServer):
    allow_reuse_address = not hasattr(socket, 'SO_EXCLUSIVEADDRUSE')
    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if not self.path.startswith('/api/jobs/'):
            super().log_message(fmt, *args)

    def reply(self, value, status=200):
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def local(self):
        if self.headers.get('Host') not in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'):
            self.reply({'error': '请使用本地地址打开。'}, 403)
            return False
        return True

    def do_GET(self):
        if not self.local():
            return
        path = urlparse(self.path).path
        if path == '/api/health':
            return self.reply({'app': IDENTITY, 'version': teams.VERSION})
        if path == '/api/bootstrap':
            catalog = teams.workbench_catalog(self.server.data)
            return self.reply({'catalog': catalog, 'sample': sample(self.server.data), 'token': TOKEN,
                               'version': teams.VERSION, 'defaults': teams.DEFAULT, 'bdon_url': teams.BDON,
                               'engine_signature': hashlib.sha256(Path(teams.__file__).read_bytes()).hexdigest()
                                                   + ':' + self.server.data.fingerprint()})
        if path.startswith('/api/jobs/'):
            with LOCK:
                job = self.server.jobs.get(path.rsplit('/', 1)[-1])
                result = {k: v for k, v in job.items() if k != 'cancel'} if job else None
            return self.reply(result or {'error': '未找到这次计算。'}, 200 if job else 404)
        target = WEB / ('index.html' if path == '/' else path.lstrip('/'))
        if not target.resolve().is_relative_to(WEB.resolve()) or target.suffix not in ('.html', '.css', '.js', '.webp') or not target.is_file():
            return self.reply({'error': '页面不存在。'}, 404)
        raw = target.read_bytes()
        mime = {'.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
                '.js': 'text/javascript; charset=utf-8', '.webp': 'image/webp'}[target.suffix]
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        if not self.local():
            return
        if self.headers.get('X-Planner-Token') != TOKEN or self.headers.get('Origin', f"http://{self.headers.get('Host')}") != f"http://{self.headers.get('Host')}":
            return self.reply({'error': '页面已过期，请刷新。'}, 403)
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 1024 * 1024:
                raise ValueError('卡库文件为空或超过 1 MiB。')
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError('请求格式不正确。')
            path = urlparse(self.path).path
            if path == '/api/check-growth':
                return self.reply(teams.growth_check(body, self.server.data))
            if path == '/api/evaluate':
                return self.reply(teams.evaluate(body, self.server.data))
            if path.startswith('/api/jobs/') and path.endswith('/cancel'):
                with LOCK:
                    job = self.server.jobs.get(path.split('/')[-2])
                    if job:
                        job['cancel'].set()
                return self.reply({'requested': True})
            if path == '/api/exit':
                with LOCK:
                    if any(j['status'] == 'running' for j in self.server.jobs.values()):
                        return self.reply({'error': '请先取消计算，再退出。'}, 409)
                self.reply({'stopping': True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if path != '/api/optimize':
                return self.reply({'error': '操作不存在。'}, 404)
            request = teams.parse_request(body, self.server.data)
            with LOCK:
                if any(j['status'] == 'running' for j in self.server.jobs.values()):
                    return self.reply({'error': '已有计算运行，请等待或取消。'}, 409)
                while len(self.server.jobs) >= 8:
                    self.server.jobs.popitem(last=False)
                jid = secrets.token_hex(12)
                stop = threading.Event()
                self.server.jobs[jid] = {'id': jid, 'status': 'running', 'stage': '准备配队',
                                         'done': 0, 'total': request['team_settings']['count'], 'cancel': stop}
            def run():
                def update(**kw):
                    with LOCK:
                        self.server.jobs[jid].update(kw)
                try:
                    result = teams.optimize(request, self.server.data, update, stop.is_set, self.server.cache)
                    if stop.is_set():
                        raise teams.p.Cancelled()
                    update(status='complete', stage='配队候选已完成', result=result)
                except teams.p.Cancelled:
                    update(status='cancelled', stage='已取消；完整候选已保存，再次计算可接着找。')
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    update(status='error', error=str(e))
            threading.Thread(target=run, daemon=True).start()
            self.reply({'job_id': jid}, 202)
        except (ValueError, TypeError, KeyError) as e:
            self.reply({'error': str(e)}, 400)


def make_server(port=8768, cache_path=None):
    server = Server(('127.0.0.1', port), Handler)
    server.data = teams.p.Data()
    server.jobs = OrderedDict()
    state_root = Path(sys.executable).parent if getattr(sys, 'frozen', False) else ROOT
    server.cache = Cache(cache_path or state_root / 'local-state/candidates.sqlite3')
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8768)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    try:
        server = make_server(args.port)
    except OSError:
        import urllib.request
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(f'http://127.0.0.1:{args.port}/api/health', timeout=2) as response:
                identity = json.load(response)
            if identity.get('app') == IDENTITY and identity.get('version') == teams.VERSION:
                if not args.no_browser:
                    webbrowser.open(f'http://127.0.0.1:{args.port}/')
                print('本地测试版已运行，复用现有窗口。', flush=True)
                return
        except (ValueError, OSError):
            pass
        raise SystemExit(f'端口 {args.port} 已占用，请用 --port 8769。')
    address = f'http://127.0.0.1:{server.server_port}/'
    print(f'Our Notes 配队候选本地测试版 {teams.VERSION}\n{address}\n请保持此窗口打开。', flush=True)
    if not args.no_browser:
        webbrowser.open(address)
    try:
        server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:
        pass
    finally:
        with LOCK:
            for job in server.jobs.values():
                job['cancel'].set()
        server.server_close()


if __name__ == '__main__':
    main()
