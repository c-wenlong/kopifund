"""G25: the phone link's registry and its gate in serve.py.

server/finance/link.py owns ~/.kopifund/devices.json (KOPIFUND_DEVICES here):
one-time pair tokens spent under a flock, and the device bearers the link
path asks about. The gate tests run a real server on an ephemeral port and
send X-Forwarded-For by hand, which is exactly what `tailscale serve` does
when it forwards the tailnet's HTTPS to loopback.
"""
import http.client
import json
import os
import re
import stat
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import serve
from finance import db, link
from finance.errors import NotFound, Refused


def _write_registry(data):
    path = link.devices_path()
    path.write_text(json.dumps(data), encoding="utf-8")
    path.chmod(0o600)


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {"KOPIFUND_DEVICES": f"{self.tmp.name}/devices.json"})
        env.start()
        self.addCleanup(env.stop)

    def test_a_pair_token_redeems_once_for_a_device_bearer(self):
        token, expires = link.mint_pair_token()
        self.assertTrue(token.startswith("kfp_"))
        self.assertLess(expires - time.time(), link.PAIR_LIFE + 1)
        bearer, device = link.redeem(token, "Test iPhone")
        self.assertTrue(bearer.startswith("kfd_"))
        self.assertEqual(device["name"], "Test iPhone")
        self.assertEqual(link.device_for(bearer)["id"], device["id"])
        (listed,) = link.list_devices()
        self.assertEqual(listed, {"id": device["id"], "name": "Test iPhone",
                                  "paired_at": device["paired_at"], "revoked": False})
        self.assertNotIn("token_hash", listed)
        with self.assertRaises(Refused):
            link.redeem(token)

    def test_an_unknown_or_stale_token_is_refused(self):
        with self.assertRaises(Refused):
            link.redeem("kfp_nope")
        with self.assertRaises(Refused):
            link.redeem("")
        _write_registry({"pair_tokens": [{"hash": "x", "expires": time.time() - 1, "used": False}],
                         "devices": []})
        link.mint_pair_token()   # the stale one is swept as the new one is minted
        self.assertEqual(len(link._read()["pair_tokens"]), 1)
        with self.assertRaises(Refused):
            link.redeem("kfp_nope")

    def test_a_raced_token_spends_exactly_once(self):
        token, _ = link.mint_pair_token()
        winners, errors = [], []

        def go():
            try:
                winners.append(link.redeem(token))
            except Refused:
                errors.append(1)
        threads = [threading.Thread(target=go) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(winners), 1)
        self.assertEqual(len(errors), 3)

    def test_revoking_flags_the_row_and_stops_the_bearer(self):
        token, _ = link.mint_pair_token()
        bearer, device = link.redeem(token)
        self.assertEqual(device["name"], "iPhone")   # no name sent
        link.revoke(device["id"])
        self.assertIsNone(link.device_for(bearer))
        self.assertTrue(link.list_devices()[0]["revoked"])
        with self.assertRaises(NotFound):
            link.revoke("dev_nope")

    def test_the_registry_is_mode_600(self):
        link.mint_pair_token()
        self.assertEqual(stat.S_IMODE(link.devices_path().stat().st_mode), 0o600)

    def test_pair_payload_is_the_deep_link(self):
        payload = link.pair_payload("https://mac.tail-abc.ts.net", "kfp_123")
        self.assertEqual(payload, "kopifund://pair?url=https%3A%2F%2Fmac.tail-abc.ts.net&token=kfp_123")
        args = parse_qs(urlsplit(payload).query)
        self.assertEqual(args["url"][0], "https://mac.tail-abc.ts.net")

    def test_tailnet_url_reads_tailscale_status(self):
        out = mock.Mock(stdout=json.dumps({"Self": {"DNSName": "mac.tail-abc.ts.net."}}))
        with mock.patch("subprocess.run", return_value=out):
            self.assertEqual(link.tailnet_url(), "https://mac.tail-abc.ts.net")
        with mock.patch("subprocess.run", side_effect=OSError):
            self.assertEqual(link.tailnet_url(), "")
        with mock.patch("subprocess.run", return_value=mock.Mock(stdout="not json")):
            self.assertEqual(link.tailnet_url(), "")


