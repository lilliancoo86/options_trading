# doomsday_option，使用长桥OpenAPI接口。

```bash
## 相关文档

- 长桥开发文档：https://open.longportapp.com/docs
- SDK文档：https://github.com/longportapp/openapi-sdk/tree/release-v2

## 系统架构
```bash
option_trading/
├── config/
│   └── config.example.py    # 配置示例
├── database/
│   └── option_trading.sql   # 数据库结构
│   └── reset_database.sql   # 数据库重置
├── scripts/
│   └── doomsday_option.service   # 系统服务
│   └── doomsday_option_logrotate # 日志轮转配置
│   ├── install.sh          # 安装脚本
│   └── monitor.sh          # 监控脚本
│   └── main.py             # 主程序入口
├── trading/
│   ├── option_strategy.py    # 期权策略实现
│   ├── position_manager.py   # 持仓管理
│   ├── risk_checker.py       # 风险检查
│   └── time_checker.py       # 时间检查
├── .env.example              # 环境变量示例
├── .gitignore                # Git忽略文件
└── requirements.txt          # 依赖管理
└── README.md                 # 说明文档
```

## 安装部署
```bash

# 下载安装脚本
这些命令应该在 root 用户下运行，目录位置不重要，因为这些是系统级命令。让我们按顺序执行：
# 1. 切换到 root 用户（如果还不是）
sudo su -

# 5. 然后再切换到项目目录运行设置脚本
cd /home/options_trading
./scripts/install.sh

wget https://raw.githubusercontent.com/lilliancoo86/options_trading/main/scripts/install.sh

# 添加执行权限
chmod +x install.sh

# 执行安装
sudo ./install.sh
```
### 1. 系统环境配置
```bash
CentOS7、Python3.7、MySQL5.7：这是一个稳定且广泛使用的版本，完全兼容。

#### CentOS 7 基础环境配置
```bash
> ⚠️ **特别注意**: 由于CentOS 7官方源已停止维护，需要先更换为阿里云镜像源

1. 更换阿里云镜像源
```bash
# 备份原有repo文件
sudo mv /etc/yum.repos.d/CentOS-Base.repo /etc/yum.repos.d/CentOS-Base.repo.backup

# 下载阿里云的repo文件
sudo curl -o /etc/yum.repos.d/CentOS-Base.repo https://mirrors.aliyun.com/repo/Centos-7.repo

# 如果curl未安装，先安装curl
sudo yum install curl -y

# 清除缓存并更新
sudo yum clean all
sudo yum makecache
sudo yum update -y
```
> 🔔 **提示**: 如果遇到无法访问镜像源问题
如果无法访问镜像源，可以手动配置DNS：
```bash
# 编辑网络配置
sudo vi /etc/resolv.conf

# 添加公共DNS
nameserver 8.8.8.8
nameserver 8.8.4.4
```


2. 安装Python环境
```bash
# 安装开发工具和依赖
sudo yum groupinstall "Development Tools" -y
sudo yum install openssl-devel bzip2-devel libffi-devel -y

# 下载并安装Python 3.7
cd /usr/src
sudo wget https://www.python.org/ftp/python/3.7.9/Python-3.7.9.tgz
sudo tar xzf Python-3.7.9.tgz
cd Python-3.7.9
sudo ./configure --enable-optimizations
sudo make altinstall

# 创建软链接
sudo ln -sf /usr/local/bin/python3.7 /usr/bin/python3
sudo ln -sf /usr/local/bin/pip3.7 /usr/bin/pip3
```
-----------------------------------------------------------------------------
```bash
在 CentOS 7 上，我们需要手动编译安装 TA-Lib：
# 1. 首先安装编译工具（如果还没安装的话）
sudo yum install -y wget

# 2. 下载并安装 TA-Lib
cd /tmp
wget http://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz
tar -xvf ta-lib-0.4.0-src.tar.gz
cd ta-lib/
./configure --prefix=/usr
make
sudo make install

