add_executable(LiveAIO WIN32 main.cpp)
target_compile_features(LiveAIO PRIVATE cxx_std_17)
if(MSVC)
  target_compile_options(LiveAIO PRIVATE /utf-8 /W3)
else()
  target_compile_options(LiveAIO PRIVATE -Wall -Wextra)
endif()
set_target_properties(LiveAIO PROPERTIES
  RUNTIME_OUTPUT_DIRECTORY "${LIVEAIO_OUT_DIR}"
  RUNTIME_OUTPUT_DIRECTORY_RELEASE "${LIVEAIO_OUT_DIR}"
  RUNTIME_OUTPUT_DIRECTORY_DEBUG "${LIVEAIO_OUT_DIR}"
  OUTPUT_NAME "LiveAIO"
)
target_link_libraries(LiveAIO PRIVATE shell32 user32)
if(MINGW)
  target_link_options(LiveAIO PRIVATE -municode)
  target_compile_definitions(LiveAIO PRIVATE UNICODE _UNICODE)
endif()
