# Cine CMR QC 真实数据验收清单

验收患者：由部署人员选择一名已脱敏、目录结构完整的测试患者。

启动入口：`<扩展目录>/scripts/launch-cine-cmr-qc.command /path/to/test-patient`

自动化测试中的覆盖保存只针对临时副本，不会写真实患者目录。人工测试真实覆盖前，先确认当前 series、mask 对齐和自动填写的 `sequence/` 路径正确；插件会在该目录内自动建立时间戳备份。若只想测试另存导出，请把输出目录改为独立临时目录，例如桌面的 `CineCMRQC_Acceptance_Output`。

## A. 启动与数据识别

- [ ] 双击启动脚本后进入 `Cine CMR 逐帧质控`，没有错误弹窗。
- [ ] 默认是中文简洁模式；没有显示 Reload/Test、手动导入、审核元数据和批量导出等高级按钮。
- [ ] Summary 中的总数、医生选择数、可加载数和排除数与测试患者目录一致。
- [ ] 自动载入首个可用 Series，MRI frame 数和 mask frame 数一致。
- [ ] 患者根目录生成 `cine_cmr_qc_review.json`，其中包含扫描到的全部 Series 条目。
- [ ] 顶部可填写患者级 EF；重新扫描后数值能够从 JSON 恢复。
- [ ] 完成当前 Series 的 ED/ES 确认和保存后，在 `动态 Series` 下拉框切换，对应 MRI 与 Mask 自动一起更换，不需要手工寻找 Mask。
- [ ] 切换 Series 或患者后，上一项的 Mask 自动隐藏，不与当前 Mask 重叠。
- [ ] 标签名称为 `心腔` 和 `心肌`，不是 `Label_1/Label_2`。

## B. 方向与解剖贴合

分别选择 basal、middle、apical 等不同采集层面的 Series 检查，具体层面由医生根据解剖确认。

- [ ] 第 0 帧没有左右镜像；腔体填充和心肌轮廓位于正确侧。
- [ ] mask 不存在整体上下翻转、90 度旋转或明显平移。
- [ ] 轮廓与 MRI 心肌边缘贴合，而不是只在形状上相似但落在另一解剖位置。
- [ ] 点击 `适配完整影像` 后显示完整采集平面，不是只有层厚形成的细条。

若某个 series 不贴合，记录：`series ID + frame + 错误类型（左右/上下/旋转/平移）`。

## C. 时间序列同步

- [ ] 点击播放后 MRI 随心动周期变化。
- [ ] mask 也逐帧变化，不是同一个静态 mask 贯穿 25 帧。
- [ ] 拖动 `trigger_time` 后 MRI 和 mask 同时切到同一时相。
- [ ] 鼠标位于切片视图上时，无修饰键滚轮可逐帧切换 MRI 与 Mask，并能在首尾之间循环。
- [ ] macOS 的 `Command + 滚轮` 或 Windows 的 `Ctrl + 滚轮`仍执行原生缩放，不会同时切换时间帧。
- [ ] macOS 的 `Command+D / Command+F` 分别激活 Paint / Erase；Windows 的 `Ctrl+D / Ctrl+F` 行为相同。
- [ ] 从第 0 帧播放到第 24 帧期间没有 mask 消失、跳帧或错位。

## D. ED/ES 初判与确认

- [ ] 自动显示且仅显示一个舒张末期 ED 和一个收缩末期 ES，二者不是同一帧。
- [ ] ED 对应标签 180 心腔面积最大帧，ES 对应面积最小帧；界面明确标记为自动初判、待医生确认。
- [ ] 跳转到 ED 时 ED 按钮处于按下状态；跳转到 ES 时 ES 按钮处于按下状态。
- [ ] 未取消原 ED/ES 标记时，不能直接在另一帧替换，并提示先取消原帧。
- [ ] 原帧取消后，可以在新帧指定；人工修改后来源显示为医生指定。
- [ ] 点击 `确认当前 Series 的 ED / ES` 后显示已确认，同时保存状态变成未保存。
- [ ] ED/ES 未确认时切换 Series 被阻止；确认后但未保存时，切换弹出立即保存提示。
- [ ] 取消保存或保存失败后仍停留在当前 Series；保存成功后才进入目标 Series。

