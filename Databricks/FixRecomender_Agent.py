# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "5"
# ///
# MAGIC %pip install -U databricks-langchain -q
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %run "./Common_Tools"

# COMMAND ----------

import yaml
from databricks_langchain import ChatDatabricks
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage

llm = ChatDatabricks(
    endpoint="databricks-qwen3-next-80b-a3b-instruct",
    temperature=0.0,
)

tools = [
    get_run_details_tool,
    notebook_fetch_tool,
    get_table_schema_tool,
    get_cluster_events_tool,
    get_run_history_tool,
]
llm_with_tools = llm.bind_tools(tools)
tools_by_name = {t.name: t for t in tools}

# COMMAND ----------

SKILL_MD_PATH = "/Workspace/Users/devaratharaiser@gmail.com/SKILL_1.md"

with open(SKILL_MD_PATH) as f:
    SYSTEM_PROMPT = f.read()

# COMMAND ----------

def clean_yaml(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    return text.strip()


def resolve_run(run_id: int, max_turns: int = 12) -> str:
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"Resolve run_id: {run_id}"),
    ]

    for _ in range(max_turns):
        response = llm_with_tools.invoke(messages)
        messages.append(response)

        if not response.tool_calls:
            cleaned = clean_yaml(response.content)
            try:
                yaml.safe_load(cleaned)
                return f"```yaml\n{cleaned}\n```"
            except yaml.YAMLError:
                messages.append(HumanMessage(
                    content="Your last response was not valid YAML. Reply with ONLY the corrected YAML block, nothing else."
                ))
                response = llm_with_tools.invoke(messages)
                cleaned = clean_yaml(response.content)
                return f"```yaml\n{cleaned}\n```"

        for call in response.tool_calls:
            tool_fn = tools_by_name[call["name"]]
            try:
                result = tool_fn.invoke(call["args"])
            except Exception as e:
                result = f"Tool error: {e}"
            messages.append(ToolMessage(content=str(result), tool_call_id=call["id"]))

    return "Stopped: exceeded max_turns without a final answer — check messages for a loop."

# COMMAND ----------

dbutils.widgets.text("run_id", "")
run_id_str = dbutils.widgets.get("run_id").strip()

if not run_id_str:
    result = "Please enter a run_id in the widget above and re-run."
else:
    try:
        result = resolve_run(run_id=int(run_id_str))
    except ValueError:
        result = f"run_id must be an integer, got: '{run_id_str}'"
    except Exception as e:
        result = f"Diagnosis failed unexpectedly: {e}"

print(result)

# COMMAND ----------

# Makes the output retrievable by whoever called this notebook
# (Genie via dbutils.notebook.run, or another notebook).
dbutils.notebook.exit(result)