# 这里的 --prefix=/usr 表示安装到系统目录
# 配置动态链接库
sudo sh -c "echo '/usr/local/lib' >> /etc/ld.so.conf"
sudo ldconfig

# 回到项目目录
cd /home/options_trading

# 确保虚拟环境已激活
source venv/bin/activate

# 升级 pip（建议的操作）
python -m pip install --upgrade pip

# 确保系统环境干净：
pip uninstall ta-lib -y

# 现在重新安装依赖
pip install -r requirements.txt
# 更新依赖
pip install -r requirements.txt --no-cache-dir

说明：
虚拟环境 venv 是独立于代码的，不需要重新创建
只有在以下情况才需要重新创建虚拟环境：
虚拟环境损坏
Python 版本需要更改
虚拟环境目录被意外删除
如果有新的依赖包更新，直接在已激活的虚拟环境中运行 pip install 即可
git pull 不会影响 venv 目录，因为它在 .gitignore 中
这样可以保持环境的连续性，避免不必要的重复工作。

虚拟环境与项目代码在同一目录下，便于管理
.gitignore 已经配置了忽略 venv/ 目录
服务配置文件 doomsday_option.service 中的路径配置也是基于这个结构
便于其他开发者理解和维护项目
注意事项：
确保 venv 目录在 .gitignore 中
不要把虚拟环境提交到 git 仓库
每次重新打开终端都需要重新激活虚拟环境
如果要退出虚拟环境，使用 deactivate 命令

### 3. 数据库配置

> ⚠️ **特别注意**: 请确保数据库密码安全，建议使用随机生成的强密码

MySQL 8.0：这是MySQL的最新主要版本，提供了许多新特性和性能改进。它也与CentOS 7和Python 3.7.9兼容。

