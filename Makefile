# Commands are the ones named in CLAUDE.md. Targets whose step has not landed yet
# (run, review, export) fail loudly rather than doing half a job.

SHELL := /bin/bash
COMPOSE := docker compose
S ?=

.DEFAULT_GOAL := help

.PHONY: help up down logs migrate seed filter translate score test lint fmt fetch run status golden review export

help: ## List targets
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-10s %s\n", $$1, $$2}'

up: ## Start Postgres, wait for healthy, apply migrations, seed the registry
	@test -f .env || { echo "no .env; copy .env.example to .env and fill it in"; exit 1; }
	$(COMPOSE) up -d postgres
	@echo "waiting for postgres to report healthy"
	@for i in $$(seq 1 30); do \
		state=$$(docker inspect -f '{{.State.Health.Status}}' monitor-postgres 2>/dev/null); \
		if [ "$$state" = "healthy" ]; then echo "postgres: healthy"; exit 0; fi; \
		sleep 2; \
	done; \
	echo "postgres did not become healthy; run 'make logs'"; exit 1
	$(MAKE) migrate
	$(MAKE) seed

migrate: ## Apply migrations/*.sql in order, once each
	uv run python -m monitor.migrate

seed: ## Load sources/*.yaml and config/*.yaml into the database
	uv run python -m monitor.registry

down: ## Stop the stack, keep the data volume
	$(COMPOSE) down

logs: ## Tail Postgres logs
	$(COMPOSE) logs -f postgres

test: ## ruff check and the test suite
	uv run ruff check .
	uv run pytest

lint: ## ruff check only
	uv run ruff check .

fmt: ## ruff format
	uv run ruff format .

fetch: ## Run one connector once: make fetch S=ted
	@test -n "$(S)" || { echo "usage: make fetch S=<source_id>"; exit 1; }
	uv run python -m monitor.cli fetch $(S)

filter: ## Run the free filter over every notice not yet filtered
	uv run python -m monitor.cli filter

translate: ## Translate notices held for want of a lexicon, then re-filter them
	uv run python -m monitor.cli translate

score: ## Score every notice the free filter passed
	uv run python -m monitor.cli score

run: ## One full pass: fetch all, filter, score, dedupe, stage
	uv run python -m monitor.cli run

status: ## Source health, today's calls and cost, queue depth, export backlog
	uv run python -m monitor.cli status

golden: ## Precision, recall and schema validity for the current prompt version
	uv run python -m monitor.cli golden

review: ## Serve the review app on 127.0.0.1:8080
	uv run uvicorn review.app:app --host 127.0.0.1 --port 8080

export: ## Produce a CSV batch and its manifest from approved, unexported records
	uv run python -m review.export
