# ===========================
# FULL K8S CLUSTER RESET + REDEPLOY (Kubespray)
# ===========================

# 1. Очистка артефактов на локалке
rm -rf inventory/mycluster/artifacts
rm -rf inventory/mycluster/.retry
rm -f cluster_redeploy_*.log

# 2. Очистка на всех нодах
ansible all -i inventory/mycluster/hosts.yaml -b -m shell -a '
  systemctl stop kubelet 2>/dev/null || true;
  systemctl stop containerd 2>/dev/null || true;
  kubeadm reset -f 2>/dev/null || true;
  rm -rf /etc/kubernetes /var/lib/etcd /var/lib/kubelet /root/.kube;
  rm -rf /var/lib/cni /etc/cni/net.d;
  ip link delete cni0 2>/dev/null || true;
  ip link delete flannel.1 2>/dev/null || true;
  ip link delete tunl0 2>/dev/null || true;
  iptables -F && iptables -t nat -F && iptables -t mangle -F && iptables -X;
  ipvsadm --clear 2>/dev/null || true;
'

# 3. Проверка, что пинги проходят
ansible all -i inventory/mycluster/hosts.yaml -m ping

# 4. Деплой нового кластера
ansible-playbook -i inventory/mycluster/hosts.yaml \
  -b -v \
  --become \
  /playbooks/cluster.yml | tee cluster_redeploy_$(date +%Y%m%d_%H%M%S).log