```bash
# 安装MySQL

更新系统包：
sudo yum update

安装MySQL 5.7：

检查 MySQL 服务状态：

sudo systemctl status mysqld

如果仍然遇到问题，可能需要重新安装 MySQL。首先，移除现有的包：

sudo yum remove mysql-community-server
sudo yum remove mysql57-community-release

下载并添加MySQL 5.7的YUM源：然后重新下载并安装 MySQL 仓库：

wget https://dev.mysql.com/get/mysql57-community-release-el7-11.noarch.rpm
sudo rpm -ivh mysql57-community-release-el7-11.noarch.rpm
sudo yum install mysql-community-server

安装完成后，再次尝试启动服务：

sudo systemctl start mysqld
sudo systemctl enable mysqld
sudo systemctl status mysqld

查找MySQL的错误日志：这应该会显示一行包含临时密码的信息。
sudo grep 'temporary password' /var/log/mysqld.log

使用这个临时密码登录 MySQL：
mysql -u root -p

登录后，你需要立即更改 root 密码。在 MySQL 提示符下执行：

ALTER USER 'root'@'localhost' IDENTIFIED BY 'P@ssws-1)a0rd#2023!QB8c';

果命令执行成功，你会看到类似 "Query OK" 的消息。

请将 'NewPassword123!' 替换为一个强密码。注意，MySQL 5.7 默认有较严格的密码策略。MySQL 5.7 的默认密码策略通常要求：

至少 8 个字符长
包含大写字母
包含小写字母
包含数字
包含特殊字符

退出 MySQL，简单地输入：

\q

请记住，在 MySQL 中，每个 SQL 语句都应该以分号 (;) 结束。"EXIT" 或 "\q" 是用来退出 MySQL 客户端的命令，不是 SQL 语句，所以不需要分号。

运行MySQL安全安装脚本：
sudo mysql_secure_installation

登录到MySQL：

mysql -u root -p

输入你在安全安装脚本中设置的新密码。

如果没有找到临时密码，或者这个方法不起作用，我们可以尝试重置 root 密码：

a. 停止 MySQL 服务：

sudo systemctl stop mysqld

创建一个新的 MySQL 配置文件来跳过授权表：

sudo bash -c 'echo "[mysqld]" > /etc/my.cnf.d/mysql-init.cnf'
sudo bash -c 'echo "skip-grant-tables" >> /etc/my.cnf.d/mysql-init.cnf'

启动 MySQL 服务：

sudo systemctl start mysqld

mysql -u root -p

在 MySQL 提示符下，执行以下命令来重置 root 密码：

FLUSH PRIVILEGES;
ALTER USER 'root'@'localhost' IDENTIFIED BY 'Mphacso4Q~s-1)a';
FLUSH PRIVILEGES;
EXIT;

删除我们创建的配置文件：

sudo rm /etc/my.cnf.d/mysql-init.cnf

重启 MySQL 服务：

sudo systemctl restart mysqld

现在尝试使用新密码登录：输入新设置的密码 'Mphacso4Q~s-1)a'

如果成功登录，退出 MySQL 并再次运行 mysql_secure_installation：

sudo mysql_secure_installation

如果这些步骤仍然不起作用，可能需要考虑重新安装 MySQL.

-----------------------------------------------------------------------------

源 "MySQL 5.7 Community Server" 的 GPG 密钥已安装，但是不适用于此软件包。请检查源的公钥 URL 是否配置正确。

 失败的软件包是：mysql-community-libs-5.7.44-1.el7.x86_64
这个错误通常是由于 GPG 密钥问题导致的。为了解决这个问题，你可以尝试以下步骤：

首先，导入 MySQL 的 GPG 密钥：
sudo rpm --import https://repo.mysql.com/RPM-GPG-KEY-mysql-2022

如果上述命令不能解决问题，你可以尝试手动下载并导入密钥：

wget https://repo.mysql.com/RPM-GPG-KEY-mysql-2022
sudo rpm --import RPM-GPG-KEY-mysql-2022

清理 YUM 缓存并重新生成：

sudo yum clean all
sudo yum makecache

-----------------------------------------------------------------------------

确保 MySQL 配置文件 (通常是 /etc/my.cnf) 包含以下设置:

[mysqld]
character-set-server = utf8mb4
collation-server = utf8mb4_unicode_ci

重启 MySQL 服务:

sudo systemctl restart mysqld

-----------------------------------------------------------------------------
# 初始化数据库
要查看当前 MySQL 的数据目录位置，可以执行：

mysql -u option_trading -p -e "SHOW VARIABLES LIKE 'datadir';"


首先，确保 option_trading 用户已创建，并且有足够的权限来执行导入操作。

使用 root 用户登录：

mysql -u root -p

执行授权命令：

-- 使用数据库
USE option_trading;

-- 删除已存在的用户（如果需要）
DROP USER IF EXISTS 'option_trading'@'localhost';

确保 SQL 文件存在且可读：

ls -l /home/options_trading/database/option_trading.sql

然后以 root 用户执行数据库初始化脚本：
# 以 root 用户执行 option_trading.sql
mysql -u root -p < /home/options_trading/database/option_trading.sql

-- 验证权限
SHOW GRANTS FOR 'option_trading'@'localhost';

如果导入成功，你应该能看到一系列的 SQL 语句执行成功的消息。

导入完成后，你可以检查数据库中的表：

------------------------
验证结果：

-- 检查数据库
SHOW DATABASES;

+--------------------+
| Database           |
+--------------------+
| information_schema |
| mysql              |
| option_trading     |
| performance_schema |
| sys                |
+--------------------+
5 rows in set (0.00 sec)

-- 使用数据库
USE option_trading;

-- 检查表
SHOW TABLES;

+--------------------------+
| Tables_in_option_trading |
+--------------------------+
| daily_stats              |
| market_data              |
| option_metrics           |
| option_trades            |
| options                  |
| order_status             |
| position_records         |
| risk_events              |
| signals                  |
| system_status            |
+--------------------------+
10 rows in set (0.00 sec)


-- 检查用户权限
SHOW GRANTS FOR 'option_trading'@'localhost';

+----------------------------------------------------------------------------+
| Grants for option_trading@localhost                                        |
+----------------------------------------------------------------------------+
| GRANT USAGE ON *.* TO 'option_trading'@'localhost'                         |
| GRANT ALL PRIVILEGES ON `option_trading`.* TO 'option_trading'@'localhost' |
+----------------------------------------------------------------------------+
2 rows in set (0.00 sec)


-- 检查初始化数据
SELECT * FROM system_status;

+----+------------------+---------+---------------------+-------------+-------------------------------------------------+---------------------+---------------------+
| id | component        | status  | last_heartbeat      | error_count | details                                         | created_at          | updated_at          |
+----+------------------+---------+---------------------+-------------+-------------------------------------------------+---------------------+---------------------+
| 16 | data_fetcher     | STOPPED | 2025-01-12 19:05:28 |           0 | {"status": "初始化", "last_update": null}       | 2025-01-12 19:05:28 | 2025-01-12 19:05:28 |
| 17 | trade_executor   | STOPPED | 2025-01-12 19:05:28 |           0 | {"status": "初始化", "orders_processed": 0}     | 2025-01-12 19:05:28 | 2025-01-12 19:05:28 |
| 18 | risk_checker     | STOPPED | 2025-01-12 19:05:28 |           0 | {"status": "初始化", "checks_performed": 0}     | 2025-01-12 19:05:28 | 2025-01-12 19:05:28 |
| 19 | position_manager | STOPPED | 2025-01-12 19:05:28 |           0 | {"status": "初始化", "active_positions": 0}     | 2025-01-12 19:05:28 | 2025-01-12 19:05:28 |
| 20 | option_strategy  | STOPPED | 2025-01-12 19:05:28 |           0 | {"status": "初始化", "signals_generated": 0}    | 2025-01-12 19:05:28 | 2025-01-12 19:05:28 |
+----+------------------+---------+---------------------+-------------+-------------------------------------------------+---------------------+---------------------+

请注意，每条语句都需要单独执行，并以分号 (;) 结束。

或者简单地输入：

\q

这些命令应该在终端（shell）中执行，而不是在 MySQL 命令行中。让我们纠正操作步骤：
首先退出 MySQL 命令行（如果你在其中）：

option_trading.sql 是初始化数据库结构和基础数据的脚本
reset_database.sql 是用于清理和重置数据的维护脚本
reset_database.sql 在以下情况下很有用：
需要清理测试数据
需要重置系统状态
数据出现异常需要重置
版本升级时需要清理旧数据


首先创建备份（以防万一）：
# 备份当前数据库
mysqldump -u option_trading -p option_trading > option_trading_backup_$(date +%Y%m%d).sql

执行重置脚本：

# 方法1：直接执行SQL文件
mysql -u option_trading -p option_trading < /home/options_trading/database/reset_database.sql

# 登录 MySQL
mysql -u option_trading -p option_trading

kWevwjjk6*u4k

-- 检查表结构
SHOW TABLES;

-- 检查系统状态表的初始记录
SELECT * FROM system_status;

-- 检查触发器是否正确创建
SHOW TRIGGERS;

如果遇到权限问题，可以使用 root 用户：
# 使用 root 用户执行
sudo mysql -u root -p option_trading < /home/options_trading/scripts/sql/reset_database.sql

如果出现问题可以恢复备份：

# 恢复备份
mysql -u option_trading -p option_trading < option_trading_backup_20250112.sql

如果你修改了 SQL 文件，记得重新导入：

SOURCE /home/options_trading/database/option_trading.sql;

---------------
重启MySQL服务以应用更改：
sudo systemctl restart mysqld

使用 systemctl 命令（推荐）：
sudo systemctl status mysqld

检查 MySQL 是否在监听端口：
sudo netstat -tlnp | grep mysql

查看 MySQL 错误日志：
sudo tail -f /var/log/mysqld.log

检查 MySQL 进程：
ps aux | grep mysql

尝试连接到 MySQL：
mysql -u option_trading -p

检查系统日志中与 MySQL 相关的消息：
sudo journalctl -u mysqld

Please use --connect-expired-password option or invoke mysql in interactive mode.
这个消息通常出现在 MySQL 5.7 或更高版本中，当用户的密码已过期时。这是一个安全特性，要求用户在首次登录时更改密码。要解决这个问题，你可以采取以下步骤：

mysql -u root -p --connect-expired-password

一旦连接上，你需要立即更改密码。在 MySQL 提示符下执行以下命令：

ALTER USER 'root'@'localhost' IDENTIFIED BY 'kWevwjjk6*u4k'

将 'new_password' 替换为你想要设置的新密码。

停止并重新启动 MySQL 服务：

sudo killall mysqld
sudo systemctl start mysqld

完成后，尝试使用新密码重新连接：

mysql -u option_trading -p

验证 MySQL 用户表： 如果你能以其他方式登录 MySQL（例如使用 sudo mysql），运行以下查询：

SELECT user, host, plugin FROM mysql.user WHERE user='option_trading'

根据你提供的 SQL 代码，我发现了问题所在。错误出现在创建 option_trades 表时，因为它引用了 position_records 表，但 position_records 表还没有被创建。

要解决这个问题，我们需要调整表的创建顺序。以下是修改后的正确顺序：

创建 options 表
创建 position_records 表
创建 option_trades 表
创建其他表

要使用这个文件:

将这些SQL语句保存到一个新文件,例如 updated_option_trading.sql。

如果你想从头开始,可以先删除现有的数据库:

DROP DATABASE IF EXISTS option_trading;

然后,使用以下命令执行SQL文件:

mysql -u root -p < /path/to/updated_option_trading.sql

-----------------------------------------------------------------------------
```bash

