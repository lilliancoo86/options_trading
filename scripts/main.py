from trading.position_manager import DoomsdayPositionManager
from trading.risk_checker import RiskChecker
from trading.time_checker import TimeChecker
from trading.option_strategy import DoomsdayOptionStrategy
from config.config import TRADING_CONFIG, LOGGING_CONFIG, API_CONFIG, DATA_CONFIG
from trading.data_cleaner import DataCleaner
from trading.data_manager import DataManager

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
import time

# 设置基础路径
BASE_DIR = Path('/home/options_trading')
LOG_DIR = BASE_DIR / 'logs'
CONFIG_DIR = BASE_DIR / 'config'

# 添加项目路径到Python路径
sys.path.append(str(BASE_DIR))

# 创建全局 logger
logger = None

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
    logger.info("日志系统初始化成功")    
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
            vix_level=market_data.get('vix', 0),
            daily_volatility=market_data.get('volatility', 0)
        )
        if risk_high:
            logger.warning(f"市场风险过高: {risk_reason}")
            return
            
        # 5. 运行交易策略
        for symbol in strategy.symbols:
            try:
                signal = await strategy.generate_signal(symbol)
                
                if signal is None:
                    continue
                    
                # 确保signal是字典类型
                if isinstance(signal, dict) and "symbol" in signal:
                    # 检查是否可以开仓
                    can_open = await position_manager.can_open_position(signal["symbol"])
                    if can_open:
                        await position_manager.open_position(
                            symbol=signal["symbol"],
                            volume=1,  # 默认交易1张
                            reason=f"Signal: {signal['action']} ({signal['trend']})"
                        )
                else:
                    logger.warning(f"无效的信号格式: {signal}")
            except Exception as e:
                logger.error(f"处理 {symbol} 的信号时出错: {str(e)}")
                continue
        
        # 6. 检查现有持仓
        positions = await position_manager.get_positions()
        for position in positions:
            # 检查持仓风险
            need_close, reason, ratio = await risk_checker.check_position_risk(position)
            if need_close:
                await position_manager.close_position(
                    symbol=position['symbol'],
                    ratio=ratio,
                    reason=reason
                )
                
    except Exception as e:
        logger.error(f"运行策略时出错: {str(e)}")

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
    """主函数"""
    try:
        # 设置日志
        logger = setup_logging()
        logger.info("=== 系统启动 ===")
        
        # 确保数据目录存在
        data_dir = Path('/home/options_trading/data')
        data_dir.mkdir(parents=True, exist_ok=True)
        
        # 创建 LongPort 配置
        try:
            longport_config = Config(
                app_key=API_CONFIG['app_key'],
                app_secret=API_CONFIG['app_secret'],
                access_token=API_CONFIG['access_token']
            )
            logger.info("LongPort配置初始化成功")
        except Exception as e:
            logger.error(f"LongPort配置初始化失败: {str(e)}")
            raise

        # 初始化组件
        components = {}
        try:
            # 创建基础配置
            base_config = {
                'symbols': TRADING_CONFIG.get('symbols', []),  # 确保包含交易标的
                'longport_config': longport_config,
                'DATA_CONFIG': DATA_CONFIG,
                'LOGGING_CONFIG': LOGGING_CONFIG
            }

            # 初始化数据管理器
            data_manager = DataManager(base_config)
            await data_manager.async_init()
            components['data_manager'] = data_manager
            
            # 初始化时间检查器
            time_checker = TimeChecker(config=TRADING_CONFIG)
            await time_checker.async_init()
            components['time_checker'] = time_checker
            
            # 初始化策略
            strategy = DoomsdayOptionStrategy(
                config=base_config,
                data_manager=data_manager
            )
            await strategy.async_init()
            components['strategy'] = strategy
            
            # 初始化持仓管理器
            position_manager = DoomsdayPositionManager(
                base_config,
                data_manager
            )
            await position_manager.async_init()
            components['position_manager'] = position_manager
            
            # 初始化风险检查器
            risk_checker = RiskChecker(
                config=base_config,
                option_strategy=strategy,  # 传入策略实例
                time_checker=time_checker  # 传入时间检查器实例
            )
            await risk_checker.async_init()
            components['risk_checker'] = risk_checker
            
            # 初始化数据清理器
            data_cleaner = DataCleaner(base_config)
            await data_cleaner.async_init()
            components['data_cleaner'] = data_cleaner
            
            # 运行主循环
            try:
                # 创建任务
                trading_task = asyncio.create_task(
                    run_trading_loop(
                        config=TRADING_CONFIG,
                        time_checker=time_checker,
                        data_manager=data_manager,
                        strategy=strategy,
                        position_manager=position_manager,
                        risk_checker=risk_checker,
                        data_cleaner=data_cleaner
                    )
                )
                
                # 等待任务完成或被取消
                await trading_task
                
            except asyncio.CancelledError:
                logger.info("主任务被取消")
            except Exception as e:
                logger.error(f"交易循环出错: {str(e)}")
                logger.exception("详细错误信息:")
                raise
            finally:
                # 关闭所有连接
                for name, component in components.items():
                    try:
                        if hasattr(component, 'close'):
                            await component.close()
                            logger.info(f"已关闭 {name}")
                    except Exception as e:
                        logger.error(f"关闭 {name} 时出错: {str(e)}")
                        
        except Exception as e:
            logger.error(f"组件初始化失败: {str(e)}")
            logger.exception("详细错误信息:")
            raise
            
    except Exception as e:
        if logger:
            logger.error(f"初始化出错: {str(e)}")
            logger.exception("详细错误信息:")
        else:
            print(f"初始化出错: {str(e)}")
        sys.exit(1)

