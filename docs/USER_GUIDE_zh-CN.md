# Cine CMR QC 中文操作手册

本文档适用于以下环境：

- 3D Slicer 5.8.1；
- Slicer 内置 Python 3.9；
- 插件源码目录：`<扩展目录>/CineCMRQC`。

本插件不运行 MedSAM2 推理。它读取 MRI 时间序列和 MedSAM2 已经生成的 mask，使用 Slicer 原生 `Sequences`、`Sequence Browser` 和 `Segment Editor` 完成逐帧检查、修改和导出。

插件自定义界面已经中文化，不要求把整个 3D Slicer 改成中文。Slicer 顶部工具栏、文件菜单以及原生 Segment Editor 的部分效果名属于 Slicer 本体；它们是否中文取决于 Slicer 的全局语言设置，不影响本插件自动配对和逐帧编辑。

真实数据部署验收请同时使用：`docs/ACCEPTANCE_CHECKLIST_zh-CN.md`。

## 1. 先理解 Volume Sequence

`Volume Sequence` 不是一种磁盘文件格式。它是 3D Slicer 内部的一种数据结构：

```text
Volume Sequence
  index 0  -> 一幅 3D volume（也可以是 Z=1 的单层 volume）
  index 1  -> 下一心动时相的 volume
  index 2  -> 下一心动时相的 volume
  ...

Sequence Browser
  负责选择当前 index、播放，并把当前 MRI 和当前 mask 同步显示出来
```

如果一条短轴 cine series 是同一个层面上的 25 个心动时相，那么它在 Slicer 中通常对应一个有 25 项的 `Volume Sequence`。每一项的尺寸可以是 `X x Y x 1`。

多个 DICOM series 不能不加判断地首尾拼接。常见情况如下：

| 医生给出的组织方式 | 正确解释 | 在插件中的处理 |
| --- | --- | --- |
| 一个 series 是 basal 层面的全部时间帧，另一个是 middle，另一个是 apex | 每个 series 都是独立的 `X x Y x 1 x T` cine | 当前版本一次审核一个 series；不要把 basal 的末帧和 middle 的首帧当作连续时间 |
| 每个 series 是一个完整 3D 心脏时相，不同 series 对应不同时间 | 多个 series 共同组成 `X x Y x Z x T` | 每个 series 作为 Volume Sequence 的一个时间项，需要按 DICOM 心动时相排序 |
| 一个 series 内已经包含所有层面和所有时间 | 数据本身已经是 3D+t | 导入后通常只需要一个 Volume Sequence |

因此，你描述的“多个 series，每个 series 内有一帧帧影像”如果分别对应 basal、middle、apex，那么当前最可靠的流程是：每个 series 建一个 Volume Sequence，分别审核。只有在确认各 series 的心动相位一致、空间位置正确后，才考虑用 SlicerHeart 的 `Reconstruct 4D cine-MRI` 重建成真正的 `X x Y x Z x T` 数据。

## 2. 插件实际支持的输入

MRI 输入支持以下三种形式：

1. 已经由 Slicer DICOM 模块载入的 `vtkMRMLSequenceNode`。这是 DICOM 数据的推荐方式。
2. 一个文件夹，每个文件是一帧 3D volume，支持 `.nii.gz`、`.nii`、`.nrrd`、`.nhdr`、`.mha`、`.mhd`。
3. 一个 4D 医学影像文件，例如 `cine.nii.gz`，其第四维是时间。

Mask 输入支持：

1. 一个文件夹，每个文件是一帧 labelmap；
2. 一个 4D labelmap 文件，第四维是时间；
3. 一个 3D labelmap 文件，此时只会得到一帧。

当前不直接读取 PNG/JPEG mask，也不直接把未经 DICOM 模块解析的原始 DICOM 文件夹当作路径输入。每帧 mask 应当是 3D volume；单层图像也要保存为 `X x Y x 1`，而不是纯二维图片。

推荐文件命名：

```text
images/
  image_frame000.nii.gz
  image_frame001.nii.gz
  ...

masks/
  mask_frame000.nii.gz
  mask_frame001.nii.gz
  ...
```

插件按文件名自然排序，要求 MRI 和 mask 帧数相等。默认标签是：

```text
0 = background
1 = LV
2 = MYO
3 = RV
```

## 3. 启动插件

