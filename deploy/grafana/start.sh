#!/bin/sh
# The demo bind-mounts its provisioning directory over /etc/grafana/provisioning.
# A second mount of one file inside that directory fails on Docker Desktop:
# the mountpoint is resolved through the host path and rejected as outside
# the container rootfs. Copy the demo files to a writable directory, add our
# alert rule, and point Grafana at the copy. The copy is not the vendor mount,
# so the submodule stays clean.
set -eu
rm -rf /tmp/grafana-provisioning
mkdir -p /tmp/grafana-provisioning
cp -R /etc/grafana/provisioning/. /tmp/grafana-provisioning/
cp /oncall/oncall-copilot.yaml /tmp/grafana-provisioning/alerting/oncall-copilot.yaml
exec /run.sh
