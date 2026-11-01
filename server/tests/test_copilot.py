"""G22: Ask Kopifund: the tool loop in both wire formats, every tool on a
small invented record, the read-only `query`, the privacy filter, the
streamed answer through serve.py's handler, and the saved threads.

No test calls the network: the provider is a fake transport that replays
scripted replies. No test touches the Keychain or the real data folder."""
import isolate  # noqa: F401  first: no test writes the real record, cache, Keychain or .env
import http.client
import json
import os
import sqlite3
import stat
import tempfile
import threading
import unittest
import warnings
from contextlib import closing
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import serve
from finance import ai, api, copilot, db, keys, setup
from finance import copilot_tools as tools
from finance import types as wire
from test_db import block, parsed, row
from test_types import problems

ACCOUNT = "111-11111-1"
PERSON, MEMO = "JANE TAN", "DINNER SHARE MARCH"


def paid(key, amount, day, payee, category, merchant=None, **kw):
    return row(key, amount, date=day, payee=payee, category=category, merchant=merchant or payee.upper(), **kw)


ROWS = [
    paid("g1", "-40.00", "2026-08-03", "FairPrice", "Groceries"),
    paid("g2", "-60.00", "2026-09-04", "FairPrice", "Groceries"),
    paid("e1", "-12.40", "2026-09-05", "Toast Box", "Eating out"),
    paid("e2", "-8.60", "2026-08-06", "", "", merchant="TOAST BOX 0123 RAFFLES", rule=""),
    paid("t1", "-30.00", "2026-08-11", "", "Eating out", merchant="WARUNG MAKAN", merchant_country="MYS"),
    row("s1", "2500.00", date="2026-08-25", direction="In", merchant="", memo="SALARY ACME", bank_txn_type="FAST",
        payee="", category="", rule=""),
    # a transfer to a person: the counterparty and memo name them, and no rule gives a payee
    row("x1", "-45.00", date="2026-09-10", bank_txn_type="PayNow", merchant="", counterparty=PERSON, memo=MEMO,
        payee="", category="Donations", rule=""),
]


