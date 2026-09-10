# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "5"
# ///
# MAGIC %pip install databricks-sdk langchain-core -q
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import base64
import re

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.workspace import ExportFormat
from langchain_core.tools import tool

w = WorkspaceClient()

# COMMAND ----------

# MAGIC %md
# MAGIC Tool 1 : Get Run Details — given a run_id, returns the failed task(s), exact error text, notebook path, and cluster_id.

# COMMAND ----------

@tool(description="Given a Databricks run_id, fetches the run's job name, and for each FAILED task: task key, exact error text, notebook path, and cluster_id. This is the starting point for every diagnosis.")
def get_run_details_tool(run_id: int) -> str:

    try:
        run = w.jobs.get_run(run_id=run_id)
    except Exception as e:
        return f"Could not fetch run {run_id}: {str(e)}"

    job_name = run.run_name or "Unknown"
    job_id = run.job_id

    failed_tasks = []
    seen_tasks = set()
    for task in run.tasks or []:
        task_result = (
            task.state.result_state.value
            if task.state and task.state.result_state
            else "UNKNOWN"
        )
        if task_result != "FAILED":
            continue

        if task.task_key in seen_tasks:
            continue
        seen_tasks.add(task.task_key)

        error_text = "No error captured"
        try:
            output = w.jobs.get_run_output(run_id=task.run_id)
            if hasattr(output, "error") and output.error:
                error_text = output.error.strip()
            elif hasattr(output, "error_trace") and output.error_trace:
                error_text = output.error_trace.strip()
        except Exception as e:
            error_text = f"Error fetching task output: {str(e)}"

        notebook_path = (
            task.notebook_task.notebook_path
            if task.notebook_task and task.notebook_task.notebook_path
            else None
        )

        cluster_id = task.existing_cluster_id or (
            task.new_cluster.cluster_id if task.new_cluster else None
        )

        failed_tasks.append({
            "task_key": task.task_key,
            "error_text": error_text,
            "notebook_path": notebook_path,
            "cluster_id": cluster_id,
        })

    if not failed_tasks:
        return f"Run {run_id} (job: {job_name}) has no FAILED tasks — nothing to diagnose."

    report_lines = [f"=== Run {run_id} — Job: {job_name} (job_id: {job_id}) ==="]
    for t in failed_tasks:
        report_lines.append(
            f"\nTask Key      : {t['task_key']}\n"
            f"Error         : {t['error_text']}\n"
            f"Notebook Path : {t['notebook_path'] or 'N/A'}\n"
            f"Cluster ID    : {t['cluster_id'] or 'N/A'}"
        )

    return "\n".join(report_lines)

print("get_run_details_tool ready ✅")

# COMMAND ----------

# MAGIC %md
# MAGIC Tool 2 : Notebook Fetch — given a notebook path, returns its source code, following %run / dbutils.notebook.run references.

# COMMAND ----------

@tool(description="Fetches the source code of ONE Databricks notebook at the given path — does not fetch any referenced notebooks. Also returns the resolved paths of any %run / dbutils.notebook.run references found in this notebook, so you can decide whether to fetch any of them next.")
def notebook_fetch_tool(notebook_path: str) -> str:

    def fetch_notebook_code(path: str) -> str:
        try:
            export_response = w.workspace.export(path=path, format=ExportFormat.SOURCE)
            if not export_response.content:
                return f"Content is empty for path: {path}"
            return base64.b64decode(export_response.content).decode("utf-8")
        except Exception as e:
            return f"Could not fetch: {str(e)}"

    def resolve_path(base_path: str, ref_path: str) -> str:
        if ref_path.startswith("/"):
            return ref_path
        base_dir = "/".join(base_path.split("/")[:-1])
        parts = (base_dir + "/" + ref_path).split("/")
        resolved = []
        for part in parts:
            if part == "..":
                if resolved:
                    resolved.pop()
            elif part and part != ".":
                resolved.append(part)
        return "/" + "/".join(resolved)

    main_code = fetch_notebook_code(notebook_path)

    run_matches = re.findall(
        r'%run\s+["\']?([^\s"\'$\n]+)["\']?|dbutils\.notebook\.run\s*\(\s*["\']([^"\']+)["\']',
        main_code
    )
    external_paths = [resolve_path(notebook_path, p) for match in run_matches for p in match if p]

    output = f"=== Notebook Source: {notebook_path} ===\n{main_code}\n"

    if external_paths:
        output += (
            "\n=== References found in this notebook (NOT fetched) ===\n"
            + "\n".join(f"- {p}" for p in external_paths)
            + "\nCall notebook_fetch_tool again on any of these ONLY if this "
              "notebook's own code doesn't explain the error.\n"
        )
    else:
        output += "\nNo external notebooks referenced.\n"

    return output

