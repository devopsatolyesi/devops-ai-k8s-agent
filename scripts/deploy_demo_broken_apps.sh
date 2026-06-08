#!/usr/bin/env bash
set -euo pipefail

kubectl apply -f demo/namespace.yaml
kubectl delete deployment,service,ingress,networkpolicy --all -n demo-broken-apps || true
sleep 2
kubectl apply -f demo/
kubectl get pods -n demo-broken-apps || true
kubectl get events -n demo-broken-apps --sort-by=.lastTimestamp || true
