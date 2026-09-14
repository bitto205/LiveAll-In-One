cmake_minimum_required(VERSION 3.21)
project(LiveAIO LANGUAGES CXX)

# Repo root = parent of scripts/
get_filename_component(LIVEAIO_REPO_ROOT "${CMAKE_CURRENT_LIST_DIR}/.." ABSOLUTE)
if(NOT LIVEAIO_OUT_DIR)
  set(LIVEAIO_OUT_DIR "${LIVEAIO_REPO_ROOT}/build/build_work/custom")
endif()
file(MAKE_DIRECTORY "${LIVEAIO_OUT_DIR}")

option(LIVEAIO_BUILD_HOST "Build LiveAIO.exe host" ON)
option(LIVEAIO_BUILD_PAGES "Build LiveAIOPages.dll" ON)
option(LIVEAIO_BUILD_TOOLS "Build LiveAIOTools.dll" ON)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
set(CMAKE_AUTOMOC OFF)
set(CMAKE_AUTORCC OFF)
set(CMAKE_AUTOUIC OFF)

# Drop lib-prefix on MinGW shared libs so names match LoadLibrary expectations.
if(MINGW)
  set(CMAKE_SHARED_LIBRARY_PREFIX "")
endif()

if(LIVEAIO_BUILD_HOST)
  add_subdirectory(host)
endif()
if(LIVEAIO_BUILD_PAGES)
  add_subdirectory(pages)
endif()
if(LIVEAIO_BUILD_TOOLS)
  add_subdirectory(tools)
endif()

message(STATUS "LIVEAIO_REPO_ROOT=${LIVEAIO_REPO_ROOT}")
message(STATUS "LIVEAIO_OUT_DIR=${LIVEAIO_OUT_DIR}")
