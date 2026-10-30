# Infra

[`docker-compose.yml`](docker-compose.yml) runs two local services, bound to `127.0.0.1` only:

- PostgreSQL 18 with pgvector 0.8.7: `pgvector/pgvector:0.8.7-pg18-trixie`.
- Neo4j Community: `neo4j:2026.09.0-community`, a rebuildable projection.

Ports, volumes and credentials come from the case `.env`. No cloud resources are used. Commands: [runbook](../docs/runbook.md). Verified on Windows 11 with Docker Desktop.
