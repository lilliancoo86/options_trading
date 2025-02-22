"""
期权策略模块
整合技术分析信号和期权合约选择
"""
from typing import Dict, List, Any, Optional, Tuple, Union
import logging
from datetime import datetime, timedelta
import asyncio
import numpy as np
import pandas as pd
from decimal import Decimal
import pytz
from longport.openapi import (
    Config, QuoteContext, SubType, PushQuote,
    TradeContext, Period, AdjustType, OptionType,
    OrderSide, OpenApiException
)

class DoomsdayOptionStrategy:
    def __init__(self, config: Dict[str, Any], data_manager, position_manager=None) -> None:
        """初始化策略"""
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.data_manager = data_manager
        self.position_manager = position_manager
        self.tz = pytz.timezone('America/New_York')
        
        # 交易标的
        self.symbols = config.get('symbols', [])
        if not self.symbols:
            self.logger.warning("未在配置中找到交易标的，尝试从 TRADING_CONFIG 中获取")
            self.symbols = config.get('TRADING_CONFIG', {}).get('symbols', [])
        
        # 记录配置的交易标的
        self.logger.info(f"已配置的交易标的: {self.symbols}")
        
        # 策略参数(简化为只使用均线)
        self.strategy_params = config.get('strategy_params', {
            'ma_fast': 5,           # 快速均线周期
            'ma_slow': 20,          # 慢速均线周期
            'ma_signal': 10,        # 信号均线周期
            'signal_threshold': 0.6, # 信号阈值
            
            # 期权筛选参数
            'min_volume': 100,         # 最小成交量
            'min_open_interest': 50,   # 最小持仓量
            'max_bid_ask_spread': 0.5, # 最大买卖价差
            'min_days_to_expiry': 7,   # 最小到期天数
            'max_days_to_expiry': 45,  # 最大到期天数
            'target_delta': {          # 目标Delta范围
                'call': (0.30, 0.70),
                'put': (-0.70, -0.30)
            }
        })
        
        # 信号缓存
        self._signal_cache = {}
        
    async def async_init(self) -> None:
        """异步初始化方法"""
        try:
            # 验证数据管理器
            if not self.data_manager:
                raise ValueError("数据管理器未初始化")
            
            # 验证交易标的
            if not self.symbols:
                raise ValueError("未配置交易标的")
            
            # 获取行情连接
            quote_ctx = await self.data_manager.ensure_quote_ctx()
            if not quote_ctx:
                raise ValueError("无法获取行情连接")
            
            # 订阅行情
            for symbol in self.symbols:
                try:
                    # 使用同步方法进行订阅
                    quote_ctx.subscribe(
                        symbols=[symbol],
                        sub_types=[SubType.Quote, SubType.Trade, SubType.Depth],
                        is_first_push=True
                    )
                    self.logger.info(f"成功订阅 {symbol} 的行情数据")
                    await asyncio.sleep(0.1)  # 避免请求过快
                except Exception as e:
                    self.logger.error(f"订阅{symbol}失败: {str(e)}")
                    continue
            
            self.logger.info("期权策略初始化完成")
            
        except Exception as e:
            self.logger.error(f"期权策略初始化失败: {str(e)}")
            raise

    async def generate_signal(self, symbol: str) -> Optional[Dict[str, Any]]:
        """生成交易信号"""
        try:
            # 检查信号缓存
            if symbol in self._signal_cache:
                last_signal_time = self._signal_cache[symbol].get('timestamp')
                if last_signal_time:
                    time_diff = (datetime.now(self.tz) - last_signal_time).seconds
                    if time_diff < 300:  # 5分钟内的信号直接返回
                        self.logger.debug(f"{symbol} 使用缓存的信号")
                        return self._signal_cache[symbol].get('signal')
            
            # 获取技术分析数据和均线指标
            tech_data = await self.data_manager.get_technical_data(symbol, self.strategy_params)
            if tech_data is None:
                self.logger.warning(f"{symbol} 无法获取技术分析数据")
                return None
            
            # 检查信号强度和趋势
            signal_strength = tech_data['signal']
            trend = tech_data['trend']
            ma_indicators = tech_data['indicators']
            
            self.logger.info(f"{symbol} 当前信号强度: {signal_strength:.2f}, 趋势: {trend}, "
                           f"均线趋势: {'向上' if ma_indicators['ma_trend'] else '向下'}")
            
            # 只在趋势明确且信号强度达到阈值时生成信号
            if abs(signal_strength) < self.strategy_params['signal_threshold']:
                self.logger.info(f"{symbol} 信号强度 {abs(signal_strength):.2f} "
                               f"未达到阈值 {self.strategy_params['signal_threshold']}")
                return None
            
            # 获取当前持仓
            current_position = None
            if self.position_manager:
                current_position = await self.position_manager.get_position(symbol)
                self.logger.debug(f"{symbol} 当前持仓: {current_position}")
            
            # 确定交易动作
            action = None
            quantity = 0
            
            # 根据趋势和均线指标确定交易动作
            if trend == 'bullish' and ma_indicators['ma_trend']:
                if not current_position:
                    action = 'buy'
                    self.logger.info(f"{symbol} 满足买入条件: 多头趋势且均线向上")
                else:
                    self.logger.info(f"{symbol} 已有持仓，不生成买入信号")
            elif trend == 'bearish' and not ma_indicators['ma_trend']:
                if current_position:
                    action = 'sell'
                    self.logger.info(f"{symbol} 满足卖出条件: 空头趋势且均线向下")
                else:
                    self.logger.info(f"{symbol} 无持仓，不生成卖出信号")
            else:
                self.logger.info(f"{symbol} 趋势与均线方向不一致，不生成交易信号")
            
            if not action:  # 第三个检查点
                return None
            
            if action:  # 只在有具体交易动作时生成信号
                signal = {
                    'symbol': symbol,
                    'action': action,
                    'quantity': quantity,
                    'signal': signal_strength,
                    'trend': trend,
                    'strength': tech_data['strength'],
                    'indicators': ma_indicators,
                    'timestamp': datetime.now(self.tz),
                    'price': tech_data['latest']['close'],
                    'ma_cross': ma_indicators['ma_cross'],  # 添加均线交叉信息
                    'ma_diff_ratio': ma_indicators['ma_diff_ratio']  # 添加均线差值比例
                }
                
                # 更新信号缓存
                self._signal_cache[symbol] = {
                    'signal': signal,
                    'timestamp': datetime.now(self.tz)
                }
                
                return signal
            
        except Exception as e:
            self.logger.error(f"生成 {symbol} 的交易信号时出错: {str(e)}")
            return None

    def _calculate_position_size(self, trend_signal: Dict[str, Any], 
                               option_data: Dict[str, Any]) -> int:
        """计算持仓规模"""
        try:
            # 获取账户规模
            account_size = self.strategy_params.get('account_size', 100000)
            max_position_size = self.strategy_params.get('max_position_size', 0.1)
            
            # 根据信号强度调整仓位
            signal_strength = abs(trend_signal['signal'])
            position_pct = max_position_size * signal_strength
            
            # 计算目标持仓金额
            target_amount = account_size * position_pct
            
            # 根据期权价格计算数量
            option_price = option_data.get('last_price', 0)
            if option_price <= 0:
                return 0
            
            quantity = int(target_amount / option_price)
            
            # 确保不超过最大持仓限制
            max_contracts = self.strategy_params.get('max_contracts', 100)
            return min(quantity, max_contracts)
            
        except Exception as e:
            self.logger.error(f"计算持仓规模时出错: {str(e)}")
            return 0

    async def get_portfolio_status(self) -> Dict[str, float]:
        """获取投资组合状态"""
        try:
            total_market_value = 0.0
            total_unrealized_pnl = 0.0
            
            if self.position_manager:
                positions = await self.position_manager.get_positions()
                for position in positions:
                    total_market_value += position.get('market_value', 0)
                    total_unrealized_pnl += position.get('unrealized_pnl', 0)
            
            return {
                'total_market_value': total_market_value,
                'total_unrealized_pnl': total_unrealized_pnl,
                'timestamp': datetime.now(self.tz)
            }
            
        except Exception as e:
            self.logger.error(f"获取投资组合状态时出错: {str(e)}")
            return {
                'total_market_value': 0.0,
                'total_unrealized_pnl': 0.0,
                'timestamp': datetime.now(self.tz)
            }
