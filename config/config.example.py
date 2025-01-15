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
        "TSLA",   # 特斯拉
    ],
    
    # 订阅类型配置
    'subscription_types': [
        'Quote',     # 基础报价
        'Depth',     # 盘口
        'Brokers',   # 经纪队列
        'Trade'      # 逐笔成交
    ],
    
    # 交易时间配置
    'market_open': '09:30:00',
    'market_close': '16:00:00',
    'force_close_time': '15:45:00',
    
    # 风险控制参数
    'max_position_size': 100,        # 最大持仓数量
    'max_daily_loss': -1000,        # 最大日亏损限制
    'max_position_value': 100000,    # 最大持仓市值
    
    # 风险限制配置
    'risk_limits': {
        'position': {
            'max_positions': 5,          # 最大同时持仓数
            'max_position_value': 10000,# 单个持仓最大市值
            'total_position_value': 50000, # 总持仓市值限制
        },
        'loss': {
            'max_daily_loss': -1000,    # 每日最大亏损
            'max_single_loss': -200,   # 单笔最大亏损
            'trailing_stop_pct': 0.1,   # 追踪止损百分比
        },
        'margin': {
            'min_margin_ratio': 0.2,    # 最小保证金比例
            'margin_call_ratio': 0.15,  # 追保线
            'liquidation_ratio': 0.1,   # 强平线
        },
        'volatility': {
            'min_vix': 15,              # 最小 VIX 阈值
            'max_vix': 40,              # 最大 VIX 阈值
            'max_daily_volatility': 0.03,# 最大日波动率
        },
        'option': {                     # 期权特定的风险控制
            'stop_loss': {
                'initial': 0.10,        # 固定止损比例 (10%)
                'trailing': 0.07,       # 移动止损比例 (7%)
                'time_based': 0.05,     # 基于时间的止损基准比例 (5%)
            },
            'take_profit': 0.20,        # 止盈目标 (20%)
            'max_holding_time': 60,     # 最大持仓时间（分钟）
            'min_time_value': 0.20,     # 最小时间价值 (20%)
            'max_theta': -0.05,         # 最大theta衰减率 (-5%)
            'position_delta': {
                'min': -0.50,           # 最小delta (-50%)
                'max': 0.50,            # 最大delta (50%)
            },
            'iv_rank': {
                'min': 20,              # 最小IV Rank (20%)
                'max': 80,              # 最大IV Rank (80%)
            }
        }
    },
    
    # 仓位管理参数
    'position_sizing': {             # 仓位管理配置
        'method': 'fixed_ratio',     # 仓位计算方法：fixed_ratio/kelly/risk_parity
        'size_limit': {
            'min': 1,                # 最小仓位
            'max': 100               # 最大仓位
        },
        'value_limit': {
            'min': 1000,             # 最小持仓金额
            'max': 100000            # 最大持仓金额
        },
        'risk_ratio': 0.02,         # 风险系数
    },
    
    # 交易策略参数
    'stop_loss_pct': 0.05,          # 止损百分比
    'take_profit_pct': 0.10,        # 止盈百分比
    'position_sizing_pct': 0.02,     # 仓位大小百分比
    
    # 其他配置
    'timezone': 'America/New_York',  # 交易时区
    'order_config': {
        'order_type': 'MO',          # 市价单
        'time_in_force': 'Day',      # 当日有效
        'max_wait_seconds': 5,       # 最大等待时间（市价单）
        'price_limit': {
            'max_spread': 0.05       # 期权最大买卖价差 (5%)
        }
    }
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
