"""Job management MCP tools — monitor and cancel BigQuery query jobs.

Lets Claude keep tabs on (or stop) long-running / expensive queries it kicked off,
which read-only BigQuery MCP servers typically have no way to do.
"""
from __future__ import annotations

from typing import Any

from google.api_core.exceptions import NotFound
from google.cloud import bigquery

from ..audit import AuditLog


def register(
    mcp,
    client: bigquery.Client,
    *,
    project_id: str,
    audit: AuditLog,
) -> None:
    @mcp.tool()
    def list_jobs(max_results: int = 20, all_users: bool = False) -> dict[str, Any]:
        """List recent BigQuery jobs (queries, loads, etc.) with status and cost info.

        By default only shows jobs run by the authenticated identity; set all_users=true
        to see every job in the project (requires appropriate IAM permissions).
        """
        jobs = client.list_jobs(project=project_id, max_results=max_results, all_users=all_users)
        results = []
        for job in jobs:
            entry = {
                "job_id": job.job_id,
                "job_type": job.job_type,
                "state": job.state,
                "created": job.created.isoformat() if job.created else None,
                "user_email": getattr(job, "user_email", None),
            }
            if isinstance(job, bigquery.QueryJob):
                entry["total_bytes_processed"] = job.total_bytes_processed
                entry["query"] = (job.query or "")[:200]
            if job.error_result:
                entry["error"] = job.error_result.get("message")
            results.append(entry)

        audit.record(tool="list_jobs", success=True, extra={"count": len(results)})
        return {"jobs": results}

    @mcp.tool()
    def get_job_status(job_id: str) -> dict[str, Any]:
        """Get detailed status for a specific BigQuery job by ID."""
        try:
            job = client.get_job(job_id, project=project_id)
        except NotFound as exc:
            return {"error": f"Job '{job_id}' not found: {exc}"}

        result = {
            "job_id": job.job_id,
            "job_type": job.job_type,
            "state": job.state,
            "created": job.created.isoformat() if job.created else None,
            "started": job.started.isoformat() if job.started else None,
            "ended": job.ended.isoformat() if job.ended else None,
        }
        if isinstance(job, bigquery.QueryJob):
            result["total_bytes_processed"] = job.total_bytes_processed
            result["total_bytes_billed"] = job.total_bytes_billed
            result["cache_hit"] = job.cache_hit
        if job.error_result:
            result["error"] = job.error_result.get("message")

        audit.record(tool="get_job_status", success=True, extra={"job_id": job_id})
        return result

    @mcp.tool()
    def cancel_job(job_id: str, confirm: bool = False) -> dict[str, Any]:
        """Cancel a running BigQuery job. Requires confirm=true."""
        if not confirm:
            return {"error": f"This will cancel job '{job_id}'. Re-run with confirm=true to proceed."}

        try:
            client.cancel_job(job_id, project=project_id)
        except NotFound as exc:
            audit.record(tool="cancel_job", success=False, error=str(exc), extra={"job_id": job_id})
            return {"error": f"Job '{job_id}' not found: {exc}"}

        audit.record(tool="cancel_job", success=True, extra={"job_id": job_id})
        return {"cancelled": job_id}
