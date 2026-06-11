#!/usr/bin/env bash

# scripts/local_test.sh — one-command Kind setup, deployment, demo validation

#

# Usage:

# ./scripts/local_test.sh

# PIONEER_API_KEY=sk-... ./scripts/local_test.sh

# ./scripts/local_test.sh --reset

# ./scripts/local_test.sh --no-open

#

# Notes:

# - Creates the Kind cluster only if it does not already exist.

# - If the cluster exists but kubeconfig context is missing, it exports the context again.

# - All kubectl commands use the explicit Kind context to avoid deploying to the wrong cluster.

set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-ai-kube-agent-local}"
IMAGE="${IMAGE:-ai-kube-agent:local}"
CONTEXT="${CONTEXT:-kind-${CLUSTER_NAME}}"
DASHBOARD_PORT="${DASHBOARD_PORT:-18080}"
RESET_CLUSTER=false
OPEN_BROWSER=true
PF_PID=""

# ── Arguments ────────────────────────────────────────────────────────────────

for arg in "$@"; do
case "${arg}" in
--reset)
RESET_CLUSTER=true
;;
--no-open)
OPEN_BROWSER=false
;;
--help|-h)
cat <<USAGE
Usage: $0 [--reset] [--no-open]

Options:
--reset     Delete and recreate the Kind cluster from scratch
--no-open   Do not open the dashboard automatically

Environment variables:
CLUSTER_NAME       Default: ai-kube-agent-local
IMAGE              Default: ai-kube-agent:local
DASHBOARD_PORT     Default: 18080
PIONEER_API_KEY    Optional. Leave empty for local rule-only mode
PIONEER_ENDPOINT   Optional. Override AI endpoint
PIONEER_MODEL      Optional. Override model name
USAGE
exit 0
;;
*)
echo "Unknown argument: ${arg}"
echo "Run: $0 --help"
exit 1
;;
esac
done

# ── Colors ───────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; }

cleanup() {
if [[ -n "${PF_PID}" ]] && kill -0 "${PF_PID}" >/dev/null 2>&1; then
echo ""
info "Stopping port-forward..."
kill "${PF_PID}" >/dev/null 2>&1 || true
fi
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║   DevOps AI Kube Agent — Kind Local Test Setup  ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# ── 1. Prerequisites ─────────────────────────────────────────────────────────

info "Checking prerequisites..."

if ! docker info >/dev/null 2>&1; then
error "Docker is not running. Please start Docker Desktop and run the script again."
exit 1
fi

for cmd in kind kubectl docker curl; do
if ! command -v "${cmd}" >/dev/null 2>&1; then
error "Required command not found: ${cmd}"
case "${cmd}" in
kind)
echo "  macOS : brew install kind"
echo "  Linux : curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.23.0/kind-linux-amd64 && chmod +x ./kind && sudo mv ./kind /usr/local/bin/kind"
;;
kubectl)
echo "  macOS : brew install kubectl"
;;
curl)
echo "  Install curl with your OS package manager."
;;
esac
exit 1
fi
done

success "Docker : $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo 'running')"
success "kind   : $(kind version | head -1)"
success "kubectl: $(kubectl version --client 2>/dev/null | sed -n '1p')"

# ── 2. API key ───────────────────────────────────────────────────────────────

echo ""
if [[ -n "${PIONEER_API_KEY:-}" ]]; then
success "PIONEER_API_KEY found in environment (${#PIONEER_API_KEY} characters)."
elif [[ -t 0 ]]; then
warn "PIONEER_API_KEY environment variable not found."
echo -n "  Enter Pioneer API key (leave empty for local rule-only mode): "
read -rs KEY_INPUT
echo ""
PIONEER_API_KEY="${KEY_INPUT:-}"
if [[ -z "${PIONEER_API_KEY}" ]]; then
warn "No API key entered. The agent will run in local rule-only mode."
else
success "API key received (${#PIONEER_API_KEY} characters)."
fi
else
PIONEER_API_KEY=""
warn "No interactive terminal and no PIONEER_API_KEY. Running in local rule-only mode."
fi

