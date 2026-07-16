#!/bin/zsh

set -eu

SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h}"
SLICER_APP="${SLICER_APP:-/Applications/Slicer.app}"
SLICER_EXECUTABLE="${SLICER_APP}/Contents/MacOS/Slicer"
PATIENT_PATH="${CINE_PATIENT_PATH:-}"
REPORT_DIR="${PROJECT_ROOT}/Testing/Reports"
REPORT_PATH="${REPORT_DIR}/cine-cmr-qc-$(date +%Y%m%d-%H%M%S).log"

# GUI tests inspect real patient data but must not create audit files there.
export CINE_CMR_QC_DISABLE_AUDIT_WRITE=1

mkdir -p "${REPORT_DIR}"
exec > >(tee "${REPORT_PATH}") 2>&1

print "Cine CMR QC test run"
print "Started: $(date '+%Y-%m-%dT%H:%M:%S%z')"
print "Project: ${PROJECT_ROOT}"
print "Slicer: ${SLICER_EXECUTABLE}"

if [[ ! -x "${SLICER_EXECUTABLE}" ]]; then
  print -u2 "3D Slicer executable was not found."
  exit 1
fi

PYTHON_FILES=(
  "${PROJECT_ROOT}/CineCMRQC/CineCMRQC.py"
  "${PROJECT_ROOT}/CineCMRQC/Testing/Python/CineCMRQCRealDataSmokeTest.py"
  "${PROJECT_ROOT}/CineCMRQC/Testing/Python/CineCMRQCPatientBatchSmokeTest.py"
  "${PROJECT_ROOT}/CineCMRQC/Testing/Python/CineCMRQCOverwriteSaveSmokeTest.py"
  "${PROJECT_ROOT}/CineCMRQC/Testing/Python/CineCMRQCCardiacPhaseGuiSmokeTest.py"
  "${PROJECT_ROOT}/CineCMRQC/Testing/Python/CineCMRQCGuiViewSmokeTest.py"
  "${PROJECT_ROOT}/scripts/LaunchCineCMRQC.py"
)

if command -v pyenv >/dev/null 2>&1 && pyenv versions --bare | grep -qx "3.8.18"; then
  print "[1/8] Python 3.8.18 syntax compatibility"
  PYENV_VERSION=3.8.18 pyenv exec python -m py_compile "${PYTHON_FILES[@]}"
else
  print -u2 "[1/8] SKIPPED: Python 3.8.18 is not installed in pyenv."
fi

if command -v pyenv >/dev/null 2>&1 && pyenv versions --bare | grep -qx "3.12.2"; then
  print "[2/8] Python 3.12.2 syntax compatibility"
  PYENV_VERSION=3.12.2 pyenv exec python -m py_compile "${PYTHON_FILES[@]}"
else
  print -u2 "[2/8] SKIPPED: Python 3.12.2 is not installed in pyenv."
fi

print "[3/8] Slicer module unit and scene round-trip tests"
"${SLICER_EXECUTABLE}" \
  --no-main-window \
  --additional-module-paths "${PROJECT_ROOT}/CineCMRQC" \
  --python-code \
  "import CineCMRQC; test=CineCMRQC.CineCMRQCTest(); test.runTest(); print('FULL MODULE TEST PASSED'); slicer.app.exit(0)"

print "[4/8] Synthetic GUI cardiac phases and mandatory-save switching"
"${SLICER_EXECUTABLE}" \
  --additional-module-paths "${PROJECT_ROOT}/CineCMRQC" \
  --python-script "${PROJECT_ROOT}/CineCMRQC/Testing/Python/CineCMRQCCardiacPhaseGuiSmokeTest.py"

if [[ -z "${PATIENT_PATH}" || ! -d "${PATIENT_PATH}" ]]; then
  print -u2 "[5/8] SKIPPED: set CINE_PATIENT_PATH to an available test-patient folder."
  print -u2 "[6/8] SKIPPED: patient batch test requires the real patient folder."
  print -u2 "[7/8] SKIPPED: overwrite-save test requires the real patient folder."
  print -u2 "[8/8] SKIPPED: GUI real-data test requires the real patient folder."
  print "Core tests passed; real-data acceptance was not executed."
  print "Report: ${REPORT_PATH}"
  exit 0
fi

IMAGE_PATH="${PATIENT_PATH}/img/series0015-Body.nii.gz"
MASK_PATH="${PATIENT_PATH}/segmentation/series0015-Body/sequence"

print "[5/8] Real series orientation, ED/ES, edit isolation, labels, and export"
CINE_CMR_QC_IMAGE_PATH="${IMAGE_PATH}" \
CINE_CMR_QC_MASK_PATH="${MASK_PATH}" \
CINE_CMR_QC_EXPECTED_FRAMES=25 \
CINE_CMR_QC_FLIP_LR=auto \
"${SLICER_EXECUTABLE}" \
  --no-main-window \
  --additional-module-paths "${PROJECT_ROOT}/CineCMRQC" \
  --python-script "${PROJECT_ROOT}/CineCMRQC/Testing/Python/CineCMRQCRealDataSmokeTest.py"

print "[6/8] Real patient 14-series / 350-frame batch export"
CINE_PATIENT_PATH="${PATIENT_PATH}" \
CINE_EXPECTED_READY_SERIES=14 \
"${SLICER_EXECUTABLE}" \
  --no-main-window \
  --additional-module-paths "${PROJECT_ROOT}/CineCMRQC" \
  --python-script "${PROJECT_ROOT}/CineCMRQC/Testing/Python/CineCMRQCPatientBatchSmokeTest.py"

print "[7/8] In-place overwrite, ED/ES metadata, backup, exact filenames, and 4D summary"
CINE_CMR_QC_IMAGE_PATH="${IMAGE_PATH}" \
CINE_CMR_QC_MASK_PATH="${MASK_PATH}" \
CINE_CMR_QC_FLIP_LR=auto \
"${SLICER_EXECUTABLE}" \
  --no-main-window \
  --additional-module-paths "${PROJECT_ROOT}/CineCMRQC" \
  --python-script "${PROJECT_ROOT}/CineCMRQC/Testing/Python/CineCMRQCOverwriteSaveSmokeTest.py"

print "[8/8] Real GUI geometry, cardiac phases, low-confidence fallback, and save guard"
CINE_PATIENT_PATH="${PATIENT_PATH}" \
CINE_SERIES_ID="series0015-Body" \
"${SLICER_EXECUTABLE}" \
  --additional-module-paths "${PROJECT_ROOT}/CineCMRQC" \
  --python-script "${PROJECT_ROOT}/CineCMRQC/Testing/Python/CineCMRQCGuiViewSmokeTest.py"

CINE_PATIENT_PATH="${PATIENT_PATH}" \
CINE_SERIES_ID="series0007-Body" \
CINE_EXPECTED_PHASE_CONFIDENCE="low" \
"${SLICER_EXECUTABLE}" \
  --additional-module-paths "${PROJECT_ROOT}/CineCMRQC" \
  --python-script "${PROJECT_ROOT}/CineCMRQC/Testing/Python/CineCMRQCGuiViewSmokeTest.py"

print "ALL AUTOMATED TESTS PASSED"
print "Completed: $(date '+%Y-%m-%dT%H:%M:%S%z')"
print "Report: ${REPORT_PATH}"