async def run_trading_loop(
    config: Dict[str, Any],
    time_checker,
    data_manager,
    strategy,
    position_manager,
    risk_checker,
    data_cleaner
) -> None:
    """运行交易主循环"""
    logger = logging.getLogger(__name__)
    
    try:
        while True:
            try:
                # 检查是否在交易时间
                if not time_checker.check_trading_time():
                    time_checker.record_status()
                    logger.debug("不在交易时间")
                    await asyncio.sleep(60)
                    continue
                
                # 执行数据清理
                await data_cleaner.cleanup()
                
                # 记录市场状态
                logger.debug("=== 开始新一轮交易循环 ===")
                
                # 更新市场数据
                await data_manager.update_all_klines()
                
                # 记录持仓状态
                positions_data = await position_manager.get_positions()
                if positions_data and isinstance(positions_data, dict):
                    active_positions = positions_data.get('active', [])
                    total_positions = len(active_positions)
                    logger.info(f"当前持仓数量: {total_positions}")
                    
                    # 检查持仓风险
                    for position in active_positions:
                        try:
                            if isinstance(position, dict):
                                risk_checker.log_risk_status(position)
                                need_close, reason, ratio = await risk_checker.check_position_risk(
                                    position,
                                    await data_manager.get_market_data()
                                )
                                
                                if need_close:
                                    await position_manager.close_position(
                                        symbol=position['symbol'],
                                        ratio=ratio,
                                        reason=reason
                                    )
                        except Exception as e:
                            logger.error(f"检查持仓风险时出错 ({position.get('symbol', 'unknown')}): {str(e)}")
                            continue
                
                # 运行策略
                for symbol in config['symbols']:
                    try:
                        # 分析趋势
                        signal = await strategy.analyze_stock_trend(symbol)
                        if not signal:
                            continue
                        
                        # 检查是否可以开仓
                        if await position_manager.can_open_position(symbol):
                            # 选择合适的期权合约
                            contract = await strategy.select_option_contract(
                                symbol,
                                signal['trend']
                            )
                            
                            if contract:
                                # 执行交易
                                await position_manager.execute_trade(
                                    symbol=contract,
                                    direction=signal['signal'],
                                    reason=f"趋势信号: {signal['trend']}"
                                )
                    except Exception as e:
                        logger.error(f"处理 {symbol} 时出错: {str(e)}")
                        continue
                
                await asyncio.sleep(1)
                
            except asyncio.CancelledError:
                logger.info("收到取消信号，准备退出...")
                break
            except Exception as e:
                logger.error(f"交易循环执行出错: {str(e)}")
                logger.error("详细错误信息:", exc_info=True)
                await asyncio.sleep(5)
                
    except KeyboardInterrupt:
        logger.info("收到中断信号，准备退出...")
    finally:
        logger.info("交易循环结束")

if __name__ == "__main__":
    asyncio.run(main()) 
    
    