### 方法 A：永久加入 Slicer

1. 打开 Slicer。
2. 进入 `Edit -> Application Settings -> Modules`。
3. 在 `Additional module paths` 中添加：

   `<扩展目录>/CineCMRQC`

4. 重启 Slicer。
5. 打开 `Modules -> Cardiac -> Cine CMR QC`。

### 方法 B：本次测试直接启动

在 macOS 终端执行：

```bash
/Applications/Slicer.app/Contents/MacOS/Slicer \
  --additional-module-paths "<扩展目录>/CineCMRQC"
```

启动后同样打开 `Modules -> Cardiac -> Cine CMR QC`。不需要单独安装 Python；插件使用 Slicer 自带的 Python 3.9。

### 方法 C：一键启动

双击 Finder 中的 `<扩展目录>/scripts/launch-cine-cmr-qc.command`。

可直接打开 Slicer 并进入插件。终端中也可以传入患者目录和可选的 series：

```bash
"<扩展目录>/scripts/launch-cine-cmr-qc.command" \
  "/path/to/patient" \
  "series0015-Body"
```

开发或更新代码后，可双击或执行统一回归测试入口：

`<扩展目录>/scripts/run-cine-cmr-qc-tests.command`

它依次检查 Python 3.8/3.12 语法、Slicer 模块和场景往返、合成数据 GUI，以及可选的真实数据测试。要运行真实数据测试，请通过 `CINE_PATIENT_PATH=/path/to/test-patient` 指定测试患者；路径不会保存在仓库中。原位覆盖测试只写系统临时目录，不会改动真实患者数据。报告保存在 `Testing/Reports/`。未指定真实数据时会明确显示 `SKIPPED`，不会把仅核心测试通过写成全部通过。

### 无真实数据时先测试插件

1. 打开 `Cine CMR QC`。
2. 点击 `Load Synthetic Cine Demo`。
3. 插件会生成 20 帧、时间索引单位为毫秒的合成 cine MRI，以及同步的 LV/MYO/RV segmentation sequence。
4. 点击播放，应该能看到心腔收缩和舒张，mask 同步变化。
5. 在任意一帧用 `Paint` 或 `Erase` 修改 mask，切换到其他帧确认修改不会贯穿全部时间点。
6. 高级模式下可勾选若干帧的 `当前帧已审核`，确认审核进度更新。
7. 选择临时输出目录并导出，检查逐帧文件和 4D 文件。

这个 demo 只用于验证插件、Slicer Sequences 和 Segment Editor 的数据链路，不代表真实 CMR 图像质量，也不能代替真实 DICOM/mask 的几何验收。

## 4. 从 DICOM 开始的真实流程

1. 打开 Slicer 的 `DICOM` 模块。
2. 点击 `Import`，选择某一位患者的 DICOM 根目录。
3. 在 DICOM Browser 中选择一个 cine series，点击 `Load`。
4. 打开 `Data` 或 `Sequences` 模块检查结果。
5. 确认场景中存在 `Sequence Browser`，并且其 master sequence 的每一项是 `vtkMRMLScalarVolumeNode`。
6. 切换 Sequence Browser 的滑块，确认 MRI 确实随心动时相变化。

如果 DICOM 被载入为一个静态 volume，而不是 sequence，不要继续加载 mask。先确认该 series 是否真的包含多个心动时相，以及 DICOM 中的 `Trigger Time`、`Temporal Position Identifier`、`Cardiac Number of Images` 等时间信息是否完整。必要时先将每个时相转换成独立 NIfTI，或使用 SlicerHeart 的 cine 重建流程。

### 约定患者目录的直接加载方式

对于当前项目约定的患者目录，使用插件最上方的 `1. 数据加载`：

1. 保持默认勾选 `简洁模式`。
2. 在 `患者目录` 选择患者根目录，不要选择其中某个 `seriesXXXX-Body`。
3. 点击 `扫描并自动加载`。插件会扫描任务并立即打开首个可用 Series。
4. 插件按 `img/` 中合法的 `seriesXXXX-Body.nii.gz` 自动发现 Series，不要求医生参考帧。
5. 对于发现的 Series，插件按完全相同的 Series ID 自动配对：

   `img/<series>.nii.gz` ↔ `segmentation/<series>/sequence/`

