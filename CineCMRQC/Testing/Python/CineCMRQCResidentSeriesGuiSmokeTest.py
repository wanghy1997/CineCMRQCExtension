import os
import shutil
import tempfile
import traceback

import numpy as np
import SimpleITK as sitk
import slicer
import CineCMRQC


def assertOnlyActiveSegmentationVisible(widget):
    activeProxy = widget.sequenceBrowserNode.GetProxyNode(
        widget.segmentationSequenceNode
    )
    visibleManagedNodes = []
    for segmentationNode in slicer.util.getNodesByClass("vtkMRMLSegmentationNode"):
        if not widget.logic._isManagedCineSegmentation(segmentationNode):
            continue
        displayNode = segmentationNode.GetDisplayNode()
        if displayNode and displayNode.GetVisibility():
            visibleManagedNodes.append(segmentationNode)
    if visibleManagedNodes != [activeProxy]:
        raise AssertionError(
            "Expected only the active segmentation proxy to be visible, got: {0}".format(
                [node.GetName() for node in visibleManagedNodes]
            )
        )


def createPatientFolder(rootPath):
    frameCount = 6
    imageFolder = os.path.join(rootPath, "img")
    framesFolder = os.path.join(rootPath, "frames")
    segmentationFolder = os.path.join(rootPath, "segmentation")
    os.makedirs(imageFolder)
    os.makedirs(framesFolder)
    os.makedirs(segmentationFolder)

    yy, xx = np.mgrid[0:32, 0:32]
    for seriesNumber in [1, 2]:
        seriesId = "series{0:04d}-Body".format(seriesNumber)
        imageFrames = []
        maskFrames = []
        for frameIndex in range(frameCount):
            radius = 5 + abs(2 - frameIndex)
            cavity = (xx - 16) ** 2 + (yy - 16) ** 2 <= radius ** 2
            myocardium = (
                ((xx - 16) ** 2 + (yy - 16) ** 2 <= (radius + 2) ** 2)
                & ~cavity
            )
            imageFrame = np.full((1, 32, 32), 25 + seriesNumber, dtype=np.float32)
            imageFrame[:, cavity] = 180
            imageFrame[:, myocardium] = 100
            maskFrame = np.zeros((1, 32, 32), dtype=np.uint16)
            maskFrame[:, cavity] = 180
            maskFrame[:, myocardium] = 255
            imageFrames.append(imageFrame)
            maskFrames.append(maskFrame)

        image4d = sitk.GetImageFromArray(np.stack(imageFrames, axis=0), isVector=False)
        image4d.SetSpacing((1.4, 1.4, 8.0, 0.04))
        sitk.WriteImage(
            image4d,
            os.path.join(imageFolder, seriesId + ".nii.gz"),
            True,
        )
        os.makedirs(os.path.join(framesFolder, seriesId))
        with open(os.path.join(framesFolder, seriesId, "frame000.png"), "wb") as fp:
            fp.write(b"reference")
        maskFolder = os.path.join(segmentationFolder, seriesId, "sequence")
        os.makedirs(maskFolder)
        for frameIndex, maskArray in enumerate(maskFrames):
            maskImage = sitk.GetImageFromArray(maskArray)
            maskImage.SetSpacing((1.4, 1.4, 8.0))
            sitk.WriteImage(
                maskImage,
                os.path.join(maskFolder, "frame_{0:05d}_seg.nii.gz".format(frameIndex)),
                True,
            )


def confirmAndSave(widget):
    state = widget.logic.getCardiacPhaseState(widget.segmentationSequenceNode)
    if not state["confirmed"]:
        widget.logic.confirmCardiacPhaseFrames(widget.segmentationSequenceNode)
    if not widget._saveCurrentSeries(showSuccess=False):
        raise AssertionError("Could not save the active synthetic patient series.")


