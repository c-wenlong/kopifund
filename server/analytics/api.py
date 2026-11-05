"""What the Net worth endpoints answer (G19: moved out of serve.py): the
stats' net worth by month (GET /api/net-worth) and the projection on the saved
plan (GET /api/growth), on unsaved inputs (POST /api/growth/preview) or after
saving them (POST /api/growth/inputs). They live in analytics, beside
growth.py, since finance never imports analytics; the rest of the endpoint
answers are in server/finance/api.py and server/finance/api_writes.py.
"""

from finance import api, results_cache, stats_rebuild
from finance.errors import NotFound, Refused


def get_net_worth(req):
    from analytics import growth
    return growth.net_worth(results_cache.results(req))


def get_growth(req):
    return _growth(req, query=req.query)


def _saved_plan(req, results):
    """The plan and the record's spending a month over the months the stats
    (`results`) call full, or ({}, None) with no record yet."""
    from finance import planning, spending
    conn = req.maybe_record()
    if conn is None:
        return {}, None                  # no record yet: no pay and no investing transfer
    return planning.get_plan(conn), spending.monthly_average(conn, results=results)


def _growth(req, query=None, inputs=None):
    """G14: the Net worth projection on the saved plan, or on the saved plan
    with the page's unsaved inputs in place of its own (a preview; nothing is
    written). An input the plan would refuse is refused here the same way."""
    from analytics import growth
    from finance import planning, profile
    results = results_cache.results(req)
    plan, hint = _saved_plan(req, results)
    if inputs is not None:
        try:
            plan = planning.clean_plan(planning.merge_inputs(plan, planning.plan_in(inputs)))
        except planning.PlanRefused as e:
            raise Refused(str(e)) from e
    return growth.project(results, plan, profile.load().get("birth_date"), query or {},
                          spend_hint=hint)


def post_growth_preview(req):
    # G14: a POST only because the unsaved inputs ride in the body; it writes nothing
    return _growth(req, inputs=req.data)


def post_growth_inputs(req):
    from finance import planning
    planning.save_inputs(req.record(create=True), planning.plan_in(req.data))
    try:
        # G13: the forward view on Plan reads them; Net worth watches the rebuild
        stats_rebuild.refresh_stats(api.config())
    except NotFound:
        pass
    return _growth(req)
