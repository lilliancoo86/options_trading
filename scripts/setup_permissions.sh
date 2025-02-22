#!/bin/bash

# 使用脚本所在目录来确定项目根目录
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"

# 验证项目目录
if [[ ! "$PROJECT_ROOT" =~ /options_trading$ ]]; then
    echo "错误: 当前目录不是 options_trading"
    echo "当前目录: $PROJECT_ROOT"
    exit 1
fi

echo "正在设置项目权限..."
echo "项目根目录: $PROJECT_ROOT"

# 验证trader用户存在
if ! id "trader" >/dev/null 2>&1; then
    echo "错误: trader用户不存在"
    echo "请先创建trader用户:"
    echo "sudo useradd -m trader"
    echo "sudo usermod -aG trader \$USER"
    exit 1
fi

# 创建必要的目录（如果不存在）
echo "创建必要的目录结构..."
sudo -u trader mkdir -p "$PROJECT_ROOT"/{data,logs,config}
sudo -u trader mkdir -p "$PROJECT_ROOT"/data/{market_data,options_data,historical,backup,risk_records}

# 设置目录所有权
echo "设置目录所有权..."
sudo chown -R trader:trader "$PROJECT_ROOT"

# 设置目录权限
echo "设置目录权限..."
sudo chmod 755 "$PROJECT_ROOT"
sudo chmod 755 "$PROJECT_ROOT"/{data,logs,config,scripts,trading}
sudo chmod 755 "$PROJECT_ROOT"/data/{market_data,options_data,historical,backup,risk_records}

# 设置敏感文件权限
echo "设置敏感文件权限..."
if [ -f "$PROJECT_ROOT/.env" ]; then
    # 如果.env文件存在，只更新权限
    sudo chown trader:trader "$PROJECT_ROOT/.env"
    sudo chmod 600 "$PROJECT_ROOT/.env"
    echo "已设置 .env 文件权限"
else
    # 如果.env不存在，从.env.example复制
    echo "从 .env.example 创建 .env 文件..."
    if [ -f "$PROJECT_ROOT/.env.example" ]; then
        sudo -u trader cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env"
        sudo chmod 600 "$PROJECT_ROOT/.env"
        echo "已从 .env.example 创建 .env 文件，请根据需要修改配置"
    else
        echo "错误: .env.example 文件不存在，无法创建 .env"
        exit 1
    fi
fi

if [ -f "$PROJECT_ROOT/config/config.py" ]; then
    sudo chown trader:trader "$PROJECT_ROOT/config/config.py"
    sudo chmod 600 "$PROJECT_ROOT/config/config.py"
    echo "已设置 config.py 文件权限"
fi

if [ -f "$PROJECT_ROOT/data/backup_status.json" ]; then
    sudo chown trader:trader "$PROJECT_ROOT/data/backup_status.json"
    sudo chmod 600 "$PROJECT_ROOT/data/backup_status.json"
    echo "已设置 backup_status.json 文件权限"
fi

# 设置数据文件权限
echo "设置数据文件权限..."
for dir in market_data options_data historical backup risk_records; do
    if [ -d "$PROJECT_ROOT/data/$dir" ]; then
        sudo find "$PROJECT_ROOT/data/$dir" -type f -exec chown trader:trader {} \;
        sudo find "$PROJECT_ROOT/data/$dir" -type f -exec chmod 644 {} \;
    fi
done

# 设置日志文件权限
echo "设置日志文件权限..."
if [ -d "$PROJECT_ROOT/logs" ]; then
    sudo find "$PROJECT_ROOT/logs" -type f -exec chown trader:trader {} \;
    sudo find "$PROJECT_ROOT/logs" -type f -exec chmod 644 {} \;
fi

# 设置Python脚本权限
echo "设置Python脚本权限..."
for dir in scripts trading; do
    if [ -d "$PROJECT_ROOT/$dir" ]; then
        sudo find "$PROJECT_ROOT/$dir" -name "*.py" -type f -exec chown trader:trader {} \;
        sudo find "$PROJECT_ROOT/$dir" -name "*.py" -type f -exec chmod 755 {} \;
    fi
done

# 验证权限设置
echo "验证权限设置..."
ls -la "$PROJECT_ROOT"
ls -la "$PROJECT_ROOT/data"
ls -la "$PROJECT_ROOT/config"
ls -la "$PROJECT_ROOT/scripts"
ls -la "$PROJECT_ROOT/trading"

echo "权限设置完成!"

# 检查关键文件
echo "检查关键文件..."
if [ ! -f "$PROJECT_ROOT/.env" ]; then
    echo "警告: .env 文件已创建，请参考 .env.example 配置环境变量"
fi

if [ ! -f "$PROJECT_ROOT/config/config.py" ]; then
    echo "警告: config.py 文件不存在，请确保配置文件存在"
fi

# 显示使用说明
echo "
使用说明:
1. 确保 .env 文件已正确配置（owner: trader, 权限: 600）
2. 确保 config.py 已根据需求修改（owner: trader, 权限: 600）
3. 确保数据目录具有正确的写入权限（owner: trader, 权限: 755）
4. 建议定期运行此脚本检查权限设置

当前权限状态:
$(ls -l "$PROJECT_ROOT/.env" 2>/dev/null)
$(ls -l "$PROJECT_ROOT/config/config.py" 2>/dev/null)

当前用户和组信息:
$(id trader)

如需手动设置权限，可以使用以下命令:
sudo chown -R trader:trader $PROJECT_ROOT
sudo chmod 600 $PROJECT_ROOT/.env
sudo chmod 600 $PROJECT_ROOT/config/config.py
"

# 如果当前用户不是trader，提供切换用户的提示
if [ "$USER" != "trader" ]; then
    echo "
注意: 当前用户($USER)不是trader
建议使用以下命令切换到trader用户:
su - trader
"
fi 