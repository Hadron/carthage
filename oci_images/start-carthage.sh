#!/bin/sh
export PYTHONPATH=/carthage
cd /carthage
runner_config=
test -f /layout/carthage_plugin.yml && runner_config="--plugin /layout"
# The next line overrides: we prefer config.yml to plugin
test -f /layout/config.yml && runner_config="--config /layout/config.yml"
if [ "$runner_configx" != "x" ]; then
    apt update
    /carthage/bin/carthage $runner_config install_dependencies
    exec /carthage/bin/carthage-runner $runner_config --generate --keep --tmux
   fi
exec ./bin/carthage-console
