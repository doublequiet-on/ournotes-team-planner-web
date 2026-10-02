"""Static preview without APIs or isolation headers, including a Pages subpath."""
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=8877)
parser.add_argument("--prefix", default="/ournotes-planner/")
args = parser.parse_args()
directory = Path(__file__).resolve().parents[1] / "dist"


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".wasm": "application/wasm", ".mjs": "text/javascript"}

    def do_GET(self):
        if self.path.startswith(args.prefix):
            self.path = "/" + self.path[len(args.prefix):]
            return super().do_GET()
        return self.send_error(404)

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, directory=str(directory)))
print(f"Static preview: http://127.0.0.1:{args.port}{args.prefix}", flush=True)
server.serve_forever()
