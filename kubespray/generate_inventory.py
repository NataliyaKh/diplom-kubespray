#!/usr/bin/env python3

import json
import yaml
import subprocess
import ipaddress
import os
import sys

def install_dependencies():
    print(">>> Installing Python and Ansible dependencies...")
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "boto3", "botocore"], check=True)
    subprocess.run(["ansible-galaxy", "collection", "install", "amazon.aws"], check=True)

def get_subnet(ip, cidr="24"):
    return str(ipaddress.IPv4Network(ip + f'/{cidr}', strict=False))

def load_terraform_output(cwd):
    result = subprocess.run(["terraform", "output", "-json"], cwd=cwd, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)

def main():
    install_dependencies()

    # --- Settings ---
    terraform_nodes_dir = "/home/vboxuser/git/diplom/diplom-k8s/nodes"
    terraform_sa_bucket_dir = "/home/vboxuser/git/diplom/diplom-tf/sa_bucket"
    output_path = "inventory/mycluster/hosts.yaml"
    ssh_user = "ubuntu"
    api_server_target_port = 6443
    vault_password_file = "group_vars/all/vault_pass.txt"

    inv_dir = os.path.dirname(output_path)
    admin_conf_path = os.path.join(inv_dir, "artifacts", "admin.conf")
    kube_dir = os.path.expanduser("~/.kube")
    kube_config = os.path.join(kube_dir, "config")

    print(">>> Loading terraform output from nodes...")
    tf_nodes = load_terraform_output(terraform_nodes_dir)

    print(">>> Loading terraform output from sa_bucket...")
    tf_bucket = load_terraform_output(terraform_sa_bucket_dir)

    # --- Parsing IPs ---
    master_private_ips = tf_nodes.get("private_master_ips", {}).get("value", [])
    master_public_ips = tf_nodes.get("external_master_ips", {}).get("value", [])
    worker_private_ips = tf_nodes.get("private_worker_ips", {}).get("value", [])
    worker_public_ips = tf_nodes.get("external_worker_ips", {}).get("value", [])

    if not master_private_ips or not master_public_ips:
        print("ERROR: Master IPs missing in terraform output")
        sys.exit(1)

    master_priv_ip = master_private_ips[0]
    master_pub_ip = master_public_ips[0]
    print(f"master_pub_ip = {master_pub_ip}")

    # --- API IP ---
    api_server_ip = master_priv_ip
    control_plane_endpoint = f"{api_server_ip}:{api_server_target_port}"

    # --- Routes ---
    all_ips = master_private_ips + worker_private_ips
    all_subnets = sorted(set(get_subnet(ip) for ip in all_ips))
    master_subnet = get_subnet(master_priv_ip)
    master_routes = []
    for subnet in all_subnets:
        if subnet != master_subnet:
            gw = str(ipaddress.ip_network(subnet)[0] + 1)
            master_routes.append({"to": subnet, "via": gw})

    # --- Inventory ---
    inventory = {
        "all": {
            "hosts": {},
            "vars": {
                "ansible_python_interpreter": "/usr/bin/python3",
                "kube_network_plugin": "calico",
                "ansible_become": True,
                "api_server_port": api_server_target_port,
                "public_api_ip": master_pub_ip,
                "loadbalancer_apiserver": {
                    "address": master_priv_ip,
                    "port": api_server_target_port
                },
                "kubeadm_config_api_fqdn": master_priv_ip,
                "control_plane_endpoint": control_plane_endpoint,
                "kubeadm_init_retry_timeout": 600
            },
            "children": {
                "kube_control_plane": {"hosts": {}},
                "kube_node": {"hosts": {}},
                "etcd": {"hosts": {}},
                "k8s_cluster": {
                    "children": {
                        "kube_control_plane": {},
                        "kube_node": {}
                    }
                },
                "calico_rr": {"hosts": {}}
            }
        }
    }

    # --- Master ---
    master_entry = {
        "ansible_host": master_pub_ip,
        "ip": master_priv_ip,
        "access_ip": master_priv_ip,
        "ansible_user": ssh_user,
        "routes_to_add": master_routes,
        "subnet": master_subnet,
        "ansible_ssh_common_args": ""
    }

    inventory["all"]["children"]["kube_control_plane"]["hosts"]["master"] = master_entry
    inventory["all"]["children"]["etcd"]["hosts"]["master"] = master_entry
    inventory["all"]["hosts"]["master"] = master_entry

    # --- Workers ---
    for i, (priv_ip, pub_ip) in enumerate(zip(worker_private_ips, worker_public_ips), start=1):
        name = f"worker{i}"
        subnet = get_subnet(priv_ip)
        worker_routes = []
        for sn in all_subnets:
            if sn != subnet:
                gw = str(ipaddress.ip_network(sn)[0] + 1)
                worker_routes.append({"to": sn, "via": gw})
        worker_entry = {
            "ansible_host": pub_ip,
            "ip": priv_ip,
            "access_ip": priv_ip,
            "ansible_user": ssh_user,
            "routes_to_add": worker_routes,
            "subnet": subnet
        }
        inventory["all"]["children"]["kube_node"]["hosts"][name] = worker_entry
        inventory["all"]["hosts"][name] = worker_entry

    # --- Save inventory ---
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        yaml.dump(inventory, f, default_flow_style=False, sort_keys=False)
    print(f"Inventory saved to {output_path}")

    # --- etcd.yml ---
    etcd_group_vars_dir = os.path.join(os.path.dirname(output_path), "group_vars")
    os.makedirs(etcd_group_vars_dir, exist_ok=True)
    etcd_file_path = os.path.join(etcd_group_vars_dir, "etcd.yml")
    etcd_config = {
        "etcd_deployment_type": "host",
        "etcd_data_dir": "/var/lib/etcd"
    }
    with open(etcd_file_path, "w") as etcd_file:
        yaml.dump(etcd_config, etcd_file, default_flow_style=False, sort_keys=False)
    print(f"etcd.yml created at {etcd_file_path}")

    # --- S3 variables ---
    s3_bucket_name = tf_bucket.get("bucket_name", {}).get("value")
    s3_access_key = tf_bucket.get("access_key", {}).get("value")
    s3_secret_key = tf_bucket.get("secret_key", {}).get("value")

    if not all([s3_bucket_name, s3_access_key, s3_secret_key]):
        print("WARNING: Some S3 variables are missing!")

    s3_config = {
        "s3_bucket_name": s3_bucket_name,
        "s3_access_key": s3_access_key,
        "s3_secret_key": s3_secret_key,
    }

    group_vars_all_dir = os.path.join(inv_dir, "group_vars", "all")
    os.makedirs(group_vars_all_dir, exist_ok=True)
    s3_yml_path = os.path.join(group_vars_all_dir, "s3.yml")
    with open(s3_yml_path, "w") as s3_file:
        yaml.dump(s3_config, s3_file, default_flow_style=False, sort_keys=False)
    print(f"s3.yml created at {s3_yml_path}")

    # --- Check vault password file exists ---
    if not os.path.isfile(vault_password_file):
        print(f"ERROR: Vault password file {vault_password_file} not found!")
        print("Please create this file with your Ansible vault password.")
        sys.exit(1)

    # --- Run playbooks ---
    vault_arg = ["--vault-password-file", vault_password_file]

    print(">>> Running Ansible playbooks...")

    playbooks = [
        "add_ssh_keys.yml",
        "install_kube_tools.yml",
        "playbooks/fix_hosts.yml",
        "playbooks/restore_ca.yml",
        "playbooks/cluster.yml",
        "playbooks/fetch_adminconf.yml",
        "create_kubeadm.yml"
    ]

    for pb in playbooks:
        try:
            subprocess.run(["ansible-playbook", "-i", output_path, pb] + vault_arg, check=True)
        except subprocess.CalledProcessError as e:
            print(f"ERROR running playbook {pb}: {e}")
            sys.exit(1)

    # --- Show inventory content ---
    with open(output_path, "r") as f:
        content = f.read()
        print("=== Inventory content ===")
        print(content)

    # --- kubeconfig ---
    os.makedirs(kube_dir, exist_ok=True)
    if os.path.exists(admin_conf_path):
        subprocess.run(["cp", admin_conf_path, kube_config], check=True)
        print("Copied admin.conf to ~/.kube/config")
    else:
        print("Warning: admin.conf not found, skipping kubeconfig setup.")

if __name__ == "__main__":
    main()
