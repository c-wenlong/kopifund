"""Move a finance-tracking folder into a Kopifund data folder.

    python3 server/finance.py migrate --from "<old finance folder>" [--dry-run]

SPEC.md, "The data folder", says what goes where; this module is that table as
code. The old folder is only ever read. Every file that is copied is checked by
sha256 against its original, and every file that is not copied is named in the
report with the reason, so nothing goes missing in silence.

Running it twice is safe: a target that already holds the same bytes is left
alone, and a target that holds different bytes stops the run before anything
is written.
"""
import hashlib
import os
import re
import shutil
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .paths import data_root

# old YouTrip wallet folder -> currency
YOUTRIP_WALLETS = {"sgd-main": "sgd", "sgd": "sgd", "sek": "sek", "usd": "usd",
                   "eur": "eur", "myr": "myr", "aud": "aud", "nok": "nok", "dkk": "dkk"}
# the old data/ folders that hold statements; statement_target names their files.
# Any other top-level folder holds documents with no parser (bills, invoices,
# receipts, whatever the owner kept) and is copied to documents/ as it is.
STATEMENT_FOLDERS = ("dbs-statements", "youtrip-statements", "revolut", "wise",
                     "tiger-brokers", "cpf-statements")
SKIP_NAMES = {".DS_Store"}


@dataclass
class Step:
    """One file and what happens to it."""
    action: str                 # copy | skip | rewrite | unmapped
    src: Path
    dst: Path | None = None
    why: str = ""


@dataclass
class Plan:
    old: Path
    new: Path
    steps: list = field(default_factory=list)
    problems: list = field(default_factory=list)

    def of(self, action):
        return [s for s in self.steps if s.action == action]


# ---------------------------------------------------------------- naming

def _month(year, m):
    return f"{int(year):04d}-{int(m):02d}"


def canonical_month_name(year, stem):
    """`8` -> `2026-08`; `1_2` or `1-12` -> `2026-01_2026-02`; None if neither."""
    if re.fullmatch(r"\d{1,2}", stem):
        return _month(year, stem)
    m = re.fullmatch(r"(\d{1,2})[_-](\d{1,2})", stem)
    if m:
        return f"{_month(year, m.group(1))}_{_month(year, m.group(2))}"
    return None


def statement_target(rel):
    """The data-folder path for a statement PDF at `rel` (relative to the old
    data/ folder), or (None, reason)."""
    parts = rel.parts
    name, stem = rel.name, rel.stem
    if parts[0] == "dbs-statements" and len(parts) == 4:
        canon = canonical_month_name(parts[2], stem)
        if canon:
            return Path("statements", parts[1], parts[2], canon + ".pdf"), ""
    if parts[0] == "youtrip-statements" and len(parts) == 4:
        ccy = YOUTRIP_WALLETS.get(parts[1].lower())
        canon = canonical_month_name(parts[2], stem)
        if ccy and canon:
            return Path("statements", f"youtrip-{ccy}", parts[2], canon + ".pdf"), ""
    if parts[0] == "revolut" and len(parts) == 2:
        m = re.match(r"account-statement_\d{4}-\d{2}-\d{2}_(\d{4})-(\d{2})-(\d{2})", name)
        if m:
            y, mo, d = m.groups()
            return Path("statements", "revolut", y, f"export-{y}-{mo}-{d}.pdf"), ""
    if parts[0] == "wise" and len(parts) == 2:
        m = re.match(r"statement_\d+_([A-Z]{3})_\d{4}-\d{2}-\d{2}_(\d{4})-(\d{2})-(\d{2})", name)
        if m:
            ccy, y, mo, d = m.groups()
            return Path("statements", f"wise-{ccy.lower()}", y, f"export-{y}-{mo}-{d}.pdf"), ""
    if parts[0] == "tiger-brokers" and len(parts) == 3:
        canon = canonical_month_name(parts[1], stem)
        if canon and "_" not in canon:
            canon = f"{canon}_{canon}"
        if canon:
            return Path("statements", "tiger", parts[1], canon + ".pdf"), ""
    if parts[0] == "cpf-statements" and len(parts) == 3 and re.fullmatch(r"\d{4}", stem):
        return Path("statements", "cpf", parts[1], f"{stem}.pdf"), ""
    return None, "no naming rule for this statement"


