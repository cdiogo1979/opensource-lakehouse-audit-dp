"""Import paths and the Hypothesis seed for the starrocks subproject's tests."""

import os
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SUBPROJECT = REPOSITORY_ROOT / "notebooks" / "workload" / "starrocks"
# Notebooks are imported by file name (silver_processing, deploy_bronze_table, ...) from their layer folders.
for _folder in sorted(path for path in SUBPROJECT.glob("**/") if "__pycache__" not in path.parts):
    sys.path.insert(0, str(_folder))
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
HYPOTHESIS_SEED = int(os.environ.get("HYPOTHESIS_SEED", "0"))


@pytest.fixture(scope="session", autouse=True)
def report_hypothesis_seed():
    """Print the fixed or configured Hypothesis seed in pytest's captured output (PBT-08)."""
    print(f"Hypothesis reproducibility seed: {HYPOTHESIS_SEED}")
