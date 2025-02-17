"""
主程序入口
负责初始化和运行交易系统
"""
import sys
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler
import asyncio
import json
import yaml
from datetime import datetime
import pytz
from typing import Dict, Any, Tuple
import os
from dotenv import load_dotenv
from config.config import (
    TRADING_CONFIG, API_CONFIG, LOGGING_CONFIG,
    DATA_CONFIG, CLEANUP_CONFIG, BASE_DIR,
    DATA_DIR, LOG_DIR, CONFIG_DIR
)

# 添加项目路径到Python路径
sys.path.append(str(BASE_DIR))

# 导入交易模块
from trading.data_manager import DataManager
from trading.data_cleaner import DataCleaner
from trading.option_strategy import DoomsdayOptionStrategy
from trading.position_manager import DoomsdayPositionManager
from trading.risk_checker import RiskChecker
from trading.time_checker import TimeChecker

# 创建统一的配置字典
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

def setup_logging() -> logging.Logger:
    """配置日志系统"""
    try:
        # 创建日志目录
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        
        # 获取当前日期
        current_date = datetime.now().strftime('%Y%m%d')
        log_file = LOG_DIR / f"trading_{current_date}.log"
        
        # 创建日志处理器
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        
        # 创建控制台处理器
        console_handler = logging.StreamHandler()
        
        # 设置日志格式
        log_format = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        file_handler.setFormatter(log_format)
        console_handler.setFormatter(log_format)
        
        # 配置根日志记录器
        logger = logging.getLogger()
        logger.setLevel(logging.INFO)
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
        
        return logger
        
    except Exception as e:
        print(f"设置日志系统时出错: {str(e)}")
        raise

def load_config() -> Dict[str, Any]:
    """加载配置文件"""
    try:
        # 验证交易配置
        if not isinstance(TRADING_CONFIG, dict):
            raise ValueError("TRADING_CONFIG 必须是字典类型")
            
        if 'symbols' not in TRADING_CONFIG:
            raise ValueError("TRADING_CONFIG 中缺少 symbols 配置")
            
        if not isinstance(TRADING_CONFIG['symbols'], list):
            raise ValueError("symbols 必须是列表类型")
            
        # 验证并清理交易标的
        valid_symbols = [
            symbol.strip() for symbol in TRADING_CONFIG['symbols']
            if isinstance(symbol, str) and symbol.strip() and symbol.endswith('.US')
        ]
        
        # 检查是否有无效的标的被过滤掉
        if len(valid_symbols) != len(TRADING_CONFIG['symbols']):
            logger.warning(f"部分交易标的格式无效，原始数量: {len(TRADING_CONFIG['symbols'])}, "
                         f"有效数量: {len(valid_symbols)}")
            
        # 确保没有重复的标的
        valid_symbols = list(dict.fromkeys(valid_symbols))
        
        if not valid_symbols:
            raise ValueError("没有有效的交易标的")
            
        # 更新配置中的标的列表
        TRADING_CONFIG['symbols'] = valid_symbols
        
        # 创建一个新的配置字典，避免引用问题
        config = {
            'BASE_DIR': BASE_DIR,
            'DATA_DIR': DATA_DIR,
            'LOG_DIR': LOG_DIR,
            'CONFIG_DIR': CONFIG_DIR,
            'TRADING_CONFIG': {
                'symbols': valid_symbols.copy(),  # 创建副本
                **{k: v for k, v in TRADING_CONFIG.items() if k != 'symbols'}
            },
            'API_CONFIG': API_CONFIG.copy() if isinstance(API_CONFIG, dict) else {},
            'LOGGING_CONFIG': LOGGING_CONFIG.copy() if isinstance(LOGGING_CONFIG, dict) else {},
            'DATA_CONFIG': DATA_CONFIG.copy() if isinstance(DATA_CONFIG, dict) else {},
            'CLEANUP_CONFIG': CLEANUP_CONFIG.copy() if isinstance(CLEANUP_CONFIG, dict) else {}
        }
        
        logger.info(f"成功加载配置文件")
        logger.info(f"已配置 {len(valid_symbols)} 个交易标的: {valid_symbols}")
        
        return config
        
    except ImportError:
        logger.error("无法导入配置文件，请确保已从 config.example.py 复制并创建 config.py")
        raise
    except Exception as e:
        logger.error(f"加载配置时出错: {str(e)}")
        logger.exception("详细错误信息：")
        raise

