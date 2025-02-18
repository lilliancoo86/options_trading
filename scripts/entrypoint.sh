#!/bin/bash

echo "Starting initialization..."

# 启动 MySQL
echo "Starting MySQL..."
/usr/sbin/mysqld --daemonize --user=mysql

# 等待 MySQL 启动
until mysqladmin ping -h localhost --silent; do
    echo 'waiting for mysqld to be ready...'
    sleep 1
done

# 初始化 MySQL（如果需要）
if [ ! -f "/var/lib/mysql/ibdata1" ]; then
    echo "Initializing MySQL..."
    mysql_install_db --user=mysql --ldata=/var/lib/mysql/
fi

# 创建并激活虚拟环境
echo "Setting up Python virtual environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

# 激活虚拟环境
source venv/bin/activate

# 安装依赖
echo "Installing Python dependencies..."
pip install --upgrade pip
pip install wheel
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
fi

# 检查依赖是否安装完整
echo "Checking installed packages..."
pip list

echo "Initialization completed."

# 启动交易程序
echo "Starting trading system..."
python -m scripts.main