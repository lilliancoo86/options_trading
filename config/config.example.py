"""
配置文件示例
使用方法:
1. 复制此文件并重命名为 config.py
2. 根据实际需求修改配置值
3. 确保 config.py 已添加到 .gitignore
"""

import logging
import os
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

TRADING_CONFIG = {
    # 交易品种配置
    'symbols': [
        "AAPL",   # 苹果公司
        "NVDA",   # 英伟达
        "TSLL",   # 特斯拉
        "IONQ",   # IONQ
    ],
    
    # 监控列表配置
    'watchlist': {
        'scan_interval': 5,          # 扫描间隔(秒)
        'filters': {
            'min_volume': 1000,      # 最小成交量
            'min_price': 0.5,        # 最小期权价格
            'max_price': 15.0,       # 最大期权价格
            'min_delta': 0.3,        # 最小Delta
            'max_delta': 0.7,        # 最大Delta
            'min_days': 15,          # 最小剩余天数
            'max_days': 45,          # 最大剩余天数
        }
    },
    
    # 趋势判断参数
    'trend_config': {
        'fast_length': 1,           # 快线周期
        'slow_length': 5,           # 慢线周期
        'curve_length': 10,         # 曲线周期
        'trend_period': 5,          # 趋势判断周期
        'vwap_dev': 2.0            # VWAP通道宽度
    },
    
    # 信号强度权重
    'signal_weights': {
        'volume_surge': 0.3,        # 成交量突增权重
        'price_trend': 0.3,         # 价格趋势权重
        'time_trend': 0.2,          # 分时趋势权重
        'option_greek': 0.2,        # 期权特征权重
    },
    
    # 风险控制参数
    'risk_limits': {
        'option': {
            'stop_loss': {
                'initial': 0.10,     # 固定止损比例 (10%)
                'trailing': 0.07,    # 移动止损比例 (7%)
            },
            'take_profit': 0.50,    # 基础止盈比例 (50%)，会根据趋势动态调整
        },
        'volatility': {
            'max_vix': 40,          # VIX上限
            'min_vix': 15           # VIX下限
        }
    },
    
    # 仓位管理参数
    'position_sizing': {
        'method': 'fixed_ratio',    # 仓位计算方法
        'size_limit': {
            'min': 1,               # 最小仓位
            'max': 100              # 最大仓位
        },
        'value_limit': {
            'min': 1000,            # 最小持仓金额
            'max': 50000            # 最大持仓金额
        },
        'risk_ratio': 0.02,        # 风险系数
    },
    
    # 组合风险限制
    'max_position_size': 100,       # 最大持仓数量
    'max_daily_loss': -1000,        # 最大日亏损限制
    'max_position_value': 50000,    # 最大持仓市值
    'max_portfolio_delta': 2.0,     # 最大组合Delta
    'min_portfolio_theta': -0.3,    # 最小组合Theta
    
    # 交易时间配置
    'market_open': '09:30:00',      # 市场开盘时间
    'market_close': '16:00:00',     # 市场收盘时间
    'force_close_time': '15:45:00', # 强制平仓时间
    
    # 订阅类型配置
    'subscription_types': [
        'Quote',     # 基础报价
        'Depth',     # 盘口
        'Brokers',   # 经纪队列
        'Trade'      # 逐笔成交
    ],
}

# LongPort OpenAPI 配置
API_CONFIG = {
    'app_key': os.getenv('LONGPORT_APP_KEY'),
    'app_secret': os.getenv('LONGPORT_APP_SECRET'),
    'access_token': os.getenv('LONGPORT_ACCESS_TOKEN'),
    'quote_context': {
        'timeout': 5,
        'reconnect_interval': 3,
        'max_retry': 3,
        'sub_types': ['Quote', 'Depth', 'Brokers', 'Trade']
    },
    'trade_context': {
        'timeout': 10,
        'reconnect_interval': 3,
        'max_retry': 3
    }
}

# 日志配置
LOGGING_CONFIG = {
    'level': logging.INFO,
    'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    'date_format': '%Y-%m-%d %H:%M:%S',
    'file_path': 'logs/trading.log',
    'max_bytes': 10 * 1024 * 1024,  # 10MB
    'backup_count': 5,
}

# 注意：实际的 config.py 文件应该包含真实的配置值
# 并且不应该被提交到版本控制系统中
