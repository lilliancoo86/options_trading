"""
末日期权系统 - 日内交易策略模块
"""
from typing import Dict, List, Any, Optional, Tuple
import logging
from datetime import datetime, timedelta
import pytz
from decimal import Decimal
import asyncio
from longport.openapi import (
    Config, 
    QuoteContext, 
    TradeContext,
    SubType, 
    OrderType, 
    OrderSide,
    TimeInForceType
)
import os
import json
import re

class DoomsdayOptionStrategy:
    def __init__(self, config: Dict[str, Any], test_mode: bool = False):
        """初始化策略"""
        self.config = config
        self.test_mode = test_mode
        self.logger = logging.getLogger(__name__)
        self.tz = pytz.timezone('America/New_York')
        
        # 初始化交易标的
        self.symbols = config.get('symbols', [
            "TSLL.US",    # 特斯拉做多ETF
            "NVDA.US",    # 英伟达
            "AAPL.US",    # 苹果
        ])
        
        # 添加VIX监控
        self.vix_symbol = "VIX.US"
        self.symbols.append(self.vix_symbol)
        
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
        
        # 使用传入的上下文
        self.quote_ctx = config['api']['quote_context']
        self.trade_ctx = config['api']['trade_context']
        
        # 添加订阅类型
        self.sub_types = [
            SubType.Quote,     # 基础报价
            SubType.Depth,     # 盘口
            SubType.Brokers,   # 经纪队列
            SubType.Trade,     # 逐笔成交
            # SubType.Greeks 已被移除，使用其他方式获取期权希腊字母
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
        
        # 策略相关配置
        self.trend_config = {
            'ma_periods': [5, 10, 20],
            'rsi_period': 14,
            'macd_params': {
                'fast': 12,
                'slow': 26,
                'signal': 9
            },
            'volume_ma': 20
        }
        
        # 建仓策略配置
        self.position_sizing = {
            'initial': {
                'ratio': 0.25,     # 初始仓位比例
                'conditions': {     
                    'technical': {
                        'ma_trend': True,      # 均线趋势向上
                        'macd': 'golden_cross', # MACD金叉
                        'rsi': (30, 70)        # RSI合理区间
                    }
                }
            },
            'scale_in': {
                'max_times': 3,    # 最大加仓次数
                'min_interval': 5, # 最小加仓间隔(分钟)
                'conditions': {
                    'trend_confirmation': {
                        'ma_alignment': True,      # 均线多头排列
                        'volume_increase': 1.2,    # 成交量需要放大20%
                        'momentum_positive': True   # 动量指标保持向上
                    }
                },
                'stages': [
                    {
                        'ratio': 0.25,
                        'technical_requirements': {
                            'ma_support': '5ma',     # 5日均线支撑
                            'volume_ratio': 1.2,     # 成交量比
                            'rsi_range': (35, 45)    # RSI回调区间
                        }
                    },
                    {
                        'ratio': 0.25,
                        'technical_requirements': {
                            'ma_support': '10ma',  # 10日均线支撑
                            'volume_ratio': 1.5,
                            'rsi_range': (30, 40)
                        }
                    },
                    {
                        'ratio': 0.25,
                        'technical_requirements': {
                            'ma_support': '20ma',  # 20日均线支撑
                            'volume_ratio': 2.0,
                            'rsi_range': (25, 35)
                        }
                    }
                ]
            }
        }
        
        # 缓存历史数据
        self.price_history = {}
        self.vwap_history = {}

        # 趋势跟踪
        self._trend_cache = {}
        self._position_records = {}

    async def __aenter__(self):
        """异步上下文管理器的进入方法"""
        try:
            self.logger.info("策略初始化完成")
            return self
        except Exception as e:
            self.logger.error(f"初始化失败: {str(e)}")
            raise

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器的退出方法"""
        try:
            self.logger.info("策略清理完成")
        except Exception as e:
            self.logger.error(f"清理资源时出错: {str(e)}")

    async def analyze_stock_trend(self, symbol: str) -> Dict[str, Any]:
        """分析股票趋势"""
        try:
            # 获取历史K线数据
            end_time = datetime.now(self.tz)
            start_time = end_time - timedelta(days=30)
            
            candlesticks = await self.quote_ctx.candlesticks(
                symbol=symbol,
                period="day",  # 使用字符串常量
                count=30,
                adjust_type="no_adjust"  # 使用字符串常量
            )
            
            if not candlesticks:
                return {"trend": "neutral", "signal": None}
            
            # 计算技术指标
            indicators = await self._calculate_indicators(candlesticks)
            
            # 获取开盘涨跌幅
            quotes = await self.quote_ctx.quote([symbol])
            if not quotes:
                return {"trend": "neutral", "signal": None}
            
            open_change_pct = self._calculate_open_change(quotes[0])
            
            # 综合分析趋势
            trend_analysis = self._analyze_trend(indicators, open_change_pct)
            
            self.logger.info(
                f"趋势分析结果 - {symbol}:\n"
                f"  趋势: {trend_analysis['trend']}\n"
                f"  信号: {trend_analysis['signal']}\n"
                f"  得分: {trend_analysis['score']:.2f}"
            )
            
            return trend_analysis
            
        except Exception as e:
            self.logger.error(f"分析股票趋势时出错: {str(e)}")
            return {"trend": "neutral", "signal": None}

    async def select_option_contract(self, stock_symbol: str, trend: str) -> Optional[str]:
        """根据趋势选择合适的期权合约"""
        try:
            # 获取可用期权列表
            options = await self._get_available_options(stock_symbol)
            if not options:
                return None
            
            # 根据趋势选择看涨或看跌期权
            option_type = "CALL" if trend in ["strong_up", "up"] else "PUT"
            
            # 筛选符合条件的期权
            filtered_options = []
            for option in options:
                if (option['type'] == option_type and 
                    1.0 <= option['price'] <= 15.0 and  # 价格范围
                    20 <= option['leverage'] <= 30 and  # 杠杆率
                    7 <= option['days_to_expiry'] <= 30):  # 到期时间
                    
                    option['score'] = self._calculate_option_score(option)
                    filtered_options.append(option)
            
            if not filtered_options:
                return None
            
            # 筛选最佳期权（取前3个得分最高的）
            filtered_options.sort(key=lambda x: x['score'], reverse=True)
            best_options = filtered_options[:3]
            
            if best_options:
                self.logger.info(
                    f"筛选出最佳期权合约:\n" + 
                    "\n".join([
                        f"  {i+1}. {opt['symbol']}\n"
                        f"     得分: {opt['score']:.1f}\n"
                        f"     杠杆: {opt['leverage']:.1f}x\n"
                        f"     成交量: {opt['volume']}\n"
                        f"     持仓量: {opt['open_interest']}\n"
                        f"     隐含波动率: {opt['implied_volatility']:.2%}\n"
                        f"     Delta: {opt['delta']:.2f}"
                        for i, opt in enumerate(best_options)
                    ])
                )
                
                # 返回得分最高的期权
                return best_options[0]['symbol']
            
            return None
            
        except Exception as e:
            self.logger.error(f"选择期权合约时出错: {str(e)}")
            return None

    async def check_entry_opportunity(self, symbol: str, market_data: Dict[str, Any]) -> Tuple[bool, float, str]:
        """检查建仓机会"""
        try:
            # 1. 获取正股代码和趋势
            stock_symbol = self._get_underlying_symbol(symbol)
            trend_data = await self.analyze_stock_trend(stock_symbol)
            
            # 2. 只在趋势明确时建仓
            if trend_data['trend'] not in ['strong_up', 'up']:
                return False, 0, "趋势不明确"
            
            # 3. 检查是否已有持仓
            if symbol in self._position_records:
                return await self._check_scale_in(symbol, stock_symbol, market_data)
            
            # 4. 检查初始建仓条件
            if not await self._check_initial_entry(symbol, stock_symbol, market_data):
                return False, 0, "不满足初始建仓条件"
            
            return True, self.position_sizing['initial']['ratio'], "初始建仓"
            
        except Exception as e:
            self.logger.error(f"检查建仓机会时出错: {str(e)}")
            return False, 0, str(e)

    async def _check_scale_in(self, option_symbol: str, stock_symbol: str, 
                             market_data: Dict[str, Any]) -> Tuple[bool, float, str]:
        """检查加仓机会"""
        try:
            position_record = self._position_records[option_symbol]
            current_stage = len(position_record['entries'])
            
            # 1. 基本条件检查
            if current_stage >= self.position_sizing['scale_in']['max_times']:
                return False, 0, "已达最大加仓次数"
            
            if not self._check_entry_interval(position_record['entries'][-1]['time']):
                return False, 0, "加仓间隔不足"
                
            # 2. 获取技术指标数据
            tech_data = await self._get_technical_indicators(stock_symbol)
            
            # 3. 检查趋势确认
            trend_conf = self.position_sizing['scale_in']['conditions']['trend_confirmation']
            if not self._check_trend_confirmation(tech_data, trend_conf):
                return False, 0, "趋势未确认"
            
            # 4. 检查回调条件
            pullback = self.position_sizing['scale_in']['conditions']['pullback']
            stock_pb = await self._calculate_pullback(stock_symbol)
            option_pb = await self._calculate_pullback(option_symbol)
            
            if not (pullback['stock']['min'] >= stock_pb >= pullback['stock']['max']):
                return False, 0, "正股回调不符合条件"
            
            if not (pullback['option']['min'] >= option_pb >= pullback['option']['max']):
                return False, 0, "期权回调不符合条件"
            
            # 5. 检查当前阶段的技术要求
            stage_reqs = self.position_sizing['scale_in']['stages'][current_stage]['technical_requirements']
            
            # 检查均线支撑
            if stage_reqs['ma_support'] == '5ma':
                if not self._check_ma_support(tech_data, 5):
                    return False, 0, "未到5日均线支撑"
            elif stage_reqs['ma_support'] == '10ma':
                if not self._check_ma_support(tech_data, 10):
                    return False, 0, "未到10日均线支撑"
            elif stage_reqs['ma_support'] == '20ma':
                if not self._check_ma_support(tech_data, 20):
                    return False, 0, "未到20日均线支撑"
            
            # 检查成交量
            if not self._check_volume_ratio(tech_data, stage_reqs['volume_ratio']):
                return False, 0, "成交量不足"
            
            # 检查RSI
            if not self._check_rsi_range(tech_data, stage_reqs['rsi_range']):
                return False, 0, "RSI不在目标区间"
            
            # 所有条件都满足，允许加仓
            return True, self.position_sizing['scale_in']['stages'][current_stage]['ratio'], f"第{current_stage + 1}次加仓"
            
        except Exception as e:
            self.logger.error(f"检查加仓机会时出错: {str(e)}")
            return False, 0, str(e)