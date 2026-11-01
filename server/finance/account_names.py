"""How an account is named wherever the app shows one (G13).

A statement prints the full account or card number, and the record keeps it:
the loader keys accounts and period blocks on it. The app never shows it. Every
page and every stat names an account by `describe`: the name the record holds
with any number taken out, else the upload picker's name for its statement
folder, else its institution. A number is shown, when at all, through `mask`,
which keeps the last four digits ("•• 4821"). `bank` picks the logo the web
app draws (apps/web/src/components/BankMark.tsx).

G21: the banks, the words that give each away and the names of their
statement folders are read from the layout registry
(server/finance/parsers/layouts.py), where each parser module declares them, so a new
bank is named and marked here with no edit. That registry is stdlib only and
reads no data folder, and nothing else of the app is imported, so the API, the
loader and the stats can all use this.
"""
import re

from .parsers import layouts

# the name of an account with nothing better to go by
GENERIC = "Account"

# every bank the web app draws a mark for (server/finance/types.py makes the Bank
# type of them), and "other" for an account no bank's words find
BANKS = tuple(b.key for b in layouts.banks()) + ("other",)


def folder_titles():
    """{folder slug: the app's name for an account kept in it}, from each
    layout's statement folder less a wallet's currency (youtrip-{ccy} is
    youtrip, "YouTrip"). A slug not here is title-cased."""
    return {lay.folder.replace("-{ccy}", ""): lay.folder_title
            for lay in layouts.every() if lay.folder_title}


def _words():
    """(bank key, pattern) in the order the patterns are tried."""
    return [(b.key, b.words) for b in layouts.banks()]


# a run that reads as an account or card number: six digits or more, which may
# be split by dashes or single spaces and led by a short letter prefix
# (Y-0000000000). Only names are cut with it: a sentence holds dates and
# figures, so a sentence is cut by the numbers the record knows (`scrub`).
_LONG = re.compile(r"(?<![\w.])(?:[A-Za-z]{1,3}-?)?\d(?:[ -]?\d){5,}(?![\w.])")
_DATE = re.compile(r"\d{4}-\d{2}(?:-\d{2})?")
_LEFT_OVER = re.compile(r"(?i)\s*(?:\b(?:no|nr)\.?|\bnumber|#)\s*:?\s*$")
_EDGES = " \t·•|,:;/#-–"


def digits(text):
    return re.sub(r"\D", "", str(text or ""))


def mask(number):
    """The last four digits of an account or card number: "•• 4821", or ""
    when there is no number."""
    d = digits(number)
    return f"•• {d[-4:]}" if d else ""


def _forms(number):
    """The spellings of one number worth looking for: as stored, and as bare
    digits when that is long enough to be the number rather than part of a
    word ("A1" is looked for only as A1)."""
    n = str(number or "").strip()
    out = {n} if n else set()
    d = digits(n)
    if len(d) >= 6:
        out.add(d)
    return sorted(out, key=len, reverse=True)


def _pattern(form):
    d = digits(form)
    if form == d and len(d) >= 6:
        # bare digits are read through any dash or single space a line splits
        # them by: a number kept as 1234567890 is found printed 123-456789-0.
        # A split spelling that runs on through a dash or a space into more
        # digits is part of a longer number ("1234 5678 9012"), and is left.
        split = r"[ -]?".join(d)
        return re.compile(rf"(?<![\w])(?:{d}(?![\w])|(?<!\d[ -]){split}(?![\w])(?![ -]\d))")
    return re.compile(rf"(?<![\w]){re.escape(form)}(?![\w])")


