KUBECONFIG_PATH = $(CURDIR)/inventory/mycluster/artifacts/admin.conf
MASTER_IP = $(shell cd ../diplom-k8s/nodes && terraform output -json private_master_ips | jq -r '.[0]')

install:
	python3 generate_inventory.py

	cp $(KUBECONFIG_PATH) ~/.kube/config
	chmod 600 ~/.kube/config

	@echo "MASTER PRIVATE IP = $(MASTER_IP)"
	@echo "KUBECONFIG = $(KUBECONFIG_PATH)"

	@export KUBECONFIG=$(KUBECONFIG_PATH) && \
	ansible-playbook -i inventory/mycluster/hosts.yaml playbooks/fix-kubeconfig.yml \
		-e "master_ip=$(MASTER_IP)"

	@echo "KUBECONFIG updated. You can now use kubectl."
