# Cine CMR Segmentation QC for 3D Slicer

[English](README.md) | [简体中文](README_zh-CN.md)

## What's New in v0.2.4

- Load only the first available series initially and load another series only after the physician selects it;
- Keep only the active patient series resident in the Slicer scene, releasing the previous series after a safe switch;
- Force-hide managed stale segmentation proxies and display nodes so masks from another series cannot overlay the active image;
- Reopen a previously saved series without a save prompt when no new annotation, review, or ED/ES changes were made;
- Track edited-frame candidates incrementally so mouse-wheel cine navigation no longer rescans every frame after an edit;
- Export all patient series sequentially to avoid retaining every series in memory at once.
- Keep Slicer's Data Probe panel collapsed by default to preserve vertical space in the module panel.

## Purpose

Cine cardiac MRI contains multiple cardiac phases, so the segmentation mask must correspond to and change with every image frame. This extension provides a 3D Slicer workflow for frame-by-frame review, correction, confirmation, and saving of temporal cardiac MRI segmentations, preventing one static mask from being incorrectly displayed across the entire sequence.

## Core Requirements

- Synchronized playback, navigation, and display of MRI and mask sequences;
- Modifier-free mouse-wheel navigation through single-slice cine frames while preserving native Command/Ctrl zoom;
- Module-scoped Command/Ctrl shortcuts for immediate Paint and Erase activation;
- Independent mask editing for each frame without changing other frames;
- Automatic pairing of MRI series and segmentation results by patient folder and Series ID;
- Automatic isolation of masks when switching patients or series;
- Initial end-diastolic (ED) and end-systolic (ES) estimates for each series, followed by mandatory physician review and confirmation;
- Protection against switching when edits are unsaved or ED/ES phases are unconfirmed;
- Series-specific saving with automatic backup of the previous segmentation files;
- Preservation of image orientation, mask geometry, and original label values;
- Audit records for ED/ES frames and areas, manually modified frames, modification time, save history, and patient ejection fraction.

For questions, feedback, or suggestions, please use [Issues](https://github.com/wanghy1997/CineCMRQCExtension/issues).
