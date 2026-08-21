#!/bin/bash
set -Eeuo pipefail
trap 'echo "[prebuild-power] failed at line $LINENO"; exit 1' ERR
shopt -s dotglob nullglob
# Must match the ubi9/python-312-minimal image used by Dockerfile.
PYTHON_VERSION=3.12
WORKDIR=$(pwd)

echo "[prebuild-power] Starting prebuild script..."

# Detect release version from requirements.txt if not already provided
if [[ -z "${RELEASE_VERSION:-}" && -f "requirements.txt" ]]; then
    RELEASE_VERSION=$(grep -E '^feast\[minimal\]' requirements.txt | sed -E 's/.*==\s*([0-9]+\.[0-9]+\.[0-9]+).*/v\1/')
    echo "[prebuild-power] Detected RELEASE_VERSION=$RELEASE_VERSION from requirements.txt"
fi

fetch_req_file() {
    local url
    local urls=(
        "https://raw.githubusercontent.com/opendatahub-io/feast/${RELEASE_VERSION}/sdk/python/requirements/py${PYTHON_VERSION}-ci-requirements.txt"
        "https://raw.githubusercontent.com/feast-dev/feast/${RELEASE_VERSION}/sdk/python/requirements/py${PYTHON_VERSION}-ci-requirements.txt"
        "https://raw.githubusercontent.com/opendatahub-io/feast/master/sdk/python/requirements/py${PYTHON_VERSION}-ci-requirements.txt"
        "https://raw.githubusercontent.com/feast-dev/feast/master/sdk/python/requirements/py${PYTHON_VERSION}-ci-requirements.txt"
    )
    for url in "${urls[@]}"; do
        echo "[prebuild-power] Trying $url ..."
        if REQ_CONTENT=$(curl -fsSL "$url"); then
            echo "[prebuild-power] Fetched package versions from $url"
            return 0
        fi
    done
    return 1
}

# feast[minimal] accepts pyarrow>=16.1.0. Prefer a version that still builds via
# apache-arrow setup.py on ppc64le (PyPI sdists for 25.x require libcst/Rust).
FALLBACK_DUCKDB_VER=1.5.5
FALLBACK_GRPCIO_VER=1.83.0
FALLBACK_PYARROW_VER=21.0.0
FALLBACK_MILVUS_VER=3.1.1

if fetch_req_file; then
    DUCKDB_VER=$(echo "$REQ_CONTENT" | grep -E "^duckdb==" | head -n1 | sed -E 's/.*==([0-9a-zA-Z\.\-]+).*/\1/')
    GRPCIO_VER=$(echo "$REQ_CONTENT" | grep -E "^grpcio==" | head -n1 | sed -E 's/.*==([0-9a-zA-Z\.\-]+).*/\1/')
    PYARROW_VER=$(echo "$REQ_CONTENT" | grep -E "^pyarrow==" | head -n1 | sed -E 's/.*==([0-9a-zA-Z\.\-]+).*/\1/')
    MILVUS_VER=$(echo "$REQ_CONTENT" | grep -E "^milvus-lite==" | head -n1 | sed -E 's/.*==([0-9a-zA-Z\.\-]+).*/\1/')
else
    echo "[prebuild-power] Could not fetch requirements; using fallback versions"
fi

DUCKDB_VER=${DUCKDB_VER:-$FALLBACK_DUCKDB_VER}
GRPCIO_VER=${GRPCIO_VER:-$FALLBACK_GRPCIO_VER}
PYARROW_VER=${PYARROW_VER:-$FALLBACK_PYARROW_VER}
MILVUS_VER=${MILVUS_VER:-$FALLBACK_MILVUS_VER}

# PyPI sdists for pyarrow 22+ pull libcst, which needs a Rust compiler.
# Build 21.x from Apache Arrow sources via setup.py instead; feast[minimal]
# only requires pyarrow>=16.1.0.
if [[ "${PYARROW_VER%%.*}" -ge 22 ]]; then
    echo "[prebuild-power] Capping pyarrow $PYARROW_VER -> 21.0.0 for ppc64le source build"
    PYARROW_VER=21.0.0
fi

echo "[prebuild-power] Detected versions:"
echo "  duckdb=$DUCKDB_VER"
echo "  grpcio=$GRPCIO_VER"
echo "  pyarrow=$PYARROW_VER"
echo "  milvus-lite=$MILVUS_VER"

