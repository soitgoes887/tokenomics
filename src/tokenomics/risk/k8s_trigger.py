"""Spawn an emergency rebalancer Job via the Kubernetes in-cluster API.

The Job spec is cloned directly from the profile's existing rebalancer
CronJob template, so it automatically inherits the correct image, secrets,
config mounts, resource limits, and env vars.

Required RBAC in the tokenomics namespace (see infrastructure/__main__.py):
  - batch/v1 CronJobs: get
  - batch/v1 Jobs:     create

Usage (called from refresh_job.py when VixGuard fires):
    from tokenomics.risk.k8s_trigger import trigger_emergency_rebalance
    job_name = trigger_emergency_rebalance("tokenomics_v4_regime", reason)
"""

import time

import structlog

logger = structlog.get_logger(__name__)


def trigger_emergency_rebalance(
    profile_name: str,
    reason: str,
    namespace: str = "tokenomics",
) -> str:
    """Create a one-off emergency rebalancer Job from the profile's CronJob.

    Args:
        profile_name: Scoring profile (e.g. "tokenomics_v4_regime")
        reason:       Human-readable trigger reason (stored as Job annotation)
        namespace:    Kubernetes namespace (default "tokenomics")

    Returns:
        Name of the created Job

    Raises:
        ImportError:   If the `kubernetes` package is not installed
        RuntimeError:  If the Job cannot be created (API error)
    """
    try:
        from kubernetes import client, config as k8s_config
    except ImportError as exc:
        raise ImportError(
            "kubernetes package not installed — add 'kubernetes>=28.0' to requirements.txt"
        ) from exc

    k8s_config.load_incluster_config()
    batch = client.BatchV1Api()

    cronjob_name = f"rebalancer-{profile_name.replace('_', '-')}"
    job_name = f"rebalancer-emergency-{int(time.time())}"

    logger.info(
        "k8s_trigger.fetching_cronjob",
        cronjob=cronjob_name,
        namespace=namespace,
    )

    try:
        cronjob = batch.read_namespaced_cron_job(name=cronjob_name, namespace=namespace)
    except Exception as exc:
        raise RuntimeError(
            f"Cannot read CronJob '{cronjob_name}' in namespace '{namespace}': {exc}"
        ) from exc

    job = client.V1Job(
        metadata=client.V1ObjectMeta(
            name=job_name,
            namespace=namespace,
            labels={
                "app": "tokenomics",
                "component": "rebalancer",
                "trigger": "emergency",
                "profile": profile_name,
            },
            annotations={"vix-trigger-reason": reason},
        ),
        spec=cronjob.spec.job_template.spec,
    )

    # Tag the container so kubectl logs / Grafana can identify emergency runs
    for container in job.spec.template.spec.containers:
        if container.env is None:
            container.env = []
        container.env.append(
            client.V1EnvVar(name="EMERGENCY_REBALANCE", value="true")
        )

    try:
        created = batch.create_namespaced_job(namespace=namespace, body=job)
    except Exception as exc:
        raise RuntimeError(f"Failed to create emergency Job '{job_name}': {exc}") from exc

    created_name = created.metadata.name
    logger.info(
        "k8s_trigger.job_created",
        job=created_name,
        profile=profile_name,
        reason=reason,
    )
    return created_name
