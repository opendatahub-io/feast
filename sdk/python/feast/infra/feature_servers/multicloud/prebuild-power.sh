#!/bin/bash

PYTHON_VERSION=3.11
WORKDIR=$(pwd)
CMAKE_VERSION=3.30.5
CMAKE_REQUIRED_VERSION=3.30.5

# Enable EPEL and install required OS packages
dnf install -y https://dl.fedoraproject.org/pub/epel/epel-release-latest-9.noarch.rpm
dnf install -y gcc-toolset-13 make cmake ninja-build libomp-devel \
               git python${PYTHON_VERSION} python${PYTHON_VERSION}-devel python${PYTHON_VERSION}-pip \
               openssl openssl-devel zlib-devel libuuid-devel 

# Enable GCC toolset
source /opt/rh/gcc-toolset-13/enable
export CXX=/opt/rh/gcc-toolset-13/root/usr/bin/g++

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
cd ../../..

#######################################################
# Build gRPC  (Python package)
#######################################################
echo "Building grpcio..."
git clone https://github.com/grpc/grpc.git -b v1.62.3
cd grpc
git checkout v1.62.3
git submodule update --init --recursive
python${PYTHON_VERSION} -m pip install -r requirements.txt
export GRPC_PYTHON_BUILD_SYSTEM_OPENSSL=1
python${PYTHON_VERSION} -m build --wheel --no-isolation
ls dist/*.whl >/dev/null
cp -v dist/*.whl /wheelhouse/
cd ..

#######################################################
# Build Pyarrow  (Python package)
#######################################################
echo "Building pyarrow..."
dnf install -y boost1.78-devel.ppc64le gflags-devel rapidjson-devel.ppc64le re2-devel.ppc64le \
               gtest-devel gmock-devel


# utf8proc installing
git clone https://github.com/JuliaStrings/utf8proc.git
cd utf8proc
git submodule update --init
git checkout v2.6.1

mkdir utf8proc_prefix
export UTF8PROC_PREFIX=$(pwd)/utf8proc_prefix

# Create build directory
mkdir build
cd build
# Run cmake to configure the build
cmake -G "Unix Makefiles" \
  -DCMAKE_BUILD_TYPE="Release" \
  -DCMAKE_INSTALL_PREFIX="${UTF8PROC_PREFIX}" \
  -DCMAKE_POSITION_INDEPENDENT_CODE=1 \
  -DBUILD_SHARED_LIBS=1 \
  ..

cmake --build .
cmake --build . --target install
cd $WORKDIR

# snappy installing
git clone https://github.com/google/snappy.git
cd snappy
git submodule update --init --recursive

mkdir -p local/snappy
export SNAPPY_PREFIX=$(pwd)/local/snappy
mkdir build
cd build
echo "Running cmake to configure the build for snappy..."
cmake -DCMAKE_INSTALL_PREFIX=$SNAPPY_PREFIX \
      -DBUILD_SHARED_LIBS=ON \
      -DCMAKE_INSTALL_LIBDIR=lib \
      ..
make -j$(nproc)
make install
cd ..
cd $WORKDIR

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
      -DARROW_SUBSTRAIT=ON \
      -DPROTOBUF_PROTOC_EXECUTABLE=/usr/bin/protoc \
      -DARROW_DEPENDENCY_SOURCE=BUNDLED \
    ..
make -j$(nproc)
make install
cd ../../python
export BUILD_TYPE=release
python${PYTHON_VERSION} setup.py build_ext --build-type=$BUILD_TYPE --bundle-arrow-cpp bdist_wheel
ls dist/*.whl >/dev/null
cp -v dist/*.whl /wheelhouse/
cd ../../..

#######################################################
# Build Milvus-Lite  (Python package)
#######################################################
echo "Building milvus-lite..."
dnf remove -y gcc-toolset-13
dnf install -y wget perl openblas-devel cargo gcc gcc-c++ libstdc++-static which libaio \
               ncurses-devel libtool m4 autoconf automake zlib-devel libffi-devel scl-utils xz

export CC=gcc
export CXX=g++
export CXXFLAGS="-std=c++17"

python${PYTHON_VERSION} -m pip install wheel conan==1.64.1 setuptools==70.0.0

echo "installing texinfo"
wget https://ftp.gnu.org/gnu/texinfo/texinfo-7.1.tar.xz
tar -xf texinfo-7.1.tar.xz
cd texinfo-7.1
./configure
make -j2
make install
cd ..

echo "installing rust 1.73"
curl https://sh.rustup.rs -sSf | sh -s -- --default-toolchain=1.73 -y
source $HOME/.cargo/env
rustc --version

echo "installing cmake"
# Install CMake
mkdir -p "${WORKDIR}/workspace"
cd "${WORKDIR}/workspace"
wget -c https://github.com/Kitware/CMake/releases/download/v${CMAKE_VERSION}/cmake-${CMAKE_VERSION}.tar.gz
tar -zxvf cmake-${CMAKE_VERSION}.tar.gz
rm -rf cmake-${CMAKE_VERSION}.tar.gz
cd cmake-${CMAKE_VERSION}
./bootstrap --prefix=/usr/local/cmake --parallel=2 -- -DBUILD_TESTING:BOOL=OFF -DCMAKE_BUILD_TYPE:STRING=Release -DCMAKE_USE_OPENSSL:BOOL=ON
make install -j2
export PATH=/usr/local/cmake/bin:$PATH
cmake --version
cd ..

cd $WORKDIR
git clone https://github.com/milvus-io/milvus-lite
cd milvus-lite/python
git checkout v2.4.12
git submodule update --init --recursive

create_cmake_conanfile()
{
    touch /usr/local/cmake/conanfile.py
    cat <<EOT >> /usr/local/cmake/conanfile.py
from conans import ConanFile, tools
class CmakeConan(ConanFile):
  name = "cmake"
  package_type = "application"
  version = "${CMAKE_REQUIRED_VERSION}"
  description = "CMake, the cross-platform, open-source build system."
  homepage = "https://github.com/Kitware/CMake"
  license = "BSD-3-Clause"
  topics = ("build", "installer")
  settings = "os", "arch"
  def package(self):
    self.copy("*")
  def package_info(self):
    self.cpp_info.libs = tools.collect_libs(self)
EOT
}

#build the package
pushd /usr/local/cmake
create_cmake_conanfile
conan export-pkg . cmake/${CMAKE_REQUIRED_VERSION}@ -s os="Linux" -s arch="ppc64le" -f
conan profile update settings.compiler.libcxx=libstdc++11 default
popd
export VCPKG_FORCE_SYSTEM_BINARIES=1
mkdir -p $HOME/.cargo/bin/

python${PYTHON_VERSION} -m build --wheel --no-isolation

ls dist/*.whl >/dev/null
cp -v dist/*.whl /wheelhouse/


