import traceback

import numpy as np
import slicer

import CineCMRQC


def runCardiacPhaseGuiSmokeTest():
    slicer.mrmlScene.Clear(0)
    slicer.util.selectModule("CineCMRQC")
    widget = slicer.modules.cinecmrqc.widgetRepresentation().self()
    imageSequenceNode, segmentationSequenceNode, browserNode = (
        widget.logic.createSyntheticCineDemo(
            {180: "心腔", 255: "心肌"},
            frameCount=10,
            imageSize=64,
        )
    )
    for node in [imageSequenceNode, segmentationSequenceNode, browserNode]:
        node.SetAttribute("CineCMRQC.PatientRoot", "/tmp/CineCMRQC-SyntheticPatient")
        node.SetAttribute("CineCMRQC.SeriesID", "SyntheticSeries001")
    widget.logic.initializeCardiacPhaseState(
        segmentationSequenceNode,
        imageSequenceNode,
    )
    widget.imageSequenceSelector.setCurrentNode(imageSequenceNode)
    widget._setActiveImageSequence(imageSequenceNode, browserNode)
    widget.segmentationSequenceNode = segmentationSequenceNode
    widget.logic.showSegmentationSequence(segmentationSequenceNode, browserNode)
    widget.configureEmbeddedSegmentEditor()
    widget._updateStatus()

    state = widget.logic.getCardiacPhaseState(segmentationSequenceNode)
    if state["ed_frame"] != 0 or state["es_frame"] != 5:
        raise AssertionError(
            "Synthetic automatic phases are incorrect: ED={0}, ES={1}.".format(
                state["ed_frame"],
                state["es_frame"],
            )
        )
    if state["confirmed"]:
        raise AssertionError("Automatic ED/ES was presented as doctor-confirmed.")
    if "SyntheticSeries001" not in widget.exportButton.text:
        raise AssertionError("The save button does not name the active series.")
    if "待确认 ED/ES" not in widget.seriesSaveStateLabel.text:
        raise AssertionError("The save state does not require ED/ES confirmation.")

    blockedMessages = []
    originalInfoDisplay = slicer.util.infoDisplay
    slicer.util.infoDisplay = lambda message, *args, **kwargs: blockedMessages.append(
        str(message)
    )
    try:
        if widget._canLeaveCurrentSeries():
            raise AssertionError("An unconfirmed series was allowed to leave.")
    finally:
        slicer.util.infoDisplay = originalInfoDisplay
    if not blockedMessages:
        raise AssertionError("Unconfirmed switching did not show an explanation.")

    replacementEdFrame = 1
    browserNode.SetSelectedItemNumber(replacementEdFrame)
    slicer.modules.sequences.logic().UpdateProxyNodesFromSequences(browserNode)
    blockedMessages = []
    originalInfoDisplay = slicer.util.infoDisplay
    slicer.util.infoDisplay = lambda message, *args, **kwargs: blockedMessages.append(
        str(message)
    )
    try:
        widget.endDiastolicButton.checked = True
        slicer.app.processEvents()
    finally:
        slicer.util.infoDisplay = originalInfoDisplay
    if widget.logic.getCardiacPhaseState(segmentationSequenceNode)["ed_frame"] != 0:
        raise AssertionError("ED was replaced without first clearing the old assignment.")
    if not blockedMessages:
        raise AssertionError("ED replacement rule did not explain how to clear the old frame.")

    widget.onJumpToCardiacPhase("ED")
    slicer.app.processEvents()
    widget.endDiastolicButton.checked = False
    browserNode.SetSelectedItemNumber(replacementEdFrame)
    slicer.modules.sequences.logic().UpdateProxyNodesFromSequences(browserNode)
    widget.endDiastolicButton.checked = True
    widget.onConfirmCardiacPhases()
    state = widget.logic.getCardiacPhaseState(segmentationSequenceNode)
    if state["ed_frame"] != replacementEdFrame or state["ed_source"] != "manual":
        raise AssertionError("Manual ED reassignment was not retained.")
    if not state["confirmed"]:
        raise AssertionError("Combined doctor confirmation failed.")
    if "未保存" not in widget.seriesSaveStateLabel.text:
        raise AssertionError("Confirmed phase edits are not shown as unsaved.")

    savePrompts = []
    originalConfirmDisplay = slicer.util.confirmYesNoDisplay
    slicer.util.confirmYesNoDisplay = lambda message, *args, **kwargs: (
        savePrompts.append(str(message)) or False
    )
    try:
        if widget._canLeaveCurrentSeries():
            raise AssertionError("Rejecting the save prompt still allowed switching.")
    finally:
        slicer.util.confirmYesNoDisplay = originalConfirmDisplay
    if not savePrompts:
        raise AssertionError("Unsaved phase edits did not trigger a save prompt.")

    widget.logic.markCardiacPhaseStateSaved(segmentationSequenceNode)
    segmentationSequenceNode.SetAttribute("CineCMRQC.MaskMayBeDirty", "0")
    widget._updateStatus()
    if not widget._canLeaveCurrentSeries():
        raise AssertionError("A confirmed and saved series was blocked from switching.")

    browserNode.SetSelectedItemNumber(2)
    slicer.modules.sequences.logic().UpdateProxyNodesFromSequences(browserNode)
    proxySegmentation = browserNode.GetProxyNode(segmentationSequenceNode)
    proxyVolume = browserNode.GetProxyNode(imageSequenceNode)
    segmentId = proxySegmentation.GetSegmentation().GetNthSegmentID(0)
    editedArray = np.array(
        slicer.util.arrayFromSegmentBinaryLabelmap(
            proxySegmentation,
            segmentId,
            proxyVolume,
        ),
        copy=True,
    )
    editedArray[0, 0, 0] = 0 if editedArray[0, 0, 0] else 1
    slicer.util.updateSegmentBinaryLabelmapFromArray(
        editedArray,
        proxySegmentation,
        segmentId,
        proxyVolume,
    )
    slicer.app.processEvents()
    savePrompts = []
    originalConfirmDisplay = slicer.util.confirmYesNoDisplay
    slicer.util.confirmYesNoDisplay = lambda message, *args, **kwargs: (
        savePrompts.append(str(message)) or False
    )
    try:
        if widget._canLeaveCurrentSeries():
            raise AssertionError("An unsaved mask edit was allowed to leave.")
    finally:
        slicer.util.confirmYesNoDisplay = originalConfirmDisplay
    if not savePrompts:
        raise AssertionError("An unsaved mask edit did not trigger a save prompt.")

    print("CARDIAC PHASE GUI TEST PASSED")
    print("Automatic ED/ES extrema: passed")
    print("Clear-before-reassign interaction: passed")
    print("Doctor confirmation requirement: passed")
    print("Series-specific dynamic save label: passed")
    print("Unconfirmed and unsaved switch blocking: passed")


exitStatus = 0
try:
    runCardiacPhaseGuiSmokeTest()
except Exception:
    traceback.print_exc()
    exitStatus = 1
finally:
    slicer.app.exit(exitStatus)
