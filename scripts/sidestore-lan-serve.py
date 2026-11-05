"""Serve the SideStore folder (frontend/public/sidestore) read-only over HTTPS
on one address of the home network.

SideStore installs only while LocalDevVPN is connected, and iOS runs one VPN
at a time, so a phone installing is off the tailnet and cannot reach the
public origin. On the home Wi-Fi it can reach this instead.

Usage: sidestore-lan-serve.py <host> <port> <cert.pem> <key.pem> [dir]
The certificate must be one the phone trusts for <host>.
"""
import functools
import http.server
import os
import ssl
import sys

host, port, cert, key = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
root = sys.argv[5] if len(sys.argv) > 5 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "frontend", "public", "sidestore")


class Handler(http.server.SimpleHTTPRequestHandler):
    def list_directory(self, path):
        self.send_error(404)

    def end_headers(self):
        self.send_header("cache-control", "no-store")
        super().end_headers()


ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(cert, key)
server = http.server.ThreadingHTTPServer(
    (host, port), functools.partial(Handler, directory=os.path.abspath(root)))
server.socket = ctx.wrap_socket(server.socket, server_side=True)
print(f"serving {os.path.abspath(root)} at https://{host}:{port}/", flush=True)
server.serve_forever()
