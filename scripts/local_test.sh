#!/usr/bin/env bash
# scripts/local_test.sh — Kind installation, deployment, and testing in a single command
# Usage:
#   ./scripts/local_test.sh                        # interactive API key prompt
#   PIONEER_API_KEY=sk-... ./scripts/local_test.sh # with API key
#   ./scripts/local_test.sh --reset                # delete and reinstall the existing cluster

set -uo pipefail  # NOTE: -e removed so port-forward errors do not stop the script

CLUSTER_NAME="${CLUSTER_NAME:-ai-kube-agent-local}"
IMAGE="${IMAGE:-ai-kube-agent:local}"
CONTEXT="kind-${CLUSTER_NAME}"
RESET_CLUSTER=false

# Parse arguments
for arg in "$@"; do
  case $arg in
    --reset) RESET_CLUSTER=true ;;
    --help|-h)
      echo "Usage: $0 [--reset]"
      echo "  --reset   Delete the existing Kind cluster and install from scratch"
      exit 0
      ;;
  esac
done

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; }

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║   DevOps AI Kube Agent — Kind Local Test Setup  ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# ── 1. Prerequisite check ───────────────────────────────────────────────────────
info "Checking prerequisites..."

if ! docker info > /dev/null 2>&1; then
  error "Docker is not running. Please start Docker Desktop."
  exit 1
fi

if ! command -v kind > /dev/null 2>&1; then
  error "'kind' not found."
  echo "  macOS : brew install kind"
  echo "  Linux : curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.23.0/kind-linux-amd64 && chmod +x ./kind && mv ./kind /usr/local/bin/kind"
  exit 1
fi

if ! command -v kubectl > /dev/null 2>&1; then
  error "'kubectl' not found."
  echo "  macOS : brew install kubectl"
  exit 1
fi

success "Docker: $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo 'running')"
success "kind  : $(kind version | head -1)"
success "kubectl: $(kubectl version --client --short 2>/dev/null | head -1)"

# ── 2. Securely retrieve API Key ─────────────────────────────────────────────────────
echo ""
if [ -n "${PIONEER_API_KEY:-}" ]; then
  KEY_LEN="${#PIONEER_API_KEY}"
  success "PIONEER_API_KEY retrieved from environment variable (${KEY_LEN} characters)."
else
  warn "PIONEER_API_KEY environment variable not found."
  echo -n "  Enter Pioneer API key (leave empty for local-only rule-based mode): "
  read -rs KEY_INPUT
  echo ""
  PIONEER_API_KEY="${KEY_INPUT:-}"
  if [ -z "${PIONEER_API_KEY}" ]; then
    warn "API key not entered → agent will run in local rule-only mode. AI analysis will not be performed."
  else
    success "API key received (${#PIONEER_API_KEY} characters)."
  fi
fi

if [ -n "${PIONEER_API_KEY}" ]; then
  info "Validating Pioneer API key..."
  ENDPOINT="${PIONEER_ENDPOINT:-https://api.pioneer.ai/v1/chat/completions}"
  MODEL="${PIONEER_MODEL:-pioneer-fast}"
  
  # Minimal ping request
  HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer ${PIONEER_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"model\": \"${MODEL}\", \"messages\": [{\"role\": \"user\", \"content\": \"ping\"}], \"max_tokens\": 1}" \
    "${ENDPOINT}")
    
  if [ "${HTTP_STATUS}" = "401" ] || [ "${HTTP_STATUS}" = "403" ]; then
    error "The PIONEER_API_KEY you entered is invalid (HTTP ${HTTP_STATUS} Unauthorized)."
    echo -n "  Do you still want to continue? (AI analyses will fail) [y/N]: "
    read CONFIRM
    if [[ ! "${CONFIRM}" =~ ^[yY](es)?$ ]]; then
      error "Setup cancelled."
      exit 1
    fi
  elif [ "${HTTP_STATUS}" = "200" ] || [ "${HTTP_STATUS}" = "201" ]; then
    success "API key successfully validated."
  else
    warn "API key validation failed (HTTP ${HTTP_STATUS}). Model name or endpoint might be incompatible. Continuing anyway..."
  fi
fi

# ── 3. Cluster management ───────────────────────────────────────────────────────
echo ""
info "Checking Kind cluster..."

