# Hosting the GTS pipeline outside Streamlit Community Cloud

The app now ships as a Docker container. The same image runs on a server
of your own, on Render, or on a laptop with Docker Desktop.

## Which host

| | Own server (**recommended**) | Render | Streamlit Community Cloud |
|---|---|---|---|
| Memory | whatever the machine has (Oracle free: 12 GB) | 2 GB on `standard` (512 MB free is too small) | ~1 GB |
| Upload size | 2 GB (set by `UPLOAD_MB`) | 400 MB | 400 MB |
| Long runs | no time limit | fine | sleeps and restarts |
| Login | username + password (Caddy) | **none**: anyone with the URL can open the app | Streamlit sharing settings |
| Cost | free (Oracle Always Free) or an existing eHA server | paid plan | free |
| Upkeep | you run `update.sh` | automatic from GitHub | automatic |

Hosts that were ruled out, and why:

- **Vercel / Netlify** run short serverless functions with a 4.5 MB request
  cap. Streamlit needs a long-running server.
- **Google Cloud Run** caps uploads at 32 MB per file, and it cuts off an open
  WebSocket at the 60-minute request timeout. Streamlit keeps one open for the
  whole session.
- **Hugging Face Spaces**: new free Docker Spaces reportedly need a PRO
  subscription since mid-2026.

---

## Option A: your own server (Oracle Cloud Always Free, or any Ubuntu VM)

### 1. Create the VM (Oracle)

1. Sign up at <https://signup.cloud.oracle.com> (a card is needed for
   verification; Always Free resources are not charged).
2. **Compute → Instances → Create instance**
   - Image: **Canonical Ubuntu 24.04**
   - Shape: **Ampere → VM.Standard.A1.Flex**, **2 OCPU / 12 GB** (the
     Always Free allowance since June 2026)
   - Networking: keep "assign a public IPv4 address"
   - SSH keys: download the private key, and keep it safe
   - Boot volume: 50 GB is plenty
3. Note the instance's **public IP**.

An eHA server or any other Ubuntu 22.04/24.04 machine with 4 GB+ RAM works
the same way; skip to step 3.

### 2. Open ports 80 and 443 (Oracle console)

**Networking → Virtual cloud networks → (your VCN) → Security Lists →
Default Security List → Add Ingress Rules**, twice:

| Source CIDR | IP protocol | Destination port |
|---|---|---|
| `0.0.0.0/0` | TCP | `80` |
| `0.0.0.0/0` | TCP | `443` |

The setup script opens the same ports in the VM's own firewall.

### 3. (Optional, recommended) point a domain at it

Create a DNS **A record**, e.g. `gts.ehealthnigeria.org → <public IP>`. With a
domain, Caddy gets an HTTPS certificate automatically. Without one the app runs
on plain `http://<IP>`. That is fine for a first test, but don't upload real
campaign data over plain HTTP.

### 4. Install

From Windows (PowerShell), connect to the VM:

```powershell
ssh -i C:\path\to\ssh-key.key ubuntu@<public IP>
```

Then on the VM:

```bash
curl -fsSL https://raw.githubusercontent.com/najibadam-commits/gts-vaccination-tracking/deploy/docker/deploy/setup-ubuntu.sh -o setup.sh
bash setup.sh
```

It asks for the domain (blank if none), a username and a password, then
builds and starts everything. The first build takes a few minutes. At the end
it prints the URL.

### 5. Private templates

Files that must not be in the public repo, like the Post Implementation Report
deck, go in `/opt/gts/deploy/private/` on the server. The app finds them there
without any code change. From Windows:

```powershell
scp -i C:\path\to\ssh-key.key "C:\my work space\GTS_Automation\Post Implementation Report 2026.pptx" ubuntu@<public IP>:/tmp/
ssh -i C:\path\to\ssh-key.key ubuntu@<public IP> "sudo mv '/tmp/Post Implementation Report 2026.pptx' /opt/gts/deploy/private/ && cd /opt/gts && sudo docker compose restart app"
```

### Day to day

| Task | Command (on the VM) |
|---|---|
| Deploy the latest code | `bash /opt/gts/deploy/update.sh` |
| See logs | `cd /opt/gts && sudo docker compose logs -f app` |
| Restart | `cd /opt/gts && sudo docker compose restart` |
| Change password | edit `AUTH_HASH` in `/opt/gts/.env` (see `deploy/env.example`), then `sudo docker compose up -d` |
| Status | `cd /opt/gts && sudo docker compose ps` |

Containers restart on their own after a crash or a reboot.

---

## Option B: Render (managed, paid)

1. Render dashboard → **New → Blueprint** → connect GitHub → pick
   `gts-vaccination-tracking`. It reads `render.yaml` (Docker, `standard`
   plan, Frankfurt, health check on `/_stcore/health`).
2. Apply. Every push to the `deploy/docker` branch redeploys.
3. The app is public to anyone with the URL: Render has no password gate. Private
   templates are uploaded through the app's own uploaders on each run.

---

## Option C: try it on your PC

With Docker Desktop installed, in the repo folder:

```powershell
docker build -t gts-pipeline .
docker run --rm -p 8501:8501 gts-pipeline
```

Open <http://localhost:8501>.

---

## What was verified

- The image layout (code in `/srv/gts/app`, working directory
  `/srv/gts/private`, config in the user profile) was reproduced on Python 3.11.
  All 15 `test_*.py` scripts pass, including the slide map against the
  bundled, slimmed template.
- Behind Caddy: requests without the password get `401`, the Streamlit
  WebSocket connects, and a 60 MB track file uploads successfully (Cloud Run
  would reject it).
- The private PIR deck is picked up from the mounted folder.
- `.github/workflows/ci.yml` builds the real image on every push, runs the
  tests inside it and checks it boots. Check the **Actions** tab after pushing.
