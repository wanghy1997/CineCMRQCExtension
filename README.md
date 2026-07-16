# CineCMR Segmentation QC Slicer Extension

## Portable local release

Build the local-install ZIP with:

```bash
./scripts/package-cine-cmr-qc.command
```

The generated `dist/CineCMRQCExtension-0.2.1.zip` follows the same local
Extension Wizard installation pattern documented by MedSAMSlicer. End-user
instructions are in `docs/PORTABLE_INSTALL_zh-CN.md` and are included in the
ZIP as `docs/INSTALL_zh-CN.md`.

This repository contains a 3D Slicer scripted module for cine CMR
segmentation quality control.

The module now defaults to a Chinese simplified workflow. Selecting a patient
folder automatically pairs each canonical series image
`img/<series>.nii.gz` with `segmentation/<series>/sequence`, and changing the
series dropdown automatically loads both. Generic manual import and QC
administration controls remain available by disabling simplified mode.

The MVP focuses on the workflow that Slicer and MedSAMSlicer do not provide
as a single turnkey tool for cine CMR correction:

- load or select a cine image `Volume Sequence`;
- load predicted label masks as a frame-aligned `Segmentation Sequence`;
- bind image and segmentation into the same `Sequence Browser`;
- edit masks frame by frame using an embedded Slicer `Segment Editor`;
- use Slicer's native playback and frame-seek controls inside the module;
- switch single-slice cine series to a one-up view aligned with the native
  oblique acquisition plane and fit the complete image;
- validate frame counts, time indices, spatial geometry, and frame-specific saving;
- track reviewed/unreviewed state for each frame;
- create a built-in synthetic beating-heart cine for immediate GUI testing;
- scan the project patient-folder convention and include only series selected
  by doctors under `frames/<series>`;
- pair each selected series with `img/<series>.nii.gz` and
  `segmentation/<series>/sequence`, while preserving DICOM `TriggerTime` when
  available;
- map the confirmed Huaxi labels `180:心腔 (Cavity)` and
  `255:心肌 (Myocardium)`;
- navigate patient series, restore already-loaded series from a saved scene,
  and synchronize loaded series to the nearest DICOM `TriggerTime`;
- mark the current frame reviewed and advance in one action, with patient-level
  reviewed-frame and completed-series progress;
- store per-frame reviewer, UTC review timestamp, and optional review comment,
  with persistence through Slicer scene save/reopen;
- distinguish untouched AI masks from manually corrected frames using a
  baseline digest of the merged integer labelmap, independently of review state;
- export corrected masks while preserving image geometry.
- detect legacy mask NIfTI files that are left-right mirrored relative to the
  source MedSAM2 PNG, apply one audited correction on import, and leave newly
  generated aligned masks unchanged;
- preserve original integer `LabelValue` values such as `180/255` in per-frame
  and 4D exports instead of re-encoding segments as `1/2`.
- export all doctor-selected ready patient series into separate folders and
  combine their frame records into `patient_manifest.csv`.

Detailed Chinese setup and data-format instructions are available in
[`docs/USER_GUIDE_zh-CN.md`](docs/USER_GUIDE_zh-CN.md).
The real-data handoff checklist is in
[`docs/ACCEPTANCE_CHECKLIST_zh-CN.md`](docs/ACCEPTANCE_CHECKLIST_zh-CN.md).

## Design choices

This module reuses native Slicer capabilities instead of reimplementing them:

- `Sequences` / `Sequence Browser` for playback and synchronized frame
  switching;
- `Segmentation Sequence` for frame-specific masks;
- `Segment Editor` for manual correction;
- `Segmentations` logic for labelmap/segmentation conversion and export.

MedSAMSlicer is used as a reference for Slicer module organization and the
idea of keeping segmentation editing inside the module UI. This project does
not run MedSAM2 inference in the first version; it consumes masks already
created by MedSAM2 or another server-side pipeline.

## Python / Slicer version

Validated locally with:

- 3D Slicer 5.8.1
- Slicer's bundled Python 3.9

The module avoids Python 3.10-only syntax and is intended to remain compatible
with Python 3.8/3.9 style code.

