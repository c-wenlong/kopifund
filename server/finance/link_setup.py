"""G25: `finance.py link` prints the phone link's setup checklist.

Each line is one thing the link needs (SPEC.md, "Phone link (G25)"): the
tailscale binary, a signed-in tailnet, MagicDNS, HTTPS certs, `tailscale
serve` proxying 443 to 127.0.0.1:8770, and the hub's /api/health answering.
A miss prints the fix instead of a detail. `checklist()` returns
(name, detail, fix) rows in order; `cmd_link` in finance.py prints them and
exits nonzero while any fix is printed. The shape follows a one-shot setup
checklist.
"""
import json
import shutil
import subprocess
import urllib.request

PORT = 8770


def _tailscale(*args, timeout=10):
    """`tailscale <args>` parsed as JSON, or None when it cannot run or did
    not answer JSON."""
    try:
        out = subprocess.run(["tailscale", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except ValueError:
        return None


class _Missing(Exception):
    """One check's fix line."""

    def __init__(self, fix):
        self.fix = fix


def _check_binary():
    where = shutil.which("tailscale")
    if not where:
        raise _Missing("install Tailscale: brew install tailscale, then open it once")
    try:
        out = subprocess.run(["tailscale", "version"], capture_output=True, text=True, timeout=5)
        version = out.stdout.splitlines()[0].strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        version = ""
    return f"{version} ({where})" if version else where


def _check_signed_in(status):
    name = (status.get("Self") or {}).get("DNSName", "").rstrip(".")
    if status.get("BackendState") != "Running" or not name:
        raise _Missing("sign in to Tailscale: open the app or run `tailscale up`")
    return name


def _check_magicdns(status):
    suffix = (status.get("CurrentTailnet") or {}).get("MagicDNSSuffix", "")
    if not suffix:
        raise _Missing("turn on MagicDNS in the tailnet's admin console, under DNS")
    return suffix


def _check_https(status):
    domains = (status.get("Self") or {}).get("CertDomains") or []
    if not domains:
        raise _Missing("enable HTTPS certificates in the tailnet's admin console, under DNS, "
                       "then run `tailscale cert` once")
    return ", ".join(domains)


def _check_serve(serve, port):
    """The phone reaches the hub through tailscale serve's HTTPS 443, so the
    serve status has to show a Web entry on port 443 whose "/" handler
    proxies to 127.0.0.1:{port} (or localhost): another published port, or a
    handler under another path, does not carry /api/health."""
    for key, handlers in (serve or {}).get("Web", {}).items():
        if key.rsplit(":", 1)[-1] != "443":
            continue
        proxy = (((handlers.get("Handlers") or {}).get("/") or {}).get("Proxy") or "").rstrip("/")
        if proxy in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
            return f"443 to {proxy}"
    raise _Missing(f"run: tailscale serve --bg {port}")


def _check_hub(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as r:
            ok = json.loads(r.read() or b"{}").get("ok")
    except (OSError, ValueError):
        ok = False
    if not ok:
        raise _Missing("start the server: make serve" + ("" if port == PORT else f" PORT={port}"))
    return "/api/health answered"


def checklist(port=PORT):
    """[(name, detail, fix)] in order; detail when ok, the fix when not.
    `port` is the hub's port, which the serve proxy must reach."""
    steps = []

    def run(name, fn, *args):
        try:
            steps.append((name, fn(*args), ""))
        except _Missing as e:
            steps.append((name, "", e.fix))

    run("tailscale", _check_binary)
    if not steps[-1][1]:
        # no binary: the tailscale lines all share one fix, the hub's is its own
        for name in ("signed in", "magicdns", "https certs", "tailscale serve"):
            steps.append((name, "", "install Tailscale first"))
        run("hub", _check_hub, port)
        return steps
    status = _tailscale("status", "--json") or {}

    def need_status(fn):
        if status.get("BackendState") != "Running":
            raise _Missing("sign in to Tailscale first")
        return fn(status)

    run("signed in", need_status, _check_signed_in)
    run("magicdns", need_status, _check_magicdns)
    run("https certs", need_status, _check_https)
    run("tailscale serve", _check_serve, _tailscale("serve", "status", "--json"), port)
    run("hub", _check_hub, port)
    return steps
