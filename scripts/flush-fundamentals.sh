#!/usr/bin/env bash
# Flush all fundamentals data from Redis.
# Usage: ./scripts/flush-fundamentals.sh [--namespace <v2_base|v3_composite|all>]
#
# Requires: kubectl access to the redis pod in the redis namespace.

set -euo pipefail

NAMESPACE="${1:---namespace}"
NS_VALUE="${2:-all}"

# Parse args
if [[ "$NAMESPACE" == "--namespace" ]]; then
    NS_VALUE="${2:-all}"
elif [[ "$NAMESPACE" != "--namespace" ]]; then
    NS_VALUE="$NAMESPACE"
fi

REDIS_PW=$(kubectl get secret -n redis redis-secret -o jsonpath='{.data.redis-password}' | base64 -d)
REDIS_CMD="kubectl exec -n redis redis-0 -- redis-cli -a $REDIS_PW --no-auth-warning"

echo "=== Redis Fundamentals Flush ==="
echo ""

# Show what exists before deleting
echo "Current key counts:"
for prefix in "fundamentals:v2_base" "fundamentals:v3_composite" "fundamentals:universe" "fundamentals:scores" "fundamentals:"; do
    count=$($REDIS_CMD KEYS "${prefix}*" 2>/dev/null | wc -l | tr -d ' ')
    echo "  ${prefix}*  →  $count keys"
done
echo ""

# Build list of patterns to delete
PATTERNS=()
case "$NS_VALUE" in
    v2_base|v2)
        PATTERNS=("fundamentals:v2_base:*")
        echo "Target: v2_base namespace only"
        ;;
    v3_composite|v3)
        PATTERNS=("fundamentals:v3_composite:*")
        echo "Target: v3_composite namespace only"
        ;;
    all)
        PATTERNS=("fundamentals:*")
        echo "Target: ALL fundamentals data (v2, v3, universe, scores)"
        ;;
    *)
        echo "ERROR: unknown namespace '$NS_VALUE'"
        echo "Usage: $0 [--namespace <v2|v3|all>]"
        exit 1
        ;;
esac

echo ""
read -p "Proceed? (y/N) " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    echo "Aborted."
    exit 0
fi

echo ""
for pattern in "${PATTERNS[@]}"; do
    echo "Deleting: $pattern"
    # SCAN + DEL in batches to avoid blocking Redis
    deleted=0
    cursor=0
    while true; do
        result=$($REDIS_CMD SCAN "$cursor" MATCH "$pattern" COUNT 500 2>/dev/null)
        cursor=$(echo "$result" | head -1)
        keys=$(echo "$result" | tail -n +2)

        if [[ -n "$keys" ]]; then
            count=$(echo "$keys" | wc -l | tr -d ' ')
            echo "$keys" | xargs $REDIS_CMD DEL >/dev/null 2>&1
            deleted=$((deleted + count))
        fi

        if [[ "$cursor" == "0" ]]; then
            break
        fi
    done
    echo "  Deleted $deleted keys"
done

echo ""
echo "Remaining key counts:"
for prefix in "fundamentals:v2_base" "fundamentals:v3_composite" "fundamentals:universe" "fundamentals:scores" "fundamentals:"; do
    count=$($REDIS_CMD KEYS "${prefix}*" 2>/dev/null | wc -l | tr -d ' ')
    echo "  ${prefix}*  →  $count keys"
done

echo ""
echo "Done."
