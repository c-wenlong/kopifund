# Kopifund: the commands worth remembering.
# `make` or `make help` lists them. Python runs as python3: the server code is
# in server/, the apps in apps/, the shipped rules in data/rules/; every
# personal file is in the data folder, $(DATA).

PY      ?= python3
# made absolute in the shell, not with $(abspath), which splits a path at its spaces;
# Make passes in its own KOPIFUND_DATA, so `make KOPIFUND_DATA=... <target>` counts too
ifndef DATA
DATA    := $(shell d="$(KOPIFUND_DATA)"; d="$${d:-$$HOME/.kopifund}"; case "$$d" in (/*) ;; (*) d="$(CURDIR)/$$d" ;; esac; printf '%s' "$$d")
endif
CACHE   := $(DATA)/cache
RULES   := $(CURDIR)/data/rules
PORT    ?= 8770
URL     := http://127.0.0.1:$(PORT)

.DEFAULT_GOAL := help
.PHONY: help install build types serve dev desktop app-icon test check setup migrate doctor load import-notion plan sync report rollup \
        payees options budget budget-plan lint why analyse refresh stats \
        review open-items people backup clean-cache link

help: ## list the commands
	@awk 'BEGIN{FS=":.*## "} /^[a-zA-Z_-]+:.*## /{printf "  %-16s %s\n", $$1, $$2} /^## /{printf "\n%s\n", substr($$0,4)}' $(MAKEFILE_LIST)

## The app
install: ## install the web app's npm packages
	cd apps/web && npm ci

build: ## build the web app into apps/web/dist
	cd apps/web && npm run build

types: ## write apps/web/src/types.gen.ts from finance/types.py and serve.py (after changing what an endpoint sends)
	cd server && $(PY) -m finance.gen_types

serve: build ## build the app, then serve it and the API at $(URL)
	$(PY) server/serve.py --port $(PORT)

link: ## the phone link's checklist (G25): tailscale serve must proxy 443 to 127.0.0.1:$(PORT), and the API up
	$(PY) server/finance.py link --port $(PORT)

# one Rust build cache for make check, make desktop and build_macos.sh, so a new
# checkout does not compile the Tauri tree from cold; make desktop and
# build_macos.sh take one lock, since they also share the bundle folder
CARGO_TARGET = $${CARGO_TARGET_DIR:-$$HOME/Library/Caches/Kopifund/cargo-target}

desktop: build ## build Kopifund.app, the desktop app (G23), and put it in ~/Applications
	@set -e; export CARGO_TARGET_DIR="$(CARGO_TARGET)"; \
	  lock="$$HOME/Library/Application Support/Kopifund"; mkdir -p "$$lock"; exec 9>"$$lock/build.lock.file"; \
	  $(PY) -c 'import fcntl; fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)' 2>/dev/null \
	    || { echo "make desktop: another build (build_macos.sh or make desktop) is running; wait for it to finish" >&2; exit 1; }; \
	  case "$$CARGO_TARGET_DIR" in (/*) ;; (*) CARGO_TARGET_DIR="$(CURDIR)/apps/desktop/src-tauri/$$CARGO_TARGET_DIR" ;; esac; \
	  built="$$CARGO_TARGET_DIR/release/bundle/macos/Kopifund.app"; \
	  rm -rf "$$built"; \
	  (cd apps/desktop && npm ci && npm run tauri -- build --bundles app -- --locked); \
	  [ -d "$$built" ] || { echo "make desktop: the build finished but $$built is not there" >&2; exit 1; }; \
	  apps="$(HOME)/Applications"; mkdir -p "$$apps"; \
	  rm -rf "$$apps/.Kopifund.app.new" "$$apps/.Kopifund.app.old"; cp -R "$$built" "$$apps/.Kopifund.app.new"; \
	  if [ -e "$$apps/Kopifund.app" ]; then mv "$$apps/Kopifund.app" "$$apps/.Kopifund.app.old"; fi; \
	  mv "$$apps/.Kopifund.app.new" "$$apps/Kopifund.app" \
	    || { [ ! -e "$$apps/.Kopifund.app.old" ] || mv "$$apps/.Kopifund.app.old" "$$apps/Kopifund.app"; exit 1; }; \
	  rm -rf "$$apps/.Kopifund.app.old"
	@echo "Kopifund.app is in ~/Applications: open it from Launchpad or Spotlight, then keep it in the Dock"

app-icon: ## write app-icons/ into the web, iOS and desktop apps; SET=iso makes iso the default first
	cd apps/desktop && npm ci
	$(PY) app-icons/apply.py $(SET)

dev: ## the app with hot reload on http://localhost:5173 (port $(PORT) sends you there): the API restarts on a Python change, the page on a web change
	@$(PY) server/serve.py --reload --port $(PORT) --page http://localhost:5173 & api=$$!; \
	(cd apps/web && KOPIFUND_API_PORT=$(PORT) exec npm run dev -- --port 5173 --strictPort) & web=$$!; \
	trap 'kill $$api $$web 2>/dev/null' INT TERM EXIT; \
	while kill -0 $$api 2>/dev/null && kill -0 $$web 2>/dev/null; do sleep 1; done; \
	if kill -0 $$api 2>/dev/null; then wait $$web; else wait $$api; fi

## Checks
test: ## Python tests, then the web tests
	cd server && $(PY) -m unittest discover tests
	cd apps/web && npm test

check: test lint ## the tests, the rules lint, the runbook table, the record schema doc, the parsing doc's layouts table, the TypeScript typecheck and the desktop shell's Rust tests: run before every push
	$(PY) docs/gen_runbook.py --check
	$(PY) -m docs.gen_schema --check
	$(PY) -m docs.gen_parsing --check
	cd apps/web && npm run typecheck
	@if [ "$$(uname)" = Darwin ] && command -v cargo >/dev/null; then \
	  cd apps/desktop/src-tauri && CARGO_TARGET_DIR="$(CARGO_TARGET)" cargo test --locked --quiet; \
	else echo "skipped the desktop shell's cargo test: it runs on a Mac with Rust installed"; fi

## Setup, once
setup: ## install pdftotext and check the token
	@command -v pdftotext >/dev/null || brew install poppler
	@echo "Token: [notion] token_command in $(DATA)/config.toml reads it from the Keychain (or export NOTION_TOKEN)"
	@$(PY) server/finance.py doctor >/dev/null 2>&1 && echo "Notion reachable, doctor clean" || echo "Run 'make doctor' to see what is missing"

migrate: ## copy a finance-tracking folder into $(DATA): make migrate FROM=<folder>
	@test -n "$(FROM)" || (echo "usage: make migrate FROM=<finance-tracking folder>"; exit 1)
	$(PY) server/finance.py migrate --from "$(FROM)"

## The monthly run (new PDFs into $(DATA)/statements/<account>/<year>/ first)
doctor: ## parse, categorise, run every check, write nothing
	$(PY) server/finance.py doctor

load: ## parse, check and write kopifund.db (the record); no Notion
	$(PY) server/finance.py load

import-notion: ## read categories, budgets, trips, accounts, cards and [plan] from Notion into kopifund.db
	$(PY) server/finance.py import-notion

plan: ## the full dry run: what sync would add, change, and what needs a rule
	$(PY) server/finance.py sync --dry-run

sync: ## the run with Notion: refuses on a problem, reads back, writes kopifund.db, then Notion
	$(PY) server/finance.py sync

report: ## current figures: rows, blocks, coverage, budgets, trips, needs a rule
	$(PY) server/finance.py report

rollup: ## redo trips, budget actuals, coverage and recurring flags (after editing a trip or budget)
	$(PY) server/finance.py rollup

payees: ## sync the Payees table from the rules (after adding or renaming payees)
	$(PY) server/finance.py payees

options: ## add missing select options to Notion (doctor names them)
	$(PY) server/finance.py add-options

budget-plan: ## show the Budgets rows 'make budget' would seed: make budget-plan MONTH=2026-10
	@test -n "$(MONTH)" || (echo "usage: make budget-plan MONTH=YYYY-MM"; exit 1)
	$(PY) server/finance.py budget --seed $(MONTH) --dry-run

budget: ## create next month's Budgets rows, Planned = 6-month median, never overwrites a typed Planned
	@test -n "$(MONTH)" || (echo "usage: make budget MONTH=YYYY-MM"; exit 1)
	$(PY) server/finance.py budget --seed $(MONTH)

## Rules
lint: ## dead, shadowed or over-broad rules against the latest parsed rows (needs the data folder)
	@if [ -d "$(DATA)/statements" ]; then \
	  $(MAKE) --no-print-directory _rules-input && \
	  cd server && $(PY) -m finance.parsers.categorise --lint "$(CACHE)/rules-input.csv" --rules "$(RULES)" --rules "$(DATA)/rules" || true; \
	else echo "lint: no $(DATA)/statements, skipped"; fi

why: ## explain one row's payee and category: make why KEY=<txn_key>
	@test -n "$(KEY)" || (echo "usage: make why KEY=<txn_key>"; exit 1)
	@test -f "$(CACHE)/rules-input.csv" || $(MAKE) --no-print-directory _rules-input
	cd server && $(PY) -m finance.parsers.categorise --why $(KEY) "$(CACHE)/rules-input.csv" --rules "$(RULES)" --rules "$(DATA)/rules"

_rules-input: ## the configured parsers' rows into $(CACHE)/rules-input.csv (and $(CACHE)/parsed)
	@cd server && $(PY) -c 'from finance import sync; from finance.model import load_config; sync.write_rules_input(load_config())'


## Analytics
analyse: ## build today's snapshot from kopifund.db (else Notion) if missing, run all 15 stats into $(CACHE)
	$(PY) server/finance.py analyse

refresh: ## force a fresh snapshot from Notion, then all stats
	$(PY) server/finance.py analyse --refresh

stats: ## print every stat's findings from the cache, no Notion access
	@$(PY) -c "import json; L=json.load(open('$(CACHE)/latest.json')); \
	  [ (lambda r: print('\n## '+r['title']+'  ('+r['generated_at'][:10]+')\n'+'\n'.join('- '+f for f in r['findings']))) (json.load(open('$(CACHE)/results/'+p))) \
	    for p in L['results'].values() ]"

## Notes and housekeeping
review: ## open the advisor review and the spending profile
	open "$(DATA)/notes/analytics/advisor-review-2026-09.md" "$(DATA)/notes/analytics/spending-profile-2026-09.md"

open-items: ## what is still unexplained, assumed, or waiting on you
	@sed -n '1,80p' "$(DATA)/notes/open-items.md"

people: ## the person payees still masked or numeric, biggest first
	@sed -n '1,60p' "$(DATA)/notes/people.md"

backup: ## archive the record, rules, config and notes into $(DATA)/backups/ (no cache, no media, no secrets)
	@set -e; mkdir -p "$(DATA)/backups"; chmod 700 "$(DATA)/backups"; \
	  out="$(DATA)/backups/kopifund-$$(date +%Y-%m-%d-%H%M).tar.gz"; \
	  cd "$(DATA)" && tar -czf "$$out" --exclude='notes/*.mp4' --exclude='notes/**/*.mp4' --exclude='.env' \
	    $$(ls -d kopifund.db rules config.toml SOURCES.yml benchmarks.json places.csv notes documents 2>/dev/null); \
	  chmod 600 "$$out"; echo "wrote $$out"

clean-cache: ## delete everything in $(CACHE): it all rebuilds
	rm -rf "$(CACHE)"/*
