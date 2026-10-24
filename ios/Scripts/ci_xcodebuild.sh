#!/usr/bin/env bash
set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ -n "${CI_ARTIFACT_DIR:-}" ]]; then
  artifact_dir="$CI_ARTIFACT_DIR"
else
  artifact_dir="$(mktemp -d "${TMPDIR:-/tmp}/ios-ci-artifacts.XXXXXX")" || exit 2
fi
simulator_udid="${IOS_SIMULATOR_UDID:-}"
result_bundle="$artifact_dir/Reflex-tests.xcresult"
overall_status=0

mkdir -p "$artifact_dir" || exit 2
if [[ -e "$result_bundle" ]]; then
  echo "Refusing to overwrite existing Xcode result bundle: $result_bundle" >&2
  echo "Set CI_ARTIFACT_DIR to a new directory or move the existing result bundle first." >&2
  exit 2
fi
echo "Artifacts: $artifact_dir"
if [[ -z "$simulator_udid" ]]; then
  echo "IOS_SIMULATOR_UDID must contain the UDID selected from the installed simulator list." >&2
  exit 2
fi

run_logged() {
  local label="$1"
  shift
  local log_path="$artifact_dir/${label}.log"
  echo "Running: $*"
  "$@" 2>&1 | tee "$log_path"
  local statuses=("${PIPESTATUS[@]}")
  if [[ ${statuses[0]} -ne 0 || ${statuses[1]} -ne 0 ]]; then
    echo "$label failed (command: ${statuses[0]}, log capture: ${statuses[1]})" >&2
    overall_status=1
  fi
}

cd "$repo_root" || exit 2

run_logged tasks-package-tests swift test --package-path ios/Packages/Tasks
run_logged hapticlink-package-tests swift test --package-path ios/Packages/HapticLink

run_logged simulator-app-and-ui-tests xcodebuild \
  -project ios/Reflex.xcodeproj \
  -scheme Reflex \
  -destination "platform=iOS Simulator,id=$simulator_udid" \
  -parallel-testing-enabled NO \
  -resultBundlePath "$result_bundle" \
  CODE_SIGNING_ALLOWED=NO \
  test

run_logged unsigned-release-build xcodebuild \
  -project ios/Reflex.xcodeproj \
  -scheme Reflex \
  -configuration Release \
  -sdk iphoneos \
  -destination 'generic/platform=iOS' \
  CODE_SIGNING_ALLOWED=NO \
  build

exit "$overall_status"