if [ "${RESET_CLUSTER}" = true ] && kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
  warn "--reset: Deleting existing cluster '${CLUSTER_NAME}'..."
  kind delete cluster --name "${CLUSTER_NAME}"
fi

if ! kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
  info "Creating cluster '${CLUSTER_NAME}' (may take 1-2 minutes)..."
  kind create cluster --name "${CLUSTER_NAME}"
  success "Cluster created."
else
  success "Cluster '${CLUSTER_NAME}' already exists, using it."
fi

# ── 4. Build Docker image ─────────────────────────────────────────────────────
echo ""
info "Building Docker image: ${IMAGE}"
if ! docker build -t "${IMAGE}" . ; then
  error "Docker build failed!"
  exit 1
fi
success "Image built: ${IMAGE}"

# ── 5. Load image into Kind ──────────────────────────────────────────────────
info "Loading image into Kind cluster..."
kind load docker-image "${IMAGE}" --name "${CLUSTER_NAME}"
success "Image loaded."

# ── 6. Namespace ─────────────────────────────────────────────────────────────
echo ""
info "Applying Kubernetes namespace..."
kubectl --context "${CONTEXT}" apply -f k8s/namespace.yaml

# ── 7. Pioneer AI Secret ─────────────────────────────────────────────────────
info "Injecting AI API key as a Secret..."
kubectl --context "${CONTEXT}" create secret generic pioneer-ai-secret \
  --from-literal=PIONEER_API_KEY="${PIONEER_API_KEY}" \
  -n ai-kube-agent \
  --dry-run=client -o yaml | kubectl --context "${CONTEXT}" apply -f -

if [ -n "${PIONEER_API_KEY}" ]; then
  success "PIONEER_API_KEY injected into Secret."
else
  warn "Secret created with empty API key. You will see a warning on the dashboard."
fi

# ── 8. Deploy agent manifests ─────────────────────────────────────────────────
info "Deploying agent components..."
kubectl --context "${CONTEXT}" apply -k k8s/

# AI disabled by default (can be toggled in frontend)
info "Setting AI_ENABLED=false in ConfigMap..."
kubectl --context "${CONTEXT}" patch configmap ai-kube-agent-config \
  -n ai-kube-agent --type merge -p '{"data":{"AI_ENABLED":"false"}}'

if [ -n "${PIONEER_ENDPOINT:-}" ]; then
  info "Applying PIONEER_ENDPOINT to ConfigMap..."
  kubectl --context "${CONTEXT}" patch configmap ai-kube-agent-config \
    -n ai-kube-agent --type merge -p "{\"data\":{\"PIONEER_ENDPOINT\":\"${PIONEER_ENDPOINT}\"}}"
fi

if [ -n "${PIONEER_MODEL:-}" ]; then
  info "Applying PIONEER_MODEL to ConfigMap..."
  kubectl --context "${CONTEXT}" patch configmap ai-kube-agent-config \
    -n ai-kube-agent --type merge -p "{\"data\":{\"PIONEER_MODEL\":\"${PIONEER_MODEL}\"}}"
fi

# ── 9. Wait for rollout ──
info "Updating deployment (rollout restart if new image is present)..."
kubectl --context "${CONTEXT}" rollout restart deployment/ai-kube-agent -n ai-kube-agent > /dev/null 2>&1 || true

info "Waiting for agent deployment (max 3 minutes)..."
if kubectl --context "${CONTEXT}" rollout status deployment/ai-kube-agent \
    -n ai-kube-agent --timeout=180s; then
  success "Agent started successfully."
else
  error "Agent deployment timeout. Check the logs:"
  echo "  kubectl --context ${CONTEXT} logs -l app.kubernetes.io/name=ai-kube-agent -n ai-kube-agent"
  exit 1
fi

# ── 10. Deploy demo workloads ──────────────────────────────────────────────
echo ""
info "Deploying demo 'broken' workloads..."
kubectl --context "${CONTEXT}" apply -f demo/namespace.yaml

