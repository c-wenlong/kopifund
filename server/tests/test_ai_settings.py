"""G22: the AI card on Settings: the provider and models saved in the record
over [ai] in config.toml, where the key is found, the models list and Test.
No test calls the network or the Keychain: the provider is a fake, and the
key store a `.env` in a throwaway folder."""
import isolate  # noqa: F401  first: no test writes the real record, cache, Keychain or .env
import json
import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from finance import ai, copilot, db, keys
from finance import types as wire
from test_types import problems

KEY = "sk-saved-in-settings-0001"


class Folder(unittest.TestCase):
    """A throwaway data folder with its own record, cache and key file, and no
    API key a developer's shell exports."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"KOPIFUND_DATA": str(self.root), "KOPIFUND_DB": str(self.root / "kopifund.db"),
                                           "KOPIFUND_CACHE": str(self.root / "cache"),
                                           "KOPIFUND_ENV_FILE": str(self.root / "keys.env")})
        env.start()
        self.addCleanup(env.stop)
        for name in [n for n in os.environ if n.endswith("_API_KEY") or n == "ANTHROPIC_WORKSPACE_ID"]:
            del os.environ[name]
        self.conn = db.connect()
        self.addCleanup(self.conn.close)

    def cfg(self, section=None):
        return SimpleNamespace(root=self.root, ai=section, folders=[], accounts={})

    def pick(self, **data):
        ai.save_settings(self.conn, data)


class MergeTest(Folder):
    def test_off_until_settings_or_config_names_a_provider(self):
        self.assertIsNone(ai.settings(self.cfg(), self.conn))
        self.assertFalse(ai.view(self.cfg(), self.conn)["enabled"])
        self.pick(provider="ollama", model="qwen3")
        s = ai.settings(self.cfg(), self.conn)
        self.assertEqual((s["provider"], s["model"], s["base_url"]), ("ollama", "qwen3", "http://localhost:11434/v1"))
        self.assertTrue(ai.view(self.cfg(), self.conn)["enabled"])

    def test_settings_win_and_drop_what_config_said_for_another_provider(self):
        section = {"provider": "openai", "model": "gpt-x", "base_url": "https://proxy.example.test/v1",
                   "key_env": "MY_OPENAI", "send_amounts": False}
        self.pick(provider="anthropic")
        s = ai.settings(self.cfg(section), self.conn)
        self.assertEqual((s["provider"], s["format"], s["base_url"], s["model"]),
                         ("anthropic", "anthropic", "https://api.anthropic.com/v1", "claude-haiku-5-5"))
        self.assertNotIn("key_env", s)
        self.assertFalse(s["send_amounts"])                # not a provider's setting: kept
        self.assertFalse(ai.view(self.cfg(section), self.conn)["from_config"])

    def test_the_same_provider_keeps_configs_url_and_a_saved_model_wins(self):
        section = {"provider": "openai", "model": "gpt-x", "base_url": "https://proxy.example.test/v1"}
        self.pick(provider="openai", model="gpt-y")
        s = ai.settings(self.cfg(section), self.conn)
        self.assertEqual((s["base_url"], s["model"]), ("https://proxy.example.test/v1", "gpt-y"))

    def test_config_alone_still_works_and_says_so(self):
        v = ai.view(self.cfg({"provider": "deepseek", "model": "deepseek-chat"}), self.conn)
        self.assertEqual((v["enabled"], v["provider"], v["model"], v["from_config"]), (True, "deepseek", "deepseek-chat", True))

    def test_the_copilot_model(self):
        self.pick(provider="anthropic")
        self.assertEqual(ai.settings(self.cfg(), self.conn)["copilot_model"], "claude-opus-5-5")
        self.pick(copilot_model="claude-sonnet-5-5")
        self.assertEqual(ai.settings(self.cfg(), self.conn)["copilot_model"], "claude-sonnet-5-5")
        self.pick(provider="deepseek", copilot_model=None)
        s = ai.settings(self.cfg(), self.conn)
        self.assertEqual((s["model"], s["copilot_model"]), ("deepseek-chat", "deepseek-chat"))
        v = ai.view(self.cfg(), self.conn)
        self.assertEqual((v["model"], v["copilot_model"]), ("", ""))

    def test_turning_ai_off_wins_over_config_until_a_provider_is_picked_again(self):
        section = {"provider": "deepseek", "model": "deepseek-chat"}
        for off in (None, "", "off"):
            with self.subTest(off=off):
                self.pick(provider="anthropic", model="claude-haiku-5-5")
                self.pick(provider=off)
                self.assertEqual(self.conn.execute("SELECT value FROM setting WHERE key = 'ai.provider'").fetchone()[0],
                                 '"off"')
                self.assertIsNone(ai.settings(self.cfg(section), self.conn))
                v = ai.view(self.cfg(section), self.conn)
                self.assertEqual((v["enabled"], v["provider"], v["from_config"], v["model_in_use"],
                                  v["copilot_model_in_use"]), (False, None, False, "", ""))
                self.assertEqual(problems(v, wire.AiSettings), [])
                self.assertEqual(ai.suggest(self.cfg(section), [{"text": "X", "field": "merchant"}], [], self.conn),
                                 {"enabled": False, "suggestions": []})
                with self.assertRaisesRegex(ai.AIRefused, "off"):
                    copilot.start(self.cfg(section), self.conn, {"question": "Hi"})
        self.pick(provider="deepseek")
        self.assertEqual(ai.settings(self.cfg(section), self.conn)["provider"], "deepseek")
        self.assertTrue(ai.view(self.cfg(section), self.conn)["enabled"])

    def test_config_enabled_false_is_off_when_settings_picked_nothing(self):
        section = {"provider": "deepseek", "model": "deepseek-chat", "enabled": False}
        self.assertIsNone(ai.settings(self.cfg(section), self.conn))
        v = ai.view(self.cfg(section), self.conn)
        self.assertEqual((v["enabled"], v["model_in_use"], v["copilot_model_in_use"]), (False, "", ""))
        with self.assertRaisesRegex(ai.AIRefused, "off"):
            copilot.start(self.cfg(section), self.conn, {"question": "Hi"})
        self.pick(provider="deepseek")
        self.assertTrue(ai.view(self.cfg(section), self.conn)["enabled"])

    def test_each_caller_needs_only_its_own_model(self):
        # G22: an Ask Kopifund model alone runs the copilot; payee suggestions refuse without theirs
        self.pick(provider="openai", copilot_model="gpt-copilot", send_rows=True)
        keys.save("openai", KEY)
        s = ai.settings(self.cfg(), self.conn)
        self.assertEqual((s["model"], s["copilot_model"]), ("", "gpt-copilot"))
        v = ai.view(self.cfg(), self.conn)
        self.assertEqual((v["model"], v["copilot_model"], v["model_in_use"], v["copilot_model_in_use"]),
                         ("", "gpt-copilot", "", "gpt-copilot"))
        events = copilot.start(self.cfg(), self.conn, {"question": "Hi"})      # not refused
        events.close()
        with self.assertRaisesRegex(ai.AIError, "Payee suggestions need a model"):
            ai.suggest(self.cfg(), [{"text": "TOAST BOX", "field": "merchant", "rows": 1, "amount": 500}],
                       ["Eating out"], self.conn)
        seen = []

        def post(url, headers, body):
            seen.append(body["model"])
            return {"choices": [{"message": {"content": "OK"}}]}
        self.assertTrue(ai.try_provider(self.cfg(), self.conn, post=post)["ok"])
        self.assertEqual(seen, ["gpt-copilot"])
        # Ollama with no model at all: the copilot refuses in words before anything streams
        self.pick(provider="ollama", copilot_model=None)
        with self.assertRaisesRegex(ai.AIRefused, "Ask Kopifund needs a model"):
            copilot.start(self.cfg(), self.conn, {"question": "Hi"})
        self.pick(model="qwen3")
        s = ai.settings(self.cfg(), self.conn)
        self.assertEqual((s["model"], s["copilot_model"]), ("qwen3", "qwen3"))

    def test_the_card_shows_the_models_in_use_beside_the_owners_choice(self):
        self.pick(provider="anthropic")
        v = ai.view(self.cfg(), self.conn)
        self.assertEqual((v["model"], v["copilot_model"], v["model_in_use"], v["copilot_model_in_use"]),
                         ("", "", "claude-haiku-5-5", "claude-opus-5-5"))
        self.pick(provider="openai", model="gpt-x")
        v = ai.view(self.cfg({"provider": "openai", "copilot_model": "gpt-big"}), self.conn)
        self.assertEqual((v["model"], v["copilot_model"], v["model_in_use"], v["copilot_model_in_use"]),
                         ("gpt-x", "", "gpt-x", "gpt-big"))
        self.assertEqual(problems(v, wire.AiSettings), [])

    def test_local_is_decided_by_the_url_not_the_provider(self):
        for url, local in (("http://localhost:11434/v1", True), ("http://127.0.0.1:8080/v1", True),
                           ("http://[::1]:11434/v1", True), ("http://gpu-box.example.test:11434/v1", False),
                           ("https://ollama.example.test/v1", False), ("http://localhost.example.test/v1", False),
                           ("http://10.0.0.5:11434/v1", False), ("", False)):
            with self.subTest(url=url):
                self.assertEqual(ai.is_local({"provider": "ollama", "base_url": url}), local)
        self.assertTrue(ai.is_local({"provider": "openai", "base_url": "http://localhost:8000/v1"}))
        remote = {"provider": "ollama", "model": "qwen3", "base_url": "http://gpu-box.example.test:11434/v1"}
        self.pick(provider="ollama", send_rows=False)
        v = ai.view(self.cfg(remote), self.conn)
        ollama = next(p for p in v["providers"] if p["name"] == "ollama")
        self.assertEqual((v["local"], ollama["local"], ollama["label"]), (False, False, "Ollama"))
        # a remote Ollama gets rows only with the owner's consent, like any provider off this machine
        with self.assertRaisesRegex(ai.AIRefused, "Send matching rows to Ollama"):
            copilot.start(self.cfg(remote), self.conn, {"question": "Hi"})
        self.assertEqual(ai.find_key(ai.settings(self.cfg(remote), self.conn), self.root), "")   # Ollama takes no key
        self.pick(model="qwen3")
        v = ai.view(self.cfg(), self.conn)
        ollama = next(p for p in v["providers"] if p["name"] == "ollama")
        self.assertEqual((v["local"], ollama["local"], ollama["label"]), (True, True, "Ollama (on this Mac)"))
        events = copilot.start(self.cfg(), self.conn, {"question": "Hi"})      # on this Mac: no consent needed
        events.close()

    def test_a_custom_provider_from_config_stays_named(self):
        section = {"provider": "lab", "base_url": "https://llm.example.test/v1", "model": "m1", "key_env": "LAB_KEY"}
        v = ai.view(self.cfg(section), self.conn)
        self.assertEqual((v["provider"], v["enabled"], v["from_config"], v["model"], v["model_in_use"], v["local"]),
                         ("lab", True, True, "m1", "m1", False))
        self.assertEqual(problems(v, wire.AiSettings), [])
        # with no base_url it is no provider Kopifund can reach
        self.assertIsNone(ai.view(self.cfg({"provider": "lab", "model": "m1"}), self.conn)["provider"])

    def test_a_custom_provider_is_config_tomls_and_picking_it_uses_config_toml(self):
        # G22: a custom provider is managed in config.toml; Settings can only hand back to it
        section = {"provider": "lab", "base_url": "http://127.0.0.1:8080/v1", "model": "m1"}
        cfg = self.cfg(section)
        self.assertEqual(ai.view(cfg, self.conn)["config_provider"], "lab")
        self.assertTrue(ai.view(cfg, self.conn)["local"])
        ai.save_settings(self.conn, {"provider": None}, cfg)
        v = ai.view(cfg, self.conn)
        self.assertEqual((v["enabled"], v["provider"], v["config_provider"]), (False, None, "lab"))
        self.assertEqual(problems(v, wire.AiSettings), [])
        # picking it again turns AI back on with config.toml's settings, and clears the saved "off"
        ai.save_settings(self.conn, {"provider": "lab", "send_rows": True}, cfg)
        self.assertIsNone(self.conn.execute("SELECT value FROM setting WHERE key = 'ai.provider'").fetchone())
        v = ai.view(cfg, self.conn)
        self.assertEqual((v["enabled"], v["provider"], v["from_config"], v["model_in_use"], v["send_rows"], v["local"]),
                         (True, "lab", True, "m1", True, True))
        # from a built-in picked in Settings too, and a saved model never reaches a custom provider
        ai.save_settings(self.conn, {"provider": "openai", "model": "gpt-x", "copilot_model": "gpt-y"}, cfg)
        self.assertEqual(ai.settings(cfg, self.conn)["provider"], "openai")
        ai.save_settings(self.conn, {"provider": "lab"}, cfg)
        s = ai.settings(cfg, self.conn)
        self.assertEqual((s["provider"], s["model"], s["copilot_model"], s["base_url"]),
                         ("lab", "m1", "m1", "http://127.0.0.1:8080/v1"))
        self.assertEqual(ai.saved_settings(self.conn).get("model"), None)
        ai.save_settings(self.conn, {"model": "stray"}, cfg)
        self.assertEqual(ai.settings(cfg, self.conn)["model"], "m1")
        self.assertEqual(ai.view(cfg, self.conn)["model"], "m1")
        # no other custom name is taken, and without config.toml not this one either
        for data, c in (({"provider": "other"}, cfg), ({"provider": "lab"}, self.cfg())):
            with self.subTest(data=data), self.assertRaises(ai.AIRefused):
                ai.save_settings(self.conn, data, c)
        # the key and models endpoints are for providers Kopifund lists
        for call in (lambda: ai.save_key(cfg, self.conn, {"provider": "lab", "key": KEY}),
                     lambda: ai.models(cfg, self.conn, "lab")):
            with self.assertRaises(ai.AIRefused):
                call()

    def test_picking_config_tomls_built_in_provider_hands_back_to_it_with_saved_models(self):
        section = {"provider": "openai", "model": "gpt-x", "base_url": "https://proxy.example.test/v1"}
        cfg = self.cfg(section)
        self.assertEqual(ai.view(cfg, self.conn)["config_provider"], "openai")
        ai.save_settings(self.conn, {"provider": "anthropic", "model": "claude-haiku-5-5"}, cfg)
        ai.save_settings(self.conn, {"provider": "openai", "model": "gpt-y", "copilot_model": ""}, cfg)
        self.assertIsNone(self.conn.execute("SELECT value FROM setting WHERE key = 'ai.provider'").fetchone())
        s = ai.settings(cfg, self.conn)
        self.assertEqual((s["provider"], s["model"], s["copilot_model"], s["base_url"]),
                         ("openai", "gpt-y", "gpt-y", "https://proxy.example.test/v1"))
        v = ai.view(cfg, self.conn)
        self.assertEqual((v["from_config"], v["model"], v["model_in_use"]), (True, "gpt-y", "gpt-y"))
        # a provider sent alone drops the models picked for the one before
        ai.save_settings(self.conn, {"provider": "anthropic", "model": "claude-haiku-5-5"}, cfg)
        ai.save_settings(self.conn, {"provider": "openai"}, cfg)
        self.assertEqual(ai.settings(cfg, self.conn)["model"], "gpt-x")
        # no [ai] provider of its own names none
        for section in (None, {}, {"provider": "auto"}, {"provider": "openai", "enabled": False},
                        {"provider": "lab", "model": "m1"}):
            with self.subTest(section=section):
                self.assertIsNone(ai.view(self.cfg(section), self.conn)["config_provider"])

    def test_saved_models_stay_with_the_provider_they_were_picked_for(self):
        # G22: models saved for config.toml's OpenAI never reach Ollama when [ai] changes provider
        ai.save_settings(self.conn, {"model": "gpt-y", "copilot_model": "gpt-z"},
                         self.cfg({"provider": "openai", "model": "gpt-x"}))
        self.assertEqual(ai.settings(self.cfg({"provider": "openai", "model": "gpt-x"}), self.conn)["model"], "gpt-y")
        moved = self.cfg({"provider": "ollama", "model": "qwen3"})
        s = ai.settings(moved, self.conn)
        self.assertEqual((s["model"], s["copilot_model"]), ("qwen3", "qwen3"))
        v = ai.view(moved, self.conn)
        self.assertEqual((v["model"], v["copilot_model"], v["model_in_use"]), ("qwen3", "", "qwen3"))
        # and models picked with one saved provider do not follow a switch to another
        ai.save_settings(self.conn, {"provider": "openai", "model": "gpt-y"})
        ai.save_settings(self.conn, {"provider": "deepseek"})
        s = ai.settings(self.cfg(), self.conn)
        self.assertEqual((s["provider"], s["model"]), ("deepseek", "deepseek-chat"))
        self.assertEqual(ai.view(self.cfg(), self.conn)["model"], "")
        # the card cannot write the server's own note
        with self.assertRaises(ai.AIRefused):
            ai.save_settings(self.conn, {"models_for": "openai"})

    def test_bad_settings_are_refused_in_words(self):
        for data in ({"provider": "skynet"}, {"send_rows": "yes"}, {"model": "two words"}, {"colour": "red"},
                     {"model": 5}):
            with self.subTest(data=data), self.assertRaises(ai.AIRefused):
                ai.save_settings(self.conn, data)

    def test_the_consent_switch_is_saved_under_its_own_key(self):
        self.pick(send_rows=True)
        self.assertEqual(self.conn.execute("SELECT value FROM setting WHERE key = 'copilot.send_rows'").fetchone()[0],
                         "true")
        self.assertTrue(ai.view(self.cfg(), self.conn)["send_rows"])
        self.assertTrue(ai.saved_settings()["send_rows"])      # read-only, with no connection


class KeyOrderTest(Folder):
    def where(self, section):
        s = ai.settings(self.cfg(section), self.conn)
        return ai.locate_key(s, self.root)

    def test_key_env_then_key_command_then_settings_then_the_usual_variables(self):
        section = {"provider": "openai", "model": "gpt-x", "key_env": "MY_KEY", "key_command": "echo from-command"}
        os.environ["OPENAI_API_KEY"] = "from-shell"
        keys.save("openai", KEY)
        os.environ["MY_KEY"] = "from-key-env"
        self.addCleanup(os.environ.pop, "MY_KEY", None)
        self.assertEqual(self.where(section), ("from-key-env", "environment"))
        del os.environ["MY_KEY"]
        self.assertEqual(self.where(section), ("from-command", "key_command"))
        del section["key_command"]
        self.assertEqual(self.where(section), (KEY, "env_file"))
        keys.remove("openai")
        self.assertEqual(self.where(section), ("from-shell", "environment"))
        del os.environ["OPENAI_API_KEY"]
        self.assertEqual(self.where(section), (None, "none"))

    def test_the_card_sees_where_the_key_is_and_its_last_four_never_the_key(self):
        self.pick(provider="openai", model="gpt-x")
        keys.save("openai", KEY)
        v = ai.view(self.cfg(), self.conn)
        self.assertEqual(v["key"], {"where": "env_file", "last4": "0001", "can_remove": True})
        self.assertNotIn(KEY, json.dumps(v))
        self.assertEqual(problems(v, wire.AiSettings), [])
        os.environ["OPENAI_API_KEY"] = "sk-exported-9999"
        keys.remove("openai")
        self.assertEqual(ai.view(self.cfg(), self.conn)["key"],
                         {"where": "environment", "last4": "9999", "can_remove": False})

    def test_a_key_file_in_the_data_folder_is_found_too(self):
        (self.root / ".env").write_text("DEEPSEEK_API_KEY=sk-dotenv-7777\n")
        self.pick(provider="deepseek")
        self.assertEqual(ai.view(self.cfg(), self.conn)["key"]["last4"], "7777")

    def test_save_and_remove_through_the_card(self):
        self.pick(provider="anthropic")
        ai.save_key(self.cfg(), self.conn, {"provider": "anthropic", "key": f" {KEY} "})
        self.assertEqual(keys.read("anthropic"), (KEY, "env_file"))
        with self.assertRaises(ai.AIRefused):
            ai.save_key(self.cfg(), self.conn, {"provider": "anthropic", "key": ""})
        with self.assertRaises(ai.AIRefused):
            ai.save_key(self.cfg(), self.conn, {"provider": "ollama", "key": KEY})
        ai.remove_key(self.cfg(), self.conn, {"provider": "anthropic"})
        self.assertEqual(keys.read("anthropic"), (None, None))

    def test_the_payee_suggestions_honour_a_provider_picked_in_settings(self):
        self.pick(provider="deepseek")
        keys.save("deepseek", KEY)
        calls = []

        def fake(url, headers, body):
            calls.append((url, headers))
            return {"choices": [{"message": {"content": json.dumps({"suggestions": []})}}]}
        with mock.patch.object(ai, "_post", fake):
            out = ai.suggest(self.cfg(), [{"text": "TOAST BOX", "field": "merchant", "rows": 1, "amount": 500}],
                             ["Eating out"], self.conn)
        self.assertEqual(out["provider"], "deepseek")
        self.assertEqual(calls[0], ("https://api.deepseek.com/v1/chat/completions", {"authorization": f"Bearer {KEY}"}))


class Pages:
    """A fake GET for the models endpoints: Anthropic's paged list or one OpenAI-style page."""

    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def __call__(self, url, headers):
        self.calls.append((url, headers))
        return self.pages[len(self.calls) - 1]


