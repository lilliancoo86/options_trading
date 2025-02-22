"""
使用方法:
1. 复制此文件并重命名为 config.py
2. 根据实际需求修改配置值
3. 确保 config.py 已添加到 .gitignore
"""
import logging
import os
from dotenv import load_dotenv
from pathlib import Path

# 加载环境变量
load_dotenv()

# 基础路径配置
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data'
LOG_DIR = BASE_DIR / 'logs'
CONFIG_DIR = BASE_DIR / 'config'

# 交易配置
TRADING_CONFIG = {
    # 交易标的配置
    'symbols': [
        'TSLA.US',    # 特斯拉
        'CELH.US',    # Celsius (添加当前持仓的标的)
#        'MSFT.US',    # 微软
#        'GOOGL.US',   # 谷歌
#        'AMZN.US',    # 亚马逊
#        'META.US',    # Meta(原Facebook)
#        'NVDA.US',    # 英伟达
#        'AAPL.US',    # 苹果
    ],
    
    # 交易参数
    'loop_interval': 60,          # 交易循环间隔(秒)
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
    
    # 连接配置
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
    'level': logging.INFO,
    'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    'date_format': '%Y-%m-%d %H:%M:%S',
    'handlers': {
        'console': {
            'enabled': True,
            'level': logging.INFO,
            'format': '%(levelname)s: %(message)s'
        },
        'file': {
            'enabled': True,
            'level': logging.DEBUG,
            'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            'filename': str(LOG_DIR / 'trading_{current_date}.log'),
            'max_bytes': 10 * 1024 * 1024,  # 10MB
            'backup_count': 5
        }
    }
}

# 数据存储配置
DATA_CONFIG = {
    'base_dir': str(DATA_DIR),
    'market_data_dir': str(DATA_DIR / 'market_data'),
    'options_data_dir': str(DATA_DIR / 'options_data'),
    'historical_dir': str(DATA_DIR / 'historical'),
    'backup_dir': str(DATA_DIR / 'backup'),
    
    # 数据更新配置
    'update_interval': int(os.getenv('DATA_UPDATE_INTERVAL', '60')),
    'retention_days': int(os.getenv('DATA_RETENTION_DAYS', '365')),
    'backup_enabled': os.getenv('DATA_BACKUP_ENABLED', 'true').lower() == 'true',
    'compression': os.getenv('DATA_COMPRESSION', 'true').lower() == 'true',
    
    # 期权数据配置
    'options_data': {
        'greeks_update_interval': 300,   # 希腊字母更新间隔(秒)
        'chain_update_interval': 1800,   # 期权链更新间隔(秒)
        'iv_history_days': 30,          # 隐含波动率历史数据保留天数
        'volume_threshold': 100         # 期权成交量阈值
    },
    
    # 存储格式配置
    'storage': {
        'format': 'parquet',            # 数据存储格式
        'compression': 'snappy',        # 压缩方式
        'partition_by': 'date',         # 分区方式
        'max_klines_per_file': 1000,    # 每个K线文件最大记录数
        'max_file_size': 100 * 1024 * 1024,  # 单个文件最大大小（100MB）
    },
    
    'data': {
        'historical_days': 100,  # 历史数据获取天数
        'update_interval': 60,   # 数据更新间隔(秒)
        'request_delay': 0.5     # 请求间隔(秒)
    }
}

# 数据清理配置
CLEANUP_CONFIG = {
    # 数据保留配置
    'retention': {
        'klines': 365,        # K线数据保留天数
        'options': 30,        # 期权数据保留天数
        'logs': 30,          # 日志保留天数
        'market_data': 90,   # 市场数据保留天数
    },
    
    # 清理任务配置
    'schedule': {
        'cleanup_interval': 12,   # 清理间隔(小时)
        'backup_interval': 24,    # 备份间隔(小时)
    },
    
    # 存储限制
    'storage': {
        'max_total_size': 5 * 1024 * 1024 * 1024,  # 最大总存储空间(5GB)
        'max_backup_size': 2 * 1024 * 1024 * 1024,  # 最大备份空间(2GB)
        'warning_threshold': 0.8,  # 存储空间警告阈值(80%)
    }
}



