-- 创建数据库
CREATE DATABASE IF NOT EXISTS option_trading CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE option_trading;

-- 期权基础信息表
CREATE TABLE IF NOT EXISTS options (
    option_code VARCHAR(20) PRIMARY KEY,
    underlying_symbol VARCHAR(20) NOT NULL COMMENT '标的代码',
    option_type ENUM('CALL', 'PUT') NOT NULL COMMENT '期权类型',
    strike_price DECIMAL(12,4) NOT NULL COMMENT '行权价',
    expiry_date DATE NOT NULL COMMENT '到期日',
    contract_unit INT NOT NULL DEFAULT 100 COMMENT '合约单位',
    is_active BOOLEAN DEFAULT TRUE COMMENT '是否活跃',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_underlying (underlying_symbol),
    INDEX idx_expiry (expiry_date),
    INDEX idx_type_strike (option_type, strike_price)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='期权基础信息';

-- 持仓记录表
CREATE TABLE IF NOT EXISTS position_records (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    option_code VARCHAR(20) NOT NULL COMMENT '期权代码',
    position_size INT NOT NULL COMMENT '持仓数量',
    entry_price DECIMAL(12,4) NOT NULL COMMENT '开仓价格',
    current_price DECIMAL(12,4) NOT NULL COMMENT '当前价格',
    stop_loss DECIMAL(12,4) NOT NULL COMMENT '止损价格',
    high_price DECIMAL(12,4) NOT NULL COMMENT '最高价格',
    entry_time DATETIME(3) NOT NULL COMMENT '开仓时间',
    exit_time DATETIME(3) DEFAULT NULL COMMENT '平仓时间',
    pnl DECIMAL(15,4) DEFAULT NULL COMMENT '平仓盈亏',
    status ENUM('OPEN', 'CLOSED') NOT NULL DEFAULT 'OPEN' COMMENT '持仓状态',
    close_reason VARCHAR(50) DEFAULT NULL COMMENT '平仓原因',
    delta DECIMAL(10,4) DEFAULT NULL COMMENT 'Delta值',
    theta DECIMAL(10,4) DEFAULT NULL COMMENT 'Theta值',
    currency VARCHAR(8) DEFAULT 'USD' COMMENT '货币',
    market_value DECIMAL(20,4) COMMENT '市值',
    unrealized_pl DECIMAL(20,4) COMMENT '未实现盈亏',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_option_status (option_code, status),
    INDEX idx_entry_time (entry_time),
    FOREIGN KEY (option_code) REFERENCES options(option_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='持仓记录';

-- 期权交易记录表
CREATE TABLE IF NOT EXISTS option_trades (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    option_code VARCHAR(20) NOT NULL COMMENT '期权代码',
    trade_type ENUM('BUY', 'SELL') NOT NULL COMMENT '交易类型',
    quantity INT NOT NULL COMMENT '交易数量',
    price DECIMAL(12,4) NOT NULL COMMENT '成交价格',
    total_amount DECIMAL(15,4) NOT NULL COMMENT '成交金额',
    commission DECIMAL(10,4) DEFAULT 0 COMMENT '手续费',
    order_id VARCHAR(50) NOT NULL COMMENT '订单号',
    position_id BIGINT DEFAULT NULL COMMENT '关联持仓ID',
    close_reason VARCHAR(50) DEFAULT NULL COMMENT '平仓原因',
    currency VARCHAR(8) DEFAULT 'USD' COMMENT '货币',
    executed_value DECIMAL(20,4) NOT NULL COMMENT '成交金额(含手续费)',
    create_time DATETIME(3) NOT NULL COMMENT '创建时间',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_option_code (option_code),
    INDEX idx_create_time (create_time),
    INDEX idx_position (position_id),
    FOREIGN KEY (option_code) REFERENCES options(option_code),
    FOREIGN KEY (position_id) REFERENCES position_records(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='期权交易记录';

-- 期权指标表
CREATE TABLE IF NOT EXISTS option_metrics (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    option_code VARCHAR(20) NOT NULL COMMENT '期权代码',
    date DATE NOT NULL COMMENT '日期',
    time TIME NOT NULL COMMENT '时间',
    implied_volatility DECIMAL(10,4) COMMENT '隐含波动率',
    delta DECIMAL(10,4) COMMENT 'Delta',
    gamma DECIMAL(10,4) COMMENT 'Gamma',
    theta DECIMAL(10,4) COMMENT 'Theta',
    vega DECIMAL(10,4) COMMENT 'Vega',
    rho DECIMAL(10,4) COMMENT 'Rho',
    bid_price DECIMAL(12,4) COMMENT '买价',
    ask_price DECIMAL(12,4) COMMENT '卖价',
    volume INT COMMENT '成交量',
    open_interest INT COMMENT '持仓量',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_option_datetime (option_code, date, time),
    FOREIGN KEY (option_code) REFERENCES options(option_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='期权指标';

-- 交易信号表
CREATE TABLE IF NOT EXISTS signals (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    option_code VARCHAR(20) NOT NULL COMMENT '期权代码',
    signal_type ENUM('BUY', 'SELL') NOT NULL COMMENT '信号类型',
    signal_strength DECIMAL(10,4) NOT NULL COMMENT '信号强度',
    generated_at DATETIME(3) NOT NULL COMMENT '生成时间',
    rsi DECIMAL(10,4) COMMENT 'RSI指标',
    macd DECIMAL(10,4) COMMENT 'MACD指标',
    macd_signal DECIMAL(10,4) COMMENT 'MACD信号线',
    macd_hist DECIMAL(10,4) COMMENT 'MACD柱状图',
    volume_ratio DECIMAL(10,4) COMMENT '成交量比率',
    vix_level DECIMAL(10,4) COMMENT 'VIX指数',
    is_executed BOOLEAN DEFAULT FALSE COMMENT '是否已执行',
    execution_time DATETIME(3) DEFAULT NULL COMMENT '执行时间',
    execution_price DECIMAL(12,4) DEFAULT NULL COMMENT '执行价格',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_option_time (option_code, generated_at),
    INDEX idx_execution (is_executed, execution_time),
    FOREIGN KEY (option_code) REFERENCES options(option_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='交易信号';

-- 每日交易统计表
CREATE TABLE IF NOT EXISTS daily_stats (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    trade_date DATE NOT NULL COMMENT '交易日期',
    total_trades INT NOT NULL DEFAULT 0 COMMENT '总交易次数',
    winning_trades INT NOT NULL DEFAULT 0 COMMENT '盈利交易次数',
    total_pnl DECIMAL(15,4) NOT NULL DEFAULT 0 COMMENT '总盈亏',
    max_drawdown DECIMAL(12,4) NOT NULL DEFAULT 0 COMMENT '最大回撤',
    max_profit DECIMAL(12,4) NOT NULL DEFAULT 0 COMMENT '最大收益',
    max_loss DECIMAL(12,4) NOT NULL DEFAULT 0 COMMENT '最大亏损',
    avg_holding_time INT DEFAULT NULL COMMENT '平均持仓时间(分钟)',
    win_rate DECIMAL(5,2) DEFAULT NULL COMMENT '胜率',
    profit_factor DECIMAL(10,4) DEFAULT NULL COMMENT '盈亏比',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_trade_date (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='每日交易统计';

-- 风险事件表
CREATE TABLE IF NOT EXISTS risk_events (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    event_type VARCHAR(50) NOT NULL COMMENT '事件类型',
    severity ENUM('LOW', 'MEDIUM', 'HIGH') NOT NULL COMMENT '严重程度',
    description TEXT NOT NULL COMMENT '事件描述',
    option_code VARCHAR(20) DEFAULT NULL COMMENT '相关期权代码',
    position_id BIGINT DEFAULT NULL COMMENT '相关持仓ID',
    vix_level DECIMAL(10,4) DEFAULT NULL COMMENT 'VIX水平',
    market_condition TEXT DEFAULT NULL COMMENT '市场状况',
    action_taken VARCHAR(255) DEFAULT NULL COMMENT '采取的行动',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_event_type (event_type),
    INDEX idx_severity (severity),
    INDEX idx_option (option_code),
    FOREIGN KEY (option_code) REFERENCES options(option_code),
    FOREIGN KEY (position_id) REFERENCES position_records(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='风险事件';

-- 市场数据表
CREATE TABLE IF NOT EXISTS market_data (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    data_time DATETIME(3) NOT NULL COMMENT '数据时间',
    vix DECIMAL(10,4) NOT NULL COMMENT 'VIX指数',
    underlying_symbol VARCHAR(20) NOT NULL COMMENT '标的代码',
    underlying_price DECIMAL(12,4) NOT NULL COMMENT '标的价格',
    volume BIGINT NOT NULL COMMENT '成交量',
    open_price DECIMAL(12,4) NOT NULL COMMENT '开盘价',
    high_price DECIMAL(12,4) NOT NULL COMMENT '最高价',
    low_price DECIMAL(12,4) NOT NULL COMMENT '最低价',
    close_price DECIMAL(12,4) NOT NULL COMMENT '收盘价',
    avg_volume_10d BIGINT DEFAULT NULL COMMENT '10日平均成交量',
    turnover DECIMAL(20,4) COMMENT '成交额',
    vwap DECIMAL(12,4) COMMENT '成交量加权平均价',
    prev_close DECIMAL(12,4) COMMENT '前收价',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_time_symbol (data_time, underlying_symbol)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='市场数据';

-- 订单状态表
CREATE TABLE IF NOT EXISTS order_status (
    order_id VARCHAR(50) PRIMARY KEY,
    option_code VARCHAR(20) NOT NULL COMMENT '期权代码',
    order_type ENUM('MARKET', 'LIMIT') NOT NULL COMMENT '订单类型',
    direction ENUM('BUY', 'SELL') NOT NULL COMMENT '交易方向',
    quantity INT NOT NULL COMMENT '数量',
    price DECIMAL(12,4) DEFAULT NULL COMMENT '限价单价格',
    status ENUM('PENDING', 'FILLED', 'CANCELLED', 'REJECTED') NOT NULL COMMENT '订单状态',
    filled_quantity INT DEFAULT 0 COMMENT '已成交数量',
    filled_price DECIMAL(12,4) DEFAULT NULL COMMENT '成交价格',
    message TEXT COMMENT '状态信息',
    create_time DATETIME(3) NOT NULL COMMENT '创建时间',
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_option_status (option_code, status),
    INDEX idx_create_time (create_time),
    FOREIGN KEY (option_code) REFERENCES options(option_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='订单状态';

-- 账户余额表（新增）
CREATE TABLE IF NOT EXISTS account_balance (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    currency VARCHAR(8) NOT NULL COMMENT '货币',
    cash_balance DECIMAL(20,4) NOT NULL COMMENT '现金余额',
    available_balance DECIMAL(20,4) NOT NULL COMMENT '可用余额',
    holding_value DECIMAL(20,4) NOT NULL COMMENT '持仓市值',
    total_assets DECIMAL(20,4) NOT NULL COMMENT '总资产',
    timestamp DATETIME NOT NULL COMMENT '时间戳',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_timestamp (timestamp)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='账户余额';

-- 系统状态表
CREATE TABLE IF NOT EXISTS system_status (
    id INT PRIMARY KEY AUTO_INCREMENT,
    component VARCHAR(50) NOT NULL COMMENT '组件名称',
    status ENUM('RUNNING', 'STOPPED', 'ERROR') NOT NULL COMMENT '状态',
    last_heartbeat TIMESTAMP NOT NULL COMMENT '最后心跳时间',
    error_count INT DEFAULT 0 COMMENT '错误计数',
    details TEXT COMMENT '详细信息',
    config_version VARCHAR(20) COMMENT '配置版本',
    sdk_version VARCHAR(20) COMMENT 'SDK版本',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_component (component),
    INDEX idx_status (status),
    INDEX idx_heartbeat (last_heartbeat)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='系统状态';

-- 清理旧数据
DELETE FROM risk_events;
DELETE FROM daily_stats;
DELETE FROM system_status;

-- 初始化系统状态记录
INSERT INTO system_status (component, status, last_heartbeat, error_count, details) VALUES 
('data_fetcher', 'STOPPED', NOW(), 0, '{"last_update": null, "status": "初始化"}'),
('trade_executor', 'STOPPED', NOW(), 0, '{"orders_processed": 0, "status": "初始化"}'),
('risk_checker', 'STOPPED', NOW(), 0, '{"checks_performed": 0, "status": "初始化"}'),
('position_manager', 'STOPPED', NOW(), 0, '{"active_positions": 0, "status": "初始化"}'),
('option_strategy', 'STOPPED', NOW(), 0, '{"signals_generated": 0, "status": "初始化"}');

-- 初始化每日交易统计
INSERT INTO daily_stats (
    trade_date, 
    total_trades, 
    winning_trades,
    total_pnl,
    max_drawdown,
    max_profit,
    max_loss,
    win_rate,
    profit_factor
) VALUES (
    CURDATE(),
    0,
    0,
    0.0000,
    0.0000,
    0.0000,
    0.0000,
    0.00,
    0.0000
);

-- 初始化风险事件记录
INSERT INTO risk_events (
    event_type,
    severity,
    description,
    market_condition
) VALUES (
    'SYSTEM_START',
    'LOW',
    '系统初始化完成',
    '{"vix": null, "market_status": "INIT"}'
);

-- 删除已存在的触发器
DROP TRIGGER IF EXISTS calculate_pnl_on_close;
DROP TRIGGER IF EXISTS update_daily_stats_on_close;

DELIMITER //

-- 创建触发器（去掉 IF NOT EXISTS）
CREATE TRIGGER calculate_pnl_on_close
BEFORE UPDATE ON position_records
FOR EACH ROW
BEGIN
    IF NEW.status = 'CLOSED' AND OLD.status = 'OPEN' THEN
        SET NEW.pnl = (NEW.current_price - NEW.entry_price) * NEW.position_size;
    END IF;
END//

CREATE TRIGGER update_daily_stats_on_close
AFTER UPDATE ON position_records
FOR EACH ROW
BEGIN
    IF NEW.status = 'CLOSED' AND OLD.status = 'OPEN' THEN
        UPDATE daily_stats
        SET 
            total_trades = total_trades + 1,
            winning_trades = winning_trades + IF(NEW.pnl > 0, 1, 0),
            total_pnl = total_pnl + NEW.pnl,
            max_profit = GREATEST(max_profit, NEW.pnl),
            max_loss = LEAST(max_loss, NEW.pnl),
            win_rate = (winning_trades / total_trades) * 100
        WHERE trade_date = CURDATE();
    END IF;
END//

DELIMITER ;

-- 添加字符集和排序规则
ALTER DATABASE option_trading 
CHARACTER SET = utf8mb4 
COLLATE = utf8mb4_unicode_ci;

-- 修改表引擎
ALTER TABLE options ENGINE = InnoDB;
