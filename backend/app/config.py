"""Application configuration.

All environment-driven knobs live here, loaded once via ``pydantic-settings`` from the
process environment and an optional ``.env`` file. Nothing else in the app reads
``os.environ`` directly — a single typed ``settings`` object is the one source of truth,
which keeps the pure ``core/`` package free of any environment coupling.

Both the LLM layer and the vector index are deliberately optional: with no
``openai_api_key`` set, the platform still runs end to end on the deterministic JSON
rule engine plus offline fallbacks (see ``app/llm/``), so a live demo never depends on a
network round-trip or a valid key.
"""
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- Application ---------------------------------------------------------
    app_name: str = "DecisionLedger Decision Automation Platform"
    environment: str = "development"
    debug: bool = False
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8000

    # --- CORS ------------------------------------------------------------------
    cors_origins: List[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    # --- Auth (JWT + MongoDB) --------------------------------------------------
    # Sessions are stateless HS256 JWTs signed with ``auth_secret``. The default is
    # deliberately insecure so the platform runs out of the box in dev — ALWAYS override
    # it in production via the environment / .env.
    auth_secret: str = "dev-insecure-change-me"
    auth_token_ttl_minutes: int = 720

    # --- MongoDB ---------------------------------------------------------------
    # One deployment backs users, the authoritative ruleset repository, and the vector
    # index. Ruleset/audit collection names are fixed (`ruleset_versions`, `audit_log`)
    # in app/stores/ruleset_repository.py and app/audit.py; only the collections that
    # are actually parameterised are configurable here.
    # No real deployment should run on this default — it points at the local Docker
    # Mongo the demo brings up (see docker-compose.yml). Point MONGODB_URI at Atlas (or
    # any other real deployment) via .env; never hardcode real credentials here, this
    # file is tracked in git.
    mongodb_uri: str = "mongodb://localhost:27017/"
    mongodb_database: str = "decision_ledger"
    mongodb_users_collection: str = "users"
    mongodb_vector_collection: str = "rule_vectors"
    mongodb_vector_index: str = "rules_vector_index"

    # --- At-rest encryption for ruleset content (Fernet) ------------------------
    # No safe default for a real deployment — set DECISION_LEDGER_ENCRYPTION_KEY via
    # .env. get_fernet() (app/encryption.py) raises a clear error if this is left empty.
    decision_ledger_encryption_key: str = ""

    # --- LLM / RAG ---------------------------------------------------------------
    # When empty, the explainer, rule generator, and vector-fallback recommender all
    # fall back to deterministic, offline implementations. Set OPENAI_API_KEY via .env
    # to light up the live LangChain path — never hardcode a real key here.
    openai_api_key: str = ""
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.0
    llm_timeout_seconds: int = 20
    enable_llm_explanations: bool = True

    # --- Vector index (fallback-only semantic search) ---------------------------
    #   VECTOR_BACKEND: "auto" -> MongoDB Atlas Vector Search if reachable (needs
    #   OPENAI_API_KEY for embeddings), else the dependency-free in-memory fallback;
    #   "mongodb" forces Atlas; "memory" forces the offline fallback (hermetic tests).
    vector_backend: str = "auto"
    # Similarity threshold (0-1, higher = more similar) a vector-search hit must clear
    # before the decision service will act on it as a fallback recommendation.
    vector_search_threshold: float = 0.75
    # Memory-backend durability: a JSON file the fallback index persists to across
    # restarts. Empty -> purely in-memory (used by the hermetic test suite).
    rules_store_path: str = ""

    # --- Decision fallback policy -------------------------------------------------
    # Verdict returned when the JSON engine has no match anywhere for a request AND
    # vector search finds no similar rule above vector_search_threshold.
    fallback_verdict: str = "REFER"

    # --- External API Integrations ------------------------------------------------
    bureau_api_url: str = ""
    bureau_api_key: str = ""
    bureau_timeout_seconds: float = 5.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @property
    def llm_enabled(self) -> bool:
        """True only when explanations are switched on AND a key is actually present."""
        return self.enable_llm_explanations and bool(self.openai_api_key.strip())


settings = Settings()
