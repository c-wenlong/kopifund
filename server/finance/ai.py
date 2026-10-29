"""AI suggestions for the payees no rule names (G12).

After an upload check, the app asks a language model to name each merchant no
rule matched and pick its category. The answer is only a suggestion: nothing
reaches the rules or the record until the user ticks it and saves, and a saved
suggestion is an ordinary personal rule with `[ai]` in its notes.

What leaves the machine, per payee: the merchant text the statement prints
and, when [ai] send_amounts is on, a rounded typical amount ("about $5"). Never
a date, an exact amount, an account number, or a counterparty or memo line,
which name people.

Two wire formats cover the providers: Anthropic's Messages API, and the
OpenAI-style chat completions endpoint that OpenAI, xAI, Gemini, DeepSeek,
Qwen, Moonshot, Zhipu, OpenRouter and Ollama all accept. stdlib only, like the
Notion client. Answers are cached by provider, model and text in the data
folder's cache, so a second check of the same statement asks nothing.
"""
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from . import dotenv, keys
from .paths import cache_dir, data_root

# provider -> (wire format, base URL, the variables its key usually lives in,
# the model used when [ai] names none). [ai] base_url overrides the URL; a
# provider not listed needs base_url and model. A default model is set only
# where the name is stable; the others need [ai] model.
PROVIDERS = {
    "anthropic": ("anthropic", "https://api.anthropic.com/v1", ("ANTHROPIC_API_KEY",), "claude-haiku-5-5"),
    "openai": ("openai", "https://api.openai.com/v1", ("OPENAI_API_KEY",), ""),
    "xai": ("openai", "https://api.x.ai/v1", ("XAI_API_KEY", "GROK_API_KEY"), ""),
    "gemini": ("openai", "https://generativelanguage.googleapis.com/v1beta/openai",
               ("GEMINI_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY", "GOOGLE_API_KEY"), ""),
    "deepseek": ("openai", "https://api.deepseek.com/v1", ("DEEPSEEK_API_KEY",), "deepseek-chat"),
    "qwen": ("openai", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
             ("DASHSCOPE_API_KEY", "QWEN_API_KEY"), ""),
    "moonshot": ("openai", "https://api.moonshot.ai/v1", ("MOONSHOT_API_KEY", "KIMI_API_KEY"), ""),
    "zhipu": ("openai", "https://open.bigmodel.cn/api/paas/v4", ("ZHIPU_API_KEY", "ZHIPUAI_API_KEY"), ""),
    "zai": ("openai", "https://api.z.ai/api/paas/v4", ("ZAI_API_KEY",), ""),
    "mistral": ("openai", "https://api.mistral.ai/v1", ("MISTRAL_API_KEY",), ""),
    "groq": ("openai", "https://api.groq.com/openai/v1", ("GROQ_API_KEY",), ""),
    "together": ("openai", "https://api.together.xyz/v1", ("TOGETHER_API_KEY", "TOGETHER_AI_API_KEY"), ""),
    "fireworks": ("openai", "https://api.fireworks.ai/inference/v1",
                  ("FIREWORKS_API_KEY", "FIREWORKS_AI_API_KEY"), ""),
    "cerebras": ("openai", "https://api.cerebras.ai/v1", ("CEREBRAS_API_KEY",), ""),
    "openrouter": ("openai", "https://openrouter.ai/api/v1", ("OPENROUTER_API_KEY",), ""),
    "ollama": ("openai", "http://localhost:11434/v1", (), ""),
}
# G22: the names the Settings card shows
LABELS = {"anthropic": "Anthropic", "openai": "OpenAI", "xai": "xAI", "gemini": "Google Gemini",
          "deepseek": "DeepSeek", "qwen": "Qwen", "moonshot": "Moonshot (Kimi)", "zhipu": "Zhipu",
          "zai": "Z.ai", "mistral": "Mistral", "groq": "Groq", "together": "Together AI",
          "fireworks": "Fireworks AI", "cerebras": "Cerebras", "openrouter": "OpenRouter",
          "ollama": "Ollama"}
# G22: the copilot's model when neither Settings nor [ai] names one; a provider
# not listed uses the payee suggestions model
COPILOT_DEFAULTS = {"anthropic": "claude-opus-5-5"}
# G22: Anthropic models that take the server-side refusal fallback on api.anthropic.com
FALLBACK_MODELS = frozenset({"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"})
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# G22: the copilot asks Claude for its thinking summarized (the newest models
# default to "omitted", which returns thinking blocks with no text). Claude
# models before Opus 4.6 and Sonnet 4.6 do not take adaptive thinking, and a
# model that is not Claude (an Anthropic-style endpoint of another provider)
# may not either, so they get no `thinking` field.
THINKING = {"type": "adaptive", "display": "summarized"}
_CLAUDE = re.compile(r"(?:^|[./])claude-")
_BEFORE_ADAPTIVE = re.compile(r"(?:^|[./])claude-(?:\d|instant|(?:opus|sonnet|haiku)-4(?:-[0-5])?(?:-\d{8})?(?:$|[@:]|-(?!\d)))")
ANTHROPIC_VERSION = "2023-06-01"
KINDS = ("Merchant", "Institution")
TIMEOUT = 90
CHAT_TIMEOUT = 300      # G22: one copilot round may think for a while before it answers
MAX_PAYEES = 60

