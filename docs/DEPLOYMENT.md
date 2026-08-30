# Deployment

> If Oracle Cloud signup is blocked (a known, common issue — Oracle's fraud-screening rejects a
> lot of legitimate first attempts with a generic error), `docs/DEPLOYMENT_GCP.md` covers the
> same setup on Google Cloud's free `e2-micro` tier as a stopgap. It's a tighter fit (1GB RAM vs.
> up to 24GB here, no India region) — meant to unblock progress while Oracle support gets sorted
> out, not as a permanent replacement for this doc.

## Architecture

Everything runs on a single VM: two Docker containers, one reverse proxy in front.

```
                        ┌─────────────────────── Oracle Cloud VM ───────────────────────┐
                        │                                                                │
  browser ── :80/:443 ──▶  caddy container                                              │
                        │  - serves the built React SPA (static files)                  │
                        │  - reverse-proxies /auth, /admin, /complaints, /notifications, │
                        │    /ask-sarthi, /uploads, /health  ──▶  backend:8000           │
                        │  - auto HTTPS via Let's Encrypt once a domain is set           │
                        │                                                                │
                        │  backend container (FastAPI + torch + sentence-transformers    │
                        │  + ChromaDB + LangGraph) -- not exposed outside the VM          │
                        │  - state (SQLite DB, uploaded photos) on a named Docker volume  │
                        │    so it survives redeploys                                    │
                        └────────────────────────────────────────────────────────────────┘
```

Why one VM instead of splitting the frontend onto Vercel/Netlify: the backend needs a real,
persistent disk (SQLite DB + uploaded photos) and 1-2GB of RAM for torch/sentence-transformers/
ChromaDB — that already rules out most serverless "free FastAPI hosting". Given that, putting the
static frontend on the same box removes an entire second vendor, avoids a cross-origin hop for
every API call, and doesn't cost anything extra on Oracle's Always Free tier.

Why Oracle Cloud specifically: its "Always Free" tier is free forever (not a 12-month trial like
AWS), and includes an Arm-based VM with up to 4 OCPUs / 24GB RAM / 200GB block storage — enough
headroom that this stack won't be fighting the host for memory the way it would on a 512MB-1GB
free-tier box elsewhere.

## What all these files actually are

A plain-language glossary of every deployment-related file in this repo and what it's for.

### CI vs. CD vs. "CI/CD"

- **CI = Continuous Integration.** Every time code is pushed or a PR is opened, a robot
  (GitHub Actions) automatically checks it: does it build, do the tests pass. It does **not**
  deploy anything anywhere — it only tells you "this code is good" or "this code is broken",
  automatically, instead of you having to remember to run tests yourself before every push.
- **CD = Continuous Deployment.** The next step after CI: if the code passed CI, automatically
  ship it to the live server, with no manual "log in and update it" step.
- **"CI/CD"** is just the name for the two put together: test automatically, then deploy
  automatically if the tests passed. That's the whole pipeline.
- Both are implemented as **GitHub Actions workflows** — YAML files under `.github/workflows/`
  that GitHub itself reads and runs on its own servers ("runners") whenever the trigger at the
  top of the file (`on:`) matches, e.g. "someone pushed to `main`".

### `.github/workflows/ci.yml` — the CI file

Runs on every push and every pull request. Two jobs, run in parallel on fresh GitHub-hosted
machines (nothing from your laptop or the VM is involved):

- **backend-tests**: installs Python + this repo's dependencies, runs `pytest tests/`. If any
  test fails, this job fails and shows a red ❌ on the commit/PR in GitHub.
- **frontend-build**: installs Node + npm packages, runs the linter, then does a real
  production build (`tsc -b && vite build`) — this catches TypeScript errors and build breakage,
  not just "does it lint".

If either job fails, GitHub shows it directly on the commit and on any open PR — that's the
"day-by-day, everyone can see it's working" visibility you were after. Nothing about this file
touches the Oracle VM or deploys anything; it only ever runs checks.

### `.github/workflows/cd.yml` — the CD file

Only runs after `ci.yml` has finished **successfully** on `main` (see the `workflow_run` trigger
at the top of the file) — so it can never deploy code that failed its tests. When it runs, it:

1. Builds the backend and frontend Docker images (using the two Dockerfiles below).
2. Pushes those images to **GHCR** (GitHub Container Registry, `ghcr.io`) — GitHub's own free
   Docker image storage, tied to this repo, no separate account needed.
3. Connects to the Oracle VM over SSH and tells it to pull the new images and restart.

