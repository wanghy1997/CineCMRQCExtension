# Cine CMR QC 本地安装与使用

## 1. 兼容性

- 推荐 3D Slicer 5.8.1；
- 纯 Python Scripted Module，不需要单独安装 PyTorch 或 MedSAM；
- 使用 Slicer 自带的 `Sequences`、`Segmentations`、`Segment Editor` 和 SimpleITK；
- 支持 macOS、Windows 和 Linux。首次部署应先用一名测试患者验证路径、方向和保存权限。

## 2. 解压发布包

将 `CineCMRQCExtension-0.2.3.zip` 解压到固定目录。安装后不要移动或删除该目录。

正确结构：

```text
CineCMRQCExtension-0.2.3/
  CMakeLists.txt
  RELEASE.json
  NOTICE.txt
  CineCMRQC/
    CMakeLists.txt
    CineCMRQC.py
  docs/
    INSTALL_zh-CN.md
```

## 3. 推荐安装：Extension Wizard

该方式与 MedSAMSlicer 的本地 Release 安装流程一致。

1. 启动 3D Slicer。
2. 如果模块菜单中没有 `Developer Tools`：
   - 打开 `Edit > Application Settings > Developer`；
   - 启用开发者模式并重启 Slicer。
3. 在顶部模块菜单选择 `Developer Tools > Extension Wizard`。
4. 点击 `Select Extension`。
5. 选择解压后的扩展根目录 `CineCMRQCExtension-0.2.3`，即包含根 `CMakeLists.txt` 的目录。不要只选里面的 `CineCMRQC` 子目录。
6. 出现“添加模块路径”询问时选择确认。
7. 重启 Slicer。
8. 在模块菜单 `Cardiac > Cine CMR 逐帧质控` 中打开插件。

此安装不经过 Slicer 官方扩展服务器。插件源码仍保存在解压目录中，升级时用新版本目录重新执行上述步骤。

## 4. 备用安装：Additional module paths

如果 Extension Wizard 无法选择扩展，可直接添加模块路径：

1. 打开 `Edit > Application Settings > Modules`。
2. 在 `Additional module paths` 点击添加。
3. 选择解压目录中的 `CineCMRQC` 子目录：

   ```text
   <解压目录>/CineCMRQCExtension-0.2.3/CineCMRQC
   ```

4. 确认设置并重启 Slicer。
5. 从 `Cardiac > Cine CMR 逐帧质控` 打开插件。

## 5. 卸载

1. 打开 `Edit > Application Settings > Modules`。
2. 从 `Additional module paths` 删除指向该扩展的路径。
3. 重启 Slicer。
4. 确认模块不再出现后，可删除解压目录。

## 6. 患者目录要求

插件按相同 Series ID 自动配对：

```text
患者目录/
  img/
    series0015-Body.nii.gz
  frames/
    series0015-Body/
      00000.png
  segmentation/
    series0015-Body/
      00000.png
      ...
      sequence/
        frame_00000_seg.nii.gz
        ...
```

- `frames/<series>` 非空表示该 Series 需要医生质控；
- `img/<series>.nii.gz` 是 MRI 4D 序列；
- `segmentation/<series>/sequence/` 是逐帧 Mask；
- 默认标签为 `0=背景`、`180=心腔`、`255=心肌`。

## 7. 日常使用

1. 在插件顶部选择患者根目录。
2. 点击 `扫描并自动加载`。
3. 检查 MRI 与 Mask 对齐并逐帧修改。
4. 检查自动 ED/ES；低置信度或极低置信度结果必须重点复核，必要时手工指定。
5. 点击 `确认当前 Series 的 ED / ES`。
6. 在顶部填写患者 EF；没有可靠 EF 时保持未填写。
7. 点击带当前 Series ID 的保存按钮。
8. 保存成功后再切换到下一个 Series。

保存会覆盖当前 Series 原来的 25 个逐帧 Mask，并在 `sequence/backup_<时间戳>/` 自动备份。按钮只保存其文字中显示的当前 Series。

患者根目录会生成 `cine_cmr_qc_review.json`，记录全部 Series、ED/ES 面积、人工修改帧、修改比例、保存历史和患者 EF。

## 8. 首次部署验收

在正式使用前至少验证：

1. MRI 与 Mask 都是预期帧数；
2. Mask 没有左右、上下或旋转错误；
3. 修改一帧不会影响其他帧；
4. 未确认 ED/ES 时无法切换；
5. 未保存修改会触发保存提示；
6. 保存后原文件有备份，重新加载仍保留修改；
7. `cine_cmr_qc_review.json` 可正常读取。

## 9. 常见问题

### 模块菜单中找不到插件

检查 `Additional module paths` 是否指向包含 `CineCMRQC.py` 的 `CineCMRQC` 子目录，然后重启 Slicer。

### 保存失败

确认患者目录可写、磁盘空间充足，并避免在保存期间拔出移动硬盘。插件使用 SimpleITK 写 NIfTI，兼容 ExFAT；保存测试失败时原文件不会进入替换阶段。

### 自动 ED/ES 显示低置信度

插件依次使用标签 180 心腔面积、标签 255 轮廓凸包面积和首帧/半周期时间位置进行初判。后两种会明确显示低或极低置信度，必须由医生重点复核后确认。
