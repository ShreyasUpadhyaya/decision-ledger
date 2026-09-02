.PHONY: test backend frontend run

# Run the backend test suite (same command CI and backend/README.md use).
test:
	cd backend && python -m pytest -q

# Start the backend API with autoreload for local development.
backend:
	cd backend && uvicorn app.main:app --reload

# Start the frontend dev server.
frontend:
	cd frontend && npm run dev

# Bring up the full stack (see README.md quickstart).
run:
	./run.sh
