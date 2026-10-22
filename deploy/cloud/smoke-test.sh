#!/bin/bash
# Smoke test of the public site through Vercel (browser -> Vercel -> Render), after a deploy.
#
#   bash deploy/cloud/smoke-test.sh PUBLIC_URL [API_URL]
#
# PUBLIC_URL is the Vercel address users open (https://<project>.vercel.app or the school's
# domain). API_URL (default https://adgvs-api.onrender.com) is only used to confirm that the
# interactive API docs are off on the API itself.
set -uo pipefail

site="${1:?usage: smoke-test.sh PUBLIC_URL [API_URL]}"
site="${site%/}"
api="${2:-https://adgvs-api.onrender.com}"
api="${api%/}"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
failures=0

fail() {
    echo "FAIL: $*"
    failures=$((failures + 1))
}
ok() {
    echo "ok:   $*"
}
# fetch URL -> sets $code; headers in $tmp/h, body in $tmp/b
fetch() {
    code=$(curl -sS --max-time 90 -D "$tmp/h" -o "$tmp/b" -w '%{http_code}' "$1") || code=000
}
header() {  # header NAME -> value of the last such response header ('' if absent)
    grep -i "^$1:" "$tmp/h" | tail -n 1 | cut -d: -f2- | sed -e 's/^ *//' -e 's/\r$//'
}
expect_header() {  # expect_header NAME SUBSTRING WHERE
    local value
    value=$(header "$1")
    if [[ "$value" == *"$2"* ]]; then ok "$3: $1 has $2"; else fail "$3: $1 is '${value}'"; fi
}

# 1. API through the Vercel rewrite; retried while a sleeping Render instance wakes up.
for _ in $(seq 1 10); do
    fetch "$site/api/health"
    [ "$code" = 200 ] && break
    sleep 15
done
if [ "$code" = 200 ] && grep -q '"status":"ok"' "$tmp/b"; then
    ok "/api/health through Vercel: $(cat "$tmp/b")"
else
    fail "/api/health through Vercel answered $code"
fi
expect_header Cache-Control no-store "/api/health"
expect_header X-Content-Type-Options nosniff "/api/health"
expect_header Strict-Transport-Security max-age= "/api/health"

# 2. Web app entry page: security headers (deploy/nginx/snippets/security-headers.conf).
fetch "$site/"
if [ "$code" = 200 ]; then ok "/ answered 200"; else fail "/ answered $code"; fi
expect_header Strict-Transport-Security max-age= "/"
expect_header Content-Security-Policy "default-src 'self'" "/"
expect_header Content-Security-Policy "frame-ancestors 'self'" "/"
expect_header Content-Security-Policy "frame-src 'self' blob:" "/"
if [[ "$(header Content-Security-Policy)" == *unsafe-inline* ]]; then
    fail "/: Content-Security-Policy allows 'unsafe-inline'"
fi
expect_header X-Content-Type-Options nosniff "/"
expect_header X-Frame-Options SAMEORIGIN "/"
expect_header Referrer-Policy no-referrer "/"
expect_header Permissions-Policy "camera=()" "/"
expect_header Cache-Control no-cache "/"

# 3. Client-side route: the SPA fallback serves index.html.
fetch "$site/requests/smoke-test"
if [ "$code" = 200 ] && grep -q 'id="root"' "$tmp/b"; then
    ok "SPA route /requests/smoke-test serves index.html"
else
    fail "SPA route /requests/smoke-test answered $code without the app shell"
fi

# 4. Login page: the app's scripts and styles load, and the API says "not signed in" (401),
#    which is what makes the app show the sign-in form.
fetch "$site/"
mapfile -t assets < <(grep -oE '/assets/[A-Za-z0-9._-]+\.(js|css)' "$tmp/b" | sort -u)
[ "${#assets[@]}" -gt 0 ] || fail "index.html references no /assets/ files"
for asset in "${assets[@]}"; do
    fetch "$site$asset"
    if [ "$code" = 200 ]; then ok "$asset loads"; else fail "$asset answered $code"; fi
    expect_header Cache-Control immutable "$asset"
done
fetch "$site/api/auth/me"
if [ "$code" = 401 ]; then
    ok "/api/auth/me without a session: 401 (login page)"
else
    fail "/api/auth/me without a session answered $code (expected 401)"
fi

# 5. Things that must stay closed.
fetch "$site/api/internal/cron/daily"
case "$code" in
    401 | 403 | 404) ok "daily jobs endpoint without the secret: $code" ;;
    *) fail "daily jobs endpoint without the secret answered $code" ;;
esac
fetch "$api/docs"
if [ "$code" = 404 ]; then ok "API docs off ($api/docs: 404)"; else fail "$api/docs answered $code"; fi

if [ "$failures" -gt 0 ]; then
    echo "SMOKE TEST FAILED: $failures check(s)"
    exit 1
fi
echo "SMOKE TEST PASSED: $site"
