import re
from pathlib import Path

import zoomctl

ROOT = Path(__file__).resolve().parents[2]


def test_python_and_swift_versions_match():
    pyproject = re.search(r'^version = "([^"]+)"', (ROOT / "ctl/pyproject.toml").read_text(), re.M)[1]
    swift = re.search(r'roomAgentVersion = "([^"]+)"',
                      (ROOT / "agent/Sources/RoomAgentCore/Report.swift").read_text())[1]
    assert pyproject == zoomctl.__version__ == swift, "run scripts/release.zsh to bump versions together"
