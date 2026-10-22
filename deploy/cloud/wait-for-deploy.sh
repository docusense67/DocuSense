#!/bin/bash
# Wait until the API answers /api/health on the commit just deployed (deploy.yml).
#
#   bash deploy/cloud/wait-for-deploy.sh API_URL COMMIT_SHA [RENDER_DEPLOY_ID]
#
# How "the new version" is recognised, best first:
#   1. RENDER_API_KEY + RENDER_SERVICE_ID set and a deploy ID given: Render's deploy status
#      must reach `live` (a failed build fails here, with Render's status).
#   2. /api/health includes "commit" (Render's RENDER_GIT_COMMIT): it must equal COMMIT_SHA.
#   3. Neither: a healthy answer is accepted with a warning, because the previous version
#      keeps serving until the new one passes Render's own health check.
# TIMEOUT_SECONDS (default 1500) covers a Docker build on the free plan plus a cold start.
set -euo pipefail

api="${1:?usage: wait-for-deploy.sh API_URL COMMIT_SHA [DEPLOY_ID]}"
api="${api%/}"
sha="${2:?usage: wait-for-deploy.sh API_URL COMMIT_SHA [DEPLOY_ID]}"
deploy_id="${3:-}"
deadline=$(( $(date +%s) + ${TIMEOUT_SECONDS:-1500} ))

out_of_time() {
    [ "$(date +%s)" -ge "$deadline" ]
}

if [ -n "${RENDER_API_KEY:-}" ] && [ -n "${RENDER_SERVICE_ID:-}" ] && [ -n "$deploy_id" ]; then
    while :; do
        status=$(curl -fsS --max-time 30 -H "Authorization: Bearer $RENDER_API_KEY" \
                     -H 'Accept: application/json' \
                     "https://api.render.com/v1/services/$RENDER_SERVICE_ID/deploys/$deploy_id" \
                 | jq -r '.status // "unknown"') || status=unreachable
        echo "Render deploy $deploy_id: $status"
        case "$status" in
            live) break ;;
            build_failed | update_failed | pre_deploy_failed | canceled | deactivated)
                echo "::error::Render deploy $deploy_id ended as $status (see the Render Dashboard logs)"
                exit 1 ;;
        esac
        if out_of_time; then
            echo "::error::Render deploy $deploy_id not live after ${TIMEOUT_SECONDS:-1500}s"
            exit 1
        fi
        sleep 20
    done
fi

while :; do
    # A sleeping free instance takes up to about a minute to wake up.
    body=$(curl -fsS --max-time 90 "$api/api/health" 2>/dev/null) || body=""
    if [ -n "$body" ] && [ "$(jq -r '.status // empty' <<< "$body")" = ok ]; then
        commit=$(jq -r '.commit // empty' <<< "$body")
        if [ "$commit" = "$sha" ]; then
            echo "API healthy on $sha"
            exit 0
        fi
        if [ -z "$commit" ]; then
            if [ -z "${RENDER_API_KEY:-}" ]; then
                echo "::warning::API is healthy, but neither /api/health nor the Render API says" \
                     "which commit is live; the smoke test may be checking the previous version."
            fi
            echo "API healthy: $body"
            exit 0
        fi
        echo "API still on ${commit:0:7}, waiting for ${sha:0:7}"
    else
        echo "API not answering yet"
    fi
    if out_of_time; then
        echo "::error::$api/api/health did not report $sha within ${TIMEOUT_SECONDS:-1500}s"
        exit 1
    fi
    sleep 20
done
