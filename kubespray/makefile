KUBECONFIG_PATH := $(CURDIR)/inventory/mycluster/artifacts/admin.conf

MASTER_PUBLIC_IP := $(shell cd /home/vboxuser/git/diplom/diplom-k8s/nodes && terraform output -json external_master_ips | jq -r '.[0]')
MASTER_PRIVATE_IP := $(shell cd /home/vboxuser/git/diplom/diplom-k8s/nodes && terraform output -json private_master_ips | jq -r '.[0]')

SSH_USER := ubuntu
LOCAL_PORT := 16443
REMOTE_PORT := 6443

tunnel:
	@echo ">>> Killing old tunnels..."
	-pkill -f "ssh.*$(LOCAL_PORT)" || true
	@echo ">>> Setting ssh tunnel at $(LOCAL_PORT) → $(MASTER_PRIVATE_IP):$(REMOTE_PORT)"
	ssh -f -N -L $(LOCAL_PORT):$(MASTER_PRIVATE_IP):$(REMOTE_PORT) $(SSH_USER)@$(MASTER_PUBLIC_IP)
	@echo ">>> Checking tunnel..."
	sleep 2
	nc -zv 127.0.0.1 $(LOCAL_PORT)

reset:
	@echo ">>> Cleaning Kubernetes cluster on all nodes..."
	ansible-playbook -i inventory/mycluster/hosts.yaml reset_k8s.yml || true
	@echo ">>> Manual cleanup on localhost (safety net)..."
	sudo kubeadm reset -f || true
	sudo systemctl stop kubelet || true
	sudo crictl rm -af || true
	sudo fuser -k 6443/tcp 10257/tcp 10259/tcp 10250/tcp || true
	sudo rm -rf /etc/kubernetes /var/lib/etcd /var/lib/kubelet /var/lib/cni /run/kubernetes ~/.kube || true

install: tunnel
	@echo ">>> Generating fresh inventory..."
	python3 generate_inventory.py

	@if [ -f "$(KUBECONFIG_PATH)" ]; then \
		echo ">>> Copying kubeconfig..."; \
		mkdir -p ~/.kube; \
		cp $(KUBECONFIG_PATH) ~/.kube/config; \
		chmod 600 ~/.kube/config; \
	else \
		echo ">>> Warning: kubeconfig not found at $(KUBECONFIG_PATH)"; \
	fi

	@echo "MASTER PRIVATE IP = $(MASTER_PRIVATE_IP)"
	@echo "MASTER PUBLIC IP = $(MASTER_PUBLIC_IP)"
	@echo "KUBECONFIG = $(KUBECONFIG_PATH)"

	@echo ">>> Updating kubeconfig for tunnel..."
	@export KUBECONFIG=$(KUBECONFIG_PATH) && \
	ansible-playbook -i inventory/mycluster/hosts.yaml playbooks/fix-kubeconfig.yml \
		-e "master_ip=127.0.0.1" \
		-e "master_port=$(LOCAL_PORT)"

	@echo ">>> Checking cluster..."
	@if [ -f "$(KUBECONFIG_PATH)" ]; then \
		KUBECONFIG=$(KUBECONFIG_PATH) kubectl cluster-info; \
		KUBECONFIG=$(KUBECONFIG_PATH) kubectl get nodes; \
	else \
		echo ">>> kubeconfig not found, cannot check cluster."; \
	fi

tunnel-clean:
	@echo ">>> Killing tunnel..."
	-pkill -f "ssh.*$(LOCAL_PORT)" || true
