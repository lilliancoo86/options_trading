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

    logging.basicConfig(

        level=LOGGING_CONFIG['level'],

        format=LOGGING_CONFIG['format'],

        handlers=[

            logging.FileHandler(LOG_DIR / 'trading.log'),

            logging.StreamHandler()

        ]

    )

    logger = logging.getLogger(__name__)

    return logger



async def run_strategy(strategy, position_manager, risk_checker, time_checker, logger):

    """运行交易策略"""

    try:

        # 初始化策略

        await strategy.initialize()

        

        # 获取市场数据

        market_data = await strategy.get_market_data()

        

        # 检查交易条件

        if not await strategy.check_trading_conditions(market_data):

            logger.info("当前不满足交易条件")

            return

            

        # 获取交易信号

        signals = await strategy.generate_trading_signals()

        if not signals:

            return

            

        # 执行交易信号

        for signal in signals:

            # 检查持仓限制

            if not position_manager.can_open_position(signal['symbol']):

                continue

                

            # 执行开仓

            await position_manager.open_position(

                symbol=signal['symbol'],

                volume=signal['volume'],

                reason=signal['reason']

            )

        

        # 检查现有持仓

        positions = await position_manager.get_real_positions()

        if positions and positions.get("active"):

            for position in positions["active"]:

                # 检查风险状态

                await position_manager.check_position_risk(position)

                

                # 检查是否需要收盘平仓

                await position_manager.check_market_close(position)

        

        # 添加休眠时间

        await asyncio.sleep(1)

        

    except Exception as e:

        logger.error(f"策略执行错误: {str(e)}")

        logger.exception("详细错误信息:")

        await asyncio.sleep(5)



async def main():

    try:

        # 初始化日志

        setup_logging()

        logger = logging.getLogger(__name__)

        

        # 解析命令行参数

        args = parse_args()

        

        # 初始化持仓管理器

        async with DoomsdayPositionManager(config=TRADING_CONFIG, test_mode=args.test) as position_manager:

            while True:

                try:

                    # 打印交易状态

                    await position_manager.print_trading_status()

                    

                    # 检查是否需要强制平仓

                    current_time = datetime.now(pytz.timezone('America/New_York'))

                    if await position_manager.check_force_close(current_time):

                        logger.warning("触发强制平仓条件")

                        # 执行强制平仓逻辑

                        positions = await position_manager.get_real_positions()

                        if positions and positions.get("active"):

                            for pos in positions["active"]:

                                await position_manager.close_position(

                                    pos["symbol"],

                                    int(pos["volume"]),

                                    "强制平仓"

                                )

                    

                    # 检查风险状态

                    await position_manager.check_position_risks()

                    

                    await asyncio.sleep(10)  # 每10秒检查一次

                    

                except Exception as e:

                    logger.error(f"主循环出错: {str(e)}")

                    logger.exception("详细错误信息:")

                    await asyncio.sleep(5)

                    

    except Exception as e:

        logger.error(f"程序运行出错: {str(e)}")

        logger.exception("详细错误信息:")



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
