#!/usr/bin/env bash
set -euo pipefail

LOCAL_PORT="${LOCAL_PORT:-8080}"
kubectl port-forward svc/ai-kube-agent "${LOCAL_PORT}:80" -n ai-kube-agent