ANTHROPIC = [
    {"data": [{"id": "claude-haiku-5-5", "display_name": "Claude Haiku 5.5", "created_at": "2026-05-01T00:00:00Z"},
              {"id": "claude-opus-5-5", "display_name": "Claude Opus 5.5", "created_at": "2026-08-01T00:00:00Z"}],
     "has_more": True, "last_id": "claude-opus-5-5"},
    {"data": [{"id": "claude-sonnet-5-5", "display_name": "Claude Sonnet 5.5", "created_at": "2026-07-01T00:00:00Z"}],
     "has_more": False, "last_id": "claude-sonnet-5-5"},
]


class ModelsTest(Folder):
    def setUp(self):
        super().setUp()
        self.pick(provider="anthropic")
        keys.save("anthropic", KEY)

    def models(self, get, q="", now=1_000_000.0, provider=None):
        return ai.models(self.cfg(), self.conn, provider, q, get=get, now=now)

    def test_pages_through_and_sorts_newest_first(self):
        get = Pages(ANTHROPIC)
        out = self.models(get)
        self.assertEqual([m["id"] for m in out["models"]], ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"])
        self.assertEqual(out["models"][0], {"id": "claude-opus-5-5", "label": "Claude Opus 5.5"})
        self.assertIn("after_id=claude-opus-5-5", get.calls[1][0])
        self.assertEqual(get.calls[0][1]["x-api-key"], KEY)
        self.assertEqual(problems(out, wire.AiModels), [])

    def test_kept_a_day_and_filtered_here(self):
        get = Pages(ANTHROPIC * 2)
        self.models(get)
        out = self.models(get, q="SONNET", now=1_000_000.0 + 3600)
        self.assertEqual([m["id"] for m in out["models"]], ["claude-sonnet-5-5"])
        self.assertEqual(len(get.calls), 2)                    # from the cache
        self.models(get, now=1_000_000.0 + 24 * 3600)
        self.assertEqual(len(get.calls), 4)                    # a day on: asked again

    def test_openai_style_lists_sort_by_date_or_id(self):
        self.pick(provider="openai")
        keys.save("openai", KEY)
        get = Pages([{"data": [{"id": "b-model", "created": 100}, {"id": "a-model", "created": 200},
                               {"id": "c-model"}]}])
        out = self.models(get)
        self.assertEqual([m["id"] for m in out["models"]], ["a-model", "b-model", "c-model"])
        self.assertEqual(get.calls[0], ("https://api.openai.com/v1/models", {"authorization": f"Bearer {KEY}"}))

    def test_no_key_or_a_refusal_is_an_empty_list_with_the_reason(self):
        out = self.models(Pages([]), provider="xai")
        self.assertEqual((out["models"], out["fetched_at"]), ([], None))
        self.assertIn("No API key", out["error"])

        def refuses(url, headers):
            raise ai.AIError(f"The AI provider said 401: bad key {KEY}")
        out = self.models(refuses)
        self.assertEqual((out["models"], out["fetched_at"]), ([], None))
        self.assertNotIn(KEY, out["error"])
        self.assertEqual(problems(out, wire.AiModels), [])


class TryTest(Folder):
    def test_a_reply_is_ok_with_its_time(self):
        self.pick(provider="anthropic")
        keys.save("anthropic", KEY)
        ticks = iter([10.0, 10.25])
        seen = []

        def post(url, headers, body):
            seen.append(body)
            return {"type": "message", "content": [{"type": "text", "text": "OK"}], "stop_reason": "end_turn"}
        out = ai.try_provider(self.cfg(), self.conn, post=post, clock=lambda: next(ticks))
        self.assertEqual(out, {"ok": True, "message": "Anthropic answered with claude-haiku-5-5 in 250 ms", "ms": 250})
        self.assertEqual(problems(out, wire.AiTest), [])

    def test_a_failure_says_why_without_the_key(self):
        self.pick(provider="openai", model="gpt-x")
        keys.save("openai", KEY)

        def post(url, headers, body):
            raise ai.AIError(f"The AI provider said 401: Incorrect API key provided: {KEY}")
        out = ai.try_provider(self.cfg(), self.conn, post=post)
        self.assertFalse(out["ok"])
        self.assertNotIn(KEY, out["message"])
        self.assertIsNone(out["ms"])
        self.pick(provider=None)
        self.assertEqual(ai.try_provider(self.cfg(), self.conn)["message"], "Pick a provider first")


if __name__ == "__main__":
    unittest.main()
