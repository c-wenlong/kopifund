"""server/finance/migrate.py: the old finance-tracking folder into the data folder."""
import isolate  # noqa: F401  first: no test writes the real record or cache
import hashlib
import os
import tempfile
import tomllib
import unittest
from pathlib import Path

from finance import migrate

OLD_CONFIG = '''
[notion]
token_env = "NOTION_TOKEN"
token_command = "security find-generic-password -s example -w"

[accounts]
"111-11111-1" = "page-a"

[[folder]]
path = "data/dbs-statements/posb-everyday"
parser = "dbs"
account_no = "111-11111-1"

[[folder]]
path = "data/youtrip-statements"
parser = "youtrip"
account_no = "YT-1"
per_currency_subfolders = ["sgd-main", "nok"]

[[folder]]
path = "data/revolut"
parser = "revolut"
account_no = "RV-1"
multi_currency = true

[[folder]]
path = "data/wise"
parser = "wise"
account_no = "WS-1"
multi_currency = true

[analytics]
tiger_folder = "data/tiger-brokers"

[plan]
dca = 100

[[plan.trips]]
name = "Somewhere"
from = "2027-01-01"
'''

SOURCE = '''# header
dbs-statements/dbs-debit-cards:
  what: debit card statements (Mastercard 1234, Visa 5678), one PDF per month
  active: false
youtrip-statements:
  what: wallet statements
  active: true
receipts/payment-advice:
  what: advices
  reference: someone@example.edu > Search
cache:
  what: generated
'''

FILES = {
    "data/dbs-statements/posb-everyday/2025/1_2.pdf": b"%PDF posb jan feb",
    "data/dbs-statements/posb-everyday/2025/1_2.txt": b"dump",
    "data/dbs-statements/posb-everyday/2026/8.pdf": b"%PDF posb aug",
    "data/youtrip-statements/sgd-main/2026/8.pdf": b"%PDF yt sgd",
    "data/youtrip-statements/nok/2025/7.pdf": b"%PDF yt nok",
    "data/revolut/account-statement_2024-06-10_2026-09-06_en-sg_abc123.pdf": b"%PDF rv",
    "data/wise/statement_123456_SGD_2024-07-06_2026-09-06.pdf": b"%PDF ws sgd",
    "data/wise/statement_123456_USD_2024-07-06_2026-09-06.pdf": b"%PDF ws usd",
    "data/tiger-brokers/2026/1-8.pdf": b"%PDF tiger",
    "data/cpf-statements/2025/2025.pdf": b"%PDF cpf",
    "data/receipts/notes.txt": b"typed by hand",
    "data/invoices/2026/a (1).pdf": b"%PDF invoice",
    "data/garden-club/2025/dues.pdf": b"%PDF dues",
    "data/cache/snapshot-2026-09-04.json": b"{}",
    "data/test-fixtures/card.txt": b"real",
    "data/.DS_Store": b"x",
    "data/receipts/._notes.txt": b"resource fork",
    "notes/.obsidian/workspace.json": b"{}",
    "data/odd.bin": b"?",
    "data/account-files/notion-token.txt": b"secret_in_a_folder",
    "data/SOURCE.yml": SOURCE.encode(),
    "rules/00-keys.csv": b"match_on,pattern\n",
    "rules/20-people.csv": b"match_on,pattern\n",
    "rules/10-types.csv": b"shared, stays in the repo\n",
    "notes/profile.md": b"# me",
    "finance/config.toml": OLD_CONFIG.encode(),
    ".env": b"NOTION_TOKEN=secret",
}


def tree_hash(root):
    h = hashlib.sha256()
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


class MigrateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = Path(self.tmp.name).resolve() / "old"
        self.new = Path(self.tmp.name) / "new"
        for rel, body in FILES.items():
            f = self.old / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(body)

    def tearDown(self):
        self.tmp.cleanup()

    def migrate(self):
        p = migrate.plan(self.old, self.new)
        return p, migrate.run(p)

    def test_statements_get_canonical_names(self):
        p, _ = self.migrate()
        dst = {str(s.dst) for s in p.of("copy")}
        for want in ("statements/posb-everyday/2025/2025-01_2025-02.pdf",
                     "statements/posb-everyday/2026/2026-08.pdf",
                     "statements/youtrip-sgd/2026/2026-08.pdf",
                     "statements/youtrip-nok/2025/2025-07.pdf",
                     "statements/revolut/2026/export-2026-09-06.pdf",
                     "statements/wise-sgd/2026/export-2026-09-06.pdf",
                     "statements/wise-usd/2026/export-2026-09-06.pdf",
                     "statements/tiger/2026/2026-01_2026-08.pdf",
                     "statements/cpf/2025/2025.pdf",
                     "documents/receipts/notes.txt",
                     "documents/invoices/2026/a (1).pdf",
                     "rules/keys.csv", "rules/people.csv", "notes/profile.md"):
            self.assertIn(want, dst)

    def test_every_copy_matches_its_original(self):
        p, (copied, verified, there) = self.migrate()
        self.assertEqual(copied, len(p.of("copy")))
        self.assertEqual(verified, copied)
        for s in p.of("copy"):
            self.assertEqual(migrate.sha256(s.src), migrate.sha256(self.new / s.dst))

    def test_the_old_folder_is_only_read(self):
        before = tree_hash(self.old)
        self.migrate()
        self.assertEqual(before, tree_hash(self.old))

    def test_derived_secret_and_fixture_files_are_skipped_with_a_reason(self):
        p, _ = self.migrate()
        skipped = {str(s.src.relative_to(self.old)): s.why for s in p.of("skip")}
        self.assertIn("text dump", skipped["data/dbs-statements/posb-everyday/2025/1_2.txt"])
        self.assertIn("derived", skipped["data/cache/snapshot-2026-09-04.json"])
        self.assertIn("real statements", skipped["data/test-fixtures/card.txt"])
        self.assertIn("Keychain", skipped[".env"])
        self.assertIn("Keychain", skipped["data/account-files/notion-token.txt"])
        self.assertFalse((self.new / "documents/account-files/notion-token.txt").exists())
        self.assertFalse((self.new / ".env").exists())
        self.assertNotIn("rules/10-types.csv", {str(s.src.relative_to(self.old)) for s in p.steps})

    def test_any_other_folder_is_copied_to_documents_under_its_own_name(self):
        p, _ = self.migrate()
        dst = {str(s.src.relative_to(self.old)): str(s.dst) for s in p.of("copy")}
        self.assertEqual(dst["data/garden-club/2025/dues.pdf"],
                         "documents/garden-club/2025/dues.pdf")
        self.assertEqual(migrate.sha256(self.new / "documents/garden-club/2025/dues.pdf"),
                         migrate.sha256(self.old / "data/garden-club/2025/dues.pdf"))

    def test_an_unknown_file_is_reported_not_copied(self):
        p, _ = self.migrate()
        self.assertEqual([s.src.name for s in p.of("unmapped")], ["odd.bin"])
        self.assertIn("unmapped data/odd.bin", migrate.report(p))

    def test_running_twice_copies_nothing_new(self):
        self.migrate()
        p, (copied, _, there) = self.migrate()
        self.assertEqual(copied, 0)
        self.assertEqual(there, len(p.of("copy")) + len(p.of("rewrite")))

    def test_a_different_file_already_there_stops_the_run(self):
        target = self.new / "statements/cpf/2025/2025.pdf"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"something else")
        p, result = self.migrate()
        self.assertEqual(result, (0, 0, 0))
        self.assertTrue(any("different content" in x for x in p.problems))
        self.assertFalse((self.new / "statements/posb-everyday").exists())

    def test_files_are_private(self):
        self.migrate()
        self.assertEqual(os.stat(self.new).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(self.new / "statements/cpf/2025/2025.pdf").st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.new / "config.toml").st_mode & 0o777, 0o600)

    def test_every_folder_made_below_the_root_is_private(self):
        self.migrate()
        for d in ("statements/cpf", "statements/cpf/2025", "statements/wise-sgd/2026"):
            self.assertEqual(os.stat(self.new / d).st_mode & 0o777, 0o700, d)

    def test_hidden_files_are_skipped_and_named(self):
        p, _ = self.migrate()
        skipped = {str(s.src.relative_to(self.old)) for s in p.of("skip")}
        self.assertIn("data/receipts/._notes.txt", skipped)
        self.assertIn("notes/.obsidian/workspace.json", skipped)
        self.assertFalse((self.new / "documents/receipts/._notes.txt").exists())
        self.assertFalse((self.new / "notes/.obsidian").exists())
        self.assertIn("data/receipts/._notes.txt", migrate.report(p))

    def test_config_folders_follow_the_new_layout(self):
        self.migrate()
        cfg = tomllib.loads((self.new / "config.toml").read_text())
        paths = {f["path"]: f for f in cfg["folder"]}
        self.assertEqual(set(paths), {"statements/posb-everyday", "statements/youtrip-sgd",
                                      "statements/youtrip-nok", "statements/revolut",
                                      "statements/wise-sgd", "statements/wise-usd"})
        self.assertEqual(paths["statements/youtrip-nok"]["wallet"], "NOK")
        self.assertNotIn("per_currency_subfolders", paths["statements/youtrip-sgd"])
        self.assertEqual(paths["statements/wise-usd"]["wallet"], "USD")
        self.assertEqual(cfg["analytics"]["tiger_folder"], "statements/tiger")
        self.assertEqual(cfg["plan"]["trips"][0]["name"], "Somewhere")
        self.assertEqual(cfg["notion"]["token_command"], "security find-generic-password -s example -w")

    def test_sources_lose_card_digits_and_emails(self):
        self.migrate()
        text = (self.new / "SOURCES.yml").read_text()
        self.assertNotIn("1234", text)
        self.assertNotIn("@example.edu", text)
        self.assertIn("youtrip-sgd:", text)
        self.assertIn("youtrip-nok:", text)
        self.assertNotIn("cache:", text)


class NamingTest(unittest.TestCase):
    def test_month_names(self):
        self.assertEqual(migrate.canonical_month_name("2026", "8"), "2026-08")
        self.assertEqual(migrate.canonical_month_name("2025", "1_2"), "2025-01_2025-02")
        self.assertEqual(migrate.canonical_month_name("2024", "1-12"), "2024-01_2024-12")
        self.assertIsNone(migrate.canonical_month_name("2024", "M.25"))

    def test_statement_month(self):
        self.assertEqual(migrate.statement_month("2025-01_2025-02"), "2025-02")
        self.assertEqual(migrate.statement_month("export-2026-09-06"), "2026-09")
        self.assertEqual(migrate.statement_month("2025"), "")


if __name__ == "__main__":
    unittest.main()
