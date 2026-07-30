
# Kubernetes Home Lab
## Install first node:

```shell
curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=v1.32.10+k3s1 sh -s - --cluster-init --token k3sblog --tls-san 192.168.32.2
```
Install Kube VIP so that it is floating between nodes in cluster to provide API HA
We only need it for control plane, Services will be handled by MetalLB
```shell
sudo tee /var/lib/rancher/k3s/server/manifests/kube-vip.yaml <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: kube-vip
  namespace: kube-system
spec:
  serviceAccountName: kube-vip
  hostNetwork: true
  containers:
  - name: kube-vip
    image: ghcr.io/kube-vip/kube-vip:v1.0.3
    args:
      - manager
    env:
      - name: vip_arp
        value: "true"
      - name: cp_enable
        value: "true"
      - name: vip_interface
        value: "eno1"
      - name: address
        value: "192.168.32.2"
    securityContext:
      capabilities:
        add:
          - NET_ADMIN
          - NET_RAW
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: kube-vip
  namespace: kube-system
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: kube-vip
rules:
- apiGroups: ["coordination.k8s.io"]
  resources: ["leases"]
  verbs: ["get", "list", "watch", "create", "update", "patch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: kube-vip
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: kube-vip
subjects:
- kind: ServiceAccount
  name: kube-vip
  namespace: kube-system
EOF

```
!!!!
Master node might need touching file to load it!!

```
sudo touch /var/lib/rancher/k3s/server/manifests/kube-vip.yaml
```




## Join another node to the cluster:

```shell
curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=v1.32.10+k3s1 sh -s - server --server https://192.168.32.2:6443 --token k3sblog
```

## Install ArgoCD

```shell
helm repo add argo https://argoproj.github.io/argo-helm
```
```shell
kubectl create namespace argocd
```
```shell
helm install argocd argo/argo-cd \
  -n argocd \
  -f argo-values.yaml
```
Add secret to be able to pull charts from Artifact Registry
```shell
k apply -f gitops/argo-cd-helm.secret.yaml
```
## Apply bootstrap Applications
```shell
cd gitops/clusters/home/bootstrap/

kubectl apply -f home-root.yaml
```

## Nodes DNS

To prevent issues with network DNS comming from router or something else 
On each node do
```shell
sudo nano /etc/systemd/resolved.conf

Put :
[Resolve]
DNS=1.1.1.1 8.8.8.8
FallbackDNS=8.8.4.4
DNSStubListener=yes



sudo ln -sf /run/systemd/resolve/resolv.conf /etc/resolv.conf
sudo systemctl restart systemd-resolved
```

## Node file watch settings:

```shell
sudo mkdir -p /etc/sysctl.d && printf "fs.inotify.max_user_watches = 1048576\nfs.inotify.max_user_instances = 8192\nfs.file-max = 2097152\n" | sudo tee /etc/sysctl.d/99-inotify.conf && sudo /sbin/sysctl --system
```

## iSCSI Multipath for QNAP Trident CSI

Trident v1.6.0+ expects multipath device maps even for single-path iSCSI LUNs.
Without this config, newly attached volumes fail with "multipath device not found".
Run on each node:

```shell
sudo bash -c 'cat > /etc/multipath.conf << "EOF"
defaults {
    find_multipaths no
    user_friendly_names yes
}
blacklist {
    devnode "^(ram|raw|loop|fd|md|dm-|sr|scd|st|sda)[0-9]*"
}
EOF
systemctl restart multipathd'
```

Verify with: `sudo multipath -ll` (should show dm- maps for each iSCSI LUN).

## Reserved ips :
192.168.32.2 -Kube API handled by kube vip


## Accessing GCP Secrets

### Create service account :

```shell
gcloud iam service-accounts create eso-gsm \
  --description="External Secrets Operator access to GSM" \
  --display-name="eso-gsm"
```

### Create read permission
```shell
gcloud projects add-iam-policy-binding techyon-393614 \
  --member="serviceAccount:eso-gsm@techyon-393614.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
  ```


### Download key file
```shell
gcloud iam service-accounts keys create eso-gsm-key.json \
  --iam-account=eso-gsm@techyon-393614.iam.gserviceaccount.com
  
```

Create secret in `secrets` namespace for external secrets to use - after ArgoCD deploys first external secrets
```shell
kubectl create secret generic gsm-sa-key \
  --namespace secrets \
  --from-file=key.json=eso-gsm-key.json
```

## Allowing Jenkins to push images to Artifact Registry
```shell
### Create service account
gcloud iam service-accounts create jenkins-artifact-registery \
--display-name "Jenkins Docker Builder"
```
### Bind permissions to service account
```shell
gcloud projects add-iam-policy-binding techyon-393614 \
  --member="serviceAccount:jenkins-artifact-registery@techyon-393614.iam.gserviceaccount.com" \
  --role="roles/artifactregistry.writer"
  
gcloud projects add-iam-policy-binding techyon-393614 \
  --member="serviceAccount:jenkins-artifact-registery@techyon-393614.iam.gserviceaccount.com" \
  --role="roles/artifactregistry.reader"
  


--For Chartsmueseum to be able to read and write charts to bucket :

# Write-only
gcloud storage buckets add-iam-policy-binding gs://techyon-393614-charts \
  --member="serviceAccount:jenkins-artifact-registery@techyon-393614.iam.gserviceaccount.com" \
  --role="roles/storage.objectCreator"

# Read-only
gcloud storage buckets add-iam-policy-binding gs://techyon-393614-charts \
  --member="serviceAccount:jenkins-artifact-registery@techyon-393614.iam.gserviceaccount.com" \
  --role="roles/storage.objectViewer"
  
```

### Get json key
```shell
gcloud iam service-accounts keys create jenkinsArtifactRegistry.json \
  --iam-account jenkins-artifact-registery@techyon-393614.iam.gserviceaccount.com
```


# Creating in cluster CA
### Generate CA key and certificate
```shell
openssl genrsa -out homelab-root-ca.key 4096

openssl req -x509 -new -nodes \
  -key homelab-root-ca.key \
  -sha256 \
  -days 3650 \
  -out homelab-root-ca.crt \
  -subj "/CN=Homelab Root CA"
  
```

### Add CA cert to trusted ones :
```shell
sudo cp homelab-root-ca.crt /usr/local/share/ca-certificates/
sudo chmod 644 /usr/local/share/ca-certificates/homelab-root-ca.crt

```

```shell
sudo update-ca-certificates
```


