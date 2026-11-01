"""The [analytics] table of config.toml, read the same way by the stats and the app.

G10: what is true of one user's life (where they lived and when, who they
repay, their age band) is a setting in the data folder's config.toml under
[analytics], never a constant in this code. Every reader falls back to a
neutral default when the file, the table or the key is missing or malformed,
so a setting can never break a stat. server/analytics/SPEC.md, "Settings", lists them.

G19: the reader lives in finance because the app reads these settings too
(the repayment payee, the long-stay length), and finance never imports
analytics. server/analytics/stats/common.py re-exports `settings`, `setting` and
`text_setting`, so a stat reads them as before.
"""
import tomllib
from pathlib import Path

# None reads the data folder's config.toml. A test points this at a file of its
# own, so the suite never depends on one machine's settings.
SETTINGS_FILE = None
_settings_cache = [None, {}]

# A stay longer than this is somewhere lived in rather than somewhere travelled
# to: a term abroad or a season working away, not a long weekend. Two months
# is the default; [analytics] long_stay_days in config.toml changes it (G10).
LONG_STAY_DAYS = 60


def settings():
    """The [analytics] table of config.toml as a dict, or {} when there is none."""
    if SETTINGS_FILE:
        path = Path(SETTINGS_FILE)
    else:
        from .paths import config_path
        path = config_path()
    try:
        st = path.stat()
    except OSError:
        return {}
    key = (str(path), st.st_mtime_ns, st.st_size)
    if _settings_cache[0] != key:
        try:
            table = tomllib.loads(path.read_text(encoding="utf-8")).get("analytics")
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            table = None
        _settings_cache[0] = key
        _settings_cache[1] = table if isinstance(table, dict) else {}
    return _settings_cache[1]


def setting(key, default=None):
    """One [analytics] value, or `default` when it is missing or empty."""
    value = settings().get(key)
    return default if value is None or value == "" else value


def text_setting(key):
    """A string setting, stripped, or "" when it is missing or not a string."""
    value = setting(key, "")
    return value.strip() if isinstance(value, str) else ""


def long_stay_days():
    """[analytics] long_stay_days, or LONG_STAY_DAYS when it is unset or not a
    positive whole number of days."""
    value = setting("long_stay_days", LONG_STAY_DAYS)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return LONG_STAY_DAYS
    return value
