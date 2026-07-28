# 3D Slicer 动态心脏 MRI 分割质控

[English](README.md) | [简体中文](README_zh-CN.md)

## v0.2.3 新增功能

- 鼠标位于单层 Cine 视图时，可直接使用滚轮逐帧切换，并支持首帧与末帧循环衔接；
- macOS 使用 `Command + 滚轮`、Windows 使用 `Ctrl + 滚轮`时，仍保留 Slicer 原生缩放；
- 使用 `Command/Ctrl + D` 随时激活 Paint，使用 `Command/Ctrl + F` 激活 Erase；
- 在 Help & Acknowledgement 中显示当前版本、GitHub 项目地址和贡献者。

## 设计目的

心脏 Cine MRI 由多个心动时相组成，影像会随时间连续变化，因此分割 Mask 也必须与每一帧一一对应并同步切换。本插件用于在 3D Slicer 中完成动态心脏 MRI 分割结果的逐帧检查、修改、确认和保存，避免将同一个静态 Mask 错误地显示在全部时相上。

## 核心需求

- MRI 时间序列与 Mask 时间序列同步播放、跳转和显示；
- 单层 Cine 视图支持无修饰键滚轮逐帧切换，同时保留 Command/Ctrl 滚轮缩放；
- 提供模块内 Command/Ctrl 快捷键，可随时激活 Paint 和 Erase；
- 每一帧的 Mask 可独立编辑，修改不会影响其他帧；
- 根据患者目录和 Series ID 自动配对 MRI 与对应分割结果；
- 切换患者或 Series 时自动隔离上一项的 Mask，避免重叠显示；
- 为每个 Series 提供舒张末期（ED）和收缩末期（ES）初步判断，并要求医生复核、修改和确认；
- 有未保存修改或未确认 ED/ES 时阻止切换，避免人工修改丢失；
- 保存当前 Series 时覆盖对应分割结果并自动备份原文件；
- 保持原始图像空间方向、Mask 几何和标签值一致；
- 记录 ED/ES 帧、面积、人工修改帧数、修改时间、保存历史及患者射血分数，便于后续质量分析和复盘。

如有任何问题或建议，请前往 [Issues](https://github.com/wanghy1997/CineCMRQCExtension/issues) 提出。