This is the file that actually makes a `git push` end up live on the server. It needs the
GitHub secrets listed in step 6 below to know *which* server to SSH into — until those are
added, this workflow will run but fail at the "deploy over SSH" step (harmless; nothing partially
breaks, it just can't reach a server that isn't configured yet).

### `backend/Dockerfile` / `frontend-react/Dockerfile` — build instructions for each half of the app

A Dockerfile is a recipe: "start from this base system, install these things, copy in this
code, run it like this." `docker build` reads one and produces a **Docker image** — a
self-contained snapshot of the app and everything it needs to run, so it behaves identically on
your laptop, the CI runner, and the Oracle VM. Two separate Dockerfiles because the backend
(Python) and frontend (Node → static files served by Caddy) are built completely differently.

### `docker-compose.prod.yml` — how the two images run together

A Docker image on its own doesn't do anything until it's run as a **container**. Compose is the
config file that says "run these containers together, on this network, with these persistent
disks, restart them if they crash." Running `docker compose -f docker-compose.prod.yml up -d`
on the VM is the actual "start/update the live app" command — everything else in `cd.yml`
exists to run that command for you automatically instead of you SSHing in by hand each time.

### `deploy/Caddyfile` — the reverse proxy's config

Caddy is the one piece that's actually exposed to the internet (ports 80/443). Its config file
says "serve the built React files for normal requests, but hand off anything starting with
`/auth`, `/admin`, etc. to the backend container instead" — and, separately, "get an HTTPS
certificate automatically once a real domain is set." Nobody on the internet ever talks to the
backend container directly; everything goes through Caddy first.

### `.dockerignore` — what *not* to copy into the images

Same idea as `.gitignore`, but for `docker build`: keeps things like `node_modules/`,
`.git/`, local `.env` secrets, and the SQLite database out of the built images, so images stay
small and no local files/secrets accidentally leak into a container running on the internet.

### `docs/DEPLOYMENT.md` — this file

The step-by-step instructions for actually setting the VM up (below) and what to do
day-to-day once it's live (rollback, logs, backups).

## Before you deploy anything: commit and push

**A large amount of the current work is uncommitted and/or untracked** (`git status` shows ~30
modified files and ~90 new untracked files, including the entire Ask Sarthi feature —
`backend/routes/ask_sarthi.py`, `backend/services/{rag_retriever,vector_store,...}.py`,
`backend/schemas/`, the RAG data under `data/rag_knowledge_base/`, and matching tests). Neither
CI nor CD in this setup can see any of that until it's committed and pushed — they operate on
what's in the GitHub repo, not your local working tree. In particular `data/rag_knowledge_base/
knowledge_records/` and `data/rag_knowledge_base/sources/` **must** be committed, since
`backend/Dockerfile` rebuilds the RAG index from them at image-build time — the deploy will
build an empty/broken index if those aren't in the repo yet.

## One-time setup

### 1. Create the Oracle Cloud VM

1. Sign up at [cloud.oracle.com](https://cloud.oracle.com) for an Always Free account (a card is
   required for identity verification, but Always Free resources don't bill it).
2. Create a compute instance:
   - Shape: **VM.Standard.A1.Flex** (Ampere/Arm, Always Free eligible) — 2 OCPU / 12GB RAM is
     plenty for this app and leaves half your Always Free allowance spare.
   - Image: Ubuntu 24.04 (or 22.04).
   - Attach a public IP (reserve a static one so it doesn't change on reboot).
   - Add your SSH public key during creation.
3. Open ports in **both** places Oracle requires — this trips up almost everyone new to OCI:
   - **VCN Security List / Network Security Group** (in the OCI console): allow ingress TCP
     22, 80, 443 from `0.0.0.0/0`.
   - **The VM's own firewall** (Ubuntu images ship with iptables rules that block this by
     default even after the console rule is open):
     ```bash
     sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
     sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
     sudo netfilter-persistent save   # or: sudo apt install iptables-persistent
     ```

### 2. Install Docker on the VM

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker   # or log out/in
docker compose version   # confirm the Compose plugin is present
```

### 3. Clone the repo and set up secrets on the VM

```bash
git clone https://github.com/<owner>/<repo>.git
cd <repo>
cp .env.example .env
nano .env   # fill in SARVAM_API_KEY, JWT_SECRET_KEY (generate one: openssl rand -hex 32), etc.
```

Leave `BACKEND_IMAGE` / `FRONTEND_IMAGE` / `SITE_ADDRESS` unset in `.env` for now — the CD
workflow manages the first two, and `SITE_ADDRESS` only matters once you have a domain (step 5).

