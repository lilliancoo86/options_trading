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
        'MSFT.US',    # 微软
        'GOOGL.US',   # 谷歌
        'AMZN.US',    # 亚马逊
        'META.US',    # Meta(原Facebook)
        'NVDA.US',    # 英伟达
        'AAPL.US',    # 苹果
        'AMD.US',     # AMD
        'INTC.US',    # 英特尔
        'SMCI.US',    # Super Micro Computer
        'NFLX.US',    # 奈飞
        'PLTR.US',    # Palantir
        'COIN.US',    # Coinbase
        'OKLO.US',    # Oklo
       'VST.US',     # Vistra
    ],
    
    # 交易参数
    'loop_interval': 60,          # 交易循环间隔(秒)
    'max_positions': 5,           # 最大持仓数量
    'position_size': 1000,      # 单个持仓规模(美元)
    'stop_loss_pct': 0.02,       # 止损比例
    'take_profit_pct': 0.05,     # 止盈比例
    
    # 风险控制
    'max_drawdown': 0.1,         # 最大回撤限制
    'max_leverage': 2.0,         # 最大杠杆倍数
    'position_limit': 0.2,       # 单个持仓占比限制
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
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'detailed': {
            'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            'datefmt': '%Y-%m-%d %H:%M:%S'
        },
        'simple': {
            'format': '%(levelname)s: %(message)s'
        }
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'level': 'INFO',
            'formatter': 'simple',
            'stream': 'ext://sys.stdout'
        },
        'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'level': 'DEBUG',  # 确保能记录DEBUG级别的日志
            'formatter': 'detailed',
            'filename': str(LOG_DIR / 'trading_{current_date}.log'),
            'maxBytes': 10 * 1024 * 1024,  # 10MB
            'backupCount': 5,
            'encoding': 'utf-8'
        },
        'error_file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'level': 'ERROR',
            'formatter': 'detailed',
            'filename': str(LOG_DIR / 'error_{current_date}.log'),
            'maxBytes': 10 * 1024 * 1024,  # 10MB
            'backupCount': 5,
            'encoding': 'utf-8'
        }
    },
    'loggers': {
        '': {  # root logger
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': True
        },
        'trading': {  # trading package logger
            'handlers': ['console', 'file', 'error_file'],
            'level': 'DEBUG',  # 设置为DEBUG以记录所有日志
            'propagate': False
        },
        'trading.data_manager': {  # data_manager module logger
            'handlers': ['console', 'file', 'error_file'],
            'level': 'DEBUG',  # 特别设置data_manager的日志级别为DEBUG
            'propagate': False
        }
    }
}

# 数据存储配置
DATA_CONFIG = {
    'cleanup': {
        'max_days': 30,  # 保留最近30天的数据
        'backup_interval': 24,  # 每24小时备份一次
        'cleanup_interval': 24,  # 每24小时清理一次
    },
    'storage': {
    'base_dir': str(DATA_DIR),
    'market_data_dir': str(DATA_DIR / 'market_data'),
    'options_data_dir': str(DATA_DIR / 'options_data'),
    'historical_dir': str(DATA_DIR / 'historical'),
    'backup_dir': str(DATA_DIR / 'backup'),
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

# 导出所有配置
CONFIG = {
    'BASE_DIR': BASE_DIR,
    'DATA_DIR': DATA_DIR,
    'LOG_DIR': LOG_DIR,
    'CONFIG_DIR': CONFIG_DIR,
    'TRADING_CONFIG': TRADING_CONFIG,
    'API_CONFIG': API_CONFIG,
    'LOGGING_CONFIG': LOGGING_CONFIG,
    'DATA_CONFIG': DATA_CONFIG,
    'CLEANUP_CONFIG': CLEANUP_CONFIG
}


