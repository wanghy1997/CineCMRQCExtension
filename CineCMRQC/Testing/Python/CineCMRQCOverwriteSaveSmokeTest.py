import csv
import json
import os
import shutil
import tempfile
import traceback

import numpy as np
import SimpleITK as sitk
import slicer

import CineCMRQC


def runOverwriteSaveSmokeTest():
    imagePath = os.environ.get("CINE_CMR_QC_IMAGE_PATH", "")
    sourceMaskFolder = os.environ.get("CINE_CMR_QC_MASK_PATH", "")
    flipSetting = os.environ.get("CINE_CMR_QC_FLIP_LR", "auto").lower()
    if not imagePath or not sourceMaskFolder:
        raise ValueError("CINE_CMR_QC_IMAGE_PATH and CINE_CMR_QC_MASK_PATH are required.")

    slicer.mrmlScene.Clear(0)
    logic = CineCMRQC.CineCMRQCLogic()
    if flipSetting == "auto":
        sourceOrientation = logic.detectMaskSequenceOrientation(
            os.path.dirname(sourceMaskFolder),
            sourceMaskFolder,
        )
        flipMaskLeftRight = sourceOrientation == "legacy-left-right-flipped"
    else:
        flipMaskLeftRight = flipSetting == "1"
    sourceFramePaths = logic._collectFrameFiles(sourceMaskFolder)
    if not sourceFramePaths:
        raise AssertionError("The source mask folder has no frame files.")

    with tempfile.TemporaryDirectory(
        prefix="CineCMRQC-overwrite-save-",
        dir=os.path.dirname(sourceMaskFolder),
    ) as temporaryRoot:
        seriesId = os.path.basename(os.path.dirname(sourceMaskFolder))
        temporarySeriesFolder = os.path.join(temporaryRoot, seriesId)
        temporaryMaskFolder = os.path.join(temporarySeriesFolder, "sequence")
        os.makedirs(temporaryMaskFolder)
        temporarySourcePaths = []
        originalBytes = {}
        for sourcePath in sourceFramePaths:
            temporaryPath = os.path.join(
                temporaryMaskFolder,
                os.path.basename(sourcePath),
            )
            shutil.copy2(sourcePath, temporaryPath)
            temporarySourcePaths.append(temporaryPath)
            with open(temporaryPath, "rb") as fp:
                originalBytes[temporaryPath] = fp.read()

        labelsPath = os.path.join(temporaryMaskFolder, "labels.csv")
        manifestPath = os.path.join(temporaryMaskFolder, "manifest.csv")
        with open(labelsPath, "w", encoding="utf-8-sig") as fp:
            fp.write("legacy-labels-sentinel\n")
        with open(manifestPath, "w", encoding="utf-8-sig") as fp:
            fp.write("legacy-manifest-sentinel\n")

        imageSequenceNode = logic.loadVolumeSequenceFromPath(
            imagePath,
            "OverwriteSaveImage",
            labelmap=False,
        )
        browserNode = logic.getOrCreateBrowserForSequence(imageSequenceNode)
        segmentationSequenceNode = logic.loadSegmentationSequenceFromMaskPath(
            temporaryMaskFolder,
            imageSequenceNode,
            browserNode,
            {180: "心腔", 255: "心肌"},
            "OverwriteSaveMask",
            False,
            True,
            flipMaskLeftRight,
        )
        logic.bindSegmentationSequence(segmentationSequenceNode, browserNode)
        for node in [imageSequenceNode, segmentationSequenceNode, browserNode]:
            node.SetAttribute("CineCMRQC.SeriesID", seriesId)
        segmentationSequenceNode.SetAttribute(
            "CineCMRQC.SourceMaskOrientation",
            "legacy-left-right-flipped" if flipMaskLeftRight else "aligned",
        )
        cardiacPhaseState = logic.initializeCardiacPhaseState(
            segmentationSequenceNode,
            imageSequenceNode,
            temporaryMaskFolder,
        )
        if cardiacPhaseState["ed_frame"] < 0 or cardiacPhaseState["es_frame"] < 0:
            raise AssertionError("Temporary series did not receive automatic ED/ES frames.")
        logic.confirmCardiacPhaseFrames(segmentationSequenceNode)
        logic.updatePatientSeriesAudit(
            temporaryRoot,
            seriesId,
            segmentationSequenceNode,
            imageSequenceNode,
            browserNode,
            "open",
        )

        frameCount = segmentationSequenceNode.GetNumberOfDataNodes()
        if frameCount != len(temporarySourcePaths):
            raise AssertionError("The temporary sequence frame count changed during loading.")
        editFrame = min(7, frameCount - 1)
        browserNode.SetSelectedItemNumber(editFrame)
        slicer.modules.sequences.logic().UpdateProxyNodesFromSequences(browserNode)
        proxySegmentation = browserNode.GetProxyNode(segmentationSequenceNode)
        proxyVolume = browserNode.GetProxyNode(imageSequenceNode)
        segmentId = proxySegmentation.GetSegmentation().GetNthSegmentID(0)
        editArray = np.array(
            slicer.util.arrayFromSegmentBinaryLabelmap(
                proxySegmentation,
                segmentId,
                proxyVolume,
            ),
            copy=True,
        )
        editArray[0, 0, 0] = 0 if editArray[0, 0, 0] else 1
        slicer.util.updateSegmentBinaryLabelmapFromArray(
            editArray,
            proxySegmentation,
            segmentId,
            proxyVolume,
        )
        if not logic.isFrameCorrected(
            segmentationSequenceNode,
            imageSequenceNode,
            browserNode,
            editFrame,
        ):
            raise AssertionError("The temporary edit was not detected before saving.")

        savedManifestPath = logic.overwriteSegmentationSequenceSourceFiles(
            segmentationSequenceNode,
            imageSequenceNode,
            browserNode,
            temporaryMaskFolder,
        )
        logic.updatePatientEjectionFraction(temporaryRoot, 58.0)
        auditPath = logic.updatePatientSeriesAudit(
            temporaryRoot,
            seriesId,
            segmentationSequenceNode,
            imageSequenceNode,
            browserNode,
            "save",
            [editFrame],
        )

        savedFramePaths = logic._collectFrameFiles(temporaryMaskFolder)
        if savedFramePaths != temporarySourcePaths:
            raise AssertionError("Overwrite save changed the original frame filenames.")
        if len(savedFramePaths) != frameCount:
            raise AssertionError("Overwrite save left an extra medical image in sequence/.")
        backupFolders = [
            name for name in os.listdir(temporaryMaskFolder)
            if name.startswith("backup_") and os.path.isdir(os.path.join(temporaryMaskFolder, name))
        ]
        if backupFolders:
            raise AssertionError("Overwrite save unexpectedly created a backup folder.")

        sourcePngPath = os.path.join(
            os.path.dirname(sourceMaskFolder),
            "00000.png",
        )
        if os.path.isfile(sourcePngPath):
            expectedFrameZero = np.squeeze(
                sitk.GetArrayFromImage(sitk.ReadImage(sourcePngPath))
            )
            savedFrameZero = np.squeeze(
                sitk.GetArrayFromImage(sitk.ReadImage(savedFramePaths[0]))
            )
            if not np.array_equal(savedFrameZero, expectedFrameZero):
                raise AssertionError("Saved frame 0 does not match the source PNG orientation.")

        allSavedLabels = set()
        for savedFramePath in savedFramePaths:
            savedArray = sitk.GetArrayFromImage(sitk.ReadImage(savedFramePath))
            allSavedLabels.update(int(value) for value in np.unique(savedArray))
        if not allSavedLabels.issubset({0, 180, 255}):
            raise AssertionError(
                "Overwrite save changed label values: {0}.".format(sorted(allSavedLabels))
            )

        summaryPath = os.path.join(temporarySeriesFolder, seriesId + "_seg.nii.gz")
        summaryImage = sitk.ReadImage(summaryPath)
        if summaryImage.GetDimension() != 4 or summaryImage.GetSize()[3] != frameCount:
            raise AssertionError("The parent summary is not a scalar 4D mask sequence.")

        with open(savedManifestPath, "r", encoding="utf-8-sig", newline="") as fp:
            manifestRows = list(csv.DictReader(fp))
        if len(manifestRows) != frameCount:
            raise AssertionError("The rewritten manifest has an incorrect frame count.")
        for frameIndex, row in enumerate(manifestRows):
            expectedPath = temporarySourcePaths[frameIndex]
            if row["source_mask_path"] != expectedPath or row["mask_path"] != expectedPath:
                raise AssertionError("The rewritten manifest does not point to the source file.")
            if row["backup_mask_path"]:
                raise AssertionError("The rewritten manifest unexpectedly contains a backup path.")
            if row["cardiac_phase_confirmed"] != "1":
                raise AssertionError("The rewritten manifest lost ED/ES confirmation.")
        if sum(row["cardiac_phase"] == "ED" for row in manifestRows) != 1:
            raise AssertionError("The rewritten manifest does not contain exactly one ED frame.")
        if sum(row["cardiac_phase"] == "ES" for row in manifestRows) != 1:
            raise AssertionError("The rewritten manifest does not contain exactly one ES frame.")

        if logic.countCorrectedFrames(
            segmentationSequenceNode,
            imageSequenceNode,
            browserNode,
        ) != 0:
            raise AssertionError("Corrected-state baseline was not reset after saving.")
        if segmentationSequenceNode.GetAttribute("CineCMRQC.SourceMaskOrientation") != "aligned":
            raise AssertionError("Saved masks were not marked as aligned.")
        if segmentationSequenceNode.GetAttribute("CineCMRQC.AppliedMaskTransform") != "none":
            raise AssertionError("Saved masks retained a stale orientation transform marker.")
        if logic.isCardiacPhaseStateDirty(segmentationSequenceNode):
            raise AssertionError("ED/ES state baseline was not reset after saving.")
        with open(auditPath, "r", encoding="utf-8") as fp:
            patientAudit = json.load(fp)
        auditRecord = patientAudit["series"][seriesId]
        if patientAudit["ejection_fraction_percent"] != 58.0:
            raise AssertionError("Patient EF was not persisted in the JSON audit.")
        if auditRecord["modified_frame_indices"] != [editFrame]:
            raise AssertionError("Modified frames were not persisted in the JSON audit.")
        if auditRecord["modified_frame_count"] != 1:
            raise AssertionError("Modified frame count is incorrect in the JSON audit.")
        if auditRecord["end_diastolic"]["area_mm2"] is None:
            raise AssertionError("ED area is missing from the JSON audit.")
        if auditRecord["end_systolic"]["area_mm2"] is None:
            raise AssertionError("ES area is missing from the JSON audit.")

    print("OVERWRITE SAVE TEST PASSED")
    print("Exact source filenames preserved: {0}".format(frameCount))
    print("Automatic backup and manifest rewrite: passed")
    print("Labels preserved: 0/180/255")
    print("Parent scalar 4D summary: passed")
    print("Corrected-state baseline reset: passed")
    print("Patient JSON audit, EF, ED/ES areas, and modification ratio: passed")


exitStatus = 0
try:
    runOverwriteSaveSmokeTest()
except Exception:
    traceback.print_exc()
    exitStatus = 1
finally:
    slicer.app.exit(exitStatus)