6. 在 `动态 Series` 下拉框选择另一个 Series 后，插件才加载其 MRI、Mask（如有）、时间轴和编辑器；安全切换后会从场景释放上一 Series，并强制隐藏全部遗留分割显示节点，始终只让当前 Series 的 Mask 可见。若当前 Series 的 ED/ES 尚未确认，则禁止切换；若存在已确认但未保存的 ED/ES、审核信息或 Mask 修改，则只能选择“立即保存并继续切换”或取消切换。已经保存过的 Series 重新打开后，若没有新修改，可以直接切换。
7. 若存在参考帧，插件将其作为初始导航提示；没有参考帧时从第 0 帧开始。
8. 使用 `上一个`、`下一个` 也会自动加载相邻 Series。
9. 高级模式默认按 DICOM `TriggerTime` 将当前 Series 的心动时相传递给新 Series；存在轻微时间差时使用最近心动时相。
10. `审核进度` 汇总已发现的 Series。保存后的 ED/ES 与逐帧审核信息会从 Mask 目录的 `manifest.csv` 恢复。

只有数据不符合上述目录约定时，才取消勾选 `简洁模式`，展开 `高级：手动导入 MRI Sequence` 和 `高级：手动导入或新建 Mask Sequence` 分别指定路径。

该目录各部分的语义是：

| 路径 | 含义 | 插件用途 |
| --- | --- | --- |
| `seriesXXXX-Body/` | 医生提供的原始 25 帧 DICOM；其中 `.nii` 是当时给出的标注来源 | 读取 DICOM `TriggerTime`，不直接改写 |
| `frames/<series>/` | 可选的医生参考帧 | 初始导航提示和审计元数据 |
| `img/<series>.nii.gz` | 一个 series 合成的 4D 模型输入 | 建立 MRI Volume Sequence |
| `segmentation/<series>/*.png` | 每个时间帧的分割结果 | 数据来源和可视化，不直接作为 Slicer labelmap 读取 |
| `segmentation/<series>/sequence/*.nii.gz` | PNG 转成的逐帧 3D NIfTI | 建立可编辑 Segmentation Sequence |
| `viz/`、`videos/`、`viz_dynamicDisplay/` | 已生成的可视化 | 插件忽略 |

不要载入 `segmentation/<series>/<series>_seg.nii.gz`。在当前数据中该文件是异常 5D 排布；Slicer 应读取 `sequence/frame_XXXXX_seg.nii.gz`。外置盘上的 `._*` 文件是 macOS AppleDouble 元数据，插件会自动排除。

当前项目标签字典为：`0=背景`、`180=心腔（Cavity）`、`255=心肌（Myocardium）`。患者目录工作流会显示中文 `心腔` 和 `心肌` segment，内部稳定 ID 仍分别为 `CineCMRQC_Label_180` 和 `CineCMRQC_Label_255`。

### 当前数据的方向处理规则

这里必须区分“模型二维数组坐标”和“Slicer 医学影像物理坐标”。当前处理链路已经核对如下：

1. `b_preprocess_addFiles.py` 从 MRI 数组生成 MedSAM2 使用的 JPG。
2. `c_preprocess_addMask.py` 中的 `np.flipud(mask)` 是把 Slicer 原始人工标注转换到 JPG/模型栅格坐标；这是进入模型前的坐标转换，不应在模型输出后原样重复。
3. MedSAM2 输出的 `segmentation/<series>/*.png` 与 JPG 处于同一栅格坐标。现有直接 PNG overlay 位置正确，说明这些 PNG 本身不需要再次翻转。
4. 旧版 `e_process_2nii.py` 又执行了 `np.flip(seg_t, axis=1)`，使 `sequence/*.nii.gz` 实际等于 PNG 的左右镜像。这正是 PNG 可视化正确、载入 Slicer 后 mask 左右相反的原因。
5. 30_2 上的 `e_process_2nii.py` 已删除这次多余左右翻转，并使用 `sitk.GetImageFromArray(seg_full, isVector=False)` 生成标量 4D mask。新生成的 NIfTI 不需要任何翻转。

患者目录扫描会抽查首帧、中间帧和末帧，将逐帧 NIfTI 与同名 PNG 比较：

