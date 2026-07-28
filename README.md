# Cine CMR Segmentation QC for 3D Slicer

[English](README.md) | [简体中文](README_zh-CN.md)

## What's New in v0.2.3

- Navigate single-slice cine frames directly with the mouse wheel, including cyclic navigation between the first and last frames;
- Preserve native Slicer zoom with `Command + wheel` on macOS or `Ctrl + wheel` on Windows;
- Activate Paint at any time with `Command/Ctrl + D`, and Erase with `Command/Ctrl + F`;
- Display the current version, GitHub project address, and contributors in Help & Acknowledgement.

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
