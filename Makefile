.PHONY: build up down logs migrate test

build:
	docker compose build --no-cache

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f

migrate:
	# run migrations inside app container (migrations SQL executed by db runner too)
	docker compose run --rm app python -c "from src.app.db import run_migrations; import asyncio; asyncio.run(run_migrations())"

test:
	docker compose run --rm app pytest -q
