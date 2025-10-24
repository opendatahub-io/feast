#!/bin/bash
set -Eeuo pipefail
trap 'echo "[prebuild-power] failed at line $LINENO"; exit 1' ERR
shopt -s dotglob nullglob

echo "[prebuild-power] Starting prebuild script..."

# Detect release version from requirements.txt if not already provided
if [[ -z "${RELEASE_VERSION:-}" && -f "requirements.txt" ]]; then
    RELEASE_VERSION=$(grep -E '^feast\[minimal\]' requirements.txt | sed -E 's/.*==\s*([0-9]+\.[0-9]+\.[0-9]+).*/v\1/')
    echo "[prebuild-power] Detected RELEASE_VERSION=$RELEASE_VERSION from requirements.txt"
fi

# URL to requirements file (using the release version)
REQ_FILE_URL="https://raw.githubusercontent.com/opendatahub-io/feast/${RELEASE_VERSION}/sdk/python/requirements/py3.11-ci-requirements.txt"

# Fetch the file
echo "[prebuild-power] Fetching package versions from $REQ_FILE_URL ..."
if ! REQ_CONTENT=$(curl -fsSL "$REQ_FILE_URL"); then
    echo "[prebuild-power] Failed to fetch from branch '$BRANCH'. Falling back to master..."
    REQ_FILE_URL="https://raw.githubusercontent.com/opendatahub-io/feast/master/sdk/python/requirements/py3.11-ci-requirements.txt"
    REQ_CONTENT=$(curl -fsSL "$REQ_FILE_URL")
fi

# Extract package versions
DUCKDB_VER=$(echo "$REQ_CONTENT" | grep -E "^duckdb==" | head -n1 | sed -E 's/.*==([0-9a-zA-Z\.\-]+).*/\1/')
GRPCIO_VER=$(echo "$REQ_CONTENT" | grep -E "^grpcio==" | head -n1 | sed -E 's/.*==([0-9a-zA-Z\.\-]+).*/\1/')
PYARROW_VER=$(echo "$REQ_CONTENT" | grep -E "^pyarrow==" | head -n1 | sed -E 's/.*==([0-9a-zA-Z\.\-]+).*/\1/')
MILVUS_VER=$(echo "$REQ_CONTENT" | grep -E "^milvus-lite==" | head -n1 | sed -E 's/.*==([0-9a-zA-Z\.\-]+).*/\1/')

echo "[prebuild-power] Detected versions:"
echo "  duckdb=$DUCKDB_VER"
echo "  grpcio=$GRPCIO_VER"
echo "  pyarrow=$PYARROW_VER"
echo "  milvus-lite=$MILVUS_VER"

# Ensure all versions were detected
if [[ -z "$DUCKDB_VER" || -z "$GRPCIO_VER" || -z "$PYARROW_VER" || -z "$MILVUS_VER" ]]; then
    echo "[prebuild-power] Error: One or more package versions could not be detected."
    exit 1
fi


PYTHON_VERSION=3.11
WORKDIR=$(pwd)

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
echo "[prebuild-power] Building duckdb==$DUCKDB_VER ..."
git clone https://github.com/duckdb/duckdb.git
cd duckdb
git checkout "v${DUCKDB_VER}"
cd tools/pythonpkg
python${PYTHON_VERSION} -m build --wheel --no-isolation
ls dist/*.whl >/dev/null
cp -v dist/*.whl /wheelhouse/
cd $WORKDIR

#######################################################
# Build gRPC  (Python package)
#######################################################
echo "[prebuild-power] Building grpcio==$GRPCIO_VER ..."
GRPC_PYTHON_BUILD_SYSTEM_OPENSSL=1 pip install --no-binary=:all: "grpcio==${GRPCIO_VER}"

#######################################################
# Build Pyarrow  (Python package)
#######################################################
echo "[prebuild-power] Building pyarrow==$PYARROW_VER ..."
git clone https://github.com/apache/arrow.git
cd arrow
git checkout "apache-arrow-${PYARROW_VER}"
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
make -j"$(nproc)"
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
echo "[prebuild-power] Building milvus-lite==$MILVUS_VER ..."
# Remove gcc-toolset-13; Milvus-Lite build (via Conan) requires standard gcc
dnf remove -y gcc-toolset-13

dnf install -y perl ncurses-devel wget openblas-devel cargo gcc gcc-c++ libstdc++-static which libaio \
               libtool m4 autoconf automake zlib-devel libffi-devel scl-utils xz

export CC=gcc
export CXX=g++
export CXXFLAGS="-std=c++17"

python${PYTHON_VERSION} -m pip install conan==1.64.1

git clone https://github.com/milvus-io/milvus-lite
cd milvus-lite/python
git checkout "v${MILVUS_VER}"
git submodule update --init --recursive
python${PYTHON_VERSION} -m pip install -v -e .
cd $WORKDIR

echo "[prebuild-power] All packages built successfully."