def runTest():
    temporaryRoot = tempfile.mkdtemp(prefix="cinecmrqc-resident-series-")
    try:
        createPatientFolder(temporaryRoot)
        slicer.util.selectModule("CineCMRQC")
        widget = slicer.modules.cinecmrqc.widgetRepresentation().self()
        dataProbe = slicer.util.mainWindow().findChild(
            "QWidget",
            "DataProbeCollapsibleWidget",
        )
        if not dataProbe:
            raise AssertionError("Data Probe collapsible widget was not found.")
        dataProbe.collapsed = False
        if not CineCMRQC.CineCMRQC.collapseDataProbe() or not dataProbe.collapsed:
            raise AssertionError("Data Probe was not collapsed by default handling.")
        widget.patientPathEdit.currentPath = temporaryRoot
        widget.onScanPatientFolder()
        slicer.app.processEvents()

        if widget.activePatientSeriesIndex != 0:
            raise AssertionError("The first available series was not loaded by default.")
        if len(widget.loadedPatientSeries) != 1:
            raise AssertionError("Initial scan retained more than one series.")
        staleSegmentation = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLSegmentationNode",
            "StaleCineCMRQCSegmentation",
        )
        staleSegmentation.CreateDefaultDisplayNodes()
        widget.logic._ensureSegments(
            staleSegmentation,
            {180: "心腔", 255: "心肌"},
        )
        staleSegmentation.SetAttribute("CineCMRQC.ManagedSegmentationProxy", "1")
        staleSegmentation.GetDisplayNode().SetVisibility(True)
        widget.logic.setActiveCineSegmentationVisibility(
            widget.segmentationSequenceNode,
            widget.sequenceBrowserNode,
        )
        assertOnlyActiveSegmentationVisible(widget)

        firstNodes = (
            widget.imageSequenceNode,
            widget.segmentationSequenceNode,
            widget.sequenceBrowserNode,
        )
        confirmAndSave(widget)
        widget.patientSeriesComboBox.setCurrentIndex(1)
        slicer.app.processEvents()
        if len(widget.loadedPatientSeries) != 1:
            raise AssertionError("Series switching retained more than one resident series.")
        if any(node.GetScene() for node in firstNodes):
            raise AssertionError("The previous series was not released from the MRML scene.")
        assertOnlyActiveSegmentationVisible(widget)

        confirmAndSave(widget)
        widget.patientSeriesComboBox.setCurrentIndex(0)
        slicer.app.processEvents()
        if widget._currentSeriesHasUnsavedChanges():
            raise AssertionError("A saved, unchanged series was treated as modified after reopening.")
        assertOnlyActiveSegmentationVisible(widget)

        proxySegmentation = widget.sequenceBrowserNode.GetProxyNode(
            widget.segmentationSequenceNode
        )
        proxyVolume = widget.sequenceBrowserNode.GetProxyNode(widget.imageSequenceNode)
        segmentId = proxySegmentation.GetSegmentation().GetNthSegmentID(0)
        originalArray = np.array(
            slicer.util.arrayFromSegmentBinaryLabelmap(
                proxySegmentation,
                segmentId,
                proxyVolume,
            ),
            copy=True,
        )
        editedArray = np.array(originalArray, copy=True)
        editedArray[0, 0, 0] = 0 if editedArray[0, 0, 0] else 1
        slicer.util.updateSegmentBinaryLabelmapFromArray(
            editedArray,
            proxySegmentation,
            segmentId,
            proxyVolume,
        )
        slicer.app.processEvents()
        if not widget._dirtyFrameCandidates():
            raise AssertionError("An actual segmentation edit was not tracked incrementally.")
        slicer.util.updateSegmentBinaryLabelmapFromArray(
            originalArray,
            proxySegmentation,
            segmentId,
            proxyVolume,
        )
        slicer.app.processEvents()
        if widget._dirtyFrameCandidates():
            raise AssertionError("Reverting an edit did not clear its incremental dirty state.")

        for frameIndex in [1, 2, 3, 2, 1, 0]:
            widget.sequenceBrowserNode.SetSelectedItemNumber(frameIndex)
            slicer.modules.sequences.logic().UpdateProxyNodesFromSequences(
                widget.sequenceBrowserNode
            )
            slicer.app.processEvents()
        if widget._dirtyFrameCandidates():
            raise AssertionError("Frame navigation alone created dirty-frame candidates.")

        print("RESIDENT SERIES GUI TEST PASSED")
        print("First-series default loading: passed")
        print("Data Probe default-collapsed state: passed")
        print("Single resident series after switching: passed")
        print("Cross-series segmentation overlay isolation: passed")
        print("Saved unchanged series leaves without a prompt: passed")
        print("Incremental edit and frame navigation state: passed")
    finally:
        slicer.mrmlScene.Clear(0)
        shutil.rmtree(temporaryRoot, ignore_errors=True)


try:
    runTest()
except Exception:
    traceback.print_exc()
    slicer.app.exit(1)
else:
    slicer.app.exit(0)