- 完全相同：标记为 `aligned`，载入时不翻转；
- NIfTI 等于 `np.fliplr(PNG)`：标记为 `legacy-left-right-flipped`，载入时只执行一次 `flip_lr`；
- 无法判定：显示 `unknown` 或具体错误状态，不自动猜测方向。

因此不要长期手动勾选 `Flip masks left/right on import`。该选项只用于脱离患者目录手工加载、并且你已经用同帧 overlay 明确证明需要左右翻转的特殊数据。重复翻转会重新制造镜像错误。

插件会把判断和实际操作分别记录为 `CineCMRQC.SourceMaskOrientation` 与 `CineCMRQC.AppliedMaskTransform`，导出时写入 `manifest.csv`，便于追溯每个结果是否做过方向修正。

## 5. 在 Cine CMR QC 中载入 MRI

以下内容位于高级模式。已有 DICOM sequence 时：

1. 在 `1. Cine image sequence` 中选择已有 image sequence。
2. 点击 `Use Selected Image Sequence`。
3. 插件会找到或创建对应的 Sequence Browser，并把 MRI 放到切片视图背景层。

使用 NIfTI/NRRD 时：

1. 在 `Load image path` 选择一帧一个文件的文件夹，或一个 4D 文件。
2. 点击 `Load Image Sequence From Path`。
3. 日志应显示实际帧数，例如 `Loaded image sequence with 25 frame(s)`。

注意：一个路径应只包含当前待审核 series 的帧，不要把 basal、middle、apex 三个 series 的所有文件混在同一个文件夹中。

## 6. 载入或新建 mask sequence

已有 MedSAM2 预测时：

1. 在 `Mask path` 选择 mask 文件夹或 4D mask 文件。
2. 检查 `Labels`，例如 `1:LV,2:MYO,3:RV`。
3. 正常情况下不要勾选 `Masks are voxel-aligned; use MRI geometry`。
4. 点击 `Load Mask Sequence And Bind`。

插件会逐帧执行以下检查：

- MRI 帧数是否等于 mask 帧数；
- 每一帧的体素尺寸 `X/Y/Z` 是否一致；
- mask 的 spacing、origin、direction 是否与 MRI 一致；
- MRI 和 mask 的时间索引是否一致；
- mask sequence 是否已绑定到同一个 Sequence Browser；
- mask sequence 是否启用了逐帧 `Save changes`。

如果 MedSAM2 只输出了数组，mask 的 NIfTI header 丢失，但你能确认每个 mask 数组与对应 MRI 在体素上一一对齐，则勾选 `Masks are voxel-aligned; use MRI geometry` 后重新载入。该选项会采用 MRI 的空间几何，不能修复旋转、翻转、裁剪或帧顺序错误。

没有预测、想从空 mask 开始时，点击 `Create Empty Editable Mask Sequence`。插件会为每个 MRI 时间点创建独立的空 segmentation。

## 7. 播放和逐帧修改

患者的每个 cine frame 是 `X x Y x 1` 单层 oblique MRI。载入后插件会自动切换到单个 Red slice view，将视图旋转到原始采集平面并适配整幅图像。其他正交视图只能看到 6–10 mm 层厚，不适合作为主要审核视图。若手动修改布局后影像又只剩一条薄层，点击 `适配完整影像` 恢复。

1. 使用 `播放` 控件播放或暂停。
2. 使用 `时间 / 帧` 滑块定位具体时间点。
3. 鼠标位于切片视图上时，直接滚动滚轮即可切换上一帧或下一帧，不需要按任何键；从首帧向前滚会回到末帧，从末帧向后滚会回到首帧。
4. `Command + 滚轮`（macOS）或 `Ctrl + 滚轮`（Windows）仍由 Slicer 用于缩放，不会触发时间切帧。无修饰键滚轮只在单层 Cine Series 且当前模块为 `Cine CMR QC` 时接管，多层 3D 图像仍保留原生层面滚动。
5. macOS 按 `Command+D` 可随时激活 `Paint`，按 `Command+F` 激活 `Erase`；Windows 对应 `Ctrl+D` 和 `Ctrl+F`。快捷键只在当前模块为 `Cine CMR QC` 时生效。
6. 在 `分割修正` 中选择 `LV`、`MYO` 或 `RV`。
7. 使用 Slicer 原生的 `Paint`、`Erase`、`Draw`、`Smoothing` 等工具修改当前帧。
8. 修改完成后切换到下一帧。Sequence Browser 会把修改写回当前 segmentation sequence 项。
9. 简洁模式只保留修改状态。需要审核元数据时取消勾选 `简洁模式`。
10. 在 `审核人` 输入姓名，并可填写 `帧备注`。
11. 审核完当前帧后勾选 `当前帧已审核`；连续审核可点击 `标记已审核并进入下一帧`。

