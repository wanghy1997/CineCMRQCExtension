import csv
import datetime
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import ctk
import qt
import slicer
import vtk

from slicer.ScriptedLoadableModule import (
    ScriptedLoadableModule,
    ScriptedLoadableModuleLogic,
    ScriptedLoadableModuleTest,
    ScriptedLoadableModuleWidget,
)

CINE_CMR_QC_VERSION = "0.3.0"
CINE_CMR_QC_GITHUB_URL = "https://github.com/wanghy1997/CineCMRQCExtension"


class CineCMRQC(ScriptedLoadableModule):
    """Cine CMR segmentation quality-control workflow."""

    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = "Cine CMR 逐帧质控"
        self.parent.categories = ["Cardiac"]
        self.parent.dependencies = ["Sequences", "Segmentations", "SegmentEditor"]
        self.parent.contributors = ["Wang Hongyi", "Codex"]
        self.parent.helpText = """
        自动配对动态心脏 MRI 与逐帧 Mask，在同一时间轴中播放、修改并导出。
        """
        self.parent.acknowledgementText = """
        <p>本模块复用 3D Slicer 的 Sequences、Segmentations 与 Segment Editor。</p>
        <p><b>当前版本：</b>v{0}</p>
        <p><b>GitHub：</b><a href="{1}">{1}</a></p>
        """.format(CINE_CMR_QC_VERSION, CINE_CMR_QC_GITHUB_URL)
        if not slicer.app.commandOptions().noMainWindow:
            slicer.app.connect(
                "startupCompleted()",
                self._collapseDataProbeAfterStartup,
            )

    def _collapseDataProbeAfterStartup(self):
        qt.QTimer.singleShot(0, self.collapseDataProbe)

    @staticmethod
    def collapseDataProbe():
        mainWindow = slicer.util.mainWindow()
        if not mainWindow:
            return False
        dataProbe = mainWindow.findChild("QWidget", "DataProbeCollapsibleWidget")
        if not dataProbe:
            return False
        dataProbe.collapsed = True
        return True