如何卸载mysql5.7

停止 MySQL 服务：

sudo systemctl stop mysqld

检查是否有 MySQL 包安装：

rpm -qa | grep mysql

卸载所有 MySQL 相关的包：这将删除所有以 "mysql-" 开头的包。

sudo yum remove mysql-*

删除 MySQL 的数据目录（注意：这将删除所有数据库数据）：

sudo rm -rf /var/lib/mysql

删除 MySQL 的配置文件：

sudo rm -f /etc/my.cnf
sudo rm -f /etc/my.cnf.d/*

删除日志文件：

sudo rm -f /var/log/mysqld.log

删除系统用户和组（如果存在）：

sudo userdel mysql
sudo groupdel mysql

删除 MySQL 仓库：

sudo rm -f /etc/yum.repos.d/mysql-community*
sudo rm -f /etc/yum.repos.d/mysql57-community*

清理 YUM 缓存：

sudo yum clean all

检查是否还有遗留的 MySQL 文件或目录：如果找到任何文件或目录，可以手动删除它们。

sudo find / -name mysql -type d

sudo find / -name mysqld -type f

重新加载系统服务：

sudo systemctl daemon-reload

如果你安装了 MySQL Workbench 或其他 MySQL 工具，也需要卸载它们：

sudo yum remove mysql-workbench*

注意事项：

在执行这些步骤之前，请确保你已经备份了所有重要的数据库数据。
删除数据目录（/var/lib/mysql）将永久删除所有数据库数据，请确保你真的想这么做。
如果你计划重新安装 MySQL，可以保留配置文件（/etc/my.cnf）以备future使用。
在生产环境中执行这些操作时要格外小心。
如果你使用的是其他 Linux 发行版，包管理器和一些路径可能会有所不同。
完成这些步骤后，MySQL 5.7 应该已经从你的系统中完全移除了。如果你计划安装新版本的 MySQL 或其他数据库系统，现在可以进行了。


## 系统配置

### 2. 项目部署

> ⚠️ **特别注意**: 项目默认部署在 `/home/options_trading` 目录下

```bash
# 1. 直接进入 /home 目录
cd /home

# 2. 克隆项目，会自动创建 options_trading 目录
git clone https://github.com/lilliancoo86/options_trading.git

# 3. 设置目录权限
sudo chown -R $(whoami):$(whoami) options_trading

# 4. 进入项目目录
cd /home/options_trading

# 5. 创建并激活虚拟环境
python3 -m venv venv
source venv/bin/activate

# 6. 安装依赖
pip install -r requirements.txt

不需要重新创建虚拟环境。更新代码时的正确步骤是：
# 1. 进入项目目录
cd /home/options_trading

# 2. 激活已有的虚拟环境
source venv/bin/activate

# 3. 拉取最新代码
git pull

# 4. 如果 requirements.txt 有更新，则更新依赖
pip install -r requirements.txt --no-cache-dir

退出虚拟环境

deactivate

然后初始化数据库（使用 root 用户）：

# 然后执行数据库初始化，查看相关部分操作

#最后启动系统服务：查看相关部分操作

-------------------------------------------------------

### 1. 环境变量配置

> ⚠️ **特别注意**: 
#  必须手动填写Longport API配置
cd /home/options_trading

创建 trader 用户和组：
# 创建 trader 用户和组
sudo useradd -r -s /bin/false trader
sudo groupadd -f trader

# 设置项目目录权限
sudo chown -R trader:trader /home/options_trading
sudo chmod -R 755 /home/options_trading

# 复制配置文件
cp .env.example .env
cp config/config.example.py config/config.py

sudo chown trader:trader config/config.py

sudo chmod 600 .env
sudo chmod 600 config/config.py

# 创建日志文件
sudo mkdir -p /home/options_trading/logs
sudo touch logs/doomsday.log
sudo touch logs/doomsday.error.log
sudo touch logs/trading.log
------------------
# 设置日志权限
sudo chown -R trader:trader logs/
sudo chmod 755 logs/
sudo chmod 644 logs/*.log

# 确保虚拟环境权限正确
sudo chown -R trader:trader venv/
sudo chmod -R 755 venv/

# 编辑环境文件
sudo vim /home/options_trading/.env

cd /home/options_trading
sudo systemctl stop doomsday_option

# 复制服务文件
sudo cp scripts/doomsday_option.service /etc/systemd/system/
sudo chmod 644 /etc/systemd/system/doomsday_option.service

# 重新加载服务
sudo systemctl daemon-reload
sudo systemctl restart doomsday_option
sudo systemctl enable doomsday_option
sudo journalctl -u doomsday_option -f

正常模式：
python -m scripts.main

测试模式：
python -m scripts.main --test

测试模式（指定模拟时间）：
python -m scripts.main --test --fake-time "2025-01-15 10:30:00"

# 编辑 crontab
创建系统级的 cron 任务：
# 创建新的 cron 文件
sudo tee /etc/cron.d/doomsday_option << EOF
25 9 * * 1-5 root systemctl start doomsday_option
05 16 * * 1-5 root systemctl stop doomsday_option
EOF

# 设置正确的权限
sudo chmod 644 /etc/cron.d/doomsday_option

# 添加以下内容（每个交易日的开盘前和收盘后）
完成后，可以使用以下命令验证：
# 查看当前 crontab
crontab -l

# 或者查看系统 cron 任务
ls -l /etc/cron.d/
cat /etc/cron.d/doomsday_option

非交易时间直接退出，不记录任何日志
只在交易时间内运行和记录日志
3. 通过 crontab 管理服务的启动和停止
避免了重复的日志输出
--------------------------------------------

# 检查目录结构
tree -L 2 /home/options_trading

# 检查关键文件权限
ls -la /home/options_trading/
ls -la /home/options_trading/logs/
ls -la /home/options_trading/config/
ls -la /home/options_trading/venv/bin/

# 查看服务状态
sudo systemctl status doomsday_option

# 查看日志
sudo tail -f /home/options_trading/logs/doomsday.log
sudo tail -f /home/options_trading/logs/doomsday.error.log

查看完整的系统#systemd日志：

sudo journalctl -u doomsday_option.service -n 50 --no-pager

# 检查应用日志
sudo tail -f /home/options_trading/logs/doomsday.log

检查服务配置文件：

sudo cat /etc/systemd/system/doomsday_option.service



#手动运行主程序测试：
# 确保在虚拟环境中
source venv/bin/activate

# 运行主程序
python -m scripts.main


让我们检查和设置 config 目录的权限：
# 1. 检查当前权限
ls -la /home/options_trading/config/

# 2. 设置目录权限
sudo chown -R trader:trader /home/options_trading/config
sudo chmod 755 /home/options_trading/config

# 3. 设置配置文件权限
sudo chown trader:trader /home/options_trading/config/config.py
sudo chown trader:trader /home/options_trading/config/config.example.py
sudo chmod 600 /home/options_trading/config/config.py
sudo chmod 644 /home/options_trading/config/config.example.py

# 4. 确保 __init__.py 存在并设置权限
sudo touch /home/options_trading/config/__init__.py
sudo chown trader:trader /home/options_trading/config/__init__.py
sudo chmod 644 /home/options_trading/config/__init__.py

# 5. 验证权限
ls -la /home/options_trading/config/


权限说明：
config 目录：755（trader 可读写执行，其他用户可读执行）
config.py：600（只有 trader 可读写）
config.example.py：644（trader 可读写，其他用户可读）
init_.py：644（trader 可读写，其他用户可读）

-------------------------------------------------------
WARNING: You are using pip version 20.1.1; however, version 24.0 is available.
You should consider upgrading via the '/usr/local/bin/python3.7 -m pip install --upgrade pip' command.

是的，让我们先升级 pip，然后再安装依赖：最好使用虚拟环境。让我们按照最佳实践来操作：

# 1. 进入项目目录
cd /home/options_trading

# 2. 激活虚拟环境
source venv/bin/activate

# 卸载当前版本
pip uninstall urllib3 -y

# 安装兼容版本
pip install 'urllib3<2.0.0'

# 3. 在虚拟环境中升级 pip
pip install --upgrade pip

# 4. 重新安装依赖
pip install -r requirements.txt --no-cache-dir

# 5. 确认安装成功
pip list | grep pytz

# 6. 退出虚拟环境
deactivate

----------------------------------
# 确保在虚拟环境中
source venv/bin/activate

# 运行主程序
python -m scripts.main

---------------------------------
# 7. 确保目录权限正确
sudo chown -R trader:trader /home/options_trading
sudo chmod -R 755 /home/options_trading
sudo chmod 600 /home/options_trading/.env


# 设置权限
sudo chown trader:trader .env
sudo chmod 600 .env

# 确保在虚拟环境中
source venv/bin/activate

# 运行程序
python -m scripts.main


让我们检查并设置日志文件和目录：
创建并设置日志目录和文件：
# 创建日志目录（如果不存在）
sudo mkdir -p /home/options_trading/logs

# 创建日志文件
sudo touch /home/options_trading/logs/doomsday.log
sudo touch /home/options_trading/logs/doomsday.error.log
sudo touch /home/options_trading/logs/trading.log

# 设置所有权
sudo chown -R trader:trader /home/options_trading/logs

# 设置目录权限
sudo chmod 755 /home/options_trading/logs

# 设置日志文件权限
sudo chmod 644 /home/options_trading/logs/doomsday.log
sudo chmod 644 /home/options_trading/logs/doomsday.error.log
sudo chmod 644 /home/options_trading/logs/trading.log
# 检查目录结构和权限
ls -la /home/options_trading/logs/

# 测试日志写入
sudo -u trader bash -c 'echo "Test log entry" >> /home/options_trading/logs/doomsday.log'

# 重启服务
sudo systemctl restart doomsday_option

# 检查状态
sudo systemctl status doomsday_option

# 查看日志
sudo tail -f /home/options_trading/logs/doomsday.log
sudo tail -f /home/options_trading/logs/doomsday.error.log

----------------------------------
# 复制服务文件
sudo cp scripts/doomsday_option.service /etc/systemd/system/

# 设置权限
sudo chmod 644 /etc/systemd/system/doomsday_option.service

# 8. 重启服务
sudo systemctl daemon-reload
sudo systemctl restart doomsday_option
sudo systemctl status doomsday_option
sudo journalctl -u doomsday_option -f


运行测试：
python -m scripts.test_risk_management

---------------------------------

环境设置完成
请重新登录 trader 用户以使设置生效：
1. 退出当前会话: exit
2. 重新登录: su - trader
3. 验证环境: systemctl --user status

# 重新登录
su - trader
# 验证
systemctl --user status
echo $XDG_RUNTIME_DIR

如果还有问题，可以尝试重启系统：
# 重启系统
reboot
# 重启后登录
su - trader
# 验证
systemctl --user status

------------------------------------

检查时区设置：
# 检查系统时区
date
timedatectl

# 设置系统时区（如果需要）
sudo timedatectl set-timezone America/New_York
------------------------------------------------------
放弃本地修改（如果确定本地修改不需要）：
# 1. 放弃本地修改
git checkout -- README.md scripts/doomsday_option.service

# 2. 拉取远程更新
git pull

# 3. 重新应用服务配置
sudo cp scripts/doomsday_option.service /etc/systemd/system/
# 2. 设置权限
sudo chmod 644 /etc/systemd/system/doomsday_option.service
# 重启服务
sudo systemctl daemon-reload
sudo systemctl restart doomsday_option

# 3. 检查状态
sudo systemctl status doomsday_option
sudo systemctl list-timers --all

# 查看系统日志
sudo journalctl -u doomsday_option -f

# 查看应用日志
sudo tail -f /home/options_trading/logs/trading.log
sudo tail -f /home/options_trading/logs/doomsday.log
sudo tail -f /home/options_trading/logs/doomsday.error.log

# 检查进程
ps aux | grep doomsday_option

# 检查 Python 进程
ps aux | grep python

# 检查日志文件
ls -la /home/options_trading/logs/


要终止服务，可以使用以下命令：
# 停止服务
sudo systemctl stop doomsday_option

# 检查服务状态
sudo systemctl status doomsday_option

# 如果要禁用开机自启
sudo systemctl disable doomsday_option

# 如果要完全移除服务
sudo rm /etc/systemd/system/doomsday_option.service
sudo systemctl daemon-reload
sudo systemctl reset-failed


-------------------------------------------------------

### 2. 数据库维护

> ⚠️ **特别注意**: 定期清理过期数据，避免数据库占用过大

```bash
# 优化数据库表
mysql -u option_trading -p option_trading -e "OPTIMIZE TABLE options, option_trades, position_records;"

# 清理30天前的数据
mysql -u option_trading -p option_trading -e "DELETE FROM market_data WHERE data_time < DATE_SUB(NOW(), INTERVAL 30 DAY);"
```

### 3. 监控检查
```bash
# 检查系统资源
free -h
df -h
top

# 检查日志
tail -f logs/trading.log
```

## 故障排除


### 2. 常见问题处理

- **服务无法启动**
  - 检查日志: `journalctl -u doomsday_option -n 100`
  - 验证配置: `python3 scripts/verify_config.py`
  - 检查权限: `ls -l /home/options_trading`

- **数据库连接失败**
  - 检查服务: `systemctl status mysqld`
  - 验证账户: `mysql -u option_trading -p -e "SELECT 1;"`
  - 检查配置: `cat .env | grep DB_`

## 注意事项

### 1. 安全建议
- 禁用root SSH登录
- 使用密钥认证
- 定期更新系统和依赖包
- 定期备份数据库

### 2. 性能优化
- 定期清理日志文件
- 优化数据库查询
- 监控系统资源使用

### 3. 特殊说明
> ⚠️ **重要提示**:
> 1. 系统使用美东时区，请确保服务器时间正确同步
> 2. 交易时段内请勿重启服务或更新代码
> 3. 必须手动配置Longport API密钥
> 4. 定期检查数据库备份
> 5. 关注长桥API的版本更新

## 版本信息

- 版本号: v1.0.0
- 更新日期: 2024-01-20
- 支持的Python版本: 3.7.9
- 支持的操作系统: CentOS 7

## 许可证

MIT License
