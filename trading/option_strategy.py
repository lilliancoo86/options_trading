# 标准库
from typing import Dict, List, Any, Optional, Tuple, Union
import logging
from datetime import datetime
import asyncio
import json
import math
from pathlib import Path

# 第三方库
import numpy as np
import pandas as pd
import pytz
from decimal import Decimal
from scipy.stats import percentileofscore

# LongPort SDK
from longport.openapi import (
    Config, 
    QuoteContext, 
    SubType, 
    PushQuote,
    TradeContext,
    Period,
    AdjustType,
    OptionType,
    OrderSide    
)

# 本地导入
from trading.data_manager import DataManager
from config.config import TRADING_CONFIG


class DoomsdayOptionStrategy:
    def __init__(self, config: Dict[str, Any], data_manager) -> None:
        """初始化策略"""
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.data_manager = data_manager
        self.tz = pytz.timezone('America/New_York')
        
        # 交易品种
        self.symbols = config.get('symbols', [])
        
        # 技术指标参数
        self.tech_params = config.get('tech_params', {
            'ma_periods': [5, 10, 20],
            'macd': {
                'fast': 12,
                'slow': 26,
                'signal': 9
            },
            'rsi_period': 14,
            'volume_ma': 20,
            'price_threshold': 0.02
        })
        
        # 期权选择参数
        self.option_params = {
            'min_volume': 100,  # 最小成交量
            'min_open_interest': 500,  # 最小持仓量
            'delta_range': {
                'call': (0.3, 0.7),  # call期权delta范围
                'put': (-0.7, -0.3)  # put期权delta范围
            },
            'min_days': 3,  # 最短到期时间
            'max_days': 30,  # 最长到期时间
            'iv_percentile': 50  # IV百分位阈值
        }


    async def async_init(self) -> None:
        """
        异步初始化方法
        
        Returns:
            None
        """
        try:
            # 验证数据管理器
            if not self.data_manager:
                raise ValueError("数据管理器未初始化")
            
            # 验证交易标的
            if not self.symbols:
                raise ValueError("未配置交易标的")
            
            # 修改订阅方式
            for symbol in self.symbols:
                try:
                    # 使用正确的订阅方法
                    await self.data_manager.quote_ctx.subscribe(
                        symbols=[symbol],
                        sub_types=[SubType.Quote, SubType.Trade, SubType.Depth],
                        is_first_push=True
                    )
                    self.logger.info(f"成功订阅 {symbol} 的行情数据")
                except Exception as e:
                    self.logger.error(f"订阅{symbol}失败: {str(e)}")
                    continue
                
                await asyncio.sleep(0.1)  # 避免请求过快
            
            self.logger.info("期权策略初始化完成")
            
        except Exception as e:
            self.logger.error(f"期权策略初始化失败: {str(e)}")
            raise


    async def _stock_klines(self, symbol: str) -> Optional[pd.DataFrame]:
        """获取股票K线数据"""
        try:
            # 直接从处理好的文件读取数据
            df = await self.data_manager.get_klines(symbol)
            if df is None:
                return None
                
            # 验证必要的技术指标列是否存在
            required_columns = [
                'close', 'volume',
                'MA5', 'MA10', 'MA20',
                'MACD', 'Signal', 'Hist',
                'RSI', 'volatility'
            ]
            
            if not all(col in df.columns for col in required_columns):
                self.logger.error(f"{symbol} 缺少必要的技术指标列")
                return None
                
            return df
            
        except Exception as e:
            self.logger.error(f"获取 {symbol} K线数据时出错: {str(e)}")
            return None

    async def _calculate_and_analyze_ma(self, klines_df: pd.DataFrame) -> Dict[str, Any]:
        """计算和分析移动平均线
        
        Args:
            klines_df: K线数据DataFrame
            
        Returns:
            Dict: 包含MA分析结果的字典
        """
        try:
            if klines_df.empty:
                return {'trend': 'neutral', 'strength': 0, 'crossover': None}
            
            result = {
                'ma_values': {},
                'crossovers': [],
                'trend': 'neutral',
                'strength': 0
            }
            
            # 计算不同周期的MA
            for period in self.tech_params['ma_periods']:
                ma_key = f'MA{period}'
                klines_df[ma_key] = klines_df['close'].rolling(window=period).mean()
                result['ma_values'][ma_key] = klines_df[ma_key].iloc[-1]
            
            # 分析趋势
            ma_short = klines_df[f"MA{self.tech_params['ma_periods'][0]}"].iloc[-1]
            ma_mid = klines_df[f"MA{self.tech_params['ma_periods'][1]}"].iloc[-1]
            ma_long = klines_df[f"MA{self.tech_params['ma_periods'][2]}"].iloc[-1]
            
            # 判断趋势
            if ma_short > ma_mid > ma_long:
                result['trend'] = 'bullish'
                result['strength'] = (ma_short/ma_long - 1) * 100
            elif ma_short < ma_mid < ma_long:
                result['trend'] = 'bearish'
                result['strength'] = (1 - ma_short/ma_long) * 100
            
            # 检测交叉
            for i in range(len(self.tech_params['ma_periods'])-1):
                ma1 = f"MA{self.tech_params['ma_periods'][i]}"
                ma2 = f"MA{self.tech_params['ma_periods'][i+1]}"
                
                # 判断是否发生交叉
                if (klines_df[ma1].iloc[-2] <= klines_df[ma2].iloc[-2] and 
                    klines_df[ma1].iloc[-1] > klines_df[ma2].iloc[-1]):
                    result['crossovers'].append({
                        'type': 'golden',
                        'ma1': ma1,
                        'ma2': ma2
                    })
                elif (klines_df[ma1].iloc[-2] >= klines_df[ma2].iloc[-2] and 
                      klines_df[ma1].iloc[-1] < klines_df[ma2].iloc[-1]):
                    result['crossovers'].append({
                        'type': 'death',
                        'ma1': ma1,
                        'ma2': ma2
                    })
            
            return result
            
        except Exception as e:
            self.logger.error(f"计算移动平均线时出错: {str(e)}")
            return {'trend': 'neutral', 'strength': 0, 'crossover': None}

    async def analyze_stock_trend(self, symbol: str) -> Dict[str, Any]:
        """分析股票趋势"""
        try:
            # 获取K线数据
            klines_df = await self.data_manager.get_klines(symbol)
            if klines_df is None or klines_df.empty:
                raise ValueError(f"无法获取 {symbol} 的K线数据")
            
            # 计算和分析移动平均线
            ma_analysis = await self._calculate_and_analyze_ma(klines_df)
            
            # 返回分析结果
            return {
                'symbol': symbol,
                'ma_analysis': ma_analysis,
                'timestamp': datetime.now(self.tz).isoformat()
            }
            
        except Exception as e:
            self.logger.error(f"分析股票趋势时出错: {str(e)}")
            return None

    async def select_option_contract(self, symbol: str, trend: Optional[str]) -> Optional[Dict]:
        """选择合适的期权合约"""
        try:
            if not trend:
                return None
                
            # 获取标的当前价格
            quote = await self.data_manager.get_quote(symbol)
            if not quote:
                return None
                
            current_price = float(quote['last_done'])
            
            # 获取期权链
            option_chain = await self.data_manager.get_option_chain(symbol)
            if not option_chain:
                return None
                
            # 获取技术指标分析结果
            df = await self._stock_klines(symbol)
            if df is None:
                return None
            
            analysis = await self.analyze_stock_trend(symbol)
            if not analysis or 'ma_analysis' not in analysis:
                self.logger.error(f"获取 {symbol} 趋势分析失败")
                return None
            
            trend_info = analysis['ma_analysis']
            trend_direction = trend_info['trend']  # 'bullish' 或 'bearish'
            signal_strength = abs(trend_info['strength'])
            
            # 根据趋势方向决定期权类型
            if trend_direction == 'neutral' or signal_strength < 0.3:  # 信号太弱，不开仓
                self.logger.info(f"{symbol} 趋势不明确或信号强度不足: {trend_direction}, {signal_strength:.2f}")
                return None
            
            # 确定买入方向
            option_type = 'call' if trend_direction == 'bullish' else 'put'
            
            # 根据波动率环境调整参数
            volatility = df['volatility'].iloc[-1]
            params = self._get_option_params(volatility, signal_strength)
            
            # 筛选合约
            valid_contracts = []
            for contract in option_chain:
                if contract['type'].lower() != option_type:
                    continue
                    
                # 计算到期时间
                days_to_expiry = (contract['expiry_date'] - datetime.now(self.tz)).days
                
                # 计算虚值程度
                moneyness = (float(contract['strike_price']) / current_price - 1)
                if option_type == 'put':
                    moneyness = -moneyness
                    
                # 基本条件筛选
                if not (params['min_days'] <= days_to_expiry <= params['max_days'] and
                       params['min_moneyness'] <= moneyness <= params['max_moneyness'] and
                       contract['volume'] >= params['min_volume'] and
                       contract['open_interest'] >= params['min_open_interest']):
                    continue
                    
                # 计算综合得分
                score = self._calculate_contract_score(
                    contract=contract,
                    current_price=current_price,
                    days_to_expiry=days_to_expiry,
                    moneyness=moneyness,
                    params=params,
                    signal_strength=signal_strength
                )
                
                if score > 0:
                    valid_contracts.append({
                        **contract,
                        'score': score,
                        'days_to_expiry': days_to_expiry,
                        'moneyness': moneyness
                    })
            
            if not valid_contracts:
                self.logger.info(f"{symbol} 未找到符合条件的期权合约")
                return None
            
            # 按得分排序并返回最佳合约
            valid_contracts.sort(key=lambda x: x['score'], reverse=True)
            best_contract = valid_contracts[0]
            
            self.logger.info(
                f"选择期权合约:\n"
                f"  标的: {symbol}\n"
                f"  信号强度: {signal_strength:.2f}\n"
                f"  合约类型: {option_type.upper()}\n"
                f"  合约代码: {best_contract['symbol']}\n"
                f"  行权价: {best_contract['strike_price']}\n"
                f"  到期天数: {best_contract['days_to_expiry']}\n"
                f"  虚值程度: {best_contract['moneyness']:.1%}\n"
                f"  成交量: {best_contract['volume']}\n"
                f"  持仓量: {best_contract['open_interest']}\n"
                f"  隐含波动率: {best_contract['implied_volatility']:.1%}\n"
                f"  综合得分: {best_contract['score']:.2f}"
            )
            
            return best_contract
            
        except Exception as e:
            self.logger.error(f"选择期权合约时出错: {str(e)}")
            return None

    def _get_option_params(self, volatility: float, signal: float) -> Dict[str, Any]:
        """根据波动率环境和信号强度确定期权参数"""
        signal_strength = abs(signal)
        
        if volatility > 0.4:  # 高波动环境
            return {
                'min_days': 15,
                'max_days': 45,
                'min_moneyness': 0.03,  # 3%虚值
                'max_moneyness': 0.10,  # 10%虚值
                'min_volume': 200,
                'min_open_interest': 1000,
                'volume_weight': 0.3,
                'iv_weight': 0.3,
                'time_value_weight': 0.2,
                'moneyness_weight': 0.2
            }
        elif volatility > 0.25:  # 中等波动环境
            return {
                'min_days': 20,
                'max_days': 60,
                'min_moneyness': 0.05,
                'max_moneyness': 0.15,
                'min_volume': 150,
                'min_open_interest': 750,
                'volume_weight': 0.25,
                'iv_weight': 0.25,
                'time_value_weight': 0.25,
                'moneyness_weight': 0.25
            }
        else:  # 低波动环境
            return {
                'min_days': 30,
                'max_days': 90,
                'min_moneyness': 0.07,
                'max_moneyness': 0.20,
                'min_volume': 100,
                'min_open_interest': 500,
                'volume_weight': 0.2,
                'iv_weight': 0.2,
                'time_value_weight': 0.3,
                'moneyness_weight': 0.3
            }

    def _calculate_contract_score(self, contract: Dict, current_price: float,
                                days_to_expiry: int, moneyness: float,
                                params: Dict, signal_strength: float) -> float:
        """计算合约综合得分"""
        try:
            # 流动性得分
            volume_score = min(1.0, contract['volume'] / (params['min_volume'] * 3))
            oi_score = min(1.0, contract['open_interest'] / (params['min_open_interest'] * 3))
            liquidity_score = (volume_score + oi_score) / 2
            
            # 时间价值得分
            time_score = 1 - (days_to_expiry - params['min_days']) / (params['max_days'] - params['min_days'])
            
            # 虚值程度得分
            moneyness_score = 1 - abs(moneyness - params['min_moneyness']) / (params['max_moneyness'] - params['min_moneyness'])
            
            # IV得分 (相对于历史波动率)
            iv_score = 1 - (contract['implied_volatility'] - contract['historical_volatility']) / contract['historical_volatility']
            
            # 综合得分
            score = (
                liquidity_score * params['volume_weight'] +
                time_score * params['time_value_weight'] +
                moneyness_score * params['moneyness_weight'] +
                iv_score * params['iv_weight']
            ) * signal_strength  # 信号强度作为整体得分的调节因子
            
            return max(0, score)
            
        except Exception as e:
            self.logger.error(f"计算合约得分时出错: {str(e)}")
            return 0

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
