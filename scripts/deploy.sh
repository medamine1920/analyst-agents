#!/usr/bin/env bash
# Deploy the latest main on the server: pull, rebuild, restart, wait for /health.
# Run by CI over SSH (as a forced command), or by hand on the server.
set -euo pipefail

# Everything lives in main() so bash reads the whole script before running it.
# That matters because `git reset` below may rewrite this very file mid-run.
main() {
    cd "$HOME/apps/analyst-agents"

    echo "==> Fetching latest main"
    git fetch --quiet origin main
    git reset --hard origin/main
    echo "    now at $(git log -1 --format='%h %s')"

    echo "==> Building and restarting the container"
    docker compose up -d --build

    echo "==> Waiting for /health"
    for _ in $(seq 1 36); do
        if curl -fsS localhost:8100/health; then
            echo
            docker image prune -f > /dev/null
            echo "==> Deploy OK"
            return 0
        fi
        sleep 5
    done

    echo "==> Container did not become healthy. Recent logs:" >&2
    docker compose logs --tail 50 >&2
    return 1
}

main "$@"