# ---------------------------------------------------------------- planning

def plan(old, new=None):
    """Every file under the old folder that matters, and what happens to it."""
    old = Path(old).expanduser().resolve()
    new = Path(new).expanduser() if new else data_root()
    p = Plan(old=old, new=new)
    data = old / "data"
    if not data.is_dir():
        p.problems.append(f"{data} is not a folder; is --from the finance-tracking folder?")
        return p

    for f in sorted(data.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(data)
        top = rel.parts[0]
        if any(part.startswith(".") for part in rel.parts) or f.name in SKIP_NAMES:
            p.steps.append(Step("skip", f, why="hidden file (macOS metadata and the like)"))
        elif top == "cache":
            p.steps.append(Step("skip", f, why="derived: rebuilt from the database"))
        elif top == "test-fixtures":
            p.steps.append(Step("skip", f, why="copies of real statements; the repo has scrubbed fixtures"))
        elif top.startswith("backup-"):
            p.steps.append(Step("skip", f, why="an old backup copy"))
        elif rel == Path("SOURCE.yml"):
            p.steps.append(Step("rewrite", f, Path("SOURCES.yml"),
                                "per account, without card digits or email addresses"))
        elif f.suffix.lower() == ".txt" and f.with_suffix(".pdf").exists():
            p.steps.append(Step("skip", f, why="text dump: regenerated into cache/text"))
        elif f.name == "notion-token.txt":
            # a secret wherever it sits, so it is checked before the documents
            p.steps.append(Step("skip", f, why="a secret: the token belongs in the Keychain"))
        elif len(rel.parts) > 1 and top not in STATEMENT_FOLDERS:
            p.steps.append(Step("copy", f, Path("documents", *rel.parts)))
        elif f.suffix.lower() == ".pdf":
            dst, why = statement_target(rel)
            p.steps.append(Step("copy", f, dst) if dst else Step("unmapped", f, why=why))
        else:
            p.steps.append(Step("unmapped", f, why="not a statement or a document"))

    for old_name, new_name in (("00-keys.csv", "keys.csv"), ("20-people.csv", "people.csv")):
        f = old / "rules" / old_name
        if f.is_file():
            p.steps.append(Step("copy", f, Path("rules", new_name)))
    notes = old / "notes"
    if notes.is_dir():
        for f in sorted(notes.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(notes)
            if any(part.startswith(".") for part in rel.parts) or f.name in SKIP_NAMES:
                p.steps.append(Step("skip", f, why="hidden file (macOS metadata and the like)"))
            else:
                p.steps.append(Step("copy", f, Path("notes", *rel.parts)))
    cfg = old / "finance" / "config.toml"
    if cfg.is_file():
        p.steps.append(Step("rewrite", cfg, Path("config.toml"),
                            "folder paths rewritten to the new layout"))
    env = old / ".env"
    if env.is_file():
        p.steps.append(Step("skip", env, why="a secret: the token is read from the Keychain "
                                             "([notion] token_command), or put .env in the data folder"))

    seen = {}
    for s in p.steps:
        if s.dst is None:
            continue
        if s.dst in seen:
            p.problems.append(f"two files map to {s.dst}: {seen[s.dst]} and {s.src}")
        seen[s.dst] = s.src
        target = new / s.dst
        if s.action == "copy" and target.exists() and sha256(target) != sha256(s.src):
            p.problems.append(f"{target} already exists with different content")
    return p


# ---------------------------------------------------------------- rewriting

def rewrite_config(text, wise_wallets=()):
    """The old config.toml with its [[folder]] paths moved to the new layout,
    one folder per YouTrip and Wise wallet, and the Tiger and CPF folders
    pointed at statements/. `wise_wallets` are the currencies the Wise export
    held, read off its file names."""
    raw = tomllib.loads(text)
    folders = []
    for f in raw.get("folder", []):
        path = f["path"]
        base = path.removeprefix("data/")
        if base.startswith("dbs-statements/"):
            folders.append({**f, "path": "statements/" + base.split("/", 1)[1]})
        elif base == "youtrip-statements":
            for wallet in f.get("per_currency_subfolders", []):
                ccy = YOUTRIP_WALLETS.get(wallet.lower(), wallet.lower())
                entry = {k: v for k, v in f.items() if k != "per_currency_subfolders"}
                folders.append({**entry, "path": f"statements/youtrip-{ccy}", "wallet": ccy.upper()})
        elif base == "revolut":
            folders.append({**f, "path": "statements/revolut"})
        elif base == "wise":
            for ccy in wise_wallets:
                entry = {k: v for k, v in f.items() if k not in ("wallets", "multi_currency")}
                folders.append({**entry, "path": f"statements/wise-{ccy.lower()}", "wallet": ccy.upper()})
        else:
            folders.append(f)
    raw["folder"] = folders
    analytics = raw.setdefault("analytics", {})
    for key, value in (("tiger_folder", "statements/tiger"), ("cpf_folder", "statements/cpf")):
        if analytics.get(key, "x") != "":
            analytics[key] = value
    header = ("# Kopifund settings, written by `finance.py migrate` on "
              f"{date.today().isoformat()}.\n# Folder paths are relative to the data folder. "
              "Comments from the old file were not carried over.\n\n")
    return header + to_toml(raw)


def to_toml(table, prefix=""):
    """A small TOML writer for the shapes config.toml uses: scalars, lists of
    scalars, tables and arrays of tables."""
    lines, subtables, arrays = [], [], []
    for k, v in table.items():
        if isinstance(v, dict):
            subtables.append((k, v))
        elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            arrays.append((k, v))
        else:
            lines.append(f"{_key(k)} = {_value(v)}")
    out = "\n".join(lines) + ("\n" if lines else "")
    for k, v in subtables:
        name = f"{prefix}{_key(k)}"
        body = to_toml(v, name + ".")
        out += f"\n[{name}]\n{body}"
    for k, items in arrays:
        name = f"{prefix}{_key(k)}"
        for item in items:
            out += f"\n[[{name}]]\n{to_toml(item, name + '.')}"
    return out


def _key(k):
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else _value(str(k))


def _value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (date,)):
        return v.isoformat()
    if isinstance(v, list):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    s = str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{s}"'


EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
CARD_DIGITS = re.compile(r"\s*\((?:[A-Za-z]+\s+\d{4}(?:,\s*)?)+\)")


def rewrite_sources(text, new_root):
    """SOURCE.yml as SOURCES.yml: one entry per new statement folder, with card
    digits and email addresses removed, and `active` recomputed for each wallet
    from the newest statement it holds."""
    entries = _read_simple_yaml(text)
    out = ["# Where each account's statements come from. Written by `finance.py migrate`;",
           "# no card digits or email addresses belong here.", ""]
    stmts = Path(new_root) / "statements"
    mapping = {"dbs-statements/dbs-multiplier": ["dbs-multiplier"],
               "dbs-statements/posb-everyday": ["posb-everyday"],
               "dbs-statements/dbs-debit-cards": ["dbs-debit-cards"],
               "dbs-statements/dbs-paylah": ["dbs-paylah"],
               "revolut": ["revolut"], "tiger-brokers": ["tiger"], "cpf-statements": ["cpf"]}
    for old_key, fields in entries.items():
        if old_key in ("cache", "test-fixtures", "notion-token.txt"):
            continue
        if old_key == "youtrip-statements":
            targets = sorted(p.name for p in stmts.glob("youtrip-*")) if stmts.is_dir() else []
        elif old_key == "wise":
            targets = sorted(p.name for p in stmts.glob("wise-*")) if stmts.is_dir() else []
        elif old_key in mapping:
            targets = mapping[old_key]
        else:
            targets = ["documents/" + old_key]
        for t in targets:
            out.append(f"{t}:")
            for k, v in fields.items():
                if k == "active" and t.startswith(("youtrip-", "wise-")):
                    v = "true" if _recent(stmts / t) else "false"
                    v += "   # recomputed per wallet: a statement in the last four months"
                v = CARD_DIGITS.sub("", EMAIL.sub("<your email>", v))
                out.append(f"  {k}: {v}")
            out.append("")
    return "\n".join(out)


def statement_month(stem):
    """The last month a canonical statement name covers: `2026-08`,
    `2025-01_2025-02` -> `2025-02`, `export-2026-09-06` -> `2026-09`, else ''."""
    m = re.search(r"(\d{4}-\d{2})(?:-\d{2})?$", stem)
    return m.group(1) if m else ""


def _recent(folder, months=4):
    newest = max((statement_month(p.stem) for p in folder.rglob("*.pdf")), default="") \
        if folder.is_dir() else ""
    if not newest:
        return False
    today = date.today()
    y, m = today.year, today.month - months
    while m <= 0:
        y, m = y - 1, m + 12
    return newest >= f"{y:04d}-{m:02d}"


def _read_simple_yaml(text):
    """The flat two-level shape SOURCE.yml uses: `key:` then `  field: value`."""
    out, current = {}, None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" ") and line.rstrip().endswith(":"):
            current = line.rstrip()[:-1].strip()
            out[current] = {}
        elif current and ":" in line:
            k, v = line.strip().split(":", 1)
            out[current][k.strip()] = v.strip()
    return out


