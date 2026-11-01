#!/usr/bin/env python3
"""G25: the phone-link e2e rig for the simulator. `tailscale serve` cannot
run in a simulator, so this stands in for it faithfully on the Mac's side:

    - a tiny CA is minted, a leaf for 127.0.0.1 signed under it, and the CA
      installed as a trusted root in the booted simulator's keychain;
    - an HTTPS forwarder on 127.0.0.1:8990 does what serve does: terminate
      TLS, add X-Forwarded-For, proxy to the loopback hub on 8770.

Run `KOPIFUND_DATA=<copy> python3 server/serve.py --port 8770` on a copied
data folder first (AGENTS.md), then `python3 apps/ios/link_e2e.py` and leave
it up while `xcodebuild test` runs LinkUITests. Ctrl-C stops the forwarder.
"""
import http.client
import http.server
import os
import shutil
import socketserver
import ssl
import subprocess
import sys
import tempfile

HUB = ("127.0.0.1", 8770)
LISTEN = ("127.0.0.1", 8990)
FAKE_IP = "100.64.1.5"
WORK = os.path.join(tempfile.gettempdir(), "kopifund-link-e2e")


def sh(*args, check=True):
    p = subprocess.run(args, capture_output=True, text=True)
    if check and p.returncode:
        sys.exit(f"{args[0]} failed: {p.stderr.strip() or p.stdout.strip()}")
    return p.stdout.strip()


def certs():
    """A 2-day CA and a 127.0.0.1/localhost leaf under WORK, remade each run."""
    os.makedirs(WORK, exist_ok=True)
    ca_key, ca = f"{WORK}/ca.key", f"{WORK}/ca.crt"
    leaf_key, csr, leaf = f"{WORK}/leaf.key", f"{WORK}/leaf.csr", f"{WORK}/leaf.crt"
    sh("openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", ca_key, "-out", ca,
       "-days", "2", "-nodes", "-subj", "/CN=kopifund-link-e2e",
       "-addext", "basicConstraints=critical,CA:TRUE")
    sh("openssl", "req", "-newkey", "rsa:2048", "-keyout", leaf_key, "-out", csr,
       "-nodes", "-subj", "/CN=127.0.0.1",
       "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost")
    with tempfile.NamedTemporaryFile("w", suffix=".cnf") as ext:
        ext.write("subjectAltName=IP:127.0.0.1,DNS:localhost\nbasicConstraints=CA:FALSE\n")
        ext.flush()
        sh("openssl", "x509", "-req", "-in", csr, "-CA", ca, "-CAkey", ca_key,
           "-CAcreateserial", "-out", leaf, "-days", "2", "-extfile", ext.name)
    return ca, leaf, leaf_key


def trust(ca):
    """Install the CA as a trusted root in every booted simulator."""
    out = sh("xcrun", "simctl", "list", "devices", "booted")
    ids = [line.split("(")[1].split(")")[0] for line in out.splitlines()
           if "(" in line and "Booted" in line]
    if not ids:
        sys.exit("no booted simulator; boot one first (xcrun simctl boot <id>)")
    for udid in ids:
        sh("xcrun", "simctl", "keychain", udid, "add-root-cert", ca)
        print(f"trusted {ca} in {udid}")


def hub_up():
    try:
        c = http.client.HTTPConnection(*HUB, timeout=2)
        c.request("GET", "/api/health")
        ok = c.getresponse().status == 200
        c.close()
        return ok
    except OSError:
        return False


class Proxy(http.server.BaseHTTPRequestHandler):
    """What tailscale serve does to a request: TLS off at the edge, the
    caller's tailnet IP into X-Forwarded-For, then plain HTTP to loopback."""

    def _go(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else None
        c = http.client.HTTPConnection(*HUB, timeout=15)
        heads = {k: v for k, v in self.headers.items()
                 if k.lower() not in ("host", "content-length", "connection")}
        heads["Host"] = f"{HUB[0]}:{HUB[1]}"
        heads["X-Forwarded-For"] = FAKE_IP
        c.request(self.command, self.path, body=body, headers=heads)
        r = c.getresponse()
        data = r.read()
        self.send_response(r.status)
        for k, v in r.getheaders():
            if k.lower() not in ("transfer-encoding", "connection"):
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)
        c.close()

    do_GET = do_POST = do_HEAD = _go

    def log_message(self, *a):
        pass


def main():
    if not hub_up():
        sys.exit("the hub is not answering: KOPIFUND_DATA=<copy> "
                 "python3 server/serve.py --port 8770 first")
    ca, leaf, leaf_key = certs()
    trust(ca)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(leaf, leaf_key)
    sock = socketserver.ThreadingTCPServer(LISTEN, Proxy)
    sock.socket = ctx.wrap_socket(sock.socket, server_side=True)
    print(f"link e2e up: https://{LISTEN[0]}:{LISTEN[1]} plays tailscale serve "
          f"(+X-Forwarded-For) for http://{HUB[0]}:{HUB[1]}")
    sock.serve_forever()


if __name__ == "__main__":
    main()
