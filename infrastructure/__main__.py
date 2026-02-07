"""Pulumi program to deploy tokenomics to Kubernetes."""

import json
import os

import pulumi
import pulumi_kubernetes as k8s

config = pulumi.Config("tokenomics")

image = os.environ.get("IMAGE_TAG", config.get("image") or "anicu/tokenomics:latest")
namespace_name = config.get("namespace") or "tokenomics"
alpaca_api_key = config.require_secret("alpaca_api_key")
alpaca_secret_key = config.require_secret("alpaca_secret_key")
gemini_api_key = config.require_secret("gemini_api_key")

# Default profiles — override in Pulumi.<stack>.yaml
DEFAULT_PROFILES = [
    {"news": "alpaca", "llm": "gemini-flash", "broker": "alpaca-paper"},
]

profiles_json = config.get("profiles")
profiles = json.loads(profiles_json) if profiles_json else DEFAULT_PROFILES

# Base settings template (everything except providers block)
BASE_SETTINGS = """\
strategy:
  name: "news-sentiment-satellite"
  capital_usd: 10000
  position_size_min_usd: 500
  position_size_max_usd: 1000
  max_open_positions: 10
  target_new_positions_per_month: 15

sentiment:
  model: "gemini-2.5-flash-lite"
  min_conviction: 70
  temperature: 0.1
  max_output_tokens: 512

risk:
  stop_loss_pct: 0.025
  take_profit_pct: 0.06
  max_hold_trading_days: 65
  daily_loss_limit_pct: 0.05
  monthly_loss_limit_pct: 0.10

news:
  poll_interval_seconds: 30
  symbols: []
  include_content: true
  exclude_contentless: false
  lookback_minutes: 5

trading:
  paper: true
  market_hours_only: true
  order_type: "market"
  time_in_force: "day"

logging:
  level: "INFO"
  trade_log: "logs/trades.log"
  decision_log: "logs/decisions.log"
  app_log: "logs/tokenomics.log"
  max_bytes: 10485760
  backup_count: 5
"""

# Shared namespace
namespace = k8s.core.v1.Namespace(
    "namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(name=namespace_name),
)

# Shared secret (all profiles use the same API keys)
secret = k8s.core.v1.Secret(
    "tokenomics-secrets",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="tokenomics-secrets",
        namespace=namespace.metadata.name,
    ),
    string_data={
        "ALPACA_API_KEY": alpaca_api_key,
        "ALPACA_SECRET_KEY": alpaca_secret_key,
        "GEMINI_API_KEY": gemini_api_key,
    },
)

# Create a deployment for each profile
for profile in profiles:
    news = profile["news"]
    llm = profile["llm"]
    broker = profile["broker"]

    deploy_name = f"tokenomics-{news}-{llm}-{broker}"

    settings_yaml = f"""\
providers:
  news: {news}
  llm: {llm}
  broker: {broker}

{BASE_SETTINGS}"""

    app_labels = {"app": "tokenomics", "profile": deploy_name}

    configmap = k8s.core.v1.ConfigMap(
        f"{deploy_name}-config",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name=f"{deploy_name}-config",
            namespace=namespace.metadata.name,
        ),
        data={"settings.yaml": settings_yaml},
    )

    deployment = k8s.apps.v1.Deployment(
        deploy_name,
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name=deploy_name,
            namespace=namespace.metadata.name,
        ),
        spec=k8s.apps.v1.DeploymentSpecArgs(
            replicas=1,
            selector=k8s.meta.v1.LabelSelectorArgs(match_labels=app_labels),
            template=k8s.core.v1.PodTemplateSpecArgs(
                metadata=k8s.meta.v1.ObjectMetaArgs(labels=app_labels),
                spec=k8s.core.v1.PodSpecArgs(
                    containers=[
                        k8s.core.v1.ContainerArgs(
                            name="tokenomics",
                            image=image,
                            env_from=[
                                k8s.core.v1.EnvFromSourceArgs(
                                    secret_ref=k8s.core.v1.SecretEnvSourceArgs(
                                        name=secret.metadata.name,
                                    ),
                                ),
                            ],
                            volume_mounts=[
                                k8s.core.v1.VolumeMountArgs(
                                    name="config",
                                    mount_path="/app/config",
                                    read_only=True,
                                ),
                                k8s.core.v1.VolumeMountArgs(
                                    name="data",
                                    mount_path="/app/data",
                                ),
                                k8s.core.v1.VolumeMountArgs(
                                    name="logs",
                                    mount_path="/app/logs",
                                ),
                            ],
                            resources=k8s.core.v1.ResourceRequirementsArgs(
                                requests={"cpu": "100m", "memory": "128Mi"},
                                limits={"cpu": "250m", "memory": "256Mi"},
                            ),
                        ),
                    ],
                    volumes=[
                        k8s.core.v1.VolumeArgs(
                            name="config",
                            config_map=k8s.core.v1.ConfigMapVolumeSourceArgs(
                                name=configmap.metadata.name,
                            ),
                        ),
                        k8s.core.v1.VolumeArgs(
                            name="data",
                            empty_dir=k8s.core.v1.EmptyDirVolumeSourceArgs(),
                        ),
                        k8s.core.v1.VolumeArgs(
                            name="logs",
                            empty_dir=k8s.core.v1.EmptyDirVolumeSourceArgs(),
                        ),
                    ],
                ),
            ),
        ),
    )

    pulumi.export(f"{deploy_name}/image", image)

pulumi.export("namespace", namespace.metadata.name)
pulumi.export("profiles", [f"{p['news']}-{p['llm']}-{p['broker']}" for p in profiles])
