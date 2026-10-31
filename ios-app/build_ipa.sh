#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

API_URL="${ORYNTRA_API_URL:-https://oryntraai.com}"

./prepare_ios_project.sh
flutter analyze
flutter test
flutter build ipa --release \
  --dart-define=ORYNTRA_API_URL="$API_URL" \
  --dart-define=ORYNTRA_PREVIEW_MODE=false \

echo "IPA built for API: $API_URL"
