"""The two answers an endpoint gives instead of its JSON (G19).

serve.py turns `NotFound` into a 404 and `Refused` into a 422, whichever
module raised it: the endpoint answers in server/finance/api.py and api_writes.py,
the upload steps, and the stat results cache, which has no NotFound of its own.
"""


class NotFound(Exception):
    pass


class Refused(Exception):
    """A write the app asked for and the record will not take, with the reason."""
