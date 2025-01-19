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
        
        # 添加趋势判断参数
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
        
        # 缓存历史数据
        self.price_history = {}
        self.vwap_history = {}

    async def get_market_data(self) -> Dict[str, Any]:
        """获取市场数据"""
        try:
            market_data = {
                'vix': 0.0,
                'volatility': 0.0,
                'quotes': []
            }
            
            # 获取VIX
            vix_quotes = await self.quote_ctx.quote([self.vix_symbol])
            if vix_quotes:
                market_data['vix'] = float(vix_quotes[0].last_done)
            
            # 获取标的行情
            for symbol in self.symbols:
                if symbol == self.vix_symbol:
                    continue
                    
                quotes = await self.quote_ctx.quote([symbol])
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
        """检查是否为期权合约"""
        try:
            # 期权代码格式: XXXYYMMDD[C/P]NNN.US
            return bool(re.search(r'\d{6}[CP]\d+\.US$', symbol))
        except Exception as e:
            self.logger.error(f"检查期权代码时出错: {str(e)}")
            return False

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

    async def analyze_stock_trend(self, symbol: str) -> Dict[str, Any]:
        """分析股票趋势"""
        try:
            # 获取历史K线数据
            end_time = datetime.now(self.tz)
            start_time = end_time - timedelta(days=30)
            
            candlesticks = await self.quote_ctx.get_candlesticks(
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
            
            # 选择得分最高的期权
            best_option = max(filtered_options, key=lambda x: x['score'])
            
            self.logger.info(
                f"选择期权合约:\n"
                f"  代码: {best_option['symbol']}\n"
                f"  类型: {best_option['type']}\n"
                f"  价格: ${best_option['price']:.2f}\n"
                f"  杠杆率: {best_option['leverage']:.1f}x"
            )
            
            return best_option['symbol']
            
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
            
            # 遍历到期日(排除当日到期)
            for expiry_date in expiry_dates:
                expiry_date_obj = datetime.strptime(expiry_date, "%Y%m%d").date()
                if expiry_date_obj <= current_date:
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

    async def _on_quote(self, symbol: str, quote: Dict[str, Any]):
        """行情回调"""
        try:
            self.logger.debug(f"收到行情: {symbol} {quote}")
            # 处理行情数据...
            
        except Exception as e:
            self.logger.error(f"处理行情数据出错: {str(e)}")