class CineCMRQCWidget(ScriptedLoadableModuleWidget):
    """Small workflow panel around native Slicer sequence/segmentation tools."""

    def __init__(self, parent=None):
        ScriptedLoadableModuleWidget.__init__(self, parent)
        self.logic = None
        self.imageSequenceNode = None
        self.segmentationSequenceNode = None
        self.sequenceBrowserNode = None
        self.embeddedEditor = None
        self.segmentEditorNode = None
        self.browserObserverTag = None
        self.observedBrowserNode = None
        self.segmentationObserverTag = None
        self.observedSegmentationProxy = None
        self.segmentationObjectObserverTags = []
        self.observedSegmentationObject = None
        self.lastSelectedItemNumber = -1
        self.updatingReviewCheckBox = False
        self.updatingReviewMetadata = False
        self.processingSegmentationProxyModified = False
        self.updatingIncrementalDirtyState = False
        self.patientSeriesEntries = []
        self.loadedPatientSeries = {}
        self.patientSeriesRuntimeState = {}
        self.syncingPatientSeries = False
        self.updatingPatientSeriesComboBox = False
        self.suppressAutomaticSeriesLoad = False
        self.loadingPatientSeries = False
        self.preferredPatientSeriesId = ""
        self.simpleModeAdvancedFields = []
        self.activePatientSeriesIndex = -1
        self.updatingCardiacPhaseControls = False
        self.updatingAnnotationDecisionControl = False
        self.updatingPatientEfControl = False
        self.sliceWheelObservers = []
        self.observedLayoutManager = None
        self.segmentEditorShortcuts = []
        self.auditWritingEnabled = os.environ.get(
            "CINE_CMR_QC_DISABLE_AUDIT_WRITE",
            "0",
        ) != "1"

    def setup(self):
        ScriptedLoadableModuleWidget.setup(self)
        self.logic = CineCMRQCLogic()
        CineCMRQC.collapseDataProbe()

        self._buildPatientSection()
        self._buildInputSection()
        self._buildMaskSection()
        self._buildWorkflowSection()
        self._buildCardiacPhaseSection()
        self._buildEmbeddedSegmentEditorSection()
        self._buildExportSection()
        self._buildLogSection()
        self._applySimpleMode(True)

        self.layout.addStretch(1)
        self._refreshNodeSelectors()
        self._observeSliceViewMouseWheels()
        self._installSegmentEditorShortcuts()
        self._log("已就绪。请选择患者目录，插件会自动匹配并加载影像与分割结果。")

    def _buildPatientSection(self):
        self.patientSection = ctk.ctkCollapsibleButton()
        self.patientSection.text = "1. 选择患者与动态 Series"
        self.patientSection.collapsed = False
        self.layout.addWidget(self.patientSection)
        form = qt.QFormLayout(self.patientSection)

        self.simpleModeCheckBox = qt.QCheckBox("简洁模式")
        self.simpleModeCheckBox.checked = True
        self.simpleModeCheckBox.toolTip = (
            "仅显示患者/Series、播放、逐帧编辑和当前 Series 导出。"
        )
        self.simpleModeCheckBox.toggled.connect(self._applySimpleMode)
        form.addRow("界面：", self.simpleModeCheckBox)

        self.patientPathEdit = ctk.ctkPathLineEdit()
        self.patientPathEdit.filters = ctk.ctkPathLineEdit.Dirs
        self.patientPathEdit.toolTip = (
            "选择包含 img/ 的患者根目录；frames/ 和 segmentation/ 为可选目录。"
        )
        form.addRow("患者目录：", self.patientPathEdit)

        self.scanPatientButton = qt.QPushButton("扫描并自动加载")
        self.scanPatientButton.clicked.connect(self.onScanPatientFolder)
        form.addRow("", self.scanPatientButton)

        self.patientSummaryLabel = qt.QLabel("尚未扫描患者目录。")
        self.patientSummaryLabel.wordWrap = True
        form.addRow("数据概况：", self.patientSummaryLabel)

        self.patientEfSpinBox = qt.QDoubleSpinBox()
        self.patientEfSpinBox.minimum = -1.0
        self.patientEfSpinBox.maximum = 100.0
        self.patientEfSpinBox.decimals = 1
        self.patientEfSpinBox.singleStep = 1.0
        self.patientEfSpinBox.specialValueText = "未填写"
        self.patientEfSpinBox.suffix = " %"
        self.patientEfSpinBox.value = -1.0
        self.patientEfSpinBox.toolTip = (
            "患者级射血分数由医生录入；独立单层 Series 不用于自动计算临床 EF。"
        )
        self.patientEfSpinBox.editingFinished.connect(self.onPatientEfChanged)
        form.addRow("射血分数 EF：", self.patientEfSpinBox)

        self.patientSeriesComboBox = qt.QComboBox()
        self.patientSeriesComboBox.currentIndexChanged.connect(
            self._onPatientSeriesSelectionChanged
        )
        form.addRow("动态 Series：", self.patientSeriesComboBox)

        navigationRow = qt.QHBoxLayout()
        self.previousPatientSeriesButton = qt.QPushButton("上一个")
        self.previousPatientSeriesButton.clicked.connect(lambda: self.onStepPatientSeries(-1))
        navigationRow.addWidget(self.previousPatientSeriesButton)
        self.nextPatientSeriesButton = qt.QPushButton("下一个")
        self.nextPatientSeriesButton.clicked.connect(lambda: self.onStepPatientSeries(1))
        navigationRow.addWidget(self.nextPatientSeriesButton)
        form.addRow("切换：", navigationRow)

        self.loadPatientSeriesButton = qt.QPushButton("重新加载当前 Series 与 Mask")
        self.loadPatientSeriesButton.clicked.connect(self.onLoadSelectedPatientSeries)
        form.addRow("", self.loadPatientSeriesButton)

        self.nextUnreviewedSeriesButton = qt.QPushButton("加载下一个未审核 Series")
        self.nextUnreviewedSeriesButton.clicked.connect(self.onLoadNextUnreviewedSeries)
        form.addRow("", self.nextUnreviewedSeriesButton)

        self.patientReviewProgressLabel = qt.QLabel("患者审核进度：0/0 帧")
        form.addRow("审核进度：", self.patientReviewProgressLabel)

        self.syncPatientSeriesCheckBox = qt.QCheckBox("切换 Series 时保持 TriggerTime")
        self.syncPatientSeriesCheckBox.checked = True
        self.syncPatientSeriesCheckBox.toolTip = (
            "医生主动选择另一个 Series 时，跳转到最接近当前 TriggerTime 的心动时相。"
        )
        form.addRow("时间同步：", self.syncPatientSeriesCheckBox)
        self._registerAdvancedField(form, self.syncPatientSeriesCheckBox)

    def _buildInputSection(self):
        self.inputSection = ctk.ctkCollapsibleButton()
        self.inputSection.text = "高级：手动导入 MRI Sequence"
        self.inputSection.collapsed = True
        self.layout.addWidget(self.inputSection)
        form = qt.QFormLayout(self.inputSection)

        self.imageSequenceSelector = slicer.qMRMLNodeComboBox()
        self.imageSequenceSelector.nodeTypes = ["vtkMRMLSequenceNode"]
        self.imageSequenceSelector.selectNodeUponCreation = False
        self.imageSequenceSelector.addEnabled = False
        self.imageSequenceSelector.removeEnabled = False
        self.imageSequenceSelector.noneEnabled = True
        self.imageSequenceSelector.showHidden = False
        self.imageSequenceSelector.showChildNodeTypes = False
        self.imageSequenceSelector.setMRMLScene(slicer.mrmlScene)
        form.addRow("已有 MRI Sequence：", self.imageSequenceSelector)

        self.useImageSequenceButton = qt.QPushButton("使用选中的 MRI Sequence")
        self.useImageSequenceButton.toolTip = "使用 Slicer 场景中已有的 Volume Sequence。"
        self.useImageSequenceButton.clicked.connect(self.onUseSelectedImageSequence)
        form.addRow("", self.useImageSequenceButton)

        self.imagePathEdit = ctk.ctkPathLineEdit()
        self.imagePathEdit.filters = ctk.ctkPathLineEdit.Files | ctk.ctkPathLineEdit.Dirs
        self.imagePathEdit.toolTip = "选择逐帧文件夹或 SimpleITK 可读取的 4D 医学影像。"
        form.addRow("MRI 路径：", self.imagePathEdit)

        self.loadImageButton = qt.QPushButton("从路径加载 MRI Sequence")
        self.loadImageButton.clicked.connect(self.onLoadImageSequence)
        form.addRow("", self.loadImageButton)

        self.loadDemoButton = qt.QPushButton("加载合成心跳演示")
        self.loadDemoButton.toolTip = "创建可编辑的 20 帧合成心跳影像与 Mask。"
        self.loadDemoButton.clicked.connect(self.onLoadDemo)
        form.addRow("", self.loadDemoButton)

    def _buildMaskSection(self):
        self.maskSection = ctk.ctkCollapsibleButton()
        self.maskSection.text = "高级：手动导入或新建 Mask Sequence"
        self.maskSection.collapsed = True
        self.layout.addWidget(self.maskSection)
        form = qt.QFormLayout(self.maskSection)

        self.maskPathEdit = ctk.ctkPathLineEdit()
        self.maskPathEdit.filters = ctk.ctkPathLineEdit.Files | ctk.ctkPathLineEdit.Dirs
        self.maskPathEdit.toolTip = "选择逐帧 Mask 文件夹或 4D Mask 文件。"
        form.addRow("Mask 路径：", self.maskPathEdit)

        self.useImageGeometryCheckBox = qt.QCheckBox("Mask 体素已对齐，采用 MRI 几何信息")
        self.useImageGeometryCheckBox.checked = False
        self.useImageGeometryCheckBox.toolTip = (
            "Enable only when each mask array has the same voxel dimensions as its MRI frame "
            "but the mask file header does not preserve spacing/origin/direction."
        )
        form.addRow("几何：", self.useImageGeometryCheckBox)

        self.flipMasksLeftRightCheckBox = qt.QCheckBox("导入时左右翻转 Mask")
        self.flipMasksLeftRightCheckBox.checked = False
        self.flipMasksLeftRightCheckBox.toolTip = (
            "Explicit correction for legacy masks mirrored in pixel space. "
            "Patient-folder scanning enables this automatically only after PNG/NIfTI comparison."
        )
        form.addRow("方向：", self.flipMasksLeftRightCheckBox)

        self.labelMapEdit = qt.QLineEdit("1:LV,2:MYO,3:RV")
        self.labelMapEdit.toolTip = "Comma-separated label map, for example: 1:LV,2:MYO,3:RV"
        form.addRow("标签：", self.labelMapEdit)

        self.autoDetectLabelsCheckBox = qt.QCheckBox("自动识别 Mask 中的非零标签值")
        self.autoDetectLabelsCheckBox.checked = True
        self.autoDetectLabelsCheckBox.toolTip = (
            "Use the integer values actually present in the mask sequence. "
            "Values missing from the Labels field are named Label_<value>."
        )
        form.addRow("标签处理：", self.autoDetectLabelsCheckBox)

        self.loadMaskButton = qt.QPushButton("加载并绑定 Mask Sequence")
        self.loadMaskButton.clicked.connect(self.onLoadMaskSequence)
        form.addRow("", self.loadMaskButton)

        self.createEmptyMaskButton = qt.QPushButton("新建逐帧空白 Mask")
        self.createEmptyMaskButton.toolTip = "为每个 MRI 时间帧创建独立的空白分割。"
        self.createEmptyMaskButton.clicked.connect(self.onCreateEmptyMaskSequence)
        form.addRow("", self.createEmptyMaskButton)

    def _buildWorkflowSection(self):
        self.workflowSection = ctk.ctkCollapsibleButton()
        self.workflowSection.text = "2. 播放与逐帧定位"
        self.layout.addWidget(self.workflowSection)
        form = qt.QFormLayout(self.workflowSection)

        self.browserSelector = slicer.qMRMLNodeComboBox()
        self.browserSelector.nodeTypes = ["vtkMRMLSequenceBrowserNode"]
        self.browserSelector.selectNodeUponCreation = False
        self.browserSelector.addEnabled = False
        self.browserSelector.removeEnabled = False
        self.browserSelector.noneEnabled = True
        self.browserSelector.showHidden = False
        self.browserSelector.showChildNodeTypes = False
        self.browserSelector.setMRMLScene(slicer.mrmlScene)
        self.browserSelector.currentNodeChanged.connect(self.onBrowserSelected)
        form.addRow("当前序列：", self.browserSelector)

        self.statusLabel = qt.QLabel("尚未加载动态序列。")
        self.statusLabel.wordWrap = True
        form.addRow("状态：", self.statusLabel)

        self.playWidget = slicer.qMRMLSequenceBrowserPlayWidget()
        self.playWidget.setMRMLScene(slicer.mrmlScene)
        form.addRow("播放：", self.playWidget)

        self.seekWidget = slicer.qMRMLSequenceBrowserSeekWidget()
        self.seekWidget.setMRMLScene(slicer.mrmlScene)
        form.addRow("时间 / 帧：", self.seekWidget)

        self.reviewerEdit = qt.QLineEdit()
        self.reviewerEdit.placeholderText = "审核人姓名"
        self.reviewerEdit.toolTip = "Stored with every frame when it is marked reviewed."
        form.addRow("审核人：", self.reviewerEdit)
        self._registerAdvancedField(form, self.reviewerEdit)

        self.reviewCommentEdit = qt.QLineEdit()
        self.reviewCommentEdit.placeholderText = "当前帧备注（可选）"
        form.addRow("帧备注：", self.reviewCommentEdit)
        self._registerAdvancedField(form, self.reviewCommentEdit)

        self.reviewedCheckBox = qt.QCheckBox("当前帧已审核")
        self.reviewedCheckBox.toggled.connect(self.onReviewedToggled)
        form.addRow("审核状态：", self.reviewedCheckBox)
        self._registerAdvancedField(form, self.reviewedCheckBox)

        self.reviewAndNextButton = qt.QPushButton("标记已审核并进入下一帧")
        self.reviewAndNextButton.clicked.connect(self.onMarkReviewedAndNext)
        form.addRow("", self.reviewAndNextButton)

        self.reviewProgressLabel = qt.QLabel("已审核：0/0")
        form.addRow("进度：", self.reviewProgressLabel)
        self._registerAdvancedField(form, self.reviewProgressLabel)

        self.correctedStateLabel = qt.QLabel("当前帧已修改：否")
        form.addRow("修改状态：", self.correctedStateLabel)

        self.reviewMetadataLabel = qt.QLabel("当前帧暂无审核记录。")
        self.reviewMetadataLabel.wordWrap = True
        form.addRow("审核记录：", self.reviewMetadataLabel)
        self._registerAdvancedField(form, self.reviewMetadataLabel)

        self.openSegmentEditorButton = qt.QPushButton("打开独立分割编辑器")
        self.openSegmentEditorButton.clicked.connect(self.onOpenSegmentEditor)
        form.addRow("", self.openSegmentEditorButton)

        self.fitCineViewButton = qt.QPushButton("适配完整影像")
        self.fitCineViewButton.toolTip = (
            "Switch to one slice view, align it to the native single-slice cine plane, and fit the image."
        )
        self.fitCineViewButton.clicked.connect(self.onFitCineView)
        form.addRow("", self.fitCineViewButton)

        self.validateButton = qt.QPushButton("检查 MRI 与 Mask 绑定")
        self.validateButton.clicked.connect(self.onValidate)
        form.addRow("", self.validateButton)

    def _buildEmbeddedSegmentEditorSection(self):
        self.editorSection = ctk.ctkCollapsibleButton()
        self.editorSection.text = "4. 逐帧修改 Mask"
        self.editorSection.collapsed = False
        self.layout.addWidget(self.editorSection)
        layout = qt.QVBoxLayout(self.editorSection)

        self.embeddedEditor = slicer.qMRMLSegmentEditorWidget()
        self.embeddedEditor.setMRMLScene(slicer.mrmlScene)
        self.embeddedEditor.setMaximumNumberOfUndoStates(10)
        self.segmentEditorNode = self.logic.getOrCreateSegmentEditorNode()
        self.embeddedEditor.setMRMLSegmentEditorNode(self.segmentEditorNode)
        layout.addWidget(self.embeddedEditor)

    def _buildCardiacPhaseSection(self):
        self.cardiacPhaseSection = ctk.ctkCollapsibleButton()
        self.cardiacPhaseSection.text = "3. 确认舒张末期与收缩末期"
        self.cardiacPhaseSection.collapsed = False
        self.layout.addWidget(self.cardiacPhaseSection)
        form = qt.QFormLayout(self.cardiacPhaseSection)

        self.cardiacPhaseSummaryLabel = qt.QLabel("尚未加载可判断的 Series。")
        self.cardiacPhaseSummaryLabel.wordWrap = True
        form.addRow("关键帧：", self.cardiacPhaseSummaryLabel)

        phaseButtonRow = qt.QHBoxLayout()
        self.endDiastolicButton = qt.QPushButton("当前帧设为舒张末期 ED")
        self.endDiastolicButton.checkable = True
        self.endDiastolicButton.toolTip = (
            "按下表示当前帧是舒张末期；如需改到其他帧，先在原帧取消。"
        )
        self.endDiastolicButton.toggled.connect(
            lambda checked: self.onCardiacPhaseToggled("ED", checked)
        )
        phaseButtonRow.addWidget(self.endDiastolicButton)
        self.endSystolicButton = qt.QPushButton("当前帧设为收缩末期 ES")
        self.endSystolicButton.checkable = True
        self.endSystolicButton.toolTip = (
            "按下表示当前帧是收缩末期；如需改到其他帧，先在原帧取消。"
        )
        self.endSystolicButton.toggled.connect(
            lambda checked: self.onCardiacPhaseToggled("ES", checked)
        )
        phaseButtonRow.addWidget(self.endSystolicButton)
        form.addRow("当前帧：", phaseButtonRow)

        jumpRow = qt.QHBoxLayout()
        self.jumpToEndDiastolicButton = qt.QPushButton("跳转到 ED")
        self.jumpToEndDiastolicButton.clicked.connect(
            lambda: self.onJumpToCardiacPhase("ED")
        )
        jumpRow.addWidget(self.jumpToEndDiastolicButton)
        self.jumpToEndSystolicButton = qt.QPushButton("跳转到 ES")
        self.jumpToEndSystolicButton.clicked.connect(
            lambda: self.onJumpToCardiacPhase("ES")
        )
        jumpRow.addWidget(self.jumpToEndSystolicButton)
        form.addRow("定位：", jumpRow)

        self.confirmCardiacPhasesButton = qt.QPushButton("确认当前 Series 的 ED / ES")
        self.confirmCardiacPhasesButton.clicked.connect(self.onConfirmCardiacPhases)
        form.addRow("", self.confirmCardiacPhasesButton)

        self.annotationNotRequiredButton = qt.QPushButton("当前 Series 无需标注")
        self.annotationNotRequiredButton.checkable = True
        self.annotationNotRequiredButton.toolTip = (
            "医生确认后删除当前 Series 已有 Mask，并将“无需标注”决定写入患者 JSON。"
        )
        self.annotationNotRequiredButton.toggled.connect(
            self.onAnnotationNotRequiredToggled
        )
        form.addRow("医生决定：", self.annotationNotRequiredButton)

        self.cardiacPhaseConfirmationLabel = qt.QLabel("状态：待医生确认")
        self.cardiacPhaseConfirmationLabel.wordWrap = True
        form.addRow("确认状态：", self.cardiacPhaseConfirmationLabel)

    def cleanup(self):
        self._removeSegmentEditorShortcuts()
        self._removeSliceViewMouseWheelObservers()
        self._observeBrowser(None)
        self._observeSegmentationProxy(None)
        if self.embeddedEditor:
            self.embeddedEditor.setMRMLScene(None)
            self.embeddedEditor = None

    def _buildExportSection(self):
        self.exportSection = ctk.ctkCollapsibleButton()
        self.exportSection.text = "5. 保存当前 Series"
        self.layout.addWidget(self.exportSection)
        form = qt.QFormLayout(self.exportSection)

        self.exportPathEdit = ctk.ctkPathLineEdit()
        self.exportPathEdit.filters = ctk.ctkPathLineEdit.Dirs
        self.exportPathEdit.toolTip = "Output folder for corrected labelmaps and manifest.csv."
        form.addRow("输出目录：", self.exportPathEdit)

        self.exportPrefixEdit = qt.QLineEdit("corrected_mask")
        form.addRow("文件前缀：", self.exportPrefixEdit)
        self._registerAdvancedField(form, self.exportPrefixEdit)

        self.exportButton = qt.QPushButton("保存当前 Series（覆盖原 Mask 并备份）")
        self.exportButton.clicked.connect(self.onExportMasks)
        form.addRow("", self.exportButton)

        self.seriesSaveStateLabel = qt.QLabel("当前 Series 尚未加载。")
        self.seriesSaveStateLabel.wordWrap = True
        form.addRow("保存状态：", self.seriesSaveStateLabel)

        self.exportAllPatientButton = qt.QPushButton("导出患者全部可用 Series")
        self.exportAllPatientButton.toolTip = (
            "Load and export every doctor-selected ready series into separate subfolders, "
            "then create patient_manifest.csv."
        )
        self.exportAllPatientButton.clicked.connect(self.onExportAllPatientSeries)
        form.addRow("", self.exportAllPatientButton)

    def _buildLogSection(self):
        self.logSection = ctk.ctkCollapsibleButton()
        self.logSection.text = "高级：运行日志"
        self.logSection.collapsed = True
        self.layout.addWidget(self.logSection)
        layout = qt.QVBoxLayout(self.logSection)

        self.logBox = qt.QPlainTextEdit()
        self.logBox.readOnly = True
        self.logBox.maximumHeight = 120
        layout.addWidget(self.logBox)

    def _registerAdvancedField(self, form, field):
        self.simpleModeAdvancedFields.append((form.labelForField(field), field))

    def _applySimpleMode(self, simpleMode):
        simpleMode = bool(simpleMode)
        for sectionName in ["inputSection", "maskSection", "logSection"]:
            section = getattr(self, sectionName, None)
            if section:
                section.visible = not simpleMode
        for label, field in self.simpleModeAdvancedFields:
            if label:
                label.visible = not simpleMode
            field.visible = not simpleMode
        for widgetName in [
            "loadPatientSeriesButton",
            "nextUnreviewedSeriesButton",
            "reviewAndNextButton",
            "openSegmentEditorButton",
            "validateButton",
            "exportAllPatientButton",
        ]:
            widget = getattr(self, widgetName, None)
            if widget:
                widget.visible = not simpleMode
        if hasattr(self, "exportButton"):
            self._updateSaveControls()
        if self.parent:
            for frameworkSection in slicer.util.findChildren(
                widget=self.parent,
                className="ctkCollapsibleButton",
            ):
                frameworkText = str(getattr(frameworkSection, "text", "")).replace(
                    "&&", "&"
                )
                if frameworkText in ["Reload & Test", "Help & Acknowledgement"]:
                    frameworkSection.visible = not simpleMode

    def _currentSeriesId(self):
        if not self.imageSequenceNode:
            return ""
        return self.imageSequenceNode.GetAttribute("CineCMRQC.SeriesID") or ""

    def _annotationDecision(self, segmentationSequenceNode=None):
        sequenceNode = segmentationSequenceNode or self.segmentationSequenceNode
        if not sequenceNode:
            return "undecided"
        # Legacy, demo, and manually imported sequences predate this attribute
        # and already belong to the annotation-required workflow. Image-only
        # patient series are explicitly initialized as "undecided" on load.
        decision = sequenceNode.GetAttribute("CineCMRQC.AnnotationDecision") or "required"
        return decision if decision in ["undecided", "required", "not_required"] else "undecided"

    def _annotationDecisionIsSaved(self, segmentationSequenceNode=None):
        sequenceNode = segmentationSequenceNode or self.segmentationSequenceNode
        if not sequenceNode:
            return False
        savedAttribute = sequenceNode.GetAttribute(
            "CineCMRQC.AnnotationDecisionSaved"
        )
        if savedAttribute is None:
            return self._annotationDecision(sequenceNode) == "required"
        return savedAttribute == "1"

    def _markCurrentSeriesAnnotationRequired(self):
        if not self.segmentationSequenceNode:
            return
        if self._annotationDecision() == "required":
            return
        self.segmentationSequenceNode.SetAttribute(
            "CineCMRQC.AnnotationDecision",
            "required",
        )
        self.segmentationSequenceNode.SetAttribute(
            "CineCMRQC.AnnotationDecisionSaved",
            "0",
        )
        self.segmentationSequenceNode.SetAttribute(
            "CineCMRQC.RequireCardiacPhaseConfirmation",
            "1",
        )
        self.segmentationSequenceNode.Modified()
        self._refreshCardiacPhaseControls()
        self._updateSaveControls()

    def onAnnotationNotRequiredToggled(self, checked):
        if self.updatingAnnotationDecisionControl:
            return
        if not self.segmentationSequenceNode or not self.imageSequenceNode:
            self._setAnnotationNotRequiredButton(False)
            return
        if checked:
            confirmed = slicer.util.confirmYesNoDisplay(
                "确认不需要标注吗？",
                windowTitle="确认",
            )
            if not confirmed:
                self._setAnnotationNotRequiredButton(False)
                return
            self._saveAnnotationNotRequiredDecision()
            return
        if self._annotationDecision() == "not_required":
            self._markCurrentSeriesAnnotationRequired()

    def _setAnnotationNotRequiredButton(self, checked):
        if not hasattr(self, "annotationNotRequiredButton"):
            return
        self.updatingAnnotationDecisionControl = True
        try:
            self.annotationNotRequiredButton.checked = bool(checked)
        finally:
            self.updatingAnnotationDecisionControl = False

    def _saveAnnotationNotRequiredDecision(self):
        patientRoot = self.imageSequenceNode.GetAttribute("CineCMRQC.PatientRoot") or ""
        seriesId = self._currentSeriesId()
        if not patientRoot or not seriesId:
            self._setAnnotationNotRequiredButton(False)
            slicer.util.errorDisplay("仅患者目录工作流支持保存“无需标注”决定。")
            return False
        if not self.auditWritingEnabled:
            self._setAnnotationNotRequiredButton(False)
            slicer.util.errorDisplay("患者 JSON 写入当前已禁用，不能删除 Mask。")
            return False
        try:
            with slicer.util.tryWithErrorDisplay(
                "保存“无需标注”决定失败。",
                waitCursor=True,
            ):
                result = self.logic.deleteSeriesMasksAndRecordDecision(
                    patientRoot,
                    seriesId,
                    self.reviewerEdit.text.strip(),
                )
                self.processingSegmentationProxyModified = True
                try:
                    self.logic.clearSegmentationSequence(
                        self.segmentationSequenceNode,
                        self.imageSequenceNode,
                        self.sequenceBrowserNode,
                        self.logic.HUAXI_LABELS,
                    )
                finally:
                    self.processingSegmentationProxyModified = False
                self.logic.setAnnotationDecisionState(
                    self.segmentationSequenceNode,
                    "not_required",
                    saved=True,
                )
                self.segmentationSequenceNode.SetAttribute(
                    "CineCMRQC.SourceMaskExists",
                    "0",
                )
                self.segmentationSequenceNode.SetAttribute(
                    "CineCMRQC.SourceMaskFolder",
                    os.path.join(patientRoot, "segmentation", seriesId, "sequence"),
                )
                if 0 <= self.activePatientSeriesIndex < len(self.patientSeriesEntries):
                    entry = self.patientSeriesEntries[self.activePatientSeriesIndex]
                    entry["mask_frame_count"] = 0
                    entry["mask_orientation"] = "not-available"
                    entry["status"] = "ready"
                    entry["annotation_decision"] = "not_required"
                self._setDirtyFrameCandidates([])
                self.logic.initializeReviewMetadataBaseline(self.segmentationSequenceNode)
                self._refreshCardiacPhaseControls()
                self._updateStatus()
                self._refreshPatientSeriesList(True)
                deletedText = "\n".join(result.get("deleted_paths", [])) or "无已有 Mask 文件"
                self._log("{0} 已判定为无需标注；Mask 已删除。".format(seriesId))
                slicer.util.infoDisplay(
                    "医生决定已保存：当前 Series 无需标注。\n\n已删除：\n{0}\n\n患者 JSON：\n{1}".format(
                        deletedText,
                        result["audit_path"],
                    )
                )
                return True
        except Exception as exc:
            self._setAnnotationNotRequiredButton(False)
            slicer.util.errorDisplay(str(exc))
            return False

    def _seriesSaveState(self, segmentationSequenceNode, imageSequenceNode=None, browserNode=None):
        if not segmentationSequenceNode:
            return "尚未加载"
        if self._annotationDecision(segmentationSequenceNode) == "undecided":
            return "待医生决定"
        if (
            self._annotationDecision(segmentationSequenceNode) == "not_required"
            and self._annotationDecisionIsSaved(segmentationSequenceNode)
        ):
            return "已保存：无需标注"
        phaseState = self.logic.getCardiacPhaseState(segmentationSequenceNode)
        if (
            segmentationSequenceNode.GetAttribute(
                "CineCMRQC.RequireCardiacPhaseConfirmation"
            ) == "1"
            and not phaseState["confirmed"]
        ):
            return "待确认 ED/ES"
        phaseDirty = self.logic.isCardiacPhaseStateDirty(segmentationSequenceNode)
        maskMayBeDirty = segmentationSequenceNode.GetAttribute(
            "CineCMRQC.MaskMayBeDirty"
        ) == "1"
        reviewMetadataDirty = segmentationSequenceNode.GetAttribute(
            "CineCMRQC.ReviewMetadataMayBeDirty"
        ) == "1"
        if phaseDirty or maskMayBeDirty or reviewMetadataDirty:
            return "未保存"
        if segmentationSequenceNode.GetAttribute("CineCMRQC.HasBeenSaved") == "1":
            return "已保存"
        return "未修改"

    def _updateSaveControls(self):
        if not hasattr(self, "exportButton"):
            return
        seriesId = self._currentSeriesId()
        if self.simpleModeCheckBox.checked:
            self.exportButton.text = (
                "保存当前 Series：{0}（覆盖原 Mask 并备份）".format(seriesId)
                if seriesId
                else "保存当前 Series（覆盖原 Mask 并备份）"
            )
        else:
            self.exportButton.text = "导出当前 Series 到指定目录"
        state = self._seriesSaveState(self.segmentationSequenceNode)
        if hasattr(self, "seriesSaveStateLabel"):
            self.seriesSaveStateLabel.text = (
                "{0}：{1}".format(seriesId, state)
                if seriesId
                else "当前 Series 尚未加载。"
            )

    def _refreshCardiacPhaseControls(self):
        if not hasattr(self, "cardiacPhaseSummaryLabel"):
            return
        state = self.logic.getCardiacPhaseState(self.segmentationSequenceNode)
        frameCount = (
            self.segmentationSequenceNode.GetNumberOfDataNodes()
            if self.segmentationSequenceNode
            else 0
        )
        currentFrame = (
            self.sequenceBrowserNode.GetSelectedItemNumber()
            if self.sequenceBrowserNode
            else -1
        )
        annotationNotRequired = self._annotationDecision() == "not_required"
        self._setAnnotationNotRequiredButton(annotationNotRequired)
        self.annotationNotRequiredButton.text = (
            "已判定无需标注（点击恢复标注）"
            if annotationNotRequired
            else "当前 Series 无需标注"
        )

        def sourceText(source):
            return {
                "automatic-mask-cavity-extrema": "自动初判",
                "automatic-contour-convex-hull-extrema": "轮廓凸包低置信度初判",
                "automatic-temporal-opposition": "时间位置极低置信度初判",
                "manual": "医生指定",
                "restored": "已恢复",
                "unavailable": "无法自动判断",
            }.get(source, source or "未指定")

        def frameText(frameIndex, source):
            if frameIndex < 0:
                return "未指定"
            return "第 {0}/{1} 帧（{2}）".format(
                frameIndex + 1,
                frameCount,
                sourceText(source),
            )

        self.cardiacPhaseSummaryLabel.text = (
            "医生决定：当前 Series 无需标注；ED / ES 不适用。"
            if annotationNotRequired
            else "舒张末期 ED：{0}\n收缩末期 ES：{1}".format(
                frameText(state["ed_frame"], state["ed_source"]),
                frameText(state["es_frame"], state["es_source"]),
            )
        )
        self.updatingCardiacPhaseControls = True
        try:
            self.endDiastolicButton.checked = currentFrame == state["ed_frame"]
            self.endSystolicButton.checked = currentFrame == state["es_frame"]
        finally:
            self.updatingCardiacPhaseControls = False
        self.endDiastolicButton.enabled = not annotationNotRequired
        self.endSystolicButton.enabled = not annotationNotRequired
        self.jumpToEndDiastolicButton.enabled = (
            not annotationNotRequired and state["ed_frame"] >= 0
        )
        self.jumpToEndSystolicButton.enabled = (
            not annotationNotRequired and state["es_frame"] >= 0
        )
        canConfirm = (
            0 <= state["ed_frame"] < frameCount
            and 0 <= state["es_frame"] < frameCount
            and state["ed_frame"] != state["es_frame"]
        )
        self.confirmCardiacPhasesButton.enabled = (
            not annotationNotRequired and canConfirm and not state["confirmed"]
        )
        self.confirmCardiacPhasesButton.text = (
            "ED / ES 已确认"
            if state["confirmed"]
            else "确认当前 Series 的 ED / ES"
        )
        if annotationNotRequired:
            self.cardiacPhaseConfirmationLabel.text = (
                "已保存“无需标注”决定，可直接进入下一个 Series。"
            )
        elif state["confirmed"]:
            self.cardiacPhaseConfirmationLabel.text = "已由医生确认，时间：{0}".format(
                state["confirmed_at"]
            )
        elif state["confidence"] in ["low", "very-low"]:
            self.cardiacPhaseConfirmationLabel.text = (
                "待医生确认；当前为{0}置信度初判，请重点复核后再保存。".format(
                    "低" if state["confidence"] == "low" else "极低"
                )
            )
        else:
            self.cardiacPhaseConfirmationLabel.text = (
                "待医生确认；切换 Series 前必须完成确认并保存。"
            )

    def onCardiacPhaseToggled(self, phase, checked):
        if self.updatingCardiacPhaseControls:
            return
        if not self.segmentationSequenceNode or not self.sequenceBrowserNode:
            return
        if checked:
            self._markCurrentSeriesAnnotationRequired()
        frameIndex = self.sequenceBrowserNode.GetSelectedItemNumber()
        state = self.logic.getCardiacPhaseState(self.segmentationSequenceNode)
        assignedFrame = state["ed_frame"] if phase == "ED" else state["es_frame"]
        if checked and assignedFrame >= 0 and assignedFrame != frameIndex:
            self.updatingCardiacPhaseControls = True
            try:
                button = self.endDiastolicButton if phase == "ED" else self.endSystolicButton
                button.checked = False
            finally:
                self.updatingCardiacPhaseControls = False
            phaseName = "舒张末期 ED" if phase == "ED" else "收缩末期 ES"
            slicer.util.infoDisplay(
                "{0} 当前已标记在第 {1} 帧。\n\n请先跳转到原帧并取消标记，再在新帧指定。".format(
                    phaseName,
                    assignedFrame + 1,
                )
            )
            return
        try:
            self.logic.setCardiacPhaseFrame(
                self.segmentationSequenceNode,
                phase,
                frameIndex if checked else -1,
            )
            self._refreshCardiacPhaseControls()
            self._updateSaveControls()
            self._recordCurrentSeriesAudit(event="phase-update")
        except Exception as exc:
            slicer.util.errorDisplay(str(exc))
            self._refreshCardiacPhaseControls()

    def onJumpToCardiacPhase(self, phase):
        if not self.segmentationSequenceNode or not self.sequenceBrowserNode:
            return
        state = self.logic.getCardiacPhaseState(self.segmentationSequenceNode)
        frameIndex = state["ed_frame"] if phase == "ED" else state["es_frame"]
        if frameIndex < 0:
            return
        self.sequenceBrowserNode.SetSelectedItemNumber(frameIndex)
        slicer.modules.sequences.logic().UpdateProxyNodesFromSequences(
            self.sequenceBrowserNode
        )

    def onConfirmCardiacPhases(self):
        try:
            self._markCurrentSeriesAnnotationRequired()
            self.logic.confirmCardiacPhaseFrames(self.segmentationSequenceNode)
            self._refreshCardiacPhaseControls()
            self._updateSaveControls()
            self._recordCurrentSeriesAudit(event="phase-update")
        except Exception as exc:
            slicer.util.errorDisplay(str(exc))

    def _currentSeriesHasUnsavedChanges(self):
        if not (
            self.imageSequenceNode
            and self.segmentationSequenceNode
            and self.sequenceBrowserNode
        ):
            return False
        maskDirty = bool(self._verifiedDirtyFrameIndices())
        self.segmentationSequenceNode.SetAttribute(
            "CineCMRQC.MaskMayBeDirty",
            "1" if maskDirty else "0",
        )
        reviewMetadataDirty = self.segmentationSequenceNode.GetAttribute(
            "CineCMRQC.ReviewMetadataMayBeDirty"
        ) == "1"
        decisionDirty = not self._annotationDecisionIsSaved()
        return (
            maskDirty
            or reviewMetadataDirty
            or self.logic.isCardiacPhaseStateDirty(self.segmentationSequenceNode)
            or decisionDirty
        )

    def _dirtyFrameCandidates(self, segmentationSequenceNode=None):
        sequenceNode = segmentationSequenceNode or self.segmentationSequenceNode
        if not sequenceNode:
            return set()
        frameCount = sequenceNode.GetNumberOfDataNodes()
        candidates = set()
        for value in (sequenceNode.GetAttribute("CineCMRQC.DirtyFrameCandidates") or "").split(","):
            try:
                frameIndex = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= frameIndex < frameCount:
                candidates.add(frameIndex)
        return candidates

    def _setDirtyFrameCandidates(self, frameIndices, segmentationSequenceNode=None):
        sequenceNode = segmentationSequenceNode or self.segmentationSequenceNode
        if not sequenceNode:
            return
        normalized = sorted(set(int(value) for value in frameIndices if int(value) >= 0))
        serialized = ",".join(str(value) for value in normalized)
        dirtyValue = "1" if normalized else "0"
        self.updatingIncrementalDirtyState = True
        try:
            if (sequenceNode.GetAttribute("CineCMRQC.DirtyFrameCandidates") or "") != serialized:
                sequenceNode.SetAttribute("CineCMRQC.DirtyFrameCandidates", serialized)
            if (sequenceNode.GetAttribute("CineCMRQC.MaskMayBeDirty") or "0") != dirtyValue:
                sequenceNode.SetAttribute("CineCMRQC.MaskMayBeDirty", dirtyValue)
        finally:
            self.updatingIncrementalDirtyState = False

    def _verifiedDirtyFrameIndices(self):
        if not (
            self.imageSequenceNode
            and self.segmentationSequenceNode
            and self.sequenceBrowserNode
        ):
            return []
        verified = []
        for frameIndex in sorted(self._dirtyFrameCandidates()):
            if self.logic.isFrameCorrected(
                self.segmentationSequenceNode,
                self.imageSequenceNode,
                self.sequenceBrowserNode,
                frameIndex,
            ):
                verified.append(frameIndex)
        self._setDirtyFrameCandidates(verified)
        return verified

    def _restoreActiveSeriesSelection(self):
        if not (0 <= self.activePatientSeriesIndex < len(self.patientSeriesEntries)):
            return
        self.updatingPatientSeriesComboBox = True
        try:
            self.patientSeriesComboBox.setCurrentIndex(self.activePatientSeriesIndex)
        finally:
            self.updatingPatientSeriesComboBox = False

    def _canLeaveCurrentSeries(self):
        if not self.segmentationSequenceNode:
            return True
        if (
            self._annotationDecision() == "not_required"
            and self._annotationDecisionIsSaved()
        ):
            return True
        if self._annotationDecision() == "undecided":
            slicer.util.infoDisplay(
                "当前 Series 尚未完成标注需求判断。\n\n"
                "如需标注，请人工指定 ED/ES 并保存；如无需标注，请点击“当前 Series 无需标注”。"
            )
            return False
        phaseState = self.logic.getCardiacPhaseState(self.segmentationSequenceNode)
        if (
            self.segmentationSequenceNode.GetAttribute(
                "CineCMRQC.RequireCardiacPhaseConfirmation"
            ) == "1"
            and not phaseState["confirmed"]
        ):
            slicer.util.infoDisplay(
                "当前 Series 尚未确认舒张末期 ED 和收缩末期 ES。\n\n"
                "请先确认两期并保存，之后才能切换 Series。"
            )
            return False
        if not self._currentSeriesHasUnsavedChanges():
            return True
        seriesId = self._currentSeriesId() or "当前 Series"
        saveNow = slicer.util.confirmYesNoDisplay(
            "{0} 存在未保存修改。\n\n切换前必须写回该 Series 的原 Mask。"
            "是否立即保存并继续切换？".format(seriesId),
            windowTitle="存在未保存修改",
        )
        if not saveNow:
            return False
        return self._saveCurrentSeries(showSuccess=False)

    def _refreshNodeSelectors(self):
        for selector in [self.imageSequenceSelector, self.browserSelector]:
            selector.setMRMLScene(slicer.mrmlScene)

    def _log(self, message):
        logging.info(message)
        if hasattr(self, "logBox"):
            self.logBox.appendPlainText(message)
        if hasattr(self, "statusLabel"):
            self.statusLabel.text = message

    def onUseSelectedImageSequence(self):
        sequenceNode = self.imageSequenceSelector.currentNode()
        if not sequenceNode:
            slicer.util.errorDisplay("请先选择一个 MRI Volume Sequence。")
            return
        try:
            browserNode = self.logic.getOrCreateBrowserForSequence(sequenceNode)
            self._setActiveImageSequence(sequenceNode, browserNode)
            self._log("正在使用 MRI Sequence：{0}".format(sequenceNode.GetName()))
        except Exception as exc:
            slicer.util.errorDisplay("无法使用所选 MRI Sequence：{0}".format(exc))

    def onScanPatientFolder(self):
        patientPath = self.patientPathEdit.currentPath
        if not patientPath:
            slicer.util.errorDisplay("请先选择患者目录。")
            return
        if self.imageSequenceNode and not self._canLeaveCurrentSeries():
            activePatientRoot = self.imageSequenceNode.GetAttribute(
                "CineCMRQC.PatientRoot"
            ) or ""
            if activePatientRoot:
                self.patientPathEdit.currentPath = activePatientRoot
            return
        self._unloadAllPatientSeries()
        self.logic.setActiveCineSegmentationVisibility(None, None)
        try:
            with slicer.util.tryWithErrorDisplay("扫描患者目录失败。", waitCursor=True):
                scanResult = self.logic.scanPatientFolder(patientPath)
                self.activePatientSeriesIndex = -1
                if self.auditWritingEnabled:
                    self.logic.initializePatientAuditSeriesCatalog(
                        patientPath,
                        scanResult["series"],
                    )
                patientAudit = self.logic.loadPatientAudit(patientPath)
                auditSeriesRecords = patientAudit.get("series", {})
                for entry in scanResult["series"]:
                    record = auditSeriesRecords.get(entry["series_id"], {})
                    entry["annotation_decision"] = record.get(
                        "annotation_decision",
                        "required" if entry["mask_frame_count"] else "undecided",
                    )
                self.updatingPatientEfControl = True
                try:
                    efValue = patientAudit.get("ejection_fraction_percent")
                    self.patientEfSpinBox.value = (
                        float(efValue) if efValue is not None else -1.0
                    )
                finally:
                    self.updatingPatientEfControl = False
                self.patientSeriesEntries = [
                    entry for entry in scanResult["series"] if entry["load_eligible"]
                ]
                self._refreshPatientSeriesList(False)
                self.patientSummaryLabel.text = (
                    "全部：{0} | 已发现：{1} | 可加载：{2} | "
                    "不可加载：{3} | 需处理：{4} | 历史左右纠正：{5}"
                ).format(
                    scanResult["total_count"],
                    scanResult["selected_count"],
                    scanResult["ready_count"],
                    scanResult["excluded_count"],
                    scanResult["attention_count"],
                    scanResult["legacy_lr_count"],
                )
                self._log("患者目录扫描完成。{0}".format(self.patientSummaryLabel.text))
                readyIndices = [
                    index
                    for index, entry in enumerate(self.patientSeriesEntries)
                    if entry["status"] == "ready"
                ]
                if readyIndices:
                    preferredIndices = [
                        index
                        for index in readyIndices
                        if self.patientSeriesEntries[index]["series_id"]
                        == self.preferredPatientSeriesId
                    ]
                    targetIndex = preferredIndices[0] if preferredIndices else readyIndices[0]
                    self.updatingPatientSeriesComboBox = True
                    try:
                        self.patientSeriesComboBox.setCurrentIndex(targetIndex)
                    finally:
                        self.updatingPatientSeriesComboBox = False
                    self.onLoadSelectedPatientSeries()
        except Exception as exc:
            slicer.util.errorDisplay(str(exc))

    def onPatientEfChanged(self):
        if self.updatingPatientEfControl:
            return
        patientPath = self.patientPathEdit.currentPath
        if not patientPath or not os.path.isdir(patientPath):
            return
        if not self.auditWritingEnabled:
            return
        efValue = self.patientEfSpinBox.value
        try:
            self.logic.updatePatientEjectionFraction(
                patientPath,
                None if efValue < 0 else float(efValue),
            )
        except Exception as exc:
            slicer.util.errorDisplay("患者 EF 写入 JSON 失败：{0}".format(exc))

    def _recordCurrentSeriesAudit(self, event="phase-update", modifiedFrameIndices=None):
        if not self.auditWritingEnabled:
            return ""
        if not (
            self.imageSequenceNode
            and self.segmentationSequenceNode
            and self.sequenceBrowserNode
        ):
            return ""
        patientRoot = self.imageSequenceNode.GetAttribute("CineCMRQC.PatientRoot") or ""
        seriesId = self._currentSeriesId()
        if not patientRoot or not seriesId:
            return ""
        try:
            if event == "save" and self.reviewerEdit.text.strip():
                self.segmentationSequenceNode.SetAttribute(
                    "CineCMRQC.DefaultReviewer",
                    self.reviewerEdit.text.strip(),
                )
            return self.logic.updatePatientSeriesAudit(
                patientRoot,
                seriesId,
                self.segmentationSequenceNode,
                self.imageSequenceNode,
                self.sequenceBrowserNode,
                event,
                modifiedFrameIndices,
            )
        except Exception as exc:
            logging.exception("Patient JSON audit update failed.")
            slicer.util.errorDisplay("患者 JSON 审计记录写入失败：{0}".format(exc))
            return ""

    def _refreshPatientSeriesList(self, preserveSelection=True):
        selectedIndex = self.patientSeriesComboBox.currentIndex if preserveSelection else 0
        self.updatingPatientSeriesComboBox = True
        self.patientSeriesComboBox.clear()
        reviewedFrameCount = 0
        totalFrameCount = 0
        completedSeriesCount = 0
        for entry in self.patientSeriesEntries:
            referenceText = ",".join(str(value) for value in entry["reference_frames"])
            reviewedCount = 0
            correctedCount = 0
            loadedNodes = self._loadedPatientSeriesNodes(entry)
            if loadedNodes:
                reviewedCount = self.logic.countReviewedFrames(loadedNodes[1])
                correctedCount = len(self._dirtyFrameCandidates(loadedNodes[1]))
            else:
                runtimeState = self.patientSeriesRuntimeState.get(
                    self._patientSeriesCacheKey(entry),
                    {},
                )
                reviewedCount = int(runtimeState.get("reviewed_count", 0))
                correctedCount = int(runtimeState.get("corrected_count", 0))
            phaseSaveState = (
                self._seriesSaveState(loadedNodes[1])
                if loadedNodes
                else self.patientSeriesRuntimeState.get(
                    self._patientSeriesCacheKey(entry),
                    {},
                ).get("save_state", "未加载")
            )
            totalFrameCount += entry["image_frame_count"]
            reviewedFrameCount += reviewedCount
            if (
                entry.get("annotation_decision") == "not_required"
                or (
                    entry["image_frame_count"] > 0
                    and reviewedCount == entry["image_frame_count"]
                )
            ):
                completedSeriesCount += 1
            displayText = (
                "{0} | 参考帧 {1} | Mask {2}/{3} | 已审核 {4}/{3} | "
                "已修改 {5}/{3} | {6} | 保存：{7} | {8} | {9}"
            ).format(
                entry["series_id"],
                referenceText,
                entry["mask_frame_count"],
                entry["image_frame_count"],
                reviewedCount,
                correctedCount,
                self._statusDisplayText(entry["status"]),
                phaseSaveState,
                self._orientationDisplayText(entry["mask_orientation"]),
                self._annotationDecisionDisplayText(
                    entry.get("annotation_decision", "undecided")
                ),
            )
            self.patientSeriesComboBox.addItem(displayText)
        if self.patientSeriesEntries:
            self.patientSeriesComboBox.setCurrentIndex(
                max(0, min(selectedIndex, len(self.patientSeriesEntries) - 1))
            )
        self.updatingPatientSeriesComboBox = False
        self.patientReviewProgressLabel.text = (
            "患者审核进度：{0}/{1} 帧 | 已完成 Series：{2}/{3}"
        ).format(
            reviewedFrameCount,
            totalFrameCount,
            completedSeriesCount,
            len(self.patientSeriesEntries),
        )

    def _statusDisplayText(self, status):
        return {
            "ready": "可编辑",
            "excluded-by-doctor": "医生未选择",
            "selected-mask-not-generated": "缺少分割结果",
            "image-mask-frame-mismatch": "MRI/Mask 帧数不一致",
            "reference-frame-out-of-range": "参考帧越界",
        }.get(status, status)

    def _orientationDisplayText(self, orientation):
        return {
            "aligned": "方向一致",
            "legacy-left-right-flipped": "已自动纠正历史左右镜像",
            "not-available": "无方向对照",
            "frame-count-mismatch": "方向对照帧数不一致",
            "shape-mismatch": "方向对照尺寸不一致",
            "up-down-flipped": "检测到上下镜像",
            "rotated-180": "检测到 180 度旋转",
            "unknown": "方向未知",
        }.get(orientation, orientation)

    def _annotationDecisionDisplayText(self, decision):
        return {
            "required": "需要标注",
            "not_required": "无需标注",
            "undecided": "待医生决定",
        }.get(decision, "待医生决定")

    def _onPatientSeriesSelectionChanged(self, *args):
        if (
            self.updatingPatientSeriesComboBox
            or self.suppressAutomaticSeriesLoad
            or self.loadingPatientSeries
            or not self.patientSeriesEntries
        ):
            return
        selectedIndex = self.patientSeriesComboBox.currentIndex
        if selectedIndex < 0 or selectedIndex >= len(self.patientSeriesEntries):
            return
        if self.patientSeriesEntries[selectedIndex]["status"] == "ready":
            self.onLoadSelectedPatientSeries()

    def _loadedPatientSeriesNodes(self, entry):
        cacheKey = self._patientSeriesCacheKey(entry)
        cachedNodes = self.loadedPatientSeries.get(cacheKey)
        if cachedNodes and all(node and node.GetScene() for node in cachedNodes):
            return cachedNodes
        loadedNodes = self.logic.findLoadedPatientSeries(
            entry["patient_path"],
            entry["series_id"],
        )
        if loadedNodes:
            self.loadedPatientSeries[cacheKey] = loadedNodes
        return loadedNodes

    def _rememberSeriesRuntimeState(self, entry, nodes):
        if not entry or not nodes:
            return
        imageSequenceNode, segmentationSequenceNode, browserNode = nodes
        self.patientSeriesRuntimeState[self._patientSeriesCacheKey(entry)] = {
            "reviewed_count": self.logic.countReviewedFrames(segmentationSequenceNode),
            "corrected_count": len(self._dirtyFrameCandidates(segmentationSequenceNode)),
            "save_state": self._seriesSaveState(segmentationSequenceNode),
            "selected_index_value": (
                imageSequenceNode.GetNthIndexValue(browserNode.GetSelectedItemNumber())
                if 0 <= browserNode.GetSelectedItemNumber() < imageSequenceNode.GetNumberOfDataNodes()
                else None
            ),
        }

    def _detachSegmentEditorsFromSegmentation(self, segmentationNode):
        editorWidgets = [self.embeddedEditor] if self.embeddedEditor else []
        try:
            representation = slicer.modules.segmenteditor.widgetRepresentation()
            if representation:
                globalEditor = representation.self().editor
                if globalEditor and globalEditor not in editorWidgets:
                    editorWidgets.append(globalEditor)
        except (AttributeError, RuntimeError):
            logging.debug("Global Segment Editor widget is not available.")
        for editorWidget in editorWidgets:
            try:
                hasSegmentationGetter = hasattr(editorWidget, "segmentationNode")
                currentSegmentation = (
                    editorWidget.segmentationNode() if hasSegmentationGetter else None
                )
                if (
                    segmentationNode
                    and hasSegmentationGetter
                    and currentSegmentation != segmentationNode
                ):
                    continue
                editorWidget.setActiveEffectByName("")
                editorWidget.setSegmentationNode(None)
                editorWidget.setSourceVolumeNode(None)
                editorWidget.setUndoEnabled(False)
                editorWidget.setUndoEnabled(True)
            except RuntimeError:
                logging.debug("Segment Editor was already being destroyed.")

    def _unloadPatientSeries(self, entry, nodes=None):
        nodes = nodes or self._loadedPatientSeriesNodes(entry)
        if not nodes:
            self.loadedPatientSeries.pop(self._patientSeriesCacheKey(entry), None)
            return
        imageSequenceNode, segmentationSequenceNode, browserNode = nodes
        self._rememberSeriesRuntimeState(entry, nodes)
        proxyNodes = []
        proxyDisplayNodes = []
        for sequenceNode in [imageSequenceNode, segmentationSequenceNode]:
            proxyNode = browserNode.GetProxyNode(sequenceNode) if browserNode else None
            if proxyNode:
                proxyNodes.append(proxyNode)
                if proxyNode.IsA("vtkMRMLSegmentationNode"):
                    self.logic.setSegmentationNodeVisibility(proxyNode, False)
                    for displayIndex in range(proxyNode.GetNumberOfDisplayNodes()):
                        displayNode = proxyNode.GetNthDisplayNode(displayIndex)
                        if displayNode:
                            proxyDisplayNodes.append(displayNode)

        if browserNode == self.sequenceBrowserNode:
            self._observeBrowser(None)
            self._observeSegmentationProxy(None)
            segmentationProxy = browserNode.GetProxyNode(segmentationSequenceNode)
            self._detachSegmentEditorsFromSegmentation(segmentationProxy)
            self.playWidget.setMRMLSequenceBrowserNode(None)
            self.seekWidget.setMRMLSequenceBrowserNode(None)
            self.imageSequenceSelector.blockSignals(True)
            self.browserSelector.blockSignals(True)
            try:
                self.imageSequenceSelector.setCurrentNode(None)
                self.browserSelector.setCurrentNode(None)
            finally:
                self.imageSequenceSelector.blockSignals(False)
                self.browserSelector.blockSignals(False)
            self.imageSequenceNode = None
            self.segmentationSequenceNode = None
            self.sequenceBrowserNode = None

        for node in [browserNode] + proxyNodes + proxyDisplayNodes + [
            segmentationSequenceNode,
            imageSequenceNode,
        ]:
            if node and node.GetScene():
                slicer.mrmlScene.RemoveNode(node)
        self.loadedPatientSeries.pop(self._patientSeriesCacheKey(entry), None)

    def _unloadAllPatientSeries(self, exceptEntry=None):
        exceptKey = self._patientSeriesCacheKey(exceptEntry) if exceptEntry else None
        entriesByKey = {
            self._patientSeriesCacheKey(entry): entry for entry in self.patientSeriesEntries
        }
        for cacheKey, nodes in list(self.loadedPatientSeries.items()):
            if cacheKey == exceptKey:
                continue
            entry = entriesByKey.get(cacheKey, {
                "patient_path": cacheKey[0],
                "series_id": cacheKey[1],
            })
            self._unloadPatientSeries(entry, nodes)
        if exceptKey is None:
            self.activePatientSeriesIndex = -1

    def _patientSeriesCacheKey(self, entry):
        return (
            os.path.abspath(entry["patient_path"]),
            entry["series_id"],
        )

    def onStepPatientSeries(self, step):
        if not self.patientSeriesEntries:
            slicer.util.errorDisplay("请先扫描患者目录。")
            return
        currentIndex = max(0, self.patientSeriesComboBox.currentIndex)
        targetIndex = max(0, min(len(self.patientSeriesEntries) - 1, currentIndex + step))
        self.patientSeriesComboBox.setCurrentIndex(targetIndex)

    def onLoadNextUnreviewedSeries(self):
        if not self.patientSeriesEntries:
            slicer.util.errorDisplay("请先扫描患者目录。")
            return
        startIndex = max(-1, self.patientSeriesComboBox.currentIndex)
        for offset in range(1, len(self.patientSeriesEntries) + 1):
            targetIndex = (startIndex + offset) % len(self.patientSeriesEntries)
            entry = self.patientSeriesEntries[targetIndex]
            if entry.get("annotation_decision") == "not_required":
                continue
            loadedNodes = self._loadedPatientSeriesNodes(entry)
            reviewedCount = (
                self.logic.countReviewedFrames(loadedNodes[1])
                if loadedNodes
                else int(
                    self.patientSeriesRuntimeState.get(
                        self._patientSeriesCacheKey(entry),
                        {},
                    ).get("reviewed_count", 0)
                )
            )
            if entry["status"] == "ready" and reviewedCount < entry["image_frame_count"]:
                self.patientSeriesComboBox.setCurrentIndex(targetIndex)
                return
        slicer.util.infoDisplay("医生关注的所有 Series 均已审核完成。")

    def onLoadSelectedPatientSeries(self):
        if self.loadingPatientSeries:
            return
        selectedIndex = self.patientSeriesComboBox.currentIndex
        if selectedIndex < 0 or selectedIndex >= len(self.patientSeriesEntries):
            slicer.util.errorDisplay("请先扫描患者目录并选择一个 Series。")
            return
        if (
            self.activePatientSeriesIndex >= 0
            and selectedIndex != self.activePatientSeriesIndex
            and not self.suppressAutomaticSeriesLoad
            and not self._canLeaveCurrentSeries()
        ):
            self._restoreActiveSeriesSelection()
            return
        entry = self.patientSeriesEntries[selectedIndex]
        if entry["status"] != "ready":
            slicer.util.errorDisplay(
                "当前 Series 无法加载：{0}。MRI 帧数={1}，Mask 帧数={2}。".format(
                    self._statusDisplayText(entry["status"]),
                    entry["image_frame_count"],
                    entry["mask_frame_count"],
                )
            )
            return
        self.loadingPatientSeries = True
        try:
            with slicer.util.tryWithErrorDisplay("加载所选 Series 及其 Mask 失败。", waitCursor=True):
                sourceIndexValue = None
                switchingSeries = (
                    self.activePatientSeriesIndex >= 0
                    and selectedIndex != self.activePatientSeriesIndex
                )
                if (
                    self.syncPatientSeriesCheckBox.checked
                    and self.imageSequenceNode
                    and self.sequenceBrowserNode
                    and self.imageSequenceNode.GetAttribute("CineCMRQC.PatientRoot") == entry["patient_path"]
                ):
                    sourceFrameIndex = self.sequenceBrowserNode.GetSelectedItemNumber()
                    if 0 <= sourceFrameIndex < self.imageSequenceNode.GetNumberOfDataNodes():
                        try:
                            sourceIndexValue = float(
                                self.imageSequenceNode.GetNthIndexValue(sourceFrameIndex)
                            )
                        except (TypeError, ValueError):
                            sourceIndexValue = None
                if switchingSeries:
                    currentEntry = self.patientSeriesEntries[self.activePatientSeriesIndex]
                    self._unloadPatientSeries(currentEntry)
                    self.activePatientSeriesIndex = -1
                cachedNodes = self._loadedPatientSeriesNodes(entry)
                if cachedNodes and all(node and node.GetScene() for node in cachedNodes):
                    imageSequenceNode, segmentationSequenceNode, browserNode = cachedNodes
                else:
                    labels = entry.get("label_map") or self.logic.parseLabelMap(self.labelMapEdit.text)
                    imageSequenceNode = self.logic.loadVolumeSequenceFromPath(
                        entry["image_path"],
                        entry["series_id"] + "Image",
                        labelmap=False,
                        indexValues=entry["time_index_values"],
                        indexName=entry["time_index_name"],
                        indexUnit=entry["time_index_unit"],
                    )
                    browserNode = self.logic.getOrCreateBrowserForSequence(imageSequenceNode)
                    if entry["mask_frame_count"]:
                        segmentationSequenceNode = self.logic.loadSegmentationSequenceFromMaskPath(
                            entry["mask_path"],
                            imageSequenceNode,
                            browserNode,
                            labels,
                            entry["series_id"] + "Mask",
                            self.useImageGeometryCheckBox.checked,
                            self.autoDetectLabelsCheckBox.checked,
                            (
                                self.flipMasksLeftRightCheckBox.checked
                                or entry["mask_orientation"] == "legacy-left-right-flipped"
                            ),
                        )
                        segmentationSequenceNode.SetAttribute(
                            "CineCMRQC.SourceMaskExists",
                            "1",
                        )
                    else:
                        segmentationSequenceNode = self.logic.createEmptySegmentationSequence(
                            imageSequenceNode,
                            browserNode,
                            labels,
                            entry["series_id"] + "MaskEmpty",
                        )
                        segmentationSequenceNode.SetAttribute(
                            "CineCMRQC.SourceMaskExists",
                            "0",
                        )
                    self.logic.bindSegmentationSequence(segmentationSequenceNode, browserNode)
                    for node in [imageSequenceNode, segmentationSequenceNode, browserNode]:
                        node.SetAttribute("CineCMRQC.PatientRoot", entry["patient_path"])
                        node.SetAttribute("CineCMRQC.SeriesID", entry["series_id"])
                        node.SetAttribute(
                            "CineCMRQC.DoctorReferenceFrames",
                            ",".join(str(value) for value in entry["reference_frames"]),
                        )
                    self.loadedPatientSeries[self._patientSeriesCacheKey(entry)] = (
                        imageSequenceNode,
                        segmentationSequenceNode,
                        browserNode,
                    )

                projectLabels = entry.get("label_map") or {}
                if projectLabels:
                    self.logic.applySegmentNames(segmentationSequenceNode, projectLabels)
                segmentationSequenceNode.SetAttribute(
                    "CineCMRQC.SourceMaskOrientation",
                    entry["mask_orientation"],
                )
                segmentationSequenceNode.SetAttribute(
                    "CineCMRQC.SourceMaskFolder",
                    os.path.abspath(entry["mask_path"]),
                )
                auditRecord = self.logic.patientSeriesAuditRecord(
                    entry["patient_path"],
                    entry["series_id"],
                )
                if not segmentationSequenceNode.GetAttribute(
                    "CineCMRQC.RequireCardiacPhaseConfirmation"
                ):
                    if entry["mask_frame_count"]:
                        self.logic.initializeCardiacPhaseState(
                            segmentationSequenceNode,
                            imageSequenceNode,
                            entry["mask_path"],
                        )
                    else:
                        self.logic.initializeCardiacPhaseStateWithoutMask(
                            segmentationSequenceNode,
                            auditRecord,
                        )
                self.logic.restoreAnnotationDecisionState(
                    segmentationSequenceNode,
                    auditRecord,
                    hasSourceMask=bool(entry["mask_frame_count"]),
                )

                self.imageSequenceSelector.setCurrentNode(imageSequenceNode)
                self._setActiveImageSequence(imageSequenceNode, browserNode)
                self.segmentationSequenceNode = segmentationSequenceNode
                effectiveLabelMap = segmentationSequenceNode.GetAttribute("CineCMRQC.LabelMap")
                if effectiveLabelMap:
                    self.labelMapEdit.text = effectiveLabelMap
                appliedMaskTransform = segmentationSequenceNode.GetAttribute(
                    "CineCMRQC.AppliedMaskTransform"
                ) or "none"
                self.exportPrefixEdit.text = entry["series_id"] + "_corrected"
                self.exportPathEdit.currentPath = entry["mask_path"]
                self.exportPathEdit.toolTip = (
                    "默认写回加载时的 Mask 目录。保存前会自动备份原始文件。"
                )
                self.logic.showSegmentationSequence(segmentationSequenceNode, browserNode)
                if sourceIndexValue is not None:
                    targetFrame = self.logic.findClosestSequenceItem(
                        imageSequenceNode,
                        sourceIndexValue,
                    )
                else:
                    targetFrame = entry["reference_frames"][0] if entry["reference_frames"] else 0
                browserNode.SetSelectedItemNumber(targetFrame)
                slicer.modules.sequences.logic().UpdateProxyNodesFromSequences(browserNode)
                self.configureEmbeddedSegmentEditor()
                self.activePatientSeriesIndex = selectedIndex
                self._unloadAllPatientSeries(exceptEntry=entry)
                self._log(
                    "已自动加载 {0}：MRI/Mask 共 {1} 帧；医生参考帧：{2}；"
                    "Mask 方向={3}；已应用变换={4}。".format(
                        entry["series_id"],
                        entry["image_frame_count"],
                        entry["reference_frames"],
                        entry["mask_orientation"],
                        appliedMaskTransform,
                    )
                )
                self._updateStatus()
                self._recordCurrentSeriesAudit(event="open")
                self._refreshPatientSeriesList(True)
        except Exception as exc:
            slicer.util.errorDisplay(str(exc))
        finally:
            self.loadingPatientSeries = False

    def onLoadImageSequence(self):
        path = self.imagePathEdit.currentPath
        if not path:
            slicer.util.errorDisplay("请先选择 MRI 文件夹或文件。")
            return
        try:
            with slicer.util.tryWithErrorDisplay("加载 MRI Sequence 失败。", waitCursor=True):
                sequenceNode = self.logic.loadVolumeSequenceFromPath(path, "CineImage", labelmap=False)
                browserNode = self.logic.getOrCreateBrowserForSequence(sequenceNode)
                self._setActiveImageSequence(sequenceNode, browserNode)
                self._log("已加载 MRI Sequence，共 {0} 帧：{1}".format(
                    sequenceNode.GetNumberOfDataNodes(), sequenceNode.GetName()))
        except Exception as exc:
            slicer.util.errorDisplay(str(exc))

    def _setActiveImageSequence(self, sequenceNode, browserNode):
        self.logic.validateImageSequence(sequenceNode)
        self.logic.setActiveCineSegmentationVisibility(None, None)
        self.imageSequenceNode = sequenceNode
        self.browserSelector.blockSignals(True)
        self.browserSelector.setCurrentNode(browserNode)
        self.browserSelector.blockSignals(False)
        self._setBrowserNode(browserNode)
        self.logic.showBrowser(browserNode)
        self.logic.showImageSequence(sequenceNode, browserNode)
        self._updateStatus()

    def onLoadDemo(self):
        labels = self.logic.parseLabelMap(self.labelMapEdit.text)
        try:
            with slicer.util.tryWithErrorDisplay("创建合成心跳演示失败。", waitCursor=True):
                imageSequenceNode, segmentationSequenceNode, browserNode = self.logic.createSyntheticCineDemo(
                    labels,
                )
                self.imageSequenceSelector.setCurrentNode(imageSequenceNode)
                self._setActiveImageSequence(imageSequenceNode, browserNode)
                self.segmentationSequenceNode = segmentationSequenceNode
                self.logic.showSegmentationSequence(segmentationSequenceNode, browserNode)
                self.configureEmbeddedSegmentEditor()
                self._log(
                    "合成演示已加载：{0} 帧 MRI 与 Mask 已同步。".format(
                        imageSequenceNode.GetNumberOfDataNodes()
                    )
                )
                self._updateStatus()
        except Exception as exc:
            slicer.util.errorDisplay(str(exc))

    def onLoadMaskSequence(self):
        if not self.imageSequenceNode or not self.sequenceBrowserNode:
            slicer.util.errorDisplay("请先加载或选择 MRI Sequence。")
            return
        path = self.maskPathEdit.currentPath
        if not path:
            slicer.util.errorDisplay("请先选择 Mask 文件夹或文件。")
            return
        labels = self.logic.parseLabelMap(self.labelMapEdit.text)
        try:
            with slicer.util.tryWithErrorDisplay("加载 Mask Sequence 失败。", waitCursor=True):
                segmentationSequenceNode = self.logic.loadSegmentationSequenceFromMaskPath(
                    path,
                    self.imageSequenceNode,
                    self.sequenceBrowserNode,
                    labels,
                    "CineMask",
                    self.useImageGeometryCheckBox.checked,
                    self.autoDetectLabelsCheckBox.checked,
                    self.flipMasksLeftRightCheckBox.checked,
                )
                self.segmentationSequenceNode = segmentationSequenceNode
                effectiveLabelMap = segmentationSequenceNode.GetAttribute("CineCMRQC.LabelMap")
                if effectiveLabelMap:
                    self.labelMapEdit.text = effectiveLabelMap
                self.logic.bindSegmentationSequence(segmentationSequenceNode, self.sequenceBrowserNode)
                self.logic.showSegmentationSequence(segmentationSequenceNode, self.sequenceBrowserNode)
                self.configureEmbeddedSegmentEditor()
                self._log("Mask Sequence 已加载并绑定，共 {0} 帧。".format(
                    segmentationSequenceNode.GetNumberOfDataNodes()))
                self._updateStatus()
        except Exception as exc:
            slicer.util.errorDisplay(str(exc))

    def onCreateEmptyMaskSequence(self):
        if not self.imageSequenceNode or not self.sequenceBrowserNode:
            slicer.util.errorDisplay("请先加载或选择 MRI Sequence。")
            return
        labels = self.logic.parseLabelMap(self.labelMapEdit.text)
        try:
            segmentationSequenceNode = self.logic.createEmptySegmentationSequence(
                self.imageSequenceNode,
                self.sequenceBrowserNode,
                labels,
                "CineMaskEmpty",
            )
            self.segmentationSequenceNode = segmentationSequenceNode
            self.logic.bindSegmentationSequence(segmentationSequenceNode, self.sequenceBrowserNode)
            self.logic.showSegmentationSequence(segmentationSequenceNode, self.sequenceBrowserNode)
            self.configureEmbeddedSegmentEditor()
            self._log("已创建逐帧空白可编辑 Mask。")
            self._updateStatus()
        except Exception as exc:
            slicer.util.errorDisplay("创建空白 Mask Sequence 失败：{0}".format(exc))

    def onBrowserSelected(self, browserNode):
        self._setBrowserNode(browserNode)

    def _setBrowserNode(self, browserNode):
        self.sequenceBrowserNode = browserNode
        self.playWidget.setMRMLSequenceBrowserNode(browserNode)
        self.seekWidget.setMRMLSequenceBrowserNode(browserNode)
        self._observeBrowser(browserNode)
        if browserNode:
            master = browserNode.GetMasterSequenceNode()
            if master:
                self.imageSequenceNode = master
            segmentationSequenceNode = self.logic.findSegmentationSequence(browserNode)
            self.segmentationSequenceNode = segmentationSequenceNode
            if self.segmentationSequenceNode:
                self.configureEmbeddedSegmentEditor()
                self.logic.showSegmentationSequence(
                    self.segmentationSequenceNode,
                    browserNode,
                )
        else:
            self.imageSequenceNode = None
            self.segmentationSequenceNode = None
        self._updateStatus()

    def _observeBrowser(self, browserNode):
        if self.observedBrowserNode and self.browserObserverTag:
            self.observedBrowserNode.RemoveObserver(self.browserObserverTag)
        self.observedBrowserNode = browserNode
        self.browserObserverTag = None
        self.lastSelectedItemNumber = -1
        if browserNode:
            self.browserObserverTag = browserNode.AddObserver(vtk.vtkCommand.ModifiedEvent, self._onBrowserModified)
            self.lastSelectedItemNumber = browserNode.GetSelectedItemNumber()

    def _onBrowserModified(self, caller, event):
        if not self.sequenceBrowserNode:
            return
        selectedItemNumber = self.sequenceBrowserNode.GetSelectedItemNumber()
        if selectedItemNumber != self.lastSelectedItemNumber:
            self.lastSelectedItemNumber = selectedItemNumber
            self.configureEmbeddedSegmentEditor()
            self._updateStatus()
            self._synchronizeLoadedPatientSeries()

    def _observeSliceViewMouseWheels(self):
        self._removeSliceViewMouseWheelObservers()
        layoutManager = slicer.app.layoutManager()
        if not layoutManager:
            return
        self.observedLayoutManager = layoutManager
        layoutManager.layoutChanged.connect(self._onLayoutChanged)
        for viewName in layoutManager.sliceViewNames():
            sliceWidget = layoutManager.sliceWidget(viewName)
            if not sliceWidget:
                continue
            interactor = sliceWidget.sliceView().interactorStyle().GetInteractor()
            if not interactor:
                continue
            forwardCallback = (
                lambda caller, event, step=1: self._onSliceViewMouseWheel(
                    caller,
                    event,
                    step,
                )
            )
            backwardCallback = (
                lambda caller, event, step=-1: self._onSliceViewMouseWheel(
                    caller,
                    event,
                    step,
                )
            )
            forwardTag = interactor.AddObserver(
                vtk.vtkCommand.MouseWheelForwardEvent,
                forwardCallback,
                1.0,
            )
            backwardTag = interactor.AddObserver(
                vtk.vtkCommand.MouseWheelBackwardEvent,
                backwardCallback,
                1.0,
            )
            self.sliceWheelObservers.extend([
                (interactor, forwardTag, forwardCallback),
                (interactor, backwardTag, backwardCallback),
            ])

    def _removeSliceViewMouseWheelObservers(self):
        if self.observedLayoutManager:
            try:
                self.observedLayoutManager.layoutChanged.disconnect(
                    self._onLayoutChanged
                )
            except (RuntimeError, TypeError):
                pass
        self.observedLayoutManager = None
        for interactor, observerTag, callback in self.sliceWheelObservers:
            try:
                interactor.RemoveObserver(observerTag)
            except RuntimeError:
                pass
        self.sliceWheelObservers = []

    def _onLayoutChanged(self, *args):
        qt.QTimer.singleShot(0, self._observeSliceViewMouseWheels)

    def _onSliceViewMouseWheel(self, caller, event, step):
        if not self.sequenceBrowserNode or not self.imageSequenceNode:
            return
        if slicer.util.selectedModule() != "CineCMRQC":
            return
        if qt.QApplication.keyboardModifiers() != qt.Qt.NoModifier:
            return
        proxyVolume = self.sequenceBrowserNode.GetProxyNode(
            self.imageSequenceNode
        )
        imageData = proxyVolume.GetImageData() if proxyVolume else None
        if not imageData or imageData.GetDimensions()[2] != 1:
            return
        numberOfItems = self.sequenceBrowserNode.GetNumberOfItems()
        if numberOfItems < 2:
            return
        currentFrame = self.sequenceBrowserNode.GetSelectedItemNumber()
        nextFrame = (currentFrame + int(step)) % numberOfItems
        self.sequenceBrowserNode.SetSelectedItemNumber(nextFrame)
        slicer.modules.sequences.logic().UpdateProxyNodesFromSequences(
            self.sequenceBrowserNode
        )

    def _installSegmentEditorShortcuts(self):
        self._removeSegmentEditorShortcuts()
        parent = slicer.util.mainWindow()
        if not parent:
            return
        modifier = "Meta" if sys.platform == "darwin" else "Ctrl"
        for key, effectName in [("D", "Paint"), ("F", "Erase")]:
            shortcut = qt.QShortcut(
                qt.QKeySequence("{0}+{1}".format(modifier, key)),
                parent,
            )
            shortcut.setContext(qt.Qt.ApplicationShortcut)
            callback = (
                lambda effectName=effectName: self._activateSegmentEditorEffect(
                    effectName
                )
            )
            shortcut.activated.connect(callback)
            self.segmentEditorShortcuts.append((shortcut, callback, effectName))

    def _removeSegmentEditorShortcuts(self):
        for shortcut, callback, effectName in self.segmentEditorShortcuts:
            try:
                shortcut.activated.disconnect(callback)
            except (RuntimeError, TypeError):
                pass
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        self.segmentEditorShortcuts = []

    def _activateSegmentEditorEffect(self, effectName):
        if slicer.util.selectedModule() != "CineCMRQC":
            return
        if not self.segmentationSequenceNode or not self.sequenceBrowserNode:
            self._log("请先加载 Mask Sequence，再使用编辑快捷键。")
            return
        self.configureEmbeddedSegmentEditor()
        self.embeddedEditor.setActiveEffectByName(effectName)
        activeEffect = self.embeddedEditor.activeEffect()
        if not activeEffect or activeEffect.name != effectName:
            slicer.util.errorDisplay(
                "无法激活 Segment Editor 工具：{0}".format(effectName)
            )

    def onStepFrame(self, step):
        if not self.sequenceBrowserNode:
            return
        numberOfItems = self.sequenceBrowserNode.GetNumberOfItems()
        if numberOfItems < 1:
            return
        nextIndex = max(0, min(numberOfItems - 1, self.sequenceBrowserNode.GetSelectedItemNumber() + step))
        self.sequenceBrowserNode.SetSelectedItemNumber(nextIndex)
        slicer.modules.sequences.logic().UpdateProxyNodesFromSequences(self.sequenceBrowserNode)
        self.configureEmbeddedSegmentEditor()
        self._updateStatus()

    def onReviewedToggled(self, reviewed):
        if self.updatingReviewCheckBox or not self.segmentationSequenceNode or not self.sequenceBrowserNode:
            return
        frameIndex = self.sequenceBrowserNode.GetSelectedItemNumber()
        if frameIndex < 0:
            return
        self._setFrameReviewState(frameIndex, reviewed)
        self._updateStatus()
        if self.patientSeriesEntries:
            self._refreshPatientSeriesList(True)

    def onMarkReviewedAndNext(self):
        if not self.segmentationSequenceNode or not self.sequenceBrowserNode:
            slicer.util.errorDisplay("请先加载 Mask Sequence。")
            return
        frameIndex = self.sequenceBrowserNode.GetSelectedItemNumber()
        if frameIndex < 0:
            return
        self._setFrameReviewState(frameIndex, True)
        self._updateStatus()
        if self.patientSeriesEntries:
            self._refreshPatientSeriesList(True)
        if frameIndex + 1 < self.sequenceBrowserNode.GetNumberOfItems():
            self.sequenceBrowserNode.SetSelectedItemNumber(frameIndex + 1)
            slicer.modules.sequences.logic().UpdateProxyNodesFromSequences(self.sequenceBrowserNode)
        elif self.patientSeriesEntries:
            self.onLoadNextUnreviewedSeries()

    def _setFrameReviewState(self, frameIndex, reviewed):
        reviewer = self.reviewerEdit.text.strip()
        comment = self.reviewCommentEdit.text.strip()
        self.logic.setFrameReviewed(
            self.segmentationSequenceNode,
            frameIndex,
            reviewed,
            reviewer,
            comment,
        )
        self.segmentationSequenceNode.SetAttribute(
            "CineCMRQC.ReviewMetadataMayBeDirty",
            "1"
            if self.logic.isReviewMetadataDirty(self.segmentationSequenceNode)
            else "0",
        )
        if reviewed and reviewer:
            self.segmentationSequenceNode.SetAttribute(
                "CineCMRQC.DefaultReviewer",
                reviewer,
            )

    def _synchronizeLoadedPatientSeries(self):
        # Only the active series remains resident. Its current TriggerTime is
        # transferred when the doctor explicitly chooses another series.
        return

    def onOpenSegmentEditor(self):
        self.configureEmbeddedSegmentEditor()
        slicer.util.selectModule("SegmentEditor")

    def onFitCineView(self):
        if not self.imageSequenceNode or not self.sequenceBrowserNode:
            slicer.util.errorDisplay("请先加载 MRI Sequence。")
            return
        self.logic.showImageSequence(self.imageSequenceNode, self.sequenceBrowserNode)

    def configureEmbeddedSegmentEditor(self):
        if not self.segmentationSequenceNode or not self.sequenceBrowserNode:
            return
        proxySegmentation = self.sequenceBrowserNode.GetProxyNode(self.segmentationSequenceNode)
        proxyVolume = self.sequenceBrowserNode.GetProxyNode(self.imageSequenceNode) if self.imageSequenceNode else None
        if proxySegmentation:
            self.logic.configureSegmentEditor(proxySegmentation, proxyVolume, self.embeddedEditor)
        self._observeSegmentationProxy(proxySegmentation)

    def _observeSegmentationProxy(self, segmentationNode):
        if self.observedSegmentationProxy and self.segmentationObserverTag:
            self.observedSegmentationProxy.RemoveObserver(self.segmentationObserverTag)
        if self.observedSegmentationObject:
            for observerTag in self.segmentationObjectObserverTags:
                self.observedSegmentationObject.RemoveObserver(observerTag)
        self.observedSegmentationProxy = segmentationNode
        self.segmentationObserverTag = None
        self.observedSegmentationObject = None
        self.segmentationObjectObserverTags = []
        if segmentationNode:
            self.segmentationObserverTag = segmentationNode.AddObserver(
                vtk.vtkCommand.ModifiedEvent,
                self._onSegmentationProxyModified,
            )
            self.observedSegmentationObject = segmentationNode.GetSegmentation()
            segmentationEvents = [vtk.vtkCommand.ModifiedEvent]
            for eventName in ["SegmentModified", "RepresentationModified"]:
                if hasattr(slicer.vtkSegmentation, eventName):
                    segmentationEvents.append(getattr(slicer.vtkSegmentation, eventName))
            for eventId in sorted(set(segmentationEvents)):
                self.segmentationObjectObserverTags.append(
                    self.observedSegmentationObject.AddObserver(
                        eventId,
                        self._onSegmentationProxyModified,
                    )
                )

    def _onSegmentationProxyModified(self, caller, event):
        if caller not in [self.observedSegmentationProxy, self.observedSegmentationObject]:
            return
        if self.processingSegmentationProxyModified or self.updatingIncrementalDirtyState:
            return
        self.processingSegmentationProxyModified = True
        try:
            if self.sequenceBrowserNode and self.segmentationSequenceNode:
                frameIndex = self.sequenceBrowserNode.GetSelectedItemNumber()
                if frameIndex >= 0:
                    candidates = self._dirtyFrameCandidates()
                    candidates.add(frameIndex)
                    self._setDirtyFrameCandidates(candidates)
                    if (
                        self._annotationDecision() != "required"
                        and self.imageSequenceNode
                        and self.logic.isFrameCorrected(
                            self.segmentationSequenceNode,
                            self.imageSequenceNode,
                            self.sequenceBrowserNode,
                            frameIndex,
                        )
                    ):
                        self._markCurrentSeriesAnnotationRequired()
            self._updateStatus()
        except RuntimeError:
            logging.debug(
                "Skipped edit-state refresh while segmentation representation was unavailable.",
                exc_info=True,
            )
        finally:
            self.processingSegmentationProxyModified = False

    def onValidate(self):
        try:
            message = self.logic.validateBinding(
                self.imageSequenceNode,
                self.segmentationSequenceNode,
                self.sequenceBrowserNode,
            )
            self._log(message)
            slicer.util.infoDisplay(message)
        except Exception as exc:
            slicer.util.errorDisplay(str(exc))

    def onExportMasks(self):
        return self._saveCurrentSeries(showSuccess=True)

    def _saveCurrentSeries(self, showSuccess=True):
        if not self.imageSequenceNode or not self.segmentationSequenceNode or not self.sequenceBrowserNode:
            slicer.util.errorDisplay("导出前必须先加载并绑定 MRI 与 Mask Sequence。")
            return False
        if (
            self._annotationDecision() == "not_required"
            and self._annotationDecisionIsSaved()
        ):
            if showSuccess:
                slicer.util.infoDisplay(
                    "当前 Series 已保存为“无需标注”，没有需要写入的 Mask。"
                )
            return True
        self._markCurrentSeriesAnnotationRequired()
        phaseState = self.logic.getCardiacPhaseState(self.segmentationSequenceNode)
        if (
            self.segmentationSequenceNode.GetAttribute(
                "CineCMRQC.RequireCardiacPhaseConfirmation"
            ) == "1"
            and not phaseState["confirmed"]
        ):
            slicer.util.errorDisplay(
                "保存前必须由医生确认当前 Series 的舒张末期 ED 和收缩末期 ES。"
            )
            return False
        sourceMaskFolder = self.segmentationSequenceNode.GetAttribute(
            "CineCMRQC.SourceMaskFolder"
        ) or ""
        sourceMaskExists = self.segmentationSequenceNode.GetAttribute(
            "CineCMRQC.SourceMaskExists"
        ) != "0"
        outputFolder = self.exportPathEdit.currentPath
        if not outputFolder and not sourceMaskExists and sourceMaskFolder:
            outputFolder = sourceMaskFolder
        if not outputFolder:
            slicer.util.errorDisplay("请先选择输出目录。")
            return False
        prefix = self.exportPrefixEdit.text.strip() or "corrected_mask"
        modifiedFrameIndices = [
            frameIndex
            for frameIndex in range(
                self.segmentationSequenceNode.GetNumberOfDataNodes()
            )
            if self.logic.isFrameCorrected(
                self.segmentationSequenceNode,
                self.imageSequenceNode,
                self.sequenceBrowserNode,
                frameIndex,
            )
        ]
        try:
            with slicer.util.tryWithErrorDisplay("导出 Mask 失败。", waitCursor=True):
                if not sourceMaskExists and sourceMaskFolder:
                    manifestPath = self.logic.createSegmentationSequenceSourceFiles(
                        self.segmentationSequenceNode,
                        self.imageSequenceNode,
                        self.sequenceBrowserNode,
                        sourceMaskFolder,
                    )
                    self.logic.setAnnotationDecisionState(
                        self.segmentationSequenceNode,
                        "required",
                        saved=True,
                    )
                    self._markActiveEntryMaskSaved()
                    self.segmentationSequenceNode.SetAttribute(
                        "CineCMRQC.DirtyFrameCandidates",
                        "",
                    )
                    self.segmentationSequenceNode.SetAttribute(
                        "CineCMRQC.MaskMayBeDirty",
                        "0",
                    )
                    self.logic.initializeReviewMetadataBaseline(
                        self.segmentationSequenceNode
                    )
                    auditPath = self._recordCurrentSeriesAudit(
                        event="save",
                        modifiedFrameIndices=modifiedFrameIndices,
                    )
                    self._log("新 Mask 已创建：{0}".format(manifestPath))
                    if showSuccess:
                        slicer.util.infoDisplay(
                            "{0} 保存完成，已创建新的 Mask。\n\nSeries 记录：\n{1}\n\n患者 JSON：\n{2}".format(
                                self._currentSeriesId(),
                                manifestPath,
                                auditPath or "写入失败，请查看错误提示",
                            )
                        )
                elif (
                    sourceMaskFolder
                    and os.path.abspath(outputFolder) == os.path.abspath(sourceMaskFolder)
                ):
                    manifestPath, backupFolder = (
                        self.logic.overwriteSegmentationSequenceSourceFiles(
                            self.segmentationSequenceNode,
                            self.imageSequenceNode,
                            self.sequenceBrowserNode,
                            outputFolder,
                        )
                    )
                    self._log("原 Mask 已覆盖；备份目录：{0}".format(backupFolder))
                    self.segmentationSequenceNode.SetAttribute(
                        "CineCMRQC.MaskMayBeDirty",
                        "0",
                    )
                    self.segmentationSequenceNode.SetAttribute(
                        "CineCMRQC.DirtyFrameCandidates",
                        "",
                    )
                    self.logic.initializeReviewMetadataBaseline(
                        self.segmentationSequenceNode
                    )
                    self.logic.setAnnotationDecisionState(
                        self.segmentationSequenceNode,
                        "required",
                        saved=True,
                    )
                    self._markActiveEntryMaskSaved()
                    auditPath = self._recordCurrentSeriesAudit(
                        event="save",
                        modifiedFrameIndices=modifiedFrameIndices,
                    )
                    if showSuccess:
                        slicer.util.infoDisplay(
                            "{0} 保存完成。\n\n原文件已备份到：\n{1}\n\n"
                            "Series 记录：\n{2}\n\n患者 JSON：\n{3}".format(
                                self._currentSeriesId(),
                                backupFolder,
                                manifestPath,
                                auditPath or "写入失败，请查看错误提示",
                            )
                        )
                else:
                    manifestPath = self.logic.exportSegmentationSequenceAsLabelmaps(
                        self.segmentationSequenceNode,
                        self.imageSequenceNode,
                        self.sequenceBrowserNode,
                        outputFolder,
                        prefix,
                    )
                    self._log("当前 Series 导出完成：{0}".format(manifestPath))
                    if showSuccess:
                        slicer.util.infoDisplay("导出完成：\n{0}".format(manifestPath))
                    self.segmentationSequenceNode.SetAttribute(
                        "CineCMRQC.DirtyFrameCandidates",
                        "",
                    )
                    self.segmentationSequenceNode.SetAttribute(
                        "CineCMRQC.MaskMayBeDirty",
                        "0",
                    )
                    self.logic.initializeReviewMetadataBaseline(
                        self.segmentationSequenceNode
                    )
            self._updateStatus()
            if self.patientSeriesEntries:
                self._refreshPatientSeriesList(True)
            return True
        except Exception as exc:
            slicer.util.errorDisplay(str(exc))
            return False

    def _markActiveEntryMaskSaved(self):
        if not 0 <= self.activePatientSeriesIndex < len(self.patientSeriesEntries):
            return
        entry = self.patientSeriesEntries[self.activePatientSeriesIndex]
        entry["mask_frame_count"] = entry["image_frame_count"]
        entry["mask_orientation"] = "aligned"
        entry["status"] = "ready"
        entry["annotation_decision"] = "required"

    def onExportAllPatientSeries(self):
        if not self.patientSeriesEntries:
            slicer.util.errorDisplay("请先扫描患者目录。")
            return
        if self.imageSequenceNode and not self._canLeaveCurrentSeries():
            return
        outputFolder = self.exportPathEdit.currentPath
        if not outputFolder:
            slicer.util.errorDisplay("请先选择输出目录。")
            return
        originalSeriesIndex = self.patientSeriesComboBox.currentIndex
        previousSuppressAutomaticLoad = self.suppressAutomaticSeriesLoad
        self.suppressAutomaticSeriesLoad = True
        try:
            with slicer.util.tryWithErrorDisplay(
                "批量导出患者 Series 失败。",
                waitCursor=True,
            ):
                manifestPaths = []
                for entryIndex, entry in enumerate(self.patientSeriesEntries):
                    if (
                        entry["status"] != "ready"
                        or int(entry.get("mask_frame_count") or 0) == 0
                        or entry.get("annotation_decision") == "not_required"
                    ):
                        continue
                    self.patientSeriesComboBox.setCurrentIndex(entryIndex)
                    self.onLoadSelectedPatientSeries()
                    loadedNodes = self._loadedPatientSeriesNodes(entry)
                    if not loadedNodes:
                        raise ValueError(
                            "无法加载可用 Series：{0}".format(entry["series_id"])
                        )
                    safeSeriesId = re.sub(
                        r"[^A-Za-z0-9_.-]+",
                        "_",
                        entry["series_id"],
                    ).strip("._") or "series"
                    seriesFolder = os.path.join(outputFolder, safeSeriesId)
                    manifestPaths.append(
                        self.logic.exportSegmentationSequenceAsLabelmaps(
                            loadedNodes[1],
                            loadedNodes[0],
                            loadedNodes[2],
                            seriesFolder,
                            safeSeriesId + "_corrected",
                        )
                    )
                    slicer.app.processEvents()
                if not manifestPaths:
                    raise ValueError("没有可供导出的医生关注 Series。")
                manifestPath = self.logic.combinePatientSeriesManifests(
                    manifestPaths,
                    outputFolder,
                )
                self._log(
                    "患者批量导出完成：{0} 个 Series；{1}".format(
                        len(manifestPaths),
                        manifestPath,
                    )
                )
                slicer.util.infoDisplay(
                    "患者批量导出完成（{0} 个 Series）：\n{1}".format(
                        len(manifestPaths),
                        manifestPath,
                    )
                )
        except Exception as exc:
            slicer.util.errorDisplay(str(exc))
        finally:
            if 0 <= originalSeriesIndex < len(self.patientSeriesEntries):
                self.patientSeriesComboBox.setCurrentIndex(originalSeriesIndex)
            self.suppressAutomaticSeriesLoad = previousSuppressAutomaticLoad
            if 0 <= originalSeriesIndex < len(self.patientSeriesEntries):
                self.onLoadSelectedPatientSeries()

    def _updateStatus(self):
        browser = self.sequenceBrowserNode
        if not browser:
            self.statusLabel.text = "尚未选择动态序列。"
            self._refreshCardiacPhaseControls()
            self._updateSaveControls()
            return
        selected = browser.GetSelectedItemNumber()
        total = browser.GetNumberOfItems()
        imageName = self.imageSequenceNode.GetName() if self.imageSequenceNode else "None"
        maskName = self.segmentationSequenceNode.GetName() if self.segmentationSequenceNode else "None"
        reviewedCount = 0
        currentReviewed = False
        currentCorrected = False
        reviewMetadata = {"reviewer": "", "timestamp": "", "comment": ""}
        if self.segmentationSequenceNode:
            reviewedCount = self.logic.countReviewedFrames(self.segmentationSequenceNode)
            if 0 <= selected < self.segmentationSequenceNode.GetNumberOfDataNodes():
                currentReviewed = self.logic.isFrameReviewed(self.segmentationSequenceNode, selected)
                reviewMetadata = self.logic.getFrameReviewMetadata(
                    self.segmentationSequenceNode,
                    selected,
                )
                if self.imageSequenceNode:
                    currentCorrected = self.logic.isFrameCorrected(
                        self.segmentationSequenceNode,
                        self.imageSequenceNode,
                        browser,
                        selected,
                    )
                    dirtyCandidates = self._dirtyFrameCandidates()
                    if currentCorrected:
                        dirtyCandidates.add(selected)
                    else:
                        dirtyCandidates.discard(selected)
                    self._setDirtyFrameCandidates(dirtyCandidates)
        self.updatingReviewCheckBox = True
        try:
            self.reviewedCheckBox.checked = currentReviewed
        finally:
            self.updatingReviewCheckBox = False
        self.updatingReviewMetadata = True
        try:
            defaultReviewer = ""
            if self.segmentationSequenceNode:
                defaultReviewer = self.segmentationSequenceNode.GetAttribute(
                    "CineCMRQC.DefaultReviewer"
                ) or ""
            if reviewMetadata["reviewer"]:
                self.reviewerEdit.text = reviewMetadata["reviewer"]
            elif defaultReviewer:
                self.reviewerEdit.text = defaultReviewer
            self.reviewCommentEdit.text = reviewMetadata["comment"]
        finally:
            self.updatingReviewMetadata = False
        if reviewMetadata["timestamp"]:
            self.reviewMetadataLabel.text = "{0}，时间：{1}".format(
                reviewMetadata["reviewer"] or "未填写审核人",
                reviewMetadata["timestamp"],
            )
        else:
            self.reviewMetadataLabel.text = "当前帧暂无审核记录。"
        self.reviewProgressLabel.text = "已审核：{0}/{1}".format(reviewedCount, total)
        self.correctedStateLabel.text = "当前帧已修改：{0}".format(
            "是" if currentCorrected else "否"
        )
        self.statusLabel.text = "序列：{0} | 帧：{1}/{2} | MRI：{3} | Mask：{4}".format(
            browser.GetName(), selected + 1 if total else 0, total, imageName, maskName)
        self._refreshCardiacPhaseControls()
        self._updateSaveControls()