SYSTEM = """You name the merchants on a Singapore resident's bank and card \
statements so a finance app can file their spending.

You get a list of statement texts, each with an id and sometimes a rough \
typical amount. For each one, answer with:
- payee: the business as people know it, in normal capitalisation ("Toast Box", \
"Challenger", "SP Group"). Drop branch names, store numbers, card ids and \
country codes.
- category: exactly one name from the category list, or "" when you cannot tell.
- kind: "Merchant" for a shop or service, "Institution" for a bank, insurer, \
government body or school.
- confident: true only when you recognise the business and the category is clear.
- note: when not confident, a few words on what it might be; otherwise "".

Never invent a business you do not recognise: give your best payee from the \
text, category "", confident false.

Reply with JSON only, in this shape:
{"suggestions": [{"id": 1, "payee": "...", "category": "...", "kind": "Merchant", \
"confident": true, "note": ""}]}"""


class AIError(Exception):
    """The provider could not be reached or gave no usable answer, in words for the app."""


class AIRefused(ValueError):
    """G22: an AI setting or key the app will not take, with the reason (a 422)."""


def _key_files(ai):
    """[ai] key_file, one path or a list: files of `NAME=value` lines (an
    `export` in front is fine), such as the one a shell profile sources."""
    files = ai.get("key_file") or []
    return [Path(f).expanduser() for f in ([files] if isinstance(files, str) else files)]


def _sources(ai, root):
    """Where keys are looked for, in order, each with the place it reports
    (types.KeyPlace): the environment, `.env` in the data folder, then each
    [ai] key_file. Each is a mapping, read when asked for."""
    yield "environment", os.environ
    yield "env_file", dotenv.read(root / ".env")
    for f in _key_files(ai):
        yield "key_file", dotenv.read(f)


def _lookup(name, ai, root, sources=None):
    """(value, place) of one variable from the first source that has it, or
    None. `sources`, a list from _sources, saves reading the files again."""
    return next(((src[name], place) for place, src in (sources or _sources(ai, root)) if src.get(name)), None)


def _detected(provider, ai, root, sources=None):
    """(key, place) for `provider` from the variables it usually lives in, then
    the `<PROVIDER>_API_KEY` every provider tends to use, or None. Every name is
    tried in the environment before any file, so a key exported now beats an
    old one on disk. `sources` as for _lookup."""
    names = PROVIDERS.get(provider, ("", "", (), ""))[2] + (f"{provider.upper()}_API_KEY",)
    names = tuple(dict.fromkeys(names))
    for place, src in (sources or _sources(ai, root)):
        key = next((src[n] for n in names if src.get(n)), None)
        if key:
            return key, place
    return None


# ---------------------------------------------------------------- G22: Settings over [ai]

# the record's `setting` keys the AI card writes, by the name the card sends
SETTING_KEYS = {"provider": "ai.provider", "model": "ai.model", "copilot_model": "ai.copilot_model",
                "send_rows": "copilot.send_rows", "models_for": "ai.models_for"}
# G22: the fields the card sends; `models_for` is the server's own note of the
# provider the saved models were picked for, so they never reach another one
CARD_FIELDS = ("provider", "model", "copilot_model", "send_rows")
# G22: `ai.provider` holding this turns AI off, whatever [ai] in config.toml says
OFF = "off"
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
# what a provider picked in Settings drops from an [ai] section written for another provider
_PROVIDER_FIELDS = ("format", "base_url", "model", "copilot_model", "key_env", "key_command", "workspace_id")


def saved_settings(conn=None):
    """{provider, model, copilot_model, send_rows, off} as the record's
    `setting` table holds them; a key not saved is left out. `off` is True
    when Settings turned AI off (`ai.provider` is "off"). Without `conn` the
    record is opened read-only, and a data folder with no record has saved
    nothing."""
    names = {v: k for k, v in SETTING_KEYS.items()}
    marks = ", ".join("?" * len(names))
    if conn is None:
        from .db import db_path
        path = db_path()
        if not path.exists():
            return {}
        try:
            ro = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        except sqlite3.Error:
            return {}
        try:
            rows = ro.execute(f"SELECT key, value FROM setting WHERE key IN ({marks})", list(names)).fetchall()
        except sqlite3.Error:
            rows = []
        finally:
            ro.close()
    else:
        rows = [tuple(r) for r in conn.execute(f"SELECT key, value FROM setting WHERE key IN ({marks})",
                                               list(names))]
    out = {}
    for key, value in rows:
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            pass
        out[names[key]] = value
    out["off"] = out.get("provider") == OFF
    if out.get("provider") not in PROVIDERS:
        out.pop("provider", None)
    out["send_rows"] = out.get("send_rows") is True
    return out


def _root(cfg):
    return Path(cfg.root) if cfg is not None and getattr(cfg, "root", None) else data_root()


def _saved_key(provider):
    try:
        return keys.read(provider)[0]
    except keys.KeyStoreError:
        return None


