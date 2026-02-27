#!/usr/bin/env bash
# Flush all fundamentals scoring data from Redis for v2 and v3 profiles.
# Universe data (shared) is preserved.
#
# Usage:
#   export REDIS_PASSWORD=$(kubectl get secret -n redis redis-secret -o jsonpath='{.data.redis-password}' | base64 -d)
#   bash scripts/flush_scores.sh          # dry-run (default)
#   bash scripts/flush_scores.sh --apply  # actually delete

set -euo pipefail

REDIS_POD="redis-0"
REDIS_NS="redis"
APPLY=false

if [[ "${1:-}" == "--apply" ]]; then
    APPLY=true
fi

if [[ -z "${REDIS_PASSWORD:-}" ]]; then
    echo "ERROR: REDIS_PASSWORD not set. Run:"
    echo '  export REDIS_PASSWORD=$(kubectl get secret -n redis redis-secret -o jsonpath='"'"'{.data.redis-password}'"'"' | base64 -d)'
    exit 1
fi

redis_cmd() {
    kubectl exec -n "$REDIS_NS" "$REDIS_POD" -- redis-cli -a "$REDIS_PASSWORD" --no-auth-warning "$@"
}

NAMESPACES=("fundamentals:v2_base" "fundamentals:v3_composite")

echo "=== Fundamentals Score Flush ==="
echo

total_keys=0

for ns in "${NAMESPACES[@]}"; do
    # Count keys matching this namespace
    keys=$(redis_cmd KEYS "${ns}:*" | grep -c . || true)
    echo "Namespace: ${ns}"
    echo "  Keys found: ${keys}"

    # Show the scores sorted set size
    scores_len=$(redis_cmd ZCARD "${ns}:scores" 2>/dev/null || echo "0")
    echo "  Scores sorted set size: ${scores_len}"

    total_keys=$((total_keys + keys))
done

echo
echo "Total keys to delete: ${total_keys}"
echo

if [[ "$APPLY" == false ]]; then
    echo "[DRY RUN] No keys deleted. Run with --apply to delete."
    exit 0
fi

echo "Deleting keys..."
echo

for ns in "${NAMESPACES[@]}"; do
    # Use SCAN-based deletion to avoid blocking Redis with a large KEYS + DEL
    deleted=$(redis_cmd --eval /dev/stdin "${ns}" <<'LUA'
local pattern = KEYS[1] .. ":*"
local cursor = "0"
local count = 0
repeat
    local result = redis.call("SCAN", cursor, "MATCH", pattern, "COUNT", 200)
    cursor = result[1]
    local keys = result[2]
    if #keys > 0 then
        redis.call("UNLINK", unpack(keys))
        count = count + #keys
    end
until cursor == "0"
return count
LUA
    )
    echo "  ${ns}: deleted ${deleted} keys"
done

echo
echo "Done. The next scorer run will re-fetch all data from Finnhub."
