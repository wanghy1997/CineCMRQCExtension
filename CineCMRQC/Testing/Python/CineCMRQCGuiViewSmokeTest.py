import math
import os
import tempfile
import traceback

import numpy as np
import slicer
import vtk


def normalizedVector(values):
    length = math.sqrt(sum(value * value for value in values))
    if length <= 0:
        raise ValueError("Cannot normalize a zero-length vector.")
    return [value / length for value in values]


def markCurrentPhaseStateAsSaved(widget):
    state = widget.logic.getCardiacPhaseState(widget.segmentationSequenceNode)
    frameCount = widget.segmentationSequenceNode.GetNumberOfDataNodes()
    if not (
        0 <= state["ed_frame"] < frameCount
        and 0 <= state["es_frame"] < frameCount
        and state["ed_frame"] != state["es_frame"]
    ):
        widget.logic.setCardiacPhaseFrame(widget.segmentationSequenceNode, "ED", -1)
        widget.logic.setCardiacPhaseFrame(widget.segmentationSequenceNode, "ES", -1)
        widget.logic.setCardiacPhaseFrame(widget.segmentationSequenceNode, "ED", 0)
        widget.logic.setCardiacPhaseFrame(
            widget.segmentationSequenceNode,
            "ES",
            max(1, frameCount // 2),
        )
        state = widget.logic.getCardiacPhaseState(widget.segmentationSequenceNode)
    if not state["confirmed"]:
        widget.logic.confirmCardiacPhaseFrames(widget.segmentationSequenceNode)
    widget.logic.markCardiacPhaseStateSaved(widget.segmentationSequenceNode)
    widget.segmentationSequenceNode.SetAttribute("CineCMRQC.MaskMayBeDirty", "0")
    widget._updateStatus()


def runGuiViewSmokeTest():
    patientPath = os.environ.get("CINE_PATIENT_PATH", "")
    seriesId = os.environ.get("CINE_SERIES_ID", "series0015-Body")
    if not patientPath:
        raise ValueError("CINE_PATIENT_PATH is required.")

    slicer.util.selectModule("CineCMRQC")
    widget = slicer.modules.cinecmrqc.widgetRepresentation().self()
    widget.preferredPatientSeriesId = seriesId
    widget.patientPathEdit.currentPath = patientPath
    widget.onScanPatientFolder()
    legacyCount = sum(
        1
        for entry in widget.patientSeriesEntries
        if entry["mask_orientation"] == "legacy-left-right-flipped"
    )
    expectedLegacyCount = int(os.environ.get("CINE_EXPECTED_LEGACY_LR_COUNT", "0"))
    if expectedLegacyCount and legacyCount != expectedLegacyCount:
        raise AssertionError(
            "Expected {0} legacy LR mask sequence(s), got {1}.".format(
                expectedLegacyCount,
                legacyCount,
            )
        )
    targetIndex = next(
        index
        for index, entry in enumerate(widget.patientSeriesEntries)
        if entry["series_id"] == seriesId
    )
    slicer.app.processEvents()

    if not widget.simpleModeCheckBox.checked:
        raise AssertionError("The Chinese simplified workflow is not enabled by default.")
    if widget.inputSection.visible or widget.maskSection.visible or widget.logSection.visible:
        raise AssertionError("Advanced sections are visible in simplified mode.")
    for frameworkSection in slicer.util.findChildren(
        widget=widget.parent,
        className="ctkCollapsibleButton",
    ):
        frameworkText = str(getattr(frameworkSection, "text", "")).replace("&&", "&")
        if (
            frameworkText in ["Reload & Test", "Help & Acknowledgement"]
            and frameworkSection.visible
        ):
            raise AssertionError(
                "Slicer developer section is visible in simplified mode: {0}.".format(
                    frameworkText
                )
            )
    if widget.imageSequenceNode.GetAttribute("CineCMRQC.SeriesID") != seriesId:
        raise AssertionError("Patient scan did not automatically load the preferred series.")
    selectedEntry = widget.patientSeriesEntries[targetIndex]
    if os.path.abspath(widget.exportPathEdit.currentPath) != os.path.abspath(
        selectedEntry["mask_path"]
    ):
        raise AssertionError("The export path does not default to the loaded mask folder.")
    expectedSaveButtonText = "保存当前 Series：{0}".format(
        seriesId
    )
    if str(widget.exportButton.text) != expectedSaveButtonText:
        raise AssertionError("The save button does not identify the current series.")
    cardiacPhaseState = widget.logic.getCardiacPhaseState(
        widget.segmentationSequenceNode
    )
    expectedPhaseConfidence = os.environ.get(
        "CINE_EXPECTED_PHASE_CONFIDENCE",
        "",
    )
    if (
        expectedPhaseConfidence
        and cardiacPhaseState["confidence"] != expectedPhaseConfidence
    ):
        raise AssertionError(
            "Expected phase confidence {0}, got {1}.".format(
                expectedPhaseConfidence,
                cardiacPhaseState["confidence"],
            )
        )
    if cardiacPhaseState["ed_frame"] < 0 or cardiacPhaseState["es_frame"] < 0:
        raise AssertionError("GUI did not initialize automatic ED/ES frames.")
    if cardiacPhaseState["ed_frame"] == cardiacPhaseState["es_frame"]:
        raise AssertionError("GUI assigned ED and ES to the same frame.")
    if cardiacPhaseState["confirmed"]:
        widget.segmentationSequenceNode.SetAttribute(
            "CineCMRQC.CardiacPhaseConfirmed",
            "0",
        )
        widget.segmentationSequenceNode.SetAttribute(
            "CineCMRQC.CardiacPhaseConfirmedAt",
            None,
        )
        widget.logic.initializeCardiacPhaseBaseline(widget.segmentationSequenceNode)
        widget._updateStatus()
        cardiacPhaseState = widget.logic.getCardiacPhaseState(
            widget.segmentationSequenceNode
        )
    if "待确认 ED/ES" not in widget.seriesSaveStateLabel.text:
        raise AssertionError("GUI does not show the required ED/ES confirmation state.")
    alternateIndex = next(
        index
        for index, entry in enumerate(widget.patientSeriesEntries)
        if entry["series_id"] != seriesId and entry["status"] == "ready"
    )
    blockedMessages = []
    originalInfoDisplay = slicer.util.infoDisplay
    slicer.util.infoDisplay = lambda message, *args, **kwargs: blockedMessages.append(
        str(message)
    )
    try:
        widget.patientSeriesComboBox.setCurrentIndex(alternateIndex)
        slicer.app.processEvents()
    finally:
        slicer.util.infoDisplay = originalInfoDisplay
    if widget.imageSequenceNode.GetAttribute("CineCMRQC.SeriesID") != seriesId:
        raise AssertionError("GUI allowed switching before ED/ES confirmation.")
    if widget.patientSeriesComboBox.currentIndex != targetIndex:
        raise AssertionError("Blocked switching did not restore the active series selection.")
    if not blockedMessages:
        raise AssertionError("Blocked switching did not explain the ED/ES requirement.")
    originalEdFrame = cardiacPhaseState["ed_frame"]
    widget.onJumpToCardiacPhase("ED")
    slicer.app.processEvents()
    if not widget.endDiastolicButton.checked:
        raise AssertionError("ED assignment button is not checked on the ED frame.")
    widget.endDiastolicButton.checked = False
    slicer.app.processEvents()
    if widget.logic.getCardiacPhaseState(widget.segmentationSequenceNode)["ed_frame"] != -1:
        raise AssertionError("Unchecking ED did not clear the original assignment.")
    replacementEdFrame = (originalEdFrame + 1) % widget.imageSequenceNode.GetNumberOfDataNodes()
    if replacementEdFrame == cardiacPhaseState["es_frame"]:
        replacementEdFrame = (replacementEdFrame + 1) % widget.imageSequenceNode.GetNumberOfDataNodes()
    widget.sequenceBrowserNode.SetSelectedItemNumber(replacementEdFrame)
    slicer.modules.sequences.logic().UpdateProxyNodesFromSequences(
        widget.sequenceBrowserNode
    )
    widget.endDiastolicButton.checked = True
    slicer.app.processEvents()
    replacementState = widget.logic.getCardiacPhaseState(widget.segmentationSequenceNode)
    if replacementState["ed_frame"] != replacementEdFrame:
        raise AssertionError("Checking ED did not assign the current frame.")
    if replacementState["ed_source"] != "manual":
        raise AssertionError("A doctor-adjusted ED frame was not marked as manual.")
    widget.onConfirmCardiacPhases()
    if not widget.logic.getCardiacPhaseState(
        widget.segmentationSequenceNode
    )["confirmed"]:
        raise AssertionError("The combined ED/ES confirmation action failed.")
    if "未保存" not in widget.seriesSaveStateLabel.text:
        raise AssertionError("Confirmed phase changes were not shown as unsaved.")
    markCurrentPhaseStateAsSaved(widget)
    savedPhaseState = widget.logic.getCardiacPhaseState(widget.segmentationSequenceNode)
    widget.logic.setCardiacPhaseFrame(
        widget.segmentationSequenceNode,
        "ES",
        savedPhaseState["es_frame"],
    )
    widget.logic.confirmCardiacPhaseFrames(widget.segmentationSequenceNode)
    widget._updateStatus()
    savePrompts = []
    originalConfirmDisplay = slicer.util.confirmYesNoDisplay
    slicer.util.confirmYesNoDisplay = lambda message, *args, **kwargs: (
        savePrompts.append(str(message)) or False
    )
    try:
        widget.patientSeriesComboBox.setCurrentIndex(alternateIndex)
        slicer.app.processEvents()
    finally:
        slicer.util.confirmYesNoDisplay = originalConfirmDisplay
    if widget.imageSequenceNode.GetAttribute("CineCMRQC.SeriesID") != seriesId:
        raise AssertionError("GUI allowed switching after rejecting the required save.")
    if not savePrompts:
        raise AssertionError("Unsaved changes did not trigger the required save prompt.")
    markCurrentPhaseStateAsSaved(widget)
    targetImageSequence = widget.imageSequenceNode
    targetSegmentationSequence = widget.segmentationSequenceNode
    targetBrowser = widget.sequenceBrowserNode
    targetProxySegmentation = targetBrowser.GetProxyNode(targetSegmentationSequence)

    alternateSeriesId = widget.patientSeriesEntries[alternateIndex]["series_id"]
    widget.patientSeriesComboBox.setCurrentIndex(alternateIndex)
    slicer.app.processEvents()
    if widget.imageSequenceNode.GetAttribute("CineCMRQC.SeriesID") != alternateSeriesId:
        raise AssertionError("Changing the series dropdown did not auto-load its MRI and mask.")
    if any(node.GetScene() for node in [targetImageSequence, targetSegmentationSequence, targetBrowser]):
        raise AssertionError("The previous series remained resident after switching.")
    if len(widget.loadedPatientSeries) != 1:
        raise AssertionError("More than one patient series remained in the resident cache.")
    alternateSegmentationSequence = widget.segmentationSequenceNode
    alternateBrowser = widget.sequenceBrowserNode
    alternateProxySegmentation = alternateBrowser.GetProxyNode(
        alternateSegmentationSequence
    )
    if not alternateProxySegmentation.GetDisplayNode().GetVisibility():
        raise AssertionError("Current-series mask is hidden after automatic loading.")
    markCurrentPhaseStateAsSaved(widget)
    widget.patientSeriesComboBox.setCurrentIndex(targetIndex)
    slicer.app.processEvents()
    if widget.imageSequenceNode.GetAttribute("CineCMRQC.SeriesID") != seriesId:
        raise AssertionError("Returning to the target series did not restore its paired data.")
    if any(node.GetScene() for node in [alternateSegmentationSequence, alternateBrowser]):
        raise AssertionError("The alternate series remained resident after returning.")
    targetProxySegmentation = widget.sequenceBrowserNode.GetProxyNode(
        widget.segmentationSequenceNode
    )
    if not targetProxySegmentation.GetDisplayNode().GetVisibility():
        raise AssertionError("Restored target-series mask is hidden.")
    # This real-data test must not write the source patient folder. Recreate the
    # saved-state marker in memory; the dedicated resident-series test verifies
    # actual manifest-backed restoration using a disposable patient folder.
    markCurrentPhaseStateAsSaved(widget)

    appliedMaskTransform = widget.segmentationSequenceNode.GetAttribute(
        "CineCMRQC.AppliedMaskTransform"
    ) or "none"
    sourceMaskOrientation = widget.segmentationSequenceNode.GetAttribute(
        "CineCMRQC.SourceMaskOrientation"
    ) or "unknown"
    if sourceMaskOrientation == "legacy-left-right-flipped" and appliedMaskTransform != "flip_lr":
        raise AssertionError(
            "Legacy LR mask sequence was loaded without the required one-time correction."
        )
    effectiveLabelMap = widget.segmentationSequenceNode.GetAttribute(
        "CineCMRQC.LabelMap"
    ) or ""
    effectiveLabels = set(item for item in effectiveLabelMap.split(",") if item)
    if not effectiveLabels or not effectiveLabels.issubset({"180:心腔", "255:心肌"}):
        raise AssertionError("Huaxi label values or names were not preserved.")
    if widget.logic.countCorrectedFrames(
        widget.segmentationSequenceNode,
        widget.imageSequenceNode,
        widget.sequenceBrowserNode,
    ) != 0:
        raise AssertionError("Freshly imported GUI masks were marked as corrected.")
    widget._updateStatus()
    if not widget.correctedStateLabel.text.endswith("否"):
        raise AssertionError("GUI corrected-state label is inconsistent with the mask baseline.")

    proxySegmentation = widget.sequenceBrowserNode.GetProxyNode(
        widget.segmentationSequenceNode
    )
    proxyVolume = widget.sequenceBrowserNode.GetProxyNode(widget.imageSequenceNode)
    editSegmentId = proxySegmentation.GetSegmentation().GetNthSegmentID(0)
    originalEditArray = np.array(
        slicer.util.arrayFromSegmentBinaryLabelmap(
            proxySegmentation,
            editSegmentId,
            proxyVolume,
        ),
        copy=True,
    )
    temporaryEditArray = np.array(originalEditArray, copy=True)
    temporaryEditArray[0, 0, 0] = 0 if temporaryEditArray[0, 0, 0] else 1
    slicer.util.updateSegmentBinaryLabelmapFromArray(
        temporaryEditArray,
        proxySegmentation,
        editSegmentId,
        proxyVolume,
    )
    slicer.app.processEvents()
    if not widget.correctedStateLabel.text.endswith("是"):
        raise AssertionError("GUI edit-state label did not update immediately after an edit.")
    slicer.util.updateSegmentBinaryLabelmapFromArray(
        originalEditArray,
        proxySegmentation,
        editSegmentId,
        proxyVolume,
    )
    slicer.app.processEvents()
    if not widget.correctedStateLabel.text.endswith("否"):
        raise AssertionError("GUI edit-state label did not clear after restoring the mask.")

    reviewFrame = widget.sequenceBrowserNode.GetSelectedItemNumber()
    widget.reviewerEdit.text = "GUI Smoke Reviewer"
    widget.reviewCommentEdit.text = "GUI metadata round-trip"
    widget._setFrameReviewState(reviewFrame, True)
    reviewMetadata = widget.logic.getFrameReviewMetadata(
        widget.segmentationSequenceNode,
        reviewFrame,
    )
    if reviewMetadata["reviewer"] != "GUI Smoke Reviewer":
        raise AssertionError("Reviewer entered in the GUI was not stored on the frame.")
    if reviewMetadata["comment"] != "GUI metadata round-trip":
        raise AssertionError("Frame comment entered in the GUI was not stored on the frame.")
    if not reviewMetadata["timestamp"]:
        raise AssertionError("GUI review action did not create a timestamp.")
    widget._setFrameReviewState(reviewFrame, False)

    layoutManager = slicer.app.layoutManager()
    expectedLayout = slicer.vtkMRMLLayoutNode.SlicerLayoutOneUpRedSliceView
    if layoutManager.layout != expectedLayout:
        raise AssertionError(
            "Expected one-up Red layout {0}, got {1}.".format(
                expectedLayout,
                layoutManager.layout,
            )
        )
    sliceWidget = layoutManager.sliceWidget("Red")
    sliceNode = sliceWidget.mrmlSliceNode()
    proxyVolume = widget.sequenceBrowserNode.GetProxyNode(widget.imageSequenceNode)

    ijkToRas = vtk.vtkMatrix4x4()
    proxyVolume.GetIJKToRASMatrix(ijkToRas)
    volumeNormal = normalizedVector([ijkToRas.GetElement(row, 2) for row in range(3)])
    sliceToRas = sliceNode.GetSliceToRAS()
    sliceNormal = normalizedVector([sliceToRas.GetElement(row, 2) for row in range(3)])
    normalDotProduct = abs(sum(
        volumeNormal[index] * sliceNormal[index]
        for index in range(3)
    ))
    if normalDotProduct < 0.999:
        raise AssertionError(
            "Slice plane is not aligned with the native cine plane: dot={0}.".format(
                normalDotProduct
            )
        )

    rasToSlice = vtk.vtkMatrix4x4()
    vtk.vtkMatrix4x4.Invert(sliceToRas, rasToSlice)
    ijkToSlice = vtk.vtkMatrix4x4()
    vtk.vtkMatrix4x4.Multiply4x4(rasToSlice, ijkToRas, ijkToSlice)
    dimensions = proxyVolume.GetImageData().GetDimensions()
    sliceCoordinates = []
    for iValue in [0, dimensions[0]]:
        for jValue in [0, dimensions[1]]:
            point = [float(iValue), float(jValue), 0.0, 1.0]
            transformed = [0.0, 0.0, 0.0, 0.0]
            ijkToSlice.MultiplyPoint(point, transformed)
            sliceCoordinates.append(transformed)
    imageWidthMm = max(point[0] for point in sliceCoordinates) - min(
        point[0] for point in sliceCoordinates
    )
    imageHeightMm = max(point[1] for point in sliceCoordinates) - min(
        point[1] for point in sliceCoordinates
    )
    fieldOfView = sliceNode.GetFieldOfView()
    if fieldOfView[0] < imageWidthMm * 0.98 or fieldOfView[1] < imageHeightMm * 0.98:
        raise AssertionError(
            "Slice field of view does not cover the full cine image: "
            "FOV={0}, image={1:.3f}x{2:.3f} mm.".format(
                fieldOfView,
                imageWidthMm,
                imageHeightMm,
            )
        )

    with tempfile.TemporaryDirectory(prefix="CineCMRQC-second-patient-") as secondPatient:
        for folderName in ["img", "frames", "segmentation"]:
            os.symlink(
                os.path.join(patientPath, folderName),
                os.path.join(secondPatient, folderName),
            )
        widget.preferredPatientSeriesId = seriesId
        widget.patientPathEdit.currentPath = secondPatient
        widget.onScanPatientFolder()
        slicer.app.processEvents()
        if widget.imageSequenceNode == targetImageSequence:
            raise AssertionError(
                "A second patient with the same series ID reused the first patient's nodes."
            )
        if os.path.abspath(
            widget.imageSequenceNode.GetAttribute("CineCMRQC.PatientRoot") or ""
        ) != os.path.abspath(secondPatient):
            raise AssertionError("The active sequence does not belong to the second patient.")
        secondPatientProxy = widget.sequenceBrowserNode.GetProxyNode(
            widget.segmentationSequenceNode
        )
        if targetProxySegmentation.GetScene():
            raise AssertionError("First-patient mask remained resident after changing patient.")
        if not secondPatientProxy.GetDisplayNode().GetVisibility():
            raise AssertionError("Second-patient current mask is hidden.")

    print("GUI VIEW TEST PASSED")
    print("Layout: One-up Red")
    print("Native-plane alignment dot product: {0:.6f}".format(normalDotProduct))
    print("Image extent: {0:.3f} x {1:.3f} mm".format(imageWidthMm, imageHeightMm))
    print("Slice FOV: {0:.3f} x {1:.3f} mm".format(fieldOfView[0], fieldOfView[1]))
    print("Labels: {0}".format(
        widget.segmentationSequenceNode.GetAttribute("CineCMRQC.LabelMap")
    ))
    print("Legacy LR sequences detected: {0}".format(legacyCount))
    print("Source mask orientation: {0}".format(sourceMaskOrientation))
    print("Applied mask transform: {0}".format(appliedMaskTransform))
    print("GUI reviewer/comment/timestamp round-trip: passed")
    print("GUI original/corrected state: original")
    print("GUI live edit-state refresh: passed")
    print("Chinese simplified mode and automatic series/mask pairing: passed")
    print("Series-specific save path and dynamic overwrite/backup button: passed")
    print("Automatic ED/ES, manual reassignment, and doctor confirmation: passed")
    print("Series/patient mask visibility isolation and cache separation: passed")


exitStatus = 0
try:
    runGuiViewSmokeTest()
except Exception:
    traceback.print_exc()
    exitStatus = 1
finally:
    slicer.app.exit(exitStatus)
