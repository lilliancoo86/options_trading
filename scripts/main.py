from trading.position_manager import DoomsdayPositionManager

from trading.risk_checker import RiskChecker

from trading.time_checker import TimeChecker

from trading.option_strategy import DoomsdayOptionStrategy

from config.config import TRADING_CONFIG, LOGGING_CONFIG, API_CONFIG

import logging

import asyncio

from typing import Dict, Any

import os

import sys

from pathlib import Path

from tabulate import tabulate

from datetime import datetime, timedelta

import pytz

import argparse

from longport.openapi import Config, QuoteContext, TradeContext

from trading.data_cleaner import DataCleaner



# 设置基础路径

BASE_DIR = Path('/home/options_trading')

LOG_DIR = BASE_DIR / 'logs'

CONFIG_DIR = BASE_DIR / 'config'



# 添加项目路径到Python路径

sys.path.append(str(BASE_DIR))



# 创建全局 logger

logger = None



def parse_args():

    """解析命令行参数"""

    parser = argparse.ArgumentParser(description='末日期权量化系统')

    parser.add_argument('--test', action='store_true', help='启用测试模式')

    parser.add_argument('--fake-time', type=str, help='测试模式下的模拟时间 (格式: YYYY-MM-DD HH:MM:SS)')

    return parser.parse_args()



def setup_logging():

    """设置日志"""

    global logger
    
    # 确保日志目录存在
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    
    logging.basicConfig(
        level=LOGGING_CONFIG['level'],
        format=LOGGING_CONFIG['format'],
        handlers=[
            logging.FileHandler(LOG_DIR / 'trading.log'),  # 保持原有路径
            logging.StreamHandler()
        ]
    )
    
    logger = logging.getLogger(__name__)
    return logger



async def run_strategy(strategy, position_manager, risk_checker, time_checker, logger):

    """运行交易策略"""

    try:

        # 1. 检查时间（最高优先级）

        need_close, reason = time_checker.check_force_close()

        if need_close:

            logger.warning(f"触发强制平仓: {reason}")

            await position_manager.close_all_positions(reason)

            return

            

        # 2. 检查市场状态

        if not time_checker.is_trading_time():

            logger.info("当前不在交易时间")

            return

            

        # 3. 获取市场数据

        market_data = await strategy.get_market_data()

        

        # 4. 检查市场风险

        risk_high, risk_reason = await risk_checker.check_market_risk(

            vix_level=market_data['vix'],

            daily_volatility=market_data['volatility']

        )

        if risk_high:

            logger.warning(f"市场风险过高: {risk_reason}")

            return

            

        # 5. 运行交易策略

        signals = await strategy.generate_trading_signals()

        if signals:

            for signal in signals:

                if position_manager.can_open_position(signal['symbol']):

                    await position_manager.open_position(

                        symbol=signal['symbol'],

                        volume=signal['volume'],

                        reason=signal['reason']

                    )

        

        # 6. 检查现有持仓

        positions = await position_manager.get_real_positions()

        if positions and positions.get("active"):

            for position in positions["active"]:

                # 检查持仓风险

                need_close, reason = await risk_checker.check_position_risk(position)

                if need_close:

                    await position_manager.close_position(

                        position['symbol'],

                        position['volume'],

                        reason

                    )

        

        # 7. 休眠间隔

        await asyncio.sleep(1)

        

    except Exception as e:

        logger.error(f"策略执行错误: {str(e)}")

        logger.exception("详细错误信息:")

        await asyncio.sleep(5)



async def run_cleanup(data_cleaner: DataCleaner):
    """运行数据清理任务"""
    try:
        # 每天凌晨2点运行清理任务
        while True:
            now = datetime.now(pytz.timezone('America/New_York'))
            if now.hour == 2 and now.minute == 0:
                await data_cleaner.cleanup()
            await asyncio.sleep(60)  # 每分钟检查一次
    except Exception as e:
        logger.error(f"运行清理任务时出错: {str(e)}")



async def main():

    """主程序入口"""

    try:

        # 初始化日志

        logger = setup_logging()

        

        # 解析命令行参数

        args = parse_args()

        

        # 确保数据目录存在

        data_dir = Path('/home/options_trading/data')

        data_dir.mkdir(parents=True, exist_ok=True)

        

        # 从环境变量加载 Longport 配置

        longport_config = Config.from_env()
        
        # 创建上下文
        quote_ctx = QuoteContext(longport_config)
        trade_ctx = TradeContext(longport_config)
        
        try:
            # 合并配置
            config = {
                **TRADING_CONFIG,
                'longport': {
                    'app_key': os.getenv('LONGPORT_APP_KEY'),
                    'app_secret': os.getenv('LONGPORT_APP_SECRET'),
                    'access_token': os.getenv('LONGPORT_ACCESS_TOKEN'),
                    'region': os.getenv('LONGPORT_REGION', 'cn')
                },
                'api': {
                    'quote_context': quote_ctx,
                    'trade_context': trade_ctx
                },
                'LOGGING_CONFIG': LOGGING_CONFIG,
                'DATA_CONFIG': {
                    'base_dir': '/home/options_trading/data',
                    'market_data_dir': '/home/options_trading/data/market_data',
                    'update_interval': 60,
                    'retention_days': 30,
                    'backup_enabled': True,
                    'compression': True
                },
                'test_mode': args.test
            }

            if args.test:
                logger.info("=== 运行在测试模式 ===")

            # 初始化组件，传入共享的上下文
            async with DoomsdayOptionStrategy(config, args.test) as strategy:
                async with DoomsdayPositionManager(config, args.test) as position_manager:
                    risk_checker = RiskChecker(config)
                    time_checker = TimeChecker(config, args.test)
                    
                    # 初始化数据清理器
                    data_cleaner = DataCleaner(config)
                    
                    # 启动清理任务
                    cleanup_task = asyncio.create_task(run_cleanup(data_cleaner))
                    
                    # 运行主循环
                    while True:
                        await run_strategy(
                            strategy=strategy,
                            position_manager=position_manager,
                            risk_checker=risk_checker,
                            time_checker=time_checker,
                            logger=logger
                        )
                        await asyncio.sleep(1)  # 添加适当的延迟
                    
        finally:
            # 确保关闭连接
            if hasattr(quote_ctx, 'close'):
                await quote_ctx.close()
            if hasattr(trade_ctx, 'close'):
                await trade_ctx.close()
                    
    except Exception as e:
        logger.error(f"程序运行出错: {str(e)}")
        logger.exception("详细错误信息:")
    finally:
        if 'cleanup_task' in locals():
            cleanup_task.cancel()



def is_trading_hours(current_time: datetime, test_mode: bool = False) -> bool:

    """检查是否在交易时间内"""

    try:

        # 测试模式下忽略时间限制

        if test_mode:

            return True

            

        # 获取当前时间的时分秒

        current = current_time.time()

        

        # 市场开盘和收盘时间

        market_open = datetime.strptime('09:30:00', '%H:%M:%S').time()

        market_close = datetime.strptime('16:00:00', '%H:%M:%S').time()

        

        # 检查是否在交易时间内

        return market_open <= current <= market_close

        

    except Exception as e:

        logger.error(f"检查交易时间出错: {str(e)}")

        return False



if __name__ == "__main__":

    asyncio.run(main()) 
