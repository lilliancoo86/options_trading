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

# 设置基础路径
BASE_DIR = Path('/home/options_trading')
LOG_DIR = BASE_DIR / 'logs'
CONFIG_DIR = BASE_DIR / 'config'

# 添加项目路径到Python路径
sys.path.append(str(BASE_DIR))

# 导入交易模块
from trading.data_manager import DataManager
from trading.data_cleaner import DataCleaner
from trading.option_strategy import DoomsdayOptionStrategy
from trading.position_manager import DoomsdayPositionManager
from trading.risk_checker import RiskChecker
from trading.time_checker import TimeChecker

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
        # 加载Python配置模块
        py_config = CONFIG_DIR / 'config.py'
        
        if not py_config.exists():
            raise FileNotFoundError(f"未找到配置文件: {py_config}")
            
        # 导入配置模块
        import importlib.util
        spec = importlib.util.spec_from_file_location("config", py_config)
        config_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(config_module)
        
        # 构建配置字典
        config = {
            'TRADING_CONFIG': config_module.TRADING_CONFIG,
            'API_CONFIG': config_module.API_CONFIG,
            'LOGGING_CONFIG': config_module.LOGGING_CONFIG,
            'DATA_CONFIG': config_module.DATA_CONFIG,
            'CLEANUP_CONFIG': config_module.CLEANUP_CONFIG
        }
        
        logger.info("已加载配置文件")
        return config
            
    except Exception as e:
        logger.error(f"加载配置文件时出错: {str(e)}")
        raise

async def initialize_components(config: Dict[str, Any]) -> Tuple[DataManager, ...]:
    """初始化交易系统组件"""
    try:
        # 确保配置中包含必要的键
        if 'TRADING_CONFIG' not in config:
            raise ValueError("配置中缺少 TRADING_CONFIG")
            
        # 初始化数据管理器
        data_manager = DataManager(config)  # 直接传入完整配置
        await data_manager.async_init()
        
        # 初始化数据清理器
        logger.info("正在初始化数据清理器...")
        data_cleaner = DataCleaner(config['DATA_CONFIG'])
        await data_cleaner.async_init()
        logger.info("数据清理器初始化完成")
        
        # 初始化时间检查器
        logger.info("正在初始化时间检查器...")
        time_checker = TimeChecker(config['TRADING_CONFIG'])
        await time_checker.async_init()
        logger.info("时间检查器初始化完成")
        
        # 初始化策略
        logger.info("正在初始化交易策略...")
        strategy = DoomsdayOptionStrategy(config['TRADING_CONFIG'], data_manager)
        await strategy.async_init()
        logger.info("交易策略初始化完成")
        
        # 初始化风险检查器
        logger.info("正在初始化风险检查器...")
        risk_checker = RiskChecker(config['TRADING_CONFIG'], strategy, time_checker)
        await risk_checker.async_init()
        logger.info("风险检查器初始化完成")
        
        # 初始化持仓管理器
        logger.info("正在初始化持仓管理器...")
        position_manager = DoomsdayPositionManager(config['TRADING_CONFIG'], data_manager)
        await position_manager.async_init()
        logger.info("持仓管理器初始化完成")
        
        return (data_manager, data_cleaner, strategy, 
                position_manager, risk_checker, time_checker)
                
    except Exception as e:
        logger.error(f"初始化组件时出错: {str(e)}")
        raise

async def run_trading_loop(
    config: Dict[str, Any],
    data_manager,
    data_cleaner,
    strategy,
    position_manager,
    risk_checker,
    time_checker
) -> None:
    """运行交易主循环"""
    try:
        while True:
            try:
                # 检查是否在交易时段
                if not await time_checker.can_trade():
                    await asyncio.sleep(60)
                    continue
                    
                # 记录市场状态
                logger.info("=== 开始新一轮交易循环 ===")
                
                # 更新市场数据
                await data_manager.update_all_klines()
                
                # 执行数据清理
                await data_cleaner.cleanup()
                
                # 获取交易信号
                for symbol in config['symbols']:
                    signal = await strategy.get_trading_signal(symbol)
                    if signal and signal.get('should_trade', False):
                        # 执行交易
                        if signal['action'] == 'open':
                            await position_manager.open_position(
                                symbol,
                                signal.get('quantity', 0)
                            )
                        elif signal['action'] == 'close':
                            await position_manager.close_position(
                                symbol,
                                signal.get('quantity', 0)
                            )
                
                # 更新持仓状态
                positions = await position_manager.get_positions()
                for position in positions:
                    await position_manager.log_position_status(position)
                    
                # 等待下一个循环
                await asyncio.sleep(config.get('loop_interval', 60))
                
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
        logger.info("开始初始化交易系统...")
        
        # 加载配置
        config = load_config()
        
        # 初始化组件
        components = await initialize_components(config)
        
        # 运行交易循环
        await run_trading_loop(config, *components)
        
    except Exception as e:
        logger.error(f"程序运行时出错: {str(e)}")
        raise
    finally:
        logger.info("交易系统已关闭")

if __name__ == "__main__":
    asyncio.run(main())