if [[ -n "${PIONEER_API_KEY}" ]]; then
info "Validating Pioneer API key..."
ENDPOINT="${PIONEER_ENDPOINT:-https://api.pioneer.ai/v1/chat/completions}"
MODEL="${PIONEER_MODEL:-pioneer-fast}"

set +e
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" 
-H "Authorization: Bearer ${PIONEER_API_KEY}" 
-H "Content-Type: application/json" 
-d "{"model": "${MODEL}", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}" 
"${ENDPOINT}")
CURL_EXIT=$?
set -e

if [[ "${CURL_EXIT}" -ne 0 ]]; then
warn "API key validation request failed locally. Continuing anyway."
elif [[ "${HTTP_STATUS}" == "401" || "${HTTP_STATUS}" == "403" ]]; then
error "The PIONEER_API_KEY appears invalid (HTTP ${HTTP_STATUS})."
echo -n "  Continue anyway? AI analyses may fail. [y/N]: "
read -r CONFIRM
if [[ ! "${CONFIRM}" =~ ^[yY](es)?$ ]]; then
error "Setup cancelled."
exit 1
fi
elif [[ "${HTTP_STATUS}" == "200" || "${HTTP_STATUS}" == "201" ]]; then
success "API key validated."
else
warn "API key validation returned HTTP ${HTTP_STATUS}. Endpoint/model may differ. Continuing anyway."
fi
fi

# ── 3. Kind cluster setup and kubeconfig preparation ─────────────────────────

echo ""
info "Preparing Kind cluster..."
info "Cluster name : ${CLUSTER_NAME}"
info "Context name : ${CONTEXT}"

if [[ "${RESET_CLUSTER}" == true ]] && kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
warn "--reset requested. Deleting existing cluster '${CLUSTER_NAME}'..."
kind delete cluster --name "${CLUSTER_NAME}"
fi

if kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
success "Kind cluster '${CLUSTER_NAME}' already exists. Reusing it."
else
info "Kind cluster '${CLUSTER_NAME}' not found. Creating it now..."
kind create cluster --name "${CLUSTER_NAME}" --wait 120s
success "Kind cluster created."
fi

info "Exporting kubeconfig context for Kind cluster..."
mkdir -p "${HOME}/.kube"
kind export kubeconfig --name "${CLUSTER_NAME}" >/dev/null

if ! kubectl config get-contexts -o name | grep -qx "${CONTEXT}"; then
error "Expected Kubernetes context was not found: ${CONTEXT}"
echo ""
echo "Try manually:"
echo "  kind export kubeconfig --name ${CLUSTER_NAME}"
echo "  kubectl config get-contexts"
exit 1
fi

if ! kubectl --context "${CONTEXT}" get nodes >/dev/null 2>&1; then
error "Context '${CONTEXT}' exists but the cluster is not reachable."
echo "Run the script with --reset:"
echo "  ./scripts/local_test.sh --reset"
exit 1
fi

kubectl config use-context "${CONTEXT}" >/dev/null
success "Kubernetes context is ready: ${CONTEXT}"
kubectl --context "${CONTEXT}" get nodes

# ── 4. Build image ───────────────────────────────────────────────────────────

echo ""
info "Building Docker image: ${IMAGE}"
docker build -t "${IMAGE}" .
success "Image built: ${IMAGE}"

# ── 5. Load image into Kind ──────────────────────────────────────────────────

info "Loading Docker image into Kind cluster..."
kind load docker-image "${IMAGE}" --name "${CLUSTER_NAME}"
success "Image loaded into Kind."

# ── 6. Deploy namespace and secret ───────────────────────────────────────────

echo ""
info "Applying Kubernetes namespace..."
kubectl --context "${CONTEXT}" apply -f k8s/namespace.yaml

info "Creating/updating Pioneer AI Secret..."
kubectl --context "${CONTEXT}" create secret generic pioneer-ai-secret 
--from-literal=PIONEER_API_KEY="${PIONEER_API_KEY}" 
-n ai-kube-agent 
--dry-run=client -o yaml | kubectl --context "${CONTEXT}" apply -f -

