from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from textwrap import dedent

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE_ENV = "COURTVISION_LIFECYCLE_SHADOW"
OBSERVATIONS_ENV = "COURTVISION_LIFECYCLE_OBSERVATIONS"

BLOCK_LIFECYCLE_IMPORT = """
import importlib.abc
import sys

class BlockLifecycleImport(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if (
            fullname == "courtvision.lifecycle"
            or fullname.startswith("courtvision.lifecycle.")
        ):
            raise ModuleNotFoundError(
                "courtvision.lifecycle blocked by isolation test",
                name=fullname,
            )
        return None

sys.meta_path.insert(0, BlockLifecycleImport())
"""


def _run_isolated(
    tmp_path: Path,
    script: str,
    *,
    flag: str | None,
    observation_flag: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if flag is None:
        env.pop(LIFECYCLE_ENV, None)
    else:
        env[LIFECYCLE_ENV] = flag
    if observation_flag is None:
        env.pop(OBSERVATIONS_ENV, None)
    else:
        env[OBSERVATIONS_ENV] = observation_flag
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join(
        item
        for item in (str(REPOSITORY_ROOT), existing_pythonpath)
        if item
    )
    return subprocess.run(
        [sys.executable, "-c", dedent(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.mark.parametrize("flag", [None, "0", "false"])
def test_flag_off_import_succeeds_without_lifecycle_package_or_side_effects(
    tmp_path: Path,
    flag: str | None,
) -> None:
    result = _run_isolated(
        tmp_path,
        BLOCK_LIFECYCLE_IMPORT
        + """
import pathlib
import sys

import courtvision_ai

assert not any(
    name == "courtvision.lifecycle"
    or name.startswith("courtvision.lifecycle.")
    for name in sys.modules
)
assert not (pathlib.Path.cwd() / "data" / "lifecycle").exists()
print("IMPORT_OK")
""",
        flag=flag,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "IMPORT_OK"


def test_flag_on_import_failure_is_classified_and_canonical_cli_continues(
    tmp_path: Path,
) -> None:
    result = _run_isolated(
        tmp_path,
        BLOCK_LIFECYCLE_IMPORT
        + """
from pathlib import Path

import pandas as pd
import courtvision_ai

class Logger:
    def error(self, *args, **kwargs):
        pass

    def exception(self, *args, **kwargs):
        pass

class Runtime:
    def __init__(self, out_dir="outputs"):
        self.out_dir = Path(out_dir)
        self.logger = Logger()

    def predict(self, prediction_date):
        print("CANONICAL_PREDICT")
        return {"summary": {}, "elite_props": pd.DataFrame()}

courtvision_ai._load_env_file = lambda: None
courtvision_ai.resolve_api_key = lambda **kwargs: (
    "test-key",
    {
        "env_var_name": "BALLDONTLIE_API_KEY",
        "source": "test",
        "masked_preview": "tes***",
    },
)
courtvision_ai.smoke_test_games_api = lambda *args, **kwargs: {
    "status_code": 200,
    "resolved_url": "fixture://games",
    "has_auth": True,
    "masked_key_preview": "tes***",
    "body_snippet": "fixture",
}
courtvision_ai.CourtVisionAI = Runtime

def write_outputs(**kwargs):
    board = (
        Path(kwargs["out_dir"])
        / "runtime"
        / "operator"
        / "elite_board_2026-07-25.csv"
    )
    board.parent.mkdir(parents=True, exist_ok=True)
    board.write_text("player_name\\n", encoding="utf-8")
    return {"elite_board": board}

courtvision_ai._write_cli_outputs = write_outputs
rc = courtvision_ai.main(
    [
        "--prediction-date",
        "2026-07-25",
        "--predict-only",
        "--out-dir",
        str(Path.cwd() / "outputs"),
    ]
)
assert rc == 0
assert not (Path.cwd() / "data" / "lifecycle").exists()
print("CANONICAL_RC=0")
""",
        flag="1",
    )
    assert result.returncode == 0, result.stderr
    assert "CANONICAL_PREDICT" in result.stdout
    assert "CANONICAL_RC=0" in result.stdout
    assert "status=DEGRADED" in result.stderr
    assert "stage=INITIALIZATION" in result.stderr
    assert "classification=LIFECYCLE_IMPORT_FAILURE" in result.stderr
    assert "error_type=ModuleNotFoundError" in result.stderr


def test_flag_on_loads_available_lifecycle_publication_in_fresh_process(
    tmp_path: Path,
) -> None:
    result = _run_isolated(
        tmp_path,
        """
from courtvision.shadow_lifecycle import load_shadow_lifecycle_hooks

hooks = load_shadow_lifecycle_hooks()
assert hooks is not None
assert callable(hooks.begin_shadow_run)
assert callable(hooks.publish_shadow_after_board)
assert callable(hooks.record_failed_shadow_run)
print("LIFECYCLE_AVAILABLE")
""",
        flag="1",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "LIFECYCLE_AVAILABLE"


def test_observation_flag_off_does_not_import_phase3_module_in_fresh_process(
    tmp_path: Path,
) -> None:
    result = _run_isolated(
        tmp_path,
        """
import importlib.abc
import sys

class BlockObservationImport(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "courtvision.lifecycle.observations":
            raise ModuleNotFoundError(
                "observations blocked by isolation test",
                name=fullname,
            )
        return None

sys.meta_path.insert(0, BlockObservationImport())

from courtvision.shadow_lifecycle import load_shadow_lifecycle_hooks

hooks = load_shadow_lifecycle_hooks()
assert hooks is not None
assert not hooks.observations_enabled
assert hooks.prepare_observation_batch is None
assert "courtvision.lifecycle.observations" not in sys.modules
print("PHASE2_ONLY")
""",
        flag="1",
        observation_flag="0",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "PHASE2_ONLY"
