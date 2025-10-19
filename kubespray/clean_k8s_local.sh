#!/usr/bin/env bash
set -euo pipefail

echo "============================"
echo " Cleaning Kubernetes node: $(hostname)"
echo "============================"

# 1. Stop services
systemctl stop kubelet 2>/dev/null || true
systemctl stop containerd 2>/dev/null || true

# 2. Reset kubeadm
kubeadm reset -f || true

# 3. Kill processes on common ports
for port in 6443 10250 10257 10259; do
    pid=$(lsof -ti tcp:$port || true)
    if [[ -n "$pid" ]]; then
        kill -9 "$pid" || true
    fi
done

# 4. Remove directories
rm -rf /etc/kubernetes \
       /var/lib/etcd \
       /var/lib/kubelet \
       /var/lib/cni \
       /run/kubernetes \
       ~/.kube

# 5. Disable swap
swapoff -a
sed -i.bak '/ swap / s/^\(.*\)$/#\1/g' /etc/fstab || true

# 6. Check ports
echo "Checking ports..."
lsof -iTCP -sTCP:LISTEN | grep -E "6443|10250|10257|10259" || echo "Ports are free"

# 7. Check directories
if [[ ! -d /etc/kubernetes && ! -d /var/lib/etcd ]]; then
    echo "Cleanup OK"
else
    echo "Warning: directories still exist"
fi

echo "============================"
echo " Done"
echo "============================"
