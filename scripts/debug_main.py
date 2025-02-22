"""
调试入口程序
用于在VPS中进行断点调试
"""
import asyncio
import logging
import sys
from datetime import datetime
from dotenv import load_dotenv
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, Any
import time
import pdb

# 添加项目根目录到Python路径
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

# 导入配置
from config.config import (
    TRADING_CONFIG, API_CONFIG, LOGGING_CONFIG,
    DATA_CONFIG, CLEANUP_CONFIG, BASE_DIR,
    DATA_DIR, LOG_DIR, CONFIG_DIR
)

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
        log_file = LOG_DIR / f"debug_{current_date}.log"

        # 创建日志处理器
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,  # 10MB
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
        logger.setLevel(logging.DEBUG)  # 调试模式使用 DEBUG 级别
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

        return logger

    except Exception as e:
        print(f"配置日志系统时出错: {str(e)}")
        raise

def load_config() -> Dict[str, Any]:
    """加载配置"""
    return CONFIG

async def initialize_components(config: Dict[str, Any]) -> Dict[str, Any]:
    """初始化组件"""
    components = {}
    try:
        # 初始化数据管理器
        logger.info("\n=== 初始化数据管理器 ===")
        data_manager = DataManager(config.get('TRADING_CONFIG', {}))
        await data_manager.async_init()
        components['data_manager'] = data_manager
        pdb.set_trace()  # 调试点：检查数据管理器初始化状态

        # 初始化数据清理器
        logger.info("\n=== 初始化数据清理器 ===")
        data_cleaner = DataCleaner(config.get('DATA_CONFIG', {}))
        components['data_cleaner'] = data_cleaner
        pdb.set_trace()  # 调试点：检查数据清理器配置

        # 初始化时间检查器
        logger.info("\n=== 初始化时间检查器 ===")
        time_checker = TimeChecker(config.get('TRADING_CONFIG', {}))
        await time_checker.async_init()
        components['time_checker'] = time_checker
        pdb.set_trace()  # 调试点：检查时间检查器状态

        # 初始化策略
        logger.info("\n=== 初始化交易策略 ===")
        strategy = DoomsdayOptionStrategy(config.get('TRADING_CONFIG', {}), data_manager)
        await strategy.async_init()
        components['strategy'] = strategy
        pdb.set_trace()  # 调试点：检查策略初始化状态

        # 初始化风险检查器
        logger.info("\n=== 初始化风险检查器 ===")
        risk_checker = RiskChecker(config.get('TRADING_CONFIG', {}), strategy, time_checker)
        await risk_checker.async_init()
        components['risk_checker'] = risk_checker
        pdb.set_trace()  # 调试点：检查风险检查器配置

        # 初始化持仓管理器
        logger.info("\n=== 初始化持仓管理器 ===")
        position_manager = DoomsdayPositionManager(config.get('TRADING_CONFIG', {}), data_manager)
        await position_manager.async_init()
        components['position_manager'] = position_manager
        pdb.set_trace()  # 调试点：检查持仓管理器状态

        return components

    except Exception as e:
        logger.error(f"初始化组件时出错: {str(e)}")
        raise

async def debug_trading_loop(
        config: Dict[str, Any],
        data_manager: DataManager,
        data_cleaner: DataCleaner,
        time_checker: TimeChecker,
        risk_checker: RiskChecker,
        position_manager: DoomsdayPositionManager,
        strategy: DoomsdayOptionStrategy
) -> None:
    """调试交易循环"""
    try:
        logger.info("\n=== 开始调试交易循环 ===")
        
        # 测试市场数据更新
        logger.info("\n测试市场数据更新:")
        await data_manager.update_all_klines()
        pdb.set_trace()  # 调试点：检查市场数据
        
        # 测试数据清理
        logger.info("\n测试数据清理:")
        await data_cleaner.cleanup()
        pdb.set_trace()  # 调试点：检查数据清理结果
        
        # 测试交易时间检查
        logger.info("\n测试交易时间检查:")
        is_trading_time = await time_checker.check_market_time()
        logger.info(f"当前是否为交易时间: {is_trading_time}")
        pdb.set_trace()  # 调试点：检查交易时间状态
        
        # 测试持仓获取
        logger.info("\n测试持仓获取:")
        positions = await position_manager.get_positions()
        logger.info(f"当前持仓: {positions}")
        pdb.set_trace()  # 调试点：检查持仓信息

        # 测试信号生成和风险检查
        for symbol in data_manager.symbols:
            logger.info(f"\n测试 {symbol} 的交易信号和风险检查:")
            try:
                signal = await strategy.generate_signal(symbol)
                if signal:
                    logger.info(f"生成的信号: {signal}")
                    risk_result = await risk_checker.check_risk(symbol, signal)
                    logger.info(f"风险检查结果: {risk_result}")
                else:
                    logger.info(f"未能为 {symbol} 生成交易信号")
                pdb.set_trace()  # 调试点：检查每个标的的信号和风险
            except Exception as e:
                logger.error(f"处理 {symbol} 时出错: {str(e)}")
                continue

        logger.info("\n=== 调试交易循环完成 ===")

    except Exception as e:
        logger.error(f"调试交易循环时出错: {str(e)}")
        raise

async def main():
    """主调试入口"""
    try:
        # 加载环境变量
        load_dotenv()

        # 设置日志系统
        global logger
        logger = setup_logging()
        logger.info("**提示**: 开始初始化调试环境...")

        # 加载配置
        config = load_config()

        # 初始化组件
        components = await initialize_components(config)

        # 运行调试循环
        await debug_trading_loop(config, **components)

    except Exception as e:
        logger.error(f"调试程序运行出错: {str(e)}")
        raise
    finally:
        logger.info("调试程序已关闭")

if __name__ == "__main__":
    asyncio.run(main()) 