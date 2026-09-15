# Every target here is a command, not a file. Without this, `make backend`
# and `make frontend` do nothing at all, because directories by those names
# exist and make treats the target as already satisfied.
.PHONY: setup uv_sync frontend_install sample load graph claims storage \
        validate report dev backend frontend build start stop clean install_uv

# Quickest path from clone to something on screen:
#   make setup && make sample && make dev

setup: uv_sync frontend_install
	@echo "Copy backend/env.example to backend/.env before running."

uv_sync:
	cd backend && uv venv && uv sync

frontend_install:
	cd frontend && npm install

sample:
	cd backend && uv run python scripts/make_sample_data.py

load:
	cd backend && uv run python scripts/load.py --drop

graph:
	cd backend && uv run python scripts/load_graph.py

claims:
	cd backend && uv run python scripts/load_claims.py --drop

storage:
	cd backend && uv run python scripts/estimate_storage.py

validate:
	cd backend && uv run python scripts/validate_mappings.py

report:
	cd backend && uv run python scripts/quality_report.py

dev:
	@echo "Backend on :8000, frontend on :3000. Ctrl-C stops both."
	cd backend && uv run uvicorn main:app --reload --port 8000 & \
	cd frontend && npm run dev

backend:
	cd backend && uv run uvicorn main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

build:
	docker-compose up --build -d

start:
	docker-compose start

stop:
	docker-compose stop

clean:
	docker-compose down --rmi all -v

install_uv:
	curl -LsSf https://astral.sh/uv/install.sh | sh