# Apply individually so one failing manifest doesn't block the rest
DEMO_OK=0; DEMO_FAIL=0
for f in demo/*.yaml; do
  [ "${f}" = "demo/namespace.yaml" ] && continue
  if kubectl --context "${CONTEXT}" apply -f "${f}" > /dev/null 2>&1; then
    DEMO_OK=$((DEMO_OK + 1))
  else
    warn "Failed to apply: ${f}"
    DEMO_FAIL=$((DEMO_FAIL + 1))
  fi
done
success "Demo manifests: ${DEMO_OK} succeeded, ${DEMO_FAIL} failed."

# ── 11. Start Port-forward ───────────────────────────────────────────────────
echo ""
info "Starting port-forward (18080 → service)..."

# If 18080 is in use, kill the process
if lsof -ti:18080 > /dev/null 2>&1; then
  warn "Port 18080 is in use. Terminating the previous process..."
  lsof -ti:18080 | xargs kill -9 > /dev/null 2>&1 || true
  sleep 1
fi

# Resilient background port-forwarding loop
(
  while true; do
    kubectl --context "${CONTEXT}" -n ai-kube-agent port-forward svc/ai-kube-agent 18080:80 > /tmp/kind-pf.log 2>&1
    sleep 1
  done
) &
PF_PID=$!

trap 'echo ""; info "Stopping port-forward..."; kill -9 ${PF_PID} > /dev/null 2>&1 || true' EXIT INT TERM

# Wait for port-forward connection
for i in $(seq 1 15); do
  if curl -fsS http://127.0.0.1:18080/healthz > /dev/null 2>&1; then
    success "Dashboard is accessible: http://127.0.0.1:18080"
    break
  fi
  [ "$i" = "15" ] && { error "Dashboard did not respond in 15 seconds. Check /tmp/kind-pf.log"; exit 1; }
  sleep 1
done

# ── 12. Trigger scan ──────────────────────────────────────────────────────────
echo ""
info "Preparing demo workloads (waiting 15 seconds)..."
sleep 15

info "Triggering scan..."
curl -fsS -X POST http://127.0.0.1:18080/api/scan > /dev/null 2>&1 || warn "Scan trigger failed, agent will still scan automatically."
sleep 5

info "Retrieving findings..."
curl -fsS http://127.0.0.1:18080/api/findings > /tmp/kind-findings.json 2>&1

# Validate findings
if command -v python3 > /dev/null 2>&1; then
python3 - << 'PY'
import json
try:
    with open('/tmp/kind-findings.json') as f:
        data = json.load(f)
    sev   = {}
    for item in data:
        s = item.get('severity', 'Unknown')
        sev[s] = sev.get(s, 0) + 1
    problems = sorted({item.get('problem_type') for item in data})
    print("\n══════════════════════════════════════════")
    print("         VALIDATION RESULTS")
    print("══════════════════════════════════════════")
    print(f"  Total findings  : {len(data)}")
    for s, c in sorted(sev.items()):
        print(f"  {s:10}: {c}")
    print(f"\n  Problem types   : {', '.join(problems)}")
    if len(problems) >= 3:
        print("\n  ✅ SUCCESS — At least 3 different error types detected!")
    else:
        print("\n  ⚠️  WARNING — Less than 3 different error types yet.")
        print("     Wait for demo pods to crash and click Run Scan on the dashboard.")
    print("══════════════════════════════════════════\n")
except Exception as e:
    print("Validation error:", e)
PY
elif command -v jq > /dev/null 2>&1; then
  echo ""
  echo "Findings:"
  jq -r '.[] | "  [\(.severity)] \(.problem_type) — \(.namespace)/\(.resource_name)"' /tmp/kind-findings.json
  echo "Total: $(jq length /tmp/kind-findings.json)"
else
  echo "Findings JSON: /tmp/kind-findings.json"
  cat /tmp/kind-findings.json
fi

# ── 13. Open browser (macOS) ────────────────────────────────────────────────
if command -v open > /dev/null 2>&1; then
  info "Opening dashboard in browser..."
  open "http://127.0.0.1:18080" 2>/dev/null || true
fi

# ── 14. Useful commands ───────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════"
echo " Dashboard  : http://127.0.0.1:18080"
echo " Pods       : kubectl --context ${CONTEXT} get pods -A"
echo " Agent logs : kubectl --context ${CONTEXT} logs -l app.kubernetes.io/name=ai-kube-agent -n ai-kube-agent -f"
echo " Delete Clus: kind delete cluster --name ${CLUSTER_NAME}"
echo "══════════════════════════════════════════════════════"
echo ""
echo "Stop port-forward with Ctrl+C."
wait ${PF_PID}
