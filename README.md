# Cine CMR Segmentation QC for 3D Slicer

## 中文

### 设计目的

心脏 Cine MRI 由多个心动时相组成，影像会随时间连续变化，因此分割 Mask 也必须与每一帧一一对应并同步切换。本插件用于在 3D Slicer 中完成动态心脏 MRI 分割结果的逐帧检查、修改、确认和保存，避免将同一个静态 Mask 错误地显示在全部时相上。

### 核心需求

- MRI 时间序列与 Mask 时间序列同步播放、跳转和显示；
- 每一帧的 Mask 可独立编辑，修改不会影响其他帧；
- 根据患者目录和 Series ID 自动配对 MRI 与对应分割结果；
- 切换患者或 Series 时自动隔离上一项的 Mask，避免重叠显示；
- 为每个 Series 提供舒张末期（ED）和收缩末期（ES）初步判断，并要求医生复核、修改和确认；
- 有未保存修改或未确认 ED/ES 时阻止切换，避免人工修改丢失；
- 保存当前 Series 时覆盖对应分割结果并自动备份原文件；
- 保持原始图像空间方向、Mask 几何和标签值一致；
- 记录 ED/ES 帧、面积、人工修改帧数、修改时间、保存历史及患者射血分数，便于后续质量分析和复盘。

## English

### Purpose

Cine cardiac MRI contains multiple cardiac phases, so the segmentation mask must correspond to and change with every image frame. This extension provides a 3D Slicer workflow for frame-by-frame review, correction, confirmation, and saving of temporal cardiac MRI segmentations, preventing one static mask from being incorrectly displayed across the entire sequence.

### Core Requirements

- Synchronized playback, navigation, and display of MRI and mask sequences;
- Independent mask editing for each frame without changing other frames;
- Automatic pairing of MRI series and segmentation results by patient folder and Series ID;
- Automatic isolation of masks when switching patients or series;
- Initial end-diastolic (ED) and end-systolic (ES) estimates for each series, followed by mandatory physician review and confirmation;
- Protection against switching when edits are unsaved or ED/ES phases are unconfirmed;
- Series-specific saving with automatic backup of the previous segmentation files;
- Preservation of image orientation, mask geometry, and original label values;
- Audit records for ED/ES frames and areas, manually modified frames, modification time, save history, and patient ejection fraction.

如有任何问题或建议，请前往 [Issues](https://github.com/wanghy1997/CineCMRQCExtension/issues)；for questions, feedback, or suggestions, please use [Issues](https://github.com/wanghy1997/CineCMRQCExtension/issues).
