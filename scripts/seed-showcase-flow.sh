#!/usr/bin/env bash
# Seed portfolio + case study on flow via admin API (no DB shell access needed).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${MISE_HOST:-flow}"
BASE="http://127.0.0.1:8400"
GALLERY_ID=1
PHOTO_IDS=(6 7 8 9 10 11)
VIDEO_ID=12
TAGS=(dishes drinks pastry interiors dishes drinks)

run_on_flow() {
  ssh "$HOST" "cd /opt/mise && $*"
}

PASS="$(run_on_flow 'grep -m1 "^MISE_ADMIN_PASSWORD=" .env | cut -d= -f2-')"
COOKIE="$(mktemp)"
trap 'rm -f "$COOKIE"' EXIT

echo "==> admin login"
CODE="$(run_on_flow "curl -sS -c /tmp/mise-seed-cookie -b /tmp/mise-seed-cookie -o /dev/null -w '%{http_code}' -X POST '$BASE/admin/login' -d 'password=${PASS}'")"
if [[ "$CODE" != "303" && "$CODE" != "302" ]]; then
  echo "login failed (HTTP $CODE)" >&2
  exit 1
fi

echo "==> star portfolio photos + video"
for id in "${PHOTO_IDS[@]}" "$VIDEO_ID"; do
  run_on_flow "curl -sS -c /tmp/mise-seed-cookie -b /tmp/mise-seed-cookie -o /dev/null -X POST '$BASE/admin/galleries/$GALLERY_ID/assets/$id/portfolio'"
done

echo "==> set portfolio tags"
for i in "${!PHOTO_IDS[@]}"; do
  id="${PHOTO_IDS[$i]}"
  tag="${TAGS[$i]}"
  run_on_flow "curl -sS -c /tmp/mise-seed-cookie -b /tmp/mise-seed-cookie -o /dev/null -X POST '$BASE/admin/galleries/$GALLERY_ID/assets/$id/tag' -d 'portfolio_tag=$tag'"
done
run_on_flow "curl -sS -c /tmp/mise-seed-cookie -b /tmp/mise-seed-cookie -o /dev/null -X POST '$BASE/admin/galleries/$GALLERY_ID/assets/$VIDEO_ID/tag' -d 'portfolio_tag=motion'"

echo "==> publish case study"
run_on_flow "curl -sS -c /tmp/mise-seed-cookie -b /tmp/mise-seed-cookie -o /dev/null -X POST '$BASE/admin/galleries/$GALLERY_ID/settings" \
  -d "title=Sample+Tasting+Menu" \
  -d "client_name=Mise+Demo" \
  -d "pin=2468" \
  -d "published=on" \
  -d "cs_published=on" \
  -d "cs_tagline=A+tasting+menu%2C+shot+at+its+peak." \
  -d "cs_brief=A+full+menu+refresh+and+brand+library+in+a+single+service+window+%E2%80%94+plating%2C+pours%2C+and+the+dining+room%2C+delivered+as+a+same-week+gallery+with+social+crops+baked+in." \
  -d "cs_credits=Client%3A+Mise+Demo%0AScope%3A+Menu+refresh+%C2%B7+brand+library%0ADeliverables%3A+6+finals+%C2%B7+social+crop+pack%0ATurnaround%3A+Same-week+gallery" \
  -d "cs_location=Asheville%2C+NC"

echo "==> seed testimonials (skip when already published)"
for payload in \
  "quote=Our+reservations+jumped+the+week+the+new+photos+went+live.+Kevin+made+the+food+look+exactly+like+the+room+feels.&attribution_name=Maria+Solis&business=C%C3%BArate&gallery_id=1&position=0&published=on" \
  "quote=Fastest+turnaround+we+have+ever+had%2C+and+the+social+crops+mean+our+marketing+person+stopped+re-cropping+everything+by+hand.&attribution_name=Dev+Carter&business=High+Five+Coffee&position=1&published=on" \
  "quote=He+shot+a+full+menu+refresh+between+lunch+and+dinner+service+without+ever+getting+in+the+way.+Rare.&attribution_name=Jamie+Booth&business=Bull+%26+Beggar&position=2&published=on"
do
  run_on_flow "curl -sS -c /tmp/mise-seed-cookie -b /tmp/mise-seed-cookie -o /dev/null -X POST '$BASE/admin/studio/testimonials' -d '$payload' || true"
done

run_on_flow "rm -f /tmp/mise-seed-cookie"
echo "==> verify public pages"
run_on_flow "curl -sS '$BASE/' | grep -c 'work-grid' || true"
run_on_flow "curl -sS '$BASE/' | grep -c 'motion-sec' || true"
run_on_flow "curl -sS '$BASE/' | grep -c 'testimonials' || true"
run_on_flow "curl -sS '$BASE/portfolio' | grep -c 'portfolio-masonry' || true"
run_on_flow "curl -sS '$BASE/work' | grep -c 'work-feature' || true"
echo "==> done (restart mise.service if motion-sec still 0 — Python must reload)"