async def initialize_components(config: Dict[str, Any]) -> Dict[str, Any]:
    components = {}
    try:
        # 初始化数据管理器
        data_manager = DataManager(config['TRADING_CONFIG'])
        await data_manager.async_init()
        components['data_manager'] = data_manager
        logger.info("数据管理器初始化完成")
        
        # 初始化数据清理器
        logger.info("正在初始化数据清理器...")
        data_cleaner = DataCleaner(config['DATA_CONFIG'])
        components['data_cleaner'] = data_cleaner
        logger.info("数据清理器初始化完成")
        
        # 初始化时间检查器
        logger.info("正在初始化时间检查器...")
        time_checker = TimeChecker(config['TRADING_CONFIG'])
        await time_checker.async_init()
        components['time_checker'] = time_checker
        logger.info("时间检查器初始化完成")
        
        # 初始化策略
        logger.info("正在初始化交易策略...")
        strategy = DoomsdayOptionStrategy(config['TRADING_CONFIG'], data_manager)
        await strategy.async_init()
        components['strategy'] = strategy
        logger.info("交易策略初始化完成")
        
        # 初始化风险检查器
        logger.info("正在初始化风险检查器...")
        risk_checker = RiskChecker(config['TRADING_CONFIG'], strategy, time_checker)
        await risk_checker.async_init()
        components['risk_checker'] = risk_checker
        logger.info("风险检查器初始化完成")
        
        # 初始化持仓管理器
        logger.info("正在初始化持仓管理器...")
        position_manager = DoomsdayPositionManager(config['TRADING_CONFIG'], data_manager)
        await position_manager.async_init()
        components['position_manager'] = position_manager
        logger.info("持仓管理器初始化完成")
        
        return components
        
    except Exception as e:
        logger.error(f"初始化组件时出错: {str(e)}")
        raise

async def run_trading_loop(
    config: Dict[str, Any],
    data_manager: DataManager,
    data_cleaner: DataCleaner,
    time_checker: TimeChecker,
    risk_checker: RiskChecker,
    position_manager: DoomsdayPositionManager,
    strategy: DoomsdayOptionStrategy
) -> None:
    """运行交易循环"""
    logger = logging.getLogger(__name__)
    
    try:
        while True:
            try:
                logger.info("\n=== 开始新一轮交易循环 ===")
                
                # 确保我们有交易标的
                if not hasattr(data_manager, 'symbols') or not data_manager.symbols:
                    logger.error("没有可用的交易标的")
                    await asyncio.sleep(10)
                    continue
                
                # 更新市场数据
                if not await data_manager.update_all_klines():
                    logger.error("更新市场数据失败")
                    await asyncio.sleep(10)
                    continue
                
                # 执行数据清理
                await data_cleaner.cleanup()
                
                # 检查交易时间
                if not await time_checker.check_market_time():
                    logger.info("当前不在交易时间")
                    await asyncio.sleep(60)
                    continue
                
                # 获取当前持仓
                positions = await position_manager.get_positions()
                
                # 遍历每个交易标的
                for symbol in data_manager.symbols:
                    try:
                        # 获取交易信号
                        signal = await strategy.generate_signal(symbol)
                        if not signal:
                            continue
                            
                        # 检查风险
                        if not await risk_checker.check_risk(symbol, signal):
                            continue  # 移除重复的警告日志
                            
                        # 执行交易 - 不需要额外的日志，因为相关模块已经有详细日志
                        if signal.get('action') == 'buy':
                            await position_manager.open_position(
                                symbol,
                                signal.get('quantity', 0),
                                signal.get('price', 0)
                            )
                        elif signal.get('action') == 'sell':
                            await position_manager.close_position(
                                symbol,
                                signal.get('quantity', 0)
                            )
                    except Exception as e:
                        logger.error(f"处理交易标的 {symbol} 时出错: {str(e)}")
                        continue
                
                # 等待下一个循环
                await asyncio.sleep(config.get('TRADING_CONFIG', {}).get('loop_interval', 60))
                
            except Exception as e:
                logger.error(f"交易循环中出错: {str(e)}")
                await asyncio.sleep(10)
                
    except KeyboardInterrupt:
        logger.info("收到终止信号，正在关闭交易系统...")
    except Exception as e:
        logger.error(f"交易系统运行时出错: {str(e)}")
        raise

async def main():
    """主程序入口"""
    try:
        # 加载环境变量
        load_dotenv()
        
        # 设置日志系统
        global logger
        logger = setup_logging()
        logger.info("**提示**: 开始初始化doomsday系统...")
        
        # 加载配置
        config = load_config()
        
        # 初始化组件
        components = await initialize_components(config)
        
        # 运行交易循环
        await run_trading_loop(config, **components)
        
    except Exception as e:
        logger.error(f"程序运行时出错: {str(e)}")
        raise
    finally:
        logger.info("交易系统已关闭")

if __name__ == "__main__":
    asyncio.run(main())

