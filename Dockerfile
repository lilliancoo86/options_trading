# 使用标准 CentOS 基础镜像
FROM centos:7.9.2009

# 设置维护者信息
LABEL maintainer="trader@doomsday.options"

# 设置环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHON_VERSION=3.9.7 \
    MYSQL_VERSION=5.7.9 \
    OPENSSL_VERSION=1.1.1 \
    PATH="/usr/local/bin:$PATH" \
    LD_LIBRARY_PATH="/usr/local/lib64:/usr/local/lib:$LD_LIBRARY_PATH"

# 设置 DNS
RUN echo "nameserver 8.8.8.8" > /etc/resolv.conf && \
    echo "nameserver 8.8.4.4" >> /etc/resolv.conf

# 安装基础工具和依赖
RUN yum update -y && \
    yum groupinstall -y "Development Tools" && \
    yum install -y \
        wget \
        zlib-devel \
        bzip2-devel \
        openssl-devel \
        ncurses-devel \
        sqlite-devel \
        readline-devel \
        tk-devel \
        gdbm-devel \
        db4-devel \
        libpcap-devel \
        xz-devel \
        libffi-devel \
        epel-release \
        which

# 安装 OpenSSL 1.1.1
RUN cd /usr/local/src && \
    wget --no-check-certificate https://www.openssl.org/source/openssl-1.1.1.tar.gz && \
    tar xzf openssl-1.1.1.tar.gz && \
    cd openssl-1.1.1 && \
    ./Configure linux-x86_64 --prefix=/usr/local/ssl --openssldir=/usr/local/ssl shared zlib && \
    make -j$(nproc) && \
    make install && \
    echo "/usr/local/ssl/lib" > /etc/ld.so.conf.d/openssl-1.1.1.conf && \
    ldconfig || true

# 安装 Python 3.9.7
RUN cd /usr/local/src && \
    wget --no-check-certificate https://www.python.org/ftp/python/3.9.7/Python-3.9.7.tgz && \
    tar xzf Python-3.9.7.tgz && \
    cd Python-3.9.7 && \
    ./configure --enable-optimizations --with-openssl=/usr/local/ssl --enable-shared && \
    make -j$(nproc) && \
    make altinstall && \
    ln -sf /usr/local/bin/python3.9 /usr/local/bin/python3 && \
    ln -sf /usr/local/bin/pip3.9 /usr/local/bin/pip3

# 安装 MySQL 5.7.9
RUN yum install -y https://dev.mysql.com/get/mysql80-community-release-el7-3.noarch.rpm && \
    yum-config-manager --disable mysql80-community && \
    yum-config-manager --enable mysql57-community && \
    yum install -y mysql-community-server-5.7.9 && \
    rm -rf /var/cache/yum/*

# 创建工作目录
WORKDIR /opt/doomsday







# 创建必要的目录
RUN mkdir -p data logs config

# 设置权限
RUN chmod +x scripts/entrypoint.sh

# 暴露端口
EXPOSE 3306

# 设置入口点
ENTRYPOINT ["./scripts/entrypoint.sh"] 