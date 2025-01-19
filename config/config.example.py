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
    #    "IONQ",   # IONQ
    ],
    
    # 风险控制参数
    'risk_limits': {
        'option': {
            'stop_loss': -10.0,  # 期权固定10%止损
            'take_profit': None  # 期权不设固定止盈
        },
        'stock': {
            'stop_loss': -3.0,   # 股票固定3%止损
            'take_profit': 5.0    # 股票固定5%止盈
        }
    },
    
    
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