if [[ -n "${PIONEER_API_KEY}" ]]; then
success "PIONEER_API_KEY injected into Kubernetes Secret."
else
warn "Secret created with an empty API key. Dashboard will show local rule-only mode until AI is enabled/configured."
fi

# ── 7. Deploy agent ──────────────────────────────────────────────────────────

info "Deploying agent manifests..."
kubectl --context "${CONTEXT}" apply -k k8s/

info "For safety, setting AI_ENABLED=false by default..."
kubectl --context "${CONTEXT}" patch configmap ai-kube-agent-config 
-n ai-kube-agent --type merge -p '{"data":{"AI_ENABLED":"false"}}'

if [[ -n "${PIONEER_ENDPOINT:-}" ]]; then
info "Applying PIONEER_ENDPOINT to ConfigMap..."
kubectl --context "${CONTEXT}" patch configmap ai-kube-agent-config 
-n ai-kube-agent --type merge -p "{"data":{"PIONEER_ENDPOINT":"${PIONEER_ENDPOINT}"}}"
fi

if [[ -n "${PIONEER_MODEL:-}" ]]; then
info "Applying PIONEER_MODEL to ConfigMap..."
kubectl --context "${CONTEXT}" patch configmap ai-kube-agent-config 
-n ai-kube-agent --type merge -p "{"data":{"PIONEER_MODEL":"${PIONEER_MODEL}"}}"
fi

info "Restarting deployment to pick up the latest image/config..."
kubectl --context "${CONTEXT}" rollout restart deployment/ai-kube-agent -n ai-kube-agent >/dev/null 2>&1 || true

info "Waiting for agent deployment rollout..."
if kubectl --context "${CONTEXT}" rollout status deployment/ai-kube-agent -n ai-kube-agent --timeout=180s; then
success "Agent started successfully."
else
error "Agent deployment rollout failed or timed out."
echo ""
echo "Debug commands:"
echo "  kubectl --context ${CONTEXT} -n ai-kube-agent get pods"
echo "  kubectl --context ${CONTEXT} -n ai-kube-agent describe pod -l app.kubernetes.io/name=ai-kube-agent"
echo "  kubectl --context ${CONTEXT} -n ai-kube-agent logs -l app.kubernetes.io/name=ai-kube-agent --tail=100"
exit 1
fi

# ── 8. Deploy demo workloads ─────────────────────────────────────────────────

echo ""
info "Deploying demo broken workloads..."
kubectl --context "${CONTEXT}" apply -f demo/namespace.yaml

