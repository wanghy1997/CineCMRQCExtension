import csv
import os
import tempfile
import traceback

import numpy as np
import SimpleITK as sitk
import slicer

import CineCMRQC


def runPatientBatchSmokeTest():
    patientPath = os.environ.get("CINE_PATIENT_PATH", "")
    expectedSeries = int(os.environ.get("CINE_EXPECTED_READY_SERIES", "0"))
    if not patientPath:
        raise ValueError("CINE_PATIENT_PATH is required.")

    slicer.mrmlScene.Clear(0)
    logic = CineCMRQC.CineCMRQCLogic()
    scanResult = logic.scanPatientFolder(patientPath)
    readyEntries = [
        entry
        for entry in scanResult["series"]
        if entry["doctor_selected"] and entry["status"] == "ready"
    ]
    if expectedSeries and len(readyEntries) != expectedSeries:
        raise AssertionError(
            "Expected {0} ready series, got {1}.".format(
                expectedSeries,
                len(readyEntries),
            )
        )

    exportItems = []
    automaticPhaseResults = []
    manualPhaseSeries = []
    lowConfidencePhaseSeries = []
    for entry in readyEntries:
        imageSequenceNode = logic.loadVolumeSequenceFromPath(
            entry["image_path"],
            entry["series_id"] + "Image",
            labelmap=False,
            indexValues=entry["time_index_values"],
            indexName=entry["time_index_name"],
            indexUnit=entry["time_index_unit"],
        )
        browserNode = logic.getOrCreateBrowserForSequence(imageSequenceNode)
        segmentationSequenceNode = logic.loadSegmentationSequenceFromMaskPath(
            entry["mask_path"],
            imageSequenceNode,
            browserNode,
            entry["label_map"],
            entry["series_id"] + "Mask",
            False,
            True,
            entry["mask_orientation"] == "legacy-left-right-flipped",
        )
        logic.bindSegmentationSequence(segmentationSequenceNode, browserNode)
        for node in [imageSequenceNode, segmentationSequenceNode, browserNode]:
            node.SetAttribute("CineCMRQC.PatientRoot", entry["patient_path"])
            node.SetAttribute("CineCMRQC.SeriesID", entry["series_id"])
            node.SetAttribute(
                "CineCMRQC.DoctorReferenceFrames",
                ",".join(str(value) for value in entry["reference_frames"]),
            )
        segmentationSequenceNode.SetAttribute(
            "CineCMRQC.SourceMaskOrientation",
            entry["mask_orientation"],
        )
        phaseState = logic.initializeCardiacPhaseState(
            segmentationSequenceNode,
            imageSequenceNode,
            entry["mask_path"],
        )
        if phaseState["ed_frame"] >= 0 and phaseState["es_frame"] >= 0:
            automaticPhaseResults.append(
                (entry["series_id"], phaseState["ed_frame"], phaseState["es_frame"])
            )
            if phaseState["confidence"] in ["low", "very-low"]:
                lowConfidencePhaseSeries.append(entry["series_id"])
        else:
            manualPhaseSeries.append(entry["series_id"])
        exportItems.append({
            "series_id": entry["series_id"],
            "image_sequence": imageSequenceNode,
            "segmentation_sequence": segmentationSequenceNode,
            "browser": browserNode,
        })

    with tempfile.TemporaryDirectory(prefix="CineCMRQC-patient-batch-") as outputFolder:
        patientManifestPath = logic.exportPatientSeries(exportItems, outputFolder)
        with open(patientManifestPath, "r", newline="") as fp:
            manifestRows = list(csv.DictReader(fp))
        expectedFrameTotal = sum(entry["image_frame_count"] for entry in readyEntries)
        if len(manifestRows) != expectedFrameTotal:
            raise AssertionError(
                "Expected {0} patient manifest rows, got {1}.".format(
                    expectedFrameTotal,
                    len(manifestRows),
                )
            )
        if any(row["corrected"] != "0" for row in manifestRows):
            raise AssertionError("Unedited imported masks were marked as manually corrected.")

        for entry in readyEntries:
            seriesId = entry["series_id"]
            sourcePngPath = os.path.join(
                patientPath,
                "segmentation",
                seriesId,
                "00000.png",
            )
            exportedFramePath = os.path.join(
                outputFolder,
                seriesId,
                seriesId + "_corrected_frame000.nii.gz",
            )
            sourceArray = np.squeeze(sitk.GetArrayFromImage(sitk.ReadImage(sourcePngPath)))
            exportedArray = np.squeeze(
                sitk.GetArrayFromImage(sitk.ReadImage(exportedFramePath))
            )
            if not np.array_equal(sourceArray, exportedArray):
                raise AssertionError(
                    "Exported frame 0 does not match source PNG for {0}.".format(seriesId)
                )
            if not set(np.unique(exportedArray)).issubset({0, 180, 255}):
                raise AssertionError(
                    "Unexpected exported labels for {0}: {1}.".format(
                        seriesId,
                        np.unique(exportedArray),
                    )
                )
            fourDimensionalPath = os.path.join(
                outputFolder,
                seriesId,
                seriesId + "_corrected_4d.nii.gz",
            )
            fourDimensionalImage = sitk.ReadImage(fourDimensionalPath)
            if fourDimensionalImage.GetDimension() != 4:
                raise AssertionError("Batch export is not 4D for {0}.".format(seriesId))
            if fourDimensionalImage.GetSize()[3] != entry["image_frame_count"]:
                raise AssertionError(
                    "Batch export frame count is incorrect for {0}.".format(seriesId)
                )

    print("PATIENT BATCH TEST PASSED")
    print("Ready series exported: {0}".format(len(readyEntries)))
    print("Patient manifest rows: {0}".format(expectedFrameTotal))
    print("Every frame-0 export matches its source PNG: yes")
    print("Every 4D export has the expected temporal frame count: yes")
    print("Unedited frames marked corrected: 0")
    print("Automatic ED/ES available: {0}/{1}".format(
        len(automaticPhaseResults),
        len(readyEntries),
    ))
    print("Series requiring manual ED/ES: {0}".format(
        ",".join(manualPhaseSeries) if manualPhaseSeries else "none"
    ))
    if manualPhaseSeries:
        raise AssertionError("Every ready series must receive an initial ED/ES estimate.")
    print("Low-confidence fallback ED/ES: {0}".format(
        ",".join(lowConfidencePhaseSeries) if lowConfidencePhaseSeries else "none"
    ))


exitStatus = 0
try:
    runPatientBatchSmokeTest()
except Exception:
    traceback.print_exc()
    exitStatus = 1
finally:
    slicer.app.exit(exitStatus)
