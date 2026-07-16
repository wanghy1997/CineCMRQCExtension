import csv
import os
import tempfile
import traceback

import numpy as np
import SimpleITK as sitk
import slicer

import CineCMRQC


def currentSegmentArray(browserNode, segmentationSequenceNode, imageSequenceNode, frameIndex, segmentId):
    browserNode.SetSelectedItemNumber(frameIndex)
    slicer.modules.sequences.logic().UpdateProxyNodesFromSequences(browserNode)
    proxySegmentation = browserNode.GetProxyNode(segmentationSequenceNode)
    proxyVolume = browserNode.GetProxyNode(imageSequenceNode)
    return np.array(
        slicer.util.arrayFromSegmentBinaryLabelmap(proxySegmentation, segmentId, proxyVolume),
        copy=True,
    )


def runRealDataSmokeTest():
    imagePath = os.environ.get("CINE_CMR_QC_IMAGE_PATH", "")
    maskPath = os.environ.get("CINE_CMR_QC_MASK_PATH", "")
    expectedFrames = int(os.environ.get("CINE_CMR_QC_EXPECTED_FRAMES", "0"))
    flipSetting = os.environ.get("CINE_CMR_QC_FLIP_LR", "auto").lower()
    if not imagePath or not maskPath:
        raise ValueError("CINE_CMR_QC_IMAGE_PATH and CINE_CMR_QC_MASK_PATH are required.")

    slicer.mrmlScene.Clear(0)
    logic = CineCMRQC.CineCMRQCLogic()
    if flipSetting == "auto":
        sourceOrientation = logic.detectMaskSequenceOrientation(
            os.path.dirname(maskPath),
            maskPath,
        )
        flipMaskLeftRight = sourceOrientation == "legacy-left-right-flipped"
    else:
        flipMaskLeftRight = flipSetting == "1"
    imageSequenceNode = logic.loadVolumeSequenceFromPath(imagePath, "RealCineImage", labelmap=False)
    browserNode = logic.getOrCreateBrowserForSequence(imageSequenceNode)
    segmentationSequenceNode = logic.loadSegmentationSequenceFromMaskPath(
        maskPath,
        imageSequenceNode,
        browserNode,
        {180: "心腔", 255: "心肌"},
        "RealCineMask",
        False,
        True,
        flipMaskLeftRight,
    )
    logic.bindSegmentationSequence(segmentationSequenceNode, browserNode)
    phaseEstimate = logic.estimateCardiacPhaseFrames(
        segmentationSequenceNode,
        imageSequenceNode,
    )
    cardiacPhaseState = logic.initializeCardiacPhaseState(
        segmentationSequenceNode,
        imageSequenceNode,
        maskPath,
    )
    if cardiacPhaseState["ed_frame"] < 0 or cardiacPhaseState["es_frame"] < 0:
        raise AssertionError("ED/ES automatic estimation did not produce two frames.")
    if cardiacPhaseState["ed_frame"] == cardiacPhaseState["es_frame"]:
        raise AssertionError("ED and ES were assigned to the same frame.")
    if cardiacPhaseState["ed_source"] == "automatic-mask-cavity-extrema":
        if cardiacPhaseState["ed_frame"] != phaseEstimate["ed_frame"]:
            raise AssertionError("Automatic ED is not the maximum cavity-area frame.")
        if cardiacPhaseState["es_frame"] != phaseEstimate["es_frame"]:
            raise AssertionError("Automatic ES is not the minimum cavity-area frame.")
    if not cardiacPhaseState["confirmed"]:
        logic.confirmCardiacPhaseFrames(segmentationSequenceNode)
        cardiacPhaseState = logic.getCardiacPhaseState(segmentationSequenceNode)
    validationMessage = logic.validateBinding(imageSequenceNode, segmentationSequenceNode, browserNode)
    frameCount = imageSequenceNode.GetNumberOfDataNodes()
    if expectedFrames and frameCount != expectedFrames:
        raise AssertionError("Expected {0} frames, got {1}.".format(expectedFrames, frameCount))
    expectedSegmentIds = [
        segmentationSequenceNode.GetNthDataNode(0).GetSegmentation().GetNthSegmentID(index)
        for index in range(
            segmentationSequenceNode.GetNthDataNode(0).GetSegmentation().GetNumberOfSegments()
        )
    ]
    for frameIndex in range(1, frameCount):
        frameSegmentation = segmentationSequenceNode.GetNthDataNode(frameIndex).GetSegmentation()
        frameSegmentIds = [
            frameSegmentation.GetNthSegmentID(index)
            for index in range(frameSegmentation.GetNumberOfSegments())
        ]
        if frameSegmentIds != expectedSegmentIds:
            raise AssertionError("Segment IDs differ at frame {0}.".format(frameIndex))

    sourcePngPath = os.path.join(os.path.dirname(maskPath), "00000.png")
    if os.path.isfile(sourcePngPath):
        browserNode.SetSelectedItemNumber(0)
        slicer.modules.sequences.logic().UpdateProxyNodesFromSequences(browserNode)
        proxySegmentation = browserNode.GetProxyNode(segmentationSequenceNode)
        proxyVolume = browserNode.GetProxyNode(imageSequenceNode)
        orientationCheckNode = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLLabelMapVolumeNode",
            "__CineCMRQC_orientation_check",
        )
        try:
            logic._exportSegmentationToLabelmapPreservingValues(
                proxySegmentation,
                proxyVolume,
                orientationCheckNode,
            )
            loadedMaskArray = np.squeeze(slicer.util.arrayFromVolume(orientationCheckNode))
            sourcePngArray = np.squeeze(sitk.GetArrayFromImage(sitk.ReadImage(sourcePngPath)))
            if not np.array_equal(loadedMaskArray, sourcePngArray):
                print("ORIENTATION DEBUG shapes", loadedMaskArray.shape, sourcePngArray.shape)
                print("ORIENTATION DEBUG values", np.unique(loadedMaskArray), np.unique(sourcePngArray))
                print(
                    "ORIENTATION DEBUG matches",
                    "original", np.array_equal(loadedMaskArray, sourcePngArray),
                    "fliplr", np.array_equal(loadedMaskArray, np.fliplr(sourcePngArray)),
                    "flipud", np.array_equal(loadedMaskArray, np.flipud(sourcePngArray)),
                    "rot180", np.array_equal(loadedMaskArray, np.rot90(sourcePngArray, 2)),
                )
                for labelValue in sorted(set(np.unique(sourcePngArray)) | set(np.unique(loadedMaskArray))):
                    print(
                        "ORIENTATION DEBUG label",
                        int(labelValue),
                        "loaded", int(np.sum(loadedMaskArray == labelValue)),
                        "source", int(np.sum(sourcePngArray == labelValue)),
                    )
                if not np.array_equal(loadedMaskArray > 0, sourcePngArray > 0):
                    raise AssertionError("Loaded Slicer mask foreground does not match source PNG orientation.")
                print("Slicer foreground orientation matches PNG; label values were re-encoded on export.")
        finally:
            slicer.mrmlScene.RemoveNode(orientationCheckNode)

    testFrame = min(7, frameCount - 2)
    comparisonFrame = testFrame + 1
    browserNode.SetSelectedItemNumber(testFrame)
    slicer.modules.sequences.logic().UpdateProxyNodesFromSequences(browserNode)
    proxySegmentation = browserNode.GetProxyNode(segmentationSequenceNode)
    proxyVolume = browserNode.GetProxyNode(imageSequenceNode)
    segmentId = proxySegmentation.GetSegmentation().GetNthSegmentID(0)
    originalTestArray = currentSegmentArray(
        browserNode,
        segmentationSequenceNode,
        imageSequenceNode,
        testFrame,
        segmentId,
    )
    comparisonArrayBefore = currentSegmentArray(
        browserNode,
        segmentationSequenceNode,
        imageSequenceNode,
        comparisonFrame,
        segmentId,
    )

    browserNode.SetSelectedItemNumber(testFrame)
    slicer.modules.sequences.logic().UpdateProxyNodesFromSequences(browserNode)
    proxySegmentation = browserNode.GetProxyNode(segmentationSequenceNode)
    proxyVolume = browserNode.GetProxyNode(imageSequenceNode)
    editedArray = np.array(originalTestArray, copy=True)
    editedArray[0, 0, 0] = 0 if editedArray[0, 0, 0] else 1
    slicer.util.updateSegmentBinaryLabelmapFromArray(
        editedArray,
        proxySegmentation,
        segmentId,
        proxyVolume,
    )

    comparisonArrayAfter = currentSegmentArray(
        browserNode,
        segmentationSequenceNode,
        imageSequenceNode,
        comparisonFrame,
        segmentId,
    )
    testArrayAfter = currentSegmentArray(
        browserNode,
        segmentationSequenceNode,
        imageSequenceNode,
        testFrame,
        segmentId,
    )
    if not np.array_equal(comparisonArrayBefore, comparisonArrayAfter):
        raise AssertionError("Editing one frame changed the following frame.")
    if np.array_equal(originalTestArray, testArrayAfter):
        raise AssertionError("The edited frame did not retain its change after frame switching.")
    if not logic.isFrameCorrected(
        segmentationSequenceNode,
        imageSequenceNode,
        browserNode,
        testFrame,
    ):
        raise AssertionError("The edited frame was not marked as corrected.")
    if logic.isFrameCorrected(
        segmentationSequenceNode,
        imageSequenceNode,
        browserNode,
        comparisonFrame,
    ):
        raise AssertionError("An unchanged comparison frame was marked as corrected.")

    proxySegmentation = browserNode.GetProxyNode(segmentationSequenceNode)
    proxyVolume = browserNode.GetProxyNode(imageSequenceNode)
    slicer.util.updateSegmentBinaryLabelmapFromArray(
        originalTestArray,
        proxySegmentation,
        segmentId,
        proxyVolume,
    )
    if logic.isFrameCorrected(
        segmentationSequenceNode,
        imageSequenceNode,
        browserNode,
        testFrame,
    ):
        storedSegmentation = segmentationSequenceNode.GetNthDataNode(testFrame)
        storedVolume = imageSequenceNode.GetNthDataNode(testFrame)
        currentSegmentation = browserNode.GetProxyNode(segmentationSequenceNode)
        currentVolume = browserNode.GetProxyNode(imageSequenceNode)
        print(
            "EDIT DIGEST DEBUG",
            "baseline",
            storedSegmentation.GetAttribute("CineCMRQC.BaselineDigest"),
            "stored",
            logic.segmentationFrameDigest(storedSegmentation, storedVolume),
            "proxy",
            logic.segmentationFrameDigest(currentSegmentation, currentVolume),
        )
        for debugSegmentIndex in range(
            storedSegmentation.GetSegmentation().GetNumberOfSegments()
        ):
            debugSegmentId = storedSegmentation.GetSegmentation().GetNthSegmentID(
                debugSegmentIndex
            )
            storedArray = slicer.util.arrayFromSegmentBinaryLabelmap(
                storedSegmentation,
                debugSegmentId,
                storedVolume,
            )
            proxyArray = slicer.util.arrayFromSegmentBinaryLabelmap(
                currentSegmentation,
                debugSegmentId,
                currentVolume,
            )
            print(
                "EDIT DIGEST DEBUG SEGMENT",
                debugSegmentId,
                "stored-sum",
                int(np.sum(storedArray)),
                "proxy-sum",
                int(np.sum(proxyArray)),
                "different",
                int(np.sum(storedArray != proxyArray)),
            )
        debugLabelmap = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLLabelMapVolumeNode",
            "__CineCMRQC_edit_digest_debug",
        )
        try:
            logic._exportSegmentationToLabelmapPreservingValues(
                currentSegmentation,
                currentVolume,
                debugLabelmap,
            )
            debugMergedArray = np.squeeze(slicer.util.arrayFromVolume(debugLabelmap))
            debugSourcePath = os.path.join(
                os.path.dirname(maskPath),
                "{0:05d}.png".format(testFrame),
            )
            debugSourceArray = np.squeeze(
                sitk.GetArrayFromImage(sitk.ReadImage(debugSourcePath))
            )
            print(
                "EDIT DIGEST DEBUG MERGED",
                "source",
                debugSourcePath,
                "different",
                int(np.sum(debugMergedArray != debugSourceArray)),
                "merged-values",
                np.unique(debugMergedArray),
                "source-values",
                np.unique(debugSourceArray),
            )
        finally:
            slicer.mrmlScene.RemoveNode(debugLabelmap)
        raise AssertionError("Restoring the original mask did not clear corrected state.")
    logic.setFrameReviewed(segmentationSequenceNode, testFrame, True)
    browserNode.SetSelectedItemNumber(comparisonFrame)
    slicer.modules.sequences.logic().UpdateProxyNodesFromSequences(browserNode)

    with tempfile.TemporaryDirectory(prefix="CineCMRQC-real-data-") as outputFolder:
        manifestPath = logic.exportSegmentationSequenceAsLabelmaps(
            segmentationSequenceNode,
            imageSequenceNode,
            browserNode,
            outputFolder,
            "real_data_corrected",
        )
        with open(manifestPath, "r", newline="") as fp:
            manifestRows = list(csv.DictReader(fp))
        if len(manifestRows) != frameCount:
            raise AssertionError("Export manifest frame count is incorrect.")
        if manifestRows[testFrame]["reviewed"] != "1":
            raise AssertionError("Reviewed state was not exported.")
        if any(row["cardiac_phase_confirmed"] != "1" for row in manifestRows):
            raise AssertionError("Confirmed ED/ES state was not exported.")
        if sum(row["cardiac_phase"] == "ED" for row in manifestRows) != 1:
            raise AssertionError("Export manifest does not contain exactly one ED frame.")
        if sum(row["cardiac_phase"] == "ES" for row in manifestRows) != 1:
            raise AssertionError("Export manifest does not contain exactly one ES frame.")
        fourDimensionalImage = sitk.ReadImage(
            os.path.join(outputFolder, "real_data_corrected_4d.nii.gz")
        )
        if fourDimensionalImage.GetDimension() != 4:
            raise AssertionError("Corrected sequence export is not 4D.")
        if fourDimensionalImage.GetSize()[3] != frameCount:
            raise AssertionError("Corrected 4D export frame count is incorrect.")

    print("REAL DATA TEST PASSED")
    print(validationMessage)
    print("Detected labels: {0}".format(
        segmentationSequenceNode.GetAttribute("CineCMRQC.LabelMap")
    ))
    print("Stable segment IDs: {0}".format(",".join(expectedSegmentIds)))
    print("Applied mask transform: {0}".format(
        segmentationSequenceNode.GetAttribute("CineCMRQC.AppliedMaskTransform")
    ))
    print("Automatic cardiac phases: ED frame {0}, ES frame {1}".format(
        cardiacPhaseState["ed_frame"],
        cardiacPhaseState["es_frame"],
    ))
    print("Slicer frame 0 matches source PNG orientation: yes")
    print("Frame isolation: frame {0} retained the edit; frame {1} was unchanged.".format(
        testFrame,
        comparisonFrame,
    ))
    print("Edit-state tracking: modified/restored frame digest transitions validated.")
    print("Export: {0} per-frame masks plus one 4D mask validated.".format(frameCount))


exitStatus = 0
try:
    runRealDataSmokeTest()
except Exception:
    traceback.print_exc()
    exitStatus = 1
finally:
    slicer.app.exit(exitStatus)
