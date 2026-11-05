"""Ask Kopifund, the copilot (G22): the loop, its threads and its answer.

POST /api/copilot/ask takes a question and the page it was asked on and
answers with one line of JSON per event as it happens (types.CopilotEvent):
the thread, then each round's steps (its summarized thinking, the text it
wrote before calling tools, a line for each tool as it starts), then the
answer's text, its charts, links and confirm cards, the follow-up questions,
and `done`. A failure is an `error` line in the app's words, then `done`.

The loop: the question goes to the model through server/finance/ai.py's Chat with
the tools of server/finance/copilot_tools.py. Each tool the model asks for runs
here, every result of a round goes back together, and the loop ends when the
model answers in plain text, or after MAX_ROUNDS requests with a line saying
it stopped. The model reads the record and nothing else; no tool writes.

Every request starts the same way, so the provider can cache it: the
instructions (SYSTEM), the tool schemas, and the data dictionary (buckets and
categories, accounts by display name, the record's first and last day). Each
question then carries its page and filters, so "this month" means the month
on screen. An earlier turn goes back as its final text only; its tool
results are dropped.

Threads are JSON files in the data folder's `copilot/` folder (mode 700, each
file 600), apart from the record, so asking never changes the record or the
pages' cache. Each holds its messages in the contract's shape
(types.CopilotMessage). KOPIFUND_COPILOT moves the folder, which the test
suite does.

A turn (the question and its answer) is saved once, when the answer is whole,
and only when it is an answer: a turn that ends in an error, or whose page
went away mid-stream, saves nothing, so Retry asks again without a duplicate
and no later request sees a failed pair. The save holds the thread's lock
while it reads the file, appends the turn to the messages it holds now and
writes it back, so a slower request never overwrites turns saved meanwhile.
A request that a newer question on the same thread overtook (Stop, then ask
again) saves nothing, and neither does one whose thread was removed while it
ran. A new thread's file appears with its first answer; until then its id is
one this server issued, which the page may ask with again (Retry).
"""
import json
import os
import re
import secrets
import sys
import tempfile
import threading
import traceback
from datetime import date, datetime, timezone
from pathlib import Path

from . import ai, copilot_tools as tools, dates
from .errors import NotFound

MAX_ROUNDS = 8
MAX_QUESTION = 4000
HISTORY = 20                   # earlier messages sent back with a question
TITLE = 60
_ID = re.compile(r"[0-9a-f]{16}")
_LOCK = threading.Lock()       # guards the tables below and the folder's creation
_THREAD_LOCKS = {}             # thread id -> the lock its load-append-save holds
_ISSUED = set()                # new threads' ids this server handed out, with no file yet
_LATEST = {}                   # thread id -> the newest question asked on it (a token)

SYSTEM = """You are Ask Kopifund, the assistant inside Kopifund, a personal finance app for one person \
in Singapore. Kopifund reads the owner's bank, card, wallet, brokerage and CPF statements into one ledger, \
the record, and shows where the money went and how long it lasts. You answer the owner's questions about \
their own money from the record, through the tools.

How to answer:
- Get every figure from a tool. Never do arithmetic yourself: no adding, subtracting, averaging or \
converting. When you need a sum, a difference or a count, ask a tool for it (query can SUM and COUNT). \
Quote figures as the tools give them and say which tool's figure you mean when it is not obvious.
- Money from the tools is integer cents with a currency code. Write SGD as S$1,234.56 and another \
currency with its code, as USD 12.00.
- Write a day as 3 Oct 2026, a month as Oct 2026 and a range as 3 Oct to 9 Oct 2026, never as ISO dates.
- Describe and estimate. Never tell the owner where to put their money: no advice to buy, sell, invest in, \
save into or move money to any product, account or asset. Say what the record shows and what the \
projection estimates, and leave the choice to them.
- Keep answers short: the figure first, then a sentence or two, or a short list. Say which months or \
filters a figure covers.
- The owner reads your final answer; notes you write between tool calls show only inside folded rows. \
So the final answer stands on its own: never refer to "above" or to an earlier note, and if you correct \
yourself, give the right figure without describing the slip.
- A tool result's `link` lists exactly the rows its figure counts, and the panel shows those links under \
your answer, so never paste an address into the text. Balances, averages and estimates have no link.
- Counterparty and memo lines never reach you: text shown as (hidden) is one. Account numbers show only \
their last four digits.
- Use show_chart when a picture says it better (a trend over months, a split). Use suggest_change only \
when the owner asks to fix or set something, or a fix is plainly what they want; nothing is saved until \
they press Confirm on the card. End with follow_ups when there is an obvious next question.
- "This month", "these" and "here" mean what the page the question was asked on shows, from its filters.
- When the record cannot answer, say so plainly."""


