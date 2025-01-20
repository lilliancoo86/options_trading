"""
配置文件示例
使用方法:
1. 复制此文件并重命名为 config.py
2. 根据实际需求修改配置值
3. 确保 config.py 已添加到 .gitignore
"""

import logging
import os
from pathlib import Path
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

TRADING_CONFIG = {
    # 交易品种配置
    'symbols': [
        "TSLL.US",   # 特斯拉做多ETF
        "NVDA.US",   # 英伟达
        "AAPL.US",   # 苹果公司
    ],
    
    # 风险控制参数
    'risk_limits': {
        'option': {
            'stop_loss': float(os.getenv('OPTION_STOP_LOSS', '-10.0')),  # 期权固定10%止损
            'take_profit': None,  # 期权不设固定止盈
            'max_loss_per_trade': float(os.getenv('OPTION_MAX_LOSS_PER_TRADE', '500')),
            'max_daily_loss': float(os.getenv('OPTION_MAX_DAILY_LOSS', '1000'))
        },
        'stock': {
            'stop_loss': float(os.getenv('STOCK_STOP_LOSS', '-3.0')),    # 股票固定3%止损
            'take_profit': float(os.getenv('STOCK_TAKE_PROFIT', '5.0')), # 股票固定5%止盈
            'max_loss_per_trade': float(os.getenv('STOCK_MAX_LOSS_PER_TRADE', '300')),
            'max_daily_loss': float(os.getenv('STOCK_MAX_DAILY_LOSS', '800'))
        },
        'market': {
            'max_position_value': float(os.getenv('MAX_POSITION_VALUE', '100000')),
            'max_total_exposure': float(os.getenv('MAX_TOTAL_EXPOSURE', '500000')),
            'max_positions': int(os.getenv('MAX_POSITIONS', '10'))
        }
    },
    
    # 市场时间配置
    'market_times': {
        'pre_market': {
            'open': os.getenv('PRE_MARKET_OPEN', '04:00'),
            'close': os.getenv('PRE_MARKET_CLOSE', '09:30')
        },
        'regular': {
            'open': os.getenv('REGULAR_MARKET_OPEN', '09:30'),
            'close': os.getenv('REGULAR_MARKET_CLOSE', '16:00')
        },
        'post_market': {
            'open': os.getenv('POST_MARKET_OPEN', '16:00'),
            'close': os.getenv('POST_MARKET_CLOSE', '20:00')
        },
        'force_close': os.getenv('FORCE_CLOSE_TIME', '15:45'),
        'warning': os.getenv('WARNING_TIME', '15:40')
    },
    
    # 订阅类型配置
    'subscription_types': [
        'Quote',     # 基础报价
        'Depth',     # 盘口
        'Brokers',   # 经纪队列
        'Trade',     # 逐笔成交
        'Greeks'     # 期权希腊字母
    ],
}

# LongPort OpenAPI 配置
API_CONFIG = {
    'app_key': os.getenv('LONGPORT_APP_KEY'),
    'app_secret': os.getenv('LONGPORT_APP_SECRET'),
    'access_token': os.getenv('LONGPORT_ACCESS_TOKEN'),
    'region': os.getenv('LONGPORT_REGION', 'cn'),
    'quote_context': {
        'timeout': 5,
        'reconnect_interval': 3,
        'max_retry': 3,
        'sub_types': ['Quote', 'Depth', 'Brokers', 'Trade', 'Greeks']
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
    'file_path': 'logs/trading.log',  # 保持相对路径
    'max_bytes': 10 * 1024 * 1024,  # 10MB
    'backup_count': 5,
}

# 数据存储配置
DATA_CONFIG = {
    'base_dir': '/home/options_trading/data',  # 项目根目录下的 data 目录
    'market_data_dir': '/home/options_trading/data/market_data',
    'update_interval': int(os.getenv('DATA_UPDATE_INTERVAL', '60')),  # 数据更新间隔（秒）
    'retention_days': int(os.getenv('DATA_RETENTION_DAYS', '365')),  # 数据保留天数
    'backup_enabled': os.getenv('DATA_BACKUP_ENABLED', 'true').lower() == 'true',  # 是否启用备份
    'compression': os.getenv('DATA_COMPRESSION', 'true').lower() == 'true',    # 是否压缩历史数据
}

# 注意：实际的 config.py 文件应该包含真实的配置值
# 并且不应该被提交到版本控制系统中