## Install for local testing

1. Open 3D Slicer.
2. Go to `Edit -> Application Settings -> Modules`.
3. Add this folder to `Additional module paths`:

   `<解压或克隆目录>/CineCMRQCExtension/CineCMRQC`

4. Restart Slicer.
5. Open `Modules -> Cardiac -> Cine CMR QC`.

## One-command local launch

Start the module without changing Slicer's permanent settings:

```bash
./scripts/launch-cine-cmr-qc.command
```

Open a patient and optional series directly:

```bash
./scripts/launch-cine-cmr-qc.command /path/to/patient series0015-Body
```

The launcher uses `/Applications/Slicer.app` by default. Set `SLICER_APP` to
another `.app` path when needed.

## Automated validation

Run the complete compatibility, Slicer, real-series, patient-batch, and GUI
suite with:

```bash
./scripts/run-cine-cmr-qc-tests.command
```

To include the optional real-data tests without storing a patient path in the
repository:

```bash
CINE_PATIENT_PATH=/path/to/test-patient ./scripts/run-cine-cmr-qc-tests.command
```

Timestamped logs are written under `Testing/Reports/`. If the configured real
patient folder is unavailable, the runner reports the three real-data tests as
skipped and only claims that the core tests passed.

## Input formats supported in the MVP

Image input:

- an already-loaded Slicer `vtkMRMLSequenceNode`, usually loaded from DICOM as
  a Volume Sequence;
- a folder containing one image frame per file, sorted naturally by filename;
- a 3D or 4D image file readable by SimpleITK.

Mask input:

- a folder containing one labelmap mask per frame, sorted naturally by filename;
- a 3D or 4D mask file readable by SimpleITK.

Default label convention:

```text
1:LV,2:MYO,3:RV
```

You can change this in the module UI.

## Recommended first GUI test

1. For a no-data smoke test, click `Load Synthetic Cine Demo`; otherwise
   load a cine MRI as a Slicer `Volume Sequence`.
2. Open `Modules -> Cardiac -> Cine CMR QC`.
3. In `Existing image sequence`, choose the loaded image sequence.
4. Click `Use Selected Image Sequence`.
5. If you already have predicted masks, choose the mask path and click
   `Load Mask Sequence And Bind`.
6. If you want to test manual annotation without predicted masks, click
   `Create Empty Editable Mask Sequence`.
7. Use the `Previous` / `Next` buttons or the Slicer Sequence Browser toolbar
   to move through frames.
8. Edit masks in the embedded `Segment Editor` panel.
9. Click `Validate Binding`. Expected result:

   `绑定正常：MRI N 帧，Mask N 帧，已启用逐帧修改保存。`

10. Review the automatically estimated end-diastolic/end-systolic frames, adjust
    them when needed, and confirm both phases.
11. The output folder defaults to the loaded series mask folder. Click the dynamic
    `保存当前 Series：<series ID>（覆盖原 Mask 并备份）` button to replace the original per-frame masks while
    preserving their filenames and creating a timestamped backup. Choose a different
    output folder to export a separate copy instead.

Expected export:

```text
manifest.csv
labels.csv
corrected_mask_4d.nii.gz
corrected_mask_frame000.nii.gz
corrected_mask_frame001.nii.gz
...
```

`manifest.csv` also records temporal index, per-frame review state, source
paths, corrected mask paths, ED/ES frame indices, automatic/manual source, and
doctor-confirmation state.

The patient root also contains `cine_cmr_qc_review.json`. It catalogs every
series, patient-entered EF, ED/ES cavity areas in mm2, first-open/default
modification time, cumulative manually modified frames, modification ratio, and
per-save history.

## Current limitations

- DICOM import itself is still handled by Slicer's native DICOM module.
- Each cine series remains an independent review unit; the extension does not
  merge basal/middle/apex series into a reconstructed 3D+t volume.
- ED/ES is estimated from maximum/minimum label-180 cavity area and must be
  confirmed by a doctor. This per-slice estimate is not EDV/ESV/EF analysis.
- MedSAM2 inference is intentionally not integrated in this first version.
