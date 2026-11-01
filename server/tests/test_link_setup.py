"""G25: `finance.py link` checks every piece `tailscale serve` needs, in
order, and prints the fix for each missing one."""
import io
import json
import unittest
from unittest.mock import patch

from finance import link_setup

STATUS_OK = {
    "BackendState": "Running",
    "Self": {"DNSName": "mac.tail1234.ts.net.", "CertDomains": ["tail1234.ts.net"]},
    "CurrentTailnet": {"MagicDNSSuffix": "tail1234.ts.net"},
}
SERVE_OK = {
    "TCP": {"443": {"HTTPS": True}},
    "Web": {"mac.tail1234.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8770"}}}},
}


def _tailscale_from(status=STATUS_OK, serve=SERVE_OK):
    def _tailscale(*args, timeout=10):
        if args[:2] == ("status", "--json"):
            return status
        if args[:3] == ("serve", "status", "--json"):
            return serve
        return None
    return _tailscale


class _Proc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


class _Body(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _patch_chain(status=STATUS_OK, serve=SERVE_OK, binary=True, hub=True):
    """The four seams a checklist run touches, all happy by default."""
    body = _Body(json.dumps({"ok": True}).encode() if hub else b"{}")
    urlopen = patch("urllib.request.urlopen", return_value=body) if hub \
        else patch("urllib.request.urlopen", side_effect=OSError("closed"))
    return [patch.object(link_setup, "_tailscale", side_effect=_tailscale_from(status, serve)),
            patch("shutil.which", return_value="/usr/local/bin/tailscale" if binary else None),
            patch("subprocess.run", return_value=_Proc("1.104.2\n")),
            urlopen]


def _run(status=STATUS_OK, serve=SERVE_OK, binary=True, hub=True):
    for p in _patch_chain(status, serve, binary, hub):
        p.start()
    try:
        return dict((name, (detail, fix)) for name, detail, fix in link_setup.checklist())
    finally:
        patch.stopall()


class ChecklistTest(unittest.TestCase):
    def test_everything_ready(self):
        rows = _run()
        for name in ("tailscale", "signed in", "magicdns", "https certs",
                     "tailscale serve", "hub"):
            detail, fix = rows[name]
            self.assertEqual(fix, "", name)
            self.assertNotEqual(detail, "", name)
        self.assertIn("8770", rows["tailscale serve"][0])

    def test_no_binary_fails_the_tailscale_lines_but_still_checks_the_hub(self):
        rows = _run(binary=False, hub=True)
        self.assertEqual(rows["tailscale"][1], "install Tailscale: brew install tailscale, then open it once")
        for name in ("signed in", "magicdns", "https certs", "tailscale serve"):
            self.assertEqual(rows[name][1], "install Tailscale first", name)
        self.assertEqual(rows["hub"][1], "", "the hub check still runs")

    def test_signed_out_tailnet(self):
        rows = _run(status={"BackendState": "NeedsLogin", "Self": {}, "CurrentTailnet": {}},
                    serve=None)
        for name in ("signed in", "magicdns", "https certs"):
            self.assertEqual(rows[name][1], "sign in to Tailscale first", name)

    def test_magicdns_off(self):
        status = {**STATUS_OK, "CurrentTailnet": {}}
        self.assertIn("MagicDNS", _run(status=status)["magicdns"][1])

    def test_https_certs_not_enabled(self):
        status = {**STATUS_OK,
                  "Self": {"DNSName": "mac.tail1234.ts.net.", "CertDomains": None}}
        self.assertIn("HTTPS certificates", _run(status=status)["https certs"][1])

    def test_serve_missing_or_proxying_another_port(self):
        self.assertIn("tailscale serve --bg 8770", _run(serve=None)["tailscale serve"][1])
        other = {"Web": {"mac.tail1234.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8477"}}}}}
        self.assertIn("tailscale serve --bg 8770", _run(serve=other)["tailscale serve"][1])
        local = {"Web": {"mac.tail1234.ts.net:443": {"Handlers": {"/": {"Proxy": "http://localhost:8770"}}}}}
        self.assertEqual(_run(serve=local)["tailscale serve"][1], "", "localhost counts too")

    def test_serve_on_another_tls_port_or_path_does_not_count(self):
        port = {"Web": {"mac.tail1234.ts.net:8443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8770"}}}}}
        self.assertIn("tailscale serve --bg 8770", _run(serve=port)["tailscale serve"][1],
                      "the phone reaches the hub on 443")
        path = {"Web": {"mac.tail1234.ts.net:443": {"Handlers": {"/api": {"Proxy": "http://127.0.0.1:8770"}}}}}
        self.assertIn("tailscale serve --bg 8770", _run(serve=path)["tailscale serve"][1],
                      "the / handler is what carries /api/health")

    def test_custom_port_checks_the_serve_proxy_and_hub_there(self):
        serve = {"Web": {"mac.tail1234.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:9999"}}}}}
        for p in _patch_chain(serve=serve):
            p.start()
        try:
            rows9 = dict((n, (d, f)) for n, d, f in link_setup.checklist(9999))
            rows0 = dict((n, (d, f)) for n, d, f in link_setup.checklist())
        finally:
            patch.stopall()
        self.assertEqual(rows9["tailscale serve"][1], "", "a proxy to the named port counts")
        self.assertIn("tailscale serve --bg 8770", rows0["tailscale serve"][1],
                      "the same entry does not count for the default port")

    def test_hub_down(self):
        self.assertEqual(_run(hub=False)["hub"][1], "start the server: make serve")


if __name__ == "__main__":
    unittest.main()
