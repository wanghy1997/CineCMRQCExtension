cmake_minimum_required(VERSION 3.16.3...3.19.7 FATAL_ERROR)

project(CineCMRQCExtension VERSION 0.3.0)

set(EXTENSION_HOMEPAGE "https://github.com/wanghy1997/CineCMRQCExtension")
set(EXTENSION_CONTRIBUTORS "Wang Hongyi, Codex")
set(EXTENSION_DESCRIPTION "Cine CMR temporal segmentation review, ED/ES confirmation, and audit workflow for 3D Slicer.")
set(EXTENSION_ICONURL "https://www.slicer.org/assets/img/3D-Slicer-Mark.svg")
set(EXTENSION_SCREENSHOTURLS "")
set(EXTENSION_DEPENDS "NA")

find_package(Slicer REQUIRED)
include(${Slicer_USE_FILE})

add_subdirectory(CineCMRQC)

include(${Slicer_EXTENSION_GENERATE_CONFIG})
include(${Slicer_EXTENSION_CPACK})
