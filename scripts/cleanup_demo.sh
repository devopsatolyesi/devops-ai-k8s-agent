#!/usr/bin/env bash
set -euo pipefail

kubectl delete -f demo/ --ignore-not-found=true