### 4. First deploy (manual, before CI/CD exists)

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

This builds both images directly on the VM (slower than pulling a prebuilt image, but doesn't
need GHCR credentials for a first smoke test). Verify:

```bash
curl http://localhost/health          # -> {"status":"ok"}
curl http://<VM_PUBLIC_IP>/           # -> the React app's index.html
```

### 5. Point a domain at it (optional, for HTTPS)

If you have a domain, add an A record pointing at the VM's public IP, then on the VM:

```bash
echo "SITE_ADDRESS=your-domain.com" >> .env
docker compose -f docker-compose.prod.yml up -d
```

Caddy automatically requests and renews a Let's Encrypt certificate — no other config needed.
Without a domain, the app is reachable over plain HTTP on the VM's IP, which is fine to develop
against but means traffic (including login tokens) isn't encrypted — don't rely on it for real
user data long-term.

### 6. Configure GitHub Actions secrets

In the GitHub repo: **Settings → Secrets and variables → Actions**, add:

| Secret | Value |
|---|---|
| `SSH_HOST` | The VM's public IP |
| `SSH_USER` | `ubuntu` (or whatever user you SSH in as) |
| `SSH_PRIVATE_KEY` | Private half of a keypair whose **public** half is in the VM's `~/.ssh/authorized_keys` — generate a dedicated deploy key (`ssh-keygen -t ed25519 -f deploy_key`), don't reuse your personal key |
| `SSH_PORT` | `22` (optional — the workflow defaults to 22 if unset) |
| `SSH_DEPLOY_PATH` | Absolute path to the cloned repo on the VM, e.g. `/home/ubuntu/<repo>` |

No secret is needed for pushing images to GHCR — the workflow uses the automatic
`GITHUB_TOKEN`. The VM's `docker login` to GHCR in the deploy step also reuses that same token
value (passed through as a secret), since GHCR requires auth to pull even public-repo packages
via `docker compose pull`. If your repo is private, GHCR packages default to private too; either
make the package public (package Settings on github.com) or make sure the token used has
`read:packages` scope (the default `GITHUB_TOKEN` does).

## How CI/CD works day to day

1. Push / open a PR against `main` → **CI** (`.github/workflows/ci.yml`) runs `pytest` and the
   frontend build+lint. Nothing deploys yet.
2. Merge to `main` → CI runs again on `main`, and once it succeeds, **CD**
   (`.github/workflows/cd.yml`) fires automatically: builds both Docker images, pushes them to
   GHCR tagged `latest` and `<git-sha>`, then SSHes into the VM, `git pull`s the repo (so any
   change to `docker-compose.prod.yml`/`deploy/Caddyfile` themselves also lands), pulls the new
   images, and restarts the containers.
3. Nothing deploys from a branch or PR — only from `main`, and only after tests pass.

## Rollback

Every image is also tagged with its commit SHA. To roll back to a known-good commit on the VM:

```bash
ssh <user>@<vm>
cd <repo>
sed -i 's/^BACKEND_IMAGE=.*/BACKEND_IMAGE=ghcr.io\/<owner>\/<repo>-backend:<good-sha>/' .env
sed -i 's/^FRONTEND_IMAGE=.*/FRONTEND_IMAGE=ghcr.io\/<owner>\/<repo>-frontend:<good-sha>/' .env
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

## Operating notes

- **Logs**: `docker compose -f docker-compose.prod.yml logs -f backend` (or `caddy`).
- **Disk**: the RAG stack's images are large (torch + sentence-transformers + chromadb baked
  in) — expect several GB per image. Give the VM's boot volume at least 50GB (Always Free allows
  up to 200GB total block storage across volumes) and periodically `docker image prune -f`
  (the CD workflow already does this after every deploy).
- **Backups**: the SQLite DB and uploaded photos live in the `backend_state` named Docker
  volume, which Oracle does not back up for you. A simple periodic snapshot:
  ```bash
  docker run --rm -v <repo>_backend_state:/state -v ~/backups:/backup alpine \
    tar czf /backup/state-$(date +%F).tar.gz -C /state .
  ```
  Wire that into a cron job on the VM if this ever holds real user data.
- **Costs**: Always Free compute/storage/networking (up to 10TB egress/month) genuinely doesn't
  bill. The only real cost in this setup is an optional domain name (~$10-15/year) if you want
  HTTPS under your own name instead of the VM's bare IP.
