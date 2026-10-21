# Simple Python Web App: Docker → AWS ECR → Kubernetes (kind)

This guide takes a small Flask app from source code to a running Kubernetes pod, then shows how to update, roll back and debug it.

1. Build a Docker image
2. Create an AWS ECR repository
3. Push the image from your local machine (EC2) to ECR
4. Deploy it on a kind cluster, pulling from ECR
5. Access it with `kubectl port-forward`
6. Update the app: rebuild, push, `kubectl set image`
7. Watch rollouts, view history, roll back
8. Debug ports, pods and image pulls

---

## Table of contents

1. [Prerequisites and variables](#1-prerequisites-and-variables)
2. [The application and Docker image](#2-the-application-and-docker-image)
3. [AWS ECR concepts and setup](#3-aws-ecr-concepts-and-setup)
4. [Push the image to ECR](#4-push-the-image-to-ecr)
5. [Deploy to Kubernetes (kind) from ECR](#5-deploy-to-kubernetes-kind-from-ecr)
6. [Access the app with port-forward](#6-access-the-app-with-port-forward)
7. [Update the app and the image](#7-update-the-app-and-the-image)
8. [Rollouts, history and rollback](#8-rollouts-history-and-rollback)
9. [Debugging](#9-debugging)
10. [Cleanup](#10-cleanup)
11. [Quick command summary](#11-quick-command-summary)

---

## 1. Prerequisites and variables

- An EC2 instance with an **IAM role attached** that has ECR permissions (e.g. `AmazonEC2ContainerRegistryFullAccess`)
- Installed on the instance: `docker`, `aws` CLI v2, `kind`, `kubectl`
- Inbound **TCP 8080** open in the EC2 security group (for browser access)

Values used in this guide (replace with your own):

| Item | Value |
|------|-------|
| AWS account ID | `963990211788` |
| Region | `us-east-1` |
| ECR repository | `simple-python-webapp` |
| Local image name | `simple-webapp` |
| Deployment / container name | `webapp` |

Set shell variables:

```bash
export AWS_REGION=us-east-1
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export ECR_URI=$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/simple-python-webapp
echo $ECR_URI
```

If `aws sts get-caller-identity` prints your account ID, the IAM role works. No `aws configure` or access keys are needed on EC2.

> **Variables only last for the current terminal session.** A new SSH session loses them. To keep them, add the three `export` lines to `~/.bashrc` and run `source ~/.bashrc`.
>
> Typing `$ECR_URI` on its own does not print it (the shell tries to run it as a command). Use `echo $ECR_URI`.

---

## 2. The application and Docker image

Project layout:

```
simple-webapp/
├── app.py
├── requirements.txt
├── Dockerfile
└── .dockerignore
```

**app.py**
```python
from flask import Flask

app = Flask(__name__)

VERSION = "1.0"
MESSAGE = "Hello from my Docker container!"

@app.route("/")
def home():
    return f"""
    <html>
      <body style="font-family: sans-serif; text-align: center; margin-top: 15vh;">
        <h1>{MESSAGE}</h1>
        <p>App version: <b>{VERSION}</b></p>
      </body>
    </html>
    """

@app.route("/health")
def health():
    return {"status": "ok", "version": VERSION}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
```

**requirements.txt**
```
flask==3.0.3
```

**Dockerfile**
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
EXPOSE 5000
CMD ["python", "app.py"]
```

Dependencies are installed before the code is copied, so code-only changes rebuild quickly thanks to Docker's layer cache.

### Build and test locally

```bash
docker build -t simple-webapp:v1 .
docker run -d --name webapp-test -p 8081:5000 simple-webapp:v1
curl http://localhost:8081/health
docker stop webapp-test && docker rm webapp-test
```

> Always stop and remove local test containers afterwards. A leftover container keeps its host port busy (see [Debugging ports](#91-port-already-in-use)) and can make you look at an old version of the app by mistake.

Useful commands: `docker images`, `docker ps`, `docker ps -a`, `docker logs <container>`.

---

## 3. AWS ECR concepts and setup

- **ECR** (Elastic Container Registry) is AWS's private Docker registry.
- You don't create folders. You create a **repository** per image name, and versions are stored as **tags** (`v1`, `v2`, ...).
- An image's full address is: `<account-id>.dkr.ecr.<region>.amazonaws.com/<repo>:<tag>`
- Docker decides where to push based on this address, so the image name must start with the registry URL.

Create the repository (one time):

```bash
aws ecr create-repository --repository-name simple-python-webapp --region $AWS_REGION
```

Or use the console: **ECR → Repositories → Create repository** (same region as your commands).

---

## 4. Push the image to ECR

**Step 1: Log Docker in to ECR**

```bash
aws ecr get-login-password --region $AWS_REGION | \
docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com
```

How it works:

- `aws ecr get-login-password` uses the EC2 role to get a temporary password (valid **12 hours**).
- The username is always the fixed word `AWS`. This is **not** a Docker Hub login.
- Docker stores the credentials for that registry in `~/.docker/config.json`.
- You should see `Login Succeeded`.

**Step 2: Tag the image with the ECR address**

```bash
docker tag simple-webapp:v1 $ECR_URI:v1
```

**Step 3: Push**

```bash
docker push $ECR_URI:v1
```

**Step 4: Verify**

```bash
aws ecr list-images --repository-name simple-python-webapp --region $AWS_REGION
```

You should see tag `v1`.

---

## 5. Deploy to Kubernetes (kind) from ECR

### 5.1 Create the cluster

```bash
kind create cluster --name demo
kubectl get nodes
```

### 5.2 Create an image pull secret

Pods on kind don't use your EC2 role, so give the cluster the ECR credentials:

```bash
kubectl create secret docker-registry ecr-secret \
  --docker-server=$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com \
  --docker-username=AWS \
  --docker-password=$(aws ecr get-login-password --region $AWS_REGION)
```

> The token in this secret **expires after 12 hours**. See [Refresh the ECR secret](#93-image-pull-problems) below.

### 5.3 Create the Deployment

Save as **deployment.yaml**:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: webapp
spec:
  replicas: 1
  selector:
    matchLabels:
      app: webapp
  template:
    metadata:
      labels:
        app: webapp
    spec:
      imagePullSecrets:
        - name: ecr-secret
      containers:
        - name: webapp
          image: 963990211788.dkr.ecr.us-east-1.amazonaws.com/simple-python-webapp:v1
          imagePullPolicy: Always
          ports:
            - containerPort: 5000
```

Notes:

- **Always include a tag.** Without one, Kubernetes assumes `:latest`, which probably doesn't exist in your repository and causes `ErrImagePull ... :latest: not found`.
- `imagePullSecrets` tells Kubernetes which credentials to use for ECR.
- `spec.selector` can't be changed after creation. If you later get `field is immutable`, delete the Deployment and apply again.

```bash
kubectl apply -f deployment.yaml
kubectl get deploy,rs,pods
```

Wait until the pod `STATUS` is `Running`.

### Understanding `kubectl get deploy,rs,pods`

| Layer | Name looks like | Role |
|-------|-----------------|------|
| Deployment | `deployment.apps/webapp` | What you manage and update |
| ReplicaSet | `replicaset.apps/webapp-6d8f9c7b5` | One per version of the pod template. Keeps the right number of pods |
| Pod | `pod/webapp-6d8f9c7b5-x7k2p` | The running container, owned by a ReplicaSet |

---

## 6. Access the app with port-forward

```bash
kubectl port-forward deployment/webapp 8080:5000 --address 0.0.0.0
```

- `deployment/webapp` is the target. kubectl picks one pod from it.
- `8080:5000` means `<EC2 port>:<pod port>`. Flask listens on 5000 inside the container.
- `--address 0.0.0.0` makes it reachable from outside the instance. The default is localhost only.
- The command stays in the foreground. Keep the terminal open, or stop it with Ctrl+C.

Open in your browser: `http://<EC2-PUBLIC-IP>:8080`

### Variations

```bash
# Run in the background
nohup kubectl port-forward deployment/webapp 8080:5000 --address 0.0.0.0 > pf.log 2>&1 &

# Forward to one specific pod
kubectl get pods
kubectl port-forward pod/<pod-name> 8080:5000 --address 0.0.0.0

# Use a different EC2 port (open it in the security group too)
kubectl port-forward deployment/webapp 9090:5000 --address 0.0.0.0

# Stop all background port-forwards
pkill -f "port-forward"
```

### How the request flows

```
Browser  →  http://EC2-IP:8080
   ↓
EC2 security group (port 8080 allowed)
   ↓
kubectl port-forward process (listening on 0.0.0.0:8080)
   ↓  tunnel through the Kubernetes API server
kind control-plane container (API server)
   ↓
kubelet → pod network namespace
   ↓
Flask app on port 5000
```

No Service is involved. The tunnel connects directly to one pod, so when that pod is replaced (after an update, restart or crash), the port-forward breaks. Stop it and start it again.

### Quick test from the EC2 instance

```bash
curl http://localhost:8080/health
```

If this works but the browser doesn't, check the security group and the public IP.

---

## 7. Update the app and the image

Saving `app.py` does not change anything that is running. The container uses the image as it was built, so you must rebuild, push and roll out.

**Step 1: Edit `app.py`** (for example set `VERSION = "2.0"` and change `MESSAGE`).

**Step 2: Rebuild with a new tag**

```bash
docker build -t simple-webapp:v2 .
```

Use a new tag for each change (`v2`, `v3`, ...). This shows which version is running and lets you roll back.

**Step 3: Tag and push to ECR**

```bash
docker tag simple-webapp:v2 $ECR_URI:v2
docker push $ECR_URI:v2
```

If it's been over 12 hours, or you get `no basic auth credentials`, repeat the login from section 4. Also confirm `echo $ECR_URI` prints a value.

**Step 4: Roll out with `kubectl set image`**

```bash
kubectl set image deployment/webapp webapp=$ECR_URI:v2
kubectl rollout status deployment/webapp
```

**Step 5: Restart the port-forward** (the old pod was replaced), then refresh the browser. Use a hard refresh (Ctrl+Shift+R) if you still see the old page.

### `kubectl set image` explained

```bash
kubectl set image deployment/<deployment-name> <container-name>=<new-image>
```

- `<container-name>` is the `name:` under `containers:` in the YAML. It is **not** the Deployment or pod name.
- In this guide both are `webapp`: `kubectl set image deployment/webapp webapp=$ECR_URI:v2`
- Find the container name with:

```bash
kubectl get deployment webapp -o jsonpath='{.spec.template.spec.containers[*].name}'
```

If the container name is wrong you get `unable to find container named "..."`.

`set image` changes the live Deployment only. Your `deployment.yaml` still shows the old tag, so update the file too if you treat it as your source of truth. The alternative is to edit the `image:` line and run `kubectl apply -f deployment.yaml`.

Confirm the new version is live:

```bash
kubectl describe deployment webapp | grep Image
curl http://localhost:8080/health      # returns the VERSION from app.py
```

---

## 8. Rollouts, history and rollback

Changing the image creates a new **ReplicaSet**. Kubernetes starts a pod in the new ReplicaSet, waits for it to be ready, then scales the old ReplicaSet down to 0. The old ReplicaSet is kept so you can roll back.

### Generic example (nginx Deployment named `web`)

These are the standard commands, shown on a Deployment named `web` with an `nginx` container:

```bash
kubectl set image deployment/web nginx=nginx:1.27   # change the image
kubectl rollout status deployment/web               # wait for the rollout to finish
kubectl get rs                                      # old RS scales to 0, new RS scales up
kubectl rollout history deployment/web              # list revisions
kubectl rollout undo deployment/web                 # go back to the previous revision
kubectl rollout undo deployment/web --to-revision=1 # go back to a specific revision
```

### The same commands for this app (`webapp`)

```bash
kubectl set image deployment/webapp webapp=$ECR_URI:v2
kubectl rollout status deployment/webapp
kubectl get rs
kubectl rollout history deployment/webapp
kubectl rollout undo deployment/webapp
kubectl rollout undo deployment/webapp --to-revision=1
```

What each one does:

| Command | Purpose |
|---------|---------|
| `kubectl set image ...` | Changes the container image and triggers a rolling update |
| `kubectl rollout status ...` | Blocks until the rollout finishes or fails |
| `kubectl get rs` | Shows ReplicaSets. After an update the old one shows `0` pods, the new one shows the desired count |
| `kubectl rollout history ...` | Lists revision numbers |
| `kubectl rollout history ... --revision=2` | Shows the details (including the image) of one revision |
| `kubectl rollout undo ...` | Returns to the previous revision |
| `kubectl rollout undo ... --to-revision=N` | Returns to revision N |

### Watching a rollout live

```bash
kubectl get deploy,rs,pods -w
```

- **Before:** one ReplicaSet with 1 pod running v1.
- **During:** a new ReplicaSet appears and creates a new pod. The old pod keeps running until the new one is ready.
- **After:** the new ReplicaSet has the pod, and the old ReplicaSet stays at `0`.

Stop watching with Ctrl+C.

### Useful details

- A rollback creates a **new revision number** that matches the old one, so history numbers don't go back down.
- To make history more readable, annotate the reason for a change:

```bash
kubectl annotate deployment/webapp kubernetes.io/change-cause="update to v2"
```

- A stuck rollout (for example a bad image tag) leaves the old pod running and the new pod in `ImagePullBackOff`. The app stays available. Fix it with `kubectl rollout undo deployment/webapp`.
- Pause and resume a rollout with `kubectl rollout pause deployment/webapp` and `kubectl rollout resume deployment/webapp`.
- Restart all pods without changing the image with `kubectl rollout restart deployment/webapp`.

---

## 9. Debugging

Start with these:

```bash
kubectl get deploy,rs,pods
kubectl get pods -o wide
kubectl describe pod <pod-name>        # read the Events section at the bottom
kubectl logs deployment/webapp
kubectl logs <pod-name> --previous     # logs from the last crashed container
```

### 9.1 Port already in use

Error:

```
Unable to listen on port 8000: ... bind: address already in use
```

Another process on the EC2 instance already uses that port. List listening ports and the process behind each:

```bash
sudo ss -tlnp
sudo ss -tlnp | grep :8000        # just one port
sudo lsof -i :8000                # alternative
```

Example output:

```
LISTEN 0 4096 0.0.0.0:8080     users:(("docker-proxy",pid=5267,fd=8))
LISTEN 0 4096 0.0.0.0:8000     users:(("kubectl",pid=8039,fd=8))
LISTEN 0 4096 127.0.0.1:38319  users:(("docker-proxy",pid=1511,fd=8))
```

How to read it:

| Process | Meaning | What to do |
|---------|---------|-----------|
| `kubectl` | An old `kubectl port-forward` is still running | `kill <PID>` or `pkill -f "port-forward"`. It may also be pointing at an old, replaced pod |
| `docker-proxy` on `0.0.0.0:<port>` | A Docker container publishes that port (often an old `docker run -p ...` test) | `docker ps`, then `docker stop <name>` and `docker rm <name>` |
| `docker-proxy` on `127.0.0.1:<random>` | The kind cluster's API server | Leave it alone. `kubectl` needs it |

Important: if port 8080 is held by an old Docker container, browsing to `:8080` shows the **old local container**, not your Kubernetes pod. That is a common reason for seeing an old version after an update. Clean up, then run the port-forward:

```bash
kill <kubectl-pid>
docker ps
docker stop <container-name> && docker rm <container-name>
kubectl port-forward deployment/webapp 8080:5000 --address 0.0.0.0
```

Or pick a different free port (and open it in the security group):

```bash
kubectl port-forward deployment/webapp 8081:5000 --address 0.0.0.0
```

### 9.2 Browser can't connect

| Check | Fix |
|-------|-----|
| Is the port-forward running? | Start it, with `--address 0.0.0.0` |
| Does `curl http://localhost:8080/health` work on the instance? | If yes, the problem is the network path, not the app |
| Security group | Open inbound TCP on the port you used |
| Public IP | The EC2 public IP can change after a stop/start |
| Pod replaced | Restart the port-forward |

### 9.3 Image pull problems

Look at the Events in `kubectl describe pod <pod-name>`.

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ErrImagePull` with `...:latest: not found` | No tag in the YAML, so Kubernetes used `:latest` | Add the real tag, or check tags with `aws ecr list-images` |
| `401`, `no basic auth credentials`, `failed to authorize` | Pull secret missing, expired, or wrong registry/region | Refresh the secret (below), confirm `imagePullSecrets` is in the YAML |
| `repository does not exist` | Wrong repo name or region | Compare with `aws ecr describe-repositories --region $AWS_REGION` |
| `not found` for a specific tag | Tag was never pushed | `aws ecr list-images --repository-name simple-python-webapp --region $AWS_REGION` |
| Secret exists but pull still fails | Secret is in a different namespace | Create it in the same namespace as the Deployment |
| Still `ImagePullBackOff` after fixing | Kubernetes retries with a growing delay | `kubectl delete pod -l app=webapp` to force a fresh attempt |

**Refresh the expired ECR secret:**

```bash
kubectl delete secret ecr-secret
kubectl create secret docker-registry ecr-secret \
  --docker-server=$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com \
  --docker-username=AWS \
  --docker-password=$(aws ecr get-login-password --region $AWS_REGION)
```

### 9.4 Other errors

| Error | Cause | Fix |
|-------|-------|-----|
| `field is immutable` on `kubectl apply` (`spec.selector`) | Existing Deployment has a different selector, which can't be changed | `kubectl delete deployment webapp`, then apply again. Or match the old labels in your YAML |
| `unable to find container named "..."` | Wrong container name in `kubectl set image` | Get it with the `jsonpath` command in section 7 |
| `Unable to locate credentials` (AWS CLI) | IAM role not attached to the instance | Attach the role in the EC2 console |
| `docker push` says `denied` | Missing ECR permissions or not logged in | Check the role policy and repeat the Docker login |
| `no basic auth credentials` on push | Docker login expired | Repeat the Docker login |
| `error: unable to forward port because pod is not running` | Pod isn't `Running` yet | `kubectl get pods`, then `kubectl describe pod` |
| `CrashLoopBackOff` | The app crashes on start | `kubectl logs <pod> --previous` |
| Old version still showing | Port held by an old container, old pod tunnel, or browser cache | See 9.1, restart the port-forward, hard refresh |

### 9.5 Debugging inside a pod

```bash
kubectl exec -it deployment/webapp -- /bin/sh
kubectl exec deployment/webapp -- python -c "print('hello')"
kubectl get events --sort-by=.lastTimestamp
```

---

## 10. Cleanup

```bash
kubectl delete -f deployment.yaml
kubectl delete secret ecr-secret
kind delete cluster --name demo
aws ecr delete-repository --repository-name simple-python-webapp --region $AWS_REGION --force
```

---

## 11. Quick command summary

```bash
# Variables
export AWS_REGION=us-east-1
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export ECR_URI=$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/simple-python-webapp

# Build
docker build -t simple-webapp:v1 .

# ECR: create repo, login, tag, push
aws ecr create-repository --repository-name simple-python-webapp --region $AWS_REGION
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com
docker tag simple-webapp:v1 $ECR_URI:v1
docker push $ECR_URI:v1

# Kubernetes: cluster, secret, deploy, access
kind create cluster --name demo
kubectl create secret docker-registry ecr-secret --docker-server=$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com --docker-username=AWS --docker-password=$(aws ecr get-login-password --region $AWS_REGION)
kubectl apply -f deployment.yaml
kubectl get deploy,rs,pods
kubectl port-forward deployment/webapp 8080:5000 --address 0.0.0.0

# Update
docker build -t simple-webapp:v2 .
docker tag simple-webapp:v2 $ECR_URI:v2
docker push $ECR_URI:v2
kubectl set image deployment/webapp webapp=$ECR_URI:v2
kubectl rollout status deployment/webapp
kubectl get rs
kubectl rollout history deployment/webapp

# Roll back
kubectl rollout undo deployment/webapp
kubectl rollout undo deployment/webapp --to-revision=1

# Debug
sudo ss -tlnp | grep :8080
kubectl describe pod <pod-name>
kubectl logs deployment/webapp
```
