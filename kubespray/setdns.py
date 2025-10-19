import re
import os

templates_dir = 'roles/kubernetes/control-plane/templates/'

pattern = re.compile(
    r'{%\s*for\s+dns_address\s+in\s+kubelet_cluster_dns\s*%}.*?{%\s*endfor\s*%}',
    re.DOTALL
)

for root, _, files in os.walk(templates_dir):
    for filename in files:
        filepath = os.path.join(root, filename)
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        if pattern.search(content):
            new_content = pattern.sub('- 10.233.0.10', content)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print(f'Заменено в файле: {filepath}')
        else:
            print(f'Блок не найден в: {filepath}')
