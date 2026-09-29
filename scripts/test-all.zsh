#!/usr/bin/env zsh
# Run every test suite: containerised (Python + Linux Swift) and native macOS Swift.
set -euo pipefail
cd "${0:A:h}/.."
docker-compose build test test-swift
docker-compose run --rm test
docker-compose run --rm test-swift
agent/scripts/test.zsh
print "✓ all suites passed"
