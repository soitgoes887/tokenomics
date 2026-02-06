  Dockerfile — multi-stage build, non-root user, copies only what's needed to run

  .github/workflows/ci.yaml — two-job pipeline:
  1. test — runs pytest on every push/PR
  2. build-and-push — builds the Docker image and pushes anicu/tokenomics:latest +
  anicu/tokenomics:<sha> to Docker Hub (only on main push, after tests pass)

  infrastructure/ — Pulumi Python project creating:
  - tokenomics namespace
  - K8s Secret with the 3 API keys (set via pulumi config set --secret)
  - ConfigMap with settings.yaml
  - Deployment (1 replica, resource limits, config/data/logs volumes)

  To set up GitHub Actions, add two secrets to your repo at Settings > Secrets > Actions:
  - DOCKERHUB_USERNAME = anicu
  - DOCKERHUB_TOKEN = your Docker Hub access token

  To deploy with Pulumi:
  cd infrastructure
  python -m venv venv && source venv/bin/activate
  pip install -r requirements.txt
  pulumi stack init dev
  pulumi config set --secret alpaca_api_key <key>
  pulumi config set --secret alpaca_secret_key <key>
  pulumi config set --secret gemini_api_key <key>
  pulumi up