# ---------------------------------------------------------------- threads

def threads_dir():
    """The data folder's `copilot/` folder, or KOPIFUND_COPILOT."""
    from .paths import data_root
    override = os.environ.get("KOPIFUND_COPILOT")
    return Path(override).expanduser() if override else data_root() / "copilot"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path(thread_id):
    if not isinstance(thread_id, str) or not _ID.fullmatch(thread_id):
        raise NotFound("No such thread")
    return threads_dir() / f"{thread_id}.json"


def load(thread_id):
    """One thread (types.CopilotThread), or NotFound. G22: a message saved
    before answers kept their steps loads with `steps` []."""
    try:
        thread = json.loads(_path(thread_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise NotFound("No such thread") from None
    if not isinstance(thread, dict) or not isinstance(thread.get("messages"), list):
        raise NotFound("No such thread")
    for m in thread["messages"]:
        if isinstance(m, dict) and not isinstance(m.get("steps"), list):
            m["steps"] = []
    return thread


def _thread_lock(thread_id):
    with _LOCK:
        return _THREAD_LOCKS.setdefault(thread_id, threading.Lock())


def save(thread):
    """Write a thread through a temporary file, so a crash leaves the old one
    or the new one. The caller holds the thread's lock (append_turn)."""
    folder = threads_dir()
    with _LOCK:
        folder.mkdir(parents=True, exist_ok=True)
        os.chmod(folder, 0o700)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".thread-", suffix=".tmp")   # mkstemp makes it 600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(thread, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, _path(thread["id"]))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def open_thread(thread_id):
    """The thread a question goes to: a saved one, or, for an id this server
    issued whose first answer is not saved yet (it failed, or was stopped),
    that thread still empty. NotFound for any other id."""
    try:
        return load(thread_id)
    except NotFound:
        with _LOCK:
            issued = thread_id in _ISSUED
        if not issued:
            raise
        return {"id": thread_id, "title": "", "updated_at": _now(), "messages": []}


def append_turn(thread_id, title, revision, turn, token):
    """Save one finished turn (the question and its answer): under the
    thread's lock, read the thread as it is now and append the turn to it.
    Nothing is saved, and False comes back, when a newer question on this
    thread started after this one (`token` is no longer the latest) or the
    thread was removed after this question began (`revision` is how many
    messages it held then; 0 for a new thread)."""
    with _thread_lock(thread_id):
        with _LOCK:
            if _LATEST.get(thread_id) is not token:
                return False
            issued = thread_id in _ISSUED
        try:
            thread = load(thread_id)
        except NotFound:
            if revision or not issued:
                return False         # removed while the question ran
            thread = {"id": thread_id, "title": title, "updated_at": turn[0]["at"], "messages": []}
        thread["messages"].extend(turn)
        thread["updated_at"] = turn[-1]["at"]
        save(thread)
        with _LOCK:
            _ISSUED.discard(thread_id)
            if _LATEST.get(thread_id) is token:
                del _LATEST[thread_id]
        return True


def outline(thread):
    """types.CopilotThreadOutline."""
    return {"id": thread["id"], "title": thread["title"], "updated_at": thread["updated_at"],
            "turns": sum(1 for m in thread["messages"] if m.get("role") == "user")}


def listing():
    """GET /api/copilot/threads: types.CopilotThreads, newest first."""
    out = []
    folder = threads_dir()
    for f in folder.glob("*.json") if folder.is_dir() else ():
        try:
            out.append(outline(load(f.stem)))
        except (NotFound, KeyError, TypeError):
            continue
    out.sort(key=lambda t: (t["updated_at"], t["id"]), reverse=True)
    return {"threads": out}


def remove(thread_id):
    """POST /api/copilot/threads/remove {id}: the list without it. A question
    still running on it saves nothing (append_turn)."""
    path = _path(thread_id)
    with _thread_lock(thread_id):
        with _LOCK:
            issued = thread_id in _ISSUED
            _ISSUED.discard(thread_id)
        try:
            path.unlink()
        except FileNotFoundError:
            if not issued:
                raise NotFound("No such thread") from None
    return listing()


def _title(question):
    line = " ".join(question.split())
    if len(line) <= TITLE:
        return line
    cut = line[:TITLE].rsplit(" ", 1)[0] or line[:TITLE]
    return cut.rstrip(",.;:") + "..."


def _message(role, text, page=None, at=None, **parts):
    """types.CopilotMessage."""
    return {"role": role, "text": text, "at": at or _now(), "page": page,
            **{k: parts.get(k) or [] for k in ("tools", "steps", "charts", "links", "cards", "followups")}}


def _step(kind, text="", tools=None):
    """types.CopilotStep."""
    return {"kind": kind, "text": text, "tools": tools or []}


# ---------------------------------------------------------------- the question

def _page(raw):
    """types.CopilotPage from the body, or None."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("page is {path, title, filters}")
    filters = raw.get("filters") or {}
    if not isinstance(filters, dict):
        raise ValueError("page.filters is an object of text")
    return {"path": str(raw.get("path") or "")[:200], "title": str(raw.get("title") or "")[:100],
            "filters": {str(k)[:40]: str(v)[:200] for k, v in list(filters.items())[:20] if v is not None}}


def _where(message, today=None):
    """The lines a question goes to the model with: the page it was asked on
    and its filters, and today's date for the newest one."""
    page = message.get("page")
    lines = []
    if page:
        where = f"Asked on the {page['title'] or 'app'} page ({page['path'] or '/'})"
        if page.get("filters"):
            where += ", filters: " + ", ".join(f"{k}={v}" for k, v in page["filters"].items())
        lines.append(where + ".")
    if today:
        lines.append(f"Today is {dates.day_label(today.isoformat())}.")
    return "\n".join(lines) + "\n\n" if lines else ""


class _Req:
    """What results_cache.results reads of a request: the record, here read-only."""

    def __init__(self, conn):
        self.conn = conn

    def maybe_record(self):
        return self.conn


def start(cfg, conn, data, transport=None, today=None):
    """POST /api/copilot/ask: check the question can be asked, then return the
    events to stream. Refused (ai.AIRefused, a 422) before anything streams
    when the copilot is off, the provider has no key, or the owner has not
    turned on sending rows to a provider off this machine."""
    from .db import db_path
    question = data.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question is the text to ask")
    if len(question) > MAX_QUESTION:
        raise ai.AIRefused(f"Ask in under {MAX_QUESTION:,} characters")
    page = _page(data.get("page"))
    thread_id = data.get("thread")
    thread = open_thread(thread_id) if thread_id is not None else None
    saved = ai.saved_settings(conn)
    try:
        settings = ai.settings(cfg, conn, saved)
        if settings is not None:
            ai.copilot_model(settings)
    except ai.AIError as e:
        raise ai.AIRefused(str(e)) from None
    if settings is None:
        raise ai.AIRefused("Ask Kopifund is off: pick a provider in Settings")
    name = ai.label(settings)
    if not ai.is_local(settings) and not saved.get("send_rows"):
        raise ai.AIRefused(f"Turn on \"Send matching rows to {name}\" in Settings first")
    try:
        key = ai.find_key(settings, ai._root(cfg))
    except ai.AIError as e:
        raise ai.AIRefused(str(e)) from None
    path = db_path()
    if not path.exists():
        raise ai.AIRefused("There is no record to ask about yet: load a statement first")
    return _events(cfg, settings, key, path, thread, question.strip(), page, transport, today or date.today())


def _events(cfg, settings, key, path, thread, question, page, transport, today):
    """The answer, one event at a time (the module's docstring)."""
    token = object()
    with _LOCK:
        if thread is None:
            thread = {"id": secrets.token_hex(8), "title": "", "updated_at": _now(), "messages": []}
            _ISSUED.add(thread["id"])
        _LATEST[thread["id"]] = token     # a question asked on this thread after now overtakes this one
    answer = _answer(cfg, settings, key, path, thread, question, page, transport, today, token)
    ended = False
    try:
        for event in answer:
            ended = event["type"] == "done"
            yield event
    except Exception:            # noqa: BLE001  Audit: a failure outside the model loop (the record would
        # not open, the turn could not be saved) still ends the stream the way the page reads one
        traceback.print_exc(file=sys.stderr)
        if not ended:
            yield {"type": "error", "message": "Something went wrong while answering. Try again."}
            yield {"type": "done"}
    finally:
        answer.close()
        with _LOCK:
            if _LATEST.get(thread["id"]) is token:
                del _LATEST[thread["id"]]


def _answer(cfg, settings, key, path, thread, question, page, transport, today, token):
    from . import results_cache
    thread["title"] = thread["title"] or _title(question)
    revision = len(thread["messages"])
    history = [m for m in thread["messages"] if m.get("role") in ("user", "assistant")][-HISTORY:]
    asked = _message("user", question, page)
    yield {"type": "thread", "thread": outline({**thread, "updated_at": asked["at"],
                                               "messages": thread["messages"] + [asked]})}

    text, error, ran, steps = "", None, [], []
    ctx = None
    conn = tools.open_readonly(path)
    try:
        results = results_cache.results(_Req(conn))
        folders = getattr(cfg, "folders", ()) or ()
        ctx = tools.Context(conn, path, results, folders, cfg, today)
        system = SYSTEM + "\n\nThe record:\n" + tools.dictionary(ctx)
        # a page's filters can name a payee the explorer took from a counterparty: they pass the privacy filter
        earlier = [(m["role"], ctx.private(_where(m)) + (m.get("text") or "") if m["role"] == "user"
                    else m.get("text") or "") for m in history]
        chat = ai.Chat(settings, key, system, tools.schemas(), earlier,
                       ctx.private(_where(asked, today)) + question, transport)
        for _ in range(MAX_ROUNDS):
            turn = chat.step()
            if turn.stop == "refusal":
                error = "The model declined to answer that one. Try asking it another way."
                break
            # G22: each round's steps, in order: its thinking, the text it wrote, the tools it ran
            if turn.thinking:
                steps.append(_step("thinking", turn.thinking))
                yield {"type": "thinking", "text": turn.thinking}
            if turn.stop == "max_tokens":
                text = (turn.text.strip() + "\n\n" if turn.text.strip() else "") + \
                    "(The answer stopped at its length limit.)"
                break
            if not turn.calls:
                text = turn.text.strip()
                break
            if turn.text.strip():
                # G22: text before a tool call is a step, kept in place, not lost to the last round's answer
                steps.append(_step("text", turn.text.strip()))
                yield {"type": "say", "text": turn.text.strip()}
            done, shown = [], []
            for call in turn.calls:
                if call.name != "follow_ups":
                    line = {"name": call.name, "label": tools.label(call.name, call.input)}
                    ran.append(line)
                    shown.append(line)
                    yield {"type": "tool", "tool": line}
                out, failed = tools.run(ctx, call.name, call.input)
                done.append((call, out, failed))
            if shown and steps and steps[-1]["kind"] == "tools":
                steps[-1]["tools"].extend(shown)   # one row for tools run back to back, as the panel groups them
            elif shown:
                steps.append(_step("tools", tools=shown))
            chat.add_results(done)
        else:
            # the last round's text is already a step above
            text = f"I stopped after {MAX_ROUNDS} rounds of looking things up. Ask something narrower and I can start there."
        said = [i for i, s in enumerate(steps) if s["kind"] != "thinking"]
        if not error and not text and said and steps[said[-1]]["kind"] == "text":
            # G22: a model that wrote its answer beside a last tool call (follow_ups) and then nothing
            # more: that text is the answer (the panel drops the step the answer repeats)
            text = steps.pop(said[-1])["text"]
        elif not error and text and said and steps[said[-1]]["kind"] == "text" \
                and steps[said[-1]]["text"] == text.strip():
            # G22: the same rule the panel applies live: a step the final answer repeats is not kept
            steps.pop(said[-1])
        if not error and not text and not (ctx.charts or ctx.cards):
            text = "I could not find an answer to that in the record."
        if not error:
            ctx.settle_links(text)
    except ai.AIError as e:
        error = str(e).replace(key, "(the key)") if key else str(e)
    except Exception:            # noqa: BLE001  the panel gets words, the server log the trace
        traceback.print_exc(file=sys.stderr)
        error = "Something went wrong while answering. Try again."
    finally:
        if ctx is not None:
            ctx.close()
        conn.close()
    if not error:
        # G22: only an answer is saved; a turn that failed saves nothing, so Retry adds no duplicate
        answer = _message("assistant", text, tools=ran, steps=steps, charts=ctx.charts, links=ctx.links,
                          cards=ctx.cards, followups=ctx.followups)
        append_turn(thread["id"], thread["title"], revision, [asked, answer], token)
    if error:
        yield {"type": "error", "message": error}
    else:
        yield {"type": "text", "text": text}
        for chart in ctx.charts:
            yield {"type": "chart", "chart": chart}
        for link in ctx.links:
            yield {"type": "link", "link": link}
        for card in ctx.cards:
            yield {"type": "card", "card": card}
        if ctx.followups:
            yield {"type": "followups", "followups": ctx.followups}
    yield {"type": "done"}
