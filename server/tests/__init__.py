"""The test suite.

The canonical command, from server/:

    python3 -m unittest discover tests

Every test module imports its fixtures by plain name (`from fake_notion import
...`) and the code under test as a package (`from finance import sync`,
`from finance.parsers import parse_statements`). `discover tests` puts the
tests directory on the path and `python3 -m` puts server/, so both work
with nothing added to sys.path (G19). One module on its own:

    python3 -m unittest discover tests -p test_sync.py
"""

from . import isolate  # noqa: F401  the data folder, record and cache go to a throwaway folder