class Record(unittest.TestCase):
    """A throwaway data folder with a small record, an Anthropic provider with
    a saved key, and the owner's consent to send rows."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.env = mock.patch.dict(os.environ, {
            "KOPIFUND_DATA": str(cls.root), "KOPIFUND_DB": str(cls.root / "kopifund.db"),
            "KOPIFUND_CACHE": str(cls.root / "cache"), "KOPIFUND_ENV_FILE": str(cls.root / "keys.env"),
            "KOPIFUND_COPILOT": str(cls.root / "copilot")})
        cls.env.start()
        for name in [n for n in os.environ if n.endswith("_API_KEY")]:
            del os.environ[name]
        setup.create(["posb-everyday"])
        with closing(db.connect()) as conn:
            db.import_user_tables(conn, {
                "accounts": [{"name": f"POSB {ACCOUNT}", "account_no": ACCOUNT, "institution": "DBS",
                              "class": "Asset", "subclass": "Cash", "base_currency": "SGD"}],
                "budgets": [{"category": "Groceries", "month": "2026-09", "planned_minor": 30_000}],
                "trips": [{"name": "Coast", "start": "2026-08-10", "end": "2026-08-14", "countries": ["MYS"],
                           "keys": []}]})
            db.load_parsed(conn, parsed([block(account=f"POSB {ACCOUNT}")], [{**r, "account": f"POSB {ACCOUNT}"}
                                                                              for r in ROWS]),
                           trips_by_key={"t1": "Coast"},
                           payees=[{"name": p, "kind": "Merchant", "country": "SGP"} for p in ("FairPrice", "Toast Box")])
            ai.save_settings(conn, {"provider": "anthropic", "send_rows": True})
        keys.save("anthropic", "sk-test-key-0001")
        cls.path = db.db_path()

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.tmp.cleanup()

    def setUp(self):
        # serve.answer and serve.write called directly leave the record open to their caller
        self.enterContext(warnings.catch_warnings())
        warnings.simplefilter("ignore", ResourceWarning)

    def context(self):
        conn = tools.open_readonly(self.path)
        self.addCleanup(conn.close)
        return tools.Context(conn, self.path, {}, (), None, date(2026, 10, 23))

    def call(self, ctx, tool, **args):
        text, failed = tools.run(ctx, tool, args)
        self.assertFalse(failed, text)
        return json.loads(text)


# ---------------------------------------------------------------- the fake provider

def tool_use(*calls, thinking=True):
    """An Anthropic reply asking for tools, with the blocks that must go back unchanged."""
    content = [{"type": "thinking", "thinking": "", "signature": "sig-abc"}] if thinking else []
    content += [{"type": "tool_use", "id": f"tu_{i}", "name": n, "input": a} for i, (n, a) in enumerate(calls)]
    return {"type": "message", "stop_reason": "tool_use", "content": content}


def answer(text, stop="end_turn"):
    return {"type": "message", "stop_reason": stop, "content": [{"type": "text", "text": text}]}


class Script:
    """Replays replies in order, keeping a deep copy of each request."""

    def __init__(self, *replies, repeat=None):
        self.replies, self.repeat, self.requests = list(replies), repeat, []

    def __call__(self, url, headers, body, **kw):
        self.requests.append((url, dict(headers), json.loads(json.dumps(body))))
        if self.replies:
            return self.replies.pop(0)
        return self.repeat


def settle(data):
    with closing(db.connect()) as conn:
        ai.save_settings(conn, data)


def ask(question, transport, thread=None, page=None):
    """Every event of one question, as the stream sends them."""
    return list(copilot.start(api.config_or_none(), None, {"thread": thread, "question": question, "page": page},
                              transport=transport, today=date(2026, 10, 23)))


# ---------------------------------------------------------------- the loop

class AnthropicLoopTest(Record):
    def test_parallel_calls_go_back_in_one_message_with_the_reply_unchanged(self):
        first = tool_use(("spending", {"from": "2026-08", "to": "2026-09", "by": "category"}),
                         ("trips", {}))
        first["content"].append({"type": "fallback", "from": {"model": "claude-opus-5-5"}, "to": {"model": "x"}})
        script = Script(first, answer("You spent S$151.00 on food."))
        events = ask("What did I spend on food?", script)
        self.assertEqual([e["type"] for e in events], ["thread", "tool", "tool", "text", "link", "done"])
        self.assertEqual(events[1]["tool"], {"name": "spending", "label": "Spending by category, Aug and Sep 2026"})
        url, headers, body = script.requests[1]
        self.assertEqual(url, "https://api.anthropic.com/v1/messages")
        # the assistant's content goes back exactly as it came, thinking and fallback blocks included
        self.assertEqual(body["messages"][-2], {"role": "assistant", "content": first["content"]})
        results = body["messages"][-1]
        self.assertEqual(results["role"], "user")
        self.assertEqual([b["type"] for b in results["content"]], ["tool_result", "tool_result"])
        self.assertEqual([b["tool_use_id"] for b in results["content"]], ["tu_0", "tu_1"])
        # the first request is the prefix of the second: append-only
        self.assertEqual(script.requests[0][2]["messages"], body["messages"][:1])

    def test_the_request_shape(self):
        script = Script(answer("Hello."))
        ask("Hi", script)
        _, headers, body = script.requests[0]
        self.assertEqual(body["model"], "claude-opus-5-5")
        self.assertEqual(body["max_tokens"], 16000)
        self.assertEqual(body["tool_choice"], {"type": "auto"})
        # G22: summarized thinking, so the panel can show each round's; never a budget or thinking off
        self.assertEqual(body["thinking"], {"type": "adaptive", "display": "summarized"})
        self.assertEqual(body["system"][-1]["cache_control"], {"type": "ephemeral"})
        self.assertIn("never tell the owner where to put their money".lower(), body["system"][0]["text"].lower())
        self.assertEqual(body["fallbacks"], "default")
        self.assertEqual(headers["anthropic-beta"], "server-side-fallback-2026-07-01")
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        self.assertEqual(headers["x-api-key"], "sk-test-key-0001")
        self.assertEqual({t["name"] for t in body["tools"]}, set(tools.BY_NAME))
        self.assertIn("Today is 23 Oct 2026.", body["messages"][-1]["content"])

    def test_no_fallback_off_anthropics_own_api_or_for_other_models(self):
        chat = ai.Chat({"provider": "anthropic", "format": "anthropic", "base_url": "https://proxy.test/v1",
                        "copilot_model": "claude-opus-5-5"}, "k", "sys", [], [], "q")
        _, headers, body = chat._anthropic_request()
        self.assertNotIn("fallbacks", body)
        self.assertNotIn("anthropic-beta", headers)
        chat = ai.Chat({"provider": "anthropic", "format": "anthropic", "base_url": "https://api.anthropic.com/v1",
                        "copilot_model": "claude-haiku-5-5"}, "k", "sys", [], [], "q")
        self.assertNotIn("fallbacks", chat._anthropic_request()[2])

    def test_it_stops_after_eight_rounds(self):
        script = Script(repeat=tool_use(("overview", {}), thinking=False))
        events = ask("Loop forever", script)
        self.assertEqual(len(script.requests), copilot.MAX_ROUNDS)
        text = next(e for e in events if e["type"] == "text")["text"]
        self.assertIn("I stopped after 8 rounds", text)
        self.assertEqual(events[-1], {"type": "done"})

    def test_a_refusal_is_an_error_in_the_apps_words(self):
        events = ask("Something it declines", Script({"stop_reason": "refusal", "content": []}))
        self.assertEqual([e["type"] for e in events], ["thread", "error", "done"])
        self.assertIn("declined", events[1]["message"])

    def test_a_length_stop_says_so(self):
        events = ask("Long", Script(answer("Part of it", stop="max_tokens")))
        self.assertIn("stopped at its length limit", next(e for e in events if e["type"] == "text")["text"])

    def test_a_provider_error_is_an_error_line_without_the_key(self):
        def fails(url, headers, body):
            raise ai.AIError("The AI provider said 401: bad key sk-test-key-0001")
        events = ask("Hi", fails)
        self.assertEqual(events[-2]["type"], "error")
        self.assertNotIn("sk-test-key-0001", events[-2]["message"])

    def test_charts_cards_links_and_followups_come_after_the_text(self):
        rows = [{"month": "2026-08", "spend_minor": 7860}, {"month": "2026-09", "spend_minor": 8100}]
        script = Script(
            tool_use(("transactions", {"category": "Groceries"}),
                     ("show_chart", {"title": "Groceries by month", "type": "bar", "x": "month",
                                     "series": ["spend_minor"], "unit": "sgd_minor", "rows": rows}),
                     ("suggest_change", {"kind": "budget", "category": "Groceries", "month": "2026-09",
                                         "planned_minor": 40_000}),
                     ("follow_ups", {"questions": ["And eating out?", "By week?", "Last year?", "A fourth?"]})),
            answer("Groceries came to S$100.00."))
        events = ask("Groceries?", script)
        self.assertEqual([e["type"] for e in events],
                         ["thread", "tool", "tool", "tool", "text", "chart", "link", "card", "followups", "done"])
        for e in events:
            self.assertEqual(problems(e, wire.CopilotEvent), [], e)
        self.assertEqual(len(events[-2]["followups"]), 3)
        card = events[7]["card"]
        self.assertEqual(card["request"], {"path": "/api/budgets", "body": {
            "category_id": card["request"]["body"]["category_id"], "category": "Groceries", "month": "2026-09",
            "planned": 40_000}})
        # G22: a budget card carries no undo: the page reads the amount at Confirm and guards Undo itself
        self.assertIsNone(card["undo"])
        # the thread keeps the answer with all it showed
        thread = copilot.load(events[0]["thread"]["id"])
        self.assertEqual(problems(thread, wire.CopilotThread), [])
        last = thread["messages"][-1]
        self.assertEqual((len(last["charts"]), len(last["cards"]), len(last["links"]), len(last["tools"])), (1, 1, 1, 3))

    def test_an_earlier_turn_goes_back_as_its_text_only(self):
        first = ask("What did I spend?", Script(tool_use(("overview", {})), answer("S$151.00.")))
        script = Script(answer("Still S$151.00."))
        ask("And again?", script, thread=first[0]["thread"]["id"])
        messages = script.requests[0][2]["messages"]
        self.assertEqual([m["role"] for m in messages], ["user", "assistant", "user"])
        self.assertEqual(messages[1]["content"], "S$151.00.")
        self.assertTrue(messages[0]["content"].endswith("What did I spend?"))


class OpenAILoopTest(Record):
    def test_tool_calls_and_role_tool_results(self):
        chat = ai.Chat({"provider": "deepseek", "format": "openai", "base_url": "https://api.deepseek.com/v1",
                        "copilot_model": "deepseek-chat"}, "k", "the instructions",
                       tools.schemas(), [("user", "earlier"), ("assistant", "before")], "now?",
                       transport=Script(
                           {"choices": [{"finish_reason": "tool_calls", "message": {
                               "role": "assistant", "content": None, "tool_calls": [
                                   {"id": "c1", "type": "function", "function": {"name": "trips", "arguments": "{}"}},
                                   {"id": "c2", "type": "function", "function": {"name": "query",
                                                                                 "arguments": "not json"}}]}}]},
                           {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "Done."}}]}))
        turn = chat.step()
        self.assertEqual(turn.stop, "tools")
        self.assertEqual([(c.id, c.name, c.input) for c in turn.calls], [("c1", "trips", {}), ("c2", "query", None)])
        chat.add_results([(c, "{}", False) for c in turn.calls])
        self.assertEqual(chat.step(), ai.Turn("Done.", [], "end"))
        url, headers, body = chat.transport.requests[1]
        self.assertEqual(url, "https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(headers, {"authorization": "Bearer k"})
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user", "assistant", "user", "assistant",
                                                                 "tool", "tool"])
        self.assertEqual(body["tool_choice"], "auto")
        self.assertEqual(body["tools"][0]["type"], "function")

    def test_a_content_filter_is_a_refusal(self):
        chat = ai.Chat({"provider": "openai", "format": "openai", "base_url": "https://x.test/v1",
                        "copilot_model": "m"}, "k", "s", [], [], "q",
                       transport=Script({"choices": [{"finish_reason": "content_filter",
                                                      "message": {"role": "assistant", "content": ""}}]}))
        self.assertEqual(chat.step().stop, "refusal")


# ---------------------------------------------------------------- G22: the steps of an answer

def round_of(*blocks, stop="tool_use"):
    """An Anthropic reply of the given blocks: ("thinking", text), ("text", text) or ("tool", name, input)."""
    content = []
    for i, b in enumerate(blocks):
        if b[0] == "thinking":
            content.append({"type": "thinking", "thinking": b[1], "signature": f"sig-{i}"})
        elif b[0] == "text":
            content.append({"type": "text", "text": b[1]})
        else:
            content.append({"type": "tool_use", "id": f"tu_{i}", "name": b[1], "input": b[2]})
    return {"type": "message", "stop_reason": stop, "content": content}


class StepsTest(Record):
    def test_anthropic_rounds_become_thinking_text_and_tools_steps_in_order(self):
        first = round_of(("thinking", "Start from the overview and the runway stat."),
                         ("text", "Your runway is about 14 months."),
                         ("tool", "overview", {}), ("tool", "stat_result", {"id": "savings-runway"}))
        second = round_of(("thinking", "The stat counts CPF, so the line above is off."),
                          ("text", "I need to correct one line above."),
                          ("tool", "query", {"sql": "SELECT COUNT(*) AS n FROM v_txn"}))
        last = round_of(("thinking", "Say the corrected figure."), ("text", "It is about 9 months without CPF."),
                        stop="end_turn")
        script = Script(first, second, last)
        events = ask("How long does my money last?", script)
        self.assertEqual([e["type"] for e in events], ["thread", "thinking", "say", "tool", "tool",
                                                       "thinking", "say", "tool", "thinking", "text", "done"])
        for e in events:
            self.assertEqual(problems(e, wire.CopilotEvent), [], e)
        self.assertEqual(events[1]["text"], "Start from the overview and the runway stat.")
        self.assertEqual(events[2]["text"], "Your runway is about 14 months.")
        self.assertEqual(events[-2]["text"], "It is about 9 months without CPF.")
        # every block goes back as it came, thinking with its signature included
        self.assertEqual(script.requests[1][2]["messages"][-2], {"role": "assistant", "content": first["content"]})
        self.assertEqual(script.requests[2][2]["messages"][-2], {"role": "assistant", "content": second["content"]})
        # the thread keeps the steps, the interim text in place, and the final answer as the text
        thread = copilot.load(events[0]["thread"]["id"])
        self.assertEqual(problems(thread, wire.CopilotThread), [])
        saved = thread["messages"][-1]
        self.assertEqual(saved["text"], "It is about 9 months without CPF.")
        self.assertEqual([(s["kind"], s["text"], [t["name"] for t in s["tools"]]) for s in saved["steps"]], [
            ("thinking", "Start from the overview and the runway stat.", []),
            ("text", "Your runway is about 14 months.", []),
            ("tools", "", ["overview", "stat_result"]),
            ("thinking", "The stat counts CPF, so the line above is off.", []),
            ("text", "I need to correct one line above.", []),
            ("tools", "", ["query"]),
            ("thinking", "Say the corrected figure.", []),
        ])
        self.assertEqual([t["name"] for t in saved["tools"]], ["overview", "stat_result", "query"])
        self.assertEqual(thread["messages"][0]["steps"], [])
        # a later question carries the earlier answer as its final text only
        again = Script(answer("Still 9 months."))
        ask("And now?", again, thread=events[0]["thread"]["id"])
        self.assertEqual(again.requests[0][2]["messages"][1],
                         {"role": "assistant", "content": "It is about 9 months without CPF."})

    def test_omitted_thinking_makes_no_step(self):
        events = ask("Trips?", Script(tool_use(("trips", {})), answer("One trip, Coast.")))
        self.assertEqual([e["type"] for e in events], ["thread", "tool", "text", "link", "done"])
        saved = copilot.load(events[0]["thread"]["id"])["messages"][-1]
        self.assertEqual(saved["steps"], [{"kind": "tools", "text": "", "tools": [{"name": "trips", "label": "Trips"}]}])

    def test_an_answer_written_beside_follow_ups_stays_the_answer(self):
        script = Script(round_of(("text", "Groceries came to S$100.00."),
                                 ("tool", "follow_ups", {"questions": ["And eating out?"]})),
                        round_of(stop="end_turn"))
        events = ask("Groceries?", script)
        self.assertEqual(next(e for e in events if e["type"] == "text")["text"], "Groceries came to S$100.00.")
        saved = copilot.load(events[0]["thread"]["id"])["messages"][-1]
        self.assertEqual((saved["text"], saved["steps"]), ("Groceries came to S$100.00.", []))

    def test_an_answer_that_repeats_the_text_beside_follow_ups_saves_it_once(self):
        script = Script(round_of(("text", "Groceries came to S$100.00."),
                                 ("tool", "follow_ups", {"questions": ["And eating out?"]})),
                        answer("Groceries came to S$100.00."))
        events = ask("Groceries?", script)
        saved = copilot.load(events[0]["thread"]["id"])["messages"][-1]
        self.assertEqual((saved["text"], [s["kind"] for s in saved["steps"]]),
                         ("Groceries came to S$100.00.", []))

    def test_only_models_that_take_adaptive_thinking_are_asked_for_it(self):
        for model, asked in [("claude-opus-5-5", True), ("claude-sonnet-5-5", True), ("claude-haiku-5-5", True),
                             ("claude-opus-4-6", True), ("claude-haiku-4-5", False),
                             ("claude-sonnet-4-5-20250929", False), ("claude-3-7-sonnet-latest", False),
                             ("kimi-k2", False)]:
            chat = ai.Chat({"provider": "anthropic", "format": "anthropic", "base_url": "https://api.anthropic.com/v1",
                            "copilot_model": model}, "k", "sys", [], [], "q")
            body = chat._anthropic_request()[2]
            self.assertEqual("thinking" in body, asked, model)
            if asked:
                self.assertEqual(body["thinking"], {"type": "adaptive", "display": "summarized"})

    def test_openai_style_reasoning_and_interim_content_become_steps(self):
        with closing(db.connect()) as conn:
            ai.save_settings(conn, {"provider": "ollama", "model": "qwen3"})
        self.addCleanup(settle, {"provider": "anthropic", "send_rows": True})
        script = Script(
            {"choices": [{"finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": "Looking at your trips first.", "reasoning_content": "Trips hold it.",
                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "trips", "arguments": "{}"}}]}}]},
            {"choices": [{"finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "overview", "arguments": "{}"}}]}}]},
            {"choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": "One trip, Coast.", "reasoning_content": "Answer plainly."}}]})
        events = ask("Trips?", script)
        self.assertEqual([e["type"] for e in events][:-2],
                         ["thread", "thinking", "say", "tool", "tool", "thinking", "text"])
        saved = copilot.load(events[0]["thread"]["id"])["messages"][-1]
        # two rounds of tools with nothing between them are one row, as the panel shows them live
        self.assertEqual([(s["kind"], s["text"], [t["name"] for t in s["tools"]]) for s in saved["steps"]], [
            ("thinking", "Trips hold it.", []), ("text", "Looking at your trips first.", []),
            ("tools", "", ["trips", "overview"]), ("thinking", "Answer plainly.", [])])
        self.assertEqual(saved["text"], "One trip, Coast.")

    def test_a_thread_saved_before_steps_still_loads(self):
        tid = "0123456789abcdef"
        old = {"id": tid, "title": "Old one", "updated_at": "2026-10-01T00:00:00+00:00", "messages": [
            {"role": "user", "text": "Kopi?", "at": "2026-10-01T00:00:00+00:00", "page": None, "tools": [],
             "charts": [], "links": [], "cards": [], "followups": []},
            {"role": "assistant", "text": "S$310", "at": "2026-10-01T00:00:05+00:00", "page": None,
             "tools": [{"name": "query", "label": "Looking it up in the record"}],
             "charts": [], "links": [], "cards": [], "followups": []}]}
        copilot.threads_dir().mkdir(parents=True, exist_ok=True)
        (copilot.threads_dir() / f"{tid}.json").write_text(json.dumps(old), encoding="utf-8")
        self.addCleanup((copilot.threads_dir() / f"{tid}.json").unlink, missing_ok=True)
        thread = copilot.load(tid)      # what GET /api/copilot/threads/<id> answers
        self.assertEqual(problems(thread, wire.CopilotThread), [])
        self.assertEqual([m["steps"] for m in thread["messages"]], [[], []])
        self.assertEqual(thread["messages"][1]["tools"], [{"name": "query", "label": "Looking it up in the record"}])
        # a question asked on it appends to it
        again = Script(answer("Still S$310."))
        ask("And now?", again, thread=tid)
        self.assertEqual(len(copilot.load(tid)["messages"]), 4)


# ---------------------------------------------------------------- the tools

class ToolsTest(Record):
    def count(self, link):
        """How many rows the Transactions page lists for a link."""
        from urllib.parse import parse_qsl, urlsplit
        query = dict(parse_qsl(urlsplit(link).query))
        with closing(db.connect()) as conn:
            return api.transactions(conn, {**query, "limit": "500"})["total"]

    def test_every_tool_runs_on_the_record(self):
        ctx = self.context()
        calls = {"overview": {}, "spending": {}, "transactions": {"month": "2026-08"}, "recurring": {}, "trips": {},
                 "net_worth": {}, "query": {"sql": "SELECT COUNT(*) AS n FROM v_txn"},
                 "show_chart": {"title": "t", "x": "m", "series": ["v"], "rows": [{"m": "a", "v": 1}]},
                 "suggest_change": {"kind": "category", "key": "e2", "category": "Eating out"},
                 "follow_ups": {"questions": ["Why?"]}}
        for name in tools.BY_NAME:
            if name == "stat_result":
                continue
            with self.subTest(tool=name):
                self.call(ctx, name, **calls[name])
        text, failed = tools.run(ctx, "stat_result", {"id": "monthly-cashflow"})
        self.assertTrue(failed)
        self.assertIn("Results there are", text)

    def test_a_result_that_counts_rows_links_to_exactly_those_rows(self):
        ctx = self.context()
        t = self.call(ctx, "transactions", category="Groceries")
        self.assertEqual(self.count(t["link"]), t["rows_matching"])
        s = self.call(ctx, "spending", **{"from": "2026-08", "to": "2026-09", "by": "category"})
        for c in (c for b in s["buckets"] for c in b["categories"]):
            with closing(db.connect()) as conn:
                from urllib.parse import parse_qsl, urlsplit
                listed = api.transactions(conn, {**dict(parse_qsl(urlsplit(c["link"]).query)), "limit": "500"})
            self.assertEqual(-listed["sum_sgd"], c["total"], c["name"])
        trip = self.call(ctx, "trips")["trips"][0]
        self.assertEqual(self.count(trip["link"]), trip["rows"])
        # balances and averages carry none
        self.assertNotIn('"link"', json.dumps(self.call(ctx, "net_worth")))
        self.assertNotIn("link", {k for k in s if "average" in k})
        self.assertEqual(ctx.links[0]["href"], t["link"])

    def test_query_runs_one_select_read_only(self):
        ctx = self.context()
        out = self.call(ctx, "query", sql="WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n "
                                           "WHERE i < 500) SELECT i FROM n;")
        self.assertEqual((out["columns"], len(out["rows"]), out["truncated"]), (["i"], 200, True))
        for sql in ("DELETE FROM txn", "UPDATE txn SET memo = ''", "INSERT INTO setting VALUES ('a', 'b')",
                    "SELECT 1; DELETE FROM txn", "PRAGMA table_info(txn)", "ATTACH 'x.db' AS x",
                    "SELECT * FROM setting", "SELECT * FROM upload_step", "", "DROP TABLE txn"):
            with self.subTest(sql=sql):
                text, failed = tools.run(ctx, "query", {"sql": sql})
                self.assertTrue(failed, text)
        with closing(db.connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM txn").fetchone()[0], len(ROWS))
        # a semicolon inside a string is part of the one statement
        self.assertEqual(self.call(ctx, "query", sql="SELECT 'a;b' AS t")["rows"], [["a;b"]])

    def test_query_cannot_build_or_hand_back_a_huge_value(self):
        # G22: the row cap and the deadline do not bound memory; SQLite's own limits do
        ctx = self.context()
        for sql, words in (("SELECT hex(randomblob(5000000)) AS h", "over 1 MB"),
                           ("SELECT length(group_concat(hex(randomblob(100000)))) FROM v_txn a, v_txn b", "over 1 MB"),
                           ("SELECT 1 /*" + "x" * 200_000 + "*/", "longer than a query may be"),
                           (" UNION ALL ".join(["SELECT 1"] * 40), "bigger than a query may be")):
            with self.subTest(sql=sql[:60]):
                text, failed = tools.run(ctx, "query", {"sql": sql})
                self.assertTrue(failed, text[:200])
                self.assertIn(words, text)
        # under the limit, what goes back to the model is still cut to size
        text, failed = tools.run(ctx, "query", {"sql": "SELECT hex(randomblob(250000)) AS h"})
        self.assertFalse(failed, text[:200])
        self.assertLess(len(text), 2 * tools.RESULT_CHARS)
        self.assertIn("more characters", text)
        out = tools.run_query(ctx.sandbox(), "SELECT hex(randomblob(400000)) FROM v_txn a, v_txn b")
        self.assertEqual(len(out["rows"]), len(ROWS) ** 2)
        self.assertTrue(all(len(c) < tools.CELL_CHARS + 50 for r in out["rows"] for c in r))
        text, failed = tools.run(ctx, "query", {"sql": "SELECT hex(randomblob(400000)) FROM v_txn a, v_txn b"})
        self.assertLess(len(text), 2 * tools.RESULT_CHARS)

    def test_the_connection_itself_refuses_a_write(self):
        conn = tools.open_readonly(self.path)
        self.addCleanup(conn.close)
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("DELETE FROM txn")

    def test_query_has_no_counterparty_memo_or_account_number_to_read(self):
        ctx = self.context()
        for sql in ("SELECT counterparty FROM v_txn", "SELECT memo FROM v_txn", "SELECT reference FROM v_txn",
                    "SELECT account_no FROM v_txn", "SELECT account_no FROM account", "SELECT * FROM txn",
                    "SELECT name FROM period", "SELECT * FROM setting"):
            with self.subTest(sql=sql):
                text, failed = tools.run(ctx, "query", {"sql": sql})
                self.assertTrue(failed, text)
        self.assertEqual(self.call(ctx, "query", sql="SELECT merchant_raw FROM v_txn WHERE txn_key = 'x1'")["rows"],
                         [[""]])

    def test_query_runs_on_scrubbed_data_so_no_sql_can_reveal_a_number_or_a_person(self):
        # G22: the after-the-fact filter cannot see through hex(), a split or a join; the copy never held them
        ctx = self.context()
        sqls = ["SELECT hex(name) AS h FROM account",
                "SELECT hex(account_name) FROM v_txn",
                "SELECT substr(name, 6, 3) || '|' || substr(name, 10, 5) || '|' || substr(name, 16) FROM account",
                "SELECT group_concat(unicode(substr(name, i, 1)), ',') FROM account, "
                "(WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 40) SELECT i FROM n)",
                "SELECT replace(name, '-', '') || ' ' || name FROM account",
                "SELECT a.name || t.txn_key, hex(t.account_name) FROM v_txn t JOIN account a ON a.id = t.account_id",
                "SELECT hex(p.name), hex(group_concat(t.payee_name)) FROM payee p "
                "LEFT JOIN v_txn t ON t.payee_id = p.id GROUP BY p.id",
                "SELECT hex(payee_name), hex(merchant_raw) FROM v_txn WHERE txn_key = 'x1'",
                "SELECT hex(account_name) FROM v_period"]
        seen = []
        for sql in sqls:
            with self.subTest(sql=sql):
                out = self.call(ctx, "query", sql=sql)
                self.assertTrue(out["rows"], sql)
                for cell in (c for r in out["rows"] for c in r if isinstance(c, str)):
                    seen.append(cell)
                    try:
                        seen.append(bytes.fromhex(cell).decode("utf-8", "replace"))
                    except ValueError:
                        pass
                    if "," in cell and cell.replace(",", "").isdigit():
                        seen.append("".join(chr(int(c)) for c in cell.split(",")))
        blob = "\n".join(seen)
        for secret in (ACCOUNT, "11111", "111|11111", PERSON, MEMO):
            self.assertNotIn(secret, blob)
        self.assertIn("1111", blob)                     # the masked number is still there to read
        # a join can only reach what the copy holds
        text, failed = tools.run(ctx, "query", {"sql": "SELECT t.counterparty FROM v_txn t JOIN account a "
                                                      "ON a.id = t.account_id"})
        self.assertTrue(failed, text)

    def test_the_copy_compares_values_exactly_as_the_record_does(self):
        # G22: an untyped copy would find no account '1' and count every integer as below '-10000'
        ctx = self.context()
        sqls = ["SELECT txn_key FROM v_txn WHERE account_id = '1' ORDER BY txn_key",
                "SELECT txn_key FROM v_txn WHERE amount_minor < '-10000' ORDER BY txn_key",
                "SELECT txn_key FROM v_txn WHERE amount_minor < '-5000' ORDER BY txn_key",
                "SELECT id FROM account WHERE id = '1'",
                "SELECT category_id FROM budget WHERE planned_minor > '1000' ORDER BY category_id",
                "SELECT id FROM v_period WHERE account_id = '1' AND closing_minor > '0'"]
        for sql in sqls:
            with self.subTest(sql=sql):
                record = [list(r) for r in ctx.conn.execute(sql)]
                self.assertEqual(self.call(ctx, "query", sql=sql)["rows"], record)
                self.assertEqual(bool(record), "10000" not in sql, sql)       # no row spent over $100
        self.assertEqual(self.call(ctx, "query", sql=sqls[2])["rows"], [["g2"]])

    def test_query_still_caps_rows_and_time(self):
        ctx = self.context()
        out = self.call(ctx, "query", sql="SELECT a.txn_key FROM v_txn AS a, v_txn AS b, v_txn AS c")
        self.assertEqual((len(out["rows"]), out["truncated"]), (tools.MAX_ROWS, True))
        conn = ctx.sandbox()
        forever = "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT count(*) FROM n"
        with self.assertRaisesRegex(tools.ToolRefused, "stopped because it ran past 0.2 seconds"):
            tools.run_query(conn, forever, seconds=0.2)
        with self.assertRaisesRegex(tools.ToolRefused, "stopped because it needed too much memory"):
            tools.run_query(conn, forever, memory=-1)
        # the copy is a read-only place too: no write, ATTACH or PRAGMA
        for sql in ("ATTACH ':memory:' AS x", "PRAGMA table_info(v_txn)"):
            with self.assertRaises(tools.ToolRefused):
                tools.run_query(conn, sql)

    def test_nothing_sent_to_the_model_names_a_person_a_memo_or_an_account_number(self):
        ctx = self.context()
        sent = [tools.dictionary(ctx)]
        for name, args in (("overview", {}), ("spending", {"by": "payee"}), ("transactions", {}),
                           ("transactions", {"key": "x1"}), ("recurring", {}), ("trips", {}),
                           ("query", {"sql": "SELECT * FROM v_txn"}),
                           ("query", {"sql": "SELECT name FROM account"})):
            sent.append(tools.run(ctx, name, args)[0])
        blob = "\n".join(sent)
        for secret in (PERSON, "JANE+TAN", "JANE%20TAN", MEMO, "SALARY ACME", ACCOUNT, "11111"):
            self.assertNotIn(secret, blob)
        self.assertIn("(hidden)", blob)                 # the payee the explorer named from the counterparty
        self.assertIn("1111", blob)                     # the last four digits stay

    def test_the_tool_lines_are_plain_words(self):
        self.assertEqual(tools.label("spending", {"from": "2026-08", "to": "2026-09", "by": "category"}),
                         "Spending by category, Aug and Sep 2026")
        self.assertEqual(tools.label("spending", {"from": "2026-01", "to": "2026-09"}),
                         "Spending by category, Jan to Sep 2026")
        self.assertEqual(tools.label("transactions", {"category": "Groceries", "month": "2026-09"}),
                         "Transactions: Groceries, Sep 2026")

    def test_a_chart_is_checked_before_it_is_shown(self):
        ctx = self.context()
        for args in ({"title": "t", "x": "m", "series": ["v"], "rows": [{"m": "a", "v": "1"}]},
                     {"title": "t", "x": "m", "series": ["v"], "rows": []},
                     {"title": "t", "type": "pie", "x": "m", "series": ["v"], "rows": [{"m": "a", "v": 1}]},
                     {"title": "t", "x": "m", "series": ["v"], "unit": "sgd_minor", "rows": [{"m": "a", "v": 1.5}]},
                     # G22: a money series under another chart unit is still whole cents
                     {"title": "t", "x": "m", "series": ["pct", "v"], "unit": "percent", "units": {"v": "sgd_minor"},
                      "rows": [{"m": "a", "pct": 12.5, "v": 1.5}]},
                     {"x": "m", "series": ["v"], "rows": [{"m": "a", "v": 1}]}):
            with self.subTest(args=args):
                self.assertTrue(tools.run(ctx, "show_chart", args)[1])
        self.assertEqual(ctx.charts, [])

    def test_a_chart_checks_each_series_by_its_own_unit(self):
        ctx = self.context()
        self.call(ctx, "show_chart", title="Spend and share", x="m", series=["spend", "pct"], unit="sgd_minor",
                  units={"pct": "percent"}, rows=[{"m": "Aug", "spend": 7860, "pct": 12.5}])
        self.assertEqual(ctx.charts[-1]["chart"]["units"], {"pct": "percent"})
        self.call(ctx, "show_chart", title="Whole cents", x="m", series=["spend"], unit="sgd_minor",
                  rows=[{"m": "Aug", "spend": 7860.0}])
        self.assertEqual(len(ctx.charts), 2)

    def test_cards_build_the_existing_writes_and_preview_them(self):
        ctx = self.context()
        rule = self.call(ctx, "suggest_change", kind="rule", match_on="merchant", pattern="TOAST BOX*",
                         payee="Toast Box", category="Eating out", why="the same cafe")
        self.assertEqual(rule["rows_that_would_change"], 2)
        card = ctx.cards[-1]
        self.assertEqual(card["request"]["path"], "/api/rules/save")
        self.assertEqual(card["request"]["body"]["rule"]["pattern"], "TOAST BOX*")
        self.assertIn("[ai]", card["request"]["body"]["rule"]["notes"])
        self.assertTrue(any("TOAST BOX 0123 RAFFLES" in line and line.endswith("to Eating out")
                            for line in card["preview"]), card["preview"])
        self.assertIsNone(card["undo"])
        self.call(ctx, "suggest_change", kind="category", key="x1", category="Person to person")
        self.assertEqual(ctx.cards[-1]["request"], {"path": "/api/rules/pin",
                                                    "body": {"key": "x1", "category": "Person to person"}})
        self.call(ctx, "suggest_change", kind="trip", name="Tokyo", start="2026-09-01", end="2026-09-07",
                  countries=["jpn"])
        self.assertEqual(ctx.cards[-1]["request"]["body"]["countries"], ["JPN"])
        for card in ctx.cards:
            self.assertEqual(problems(card, wire.CopilotCard), [])
        for args in ({"kind": "category", "key": "x1", "category": "Not a category"},
                     {"kind": "category", "key": "nope", "category": "Groceries"},
                     {"kind": "budget", "category": "Groceries", "month": "Sep", "planned_minor": 1},
                     {"kind": "budget", "category": "Groceries", "month": "2026-09", "planned_minor": 12.5},
                     {"kind": "trip", "name": "X", "start": "2026-09-09", "end": "2026-09-01"},
                     {"kind": "rule", "pattern": "", "category": "Groceries"},
                     {"kind": "wish"}):
            with self.subTest(args=args):
                self.assertTrue(tools.run(ctx, "suggest_change", args)[1])

    def test_a_card_the_owner_confirms_saves_through_the_endpoint_it_names(self):
        ctx = self.context()
        self.call(ctx, "suggest_change", kind="budget", category="Groceries", month="2026-09", planned_minor=45_000)
        card = ctx.cards[-1]
        # G22: no undo captured when the card was made: the amount may change before Confirm
        self.assertIsNone(card["undo"])
        with mock.patch("finance.stats_rebuild.refresh_stats"):
            serve.write(card["request"]["path"], {}, json.dumps(card["request"]["body"]).encode())
            with closing(db.connect()) as conn:
                self.assertEqual(conn.execute("SELECT planned_minor FROM budget WHERE month = '2026-09'").fetchone()[0],
                                 45_000)
            # what the page's Undo sends: the amount it read at Confirm, to the same endpoint
            serve.write(card["request"]["path"], {}, json.dumps({**card["request"]["body"], "planned": 30_000}).encode())
        with closing(db.connect()) as conn:
            self.assertEqual(conn.execute("SELECT planned_minor FROM budget WHERE month = '2026-09'").fetchone()[0],
                             30_000)


# ---------------------------------------------------------------- the endpoint and the threads

class StreamTest(Record):
    def setUp(self):
        super().setUp()
        self.server = serve.make_server(0, self.root / "dist")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def post(self, path, body):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=20)
        self.addCleanup(conn.close)
        conn.request("POST", path, json.dumps(body), {"X-Kopifund": "1", "Content-Type": "application/json"})
        return conn.getresponse()

    def test_the_answer_streams_one_event_per_line(self):
        script = Script(tool_use(("trips", {})), answer("One trip, Coast."))
        with mock.patch.object(ai, "_post", script):
            r = self.post("/api/copilot/ask", {"thread": None, "question": "Trips?",
                                               "page": {"path": "/trips", "title": "Trips", "filters": {}}})
            body = r.read().decode()
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader("Content-Type"), "application/x-ndjson")
        self.assertEqual(r.getheader("Cache-Control"), "no-store")
        self.assertIsNone(r.getheader("Content-Length"))
        events = [json.loads(line) for line in body.splitlines()]
        self.assertEqual([e["type"] for e in events], ["thread", "tool", "text", "link", "done"])
        self.assertEqual(events[1]["tool"], {"name": "trips", "label": "Trips"})
        # G22: the trip the answer names comes with the link to its rows
        self.assertEqual(events[3]["link"], {"label": "Spending on Coast",
                                             "href": "/transactions?trip=Coast&spend=1&hide_transfers=0"})
        for e in events:
            self.assertEqual(problems(e, wire.CopilotEvent), [])
        # the thread is saved with its page, the folder 700 and the file 600
        tid = events[0]["thread"]["id"]
        thread = serve.answer(f"/api/copilot/threads/{tid}", {})
        self.assertEqual(problems(thread, wire.CopilotThread), [])
        self.assertEqual(thread["messages"][0]["page"], {"path": "/trips", "title": "Trips", "filters": {}})
        self.assertEqual(stat.S_IMODE(copilot.threads_dir().stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((copilot.threads_dir() / f"{tid}.json").stat().st_mode), 0o600)

    def test_refused_before_the_stream_without_consent(self):
        with closing(db.connect()) as conn:
            ai.save_settings(conn, {"send_rows": False})
        self.addCleanup(settle, {"send_rows": True})
        r = self.post("/api/copilot/ask", {"thread": None, "question": "Hi", "page": None})
        self.assertEqual(r.status, 422)
        self.assertIn("Send matching rows to Anthropic", json.loads(r.read())["error"])

    def test_a_local_provider_needs_no_consent(self):
        with closing(db.connect()) as conn:
            ai.save_settings(conn, {"provider": "ollama", "model": "qwen3", "send_rows": False})
        self.addCleanup(settle, {"provider": "anthropic", "send_rows": True})
        script = Script({"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "Hi."}}]})
        with mock.patch.object(ai, "_post", script):
            r = self.post("/api/copilot/ask", {"thread": None, "question": "Hi", "page": None})
            r.read()
        self.assertEqual(r.status, 200)
        self.assertEqual(script.requests[0][0], "http://localhost:11434/v1/chat/completions")

    def test_off_is_refused_too(self):
        with closing(db.connect()) as conn:
            ai.save_settings(conn, {"provider": None})
        self.addCleanup(settle, {"provider": "anthropic"})
        self.assertEqual(self.post("/api/copilot/ask", {"question": "Hi"}).status, 422)

    def test_threads_round_trip(self):
        events = ask("First question here", Script(answer("First answer.")))
        tid = events[0]["thread"]["id"]
        listed = serve.answer("/api/copilot/threads", {})
        self.assertEqual(problems(listed, wire.CopilotThreads), [])
        mine = next(t for t in listed["threads"] if t["id"] == tid)
        self.assertEqual((mine["title"], mine["turns"]), ("First question here", 1))
        out = serve.write("/api/copilot/threads/remove", {}, json.dumps({"id": tid}).encode())
        self.assertNotIn(tid, {t["id"] for t in out["threads"]})
        for bad in ("../../etc/passwd", tid):
            with self.assertRaises(serve.NotFound):
                serve.answer(f"/api/copilot/threads/{bad}", {})

    def test_the_ai_endpoints_answer_in_their_shapes(self):
        self.assertEqual(problems(serve.answer("/api/ai", {}), wire.AiSettings), [])
        with mock.patch.object(ai, "_get", lambda url, headers: {"data": [], "has_more": False}):
            self.assertEqual(problems(serve.answer("/api/ai/models", {"q": "opus"}), wire.AiModels), [])
        self.assertEqual(problems(serve.write("/api/ai/settings", {}, b'{"model": "claude-haiku-5-5"}'),
                                  wire.AiSettings), [])
        with mock.patch.object(ai, "_post", lambda *a, **k: {"type": "message", "content": []}):
            self.assertEqual(problems(serve.write("/api/ai/test", {}, b"{}"), wire.AiTest), [])
        with self.assertRaises(serve.Refused):
            serve.write("/api/ai/settings", {}, b'{"provider": "skynet"}')
        with self.assertRaises(serve.Refused):
            serve.write("/api/ai/key", {}, b'{"provider": "anthropic", "key": "  "}')


class ThreadSaveTest(Record):
    """G22: a turn is saved whole, once its answer is, and never over turns saved meanwhile."""

    def texts(self, tid):
        return [(m["role"], m["text"]) for m in copilot.load(tid)["messages"]]

    def started(self, question, transport, thread=None):
        """A question's events, the first (`thread`) already read: the rest run on next()."""
        gen = copilot.start(api.config_or_none(), None, {"thread": thread, "question": question, "page": None},
                            transport=transport, today=date(2026, 10, 23))
        first = next(gen)
        self.assertEqual(first["type"], "thread")
        return first["thread"]["id"], gen

    def test_a_failed_turn_saves_nothing_so_retry_adds_no_duplicate(self):
        def fails(url, headers, body):
            raise ai.AIError("The AI provider said 529: overloaded")
        # a new thread whose first turn fails leaves no file, and its id still takes the Retry
        events = ask("How much on groceries?", fails)
        self.assertEqual([e["type"] for e in events], ["thread", "error", "done"])
        tid = events[0]["thread"]["id"]
        self.assertEqual(events[0]["thread"]["turns"], 1)
        self.assertFalse((copilot.threads_dir() / f"{tid}.json").exists())
        self.assertNotIn(tid, {t["id"] for t in copilot.listing()["threads"]})
        retry = Script(answer("S$100.00."))
        ask("How much on groceries?", retry, thread=tid)
        self.assertEqual(self.texts(tid), [("user", "How much on groceries?"), ("assistant", "S$100.00.")])
        self.assertEqual(len(retry.requests[0][2]["messages"]), 1)
        # a later turn that fails leaves the thread as it was, and the next request never sees it
        ask("And eating out?", fails, thread=tid)
        self.assertEqual(len(self.texts(tid)), 2)
        after = Script(answer("S$12.40."))
        ask("And eating out?", after, thread=tid)
        sent = after.requests[0][2]["messages"]
        self.assertEqual([m["role"] for m in sent], ["user", "assistant", "user"])
        self.assertEqual(self.texts(tid)[2:], [("user", "And eating out?"), ("assistant", "S$12.40.")])

    def test_a_question_overtaken_by_a_newer_one_does_not_overwrite_it(self):
        tid = ask("First", Script(answer("One.")))[0]["thread"]["id"]

        def slow(url, headers, body):
            # Stop, then ask again, while this first request still waits on the provider
            ask("Second", Script(answer("Two.")), thread=tid)
            return answer("Late.")
        _, gen = self.started("Stopped one", slow, thread=tid)
        rest = list(gen)
        self.assertEqual(rest[-2:], [{"type": "text", "text": "Late."}, {"type": "done"}])
        self.assertEqual(self.texts(tid), [("user", "First"), ("assistant", "One."),
                                           ("user", "Second"), ("assistant", "Two.")])

    def test_an_older_request_finishing_first_does_not_drop_the_newer_one(self):
        tid = ask("First", Script(answer("One.")))[0]["thread"]["id"]
        _, older = self.started("Stopped one", Script(answer("Late.")), thread=tid)
        newer_tid, newer = self.started("Second", Script(answer("Two.")), thread=tid)
        list(older)                       # finishes first, but a newer question overtook it
        list(newer)
        self.assertEqual(self.texts(tid), [("user", "First"), ("assistant", "One."),
                                           ("user", "Second"), ("assistant", "Two.")])

    def test_a_thread_removed_while_a_question_runs_is_not_brought_back(self):
        tid = ask("First", Script(answer("One.")))[0]["thread"]["id"]

        def removes(url, headers, body):
            copilot.remove(tid)
            return answer("Gone.")
        _, gen = self.started("Second", removes, thread=tid)
        list(gen)
        with self.assertRaises(serve.NotFound):
            copilot.load(tid)
        with self.assertRaises(serve.NotFound):
            ask("Third", Script(answer("x")), thread=tid)

    def test_a_new_thread_removed_before_its_first_answer_stays_gone(self):
        holder = {}

        def removes(url, headers, body):
            copilot.remove(holder["id"])   # the page's list may not show it yet; removing it still works
            return answer("Gone.")
        holder["id"], gen = self.started("First", removes)
        list(gen)
        self.assertFalse((copilot.threads_dir() / f"{holder['id']}.json").exists())
        with self.assertRaises(serve.NotFound):
            ask("Again", Script(answer("x")), thread=holder["id"])

    def test_an_unknown_thread_is_not_found(self):
        with self.assertRaises(serve.NotFound):
            ask("Hi", Script(answer("x")), thread="0123456789abcdef")