def settings(cfg, conn=None, saved=None):
    """[ai] from config.toml with the provider filled in, or None when it is off.

    G22: the provider and models Settings saved in the record (`saved`, else
    read from `conn` or the record) win over [ai]. A provider picked there
    turns the feature on with no [ai] at all; one that differs from [ai]'s
    leaves [ai]'s URL, models and key settings behind, since they were for
    the other provider. AI turned off in Settings is off whatever [ai] says.

    `model` (payee suggestions) and `copilot_model` (Ask Kopifund) are each
    what that caller's requests use, "" when there is none: each caller
    refuses only when its own model is missing (payee_model, copilot_model).

    The section turns the feature on. `provider = "auto"`, or no provider,
    picks the first provider in PROVIDERS whose key is found and which has a
    default model, so a profile that exports ANTHROPIC_API_KEY needs nothing
    more than `[ai]`."""
    raw = getattr(cfg, "ai", None)
    saved = saved_settings(conn) if saved is None else saved
    root = _root(cfg)
    if saved.get("off"):
        return None
    if saved.get("provider"):
        ai = dict(raw or {})
        ai.pop("enabled", None)
        if ai.get("provider") != saved["provider"]:
            for k in _PROVIDER_FIELDS:
                ai.pop(k, None)
        ai["provider"] = saved["provider"]
        if saved.get("models_for") == saved["provider"]:
            for k in ("model", "copilot_model"):
                if saved.get(k):
                    ai[k] = saved[k]
        ai["from_config"] = False
    elif raw is None or raw.get("enabled") is False:
        return None
    else:
        ai = {**raw, "from_config": True}
        # G22: models picked in Settings for config.toml's own provider win over
        # [ai]'s; a custom provider's model is config.toml's alone
        if ai.get("provider") in PROVIDERS and saved.get("models_for") == ai.get("provider"):
            for k in ("model", "copilot_model"):
                if saved.get(k):
                    ai[k] = saved[k]
    sources = None
    if ai.get("provider", "auto") == "auto":
        # Audit: every file read once and the Keychain asked once per provider
        # for the whole pick, not once per provider per file
        sources = list(_sources(ai, root))
        pick = next((p for p, (_, _, _, model) in PROVIDERS.items()
                     if model and (_detected(p, ai, root, sources) or _saved_key(p))), None)
        if pick is None:
            found = [p for p in PROVIDERS if PROVIDERS[p][2] and _detected(p, ai, root, sources)]
            if found:
                raise AIError(f"[ai] found keys for {', '.join(found)} but no default model: "
                              "set provider and model")
            raise AIError("[ai] found no API key to use: export one such as ANTHROPIC_API_KEY, "
                          "point key_file at the file that holds them, or name a provider")
        # a model the user named is kept; the default fills only a gap
        ai["provider"], ai["model"] = pick, ai.get("model") or PROVIDERS[pick][3]
    wire, url, _, model = PROVIDERS.get(ai["provider"], (ai.get("format", "openai"), "", (), ""))
    ai["format"] = ai.get("format", wire)
    ai["base_url"] = (ai.get("base_url") or url).rstrip("/")
    ai["model"] = ai.get("model") or model
    if not ai["base_url"]:
        raise AIError(f"[ai] provider {ai['provider']!r} is not one Kopifund knows; set base_url")
    # G22: the copilot's model: its own, else the provider's copilot default, else the payee model
    ai["copilot_model"] = ai.get("copilot_model") or COPILOT_DEFAULTS.get(ai["provider"]) or ai["model"] or ""
    ai.setdefault("send_amounts", True)
    if ai["format"] == "anthropic" and not ai.get("workspace_id"):
        found = _lookup("ANTHROPIC_WORKSPACE_ID", ai, root, sources)
        ai["workspace_id"] = found[0] if found else ""
    return ai


def config_provider(cfg):
    """G22: the provider [ai] in config.toml names, built-in or custom (a name
    PROVIDERS does not list, with base_url), whatever Settings saved, AI turned
    off included. None when there is no [ai], it says `enabled = false`, or it
    names no provider of its own (none, or "auto")."""
    raw = getattr(cfg, "ai", None)
    if not raw or raw.get("enabled") is False:
        return None
    p = raw.get("provider")
    if not isinstance(p, str) or p in ("", "auto", OFF):
        return None
    return p if p in PROVIDERS or raw.get("base_url") else None


def is_local(ai):
    """G22: the provider's base URL is on this machine (localhost, 127.0.0.1
    or ::1), so nothing sent to it leaves the machine and it needs no key.
    Decided from the URL alone: an Ollama on another host is not local."""
    try:
        host = urllib.parse.urlsplit(ai.get("base_url") or "").hostname or ""
    except ValueError:
        return False
    return host in LOCAL_HOSTS


def needs_key(ai):
    """G22: whether requests to the provider carry an API key. A local server
    takes none, and Ollama has no keys wherever it runs."""
    return not is_local(ai) and ai.get("provider") != "ollama"


def label(ai):
    """The provider's name for a sentence: "Ollama (on this Mac)" only when
    its URL is on this machine."""
    name = LABELS.get(ai["provider"], ai["provider"])
    return f"{name} (on this Mac)" if ai["provider"] == "ollama" and is_local(ai) else name


def payee_model(ai):
    """G22: the payee suggestions model, or AIError when none is set."""
    if not ai.get("model"):
        raise AIError(f"Payee suggestions need a model for {label(ai)}: pick one in Settings "
                      "or set [ai] model")
    return ai["model"]


def copilot_model(ai):
    """G22: the Ask Kopifund model, or AIError when none is set."""
    if not ai.get("copilot_model"):
        raise AIError(f"Ask Kopifund needs a model for {label(ai)}: pick one in Settings")
    return ai["copilot_model"]


def locate_key(ai, root):
    """(key, place) for the API key, from the first of: [ai] key_env
    (environment, `.env` in the data folder, key_file), the output of [ai]
    key_command, the key Settings saved (server/finance/keys.py: the Keychain, or
    `.env` with no keychain), then the variables the provider's key usually
    lives in. (None, "none") when there is none. Never raises."""
    root = Path(root)
    if ai.get("key_env"):
        found = _lookup(ai["key_env"], ai, root)
        if found:
            return found
    if ai.get("key_command"):
        try:
            r = subprocess.run(ai["key_command"], shell=True, capture_output=True, text=True, timeout=30)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip(), "key_command"
        except (OSError, subprocess.TimeoutExpired):
            pass
    key, place = keys.read(ai["provider"])
    if key:
        return key, place
    found = _detected(ai["provider"], ai, root)
    if found:
        return found
    return None, "none"


