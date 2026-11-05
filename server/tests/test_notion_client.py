"""The real NotionClient's pacing, pool and sharded reads, with the transport
stubbed out: no network, no token."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import threading
import time
import unittest
import unittest.mock


from finance import notion


class StubClient(notion.NotionClient):
    """call() is replaced by a recorder; everything above it is the real code."""

    def __init__(self, pace=0.0, workers=4, fail_on=None, latency=0.0):
        super().__init__("token", "v", pace=pace, workers=workers)
        self.requests = []
        self.times = []
        self.fail_on = fail_on
        self.latency = latency
        self._rec = threading.Lock()

    def call(self, method, path, body=None):
        self._slot()
        with self._rec:
            self.times.append(time.time())
            self.requests.append((method, path, body))
        if self.latency:
            time.sleep(self.latency)
        if self.fail_on and self.fail_on in path:
            raise notion.NotionError(f"boom on {path}")
        if path.endswith("/query"):
            f = (body or {}).get("filter") or {}
            key = str(f.get("date") or f.get("and"))
            return {"results": [{"id": f"page-{key}"}], "has_more": False}
        return {"ok": True, "path": path}


class RetryTest(unittest.TestCase):

    def test_a_socket_timeout_is_retried_like_a_network_error(self):
        import urllib.request
        c = notion.NotionClient("token", "v", pace=0.0, tries=3)
        calls = []

        def flaky(req, timeout=None):
            calls.append(1)
            if len(calls) < 3:
                raise TimeoutError("The read operation timed out")
            import io, json
            class R(io.BytesIO):
                def __enter__(self): return self
                def __exit__(self, *a): return False
            return R(json.dumps({"ok": True}).encode())

        orig = urllib.request.urlopen
        urllib.request.urlopen = flaky
        try:
            with unittest.mock.patch("time.sleep"):
                self.assertEqual(c.call("GET", "/x"), {"ok": True})
        finally:
            urllib.request.urlopen = orig
        self.assertEqual(len(calls), 3)


class CreateIsNotResentTest(unittest.TestCase):
    """POST /pages is not idempotent: a create whose answer was lost may have
    made the page, so sending it again could make a second one with the same
    key (audit, notion.call). Reads and patches keep their retries."""

    def run_call(self, method, path, failures):
        import io
        import json
        import urllib.request
        c = notion.NotionClient("token", "v", pace=0.0, tries=3)
        calls = []

        def flaky(req, timeout=None):
            calls.append(1)
            if len(calls) <= len(failures):
                raise failures[len(calls) - 1]

            class R(io.BytesIO):
                def __enter__(self): return self
                def __exit__(self, *a): return False
            return R(json.dumps({"id": "p1"}).encode())

        with unittest.mock.patch.object(urllib.request, "urlopen", flaky), \
                unittest.mock.patch("time.sleep"), unittest.mock.patch("sys.stderr"):
            try:
                return c.call(method, path, {}), len(calls)
            except notion.NotionError as e:
                return e, len(calls)

    @staticmethod
    def http(code):
        import email.message
        import io
        import urllib.error
        headers = email.message.Message()
        return urllib.error.HTTPError("https://api.notion.com/v1/pages", code, "x", headers, io.BytesIO(b"{}"))

    def test_a_create_that_timed_out_is_not_sent_again(self):
        got, calls = self.run_call("POST", "/pages", [TimeoutError("The read operation timed out")])
        self.assertIsInstance(got, notion.NotionError)
        self.assertIn("may have been created", str(got))
        self.assertEqual(calls, 1)

    def test_a_create_that_got_a_gateway_error_is_not_sent_again(self):
        for code in (500, 502, 503, 504):
            with self.subTest(code):
                got, calls = self.run_call("POST", "/pages", [self.http(code)])
                self.assertIsInstance(got, notion.NotionError)
                self.assertEqual(calls, 1)

    def test_a_refused_create_is_not_reported_as_maybe_made(self):
        # a 400 means Notion wrote nothing: the plain error, one call
        got, calls = self.run_call("POST", "/pages", [self.http(400)])
        self.assertIsInstance(got, notion.NotionError)
        self.assertNotIn("may have been created", str(got))
        self.assertEqual(calls, 1)

    def test_a_create_is_retried_where_notion_never_saw_it(self):
        import socket
        import urllib.error
        for failure in (self.http(429), urllib.error.URLError(ConnectionRefusedError()),
                        urllib.error.URLError(socket.gaierror("no name"))):
            with self.subTest(failure):
                got, calls = self.run_call("POST", "/pages", [failure])
                self.assertEqual((got, calls), ({"id": "p1"}, 2))

    def test_reads_and_patches_still_retry(self):
        for method, path in (("POST", "/data_sources/ds/query"), ("PATCH", "/pages/p1"), ("GET", "/pages/p1")):
            with self.subTest(path):
                got, calls = self.run_call(method, path, [TimeoutError("timed out"), self.http(502)])
                self.assertEqual((got, calls), ({"id": "p1"}, 3))


class PacingTest(unittest.TestCase):

    def test_threads_share_one_rate(self):
        c = StubClient(pace=0.05, workers=4)
        c.map(lambda i: c.call("GET", f"/x/{i}"), range(8))
        # Eight calls through one pace of 0.05 s take at least seven slots.
        # Individual gaps jitter with thread scheduling, so the check is on the
        # span, not on every neighbouring pair.
        span = max(c.times) - min(c.times)
        self.assertGreaterEqual(span, 7 * 0.05 * 0.95, sorted(c.times))

    def test_pool_removes_latency_not_pace(self):
        c = StubClient(pace=0.0, workers=4, latency=0.05)
        t = time.time()
        c.map(lambda i: c.call("GET", f"/x/{i}"), range(8))
        self.assertLess(time.time() - t, 0.05 * 8)

    def test_writes_count_under_threads(self):
        c = StubClient(workers=4)
        c.map(lambda i: c.patch(f"p{i}", {}), range(20))
        self.assertEqual(c.writes, 20)


class MapTest(unittest.TestCase):

    def test_results_keep_item_order(self):
        c = StubClient(workers=4, latency=0.001)
        self.assertEqual(c.map(lambda i: i * i, range(10)), [i * i for i in range(10)])

    def test_progress_counts_completions(self):
        seen = []
        c = StubClient(workers=3)
        c.map(lambda i: i, range(5), progress=lambda d, t: seen.append((d, t)))
        self.assertEqual(seen, [(i, 5) for i in range(1, 6)])

    def test_first_error_stops_the_run(self):
        c = StubClient(workers=2, fail_on="/x/3", latency=0.01)
        with self.assertRaises(notion.NotionError):
            c.map(lambda i: c.call("GET", f"/x/{i}"), range(40))
        self.assertLess(len(c.requests), 40)

    def test_one_worker_is_plain_sequential(self):
        c = StubClient(workers=1)
        self.assertEqual(c.map(lambda i: i + 1, [1, 2, 3]), [2, 3, 4])
        self.assertEqual(c.map(lambda i: i, []), [])


class ShardTest(unittest.TestCase):

    def test_shards_cover_the_whole_line(self):
        c = StubClient(workers=4)
        pages = c.query_sharded("ds", "Txn date", ["2025-01-01", "2025-04-01"])
        filters = sorted((b["filter"] for _, _, b in c.requests), key=str)
        self.assertEqual(filters, sorted([
            {"property": "Txn date", "date": {"before": "2025-01-01"}},
            {"and": [{"property": "Txn date", "date": {"on_or_after": "2025-01-01"}},
                     {"property": "Txn date", "date": {"before": "2025-04-01"}}]},
            {"property": "Txn date", "date": {"on_or_after": "2025-04-01"}},
            {"property": "Txn date", "date": {"is_empty": True}}], key=str))
        self.assertEqual(len(pages), 4)

    def test_no_edges_is_one_query(self):
        c = StubClient()
        c.query_sharded("ds", "Txn date", [])
        self.assertEqual(len(c.requests), 1)
        self.assertNotIn("filter", c.requests[0][2])

    def test_duplicate_pages_across_shards_are_dropped(self):
        c = StubClient()
        c.call = lambda m, p, b=None: {"results": [{"id": "same"}], "has_more": False}
        self.assertEqual(len(c.query_sharded("ds", "Txn date", ["2025-01-01"])), 1)

    def test_quarter_edges(self):
        self.assertEqual(notion.quarter_edges("2025-02-15", "2025-11-30"),
                         ["2025-01-01", "2025-04-01", "2025-07-01", "2025-10-01"])
        self.assertEqual(notion.quarter_edges("2026-01-01", "2025-12-31"), [])


if __name__ == "__main__":
    unittest.main()
