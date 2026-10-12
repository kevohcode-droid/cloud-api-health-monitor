import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

healthy = True


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_error(404)
            return
        status = 200 if healthy else 503
        payload = json.dumps({"status": "ok" if healthy else "unavailable"}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        global healthy
        if self.path == "/fail":
            healthy = False
        elif self.path == "/recover":
            healthy = True
        else:
            self.send_error(404)
            return
        self.send_response(204)
        self.end_headers()

if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8765), HealthHandler)
    print("Test target listening at http://127.0.0.1:8765/health")
    print("POST /fail to simulate an outage; POST /recover to restore it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
