#!/usr/bin/env python3
"""LAN gallery server for the Lumen Gallery fork.

Serves the Radishchev Museum wallpaper collection to the TV app:

- GET /manifest.json  works in the app's Artwork format (src/config/art.ts),
                      source 'r'; built from tools/out/radmuseum.json (one
                      entry per month, local files only);
- GET /images/<name>  a JPEG from the wallpaper folder.

Read-only, LAN-only, no authentication: the collection is public and the
service binds the home network. Run under systemd (server/radmuseum-gallery.service).

Env:  GALLERY_DATA  path to radmuseum.json (default: ../tools/out/radmuseum.json)
      GALLERY_DIR   wallpaper folder (default: /mnt/homestorage/media/Картины)
      GALLERY_PORT  port (default: 8790)
"""
import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("GALLERY_DATA", os.path.join(ROOT, "..", "tools", "out", "radmuseum.json"))
DIR = os.environ.get("GALLERY_DIR", "/mnt/homestorage/media/Картины")
PORT = int(os.environ.get("GALLERY_PORT", "8790"))


def manifest():
    with open(DATA, encoding="utf-8") as f:
        entries = json.load(f)["entries"]
    out, seen = [], set()
    for e in entries:
        if e["key"] in seen or not e.get("file"):
            continue
        seen.add(e["key"])
        out.append({
            "id": "r-" + e["key"],
            "t": e["title"] or "Радищевский музей",
            "a": e["artist"] or "Саратовский музей им. А.Н. Радищева",
            "d": (e.get("catalog") or {}).get("date") or "",
            "u": e["file"],
            "w": e["w"], "h": e["h"], "p": e["p"], "n": e["n"],
            "c": e["collection"], "s": "r",
        })
    return {"server": "radmuseum", "entries": out}


class Handler(BaseHTTPRequestHandler):
    server_version = "RadmuseumGallery/1.0"

    def _send(self, code, body, ctype):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        if path == "/manifest.json":
            try:
                self._send(200, json.dumps(manifest(), ensure_ascii=False), "application/json; charset=utf-8")
            except OSError as e:
                self._send(500, '{"error": "%s"}' % e, "application/json")
            return
        if path.startswith("/images/"):
            name = os.path.basename(path[len("/images/"):])  # never leave the folder
            full = os.path.join(DIR, name)
            if name and os.path.isfile(full):
                with open(full, "rb") as f:
                    self._send(200, f.read(), "image/jpeg")
                return
        self._send(404, '{"error": "not found"}', "application/json")

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