def strip_number(name, number=""):
    """`name` with `number`, and any other run that reads as an account or
    card number, taken out, and the separators it leaves tidied away.
    "YouTrip wallet · Y-0000000000" is "YouTrip wallet"."""
    text = str(name or "")
    for form in _forms(number):
        text = _pattern(form).sub(" ", text)
    text = _LONG.sub(lambda m: m.group(0) if _DATE.fullmatch(m.group(0)) else " ", text)
    text = re.sub(r"\(\s*\)|\[\s*\]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(_EDGES)
    text = _LEFT_OVER.sub("", text).strip(_EDGES)
    # a separator left doubled where the number sat between two words
    text = re.sub(r"\s*([·|,])(?:\s*[·|,])+\s*", r" \1 ", text)
    return re.sub(r"\s+", " ", text).strip(_EDGES).strip()


def _get(obj, key, default=""):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _slug(folder):
    """The folder's own name less a wallet's currency: youtrip-sek is youtrip."""
    slug = str(_get(folder, "path") or "").rstrip("/").rsplit("/", 1)[-1]
    wallet = str(_get(folder, "wallet") or "").lower()
    return slug[: -len(wallet) - 1] if wallet and slug.endswith(f"-{wallet}") else slug


def folder_key(folder):
    """What the upload picker groups a folder under: its path less a wallet's
    currency, so statements/youtrip-sek and statements/youtrip-usd are one
    account, statements/youtrip (apps/web/src/accounts.ts does the same cut)."""
    path = str(_get(folder, "path") or "").rstrip("/")
    wallet = str(_get(folder, "wallet") or "").lower()
    return path[: -len(wallet) - 1] if wallet and path.endswith(f"-{wallet}") else path


def folder_name(folder):
    """The upload picker's name for a statement folder."""
    slug = _slug(folder)
    return folder_titles().get(slug) or " ".join(w[:1].upper() + w[1:] for w in slug.split("-") if w)


def folder_bank(folder):
    """The bank key for a statement folder's mark: its layout's bank, the
    layout told by the folder's own name where one parser reads several (POSB
    and PayLah! statements go through the DBS parser)."""
    lay = layouts.of_folder(str(_get(folder, "parser") or ""), _slug(folder))
    known = {bk.key for bk in layouts.banks()}
    return lay.bank if lay is not None and lay.bank in known else "other"


def _folder_for(number, folders):
    number = str(number or "")
    if not number:
        return None
    return next((f for f in folders or () if str(_get(f, "account_no") or "") == number), None)


def bank(name="", number="", institution=None, folders=()):
    """The bank key for the logo: from the statement folder that names this
    number, else from the words in the institution and the name."""
    folder = _folder_for(number, folders)
    if folder is not None and folder_bank(folder) != "other":
        return folder_bank(folder)
    text = f"{institution or ''} {name or ''}".lower()
    return next((key for key, word in _words() if re.search(word, text)), "other")


def display_name(name="", number="", institution=None, folders=()):
    """A name to show for one account, with no number in it."""
    own = strip_number(name, number)
    if own:
        return own
    folder = _folder_for(number, folders)
    if folder is not None and folder_name(folder):
        return folder_name(folder)
    return strip_number(institution, number) or GENERIC


def describe(accounts, folders=()):
    """[{name, label, bank, number}] for each account (dicts with name,
    account_no and institution), in the same order. `number` is the masked
    number, for a page that shows it beside the name. `label` is the name
    alone where nothing else tells the account apart, and the name with its
    masked number where something must: an account with nothing better than
    "Account" to go by, or two accounts that would show one name. A table
    keyed by label never folds two accounts into one."""
    out = []
    for a in accounts:
        no = _get(a, "account_no") or ""
        out.append({"name": display_name(_get(a, "name"), no, _get(a, "institution", None), folders),
                    "bank": bank(_get(a, "name"), no, _get(a, "institution", None), folders),
                    "number": mask(no)})
    seen = {}
    for d in out:
        seen[d["name"]] = seen.get(d["name"], 0) + 1
    for d in out:
        alike = seen[d["name"]] > 1 or d["name"] == GENERIC
        d["label"] = f"{d['name']} {d['number']}" if alike and d["number"] else d["name"]
    # two numbers can end in the same four digits: those are counted apart
    labels = unique({i: d["label"] for i, d in enumerate(out)})
    for i, d in enumerate(out):
        d["label"] = labels[i]
    return out


def unique(names, reserved=()):
    """{key: name} with every name distinct from the others and from
    `reserved`, in key order: a repeat gets " (2)", " (3)" after it."""
    taken, out = set(reserved), {}
    for key in sorted(names, key=str):
        base = name = names[key]
        n = 1
        while name in taken:
            n += 1
            name = f"{base} ({n})"
        taken.add(name)
        out[key] = name
    return out


def label(name="", number="", institution=None, folders=()):
    """`describe`'s label for one account on its own."""
    return describe([{"name": name, "account_no": number, "institution": institution}], folders)[0]["label"]


def by_number(accounts, folders=()):
    """{account number: label} for the accounts that have a number."""
    return {_get(a, "account_no"): d["label"] for a, d in zip(accounts, describe(accounts, folders))
            if _get(a, "account_no")}


def record_names(conn, folders=()):
    """`by_number` over the record's accounts (an open kopifund.db)."""
    return by_number([dict(r) for r in conn.execute(
        "SELECT name, account_no, institution FROM account ORDER BY name")], folders)


def own_numbers(conn=None, cfg=None):
    """Every account number the owner's record (an open kopifund.db) or config
    knows, as stored: the numbers `scrub` masks in what a page shows. A figure
    or someone else's account is never among them."""
    numbers = set()
    if conn is not None:
        numbers |= {r[0] for r in conn.execute(
            "SELECT account_no FROM account WHERE account_no != '' "
            "UNION SELECT account_no FROM period WHERE account_no != ''")}
    if cfg is not None:
        numbers |= set(cfg.accounts) | {f.account_no for f in cfg.folders}
    return {str(n) for n in numbers if n}


def scrub(text, numbers):
    """`text` with every spelling of each of `numbers` replaced by its mask.
    For sentences, where a figure or a date must survive: only the numbers
    the record knows are touched."""
    text = str(text)
    have = digits(text)
    for number in sorted({str(n) for n in numbers if n}, key=len, reverse=True):
        if number not in text and (not digits(number) or digits(number) not in have):
            continue
        for form in _forms(number):
            text = _pattern(form).sub(mask(number), text)
    return text


def scrub_tree(obj, numbers, keep=()):
    """A JSON value with `scrub` applied to every string and every key in it.
    Two keys that mask to one (two numbers ending in the same four digits)
    are counted apart, as `unique` does, so no value is dropped. The value
    under a key in `keep` passes as it is, at any depth: one a page sends
    back as it came (a needs-rule group's text becomes a rule's pattern)."""
    numbers = [n for n in numbers if n]
    if not numbers:
        return obj

    def walk(x):
        if isinstance(x, str):
            return scrub(x, numbers)
        if isinstance(x, list):
            return [walk(v) for v in x]
        if isinstance(x, dict):
            out = {}
            for k, v in x.items():
                if k in keep:
                    out[k] = v
                    continue
                key = walk(k) if isinstance(k, str) else k
                if key in out and isinstance(key, str):
                    n, base = 2, key
                    while f"{base} ({n})" in out or f"{base} ({n})" in x:
                        n += 1
                    key = f"{base} ({n})"
                out[key] = walk(v)
            return out
        return x
    return walk(obj)