`Edit state` 与 `Reviewed` 是两个独立状态：

- `Reviewed=yes` 表示医生确认检查过该帧；
- `Corrected=yes` 表示当前整数 labelmap 与导入时的 AI mask 确实不同；
- 医生检查后认为无需修改时，可以是 `Reviewed=yes, Corrected=no`；
- 发生 Paint/Erase/Draw 等像素变化时才成为 `Corrected=yes`，恢复成原始 mask 后会自动回到 `no`。

插件对导入时合并后的整数 labelmap 保存 SHA-256 基线摘要。比较基于最终 `0/180/255` 数组，而不是 Slicer binary segment 的内部标量，因此不会把内部 representation 规范化误判为人工修改。

关键点是：编辑器里看到的是 Sequence Browser 的 segmentation proxy；插件已经为 mask sequence 打开 `Save changes`。因此切换帧时，当前修改保存到当前帧，不会作为一个静态 mask 贯穿所有时间点。

首次使用时建议做一次人工验收：

1. 在第 1 帧画一个很小、容易辨认的标记。
2. 切换到第 2 帧，确认该标记不出现。
3. 再回到第 1 帧，确认标记仍存在。
4. 撤销该测试标记。
5. 点击 `Validate Binding`，应看到：

   `绑定正常：MRI N 帧，Mask N 帧，已启用逐帧修改保存。`

## 8. 时相确认

患者目录工作流会为每个 Series 给出两个初步关键帧：

- 舒张末期 `ED`：标签 180 心腔像素面积最大的帧；
- 收缩末期 `ES`：标签 180 心腔像素面积最小的帧。

单层 cine 各帧的像素间距相同，因此面积排序可以用心腔体素数完成。这只是基于当前分割结果的辅助初判，不是独立模型，也不是临床诊断；分割错误、流出道层面或心腔显示不完整都可能使极值帧不可靠。自动结果始终显示为“待医生确认”。初判按以下顺序回退：标签 180 心腔面积极值；标签 255 开放轮廓的凸包面积极值（低置信度）；首帧与半周期时间位置（极低置信度）。后两种必须重点人工复核。

操作规则：

1. 使用 `跳转到 ED` 和 `跳转到 ES` 检查自动初判。
2. 当前帧是已指定 ED 时，`当前帧设为舒张末期 ED` 按钮处于按下状态；ES 同理。
3. 如需更换某一期，先在原关键帧再次点击对应按钮取消，使其弹起。
4. 切到新的目标帧，再按下对应按钮。ED 和 ES 不能是同一帧。
5. 两帧检查完成后，点击 `确认当前 Series 的 ED/ES`。
6. 确认会使该 Series 进入“未保存”状态；必须点击带有当前 Series 名称的保存按钮写回磁盘。

任何重新指定 ED/ES 的操作都会自动撤销之前的医生确认。未完成确认时不能切换 Series；确认后只要 Mask 或相位信息尚未保存，切换时就会询问是否立即保存。取消保存或保存失败都会留在当前 Series。

## 9. 中断后继续审核

审核过程中使用 Slicer 顶部的 `Save` 保存整个场景，建议保存为 `.mrb`。场景会保留 image sequence、segmentation sequence、Sequence Browser 关系和每帧审核状态。

重新打开场景后：

1. 打开 `Cine CMR QC`。
2. 在 `Sequence browser` 中选择之前的 browser。
3. 插件会自动找回 master image sequence 和已同步的 segmentation sequence。
4. `已审核：x/N` 会显示已有审核进度。

## 10. 保存当前 Series

从患者目录自动加载 series 后，`输出目录` 会自动填成该 series 原来载入 mask 的目录，例如：

```text
segmentation/series0015-Body/sequence/
```

简洁模式下按钮会显示当前 Series，例如 `保存当前 Series：series0015-Body`。该按钮只保存名字中显示的当前 Series，不会顺带保存其他仍在内存中的 Series。正常审核流程不需要重新寻找输出目录：