class LinksTest(Record):
    def test_a_trip_or_recurring_payee_links_only_when_the_answer_names_it(self):
        ctx = self.context()
        self.call(ctx, "trips")
        rows = tools._recurring_rows(ctx, [{"name": "Netflix", "payee": "Netflix", "amount": -1798,
                                            "link": {"payee": "Netflix", "out": "1"}}])
        self.assertEqual(ctx.links, [])
        ctx.settle_links("You paid Netflix S$17.98 a month, and spent S$30.00 on the Coast trip.")
        self.assertEqual(ctx.links, [
            {"label": "Spending on Coast", "href": "/transactions?trip=Coast&spend=1&hide_transfers=0"},
            {"label": "Payments to Netflix", "href": rows[0]["payments_link"]}])
        other = self.context()
        self.call(other, "trips")
        other.settle_links("You took no trips abroad (Coastal is not a trip).")
        self.assertEqual(other.links, [])

    def test_a_query_that_lists_its_rows_links_to_exactly_them_and_a_bare_sum_does_not(self):
        ctx = self.context()
        text = self.call(ctx, "query", sql="SELECT SUM(amount_sgd_minor) AS total FROM v_txn")
        self.assertNotIn("link", text)
        self.assertEqual(ctx.links, [])
        keys = [r[0] for r in tools.open_readonly(self.path).execute(
            "SELECT txn_key FROM v_txn ORDER BY txn_key LIMIT 3")]
        text = self.call(ctx, "query", sql="SELECT txn_key, amount_sgd_minor FROM v_txn ORDER BY txn_key LIMIT 3")
        want = tools.href({"key": ",".join(keys)})
        self.assertEqual(text["link"], want)
        self.assertEqual(ctx.links, [{"label": "Open the 3 rows in Transactions", "href": want}])

    def test_links_stay_a_few(self):
        ctx = self.context()
        for i in range(10):
            ctx.offer_named_link(f"Shop{i}", f"Payments to Shop{i}", f"/transactions?payee=Shop{i}")
        ctx.settle_links(" ".join(f"Shop{i}" for i in range(10)))
        self.assertEqual(len(ctx.links), tools.LINKS_SHOWN)


class IsolationTest(unittest.TestCase):
    def test_the_threads_folder_is_a_throwaway_one(self):
        self.assertTrue(copilot.threads_dir().is_relative_to(Path(os.environ["KOPIFUND_TEST_SCRATCH"])))


if __name__ == "__main__":
    unittest.main()
