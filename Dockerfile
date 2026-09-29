# Containerised test suite.
#   docker-compose run --rm test         # Python (zoomctl) tests + schema drift check
#   docker-compose run --rm test-swift   # RoomAgentCore tests on Linux
#   docker-compose run --rm zoomctl ...  # run the CLI

FROM python:3.12-slim AS zoomctl
ENV PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN useradd --create-home app
WORKDIR /src
COPY ctl/pyproject.toml ctl/README.md ctl/
COPY ctl/zoomctl ctl/zoomctl
RUN python -m venv /opt/venv && /opt/venv/bin/pip install ./ctl
ENV PATH=/opt/venv/bin:$PATH
USER app
ENTRYPOINT ["zoomctl"]

FROM zoomctl AS python-test
USER root
RUN /opt/venv/bin/pip install -e './ctl[dev]'
COPY ctl/tests ctl/tests
COPY examples examples
COPY schemas schemas
COPY agent/Sources/RoomAgentCore/Report.swift agent/Sources/RoomAgentCore/Report.swift
RUN chown -R app /src
USER app
WORKDIR /src/ctl
ENTRYPOINT []
CMD ["sh", "-c", "pytest -q && zoomctl schema --check --out ../schemas && zoomctl validate -c ../examples/config"]

FROM swift:6.1-jammy AS swift-test
WORKDIR /src/agent
COPY agent/Package.swift agent/Package.resolved* ./
RUN swift package resolve
COPY agent/Sources Sources
COPY agent/Tests Tests
CMD ["sh", "-c", "swift test 2>&1 | tee /tmp/t.log; n=$(grep -Eo 'Executed [0-9]+ tests?, with 0 failures' /tmp/t.log | tail -1 | grep -Eo '[0-9]+' | head -1); [ -n \"$n\" ] && [ \"$n\" -gt 0 ] || { echo 'no Swift tests executed or failures present' >&2; exit 1; }; echo \"ok: $n Swift tests\""]
