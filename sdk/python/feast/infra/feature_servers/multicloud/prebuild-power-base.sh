#!/bin/bash
set -Eeuo pipefail
trap 'echo "[prebuild-power] failed at line $LINENO"; exit 1' ERR
shopt -s dotglob nullglob

PYTHON_VERSION=3.11
WORKDIR=$(pwd)
CMAKE_VERSION=3.30.5
CMAKE_REQUIRED_VERSION=3.30.5

dnf install -y gcc-toolset-13 make cmake ninja-build libomp-devel \
               git python${PYTHON_VERSION} python${PYTHON_VERSION}-devel python${PYTHON_VERSION}-pip \
               openssl openssl-devel zlib-devel libuuid-devel 

# Enable GCC toolset
source /opt/rh/gcc-toolset-13/enable
export CXX=/opt/rh/gcc-toolset-13/root/usr/bin/g++

# Ensure CXXFLAGS and LINKFLAGS are initialized
: "${CMAKE_ARGS:=""}"
: "${CXXFLAGS:=""}"
: "${CFLAGS:=""}"
: "${LINKFLAGS:=""}"

# Installing Python build dependencies
python${PYTHON_VERSION} -m pip install build wheel setuptools ninja pybind11 numpy setuptools_scm Cython==3.0.8

# Directory to collect built wheels
mkdir -p /wheelhouse

#######################################################
# Build DuckDB (Python package)
#######################################################
echo "Building duckdb..."
git clone https://github.com/duckdb/duckdb.git
cd duckdb
git checkout v1.1.3
cd tools/pythonpkg
python${PYTHON_VERSION} -m build --wheel --no-isolation
ls dist/*.whl >/dev/null
cp -v dist/*.whl /wheelhouse/
cd $WORKDIR

#######################################################
# Build gRPC  (Python package)
#######################################################
echo "Building grpcio..."
export GRPC_PYTHON_BUILD_SYSTEM_OPENSSL=1
pip install grpcio==1.62.3

#######################################################
# Build Pyarrow  (Python package)
#######################################################
git clone https://github.com/apache/arrow.git
cd arrow
git checkout apache-arrow-17.0.0
git submodule update --init --recursive
cd cpp
mkdir -p release && cd release
cmake -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/usr/local \
      -DARROW_PYTHON=ON \
      -DARROW_PARQUET=ON \
      -DARROW_ORC=ON \
      -DARROW_FILESYSTEM=ON \
      -DARROW_WITH_LZ4=ON \
      -DARROW_WITH_ZSTD=ON \
      -DARROW_WITH_SNAPPY=ON \
      -DARROW_JSON=ON \
      -DARROW_CSV=ON \
      -DARROW_DATASET=ON \
      -DARROW_S3=ON \
      -DARROW_BUILD_TESTS=OFF \
      -DARROW_SUBSTRAIT=ON \
      -DProtobuf_SOURCE=BUNDLED \
      -DARROW_DEPENDENCY_SOURCE=BUNDLED \
    ..
make -j$(nproc)
make install
cd ../../python
export BUILD_TYPE=release
python${PYTHON_VERSION} setup.py build_ext --build-type=$BUILD_TYPE --bundle-arrow-cpp bdist_wheel
ls dist/*.whl >/dev/null
cp -v dist/*.whl /wheelhouse/
cd $WORKDIR

#######################################################
# Build Milvus-Lite  (Python package)
#######################################################
echo "Building milvus-lite..."
dnf remove -y gcc-toolset-13

dnf install -y perl ncurses-devel wget openblas-devel cargo gcc gcc-c++ libstdc++-static which libaio \
               libtool m4 autoconf automake zlib-devel libffi-devel scl-utils xz

export CC=gcc
export CXX=g++
export CXXFLAGS="-std=c++17"

python${PYTHON_VERSION} -m pip install conan==1.64.1 setuptools==70.0.0

git clone https://github.com/milvus-io/milvus-lite
cd milvus-lite/python
git checkout v2.4.12
git submodule update --init --recursive
python${PYTHON_VERSION} -m pip install -v -e .
