"""AI payee suggestions (G12): what is sent, what comes back, and the batch save."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import csv
import http.server
import threading
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from finance import ai, rules_app
from test_app_writes import DataFolder, record

CATS = ["Eating out", "Shopping", "Transport", "Person to person"]
GROUPS = [
    {"text": "TOAST BOX - RAFFLES PL", "field": "merchant", "rows": 4, "amount": 2280},
    {"text": "VM-B302 PL", "field": "merchant", "rows": 10, "amount": 1450},
    {"text": "JANE TAN", "field": "counterparty", "rows": 1, "amount": 5000},
    {"text": "ref 123", "field": "memo", "rows": 1, "amount": 100},
]


def cfg(root, **over):
    base = {"provider": "anthropic", "model": "claude-haiku-5-5", "key_env": "KOPIFUND_TEST_AI_KEY"}
    return SimpleNamespace(root=Path(root), ai={**base, **over})


class Fake:
    """Stands in for ai._post: records each request, answers like the provider."""

    def __init__(self, suggestions, wire="anthropic"):
        self.calls, self.suggestions, self.wire = [], suggestions, wire

    def __call__(self, url, headers, body):
        self.calls.append((url, headers, body))
        text = json.dumps({"suggestions": self.suggestions})
        if self.wire == "anthropic":
            return {"stop_reason": "end_turn", "content": [{"type": "thinking", "thinking": ""},
                                                           {"type": "text", "text": text}]}
        return {"choices": [{"message": {"content": text}}]}


ANSWER = [
    {"id": 1, "payee": "Toast Box", "category": "Eating out", "kind": "Merchant", "confident": True, "note": ""},
    {"id": 2, "payee": "Vending machine", "category": "", "kind": "Merchant", "confident": False,
     "note": "a campus vending machine?"},
]


class SuggestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {"KOPIFUND_TEST_AI_KEY": "test-key",
                                           "KOPIFUND_CACHE": str(Path(self.tmp.name) / "cache")})
        env.start()
        self.addCleanup(env.stop)
        # the keys a developer's own shell exports must not decide a test
        for name in [n for n in os.environ if n.endswith("_API_KEY") or n == "ANTHROPIC_WORKSPACE_ID"]:
            del os.environ[name]

    def run_suggest(self, fake, **over):
        with mock.patch.object(ai, "_post", fake):
            return ai.suggest(cfg(self.tmp.name, **over), GROUPS, CATS)

    def test_off_without_an_ai_section(self):
        out = ai.suggest(SimpleNamespace(root=Path(self.tmp.name), ai=None), GROUPS, CATS)
        self.assertEqual(out, {"enabled": False, "suggestions": []})

    def test_sends_merchant_text_and_rough_amounts_only(self):
        fake = Fake(ANSWER)
        self.run_suggest(fake)
        url, headers, body = fake.calls[0]
        self.assertEqual(url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(headers["x-api-key"], "test-key")
        sent = body["messages"][0]["content"]
        self.assertIn("TOAST BOX - RAFFLES PL", sent)
        self.assertIn("about $6", sent)            # 2280 cents over 4 rows
        self.assertNotIn("JANE TAN", sent)         # people never leave the machine
        self.assertNotIn("ref 123", sent)
        self.assertNotIn("2280", sent)

    def test_amounts_stay_home_when_switched_off(self):
        fake = Fake(ANSWER)
        self.run_suggest(fake, send_amounts=False)
        self.assertNotIn("about $", fake.calls[0][2]["messages"][0]["content"])

    def test_answers_are_checked_and_keyed_by_text(self):
        out = self.run_suggest(Fake(ANSWER))
        by_text = {s["text"]: s for s in out["suggestions"]}
        self.assertEqual(by_text["TOAST BOX - RAFFLES PL"]["category"], "Eating out")
        self.assertTrue(by_text["TOAST BOX - RAFFLES PL"]["confident"])
        self.assertFalse(by_text["VM-B302 PL"]["confident"])
        self.assertEqual(out["model"], "claude-haiku-5-5")

    def test_a_category_not_in_the_list_is_dropped(self):
        answer = [{**ANSWER[0], "category": "Food and drink"}]
        s = self.run_suggest(Fake(answer))["suggestions"][0]
        self.assertEqual((s["category"], s["confident"]), ("", False))

    def test_a_second_check_asks_nothing(self):
        fake = Fake(ANSWER)
        self.run_suggest(fake)
        again = self.run_suggest(fake)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(len(again["suggestions"]), 2)

    def test_openai_style_providers(self):
        fake = Fake(ANSWER, wire="openai")
        out = self.run_suggest(fake, provider="deepseek", model="deepseek-chat")
        url, headers, body = fake.calls[0]
        self.assertEqual(url, "https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(headers["authorization"], "Bearer test-key")
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(len(out["suggestions"]), 2)

    def test_a_custom_provider_needs_a_base_url(self):
        with self.assertRaises(ai.AIError):
            self.run_suggest(Fake(ANSWER, wire="openai"), provider="somewhere")
        out = self.run_suggest(Fake(ANSWER, wire="openai"), provider="somewhere",
                               base_url="https://llm.example.test/v1/")
        self.assertEqual(len(out["suggestions"]), 2)

    def test_local_ollama_needs_no_key(self):
        os.environ.pop("KOPIFUND_TEST_AI_KEY")
        fake = Fake(ANSWER, wire="openai")
        self.run_suggest(fake, provider="ollama", model="qwen3")
        self.assertNotIn("authorization", fake.calls[0][1])

    def test_a_missing_key_says_where_to_put_it(self):
        os.environ.pop("KOPIFUND_TEST_AI_KEY")
        with self.assertRaisesRegex(ai.AIError, "key_command"):
            self.run_suggest(Fake(ANSWER))

    def test_a_refusal_or_bad_json_is_an_error_not_a_guess(self):
        refuse = lambda *a: {"stop_reason": "refusal", "content": []}  # noqa: E731
        with mock.patch.object(ai, "_post", refuse), self.assertRaises(ai.AIError):
            ai.suggest(cfg(self.tmp.name), GROUPS, CATS)
        prose = lambda *a: {"content": [{"type": "text", "text": "Sure! Toast Box is a cafe."}]}  # noqa: E731
        with mock.patch.object(ai, "_post", prose), self.assertRaises(ai.AIError):
            ai.suggest(cfg(self.tmp.name), GROUPS, CATS)

    def test_only_a_json_true_is_confident(self):
        answer = [{**ANSWER[0], "confident": "false"}]
        self.assertFalse(self.run_suggest(Fake(answer))["suggestions"][0]["confident"])

    def test_answers_saved_by_another_check_meanwhile_are_kept(self):
        fake = Fake(ANSWER)

        def meanwhile(*a):
            # another request finishes and saves while this one waits on the provider
            ai._merge_cache({"anthropic|claude-haiku-5-5|OTHER SHOP": ai.clean(ANSWER[0], CATS)})
            return fake(*a)
        self.run_suggest(meanwhile)
        cache = json.loads((Path(self.tmp.name) / "cache" / "ai-suggestions.json").read_text())
        self.assertIn("anthropic|claude-haiku-5-5|OTHER SHOP", cache)
        self.assertIn("anthropic|claude-haiku-5-5|TOAST BOX - RAFFLES PL", cache)
        self.assertEqual(oct((Path(self.tmp.name) / "cache" / "ai-suggestions.json").stat().st_mode & 0o777), "0o600")

    def test_auto_picks_the_provider_whose_key_is_exported(self):
        fake = Fake(ANSWER)
        os.environ["ANTHROPIC_API_KEY"] = "from-shell"
        out = self.run_suggest(fake, provider="auto", model=None, key_env=None)
        self.assertEqual((out["provider"], out["model"]), ("anthropic", "claude-haiku-5-5"))
        self.assertEqual(fake.calls[0][1]["x-api-key"], "from-shell")

    def test_an_empty_section_is_auto(self):
        os.environ["DEEPSEEK_API_KEY"] = "ds"
        fake = Fake(ANSWER, wire="openai")
        with mock.patch.object(ai, "_post", fake):
            out = ai.suggest(SimpleNamespace(root=Path(self.tmp.name), ai={}), GROUPS, CATS)
        self.assertEqual((out["provider"], out["model"]), ("deepseek", "deepseek-chat"))
        self.assertEqual(fake.calls[0][1]["authorization"], "Bearer ds")

    def test_a_bare_ai_section_in_config_toml_turns_it_on(self):
        from finance import model
        root = Path(self.tmp.name)
        (root / "config.toml").write_text("[ai]\n")
        self.assertEqual(model.load_config(root / "config.toml", root).ai, {})
        (root / "config.toml").write_text("[notion]\n")
        self.assertIsNone(model.load_config(root / "config.toml", root).ai)

    def test_auto_keeps_a_model_the_user_named(self):
        os.environ["DEEPSEEK_API_KEY"] = "ds"
        fake = Fake(ANSWER, wire="openai")
        out = self.run_suggest(fake, provider="auto", model="deepseek-reasoner", key_env=None)
        self.assertEqual((out["provider"], out["model"]), ("deepseek", "deepseek-reasoner"))

    def test_an_exported_key_beats_an_old_one_in_the_file(self):
        keys = Path(self.tmp.name) / "api_keys"
        keys.write_text("export XAI_API_KEY=old\n")
        os.environ["GROK_API_KEY"] = "new"
        fake = Fake(ANSWER, wire="openai")
        self.run_suggest(fake, provider="xai", model="grok", key_env=None, key_file=str(keys))
        self.assertEqual(fake.calls[0][1]["authorization"], "Bearer new")

    def test_keys_are_read_from_the_file_a_shell_profile_sources(self):
        keys = Path(self.tmp.name) / "api_keys"
        keys.write_text('export OPENAI_API_KEY="sk-o"\nexport ANTHROPIC_API_KEY=sk-a\n')
        fake = Fake(ANSWER)
        out = self.run_suggest(fake, provider="auto", model=None, key_env=None, key_file=str(keys))
        self.assertEqual(out["provider"], "anthropic")
        self.assertEqual(fake.calls[0][1]["x-api-key"], "sk-a")

    def test_a_named_provider_finds_its_usual_variable(self):
        os.environ["KIMI_API_KEY"] = "kimi"
        fake = Fake(ANSWER, wire="openai")
        self.run_suggest(fake, provider="moonshot", model="kimi-k2", key_env=None)
        self.assertEqual(fake.calls[0][1]["authorization"], "Bearer kimi")

    def test_an_unlisted_provider_uses_its_own_name(self):
        os.environ["MINIMAX_API_KEY"] = "mm"
        fake = Fake(ANSWER, wire="openai")
        self.run_suggest(fake, provider="minimax", model="m1", key_env=None, base_url="https://api.minimax.test/v1")
        self.assertEqual(fake.calls[0][1]["authorization"], "Bearer mm")

    def test_auto_says_what_it_found_when_it_cannot_pick(self):
        os.environ["OPENAI_API_KEY"] = "sk-o"
        with self.assertRaisesRegex(ai.AIError, "found keys for openai"):
            self.run_suggest(Fake(ANSWER), provider="auto", model=None, key_env=None)
        del os.environ["OPENAI_API_KEY"]
        with self.assertRaisesRegex(ai.AIError, "found no API key"):
            self.run_suggest(Fake(ANSWER), provider="auto", model=None, key_env=None)

    def test_a_provider_with_no_default_model_asks_for_one(self):
        os.environ["ZAI_API_KEY"] = "z"
        with self.assertRaisesRegex(ai.AIError, "need a model for Z.ai"):
            self.run_suggest(Fake(ANSWER, wire="openai"), provider="zai", model=None, key_env=None)

    def test_a_workspace_is_named_when_the_key_needs_one(self):
        fake = Fake(ANSWER)
        self.run_suggest(fake)
        self.assertNotIn("anthropic-workspace-id", fake.calls[0][1])
        keys = Path(self.tmp.name) / "api_keys"
        keys.write_text("export ANTHROPIC_WORKSPACE_ID=wrkspc_from_file\n")
        fake = Fake(ANSWER)
        self.run_suggest(fake, key_file=str(keys), model="claude-haiku-5-5-b")   # a new cache key
        self.assertEqual(fake.calls[0][1]["anthropic-workspace-id"], "wrkspc_from_file")
        fake = Fake(ANSWER)
        self.run_suggest(fake, workspace_id="wrkspc_cfg", model="claude-haiku-5-5-c")
        self.assertEqual(fake.calls[0][1]["anthropic-workspace-id"], "wrkspc_cfg")

    def test_a_long_error_body_still_gives_its_message(self):
        import io
        import urllib.error
        body = json.dumps({"type": "error", "error": {"type": "invalid_request_error",
                                                      "message": "needs a workspace " + "x" * 400}})
        err = urllib.error.HTTPError("u", 400, "Bad Request", {}, io.BytesIO(body.encode()))
        with mock.patch.object(ai._OPENER, "open", side_effect=err), \
                self.assertRaisesRegex(ai.AIError, r"^The AI provider said 400: needs a workspace x+$"):
            ai._post("https://example.test", {}, {})

    def test_rough_amounts(self):
        self.assertEqual([ai.rough(c) for c in (370, 2280, 13100, 245000)],
                         ["about $4", "about $25", "about $150", "about $2,500"])


class BatchTest(DataFolder):
    def rule(self, pattern, category="Eating out"):
        return {"match_on": "merchant", "pattern": pattern, "payee": pattern.title().rstrip("*"),
                "kind": "Merchant", "category": category, "notes": "[ai] suggested by a test model"}

    def test_all_rules_go_in_with_one_load(self):
        out = rules_app.add_many(record(), [self.rule("TOAST BOX*"), self.rule("KOPITIAM*")])
        self.assertEqual(out["added"], 2)
        with (self.root / "rules" / "merchants.csv").open(newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual([r["pattern"] for r in rows], ["TOAST BOX*", "KOPITIAM*"])
        self.assertTrue(rows[0]["notes"].startswith("[ai]"))

    def test_one_bad_rule_keeps_every_rule_out(self):
        rules_app.add_many(record(), [self.rule("TOAST BOX*")])
        before = (self.root / "rules" / "merchants.csv").read_bytes()
        with self.assertRaises(rules_app.RuleRefused):
            rules_app.add_many(record(), [self.rule("KOPITIAM*"), self.rule("SHOP*", "No such category")])
        self.assertEqual((self.root / "rules" / "merchants.csv").read_bytes(), before)

    def test_a_file_that_cannot_be_written_puts_every_rule_back(self):
        rules_app.add_many(record(), [self.rule("TOAST BOX*")])
        before = (self.root / "rules" / "merchants.csv").read_bytes()
        person = {"match_on": "counterparty", "pattern": "J TAN*", "payee": "J Tan", "kind": "Person",
                  "category": "Person to person"}
        real = rules_app._write_atomic

        def write(path, data):
            if path.name == "people.csv":
                raise PermissionError("read-only")
            real(path, data)
        with mock.patch.object(rules_app, "_write_atomic", write), \
                self.assertRaisesRegex(rules_app.RuleRefused, "Could not write"):
            rules_app.add_many(record(), [self.rule("KOPITIAM*"), person])
        self.assertEqual((self.root / "rules" / "merchants.csv").read_bytes(), before)
        self.assertFalse((self.root / "rules" / "people.csv").exists())

    def test_an_empty_batch_is_refused(self):
        with self.assertRaises(rules_app.RuleRefused):
            rules_app.add_many(record(), [])


class RouteTest(DataFolder):
    """The two POST routes the upload page calls."""

    def write(self, path, body, ai_section=None):
        import serve
        conf = SimpleNamespace(root=self.root, ai=ai_section)
        # G13: a rule write relinks the record and reruns the stats in the
        # background; this folder has no statements to rerun them over
        with mock.patch("finance.api.config", lambda: conf), mock.patch("finance.stats_rebuild.refresh_stats"):
            return serve.write(path, {}, json.dumps(body).encode())

    def test_without_an_ai_section_the_table_still_gets_guesses(self):
        out = self.write("/api/rules/suggest", {"groups": GROUPS})
        self.assertEqual((out["enabled"], out["suggestions"]), (False, []))
        self.assertEqual(out["drafts"]["TOAST BOX - RAFFLES PL"]["pattern"], "TOAST BOX*")
        self.assertEqual(out["drafts"]["JANE TAN"]["kind"], "Person")
        self.assertIn("Eating out", out["categories"])

    def test_a_provider_failure_comes_back_in_words(self):
        def down(*a):
            raise ai.AIError("could not reach the AI provider: offline")
        with mock.patch.object(ai, "_post", down), \
                mock.patch.dict(os.environ, {"KOPIFUND_TEST_AI_KEY": "k"}):
            out = self.write("/api/rules/suggest", {"groups": GROUPS}, cfg(self.root).ai)
        self.assertIn("offline", out["error"])
        self.assertEqual(len(out["drafts"]), 4)

    def test_batch_adds_the_rules(self):
        rule = {"match_on": "merchant", "pattern": "TOAST BOX*", "payee": "Toast Box",
                "kind": "Merchant", "category": "Eating out", "notes": "[ai]"}
        self.assertEqual(self.write("/api/rules/batch", {"rules": [rule]})["added"], 1)


if __name__ == "__main__":
    unittest.main()


class Recorder(http.server.BaseHTTPRequestHandler):
    """A local server: /moved answers 302 to `target`, /deny answers 401 with
    an error that echoes the key it was sent, anything else records the
    headers it got and answers {}."""
    target, seen = "", []

    def log_message(self, *args):
        pass

    def answer(self):
        if self.path == "/moved":
            self.send_response(302)
            self.send_header("Location", self.target)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path.startswith("/deny"):
            body = json.dumps({"error": {"message": f"Incorrect API key provided: {self.headers.get('x-api-key')}, "
                                                    f"{self.headers.get('authorization')}"}}).encode()
            self.send_response(401)
        else:
            type(self).seen.append({k.lower(): v for k, v in self.headers.items()})
            body = b"{}"
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.answer()

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.answer()


class TransportTest(unittest.TestCase):
    """Audit: the key goes to the provider's own address and nowhere else, and
    never comes back in an error's words."""
    KEY = "sk-secret-123456789"

    def server(self):
        Recorder.seen = []
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    def test_a_redirect_is_refused_so_the_key_never_reaches_another_host(self):
        here = self.server()
        Recorder.target = f"http://localhost:{here.rsplit(':', 1)[1]}/elsewhere"
        headers = {"x-api-key": self.KEY, "authorization": f"Bearer {self.KEY}"}
        for body in (None, {"q": 1}):
            with self.subTest(post=body is not None):
                with self.assertRaisesRegex(ai.AIError, "redirect"):
                    ai._request(f"{here}/moved", headers, body)
        self.assertEqual(Recorder.seen, [])
        ai._request(f"{here}/fine", headers)        # the same headers to the address asked for still go
        self.assertEqual(Recorder.seen[0].get("x-api-key"), self.KEY)

    def test_an_error_that_echoes_the_key_comes_back_without_it(self):
        here = self.server()
        for headers in ({"x-api-key": self.KEY, "anthropic-version": "2023-06-01"},
                        {"authorization": f"Bearer {self.KEY}"}):
            with self.subTest(headers=list(headers)):
                with self.assertRaises(ai.AIError) as caught:
                    ai._request(f"{here}/deny", headers, {"q": 1})
                self.assertIn("401", str(caught.exception))
                self.assertNotIn(self.KEY, str(caught.exception))
                self.assertNotIn(self.KEY[-12:], str(caught.exception))

    def test_payee_suggestions_show_a_provider_error_without_the_key(self):
        # the payee suggestions pass the provider's words on as they come (api_writes.post_rules_suggest)
        here = self.server()
        for fmt in ("anthropic", "openai"):
            with self.subTest(format=fmt):
                settings = {"provider": fmt, "format": fmt, "base_url": f"{here}/deny", "model": "m"}
                with self.assertRaises(ai.AIError) as caught:
                    ai.ask(settings, self.KEY, "hello")
                self.assertIn("401", str(caught.exception))
                self.assertNotIn(self.KEY, str(caught.exception))
