#!/usr/bin/env python3

import json
import yaml
import subprocess
import ipaddress
import sys
import os
import socket
import time

# Настройки
terraform_dir = "/home/vboxuser/git/diplom/diplom-k8s/nodes"
output_path = "inventory/mycluster/hosts.yaml"
use_public_api = False
ssh_user = "ubuntu"
ssh_tunnel_port = 6443

inv_dir = os.path.dirname(output_path)
admin_conf_path = os.path.join(inv_dir, "artifacts", "admin.conf")
kube_dir = os.path.expanduser("~/.kube")
kube_config = os.path.join(kube_dir, "config")


def get_subnet(ip, cidr="24"):
    return str(ipaddress.IPv4Network(ip + f'/{cidr}', strict=False))


def is_port_open(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def cleanup_ssh_tunnel(port):
    print(f"Cleaning up any SSH tunnels on localhost port {port}...")
    subprocess.run(["pkill", "-f", f"ssh.*{port}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def ensure_ssh_tunnel(master_pub_ip, master_priv_ip, local_port):
    if is_port_open("127.0.0.1", local_port):
        print(f"SSH tunnel already active on 127.0.0.1:{local_port}")
        return

    cleanup_ssh_tunnel(local_port)

    print(f"Starting SSH tunnel: 127.0.0.1:{local_port} → {master_priv_ip}:6443 via {master_pub_ip}")
    subprocess.Popen([
        "ssh", "-f", "-N",
        "-L", f"{local_port}:{master_priv_ip}:6443",
        f"{ssh_user}@{master_pub_ip}"
    ])

    # Ждём открытия порта до 10 секунд
    for _ in range(10):
        if is_port_open("127.0.0.1", local_port):
            print("SSH tunnel established")
            return
        time.sleep(1)

    print("Failed to open SSH tunnel")
    sys.exit(1)


print("Getting terraform output JSON...")
with open("terraform-output.json", "w") as outfile:
    subprocess.run(["terraform", "output", "-json"], cwd=terraform_dir, stdout=outfile, check=True)

print("Reading terraform output...")
with open("terraform-output.json", "r") as f:
    tf_output = json.load(f)

master_private_ips = tf_output["private_master_ips"]["value"]
master_public_ips = tf_output["external_master_ips"]["value"]
worker_private_ips = tf_output["private_worker_ips"]["value"]
worker_public_ips = tf_output["external_worker_ips"]["value"]

master_priv_ip = master_private_ips[0]
master_pub_ip = master_public_ips[0]

if use_public_api:
    api_server_ip = master_pub_ip
else:
    ensure_ssh_tunnel(master_pub_ip, master_priv_ip, ssh_tunnel_port)
    api_server_ip = "127.0.0.1"

# Собираем все подсети для маршрутов
all_ips = master_private_ips + worker_private_ips
all_subnets = sorted(set(get_subnet(ip) for ip in all_ips))
master_subnet = get_subnet(master_priv_ip)
master_routes = []

for subnet in all_subnets:
    if subnet != master_subnet:
        gw = str(ipaddress.ip_network(subnet)[0] + 1)
        master_routes.append({"to": subnet, "via": gw})

inventory = {
    "all": {
        "hosts": {},
        "vars": {
            "ansible_python_interpreter": "/usr/bin/python3",
            "kube_network_plugin": "calico",
            "ansible_become": True,
            "public_api_ip": api_server_ip,
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
            "bastion": {"hosts": {}},
            "calico_rr": {"hosts": {}}
        }
    }
}

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
inventory["all"]["children"]["bastion"]["hosts"]["bastion"] = master_entry

for i, (priv_ip, pub_ip) in enumerate(zip(worker_private_ips, worker_public_ips), start=1):
    name = f"worker{i}"
    subnet = get_subnet(priv_ip)
    worker_routes = []
    for sn in all_subnets:
        if sn != subnet:
            gw = str(ipaddress.ip_network(sn)[0] + 1)
            worker_routes.append({"to": sn, "via": gw})

    inventory["all"]["children"]["kube_node"]["hosts"][name] = {
        "ansible_host": pub_ip,
        "ip": priv_ip,
        "access_ip": priv_ip,
        "ansible_user": ssh_user,
        "routes_to_add": worker_routes,
        "subnet": subnet,
    }

os.makedirs(os.path.dirname(output_path), exist_ok=True)
with open(output_path, "w") as f:
    yaml.dump(inventory, f, default_flow_style=False, sort_keys=False)

print(f"Inventory created at {output_path}")

print("Deploying SSH keys...")
subprocess.run(["ansible-playbook", "-i", output_path, "add_ssh_keys.yml"], check=True)

print("Installing kubeadm, kubelet, kubectl...")
subprocess.run(["ansible-playbook", "-i", output_path, "install_kube_tools.yml"], check=True)

print("Running kubespray cluster deployment...")
subprocess.run(["ansible-playbook", "-i", output_path, "playbooks/cluster.yml"], check=True)

print("Deploying kubeadm-client.conf...")
subprocess.run(["ansible-playbook", "-i", output_path, "create_kubeadm.yml"], check=True)

# Создаем каталог ~/.kube, копируем конфиг и при необходимости патчим kubeconfig под локальный туннель
os.makedirs(kube_dir, exist_ok=True)
subprocess.run(["cp", admin_conf_path, kube_config], check=True)

if not use_public_api:
    print(f"Patching kubeconfig: replacing https://{master_priv_ip}:6443 with https://127.0.0.1:6443")
    subprocess.run([
        "sed", "-i",
        f"s|https://{master_priv_ip}:6443|https://127.0.0.1:6443|",
        kube_config
    ], check=True)
    print("kubeconfig patched successfully")

os.environ["KUBECONFIG"] = kube_config

print("\n=== Cluster Nodes ===")
subprocess.run(["kubectl", "get", "nodes"])

print("\n=== All Pods ===")
subprocess.run(["kubectl", "get", "pods", "--all-namespaces"])

print("\nKubectl configured!")
