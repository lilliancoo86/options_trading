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

class DoomsdayOptionStrategy:
    def __init__(self, config: Dict[str, Any]):
        """初始化策略"""
        self.logger = logging.getLogger(__name__)
        self.config = config
        self.tz = pytz.timezone('America/New_York')
        
        # 监控的标的
        self.symbols = ["TSLL.US", "NVDA.US", "AAPL.US"]
        
        # 交易参数
        self.params = {
            'min_iv_percentile': 30,    # 最小IV百分位
            'max_iv_percentile': 85,    # 最大IV百分位
            'min_volume': 100,          # 最小成交量
            'min_open_interest': 50,    # 最小持仓量
            'max_spread_pct': 15,       # 最大买卖价差百分比
            'max_position_size': 5,     # 每个标的最大持仓数量
            'max_loss_pct': 25,         # 最大止损比例
            'profit_target_pct': 50,    # 目标止盈比例
            'time_stop': '15:45',       # 最晚平仓时间
        }
        
        # 趋势判断参数
        self.trend_params = {
            'rsi_period': 14,           # RSI周期
            'rsi_overbought': 70,       # RSI超买
            'rsi_oversold': 30,         # RSI超卖
            'ma_fast': 5,               # 快速均线
            'ma_slow': 20,              # 慢速均线
            'volume_ma': 20,            # 成交量均线
            'vwap_dev_up': 1.5,         # VWAP上轨偏差
            'vwap_dev_down': 1.5,       # VWAP下轨偏差
            'vwap_period': 30,          # VWAP计算周期(分钟)
        }
        
        # 缓存数据
        self.price_cache = {}           # 价格缓存
        self.iv_cache = {}              # 隐波缓存
        self.signals = {}               # 交易信号缓存
        
        # 持仓管理
        self.positions = {}             # 当前持仓
        
        # 添加交易和行情上下文
        self.quote_ctx = QuoteContext(config)
        self.trade_ctx = TradeContext(config)
        
        # 添加期权订阅类型
        self.sub_types = [
            SubType.Quote,       # 实时报价
            SubType.Trade,       # 实时成交
            SubType.Depth,       # 盘口
            SubType.Greeks,      # 希腊字母
        ]
    
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
    
    async def execute_trade(self, option: Dict, side: OrderSide):
        """执行期权交易"""
        try:
            # 计算下单数量
            quantity = self._calculate_position_size(option)
            
            # 提交市价单
            order_resp = await self.trade_ctx.submit_order(
                symbol=option['symbol'],
                order_type=OrderType.MO,  # 市价单
                side=side,
                submitted_quantity=Decimal(str(quantity)),
                time_in_force=TimeInForceType.Day,
                remark="Doomsday Option Strategy"
            )
            
            self.logger.info(f"提交订单成功: {option['symbol']}, 方向: {side}, "
                            f"数量: {quantity}, 订单ID: {order_resp.order_id}")
            
            # 等待订单状态
            for i in range(5):
                await asyncio.sleep(1)
                order = await self.trade_ctx.order_detail(order_resp.order_id)
                self.logger.info(f"订单状态 ({i+1}/5): {order.status}")
                
                if order.status in ["filled", "partially_filled"]:
                    self.logger.info(f"订单执行成功: {option['symbol']}")
                    return True
                    
            return False
            
        except Exception as e:
            self.logger.error(f"执行交易失败: {str(e)}")
            return False
    
    def _calculate_position_size(self, option: Dict) -> int:
        """计算开仓数量"""
        try:
            # 获取期权价格
            price = (option['ask'] + option['bid']) / 2
            
            # 计算每手价值
            contract_value = price * 100  # 每张期权对应100股
            
            # 获取账户可用资金(示例)
            cash = Decimal('100000')  # 实际应该从账户获取
            
            # 计算最大可开仓数量
            max_contracts = int(cash / contract_value)
            
            # 根据风险限制计算实际开仓数量
            position_size = min(
                max_contracts,
                self.params['max_position_size']
            )
            
            return position_size
            
        except Exception as e:
            self.logger.error(f"计算开仓数量失败: {str(e)}")
            return 1
    
    async def run(self):
        """运行策略"""
        try:
            # 初始化数据
            await self.init_data()
            
            while True:
                # 检查是否在交易时段
                if not self._is_trading_time():
                    await asyncio.sleep(60)
                    continue
                    
                # 检查开仓信号
                signals = await self.check_entry_signals()
                
                if signals:
                    # 订阅期权行情
                    await self.subscribe_options(signals)
                    
                    for signal in signals:
                        # 确定交易方向
                        side = OrderSide.Buy if signal['type'].lower() == 'call' else OrderSide.Sell
                        
                        # 执行交易
                        success = await self.execute_trade(signal, side)
                        
                        if success:
                            # 记录持仓
                            self.positions[signal['symbol']] = {
                                'entry_price': signal['price'],
                                'quantity': signal['quantity'],
                                'side': side,
                                'entry_time': datetime.now(self.tz)
                            }
                
                # 检查持仓风险
                await self._check_positions()
                
                # 等待下一个检查周期
                await asyncio.sleep(60)
                
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