1. 确认当前 series 和画面中的 mask 正确。
2. 完成逐帧修改。
3. 保持自动填写的 `输出目录` 不变。
4. 确认 ED/ES。
5. 点击带当前 Series 名称的保存按钮。

插件先把全部新结果导出到同一磁盘的临时目录；确认逐帧和 4D 结果完整后，直接替换原文件，不创建额外备份目录。

覆盖保存后的目录语义如下：

```text
segmentation/series0015-Body/
  series0015-Body_seg.nii.gz       # 更新后的 25 帧 4D 汇总
  sequence/
    frame_00000_seg.nii.gz         # 原文件名不变，内容为修正结果
    ...
    frame_00024_seg.nii.gz
    labels.csv
    manifest.csv
```

`sequence/` 顶层只保留原来的 25 个医学影像文件，不会额外写入第 26 个 4D NIfTI。4D 汇总固定写到其父目录。保存完成后，当前结果成为新的比较基线，因此界面的“已修改帧数”归零；这表示改动已经落盘，不表示改动丢失。

### 患者级 JSON 审计记录

扫描患者时，插件在患者根目录创建或更新一个文件：

```text
cine_cmr_qc_review.json
```

该 JSON 的 `series` 对象包含扫描到的全部 Series，包括医生未选择项。未打开的 Series 只有目录状态，`first_opened_at`、ED/ES 和面积为空；首次在 Slicer 中打开后写入：

- `first_opened_at`、`last_opened_at`：首次和最近打开时间；
- `last_modified_at`：最近一次实际保存人工 Mask 修改的时间；
- `last_modified_source`：没有修改记录时为 `series-opened-default`，此时 `last_modified_at` 等于首次打开时间；
- `modified_frame_indices`、`modified_frame_numbers`：历史上曾被人工修改并保存过的帧并集；
- `modified_frame_count`：历史人工修改帧数；
- `manual_modification_ratio`：`modified_frame_count / frame_count`；
- `latest_save_modified_frame_count`：最近一次保存涉及的修改帧数；
- `save_history`：每次保存的时间、修改帧和当时的 ED/ES；
- `end_diastolic`、`end_systolic`：帧索引、从 1 开始的帧号、自动/人工来源、标签 180 像素数和物理面积 `area_mm2`；
- `cardiac_phase_confirmed` 和确认时间。

同一帧在多次保存中只计入一次历史修改比例，但每次保存仍分别保留在 `save_history`。这使后续可以统计模型输出中有多少帧曾被人工纠正。

患者级 `ejection_fraction_percent` 通过界面顶部 `射血分数 EF` 数值框由医生录入，来源记录为 `manual`。插件不会用独立单层 Series 的面积变化冒充临床 EF；没有可靠 EF 时保持 `null`。

如果主动把 `输出目录` 改到其他位置，插件会切换为另存导出，不覆盖原 mask。此时可设置 `文件前缀`，输出 25 个 `*_frameNNN.nii.gz`、一个 `*_4d.nii.gz`、`labels.csv` 和 `manifest.csv`。

需要一次导出当前患者全部医生关注且可加载的 series 时，取消勾选 `简洁模式`，点击 `导出患者全部可用 Series`。插件会按 series 建立独立子目录，必要时自动载入尚未打开的 series，并在输出根目录生成 `patient_manifest.csv`。未审核帧也会导出，但其 `reviewed=0`，便于继续追踪而不会伪装成已完成。

输出示例：

```text
manifest.csv
labels.csv
patient001_series003_corrected_4d.nii.gz
patient001_series003_corrected_frame000.nii.gz
patient001_series003_corrected_frame001.nii.gz
...
```

其中 `*_4d.nii.gz` 是把所有修正帧沿第四维重新组合得到的 series 级结果，`labels.csv` 保存整数标签与 LV/MYO/RV 名称的对应关系。逐帧文件仍然保留，便于抽查和模型评估。

`manifest.csv` 记录：

