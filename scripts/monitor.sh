#!/bin/bash

# 设置基础路径
BASE_DIR="/home/options_trading"
LOG_DIR="$BASE_DIR/logs"

# 检查服务状态
check_service() {
    if ! supervisorctl status option_trading | grep -q "RUNNING"; then
        echo "[ERROR] Option Trading Service is not running!"
        supervisorctl restart option_trading
        echo "[INFO] Service restarted."
    fi
}

# 检查日志错误
check_logs() {
    ERROR_COUNT=$(grep -c "ERROR" "$LOG_DIR/trading.log")
    if [ $ERROR_COUNT -gt 0 ]; then
        echo "[WARNING] Found $ERROR_COUNT errors in log file"
        tail -n 10 "$LOG_DIR/trading.log" | grep "ERROR"
    fi
}

# 检查内存使用
check_memory() {
    MEM_USAGE=$(free | awk '/Mem/{printf("%.2f"), $3/$2*100}')
    if [ $(echo "$MEM_USAGE > 80" | bc) -eq 1 ]; then
        echo "[WARNING] Memory usage is at ${MEM_USAGE}%"
    fi
}

# 检查磁盘空间
check_disk_space() {
    DISK_USAGE=$(df -h / | awk 'NR==2 {print $5}' | sed 's/%//')
    if [ $DISK_USAGE -gt 80 ]; then
        echo "[WARNING] Disk usage is at ${DISK_USAGE}%"
    fi
}

# 检查数据库连接
check_database() {
    if ! mysql -u option_trading -p4kkyaup6 option_trading -e "SELECT 1" >/dev/null 2>&1; then
        echo "[ERROR] Database connection failed!"
    fi
}

# 检查交易时间
check_trading_hours() {
    # 获取美东时间
    ET_HOUR=$(TZ="America/New_York" date +%H)
    ET_MIN=$(TZ="America/New_York" date +%M)
    ET_TIME=$((ET_HOUR * 60 + ET_MIN))
    
    # 交易时间范围（4:00 AM - 8:00 PM ET）
    TRADING_START=$((4 * 60))
    TRADING_END=$((20 * 60))
    
    if [ $ET_TIME -ge $TRADING_START ] && [ $ET_TIME -le $TRADING_END ]; then
        CHECK_INTERVAL=60  # 交易时间内每分钟检查
    else
        CHECK_INTERVAL=300  # 非交易时间每5分钟检查
    fi
    
    echo $CHECK_INTERVAL
}

# 记录监控日志
log_message() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_DIR/monitor.log"
    echo "$1"
}

# 主循环
while true; do
    # 获取检查间隔
    CHECK_INTERVAL=$(check_trading_hours)
    
    # 执行所有检查
    check_service
    check_logs
    check_memory
    check_disk_space
    check_database
    
    # 等待下一次检查
    sleep $CHECK_INTERVAL
done 