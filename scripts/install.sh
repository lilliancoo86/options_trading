#!/bin/bash

# 检查是否以 root 权限运行
if [ "$EUID" -ne 0 ]; then 
    echo "请使用 sudo 运行此脚本"
    exit 1
fi

# 设置工作目录
cd /home/options_trading || exit 1

# 激活虚拟环境并更新代码
source venv/bin/activate
git pull
deactivate

# 创建用户和组
useradd -r -s /bin/false trader 2>/dev/null || true
groupadd -f trader 2>/dev/null || true

# 复制配置文件（如果不存在）
if [ ! -f config/config.py ]; then
    cp config/config.example.py config/config.py
fi

# 设置目录权限
chown -R trader:trader /home/options_trading
chmod -R 755 /home/options_trading

# 设置敏感文件权限
chown trader:trader config/config.py
if [ -f .env ]; then
    chmod 600 .env
fi
chmod 600 config/config.py

# 安装和配置系统服务
cp scripts/doomsday_option.service /etc/systemd/system/
chmod 644 /etc/systemd/system/doomsday_option.service

# 重新加载和重启服务
systemctl daemon-reload
systemctl restart doomsday_option
systemctl enable doomsday_option

echo "安装完成！"