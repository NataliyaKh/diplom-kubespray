#!/usr/bin/env bash
set -euo pipefail

# ===============================
#  Full reset and redeploy script
# ===============================

INVENTORY="inventory/mycluster/hosts.yaml"
LOG_FILE="cluster_redeploy_$(date +%Y%m%d_%H%M%S).log"

echo "Log will be written to: ${LOG_FILE}"
echo "Starting full reset & redeploy..." | tee "$LOG_FILE"

# --- Helper: extract a top-level var under "vars:" ---
get_var_from_inventory() {
  local key="$1"
  awk -v k="$key" '
    /^[[:space:]]*vars:[[:space:]]*$/ { found_vars=1; next }
    found_vars && match($0, "^[[:space:]]*"k"[[:space:]]*:") {
      sub("^[^:]*:[[:space:]]*", "", $0)
      gsub(/^[ \t]+|[ \t]+$/, "", $0)
      print $0
      exit
    }
  ' "$INVENTORY"
}

# --- Helper: get ansible_host for a given hostname ---
get_ansible_host_for() {
  local hostname="$1"
  awk -v h="$hostname" '
    $0 ~ ("^[[:space:]]*"h":[[:space:]]*$") { found=1; next }
    found && match($0, "^[[:space:]]*ansible_host:[[:space:]]*") {
      sub("^[^:]*:[[:space:]]*", "", $0)
      gsub(/^[ \t]+|[ \t]+$/, "", $0)
      print $0
      exit
    }
  ' "$INVENTORY"
}

# --- Parse control_plane_endpoint ---
control_plane_endpoint=$(get_var_from_inventory "control_plane_endpoint" || true)
echo "[DEBUG] Parsed control_plane_endpoint: '${control_plane_endpoint:-<empty>}'" | tee -a "$LOG_FILE"

if [[ -z "$control_plane_endpoint" ]]; then
  echo "ERROR: control_plane_endpoint not found in $INVENTORY" | tee -a "$LOG_FILE"
  echo "Snippet of $INVENTORY for debugging:" | tee -a "$LOG_FILE"
  sed -n '1,240p' "$INVENTORY" | tee -a "$LOG_FILE"
  exit 1
fi

control_plane_host="${control_plane_endpoint%%:*}"
echo "[INFO] control_plane_endpoint = $control_plane_endpoint" | tee -a "$LOG_FILE"
echo "[INFO] control_plane_host = $control_plane_host" | tee -a "$LOG_FILE"

# --- Find master ansible_host ---
master_ansible_host=$(get_ansible_host_for "master" || true)
if [[ -z "$master_ansible_host" ]]; then
  echo "ERROR: master.ansible_host not found in $INVENTORY" | tee -a "$LOG_FILE"
  exit 1
fi
echo "[INFO] master ansible_host = $master_ansible_host" | tee -a "$LOG_FILE"

# --- Collect worker hosts dynamically ---
worker_hosts=()
for w in worker1 worker2 worker3 worker4; do
  ah=$(get_ansible_host_for "$w" || true)
  [[ -n "$ah" ]] && worker_hosts+=("$ah")
done
echo "[INFO] workers: ${worker_hosts[*]:-<none>}" | tee -a "$LOG_FILE"

