"""What the app's writes (POST) answer (G19: moved out of serve.py), each a
`post_<name>(req)` that serve.py's route table names. `req` is the route's
call (serve.Request): `req.data` is the body as a JSON object for a route that
reads one, `req.cfg` the config for a route that loads it, and `req.record`
the one connection to the record. A write that changes what the stats read
reruns them in the background (server/finance/stats_rebuild.py) and says so; the
projection's two writes are in server/analytics/api.py, since they draw on
analytics.growth.
"""

from . import app_icon, results_cache, stats_rebuild, upload_steps
from .errors import Refused
# the stats rerun after these writes (stats_rebuild.refresh_stats, which a test can stub)
from .stats_rebuild import followed as _followed, rebuilding as _rebuilding


def post_setup(req):
    from . import setup
    return setup.create(req.data.get("providers"))


def post_profile(req):
    from . import profile
    return {"about": profile.save(req.data)}


def post_scam_settings(req):
    # G15: the thresholds
    from . import scam_checks
    conn = req.record(create=True)
    scam_checks.save_settings(conn, req.data)
    return scam_checks.page(conn, results_cache.results(req))


def post_app_icon(req):
    return app_icon.choose(req.record(create=True), req.data.get("name"))


def post_scam_fine(req):
    # G15: a row marked fine (or not any more)
    from . import scam_checks
    conn = req.record(create=True)
    scam_checks.mark_fine(conn, req.data.get("key"), req.data.get("fine", True) is not False)
    return scam_checks.page(conn, results_cache.results(req))


def post_series(req):
    # G16: one series' edit, kept in the record (server/finance/series.py)
    from . import recurring
    return recurring.save_series(req.record(), results_cache.results(req), req.data)


def post_payee(req):
    # G16: a payee the rules mark recurring said not to be
    from . import recurring
    return recurring.save_payee(req.record(), req.data)


def post_uploads_start(req):
    from . import upload_log
    return {"attempt": upload_log.start(req.record(create=True), req.data.get("origin", "upload"))}


def post_budget(req):
    from . import planning
    d = req.data
    return planning.set_budget(req.record(create=True), _text(d, "category"), d.get("month", ""),
                               d.get("planned"), _id(d, "category_id"))


def post_budgets_copy(req):
    from . import planning
    d = req.data
    return planning.copy_budgets(req.record(create=True), d.get("from", ""), d.get("to", ""))


def post_plan(req):
    from . import planning
    out = planning.plan_out(planning.save_plan_page(req.record(create=True), planning.plan_in(req.data)))
    # G13: the forward view reads the plan, so the stats rerun. The
    # answer is the plan itself, which the page keeps, so it
    # carries no "stats" key: the page watches the rebuild itself
    stats_rebuild.refresh_stats(req.cfg)
    return out


def post_need_class(req):
    from . import planning
    d = req.data
    out = planning.set_need_class(req.record(create=True), _text(d, "name"), d.get("need_class"), _id(d, "id"))
    return _rebuilding(req, out)         # needs-wants-savings reads the class


# G13: a trip's rows follow it at once (server/finance/relink.py)
def post_trip(req):
    from . import planning
    d = req.data
    out = planning.save_trip(req.record(create=True), d, old_name=_text(d, "old_name"), cfg=req.cfg,
                             trip_id=_id(d, "id"))
    return _followed(req.cfg, out, always=True)


def post_trip_undo(req):
    from . import planning
    out = planning.undo_trip_save(req.record(create=True), req.data.get("undo"), cfg=req.cfg)
    return _followed(req.cfg, out, always=True)


def post_trip_restore(req):
    from . import planning
    out = planning.restore_trip(req.record(create=True), req.data, cfg=req.cfg)
    return _followed(req.cfg, out, always=True)


def post_trip_remove(req):
    from . import planning
    d = req.data
    out = planning.remove_trip(req.record(create=True), _text(d, "name"), cfg=req.cfg, trip_id=_id(d, "id"))
    return _followed(req.cfg, out, always=True)


def _text(d, k):
    v = d.get(k)
    if v is not None and not isinstance(v, str):
        raise ValueError(f"{k} is text")
    return v or ""


def _id(d, k):
    """G17: a row's id from a write's body, or None when it sends none."""
    v = d.get(k)
    if v is None:
        return None
    if not isinstance(v, int) or isinstance(v, bool) or v < 1:
        raise ValueError(f"{k} is a row's id, a whole number")
    return v


# The Categories page's writes (server/finance/categories.py)
def post_category_add(req):
    from . import categories
    d = req.data
    return _rebuilding(req, categories.add_category(
        req.record(create=True), _text(d, "name"), _text(d, "group"), _text(d, "need_class") or None,
        group_id=_id(d, "group_id")))


def post_category_rename(req):
    from . import categories
    d = req.data
    return _rebuilding(req, categories.rename_category(req.record(create=True), _text(d, "name"), _text(d, "new_name"),
                                                       category_id=_id(d, "id")))


