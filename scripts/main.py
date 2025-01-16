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
        await strategy.initialize()
        market_data = await strategy.get_market_data()
        
        # 获取 VIX 值和风险限制
        vix_level = market_data['vix']
        risk_limits = TRADING_CONFIG.get('risk_limits', {})
        volatility_limits = risk_limits.get('volatility', {})
        min_vix = volatility_limits.get('min_vix', 15)
        max_vix = volatility_limits.get('max_vix', 40)
        
        # 检查每个标的的交易条件
        trading_conditions = []
        for quote in market_data['quotes']:
            if quote.symbol == "VIX.US":
                continue
                
            # 计算日内波动率
            daily_volatility = (quote.high - quote.low) / quote.open * 100
            # 判断是否满足所有条件
            all_conditions_met = (
                min_vix <= vix_level <= max_vix and
                time_checker.is_trading_time() and
                daily_volatility <= volatility_limits.get('max_daily_volatility', 3) * 100
            )
            
            conditions = {
                "标的": quote.symbol,
                "VIX条件": "满足" if min_vix <= vix_level <= max_vix else "不满足",
                "交易时间": "是" if time_checker.is_trading_time() else "否",
                "波动率": f"{daily_volatility:.2f}%",
                "状态": "可交易" if all_conditions_met else "禁止交易"
            }
            trading_conditions.append(conditions)
        
        # 获取当前持仓状况
        positions = position_manager.get_all_positions()
        position_status = []
        for symbol, pos in positions.items():
            status = {
                "标的": symbol,
                "持仓量": pos['quantity'],
                "持仓价": f"{pos['entry_price']:.2f}",
                "现价": f"{pos['current_price']:.2f}",
                "盈亏": f"{pos['pnl']:.2f}",
                "持仓时间": f"{pos['holding_time'].total_seconds()/3600:.1f}小时"
            }
            position_status.append(status)
        
        # 每5分钟打印一次完整状态
        current_time = datetime.now()
        if not hasattr(run_strategy, '_last_status_log') or \
           (current_time - run_strategy._last_status_log).seconds >= 300:
            
            logger.info("\n=== 交易系统状态 ===")
            logger.info(f"VIX指数: {vix_level:.2f} (限制范围: {min_vix}-{max_vix})")
            
            logger.info("\n交易条件状态:")
            if trading_conditions:
                table = tabulate(
                    trading_conditions,
                    headers="keys",
                    tablefmt="grid",
                    numalign="right"
                )
                logger.info(f"\n{table}")
            
            logger.info("\n当前持仓状态:")
            if position_status:
                table = tabulate(
                    position_status,
                    headers="keys",
                    tablefmt="grid",
                    numalign="right"
                )
                logger.info(f"\n{table}")
            else:
                logger.info("当前无持仓")
            
            run_strategy._last_status_log = current_time
        
        # 检查市场条件
        market_condition = risk_checker.check_market_condition(
            vix_level,
            time_checker.current_time_str()
        )
        
        # 只在状态变化时记录日志
        if not hasattr(run_strategy, '_last_market_condition') or \
           run_strategy._last_market_condition != market_condition:
            if not market_condition:
                logger.warning("市场条件不满足交易要求")
            else:
                logger.info("市场条件满足交易要求")
            run_strategy._last_market_condition = market_condition
            
        if not market_condition:
            await asyncio.sleep(60)
            return
        
        # 生成交易信号
        signals = await strategy.generate_signals(market_data)
        
        # 执行交易信号
        if signals:
            await strategy.execute_signals(signals)
        
        # 处理交易信号
        for signal in signals:
            # 检查是否可以开仓
            if not position_manager.can_open_position(
                signal['symbol'],
                vix_level  # 使用提取的 VIX 值
            ):
                continue
            
            # 计算仓位大小
            position_size = strategy.calculate_position_size(signal)
            
            # 生成订单
            order = {
                'symbol': signal['symbol'],
                'quantity': position_size,
                'price': signal['price'],
                'volatility': signal.get('volatility', 0.2),
                'delta': signal.get('delta', 0),
                'theta': signal.get('theta', 0)
            }
            
            # 开仓
            if position_manager.open_position(order):
                logger.info(f"开仓成功: {signal['symbol']}, 数量: {position_size}")
        
        # 更新现有持仓
        active_positions = position_manager.get_all_positions()
        for symbol in active_positions:
            current_price = await strategy.get_current_price(symbol)
            
            # 检查是否需要平仓
            if position_manager.should_close_position(symbol, current_price):
                close_info = position_manager.close_position(symbol, current_price)
                logger.info(f"平仓触发: {symbol}, 盈亏: {close_info['pnl']}")
        
        # 添加强制休眠时间
        await asyncio.sleep(1)  # 至少间隔1秒
        
    except Exception as e:
        logger.error(f"策略执行错误: {str(e)}")
        await asyncio.sleep(5)
        # 如果发生错误，检查是否需要平仓
        if position_manager and position_manager.get_all_positions():
            logger.warning("发生错误，尝试平仓所有持仓")
            await strategy.close()  # 添加关闭连接的调用
            position_manager.force_close_all()

async def main():
    global logger
    logger = setup_logging()
    position_manager = None
    strategy = None
    
    try:
        # 解析命令行参数
        args = parse_args()
        
        # 获取当前时间（美东时间）
        ny_tz = pytz.timezone('America/New_York')
        if args.test and args.fake_time:
            current_time = datetime.strptime(args.fake_time, '%Y-%m-%d %H:%M:%S')
            current_time = ny_tz.localize(current_time)
            logger.info(f"测试模式: 使用模拟时间 {current_time}")
        else:
            current_time = datetime.now(ny_tz)
        
        # 检查是否在交易时间内
        if not is_trading_hours(current_time, args.test):
            if not args.test:
                logger.info("非交易时间")
                return
            else:
                logger.info("测试模式: 忽略交易时间限制")
        
        # 初始化交易组件
        logger.info("初始化交易组件...")
        
        # 初始化时间检查器
        time_checker = TimeChecker(
            market_open=TRADING_CONFIG['market_open'],
            market_close=TRADING_CONFIG['market_close'],
            force_close_time=TRADING_CONFIG['force_close_time'],
            test_mode=args.test
        )
        
        risk_checker = RiskChecker(TRADING_CONFIG)
        position_manager = DoomsdayPositionManager(TRADING_CONFIG)
        strategy = DoomsdayOptionStrategy(TRADING_CONFIG, API_CONFIG)
        
        logger.info(f"末日期权量化系统启动... {'[测试模式]' if args.test else ''}")
        
        # 运行策略直到收盘
        while is_trading_hours(datetime.now(ny_tz), args.test):
            await run_strategy(strategy, position_manager, risk_checker, time_checker, logger)
            await asyncio.sleep(5)
        
        logger.info("交易日结束，系统关闭")
        
    except Exception as e:
        logger.error(f"系统运行错误: {str(e)}")
        if logger.isEnabledFor(logging.DEBUG):
            logger.exception("详细错误信息:")
    finally:
        if position_manager is not None and position_manager.get_all_positions():
            logger.info("系统关闭前清仓")
            if strategy:
                await strategy.close()
            position_manager.force_close_all()

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