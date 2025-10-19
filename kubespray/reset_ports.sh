#!/usr/bin/env bash
set -e

echo "[1/5] Killing containers kube-* ..."
crictl --runtime-endpoint unix:///var/run/containerd/containerd.sock ps -a | grep kube || echo "контейнеров kube-* не найдено"
crictl --runtime-endpoint unix:///var/run/containerd/containerd.sock ps -a | grep kube | awk '{print $1}' | xargs -r crictl --runtime-endpoint unix:///var/run/containerd/containerd.sock rm -f

echo "[2/5] kubeadm reset ..."
kubeadm reset -f || true

echo "[3/5] Deleting control-plane manifest..."
rm -f /etc/kubernetes/manifests/kube-apiserver.yaml
rm -f /etc/kubernetes/manifests/kube-controller-manager.yaml
rm -f /etc/kubernetes/manifests/kube-scheduler.yaml
rm -f /etc/kubernetes/admin.conf || true

echo "[4/5] Restarting containerd and kubelet..."
systemctl restart containerd
systemctl restart kubelet

echo "[5/5] Checking free ports..."
ss -tulpn | grep -E '6443|10250|10257|10259' || echo "Ports free"

echo "Cleanup finished. You can start ansible-playbook now."