## E. 单帧编辑隔离

在一个容易识别的位置做临时修改，验收后使用 Undo 恢复。

- [ ] 第 7 帧 Paint 一个小点后，`Edit state` 立即显示 `corrected: yes`。
- [ ] 切到第 8 帧，小点不存在，且第 8 帧仍为 `corrected: no`。
- [ ] 返回第 7 帧，小点仍然存在。
- [ ] Undo 或 Erase 恢复原 mask 后，第 7 帧回到 `corrected: no`。
- [ ] 编辑过程没有把 `Cavity=180`、`Myocardium=255` 变成业务标签 1/2。

## F. 审核记录与恢复

- [ ] 输入 Reviewer 和 Frame comment，点击 `Mark Reviewed And Next Frame` 后自动进入下一帧。
- [ ] `Reviewed` 增加，但没有修改像素时 `Corrected` 仍为 no。
- [ ] 使用 Slicer 顶部 Save 保存为 `.mrb`。
- [ ] 关闭并重新打开 `.mrb` 后，审核状态、审核人、时间、备注和逐帧修改仍存在。

## G. 保存与导出

- [ ] 自动加载 series 后，输出目录默认等于该 series 的原 `segmentation/<series>/sequence/` 路径。
- [ ] 简洁模式按钮显示当前 Series ID，例如 `保存当前 Series：series0015-Body（覆盖原 Mask 并备份）`。
- [ ] 保存按钮只保存按钮上显示的当前 Series，不会顺带写入其他未保存 Series。
- [ ] 覆盖保存后，原 25 个逐帧 NIfTI 文件名不变，且 `sequence/` 顶层没有多出第 26 个医学影像文件。
- [ ] `sequence/backup_<时间戳>/` 保存覆盖前的逐帧文件；提示框给出的备份路径真实存在。
- [ ] 父目录的 `<series>_seg.nii.gz` 更新为 25 帧 4D 汇总；`labels.csv` 和 `manifest.csv` 位于 `sequence/`。
- [ ] 保存完成后“已修改帧数”归零，重新加载结果仍保留医生改动。
- [ ] 改到其他输出目录时，另存导出生成 25 个逐帧 NIfTI、一个 4D NIfTI、`labels.csv` 和 `manifest.csv`，不覆盖原文件。
- [ ] `manifest.csv` 包含 `reviewed`、`corrected`、`reviewer`、`review_timestamp`、`review_comment`、方向判定和实际变换。
- [ ] `manifest.csv` 仅有一行标记 `cardiac_phase=ED`、一行标记 `cardiac_phase=ES`，并包含来源、确认状态与确认时间。
- [ ] 覆盖保存的 `manifest.csv` 中 `source_mask_path`、`mask_path` 指向更新后的原文件，`backup_mask_path` 指向覆盖前备份。
- [ ] 患者 JSON 中当前 Series 记录 ED/ES 帧号、像素数和 `area_mm2`。
- [ ] 无人工修改的 Series 使用首次打开时间作为默认 `last_modified_at`，来源为 `series-opened-default`。
- [ ] 有人工修改后，JSON 记录本次及累计修改帧数、帧号、修改比例、保存时间和 `save_history`。
- [ ] `labels.csv` 中为 `180:心腔`、`255:心肌`。
- [ ] `Export All Ready Patient Series` 生成 14 个 series 子目录和一个 `patient_manifest.csv`。
- [ ] 批量 manifest 共 350 个 frame 记录。

## 反馈格式

请按下面格式回复，失败项只需要给最小可复现信息：

```text
A 启动：通过 / 失败
B 方向贴合：通过 / 失败，series=，frame=，现象=
C 时序同步：通过 / 失败，series=，frame=，现象=
D ED/ES：通过 / 失败，自动 ED=，自动 ES=，医生调整后=
E 编辑隔离：通过 / 失败，frame=，现象=
F 审核恢复：通过 / 失败，现象=
G 保存导出：通过 / 失败，输出目录=，现象=
```