# --- Read loadbalancer_apiserver.address ---
loadbal_addr=$(awk '
  /^[[:space:]]*loadbalancer_apiserver:[[:space:]]*$/ { found=1; next }
  found && match($0, "^[[:space:]]*address:[[:space:]]*") {
    sub("^[^:]*:[[:space:]]*", "", $0)
    gsub(/^[ \t]+|[ \t]+$/, "", $0)
    print $0
    exit
  }
' "$INVENTORY" || true)
echo "[INFO] loadbalancer_apiserver.address = ${loadbal_addr:-<none>}" | tee -a "$LOG_FILE"

if [[ -n "$loadbal_addr" && "$control_plane_host" != "$loadbal_addr" && "$control_plane_host" != "$master_ansible_host" ]]; then
  echo "⚠ WARNING: control_plane_host ($control_plane_host) differs from loadbalancer_apiserver.address ($loadbal_addr) and master ($master_ansible_host)" | tee -a "$LOG_FILE"
  echo "SAN mismatch may break TLS — check inventory!" | tee -a "$LOG_FILE"
fi

# --- Step 1: Cleanup ---
echo "[STEP] Running Ansible cleanup playbook..." | tee -a "$LOG_FILE"
if [[ -f cleanup.yml ]]; then
  ansible-playbook -i "$INVENTORY" cleanup.yml -vvv 2>&1 | tee -a "$LOG_FILE" || true
else
  [[ -f pre_cleanup.yml ]] && ansible-playbook -i "$INVENTORY" pre_cleanup.yml -vvv 2>&1 | tee -a "$LOG_FILE" || true
  [[ -f reset_k8s.yml ]] && ansible-playbook -i "$INVENTORY" reset_k8s.yml -vvv 2>&1 | tee -a "$LOG_FILE" || true
fi

# --- Step 2: Local cleanup ---
if [[ -x ./clean_k8s_local.sh ]]; then
  echo "[STEP] Running clean_k8s_local.sh..." | tee -a "$LOG_FILE"
  sudo bash ./clean_k8s_local.sh 2>&1 | tee -a "$LOG_FILE" || true
fi
if [[ -x ./reset_ports.sh ]]; then
  echo "[STEP] Running reset_ports.sh..." | tee -a "$LOG_FILE"
  sudo bash ./reset_ports.sh 2>&1 | tee -a "$LOG_FILE" || true
fi

# --- Step 3: Check ports ---
echo "[STEP] Checking ports 6443,10250,10257,10259..." | tee -a "$LOG_FILE"
if ! ss -tulpn | grep -E '6443|10250|10257|10259'; then
  echo "Ports appear free" | tee -a "$LOG_FILE"
fi

# --- Step 4: Generate inventory & deploy ---
echo "[STEP] Generating inventory and running install..." | tee -a "$LOG_FILE"
python3 generate_inventory.py 2>&1 | tee -a "$LOG_FILE"

# --- Step 5: Wait and check API ---
echo "[STEP] Waiting 10s for API to settle..." | tee -a "$LOG_FILE"
sleep 10

echo "[STEP] Checking API health (insecure) at $control_plane_host:6443 ..." | tee -a "$LOG_FILE"
if curl -k --max-time 10 "https://${control_plane_host}:6443/healthz" 2>&1 | tee -a "$LOG_FILE"; then
  echo "✅ API responded (healthz check)" | tee -a "$LOG_FILE"
else
  echo "⚠ WARNING: API healthz did not respond. Check kubelet, apiserver and logs." | tee -a "$LOG_FILE"
fi

# --- Step 6: Collect cert fingerprints ---
echo "[STEP] Collecting CA fingerprints..." | tee -a "$LOG_FILE"
echo "=== Master: $master_ansible_host ===" | tee -a "$LOG_FILE"
ssh -o BatchMode=yes -o ConnectTimeout=8 "ubuntu@${master_ansible_host}" \
  "sudo openssl x509 -in /etc/kubernetes/pki/ca.crt -noout -fingerprint -sha256" 2>&1 | tee -a "$LOG_FILE" || true

for wh in "${worker_hosts[@]}"; do
  echo "=== Worker: $wh ===" | tee -a "$LOG_FILE"
  ssh -o BatchMode=yes -o ConnectTimeout=8 "ubuntu@${wh}" \
    "sudo openssl x509 -in /var/lib/kubelet/pki/ca.crt -noout -fingerprint -sha256" 2>&1 | tee -a "$LOG_FILE" || \
    echo "No CA file on worker $wh or SSH failed" | tee -a "$LOG_FILE"
done

echo "✅ Done. Log saved to ${LOG_FILE}"