# ---------------------------------------------------------------- running

def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(p, dry_run=False):
    """Carry out a plan. Returns (copied, verified, already_there)."""
    if p.problems:
        return 0, 0, 0
    copied = verified = there = 0
    if not dry_run:
        _private_dir(p.new)
        for sub in ("statements", "documents", "rules", "notes", "exports", "backups", "cache"):
            _private_dir(p.new / sub)
    for s in p.of("copy"):
        target = p.new / s.dst
        if target.exists():
            there += 1
            continue
        if dry_run:
            continue
        _private_dir(target.parent, p.new)
        shutil.copyfile(s.src, target)
        os.chmod(target, 0o600)
        copied += 1
        if sha256(target) != sha256(s.src):
            raise RuntimeError(f"sha256 mismatch after copying {s.src} to {target}")
        verified += 1
    if not dry_run:
        for s in p.of("rewrite"):
            target = p.new / s.dst
            if target.exists():
                there += 1
                continue
            text = s.src.read_text(encoding="utf-8")
            wise = sorted({c.dst.parts[1].removeprefix("wise-") for c in p.of("copy")
                           if c.dst.parts[:1] == ("statements",) and c.dst.parts[1].startswith("wise-")})
            body = (rewrite_config(text, wise) if s.dst.name == "config.toml"
                    else rewrite_sources(text, p.new))
            target.write_text(body, encoding="utf-8")
            os.chmod(target, 0o600)
    return copied, verified, there


