"""
末日期权系统 - 日内交易策略模块
"""
from typing import Dict, List, Any, Optional
import logging
from datetime import datetime, timedelta
import pytz
from decimal import Decimal
import asyncio
from longport.openapi import (
    TradeContext, 
    QuoteContext, 
    SubType, 
    OrderType, 
    OrderSide,
    TimeInForceType,
    Config
)
import os
import json

class DoomsdayOptionStrategy:
    def __init__(self, config: Dict[str, Any]):
        """初始化策略"""
        self.logger = logging.getLogger(__name__)
        self.config = config
        self.tz = pytz.timezone('America/New_York')
        
        # 初始化交易标的
        self.symbols = ["TSLL.US", "NVDA.US", "AAPL.US"]
        
        # 简化的风险控制参数
        self.risk_limits = {
            'option': {
                'stop_loss': -10.0,  # 期权固定10%止损
                'take_profit': None  # 期权不设固定止盈
            },
            'stock': {
                'stop_loss': -3.0,   # 股票固定3%止损
                'take_profit': 5.0    # 股票固定5%止盈
            }
        }
        
        # 添加收盘平仓时间设置
        self.market_close = {
            'force_close_time': '15:45',  # 收盘前15分钟强制平仓
            'warning_time': '15:40'       # 收盘前20分钟发出警告
        }
        
        # 初始化 Longport 配置
        try:
            self.longport_config = Config(
                app_key=config['longport']['app_key'],
                app_secret=config['longport']['app_secret'],
                access_token=config['longport']['access_token']
            )
            self.logger.info("Longport配置初始化成功")
        except Exception as e:
            self.logger.error(f"Longport配置初始化失败: {str(e)}")
            raise
        
        # 初始化交易和行情上下文
        self.quote_ctx = QuoteContext(self.longport_config)
        self.trade_ctx = TradeContext(self.longport_config)
        
        # 添加订阅类型
        self.sub_types = [
            SubType.Quote,              # 报价
            SubType.Trade,              # 成交
            SubType.Depth,              # 深度
            SubType.Greeks,             # 期权希腊字母
        ]
        
        # 缓存数据
        self.price_cache = {
            symbol: {
                'close': [],
                'volume': [],
                'high': [],
                'low': []
            } for symbol in self.symbols
        }
        
        # 持仓管理
        self.positions = {}             # 当前持仓
        
        # 添加趋势判断参数
        self.trend_params = {
            'fast_length': 1,      # 快线周期
            'slow_length': 5,      # 慢线周期
            'curve_length': 10,    # 曲线周期
            'trend_period': 5,     # 趋势判断周期
            'vwap_dev': 2.0       # VWAP通道宽度
        }
        
        # 缓存历史数据
        self.price_history = {}
        self.vwap_history = {}

    async def check_market_close(self, position: Dict[str, Any]) -> bool:
        """检查是否需要收盘平仓"""
        try:
            current_time = datetime.now(self.tz).strftime('%H:%M')
            
            # 收盘前警告
            if current_time >= self.market_close['warning_time']:
                self.logger.warning(f"接近收盘时间，准备平仓: {position['symbol']}")
            
            # 强制平仓检查
            if current_time >= self.market_close['force_close_time']:
                self.logger.warning(
                    f"收盘前强制平仓:\n"
                    f"  标的: {position['symbol']}\n"
                    f"  当前时间: {current_time}\n"
                    f"  平仓类型: 收盘平仓"
                )
                await self._execute_market_close(position)
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"检查收盘平仓时出错: {str(e)}")
            return False

    async def _execute_market_close(self, position: dict):
        """执行收盘平仓"""
        try:
            symbol = position["symbol"]
            volume = abs(position["volume"])
            
            self.logger.warning(
                f"执行收盘平仓:\n"
                f"  标的: {symbol}\n"
                f"  数量: {volume}\n"
                f"  成本价: ${position['cost_price']:.2f}\n"
                f"  现价: ${position['current_price']:.2f}\n"
                f"  平仓原因: 收盘前强制平仓"
            )
            
            # 执行市价单平仓
            order = await self.trade_ctx.submit_order(
                symbol=symbol,
                order_type=OrderType.MO,  # 使用市价单
                side=OrderSide.SELL if position["volume"] > 0 else OrderSide.BUY,
                submitted_quantity=volume,
                time_in_force=TimeInForceType.DAY,
                remark="Market Close"
            )
            
            self.logger.info(f"收盘平仓订单已提交 - 订单号: {order.order_id}")
            
            # 等待订单状态更新
            await asyncio.sleep(1)
            order_status = await self.trade_ctx.get_order(order.order_id)
            self.logger.info(f"收盘平仓订单状态: {order_status.status}")
            
        except Exception as e:
            self.logger.error(f"执行收盘平仓时出错: {str(e)}")
            self.logger.exception("详细错误信息:")

    async def check_position_risk(self, position: Dict[str, Any]) -> bool:
        """检查持仓风险"""
        try:
            # 首先检查是否需要收盘平仓（最高优先级）
            if await self.check_market_close(position):
                return True
            
            # 分析趋势
            trend_analysis = await self.analyze_trend(position['symbol'])
            
            # 根据趋势信号处理
            if trend_analysis['signal'] == 'close':
                self.logger.warning(f"趋势信号触发平仓: {position['symbol']}")
                await self._execute_market_close(position)
                return True
            elif trend_analysis['signal'] == 'reduce':
                # 可以添加减仓逻辑
                pass
            elif trend_analysis['signal'] == 'add':
                # 可以添加加仓逻辑
                pass
            
            # 继续检查止损条件
            current_price = float(position.get('current_price', 0))
            cost_price = float(position.get('cost_price', 0))
            if cost_price == 0:
                return False
                
            pnl_pct = (current_price - cost_price) / cost_price * 100
            is_option = self._is_option(position['symbol'])
            limits = self.risk_limits['option'] if is_option else self.risk_limits['stock']
            
            # 检查止损条件
            if limits['stop_loss'] is not None and pnl_pct <= limits['stop_loss']:
                self.logger.warning(f"触发固定止损: 当前亏损 {pnl_pct:.1f}% <= {limits['stop_loss']}%")
                await self._execute_stop_loss(position)
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"检查持仓风险时出错: {str(e)}")
            return False

    def _is_option(self, symbol: str) -> bool:
        """检查是否为期权"""
        return any(x in symbol for x in ['C', 'P'])

    async def _execute_stop_loss(self, position: dict):
        """执行止损"""
        try:
            symbol = position["symbol"]
            volume = abs(position["volume"])
            
            self.logger.warning(
                f"执行止损:\n"
                f"  标的: {symbol}\n"
                f"  数量: {volume}\n"
                f"  成本价: ${position['cost_price']:.2f}\n"
                f"  现价: ${position['current_price']:.2f}\n"
                f"  止损类型: 固定止损"
            )
            
            # 执行市价单平仓
            order = await self.trade_ctx.submit_order(
                symbol=symbol,
                order_type=OrderType.MO,  # 使用市价单
                side=OrderSide.SELL if position["volume"] > 0 else OrderSide.BUY,
                submitted_quantity=volume,
                time_in_force=TimeInForceType.DAY,
                remark="Fixed Stop Loss"
            )
            
            self.logger.info(f"止损订单已提交 - 订单号: {order.order_id}")
            
            # 等待订单状态更新
            await asyncio.sleep(1)
            order_status = await self.trade_ctx.get_order(order.order_id)
            self.logger.info(f"止损订单状态: {order_status.status}")
            
        except Exception as e:
            self.logger.error(f"执行止损时出错: {str(e)}")
            self.logger.exception("详细错误信息:")

    async def _execute_take_profit(self, position: dict):
        """执行止盈"""
        try:
            symbol = position["symbol"]
            volume = abs(position["volume"])
            
            self.logger.warning(
                f"执行止盈:\n"
                f"  标的: {symbol}\n"
                f"  数量: {volume}\n"
                f"  成本价: ${position['cost_price']:.2f}\n"
                f"  现价: ${position['current_price']:.2f}\n"
                f"  止盈类型: 固定止盈"
            )
            
            # 执行市价单平仓
            order = await self.trade_ctx.submit_order(
                symbol=symbol,
                order_type=OrderType.MO,  # 使用市价单
                side=OrderSide.SELL if position["volume"] > 0 else OrderSide.BUY,
                submitted_quantity=volume,
                time_in_force=TimeInForceType.DAY,
                remark="Fixed Take Profit"
            )
            
            self.logger.info(f"止盈订单已提交 - 订单号: {order.order_id}")
            
            # 等待订单状态更新
            await asyncio.sleep(1)
            order_status = await self.trade_ctx.get_order(order.order_id)
            self.logger.info(f"止盈订单状态: {order_status.status}")
            
        except Exception as e:
            self.logger.error(f"执行止盈时出错: {str(e)}")
            self.logger.exception("详细错误信息:")

    async def analyze_trend(self, symbol: str) -> Dict[str, Any]:
        """分析趋势"""
        try:
            # 获取历史数据
            prices = self.price_history.get(symbol, [])
            if not prices:
                return {'trend': 'neutral', 'signal': None}
            
            # 计算指标
            fast_ma = self._calculate_sma(prices, self.trend_params['fast_length'])
            slow_ma = self._calculate_sma(prices, self.trend_params['slow_length'])
            curve = self._calculate_sma(
                [f + s for f, s in zip(fast_ma, slow_ma)],
                self.trend_params['curve_length']
            )
            
            # 计算VWAP和通道
            vwap = self.vwap_history.get(symbol, [])
            if vwap:
                std_dev = self._calculate_stdev(vwap, self.trend_params['trend_period'])
                upper_band = vwap[-1] + std_dev * self.trend_params['vwap_dev']
                lower_band = vwap[-1] - std_dev * self.trend_params['vwap_dev']
                
                current_price = prices[-1]
                long_term_trend = self._calculate_sma(curve, self.trend_params['trend_period'])
                
                # 趋势判断
                is_up_trend = (long_term_trend[-1] > long_term_trend[-2] and 
                             current_price > vwap[-1])
                is_strong_up = is_up_trend and current_price > upper_band
                is_down_trend = (long_term_trend[-1] < long_term_trend[-2] and 
                               current_price < vwap[-1])
                is_strong_down = is_down_trend and current_price < lower_band
                was_down_trend = long_term_trend[-2] < long_term_trend[-3]
                
                # 生成信号
                if is_strong_up:
                    return {'trend': 'strong_up', 'signal': 'reduce'}
                elif is_up_trend and was_down_trend:
                    return {'trend': 'up', 'signal': 'add'}
                elif is_strong_down:
                    return {'trend': 'strong_down', 'signal': 'close'}
                elif is_down_trend and not was_down_trend:
                    return {'trend': 'down', 'signal': 'reduce'}
                else:
                    return {'trend': 'up' if is_up_trend else 'down', 'signal': None}
            
            return {'trend': 'neutral', 'signal': None}
            
        except Exception as e:
            self.logger.error(f"分析趋势时出错: {str(e)}")
            return {'trend': 'neutral', 'signal': None}

    def _calculate_sma(self, data: List[float], length: int) -> List[float]:
        """计算简单移动平均"""
        if len(data) < length:
            return data
        return [sum(data[i:i+length])/length for i in range(len(data)-length+1)]

    def _calculate_stdev(self, data: List[float], length: int) -> float:
        """计算标准差"""
        if len(data) < length:
            return 0
        subset = data[-length:]
        mean = sum(subset) / length
        squared_diff = [(x - mean) ** 2 for x in subset]
        return (sum(squared_diff) / length) ** 0.5