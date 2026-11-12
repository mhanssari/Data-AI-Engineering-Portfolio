# 0003 Docker is required for this case

Status: accepted

Date: 12 November 2026

## Context

The portfolio prefers a default path with no Docker (case 02 has one). This case is about services: a relational sink with transactions, a Kafka broker, and a stream processor with checkpoints. Imitating them in-process would hide exactly what the case is meant to show.

## Options

1. **No Docker:** SQLite or DuckDB instead of PostgreSQL, and a file replay instead of Kafka. Easy to run, but it shows nothing about brokers, consumer groups, checkpoints or concurrent sinks.
2. **Docker Compose for the services, the CLI in a container or with uv.** One `docker compose up`, as in case 06.

## Decision and rationale

Option 2. The case README states plainly that Docker is needed. The pure logic (generator, state machine, fee rule) is unit tested without services, so most tests and CI do not depend on Docker.

## Consequences and validation

- Reviewers need Docker Desktop or Docker Engine. The memory budget is in [architecture](../architecture.md).
- Milestone 1 needs PostgreSQL only; Kafka and Spark start only for milestone 2.
- CI runs PostgreSQL as a service container, so integration tests run on GitHub too.
