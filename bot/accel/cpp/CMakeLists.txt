cmake_minimum_required(VERSION 3.18)
project(everabot_chunk_cpp LANGUAGES CXX)

# Recommended with scikit-build-core + pybind11
set(PYBIND11_FINDPYTHON ON)          # let pybind11 use modern FindPython
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(pybind11 REQUIRED)

pybind11_add_module(everabot_chunk_cpp src/chunk_ext.cpp)

# ⬅️ This is the crucial bit: put the extension at the wheel root
install(TARGETS everabot_chunk_cpp LIBRARY DESTINATION .)
