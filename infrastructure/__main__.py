"""Pulumi program to deploy tokenomics to Kubernetes."""

import os

import pulumi
import pulumi_kubernetes as k8s

config = pulumi.Config("tokenomics")

image = os.environ.get("IMAGE_TAG", config.get("image") or "anicu/tokenomics:latest")
namespace_name = config.get("namespace") or "tokenomics"
alpaca_api_key = config.require_secret("alpaca_api_key")
alpaca_secret_key = config.require_secret("alpaca_secret_key")
alpaca_api_key_v3 = config.require_secret("alpaca_api_key_v3")
alpaca_secret_key_v3 = config.require_secret("alpaca_secret_key_v3")
gemini_api_key = config.require_secret("gemini_api_key")
finnhub_api_key = config.require_secret("finnhub_api_key")
perplexity_api_key = config.require_secret("perplexity_api_key")
marketaux_api_key = config.require_secret("marketaux_api_key")

# Rebalancer settings - score-based portfolio rebalancing
REBALANCER_SETTINGS = """\
providers:
  broker: alpaca-paper

strategy:
  name: "score-rebalancer"
  capital_usd: 100000
  position_size_min_usd: 500
  position_size_max_usd: 5000
  max_open_positions: 100
  target_new_positions_per_month: 100

rebalancing:
  top_n_stocks: 100
  weighting: "score"
  max_position_pct: 5.0
  min_score: 50.0
  rebalance_threshold_pct: 20.0
  min_trade_usd: 100.0

scoring_profiles:
  tokenomics_v2_base:
    scorer_class: "FundamentalsScorer"
    redis_namespace: "fundamentals:v2_base"
    alpaca_api_key_env: "ALPACA_API_KEY"
    alpaca_secret_key_env: "ALPACA_SECRET_KEY"
    description: "Original 3-factor scorer (ROE/Debt/Growth)"

  tokenomics_v3_composite:
    scorer_class: "CompositeScorer"
    redis_namespace: "fundamentals:v3_composite"
    alpaca_api_key_env: "ALPACA_API_KEY_V3"
    alpaca_secret_key_env: "ALPACA_SECRET_KEY_V3"
    description: "Extended composite scorer (placeholder)"

  default_profile: "tokenomics_v2_base"

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

# Get Redis secret from redis namespace and copy to tokenomics namespace
redis_secret_data = k8s.core.v1.Secret.get(
    "redis-secret-ref",
    id="redis/redis-secret",
)

# Copy Redis secret to tokenomics namespace
redis_secret_copy = k8s.core.v1.Secret(
    "redis-secret-copy",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="redis-secret",
        namespace=namespace.metadata.name,
    ),
    data=redis_secret_data.data,
)

# Shared secret (all profiles' Alpaca keys + shared API keys)
secret = k8s.core.v1.Secret(
    "tokenomics-secrets",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="tokenomics-secrets",
        namespace=namespace.metadata.name,
    ),
    string_data={
        "ALPACA_API_KEY": alpaca_api_key,
        "ALPACA_SECRET_KEY": alpaca_secret_key,
        "ALPACA_API_KEY_V3": alpaca_api_key_v3,
        "ALPACA_SECRET_KEY_V3": alpaca_secret_key_v3,
        "GEMINI_API_KEY": gemini_api_key,
        "FINNHUB_API_KEY": finnhub_api_key,
        "PERPLEXITY_API_KEY": perplexity_api_key,
        "MARKETAUX_API_KEY": marketaux_api_key,
    },
)

# Rebalancer ConfigMap
rebalancer_configmap = k8s.core.v1.ConfigMap(
    "rebalancer-config",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="rebalancer-config",
        namespace=namespace.metadata.name,
    ),
    data={"settings.yaml": REBALANCER_SETTINGS},
)

# Common Redis env vars shared by all CronJobs
REDIS_ENV = [
    k8s.core.v1.EnvVarArgs(
        name="REDIS_HOST",
        value="redis.redis.svc.cluster.local",
    ),
    k8s.core.v1.EnvVarArgs(
        name="REDIS_PORT",
        value="6379",
    ),
    k8s.core.v1.EnvVarArgs(
        name="REDIS_PASSWORD",
        value_from=k8s.core.v1.EnvVarSourceArgs(
            secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                name="redis-secret",
                key="redis-password",
            ),
        ),
    ),
]

FINNHUB_ENV = k8s.core.v1.EnvVarArgs(
    name="FINNHUB_API_KEY",
    value_from=k8s.core.v1.EnvVarSourceArgs(
        secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
            name="tokenomics-secrets",
            key="FINNHUB_API_KEY",
        ),
    ),
)

# ---------- Per-profile CronJobs ----------

# Scoring profiles and their schedules
PROFILES = {
    "tokenomics_v2_base": {
        "fundamentals_schedule": "0 2 * * 1",   # Monday 2AM UTC
        "rebalancer_schedule": "0 15 * * 1",     # Monday 3PM UTC
    },
    "tokenomics_v3_composite": {
        "fundamentals_schedule": "0 3 * * 1",   # Monday 3AM UTC (staggered)
        "rebalancer_schedule": "0 16 * * 1",     # Monday 4PM UTC (staggered)
    },
}


def make_fundamentals_cronjob(profile_name: str, schedule: str):
    """Create a fundamentals-refresh CronJob for a specific scoring profile."""
    resource_name = f"fundamentals-refresh-{profile_name.replace('_', '-')}"
    return k8s.batch.v1.CronJob(
        resource_name,
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name=resource_name,
            namespace=namespace.metadata.name,
        ),
        spec=k8s.batch.v1.CronJobSpecArgs(
            schedule=schedule,
            concurrency_policy="Forbid",
            successful_jobs_history_limit=3,
            failed_jobs_history_limit=3,
            job_template=k8s.batch.v1.JobTemplateSpecArgs(
                spec=k8s.batch.v1.JobSpecArgs(
                    ttl_seconds_after_finished=86400,
                    backoff_limit=3,
                    template=k8s.core.v1.PodTemplateSpecArgs(
                        metadata=k8s.meta.v1.ObjectMetaArgs(
                            labels={
                                "app": "tokenomics",
                                "component": "fundamentals-refresh",
                                "profile": profile_name,
                            },
                        ),
                        spec=k8s.core.v1.PodSpecArgs(
                            restart_policy="OnFailure",
                            containers=[
                                k8s.core.v1.ContainerArgs(
                                    name="fundamentals-refresh",
                                    image=image,
                                    command=["python", "-m", "tokenomics.fundamentals.refresh_job"],
                                    env=[
                                        *REDIS_ENV,
                                        FINNHUB_ENV,
                                        k8s.core.v1.EnvVarArgs(
                                            name="SCORING_PROFILE",
                                            value=profile_name,
                                        ),
                                        k8s.core.v1.EnvVarArgs(
                                            name="FUNDAMENTALS_LIMIT",
                                            value="1000",
                                        ),
                                        k8s.core.v1.EnvVarArgs(
                                            name="FUNDAMENTALS_BATCH_SIZE",
                                            value="50",
                                        ),
                                    ],
                                    env_from=[
                                        k8s.core.v1.EnvFromSourceArgs(
                                            secret_ref=k8s.core.v1.SecretEnvSourceArgs(
                                                name=secret.metadata.name,
                                            ),
                                        ),
                                    ],
                                    resources=k8s.core.v1.ResourceRequirementsArgs(
                                        requests={"cpu": "100m", "memory": "256Mi"},
                                        limits={"cpu": "500m", "memory": "512Mi"},
                                    ),
                                ),
                            ],
                        ),
                    ),
                ),
            ),
        ),
    )


def make_rebalancer_cronjob(profile_name: str, schedule: str):
    """Create a rebalancer CronJob for a specific scoring profile."""
    resource_name = f"rebalancer-{profile_name.replace('_', '-')}"
    return k8s.batch.v1.CronJob(
        resource_name,
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name=resource_name,
            namespace=namespace.metadata.name,
        ),
        spec=k8s.batch.v1.CronJobSpecArgs(
            schedule=schedule,
            concurrency_policy="Forbid",
            successful_jobs_history_limit=3,
            failed_jobs_history_limit=3,
            job_template=k8s.batch.v1.JobTemplateSpecArgs(
                spec=k8s.batch.v1.JobSpecArgs(
                    ttl_seconds_after_finished=86400,
                    backoff_limit=2,
                    template=k8s.core.v1.PodTemplateSpecArgs(
                        metadata=k8s.meta.v1.ObjectMetaArgs(
                            labels={
                                "app": "tokenomics",
                                "component": "rebalancer",
                                "profile": profile_name,
                            },
                        ),
                        spec=k8s.core.v1.PodSpecArgs(
                            restart_policy="OnFailure",
                            containers=[
                                k8s.core.v1.ContainerArgs(
                                    name="rebalancer",
                                    image=image,
                                    env=[
                                        *REDIS_ENV,
                                        k8s.core.v1.EnvVarArgs(
                                            name="SCORING_PROFILE",
                                            value=profile_name,
                                        ),
                                    ],
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
                                            name="logs",
                                            mount_path="/app/logs",
                                        ),
                                    ],
                                    resources=k8s.core.v1.ResourceRequirementsArgs(
                                        requests={"cpu": "100m", "memory": "128Mi"},
                                        limits={"cpu": "500m", "memory": "256Mi"},
                                    ),
                                ),
                            ],
                            volumes=[
                                k8s.core.v1.VolumeArgs(
                                    name="config",
                                    config_map=k8s.core.v1.ConfigMapVolumeSourceArgs(
                                        name=rebalancer_configmap.metadata.name,
                                    ),
                                ),
                                k8s.core.v1.VolumeArgs(
                                    name="logs",
                                    empty_dir=k8s.core.v1.EmptyDirVolumeSourceArgs(),
                                ),
                            ],
                        ),
                    ),
                ),
            ),
        ),
    )


# Create per-profile CronJobs
fundamentals_cronjobs = {}
rebalancer_cronjobs = {}

for profile_name, schedules in PROFILES.items():
    fundamentals_cronjobs[profile_name] = make_fundamentals_cronjob(
        profile_name, schedules["fundamentals_schedule"]
    )
    rebalancer_cronjobs[profile_name] = make_rebalancer_cronjob(
        profile_name, schedules["rebalancer_schedule"]
    )

# Universe refresh CronJob - runs monthly to update stock universe by market cap
# This job is SHARED across all profiles (universe is not namespaced)
universe_cronjob = k8s.batch.v1.CronJob(
    "universe-refresh",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="universe-refresh",
        namespace=namespace.metadata.name,
    ),
    spec=k8s.batch.v1.CronJobSpecArgs(
        # Run on the 1st of each month at 1:00 AM UTC
        schedule="0 1 1 * *",
        concurrency_policy="Forbid",
        successful_jobs_history_limit=2,
        failed_jobs_history_limit=2,
        job_template=k8s.batch.v1.JobTemplateSpecArgs(
            spec=k8s.batch.v1.JobSpecArgs(
                # Long TTL - job can take several hours
                ttl_seconds_after_finished=172800,  # Clean up after 48 hours
                backoff_limit=2,
                # No deadline - job may take 5+ hours for 17k symbols
                template=k8s.core.v1.PodTemplateSpecArgs(
                    metadata=k8s.meta.v1.ObjectMetaArgs(
                        labels={"app": "tokenomics", "component": "universe-refresh"},
                    ),
                    spec=k8s.core.v1.PodSpecArgs(
                        restart_policy="OnFailure",
                        containers=[
                            k8s.core.v1.ContainerArgs(
                                name="universe-refresh",
                                image=image,
                                command=["python", "-m", "tokenomics.fundamentals.universe_job"],
                                env=[
                                    *REDIS_ENV,
                                    FINNHUB_ENV,
                                    # Configuration - how many top companies to track
                                    k8s.core.v1.EnvVarArgs(
                                        name="UNIVERSE_SIZE",
                                        value="1500",
                                    ),
                                ],
                                resources=k8s.core.v1.ResourceRequirementsArgs(
                                    # Higher limits - this job runs longer
                                    requests={"cpu": "100m", "memory": "256Mi"},
                                    limits={"cpu": "500m", "memory": "512Mi"},
                                ),
                            ),
                        ],
                    ),
                ),
            ),
        ),
    ),
)

pulumi.export("namespace", namespace.metadata.name)
pulumi.export("universe-cronjob", universe_cronjob.metadata.name)
for profile_name in PROFILES:
    safe_name = profile_name.replace("_", "-")
    pulumi.export(f"fundamentals-cronjob-{safe_name}", fundamentals_cronjobs[profile_name].metadata.name)
    pulumi.export(f"rebalancer-cronjob-{safe_name}", rebalancer_cronjobs[profile_name].metadata.name)
