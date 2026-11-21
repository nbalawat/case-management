.PHONY: backbone backbone-cdc demo dev-api dev-ui test seed build reset

demo:            ## Docker: Postgres + app on http://localhost:8000
	docker compose up --build

# An Iceberg catalog kept in the demo's own Postgres, with the tables on a Docker volume.
LAKE := {"type":"sql","uri":"postgresql+psycopg://caseforge:caseforge-local-only@db:5432/caseforge","warehouse":"file:///app/data/warehouse"}

backbone:        ## Docker: the demo plus Kafka, the outbox relay, the stream consumers and the Iceberg lake
	EVENT_BUS_URL=kafka://kafka:9092 LAKE_CATALOG='$(LAKE)' docker compose --profile backbone up --build

backbone-cdc:    ## Docker: the same, with Debezium publishing the outbox instead of the relay
	EVENT_BUS_URL=kafka://kafka:9092 LAKE_CATALOG='$(LAKE)' OUTBOX_PUBLISHER=debezium docker compose --profile backbone --profile cdc up --build

build:           ## Build the UI so the API can serve it
	cd frontend && npm install && npm run build

run: build       ## No Docker: SQLite + app on http://localhost:8000
	cd backend && uv run uvicorn app.main:app --port 8000

dev-api:         ## API with reload (pair with dev-ui)
	cd backend && uv run uvicorn app.main:app --reload --port 8000

dev-ui:          ## UI dev server on http://localhost:5173
	cd frontend && npm run dev

test:
	cd backend && uv run pytest -q

seed:            ## Reset the demo data
	cd backend && uv run python -m app.seed

reset:           ## Docker: wipe the database and document volumes
	docker compose down -v
