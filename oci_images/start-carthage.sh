#!/bin/sh
export PYTHONPATH=/carthage
cd /carthage
test -f /layout/config.yml &&exec ./bin/carthage-runner --tmux --generate --config /layout/config.yml
exec ./bin/carthage-console
