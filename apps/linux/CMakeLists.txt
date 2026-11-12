cmake_minimum_required(VERSION 3.20)

project(firesky_linux_native LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
option(FIRESKY_BUILD_GTK_UI "Build the optional GTK4 native UI" ON)
find_package(Threads REQUIRED)

add_library(firesky_ffi_client STATIC
    src/firesky_ffi_client.cpp
)

target_include_directories(firesky_ffi_client
    PUBLIC
        "${CMAKE_CURRENT_SOURCE_DIR}/include"
        "${CMAKE_CURRENT_SOURCE_DIR}/../../engine/include"
)

target_compile_features(firesky_ffi_client PUBLIC cxx_std_20)
target_compile_options(firesky_ffi_client PRIVATE -Wall -Wextra -Wpedantic)
target_link_libraries(firesky_ffi_client PUBLIC ${CMAKE_DL_LIBS} Threads::Threads)

add_executable(firesky_linux_ffi_smoke
    src/ffi_smoke.cpp
)
target_link_libraries(firesky_linux_ffi_smoke PRIVATE firesky_ffi_client)
target_compile_options(firesky_linux_ffi_smoke PRIVATE -Wall -Wextra -Wpedantic)

if(FIRESKY_BUILD_GTK_UI)
    find_package(PkgConfig QUIET)
    if(PkgConfig_FOUND)
        pkg_check_modules(GTK4 QUIET IMPORTED_TARGET gtk4)
    endif()
    if(GTK4_FOUND)
        add_executable(firesky_linux_native
            src/gtk_app.cpp
        )
        target_link_libraries(firesky_linux_native
            PRIVATE
                firesky_ffi_client
                PkgConfig::GTK4
        )
        target_compile_options(firesky_linux_native PRIVATE -Wall -Wextra -Wpedantic)
    else()
        message(STATUS "GTK4 not found; skipping firesky_linux_native (FFI smoke remains available)")
    endif()
endif()