- `patient_id`、`series_id`：患者和 series；
- `frame`：从 0 开始的帧号；
- `index_value`、`index_name`、`index_unit`：Slicer sequence 的时间索引；患者目录工作流优先使用 DICOM `TriggerTime` 和毫秒单位；
- `doctor_reference_frames`：医生在 `frames/<series>` 中选中的参考帧；
- `source_mask_orientation`：导入前逐帧 NIfTI 相对于源 PNG 的方向判定；
- `applied_mask_transform`：本次导入实际应用的变换，当前为 `none` 或 `flip_lr`；
- `cardiac_phase`：该行对应帧是否为 `ED` 或 `ES`；
- `end_diastolic_frame`、`end_systolic_frame`：当前 Series 的 ED/ES 帧号；
- `end_diastolic_source`、`end_systolic_source`：`automatic-mask-cavity-extrema` 或 `manual`；
- `cardiac_phase_confirmed`、`cardiac_phase_confirmed_at`：医生是否确认及确认时间；
- `cardiac_phase_metric`、`cardiac_phase_confidence`：自动初判使用的度量与置信度；
- `end_diastolic_pixel_count`、`end_diastolic_area_mm2`：ED 心腔像素数和物理面积；
- `end_systolic_pixel_count`、`end_systolic_area_mm2`：ES 心腔像素数和物理面积；
- `reviewed`：该帧是否已人工审核；
- `corrected`：该帧最终 labelmap 是否相对导入时 AI mask 发生像素变化；
- `reviewer`、`review_timestamp`、`review_comment`：审核人、UTC 审核时间和逐帧备注；
- `source_image_path`：从路径载入时的原 MRI 文件；
- `source_mask_path`：从路径载入时的原预测文件；
- `mask_path`：修正后 mask 文件。
- `backup_mask_path`：为保持旧版 manifest 兼容而保留的空字段；当前版本不创建备份文件。

每帧导出文件采用对应 MRI 帧的空间几何。导出代码按稳定 segment ID 对二值表示进行合并，因此项目标签保持为 `0/180/255`，不会被 Slicer 默认导出流程重新编码成 `0/1/2`。空 segmentation 也会导出为与 MRI 同尺寸的全零 labelmap。

插件的 segment ID 固定为 `CineCMRQC_Label_180` 和 `CineCMRQC_Label_255`。Slicer 编辑二值 representation 时内部可能临时使用标量 `1`；摘要和导出会从稳定 ID 恢复业务标签值，因此人工编辑后仍输出 `180/255`。

患者级批量输出结构示例：

```text
output/
  patient_manifest.csv
  series0015-Body/
    manifest.csv
    labels.csv
    series0015-Body_corrected_4d.nii.gz
    series0015-Body_corrected_frame000.nii.gz
    ...
  series0019-Body/
    ...
```

## 11. 当前版本边界

当前版本已经完成患者目录扫描、医生关注 series 筛选、多 series 切换、按 DICOM `TriggerTime` 同步、逐帧修改、ED/ES 面积极值初判及医生确认、审核状态和逐帧/4D 导出。每个 cine series 仍作为独立审核单元；插件不会把不同层面的 series 错拼成一条时间轴，也不会把单层面积极值当成患者级 EDV、ESV 或 EF。

## 12. 常见错误

`Image/mask frame count mismatch`

MRI 和 mask 数量不同，或文件夹中混入了其他 `.nii/.nrrd` 文件。先检查目录和排序。

`Mask spatial geometry mismatch`

mask header 的 spacing/origin/direction 与 MRI 不一致。优先修复生成 mask 的脚本，使其复制原 MRI affine；只有确认数组体素严格对齐时才使用“采用 MRI 几何”选项。

播放时 MRI 在动，但 mask 不动

通常是把一个普通 `Segmentation` 当成静态节点加载了。应通过本插件创建 `Segmentation Sequence`，并与 image sequence 放在同一个 Sequence Browser 中。

选择列表中没有 MRI sequence

DICOM 还没有被解析为 Slicer 的 `vtkMRMLSequenceNode`。先回到 DICOM/Sequences 模块处理数据组织。

修改一帧后其他帧也出现同样内容

点击 `Validate Binding`。如果报 `Save changes is disabled`，说明当前 mask sequence 没有启用逐帧保存；重新通过插件绑定 mask sequence。

切换患者后仍看到上一患者的 Mask

最新版会使用 `患者绝对路径 + Series ID` 作为缓存键，并只显示当前 Sequence Browser 的 Mask。若旧场景由早期版本创建，请重新启动插件并再次扫描患者目录；不要手工把多个 segmentation 的眼睛图标同时打开。
