"""
末日期权系统 - 日内交易策略模块
"""
from typing import Dict, List, Any, Optional
import logging
from datetime import datetime, timedelta
import pytz
from decimal import Decimal
import asyncio
import numpy as np
from longport.openapi import TradeContext, QuoteContext, SubType, OrderType, OrderSide
import aiohttp
from datetime import timezone
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
        
        # 添加期权策略参数
        self.params = {
            'min_days_to_expiry': 3,     # 最小到期天数
            'max_days_to_expiry': 14,    # 最大到期天数
            'min_delta': 0.3,            # 最小Delta
            'max_delta': 0.7,            # 最大Delta
            'min_theta': -0.1,           # 最小Theta
            'min_volume': 100,           # 最小成交量
            'min_open_interest': 500,    # 最小持仓量
            'max_spread_pct': 10,        # 最大价差百分比
            'min_iv_percentile': 20,     # 最小IV百分位
            'max_iv_percentile': 80,     # 最大IV百分位
            'momentum_threshold': 0.02,   # 动量阈值
            'entry_rsi_threshold': 60,    # 入场RSI阈值
            'gap_threshold': 2.0,        # 跳空阈值
            'premarket_weight': 1.5,     # 盘前权重
            'news_weight': 1.0,          # 新闻权重
            'volume_weight': 1.0,        # 成交量权重
            'max_position_size': 10      # 最大持仓数量
        }
        
        # 添加信号缓存
        self.signals = {
            symbol: {
                'options': [],           # 期权信号
                'trend': None,           # 趋势信号
                'momentum': None,        # 动量信号
                'volatility': None,      # 波动率信号
                'last_update': None      # 最后更新时间
            } for symbol in self.symbols
        }
        
        # 添加订阅类型
        self.sub_types = [
            SubType.QUOTE,              # 报价
            SubType.TRADE,              # 成交
            SubType.DEPTH,              # 深度
            SubType.OPTION_GREEKS       # 期权希腊字母
        ]
        
        # 添加缓存
        self.cache = {
            'news': {},        # 新闻缓存
            'sentiment': {},   # 情绪分析缓存
            'options': {},     # 期权链缓存
            'market': {},      # 市场数据缓存
        }
        
        # 添加波动率阈值配置
        self.volatility_threshold = {
            'high': 0.4,  # 高波动率阈值
            'low': 0.2    # 低波动率阈值
        }
        
        # 趋势判断参数
        self.trend_params = {
            'ma_fast': 5,          # 快速均线周期
            'ma_slow': 20,         # 慢速均线周期
            'volume_ma': 10,       # 成交量均线周期
            'rsi_period': 14,      # RSI周期
            'rsi_overbought': 70,  # RSI超买线
            'rsi_oversold': 30,    # RSI超卖线
            'ema_fast': 5,         # EMA快线
            'ema_mid': 13,         # EMA中线
            'ema_slow': 21,        # EMA慢线
            'vwap_period': 20,     # VWAP周期
            'vwap_dev_up': 2,      # VWAP上轨标准差倍数
            'vwap_dev_down': 2,    # VWAP下轨标准差倍数
            'momentum_period': 10   # 动量计算周期
        }
        
        # 缓存数据
        self.price_cache = {
            symbol: {
                'close': [],
                'volume': [],
                'high': [],
                'low': []
            } for symbol in self.symbols
        }
        self.iv_cache = {}              # 隐波缓存
        
        # 持仓管理
        self.positions = {}             # 当前持仓
        
        # 添加交易和行情上下文
        self.quote_ctx = QuoteContext(config)
        self.trade_ctx = TradeContext(config)
        
        # 添加关键词自动更新配置
        self.keyword_update = {
            'last_update': datetime.now(self.tz),
            'update_interval': 24 * 3600,  # 每天更新一次
            'min_correlation': 0.3,        # 最小相关系数
            'max_keywords': 50,            # 每类最大关键词数
            'history_days': 30,            # 分析历史天数
        }
        
        # 添加市场资讯监控配置
        self.market_news_config = {
            'sources': {
                'company_news': 2.0,      # 公司新闻权重
                'industry_news': 1.5,     # 行业新闻权重
                'market_news': 1.0,       # 市场新闻权重
                'analyst_ratings': 1.8    # 分析师评级权重
            },
            'update_interval': 300,       # 每5分钟更新一次
            'impact_threshold': 0.6,      # 影响力阈值
            'categories': {
                'earnings': 2.0,          # 财报相关
                'guidance': 1.8,          # 业绩指引
                'analyst': 1.5,           # 分析师评级
                'insider': 1.7,           # 内部交易
                'product': 1.3,           # 产品相关
                'partnership': 1.4,       # 合作关系
                'regulatory': 1.6         # 监管相关
            }
        }
        
        # 资讯缓存
        self.news_cache = {
            'market_data': {},
            'last_update': datetime.now(self.tz)
        }
        
        # 添加交易日志配置
        self.trade_log_config = {
            'log_file': 'logs/trade_log.json',
            'detail_levels': {
                'basic': ['symbol', 'type', 'price', 'quantity', 'time'],
                'signals': ['technical', 'news', 'market', 'option_flow'],
                'analysis': ['trend', 'sentiment', 'volatility'],
                'risk': ['position_size', 'account_risk', 'market_risk']
            }
        }
        
        # 添加分时段止盈止损配置
        self.time_based_exit = {
            'periods': {
                'open': {  # 开盘前30分钟(9:30-10:00)
                    'start': '09:30',
                    'end': '10:00',
                    'take_profit': 0.3,    # 30%止盈
                    'stop_loss': -0.15,    # 15%止损
                    'trailing_stop': 0.1    # 10%追踪止损
                },
                'morning': {  # 上午时段(10:00-12:00)
                    'start': '10:00',
                    'end': '12:00',
                    'take_profit': 0.5,     # 50%止盈
                    'stop_loss': -0.2,      # 20%止损
                    'trailing_stop': 0.15    # 15%追踪止损
                },
                'lunch': {  # 午间时段(12:00-13:00)
                    'start': '12:00',
                    'end': '13:00',
                    'take_profit': 0.4,     # 40%止盈
                    'stop_loss': -0.25,     # 25%止损
                    'trailing_stop': 0.2     # 20%追踪止损
                },
                'afternoon': {  # 下午时段(13:00-15:30)
                    'start': '13:00',
                    'end': '15:30',
                    'take_profit': 0.6,     # 60%止盈
                    'stop_loss': -0.25,     # 25%止损
                    'trailing_stop': 0.2     # 20%追踪止损
                },
                'close': {  # 收盘前30分钟(15:30-16:00)
                    'start': '15:30',
                    'end': '16:00',
                    'take_profit': 0.2,     # 20%止盈
                    'stop_loss': -0.1,      # 10%止损
                    'trailing_stop': 0.05    # 5%追踪止损
                }
            },
            'volatility_adjust': {
                'high': 1.2,    # 高波动率时提高阈值
                'low': 0.8      # 低波动率时降低阈值
            },
            'trend_adjust': {
                'strong': 1.2,  # 强趋势时提高止盈
                'weak': 0.8     # 弱趋势时降低止盈
            }
        }
        
        # 添加强制止损配置
        self.force_stop_loss = {
            'threshold': -0.10,  # 强制10%止损
            'enable': True,      # 启用强制止损
            'priority': 'high'   # 优先级高于其他止损条件
        }
    
    async def init_data(self):
        """初始化历史数据"""
        for symbol in self.symbols:
            # 获取历史K线数据
            await self._update_price_history(symbol)
            # 获取期权链数据
            await self._update_option_chain(symbol)
    
    async def _update_price_history(self, symbol: str):
        """更新价格历史数据"""
        try:
            # 获取日内1分钟K线数据
            klines = await self.quote_ctx.history_candlesticks(
                symbol=symbol,
                period="1m",
                count=100
            )
            
            if klines:
                self.price_cache[symbol] = {
                    'close': [k.close for k in klines],
                    'volume': [k.volume for k in klines],
                    'high': [k.high for k in klines],
                    'low': [k.low for k in klines]
                }
                
        except Exception as e:
            self.logger.error(f"更新{symbol}历史数据失败: {str(e)}")
    
    async def _update_option_chain(self, symbol: str):
        """更新期权链数据"""
        try:
            # 获取期权链
            chain = await self.quote_ctx.option_chain(
                symbol=symbol,
                expiry_date_list=[self._get_next_expiry()]
            )
            
            if chain:
                # 筛选符合条件的期权
                valid_options = self._filter_options(chain)
                self.signals[symbol] = {
                    'options': valid_options,
                    'last_update': datetime.now(self.tz)
                }
                
        except Exception as e:
            self.logger.error(f"更新{symbol}期权链失败: {str(e)}")
    
    def _filter_options(self, chain: List[Dict]) -> List[Dict]:
        """筛选符合条件的期权"""
        valid_options = []
        
        for option in chain:
            # 检查成交量和持仓量
            if (option['volume'] >= self.params['min_volume'] and 
                option['open_interest'] >= self.params['min_open_interest']):
                
                # 检查买卖价差
                spread_pct = (option['ask'] - option['bid']) / option['bid'] * 100
                if spread_pct <= self.params['max_spread_pct']:
                    
                    # 检查隐含波动率
                    if (self.params['min_iv_percentile'] <= option['iv_percentile'] <= 
                        self.params['max_iv_percentile']):
                        
                        valid_options.append(option)
        
        return valid_options
    
    async def check_entry_signals(self) -> List[Dict]:
        """检查开仓信号"""
        signals = []
        
        for symbol in self.symbols:
            # 更新数据
            await self._update_price_history(symbol)
            await self._update_option_chain(symbol)
            
            # 获取趋势信号
            trend = self._analyze_trend(symbol)
            
            if trend['signal'] in ['strong_buy', 'buy']:
                # 寻找看涨期权
                calls = self._find_best_options(symbol, 'call', trend)
                if calls:
                    signals.extend(calls)
                    
            elif trend['signal'] in ['strong_sell', 'sell']:
                # 寻找看跌期权
                puts = self._find_best_options(symbol, 'put', trend)
                if puts:
                    signals.extend(puts)
        
        return signals
    
    def _analyze_trend(self, symbol: str) -> Dict:
        """分析趋势"""
        try:
            data = self.price_cache[symbol]
            closes = data['close']
            volumes = data['volume']
            highs = data['high']
            lows = data['low']
            
            # 计算VWAP
            vwap_data = self._calculate_vwap(
                highs[-self.trend_params['vwap_period']:],
                lows[-self.trend_params['vwap_period']:],
                closes[-self.trend_params['vwap_period']:],
                volumes[-self.trend_params['vwap_period']:]
            )
            
            vwap = vwap_data['vwap']
            upper_band = vwap_data['upper_band']
            lower_band = vwap_data['lower_band']
            
            # 计算RSI
            rsi = self._calculate_rsi(closes)
            
            # 计算均线
            ma_fast = np.mean(closes[-self.trend_params['ma_fast']:])
            ma_slow = np.mean(closes[-self.trend_params['ma_slow']:])
            
            # 计算成交量均线
            vol_ma = np.mean(volumes[-self.trend_params['volume_ma']:])
            
            # 判断趋势
            trend = {
                'price_trend': 'neutral',
                'volume_trend': 'neutral',
                'vwap_trend': 'neutral',
                'signal': 'neutral',
                'strength': 0
            }
            
            current_price = closes[-1]
            
            # VWAP趋势判断
            if current_price > upper_band:
                trend['vwap_trend'] = 'strong_up'
                trend['strength'] += 2
            elif current_price > vwap:
                trend['vwap_trend'] = 'up'
                trend['strength'] += 1
            elif current_price < lower_band:
                trend['vwap_trend'] = 'strong_down'
                trend['strength'] -= 2
            elif current_price < vwap:
                trend['vwap_trend'] = 'down'
                trend['strength'] -= 1
            
            # 价格趋势判断
            if ma_fast > ma_slow * 1.02:  # 快线在慢线上方2%以上
                trend['price_trend'] = 'strong_up'
                trend['strength'] += 2
            elif ma_fast > ma_slow:
                trend['price_trend'] = 'up'
                trend['strength'] += 1
            elif ma_fast < ma_slow * 0.98:  # 快线在慢线下方2%以上
                trend['price_trend'] = 'strong_down'
                trend['strength'] -= 2
            elif ma_fast < ma_slow:
                trend['price_trend'] = 'down'
                trend['strength'] -= 1
            
            # RSI趋势判断
            if rsi >= self.trend_params['rsi_overbought']:
                if trend['strength'] > 0:  # 已经是上升趋势
                    trend['strength'] += 1  # 加强上升信号
                else:
                    trend['strength'] -= 1  # 可能超买
            elif rsi <= self.trend_params['rsi_oversold']:
                if trend['strength'] < 0:  # 已经是下降趋势
                    trend['strength'] -= 1  # 加强下降信号
                else:
                    trend['strength'] += 1  # 可能超卖
            
            # 成交量趋势判断
            current_volume = volumes[-1]
            if current_volume > vol_ma * 1.5:  # 成交量显著放大
                if trend['strength'] > 0:
                    trend['volume_trend'] = 'strong_up'
                    trend['strength'] += 2
                elif trend['strength'] < 0:
                    trend['volume_trend'] = 'strong_down'
                    trend['strength'] -= 2
            elif current_volume > vol_ma:
                if trend['strength'] > 0:
                    trend['volume_trend'] = 'up'
                    trend['strength'] += 1
                elif trend['strength'] < 0:
                    trend['volume_trend'] = 'down'
                    trend['strength'] -= 1
            
            # 综合信号判断
            if trend['strength'] >= 4:
                trend['signal'] = 'strong_buy'
            elif trend['strength'] >= 2:
                trend['signal'] = 'buy'
            elif trend['strength'] <= -4:
                trend['signal'] = 'strong_sell'
            elif trend['strength'] <= -2:
                trend['signal'] = 'sell'
            
            # 添加详细信息
            trend['details'] = {
                'current_price': current_price,
                'vwap': vwap,
                'upper_band': upper_band,
                'lower_band': lower_band,
                'rsi': rsi,
                'ma_fast': ma_fast,
                'ma_slow': ma_slow,
                'volume': current_volume,
                'volume_ma': vol_ma
            }
            
            return trend
            
        except Exception as e:
            self.logger.error(f"分析{symbol}趋势失败: {str(e)}")
            return {'signal': 'neutral', 'strength': 0}
    
    def _find_best_options(self, symbol: str, option_type: str, trend: Dict) -> List[Dict]:
        """寻找最佳期权"""
        try:
            options = self.signals[symbol]['options']
            valid_options = []
            
            for option in options:
                if option['type'].lower() == option_type:
                    score = self._calculate_option_score(option, trend)
                    if score > 0:
                        option['score'] = score
                        valid_options.append(option)
            
            # 按分数排序
            valid_options.sort(key=lambda x: x['score'], reverse=True)
            
            # 返回最佳的3个期权
            return valid_options[:3]
            
        except Exception as e:
            self.logger.error(f"寻找{symbol}最佳期权失败: {str(e)}")
            return []
    
    def _calculate_option_score(self, option: Dict, trend: Dict) -> float:
        """计算期权分数"""
        try:
            score = 0
            
            # 趋势强度得分
            score += abs(trend['strength']) * 2
            
            # 流动性得分
            score += min(option['volume'] / self.params['min_volume'], 5)
            score += min(option['open_interest'] / self.params['min_open_interest'], 5)
            
            # 价差得分
            spread_pct = (option['ask'] - option['bid']) / option['bid'] * 100
            score += (self.params['max_spread_pct'] - spread_pct) / 5
            
            # IV得分
            if 45 <= option['iv_percentile'] <= 65:  # IV在中位
                score += 3
            elif 35 <= option['iv_percentile'] <= 75:  # IV适中
                score += 2
            
            return max(0, score)
            
        except Exception as e:
            self.logger.error(f"计算期权分数失败: {str(e)}")
            return 0
    
    def _get_next_expiry(self) -> str:
        """获取下一个到期日"""
        today = datetime.now(self.tz)
        
        # 寻找7-30天之间的到期日
        expiry = today + timedelta(days=14)  # 优先选择两周后到期
        
        # 确保是周五
        while expiry.weekday() != 4:  # 4 = Friday
            expiry += timedelta(days=1)
        
        return expiry.strftime('%Y%m%d')
    
    def _calculate_rsi(self, prices: List[float], period: int = 14) -> float:
        """计算RSI"""
        try:
            deltas = np.diff(prices)
            gain = np.where(deltas > 0, deltas, 0)
            loss = np.where(deltas < 0, -deltas, 0)
            
            avg_gain = np.mean(gain[:period])
            avg_loss = np.mean(loss[:period])
            
            if avg_loss == 0:
                return 100
            
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            
            return rsi
            
        except Exception as e:
            self.logger.error(f"计算RSI失败: {str(e)}")
            return 50
    
    async def subscribe_options(self, options: List[Dict]):
        """订阅期权行情"""
        try:
            # 获取期权symbols
            symbols = [opt['symbol'] for opt in options]
            
            # 订阅行情
            await self.quote_ctx.subscribe(
                symbols=symbols,
                sub_types=self.sub_types
            )
            self.logger.info(f"订阅期权行情成功: {symbols}")
            
        except Exception as e:
            self.logger.error(f"订阅期权行情失败: {str(e)}")
    
    async def execute_trade(self, option: Dict, side: OrderSide, signals: Dict):
        """执行期权交易"""
        try:
            # 生成交易ID
            trade_id = f"{datetime.now(self.tz).strftime('%Y%m%d_%H%M%S')}_{option['symbol']}"
            
            # 记录交易前的信号和分析
            trade_log = {
                'trade_id': trade_id,
                'timestamp': datetime.now(self.tz).isoformat(),
                'action': 'BUY' if side == OrderSide.Buy else 'SELL',
                'symbol': option['symbol'],
                'option_type': 'CALL' if 'C' in option['symbol'] else 'PUT',
                'price': option['price'],
                'signals': {
                    'technical': {
                        'trend_score': signals['trend_score'],
                        'trend_direction': signals['trend_direction'],
                        'key_levels': signals['key_levels'],
                        'momentum': signals['momentum']
                    },
                    'market': {
                        'market_score': signals['market_score'],
                        'volatility': signals['volatility'],
                        'sector_performance': signals['sector_performance']
                    },
                    'news': {
                        'sentiment': signals['news_sentiment'],
                        'impact_score': signals['news_score'],
                        'key_events': signals['key_events']
                    },
                    'option_flow': {
                        'unusual_activity': signals['unusual_activity'],
                        'volume_surge': signals['volume_surge'],
                        'open_interest_change': signals['oi_change']
                    }
                },
                'reasons': [],  # 用于存储交易原因
                'risk_metrics': {
                    'position_size': None,
                    'account_risk': None,
                    'market_risk': None
                }
            }
            
            # 分析并记录交易原因
            trade_log['reasons'] = self._analyze_trade_reasons(signals)
            
            # 计算下单数量
            quantity = self._calculate_position_size(option)
            trade_log['quantity'] = quantity
            
            # 记录风险指标
            trade_log['risk_metrics'] = self._calculate_risk_metrics(option, quantity)
            
            # 执行交易
            order_resp = await self.trade_ctx.submit_order(
                symbol=option['symbol'],
                order_type=OrderType.MO,
                side=side,
                submitted_quantity=Decimal(str(quantity)),
                time_in_force=TimeInForceType.Day,
                remark=f"Trade ID: {trade_id}"
            )
            
            # 更新交易日志
            trade_log['order'] = {
                'order_id': order_resp.order_id,
                'status': 'SUBMITTED'
            }
            
            # 等待并记录订单执行
            for i in range(5):
                await asyncio.sleep(1)
                order = await self.trade_ctx.order_detail(order_resp.order_id)
                trade_log['order']['status'] = order.status
                
                if order.status in ["filled", "partially_filled"]:
                    trade_log['order']['filled_price'] = float(order.filled_price)
                    trade_log['order']['filled_quantity'] = int(order.filled_quantity)
                    
                    # 记录成功交易日志
                    self.logger.info(self._format_trade_log(trade_log))
                    await self._save_trade_log(trade_log)
                    return True
                    
            # 记录失败交易日志
            trade_log['order']['failure_reason'] = "Order timeout or rejected"
            self.logger.warning(self._format_trade_log(trade_log))
            await self._save_trade_log(trade_log)
            return False
            
        except Exception as e:
            self.logger.error(f"执行交易失败: {str(e)}")
            return False

    def _analyze_trade_reasons(self, signals: Dict) -> List[str]:
        """分析交易原因"""
        reasons = []
        
        # 技术分析原因
        if signals['trend_score'] >= 6:
            reasons.append(f"强劲上升趋势 (趋势得分: {signals['trend_score']})")
        elif signals['trend_score'] <= -6:
            reasons.append(f"强劲下降趋势 (趋势得分: {signals['trend_score']})")
        
        # 市场环境原因
        if abs(signals['market_score']) > 1.5:
            direction = "利好" if signals['market_score'] > 0 else "利空"
            reasons.append(f"市场环境{direction} (得分: {signals['market_score']:.2f})")
        
        # 新闻影响
        if signals['news_score'] != 0:
            sentiment = "正面" if signals['news_score'] > 0 else "负面"
            reasons.append(f"新闻情绪{sentiment} (得分: {signals['news_score']:.2f})")
            if signals['key_events']:
                reasons.append(f"关键事件: {', '.join(signals['key_events'][:2])}")
        
        # 期权异动
        if signals['unusual_activity']:
            reasons.append(f"期权异常活动 (成交量激增: {signals['volume_surge']}倍)")
        
        # 波动率机会
        if signals['volatility'].get('opportunity'):
            reasons.append(f"波动率机会: {signals['volatility']['description']}")
        
        return reasons

    def _format_trade_log(self, trade_log: Dict) -> str:
        """格式化交易日志"""
        log_lines = [
            f"\n{'='*50} 交易执行日志 {'='*50}",
            f"交易ID: {trade_log['trade_id']}",
            f"时间: {trade_log['timestamp']}",
            f"标的: {trade_log['symbol']} ({trade_log['option_type']})",
            f"操作: {trade_log['action']}",
            f"数量: {trade_log['quantity']}",
            f"价格: ${trade_log['price']:.2f}",
            "\n交易原因:",
        ]
        
        for i, reason in enumerate(trade_log['reasons'], 1):
            log_lines.append(f"{i}. {reason}")
        
        log_lines.extend([
            "\n信号详情:",
            f"技术分析: 趋势得分 {trade_log['signals']['technical']['trend_score']:.2f}",
            f"市场环境: 市场得分 {trade_log['signals']['market']['market_score']:.2f}",
            f"新闻情绪: {trade_log['signals']['news']['sentiment']} (得分: {trade_log['signals']['news']['impact_score']:.2f})",
            "\n风险指标:",
            f"仓位大小: {trade_log['risk_metrics']['position_size']}",
            f"账户风险: {trade_log['risk_metrics']['account_risk']:.2f}%",
            f"市场风险: {trade_log['risk_metrics']['market_risk']}",
            f"\n订单状态: {trade_log['order']['status']}",
            f"{'='*120}\n"
        ])
        
        return '\n'.join(log_lines)

    async def _save_trade_log(self, trade_log: Dict):
        """保存交易日志"""
        try:
            # 确保日志目录存在
            os.makedirs(os.path.dirname(self.trade_log_config['log_file']), exist_ok=True)
            
            # 读取现有日志
            existing_logs = []
            if os.path.exists(self.trade_log_config['log_file']):
                with open(self.trade_log_config['log_file'], 'r') as f:
                    existing_logs = json.load(f)
            
            # 添加新日志
            existing_logs.append(trade_log)
            
            # 保存日志
            with open(self.trade_log_config['log_file'], 'w') as f:
                json.dump(existing_logs, f, indent=2)
                
        except Exception as e:
            self.logger.error(f"保存交易日志失败: {str(e)}")
    
    def _calculate_position_size(self, option: Dict) -> int:
        """计算开仓数量"""
        try:
            # 获取账户信息
            account = self.trade_ctx.account_balance()
            available_cash = float(account.cash_balance)
            
            # 计算ATR
            highs = self.price_cache[option['symbol']]['high'][-20:]
            lows = self.price_cache[option['symbol']]['low'][-20:]
            closes = self.price_cache[option['symbol']]['close'][-20:]
            
            atr = self._calculate_atr(highs, lows, closes)
            
            # 计算每手风险
            option_price = (option['ask'] + option['bid']) / 2
            contract_value = option_price * 100  # 每张期权对应100股
            
            # 基于ATR的风险计算
            risk_per_contract = atr * 100  # ATR对应的每张合约风险
            
            # 计算可承受的最大亏损
            max_loss = available_cash * 0.02  # 最大承受2%账户亏损
            
            # 计算合适的合约数量
            position_size = int(max_loss / risk_per_contract)
            
            # 应用限制
            position_size = min(
                position_size,
                self.params['max_position_size'],
                int(available_cash * 0.1 / contract_value)  # 最多使用10%可用资金
            )
            
            return max(1, position_size)  # 至少开仓1张
            
        except Exception as e:
            self.logger.error(f"计算开仓数量失败: {str(e)}")
            return 1
    
    async def run(self):
        """运行策略"""
        try:
            # 初始化数据
            await self.init_data()
            
            while True:
                # 监控期权异动
                await self.monitor_option_activity()
                
                # 只在有异动标的时执行交易策略
                if self.active_symbols:
                    # 分析交易机会
                    opportunities = await self.find_trading_opportunities()
                    
                    # 过滤只保留异动标的的机会
                    active_opportunities = [
                        opp for opp in opportunities
                        if opp['symbol'] in [item['symbol'] for item in self.active_symbols]
                    ]
                    
                    # 执行交易
                    for opp in active_opportunities:
                        # 确定交易方向
                        side = OrderSide.Buy if opp['type'] == 'call' else OrderSide.Sell
                        
                        # 执行交易
                        success = await self.execute_trade(opp['option'], side, opp['context'])
                        
                        if success:
                            # 记录持仓
                            self.positions[opp['symbol']] = {
                                'entry_price': opp['option']['price'],
                                'quantity': opp['option']['quantity'],
                                'side': side,
                                'entry_time': datetime.now(self.tz)
                            }
                
                # 等待下一次扫描
                await asyncio.sleep(self.option_monitor['scan_interval'])
                
        except Exception as e:
            self.logger.error(f"策略运行错误: {str(e)}")
            
        finally:
            # 关闭上下文
            await self.quote_ctx.close()
            await self.trade_ctx.close()
    
    def _is_trading_time(self) -> bool:
        """检查是否在交易时段"""
        try:
            now = datetime.now(self.tz)
            
            # 检查是否是工作日
            if now.weekday() > 4:  # 周六日不交易
                return False
                
            # 获取当前时间字符串
            current_time = now.strftime('%H:%M')
            
            # 检查是否在交易时段 (美股常规交易时段 9:30-16:00)
            if '09:30' <= current_time <= '16:00':
                return True
                
            return False
            
        except Exception as e:
            self.logger.error(f"检查交易时段失败: {str(e)}")
            return False
    
    def _calculate_vwap(self, highs: List[float], lows: List[float], 
                       closes: List[float], volumes: List[float]) -> Dict:
        """计算VWAP和波动带"""
        try:
            # 计算典型价格
            typical_prices = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
            
            # 计算累计值
            cum_tp_vol = sum(tp * vol for tp, vol in zip(typical_prices, volumes))
            cum_vol = sum(volumes)
            
            # 计算VWAP
            vwap = cum_tp_vol / cum_vol if cum_vol > 0 else typical_prices[-1]
            
            # 计算标准差
            variance = sum((tp - vwap) ** 2 * vol for tp, vol in zip(typical_prices, volumes)) / cum_vol
            std_dev = np.sqrt(variance)
            
            # 计算波动带
            upper_band = vwap + (std_dev * self.trend_params['vwap_dev_up'])
            lower_band = vwap - (std_dev * self.trend_params['vwap_dev_down'])
            
            return {
                'vwap': vwap,
                'upper_band': upper_band,
                'lower_band': lower_band,
                'std_dev': std_dev
            }
            
        except Exception as e:
            self.logger.error(f"计算VWAP失败: {str(e)}")
            return {
                'vwap': closes[-1],
                'upper_band': closes[-1] * 1.02,
                'lower_band': closes[-1] * 0.98,
                'std_dev': 0
            }
    
    async def _check_positions(self):
        """检查持仓风险"""
        try:
            # 首先检查是否在交易时段
            if not self._is_trading_time():
                # 盘前盘后只更新数据，不执行交易
                for symbol in self.positions:
                    await self._update_position_data(symbol)
                return
            
            for symbol, position in list(self.positions.items()):
                # 更新持仓数据
                current_data = await self._update_position_data(symbol)
                if not current_data:
                    continue
                
                # 检查是否需要强制平仓（临近收盘）
                if self._should_force_close():
                    await self._close_position(position, "临近收盘强制平仓")
                    continue
                
                # 检查止损条件
                if await self._check_stop_loss(position):
                    await self._close_position(position, "触发止损")
                    continue
                
                # 检查止盈条件
                if await self._check_take_profit(position):
                    await self._close_position(position, "触发止盈")
                    continue
                
        except Exception as e:
            self.logger.error(f"检查持仓风险失败: {str(e)}")
    
    def _should_force_close(self) -> bool:
        """检查是否需要强制平仓"""
        try:
            now = datetime.now(self.tz)
            current_time = now.strftime('%H:%M')
            
            # 检查是否接近收盘时间
            if current_time >= self.params['time_stop']:
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"检查强制平仓时间失败: {str(e)}")
            return False
    
    async def _update_position_data(self, symbol: str) -> Optional[Dict]:
        """更新持仓数据"""
        try:
            # 获取最新行情
            quotes = await self.quote_ctx.quote([symbol])
            if not quotes:
                self.logger.error(f"获取{symbol}行情数据失败")
                return None
            
            quote = quotes[0]
            
            # 更新价格历史
            if symbol not in self.price_cache:
                self.price_cache[symbol] = {
                    'close': [],
                    'volume': [],
                    'high': [],
                    'low': []
                }
            
            # 只在交易时段更新价格历史
            if self._is_trading_time():
                self.price_cache[symbol]['close'].append(quote.last_done)
                self.price_cache[symbol]['volume'].append(quote.volume)
                self.price_cache[symbol]['high'].append(quote.high)
                self.price_cache[symbol]['low'].append(quote.low)
                
                # 保持固定长度
                max_length = max(
                    self.trend_params['ma_slow'],
                    self.trend_params['volume_ma'],
                    self.trend_params['vwap_period']
                )
                for key in self.price_cache[symbol]:
                    self.price_cache[symbol][key] = self.price_cache[symbol][key][-max_length:]
            
            return {
                'current_price': quote.last_done,
                'volume': quote.volume,
                'bid': quote.bid[0],
                'ask': quote.ask[0]
            }
            
        except Exception as e:
            self.logger.error(f"更新{symbol}持仓数据失败: {str(e)}")
            return None
    
    async def _close_position(self, position: Dict, reason: str):
        """平仓操作"""
        try:
            # 再次检查是否在交易时段
            if not self._is_trading_time():
                self.logger.warning(f"非交易时段，暂不执行平仓: {position['symbol']}")
                return False
            
            # 检查是否有足够的流动性
            quote = await self.quote_ctx.quote([position['symbol']])
            if not quote or not quote[0].bid or not quote[0].ask:
                self.logger.warning(f"流动性不足，暂不平仓: {position['symbol']}")
                return False
            
            # 计算滑点
            spread = (quote[0].ask[0] - quote[0].bid[0]) / quote[0].bid[0]
            if spread > self.params['max_spread_pct'] / 100:
                self.logger.warning(f"价差过大 ({spread:.2%})，暂不平仓: {position['symbol']}")
                return False
            
            # 执行平仓
            side = OrderSide.Sell if position['side'] == OrderSide.Buy else OrderSide.Buy
            success = await self.execute_trade({
                'symbol': position['symbol'],
                'price': quote[0].last_done
            }, side)
            
            if success:
                self.logger.info(f"平仓成功: {position['symbol']}, 原因: {reason}")
                del self.positions[position['symbol']]
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"执行平仓失败: {str(e)}")
            return False

    async def _analyze_stock_trend(self, symbol: str) -> Dict:
        """分析正股趋势"""
        try:
            data = self.price_cache[symbol]
            closes = np.array(data['close'])
            volumes = np.array(data['volume'])
            
            # 计算EMA
            ema_fast = self._calculate_ema(closes, self.trend_params['ema_fast'])
            ema_mid = self._calculate_ema(closes, self.trend_params['ema_mid'])
            ema_slow = self._calculate_ema(closes, self.trend_params['ema_slow'])
            
            # 计算MACD
            macd, signal, hist = self._calculate_macd(closes)
            
            # 计算动量
            momentum = (closes[-1] - closes[-self.trend_params['momentum_period']]) / closes[-self.trend_params['momentum_period']]
            
            # 计算RSI
            rsi = self._calculate_rsi(closes.tolist())
            
            # 计算VWAP
            vwap_data = self._calculate_vwap(
                data['high'][-self.trend_params['vwap_period']:],
                data['low'][-self.trend_params['vwap_period']:],
                data['close'][-self.trend_params['vwap_period']:],
                data['volume'][-self.trend_params['vwap_period']:]
            )
            
            # 趋势评分系统
            trend_score = 0
            
            # EMA趋势判断
            if ema_fast > ema_mid > ema_slow:
                trend_score += 2
            elif ema_fast > ema_mid:
                trend_score += 1
                
            # MACD判断
            if hist[-1] > 0 and hist[-1] > hist[-2]:
                trend_score += 2
            elif hist[-1] > 0:
                trend_score += 1
                
            # 动量判断
            if momentum > self.params['momentum_threshold']:
                trend_score += 2
                
            # RSI判断
            if rsi > self.params['entry_rsi_threshold']:
                trend_score += 1
                
            # VWAP判断
            if closes[-1] > vwap_data['vwap']:
                trend_score += 1
                
            # 成交量判断
            vol_ma = np.mean(volumes[-self.trend_params['volume_ma']:])
            if volumes[-1] > vol_ma * 1.2:  # 成交量放大20%
                trend_score += 1
                
            return {
                'symbol': symbol,
                'trend_score': trend_score,
                'is_uptrend': trend_score >= 6,  # 至少6分才确认上升趋势
                'momentum': momentum,
                'rsi': rsi,
                'last_price': closes[-1],
                'details': {
                    'ema_fast': ema_fast,
                    'ema_mid': ema_mid,
                    'ema_slow': ema_slow,
                    'macd': macd[-1],
                    'macd_signal': signal[-1],
                    'macd_hist': hist[-1],
                    'volume_ratio': volumes[-1] / vol_ma
                }
            }
            
        except Exception as e:
            self.logger.error(f"分析{symbol}趋势失败: {str(e)}")
            return None

    async def _find_best_option(self, stock_trend: Dict, option_type: str) -> Optional[Dict]:
        """查找最佳期权"""
        try:
            # 获取期权链
            chain = await self.quote_ctx.option_chain(
                symbol=stock_trend['symbol'],
                expiry_date_list=[self._get_next_expiry()]
            )
            
            if not chain:
                return None
            
            valid_options = []
            for option in chain:
                # 检查期权类型
                if option_type == 'call' and 'P' in option['symbol']:
                    continue
                if option_type == 'put' and 'C' in option['symbol']:
                    continue
                
                # 检查到期时间
                days_to_expiry = (option['expiry_date'] - datetime.now(self.tz)).days
                if not (self.params['min_days_to_expiry'] <= days_to_expiry <= self.params['max_days_to_expiry']):
                    continue
                    
                # 检查Delta
                if not (self.params['min_delta'] <= abs(option['delta']) <= self.params['max_delta']):
                    continue
                    
                # 检查Theta
                if option['theta'] < self.params['min_theta']:
                    continue
                    
                # 计算期权得分
                score = self._calculate_option_score(option, stock_trend)
                if score > 0:
                    option['score'] = score
                    valid_options.append(option)
            
            # 按得分排序
            valid_options.sort(key=lambda x: x['score'], reverse=True)
            
            return valid_options[0] if valid_options else None
            
        except Exception as e:
            self.logger.error(f"查找最佳期权失败: {str(e)}")
            return None

    def _calculate_option_score(self, option: Dict, stock_trend: Dict) -> float:
        """计算期权得分"""
        try:
            score = 0
            
            # 基础分数来自股票趋势
            score += stock_trend['trend_score'] * 2
            
            # Delta得分（偏好接近0.5的Delta）
            delta_score = 1 - abs(abs(option['delta']) - 0.5)
            score += delta_score * 3
            
            # Theta得分（偏好较小的时间衰减）
            theta_score = 1 - abs(option['theta']) / 0.1  # 假设-0.1是基准Theta
            score += theta_score * 2
            
            # 流动性得分
            score += min(option['volume'] / self.params['min_volume'], 5)
            score += min(option['open_interest'] / self.params['min_open_interest'], 5)
            
            # IV得分（偏好适中的IV）
            if 40 <= option['iv_percentile'] <= 60:
                score += 3
            elif 30 <= option['iv_percentile'] <= 70:
                score += 2
            
            return max(0, score)
            
        except Exception as e:
            self.logger.error(f"计算期权得分失败: {str(e)}")
            return 0

    async def analyze_market_context(self, symbol: str) -> Dict:
        """分析市场环境"""
        try:
            # 检查缓存
            cache_key = f"{symbol}_market"
            if cache_key in self.cache['market']:
                cached_data = self.cache['market'][cache_key]
                if (datetime.now() - cached_data['timestamp']).seconds < self.portai_config['cache_duration']:
                    return cached_data['data']
            
            context = {
                'premarket_change': 0,
                'news_sentiment': 'neutral',
                'volume_surge': False,
                'gap_detected': False,
                'market_status': 'normal',
                'score': 0,
                'details': {}
            }
            
            # 获取盘前数据
            premarket = await self.quote_ctx.quote([symbol])
            if premarket:
                quote = premarket[0]
                prev_close = float(quote.prev_close)
                current = float(quote.last_done)
                
                # 计算盘前涨跌幅
                context['premarket_change'] = (current - prev_close) / prev_close * 100
                
                # 添加更多市场细节
                context['details'].update({
                    'bid_ask_spread': (quote.ask[0] - quote.bid[0]) / quote.bid[0] * 100,
                    'volume': quote.volume,
                    'turnover': quote.turnover,
                    'high_low_range': (quote.high - quote.low) / quote.low * 100
                })
                
                # 计算加权得分
                if abs(context['premarket_change']) > self.params['gap_threshold']:
                    context['gap_detected'] = True
                    context['score'] += (context['premarket_change'] / self.params['gap_threshold']) * self.params['premarket_weight']
            
            # 获取新闻情绪
            sentiment_data = await self._get_cached_sentiment(symbol)
            if sentiment_data:
                context['news_sentiment'] = sentiment_data['sentiment']
                context['details']['news_count'] = sentiment_data['news_count']
                context['details']['sentiment_score'] = sentiment_data['score']
                
                # 加权新闻得分
                context['score'] += sentiment_data['score'] * self.params['news_weight']
            
            # 分析成交量
            volume_analysis = self._analyze_volume_pattern(symbol)
            if volume_analysis:
                context['volume_surge'] = volume_analysis['is_surge']
                context['details']['volume_pattern'] = volume_analysis['pattern']
                context['score'] += volume_analysis['score'] * self.params['volume_weight']
            
            # 更新缓存
            self.cache['market'][cache_key] = {
                'timestamp': datetime.now(),
                'data': context
            }
            
            return context
            
        except Exception as e:
            self.logger.error(f"分析市场环境失败: {str(e)}")
            return None

    async def _get_cached_sentiment(self, symbol: str) -> Optional[Dict]:
        """获取缓存的情绪分析结果"""
        try:
            cache_key = f"{symbol}_sentiment"
            now = datetime.now()
            
            # 检查缓存
            if cache_key in self.cache['sentiment']:
                cached_data = self.cache['sentiment'][cache_key]
                if (now - cached_data['timestamp']).seconds < self.portai_config['cache_duration']:
                    return cached_data['data']
            
            # 获取新闻
            news = await self._get_stock_news(symbol)
            if not news:
                return None
            
            # 分析情绪
            sentiment = await self._analyze_news_sentiment(news)
            
            # 准备结果
            result = {
                'sentiment': sentiment,
                'news_count': len(news),
                'score': 0
            }
            
            # 计算情绪得分
            if sentiment == 'positive':
                result['score'] = len(news) * 0.2  # 每条正面新闻0.2分
            elif sentiment == 'negative':
                result['score'] = -len(news) * 0.2  # 每条负面新闻-0.2分
            
            # 更新缓存
            self.cache['sentiment'][cache_key] = {
                'timestamp': now,
                'data': result
            }
            
            return result
            
        except Exception as e:
            self.logger.error(f"获取情绪分析缓存失败: {str(e)}")
            return None

    def _analyze_volume_pattern(self, symbol: str) -> Dict:
        """分析成交量模式"""
        try:
            volumes = np.array(self.price_cache[symbol]['volume'][-20:])  # 取最近20个成交量
            vol_ma = np.mean(volumes)
            vol_std = np.std(volumes)
            current_vol = volumes[-1]
            
            result = {
                'is_surge': False,
                'pattern': 'normal',
                'score': 0
            }
            
            # 检查成交量突破
            if current_vol > vol_ma + 2 * vol_std:
                result['is_surge'] = True
                result['pattern'] = 'strong_surge'
                result['score'] = 2
            elif current_vol > vol_ma + vol_std:
                result['is_surge'] = True
                result['pattern'] = 'surge'
                result['score'] = 1
            elif current_vol < vol_ma - vol_std:
                result['pattern'] = 'weak'
                result['score'] = -1
            
            # 检查成交量趋势
            vol_trend = np.polyfit(range(len(volumes)), volumes, 1)[0]
            if vol_trend > 0:
                result['score'] += 0.5
            elif vol_trend < 0:
                result['score'] -= 0.5
            
            return result
            
        except Exception as e:
            self.logger.error(f"分析成交量模式失败: {str(e)}")
            return None

    async def find_trading_opportunities(self) -> List[Dict]:
        """寻找交易机会"""
        opportunities = []
        
        for symbol in self.symbols:
            try:
                # 分析市场环境
                market_context = await self.analyze_market_context(symbol)
                if not market_context:
                    continue
                
                # 分析技术趋势
                stock_trend = await self._analyze_stock_trend(symbol)
                if not stock_trend:
                    continue
                
                # 分析市场资讯
                news_impact = await self.analyze_market_news(symbol)
                
                # 综合分析
                total_score = (
                    stock_trend['trend_score'] * 2.0 +  # 技术分析权重
                    market_context['score'] * 1.5 +     # 市场环境权重
                    news_impact['score']                # 资讯影响权重
                )
                
                # 上升趋势
                if (stock_trend['is_uptrend'] and 
                    market_context['score'] > 0 and 
                    news_impact['sentiment'] != 'negative'):
                    
                    best_call = await self._find_best_option(stock_trend, 'call')
                    if best_call:
                        opportunities.append({
                            'symbol': symbol,
                            'option': best_call,
                            'type': 'call',
                            'score': total_score,
                            'trend_score': stock_trend['trend_score'],
                            'market_score': market_context['score'],
                            'news_score': news_impact['score'],
                            'context': {
                                'market': market_context,
                                'news': news_impact
                            }
                        })
                
                # 下降趋势
                elif (stock_trend['trend_score'] <= -6 and 
                      market_context['score'] < 0 and 
                      news_impact['sentiment'] != 'positive'):
                    
                    best_put = await self._find_best_option(stock_trend, 'put')
                    if best_put:
                        opportunities.append({
                            'symbol': symbol,
                            'option': best_put,
                            'type': 'put',
                            'score': abs(total_score),
                            'trend_score': abs(stock_trend['trend_score']),
                            'market_score': abs(market_context['score']),
                            'news_score': news_impact['score'],
                            'context': {
                                'market': market_context,
                                'news': news_impact
                            }
                        })
            
            except Exception as e:
                self.logger.error(f"分析{symbol}交易机会失败: {str(e)}")
        
        # 按综合得分排序
        opportunities.sort(key=lambda x: x['score'], reverse=True)
        return opportunities

    async def _analyze_news_sentiment(self, news: List[Dict]) -> Dict:
        """分析新闻情绪"""
        try:
            sentiment_score = 0
            total_weight = 0
            news_impact = {
                'score': 0,
                'sentiment': 'neutral',
                'details': {
                    'recent_news': [],
                    'major_news': [],
                    'keywords_found': set()
                }
            }
            
            for item in news:
                # 计算时间权重
                news_time = datetime.fromisoformat(item['time'].replace('Z', '+00:00'))
                hours_ago = (datetime.now(timezone.utc) - news_time).total_seconds() / 3600
                
                if hours_ago <= 1:
                    time_weight = self.news_config['time_decay']['recent']
                elif hours_ago <= 3:
                    time_weight = self.news_config['time_decay']['hour']
                elif hours_ago <= 24:
                    time_weight = self.news_config['time_decay']['day']
                else:
                    time_weight = self.news_config['time_decay']['old']
                
                # 确定新闻源权重
                source_weight = self._get_source_weight(item.get('source', ''))
                
                # 分析标题
                title = item['title'].lower()
                title_score = 0
                
                # 检查强烈关键词
                for word in self.news_config['keywords']['strong_positive']:
                    if word in title:
                        title_score += self.news_config['weights']['title']['strong_positive']
                        news_impact['details']['keywords_found'].add(word)
                
                for word in self.news_config['keywords']['strong_negative']:
                    if word in title:
                        title_score += self.news_config['weights']['title']['strong_negative']
                        news_impact['details']['keywords_found'].add(word)
                
                # 检查普通关键词
                for word in self.news_config['keywords']['positive']:
                    if word in title:
                        title_score += self.news_config['weights']['title']['positive']
                        news_impact['details']['keywords_found'].add(word)
                
                for word in self.news_config['keywords']['negative']:
                    if word in title:
                        title_score += self.news_config['weights']['title']['negative']
                        news_impact['details']['keywords_found'].add(word)
                
                # 分析内容
                content = item['content'].lower()
                content_score = 0
                
                # 检查强烈关键词
                for word in self.news_config['keywords']['strong_positive']:
                    if word in content:
                        content_score += self.news_config['weights']['content']['strong_positive']
                
                for word in self.news_config['keywords']['strong_negative']:
                    if word in content:
                        content_score += self.news_config['weights']['content']['strong_negative']
                
                # 检查普通关键词
                for word in self.news_config['keywords']['positive']:
                    if word in content:
                        content_score += self.news_config['weights']['content']['positive']
                
                for word in self.news_config['keywords']['negative']:
                    if word in content:
                        content_score += self.news_config['weights']['content']['negative']
                
                # 计算该条新闻的总分
                news_score = (title_score + content_score) * time_weight * source_weight
                
                # 记录重要新闻
                if abs(news_score) >= self.news_config['threshold']['normal']:
                    news_info = {
                        'title': item['title'],
                        'time': news_time,
                        'score': news_score,
                        'source': item.get('source', 'unknown')
                    }
                    
                    if hours_ago <= 3:
                        news_impact['details']['recent_news'].append(news_info)
                    if source_weight > 1.0:
                        news_impact['details']['major_news'].append(news_info)
                
                sentiment_score += news_score
                total_weight += time_weight * source_weight
            
            # 计算最终情绪得分
            if total_weight > 0:
                final_score = sentiment_score / total_weight
                news_impact['score'] = final_score
                
                # 判断情绪强度
                if abs(final_score) >= self.news_config['threshold']['strong']:
                    news_impact['sentiment'] = 'strong_positive' if final_score > 0 else 'strong_negative'
                elif abs(final_score) >= self.news_config['threshold']['normal']:
                    news_impact['sentiment'] = 'positive' if final_score > 0 else 'negative'
            
            return news_impact
            
        except Exception as e:
            self.logger.error(f"分析新闻情绪失败: {str(e)}")
            return {'score': 0, 'sentiment': 'neutral', 'details': {}}

    def _get_source_weight(self, source: str) -> float:
        """获取新闻源权重"""
        source = source.lower()
        
        # 官方新闻源
        if any(x in source for x in ['ir.', 'investor.', 'press.', 'official']):
            return self.news_config['source_weight']['official']
        
        # 主流媒体
        major_sources = ['bloomberg', 'reuters', 'cnbc', 'wsj', 'ft.com', 
                        'marketwatch', 'barrons', 'yahoo finance']
        if any(x in source for x in major_sources):
            return self.news_config['source_weight']['major']
        
        # 普通来源
        return self.news_config['source_weight']['normal']

    async def monitor_option_activity(self):
        """监控期权异动"""
        try:
            # 获取当前阈值
            thresholds = self._get_current_thresholds()
            
            # 检查是否需要重置计数
            now = datetime.now(self.tz)
            if (now - self.monitor_state['last_reset']).seconds >= self.option_monitor['reset_interval']:
                self.monitor_state['continuous_signals'] = {}
                self.monitor_state['last_reset'] = now
            
            activity_scores = {}
            
            for symbol in self.symbols:
                # 获取标的特定的最小成交量
                min_volume = self.option_monitor['min_volume'].get(
                    symbol, 
                    self.option_monitor['min_volume']['default']
                )
                
                # 获取期权链
                chain = await self.quote_ctx.option_chain(
                    symbol=symbol,
                    expiry_date_list=[self._get_next_expiry()]
                )
                
                if not chain:
                    continue
                
                # 筛选近月期权
                active_options = self._filter_active_strikes(
                    chain, 
                    self.price_cache[symbol]['close'][-1], 
                    self.option_monitor['active_strikes']['normal']
                )
                
                # 计算异动得分
                total_score = 0
                unusual_count = 0
                
                for option in active_options:
                    # 获取期权成交量数据
                    volume_data = await self._get_option_volume_history(option['symbol'])
                    if not volume_data:
                        continue
                    
                    # 计算成交量均值和标准差
                    avg_volume = np.mean(volume_data)
                    std_volume = np.std(volume_data)
                    current_volume = volume_data[-1]
                    
                    # 检查是否有异动
                    if (current_volume > min_volume and 
                        current_volume > avg_volume * self.option_monitor['volume_threshold']['normal']):
                        # 计算异动分数
                        volume_ratio = current_volume / avg_volume
                        score = volume_ratio * (current_volume / min_volume)
                        
                        # 根据期权类型调整得分
                        if 'C' in option['symbol']:  # 看涨期权
                            total_score += score
                        else:  # 看跌期权
                            total_score -= score
                        
                        unusual_count += 1
                
                # 更新连续异动计数
                if unusual_count > 0:
                    if symbol not in self.monitor_state['continuous_signals']:
                        self.monitor_state['continuous_signals'][symbol] = {
                            'count': 1,
                            'direction': 'call' if total_score > 0 else 'put'
                        }
                    else:
                        prev_signal = self.monitor_state['continuous_signals'][symbol]
                        current_direction = 'call' if total_score > 0 else 'put'
                        
                        if prev_signal['direction'] == current_direction:
                            prev_signal['count'] += 1
                        else:
                            prev_signal['count'] = 1
                            prev_signal['direction'] = current_direction
                        
                        # 只有连续异动达到阈值才记录
                        if self.monitor_state['continuous_signals'][symbol]['count'] >= self.option_monitor['continuous_threshold']:
                            activity_scores[symbol] = {
                                'score': abs(total_score),
                                'direction': 'call' if total_score > 0 else 'put',
                                'unusual_count': unusual_count
                            }
            
            # 返回下一次扫描间隔
            return thresholds['interval']
            
        except Exception as e:
            self.logger.error(f"监控期权异动失败: {str(e)}")
            return self.option_monitor['scan_interval']['normal']

    def _filter_active_strikes(self, chain: List[Dict], current_price: float, 
                             num_strikes: int) -> List[Dict]:
        """筛选最接近现价的期权"""
        try:
            # 按行权价排序
            sorted_options = sorted(chain, key=lambda x: abs(x['strike_price'] - current_price))
            
            # 返回最接近的N个行权价的期权
            return sorted_options[:num_strikes * 2]  # 包括看涨和看跌
            
        except Exception as e:
            self.logger.error(f"筛选活跃期权失败: {str(e)}")
            return []

    async def _get_option_volume_history(self, option_symbol: str) -> List[float]:
        """获取期权成交量历史数据"""
        try:
            # 获取日内分钟K线数据
            klines = await self.quote_ctx.history_candlesticks(
                symbol=option_symbol,
                period="1m",
                count=30  # 获取最近30分钟数据
            )
            
            if klines:
                return [k.volume for k in klines]
            return []
            
        except Exception as e:
            self.logger.error(f"获取期权成交量历史失败: {str(e)}")
            return []

    def _get_current_thresholds(self) -> Dict:
        """获取当前时段的监控阈值"""
        try:
            now = datetime.now(self.tz)
            current_time = now.strftime('%H:%M')
            
            # 确定市场状态
            if '09:30' <= current_time <= '10:00':
                self.monitor_state['market_status'] = 'open'
                return {
                    'volume': self.option_monitor['volume_threshold']['open'],
                    'interval': self.option_monitor['scan_interval']['open'],
                    'strikes': self.option_monitor['active_strikes']['volatile']
                }
            elif '12:00' <= current_time <= '13:00':
                self.monitor_state['market_status'] = 'lunch'
                return {
                    'volume': self.option_monitor['volume_threshold']['lunch'],
                    'interval': self.option_monitor['scan_interval']['lunch'],
                    'strikes': self.option_monitor['active_strikes']['normal']
                }
            elif '15:30' <= current_time <= '16:00':
                self.monitor_state['market_status'] = 'close'
                return {
                    'volume': self.option_monitor['volume_threshold']['close'],
                    'interval': self.option_monitor['scan_interval']['normal'],
                    'strikes': self.option_monitor['active_strikes']['normal']
                }
            else:
                # 检查是否是活跃时段
                self.monitor_state['active_period'] = self._is_active_period()
                if self.monitor_state['active_period']:
                    return {
                        'volume': self.option_monitor['volume_threshold']['normal'] * 1.2,
                        'interval': self.option_monitor['scan_interval']['active'],
                        'strikes': self.option_monitor['active_strikes']['volatile']
                    }
                else:
                    return {
                        'volume': self.option_monitor['volume_threshold']['normal'],
                        'interval': self.option_monitor['scan_interval']['normal'],
                        'strikes': self.option_monitor['active_strikes']['normal']
                    }
                
        except Exception as e:
            self.logger.error(f"获取监控阈值失败: {str(e)}")
            return {
                'volume': self.option_monitor['volume_threshold']['normal'],
                'interval': self.option_monitor['scan_interval']['normal'],
                'strikes': self.option_monitor['active_strikes']['normal']
            }

    def _is_active_period(self) -> bool:
        """判断是否处于活跃时段"""
        try:
            # 检查最近的成交量和波动率
            active_symbols = 0
            
            for symbol in self.symbols:
                if symbol not in self.price_cache:
                    continue
                
                volumes = self.price_cache[symbol]['volume'][-10:]  # 最近10分钟
                prices = self.price_cache[symbol]['close'][-10:]
                
                # 计算成交量活跃度
                avg_volume = np.mean(volumes)
                current_volume = volumes[-1]
                
                # 计算价格波动率
                price_range = (max(prices) - min(prices)) / min(prices) * 100
                
                # 判断是否活跃
                if (current_volume > avg_volume * 1.5 or price_range > 0.5):
                    active_symbols += 1
            
            # 如果超过半数标的活跃，认为是活跃时段
            return active_symbols >= len(self.symbols) / 2
            
        except Exception as e:
            self.logger.error(f"判断活跃时段失败: {str(e)}")
            return False

    def _init_ml_model(self):
        """初始化机器学习模型"""
        try:
            import tensorflow as tf
            from sklearn.feature_extraction.text import TfidfVectorizer
            
            # 创建模型
            model = tf.keras.Sequential([
                tf.keras.layers.Dense(128, activation='relu'),
                tf.keras.layers.Dropout(0.2),
                tf.keras.layers.Dense(64, activation='relu'),
                tf.keras.layers.Dropout(0.2),
                tf.keras.layers.Dense(32, activation='relu'),
                tf.keras.layers.Dense(1, activation='sigmoid')
            ])
            
            # 编译模型
            model.compile(optimizer='adam',
                         loss='binary_crossentropy',
                         metrics=['accuracy'])
            
            # 加载已有模型（如果存在）
            if os.path.exists(self.ml_config['model_path']):
                model.load_weights(self.ml_config['model_path'])
            
            return {
                'model': model,
                'vectorizer': TfidfVectorizer(max_features=5000)
            }
            
        except Exception as e:
            self.logger.error(f"初始化机器学习模型失败: {str(e)}")
            return None

    async def _update_ml_model(self):
        """更新机器学习模型"""
        try:
            if not self.ml_model:
                return
            
            # 收集训练数据
            training_data = []
            for symbol in self.symbols:
                # 获取历史数据
                news_data = await self._get_historical_news(symbol, 30)
                price_data = await self._get_historical_prices(symbol, 30)
                
                if not news_data or not price_data:
                    continue
                
                # 准备特征和标签
                features = self._prepare_ml_features(news_data, price_data)
                labels = self._prepare_ml_labels(price_data)
                
                training_data.extend(zip(features, labels))
            
            if len(training_data) < self.ml_config['min_samples']:
                return
            
            # 分割特征和标签
            X, y = zip(*training_data)
            
            # 转换文本特征
            X_transformed = self.ml_model['vectorizer'].fit_transform(X)
            
            # 训练模型
            self.ml_model['model'].fit(
                X_transformed, 
                y,
                epochs=10,
                batch_size=32,
                validation_split=0.2
            )
            
            # 保存模型
            self.ml_model['model'].save_weights(self.ml_config['model_path'])
            
        except Exception as e:
            self.logger.error(f"更新机器学习模型失败: {str(e)}")

    async def _predict_sentiment(self, text: str) -> float:
        """使用机器学习模型预测情绪"""
        try:
            if not self.ml_model:
                return 0.5
            
            # 转换文本特征
            X = self.ml_model['vectorizer'].transform([text])
            
            # 预测
            prediction = self.ml_model['model'].predict(X)[0][0]
            
            return float(prediction)
            
        except Exception as e:
            self.logger.error(f"预测情绪失败: {str(e)}")
            return 0.5

    def _prepare_ml_features(self, news_data: List[Dict], price_data: List[Dict]) -> List[str]:
        """准备机器学习特征"""
        features = []
        for news in news_data:
            feature_text = f"{news['title']} {news['content']}"
            
            if self.ml_config['features']['price_change']:
                # 添加价格变化信息
                price_change = self._get_price_change_at_time(price_data, news['time'])
                feature_text += f" price_change_{price_change}"
            
            if self.ml_config['features']['volume_change']:
                # 添加成交量变化信息
                volume_change = self._get_volume_change_at_time(price_data, news['time'])
                feature_text += f" volume_change_{volume_change}"
            
            if self.ml_config['features']['news_keywords']:
                # 添加新闻关键词信息
                news_keywords = self._extract_news_keywords(news['title'] + ' ' + news['content'])
                feature_text += ' ' + ' '.join(news_keywords)
            
            if self.ml_config['features']['technical_indicators']:
                # 添加技术指标信息
                technical_indicators = self._extract_technical_indicators(price_data, news['time'])
                feature_text += ' ' + ' '.join(technical_indicators)
            
            features.append(feature_text)
        
        return features

    def _prepare_ml_labels(self, price_data: List[Dict]) -> List[int]:
        """准备机器学习标签"""
        labels = []
        for i in range(len(price_data) - 1):
            # 计算未来价格变化
            future_return = (price_data[i+1]['close'] - price_data[i]['close']) / price_data[i]['close']
            labels.append(1 if future_return > 0 else 0)
        
        return labels

    async def _update_keywords(self):
        """自动更新关键词库"""
        try:
            now = datetime.now(self.tz)
            if (now - self.keyword_update['last_update']).total_seconds() < self.keyword_update['update_interval']:
                return
            
            self.logger.info("开始更新关键词库...")
            new_keywords = {
                'strong_positive': set(),
                'positive': set(),
                'strong_negative': set(),
                'negative': set()
            }
            
            # 收集历史新闻和价格数据
            for symbol in self.symbols:
                # 获取历史新闻
                news_data = await self._get_historical_news(symbol, self.keyword_update['history_days'])
                
                # 获取历史价格数据
                price_data = await self._get_historical_prices(symbol, self.keyword_update['history_days'])
                
                if not news_data or not price_data:
                    continue
                
                # 分析关键词与价格变动的相关性
                correlations = self._analyze_keyword_correlations(news_data, price_data)
                
                # 根据相关性分类关键词
                for word, corr in correlations.items():
                    if corr >= self.keyword_update['min_correlation']:
                        new_keywords['strong_positive'].add(word)
                    elif 0.3 <= corr < 0.6:
                        new_keywords['positive'].add(word)
                    elif corr <= -0.6:
                        new_keywords['strong_negative'].add(word)
                    elif -0.6 < corr <= -0.3:
                        new_keywords['negative'].add(word)
            
            # 更新关键词库
            for category in new_keywords:
                # 保留原有关键词中最重要的一部分
                original_keywords = set(self.news_config['keywords'][category])
                combined_keywords = original_keywords.union(new_keywords[category])
                
                # 限制关键词数量
                if len(combined_keywords) > self.keyword_update['max_keywords']:
                    # 按重要性排序并截取
                    sorted_keywords = sorted(combined_keywords, 
                                          key=lambda x: self._calculate_keyword_importance(x),
                                          reverse=True)
                    self.news_config['keywords'][category] = sorted_keywords[:self.keyword_update['max_keywords']]
                else:
                    self.news_config['keywords'][category] = list(combined_keywords)
            
            self.keyword_update['last_update'] = now
            self.logger.info("关键词库更新完成")
            
        except Exception as e:
            self.logger.error(f"更新关键词库失败: {str(e)}")

    def _calculate_keyword_importance(self, word: str) -> float:
        """计算关键词重要性"""
        try:
            # 计算关键词在新闻中的出现次数
            news_count = sum(1 for news in self._get_historical_news(symbol, self.keyword_update['history_days']) for item in news if word in item['title'] or word in item['content'])
            
            # 计算关键词在价格数据中的出现次数
            price_count = sum(1 for price in self._get_historical_prices(symbol, self.keyword_update['history_days']) if word in price['title'] or word in price['content'])
            
            # 计算关键词的重要性
            importance = (news_count + price_count) / (self.keyword_update['max_keywords'] * 2)
            
            return importance
            
        except Exception as e:
            self.logger.error(f"计算关键词重要性失败: {str(e)}")
            return 0.0

    async def analyze_market_news(self, symbol: str) -> Dict:
        """分析市场资讯"""
        try:
            # 检查缓存
            if (symbol in self.news_cache['market_data'] and 
                (datetime.now(self.tz) - self.news_cache['market_data'][symbol]['timestamp']).seconds 
                < self.market_news_config['update_interval']):
                return self.news_cache['market_data'][symbol]['data']
            
            news_impact = {
                'score': 0,
                'sentiment': 'neutral',
                'signals': [],
                'key_events': [],
                'details': {
                    'company_news': [],
                    'analyst_ratings': [],
                    'sector_impact': []
                }
            }
            
            # 获取公司新闻
            company_news = await self._fetch_company_news(symbol)
            if company_news:
                impact = self._analyze_company_news(company_news)
                news_impact['score'] += impact['score'] * self.market_news_config['sources']['company_news']
                news_impact['details']['company_news'] = impact['events']
            
            # 获取分析师评级
            ratings = await self._fetch_analyst_ratings(symbol)
            if ratings:
                impact = self._analyze_ratings(ratings)
                news_impact['score'] += impact['score'] * self.market_news_config['sources']['analyst_ratings']
                news_impact['details']['analyst_ratings'] = impact['ratings']
            
            # 获取行业动态
            sector_news = await self._fetch_sector_news(symbol)
            if sector_news:
                impact = self._analyze_sector_impact(sector_news)
                news_impact['score'] += impact['score'] * self.market_news_config['sources']['industry_news']
                news_impact['details']['sector_impact'] = impact['events']
            
            # 确定整体情绪
            if news_impact['score'] >= self.market_news_config['impact_threshold']:
                news_impact['sentiment'] = 'positive'
            elif news_impact['score'] <= -self.market_news_config['impact_threshold']:
                news_impact['sentiment'] = 'negative'
            
            # 更新缓存
            self.news_cache['market_data'][symbol] = {
                'timestamp': datetime.now(self.tz),
                'data': news_impact
            }
            
            return news_impact
            
        except Exception as e:
            self.logger.error(f"分析市场资讯失败: {str(e)}")
            return {'score': 0, 'sentiment': 'neutral', 'signals': [], 'key_events': []}

    def _analyze_company_news(self, news: List[Dict]) -> Dict:
        """分析公司新闻"""
        try:
            impact = {
                'score': 0,
                'events': []
            }
            
            for item in news:
                event_score = 0
                category = self._categorize_news(item['title'], item['content'])
                
                # 应用类别权重
                if category in self.market_news_config['categories']:
                    event_score = self._calculate_news_score(item) * self.market_news_config['categories'][category]
                
                # 记录重要事件
                if abs(event_score) >= self.market_news_config['impact_threshold']:
                    impact['events'].append({
                        'title': item['title'],
                        'category': category,
                        'score': event_score,
                        'time': item['time']
                    })
                
                impact['score'] += event_score
            
            return impact
            
        except Exception as e:
            self.logger.error(f"分析公司新闻失败: {str(e)}")
            return {'score': 0, 'events': []}

    def _analyze_ratings(self, ratings: List[Dict]) -> Dict:
        """分析分析师评级"""
        try:
            impact = {
                'score': 0,
                'ratings': []
            }
            
            for rating in ratings:
                score = 0
                # 评级变化
                if rating['action'] == 'upgrade':
                    score = 1.0
                elif rating['action'] == 'downgrade':
                    score = -1.0
                elif rating['action'] == 'initiate':
                    score = 0.5 if rating['rating'] in ['buy', 'outperform'] else -0.5
                
                # 目标价变化
                if 'price_target_change' in rating:
                    pt_change = rating['price_target_change']
                    score += (pt_change / 100) * 0.5  # 目标价变化每1%影响0.5分
                
                impact['ratings'].append({
                    'firm': rating['firm'],
                    'action': rating['action'],
                    'rating': rating['rating'],
                    'score': score,
                    'time': rating['time']
                })
                
                impact['score'] += score
            
            return impact
            
        except Exception as e:
            self.logger.error(f"分析评级失败: {str(e)}")
            return {'score': 0, 'ratings': []}

    async def _check_exit_signals(self, position: Dict) -> Optional[str]:
        """检查平仓信号"""
        try:
            # 获取当前价格和计算收益率
            quote = await self.quote_ctx.quote([position['symbol']])
            if not quote:
                return None
                
            current_price = float(quote[0].last_done)
            
            # 计算收益率
            if position['side'] == OrderSide.Buy:
                return_rate = (current_price - position['entry_price']) / position['entry_price']
            else:
                return_rate = (position['entry_price'] - current_price) / position['entry_price']
            
            # 强制止损检查（优先级最高）
            if self.force_stop_loss['enable'] and return_rate <= self.force_stop_loss['threshold']:
                self.logger.warning(f"触发强制止损: {position['symbol']}, 收益率: {return_rate:.2%}")
                return 'force_stop_loss'
            
            # 获取当前时段的止盈止损设置
            period_settings = None
            for period, settings in self.time_based_exit['periods'].items():
                if settings['start'] <= datetime.now(self.tz).strftime('%H:%M') < settings['end']:
                    period_settings = settings
                    break
            
            if not period_settings:
                return None
                
            # 获取市场状态调整因子
            volatility_factor = await self._get_volatility_factor(position['symbol'])
            trend_factor = await self._get_trend_factor(position['symbol'])
            
            # 调整止盈止损阈值（但不调整强制止损阈值）
            take_profit = period_settings['take_profit'] * volatility_factor * trend_factor
            stop_loss = max(period_settings['stop_loss'] * volatility_factor, 
                           self.force_stop_loss['threshold'])  # 不允许止损设置低于强制止损
            trailing_stop = period_settings['trailing_stop'] * volatility_factor
            
            # 更新最高收益率
            if 'max_return' not in position:
                position['max_return'] = return_rate
            else:
                position['max_return'] = max(position['max_return'], return_rate)
            
            # 检查止盈信号
            if return_rate >= take_profit:
                return 'take_profit'
            
            # 检查常规止损信号
            if return_rate <= stop_loss:
                return 'stop_loss'
            
            # 检查追踪止损
            if position['max_return'] - return_rate >= trailing_stop:
                return 'trailing_stop'
            
            # 检查收盘平仓
            if datetime.now(self.tz).strftime('%H:%M') >= '15:45':  # 收盘前15分钟强制平仓
                return 'market_close'
            
            return None
            
        except Exception as e:
            self.logger.error(f"检查平仓信号失败: {str(e)}")
            return None

    async def _get_volatility_factor(self, symbol: str) -> float:
        """获取波动率调整因子"""
        try:
            # 计算当前波动率
            volatility = await self._calculate_volatility(symbol)
            
            if volatility > self.volatility_threshold['high']:
                return self.time_based_exit['volatility_adjust']['high']
            elif volatility < self.volatility_threshold['low']:
                return self.time_based_exit['volatility_adjust']['low']
            
            return 1.0
            
        except Exception as e:
            self.logger.error(f"获取波动率调整因子失败: {str(e)}")
            return 1.0

    async def _get_trend_factor(self, symbol: str) -> float:
        """获取趋势调整因子"""
        try:
            # 获取趋势强度
            trend = await self._analyze_stock_trend(symbol)
            
            if abs(trend['trend_score']) >= 8:
                return self.time_based_exit['trend_adjust']['strong']
            elif abs(trend['trend_score']) <= 4:
                return self.time_based_exit['trend_adjust']['weak']
            
            return 1.0
            
        except Exception as e:
            self.logger.error(f"获取趋势调整因子失败: {str(e)}")
            return 1.0

    async def _execute_exit(self, position: Dict, reason: str):
        """执行平仓"""
        try:
            # 准备平仓日志
            exit_log = {
                'symbol': position['symbol'],
                'entry_price': position['entry_price'],
                'exit_price': None,
                'hold_time': (datetime.now(self.tz) - position['entry_time']).total_seconds() / 60,
                'return_rate': None,
                'reason': reason,
                'market_context': await self.analyze_market_context(position['symbol'])
            }
            
            # 如果是强制止损，添加警告信息
            if reason == 'force_stop_loss':
                self.logger.warning(f"执行强制止损: {position['symbol']}")
                exit_log['warning'] = "触发强制止损保护"
            
            # 执行平仓
            close_side = OrderSide.Sell if position['side'] == OrderSide.Buy else OrderSide.Buy
            success = await self.execute_trade(
                {'symbol': position['symbol']},
                close_side,
                {'exit_reason': reason}
            )
            
            if success:
                # 更新平仓日志
                quote = await self.quote_ctx.quote([position['symbol']])
                if quote:
                    exit_price = float(quote[0].last_done)
                    exit_log['exit_price'] = exit_price
                    exit_log['return_rate'] = (
                        (exit_price - position['entry_price']) / position['entry_price']
                        if position['side'] == OrderSide.Buy
                        else (position['entry_price'] - exit_price) / position['entry_price']
                    )
                
                # 记录平仓日志
                self.logger.info(f"平仓成功: {self._format_exit_log(exit_log)}")
                
                # 如果是强制止损，可能需要暂停交易一段时间
                if reason == 'force_stop_loss':
                    await self._handle_force_stop_loss(position['symbol'])
                
                return True
                
            return False
            
        except Exception as e:
            self.logger.error(f"执行平仓失败: {str(e)}")
            return False

    async def _handle_force_stop_loss(self, symbol: str):
        """处理强制止损后的操作"""
        try:
            self.logger.warning(f"{symbol} 触发强制止损，进行风险评估...")
            
            # 这里可以添加一些风险控制逻辑
            # 例如：暂停该标的的交易、调整仓位大小、发送通知等
            
        except Exception as e:
            self.logger.error(f"处理强制止损失败: {str(e)}")

    def _format_exit_log(self, exit_log: Dict) -> str:
        """格式化平仓日志"""
        return (
            f"\n{'='*30} 平仓记录 {'='*30}\n"
            f"标的: {exit_log['symbol']}\n"
            f"持仓时间: {exit_log['hold_time']:.1f}分钟\n"
            f"入场价格: ${exit_log['entry_price']:.2f}\n"
            f"出场价格: ${exit_log['exit_price']:.2f}\n"
            f"收益率: {exit_log['return_rate']*100:.1f}%\n"
            f"平仓原因: {exit_log['reason']}\n"
            f"市场环境: {exit_log['market_context']['description']}\n"
            f"{'='*70}"
        )
