# Unity TU: tools_main.cpp includes tool sources + gift/skin/widgets.
set(TOOLS_ROOT "${LIVEAIO_REPO_ROOT}/tools")
add_library(LiveAIOTools SHARED "${TOOLS_ROOT}/tools_main.cpp")
target_compile_definitions(LiveAIOTools PRIVATE LIVEAIO_TOOLS_BUILD)
target_compile_features(LiveAIOTools PRIVATE cxx_std_17)
target_include_directories(LiveAIOTools PRIVATE
  "${TOOLS_ROOT}"
  "${LIVEAIO_REPO_ROOT}/util"
  "${LIVEAIO_REPO_ROOT}/resources/cpp"
  "${LIVEAIO_REPO_ROOT}/pages"
)
if(MSVC)
  target_compile_options(LiveAIOTools PRIVATE /utf-8 /bigobj /W3)
else()
  target_compile_options(LiveAIOTools PRIVATE -Wall -Wextra)
endif()

find_package(Qt6 REQUIRED COMPONENTS Widgets Network Concurrent OpenGL)
target_link_libraries(LiveAIOTools PRIVATE
  Qt6::Widgets Qt6::Network Qt6::Concurrent Qt6::OpenGL
)

set_target_properties(LiveAIOTools PROPERTIES
  RUNTIME_OUTPUT_DIRECTORY "${LIVEAIO_OUT_DIR}"
  RUNTIME_OUTPUT_DIRECTORY_RELEASE "${LIVEAIO_OUT_DIR}"
  RUNTIME_OUTPUT_DIRECTORY_DEBUG "${LIVEAIO_OUT_DIR}"
  LIBRARY_OUTPUT_DIRECTORY "${LIVEAIO_OUT_DIR}"
  LIBRARY_OUTPUT_DIRECTORY_RELEASE "${LIVEAIO_OUT_DIR}"
  LIBRARY_OUTPUT_DIRECTORY_DEBUG "${LIVEAIO_OUT_DIR}"
  ARCHIVE_OUTPUT_DIRECTORY "${LIVEAIO_OUT_DIR}"
  PREFIX ""
  OUTPUT_NAME "LiveAIOTools"
)
