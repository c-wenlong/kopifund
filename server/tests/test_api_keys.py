"""G22: where Settings keeps an AI provider's API key (server/finance/keys.py).

No test here, or anywhere, reaches the real Keychain: server/tests/isolate.py sets
KOPIFUND_NO_KEYCHAIN, which makes the Keychain backend refuse the real
`security` binary, and these tests hand it a fake runner instead."""
import isolate  # noqa: F401  first: no test writes the real record, cache, Keychain or .env
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from finance import keys

KEY = "sk-ant-api03-Abc_def-1234567890xyzWXYZ"


class FakeSecurity:
    """Stands in for `security`: keeps passwords by (account, service) and
    records every call's arguments and standard input."""

    def __init__(self):
        self.items, self.calls = {}, []

    def __call__(self, args, stdin=None):
        self.calls.append((list(args), stdin))
        if args == ["-i"]:
            for line in (stdin or "").splitlines():
                self.one(shlex.split(line))
            return subprocess.CompletedProcess(args, 0, "", "")
        return self.one(list(args))

    def one(self, argv):
        cmd, opts, i = argv[0], {}, 1
        while i < len(argv):
            if argv[i] in ("-U",):
                i += 1
                continue
            if argv[i] == "-w" and (i + 1 == len(argv)):
                opts["-w"] = True
                i += 1
                continue
            opts[argv[i]] = argv[i + 1]
            i += 2
        where = (opts.get("-a"), opts.get("-s"))
        if cmd == "add-generic-password":
            self.items[where] = opts["-w"]
            return subprocess.CompletedProcess(argv, 0, "", "")
        if cmd == "find-generic-password":
            if where not in self.items:
                return subprocess.CompletedProcess(argv, 44, "", "not found")
            return subprocess.CompletedProcess(argv, 0, self.items[where] + "\n", "")
        if cmd == "delete-generic-password":
            return subprocess.CompletedProcess(argv, 0 if self.items.pop(where, None) else 44, "", "")
        raise AssertionError(argv)


class KeychainTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeSecurity()
        self.chain = keys.Keychain(self.fake)

    def test_the_key_goes_on_standard_input_never_in_the_arguments(self):
        self.chain.save("anthropic", KEY)
        for args, _ in self.fake.calls:
            self.assertFalse(any(KEY in a for a in args), args)
        writes = [stdin for args, stdin in self.fake.calls if args == ["-i"]]
        self.assertEqual(len(writes), 1)
        self.assertIn(KEY, writes[0])
        self.assertTrue(writes[0].startswith("add-generic-password -U -a kopifund -s kopifund.ai.anthropic "))

    def test_saved_read_and_removed_under_its_own_service(self):
        self.chain.save("anthropic", f"  {KEY}\n")
        self.assertEqual(self.fake.items, {("kopifund", "kopifund.ai.anthropic"): KEY})
        self.assertEqual(self.chain.read("anthropic"), KEY)
        self.assertIsNone(self.chain.read("openai"))
        self.chain.remove("anthropic")
        self.chain.remove("anthropic")              # gone already is fine
        self.assertIsNone(self.chain.read("anthropic"))

    def test_a_write_the_keychain_did_not_keep_is_an_error(self):
        def refuses(args, stdin=None):
            return subprocess.CompletedProcess(args, 1 if args == ["-i"] else 44, "", "")
        with self.assertRaises(keys.KeyStoreError):
            keys.Keychain(refuses).save("anthropic", KEY)

    def test_the_real_security_binary_is_refused_under_the_tests(self):
        self.assertEqual(os.environ.get("KOPIFUND_NO_KEYCHAIN"), "1")
        with self.assertRaises(keys.KeyStoreError):
            keys.Keychain().read("anthropic")
        with self.assertRaises(keys.KeyStoreError):
            keys.Keychain().save("anthropic", KEY)
        self.assertFalse(keys.keychain_available())
        self.assertIsInstance(keys.store(), keys.EnvFile)


class EnvFileTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / ".env"
        self.env = keys.EnvFile(self.path)

    def test_a_new_file_is_mode_600(self):
        self.env.save("openai", KEY)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(self.path.read_text(), f"OPENAI_API_KEY={KEY}\n")
        self.assertEqual(self.env.read("openai"), KEY)

    def test_a_save_replaces_its_line_and_keeps_the_others(self):
        self.path.write_text("# mine\nNOTION_TOKEN=abc\nexport OPENAI_API_KEY=old-key-123\nOTHER=1\n"
                             "OPENAI_API_KEY=older-key-456\n")
        self.path.chmod(0o644)
        self.env.save("openai", KEY)
        self.assertEqual(self.path.read_text(), f"# mine\nNOTION_TOKEN=abc\nOPENAI_API_KEY={KEY}\nOTHER=1\n")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.env.remove("openai")
        self.assertEqual(self.path.read_text(), "# mine\nNOTION_TOKEN=abc\nOTHER=1\n")
        self.assertIsNone(self.env.read("openai"))

    def test_two_saves_at_once_keep_both_lines(self):
        # G22: two Settings tabs saving keys for two providers: each read-modify-write holds the lock
        read = keys.EnvFile._lines

        def slow(env):
            lines = read(env)
            time.sleep(0.2)              # the other save reads now, without the lock
            return lines
        with mock.patch.object(keys.EnvFile, "_lines", slow):
            saves = [threading.Thread(target=keys.EnvFile(self.path).save, args=(p, KEY)) for p in ("openai", "xai")]
            for t in saves:
                t.start()
            for t in saves:
                t.join()
        self.assertEqual(sorted(self.path.read_text().splitlines()), [f"OPENAI_API_KEY={KEY}", f"XAI_API_KEY={KEY}"])

    def test_a_save_in_another_process_waits_for_the_lock(self):
        root = Path(__file__).resolve().parent.parent
        code = "import sys; from finance import keys; keys.EnvFile(sys.argv[1]).save('xai', sys.argv[2])"
        with keys._locked(self.path):
            child = subprocess.Popen([sys.executable, "-c", code, str(self.path), KEY], cwd=root)
            self.addCleanup(child.wait)
            time.sleep(0.5)
            self.assertIsNone(child.poll())          # held off by the flock
            self.path.write_text(f"OPENAI_API_KEY={KEY}\n")
        self.assertEqual(child.wait(timeout=20), 0)
        self.assertEqual(self.path.read_text(), f"OPENAI_API_KEY={KEY}\nXAI_API_KEY={KEY}\n")

    def test_the_tests_env_file_is_a_throwaway_one(self):
        self.assertTrue(keys.env_file().is_relative_to(Path(os.environ["KOPIFUND_TEST_SCRATCH"])))


class CheckTest(unittest.TestCase):
    def test_a_pasted_key_is_trimmed(self):
        self.assertEqual(keys.check(f"\t{KEY} \n"), KEY)
        self.assertEqual(keys.last4(KEY), "WXYZ")

    def test_what_is_not_a_key_is_refused_in_words(self):
        for bad in ("", "   ", None, 42, "short", "x" * 401, "sk-has space-in-it", 'sk-"quoted"-1234', "sk-a\nb-12345"):
            with self.subTest(bad=bad), self.assertRaises(keys.KeyStoreError):
                keys.check(bad)


class StoreTest(unittest.TestCase):
    def test_remove_also_clears_the_data_folders_env_line(self):
        fake = keys.Keychain(FakeSecurity())
        before = keys.use(fake)
        self.addCleanup(keys.use, before)
        keys.EnvFile().save("xai", KEY)
        fake.save("xai", KEY)
        self.assertEqual(keys.read("xai"), (KEY, "keychain"))
        keys.remove("xai")
        self.assertEqual(keys.read("xai"), (None, None))
        self.assertIsNone(keys.EnvFile().read("xai"))


if __name__ == "__main__":
    unittest.main()
