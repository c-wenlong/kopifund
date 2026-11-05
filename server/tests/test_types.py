"""G17: the app's types are generated from server/finance/types.py, and every
endpoint answers in exactly those shapes.

Two checks keep the browser and the server in step. The committed
apps/web/src/types.gen.ts must be what `make types` writes now, so a change to
server/finance/types.py (or to serve.py's cached and no-change route lists) cannot
land without the TypeScript that matches it. And each endpoint, run through
serve.answer on a small invented record, must answer with every required key
present, no key the TypedDict does not name, and every value of its type, at
every level; every TypedDict an endpoint can answer with must have been met
at least once, so a list left empty by the fixture does not pass unchecked."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import json
import os
import tempfile
import typing
import unittest
import warnings
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest import mock

import serve
from finance import (ai, copilot, dashboard, db, gen_types, home, keys, pending, planning, recurring, rules_app,
                     scam_checks, setup, upload_log)
from finance import types as wire
from test_db import block, parsed, row

# ---------------------------------------------------------------- the checker


def problems(value, tp, where="$", met=None):
    """Every way `value` is not of type `tp`, as "where: what" lines; the
    TypedDicts it met go into `met`."""
    met = met if met is not None else set()
    if isinstance(tp, typing.TypeAliasType):
        return problems(value, tp.__value__, where, met)
    if isinstance(tp, typing.NewType):
        return problems(value, tp.__supertype__, where, met)
    if typing.is_typeddict(tp):
        if not isinstance(value, dict):
            return [f"{where}: {tp.__name__} is an object, got {type(value).__name__}"]
        met.add(tp)
        hints = typing.get_type_hints(tp)
        out = [f"{where}: {tp.__name__} lacks {k!r}" for k in sorted(tp.__required_keys__ - value.keys())]
        out += [f"{where}: {tp.__name__} has no {k!r}" for k in sorted(value.keys() - hints.keys())]
        for k in hints.keys() & value.keys():
            out += problems(value[k], hints[k], f"{where}.{k}", met)
        return out
    if tp is typing.Any:
        return []
    origin, args = typing.get_origin(tp), typing.get_args(tp)
    if origin is typing.Union:
        tried = [problems(value, a, where, set()) for a in args]
        if any(not t for t in tried):
            # the branch that fits, checked again to note what it met
            return problems(value, args[tried.index([])], where, met)
        return [f"{where}: {value!r:.60} is none of {tp}"]
    if origin is typing.Literal:
        if any(value == a and type(value) is type(a) for a in args):
            return []
        return [f"{where}: {value!r} is not one of {args}"]
    if origin is list:
        if not isinstance(value, list):
            return [f"{where}: a list, got {type(value).__name__}"]
        return [p for i, v in enumerate(value) for p in problems(v, args[0], f"{where}[{i}]", met)]
    if origin is dict:
        if not isinstance(value, dict):
            return [f"{where}: an object, got {type(value).__name__}"]
        return [p for k, v in value.items()
                for p in problems(k, args[0], f"{where} key", met) + problems(v, args[1], f"{where}[{k!r}]", met)]
    if tp is type(None):
        return [] if value is None else [f"{where}: null, got {value!r:.60}"]
    if tp is bool:
        return [] if isinstance(value, bool) else [f"{where}: a boolean, got {value!r:.60}"]
    if tp is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
        return [] if ok else [f"{where}: a whole number, got {value!r:.60}"]
    if tp is float:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        return [] if ok else [f"{where}: a number, got {value!r:.60}"]
    if tp is str:
        return [] if isinstance(value, str) else [f"{where}: text, got {value!r:.60}"]
    raise TypeError(f"the checker has no case for {tp!r}")


def reachable(tp, found=None):
    """Every TypedDict a value of `tp` can hold, `tp` included."""
    found = found if found is not None else set()
    if isinstance(tp, typing.TypeAliasType):
        return reachable(tp.__value__, found)
    if typing.is_typeddict(tp):
        if tp not in found:
            found.add(tp)
            for h in typing.get_type_hints(tp).values():
                reachable(h, found)
        return found
    for a in typing.get_args(tp):
        if not isinstance(a, (str, int, bool)):
            reachable(a, found)
    return found


class CheckerTest(unittest.TestCase):
    """The checker itself catches what the endpoint test relies on it to catch."""

    def test_a_missing_an_extra_and_a_wrong_key_are_each_named(self):
        good = {"code": "twice", "text": "Charged twice"}
        self.assertEqual(problems(good, wire.ScamReason), [])
        self.assertEqual(problems({"code": "twice"}, wire.ScamReason), ["$: ScamReason lacks 'text'"])
        self.assertEqual(problems({**good, "why": ""}, wire.ScamReason), ["$: ScamReason has no 'why'"])
        self.assertEqual(problems({**good, "code": "odd"}, wire.ScamReason)[0][:25], "$.code: 'odd' is not one ")

    def test_money_is_whole_cents_and_null_only_where_typed(self):
        fine = {"key": "k", "date": "2026-08-01", "payee": "P", "amount": 1250}
        self.assertEqual(problems(fine, wire.ScamFine), [])
        self.assertTrue(problems({**fine, "amount": 12.5}, wire.ScamFine))
        self.assertTrue(problems({**fine, "amount": True}, wire.ScamFine))
        self.assertTrue(problems({**fine, "payee": None}, wire.ScamFine))

    def test_a_key_the_handler_may_leave_out_is_optional(self):
        state = {"needs": [], "needs_total": 0, "categories": [], "kinds": [], "tiers": [], "tier_files": {}, "rules": [],
                 "groups": [], "history": [], "pending": 0, "rules_error": "bad"}
        self.assertEqual(problems(state, wire.RulesState), [])
        self.assertEqual(problems({**state, "stale": True}, wire.RulesState), [])


# ---------------------------------------------------------------- the generated file

class GeneratedTest(unittest.TestCase):
    def test_the_committed_file_is_what_make_types_writes(self):
        self.assertEqual(gen_types.OUT.read_text(encoding="utf-8"), gen_types.render(),
                         "apps/web/src/types.gen.ts is stale: run `make types` and commit it")

    def test_the_app_reads_the_route_lists_from_it(self):
        # RT4: serve.py is the one list; api.ts no longer keeps its own
        text = (gen_types.ROOT / "apps" / "web" / "src" / "api.ts").read_text(encoding="utf-8")
        self.assertIn("new Set(CACHED_PATHS)", text)
        self.assertIn("NO_CHANGE = NO_CHANGE_PATHS", text)
        self.assertNotIn('"/api/run/check"', text)

    def test_a_row_a_write_names_carries_its_id(self):
        # G17 (T4): the writes name trips, categories, buckets and budgets by id
        for tp in (wire.Trip, wire.CategoryEntry, wire.CategoryGroup, wire.Category):
            self.assertIs(typing.get_type_hints(tp)["id"], int, tp.__name__)
        self.assertIs(typing.get_type_hints(wire.BudgetRow)["category_id"], int)
        # T5: cost_type, with group typed beside it and marked deprecated for the app
        for tp in (wire.CategoryEntry, wire.Category):
            hints = typing.get_type_hints(tp)
            self.assertEqual((hints["cost_type"], hints["group"]), (str | None, str | None), tp.__name__)
        text = gen_types.OUT.read_text(encoding="utf-8")
        self.assertEqual(text.count("  /** @deprecated the same value as `cost_type`"), 2)

    def test_a_view_asked_again_after_a_no_change_write_is_a_cached_get(self):
        # G18: a write that reloads everything needs no entry; a GET asked afresh every time needs none either
        import serve
        self.assertLessEqual(set(serve.REREAD_AFTER), set(serve.NO_CHANGE))
        self.assertLessEqual({p for paths in serve.REREAD_AFTER.values() for p in paths}, set(serve.CACHED))
        self.assertIn('"/api/run/check": ["/api/overview"]', gen_types.render())

    def test_the_scam_settings_are_the_ones_the_checks_keep(self):
        self.assertEqual(list(typing.get_type_hints(wire.ScamSettings)), list(scam_checks.DEFAULTS))


# ---------------------------------------------------------------- every endpoint

HEADER = "match_on,pattern,payee,kind,country,category,notes,account_no,recurring\n"
POSB = {"name": "POSB", "account_no": "111-11111-1", "institution": "DBS", "class": "Asset",
        "subclass": "Cash", "base_currency": "SGD"}


def paid(key, amount, date, payee, category, merchant=None, **kw):
    return row(key, amount, date=date, payee=payee, category=category, merchant=merchant or payee.upper(), **kw)


# invented rows: the record runs from May to 20 September 2026
ROWS = [
    paid("early", "-5.00", "2026-05-02", "", "Groceries", merchant="SHOP A"),
    *(paid(f"g{i}", "-50.00", d, "Gym", "Fitness")
      for i, d in enumerate(("2026-06-05", "2026-07-05", "2026-08-05", "2026-09-04"))),
    # G16: a second gym charge in September, which Coming up lists as extra
    paid("g9", "-50.00", "2026-09-15", "Gym", "Fitness"),
    *(paid(f"p{i}", "-20.00", d, "Phone", "Phone and utilities")
      for i, d in enumerate(("2026-06-02", "2026-07-02", "2026-08-02"))),
    paid("c1", "-12.00", "2026-08-15", "Club", "Entertainment"),
    paid("t1", "-30.00", "2026-08-11", "", "Eating out", merchant="WARUNG MAKAN", merchant_country="MYS"),
    # the salary a key rule pins, and one like it no rule names
    *(row(k, "2500.00", date=d, direction="In", merchant="", memo="SALARY PAYMENT", bank_txn_type="FAST",
          payee="", category="", rule="") for k, d in (("s1", "2026-08-25"), ("s2", "2026-07-25"))),
    row("k3", "-4.20", date="2026-08-20", merchant="KOPITIAM 0456 TPY", payee="", category="", rule=""),
    # a big transfer to a new payee, which the scam checks flag, and one marked fine
    *(row(k, a, date=d, bank_txn_type="PayNow", merchant="", counterparty=to, payee="", category="", rule="")
      for k, a, d, to in (("x1", "-800.00", "2026-08-10", "SCAMMER PTE"),
                          ("x2", "-900.00", "2026-09-20", "LANDLORD LEE"))),
]


def series(payee, last_seen, category, **kw):
    return {"payee": payee, "grouped_by": "payee", "category": category, "bucket": "Spend",
            "cadence": "monthly", "cadence_days": 30.44, "occurrences": 4, "first_seen": "2026-06-01",
            # G20: the detector writes its money in cents
            "last_seen": last_seen, "median_amount_minor": 5_000, "avg_amount_minor": 5_000,
            "monthly_cost_minor": 5_000, "amount_varies": False, "flagged_recurring": True, **kw}


# G17: an invented plan with every kind of row, so each plan type is met; a
# segment from before G14 keeps its note, and a cent shows the money is cents
PLAN = {
    "to": "2027-06", "dca": "100", "insurance": "120.50", "other_income": "0", "repayments": "45",
    "cpf_employer_rate": 0.17, "spend_rate": "window", "spend_exclude_keys": ["k3"],
    "income": [{"name": "Acme Co", "type": "employment", "from": "2026-01-01", "to": None, "gross": "5000.05",
                "cpf": True, "bonus_months": "1.5", "bonus_month": 12},
               {"from": "2025-01-01", "to": "2025-12-31", "gross": "300", "note": "Tutoring"}],
    "investing": [{"from": None, "amount": "250"}, {"from": "2027-01", "amount": "400.10"}],
    "trips": [{"name": "Tokyo", "from": "2027-03-01", "to": "2027-03-07", "per_day": "150", "flights": "620.40",
               "flights_month": "2027-01", "note": "spring"}],
    "one_offs": [{"month": "2027-02", "amount": "1200", "label": "Laptop"}],
    "projection": {"expected_pct": "6", "horizon": "20y", "dollars": "today", "spend": "2500", "milestone": "100000"},
}


RESULTS = {"recurring-detector": {"generated_at": "2026-09-20T08:00:00", "data": {
    "active": [series("Gym", "2026-09-04", "Fitness"),
               series("Phone", "2026-08-02", "Phone and utilities", median_amount_minor=2_000,
                      avg_amount_minor=2_000)],
    "paused": [series("Pool", "2026-07-05", "Fitness", status="paused", paused=True)],
    "stopped": [series("Old gym", "2026-01-05", "Fitness", first_seen="2025-06-05")],
    "flag_mismatches": {"flagged_not_detected": ["Club"], "detected_not_flagged": []}}}}


class December(date):
    """G18: a day when the record's account is late (finance.home reads `date.today`)."""
    @classmethod
    def today(cls):
        return cls(2026, 12, 1)


class EndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.env = mock.patch.dict(os.environ, {"KOPIFUND_DATA": str(root), "KOPIFUND_DB": str(root / "kopifund.db"),
                                               "KOPIFUND_CACHE": str(root / "cache"),
                                               "KOPIFUND_COPILOT": str(root / "copilot"),
                                               "KOPIFUND_ENV_FILE": str(root / "keys.env"),
                                               "KOPIFUND_DEVICES": str(root / "devices.json")})
        cls.env.start()
        cls.results = mock.patch("finance.results_cache.results", lambda req=None: RESULTS)
        cls.results.start()
        # G25: the tailnet URL is this machine's, so fake it rather than ask tailscale;
        # one paired phone so LinkDevice is checked too, not just the empty list
        cls.tailnet = mock.patch("finance.link.tailnet_url", return_value="https://kopifund-mac.tailnet-name.ts.net")
        cls.tailnet.start()
        (root / "devices.json").write_text(json.dumps({"pair_tokens": [], "devices": [
            {"id": "dev_1", "name": "Test iPhone", "token_hash": "x", "paired_at": "2026-10-30T08:00:00",
             "revoked": False}]}), encoding="utf-8")
        setup.create(["posb-everyday"])
        # two rules with one pattern in one tier, so the lint has a problem to show
        (root / "rules" / "merchants.csv").write_text(
            HEADER + "merchant,GYM*,Gym,Merchant,SGP,Fitness,,,yes\nmerchant,GYM*,Gym,Merchant,SGP,Fitness,,,yes\n",
            encoding="utf-8")
        with closing(db.connect()) as conn:
            db.import_user_tables(conn, {
                "accounts": [POSB],
                "budgets": [{"category": "Groceries", "month": "2026-08", "planned_minor": 30_000}],
                "trips": [{"name": "Coast", "start": "2026-08-10", "end": "2026-08-14", "countries": ["MYS"],
                           "keys": []}]})
            db.load_parsed(conn, parsed([block()], ROWS), trips_by_key={"t1": "Coast"},
                           payees=[{"name": p, "kind": "Merchant", "country": "SGP", "recurring": True}
                                   for p in ("Gym", "Phone", "Club")])
            rules_app.pin(conn, {"key": "s1", "category": "Salary", "payee": "Acme Co", "kind": "Employer"})
            scam_checks.mark_fine(conn, "x2")
            # G25: one pending row matched to the kopitiam row it is, one
            # still waiting, so PendingRow checks with both states
            pending.post_rows(conn, "dev_1", [
                {"import_id": "p1", "date": "2026-08-20", "amount_minor": -420, "currency": "SGD",
                 "payee": "Kopitiam"},
                {"import_id": "p2", "date": "2026-08-21", "amount_minor": -7777, "currency": "SGD",
                 "payee": "NOBODY"}])
            # G16: a series the owner said is not recurring, which the page lists to take back
            recurring.save_series(conn, RESULTS, {"id": "payee:Pool", "recurring": False})
            planning.set_plan(conn, PLAN)
            attempt = upload_log.start(conn, "upload")
            upload_log.record(conn, attempt, "check", upload_log.now(), "ok", "1 statement",
                              {"folder": "statements/posb-everyday"}, {"rows": 1})
            # G18: a later check that found a problem, which Needs your eye shows
            # G22: a provider picked in Settings with a key saved, and one copilot thread
            ai.save_settings(conn, {"provider": "anthropic", "send_rows": True})
            keys.save("anthropic", "sk-endpoint-test-0001")
            copilot.save({"id": "0123456789abcdef", "title": "What did I spend?",
                          "updated_at": "2026-10-01T00:00:00+00:00", "messages": []})
            later = upload_log.start(conn, "upload")
            upload_log.record(conn, later, "check", upload_log.now(), "problems", "1 problem", {},
                              {"problems": [{"code": "unbalanced"}], "to_add_count": 0})
            # G26: the dashboard's saved state, so every type it answers with is met
            dashboard.save_layouts(conn, {
                "layouts": [{"id": "main", "name": "Home",
                             "widgets": [{"id": "w-spend", "type": "line", "title": "Spend",
                                          "layout": {"x": 0, "y": 0, "w": 6, "h": 3},
                                          "options": {"legend": False},
                                          "query": {"source": "spending", "metric": "spend",
                                                    "dimension": "bucket",
                                                    "range": {"from": "2026-08", "to": "2026-08"}}}]}],
                "active": "main"})
            dashboard.save_goal(conn, RESULTS, {
                "name": "A year aside", "target_minor": 6_000_000, "month": "2027-06",
                "note": "invented", "milestones": [{"month": "2026-12", "target_minor": 1_500_000}],
                "source": "spending", "metric": "spend", "dimension": "month"})
            bucket = conn.execute("SELECT name FROM category_group WHERE kind = 'spend' "
                                  "ORDER BY name LIMIT 1").fetchone()[0]
            dashboard.save_allocation(conn, {"buckets": [{"bucket": bucket, "planned_minor": 50_000}]})
            posb = conn.execute("SELECT id FROM account WHERE name = 'POSB'").fetchone()[0]
            dashboard.save_game_pot(conn, {"name": "Punt", "benchmark": "VWRA", "note": "invented",
                                           "account_ids": [posb]})
        cls.attempt = attempt

    @classmethod
    def tearDownClass(cls):
        cls.tailnet.stop()
        cls.results.stop()
        cls.env.stop()
        cls.tmp.cleanup()

    def answers(self):
        """(path, query, the type it answers with) for each ask."""
        return [("/api/plan", {}, wire.PlanState),
                ("/api/transactions", {}, wire.TxnPage),
                ("/api/trips", {}, wire.TripList),
                ("/api/categories", {}, wire.CategoriesState),
                ("/api/budgets", {"month": "2026-08"}, wire.Budgets),
                ("/api/rules", {}, wire.RulesState),
                ("/api/rules/rows", {"text": "KOPITIAM 0456 TPY"}, wire.GroupRows),
                ("/api/rules/rows", {"key": "s1"}, wire.GroupRows),
                ("/api/recurring", {}, wire.RecurringPage),
                ("/api/recurring/series", {"id": "payee:Gym"}, wire.SeriesView),
                ("/api/profile", {}, wire.Profile),
                ("/api/scam-checks", {}, wire.ScamChecks),
                ("/api/uploads", {}, wire.UploadPage),
                (f"/api/uploads/{self.attempt}", {}, wire.UploadAttemptDetail),
                ("/api/ai", {}, wire.AiSettings),
                ("/api/copilot/threads", {}, wire.CopilotThreads),
                ("/api/link/devices", {}, wire.LinkDevices),
                ("/api/link/pending", {}, wire.LinkPending),
                ("/api/app-icon", {}, wire.AppIconPage),
                ("/api/server", {}, wire.ServerInfo),
                # G26: the dashboard's state and one query resolved over the record
                ("/api/dashboard/layouts", {}, wire.DashboardLayouts),
                ("/api/dashboard/layouts/main", {}, wire.DashboardLayout),
                ("/api/dashboard/query",
                 {"q": json.dumps({"source": "spending", "metric": "spend", "dimension": "month"})},
                 wire.WidgetAnswer),
                ("/api/dashboard/goals", {}, wire.DashboardGoals),
                ("/api/dashboard/allocation", {"month": "2026-08"}, wire.Allocation),
                ("/api/dashboard/game-pot", {}, wire.GamePot)]

    def test_every_endpoint_answers_in_its_generated_shape(self):
        # several handlers leave their connection to the garbage collector; that is not this test's subject
        self.enterContext(warnings.catch_warnings())
        warnings.simplefilter("ignore", ResourceWarning)
        met = set()
        for path, query, tp in self.answers():
            with self.subTest(path=path, query=query):
                self.assertEqual(problems(serve.answer(path, query), tp, path, met), [])
        # G17: the plan's write answers in the same shape, and so do the Net worth page's inputs
        with mock.patch("finance.stats_rebuild.refresh_stats"):
            saved = serve.write("/api/plan", {}, json.dumps(serve.answer("/api/plan", {})["plan"]).encode())
        self.assertEqual(problems(saved, wire.Plan, "POST /api/plan", met), [])
        self.assertEqual(problems(serve.answer("/api/growth", {})["inputs"], wire.GrowthInputs,
                                  "/api/growth.inputs", met), [])
        expected = set().union(*(reachable(tp) for tp in (*wire.ENDPOINTS.values(), wire.GrowthInputs)))
        self.assertEqual({t.__name__ for t in expected - met}, set(),
                         "the fixture never filled these, so they went unchecked")

    def test_needs_your_eye_answers_in_its_generated_shape(self):
        # G18: the rest of GET /api/overview is not generated yet. Today is December 2026, so the account is
        # late, and a one-off falls in Coming up's month (the record's last, September): every part of the
        # card has something to check
        self.enterContext(warnings.catch_warnings())
        warnings.simplefilter("ignore", ResourceWarning)
        self.enterContext(mock.patch.object(home, "date", December))
        offs = {"one-offs": {"data": {"one_offs": [{"date": "2026-09-15", "month": "2026-09", "payee": "Club",
                                                    "category": "Health", "amount_minor": 45_000,
                                                    "multiple": 6.1}]}}}
        self.enterContext(mock.patch("finance.results_cache.results", lambda req=None: {**RESULTS, **offs}))
        eye, met = serve.answer("/api/overview", {})["needs_your_eye"], set()
        self.assertEqual(problems(eye, wire.NeedsYourEye, "/api/overview.needs_your_eye", met), [])
        # the Rules page's total and the sidebar's count are one number
        self.assertEqual(serve.answer("/api/rules", {})["needs_total"], eye["needs_rule"])
        self.assertEqual({t.__name__ for t in reachable(wire.NeedsYourEye) - met}, set(),
                         "the fixture never filled these, so they went unchecked")
        self.assertEqual([line["kind"] for line in eye["lines"]],
                         ["scam", "upload", "needs_category", "recurring_off", "needs_rule", "one_off",
                          "stale_accounts", "pending", "irregular"])

    def test_the_rules_answer_groups_the_rows_no_rule_names_once(self):
        # G18: the list and its total come from one pass over the record
        self.enterContext(warnings.catch_warnings())
        warnings.simplefilter("ignore", ResourceWarning)
        spy = self.enterContext(mock.patch.object(rules_app, "_needs_groups", wraps=rules_app._needs_groups))
        rules = serve.answer("/api/rules", {})
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(rules["needs_total"], len(rules["needs"]))

    def test_coming_up_holds_an_extra_row_so_its_shape_is_checked(self):
        rows = serve.answer("/api/recurring", {})["coming_up"]["rows"]
        self.assertIn(("Gym", "extra"), {(r["payee"], r["status"]) for r in rows})

    def test_cost_type_and_its_deprecated_alias_agree(self):
        self.enterContext(warnings.catch_warnings())
        warnings.simplefilter("ignore", ResourceWarning)
        cats = [c for g in serve.answer("/api/categories", {})["groups"] for c in g["categories"]]
        cats += serve.answer("/api/plan", {})["categories"]
        self.assertTrue(cats)
        self.assertEqual([c["group"] for c in cats], [c["cost_type"] for c in cats])

    def test_endpoints_lists_every_answer_checked_here(self):
        def route(p):
            if p.startswith("/api/uploads/"):
                return "/api/uploads/<id>"
            if p.startswith("/api/dashboard/layouts/"):
                return "/api/dashboard/layouts/<id>"
            return p
        asked = {route(p) for p, _, _ in self.answers()}
        self.assertEqual(asked, set(wire.ENDPOINTS))
        for path, _, tp in self.answers():
            self.assertIs(wire.ENDPOINTS[route(path)], tp)


if __name__ == "__main__":
    unittest.main()
