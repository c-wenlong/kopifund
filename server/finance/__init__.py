"""The monthly statement sync: parse, categorise, doctor, upsert, verify.

finance.py is the entry point; config.toml in the data folder (server/finance/paths.py) holds
every workspace fact
(data-source ids, account and card pages, statement folders).
"""
