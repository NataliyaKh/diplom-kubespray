#!/usr/bin/env python3

import json
import yaml
import subprocess
import ipaddress
import sys
import os
import socket
import time

terraform_dir = "/home/vboxuser/git/diplom/diplom-k8s/nodes"
output_path = "inventory/mycluster/hosts.yaml"
ssh_user = "ubuntu"
ssh_tunnel_port = 16443
api_server_target_port = 6443
use_public_api = True

inv_dir = os.path.dirname(output_path)
admin_conf_path = os.path.join(inv_dir, "artifacts", "admin.conf")
kube_dir = os.path.expanduser("~/.kube")
kube_config = os.path.join(kube_dir, "config")

def get_subnet(ip, cidr="24"):
    return str(ipaddress.IPv4Network(ip + f'/{cidr}', strict=False))

def is_port_open(host, port, timeout=3.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

def cleanup_ssh_tunnel(port):
    subprocess.run(["pkill", "-f", f"ssh.*{port}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def ensure_ssh_tunnel(master_pub_ip, master_priv_ip, local_port):
    if is_port_open("127.0.0.1", local_port):
        return
    cleanup_ssh_tunnel(local_port)
    subprocess.Popen([
        "ssh", "-f", "-N",
        "-L", f"{local_port}:{master_priv_ip}:{api_server_target_port}",
        f"{ssh_user}@{master_pub_ip}"
    ])
    for _ in range(10):
        if is_port_open("127.0.0.1", local_port):
            return
        time.sleep(1)
    sys.exit(1)

with open("terraform-output.json", "w") as outfile:
    subprocess.run(["terraform", "output", "-json"], cwd=terraform_dir, stdout=outfile, check=True)

with open("terraform-output.json", "r") as f:
    tf_output = json.load(f)

master_private_ips = tf_output["private_master_ips"]["value"]
master_public_ips = tf_output["external_master_ips"]["value"]
worker_private_ips = tf_output["private_worker_ips"]["value"]
worker_public_ips = tf_output["external_worker_ips"]["value"]

master_priv_ip = master_private_ips[0]
master_pub_ip = master_public_ips[0]
print(f"master_pub_ip = {master_pub_ip}")

if use_public_api:
    api_server_ip = master_pub_ip
else:
    ensure_ssh_tunnel(master_pub_ip, master_priv_ip, ssh_tunnel_port)
    api_server_ip = "127.0.0.1"

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
inventory["all"]["hosts"]["master"] = master_entry

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
        "subnet": subnet,
    }
    inventory["all"]["children"]["kube_node"]["hosts"][name] = worker_entry
    inventory["all"]["hosts"][name] = worker_entry

os.makedirs(os.path.dirname(output_path), exist_ok=True)
with open(output_path, "w") as f:
    yaml.dump(inventory, f, default_flow_style=False, sort_keys=False)

subprocess.run(["ansible-playbook", "-i", output_path, "add_ssh_keys.yml"], check=True)
subprocess.run(["ansible-playbook", "-i", output_path, "install_kube_tools.yml"], check=True)
subprocess.run(["ansible-playbook", "-i", output_path, "playbooks/cluster.yml"], check=True)
subprocess.run(["ansible-playbook", "-i", output_path, "create_kubeadm.yml"], check=True)

with open(output_path, "r") as f:
    content = f.read()
    print("=== Written inventory/mycluster/hosts.yaml content ===")
    print(content)

os.makedirs(kube_dir, exist_ok=True)
subprocess.run(["cp", admin_conf_path, kube_config], check=True)

subprocess.run([
    "sed", "-i",
    rf"s|https://.*:{api_server_target_port}|https://127.0.0.1:{ssh_tunnel_port}|",
    kube_config
], check=True)

subprocess.run(["rm", "-rf", os.path.expanduser("~/.kube/cache")])

bashrc = os.path.expanduser("~/.bashrc")
subprocess.run(["sed", "-i", "/KUBECONFIG/d", bashrc], check=False)
with open(bashrc, "a") as f:
    f.write(f"\nexport KUBECONFIG={kube_config}\n")
os.environ["KUBECONFIG"] = kube_config

subprocess.run(["kubectl", "cluster-info"])
subprocess.run(["kubectl", "get", "nodes"])
subprocess.run(["kubectl", "get", "pods", "--all-namespaces"])
