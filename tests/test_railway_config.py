"""Contract tests for the Railway deployment manifest."""

import json
from pathlib import Path

import pytest


@pytest.mark.issue(772)
def test_railway_deploy_config_uses_schema_compatible_types() -> None:
    """Keep Railway numeric deployment settings encoded as JSON numbers."""

    config = json.loads(Path("railway.json").read_text())
    deploy = config["deploy"]

    assert deploy["drainingSeconds"] == 15
    assert isinstance(deploy["drainingSeconds"], int)
    assert isinstance(deploy["healthcheckTimeout"], int)
    assert isinstance(deploy["restartPolicyMaxRetries"], int)