def _private_dir(d, root=None):
    """Make `d` and set it to mode 700, along with every folder between it and
    `root` that mkdir(parents=True) may have made with the default mode."""
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    if root is not None:
        for parent in d.parents:
            if parent == root or root not in parent.parents:
                break
            os.chmod(parent, 0o700)


def _shown(path, old):
    try:
        return str(path.relative_to(old))
    except ValueError:
        return str(path)


def report(p, result=None, dry_run=False):
    """The run as text: every file not copied is named with its reason."""
    lines = [f"migrate {p.old} -> {p.new}{'  (dry run)' if dry_run else ''}", ""]
    if p.problems:
        lines += ["stopped, nothing written:"] + [f"  {x}" for x in p.problems]
        return "\n".join(lines)
    copies = p.of("copy")
    lines.append(f"copy     {len(copies)} files "
                 f"({sum(1 for s in copies if s.dst.parts[0] == 'statements')} statements, "
                 f"{sum(1 for s in copies if s.dst.parts[0] == 'documents')} documents, "
                 f"{sum(1 for s in copies if s.dst.parts[0] == 'rules')} rule files, "
                 f"{sum(1 for s in copies if s.dst.parts[0] == 'notes')} notes)")
    for s in p.of("rewrite"):
        lines.append(f"rewrite  {s.src.relative_to(p.old)} -> {s.dst}: {s.why}")
    skipped = p.of("skip")
    reasons = {}
    for s in skipped:
        reasons.setdefault(s.why, []).append(s)
    lines.append(f"skip     {len(skipped)} files")
    for why, steps in sorted(reasons.items()):
        lines.append(f"  {len(steps):4d}  {why}")
        lines += [f"        {_shown(s.src, p.old)}" for s in steps]
    for s in p.of("unmapped"):
        lines.append(f"unmapped {s.src.relative_to(p.old)}: {s.why} (not copied)")
    if result:
        copied, verified, there = result
        lines += ["", f"copied {copied}, verified by sha256 {verified}, already there {there}"]
    lines += ["", f"the old folder {p.old} was only read"]
    return "\n".join(lines)