if [[ -z "$DUCKDB_VER" || -z "$GRPCIO_VER" || -z "$PYARROW_VER" || -z "$MILVUS_VER" ]]; then
    echo "[prebuild-power] Error: One or more package versions could not be detected."
    exit 1
fi

# libcurl-devel is required when ARROW_S3=ON (aws-sdk-cpp).
# libatomic is required to link libarrow.so on ppc64le (128-bit atomics).
dnf install -y gcc-toolset-13 make cmake ninja-build libomp-devel \
               git python${PYTHON_VERSION} python${PYTHON_VERSION}-devel python${PYTHON_VERSION}-pip \
               openssl openssl-devel zlib-devel libuuid-devel libcurl-devel pkgconf-pkg-config \
               libatomic
dnf install -y gcc-toolset-13-libatomic-devel || \
    echo "[prebuild-power] gcc-toolset-13-libatomic-devel not available; using system libatomic"

# Enable GCC toolset
source /opt/rh/gcc-toolset-13/enable
export CXX=/opt/rh/gcc-toolset-13/root/usr/bin/g++
export CC=/opt/rh/gcc-toolset-13/root/usr/bin/gcc
export LIBRARY_PATH="/usr/lib64${LIBRARY_PATH:+:$LIBRARY_PATH}"
: "${LDFLAGS:=""}"
: "${LINKFLAGS:=""}"
export LDFLAGS="${LDFLAGS} -L/usr/lib64 -latomic"
export LINKFLAGS="${LINKFLAGS} -L/usr/lib64 -latomic"

# Installing Python build dependencies
python${PYTHON_VERSION} -m pip install build wheel setuptools ninja pybind11 numpy setuptools_scm Cython==3.0.8

# Directory to collect built wheels
mkdir -p /wheelhouse

# #######################################################
# # Build DuckDB (Python package)
# #######################################################
# echo "[prebuild-power] Building duckdb==$DUCKDB_VER ..."
# git clone https://github.com/duckdb/duckdb.git
# cd duckdb
# git checkout "v${DUCKDB_VER}"
# cd tools/pythonpkg
# python${PYTHON_VERSION} -m build --wheel --no-isolation
# ls dist/*.whl >/dev/null
# cp -v dist/*.whl /wheelhouse/
# cd $WORKDIR
echo "[prebuild-power] Skipping duckdb - will be built by uv from source"

#######################################################
# Build gRPC  (Python package)
#######################################################
echo "[prebuild-power] Building grpcio==$GRPCIO_VER ..."
GRPC_PYTHON_BUILD_SYSTEM_OPENSSL=1 python${PYTHON_VERSION} -m pip wheel --no-binary=:all: -w /wheelhouse "grpcio==${GRPCIO_VER}"

#######################################################
# Build Pyarrow  (Python package)
#######################################################
echo "[prebuild-power] Building pyarrow==$PYARROW_VER ..."
IBM_INDEX="https://wheels.developerfirst.ibm.com/ppc64le/linux"
if python${PYTHON_VERSION} -m pip download --only-binary=:all: --no-deps \
      -d /wheelhouse --extra-index-url "$IBM_INDEX" "pyarrow==${PYARROW_VER}"; then
    echo "[prebuild-power] Using prebuilt IBM ppc64le pyarrow wheel"
else
    echo "[prebuild-power] No IBM wheel; building pyarrow from source"
    git clone https://github.com/apache/arrow.git
    cd arrow
    git checkout "apache-arrow-${PYARROW_VER}"
    git submodule update --init --recursive
    cd cpp
    mkdir -p release && cd release
    cmake -DCMAKE_BUILD_TYPE=Release \
          -DCMAKE_INSTALL_PREFIX=/usr/local \
          -DCMAKE_SHARED_LINKER_FLAGS="-L/usr/lib64 -latomic" \
          -DCMAKE_EXE_LINKER_FLAGS="-L/usr/lib64 -latomic" \
          -DCMAKE_MODULE_LINKER_FLAGS="-L/usr/lib64 -latomic" \
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
fi

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
echo "[prebuild-power] Wheels in /wheelhouse:"
ls -lh /wheelhouse
