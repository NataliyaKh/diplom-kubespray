Commands for Grafana (from helm/kube-prometheus)

```
kubectl apply --server-side -f manifests/setup

kubectl wait \
    --for condition=Established \
    --all CustomResourceDefinition \
    --namespace=monitoring

kubectl apply -f manifests/

kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.8.2/deploy/static/provider/cloud/deploy.yaml

kubectl patch deployment -n ingress-nginx ingress-nginx-controller -p '{"spec":{"template":{"spec":{"hostNetwork":true,"dnsPolicy":"ClusterFirstWithHostNet","nodeSelector":{"kubernetes.io/hostname":"master"},"tolerations":[{"effect":"NoSchedule","key":"node-role.kubernetes.io/control-plane"},{"effect":"NoSchedule","key":"node-role.kubernetes.io/master"}]}}}}'

kubectl patch svc -n ingress-nginx ingress-nginx-controller -p '{"spec": {"type": "ClusterIP"}}'

kubectl rollout restart deployment -n ingress-nginx ingress-nginx-controller

kubectl apply --server-side -f manifests/setup ; kubectl wait --for condition=Established --all CustomResourceDefinition --namespace=monitoring ; kubectl apply -f manifests/

kubectl apply -f - <<EOF
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: grafana
  namespace: monitoring
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: grafana
  policyTypes:
  - Ingress
  ingress:
  - from: []
    ports:
    - protocol: TCP
      port: 3000
EOF

kubectl apply -f - <<EOF
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: grafana
  namespace: monitoring
  annotations:
    nginx.ingress.kubernetes.io/rewrite-target: /
spec:
  ingressClassName: nginx
  rules:
  - http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: grafana
            port:
              number: 3000
EOF
```
