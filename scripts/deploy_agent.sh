#!/usr/bin/env bash
set -euo pipefail

kubectl apply -k k8s/
kubectl rollout status deployment/ai-kube-agent -n ai-kube-agent --timeout=180s
kubectl get pods -n ai-kube-agent

