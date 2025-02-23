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
import pytz

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
        position_manager = DoomsdayPositionManager(
            config=config.get('TRADING_CONFIG', {}),
            data_manager=data_manager,
            option_strategy=strategy  # 添加策略实例
        )
        await position_manager.async_init()
        components['position_manager'] = position_manager
        pdb.set_trace()  # 调试点：检查持仓管理器状态

        # 更新策略的position_manager引用
        strategy.position_manager = position_manager

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

        # 详细测试每个交易标的的策略信号和开仓流程
        for symbol in data_manager.symbols:
            logger.info(f"\n{'='*20} 测试 {symbol} 的完整交易流程 {'='*20}")
            
            try:
                # 1. 获取市场数据
                logger.info(f"\n1. 获取 {symbol} 的市场数据:")
                market_data = await data_manager.get_latest_quote(symbol)
                logger.info(f"市场数据: {market_data}")
                pdb.set_trace()  # 调试点：检查市场数据
                
                # 2. 生成策略信号
                logger.info(f"\n2. 生成 {symbol} 的策略信号:")
                signal = await strategy.generate_signal(symbol)
                if signal:
                    logger.info(f"原始策略信号: {signal}")
                    # 详细输出信号组成部分
                    logger.info("信号详情:")
                    logger.info(f"- 交易方向: {signal.get('action', 'unknown')}")
                    logger.info(f"- 建议数量: {signal.get('quantity', 0)}")
                    logger.info(f"- 目标价格: {signal.get('price', 0)}")
                    logger.info(f"- 信号强度: {signal.get('strength', 0)}")
                    logger.info(f"- 止损价格: {signal.get('stop_loss', 0)}")
                    logger.info(f"- 止盈价格: {signal.get('take_profit', 0)}")
                #else:
                #    logger.info(f"未能为 {symbol} 生成交易信号")
                pdb.set_trace()  # 调试点：检查策略信号详情

                if signal:
                    # 3. 风险检查
                    logger.info(f"\n3. 执行 {symbol} 的风险检查:")
                    risk_result = await risk_checker.check_risk(symbol, signal)
                    logger.info(f"风险检查结果: {risk_result}")
                    if not risk_result:
                        logger.warning("风险检查未通过，跳过交易")
                    pdb.set_trace()  # 调试点：检查风险评估结果

                    # 4. 模拟开仓操作
                    if risk_result:
                        logger.info(f"\n4. 模拟 {symbol} 的开仓操作:")
                        
                        # 获取期权合约信息
                        logger.info("选择期权合约...")
                        contract_info = await strategy.select_option_contract(symbol)
                        if not contract_info:
                            logger.warning(f"未找到合适的期权合约: {symbol}")
                            pdb.set_trace()  # 调试点：检查期权合约选择失败原因
                            continue
                            
                        # 显示期权合约详情
                        logger.info("期权合约信息:")
                        logger.info(f"- 合约代码: {contract_info.get('symbol', 'unknown')}")
                        logger.info(f"- 合约类型: {contract_info.get('type', 'unknown')}")  # call/put
                        logger.info(f"- 执行价格: {contract_info.get('strike_price', 0)}")
                        logger.info(f"- 到期日期: {contract_info.get('expiry_date', 'unknown')}")
                        logger.info(f"- 隐含波动率: {contract_info.get('implied_volatility', 0):.2f}%")
                        logger.info(f"- Delta值: {contract_info.get('delta', 0):.3f}")
                        logger.info(f"- Theta值: {contract_info.get('theta', 0):.3f}")
                        logger.info(f"- Gamma值: {contract_info.get('gamma', 0):.3f}")
                        
                        # 显示开仓参数
                        logger.info("\n开仓参数:")
                        logger.info(f"- 标的股票: {symbol}")
                        logger.info(f"- 期权合约: {contract_info.get('symbol', 'unknown')}")
                        logger.info(f"- 交易数量: {signal.get('quantity', 0)}")
                        logger.info(f"- 限价: ${signal.get('price', 0):.2f}")
                        logger.info(f"- 交易方向: {signal.get('action', 'unknown')}")
                        logger.info(f"- 止损价格: ${signal.get('stop_loss', 0):.2f}")
                        logger.info(f"- 止盈价格: ${signal.get('take_profit', 0):.2f}")
                        
                        pdb.set_trace()  # 调试点：检查开仓前的合约和参数
                        
                        # 执行模拟开仓
                        try:
                            open_result = await position_manager.open_position(
                                contract_info.get('symbol'),  # 使用期权合约代码
                                signal.get('quantity', 0),
                                signal.get('price', 0)
                            )
                            logger.info(f"开仓结果: {'成功' if open_result else '失败'}")
                            
                            if open_result:
                                logger.info("开仓成功详情:")
                                logger.info(f"- 成交价格: ${signal.get('price', 0):.2f}")
                                logger.info(f"- 成交数量: {signal.get('quantity', 0)}")
                                logger.info(f"- 交易方向: {signal.get('action', 'unknown')}")
                                logger.info(f"- 交易时间: {datetime.now(pytz.timezone('America/New_York'))}")
                            
                        except Exception as e:
                            logger.error(f"开仓操作失败: {str(e)}")
                        
                        pdb.set_trace()  # 调试点：检查开仓结果
                        
                        # 5. 检查更新后的持仓
                        if open_result:
                            logger.info("\n5. 检查更新后的持仓状态:")
                            updated_positions = await position_manager.get_positions()
                            logger.info(f"更新后的持仓: {updated_positions}")
                            pdb.set_trace()  # 调试点：检查更新后的持仓状态

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