# COMMAND ----------

# MAGIC %md
# MAGIC Tool 3 : Table Schema — given a table name, returns its current columns and schema change history.

# COMMAND ----------

@tool(description="Given a fully-qualified Delta table name (e.g. catalog.schema.table or default.table), returns its current column list and recent schema-change history. Use when a hypothesis involves a missing, renamed, or type-mismatched column.")
def get_table_schema_tool(table_name: str) -> str:

    try:
        schema_rows = spark.sql(f"DESCRIBE TABLE {table_name}").collect()
        current_columns = [
            f"{row['col_name']} ({row['data_type']})"
            for row in schema_rows
            if row['col_name'] and not row['col_name'].startswith('#')
        ]
    except Exception as e:
        return f"Could not fetch schema for table '{table_name}': {str(e)}"

    result = f"=== Current Schema: {table_name} ===\n"
    result += "\n".join(current_columns) if current_columns else "No columns found."

    # Schema history via DESCRIBE HISTORY (requires SQL execution, not SDK-native)
    try:
        history_df = spark.sql(f"DESCRIBE HISTORY {table_name} LIMIT 10")
        history_rows = history_df.select("version", "timestamp", "operation").collect()
        result += "\n\n=== Recent History (last 10 operations) ===\n"
        for row in history_rows:
            result += f"v{row['version']} | {row['timestamp']} | {row['operation']}\n"
    except Exception as e:
        result += f"\n\nCould not fetch table history: {str(e)}"

    return result

print("get_table_schema_tool ready ✅")

# COMMAND ----------

# MAGIC %md
# MAGIC Tool 4 : Cluster Events — given a cluster_id, returns why it terminated (if it did).

# COMMAND ----------

@tool(description="Given a Databricks cluster_id, fetches its recent events — including termination reason if the cluster died. Use when a hypothesis points to an infrastructure/platform fault rather than a code issue.")
def get_cluster_events_tool(cluster_id: str) -> str:

    if not cluster_id or cluster_id == "N/A":
        return "No cluster_id provided — cannot fetch cluster events."

    try:
        events = list(w.clusters.events(cluster_id=cluster_id, limit=20))
    except Exception as e:
        return f"Could not fetch events for cluster '{cluster_id}': {str(e)}"

    if not events:
        return f"No events found for cluster '{cluster_id}'."

    result = f"=== Recent Events: cluster {cluster_id} ===\n"
    for e in events:
        event_type = e.type.value if e.type else "UNKNOWN"
        details = e.details
        reason = ""
        if details and details.reason:
            reason = f" | reason: {details.reason.code} — {details.reason.parameters}"
        result += f"{e.timestamp} | {event_type}{reason}\n"

    return result

print("get_cluster_events_tool ready ✅")

# COMMAND ----------

# MAGIC %md
# MAGIC Tool 5 : Run History — given a job_id, returns the last N runs' outcomes to spot a recurring vs one-off failure.

# COMMAND ----------

@tool(description="Given a Databricks job_id, returns the outcome (SUCCESS/FAILED/etc) of its last several runs. Use to check whether a failure is a one-off or a recurring pattern.")
def get_run_history_tool(job_id: int, limit: int = 5) -> str:

    try:
        runs = list(w.jobs.list_runs(job_id=job_id, limit=limit))
    except Exception as e:
        return f"Could not fetch run history for job {job_id}: {str(e)}"

    if not runs:
        return f"No run history found for job {job_id}."

    result = f"=== Last {len(runs)} runs for job {job_id} ===\n"
    for r in runs:
        state = (
            r.state.result_state.value
            if r.state and r.state.result_state
            else "UNKNOWN"
        )
        start = (
            r.start_time
        )
        result += f"run_id {r.run_id} | {state} | start_time_ms: {start}\n"

    return result

print("get_run_history_tool ready ✅")
