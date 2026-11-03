"""Runtime settings, read from the environment or the case-level .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

CASE_DIR = Path(__file__).resolve().parents[2]
SQL_DIR = CASE_DIR / "sql"
ONTOLOGY_DIR = CASE_DIR / "ontology"
GRAPH_DIR = CASE_DIR / "graph"
REFERENCE_DIR = CASE_DIR / "data" / "reference"
GENERATED_DIR = CASE_DIR / "data" / "generated"
SAMPLES_DIR = CASE_DIR / "data" / "samples"
EVALUATION_DIR = CASE_DIR / "evaluation"
EVIDENCE_DIR = CASE_DIR / "evidence"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=CASE_DIR / ".env", extra="ignore")

    # 127.0.0.1, not localhost: containers bind IPv4 only, and on Windows localhost tries ::1 first and stalls.
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 55432
    postgres_db: str = "p2p"
    postgres_user: str = "p2p_admin"
    postgres_password: str = ""

    # The login people and agents connect through. It holds no privileges until it assumes a role.
    app_db_user: str = "p2p_app"
    app_db_password: str = ""

    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""

    # offline needs no key or network; anthropic and ollama are opt-in.
    llm_provider: str = "offline"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = "medium"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.2"

    def app_conninfo(self) -> str:
        return (
            f"host={self.postgres_host} port={self.postgres_port} dbname={self.postgres_db} "
            f"user={self.app_db_user} password={self.app_db_password} connect_timeout=10"
        )

    def conninfo(self, dbname: str | None = None) -> str:
        return (
            f"host={self.postgres_host} port={self.postgres_port} dbname={dbname or self.postgres_db} "
            f"user={self.postgres_user} password={self.postgres_password} connect_timeout=10"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
