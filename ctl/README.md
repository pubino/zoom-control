# zoomctl

Python controller for [zoom-control](../README.md): validates room/event specs,
reconciles them against the Zoom Webinar API, dispatches room jobs, and runs the
on-node event lifecycle by driving the Swift `roomagent` binary.

```zsh
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest
zoomctl --help
```
