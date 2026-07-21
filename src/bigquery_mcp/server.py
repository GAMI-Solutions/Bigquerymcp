"""bigquery-mcp server entry point.

Usage:
    bigquery-mcp --project-id my-project [--config-file config.json] [--http --port 8080]

Resolution order for settings: CLI flags > environment variables (BQMCP_*) > config file > defaults.
"""
from __future__ import annotations

import argparse
import logging
import sys

from mcp.server.fastmcp import FastMCP

from .auth import AuthError, get_bigquery_client, test_connection
from .audit import AuditLog
from .cache import QueryResultCache
from .config import load_config
from .protection import SensitiveFieldGuard
from .tools import audit_tool, jobs, query, tables

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("bigquery_mcp.server")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bigquery-mcp", description="MCP server for Google BigQuery.")
    parser.add_argument("--project-id", dest="project_id", help="Google Cloud project ID.")
    parser.add_argument("--location", dest="location", help="BigQuery location (e.g. US, europe-west1).")
    parser.add_argument("--key-file", dest="key_file", help="Path to a service account key JSON file.")
    parser.add_argument("--config-file", dest="config_file", help="Path to a config.json file.")
    parser.add_argument(
        "--maximum-bytes-billed", dest="maximum_bytes_billed", type=int,
        help="Override maximum bytes billed per query.",
    )
    parser.add_argument("--allow-ddl", action="store_true", default=None, help="Enable DDL statements.")
    parser.add_argument("--allow-dml", action="store_true", default=None, help="Enable DML statements.")
    parser.add_argument("--http", action="store_true", help="Serve over HTTP instead of stdio.")
    parser.add_argument("--port", type=int, default=8080, help="Port for --http mode.")
    return parser


def build_server(config) -> FastMCP:
    """Construct the FastMCP server with an authenticated client and all tools registered."""
    try:
        client = get_bigquery_client(
            project_id=config.project_id,
            key_file=config.key_file,
            location=config.location,
        )
        test_connection(client)
    except AuthError as exc:
        logger.error(str(exc))
        sys.exit(1)

    cache = QueryResultCache(ttl_seconds=config.cache_ttl_seconds)
    guard = SensitiveFieldGuard(
        protection_mode=config.protection_mode,
        prevented_fields=config.prevented_fields,
        sensitive_field_patterns=config.sensitive_field_patterns,
    )
    audit = AuditLog(path=config.audit_log_path)

    mcp = FastMCP("bigquery-mcp")

    query.register(
        mcp, client,
        maximum_bytes_billed=config.maximum_bytes_billed,
        allow_ddl=config.allow_ddl,
        allow_dml=config.allow_dml,
        cache=cache,
        guard=guard,
        audit=audit,
    )
    tables.register(
        mcp, client,
        project_id=config.project_id,
        allow_ddl=config.allow_ddl,
        guard=guard,
        audit=audit,
    )
    jobs.register(mcp, client, project_id=config.project_id, audit=audit)
    audit_tool.register(mcp, audit=audit)

    logger.info(
        "bigquery-mcp ready: project=%s location=%s protection_mode=%s allow_ddl=%s allow_dml=%s",
        config.project_id, config.location, config.protection_mode, config.allow_ddl, config.allow_dml,
    )
    return mcp


def main() -> None:
    args = build_arg_parser().parse_args()

    overrides = {
        "project_id": args.project_id,
        "location": args.location,
        "key_file": args.key_file,
        "maximum_bytes_billed": args.maximum_bytes_billed,
        "allow_ddl": args.allow_ddl,
        "allow_dml": args.allow_dml,
    }

    try:
        config = load_config(config_file=args.config_file, overrides=overrides)
    except (FileNotFoundError, ValueError) as exc:
        logger.error(str(exc))
        sys.exit(1)

    mcp = build_server(config)

    if args.http:
        logger.info("Starting HTTP transport on port %d", args.port)
        mcp.run(transport="streamable-http", port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
