# Unity TU: pages_main.cpp includes the other page sources.
set(PAGES_ROOT "${LIVEAIO_REPO_ROOT}/pages")
add_library(LiveAIOPages SHARED "${PAGES_ROOT}/pages_main.cpp")
target_compile_definitions(LiveAIOPages PRIVATE LIVEAIO_PAGES_BUILD)
target_compile_features(LiveAIOPages PRIVATE cxx_std_17)
target_include_directories(LiveAIOPages PRIVATE
  "${PAGES_ROOT}"
  "${LIVEAIO_REPO_ROOT}/util"
  "${LIVEAIO_REPO_ROOT}/resources/cpp"
  "${LIVEAIO_REPO_ROOT}/tools"
)
if(MSVC)
  target_compile_options(LiveAIOPages PRIVATE /utf-8 /bigobj /W3)
else()
  target_compile_options(LiveAIOPages PRIVATE -Wall -Wextra)
endif()

find_package(Qt6 REQUIRED COMPONENTS Widgets Network Concurrent OpenGL)
target_link_libraries(LiveAIOPages PRIVATE
  Qt6::Widgets Qt6::Network Qt6::Concurrent Qt6::OpenGL
)

set_target_properties(LiveAIOPages PROPERTIES
  RUNTIME_OUTPUT_DIRECTORY "${LIVEAIO_OUT_DIR}"
  RUNTIME_OUTPUT_DIRECTORY_RELEASE "${LIVEAIO_OUT_DIR}"
  RUNTIME_OUTPUT_DIRECTORY_DEBUG "${LIVEAIO_OUT_DIR}"
  LIBRARY_OUTPUT_DIRECTORY "${LIVEAIO_OUT_DIR}"
  LIBRARY_OUTPUT_DIRECTORY_RELEASE "${LIVEAIO_OUT_DIR}"
  LIBRARY_OUTPUT_DIRECTORY_DEBUG "${LIVEAIO_OUT_DIR}"
  ARCHIVE_OUTPUT_DIRECTORY "${LIVEAIO_OUT_DIR}"
  PREFIX ""
  OUTPUT_NAME "LiveAIOPages"
)
