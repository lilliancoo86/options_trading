#!/bin/bash

# 设置错误时退出
set -e

echo "开始更新代码..."

# 1. 进入项目目录
cd /home/options_trading
echo "已进入项目目录: $(pwd)"

# 2. 激活虚拟环境
source venv/bin/activate
echo "已激活虚拟环境"

# 3. 拉取最新代码
echo "正在拉取最新代码..."
git fetch origin
git reset --hard origin/main  # 强制使用远程代码覆盖本地
git clean -fd  # 清理未跟踪的文件和目录
echo "已强制更新到最新代码"

# 4. 退出虚拟环境
deactivate
echo "已退出虚拟环境"

# 5. 设置权限
echo "正在设置目录权限..."
sudo chown -R trader:trader /home/options_trading
sudo chmod -R 755 /home/options_trading

# 6. 设置敏感文件权限
if [ -f .env ]; then
    sudo chmod 600 .env
    echo "已设置 .env 权限"
fi

if [ -f config/config.py ]; then
    sudo chmod 600 config/config.py
    echo "已设置 config.py 权限"
fi

echo "更新完成！" 