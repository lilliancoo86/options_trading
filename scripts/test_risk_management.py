import asyncio
from datetime import datetime, timedelta
from trading.position_manager import DoomsdayPositionManager
from config.config import TRADING_CONFIG
import logging

# 初始化日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def get_test_config():
    """获取测试配置"""
    test_config = TRADING_CONFIG.copy()
    
    # 修改为测试模式的配置
    test_config.update({
        # 趋势判断参数
        'trend_config': {
            'fast_length': 1,
            'slow_length': 5,
            'curve_length': 10,
            'trend_period': 5,
            'vwap_dev': 2.0
        },
        
        # 监控列表配置
        'watchlist': {
            'scan_interval': 5,
            'filters': {
                'min_volume': 1000,
                'min_price': 0.5,
                'max_price': 15.0,
                'min_delta': 0.3,
                'max_delta': 0.7,
                'min_days': 15,
                'max_days': 45,
            }
        },
        
        # 信号强度权重
        'signal_weights': {
            'volume_surge': 0.3,
            'price_trend': 0.3,
            'time_trend': 0.2,
            'option_greek': 0.2,
        },
        
        # 风险限制配置
        'risk_limits': {
            'option': {
                'stop_loss': {
                    'initial': 0.10,
                    'trailing': 0.07,
                },
                'take_profit': 0.50,
            },
            'volatility': {
                'max_vix': 40,
                'min_vix': 15
            }
        },
        
        # 组合风险限制
        'max_portfolio_delta': 2.0,
        'min_portfolio_theta': -0.3,
    })
    
    return test_config

async def test_market_close(position_manager):
    """测试收盘平仓"""
    logger.info("\n=== 测试收盘平仓 ===")
    
    # 模拟收盘前的持仓
    test_positions = {
        "active": [
            {
                "symbol": "TSLA250117C250000.US",
                "volume": 1,
                "cost_price": 10.0,
                "current_price": 12.0,
                "market_value": 1200.0,
                "delta": 0.5,
                "theta": -0.1
            },
            {
                "symbol": "AAPL250117C180000.US",
                "volume": 2,
                "cost_price": 5.0,
                "current_price": 5.5,
                "market_value": 1100.0,
                "delta": 0.4,
                "theta": -0.05
            }
        ]
    }
    
    # 执行收盘平仓
    await position_manager.close_all_positions_before_market_close()
    logger.info("收盘平仓测试完成")

async def test_risk_management(position_manager):
    """风控测试"""
    # 获取当前实际持仓
    positions = await position_manager.get_real_positions()
    if not positions or not positions.get("active"):
        logger.info("当前没有持仓，无法进行测试")
        return
        
    # 对每个持仓进行测试
    for position in positions["active"]:
        # 只测试期权持仓
        if not position_manager._is_option(position['symbol']):
            logger.info(f"跳过非期权持仓: {position['symbol']}")
            continue
        
        logger.info(f"\n开始测试持仓: {position['symbol']}")
        
        # 获取当前价格作为基准
        cost_price = float(position.get('cost_price', 0))
        current_price = float(position.get('current_price', cost_price))
        
        # 测试场景
        test_scenarios = [
            # 当前价格
            {
                "price": current_price,
                "desc": "当前价格",
                "price_trend": "normal",
                "time_trend": "neutral",
                "vix": 25
            },
            
            # 超强势上涨场景
            {
                "price": cost_price * 3.0,
                "desc": "超强势上涨 (200%) - 分时走强",
                "price_trend": "super_strong",
                "time_trend": "strong_up",
                "vix": 30
            },
            {
                "price": cost_price * 5.0,
                "desc": "超强势上涨 (400%) - 分时走强",
                "price_trend": "super_strong",
                "time_trend": "strong_up",
                "vix": 35
            },
            
            # 高位回撤场景
            {
                "price": cost_price * 6.3,
                "desc": "高位回撤 (10%)",
                "price_trend": "super_strong",
                "time_trend": "down",
                "peak": cost_price * 7.0,
                "vix": 38
            },
            
            # VIX过高场景
            {
                "price": cost_price * 1.2,
                "desc": "VIX过高",
                "price_trend": "normal",
                "time_trend": "up",
                "vix": 45
            },
            
            # VIX过低场景
            {
                "price": cost_price * 1.1,
                "desc": "VIX过低",
                "price_trend": "normal",
                "time_trend": "up",
                "vix": 12
            }
        ]
        
        # 运行测试场景
        position_copy = position.copy()
        
        for scenario in test_scenarios:
            logger.info(f"\n测试场景: {scenario['desc']}")
            
            # 设置最高价（如果有）
            if 'peak' in scenario:
                position_copy['peak_price'] = scenario['peak']
                position_copy['peak_pnl'] = (scenario['peak'] - cost_price) / cost_price * 100
            
            # 模拟价格历史数据以生成趋势
            trend_prices = []
            if scenario['time_trend'] == 'strong_up':
                trend_prices = [scenario['price'] * (1 - 0.03 * i) for i in range(5)]
            elif scenario['time_trend'] == 'up':
                trend_prices = [scenario['price'] * (1 - 0.02 * i) for i in range(5)]
            elif scenario['time_trend'] == 'down':
                trend_prices = [scenario['price'] * (1 + 0.02 * i) for i in range(5)]
            elif scenario['time_trend'] == 'strong_down':
                trend_prices = [scenario['price'] * (1 + 0.03 * i) for i in range(5)]
            else:
                trend_prices = [scenario['price']] * 5
            
            # 更新价格历史
            position_manager.price_history[position['symbol']] = trend_prices[::-1]
            
            # 测试风控
            result = await position_manager.test_risk_management(
                position_copy.copy(),
                scenario['price'],
                scenario['vix']
            )
            
            # 计算当前收益率
            pnl_pct = (scenario['price'] - cost_price) / cost_price * 100
            
            logger.info(f"测试结果: {'需要平仓' if result else '继续持仓'}")
            logger.info(f"价格趋势: {scenario['price_trend']}")
            logger.info(f"分时趋势: {scenario['time_trend']}")
            logger.info(f"当前收益: {pnl_pct:.1f}%")
            logger.info(f"VIX指数: {scenario['vix']}")
            if 'peak' in scenario:
                peak_pnl = (scenario['peak'] - cost_price) / cost_price * 100
                drawdown = (pnl_pct - peak_pnl) / peak_pnl * 100
                logger.info(f"从最高点回撤: {drawdown:.1f}%\n")
            else:
                logger.info("")
            
            await asyncio.sleep(1)
        
        # 分隔不同持仓的测试结果
        logger.info("=" * 80)

async def main():
    """主测试函数"""
    # 初始化持仓管理器（测试模式）
    test_config = get_test_config()
    position_manager = DoomsdayPositionManager(test_config, test_mode=True)
    
    # 运行风控测试
    await test_risk_management(position_manager)
    
    # 运行收盘平仓测试
    await test_market_close(position_manager)

if __name__ == "__main__":
    asyncio.run(main()) 