class GateTest(unittest.TestCase):
    """The link path at HTTP level: a request with X-Forwarded-For is one
    tailscale serve forwarded, so it needs a paired device's bearer."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {"KOPIFUND_DEVICES": f"{self.tmp.name}/devices.json"})
        env.start()
        self.addCleanup(env.stop)
        tailnet = mock.patch("finance.link.tailnet_url", return_value="https://mac.tail-abc.ts.net")
        tailnet.start()
        self.addCleanup(tailnet.stop)
        # a record to read, so a bearer read answers whatever order the tests run in
        db.connect().close()
        self.server = serve.make_server(0, serve.DIST)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)

    def call(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        self.addCleanup(conn.close)
        conn.request(method, path, json.dumps(body) if body is not None else None,
                     {"Content-Type": "application/json", **(headers or {})})
        r = conn.getresponse()
        return r, r.read()

    XFF = {"X-Forwarded-For": "100.64.1.2"}

    def pair(self):
        """The loopback side of pairing: mint a token, redeem it over the
        link path, return the bearer."""
        r, raw = self.call("POST", "/api/link/pair-token", {}, {"X-Kopifund": "1"})
        self.assertEqual(r.status, 200)
        payload = json.loads(raw)["payload"]
        token = parse_qs(urlsplit(payload).query)["token"][0]
        r, raw = self.call("POST", "/api/pair/redeem", {"token": token, "name": "Test iPhone"}, self.XFF)
        self.assertEqual(r.status, 200)
        return json.loads(raw)["token"]

    def test_health_stays_open_on_the_link_path(self):
        r, _ = self.call("GET", "/api/health", headers=self.XFF)
        self.assertEqual(r.status, 200)

    def test_a_link_get_needs_a_paired_devices_bearer(self):
        r, _ = self.call("GET", "/api/categories", headers=self.XFF)
        self.assertEqual(r.status, 401)
        self.assertEqual(r.getheader("WWW-Authenticate"), 'Bearer realm="kopifund"')
        r, _ = self.call("GET", "/api/categories", headers={**self.XFF, "Authorization": "Bearer kfd_wrong"})
        self.assertEqual(r.status, 401)

    def test_pair_and_read_end_to_end(self):
        bearer = self.pair()
        r, raw = self.call("GET", "/api/categories", headers={**self.XFF, "Authorization": f"Bearer {bearer}"})
        self.assertEqual(r.status, 200)
        self.assertIn("groups", json.loads(raw))
        # the registry is an admin view: loopback only, bearer or not
        r, _ = self.call("GET", "/api/link/devices", headers={**self.XFF, "Authorization": f"Bearer {bearer}"})
        self.assertEqual(r.status, 403)
        r, raw = self.call("GET", "/api/link/devices")
        self.assertEqual(r.status, 200)
        self.assertEqual([d["name"] for d in json.loads(raw)["devices"]], ["Test iPhone"])

    def test_a_bearer_reads_only_the_link_reads(self):
        """The link path is an allowlist: the phone's four reads answer, and
        every other GET route is refused even to a paired bearer, so a lost
        phone cannot pull exports, uploads, AI threads or the key's tail."""
        bearer = self.pair()
        auth = {**self.XFF, "Authorization": f"Bearer {bearer}"}
        for path in sorted(serve.LINK_READS):
            r, _ = self.call("GET", path, headers=auth)
            self.assertNotIn(r.status, (401, 403), path)
            # the same read needs the bearer
            r, _ = self.call("GET", path, headers=self.XFF)
            self.assertEqual(r.status, 401, path)
        refused = [route for route in serve.GET_ROUTES
                   if route not in serve.LINK_READS and route != "/api/health"]
        self.assertIn("/api/exports/<stamp>/<file>", refused)
        for route in refused:
            path = re.sub(r"<[a-z_]+>", "x", route)
            r, raw = self.call("GET", path, headers=auth)
            self.assertEqual(r.status, 403, path)
            self.assertEqual(json.loads(raw), {"error": "This route is not served on the link"}, path)
        # a real export file name, and a stat result, are refused just the same
        for path in ("/api/exports/2026-08-01T000000/transactions.csv", "/api/results/monthly-cashflow",
                     "/api/ai", "/api/copilot/threads", "/api/server", "/api/run/status"):
            r, _ = self.call("GET", path, headers=auth)
            self.assertEqual(r.status, 403, path)
        # loopback still answers what the link refuses
        r, _ = self.call("GET", "/api/countries")
        self.assertEqual(r.status, 200)

    def test_pages_are_never_served_on_the_link_path(self):
        bearer = self.pair()
        r, _ = self.call("GET", "/", headers={**self.XFF, "Authorization": f"Bearer {bearer}"})
        self.assertEqual(r.status, 403)
        r, _ = self.call("GET", "/", headers=self.XFF)
        self.assertEqual(r.status, 401)

    def test_the_link_path_answers_only_the_pairing_and_bearer_writes(self):
        bearer = self.pair()
        # an ordinary write is refused even with a bearer
        r, _ = self.call("POST", "/api/rules/save", {"rule": {}}, {**self.XFF, "Authorization": f"Bearer {bearer}"})
        self.assertEqual(r.status, 403)
        # the pair-token mint is loopback-only
        r, _ = self.call("POST", "/api/link/pair-token", {}, {**self.XFF, "Authorization": f"Bearer {bearer}"})
        self.assertEqual(r.status, 403)
        # the bearer-guarded phone write asks for the bearer first
        r, _ = self.call("POST", "/api/link/transactions", {"rows": []}, self.XFF)
        self.assertEqual(r.status, 401)
        # the pending admin routes stay loopback even to a paired phone
        r, _ = self.call("POST", "/api/link/pending/dismiss", {"import_id": "x"},
                         {**self.XFF, "Authorization": f"Bearer {bearer}"})
        self.assertEqual(r.status, 403)

    def test_the_pair_token_write_is_loopback_only_in_practice(self):
        # on loopback the usual write guards still hold: no X-Kopifund header, no write
        r, _ = self.call("POST", "/api/pair/redeem", {"token": "kfp_x"})
        self.assertEqual(r.status, 403)
        r, _ = self.call("POST", "/api/link/pair-token")
        self.assertEqual(r.status, 403)

    def test_revoking_over_loopback_stops_the_bearer(self):
        bearer = self.pair()
        r, raw = self.call("GET", "/api/link/devices")
        device_id = json.loads(raw)["devices"][0]["id"]
        r, _ = self.call("POST", "/api/link/devices/revoke", {"id": device_id}, {"X-Kopifund": "1"})
        self.assertEqual(r.status, 200)
        r, _ = self.call("GET", "/api/categories", headers={**self.XFF, "Authorization": f"Bearer {bearer}"})
        self.assertEqual(r.status, 401)

    def test_a_phone_row_posts_over_the_link_and_lists_on_loopback(self):
        bearer = self.pair()
        conn = db.connect()
        self.addCleanup(conn.close)
        self.addCleanup(conn.commit)
        self.addCleanup(conn.execute, "DELETE FROM pending_txn")
        rows = {"rows": [{"import_id": "imp_e2e", "date": "2026-08-03", "amount_minor": -1250,
                          "currency": "SGD", "payee": "Invented Shop"}]}
        r, raw = self.call("POST", "/api/link/transactions", rows,
                           {**self.XFF, "Authorization": f"Bearer {bearer}"})
        self.assertEqual(r.status, 200)
        self.assertEqual(json.loads(raw), {"accepted": 1, "matched": 0})
        # the pending list is the web card's view: loopback only, no bearer
        r, _ = self.call("GET", "/api/link/pending", headers=self.XFF)
        self.assertEqual(r.status, 401)
        r, _ = self.call("GET", "/api/link/pending", headers={**self.XFF, "Authorization": f"Bearer {bearer}"})
        self.assertEqual(r.status, 403)
        r, raw = self.call("GET", "/api/link/pending")
        self.assertEqual(r.status, 200)
        (row,) = json.loads(raw)["rows"]
        self.assertEqual((row["import_id"], row["device"], row["state"], row["payee"]),
                         ("imp_e2e", "Test iPhone", "waiting", "Invented Shop"))
        # dismiss and restore on loopback, armed by X-Kopifund like any write
        r, _ = self.call("POST", "/api/link/pending/dismiss", {"import_id": "imp_e2e"})
        self.assertEqual(r.status, 403)
        r, raw = self.call("POST", "/api/link/pending/dismiss", {"import_id": "imp_e2e"}, {"X-Kopifund": "1"})
        self.assertEqual(json.loads(raw)["rows"][0]["state"], "dismissed")
        r, raw = self.call("POST", "/api/link/pending/restore", {"import_id": "imp_e2e"}, {"X-Kopifund": "1"})
        self.assertEqual(json.loads(raw)["rows"][0]["state"], "waiting")

    def test_a_spent_or_stale_token_is_a_422(self):
        r, raw = self.call("POST", "/api/pair/redeem", {"token": "kfp_wrong"}, self.XFF)
        self.assertEqual(r.status, 422)

    def test_a_non_string_token_is_a_400_not_a_dropped_connection(self):
        r, _ = self.call("POST", "/api/pair/redeem", {"token": 123}, self.XFF)
        self.assertEqual(r.status, 400)
        r, _ = self.call("POST", "/api/link/devices/revoke", {"id": 123}, {"X-Kopifund": "1"})
        self.assertEqual(r.status, 400)


if __name__ == "__main__":
    unittest.main()
