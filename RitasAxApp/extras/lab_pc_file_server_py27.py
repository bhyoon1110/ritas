# -*- coding: utf-8 -*-
from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler
import os
import json
import urllib

DATA_DIR = r"C:\My Documents\O-ring\Spectra"

class Handler(BaseHTTPRequestHandler):
    def _json(self, payload):
        body = json.dumps(payload)
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/list':
            items = []
            for name in os.listdir(DATA_DIR):
                path = os.path.join(DATA_DIR, name)
                if os.path.isfile(path):
                    items.append({
                        'name': name,
                        'sizeBytes': os.path.getsize(path),
                        'modifiedAt': str(int(os.path.getmtime(path))),
                    })
            self._json(items)
            return

        if self.path.startswith('/download/'):
            name = urllib.unquote(self.path.replace('/download/', ''))
            path = os.path.join(DATA_DIR, name)
            if os.path.exists(path):
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.end_headers()
                with open(path, 'rb') as f:
                    self.wfile.write(f.read())
                return

        self.send_response(404)
        self.end_headers()

if __name__ == '__main__':
    print('Serving on 0.0.0.0:8080 from %s' % DATA_DIR)
    HTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
