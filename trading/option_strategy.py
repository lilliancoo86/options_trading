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
        
        # 初始化交易和行情上下文
        self.quote_ctx = QuoteContext(self.longport_config)
        self.trade_ctx = TradeContext(self.longport_config)
        
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

    async def get_market_data(self) -> Dict[str, Any]:
        """获取市场数据"""
        try:
            market_data = {
                'vix': 0.0,
                'volatility': 0.0,
                'quotes': []
            }
            
            # 获取VIX
            vix_quotes = await self.quote_ctx.get_basic_quote([self.vix_symbol])
            if vix_quotes:
                market_data['vix'] = float(vix_quotes[0].last_done)
            
            # 获取标的行情
            for symbol in self.symbols:
                if symbol == self.vix_symbol:
                    continue
                    
                quotes = await self.quote_ctx.get_basic_quote([symbol])
                if quotes:
                    quote = quotes[0]
                    # 计算日内波动率
                    volatility = (float(quote.high) - float(quote.low)) / float(quote.open) * 100
                    market_data['volatility'] = max(market_data['volatility'], volatility)
                    market_data['quotes'].append(quote)
            
            return market_data
            
        except Exception as e:
            self.logger.error(f"获取市场数据时出错: {str(e)}")
            return {'vix': 0.0, 'volatility': 0.0, 'quotes': []}

    async def generate_trading_signals(self) -> List[Dict[str, Any]]:
        """生成交易信号"""
        try:
            signals = []
            
            for symbol in self.symbols:
                if symbol == self.vix_symbol:
                    continue
                    
                # 分析趋势
                trend = await self.analyze_stock_trend(symbol)
                if not trend['signal']:
                    continue
                
                # 选择期权合约
                option = await self.select_option_contract(
                    symbol,
                    trend['trend']
                )
                if not option:
                    continue
                
                signals.append({
                    'symbol': option['symbol'],
                    'volume': 1,  # 默认交易1张
                    'type': option['type'],
                    'reason': f"趋势信号: {trend['trend']}"
                })
            
            return signals
            
        except Exception as e:
            self.logger.error(f"生成交易信号时出错: {str(e)}")
            return []

    def _is_option(self, symbol: str) -> bool:
        """检查是否为期权合约"""
        try:
            # 期权代码格式: XXXYYMMDD[C/P]NNN.US
            return bool(re.search(r'\d{6}[CP]\d+\.US$', symbol))
        except Exception as e:
            self.logger.error(f"检查期权代码时出错: {str(e)}")
            return False

    async def analyze_stock_trend(self, symbol: str) -> Dict[str, Any]:
        """分析股票趋势"""
        try:
            # 获取历史K线数据
            end_time = datetime.now(self.tz)
            start_time = end_time - timedelta(days=30)
            
            candlesticks = await self.quote_ctx.get_candlestick_list(
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
            quotes = await self.quote_ctx.get_basic_quote([symbol])
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

    async def _calculate_indicators(self, klines: List[Any]) -> Dict[str, Any]:
        """计算所有技术指标"""
        try:
            prices = {
                'close': [float(k.close) for k in klines],
                'open': [float(k.open) for k in klines],
                'high': [float(k.high) for k in klines],
                'low': [float(k.low) for k in klines],
                'volume': [float(k.volume) for k in klines]
            }
            
            # 计算各种技术指标
            indicators = {
                'ma': {
                    f'ma{period}': self._calculate_ma(prices['close'], period)
                    for period in self.trend_config['ma_periods']
                },
                'rsi': self._calculate_rsi(
                    prices['close'], 
                    self.trend_config['rsi_period']
                ),
                'macd': self._calculate_macd(
                    prices['close'], 
                    self.trend_config['macd_params']
                ),
                'volume': {
                    'current': prices['volume'][-1],
                    'ma': self._calculate_ma(
                        prices['volume'], 
                        self.trend_config['volume_ma']
                    )[-1]
                }
            }
            
            return indicators
            
        except Exception as e:
            self.logger.error(f"计算技术指标时出错: {str(e)}")
            return {}

    def _analyze_trend(self, indicators: Dict[str, Any], open_change_pct: float) -> Dict[str, Any]:
        """综合分析趋势"""
        try:
            # 1. 均线趋势
            ma_trend = self._check_ma_trend(indicators['ma'])
            
            # 2. RSI信号
            rsi_signal = self._check_rsi_signal(indicators['rsi'][-1])
            
            # 3. MACD信号
            macd_signal = self._check_macd_signal(indicators['macd'])
            
            # 4. 成交量信号
            volume_signal = self._check_volume_signal(
                indicators['volume']['current'],
                indicators['volume']['ma']
            )
            
            # 5. 缺口信号
            gap_signal = self._check_gap_signal(open_change_pct)
            
            # 综合评分
            trend_score = self._calculate_trend_score({
                'ma_trend': ma_trend,
                'rsi_signal': rsi_signal,
                'macd_signal': macd_signal,
                'volume_signal': volume_signal,
                'gap_signal': gap_signal
            })
            
            # 生成交易信号
            if trend_score >= 0.7:
                trend = "strong_up"
                signal = "buy_call"
            elif trend_score <= -0.7:
                trend = "strong_down"
                signal = "buy_put"
            elif trend_score >= 0.3:
                trend = "up"
                signal = "buy_call"
            elif trend_score <= -0.3:
                trend = "down"
                signal = "buy_put"
            else:
                trend = "neutral"
                signal = None
                
            return {
                "trend": trend,
                "signal": signal,
                "score": trend_score,
                "details": {
                    "ma_trend": ma_trend,
                    "rsi": indicators['rsi'][-1],
                    "macd": indicators['macd']['histogram'][-1],
                    "volume_signal": volume_signal,
                    "gap_signal": gap_signal
                }
            }
            
        except Exception as e:
            self.logger.error(f"分析趋势时出错: {str(e)}")
            return {
                "trend": "neutral",
                "signal": None,
                "score": 0,
                "details": {}
            }

    async def _get_available_options(self, stock_symbol: str) -> List[Dict[str, Any]]:
        """获取可用的期权合约"""
        try:
            # 获取期权到期日列表
            expiry_dates = await self.quote_ctx.option_chain_expiry_date_list(stock_symbol)
            if not expiry_dates:
                self.logger.warning(f"未找到 {stock_symbol} 的期权到期日")
                return []
            
            available_options = []
            current_date = datetime.now(self.tz).date()
            
            # 遍历到期日(排除当日和次日到期)
            for expiry_date in expiry_dates:
                expiry_date_obj = datetime.strptime(expiry_date, "%Y%m%d").date()
                if (expiry_date_obj - current_date).days <= 1:  # 排除当日和次日到期
                    continue
                    
                # 获取该到期日的期权链
                chain_info = await self.quote_ctx.option_chain_info_by_date(
                    symbol=stock_symbol,
                    expiry_date=expiry_date
                )
                
                if not chain_info:
                    continue
                
                # 获取期权实时行情
                for option in chain_info:
                    # 只处理价外期权
                    if option.call_put == "CALL" and float(option.strike_price) > float(option.spot_price):
                        option_symbol = option.symbol
                    elif option.call_put == "PUT" and float(option.strike_price) < float(option.spot_price):
                        option_symbol = option.symbol
                    else:
                        continue
                    
                    # 获取期权报价
                    quotes = await self.quote_ctx.option_quote([option_symbol])
                    if not quotes:
                        continue
                        
                    quote = quotes[0]
                    
                    # 构建期权信息
                    option_info = {
                        "symbol": option_symbol,
                        "price": float(quote.last_done),
                        "delta": float(quote.delta),
                        "volume": float(quote.volume),
                        "open_interest": float(quote.open_interest),
                        "expiry_date": expiry_date_obj,
                        "strike_price": float(option.strike_price),
                        "type": option.call_put,
                        "spot_price": float(option.spot_price),
                        "implied_volatility": float(quote.implied_volatility)
                    }
                    
                    available_options.append(option_info)
            
            # 按到期日排序
            available_options.sort(key=lambda x: x["expiry_date"])
            
            self.logger.info(
                f"获取到 {len(available_options)} 个可用期权合约\n"
                f"首个合约信息:\n"
                f"  标的: {available_options[0]['symbol'] if available_options else 'N/A'}\n"
                f"  类型: {available_options[0]['type'] if available_options else 'N/A'}\n"
                f"  到期日: {available_options[0]['expiry_date'] if available_options else 'N/A'}"
            )
            
            return available_options
            
        except Exception as e:
            self.logger.error(f"获取可用期权合约时出错: {str(e)}")
            self.logger.exception("详细错误信息:")
            return []

    def _calculate_option_score(self, option: Dict[str, Any]) -> float:
        """计算期权得分"""
        try:
            score = 0.0
            
            # 1. 杠杆分数 (20-30倍最优)
            leverage = option['leverage']
            if 20 <= leverage <= 30:
                score += 30  # 杠杆在理想范围内
                # 更倾向于杠杆在25倍左右
                score -= abs(25 - leverage) * 0.5
            else:
                return 0  # 杠杆不在范围内直接排除
            
            # 2. 流动性分数 (25分)
            volume = option['volume']
            open_interest = option['open_interest']
            if volume > 1000 and open_interest > 5000:
                score += 25
            elif volume > 500 and open_interest > 2000:
                score += 15
            elif volume > 100 and open_interest > 1000:
                score += 5
            
            # 3. 隐含波动率分数 (20分)
            iv = option['implied_volatility']
            if 0.3 <= iv <= 0.7:  # 适中的隐含波动率
                score += 20
            elif 0.2 <= iv <= 0.8:
                score += 10
            
            # 4. Delta分数 (15分)
            delta = abs(option['delta'])
            if 0.3 <= delta <= 0.5:  # 适中的Delta
                score += 15
            elif 0.2 <= delta <= 0.6:
                score += 8
            
            # 5. 到期时间分数 (10分)
            days_to_expiry = (option['expiry_date'] - datetime.now(self.tz).date()).days
            if 14 <= days_to_expiry <= 21:  # 优先选择2-3周到期
                score += 10
            elif 7 <= days_to_expiry <= 30:
                score += 5
            
            return score
            
        except Exception as e:
            self.logger.error(f"计算期权得分时出错: {str(e)}")
            return 0.0

    async def _on_quote(self, symbol: str, quote: Dict[str, Any]):
        """行情回调"""
        try:
            self.logger.debug(f"收到行情: {symbol} {quote}")
            # 处理行情数据...
            
        except Exception as e:
            self.logger.error(f"处理行情数据出错: {str(e)}")

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

    async def _check_initial_entry(self, symbol: str, stock_symbol: str, 
                                 market_data: Dict[str, Any]) -> bool:
        """检查初始建仓条件"""
        try:
            conditions = self.position_sizing['initial']['conditions']
            
            # 1. 检查VIX
            if market_data['vix'] > conditions['vix_max']:
                return False
            
            # 2. 检查成交量
            if market_data['volume'] < conditions['min_volume']:
                return False
            
            # 3. 检查期权合约的特定条件
            option_data = await self._get_option_data(symbol)
            if not self._check_option_conditions(option_data):
                return False
            
            return True
            
        except Exception as e:
            self.logger.error(f"检查初始建仓条件时出错: {str(e)}")
            return False

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