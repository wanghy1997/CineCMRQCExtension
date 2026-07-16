#!/bin/zsh

set -eu

SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h}"
SLICER_APP="${SLICER_APP:-/Applications/Slicer.app}"
SLICER_EXECUTABLE="${SLICER_APP}/Contents/MacOS/Slicer"

if [[ ! -x "${SLICER_EXECUTABLE}" ]]; then
  print -u2 "3D Slicer executable was not found: ${SLICER_EXECUTABLE}"
  exit 1
fi

if [[ $# -ge 1 && -n "$1" ]]; then
  export CINE_PATIENT_PATH="$1"
fi
if [[ $# -ge 2 && -n "$2" ]]; then
  export CINE_SERIES_ID="$2"
fi

exec "${SLICER_EXECUTABLE}" \
  --additional-module-paths "${PROJECT_ROOT}/CineCMRQC" \
  --python-script "${SCRIPT_DIR}/LaunchCineCMRQC.py"

