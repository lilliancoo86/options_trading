-- 删除现有触发器
DROP TRIGGER IF EXISTS calculate_pnl_on_close;
DROP TRIGGER IF EXISTS update_daily_stats_on_close;

-- 禁用外键检查
SET FOREIGN_KEY_CHECKS = 0;

-- 清空所有表（按照依赖关系顺序）
TRUNCATE TABLE daily_stats;
TRUNCATE TABLE risk_events;
TRUNCATE TABLE market_data;
TRUNCATE TABLE option_metrics;
TRUNCATE TABLE signals;
TRUNCATE TABLE option_trades;
TRUNCATE TABLE order_status;
TRUNCATE TABLE position_records;
TRUNCATE TABLE system_status;
TRUNCATE TABLE options;

-- 启用外键检查
SET FOREIGN_KEY_CHECKS = 1;

-- 重新导入初始数据
SOURCE /home/options_trading/database/option_trading.sql; 