def post_category_move(req):
    from . import categories
    d = req.data
    conn = req.record(create=True)
    index = d.get("index")
    if index is not None and (not isinstance(index, int) or isinstance(index, bool)):
        raise ValueError("index is a whole number")
    return _rebuilding(req, categories.move_category(conn, _text(d, "name"), _text(d, "group"), index,
                                                     category_id=_id(d, "id"), group_id=_id(d, "group_id")))


def post_category_delete(req):
    from . import categories
    d = req.data
    return _rebuilding(req, categories.delete_category(req.record(create=True), _text(d, "name"), _text(d, "move_to"),
                                                       category_id=_id(d, "id"), move_to_id=_id(d, "move_to_id")))


def post_group_add(req):
    from . import categories
    return _rebuilding(req, categories.add_group(req.record(create=True), _text(req.data, "name")))


def post_group_rename(req):
    from . import categories
    d = req.data
    return _rebuilding(req, categories.rename_group(req.record(create=True), _text(d, "name"), _text(d, "new_name"),
                                                    group_id=_id(d, "id")))


def post_group_reorder(req):
    from . import categories
    conn = req.record(create=True)
    if "ids" in req.data:
        ids, names = req.data["ids"], req.data.get("names")
        if not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
            raise ValueError("ids is a list of the buckets' ids")
        if names is not None and (not isinstance(names, list) or not all(isinstance(n, str) for n in names)):
            raise ValueError("names is a list of the buckets' names")
        return _rebuilding(req, categories.reorder_groups(conn, names, ids=ids))
    names = req.data.get("names")
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ValueError("names is a list of the buckets' names")
    return _rebuilding(req, categories.reorder_groups(conn, names))


def post_group_delete(req):
    from . import categories
    d = req.data
    return _rebuilding(req, categories.delete_group(req.record(create=True), _text(d, "name"), _text(d, "move_to"),
                                                    group_id=_id(d, "id"), move_to_id=_id(d, "move_to_id")))


def post_group_style(req):
    """G17 (U1): a bucket's icon and colour. A key left out keeps its value,
    null puts back the default; no stat reads them, so nothing rebuilds."""
    from . import categories
    d = req.data
    style = {}
    for k in ("icon", "colour"):
        if k in d:
            if d[k] is not None and not isinstance(d[k], str):
                raise ValueError(f"{k} is text or null")
            style[k] = d[k]
    if not style:
        raise ValueError("Send an icon, a colour or both")
    return categories.set_group_style(req.record(create=True), _text(d, "name"), group_id=_id(d, "id"), **style)


def post_rules_suggest(req):
    from . import ai, rules_app
    groups = [g for g in req.data.get("groups") or [] if isinstance(g, dict)]
    cats = rules_app.categories(req.record(create=True))
    # every group gets the rule-from-text guess, so a payee the model is not
    # asked about (a person) or a failed request still fills the table
    base = {"categories": cats, "drafts": {
        str(g.get("text", "")): rules_app.suggest(str(g.get("text", "")), g.get("field", "merchant"))
        for g in groups if g.get("text") and g.get("field") in rules_app.PERSONAL_FILE}}
    try:
        return {**ai.suggest(req.cfg, groups, cats, req.record(create=True)), **base}
    except ai.AIError as e:
        return {"enabled": True, "suggestions": [], "error": str(e), **base}


# G13: every rule write relinks the rows it decides differently at once, as a
# load would (server/finance/relink.py), and its Undo puts them back
def post_rules_remove(req):
    from . import rules_app
    d = req.data
    out = rules_app.remove(d.get("file", ""), d.get("line", 0), d.get("pattern"), d.get("revision"),
                           conn=req.record(create=True))
    return _followed(req.cfg, out)


def post_rules_batch(req):
    from . import rules_app
    return _followed(req.cfg, rules_app.add_many(req.record(create=True), req.data.get("rules")))


def post_rules_pin(req):
    from . import rules_app
    return _followed(req.cfg, rules_app.pin(req.record(create=True), req.data))


def post_rules_save(req):
    from . import rules_editor
    return _followed(req.cfg, rules_editor.save(req.record(create=True), req.data))


def post_rules_undo(req):
    from . import rules_app
    # G13: {change} undoes that write only, refused once a newer one is kept
    return _followed(req.cfg, rules_app.undo(req.data.get("change"), conn=req.record(create=True)))


def post_rules_apply(req):
    from . import rules_app
    return _followed(req.cfg, rules_app.apply(req.record(create=True), req.cfg))


def post_export(req):
    from . import export
    return export.export(req.record(), results_cache.results(req))


def _step(req, step, query):
    return upload_steps.logged_step(req.record(create=True), req.cfg, step, query, req.body)


def post_upload(req):
    return _step(req, "upload", {**req.query, "folder": req.query.get("folder", "")})


def post_upload_auto(req):
    """G13: an upload whose folder the statement's own text names
    (monthly.find_folder); one it cannot place is answered with
    `needs-account` and kept nowhere, not even in the history, until the
    owner names the account and the page sends it to /api/statements."""
    return _step(req, "upload", {**req.query, "folder": None})


def post_check(req):
    return _step(req, "check", req.query)


