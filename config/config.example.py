"""
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

# 交易配置
TRADING_CONFIG = {
    # 交易标的配置
    'symbols': [
        'AAPL.US',    # 苹果
        'MSFT.US',    # 微软
        'GOOGL.US',   # 谷歌
        'AMZN.US',    # 亚马逊
        'META.US',    # Meta(原Facebook)
        'NVDA.US',    # 英伟达
        'TSLA.US',    # 特斯拉
        'AMD.US',     # AMD
        'INTC.US',    # 英特尔
        'SMCI.US',    # Super Micro Computer
        'NFLX.US',    # 奈飞
        'PLTR.US',    # Palantir
        'COIN.US',    # Coinbase
        'OKLO.US',    # Oklo
        'VST.US',     # Vistra
    ],
}

# LongPort OpenAPI 配置
API_CONFIG = {
    'app_key': os.getenv('LONGPORT_APP_KEY'),
    'app_secret': os.getenv('LONGPORT_APP_SECRET'),
    'access_token': os.getenv('LONGPORT_ACCESS_TOKEN'),
    'http_url': os.getenv('LONGPORT_HTTP_URL', 'https://openapi.longportapp.com'),
    'quote_ws_url': os.getenv('LONGPORT_QUOTE_WS_URL', 'wss://openapi-quote.longportapp.com/v2'),
    'trade_ws_url': os.getenv('LONGPORT_TRADE_WS_URL', 'wss://openapi-trade.longportapp.com/v2'),
    'region': os.getenv('LONGPORT_REGION', 'hk'),
    
    # 请求限制配置
    'request_limit': {
        'max_requests': 120,    # 每分钟最大请求数
        'time_window': 60,      # 时间窗口（秒）
        'quote': {
            'max_requests': 10,  # 行情接口每秒最大请求数
            'time_window': 1,    # 行情接口时间窗口（秒）
            'max_symbols': 20    # 每批次最大标的数量
        }
    },
    
    'quote_context': {
        'timeout': 30,
        'reconnect_interval': 3,
        'max_retry': 3
    },
    'trade_context': {
        'timeout': 10,
        'reconnect_interval': 3,
        'max_retry': 3
    }
}


# 日志配置
LOGGING_CONFIG = {
    'level': logging.DEBUG,
    'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    'date_format': '%Y-%m-%d %H:%M:%S',
    'file_path': 'logs/trading.log',
    'max_bytes': 2 * 1024 * 1024,  # 2MB
    'backup_count': 3,
    'handlers': {
        'console': {
            'enabled': True,
            'level': logging.INFO,
            'format': '%(levelname)s: %(message)s'
        },
        'file': {
            'enabled': True,
            'level': logging.DEBUG,
            'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        }
    }
}

# 数据存储配置
DATA_CONFIG = {
    'base_dir': '/home/options_trading/data',          # 基础数据目录
    'market_data_dir': '/home/options_trading/data/market_data',    # 市场数据目录
    'options_data_dir': '/home/options_trading/data/options_data',  # 期权数据目录
    'historical_dir': '/home/options_trading/data/historical',      # 历史数据目录
    'backup_dir': '/home/options_trading/data/backup',             # 备份目录
    'update_interval': int(os.getenv('DATA_UPDATE_INTERVAL', '60')),      # 数据更新间隔(秒)
    'retention_days': int(os.getenv('DATA_RETENTION_DAYS', '365')),      # 数据保留天数
    'backup_enabled': os.getenv('DATA_BACKUP_ENABLED', 'true').lower() == 'true',  # 是否启用备份
    'compression': os.getenv('DATA_COMPRESSION', 'true').lower() == 'true',        # 是否启用压缩
    
    # 期权数据配置
    'options_data': {
        'greeks_update_interval': 300,  # 希腊字母更新间隔(秒)
        'chain_update_interval': 1800,  # 期权链更新间隔(秒)
        'iv_history_days': 30,         # 隐含波动率历史数据保留天数
        'volume_threshold': 100        # 期权成交量阈值
    },
    
    # 数据存储配置
    'storage': {
        'format': 'parquet',           # 数据存储格式
        'compression': 'snappy',       # 压缩方式
        'partition_by': 'date'         # 分区方式
    },
    
    # 数据存储配置
    'storage': {
        'max_klines_per_file': 1000,   # 每个K线文件最大记录数
        'max_file_size': 100 * 1024 * 1024,  # 单个文件最大大小（100MB）
        'backup_enabled': False,        # 是否启用备份
        'backup_interval': 86400,       # 备份间隔（秒，默认24小时）
    }
}

# 数据清理配置
CLEANUP_CONFIG = {
    # 数据保留时间
    'klines_retention_days': 365,     # K线数据保留天数
    'options_retention_days': 30,      # 期权数据保留天数
    'logs_retention_days': 30,         # 日志保留天数
    'market_data_retention_days': 90,  # 市场数据保留天数
    
    # 清理任务配置
    'cleanup_interval': 86400,         # 清理间隔（秒，默认24小时）
    'cleanup_time': '00:00',           # 每日清理时间点
    'cleanup_enabled': True,           # 是否启用自动清理
    
    # 清理规则
    'cleanup_rules': {
        'min_records': 100,            # 最小保留记录数
        'max_file_age': 365,           # 文件最大保留天数
        'delete_empty_dirs': True,     # 删除空目录
        'skip_recent_files': True,     # 跳过最近修改的文件
        'recent_threshold': 3600       # 最近文件阈值(秒)
    },
    
    # 日志清理
    'log_cleanup': {
        'max_log_size': 10 * 1024 * 1024,  # 单个日志文件最大大小（10MB）
        'max_log_files': 30,               # 最大日志文件数
        'compress_logs': False,            # 是否压缩旧日志
        'delete_empty_logs': True          # 删除空日志文件
    }
}


