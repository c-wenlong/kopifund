"""The upload page's four steps, each kept in the upload history (G19: moved
out of serve.py): an upload, a check, a load and the Notion mirror.
POST /api/statements, /api/statements/auto, /api/run/check, /api/run/load and
/api/run/mirror each run one (server/finance/api_writes.py), and server/finance/upload_log.py
keeps what it said.
"""

from .errors import Refused
from .parsers import layouts as registry
from .paths import SERVER

NOT_SUPPORTED = "Kopifund does not read this statement yet, so nothing was kept"

def logged_step(conn, cfg, step, query, body):
    """G13: what the step says is kept in the history, and answered, with the
    owner's account numbers masked: a problem names a block by a period name
    that starts with its number, and a mirror's report prints them. The
    numbers are read once the step has run, so a load's new accounts count,
    and a check adds the numbers of the statements it parsed, so a first one,
    before the record knows them, is masked too. The answer keeps the
    needs-rule groups' text as printed, since the Upload page turns it into a
    rule's pattern, and an upload's folders as the config names them, since
    the page sends one back as the folder to save into; the history masks
    them too."""
    from . import account_names, api
    parsed = set()                       # filled by a check (_run_step)

    def scrub(value, keep=()):
        return account_names.scrub_tree(value, api.own_numbers(conn, cfg) | parsed, keep)

    try:
        return scrub(_run_step(conn, cfg, step, query, body, scrub, parsed),
                     keep=("needs_rule", "account_folder", "candidates"))
    except Refused as e:
        raise Refused(scrub(str(e))) from e


def _run_step(conn, cfg, step, query, body, scrub, parsed):
    import hashlib
    from . import monthly, upload_log
    attempt = upload_log.attempt_for(conn, query.get("attempt"), query.get("from", "app"))
    if step == "upload":
        folder = query.get("folder")
        given = {"file": query.get("name", ""), "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
        found = {}
        if folder is None:
            # G13: the folder from the statement's own text
            try:
                found = monthly.find_folder(cfg, body)
            except monthly.UploadRefused as e:
                upload_log.record(conn, attempt, step, upload_log.now(), "refused", str(e),
                                  {"folder": "", **given}, {"error": str(e)}, scrub)
                raise Refused(str(e)) from e
            if found.get("supported") is False:
                # G21: no layout reads it, so there is no account to choose. It is
                # kept nowhere, not even in the history; the page lists the banks
                # Kopifund reads, from the layout registry
                banks, soon = registry.supported()
                return {"status": "not-supported", "message": NOT_SUPPORTED, "supported": banks,
                        "coming_soon": soon, "attempt": attempt}
            if not found["folder"]:
                return {"status": "needs-account", "message": found["why"], "kind": found["kind"],
                        "currency": found["currency"], "candidates": found["candidates"], "attempt": attempt}
            folder = found["folder"]

        def described(r):
            return ("ok" if r["status"] == "added" else "duplicate", r["message"], r)
        try:
            out = upload_log.logged(
                conn, attempt, step,
                {"folder": folder, **given, "folder_from": "statement" if found else "owner"},
                lambda: monthly.upload(cfg, folder, body), described, refusals=(monthly.UploadRefused,),
                scrub=scrub)
        except monthly.UploadRefused as e:
            raise Refused(str(e)) from e
        out = {**out, "account_folder": folder, "kind": found.get("kind", "")}
    elif step == "check":
        def described(report):
            return ("problems" if monthly.blocking(report) else "ok", upload_log.check_summary(report), report)

        def check():
            statements, report = monthly.check(cfg, conn)
            parsed.update(monthly.statement_numbers(statements))
            return report
        out = upload_log.logged(conn, attempt, step, {"folders": [f["path"] for f in monthly.folders(cfg)]},
                                check, described, scrub=scrub)
    elif step == "load":
        def described(r):
            report = r["report"]
            seen = {k: report[k] for k in ("statements", "rows", "to_add_count", "recategorised_count",
                                           "to_archive_count", "rows_by_statement")}
            if not r["loaded"]:
                n = len(monthly.blocking(report))
                return ("blocked", f"not loaded: {n} problem{'s' * (n != 1)}",
                        {"checked": seen, "problems": report["problems"]})
            c = r["counts"]
            pending_note = (f"; {c['phone rows matched']} phone row{'s' * (c['phone rows matched'] != 1)} "
                            "matched" if c.get("phone rows matched") else "")
            return ("ok", f"added {c['inserted']}, updated {c['updated']}, archived {c['archived']}; "
                          f"{(r.get('stats') or {}).get('stats', 0)} stats rerun{pending_note}",
                    {"checked": seen, "counts": c, "stats": r.get("stats"), "added": report["to_add"]})
        out = upload_log.logged(conn, attempt, step, {}, lambda: monthly.load(cfg, conn), described,
                                scrub=scrub)
    else:
        def described(r):
            return ("ok" if r["code"] == 0 else "failed", f"exit code {r['code']}", r)
        out = upload_log.logged(conn, attempt, step, {}, lambda: dict(zip(("code", "output"), monthly.mirror(SERVER))),
                                described, scrub=scrub)
    return {**out, "attempt": attempt}