def post_load(req):
    return _step(req, "load", req.query)


def post_mirror(req):
    return _step(req, "mirror", req.query)


# G22: the AI card on Settings, and Ask Kopifund
def post_ai_settings(req):
    from . import ai
    from .api import config_or_none
    conn, cfg = req.record(create=True), config_or_none()
    ai.save_settings(conn, req.data, cfg)
    return ai.view(cfg, conn)


def post_ai_key(req):
    from . import ai
    from .api import config_or_none
    cfg, conn = config_or_none(), req.maybe_record()
    ai.save_key(cfg, conn, req.data)
    return ai.view(cfg, conn)


def post_ai_key_remove(req):
    from . import ai
    from .api import config_or_none
    cfg, conn = config_or_none(), req.maybe_record()
    ai.remove_key(cfg, conn, req.data)
    return ai.view(cfg, conn)


def post_ai_test(req):
    from . import ai
    from .api import config_or_none
    return ai.try_provider(config_or_none(), req.maybe_record())


def post_copilot_ask(req):
    from . import copilot
    from .api import Stream, config_or_none
    return Stream(copilot.start(config_or_none(), req.maybe_record(), req.data))


def post_copilot_threads_remove(req):
    from . import copilot
    return copilot.remove(req.data.get("id"))


# G25: the phone link (server/finance/link.py)
def post_pair_token(req):
    """Mint a single-use pair token and answer the QR payload. Loopback
    only in practice: the link path refuses this write."""
    from . import link
    url = link.tailnet_url()
    if not url:
        # a code without the Mac's address pairs nothing: the phone refuses it
        raise Refused("Tailscale cannot name this Mac, so a pairing code would carry no address. "
                      "Run make link, fix what it lists, then try again")
    token, _expires = link.mint_pair_token()
    return {"payload": link.pair_payload(url, token), "url": url, "expires_in": link.PAIR_LIFE}


def post_pair_redeem(req):
    """Spend a pair token for a device bearer: the one link write with no
    bearer, since the token is the credential."""
    from . import link
    d = req.data
    bearer, device = link.redeem(_text(d, "token"), _text(d, "name"), _text(d, "nonce"))
    return {"device_id": device["id"], "name": device["name"], "token": bearer}


def post_devices_revoke(req):
    from . import link
    link.revoke(_text(req.data, "id"))
    return {"devices": link.list_devices()}


def post_link_transactions(req):
    """The one bearer write: the phone's approved alert rows upsert into
    pending_txn by their import_id. `req.device` is the paired device the
    gate resolved; a loopback call (a test) carries none and writes ""."""
    from . import pending
    device_id = req.device["id"] if req.device else ""
    conn = req.record(create=True)
    return pending.post_rows(conn, device_id, req.data.get("rows"))


def _pending_list(req):
    from . import link, pending
    names = {d["id"]: d["name"] for d in link.list_devices()}
    return {"rows": pending.rows(req.record(create=True), names)}


def post_pending_dismiss(req):
    """A waiting row is noise: the owner says so, it never matches."""
    from . import pending
    pending.dismiss(req.record(create=True), _text(req.data, "import_id"))
    return _pending_list(req)


def post_pending_dismiss_all(req):
    """Every waiting row dismissed at once: the owner's way to clear rows no
    statement will ever confirm. Each can still be put back."""
    from . import pending
    pending.dismiss_all(req.record(create=True))
    return _pending_list(req)


def post_pending_restore(req):
    """Put a matched or dismissed row back to waiting; a match it was in is
    recorded on its rejected list, so the same pair is never offered again."""
    from . import pending
    pending.restore(req.record(create=True), _text(req.data, "import_id"))
    return _pending_list(req)


# G26: the dashboard's state and its queries (server/finance/dashboard.py)
def post_dashboard_layouts(req):
    from . import dashboard
    return dashboard.save_layouts(req.record(create=True), req.data)


def post_dashboard_layout(req):
    from . import dashboard
    return dashboard.save_layout(req.record(create=True), req.args["id"], req.data)


def post_dashboard_query(req):
    """The same answer as the GET, for a caller that posts the query as the
    body. It writes nothing, so it sits in NO_CHANGE."""
    from . import dashboard
    return dashboard.resolve(req.maybe_record(), results_cache.results_view(req), req.data)


def post_dashboard_goal(req):
    from . import dashboard
    return dashboard.save_goal(req.record(create=True), results_cache.results(req), req.data)


def post_dashboard_goal_remove(req):
    from . import dashboard
    return dashboard.remove_goal(req.record(create=True), results_cache.results(req), req.data)


def post_dashboard_scenario_preview(req):
    """The query's series drawn ahead: an estimate, and by design never
    written to the plan or anywhere else."""
    from . import dashboard
    return dashboard.scenario_preview(req.maybe_record(), results_cache.results_view(req), req.data)


def post_dashboard_allocation(req):
    from . import dashboard
    return dashboard.save_allocation(req.record(create=True), req.data)


def post_dashboard_game_pot(req):
    from . import dashboard
    return dashboard.save_game_pot(req.record(create=True), req.data)
