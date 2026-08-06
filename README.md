# Cine CMR Segmentation QC for 3D Slicer

[English](README.md) | [简体中文](README_zh-CN.md)

## What's New in v0.2.5

- Load every physician-selected MRI series in the patient batch, whether or not a source Mask exists;
- Determine loading eligibility from physician reference frames only, excluding unselected mixed or non-target series regardless of Mask state;
- Prepare an empty in-memory segmentation for image-only series without creating Mask files before the physician saves an annotation;
- Let the physician mark the current series as annotation-not-required, directly delete its existing Mask, and record the decision and history in the patient JSON;
- Allow a later physician to annotate the same series again, recreate its canonical Mask files, and retain the decision-change history;
- Require manual ED/ES selection only when an image-only series is being saved with annotations;
- Skip image-only and annotation-not-required series during whole-patient Mask export.

## Purpose

Cine cardiac MRI contains multiple cardiac phases, so the segmentation mask must correspond to and change with every image frame. This extension provides a 3D Slicer workflow for frame-by-frame review, correction, confirmation, and saving of temporal cardiac MRI segmentations, preventing one static mask from being incorrectly displayed across the entire sequence.

## Core Requirements

- Synchronized playback, navigation, and display of MRI and mask sequences;
- Modifier-free mouse-wheel navigation through single-slice cine frames while preserving native Command/Ctrl zoom;
- Module-scoped Command/Ctrl shortcuts for immediate Paint and Erase activation;
- Independent mask editing for each frame without changing other frames;
- Automatic pairing of MRI series and segmentation results by patient folder and Series ID;
- Loading of physician-selected image-only series with an editable empty segmentation;
- Automatic isolation of masks when switching patients or series;
- Initial end-diastolic (ED) and end-systolic (ES) estimates for each series, followed by mandatory physician review and confirmation;
- Protection against switching when edits are unsaved or ED/ES phases are unconfirmed;
- Series-specific saving with automatic backup of the previous segmentation files;
- A physician decision for annotation-not-required series that deletes existing Masks and is recoverable later by creating new annotations;
- Preservation of image orientation, mask geometry, and original label values;
- Audit records for ED/ES frames and areas, manually modified frames, modification time, save history, and patient ejection fraction.

For questions, feedback, or suggestions, please use [Issues](https://github.com/wanghy1997/CineCMRQCExtension/issues).
