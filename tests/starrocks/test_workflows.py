"""The starrocks workflows: shape, entry points, schedule and names."""

import json
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPOSITORY_ROOT / "workflows" / "starrocks"


def load(name):
    return json.loads((WORKFLOWS / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["starrocks_audit", "starrocks_deploy"])
def test_workflow_shape_and_entry_points(name):
    workflow = load(name)
    assert workflow["apiVersion"] == "lakehouse.local/workflow/v1" and workflow["name"] == name
    assert len(workflow["description"]) <= 500 and "spark" not in workflow["tags"]
    for task in workflow["tasks"]:
        assert (REPOSITORY_ROOT / "notebooks" / "workload" / "starrocks" / task["entrypoint"]).is_file()


def test_the_hourly_load_pulls_bronze_then_merges_silver_before_the_views():
    workflow = load("starrocks_audit")
    assert workflow["schedule"] == "20 * * * *"
    bronze_task, silver_task = workflow["tasks"]
    assert bronze_task == {"name": "starrocks_audit_event_fe_brz_load", "entrypoint": "bronze/bronze_ingestion.py", "retries": 1, "retry_delay_seconds": 300, "execution_timeout_seconds": 1800}
    assert silver_task == {
        "name": "starrocks_audit_event_load",
        "entrypoint": "silver/silver_processing.py",
        "runs_after": ["starrocks_audit_event_fe_brz_load"],
        "retries": 1,
        "retry_delay_seconds": 300,
        "execution_timeout_seconds": 2400,
    }


def test_the_deploy_is_manual():
    workflow = load("starrocks_deploy")
    assert workflow["schedule"] is None
    assert workflow["tasks"] == [{"name": "starrocks_audit_event_fe_brz_create", "entrypoint": "deploy/deploy_bronze_table.py"}]


def test_workflow_names_are_unique_across_the_repository():
    names = [json.loads(path.read_text(encoding="utf-8"))["name"] for path in (REPOSITORY_ROOT / "workflows").glob("*/*.json")]
    assert len(names) == len(set(names))
