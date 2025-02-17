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
    def __init__(self, config: Dict[str, Any], data_manager) -> None:
        """初始化策略"""
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.data_manager = data_manager
        self.tz = pytz.timezone('America/New_York')
        
        # 交易标的
        self.symbols = config.get('symbols', [])
        if not self.symbols:
            self.logger.warning("未在配置中找到交易标的，尝试从 TRADING_CONFIG 中获取")
            self.symbols = config.get('TRADING_CONFIG', {}).get('symbols', [])
        
        # 记录配置的交易标的
        self.logger.info(f"已配置的交易标的: {self.symbols}")
        
        # 策略参数
        self.strategy_params = config.get('strategy_params', {
            'trend_weight': 0.25,      # 趋势策略权重
            'mean_reversion_weight': 0.20,  # 均值回归策略权重
            'momentum_weight': 0.25,    # 动量策略权重
            'volatility_weight': 0.15,  # 波动率策略权重
            'stat_arb_weight': 0.15,    # 统计套利策略权重
            
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

    async def analyze_stock_trend(self, symbol: str) -> Optional[Dict[str, Any]]:
        """分析股票趋势并生成交易信号"""
        try:
            # 获取技术分析数据
            df = await self.data_manager.get_technical_data(symbol)
            if df is None or df.empty:
                return None
            
            if not await self._validate_data(df):
                return None
            
            # 计算各策略信号
            signals = {
                'trend': self._calculate_trend_signal(df),
                'mean_reversion': self._calculate_mean_reversion_signal(df),
                'momentum': self._calculate_momentum_signal(df),
                'volatility': self._calculate_volatility_signal(df),
                'stat_arb': self._calculate_stat_arb_signal(df)
            }
            
            # 加权合成信号
            composite_signal = self._calculate_composite_signal(signals)
            
            # 生成交易信号
            if abs(composite_signal) >= self.strategy_params.get('signal_threshold', 0.6):
                return {
                    'symbol': symbol,
                    'trend': 'bullish' if composite_signal > 0 else 'bearish',
                    'signal': composite_signal,
                    'timestamp': datetime.now(self.tz)
                }
            
            return None
            
        except Exception as e:
            self.logger.error(f"分析 {symbol} 趋势时出错: {str(e)}")
            return None

    async def select_option_contract(
        self, 
        symbol: str,
        trend: str
    ) -> Optional[Dict[str, Any]]:
        """选择合适的期权合约"""
        try:
            # 获取期权链
            quote_ctx = await self.data_manager.ensure_quote_ctx()
            if not quote_ctx:
                return None
            
            # 获取标的当前价格
            quote = await quote_ctx.quote(symbols=[symbol])
            if not quote:
                return None
            
            current_price = quote[0].last_done
            
            # 获取期权链
            options = await quote_ctx.option_chain(
                symbol=symbol,
                start_date=datetime.now(self.tz).date(),
                end_date=(datetime.now(self.tz) + timedelta(
                    days=self.strategy_params['max_days_to_expiry']
                )).date()
            )
            
            if not options:
                return None
            
            # 筛选合适的期权合约
            filtered_options = []
            for option in options:
                # 基本筛选条件
                if (option.volume < self.strategy_params['min_volume'] or
                    option.open_interest < self.strategy_params['min_open_interest'] or
                    (option.ask_price - option.bid_price) > self.strategy_params['max_bid_ask_spread']):
                    continue
                
                # 到期日筛选
                days_to_expiry = (option.expiry_date - datetime.now(self.tz).date()).days
                if (days_to_expiry < self.strategy_params['min_days_to_expiry'] or
                    days_to_expiry > self.strategy_params['max_days_to_expiry']):
                    continue
                
                filtered_options.append(option)
            
            if not filtered_options:
                return None
            
            # 根据趋势选择看涨或看跌期权
            option_type = OptionType.Call if trend == 'bullish' else OptionType.Put
            target_delta = self.strategy_params['target_delta']['call' if trend == 'bullish' else 'put']
            
            # 选择最佳合约
            best_contract = None
            best_score = 0
            
            for option in filtered_options:
                if option.type != option_type:
                    continue
                
                # 计算合约得分
                score = await self._calculate_contract_score(
                    option, current_price, target_delta
                )
                
                if score > best_score:
                    best_score = score
                    best_contract = option
            
            if best_contract:
                return {
                    'symbol': best_contract.symbol,
                    'side': OrderSide.Buy if trend == 'bullish' else OrderSide.Sell,
                    'score': best_score
                }
            
            return None
            
        except Exception as e:
            self.logger.error(f"选择期权合约时出错: {str(e)}")
            return None

    def _calculate_trend_signal(self, df: pd.DataFrame) -> float:
        """计算趋势信号"""
        try:
            # 使用移动平均线和ADX
            ema_short = df['MA5'].iloc[-1]
            ema_mid = df['MA10'].iloc[-1]
            ema_long = df['MA20'].iloc[-1]
            
            trend_strength = df['trend_strength'].iloc[-1]
            
            # 计算趋势信号
            if ema_short > ema_mid > ema_long and trend_strength > 25:
                return 1.0
            elif ema_short < ema_mid < ema_long and trend_strength > 25:
                return -1.0
            else:
                return 0.0
                
        except Exception as e:
            self.logger.error(f"计算趋势信号时出错: {str(e)}")
            return 0.0

    def _calculate_mean_reversion_signal(self, df: pd.DataFrame) -> float:
        """计算均值回归信号"""
        try:
            # 使用价格与移动平均线的偏离度
            current_price = df['close'].iloc[-1]
            ma20 = df['MA20'].iloc[-1]
            
            # 计算Z分数
            std = df['price_std'].iloc[-1]
            z_score = (current_price - ma20) / std if std != 0 else 0
            
            # 生成信号
            if z_score < -2:
                return 1.0  # 超卖
            elif z_score > 2:
                return -1.0  # 超买
            else:
                return 0.0
                
        except Exception as e:
            self.logger.error(f"计算均值回归信号时出错: {str(e)}")
            return 0.0

    def _calculate_momentum_signal(self, df: pd.DataFrame) -> float:
        """计算动量信号"""
        try:
            # 使用MACD和RSI
            macd = df['MACD'].iloc[-1]
            signal = df['Signal'].iloc[-1]
            rsi = df['RSI'].iloc[-1]
            
            # 综合信号
            momentum_signal = 0.0
            
            # MACD信号
            if macd > signal:
                momentum_signal += 0.5
            elif macd < signal:
                momentum_signal -= 0.5
            
            # RSI信号
            if rsi > 70:
                momentum_signal -= 0.5
            elif rsi < 30:
                momentum_signal += 0.5
            
            return momentum_signal
            
        except Exception as e:
            self.logger.error(f"计算动量信号时出错: {str(e)}")
            return 0.0

    def _calculate_volatility_signal(self, df: pd.DataFrame) -> float:
        """计算波动率信号"""
        try:
            vol_zscore = df['volatility_zscore'].iloc[-1]
            
            if vol_zscore < -1.5:
                return 1.0  # 低波动率，可能突破
            elif vol_zscore > 1.5:
                return -1.0  # 高波动率，可能回落
            else:
                return 0.0
                
        except Exception as e:
            self.logger.error(f"计算波动率信号时出错: {str(e)}")
            return 0.0

    def _calculate_stat_arb_signal(self, df: pd.DataFrame) -> float:
        """计算统计套利信号"""
        try:
            # 使用价格变化和成交量比率
            price_change = df['price_change'].iloc[-1]
            volume_ratio = df['volume_ratio'].iloc[-1]
            
            # 生成信号
            if price_change < -0.02 and volume_ratio > 1.5:
                return 1.0  # 超卖
            elif price_change > 0.02 and volume_ratio > 1.5:
                return -1.0  # 超买
            else:
                return 0.0
                
        except Exception as e:
            self.logger.error(f"计算统计套利信号时出错: {str(e)}")
            return 0.0

    def _calculate_composite_signal(self, signals: Dict[str, float]) -> float:
        """计算综合信号"""
        try:
            # 加权平均
            composite = (
                signals['trend'] * self.strategy_params['trend_weight'] +
                signals['mean_reversion'] * self.strategy_params['mean_reversion_weight'] +
                signals['momentum'] * self.strategy_params['momentum_weight'] +
                signals['volatility'] * self.strategy_params['volatility_weight'] +
                signals['stat_arb'] * self.strategy_params['stat_arb_weight']
            )
            
            return np.clip(composite, -1, 1)
            
        except Exception as e:
            self.logger.error(f"计算综合信号时出错: {str(e)}")
            return 0.0

    async def _calculate_contract_score(
        self, 
        option: Any,
        current_price: float,
        target_delta: Tuple[float, float]
    ) -> float:
        """计算期权合约得分"""
        try:
            # 计算到期时间得分
            days_to_expiry = (option.expiry_date - datetime.now(self.tz).date()).days
            time_score = 1.0 - (days_to_expiry - self.strategy_params['min_days_to_expiry']) / (
                self.strategy_params['max_days_to_expiry'] - self.strategy_params['min_days_to_expiry']
            )
            
            # 计算流动性得分
            volume_score = min(1.0, option.volume / self.strategy_params['min_volume'])
            spread_score = 1.0 - min(1.0, (option.ask_price - option.bid_price) / 
                                   self.strategy_params['max_bid_ask_spread'])
            
            # 计算价格得分
            strike_diff = abs(option.strike_price - current_price) / current_price
            price_score = 1.0 - min(1.0, strike_diff)
            
            # 综合得分
            return (time_score * 0.3 + 
                   volume_score * 0.2 + 
                   spread_score * 0.2 + 
                   price_score * 0.3)
            
        except Exception as e:
            self.logger.error(f"计算合约得分时出错: {str(e)}")
            return 0.0

    async def _validate_data(self, df: pd.DataFrame) -> bool:
        """验证技术指标数据完整性"""
        try:
            required_columns = [
                'close', 'volume', 'high', 'low',
                'MA5', 'MA10', 'MA20',
                'MACD', 'Signal', 'Hist',
                'RSI', 'volatility',
                'price_change', 'price_std',
                'volume_ratio', 'trend_strength',
                'momentum', 'momentum_ma',
                'volatility_zscore'
            ]
            
            missing_columns = [col for col in required_columns if col not in df.columns]
            if missing_columns:
                self.logger.error(f"缺少必要的技术指标列: {missing_columns}")
                return False
                
            # 检查数据质量
            if df.isnull().sum().any():
                self.logger.warning("数据中存在空值，将使用前向填充方法处理")
                df.fillna(method='ffill', inplace=True)
                
            return True
            
        except Exception as e:
            self.logger.error(f"验证数据时出错: {str(e)}")
            return False