DEMO_OK=0
DEMO_FAIL=0
for f in demo/*.yaml; do
[[ -e "${f}" ]] || continue
[[ "${f}" == "demo/namespace.yaml" ]] && continue

if kubectl --context "${CONTEXT}" apply -f "${f}" >/dev/null 2>&1; then
DEMO_OK=$((DEMO_OK + 1))
else
warn "Failed to apply demo manifest: ${f}"
DEMO_FAIL=$((DEMO_FAIL + 1))
fi
done
success "Demo manifests applied: ${DEMO_OK} succeeded, ${DEMO_FAIL} failed."

# ── 9. Port-forward ──────────────────────────────────────────────────────────

echo ""
info "Starting port-forward: [http://127.0.0.1:${DASHBOARD_PORT}](http://127.0.0.1:${DASHBOARD_PORT}) → svc/ai-kube-agent:80"

if command -v lsof >/dev/null 2>&1 && lsof -ti:"${DASHBOARD_PORT}" >/dev/null 2>&1; then
warn "Port ${DASHBOARD_PORT} is already in use. Killing existing process on that port..."
lsof -ti:"${DASHBOARD_PORT}" | xargs kill -9 >/dev/null 2>&1 || true
sleep 1
fi

(
set +e
while true; do
kubectl --context "${CONTEXT}" -n ai-kube-agent port-forward svc/ai-kube-agent "${DASHBOARD_PORT}:80" >/tmp/ai-kube-agent-port-forward.log 2>&1
sleep 1
done
) &
PF_PID=$!

for i in $(seq 1 20); do
if curl -fsS "[http://127.0.0.1:${DASHBOARD_PORT}/healthz](http://127.0.0.1:${DASHBOARD_PORT}/healthz)" >/dev/null 2>&1; then
success "Dashboard is accessible: [http://127.0.0.1:${DASHBOARD_PORT}](http://127.0.0.1:${DASHBOARD_PORT})"
break
fi

if [[ "${i}" == "20" ]]; then
error "Dashboard did not respond. Check port-forward logs:"
echo "  cat /tmp/ai-kube-agent-port-forward.log"
exit 1
fi

sleep 1
done

# ── 10. Trigger initial scan ─────────────────────────────────────────────────

echo ""
info "Waiting for demo workloads to settle..."
sleep 15

info "Triggering initial scan..."
curl -fsS -X POST "[http://127.0.0.1:${DASHBOARD_PORT}/api/scan](http://127.0.0.1:${DASHBOARD_PORT}/api/scan)" >/dev/null 2>&1 || warn "Manual scan trigger failed. The agent may still scan automatically."
sleep 5

info "Retrieving findings..."
if curl -fsS "[http://127.0.0.1:${DASHBOARD_PORT}/api/findings](http://127.0.0.1:${DASHBOARD_PORT}/api/findings)" >/tmp/ai-kube-agent-findings.json 2>/dev/null; then
if command -v python3 >/dev/null 2>&1; then
python3 - <<'PY'
import json
from pathlib import Path

path = Path('/tmp/ai-kube-agent-findings.json')
try:
data = json.loads(path.read_text())
severity = {}
problem_types = set()
for item in data:
severity[item.get('severity', 'Unknown')] = severity.get(item.get('severity', 'Unknown'), 0) + 1
if item.get('problem_type'):
problem_types.add(item.get('problem_type'))

```
print("\n══════════════════════════════════════════")
print("         VALIDATION RESULTS")
print("══════════════════════════════════════════")
print(f"  Total findings : {len(data)}")
for sev, count in sorted(severity.items()):
    print(f"  {sev:10}: {count}")
print(f"\n  Problem types  : {', '.join(sorted(problem_types)) or 'none yet'}")
if len(problem_types) >= 3:
    print("\n  ✅ SUCCESS — Multiple demo error types detected.")
else:
    print("\n  ⚠️  Fewer than 3 error types detected yet.")
    print("     Wait a little and click 'Run Scan' on the dashboard.")
print("══════════════════════════════════════════\n")
```

except Exception as exc:
print(f"Validation parse error: {exc}")
PY
else
echo "Findings JSON saved to: /tmp/ai-kube-agent-findings.json"
fi
else
warn "Could not retrieve findings yet. Open dashboard and click Run Scan."
fi

# ── 11. Open browser and print commands ──────────────────────────────────────

if [[ "${OPEN_BROWSER}" == true ]]; then
if command -v open >/dev/null 2>&1; then
info "Opening dashboard in browser..."
open "[http://127.0.0.1:${DASHBOARD_PORT}](http://127.0.0.1:${DASHBOARD_PORT})" >/dev/null 2>&1 || true
elif command -v xdg-open >/dev/null 2>&1; then
info "Opening dashboard in browser..."
xdg-open "[http://127.0.0.1:${DASHBOARD_PORT}](http://127.0.0.1:${DASHBOARD_PORT})" >/dev/null 2>&1 || true
fi
fi

echo ""
echo "══════════════════════════════════════════════════════"
echo " Dashboard   : [http://127.0.0.1:${DASHBOARD_PORT}](http://127.0.0.1:${DASHBOARD_PORT})"
echo " Context     : ${CONTEXT}"
echo " Pods        : kubectl --context ${CONTEXT} get pods -A"
echo " Agent logs  : kubectl --context ${CONTEXT} -n ai-kube-agent logs -l app.kubernetes.io/name=ai-kube-agent -f"
echo " PF logs     : cat /tmp/ai-kube-agent-port-forward.log"
echo " Delete lab  : kind delete cluster --name ${CLUSTER_NAME}"
echo "══════════════════════════════════════════════════════"
echo ""
echo "Stop port-forward with Ctrl+C."

wait "${PF_PID}"
