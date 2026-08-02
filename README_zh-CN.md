# 3D Slicer 动态心脏 MRI 分割质控

[English](README.md) | [简体中文](README_zh-CN.md)

## v0.2.4 新增功能

- 扫描患者后默认只加载首个可用 Series，其他 Series 仅在医生主动选择时加载；
- 安全切换后从 Slicer 场景释放上一 Series，始终只让当前患者 Series 常驻内存；
- 强制隐藏遗留的分割 Proxy 与 Display Node，避免其他 Series 的 Mask 叠加到当前影像；
- 已保存 Series 重新打开后，若没有新的标注、审核或 ED/ES 修改，切换时不再提示保存；
- 使用增量方式记录可能修改的帧，滚轮切帧不再因为一次编辑而重复扫描整套 Mask；
- 患者全部 Series 导出改为依次加载和导出，避免同时保留全部 Series。
- Slicer 底部的 Data Probe 面板默认折叠，为插件菜单保留更多纵向空间。

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
