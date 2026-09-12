#!/usr/bin/env bash
# First boot of the pilot host. Architecture v0.4 section 6, Containers.
#
# This script does four things and nothing else: the service account, the
# directories, Docker, and the group membership that lets the account use Docker.
# It deliberately does NOT clone the repository, write an environment file, pull an
# image or start anything.
#
# Why so little:
#
#   - Rule 20. No credential may appear in code or config, and user_data is neither
#     encrypted nor access-controlled beyond the instance profile: anything written
#     here is readable from the instance metadata service and is stored in
#     Terraform state in clear text. The environment file this host needs
#     (/etc/monitor/monitor.env, three database URLs and the model credential) is
#     rendered from Secrets Manager ON the host, after boot - secrets.tf says by
#     what.
#   - Deployment is SSM Run Command from GitHub Actions (identity.tf), so a host
#     that boots ready to receive a deployment is the whole job. A user_data script
#     that also deployed would be a second deployment path (rule 1).
#
# The layout below is deploy/README.md's, not a new convention: checkout at
# /opt/monitor, service account `monitor`, environment file at
# /etc/monitor/monitor.env, logs in /var/log/monitor/.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

apt-get update
# docker.io and docker-compose-v2 are Ubuntu 24.04's own packages, which is one
# apt source instead of two: adding Docker's repository would mean a second key and
# a second origin to keep working for the sake of a newer point release than a
# four-container compose file can tell apart. docker-compose-v2 provides the
# `docker compose` subcommand the Makefile and docker-compose.yml expect.
apt-get install -y --no-install-recommends docker.io docker-compose-v2 git

systemctl enable --now docker

# A system account with no login shell. It owns the checkout because `uv run` syncs
# the environment inside it and the fetch stage writes storage/.
useradd --system --create-home --shell /usr/sbin/nologin monitor

install -d -o monitor -g monitor /opt/monitor
install -d -o monitor -g monitor /var/log/monitor
# 0750 root:monitor - the account reads the environment file, nobody else on the
# host can list the directory.
install -d -m 0750 -o root -g monitor /etc/monitor

# Compose runs as the service account, and talking to the Docker socket needs this
# group. It is the one privilege escalation on the host and it is worth naming:
# membership of `docker` is effectively root, which is why nothing else joins it.
usermod -aG docker monitor