def find_key(ai, root):
    """The API key (locate_key), "" for Ollama and other local servers, which
    need none, or AIError saying where to put one."""
    key, _ = locate_key(ai, root)
    if key:
        return key
    if not needs_key(ai):
        return ""
    raise AIError(f"No API key for {ai['provider']}: save one in Settings, export one, point [ai] key_file "
                  "at the file that holds it, or set key_command to a keychain lookup")


def rough(cents):
    """A typical amount, rounded so it hints at the size and no more: to the
    dollar under $10, then to $5, $50 and $500. Integer cents throughout."""
    c = abs(int(cents))
    step = 100 if c < 1_000 else 500 if c < 10_000 else 5_000 if c < 100_000 else 50_000
    rounded = max(step, (c + step // 2) // step * step)
    return f"about ${rounded // 100:,}"


def asked(groups, send_amounts):
    """The merchant groups worth asking about, as the items sent: id, text and,
    when allowed, a rough typical amount. Counterparty and memo groups stay home."""
    items = []
    for g in groups:
        text = str(g.get("text") or "").strip()
        if g.get("field") != "merchant" or not text:
            continue
        item = {"id": len(items) + 1, "text": text[:80]}
        rows = int(g.get("rows") or 0)
        if send_amounts and rows:
            item["typical"] = rough(int(g.get("amount") or 0) // rows)
        items.append(item)
        if len(items) >= MAX_PAYEES:
            break
    return items


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Audit: urllib's own handler follows a redirect to any host with every
    header copied, the key's included. No provider API redirects, so a 3xx is
    answered as the error it is and the key goes nowhere but the URL asked."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)
# header values that are not secrets, left in an error's words as they are
_PLAIN_HEADERS = frozenset({"content-type", "accept", "anthropic-version", "anthropic-beta"})


def _scrubbed(text, headers):
    """`text` with every secret header value taken out (an `authorization`
    value with and without its scheme), should a provider echo one back."""
    for name, value in headers.items():
        if name.lower() in _PLAIN_HEADERS or not isinstance(value, str):
            continue
        # longest first, so the bare key never splits a match of the whole value
        for secret in sorted({value, value.split(" ", 1)[-1]}, key=len, reverse=True):
            if secret:
                text = text.replace(secret, "(the key)")
    return text


def _request(url, headers, body=None, timeout=TIMEOUT):
    """POST `body` as JSON (GET when it is None), return the decoded JSON reply,
    or AIError in words for the app. The key travels in a header only, to `url`
    alone (a redirect is refused), and no message here ever holds a header's
    value, even when the provider's own error repeats it."""
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="GET" if body is None else "POST",
                                 headers={"content-type": "application/json", **headers})
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if 300 <= e.code < 400:
            raise AIError(f"The AI provider answered {e.code}, a redirect to another address, which Kopifund "
                          "does not follow with the key: check the provider's base_url") from None
        # read the whole (small) error body so its JSON parses, then keep the message short
        detail = e.read(20_000).decode("utf-8", "replace")
        try:
            detail = json.loads(detail)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            pass
        # the key out before the cut, so a key the cut would split leaves no part behind
        raise AIError(f"The AI provider said {e.code}: {_scrubbed(str(detail), headers)[:300]}") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise AIError(_scrubbed(f"Could not reach the AI provider: {getattr(e, 'reason', e)}", headers)) from None
    except ValueError:
        raise AIError("The AI provider's answer was not JSON") from None


def _post(url, headers, body, timeout=TIMEOUT):
    """POST JSON, return the decoded JSON reply. Tests replace this."""
    return _request(url, headers, body, timeout)


def _get(url, headers):
    """GET JSON (G22: a provider's list of models). Tests replace this."""
    return _request(url, headers)


def _anthropic_headers(ai, key):
    headers = {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
    if ai.get("workspace_id"):
        # a key that is not scoped to one workspace must name the workspace
        headers["anthropic-workspace-id"] = ai["workspace_id"]
    return headers


def _bearer(key):
    return {"authorization": f"Bearer {key}"} if key else {}


def ask(ai, key, user):
    """One request in the provider's own format; returns the reply text."""
    if ai["format"] == "anthropic":
        reply = _post(f"{ai['base_url']}/messages", _anthropic_headers(ai, key),
                      {"model": ai["model"], "max_tokens": 16000, "system": SYSTEM,
                       "messages": [{"role": "user", "content": user}]})
        if reply.get("stop_reason") == "refusal":
            raise AIError("The model declined to answer")
        return "".join(b.get("text", "") for b in reply.get("content", []) if b.get("type") == "text")
    reply = _post(f"{ai['base_url']}/chat/completions", _bearer(key),
                  {"model": ai["model"], "response_format": {"type": "json_object"},
                   "messages": [{"role": "system", "content": SYSTEM},
                                {"role": "user", "content": user}]})
    try:
        return reply["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise AIError("The AI provider sent no answer") from None


def parse(text):
    """The suggestions list out of a reply, tolerating a code fence or a sentence
    around the JSON object."""
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        out = json.loads(m.group(0)) if m else None
    except ValueError:
        out = None
    if not isinstance(out, dict) or not isinstance(out.get("suggestions"), list):
        raise AIError("The AI answer was not the JSON Kopifund asked for")
    return out["suggestions"]


def clean(raw, categories):
    """One suggestion checked against what a rule may hold: a category from the
    list or none, a known kind, a short payee."""
    category = raw.get("category") if raw.get("category") in categories else ""
    payee = " ".join(str(raw.get("payee") or "").split())[:60]
    return {"payee": payee, "category": category,
            "kind": raw.get("kind") if raw.get("kind") in KINDS else "Merchant",
            # only a JSON true counts: "false" as a string must not read as sure
            "confident": raw.get("confident") is True and bool(category) and bool(payee),
            "note": " ".join(str(raw.get("note") or "").split())[:120]}


def _cache_path():
    return cache_dir() / "ai-suggestions.json"


def _load_cache():
    try:
        return json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# two checks can ask at once (the server answers on threads); the cache file is
# read, merged and replaced under this lock, never while a request is out
_CACHE_LOCK = threading.Lock()


def _merge_cache(new):
    """Add answers to the cache file: reread it first, so answers another
    request saved meanwhile stay, then replace it through a temporary file of
    this call's own."""
    with _CACHE_LOCK:
        cache = {**_load_cache(), **new}
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".ai-suggestions-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:   # mkstemp makes it 600
                json.dump(cache, fh, indent=1, sort_keys=True)
            os.replace(tmp, path)
        except BaseException:
            os.unlink(tmp)
            raise
        return cache


def suggest(cfg, groups, categories, conn=None):
    """Suggestions for the merchant groups in `groups` (the check's needs_rule
    list): {"enabled", "provider", "model", "suggestions": [...]} with each
    suggestion keyed by the group's text. Raises AIError when the provider fails.
    G22: the provider and model are the ones Settings saved in the record
    (`conn`), else [ai]'s."""
    ai = settings(cfg, conn)
    if ai is None:
        return {"enabled": False, "suggestions": []}
    payee_model(ai)
    items = asked(groups, ai["send_amounts"])
    cache = _load_cache()
    prefix = f"{ai['provider']}|{ai['model']}|"
    todo = [i for i in items if prefix + i["text"] not in cache]
    if todo:
        key = find_key(ai, _root(cfg))
        user = ("Categories: " + json.dumps(list(categories)) + "\n\nStatement texts:\n" +
                json.dumps(todo, ensure_ascii=False))
        by_id = {}
        for raw in parse(ask(ai, key, user)):
            if isinstance(raw, dict) and isinstance(raw.get("id"), int):
                by_id[raw["id"]] = raw
        cache = _merge_cache({prefix + i["text"]: clean(by_id[i["id"]], categories)
                              for i in todo if i["id"] in by_id})
    out = []
    for i in items:
        s = cache.get(prefix + i["text"])
        if s:
            # a category removed since the answer was cached is no longer offered
            s = {**s, "category": s["category"] if s["category"] in categories else ""}
            s["confident"] = s["confident"] and bool(s["category"])
            out.append({"text": i["text"], **s})
    return {"enabled": True, "provider": ai["provider"], "model": ai["model"], "suggestions": out}


# ---------------------------------------------------------------- G22: the AI card on Settings

def provider_info(cfg=None, current=None):
    """types.AiProviderInfo for every provider, in PROVIDERS' order. `local`
    is decided from the URL requests to that provider would really go to:
    `current` (the settings in use) for its own provider, else [ai]'s."""
    out = []
    for p, (_, _, _, model) in PROVIDERS.items():
        ai = current if current and current["provider"] == p else _bare(p, cfg)
        out.append({"name": p, "label": label(ai), "local": is_local(ai),
                    "default_model": model, "copilot_default_model": COPILOT_DEFAULTS.get(p, "")})
    return out


def _bare(provider, cfg):
    """Settings for `provider` with nothing chosen but what [ai] says about
    that same provider: for a key lookup or a model list of a provider other
    than the one in use."""
    raw = dict(getattr(cfg, "ai", None) or {})
    ai = raw if raw.get("provider") == provider else {k: raw[k] for k in ("key_file",) if k in raw}
    wire, url, _, model = PROVIDERS[provider]
    ai = {**ai, "provider": provider}
    ai["format"] = ai.get("format", wire)
    ai["base_url"] = (ai.get("base_url") or url).rstrip("/")
    ai["model"] = ai.get("model") or model
    if ai["format"] == "anthropic" and not ai.get("workspace_id"):
        found = _lookup("ANTHROPIC_WORKSPACE_ID", ai, _root(cfg))
        ai["workspace_id"] = found[0] if found else ""
    return ai


def _effective(provider, cfg, conn=None):
    """Settings for `provider` as requests to it would really go: the ones in
    use when it is the provider in use, else _bare."""
    try:
        current = settings(cfg, conn)
    except AIError:
        current = None
    return current if current and current["provider"] == provider else _bare(provider, cfg)


def key_view(ai, root):
    """types.AiKey: where the key is and its last four characters, never the key."""
    key, place = locate_key(ai, root)
    return {"where": place if key else "none", "last4": keys.last4(key) if key else None,
            "can_remove": bool(key) and place in ("keychain", "env_file")}


def view(cfg, conn=None):
    """GET /api/ai: types.AiSettings. Never raises for a setting that does not
    work yet (no key, a provider [ai] cannot name): the card is where that is fixed.

    `model` and `copilot_model` are the owner's own choice (Settings', else
    [ai]'s for the same provider), "" for none; `model_in_use` and
    `copilot_model_in_use` are what requests really use, a default included,
    "" when AI is off or that caller has no model."""
    saved = saved_settings(conn)
    raw = getattr(cfg, "ai", None)
    try:
        ai = settings(cfg, conn, saved)
    except AIError:
        ai = None
    off = bool(saved.get("off"))
    on = not off and (bool(saved.get("provider")) or (raw is not None and raw.get("enabled") is not False))
    provider = None if off else (ai or {}).get("provider") or saved.get("provider") or (raw or {}).get("provider")
    # G22: a provider Kopifund does not list stays named when [ai] sets it up (base_url) and it works
    provider = provider if provider in PROVIDERS or (ai and ai["provider"] == provider) else None
    from_config = not off and not saved.get("provider") and raw is not None
    # G22: saved models count only for the provider they were picked for
    mine = {k: saved[k] for k in ("model", "copilot_model") if saved.get(k)} \
        if provider and saved.get("models_for") == provider else {}
    if saved.get("provider") or off:
        chosen = mine
    elif provider and (raw or {}).get("provider") == provider:
        # G22: Settings' models over [ai]'s, for a provider Kopifund lists
        chosen = {**raw, **mine} if provider in PROVIDERS else raw
    else:
        chosen = {}
    lookup = ai if ai and ai["provider"] == provider else _bare(provider, cfg) if provider else None
    return {"enabled": on, "provider": provider, "providers": provider_info(cfg, ai),
            "model": str(chosen.get("model") or ""), "copilot_model": str(chosen.get("copilot_model") or ""),
            "model_in_use": str((ai or {}).get("model") or ""),
            "copilot_model_in_use": str((ai or {}).get("copilot_model") or ""),
            "key": key_view(lookup, _root(cfg)) if lookup else {"where": "none", "last4": None, "can_remove": False},
            "send_rows": saved.get("send_rows") is True,
            "local": bool(lookup and is_local(lookup)),
            "from_config": from_config, "config_provider": config_provider(cfg)}


def _model_name(value, name):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise AIRefused(f"{name} is text")
    value = value.strip()
    if len(value) > 200 or any(c.isspace() or not c.isprintable() for c in value):
        raise AIRefused(f"{name} is a model's name, like claude-opus-5-5")
    return value


def save_settings(conn, data, cfg=None):
    """POST /api/ai/settings: save the sent fields of {provider, model,
    copilot_model, send_rows} in the record's `setting` table; a field left out
    keeps its value. G22: a provider of null, "" or "off" turns AI off (payee
    suggestions and Ask Kopifund both), saved as "off" so it wins over [ai]
    in config.toml; saving a provider turns it back on.

    G22: the provider config.toml names (config_provider(cfg), built-in or
    custom) means "use config.toml": the saved provider (or "off") and any
    model not sent with it are cleared, so [ai] decides again. Models sent
    with it are kept, and apply only when it is a provider Kopifund lists."""
    from .db import transaction
    unknown = set(data) - set(CARD_FIELDS)
    if unknown:
        raise AIRefused(f"Not an AI setting: {', '.join(sorted(unknown))}")
    writes = {}
    if "provider" in data:
        p = data["provider"]
        from_config = config_provider(cfg)
        if p not in (None, "", OFF) and p not in PROVIDERS and p != from_config:
            raise AIRefused(f"provider is one of {', '.join(PROVIDERS)}, or null to turn AI off")
        if p is not None and p == from_config:
            writes["provider"] = None
            writes.update({k: None for k in ("model", "copilot_model") if k not in data})
        else:
            writes["provider"] = p or OFF
    for k in ("model", "copilot_model"):
        if k in data:
            writes[k] = _model_name(data[k], k) or None
    # G22: models belong to the provider they were picked for: the one sent
    # with them, else the one in use now (saved, or config.toml's)
    if any(writes.get(k) for k in ("model", "copilot_model")):
        if "provider" in data and writes.get("provider") not in (None, OFF):
            owner = writes["provider"]
        elif "provider" in data and writes.get("provider") is None:
            owner = config_provider(cfg)
        else:
            current = saved_settings(conn)
            owner = current.get("provider") if not current.get("off") and current.get("provider") \
                else config_provider(cfg)
        writes["models_for"] = owner
    elif all(k in writes and writes[k] is None for k in ("model", "copilot_model")):
        writes["models_for"] = None
    if "send_rows" in data:
        if not isinstance(data["send_rows"], bool):
            raise AIRefused("send_rows is true or false")
        writes["send_rows"] = data["send_rows"]
    with transaction(conn):
        for k, v in writes.items():
            if v is None:
                conn.execute("DELETE FROM setting WHERE key = ?", (SETTING_KEYS[k],))
            else:
                conn.execute("INSERT INTO setting(key, value) VALUES (?, ?) "
                             "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (SETTING_KEYS[k], json.dumps(v)))


def _provider_arg(data, cfg=None, conn=None):
    p = data.get("provider")
    if p in (None, ""):
        p = view(cfg, conn)["provider"]
    if p not in PROVIDERS:
        raise AIRefused(f"provider is one of {', '.join(PROVIDERS)}")
    return p


def save_key(cfg, conn, data):
    """POST /api/ai/key {provider, key}: keep the key where Settings keeps keys
    (server/finance/keys.py). A local provider takes none."""
    provider = _provider_arg(data, cfg, conn)
    where = _effective(provider, cfg, conn)
    if is_local(where):
        raise AIRefused(f"{label(where)} runs on this Mac and needs no key")
    try:
        keys.save(provider, data.get("key"))
    except keys.KeyStoreError as e:
        raise AIRefused(str(e)) from None


def remove_key(cfg, conn, data):
    """POST /api/ai/key/remove {provider}: take out the key Settings saved."""
    provider = _provider_arg(data, cfg, conn)
    try:
        keys.remove(provider)
    except keys.KeyStoreError as e:
        raise AIRefused(str(e)) from None


# ---------------------------------------------------------------- G22: the provider's models

MODELS_TTL = 24 * 3600
MODELS_SHOWN = 100


def _models_path(provider):
    return cache_dir() / "ai-models" / f"{provider}.json"


def _iso(created):
    """A model's creation time as ISO text: Anthropic sends ISO, OpenAI-style
    providers seconds since 1970; anything else is no date."""
    if isinstance(created, (int, float)) and not isinstance(created, bool) and created > 0:
        return datetime.fromtimestamp(created, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return created if isinstance(created, str) else ""


def _fetch_models(ai, key, get=None):
    """[{id, label, created}] from the provider, newest first where it dates
    them, else by id."""
    get = get or _get
    out = []
    if ai["format"] == "anthropic":
        after = None
        for _ in range(20):                                  # pages of 1000; a guard, not a limit
            query = {"limit": 1000, **({"after_id": after} if after else {})}
            page = get(f"{ai['base_url']}/models?{urllib.parse.urlencode(query)}", _anthropic_headers(ai, key))
            out += [{"id": m["id"], "label": m.get("display_name") or m["id"], "created": _iso(m.get("created_at"))}
                    for m in page.get("data") or () if isinstance(m, dict) and m.get("id")]
            if not page.get("has_more") or not page.get("last_id"):
                break
            after = page["last_id"]
    else:
        page = get(f"{ai['base_url']}/models", _bearer(key))
        rows = page.get("data") if isinstance(page, dict) else page
        out = [{"id": str(m["id"]), "label": str(m.get("name") or m.get("display_name") or m["id"]),
                "created": _iso(m.get("created"))}
               for m in rows or () if isinstance(m, dict) and m.get("id")]
    out = list({m["id"]: m for m in out}.values())
    out.sort(key=lambda m: m["id"])
    out.sort(key=lambda m: m["created"], reverse=True)   # stable: undated models keep id order, last
    return out


def models(cfg, conn, provider=None, q="", get=None, now=None):
    """GET /api/ai/models?provider=&q=: types.AiModels. The provider's whole
    list is kept in the cache for a day; `q` filters it here (a part of the id
    or the name, any case). No key, or a provider that will not list, is
    models [] with `error` set, never a failure."""
    provider = provider or view(cfg, conn)["provider"]
    if provider not in PROVIDERS:
        raise AIRefused(f"provider is one of {', '.join(PROVIDERS)}")
    now = time.time() if now is None else now
    ai = _effective(provider, cfg, conn)
    path = _models_path(provider)
    cached = None
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached.get("base_url") != ai["base_url"] or now - float(cached.get("fetched", 0)) >= MODELS_TTL:
            cached = None
    except (OSError, ValueError, AttributeError, TypeError):
        cached = None
    if cached is None:
        key, _ = locate_key(ai, _root(cfg))
        if not key and needs_key(ai):
            return {"provider": provider, "models": [], "fetched_at": None,
                    "error": f"No API key for {LABELS.get(provider, provider)} yet: save one first"}
        try:
            listed = _fetch_models(ai, key or "", get)
        except AIError as e:
            return {"provider": provider, "models": [], "fetched_at": None, "error": _without(str(e), key)}
        except (KeyError, TypeError, AttributeError):
            return {"provider": provider, "models": [], "fetched_at": None,
                    "error": "The provider's list of models was not in a shape Kopifund reads"}
        cached = {"base_url": ai["base_url"], "fetched": now,
                  "fetched_at": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "models": listed}
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".models-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(cached, fh)
        os.replace(tmp, path)
    q = (q or "").strip().lower()
    found = [{"id": m["id"], "label": m["label"]} for m in cached["models"]
             if not q or q in m["id"].lower() or q in m["label"].lower()]
    return {"provider": provider, "models": found[:MODELS_SHOWN], "fetched_at": cached["fetched_at"], "error": None}


def _without(text, key):
    """`text` with the key, should a provider ever echo it, taken out."""
    return text.replace(key, "(the key)") if key else text


# ---------------------------------------------------------------- G22: Test on the card

def try_provider(cfg, conn, post=None, clock=time.monotonic):
    """POST /api/ai/test: types.AiTest. One short request to the provider
    with the payee suggestions model (G22: else the Ask Kopifund model, when
    only that one is set); the answer says whether it replied and how long it
    took, never the key."""
    post = post or _post
    try:
        ai = settings(cfg, conn)
    except AIError as e:
        return {"ok": False, "message": str(e), "ms": None}
    if ai is None:
        return {"ok": False, "message": "Pick a provider first", "ms": None}
    name = label(ai)
    model = ai["model"] or ai["copilot_model"]
    if not model:
        return {"ok": False, "message": f"Pick a model for {name} first", "ms": None}
    key = ""
    try:
        key = find_key(ai, _root(cfg))
        start = clock()
        if ai["format"] == "anthropic":
            reply = post(f"{ai['base_url']}/messages", _anthropic_headers(ai, key),
                         {"model": model, "max_tokens": 64,
                          "messages": [{"role": "user", "content": "Reply with the word OK."}]})
            if not isinstance(reply, dict) or reply.get("type") == "error":
                raise AIError("The AI provider sent no answer")
        else:
            reply = post(f"{ai['base_url']}/chat/completions", _bearer(key),
                         {"model": model, "messages": [{"role": "user", "content": "Reply with the word OK."}]})
            if not isinstance(reply, dict) or not reply.get("choices"):
                raise AIError("The AI provider sent no answer")
        ms = max(0, round((clock() - start) * 1000))
    except AIError as e:
        return {"ok": False, "message": _without(str(e), key), "ms": None}
    return {"ok": True, "message": f"{name} answered with {model} in {ms:,} ms", "ms": ms}


# ---------------------------------------------------------------- G22: a chat with tools

class ToolCall(NamedTuple):
    id: str
    name: str
    input: dict | None
    """The arguments, or None when the model sent ones that are not a JSON object."""


class Turn(NamedTuple):
    """One answer of the model: its text, the tools it asks for, and why it
    stopped: `tools`, `end`, `max_tokens` or `refusal`. G22: `thinking` is the
    round's thinking as the provider shows it (Anthropic's summarized
    `thinking` blocks, or an OpenAI-style `reasoning_content`), "" for none."""
    text: str
    calls: list
    stop: str
    thinking: str = ""


def takes_thinking(model):
    """G22: whether a model takes `thinking: {type: "adaptive"}`: a Claude
    model from Opus 4.6 and Sonnet 4.6 on."""
    return bool(_CLAUDE.search(model or "")) and not _BEFORE_ADAPTIVE.search(model or "")


class Chat:
    """One question's conversation with tools, in the provider's own wire
    format: Anthropic's Messages API (`tool_use` and `tool_result` blocks), or
    the OpenAI-style chat completions (`tool_calls` and `role: tool`).

    `tools` are [{name, description, input_schema}]; `history` the earlier
    turns as (role, text), their tool results already dropped; `transport`
    (url, headers, body) -> reply, which tests replace with scripted replies.

    Within the question the history is only ever appended to: each reply goes
    back exactly as it came (Anthropic's `thinking` and `fallback` blocks
    included), so the provider sees the same prefix every round and its cache
    and the model's own reasoning stay valid. The instructions and the tools
    are the stable prefix, marked for Anthropic's prompt cache."""

    def __init__(self, ai, key, system, tools, history, question, transport=None):
        self.ai, self.key = ai, key
        self.model = ai["copilot_model"]
        self.anthropic = ai["format"] == "anthropic"
        self.transport = transport or (lambda url, headers, body: _post(url, headers, body, timeout=CHAT_TIMEOUT))
        self.requests = 0
        turns = [{"role": r, "content": t} for r, t in history if t] + [{"role": "user", "content": question}]
        if self.anthropic:
            self.system = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
            self.tools = [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
                          for t in tools]
            self.messages = turns
        else:
            self.tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                             "parameters": t["input_schema"]}} for t in tools]
            self.messages = [{"role": "system", "content": system}] + turns

    def _anthropic_request(self):
        headers = _anthropic_headers(self.ai, self.key)
        body = {"model": self.model, "max_tokens": 16000, "system": self.system, "tools": self.tools,
                "tool_choice": {"type": "auto"}, "messages": self.messages}
        if takes_thinking(self.model):
            body["thinking"] = dict(THINKING)   # G22: the panel shows each round's summary
        # G22: a refusal is retried server-side on the model Anthropic picks for its category
        host = urllib.parse.urlsplit(self.ai["base_url"]).hostname
        if host == "api.anthropic.com" and self.model in FALLBACK_MODELS:
            headers["anthropic-beta"] = FALLBACK_BETA
            body["fallbacks"] = "default"
        return f"{self.ai['base_url']}/messages", headers, body

    def step(self):
        """Send the conversation, keep the reply, and return it as a Turn."""
        self.requests += 1
        if self.anthropic:
            reply = self.transport(*self._anthropic_request())
            if not isinstance(reply, dict) or not isinstance(reply.get("content"), list):
                raise AIError("The AI provider sent no answer")
            # unchanged: thinking, fallback and every other block goes back as it came
            self.messages.append({"role": "assistant", "content": reply["content"]})
            blocks = [b for b in reply["content"] if isinstance(b, dict)]
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            # G22: a round's thinking blocks, summarized, as one step; a redacted or omitted one has no text
            thinking = "\n\n".join(b["thinking"].strip() for b in blocks
                                    if b.get("type") == "thinking" and isinstance(b.get("thinking"), str)
                                    and b["thinking"].strip())
            calls = [ToolCall(b.get("id", ""), b.get("name", ""), b.get("input") if isinstance(b.get("input"), dict)
                              else None) for b in blocks if b.get("type") == "tool_use"]
            reason = reply.get("stop_reason")
            stop = ("refusal" if reason == "refusal" else "max_tokens" if reason == "max_tokens"
                    else "tools" if calls else "end")
            return Turn(text, calls, stop, thinking)
        body = {"model": self.model, "messages": self.messages, "tools": self.tools, "tool_choice": "auto"}
        reply = self.transport(f"{self.ai['base_url']}/chat/completions", _bearer(self.key), body)
        try:
            choice = reply["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError):
            raise AIError("The AI provider sent no answer") from None
        self.messages.append(message)
        calls = []
        for c in message.get("tool_calls") or ():
            fn = c.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (TypeError, ValueError):
                args = None
            calls.append(ToolCall(c.get("id", ""), fn.get("name", ""), args if isinstance(args, dict) else None))
        reason = choice.get("finish_reason")
        stop = ("refusal" if reason == "content_filter" else "max_tokens" if reason == "length"
                else "tools" if calls else "end")
        # G22: DeepSeek, Qwen and others send their reasoning beside the answer
        reasoning = message.get("reasoning_content")
        return Turn(message.get("content") or "", calls, stop,
                    reasoning.strip() if isinstance(reasoning, str) else "")

    def add_results(self, results):
        """Every result of one round, [(call, text, is_error)], back to the
        model together: one user message of `tool_result` blocks, or, in the
        OpenAI-style format, which has no such message, one `role: tool`
        message per call, all before the next request."""
        if self.anthropic:
            content = []
            for call, text, error in results:
                block = {"type": "tool_result", "tool_use_id": call.id, "content": text}
                if error:
                    block["is_error"] = True
                content.append(block)
            self.messages.append({"role": "user", "content": content})
        else:
            for call, text, _ in results:
                self.messages.append({"role": "tool", "tool_call_id": call.id, "content": text})
