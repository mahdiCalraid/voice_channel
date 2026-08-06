#!/usr/bin/env bash
set -euo pipefail

PUBLIC_URL="${1:-https://vice.peyvastegi.uk}"

STATUS="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' --max-time 15 "$PUBLIC_URL/")"

case "$STATUS" in
  301|302|303|307|308|401|403)
    echo "PASS: $PUBLIC_URL is gated before the Voice Channel application (HTTP $STATUS)."
    ;;
  200)
    echo "FAIL: $PUBLIC_URL is publicly readable without an access challenge."
    exit 1
    ;;
  *)
    echo "FAIL: $PUBLIC_URL returned unexpected HTTP status $STATUS."
    exit 1
    ;;
esac
