.PHONY: backup backup-drill observability lineage load-test lake-backfill migration full streaming flink-job backbone backbone-cdc demo dev-api dev-ui test seed build reset infra-fmt infra-contract infra-validate infra-test infra-lint infra-scan infra-lock charts-check

demo:            ## Docker: Postgres + app on http://localhost:8000
	docker compose up --build

# An Iceberg catalog kept in the demo's own Postgres, with the tables on a Docker volume.
GRAPH := age://caseforge:caseforge-local-only@graph:5432/graph
LAKE := {"type":"sql","uri":"postgresql+psycopg://caseforge:caseforge-local-only@db:5432/caseforge","warehouse":"file:///app/data/warehouse"}

backbone:        ## Docker: the demo plus Kafka, the outbox relay, the stream consumers and the Iceberg lake
	EVENT_BUS_URL=kafka://kafka:9092 LAKE_CATALOG='$(LAKE)' docker compose --profile backbone up --build

backbone-cdc:    ## Docker: the same, with Debezium publishing the outbox instead of the relay
	EVENT_BUS_URL=kafka://kafka:9092 LAKE_CATALOG='$(LAKE)' OUTBOX_PUBLISHER=debezium docker compose --profile backbone --profile cdc up --build

full:            ## Docker: the demo with Kafka, the Iceberg lake, OpenSearch, the graph engine (Apache AGE) and Temporal
	EVENT_BUS_URL=kafka://kafka:9092 LAKE_CATALOG='$(LAKE)' SEARCH_URL=opensearch://opensearch:9200 GRAPH_URL=$(GRAPH) TEMPORAL_URL=temporal://temporal:7233 docker compose --profile backbone --profile search --profile graph --profile durable up --build

observability:   ## Docker: everything in `full`, plus Prometheus (http://localhost:9090), Grafana (http://localhost:3000) and Jaeger (http://localhost:16686)
	EVENT_BUS_URL=kafka://kafka:9092 LAKE_CATALOG='$(LAKE)' SEARCH_URL=opensearch://opensearch:9200 GRAPH_URL=$(GRAPH) TEMPORAL_URL=temporal://temporal:7233 OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318 LOG_FORMAT=json docker compose --profile backbone --profile search --profile graph --profile durable --profile observability up --build

lineage:         ## Docker: everything in `full`, plus Marquez, an OpenLineage catalog that every lineage event is also sent to (http://localhost:3001)
	EVENT_BUS_URL=kafka://kafka:9092 LAKE_CATALOG='$(LAKE)' SEARCH_URL=opensearch://opensearch:9200 GRAPH_URL=$(GRAPH) TEMPORAL_URL=temporal://temporal:7233 LINEAGE_URL=http://marquez:5000/api/v1/lineage docker compose --profile backbone --profile search --profile graph --profile durable --profile lineage up --build

streaming:       ## Docker: everything in `full`, plus an Apache Flink cluster for stream detection (console on http://localhost:8081)
	EVENT_BUS_URL=kafka://kafka:9092 LAKE_CATALOG='$(LAKE)' SEARCH_URL=opensearch://opensearch:9200 GRAPH_URL=$(GRAPH) TEMPORAL_URL=temporal://temporal:7233 FLINK_URL=http://flink-jobmanager:8081 FLINK_GATEWAY_URL=http://flink-sql-gateway:8083 docker compose --profile backbone --profile search --profile graph --profile durable --profile streaming up --build

CLIENT ?= meridian
JOB ?= structuring
flink-job:       ## Docker, with `make streaming` running: render and submit one client's job by hand (the Integrations page does the same). make flink-job CLIENT=meridian JOB=rapid_movement
	mkdir -p deploy/flink/jobs
	docker compose exec -T app uv run --no-dev python -m app.streamjobs $(JOB) $(CLIENT) --bootstrap kafka:9092 > deploy/flink/jobs/$(CLIENT)-$(JOB).sql
	docker compose exec flink-jobmanager ./bin/sql-client.sh -f /jobs/$(CLIENT)-$(JOB).sql

lake-backfill:   ## Docker, with `make full` running: copy a client's file-loaded transactions into the lake. make lake-backfill CLIENT=meridian
	docker compose exec -T app uv run --no-dev python -m app.lake.backfill $(CLIENT)

load-test:       ## Docker, with `make backbone` or `make full` running: transactions through Kafka, the stream consumers and the lake, timed. make load-test N=200000
	docker compose exec -T app uv run --no-dev python -m app.loadtest run --transactions $(or $(N),20000) --api http://localhost:8000
	mkdir -p data/loadtest && docker compose cp app:/app/data/loadtest/. data/loadtest/

backup:          ## Docker: back up the whole platform (or CLIENT=meridian) to BACKUP_URL, data/backups by default
	docker compose exec -T app uv run --no-dev python -m app.backup create $(if $(CLIENT),--client $(CLIENT))

backup-drill:    ## Docker: back up, restore into a scratch database, and compare every table
	docker compose exec -T app uv run --no-dev python -m app.backup drill

build:           ## Build the UI so the API can serve it
	cd frontend && npm install && npm run build

run: build       ## No Docker: SQLite + app on http://localhost:8000
	cd backend && uv run uvicorn app.main:app --port 8000

dev-api:         ## API with reload (pair with dev-ui)
	cd backend && uv run uvicorn app.main:app --reload --port 8000

dev-ui:          ## UI dev server on http://localhost:5173
	cd frontend && npm run dev

migration:       ## After changing a model: make migration m="what changed" writes the next file in backend/app/migrations/versions
	cd backend && uv run python -m app.migrate revision "$(m)"

test:
	cd backend && uv run pytest -q

seed:            ## Reset the demo data
	cd backend && uv run python -m app.seed

reset:           ## Docker: wipe the database and document volumes
	docker compose down -v

# ---- Infrastructure as code and charts (infra/README.md). Nothing here touches a cloud account.
TOFU ?= tofu
infra-fmt:       ## Format every OpenTofu file
	$(TOFU) fmt -recursive infra

infra-contract:  ## Copy the stamp contract's variables into each cloud's stamp module and check the outputs agree
	python3 infra/contract/check.py --fix

infra-validate:  ## Format check, contract check and `tofu validate` in every module and stack
	$(TOFU) fmt -check -recursive infra
	python3 infra/contract/check.py
	TOFU=$(TOFU) infra/bin/each.sh validate

infra-test:      ## `tofu test` against mocked providers
	TOFU=$(TOFU) infra/bin/each.sh test

infra-lint:      ## TFLint with the AWS, Azure and Google rulesets
	cd infra && tflint --init --config=$(CURDIR)/infra/.tflint.hcl
	cd infra && tflint --recursive --chdir=modules --config=$(CURDIR)/infra/.tflint.hcl
	cd infra && tflint --recursive --chdir=stacks --config=$(CURDIR)/infra/.tflint.hcl

infra-scan:      ## Checkov over the IaC (every skip carries its reason)
	cd infra && checkov -d . --framework terraform --quiet --compact --skip-path .tofu

infra-lock:      ## Write provider lock files for the platforms CI and laptops use
	infra/bin/each.sh lock

charts-check:    ## Lint and render every chart, then check it strictly against Kubernetes and CRD schemas
	deploy/scripts/crd-schemas.sh .schemas
	deploy/scripts/check-charts.sh .schemas