class CineCMRQCLogic(ScriptedLoadableModuleLogic):
    """Non-GUI logic for cine CMR sequence and mask QC."""

    SUPPORTED_EXTENSIONS = [
        ".nii.gz",
        ".nii",
        ".nrrd",
        ".nhdr",
        ".mha",
        ".mhd",
    ]
    HUAXI_LABELS = {
        180: "心腔",
        255: "心肌",
    }

    def parseLabelMap(self, text):
        labels = {}
        for item in text.split(","):
            item = item.strip()
            if not item:
                continue
            if ":" not in item:
                raise ValueError("Invalid label item '{0}'. Expected format such as 1:LV.".format(item))
            valueText, name = item.split(":", 1)
            value = int(valueText.strip())
            name = name.strip()
            if value <= 0:
                raise ValueError("Label value must be positive: {0}".format(value))
            if not name:
                raise ValueError("Label name cannot be empty for value {0}".format(value))
            labels[value] = name
        if not labels:
            raise ValueError("At least one label is required, for example 1:LV.")
        return labels

    def getOrCreateBrowserForSequence(self, sequenceNode):
        if sequenceNode is None:
            raise ValueError("Sequence node is invalid.")
        existingBrowser = slicer.modules.sequences.logic().GetFirstBrowserNodeForSequenceNode(sequenceNode)
        if existingBrowser:
            return existingBrowser
        browserNode = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLSequenceBrowserNode",
            slicer.mrmlScene.GetUniqueNameByString(sequenceNode.GetName() + " browser"),
        )
        browserNode.SetAndObserveMasterSequenceNodeID(sequenceNode.GetID())
        browserNode.SetSaveChanges(sequenceNode, False)
        slicer.modules.sequences.logic().UpdateProxyNodesFromSequences(browserNode)
        return browserNode

    def validateImageSequence(self, sequenceNode):
        if not sequenceNode or not sequenceNode.IsA("vtkMRMLSequenceNode"):
            raise ValueError("A vtkMRMLSequenceNode is required.")
        frameCount = sequenceNode.GetNumberOfDataNodes()
        if frameCount < 1:
            raise ValueError("The selected image sequence has no frames.")
        for frameIndex in range(frameCount):
            frameNode = sequenceNode.GetNthDataNode(frameIndex)
            if not frameNode or not frameNode.IsA("vtkMRMLScalarVolumeNode"):
                nodeType = frameNode.GetClassName() if frameNode else "None"
                raise ValueError(
                    "The selected sequence is not a cine image Volume Sequence: "
                    "frame {0} is {1}.".format(frameIndex, nodeType)
                )
        return frameCount

    def showBrowser(self, browserNode):
        if hasattr(slicer.modules.sequences, "showSequenceBrowser"):
            slicer.modules.sequences.showSequenceBrowser(browserNode)
        if hasattr(slicer.modules.sequences, "setToolBarActiveBrowserNode"):
            slicer.modules.sequences.setToolBarActiveBrowserNode(browserNode)
        elif hasattr(slicer.modules.sequences, "toolBar"):
            slicer.modules.sequences.toolBar().setActiveBrowserNode(browserNode)
        if hasattr(slicer.modules.sequences, "setToolBarVisible"):
            slicer.modules.sequences.setToolBarVisible(True)

    def showImageSequence(self, imageSequenceNode, browserNode):
        slicer.modules.sequences.logic().UpdateProxyNodesFromSequences(browserNode)
        proxyVolume = browserNode.GetProxyNode(imageSequenceNode)
        if proxyVolume:
            slicer.util.setSliceViewerLayers(background=proxyVolume)
            self.configureCineSliceView(proxyVolume)

    def configureCineSliceView(self, volumeNode):
        layoutManager = slicer.app.layoutManager()
        if not layoutManager or not volumeNode:
            return
        layoutManager.setLayout(slicer.vtkMRMLLayoutNode.SlicerLayoutOneUpRedSliceView)
        sliceWidget = layoutManager.sliceWidget("Red")
        if not sliceWidget:
            return
        sliceNode = sliceWidget.mrmlSliceNode()
        sliceNode.RotateToVolumePlane(volumeNode, True)
        sliceWidget.sliceLogic().FitSliceToAll()

    def showSegmentationSequence(self, segmentationSequenceNode, browserNode):
        slicer.modules.sequences.logic().UpdateProxyNodesFromSequences(browserNode)
        proxySegmentation = browserNode.GetProxyNode(segmentationSequenceNode)
        if proxySegmentation:
            self._tagManagedSegmentationProxy(
                proxySegmentation,
                segmentationSequenceNode,
                browserNode,
            )
        self.setActiveCineSegmentationVisibility(
            segmentationSequenceNode,
            browserNode,
        )
        if proxySegmentation:
            proxySegmentation.CreateDefaultDisplayNodes()
            displayNode = proxySegmentation.GetDisplayNode()
            if displayNode:
                self.setSegmentationNodeVisibility(proxySegmentation, True)
                displayNode.SetOpacity2DFill(0.45)
                displayNode.SetOpacity2DOutline(1.0)

    def _tagManagedSegmentationProxy(
        self,
        proxySegmentation,
        segmentationSequenceNode=None,
        browserNode=None,
    ):
        if not proxySegmentation:
            return
        proxySegmentation.SetAttribute("CineCMRQC.ManagedSegmentationProxy", "1")
        for attributeName in ["CineCMRQC.PatientRoot", "CineCMRQC.SeriesID"]:
            value = ""
            for sourceNode in [segmentationSequenceNode, browserNode]:
                if sourceNode and sourceNode.GetAttribute(attributeName):
                    value = sourceNode.GetAttribute(attributeName)
                    break
            if value:
                proxySegmentation.SetAttribute(attributeName, value)
        proxySegmentation.CreateDefaultDisplayNodes()
        for displayIndex in range(proxySegmentation.GetNumberOfDisplayNodes()):
            displayNode = proxySegmentation.GetNthDisplayNode(displayIndex)
            if displayNode:
                displayNode.SetAttribute("CineCMRQC.ManagedSegmentationDisplay", "1")

    def _isManagedCineSegmentation(self, segmentationNode, browserProxyIds=None):
        if not segmentationNode:
            return False
        if segmentationNode.GetAttribute("CineCMRQC.ManagedSegmentationProxy") == "1":
            return True
        if browserProxyIds and segmentationNode.GetID() in browserProxyIds:
            return True
        segmentation = segmentationNode.GetSegmentation()
        if not segmentation:
            return False
        return any(
            segmentation.GetSegment(self._stableSegmentId(labelValue)) is not None
            for labelValue in self.HUAXI_LABELS
        )

    def setSegmentationNodeVisibility(self, segmentationNode, visible):
        if not segmentationNode:
            return
        segmentationNode.CreateDefaultDisplayNodes()
        for displayIndex in range(segmentationNode.GetNumberOfDisplayNodes()):
            displayNode = segmentationNode.GetNthDisplayNode(displayIndex)
            if not displayNode:
                continue
            displayNode.SetVisibility(bool(visible))
            if hasattr(displayNode, "SetVisibility2D"):
                displayNode.SetVisibility2D(bool(visible))
            if hasattr(displayNode, "SetVisibility3D"):
                displayNode.SetVisibility3D(bool(visible))
            displayNode.SetAttribute("CineCMRQC.ManagedSegmentationDisplay", "1")

    def setActiveCineSegmentationVisibility(
        self,
        activeSegmentationSequenceNode,
        activeBrowserNode,
    ):
        activeProxy = None
        activeDisplayNodeIds = set()
        if activeSegmentationSequenceNode and activeBrowserNode:
            activeProxy = activeBrowserNode.GetProxyNode(activeSegmentationSequenceNode)
            if activeProxy:
                self._tagManagedSegmentationProxy(
                    activeProxy,
                    activeSegmentationSequenceNode,
                    activeBrowserNode,
                )
                for displayIndex in range(activeProxy.GetNumberOfDisplayNodes()):
                    displayNode = activeProxy.GetNthDisplayNode(displayIndex)
                    if displayNode and displayNode.GetID():
                        activeDisplayNodeIds.add(displayNode.GetID())
        browserProxyIds = set()
        for browserNode in slicer.util.getNodesByClass("vtkMRMLSequenceBrowserNode"):
            segmentationSequenceNode = self.findSegmentationSequence(browserNode)
            if not segmentationSequenceNode:
                continue
            proxySegmentation = browserNode.GetProxyNode(segmentationSequenceNode)
            if not proxySegmentation:
                continue
            if proxySegmentation.GetID():
                browserProxyIds.add(proxySegmentation.GetID())
            self._tagManagedSegmentationProxy(
                proxySegmentation,
                segmentationSequenceNode,
                browserNode,
            )
        for segmentationNode in slicer.util.getNodesByClass("vtkMRMLSegmentationNode"):
            if not self._isManagedCineSegmentation(segmentationNode, browserProxyIds):
                continue
            self.setSegmentationNodeVisibility(
                segmentationNode,
                segmentationNode == activeProxy,
            )
        for displayNode in slicer.util.getNodesByClass(
            "vtkMRMLSegmentationDisplayNode"
        ):
            if displayNode.GetAttribute("CineCMRQC.ManagedSegmentationDisplay") != "1":
                continue
            if displayNode.GetID() not in activeDisplayNodeIds:
                displayNode.SetVisibility(False)
                if hasattr(displayNode, "SetVisibility2D"):
                    displayNode.SetVisibility2D(False)
                if hasattr(displayNode, "SetVisibility3D"):
                    displayNode.SetVisibility3D(False)

    def scanPatientFolder(self, patientPath):
        import SimpleITK as sitk

        patientPath = os.path.abspath(patientPath)
        imageFolder = os.path.join(patientPath, "img")
        segmentationFolder = os.path.join(patientPath, "segmentation")
        if not os.path.isdir(imageFolder):
            raise ValueError("Patient folder has no img directory: {0}".format(patientPath))

        imageFilePattern = re.compile(r"^(series\d+-Body)\.nii(?:\.gz)?$", re.IGNORECASE)
        entries = []
        for fileName in sorted(os.listdir(imageFolder), key=self._naturalSortKey):
            if fileName.startswith("."):
                continue
            match = imageFilePattern.match(fileName)
            if not match:
                continue
            seriesId = match.group(1)
            imagePath = os.path.join(imageFolder, fileName)
            imageDimension = 0
            imageSize = ()
            imageFrameCount = 0
            imageError = ""
            try:
                reader = sitk.ImageFileReader()
                reader.SetFileName(imagePath)
                reader.ReadImageInformation()
                imageDimension = reader.GetDimension()
                imageSize = tuple(reader.GetSize())
                if imageDimension == 4:
                    imageFrameCount = imageSize[3]
                elif imageDimension == 3:
                    imageFrameCount = 1
                else:
                    imageError = "unsupported-image-dimension-{0}".format(imageDimension)
            except Exception as exc:
                imageError = "unreadable-image: {0}".format(exc)

            framesFolder = os.path.join(patientPath, "frames")
            doctorFramesPath = os.path.join(framesFolder, seriesId)
            doctorFrameFiles = []
            if os.path.isdir(doctorFramesPath):
                doctorFrameFiles = sorted(
                    [
                        name
                        for name in os.listdir(doctorFramesPath)
                        if not name.startswith(".") and name.lower().endswith(".png")
                    ],
                    key=self._naturalSortKey,
                )
            referenceFrames = []
            for frameFile in doctorFrameFiles:
                frameMatch = re.search(r"(\d+)", os.path.splitext(frameFile)[0])
                if frameMatch:
                    referenceFrames.append(int(frameMatch.group(1)))
            # Inference-only datasets do not provide physician reference
            # frames. Every valid canonical MRI series is therefore eligible;
            # frames, when present, are retained only as optional navigation
            # hints and audit metadata.
            doctorSelected = True

            segmentationSeriesPath = os.path.join(segmentationFolder, seriesId)
            maskPath = os.path.join(segmentationSeriesPath, "sequence")
            maskFrameCount = len(self._collectFrameFiles(maskPath))
            maskOrientation = self.detectMaskSequenceOrientation(
                segmentationSeriesPath,
                maskPath,
            ) if maskFrameCount else "not-available"
            timeIndexValues = []
            timeIndexName = ""
            timeIndexUnit = ""
            sourceSeriesPath = os.path.join(patientPath, seriesId)
            dicomFiles = []
            if os.path.isdir(sourceSeriesPath):
                dicomFiles = sorted(
                    [
                        os.path.join(sourceSeriesPath, name)
                        for name in os.listdir(sourceSeriesPath)
                        if not name.startswith(".") and name.lower().endswith(".dcm")
                    ],
                    key=self._naturalSortKey,
                )
            if dicomFiles:
                try:
                    import pydicom

                    triggerTimes = []
                    for dicomPath in dicomFiles:
                        dataset = pydicom.dcmread(
                            dicomPath,
                            stop_before_pixels=True,
                            specific_tags=["TriggerTime"],
                        )
                        if not hasattr(dataset, "TriggerTime"):
                            triggerTimes = []
                            break
                        triggerTimes.append(float(dataset.TriggerTime))
                    if (
                        len(triggerTimes) == imageFrameCount
                        and len(set(triggerTimes)) == len(triggerTimes)
                    ):
                        timeIndexValues = triggerTimes
                        timeIndexName = "trigger_time"
                        timeIndexUnit = "ms"
                except Exception:
                    logging.warning("Could not read DICOM TriggerTime for %s", seriesId, exc_info=True)
            if imageError:
                status = imageError
            elif any(value < 0 or value >= imageFrameCount for value in referenceFrames):
                status = "reference-frame-out-of-range"
            elif maskFrameCount not in [0, imageFrameCount]:
                status = "image-mask-frame-mismatch"
            else:
                status = "ready"

            entries.append({
                "patient_path": patientPath,
                "patient_id": os.path.basename(patientPath),
                "series_id": seriesId,
                "image_path": imagePath,
                "image_dimension": imageDimension,
                "image_size": imageSize,
                "image_frame_count": imageFrameCount,
                "doctor_selected": doctorSelected,
                # Loading eligibility is determined only by the physician's
                # reference-frame selection. Mask presence is deliberately not
                # part of this decision: selected image-only series remain
                # available for a physician annotation decision.
                "load_eligible": status == "ready",
                "reference_frames": referenceFrames,
                "mask_path": maskPath,
                "mask_frame_count": maskFrameCount,
                "mask_orientation": maskOrientation,
                "time_index_values": timeIndexValues,
                "time_index_name": timeIndexName,
                "time_index_unit": timeIndexUnit,
                "label_map": dict(self.HUAXI_LABELS),
                "status": status,
            })

        if not entries:
            raise ValueError("No canonical img/seriesXXXX-Body.nii.gz files were found.")
        selectedEntries = [entry for entry in entries if entry["load_eligible"]]
        return {
            "patient_path": patientPath,
            "series": entries,
            "total_count": len(entries),
            "selected_count": len(selectedEntries),
            "ready_count": sum(1 for entry in entries if entry["status"] == "ready"),
            "excluded_count": sum(1 for entry in entries if entry["status"] == "excluded-by-doctor"),
            "attention_count": sum(
                1
                for entry in selectedEntries
                if entry["status"] != "ready"
            ),
            "legacy_lr_count": sum(
                1
                for entry in entries
                if entry["mask_orientation"] == "legacy-left-right-flipped"
            ),
        }

    def detectMaskSequenceOrientation(self, segmentationSeriesPath, maskSequencePath):
        import SimpleITK as sitk
        import numpy as np

        if not os.path.isdir(segmentationSeriesPath):
            return "not-available"
        pngFiles = sorted(
            [
                os.path.join(segmentationSeriesPath, name)
                for name in os.listdir(segmentationSeriesPath)
                if not name.startswith(".") and name.lower().endswith(".png")
            ],
            key=self._naturalSortKey,
        )
        maskFiles = self._collectFrameFiles(maskSequencePath)
        if not pngFiles or not maskFiles:
            return "not-available"
        if len(pngFiles) != len(maskFiles):
            return "frame-count-mismatch"

        sampleIndices = sorted(set([0, len(pngFiles) // 2, len(pngFiles) - 1]))
        sampleStatuses = []
        for sampleIndex in sampleIndices:
            pngArray = sitk.GetArrayFromImage(sitk.ReadImage(pngFiles[sampleIndex]))
            maskArray = sitk.GetArrayFromImage(sitk.ReadImage(maskFiles[sampleIndex]))
            png2d = np.squeeze(pngArray)
            mask2d = np.squeeze(maskArray)
            if png2d.shape != mask2d.shape:
                return "shape-mismatch"
            if np.array_equal(mask2d, png2d):
                sampleStatuses.append("aligned")
            elif np.array_equal(mask2d, np.fliplr(png2d)):
                sampleStatuses.append("legacy-left-right-flipped")
            elif np.array_equal(mask2d, np.flipud(png2d)):
                sampleStatuses.append("up-down-flipped")
            elif np.array_equal(mask2d, np.rot90(png2d, 2)):
                sampleStatuses.append("rotated-180")
            else:
                sampleStatuses.append("unknown")
        if len(set(sampleStatuses)) == 1:
            return sampleStatuses[0]
        return "mixed:" + ",".join(sampleStatuses)

    def findLoadedPatientSeries(self, patientPath, seriesId):
        normalizedPatientPath = os.path.abspath(patientPath)
        for browserNode in slicer.util.getNodesByClass("vtkMRMLSequenceBrowserNode"):
            browserPatientPath = browserNode.GetAttribute("CineCMRQC.PatientRoot") or ""
            browserSeriesId = browserNode.GetAttribute("CineCMRQC.SeriesID") or ""
            if os.path.abspath(browserPatientPath) != normalizedPatientPath:
                continue
            if browserSeriesId != seriesId:
                continue
            imageSequenceNode = browserNode.GetMasterSequenceNode()
            segmentationSequenceNode = self.findSegmentationSequence(browserNode)
            if imageSequenceNode and segmentationSequenceNode:
                return imageSequenceNode, segmentationSequenceNode, browserNode
        return None

    def findClosestSequenceItem(self, sequenceNode, targetIndexValue):
        if not sequenceNode or sequenceNode.GetNumberOfDataNodes() < 1:
            raise ValueError("Sequence has no items.")
        numericValues = []
        for itemIndex in range(sequenceNode.GetNumberOfDataNodes()):
            try:
                numericValues.append(float(sequenceNode.GetNthIndexValue(itemIndex)))
            except (TypeError, ValueError):
                raise ValueError("Sequence index values are not numeric.")
        return min(
            range(len(numericValues)),
            key=lambda itemIndex: abs(numericValues[itemIndex] - float(targetIndexValue)),
        )

    def loadVolumeSequenceFromPath(
        self,
        path,
        baseName,
        labelmap=False,
        indexValues=None,
        indexName=None,
        indexUnit=None,
    ):
        frameFiles = self._collectFrameFiles(path)
        if frameFiles:
            return self._loadVolumeSequenceFromFrameFiles(
                frameFiles,
                baseName,
                labelmap,
                indexValues,
                indexName,
                indexUnit,
            )
        if os.path.isfile(path):
            return self._loadVolumeSequenceFromImageFile(
                path,
                baseName,
                labelmap,
                indexValues,
                indexName,
                indexUnit,
            )
        raise ValueError("No supported image files found in: {0}".format(path))

    def createSyntheticCineDemo(self, labels, frameCount=20, imageSize=128):
        if frameCount < 2:
            raise ValueError("Synthetic demo requires at least two frames.")
        if imageSize < 32:
            raise ValueError("Synthetic demo image size must be at least 32 pixels.")
        if max(labels) > 65535:
            raise ValueError("Synthetic demo label values must be <= 65535.")
        import math
        import numpy as np

        imageSequenceNode = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLSequenceNode",
            slicer.mrmlScene.GetUniqueNameByString("SyntheticCineImageSequence"),
        )
        labelSequenceNode = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLSequenceNode",
            slicer.mrmlScene.GetUniqueNameByString("SyntheticCineLabelSequence"),
        )
        for sequenceNode in [imageSequenceNode, labelSequenceNode]:
            sequenceNode.SetIndexName("time")
            sequenceNode.SetIndexUnit("ms")
            sequenceNode.SetAttribute("CineCMRQC.IsSyntheticDemo", "1")

        labelValues = sorted(labels)
        lvLabel = labelValues[0]
        myoLabel = labelValues[1] if len(labelValues) > 1 else None
        rvLabel = labelValues[2] if len(labelValues) > 2 else None
        yy, xx = np.mgrid[0:imageSize, 0:imageSize]
        randomGenerator = np.random.RandomState(20260716)
        frameDurationMs = 1000.0 / float(frameCount)
        browserNode = None
        segmentationSequenceNode = None

        try:
            for frameIndex in range(frameCount):
                phase = 2.0 * math.pi * frameIndex / float(frameCount)
                centerX = imageSize * 0.43 + imageSize * 0.015 * math.sin(phase)
                centerY = imageSize * 0.50
                innerRadius = imageSize * (0.145 + 0.035 * math.cos(phase))
                outerRadius = innerRadius + imageSize * 0.055
                lvDistance = np.sqrt((xx - centerX) ** 2 + (yy - centerY) ** 2)
                lvRegion = lvDistance <= innerRadius
                myoRegion = (lvDistance > innerRadius) & (lvDistance <= outerRadius)

                rvCenterX = centerX + imageSize * 0.255
                rvCenterY = centerY - imageSize * 0.025
                rvRadiusX = imageSize * (0.105 + 0.018 * math.cos(phase + 0.35))
                rvRadiusY = imageSize * (0.155 + 0.025 * math.cos(phase + 0.35))
                rvRegion = (
                    ((xx - rvCenterX) / rvRadiusX) ** 2
                    + ((yy - rvCenterY) / rvRadiusY) ** 2
                    <= 1.0
                )
                rvRegion &= ~myoRegion

                image2d = np.full((imageSize, imageSize), 22.0, dtype=np.float32)
                image2d[myoRegion] = 105.0
                image2d[lvRegion] = 185.0
                image2d[rvRegion] = 155.0
                image2d += 18.0 * np.exp(-(
                    ((xx - imageSize * 0.5) ** 2 + (yy - imageSize * 0.5) ** 2)
                    / (2.0 * (imageSize * 0.36) ** 2)
                )).astype(np.float32)
                image2d += randomGenerator.normal(0.0, 3.0, image2d.shape).astype(np.float32)

                mask2d = np.zeros((imageSize, imageSize), dtype=np.uint16)
                mask2d[lvRegion] = lvLabel
                if myoLabel is not None:
                    mask2d[myoRegion] = myoLabel
                if rvLabel is not None:
                    mask2d[rvRegion] = rvLabel

                imageNode = slicer.mrmlScene.AddNewNodeByClass(
                    "vtkMRMLScalarVolumeNode",
                    "SyntheticCineImage_frame{0:03d}".format(frameIndex),
                )
                labelNode = slicer.mrmlScene.AddNewNodeByClass(
                    "vtkMRMLLabelMapVolumeNode",
                    "SyntheticCineMask_frame{0:03d}".format(frameIndex),
                )
                slicer.util.updateVolumeFromArray(imageNode, image2d[np.newaxis, :, :])
                slicer.util.updateVolumeFromArray(labelNode, mask2d[np.newaxis, :, :])
                imageNode.SetSpacing(1.4, 1.4, 8.0)
                imageNode.SetOrigin(-imageSize * 0.7, -imageSize * 0.7, 0.0)
                labelNode.CopyOrientation(imageNode)
                sourceName = "synthetic-demo/frame{0:03d}".format(frameIndex)
                imageNode.SetAttribute("CineCMRQC.SourcePath", sourceName)
                labelNode.SetAttribute("CineCMRQC.SourcePath", sourceName)
                imageNode.SetAttribute("CineCMRQC.SourceFrameIndex", str(frameIndex))
                labelNode.SetAttribute("CineCMRQC.SourceFrameIndex", str(frameIndex))
                indexValue = "{0:.6g}".format(frameIndex * frameDurationMs)
                imageSequenceNode.SetDataNodeAtValue(imageNode, indexValue)
                labelSequenceNode.SetDataNodeAtValue(labelNode, indexValue)
                slicer.mrmlScene.RemoveNode(imageNode)
                slicer.mrmlScene.RemoveNode(labelNode)

            browserNode = self.getOrCreateBrowserForSequence(imageSequenceNode)
            segmentationSequenceNode = self.createSegmentationSequenceFromLabelmapSequence(
                labelSequenceNode,
                imageSequenceNode,
                browserNode,
                labels,
                "SyntheticCineSegmentation",
                False,
            )
            segmentationSequenceNode.SetAttribute("CineCMRQC.IsSyntheticDemo", "1")
            browserNode.SetAttribute("CineCMRQC.IsSyntheticDemo", "1")
            self.bindSegmentationSequence(segmentationSequenceNode, browserNode)
            browserNode.SetPlaybackRateFps(10.0)
            return imageSequenceNode, segmentationSequenceNode, browserNode
        except Exception:
            for node in [segmentationSequenceNode, browserNode, imageSequenceNode, labelSequenceNode]:
                if node and node.GetScene():
                    slicer.mrmlScene.RemoveNode(node)
            raise
        finally:
            if labelSequenceNode and labelSequenceNode.GetScene():
                slicer.mrmlScene.RemoveNode(labelSequenceNode)

    def _loadVolumeSequenceFromFrameFiles(
        self,
        frameFiles,
        baseName,
        labelmap=False,
        indexValues=None,
        indexName=None,
        indexUnit=None,
    ):
        import SimpleITK as sitk
        import sitkUtils

        sequenceNode = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLSequenceNode",
            slicer.mrmlScene.GetUniqueNameByString(baseName + "Sequence"),
        )
        normalizedIndexValues = self._normalizedIndexValues(indexValues, len(frameFiles))
        sequenceNode.SetIndexName(indexName or "frame")
        sequenceNode.SetIndexUnit(indexUnit or "")

        loadedNodes = []
        try:
            for frameIndex, filePath in enumerate(frameFiles):
                nodeName = "{0}_frame{1:03d}".format(baseName, frameIndex)
                frameImage = sitk.ReadImage(filePath)
                if frameImage.GetDimension() != 3:
                    raise ValueError(
                        "Each file in a frame folder must be a 3D image. "
                        "Got dimension {0} in: {1}".format(
                            frameImage.GetDimension(),
                            filePath,
                        )
                    )
                nodeClass = "vtkMRMLLabelMapVolumeNode" if labelmap else "vtkMRMLScalarVolumeNode"
                frameNode = sitkUtils.PushVolumeToSlicer(
                    frameImage,
                    None,
                    nodeName,
                    className=nodeClass,
                )
                if not frameNode:
                    raise ValueError("Failed to load frame file: {0}".format(filePath))
                frameNode.SetAttribute("CineCMRQC.SourcePath", os.path.abspath(filePath))
                frameNode.SetAttribute("CineCMRQC.SourceFrameIndex", str(frameIndex))
                loadedNodes.append(frameNode)
                sequenceNode.SetDataNodeAtValue(frameNode, normalizedIndexValues[frameIndex])
        except Exception:
            if sequenceNode and sequenceNode.GetScene():
                slicer.mrmlScene.RemoveNode(sequenceNode)
            raise
        finally:
            for node in loadedNodes:
                if node and node.GetScene():
                    slicer.mrmlScene.RemoveNode(node)

        return sequenceNode

    def _loadVolumeSequenceFromImageFile(
        self,
        filePath,
        baseName,
        labelmap=False,
        indexValues=None,
        indexName=None,
        indexUnit=None,
    ):
        import SimpleITK as sitk
        import sitkUtils

        image = sitk.ReadImage(filePath)
        dimension = image.GetDimension()
        if dimension == 3:
            nodeClass = "vtkMRMLLabelMapVolumeNode" if labelmap else "vtkMRMLScalarVolumeNode"
            frameNode = sitkUtils.PushVolumeToSlicer(image, None, baseName + "_frame000", className=nodeClass)
            sequenceNode = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLSequenceNode",
                slicer.mrmlScene.GetUniqueNameByString(baseName + "Sequence"),
            )
            normalizedIndexValues = self._normalizedIndexValues(indexValues, 1)
            sequenceNode.SetIndexName(indexName or "frame")
            sequenceNode.SetIndexUnit(indexUnit or "")
            frameNode.SetAttribute("CineCMRQC.SourcePath", os.path.abspath(filePath))
            frameNode.SetAttribute("CineCMRQC.SourceFrameIndex", "0")
            sequenceNode.SetDataNodeAtValue(frameNode, normalizedIndexValues[0])
            slicer.mrmlScene.RemoveNode(frameNode)
            return sequenceNode
        if dimension != 4:
            raise ValueError("Only 3D or 4D image files are supported. Got dimension {0}.".format(dimension))

        size4d = list(image.GetSize())
        frameCount = size4d[3]
        sequenceNode = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLSequenceNode",
            slicer.mrmlScene.GetUniqueNameByString(baseName + "Sequence"),
        )
        if indexValues:
            normalizedIndexValues = self._normalizedIndexValues(indexValues, frameCount)
            sequenceNode.SetIndexName(indexName or "time")
            sequenceNode.SetIndexUnit(indexUnit or "")
        else:
            temporalSpacing = float(image.GetSpacing()[3])
            temporalOrigin = float(image.GetOrigin()[3])
            normalizedIndexValues = [
                "{0:.12g}".format(temporalOrigin + frameIndex * temporalSpacing)
                for frameIndex in range(frameCount)
            ]
            sequenceNode.SetIndexName(indexName or "time")
            if indexUnit is not None:
                sequenceNode.SetIndexUnit(indexUnit)
            elif filePath.lower().endswith((".nii", ".nii.gz")):
                sequenceNode.SetIndexUnit("s")
        nodeClass = "vtkMRMLLabelMapVolumeNode" if labelmap else "vtkMRMLScalarVolumeNode"
        for frameIndex in range(frameCount):
            extractSize = [size4d[0], size4d[1], size4d[2], 0]
            extractIndex = [0, 0, 0, frameIndex]
            frameImage = sitk.Extract(image, extractSize, extractIndex)
            frameNode = sitkUtils.PushVolumeToSlicer(
                frameImage,
                None,
                "{0}_frame{1:03d}".format(baseName, frameIndex),
                className=nodeClass,
            )
            frameNode.SetAttribute("CineCMRQC.SourcePath", os.path.abspath(filePath))
            frameNode.SetAttribute("CineCMRQC.SourceFrameIndex", str(frameIndex))
            sequenceNode.SetDataNodeAtValue(frameNode, normalizedIndexValues[frameIndex])
            slicer.mrmlScene.RemoveNode(frameNode)
        return sequenceNode

    def _normalizedIndexValues(self, indexValues, frameCount):
        if indexValues is None or len(indexValues) == 0:
            return [str(frameIndex) for frameIndex in range(frameCount)]
        if len(indexValues) != frameCount:
            raise ValueError(
                "Sequence index count mismatch: expected {0}, got {1}.".format(
                    frameCount,
                    len(indexValues),
                )
            )
        normalizedValues = ["{0:.12g}".format(float(value)) for value in indexValues]
        if len(set(normalizedValues)) != len(normalizedValues):
            raise ValueError("Sequence index values must be unique.")
        return normalizedValues

    def loadSegmentationSequenceFromMaskPath(
        self,
        path,
        imageSequenceNode,
        browserNode,
        labels,
        baseName,
        useImageGeometry=False,
        autoDetectLabels=True,
        flipMaskLeftRight=False,
    ):
        labelSequence = self.loadVolumeSequenceFromPath(path, baseName + "Labelmap", labelmap=True)
        try:
            if flipMaskLeftRight:
                self.flipLabelSequenceLeftRight(labelSequence)
            detectedLabelValues = self.detectLabelValues(labelSequence)
            effectiveLabels = self.resolveLabelMap(labels, detectedLabelValues, autoDetectLabels)
            segmentationSequenceNode = self.createSegmentationSequenceFromLabelmapSequence(
                labelSequence,
                imageSequenceNode,
                browserNode,
                effectiveLabels,
                baseName + "Segmentation",
                useImageGeometry,
            )
            segmentationSequenceNode.SetAttribute(
                "CineCMRQC.DetectedLabelValues",
                ",".join(str(value) for value in detectedLabelValues),
            )
            segmentationSequenceNode.SetAttribute(
                "CineCMRQC.LabelMap",
                self.formatLabelMap(effectiveLabels),
            )
            segmentationSequenceNode.SetAttribute(
                "CineCMRQC.AppliedMaskTransform",
                "flip_lr" if flipMaskLeftRight else "none",
            )
            return segmentationSequenceNode
        finally:
            if labelSequence and labelSequence.GetScene():
                slicer.mrmlScene.RemoveNode(labelSequence)

    def flipLabelSequenceLeftRight(self, labelSequenceNode):
        import numpy as np

        for frameIndex in range(labelSequenceNode.GetNumberOfDataNodes()):
            frameNode = labelSequenceNode.GetNthDataNode(frameIndex)
            frameArray = np.array(slicer.util.arrayFromVolume(frameNode), copy=True)
            correctedArray = np.flip(frameArray, axis=-1).copy()
            slicer.util.updateVolumeFromArray(frameNode, correctedArray)
        labelSequenceNode.Modified()

    def detectLabelValues(self, labelSequenceNode):
        import numpy as np

        detectedValues = set()
        for frameIndex in range(labelSequenceNode.GetNumberOfDataNodes()):
            frameNode = labelSequenceNode.GetNthDataNode(frameIndex)
            if not frameNode or not frameNode.GetImageData():
                continue
            frameArray = slicer.util.arrayFromVolume(frameNode)
            for value in np.unique(frameArray):
                numericValue = float(value)
                integerValue = int(round(numericValue))
                if abs(numericValue - integerValue) > 1e-6:
                    raise ValueError(
                        "Mask label value {0} at frame {1} is not an integer.".format(
                            numericValue,
                            frameIndex,
                        )
                    )
                if integerValue != 0:
                    detectedValues.add(integerValue)
        return sorted(detectedValues)

    def resolveLabelMap(self, requestedLabels, detectedLabelValues, autoDetectLabels=True):
        if not autoDetectLabels or not detectedLabelValues:
            return dict(requestedLabels)
        return {
            value: requestedLabels.get(value, "Label_{0}".format(value))
            for value in detectedLabelValues
        }

    def applySegmentNames(self, segmentationSequenceNode, labels):
        appliedLabels = {}
        for frameIndex in range(segmentationSequenceNode.GetNumberOfDataNodes()):
            segmentationNode = segmentationSequenceNode.GetNthDataNode(frameIndex)
            segmentation = segmentationNode.GetSegmentation()
            for labelValue, segmentName in labels.items():
                segment = segmentation.GetSegment(self._stableSegmentId(labelValue))
                if segment:
                    segment.SetName(segmentName)
                    segment.SetLabelValue(labelValue)
                    appliedLabels[labelValue] = segmentName
            segmentationNode.Modified()
        segmentationSequenceNode.SetAttribute("CineCMRQC.LabelMap", self.formatLabelMap(appliedLabels))
        segmentationSequenceNode.Modified()

    def formatLabelMap(self, labels):
        return ",".join("{0}:{1}".format(value, labels[value]) for value in sorted(labels))

    def createSegmentationSequenceFromLabelmapSequence(
        self,
        labelSequenceNode,
        imageSequenceNode,
        browserNode,
        labels,
        baseName,
        useImageGeometry=False,
    ):
        if labelSequenceNode.GetNumberOfDataNodes() != imageSequenceNode.GetNumberOfDataNodes():
            raise ValueError(
                "Image/mask frame count mismatch: image={0}, mask={1}".format(
                    imageSequenceNode.GetNumberOfDataNodes(),
                    labelSequenceNode.GetNumberOfDataNodes(),
                )
            )

        segmentationSequenceNode = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLSequenceNode",
            slicer.mrmlScene.GetUniqueNameByString(baseName + "Sequence"),
        )
        segmentationSequenceNode.SetIndexName(imageSequenceNode.GetIndexName() or "frame")
        segmentationSequenceNode.SetIndexUnit(imageSequenceNode.GetIndexUnit() or "")

        try:
            for frameIndex in range(labelSequenceNode.GetNumberOfDataNodes()):
                indexValue = imageSequenceNode.GetNthIndexValue(frameIndex)
                labelmapNode = labelSequenceNode.GetNthDataNode(frameIndex)
                imageFrameNode = imageSequenceNode.GetNthDataNode(frameIndex)
                self.validateOrAlignMaskGeometry(
                    labelmapNode,
                    imageFrameNode,
                    frameIndex,
                    useImageGeometry,
                )
                segmentationNode = self._createSegmentationFromLabelmap(
                    labelmapNode,
                    imageFrameNode,
                    labels,
                    "{0}_frame{1:03d}".format(baseName, frameIndex),
                )
                segmentationNode.SetAttribute(
                    "CineCMRQC.BaselineDigest",
                    self.labelmapFrameDigest(labelmapNode),
                )
                segmentationSequenceNode.SetDataNodeAtValue(segmentationNode, indexValue)
                slicer.mrmlScene.RemoveNode(segmentationNode)
        except Exception:
            if segmentationSequenceNode and segmentationSequenceNode.GetScene():
                slicer.mrmlScene.RemoveNode(segmentationSequenceNode)
            raise

        segmentationSequenceNode.SetAttribute(
            "CineCMRQC.BaselineSource",
            "labelmap-import",
        )
        segmentationSequenceNode.SetAttribute("CineCMRQC.DirtyFrameCandidates", "")
        segmentationSequenceNode.SetAttribute("CineCMRQC.MaskMayBeDirty", "0")
        self.initializeReviewMetadataBaseline(segmentationSequenceNode)
        segmentationSequenceNode.Modified()
        return segmentationSequenceNode

    def createEmptySegmentationSequence(self, imageSequenceNode, browserNode, labels, baseName):
        segmentationSequenceNode = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLSequenceNode",
            slicer.mrmlScene.GetUniqueNameByString(baseName + "Sequence"),
        )
        segmentationSequenceNode.SetIndexName(imageSequenceNode.GetIndexName() or "frame")
        segmentationSequenceNode.SetIndexUnit(imageSequenceNode.GetIndexUnit() or "")

        for frameIndex in range(imageSequenceNode.GetNumberOfDataNodes()):
            indexValue = imageSequenceNode.GetNthIndexValue(frameIndex)
            imageFrameNode = imageSequenceNode.GetNthDataNode(frameIndex)
            segmentationNode = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLSegmentationNode",
                "{0}_frame{1:03d}".format(baseName, frameIndex),
            )
            segmentationNode.CreateDefaultDisplayNodes()
            if imageFrameNode:
                segmentationNode.SetReferenceImageGeometryParameterFromVolumeNode(imageFrameNode)
            self._ensureSegments(segmentationNode, labels)
            segmentationNode.SetAttribute("CineCMRQC.Reviewed", "0")
            segmentationSequenceNode.SetDataNodeAtValue(segmentationNode, indexValue)
            slicer.mrmlScene.RemoveNode(segmentationNode)

        self.initializeBaselineDigests(
            segmentationSequenceNode,
            imageSequenceNode,
            "empty-sequence",
        )
        segmentationSequenceNode.SetAttribute("CineCMRQC.DirtyFrameCandidates", "")
        segmentationSequenceNode.SetAttribute("CineCMRQC.MaskMayBeDirty", "0")
        self.initializeReviewMetadataBaseline(segmentationSequenceNode)
        return segmentationSequenceNode

    def bindSegmentationSequence(self, segmentationSequenceNode, browserNode):
        if not segmentationSequenceNode or not browserNode:
            raise ValueError("Segmentation sequence and browser are required.")
        browserNode.AddSynchronizedSequenceNode(segmentationSequenceNode.GetID())
        browserNode.SetSaveChanges(segmentationSequenceNode, True)
        slicer.modules.sequences.logic().UpdateProxyNodesFromSequences(browserNode)

    def findSegmentationSequence(self, browserNode):
        if not browserNode:
            return None
        synchronizedSequences = vtk.vtkCollection()
        browserNode.GetSynchronizedSequenceNodes(synchronizedSequences, False)
        for itemIndex in range(synchronizedSequences.GetNumberOfItems()):
            sequenceNode = synchronizedSequences.GetItemAsObject(itemIndex)
            if not sequenceNode or sequenceNode.GetNumberOfDataNodes() < 1:
                continue
            if sequenceNode.GetNthDataNode(0).IsA("vtkMRMLSegmentationNode"):
                return sequenceNode
        return None

    def setFrameReviewed(
        self,
        segmentationSequenceNode,
        frameIndex,
        reviewed,
        reviewer="",
        comment="",
        timestamp=None,
    ):
        if not segmentationSequenceNode:
            raise ValueError("Segmentation sequence is required.")
        if frameIndex < 0 or frameIndex >= segmentationSequenceNode.GetNumberOfDataNodes():
            raise ValueError("Frame index is out of range: {0}".format(frameIndex))
        frameNode = segmentationSequenceNode.GetNthDataNode(frameIndex)
        reviewedValue = "1" if reviewed else "0"
        frameNode.SetAttribute("CineCMRQC.Reviewed", reviewedValue)
        metadata = {
            "CineCMRQC.Reviewer": reviewer.strip() if reviewed else None,
            "CineCMRQC.ReviewTimestamp": (
                timestamp
                or datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
            ) if reviewed else None,
            "CineCMRQC.ReviewComment": comment.strip() if reviewed else None,
        }
        for attributeName, attributeValue in metadata.items():
            frameNode.SetAttribute(attributeName, attributeValue)
        browserNode = slicer.modules.sequences.logic().GetFirstBrowserNodeForSequenceNode(segmentationSequenceNode)
        if browserNode and browserNode.GetSelectedItemNumber() == frameIndex:
            proxyNode = browserNode.GetProxyNode(segmentationSequenceNode)
            if proxyNode:
                proxyNode.SetAttribute("CineCMRQC.Reviewed", reviewedValue)
                for attributeName, attributeValue in metadata.items():
                    proxyNode.SetAttribute(attributeName, attributeValue)
        segmentationSequenceNode.Modified()

    def getFrameReviewMetadata(self, segmentationSequenceNode, frameIndex):
        if (
            not segmentationSequenceNode
            or frameIndex < 0
            or frameIndex >= segmentationSequenceNode.GetNumberOfDataNodes()
        ):
            return {"reviewer": "", "timestamp": "", "comment": ""}
        frameNode = segmentationSequenceNode.GetNthDataNode(frameIndex)
        return {
            "reviewer": frameNode.GetAttribute("CineCMRQC.Reviewer") or "",
            "timestamp": frameNode.GetAttribute("CineCMRQC.ReviewTimestamp") or "",
            "comment": frameNode.GetAttribute("CineCMRQC.ReviewComment") or "",
        }

    def isFrameReviewed(self, segmentationSequenceNode, frameIndex):
        if not segmentationSequenceNode:
            return False
        if frameIndex < 0 or frameIndex >= segmentationSequenceNode.GetNumberOfDataNodes():
            return False
        return segmentationSequenceNode.GetNthDataNode(frameIndex).GetAttribute("CineCMRQC.Reviewed") == "1"

    def countReviewedFrames(self, segmentationSequenceNode):
        if not segmentationSequenceNode:
            return 0
        return sum(
            1
            for frameIndex in range(segmentationSequenceNode.GetNumberOfDataNodes())
            if self.isFrameReviewed(segmentationSequenceNode, frameIndex)
        )

    def reviewMetadataDigest(self, segmentationSequenceNode):
        if not segmentationSequenceNode:
            return ""
        values = []
        for frameIndex in range(segmentationSequenceNode.GetNumberOfDataNodes()):
            frameNode = segmentationSequenceNode.GetNthDataNode(frameIndex)
            values.append("\x1f".join([
                frameNode.GetAttribute("CineCMRQC.Reviewed") or "0",
                frameNode.GetAttribute("CineCMRQC.Reviewer") or "",
                frameNode.GetAttribute("CineCMRQC.ReviewTimestamp") or "",
                frameNode.GetAttribute("CineCMRQC.ReviewComment") or "",
            ]))
        return hashlib.sha256("\x1e".join(values).encode("utf-8")).hexdigest()

    def initializeReviewMetadataBaseline(self, segmentationSequenceNode):
        if not segmentationSequenceNode:
            return
        segmentationSequenceNode.SetAttribute(
            "CineCMRQC.ReviewMetadataBaselineDigest",
            self.reviewMetadataDigest(segmentationSequenceNode),
        )
        segmentationSequenceNode.SetAttribute(
            "CineCMRQC.ReviewMetadataMayBeDirty",
            "0",
        )

    def isReviewMetadataDirty(self, segmentationSequenceNode):
        if not segmentationSequenceNode:
            return False
        baseline = segmentationSequenceNode.GetAttribute(
            "CineCMRQC.ReviewMetadataBaselineDigest"
        )
        if not baseline:
            self.initializeReviewMetadataBaseline(segmentationSequenceNode)
            return False
        return self.reviewMetadataDigest(segmentationSequenceNode) != baseline

    def segmentationFrameDigest(self, segmentationNode, referenceVolumeNode):
        import numpy as np

        if not segmentationNode or not referenceVolumeNode:
            raise ValueError("Segmentation and reference volume are required for digesting.")
        referenceShape = slicer.util.arrayFromVolume(referenceVolumeNode).shape
        segmentation = segmentationNode.GetSegmentation()
        binaryName = slicer.vtkSegmentationConverter.GetSegmentationBinaryLabelmapRepresentationName()
        mergedArray = np.zeros(referenceShape, dtype=np.uint32)
        segmentsByLabelValue = []
        for segmentIndex in range(segmentation.GetNumberOfSegments()):
            segmentId = segmentation.GetNthSegmentID(segmentIndex)
            segment = segmentation.GetSegment(segmentId)
            labelValue = self._labelValueForSegment(segmentId, segment)
            segmentsByLabelValue.append((labelValue, segmentId, segment))
        for labelValue, segmentId, segment in sorted(segmentsByLabelValue):
            if not segment.GetRepresentation(binaryName):
                continue
            segmentArray = slicer.util.arrayFromSegmentBinaryLabelmap(
                segmentationNode,
                segmentId,
                referenceVolumeNode,
            )
            mergedArray[segmentArray > 0] = labelValue
        return self._labelArrayDigest(mergedArray)

    def labelmapFrameDigest(self, labelmapNode):
        import numpy as np

        if not labelmapNode:
            raise ValueError("Labelmap volume is required for digesting.")
        labelArray = np.asarray(
            slicer.util.arrayFromVolume(labelmapNode),
            dtype=np.uint32,
        )
        return self._labelArrayDigest(labelArray)

    def _labelArrayDigest(self, labelArray):
        import numpy as np

        normalizedArray = np.ascontiguousarray(labelArray, dtype=np.uint32)
        digest = hashlib.sha256()
        digest.update(str(normalizedArray.shape).encode("ascii"))
        digest.update(normalizedArray.tobytes())
        return digest.hexdigest()

    def initializeBaselineDigests(
        self,
        segmentationSequenceNode,
        imageSequenceNode,
        source="current-scene",
    ):
        if not segmentationSequenceNode or not imageSequenceNode:
            raise ValueError("Image and segmentation sequences are required.")
        if (
            segmentationSequenceNode.GetNumberOfDataNodes()
            != imageSequenceNode.GetNumberOfDataNodes()
        ):
            raise ValueError("Cannot initialize edit baselines for mismatched sequences.")
        for frameIndex in range(segmentationSequenceNode.GetNumberOfDataNodes()):
            segmentationNode = segmentationSequenceNode.GetNthDataNode(frameIndex)
            referenceVolumeNode = imageSequenceNode.GetNthDataNode(frameIndex)
            segmentationNode.SetAttribute(
                "CineCMRQC.BaselineDigest",
                self.segmentationFrameDigest(segmentationNode, referenceVolumeNode),
            )
        segmentationSequenceNode.SetAttribute("CineCMRQC.BaselineSource", source)
        segmentationSequenceNode.Modified()

    def isFrameCorrected(
        self,
        segmentationSequenceNode,
        imageSequenceNode,
        browserNode,
        frameIndex,
    ):
        if (
            not segmentationSequenceNode
            or not imageSequenceNode
            or frameIndex < 0
            or frameIndex >= segmentationSequenceNode.GetNumberOfDataNodes()
        ):
            return False
        storedFrameNode = segmentationSequenceNode.GetNthDataNode(frameIndex)
        baselineDigest = storedFrameNode.GetAttribute("CineCMRQC.BaselineDigest")
        referenceFrameNode = imageSequenceNode.GetNthDataNode(frameIndex)
        if not baselineDigest:
            baselineDigest = self.segmentationFrameDigest(storedFrameNode, referenceFrameNode)
            storedFrameNode.SetAttribute("CineCMRQC.BaselineDigest", baselineDigest)
            if not segmentationSequenceNode.GetAttribute("CineCMRQC.BaselineSource"):
                segmentationSequenceNode.SetAttribute(
                    "CineCMRQC.BaselineSource",
                    "legacy-scene-current-state",
                )
        currentSegmentationNode = storedFrameNode
        currentReferenceNode = referenceFrameNode
        if browserNode and browserNode.GetSelectedItemNumber() == frameIndex:
            proxySegmentation = browserNode.GetProxyNode(segmentationSequenceNode)
            proxyVolume = browserNode.GetProxyNode(imageSequenceNode)
            if proxySegmentation and proxyVolume:
                currentSegmentationNode = proxySegmentation
                currentReferenceNode = proxyVolume
        return self.segmentationFrameDigest(
            currentSegmentationNode,
            currentReferenceNode,
        ) != baselineDigest

    def countCorrectedFrames(
        self,
        segmentationSequenceNode,
        imageSequenceNode,
        browserNode=None,
    ):
        if not segmentationSequenceNode or not imageSequenceNode:
            return 0
        return sum(
            1
            for frameIndex in range(segmentationSequenceNode.GetNumberOfDataNodes())
            if self.isFrameCorrected(
                segmentationSequenceNode,
                imageSequenceNode,
                browserNode,
                frameIndex,
            )
        )

    def estimateCardiacPhaseFrames(
        self,
        segmentationSequenceNode,
        imageSequenceNode,
        cavityLabelValue=180,
    ):
        """Estimate ED/ES with progressively lower-confidence mask/time fallbacks."""
        if not segmentationSequenceNode or not imageSequenceNode:
            raise ValueError("Image and segmentation sequences are required.")
        frameCount = segmentationSequenceNode.GetNumberOfDataNodes()
        if frameCount != imageSequenceNode.GetNumberOfDataNodes() or frameCount < 2:
            return {
                "ed_frame": -1,
                "es_frame": -1,
                "cavity_counts": [],
                "metric": "label-{0}-voxel-count".format(cavityLabelValue),
                "source": "unavailable",
                "confidence": "unavailable",
            }
        segmentId = self._stableSegmentId(cavityLabelValue)
        binaryName = slicer.vtkSegmentationConverter.GetSegmentationBinaryLabelmapRepresentationName()
        cavityCounts = []
        cavityAvailable = True
        for frameIndex in range(frameCount):
            segmentationNode = segmentationSequenceNode.GetNthDataNode(frameIndex)
            referenceVolumeNode = imageSequenceNode.GetNthDataNode(frameIndex)
            segment = segmentationNode.GetSegmentation().GetSegment(segmentId)
            if not segment:
                cavityAvailable = False
                cavityCounts = []
                break
            if not segment.GetRepresentation(binaryName):
                cavityCounts.append(0)
                continue
            segmentArray = slicer.util.arrayFromSegmentBinaryLabelmap(
                segmentationNode,
                segmentId,
                referenceVolumeNode,
            )
            cavityCounts.append(int((segmentArray > 0).sum()))
        if cavityAvailable and cavityCounts and max(cavityCounts) != min(cavityCounts):
            return {
                "ed_frame": cavityCounts.index(max(cavityCounts)),
                "es_frame": cavityCounts.index(min(cavityCounts)),
                "cavity_counts": cavityCounts,
                "metric": "label-{0}-voxel-count".format(cavityLabelValue),
                "source": "automatic-mask-cavity-extrema",
                "confidence": "standard",
            }

        import numpy as np
        from scipy.spatial import ConvexHull, QhullError

        contourSegmentId = self._stableSegmentId(255)
        contourAreas = []
        contourAvailable = True
        for frameIndex in range(frameCount):
            segmentationNode = segmentationSequenceNode.GetNthDataNode(frameIndex)
            referenceVolumeNode = imageSequenceNode.GetNthDataNode(frameIndex)
            segment = segmentationNode.GetSegmentation().GetSegment(contourSegmentId)
            if not segment or not segment.GetRepresentation(binaryName):
                contourAvailable = False
                break
            segmentArray = np.squeeze(
                slicer.util.arrayFromSegmentBinaryLabelmap(
                    segmentationNode,
                    contourSegmentId,
                    referenceVolumeNode,
                )
            )
            points = np.argwhere(segmentArray > 0)
            if points.shape[0] < 3:
                contourAvailable = False
                break
            try:
                contourAreas.append(float(ConvexHull(points[:, [1, 0]]).volume))
            except QhullError:
                contourAvailable = False
                break
        if (
            contourAvailable
            and contourAreas
            and max(contourAreas) != min(contourAreas)
        ):
            return {
                "ed_frame": contourAreas.index(max(contourAreas)),
                "es_frame": contourAreas.index(min(contourAreas)),
                "cavity_counts": cavityCounts,
                "contour_areas": contourAreas,
                "metric": "label-255-contour-convex-hull-area",
                "source": "automatic-contour-convex-hull-extrema",
                "confidence": "low",
            }

        return {
            "ed_frame": 0,
            "es_frame": max(1, frameCount // 2),
            "cavity_counts": cavityCounts,
            "contour_areas": contourAreas,
            "metric": "temporal-opposition-fallback",
            "source": "automatic-temporal-opposition",
            "confidence": "very-low",
        }

    def initializeCardiacPhaseState(
        self,
        segmentationSequenceNode,
        imageSequenceNode,
        maskFolder="",
    ):
        if not segmentationSequenceNode or not imageSequenceNode:
            return self.getCardiacPhaseState(segmentationSequenceNode)
        frameCount = segmentationSequenceNode.GetNumberOfDataNodes()
        restored = False
        manifestPath = os.path.join(maskFolder, "manifest.csv") if maskFolder else ""
        if manifestPath and os.path.isfile(manifestPath):
            try:
                with open(manifestPath, "r", encoding="utf-8-sig", newline="") as fp:
                    manifestRows = list(csv.DictReader(fp))
                firstRow = manifestRows[0] if manifestRows else None
                for rowIndex, row in enumerate(manifestRows):
                    try:
                        frameIndex = int(row.get("frame", rowIndex))
                    except (TypeError, ValueError):
                        continue
                    if not 0 <= frameIndex < frameCount:
                        continue
                    frameNode = segmentationSequenceNode.GetNthDataNode(frameIndex)
                    reviewed = row.get("reviewed", "0") == "1"
                    frameNode.SetAttribute("CineCMRQC.Reviewed", "1" if reviewed else "0")
                    frameNode.SetAttribute(
                        "CineCMRQC.Reviewer",
                        (row.get("reviewer", "") or None) if reviewed else None,
                    )
                    frameNode.SetAttribute(
                        "CineCMRQC.ReviewTimestamp",
                        (row.get("review_timestamp", "") or None) if reviewed else None,
                    )
                    frameNode.SetAttribute(
                        "CineCMRQC.ReviewComment",
                        (row.get("review_comment", "") or None) if reviewed else None,
                    )
                if firstRow:
                    edFrame = int(firstRow.get("end_diastolic_frame", ""))
                    esFrame = int(firstRow.get("end_systolic_frame", ""))
                    if (
                        0 <= edFrame < frameCount
                        and 0 <= esFrame < frameCount
                        and edFrame != esFrame
                    ):
                        segmentationSequenceNode.SetAttribute(
                            "CineCMRQC.EndDiastolicFrame",
                            str(edFrame),
                        )
                        segmentationSequenceNode.SetAttribute(
                            "CineCMRQC.EndSystolicFrame",
                            str(esFrame),
                        )
                        segmentationSequenceNode.SetAttribute(
                            "CineCMRQC.EndDiastolicSource",
                            firstRow.get("end_diastolic_source", "") or "restored",
                        )
                        segmentationSequenceNode.SetAttribute(
                            "CineCMRQC.EndSystolicSource",
                            firstRow.get("end_systolic_source", "") or "restored",
                        )
                        segmentationSequenceNode.SetAttribute(
                            "CineCMRQC.CardiacPhaseConfirmed",
                            "1" if firstRow.get("cardiac_phase_confirmed", "0") == "1" else "0",
                        )
                        segmentationSequenceNode.SetAttribute(
                            "CineCMRQC.CardiacPhaseConfirmedAt",
                            firstRow.get("cardiac_phase_confirmed_at", "") or None,
                        )
                        segmentationSequenceNode.SetAttribute(
                            "CineCMRQC.CardiacPhaseMetric",
                            firstRow.get("cardiac_phase_metric", "")
                            or "restored-manifest",
                        )
                        segmentationSequenceNode.SetAttribute(
                            "CineCMRQC.CardiacPhaseConfidence",
                            firstRow.get("cardiac_phase_confidence", "")
                            or "restored",
                        )
                        restored = True
            except (OSError, TypeError, ValueError):
                logging.warning(
                    "Could not restore ED/ES metadata from %s; estimating again.",
                    manifestPath,
                    exc_info=True,
                )
        if not restored:
            estimate = self.estimateCardiacPhaseFrames(
                segmentationSequenceNode,
                imageSequenceNode,
            )
            segmentationSequenceNode.SetAttribute(
                "CineCMRQC.EndDiastolicFrame",
                str(estimate["ed_frame"]),
            )
            segmentationSequenceNode.SetAttribute(
                "CineCMRQC.EndSystolicFrame",
                str(estimate["es_frame"]),
            )
            segmentationSequenceNode.SetAttribute(
                "CineCMRQC.EndDiastolicSource",
                estimate["source"],
            )
            segmentationSequenceNode.SetAttribute(
                "CineCMRQC.EndSystolicSource",
                estimate["source"],
            )
            segmentationSequenceNode.SetAttribute(
                "CineCMRQC.CardiacPhaseConfirmed",
                "0",
            )
            segmentationSequenceNode.SetAttribute(
                "CineCMRQC.CardiacPhaseConfirmedAt",
                None,
            )
            segmentationSequenceNode.SetAttribute(
                "CineCMRQC.CardiacPhaseMetric",
                estimate["metric"],
            )
            segmentationSequenceNode.SetAttribute(
                "CineCMRQC.CardiacPhaseConfidence",
                estimate["confidence"],
            )
        segmentationSequenceNode.SetAttribute(
            "CineCMRQC.RequireCardiacPhaseConfirmation",
            "1",
        )
        segmentationSequenceNode.SetAttribute(
            "CineCMRQC.HasBeenSaved",
            "1" if restored else "0",
        )
        segmentationSequenceNode.SetAttribute("CineCMRQC.DirtyFrameCandidates", "")
        segmentationSequenceNode.SetAttribute("CineCMRQC.MaskMayBeDirty", "0")
        self.initializeReviewMetadataBaseline(segmentationSequenceNode)
        self.initializeCardiacPhaseBaseline(segmentationSequenceNode)
        segmentationSequenceNode.Modified()
        return self.getCardiacPhaseState(segmentationSequenceNode)

    def initializeCardiacPhaseStateWithoutMask(
        self,
        segmentationSequenceNode,
        auditRecord=None,
    ):
        if not segmentationSequenceNode:
            return self.getCardiacPhaseState(segmentationSequenceNode)
        frameCount = segmentationSequenceNode.GetNumberOfDataNodes()
        auditRecord = auditRecord or {}
        edRecord = auditRecord.get("end_diastolic") or {}
        esRecord = auditRecord.get("end_systolic") or {}
        try:
            edFrame = int(edRecord.get("frame_index"))
            esFrame = int(esRecord.get("frame_index"))
        except (TypeError, ValueError):
            edFrame = -1
            esFrame = -1
        restored = (
            0 <= edFrame < frameCount
            and 0 <= esFrame < frameCount
            and edFrame != esFrame
        )
        segmentationSequenceNode.SetAttribute(
            "CineCMRQC.EndDiastolicFrame",
            str(edFrame if restored else -1),
        )
        segmentationSequenceNode.SetAttribute(
            "CineCMRQC.EndSystolicFrame",
            str(esFrame if restored else -1),
        )
        segmentationSequenceNode.SetAttribute(
            "CineCMRQC.EndDiastolicSource",
            (edRecord.get("source") or "restored") if restored else "manual-required",
        )
        segmentationSequenceNode.SetAttribute(
            "CineCMRQC.EndSystolicSource",
            (esRecord.get("source") or "restored") if restored else "manual-required",
        )
        segmentationSequenceNode.SetAttribute(
            "CineCMRQC.CardiacPhaseConfirmed",
            "1" if restored and auditRecord.get("cardiac_phase_confirmed") else "0",
        )
        segmentationSequenceNode.SetAttribute(
            "CineCMRQC.CardiacPhaseConfirmedAt",
            auditRecord.get("cardiac_phase_confirmed_at") if restored else None,
        )
        segmentationSequenceNode.SetAttribute(
            "CineCMRQC.CardiacPhaseMetric",
            "manual-image-review",
        )
        segmentationSequenceNode.SetAttribute(
            "CineCMRQC.CardiacPhaseConfidence",
            "manual" if restored else "not-estimated-no-mask",
        )
        segmentationSequenceNode.SetAttribute(
            "CineCMRQC.RequireCardiacPhaseConfirmation",
            "1",
        )
        segmentationSequenceNode.SetAttribute(
            "CineCMRQC.HasBeenSaved",
            "1" if restored else "0",
        )
        segmentationSequenceNode.SetAttribute("CineCMRQC.DirtyFrameCandidates", "")
        segmentationSequenceNode.SetAttribute("CineCMRQC.MaskMayBeDirty", "0")
        self.initializeReviewMetadataBaseline(segmentationSequenceNode)
        self.initializeCardiacPhaseBaseline(segmentationSequenceNode)
        segmentationSequenceNode.Modified()
        return self.getCardiacPhaseState(segmentationSequenceNode)

    def setAnnotationDecisionState(
        self,
        segmentationSequenceNode,
        decision,
        saved=False,
    ):
        if not segmentationSequenceNode:
            return
        if decision not in ["undecided", "required", "not_required"]:
            raise ValueError("Unsupported annotation decision: {0}".format(decision))
        segmentationSequenceNode.SetAttribute(
            "CineCMRQC.AnnotationDecision",
            decision,
        )
        segmentationSequenceNode.SetAttribute(
            "CineCMRQC.AnnotationDecisionSaved",
            "1" if saved else "0",
        )
        if decision == "not_required":
            segmentationSequenceNode.SetAttribute(
                "CineCMRQC.RequireCardiacPhaseConfirmation",
                "0",
            )
            segmentationSequenceNode.SetAttribute("CineCMRQC.EndDiastolicFrame", "-1")
            segmentationSequenceNode.SetAttribute("CineCMRQC.EndSystolicFrame", "-1")
            segmentationSequenceNode.SetAttribute(
                "CineCMRQC.EndDiastolicSource",
                "not-required",
            )
            segmentationSequenceNode.SetAttribute(
                "CineCMRQC.EndSystolicSource",
                "not-required",
            )
            segmentationSequenceNode.SetAttribute("CineCMRQC.CardiacPhaseConfirmed", "0")
            segmentationSequenceNode.SetAttribute("CineCMRQC.CardiacPhaseConfirmedAt", None)
            segmentationSequenceNode.SetAttribute(
                "CineCMRQC.CardiacPhaseMetric",
                "not-applicable",
            )
            segmentationSequenceNode.SetAttribute(
                "CineCMRQC.CardiacPhaseConfidence",
                "not-applicable",
            )
            segmentationSequenceNode.SetAttribute(
                "CineCMRQC.HasBeenSaved",
                "1" if saved else "0",
            )
            self.initializeCardiacPhaseBaseline(segmentationSequenceNode)
        elif decision == "required":
            segmentationSequenceNode.SetAttribute(
                "CineCMRQC.RequireCardiacPhaseConfirmation",
                "1",
            )
        segmentationSequenceNode.Modified()

    def restoreAnnotationDecisionState(
        self,
        segmentationSequenceNode,
        auditRecord,
        hasSourceMask=False,
    ):
        auditRecord = auditRecord or {}
        recordedDecision = auditRecord.get("annotation_decision")
        if recordedDecision == "not_required":
            decision = "not_required"
            saved = True
        elif recordedDecision == "required" and hasSourceMask:
            decision = "required"
            saved = True
        else:
            decision = "required" if hasSourceMask else "undecided"
            saved = bool(hasSourceMask)
        self.setAnnotationDecisionState(
            segmentationSequenceNode,
            decision,
            saved,
        )

    def clearSegmentationSequence(
        self,
        segmentationSequenceNode,
        imageSequenceNode,
        browserNode,
        labels,
    ):
        if not segmentationSequenceNode or not imageSequenceNode:
            raise ValueError("MRI and segmentation sequences are required.")
        for frameIndex in range(segmentationSequenceNode.GetNumberOfDataNodes()):
            segmentationNode = segmentationSequenceNode.GetNthDataNode(frameIndex)
            segmentationNode.GetSegmentation().RemoveAllSegments()
            self._ensureSegments(segmentationNode, labels)
            segmentationNode.Modified()
        segmentationSequenceNode.SetAttribute("CineCMRQC.DirtyFrameCandidates", "")
        segmentationSequenceNode.SetAttribute("CineCMRQC.MaskMayBeDirty", "0")
        if browserNode:
            slicer.modules.sequences.logic().UpdateProxyNodesFromSequences(browserNode)
        self.initializeBaselineDigests(
            segmentationSequenceNode,
            imageSequenceNode,
            "empty-after-not-required-decision",
        )
        segmentationSequenceNode.Modified()

    def getCardiacPhaseState(self, segmentationSequenceNode):
        if not segmentationSequenceNode:
            return {
                "ed_frame": -1,
                "es_frame": -1,
                "ed_source": "",
                "es_source": "",
                "confirmed": False,
                "confirmed_at": "",
                "metric": "",
                "confidence": "",
            }

        def integerAttribute(name):
            try:
                return int(segmentationSequenceNode.GetAttribute(name) or "-1")
            except (TypeError, ValueError):
                return -1

        return {
            "ed_frame": integerAttribute("CineCMRQC.EndDiastolicFrame"),
            "es_frame": integerAttribute("CineCMRQC.EndSystolicFrame"),
            "ed_source": segmentationSequenceNode.GetAttribute(
                "CineCMRQC.EndDiastolicSource"
            ) or "",
            "es_source": segmentationSequenceNode.GetAttribute(
                "CineCMRQC.EndSystolicSource"
            ) or "",
            "confirmed": segmentationSequenceNode.GetAttribute(
                "CineCMRQC.CardiacPhaseConfirmed"
            ) == "1",
            "confirmed_at": segmentationSequenceNode.GetAttribute(
                "CineCMRQC.CardiacPhaseConfirmedAt"
            ) or "",
            "metric": segmentationSequenceNode.GetAttribute(
                "CineCMRQC.CardiacPhaseMetric"
            ) or "",
            "confidence": segmentationSequenceNode.GetAttribute(
                "CineCMRQC.CardiacPhaseConfidence"
            ) or "",
        }

    def setCardiacPhaseFrame(self, segmentationSequenceNode, phase, frameIndex):
        if not segmentationSequenceNode:
            raise ValueError("Segmentation sequence is required.")
        phase = str(phase).upper()
        if phase not in ["ED", "ES"]:
            raise ValueError("Cardiac phase must be ED or ES.")
        frameCount = segmentationSequenceNode.GetNumberOfDataNodes()
        if frameIndex < -1 or frameIndex >= frameCount:
            raise ValueError("Cardiac phase frame is out of range.")
        state = self.getCardiacPhaseState(segmentationSequenceNode)
        otherFrame = state["es_frame"] if phase == "ED" else state["ed_frame"]
        if frameIndex >= 0 and frameIndex == otherFrame:
            raise ValueError("舒张末期和收缩末期不能是同一帧。")
        prefix = "EndDiastolic" if phase == "ED" else "EndSystolic"
        segmentationSequenceNode.SetAttribute(
            "CineCMRQC.{0}Frame".format(prefix),
            str(frameIndex),
        )
        segmentationSequenceNode.SetAttribute(
            "CineCMRQC.{0}Source".format(prefix),
            "manual",
        )
        segmentationSequenceNode.SetAttribute("CineCMRQC.CardiacPhaseConfirmed", "0")
        segmentationSequenceNode.SetAttribute("CineCMRQC.CardiacPhaseConfirmedAt", None)
        segmentationSequenceNode.SetAttribute("CineCMRQC.CardiacPhaseConfidence", "manual")
        segmentationSequenceNode.Modified()

    def confirmCardiacPhaseFrames(self, segmentationSequenceNode):
        state = self.getCardiacPhaseState(segmentationSequenceNode)
        frameCount = segmentationSequenceNode.GetNumberOfDataNodes()
        if not (
            0 <= state["ed_frame"] < frameCount
            and 0 <= state["es_frame"] < frameCount
            and state["ed_frame"] != state["es_frame"]
        ):
            raise ValueError("必须先为当前 Series 指定不同的舒张末期和收缩末期帧。")
        segmentationSequenceNode.SetAttribute("CineCMRQC.CardiacPhaseConfirmed", "1")
        segmentationSequenceNode.SetAttribute(
            "CineCMRQC.CardiacPhaseConfirmedAt",
            datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        )
        segmentationSequenceNode.Modified()

    def cardiacPhaseStateDigest(self, segmentationSequenceNode):
        state = self.getCardiacPhaseState(segmentationSequenceNode)
        serialized = "|".join([
            str(state["ed_frame"]),
            str(state["es_frame"]),
            state["ed_source"],
            state["es_source"],
            "1" if state["confirmed"] else "0",
            state["confirmed_at"],
            state["metric"],
            state["confidence"],
        ])
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def initializeCardiacPhaseBaseline(self, segmentationSequenceNode):
        if segmentationSequenceNode:
            segmentationSequenceNode.SetAttribute(
                "CineCMRQC.CardiacPhaseBaselineDigest",
                self.cardiacPhaseStateDigest(segmentationSequenceNode),
            )

    def isCardiacPhaseStateDirty(self, segmentationSequenceNode):
        if not segmentationSequenceNode:
            return False
        baseline = segmentationSequenceNode.GetAttribute(
            "CineCMRQC.CardiacPhaseBaselineDigest"
        )
        if not baseline:
            self.initializeCardiacPhaseBaseline(segmentationSequenceNode)
            return False
        return self.cardiacPhaseStateDigest(segmentationSequenceNode) != baseline

    def markCardiacPhaseStateSaved(self, segmentationSequenceNode):
        if not segmentationSequenceNode:
            return
        self.initializeCardiacPhaseBaseline(segmentationSequenceNode)
        segmentationSequenceNode.SetAttribute("CineCMRQC.HasBeenSaved", "1")
        segmentationSequenceNode.Modified()

    def cardiacPhaseAreaMetrics(
        self,
        segmentationSequenceNode,
        imageSequenceNode,
        browserNode=None,
        cavityLabelValue=180,
    ):
        state = self.getCardiacPhaseState(segmentationSequenceNode)
        binaryName = slicer.vtkSegmentationConverter.GetSegmentationBinaryLabelmapRepresentationName()

        def frameMetrics(frameIndex):
            if (
                frameIndex < 0
                or frameIndex >= segmentationSequenceNode.GetNumberOfDataNodes()
            ):
                return {"pixel_count": None, "area_pixels": None, "area_mm2": None}
            segmentationNode = segmentationSequenceNode.GetNthDataNode(frameIndex)
            referenceVolumeNode = imageSequenceNode.GetNthDataNode(frameIndex)
            if browserNode and browserNode.GetSelectedItemNumber() == frameIndex:
                proxySegmentation = browserNode.GetProxyNode(segmentationSequenceNode)
                proxyVolume = browserNode.GetProxyNode(imageSequenceNode)
                if proxySegmentation and proxyVolume:
                    segmentationNode = proxySegmentation
                    referenceVolumeNode = proxyVolume
            areaPixels = None
            if state["metric"] == "label-255-contour-convex-hull-area":
                import numpy as np
                from scipy.spatial import ConvexHull, QhullError

                segmentId = self._stableSegmentId(255)
                segment = segmentationNode.GetSegmentation().GetSegment(segmentId)
                if segment and segment.GetRepresentation(binaryName):
                    segmentArray = np.squeeze(
                        slicer.util.arrayFromSegmentBinaryLabelmap(
                            segmentationNode,
                            segmentId,
                            referenceVolumeNode,
                        )
                    )
                    points = np.argwhere(segmentArray > 0)
                    if points.shape[0] >= 3:
                        try:
                            areaPixels = float(ConvexHull(points[:, [1, 0]]).volume)
                        except QhullError:
                            areaPixels = None
            elif state["metric"] != "temporal-opposition-fallback":
                segmentId = self._stableSegmentId(cavityLabelValue)
                segment = segmentationNode.GetSegmentation().GetSegment(segmentId)
                if not segment or not segment.GetRepresentation(binaryName):
                    areaPixels = 0.0
                else:
                    segmentArray = slicer.util.arrayFromSegmentBinaryLabelmap(
                        segmentationNode,
                        segmentId,
                        referenceVolumeNode,
                    )
                    areaPixels = float((segmentArray > 0).sum())
            if areaPixels is None:
                return {"pixel_count": None, "area_pixels": None, "area_mm2": None}
            pixelCount = int(round(areaPixels))
            spacing = referenceVolumeNode.GetSpacing()
            areaMm2 = areaPixels * float(spacing[0]) * float(spacing[1])
            return {
                "pixel_count": pixelCount,
                "area_pixels": round(areaPixels, 6),
                "area_mm2": round(areaMm2, 6),
            }

        return {
            "ed": frameMetrics(state["ed_frame"]),
            "es": frameMetrics(state["es_frame"]),
        }

    def patientAuditPath(self, patientRoot):
        return os.path.join(os.path.abspath(patientRoot), "cine_cmr_qc_review.json")

    def _utcNow(self):
        return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    def loadPatientAudit(self, patientRoot):
        patientRoot = os.path.abspath(patientRoot)
        auditPath = self.patientAuditPath(patientRoot)
        if os.path.isfile(auditPath):
            try:
                with open(auditPath, "r", encoding="utf-8") as fp:
                    audit = json.load(fp)
            except (OSError, ValueError) as exc:
                raise ValueError(
                    "患者审计 JSON 无法读取，请先检查文件：{0}；{1}".format(
                        auditPath,
                        exc,
                    )
                )
            if not isinstance(audit, dict):
                raise ValueError("患者审计 JSON 顶层必须是对象：{0}".format(auditPath))
        else:
            now = self._utcNow()
            audit = {
                "schema_version": 1,
                "patient_id": os.path.basename(patientRoot),
                "patient_path": patientRoot,
                "created_at": now,
                "updated_at": now,
                "ejection_fraction_percent": None,
                "ejection_fraction_source": "not-recorded",
                "series": {},
            }
        audit.setdefault("schema_version", 1)
        audit.setdefault("patient_id", os.path.basename(patientRoot))
        audit["patient_path"] = patientRoot
        audit.setdefault("ejection_fraction_percent", None)
        audit.setdefault("ejection_fraction_source", "not-recorded")
        if not isinstance(audit.get("series"), dict):
            audit["series"] = {}
        return audit

    def writePatientAudit(self, patientRoot, audit):
        patientRoot = os.path.abspath(patientRoot)
        if not os.path.isdir(patientRoot):
            raise ValueError("患者目录不存在：{0}".format(patientRoot))
        audit["updated_at"] = self._utcNow()
        auditPath = self.patientAuditPath(patientRoot)
        fileDescriptor, temporaryPath = tempfile.mkstemp(
            prefix="cine_cmr_qc_review-",
            suffix=".tmp",
            dir=patientRoot,
        )
        try:
            with os.fdopen(fileDescriptor, "w", encoding="utf-8") as fp:
                json.dump(audit, fp, ensure_ascii=False, indent=2, sort_keys=True)
                fp.write("\n")
                fp.flush()
                os.fsync(fp.fileno())
            os.replace(temporaryPath, auditPath)
        except Exception:
            if os.path.exists(temporaryPath):
                os.remove(temporaryPath)
            raise
        return auditPath

    def updatePatientEjectionFraction(self, patientRoot, efPercent):
        if efPercent is not None and not 0.0 <= float(efPercent) <= 100.0:
            raise ValueError("射血分数 EF 必须在 0 到 100 之间。")
        audit = self.loadPatientAudit(patientRoot)
        audit["ejection_fraction_percent"] = (
            None if efPercent is None else round(float(efPercent), 1)
        )
        audit["ejection_fraction_source"] = (
            "not-recorded" if efPercent is None else "manual"
        )
        audit["ejection_fraction_updated_at"] = self._utcNow()
        return self.writePatientAudit(patientRoot, audit)

    def patientSeriesAuditRecord(self, patientRoot, seriesId):
        if not patientRoot or not seriesId:
            return {}
        audit = self.loadPatientAudit(patientRoot)
        record = audit.get("series", {}).get(seriesId, {})
        return dict(record) if isinstance(record, dict) else {}

    def _appendAnnotationDecisionHistory(
        self,
        record,
        decision,
        reviewer,
        maskPresent,
        decidedAt,
    ):
        previousDecision = record.get("annotation_decision")
        if previousDecision == decision:
            return
        record.setdefault("annotation_decision_history", []).append({
            "decision": decision,
            "decided_at": decidedAt,
            "reviewer": reviewer or "",
            "mask_present_before_decision": bool(maskPresent),
        })

    def deleteSeriesMasksAndRecordDecision(
        self,
        patientRoot,
        seriesId,
        reviewer="",
    ):
        patientRoot = os.path.abspath(patientRoot)
        if not re.match(r"^series\d+-Body$", seriesId or "", re.IGNORECASE):
            raise ValueError("Series ID 不符合安全删除规则：{0}".format(seriesId))
        segmentationRoot = os.path.abspath(
            os.path.join(patientRoot, "segmentation")
        )
        targetPath = os.path.abspath(os.path.join(segmentationRoot, seriesId))
        if os.path.dirname(targetPath) != segmentationRoot:
            raise ValueError("Mask 删除路径越出当前患者 segmentation 目录。")
        if os.path.islink(targetPath):
            raise ValueError("拒绝删除符号链接形式的 Mask 目录：{0}".format(targetPath))

        originalAudit = self.loadPatientAudit(patientRoot)
        audit = json.loads(json.dumps(originalAudit, ensure_ascii=False))
        now = self._utcNow()
        record = audit.setdefault("series", {}).setdefault(seriesId, {})
        maskPresent = os.path.isdir(targetPath)
        self._appendAnnotationDecisionHistory(
            record,
            "not_required",
            reviewer,
            maskPresent,
            now,
        )
        record["annotation_decision"] = "not_required"
        record["annotation_decision_confirmed"] = True
        record["annotation_decision_at"] = now
        record["annotation_decision_reviewer"] = reviewer or ""
        record["mask_present_at_decision"] = bool(maskPresent)
        record["mask_deleted"] = bool(maskPresent)
        record["mask_frame_count"] = 0
        record["review_status"] = "completed-no-annotation"
        record["data_status"] = "ready-image-only"
        record["end_diastolic"] = {
            "frame_index": None,
            "frame_number": None,
            "source": "not-required",
            "pixel_count": None,
            "area_pixels": None,
            "area_mm2": None,
        }
        record["end_systolic"] = dict(record["end_diastolic"])
        record["cardiac_phase_confirmed"] = False
        record["cardiac_phase_confirmed_at"] = None
        record["cardiac_phase_metric"] = "not-applicable"
        record["cardiac_phase_confidence"] = "not-applicable"
        record["last_saved_at"] = now
        record["save_count"] = int(record.get("save_count", 0)) + 1
        record.setdefault("save_history", []).append({
            "saved_at": now,
            "save_type": "annotation-not-required",
            "mask_deleted": bool(maskPresent),
            "reviewer": reviewer or "",
        })

        stagedPath = ""
        auditWritten = False
        try:
            if maskPresent:
                suffix = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                stagedPath = os.path.join(
                    segmentationRoot,
                    ".cinecmrqc-delete-{0}-{1}".format(seriesId, suffix),
                )
                if os.path.exists(stagedPath):
                    raise ValueError("临时删除路径已存在：{0}".format(stagedPath))
                os.replace(targetPath, stagedPath)
            auditPath = self.writePatientAudit(patientRoot, audit)
            auditWritten = True
            if stagedPath:
                shutil.rmtree(stagedPath)
            return {
                "audit_path": auditPath,
                "deleted_paths": [targetPath] if maskPresent else [],
            }
        except Exception:
            if stagedPath and os.path.isdir(stagedPath) and not os.path.exists(targetPath):
                os.replace(stagedPath, targetPath)
            if auditWritten:
                try:
                    self.writePatientAudit(patientRoot, originalAudit)
                except Exception:
                    logging.exception("Failed to restore patient audit after Mask deletion failure.")
            raise

    def initializePatientAuditSeriesCatalog(self, patientRoot, seriesEntries):
        audit = self.loadPatientAudit(patientRoot)
        records = audit.setdefault("series", {})
        for entry in seriesEntries:
            seriesId = str(entry.get("series_id") or "").strip()
            if not seriesId:
                continue
            record = records.setdefault(seriesId, {})
            record["doctor_selected"] = bool(entry.get("doctor_selected"))
            record["data_status"] = entry.get("status") or "unknown"
            record["frame_count"] = int(entry.get("image_frame_count") or 0)
            record["mask_frame_count"] = int(entry.get("mask_frame_count") or 0)
            record["doctor_reference_frames"] = list(entry.get("reference_frames") or [])
            record["image_path"] = entry.get("image_path") or ""
            record["mask_path"] = entry.get("mask_path") or ""
            record.setdefault("first_opened_at", None)
            record.setdefault("last_opened_at", None)
            record.setdefault("last_modified_at", None)
            record.setdefault("last_modified_source", "not-opened")
            record.setdefault("modified_frame_indices", [])
            record.setdefault("modified_frame_numbers", [])
            record.setdefault("modified_frame_count", 0)
            record.setdefault("manual_modification_ratio", 0.0)
            record.setdefault("save_count", 0)
            record.setdefault("save_history", [])
            record.setdefault("end_diastolic", {
                "frame_index": None,
                "frame_number": None,
                "source": "",
                "pixel_count": None,
                "area_pixels": None,
                "area_mm2": None,
            })
            record.setdefault("end_systolic", {
                "frame_index": None,
                "frame_number": None,
                "source": "",
                "pixel_count": None,
                "area_pixels": None,
                "area_mm2": None,
            })
            record.setdefault("cardiac_phase_confirmed", False)
            record.setdefault("cardiac_phase_confirmed_at", None)
            record.setdefault("cardiac_phase_confidence", "")
            record.setdefault(
                "annotation_decision",
                "required" if int(entry.get("mask_frame_count") or 0) else "undecided",
            )
            record.setdefault("annotation_decision_confirmed", False)
            record.setdefault("annotation_decision_at", None)
            record.setdefault("annotation_decision_reviewer", "")
            record.setdefault("annotation_decision_history", [])
        audit["series_catalog_count"] = len(records)
        return self.writePatientAudit(patientRoot, audit)

    def updatePatientSeriesAudit(
        self,
        patientRoot,
        seriesId,
        segmentationSequenceNode,
        imageSequenceNode,
        browserNode=None,
        event="phase-update",
        modifiedFrameIndices=None,
    ):
        if event not in ["open", "phase-update", "save"]:
            raise ValueError("Unsupported patient audit event: {0}".format(event))
        audit = self.loadPatientAudit(patientRoot)
        now = self._utcNow()
        frameCount = segmentationSequenceNode.GetNumberOfDataNodes()
        seriesRecords = audit.setdefault("series", {})
        record = seriesRecords.setdefault(seriesId, {})
        if event == "open":
            if not record.get("first_opened_at"):
                record["first_opened_at"] = now
            record["last_opened_at"] = now
        firstOpenedAt = record.get("first_opened_at") or now
        if not record.get("first_opened_at"):
            record["first_opened_at"] = firstOpenedAt
        if not record.get("last_opened_at"):
            record["last_opened_at"] = firstOpenedAt
        if not record.get("last_modified_at"):
            record["last_modified_at"] = firstOpenedAt
            record["last_modified_source"] = "series-opened-default"
        record["frame_count"] = frameCount

        phaseState = self.getCardiacPhaseState(segmentationSequenceNode)
        areaMetrics = self.cardiacPhaseAreaMetrics(
            segmentationSequenceNode,
            imageSequenceNode,
            browserNode,
        )

        def phaseRecord(phase):
            frameIndex = phaseState["ed_frame"] if phase == "ed" else phaseState["es_frame"]
            source = phaseState["ed_source"] if phase == "ed" else phaseState["es_source"]
            return {
                "frame_index": frameIndex if frameIndex >= 0 else None,
                "frame_number": frameIndex + 1 if frameIndex >= 0 else None,
                "source": source,
                "pixel_count": areaMetrics[phase]["pixel_count"],
                "area_pixels": areaMetrics[phase]["area_pixels"],
                "area_mm2": areaMetrics[phase]["area_mm2"],
            }

        record["end_diastolic"] = phaseRecord("ed")
        record["end_systolic"] = phaseRecord("es")
        record["cardiac_phase_confirmed"] = phaseState["confirmed"]
        record["cardiac_phase_confirmed_at"] = phaseState["confirmed_at"] or None
        record["cardiac_phase_metric"] = phaseState["metric"]
        record["cardiac_phase_confidence"] = phaseState["confidence"]

        existingModifiedIndices = set()
        for value in record.get("modified_frame_indices", []):
            try:
                frameIndex = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= frameIndex < frameCount:
                existingModifiedIndices.add(frameIndex)
        latestModifiedIndexSet = set()
        for value in modifiedFrameIndices or []:
            try:
                frameIndex = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= frameIndex < frameCount:
                latestModifiedIndexSet.add(frameIndex)
        latestModifiedIndices = sorted(latestModifiedIndexSet)
        if event == "save":
            reviewer = segmentationSequenceNode.GetAttribute(
                "CineCMRQC.DefaultReviewer"
            ) or ""
            self._appendAnnotationDecisionHistory(
                record,
                "required",
                reviewer,
                True,
                now,
            )
            record["annotation_decision"] = "required"
            record["annotation_decision_confirmed"] = True
            record["annotation_decision_at"] = now
            record["annotation_decision_reviewer"] = reviewer
            record["mask_present_at_decision"] = True
            record["mask_deleted"] = False
            record["mask_frame_count"] = frameCount
            record["review_status"] = "completed-with-annotation"
            record["data_status"] = "ready"
            existingModifiedIndices.update(latestModifiedIndices)
            record["latest_save_modified_frame_indices"] = latestModifiedIndices
            record["latest_save_modified_frame_numbers"] = [
                value + 1 for value in latestModifiedIndices
            ]
            record["latest_save_modified_frame_count"] = len(latestModifiedIndices)
            record["last_saved_at"] = now
            record["save_count"] = int(record.get("save_count", 0)) + 1
            if latestModifiedIndices:
                record["last_modified_at"] = now
                record["last_modified_source"] = "manual-mask-save"
            history = record.setdefault("save_history", [])
            history.append({
                "saved_at": now,
                "save_type": "annotation-required",
                "modified_frame_indices": latestModifiedIndices,
                "modified_frame_numbers": [value + 1 for value in latestModifiedIndices],
                "modified_frame_count": len(latestModifiedIndices),
                "end_diastolic": dict(record["end_diastolic"]),
                "end_systolic": dict(record["end_systolic"]),
                "cardiac_phase_confirmed": phaseState["confirmed"],
            })
        modifiedIndices = sorted(existingModifiedIndices)
        record["modified_frame_indices"] = modifiedIndices
        record["modified_frame_numbers"] = [value + 1 for value in modifiedIndices]
        record["modified_frame_count"] = len(modifiedIndices)
        record["manual_modification_ratio"] = (
            round(float(len(modifiedIndices)) / float(frameCount), 6)
            if frameCount
            else 0.0
        )
        record["updated_at"] = now
        return self.writePatientAudit(patientRoot, audit)

    def getOrCreateSegmentEditorNode(self):
        segmentEditorNode = slicer.mrmlScene.GetSingletonNode("CineCMRQC", "vtkMRMLSegmentEditorNode")
        if segmentEditorNode is None:
            segmentEditorNode = slicer.mrmlScene.CreateNodeByClass("vtkMRMLSegmentEditorNode")
            segmentEditorNode.UnRegister(None)
            segmentEditorNode.SetSingletonTag("CineCMRQC")
            slicer.mrmlScene.AddNode(segmentEditorNode)
        return segmentEditorNode

    def configureSegmentEditor(self, segmentationNode, sourceVolumeNode, segmentEditorWidget=None):
        if segmentEditorWidget is None:
            segmentEditorWidget = slicer.modules.segmenteditor.widgetRepresentation().self().editor
            segmentEditorNode = slicer.mrmlScene.GetSingletonNode("SegmentEditor", "vtkMRMLSegmentEditorNode")
            if segmentEditorNode is None:
                segmentEditorNode = slicer.mrmlScene.CreateNodeByClass("vtkMRMLSegmentEditorNode")
                segmentEditorNode.UnRegister(None)
                segmentEditorNode.SetSingletonTag("SegmentEditor")
                slicer.mrmlScene.AddNode(segmentEditorNode)
        else:
            segmentEditorNode = self.getOrCreateSegmentEditorNode()
        segmentEditorWidget.setMRMLScene(slicer.mrmlScene)
        segmentEditorWidget.setMRMLSegmentEditorNode(segmentEditorNode)
        segmentEditorWidget.setSegmentationNode(segmentationNode)
        if sourceVolumeNode:
            segmentEditorWidget.setSourceVolumeNode(sourceVolumeNode)

    def validateBinding(self, imageSequenceNode, segmentationSequenceNode, browserNode):
        if not imageSequenceNode:
            raise ValueError("No image sequence selected.")
        if not browserNode:
            raise ValueError("No sequence browser selected.")
        if browserNode.GetMasterSequenceNode() != imageSequenceNode:
            raise ValueError("The selected image sequence is not the browser master sequence.")
        imageFrames = imageSequenceNode.GetNumberOfDataNodes()
        if imageFrames < 1:
            raise ValueError("Image sequence has no frames.")
        if not segmentationSequenceNode:
            return "MRI Sequence 有效（{0} 帧），但尚未绑定 Mask Sequence。".format(imageFrames)
        maskFrames = segmentationSequenceNode.GetNumberOfDataNodes()
        if maskFrames != imageFrames:
            raise ValueError("Frame mismatch: image={0}, mask={1}".format(imageFrames, maskFrames))
        if not browserNode.IsSynchronizedSequenceNode(segmentationSequenceNode):
            raise ValueError("The mask sequence is not synchronized by the selected browser.")
        if not browserNode.GetSaveChanges(segmentationSequenceNode):
            raise ValueError("Frame-specific Save changes is disabled for the mask sequence.")
        imageIndexValues = [imageSequenceNode.GetNthIndexValue(i) for i in range(imageFrames)]
        maskIndexValues = [segmentationSequenceNode.GetNthIndexValue(i) for i in range(maskFrames)]
        if imageIndexValues != maskIndexValues:
            raise ValueError("Image/mask temporal index values do not match.")
        proxySegmentation = browserNode.GetProxyNode(segmentationSequenceNode)
        if not proxySegmentation:
            raise ValueError("Mask sequence has no proxy segmentation in this browser.")
        return "绑定正常：MRI {0} 帧，Mask {1} 帧，已启用逐帧修改保存。".format(
            imageFrames, maskFrames)

    def exportSegmentationSequenceAsLabelmaps(
        self,
        segmentationSequenceNode,
        imageSequenceNode,
        browserNode,
        outputFolder,
        prefix,
    ):
        if not os.path.isdir(outputFolder):
            os.makedirs(outputFolder)

        manifestRows = []
        exportedFramePaths = []
        patientRoot = imageSequenceNode.GetAttribute("CineCMRQC.PatientRoot") or ""
        patientId = os.path.basename(patientRoot) if patientRoot else ""
        seriesId = imageSequenceNode.GetAttribute("CineCMRQC.SeriesID") or ""
        doctorReferenceFrames = imageSequenceNode.GetAttribute("CineCMRQC.DoctorReferenceFrames") or ""
        appliedMaskTransform = segmentationSequenceNode.GetAttribute(
            "CineCMRQC.AppliedMaskTransform"
        ) or "none"
        sourceMaskOrientation = segmentationSequenceNode.GetAttribute(
            "CineCMRQC.SourceMaskOrientation"
        ) or "unknown"
        cardiacPhaseState = self.getCardiacPhaseState(segmentationSequenceNode)
        cardiacPhaseAreas = self.cardiacPhaseAreaMetrics(
            segmentationSequenceNode,
            imageSequenceNode,
            browserNode,
        )
        originalIndex = browserNode.GetSelectedItemNumber()
        labelmapNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLabelMapVolumeNode", "__CineCMRQC_export_labelmap")
        try:
            for frameIndex in range(segmentationSequenceNode.GetNumberOfDataNodes()):
                browserNode.SetSelectedItemNumber(frameIndex)
                slicer.modules.sequences.logic().UpdateProxyNodesFromSequences(browserNode)
                segmentationNode = browserNode.GetProxyNode(segmentationSequenceNode)
                referenceVolumeNode = browserNode.GetProxyNode(imageSequenceNode)
                if not segmentationNode or not referenceVolumeNode:
                    raise ValueError("Missing proxy node at frame {0}".format(frameIndex))

                labelmapNode.SetName("__CineCMRQC_export_labelmap_{0:03d}".format(frameIndex))
                self._exportSegmentationToLabelmapPreservingValues(
                    segmentationNode,
                    referenceVolumeNode,
                    labelmapNode,
                )

                fileName = "{0}_frame{1:03d}.nii.gz".format(prefix, frameIndex)
                filePath = os.path.join(outputFolder, fileName)
                import SimpleITK as sitk
                import sitkUtils

                exportedImage = sitkUtils.PullVolumeFromSlicer(labelmapNode)
                sitk.WriteImage(exportedImage, filePath, True)
                if not os.path.isfile(filePath):
                    raise ValueError("Failed to save: {0}".format(filePath))
                exportedFramePaths.append(filePath)
                reviewMetadata = self.getFrameReviewMetadata(
                    segmentationSequenceNode,
                    frameIndex,
                )
                corrected = self.isFrameCorrected(
                    segmentationSequenceNode,
                    imageSequenceNode,
                    browserNode,
                    frameIndex,
                )
                manifestRows.append({
                    "patient_id": patientId,
                    "series_id": seriesId,
                    "frame": frameIndex,
                    "index_value": segmentationSequenceNode.GetNthIndexValue(frameIndex),
                    "index_name": imageSequenceNode.GetIndexName() or "",
                    "index_unit": imageSequenceNode.GetIndexUnit() or "",
                    "doctor_reference_frames": doctorReferenceFrames,
                    "applied_mask_transform": appliedMaskTransform,
                    "source_mask_orientation": sourceMaskOrientation,
                    "cardiac_phase": (
                        "ED"
                        if frameIndex == cardiacPhaseState["ed_frame"]
                        else "ES"
                        if frameIndex == cardiacPhaseState["es_frame"]
                        else ""
                    ),
                    "end_diastolic_frame": (
                        cardiacPhaseState["ed_frame"]
                        if cardiacPhaseState["ed_frame"] >= 0
                        else ""
                    ),
                    "end_systolic_frame": (
                        cardiacPhaseState["es_frame"]
                        if cardiacPhaseState["es_frame"] >= 0
                        else ""
                    ),
                    "end_diastolic_source": cardiacPhaseState["ed_source"],
                    "end_systolic_source": cardiacPhaseState["es_source"],
                    "cardiac_phase_confirmed": (
                        "1" if cardiacPhaseState["confirmed"] else "0"
                    ),
                    "cardiac_phase_confirmed_at": cardiacPhaseState["confirmed_at"],
                    "cardiac_phase_metric": cardiacPhaseState["metric"],
                    "cardiac_phase_confidence": cardiacPhaseState["confidence"],
                    "end_diastolic_pixel_count": cardiacPhaseAreas["ed"]["pixel_count"],
                    "end_diastolic_area_mm2": cardiacPhaseAreas["ed"]["area_mm2"],
                    "end_systolic_pixel_count": cardiacPhaseAreas["es"]["pixel_count"],
                    "end_systolic_area_mm2": cardiacPhaseAreas["es"]["area_mm2"],
                    "reviewed": "1" if self.isFrameReviewed(segmentationSequenceNode, frameIndex) else "0",
                    "corrected": "1" if corrected else "0",
                    "reviewer": reviewMetadata["reviewer"],
                    "review_timestamp": reviewMetadata["timestamp"],
                    "review_comment": reviewMetadata["comment"],
                    "source_image_path": self._sourcePath(imageSequenceNode.GetNthDataNode(frameIndex)),
                    "source_mask_path": self._sourcePath(segmentationSequenceNode.GetNthDataNode(frameIndex)),
                    "mask_path": filePath,
                })
        finally:
            temporaryColorNode = None
            if labelmapNode and labelmapNode.GetDisplayNode():
                temporaryColorNode = labelmapNode.GetDisplayNode().GetColorNode()
            if labelmapNode and labelmapNode.GetScene():
                slicer.mrmlScene.RemoveNode(labelmapNode)
            if (
                temporaryColorNode
                and temporaryColorNode.GetScene()
                and temporaryColorNode.GetName().startswith("__CineCMRQC_export_labelmap")
            ):
                slicer.mrmlScene.RemoveNode(temporaryColorNode)
            if originalIndex >= 0:
                browserNode.SetSelectedItemNumber(originalIndex)
                slicer.modules.sequences.logic().UpdateProxyNodesFromSequences(browserNode)

        self._writeFourDimensionalMask(
            exportedFramePaths,
            segmentationSequenceNode,
            os.path.join(outputFolder, prefix + "_4d.nii.gz"),
        )
        self._writeLabelDefinitions(
            segmentationSequenceNode,
            os.path.join(outputFolder, "labels.csv"),
        )

        manifestPath = os.path.join(outputFolder, "manifest.csv")
        with open(manifestPath, "w", encoding="utf-8-sig", newline="") as fp:
            writer = csv.DictWriter(
                fp,
                fieldnames=[
                    "patient_id",
                    "series_id",
                    "frame",
                    "index_value",
                    "index_name",
                    "index_unit",
                    "doctor_reference_frames",
                    "applied_mask_transform",
                    "source_mask_orientation",
                    "cardiac_phase",
                    "end_diastolic_frame",
                    "end_systolic_frame",
                    "end_diastolic_source",
                    "end_systolic_source",
                    "cardiac_phase_confirmed",
                    "cardiac_phase_confirmed_at",
                    "cardiac_phase_metric",
                    "cardiac_phase_confidence",
                    "end_diastolic_pixel_count",
                    "end_diastolic_area_mm2",
                    "end_systolic_pixel_count",
                    "end_systolic_area_mm2",
                    "reviewed",
                    "corrected",
                    "reviewer",
                    "review_timestamp",
                    "review_comment",
                    "source_image_path",
                    "source_mask_path",
                    "mask_path",
                ],
            )
            writer.writeheader()
            for row in manifestRows:
                writer.writerow(row)
        return manifestPath

    def createSegmentationSequenceSourceFiles(
        self,
        segmentationSequenceNode,
        imageSequenceNode,
        browserNode,
        outputFolder,
    ):
        self.validateBinding(imageSequenceNode, segmentationSequenceNode, browserNode)
        outputFolder = os.path.abspath(outputFolder)
        seriesFolder = os.path.dirname(outputFolder)
        segmentationRoot = os.path.dirname(seriesFolder)
        seriesId = imageSequenceNode.GetAttribute("CineCMRQC.SeriesID") or "cine_mask"
        if os.path.basename(seriesFolder) != seriesId:
            raise ValueError("新建 Mask 的输出目录与当前 Series ID 不一致。")
        if os.path.basename(outputFolder) != "sequence":
            raise ValueError("新建 Mask 必须写入当前 Series 的 sequence 目录。")
        os.makedirs(segmentationRoot, exist_ok=True)
        os.makedirs(seriesFolder, exist_ok=True)
        if os.path.isdir(outputFolder) and self._collectFrameFiles(outputFolder):
            raise ValueError("Mask sequence 目录已包含医学影像文件，不能作为新 Mask 写入。")

        temporaryFolder = tempfile.mkdtemp(
            prefix=".cinecmrqc-new-mask-",
            dir=seriesFolder,
        )
        frameCount = segmentationSequenceNode.GetNumberOfDataNodes()
        createdPaths = []
        try:
            temporaryManifestPath = self.exportSegmentationSequenceAsLabelmaps(
                segmentationSequenceNode,
                imageSequenceNode,
                browserNode,
                temporaryFolder,
                "__cinecmrqc_new",
            )
            generatedFramePaths = [
                os.path.join(
                    temporaryFolder,
                    "__cinecmrqc_new_frame{0:03d}.nii.gz".format(frameIndex),
                )
                for frameIndex in range(frameCount)
            ]
            generatedSummaryPath = os.path.join(
                temporaryFolder,
                "__cinecmrqc_new_4d.nii.gz",
            )
            if any(not os.path.isfile(path) for path in generatedFramePaths):
                raise ValueError("新 Mask 的逐帧导出不完整。")
            if not os.path.isfile(generatedSummaryPath):
                raise ValueError("新 Mask 的 4D 汇总导出失败。")

            os.makedirs(outputFolder, exist_ok=True)
            sourcePaths = [
                os.path.join(
                    outputFolder,
                    "frame_{0:05d}_seg.nii.gz".format(frameIndex),
                )
                for frameIndex in range(frameCount)
            ]
            summaryPath = os.path.join(seriesFolder, seriesId + "_seg.nii.gz")
            labelsPath = os.path.join(outputFolder, "labels.csv")
            manifestPath = os.path.join(outputFolder, "manifest.csv")
            targets = sourcePaths + [summaryPath, labelsPath, manifestPath]
            if any(os.path.exists(path) for path in targets):
                raise ValueError("新 Mask 的目标文件已存在，已取消写入。")

            for generatedPath, sourcePath in zip(generatedFramePaths, sourcePaths):
                os.replace(generatedPath, sourcePath)
                createdPaths.append(sourcePath)
            os.replace(generatedSummaryPath, summaryPath)
            createdPaths.append(summaryPath)
            os.replace(os.path.join(temporaryFolder, "labels.csv"), labelsPath)
            createdPaths.append(labelsPath)

            with open(temporaryManifestPath, "r", encoding="utf-8-sig", newline="") as fp:
                reader = csv.DictReader(fp)
                rows = list(reader)
                fieldnames = list(reader.fieldnames or [])
            if len(rows) != frameCount:
                raise ValueError("新 Mask 的 manifest 帧数不正确。")
            if "backup_mask_path" not in fieldnames:
                fieldnames.append("backup_mask_path")
            for frameIndex, row in enumerate(rows):
                row["source_mask_path"] = sourcePaths[frameIndex]
                row["mask_path"] = sourcePaths[frameIndex]
                row["backup_mask_path"] = ""
            replacementManifestPath = os.path.join(
                temporaryFolder,
                "manifest_rewritten.csv",
            )
            with open(replacementManifestPath, "w", encoding="utf-8-sig", newline="") as fp:
                writer = csv.DictWriter(fp, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            os.replace(replacementManifestPath, manifestPath)
            createdPaths.append(manifestPath)

            for frameIndex, sourcePath in enumerate(sourcePaths):
                segmentationSequenceNode.GetNthDataNode(frameIndex).SetAttribute(
                    "CineCMRQC.SourcePath",
                    sourcePath,
                )
            segmentationSequenceNode.SetAttribute(
                "CineCMRQC.SourceMaskFolder",
                outputFolder,
            )
            segmentationSequenceNode.SetAttribute("CineCMRQC.SourceMaskExists", "1")
            segmentationSequenceNode.SetAttribute("CineCMRQC.SourceMaskOrientation", "aligned")
            segmentationSequenceNode.SetAttribute("CineCMRQC.AppliedMaskTransform", "none")
            self.initializeBaselineDigests(
                segmentationSequenceNode,
                imageSequenceNode,
                "saved-new-source-files",
            )
            self.markCardiacPhaseStateSaved(segmentationSequenceNode)
            return manifestPath
        except Exception:
            for path in reversed(createdPaths):
                if os.path.isfile(path):
                    os.remove(path)
            raise
        finally:
            shutil.rmtree(temporaryFolder, ignore_errors=True)

    def overwriteSegmentationSequenceSourceFiles(
        self,
        segmentationSequenceNode,
        imageSequenceNode,
        browserNode,
        outputFolder,
    ):
        self.validateBinding(imageSequenceNode, segmentationSequenceNode, browserNode)
        outputFolder = os.path.abspath(outputFolder)
        frameCount = segmentationSequenceNode.GetNumberOfDataNodes()
        rawSourcePaths = [
            self._sourcePath(segmentationSequenceNode.GetNthDataNode(index))
            for index in range(frameCount)
        ]
        if any(not path for path in rawSourcePaths):
            raise ValueError("无法确定全部原始 Mask 文件，已取消覆盖。")
        sourcePaths = [os.path.abspath(path) for path in rawSourcePaths]
        if len(set(sourcePaths)) != frameCount:
            raise ValueError("原始 Mask 文件路径重复，已取消覆盖。")
        if any(os.path.dirname(path) != outputFolder for path in sourcePaths):
            raise ValueError("原始 Mask 文件不全在当前输出目录中，已取消覆盖。")
        if any(not os.path.isfile(path) for path in sourcePaths):
            raise ValueError("部分原始 Mask 文件不存在，已取消覆盖。")
        extraFrameFiles = sorted(set(self._collectFrameFiles(outputFolder)) - set(sourcePaths))
        if extraFrameFiles:
            raise ValueError(
                "Mask 目录中存在不属于当前 25 帧的额外医学影像文件：{0}".format(
                    ", ".join(os.path.basename(path) for path in extraFrameFiles)
                )
            )

        seriesId = imageSequenceNode.GetAttribute("CineCMRQC.SeriesID") or "cine_mask"
        summaryPath = os.path.join(
            os.path.dirname(outputFolder),
            seriesId + "_seg.nii.gz",
        )
        manifestPath = os.path.join(outputFolder, "manifest.csv")
        labelsPath = os.path.join(outputFolder, "labels.csv")
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backupFolder = os.path.join(outputFolder, "backup_" + timestamp)
        suffix = 1
        while os.path.exists(backupFolder):
            backupFolder = os.path.join(
                outputFolder,
                "backup_{0}_{1}".format(timestamp, suffix),
            )
            suffix += 1
        temporaryFolder = tempfile.mkdtemp(prefix=".cinecmrqc-save-", dir=outputFolder)
        backupByTarget = {}
        targets = list(sourcePaths) + [summaryPath, labelsPath, manifestPath]
        originallyExisting = set(path for path in targets if os.path.exists(path))
        try:
            temporaryManifestPath = self.exportSegmentationSequenceAsLabelmaps(
                segmentationSequenceNode,
                imageSequenceNode,
                browserNode,
                temporaryFolder,
                "__cinecmrqc_save",
            )
            generatedFramePaths = [
                os.path.join(
                    temporaryFolder,
                    "__cinecmrqc_save_frame{0:03d}.nii.gz".format(index),
                )
                for index in range(frameCount)
            ]
            generatedSummaryPath = os.path.join(
                temporaryFolder,
                "__cinecmrqc_save_4d.nii.gz",
            )
            if any(not os.path.isfile(path) for path in generatedFramePaths):
                raise ValueError("临时逐帧 Mask 导出不完整，已取消覆盖。")
            if not os.path.isfile(generatedSummaryPath):
                raise ValueError("临时 4D Mask 导出失败，已取消覆盖。")

            os.makedirs(backupFolder)
            for targetPath in targets:
                if not os.path.isfile(targetPath):
                    continue
                backupPath = os.path.join(backupFolder, os.path.basename(targetPath))
                shutil.copy2(targetPath, backupPath)
                backupByTarget[targetPath] = backupPath

            for generatedPath, sourcePath in zip(generatedFramePaths, sourcePaths):
                os.replace(generatedPath, sourcePath)
            os.replace(generatedSummaryPath, summaryPath)
            os.replace(os.path.join(temporaryFolder, "labels.csv"), labelsPath)

            with open(temporaryManifestPath, "r", encoding="utf-8-sig", newline="") as fp:
                reader = csv.DictReader(fp)
                rows = list(reader)
                fieldnames = list(reader.fieldnames or [])
            if len(rows) != frameCount:
                raise ValueError("临时 manifest 帧数不正确，已恢复原文件。")
            if "backup_mask_path" not in fieldnames:
                fieldnames.append("backup_mask_path")
            for frameIndex, row in enumerate(rows):
                sourcePath = sourcePaths[frameIndex]
                row["source_mask_path"] = sourcePath
                row["mask_path"] = sourcePath
                row["backup_mask_path"] = backupByTarget.get(sourcePath, "")
            replacementManifestPath = os.path.join(
                temporaryFolder,
                "manifest_rewritten.csv",
            )
            with open(replacementManifestPath, "w", encoding="utf-8-sig", newline="") as fp:
                writer = csv.DictWriter(fp, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            os.replace(replacementManifestPath, manifestPath)

            self.initializeBaselineDigests(
                segmentationSequenceNode,
                imageSequenceNode,
                "saved-source-files",
            )
            segmentationSequenceNode.SetAttribute(
                "CineCMRQC.LastSaveBackupFolder",
                backupFolder,
            )
            segmentationSequenceNode.SetAttribute(
                "CineCMRQC.SourceMaskOrientation",
                "aligned",
            )
            segmentationSequenceNode.SetAttribute(
                "CineCMRQC.AppliedMaskTransform",
                "none",
            )
            self.markCardiacPhaseStateSaved(segmentationSequenceNode)
            return manifestPath, backupFolder
        except Exception:
            for targetPath in targets:
                backupPath = backupByTarget.get(targetPath)
                if backupPath and os.path.isfile(backupPath):
                    shutil.copy2(backupPath, targetPath)
                elif targetPath not in originallyExisting and os.path.isfile(targetPath):
                    os.remove(targetPath)
            raise
        finally:
            shutil.rmtree(temporaryFolder, ignore_errors=True)

    def exportPatientSeries(self, seriesItems, outputFolder):
        if not seriesItems:
            raise ValueError("No patient series were provided for export.")
        if not os.path.isdir(outputFolder):
            os.makedirs(outputFolder)
        manifestPaths = []
        usedSeriesIds = set()
        for item in seriesItems:
            seriesId = str(item.get("series_id") or "series").strip()
            safeSeriesId = re.sub(r"[^A-Za-z0-9_.-]+", "_", seriesId).strip("._") or "series"
            if safeSeriesId in usedSeriesIds:
                raise ValueError("Duplicate patient export series ID: {0}".format(seriesId))
            usedSeriesIds.add(safeSeriesId)
            seriesFolder = os.path.join(outputFolder, safeSeriesId)
            manifestPath = self.exportSegmentationSequenceAsLabelmaps(
                item.get("segmentation_sequence"),
                item.get("image_sequence"),
                item.get("browser"),
                seriesFolder,
                safeSeriesId + "_corrected",
            )
            manifestPaths.append(manifestPath)
        return self.combinePatientSeriesManifests(manifestPaths, outputFolder)

    def combinePatientSeriesManifests(self, manifestPaths, outputFolder):
        if not manifestPaths:
            raise ValueError("No patient series manifests were provided.")
        allRows = []
        fieldnames = None
        for manifestPath in manifestPaths:
            with open(manifestPath, "r", encoding="utf-8-sig", newline="") as fp:
                reader = csv.DictReader(fp)
                currentFieldnames = list(reader.fieldnames or [])
                if fieldnames is None:
                    fieldnames = currentFieldnames
                elif currentFieldnames != fieldnames:
                    raise ValueError("Patient series manifest columns do not match.")
                allRows.extend(list(reader))
        patientManifestPath = os.path.join(outputFolder, "patient_manifest.csv")
        with open(patientManifestPath, "w", encoding="utf-8-sig", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=fieldnames or [])
            writer.writeheader()
            writer.writerows(allRows)
        return patientManifestPath

    def _exportSegmentationToLabelmapPreservingValues(
        self,
        segmentationNode,
        referenceVolumeNode,
        labelmapNode,
    ):
        import numpy as np

        referenceArray = slicer.util.arrayFromVolume(referenceVolumeNode)
        segmentation = segmentationNode.GetSegmentation()
        labelValues = []
        for segmentIndex in range(segmentation.GetNumberOfSegments()):
            segmentId = segmentation.GetNthSegmentID(segmentIndex)
            segment = segmentation.GetSegment(segmentId)
            labelValues.append(self._labelValueForSegment(segmentId, segment))
        maximumLabelValue = max(labelValues) if labelValues else 0
        if maximumLabelValue <= 255:
            outputType = np.uint8
        elif maximumLabelValue <= 65535:
            outputType = np.uint16
        else:
            outputType = np.uint32
        mergedArray = np.zeros(referenceArray.shape, dtype=outputType)
        binaryName = slicer.vtkSegmentationConverter.GetSegmentationBinaryLabelmapRepresentationName()
        segmentsByLabelValue = []
        for segmentIndex in range(segmentation.GetNumberOfSegments()):
            segmentId = segmentation.GetNthSegmentID(segmentIndex)
            segment = segmentation.GetSegment(segmentId)
            labelValue = self._labelValueForSegment(segmentId, segment)
            segmentsByLabelValue.append((labelValue, segmentId, segment))
        for labelValue, segmentId, segment in sorted(segmentsByLabelValue):
            if not segment.GetRepresentation(binaryName):
                continue
            segmentArray = slicer.util.arrayFromSegmentBinaryLabelmap(
                segmentationNode,
                segmentId,
                referenceVolumeNode,
            )
            if segmentArray.shape != mergedArray.shape:
                raise ValueError(
                    "Segment {0} geometry does not match the reference volume.".format(segmentId)
                )
            mergedArray[segmentArray > 0] = labelValue
        slicer.util.updateVolumeFromArray(labelmapNode, mergedArray)
        labelmapNode.CopyOrientation(referenceVolumeNode)

    def _writeFourDimensionalMask(self, framePaths, sequenceNode, outputPath):
        if not framePaths:
            raise ValueError("No exported mask frames are available for 4D export.")
        import SimpleITK as sitk

        temporalOrigin, temporalSpacing = self._sequenceTemporalOriginAndSpacing(sequenceNode)
        frameImages = []
        for framePath in framePaths:
            frameImage = sitk.Cast(sitk.ReadImage(framePath), sitk.sitkUInt16)
            sitk.WriteImage(frameImage, framePath, True)
            frameImages.append(frameImage)
        fourDimensionalImage = sitk.JoinSeries(frameImages, temporalOrigin, temporalSpacing)
        sitk.WriteImage(fourDimensionalImage, outputPath, True)

    def _sequenceTemporalOriginAndSpacing(self, sequenceNode):
        values = []
        for frameIndex in range(sequenceNode.GetNumberOfDataNodes()):
            try:
                values.append(float(sequenceNode.GetNthIndexValue(frameIndex)))
            except (TypeError, ValueError):
                return 0.0, 1.0
        if not values:
            return 0.0, 1.0
        if len(values) == 1:
            return values[0], 1.0
        differences = [values[index + 1] - values[index] for index in range(len(values) - 1)]
        meanDifference = sum(differences) / float(len(differences))
        tolerance = max(1e-6, abs(meanDifference) * 1e-4)
        if meanDifference <= 0 or any(abs(value - meanDifference) > tolerance for value in differences):
            return values[0], 1.0
        return values[0], meanDifference

    def _writeLabelDefinitions(self, segmentationSequenceNode, outputPath):
        if not segmentationSequenceNode or segmentationSequenceNode.GetNumberOfDataNodes() < 1:
            raise ValueError("Segmentation sequence has no frames.")
        segmentationNode = segmentationSequenceNode.GetNthDataNode(0)
        segmentation = segmentationNode.GetSegmentation()
        with open(outputPath, "w", encoding="utf-8-sig", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=["label_value", "segment_name", "segment_id"])
            writer.writeheader()
            for segmentIndex in range(segmentation.GetNumberOfSegments()):
                segmentId = segmentation.GetNthSegmentID(segmentIndex)
                segment = segmentation.GetSegment(segmentId)
                writer.writerow({
                    "label_value": self._labelValueForSegment(segmentId, segment),
                    "segment_name": segment.GetName(),
                    "segment_id": segmentId,
                })

    def _ensureExportableLabelmap(self, labelmapNode, referenceVolumeNode):
        imageData = labelmapNode.GetImageData()
        hasValidImage = False
        if imageData and imageData.GetNumberOfScalarComponents() > 0:
            dims = imageData.GetDimensions()
            hasValidImage = imageData.GetNumberOfPoints() > 0 and min(dims) > 0
        if hasValidImage:
            labelmapNode.CopyOrientation(referenceVolumeNode)
            return
        import numpy as np
        referenceArray = slicer.util.arrayFromVolume(referenceVolumeNode)
        emptyArray = np.zeros(referenceArray.shape, dtype=np.uint8)
        slicer.util.updateVolumeFromArray(labelmapNode, emptyArray)
        labelmapNode.CopyOrientation(referenceVolumeNode)

    def _createSegmentationFromLabelmap(self, labelmapNode, referenceVolumeNode, labels, name):
        segmentationNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode", name)
        segmentationNode.CreateDefaultDisplayNodes()
        if referenceVolumeNode:
            segmentationNode.SetReferenceImageGeometryParameterFromVolumeNode(referenceVolumeNode)
        ok = slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(labelmapNode, segmentationNode)
        if not ok:
            raise ValueError("Failed to import labelmap into segmentation.")
        self._normalizeSegments(segmentationNode, labels)
        sourcePath = self._sourcePath(labelmapNode)
        if sourcePath:
            segmentationNode.SetAttribute("CineCMRQC.SourcePath", sourcePath)
        sourceFrameIndex = labelmapNode.GetAttribute("CineCMRQC.SourceFrameIndex")
        if sourceFrameIndex is not None:
            segmentationNode.SetAttribute("CineCMRQC.SourceFrameIndex", sourceFrameIndex)
        segmentationNode.SetAttribute("CineCMRQC.Reviewed", "0")
        return segmentationNode

    def validateOrAlignMaskGeometry(self, maskNode, imageNode, frameIndex, useImageGeometry=False):
        if not maskNode or not imageNode or not maskNode.GetImageData() or not imageNode.GetImageData():
            raise ValueError("Missing image data at frame {0}.".format(frameIndex))
        maskDimensions = tuple(maskNode.GetImageData().GetDimensions())
        imageDimensions = tuple(imageNode.GetImageData().GetDimensions())
        if maskDimensions != imageDimensions:
            raise ValueError(
                "Mask geometry mismatch at frame {0}: image dimensions={1}, mask dimensions={2}. "
                "The arrays must have identical voxel dimensions.".format(
                    frameIndex,
                    imageDimensions,
                    maskDimensions,
                )
            )
        if useImageGeometry:
            maskNode.CopyOrientation(imageNode)
            return
        imageMatrix = vtk.vtkMatrix4x4()
        maskMatrix = vtk.vtkMatrix4x4()
        imageNode.GetIJKToRASMatrix(imageMatrix)
        maskNode.GetIJKToRASMatrix(maskMatrix)
        maximumDifference = max(
            abs(imageMatrix.GetElement(row, column) - maskMatrix.GetElement(row, column))
            for row in range(4)
            for column in range(4)
        )
        if maximumDifference > 1e-4:
            raise ValueError(
                "Mask spatial geometry mismatch at frame {0} (maximum IJK-to-RAS difference {1:.6g}). "
                "Fix the mask header, or explicitly enable 'Masks are voxel-aligned; use MRI geometry' "
                "when the arrays are known to be voxel-aligned.".format(frameIndex, maximumDifference)
            )

    def _sourcePath(self, node):
        if not node:
            return ""
        return node.GetAttribute("CineCMRQC.SourcePath") or ""

    def _normalizeSegments(self, segmentationNode, labels):
        segmentation = segmentationNode.GetSegmentation()
        segmentsByLabelValue = {}
        effectiveLabels = dict(labels)
        for segmentIndex in range(segmentation.GetNumberOfSegments()):
            segmentId = segmentation.GetNthSegmentID(segmentIndex)
            segment = segmentation.GetSegment(segmentId)
            labelValue = int(segment.GetLabelValue())
            segmentCopy = slicer.vtkSegment()
            segmentCopy.DeepCopy(segment)
            segmentsByLabelValue[labelValue] = segmentCopy
            if labelValue not in effectiveLabels:
                effectiveLabels[labelValue] = "Label_{0}".format(labelValue)

        segmentation.RemoveAllSegments()
        for labelValue in sorted(effectiveLabels):
            segment = segmentsByLabelValue.get(labelValue)
            if segment is None:
                segment = slicer.vtkSegment()
            segment.SetName(effectiveLabels[labelValue])
            segment.SetLabelValue(labelValue)
            stableSegmentId = self._stableSegmentId(labelValue)
            if not segmentation.AddSegment(segment, stableSegmentId):
                raise ValueError(
                    "Failed to add normalized segment {0} for label {1}.".format(
                        stableSegmentId,
                        labelValue,
                    )
                )

    def _ensureSegments(self, segmentationNode, labels):
        segmentation = segmentationNode.GetSegmentation()
        for labelValue in sorted(labels):
            name = labels[labelValue]
            stableSegmentId = self._stableSegmentId(labelValue)
            segment = segmentation.GetSegment(stableSegmentId)
            if segment:
                segment.SetName(name)
                segment.SetLabelValue(labelValue)
                continue
            segmentId = segmentation.AddEmptySegment(stableSegmentId, name)
            segment = segmentation.GetSegment(segmentId)
            if segment:
                segment.SetLabelValue(labelValue)

    def _stableSegmentId(self, labelValue):
        return "CineCMRQC_Label_{0}".format(labelValue)

    def _labelValueForSegment(self, segmentId, segment):
        stableIdMatch = re.match(r"^CineCMRQC_Label_(-?\d+)$", segmentId or "")
        if stableIdMatch:
            return int(stableIdMatch.group(1))
        return int(segment.GetLabelValue())

    def _collectFrameFiles(self, path):
        if os.path.isdir(path):
            files = []
            for name in os.listdir(path):
                fullPath = os.path.join(path, name)
                if os.path.isfile(fullPath) and self._isSupportedImageFile(fullPath):
                    files.append(fullPath)
            return sorted(files, key=self._naturalSortKey)
        return []

    def _isSupportedImageFile(self, path):
        if os.path.basename(path).startswith("."):
            return False
        lower = path.lower()
        return any(lower.endswith(ext) for ext in self.SUPPORTED_EXTENSIONS)

    def _naturalSortKey(self, path):
        name = os.path.basename(path)
        return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


class CineCMRQCTest(ScriptedLoadableModuleTest):
    """Minimal smoke tests for non-GUI helper logic."""

    def setUp(self):
        slicer.mrmlScene.Clear(0)

    def runTest(self):
        self.setUp()
        self.test_parseLabelMap()
        self.test_naturalSort()
        self.test_imageOnlySeriesAndAnnotationDecisionAudit()
        self.test_frameSpecificEditingAndReviewState()
        self.test_geometryValidation()
        self.test_syntheticCineDemo()

    def test_parseLabelMap(self):
        logic = CineCMRQCLogic()
        labels = logic.parseLabelMap("1:LV,2:MYO,3:RV")
        self.assertEqual(labels[1], "LV")
        self.assertEqual(labels[2], "MYO")
        self.assertEqual(labels[3], "RV")

    def test_naturalSort(self):
        logic = CineCMRQCLogic()
        files = ["frame10.nii.gz", "frame2.nii.gz", "frame1.nii.gz"]
        ordered = sorted(files, key=logic._naturalSortKey)
        self.assertEqual(ordered, ["frame1.nii.gz", "frame2.nii.gz", "frame10.nii.gz"])
        self.assertFalse(logic._isSupportedImageFile("._frame001.nii.gz"))
        self.assertEqual(
            logic.resolveLabelMap(
                {1: "LV", 2: "MYO", 3: "RV"},
                [180, 255],
                True,
            ),
            {180: "Label_180", 255: "Label_255"},
        )
        self.assertEqual(logic.formatLabelMap({255: "Boundary", 180: "Cavity"}), "180:Cavity,255:Boundary")

    def test_imageOnlySeriesAndAnnotationDecisionAudit(self):
        import numpy as np
        import SimpleITK as sitk

        logic = CineCMRQCLogic()
        with tempfile.TemporaryDirectory() as patientRoot:
            seriesId = "series0001-Body"
            imageFolder = os.path.join(patientRoot, "img")
            os.makedirs(imageFolder)
            image = sitk.GetImageFromArray(
                np.zeros((3, 1, 8, 8), dtype=np.float32),
                isVector=False,
            )
            sitk.WriteImage(
                image,
                os.path.join(imageFolder, seriesId + ".nii.gz"),
            )
            sitk.WriteImage(
                image,
                os.path.join(imageFolder, "series0002-Body.nii.gz"),
            )
            sitk.WriteImage(
                image,
                os.path.join(imageFolder, "series0003-unknown.nii.gz"),
            )
            scanResult = logic.scanPatientFolder(patientRoot)
            self.assertEqual(scanResult["total_count"], 2)
            self.assertEqual(scanResult["selected_count"], 2)
            entriesById = {
                entry["series_id"]: entry for entry in scanResult["series"]
            }
            entry = entriesById[seriesId]
            self.assertTrue(entry["doctor_selected"])
            self.assertTrue(entry["load_eligible"])
            self.assertEqual(entry["mask_frame_count"], 0)
            self.assertEqual(entry["status"], "ready")
            excludedEntry = entriesById["series0002-Body"]
            self.assertTrue(excludedEntry["doctor_selected"])
            self.assertTrue(excludedEntry["load_eligible"])
            self.assertEqual(excludedEntry["status"], "ready")

            logic.initializePatientAuditSeriesCatalog(
                patientRoot,
                scanResult["series"],
            )
            maskFolder = os.path.join(
                patientRoot,
                "segmentation",
                seriesId,
                "sequence",
            )
            os.makedirs(maskFolder)
            open(os.path.join(maskFolder, "frame_00000_seg.nii.gz"), "wb").close()
            result = logic.deleteSeriesMasksAndRecordDecision(
                patientRoot,
                seriesId,
                "测试医生",
            )
            self.assertFalse(os.path.exists(os.path.dirname(maskFolder)))
            self.assertEqual(
                result["deleted_paths"],
                [os.path.dirname(maskFolder)],
            )
            record = logic.patientSeriesAuditRecord(patientRoot, seriesId)
            self.assertEqual(record["annotation_decision"], "not_required")
            self.assertTrue(record["annotation_decision_confirmed"])
            self.assertTrue(record["mask_deleted"])
            self.assertEqual(record["mask_frame_count"], 0)
            self.assertEqual(
                record["annotation_decision_history"][-1]["decision"],
                "not_required",
            )
            self.assertEqual(
                record["annotation_decision_history"][-1]["reviewer"],
                "测试医生",
            )

    def test_frameSpecificEditingAndReviewState(self):
        import numpy as np
        import os
        import tempfile
        import SimpleITK as sitk

        logic = CineCMRQCLogic()
        imageSequenceNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSequenceNode", "TestImageSequence")
        imageSequenceNode.SetIndexName("frame")
        for frameIndex in range(3):
            imageNode = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLScalarVolumeNode",
                "TestImage_{0}".format(frameIndex),
            )
            slicer.util.updateVolumeFromArray(
                imageNode,
                np.full((3, 4, 5), frameIndex, dtype=np.float32),
            )
            imageSequenceNode.SetDataNodeAtValue(imageNode, str(frameIndex))
            slicer.mrmlScene.RemoveNode(imageNode)

        browserNode = logic.getOrCreateBrowserForSequence(imageSequenceNode)
        segmentationSequenceNode = logic.createEmptySegmentationSequence(
            imageSequenceNode,
            browserNode,
            {1: "心腔"},
            "TestMask",
        )
        logic.bindSegmentationSequence(segmentationSequenceNode, browserNode)
        self.assertIn("绑定正常", logic.validateBinding(
            imageSequenceNode,
            segmentationSequenceNode,
            browserNode,
        ))
        self.assertEqual(
            logic.countCorrectedFrames(
                segmentationSequenceNode,
                imageSequenceNode,
                browserNode,
            ),
            0,
        )
        firstFrameSegmentation = segmentationSequenceNode.GetNthDataNode(0).GetSegmentation()
        secondFrameSegmentation = segmentationSequenceNode.GetNthDataNode(1).GetSegmentation()
        self.assertEqual(
            firstFrameSegmentation.GetNthSegmentID(0),
            secondFrameSegmentation.GetNthSegmentID(0),
        )

        browserNode.SetSelectedItemNumber(1)
        slicer.modules.sequences.logic().UpdateProxyNodesFromSequences(browserNode)
        proxySegmentation = browserNode.GetProxyNode(segmentationSequenceNode)
        proxyVolume = browserNode.GetProxyNode(imageSequenceNode)
        segmentId = proxySegmentation.GetSegmentation().GetNthSegmentID(0)
        editedArray = np.zeros((3, 4, 5), dtype=np.uint8)
        editedArray[1, 2, 3] = 1
        slicer.util.updateSegmentBinaryLabelmapFromArray(
            editedArray,
            proxySegmentation,
            segmentId,
            proxyVolume,
        )
        logic.setFrameReviewed(
            segmentationSequenceNode,
            1,
            True,
            "测试医生",
            "中文审核备注",
            "2026-07-16T12:00:00+00:00",
        )

        browserNode.SetSelectedItemNumber(0)
        slicer.modules.sequences.logic().UpdateProxyNodesFromSequences(browserNode)
        self.assertEqual(self._currentSegmentVoxelSum(browserNode, segmentationSequenceNode, imageSequenceNode), 0)
        browserNode.SetSelectedItemNumber(2)
        slicer.modules.sequences.logic().UpdateProxyNodesFromSequences(browserNode)
        self.assertEqual(self._currentSegmentVoxelSum(browserNode, segmentationSequenceNode, imageSequenceNode), 0)
        browserNode.SetSelectedItemNumber(1)
        slicer.modules.sequences.logic().UpdateProxyNodesFromSequences(browserNode)
        self.assertEqual(self._currentSegmentVoxelSum(browserNode, segmentationSequenceNode, imageSequenceNode), 1)
        self.assertTrue(logic.isFrameCorrected(
            segmentationSequenceNode,
            imageSequenceNode,
            browserNode,
            1,
        ))
        self.assertEqual(logic.countCorrectedFrames(
            segmentationSequenceNode,
            imageSequenceNode,
            browserNode,
        ), 1)
        self.assertTrue(logic.isFrameReviewed(segmentationSequenceNode, 1))
        self.assertEqual(logic.countReviewedFrames(segmentationSequenceNode), 1)
        self.assertEqual(
            logic.getFrameReviewMetadata(segmentationSequenceNode, 1),
            {
                "reviewer": "测试医生",
                "timestamp": "2026-07-16T12:00:00+00:00",
                "comment": "中文审核备注",
            },
        )
        self.assertEqual(logic.findSegmentationSequence(browserNode), segmentationSequenceNode)
        imageSequenceNode.SetAttribute("CineCMRQC.PatientRoot", "/tmp/Patient001")
        imageSequenceNode.SetAttribute("CineCMRQC.SeriesID", "Series001")
        imageSequenceNode.SetAttribute("CineCMRQC.DoctorReferenceFrames", "0,2")
        logic.setCardiacPhaseFrame(segmentationSequenceNode, "ED", 0)
        logic.setCardiacPhaseFrame(segmentationSequenceNode, "ES", 2)
        logic.confirmCardiacPhaseFrames(segmentationSequenceNode)

        with tempfile.TemporaryDirectory() as outputFolder:
            manifestPath = logic.exportSegmentationSequenceAsLabelmaps(
                segmentationSequenceNode,
                imageSequenceNode,
                browserNode,
                outputFolder,
                "test_mask",
            )
            self.assertTrue(os.path.isfile(manifestPath))
            with open(manifestPath, "r", encoding="utf-8-sig", newline="") as fp:
                rows = list(csv.DictReader(fp))
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[1]["reviewed"], "1")
            self.assertEqual(rows[0]["corrected"], "0")
            self.assertEqual(rows[1]["corrected"], "1")
            self.assertEqual(rows[2]["corrected"], "0")
            self.assertEqual(rows[1]["reviewer"], "测试医生")
            self.assertEqual(rows[1]["review_timestamp"], "2026-07-16T12:00:00+00:00")
            self.assertEqual(rows[1]["review_comment"], "中文审核备注")
            self.assertEqual(rows[0]["patient_id"], "Patient001")
            self.assertEqual(rows[0]["series_id"], "Series001")
            self.assertEqual(rows[0]["doctor_reference_frames"], "0,2")
            self.assertEqual(rows[0]["cardiac_phase"], "ED")
            self.assertEqual(rows[2]["cardiac_phase"], "ES")
            self.assertEqual(rows[0]["end_diastolic_frame"], "0")
            self.assertEqual(rows[0]["end_systolic_frame"], "2")
            self.assertEqual(rows[0]["cardiac_phase_confirmed"], "1")
            for frameIndex in range(3):
                self.assertTrue(os.path.isfile(os.path.join(
                    outputFolder,
                    "test_mask_frame{0:03d}.nii.gz".format(frameIndex),
                )))
            fourDimensionalPath = os.path.join(outputFolder, "test_mask_4d.nii.gz")
            self.assertTrue(os.path.isfile(fourDimensionalPath))
            fourDimensionalImage = sitk.ReadImage(fourDimensionalPath)
            self.assertEqual(fourDimensionalImage.GetDimension(), 4)
            self.assertEqual(fourDimensionalImage.GetSize()[3], 3)
            self.assertEqual(int(np.sum(sitk.GetArrayFromImage(fourDimensionalImage))), 1)
            labelsPath = os.path.join(outputFolder, "labels.csv")
            self.assertTrue(os.path.isfile(labelsPath))
            with open(labelsPath, "r", encoding="utf-8-sig", newline="") as fp:
                labelRows = list(csv.DictReader(fp))
            self.assertEqual(labelRows[0]["segment_name"], "心腔")
            patientManifestPath = logic.exportPatientSeries(
                [{
                    "series_id": "Series001",
                    "image_sequence": imageSequenceNode,
                    "segmentation_sequence": segmentationSequenceNode,
                    "browser": browserNode,
                }],
                os.path.join(outputFolder, "patient_export"),
            )
            self.assertTrue(os.path.isfile(patientManifestPath))
            with open(patientManifestPath, "r", encoding="utf-8-sig", newline="") as fp:
                patientRows = list(csv.DictReader(fp))
            self.assertEqual(len(patientRows), 3)
            self.assertEqual(patientRows[1]["reviewer"], "测试医生")
            self.assertTrue(os.path.isfile(os.path.join(
                outputFolder,
                "patient_export",
                "Series001",
                "Series001_corrected_4d.nii.gz",
            )))
            temporaryColorNodes = [
                node
                for node in slicer.util.getNodesByClass("vtkMRMLColorTableNode")
                if node.GetName().startswith("__CineCMRQC_export_labelmap")
            ]
            self.assertEqual(temporaryColorNodes, [])

            scenePath = os.path.join(outputFolder, "cine_qc_roundtrip.mrb")
            self.assertTrue(slicer.util.saveScene(scenePath))
            slicer.mrmlScene.Clear(0)
            self.assertTrue(slicer.util.loadScene(scenePath))
            loadedBrowsers = slicer.util.getNodesByClass("vtkMRMLSequenceBrowserNode")
            self.assertEqual(len(loadedBrowsers), 1)
            loadedSegmentationSequence = logic.findSegmentationSequence(loadedBrowsers[0])
            self.assertIsNotNone(loadedSegmentationSequence)
            self.assertTrue(logic.isFrameReviewed(loadedSegmentationSequence, 1))
            loadedImageSequence = loadedBrowsers[0].GetMasterSequenceNode()
            self.assertTrue(logic.isFrameCorrected(
                loadedSegmentationSequence,
                loadedImageSequence,
                loadedBrowsers[0],
                1,
            ))
            self.assertEqual(
                logic.getFrameReviewMetadata(loadedSegmentationSequence, 1)["reviewer"],
                "测试医生",
            )

    def _currentSegmentVoxelSum(self, browserNode, segmentationSequenceNode, imageSequenceNode):
        import numpy as np

        proxySegmentation = browserNode.GetProxyNode(segmentationSequenceNode)
        proxyVolume = browserNode.GetProxyNode(imageSequenceNode)
        segmentId = proxySegmentation.GetSegmentation().GetNthSegmentID(0)
        segment = proxySegmentation.GetSegmentation().GetSegment(segmentId)
        binaryName = slicer.vtkSegmentationConverter.GetSegmentationBinaryLabelmapRepresentationName()
        if not segment.GetRepresentation(binaryName):
            return 0
        array = slicer.util.arrayFromSegmentBinaryLabelmap(proxySegmentation, segmentId, proxyVolume)
        return int(np.sum(array))

    def test_geometryValidation(self):
        import numpy as np

        logic = CineCMRQCLogic()
        imageNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLScalarVolumeNode", "GeometryImage")
        maskNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLabelMapVolumeNode", "GeometryMask")
        slicer.util.updateVolumeFromArray(imageNode, np.zeros((2, 3, 4), dtype=np.float32))
        slicer.util.updateVolumeFromArray(maskNode, np.zeros((2, 3, 4), dtype=np.uint8))
        imageNode.SetOrigin(10.0, 20.0, 30.0)
        maskNode.SetOrigin(11.0, 20.0, 30.0)
        with self.assertRaises(ValueError):
            logic.validateOrAlignMaskGeometry(maskNode, imageNode, 0, False)
        logic.validateOrAlignMaskGeometry(maskNode, imageNode, 0, True)
        imageMatrix = vtk.vtkMatrix4x4()
        maskMatrix = vtk.vtkMatrix4x4()
        imageNode.GetIJKToRASMatrix(imageMatrix)
        maskNode.GetIJKToRASMatrix(maskMatrix)
        for row in range(4):
            for column in range(4):
                self.assertAlmostEqual(
                    imageMatrix.GetElement(row, column),
                    maskMatrix.GetElement(row, column),
                )

    def test_syntheticCineDemo(self):
        logic = CineCMRQCLogic()
        imageSequenceNode, segmentationSequenceNode, browserNode = logic.createSyntheticCineDemo(
            {180: "心腔", 255: "心肌"},
            frameCount=6,
            imageSize=64,
        )
        self.assertEqual(imageSequenceNode.GetNumberOfDataNodes(), 6)
        self.assertEqual(segmentationSequenceNode.GetNumberOfDataNodes(), 6)
        self.assertEqual(imageSequenceNode.GetIndexName(), "time")
        self.assertEqual(imageSequenceNode.GetIndexUnit(), "ms")
        self.assertIn("绑定正常", logic.validateBinding(
            imageSequenceNode,
            segmentationSequenceNode,
            browserNode,
        ))
        browserNode.SetSelectedItemNumber(0)
        slicer.modules.sequences.logic().UpdateProxyNodesFromSequences(browserNode)
        endDiastolicVoxelCount = self._currentSegmentVoxelSum(
            browserNode,
            segmentationSequenceNode,
            imageSequenceNode,
        )
        browserNode.SetSelectedItemNumber(3)
        slicer.modules.sequences.logic().UpdateProxyNodesFromSequences(browserNode)
        endSystolicVoxelCount = self._currentSegmentVoxelSum(
            browserNode,
            segmentationSequenceNode,
            imageSequenceNode,
        )
        self.assertGreater(endDiastolicVoxelCount, endSystolicVoxelCount)
        estimate = logic.estimateCardiacPhaseFrames(
            segmentationSequenceNode,
            imageSequenceNode,
        )
        self.assertEqual(estimate["ed_frame"], 0)
        self.assertEqual(estimate["es_frame"], 3)
        state = logic.initializeCardiacPhaseState(
            segmentationSequenceNode,
            imageSequenceNode,
        )
        self.assertEqual(state["ed_frame"], 0)
        self.assertEqual(state["es_frame"], 3)
        self.assertFalse(state["confirmed"])
        self.assertFalse(logic.isCardiacPhaseStateDirty(segmentationSequenceNode))
        logic.setCardiacPhaseFrame(segmentationSequenceNode, "ED", -1)
        self.assertTrue(logic.isCardiacPhaseStateDirty(segmentationSequenceNode))
        logic.setCardiacPhaseFrame(segmentationSequenceNode, "ED", 1)
        logic.confirmCardiacPhaseFrames(segmentationSequenceNode)
        self.assertTrue(logic.getCardiacPhaseState(segmentationSequenceNode)["confirmed"])
        logic.markCardiacPhaseStateSaved(segmentationSequenceNode)
        self.assertFalse(logic.isCardiacPhaseStateDirty(segmentationSequenceNode))
        with tempfile.TemporaryDirectory(prefix="CineCMRQC-audit-") as patientRoot:
            logic.initializePatientAuditSeriesCatalog(
                patientRoot,
                [
                    {
                        "series_id": "SyntheticSeries",
                        "doctor_selected": True,
                        "status": "ready",
                        "image_frame_count": 6,
                        "mask_frame_count": 6,
                        "reference_frames": [0],
                    },
                    {
                        "series_id": "ExcludedSeries",
                        "doctor_selected": False,
                        "status": "excluded-by-doctor",
                        "image_frame_count": 6,
                        "mask_frame_count": 0,
                        "reference_frames": [],
                    },
                ],
            )
            catalogAudit = logic.loadPatientAudit(patientRoot)
            self.assertEqual(catalogAudit["series_catalog_count"], 2)
            self.assertIsNone(
                catalogAudit["series"]["SyntheticSeries"]["first_opened_at"]
            )
            auditPath = logic.updatePatientSeriesAudit(
                patientRoot,
                "SyntheticSeries",
                segmentationSequenceNode,
                imageSequenceNode,
                browserNode,
                "open",
            )
            self.assertTrue(os.path.isfile(auditPath))
            audit = logic.loadPatientAudit(patientRoot)
            record = audit["series"]["SyntheticSeries"]
            self.assertEqual(record["modified_frame_count"], 0)
            self.assertEqual(record["manual_modification_ratio"], 0.0)
            self.assertEqual(record["last_modified_at"], record["first_opened_at"])
            self.assertEqual(record["last_modified_source"], "series-opened-default")
            self.assertGreater(record["end_diastolic"]["area_mm2"], 0.0)
            self.assertGreater(record["end_systolic"]["area_mm2"], 0.0)

            logic.updatePatientEjectionFraction(patientRoot, 55.5)
            logic.updatePatientSeriesAudit(
                patientRoot,
                "SyntheticSeries",
                segmentationSequenceNode,
                imageSequenceNode,
                browserNode,
                "save",
                [1, 3],
            )
            logic.updatePatientSeriesAudit(
                patientRoot,
                "SyntheticSeries",
                segmentationSequenceNode,
                imageSequenceNode,
                browserNode,
                "save",
                [3, 4],
            )
            audit = logic.loadPatientAudit(patientRoot)
            record = audit["series"]["SyntheticSeries"]
            self.assertEqual(audit["ejection_fraction_percent"], 55.5)
            self.assertEqual(audit["ejection_fraction_source"], "manual")
            self.assertEqual(record["modified_frame_indices"], [1, 3, 4])
            self.assertEqual(record["modified_frame_numbers"], [2, 4, 5])
            self.assertEqual(record["modified_frame_count"], 3)
            self.assertEqual(record["manual_modification_ratio"], 0.5)
            self.assertEqual(record["latest_save_modified_frame_indices"], [3, 4])
            self.assertEqual(record["save_count"], 2)
            self.assertEqual(len(record["save_history"]), 2)
            self.assertEqual(record["annotation_decision"], "required")
            self.assertEqual(
                record["save_history"][-1]["save_type"],
                "annotation-required",
            )
            self.assertEqual(record["last_modified_source"], "manual-mask-save")

            newSeriesId = "series0002-Body"
            imageSequenceNode.SetAttribute("CineCMRQC.SeriesID", newSeriesId)
            sourceMaskFolder = os.path.join(
                patientRoot,
                "segmentation",
                newSeriesId,
                "sequence",
            )
            manifestPath = logic.createSegmentationSequenceSourceFiles(
                segmentationSequenceNode,
                imageSequenceNode,
                browserNode,
                sourceMaskFolder,
            )
            self.assertTrue(os.path.isfile(manifestPath))
            self.assertEqual(
                len(logic._collectFrameFiles(sourceMaskFolder)),
                6,
            )
            self.assertTrue(os.path.isfile(os.path.join(
                os.path.dirname(sourceMaskFolder),
                newSeriesId + "_seg.nii.gz",
            )))
