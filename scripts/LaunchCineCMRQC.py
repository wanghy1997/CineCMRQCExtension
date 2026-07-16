import os
import traceback

import slicer


def launchCineCMRQC():
    patientPath = os.environ.get("CINE_PATIENT_PATH", "").strip()
    seriesId = os.environ.get("CINE_SERIES_ID", "").strip()

    slicer.util.selectModule("CineCMRQC")
    widget = slicer.modules.cinecmrqc.widgetRepresentation().self()
    if patientPath:
        if not os.path.isdir(patientPath):
            raise ValueError("患者目录不存在：{0}".format(patientPath))
        widget.preferredPatientSeriesId = seriesId
        widget.patientPathEdit.currentPath = patientPath
        widget.onScanPatientFolder()
        if not widget.patientSeriesEntries:
            raise ValueError("患者目录中没有医生关注的 Series。")
        if seriesId and (
            not widget.imageSequenceNode
            or widget.imageSequenceNode.GetAttribute("CineCMRQC.SeriesID") != seriesId
        ):
            raise ValueError("找不到或无法加载 Series：{0}".format(seriesId))

    mainWindow = slicer.util.mainWindow()
    if mainWindow:
        pythonConsole = mainWindow.pythonConsole()
        if pythonConsole and pythonConsole.parent():
            pythonConsole.parent().hide()
        mainWindow.showMaximized()
        mainWindow.raise_()
        mainWindow.activateWindow()


try:
    launchCineCMRQC()
except Exception as exc:
    traceback.print_exc()
    slicer.util.errorDisplay("启动 Cine CMR 逐帧质控失败：\n{0}".format(exc))
