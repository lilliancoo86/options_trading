"""
持仓管理模块
负责管理交易持仓、风险控制和资金管理
"""
from typing import Dict, List, Any, Optional
import logging
import os
from datetime import datetime, timedelta
import pytz
from decimal import Decimal
from dotenv import load_dotenv
from longport.openapi import TradeContext, QuoteContext, Config, SubType, OrderType, OrderSide, TimeInForceType
from tabulate import tabulate
import asyncio

class DoomsdayPositionManager:
    def __init__(self, config: Dict[str, Any]):
        """
        初始化持仓管理器
        
        Args:
            config: 配置字典，包含交易和风险控制参数
        """
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.positions = {}  # 当前持仓
        self.daily_pnl = Decimal('0')  # 每日盈亏
        self.tz = pytz.timezone('America/New_York')
        
        # 初始化Longport配置
        load_dotenv()  # 加载环境变量
        self.app_key = os.getenv('LONGPORT_APP_KEY')
        self.app_secret = os.getenv('LONGPORT_APP_SECRET')
        self.access_token = os.getenv('LONGPORT_ACCESS_TOKEN')
        self.trade_env = os.getenv('TRADE_ENV', 'PAPER')
        
        if not all([self.app_key, self.app_secret, self.access_token]):
            raise ValueError("缺少必要的Longport配置，请检查环境变量")
        
        # 初始化Longport配置
        self.longport_config = Config(
            app_key=self.app_key,
            app_secret=self.app_secret,
            access_token=self.access_token
        )
        
        # 初始化交易和行情上下文
        self._trade_ctx = None
        self._quote_ctx = None
        
        try:
            # 创建并验证交易上下文
            self._trade_ctx = TradeContext(self.longport_config)
            self._trade_ctx.account_balance()
            self.logger.debug("交易上下文初始化成功")
            
            # 创建并验证行情上下文
            self._quote_ctx = QuoteContext(self.longport_config)
            # 测试行情订阅
            self._quote_ctx.subscribe(
                symbols=["AAPL.US"],  # 使用一个常见股票测试
                sub_types=[SubType.Quote],
                is_first_push=False  # 不需要首次推送
            )
            self._quote_ctx.unsubscribe(
                symbols=["AAPL.US"],
                sub_types=[SubType.Quote]
            )
            self.logger.debug("行情上下文初始化成功")
            
        except Exception as e:
            self.logger.error(f"上下文初始化失败: {str(e)}")
            if self._trade_ctx:
                self._trade_ctx = None
            if self._quote_ctx:
                self._quote_ctx = None
        
        # 从配置中获取限制
        self.position_limits = config['position_sizing']
        self.risk_limits = config['risk_limits']
        
        # 初始化统计数据
        self.stats = {
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'max_drawdown': Decimal('0'),
            'peak_value': Decimal('0'),
            'current_value': Decimal('0')
        }
        
        # 添加移动止损配置
        self.trailing_stop = {
            'activation_return': Decimal('0.10'),  # 10%收益激活移动止损
            'trailing_distance': Decimal('0.05'),  # 5%移动止损距离
            'min_profit_lock': Decimal('0.05')    # 5%最小锁定收益
        }
        
        # 设置日志级别为 DEBUG
        self.logger.setLevel(logging.DEBUG)

    async def init_contexts(self):
        """初始化交易和行情上下文"""
        try:
            # 创建交易上下文
            self._trade_ctx = TradeContext(self.longport_config)
            # 创建行情上下文
            self._quote_ctx = QuoteContext(self.longport_config)
            
            # 验证交易上下文 (同步调用)
            self._trade_ctx.account_balance()
            self.logger.info("交易上下文初始化成功")
            
            # 验证行情上下文 (同步调用)
            try:
                # 使用基本报价类型进行测试
                self._quote_ctx.subscribe(
                    symbols=["US.AAPL"],
                    sub_types=[SubType.Quote]
                )
                self._quote_ctx.unsubscribe(
                    symbols=["US.AAPL"],
                    sub_types=[SubType.Quote]
                )
                self.logger.info("行情上下文初始化成功")
            except Exception as e:
                self.logger.error(f"行情上下文验证失败: {str(e)}")
                raise
            
        except Exception as e:
            self.logger.error(f"初始化Longport上下文失败: {str(e)}")
            self.close_contexts()  # 改为同步调用
            raise

    def close_contexts(self):
        """关闭所有上下文"""
        try:
            if self._trade_ctx:
                self._trade_ctx = None
                self.logger.info("交易上下文已关闭")
            
            if self._quote_ctx:
                self._quote_ctx = None
                self.logger.info("行情上下文已关闭")
                
        except Exception as e:
            self.logger.error(f"关闭Longport上下文时出错: {str(e)}")

    @property
    def trade_ctx(self) -> TradeContext:
        """获取交易上下文"""
        if self._trade_ctx is None:
            self._trade_ctx = TradeContext(self.longport_config)
            # 验证交易上下文
            try:
                self._trade_ctx.account_balance()
                self.logger.debug("交易上下文初始化成功")
            except Exception as e:
                self.logger.error(f"交易上下文初始化失败: {str(e)}")
                self._trade_ctx = None
                raise
        return self._trade_ctx
    
    @property
    def quote_ctx(self) -> QuoteContext:
        """获取行情上下文"""
        if self._quote_ctx is None:
            try:
                self._quote_ctx = QuoteContext(self.longport_config)
                # 测试行情订阅以验证上下文
                self._quote_ctx.subscribe(
                    symbols=["AAPL.US"],
                    sub_types=[SubType.Quote],
                    is_first_push=False
                )
                self._quote_ctx.unsubscribe(
                    symbols=["AAPL.US"],
                    sub_types=[SubType.Quote]
                )
                self.logger.debug("行情上下文初始化成功")
            except Exception as e:
                self.logger.error(f"行情上下文初始化失败: {str(e)}")
                raise
        return self._quote_ctx

    async def can_open_position(self, symbol: str, vix_level: float) -> bool:
        """
        检查是否可以开新仓位
        
        Args:
            symbol: 交易标的代码
            vix_level: 当前VIX指数水平
            
        Returns:
            bool: 是否可以开仓
        """
        try:
            # 检查是否是期权
            if not (symbol.endswith('.US') and ('C' in symbol or 'P' in symbol)):
                self.logger.warning(f"不是期权合约: {symbol}")
                return False
            
            # 检查是否是监控标的的期权
            base_symbols = [s.split('.')[0] for s in self.config.get('symbols', [])]
            option_base = symbol.split('C')[0].split('P')[0]  # 获取期权的基础资产代码
            if option_base not in base_symbols:
                self.logger.warning(f"不是监控标的的期权: {symbol}")
                return False
            
            # 检查持仓数量限制
            max_positions = self.position_limits.get('size_limit', {}).get('max', 5)
            
            # 获取实际持仓数据
            real_positions = await self.get_real_positions()
            if real_positions is None:
                self.logger.error("无法获取实际持仓数据")
                return False
            
            active_positions = real_positions.get("active", [])
            
            # 检查总持仓数量
            if len(active_positions) >= max_positions:
                self.logger.warning(f"达到最大持仓数量限制: {max_positions}")
                return False

            # 检查VIX限制
            volatility_limits = self.risk_limits.get('volatility', {})
            min_vix = volatility_limits.get('min_vix', 15)
            max_vix = volatility_limits.get('max_vix', 40)
            if not (min_vix <= vix_level <= max_vix):
                self.logger.warning(f"VIX超出允许范围: {vix_level} (限制: {min_vix}-{max_vix})")
                return False

            # 检查是否已持有该标的
            for pos in active_positions:
                if pos["symbol"] == symbol:
                    self.logger.warning(f"已持有该期权: {symbol}")
                    return False

            # 计算当前总持仓市值
            total_value = sum(float(pos.get("value", 0)) for pos in active_positions)
            
            # 检查总持仓市值限制
            max_total_value = self.position_limits.get('value_limit', {}).get('max', 100000)
            if total_value >= max_total_value:
                self.logger.warning(f"达到总持仓市值限制: {total_value:.2f}")
                return False

            return True
            
        except Exception as e:
            self.logger.error(f"检查开仓条件时出错: {str(e)}")
            return False

    async def open_position(self, order: Dict[str, Any]) -> bool:
        """开仓"""
        try:
            symbol = order['symbol']
            quantity = order['quantity']
            
            # 检查是否可以开仓
            if not await self.can_open_position(symbol, order.get('vix', 20)):
                self.logger.warning(f"不满足开仓条件: {symbol}")
                return False
            
            # 执行开仓操作
            try:
                # 获取最新行情
                quote = self.quote_ctx.quote([symbol])
                if not quote:
                    self.logger.error("无法获取行情数据")
                    return False
                    
                current_quote = quote[0]
                bid_price = float(current_quote.bid_price)
                ask_price = float(current_quote.ask_price)
                
                # 检查买卖价差
                spread = (ask_price - bid_price) / bid_price
                max_spread = self.config.get('order_config', {}).get('price_limit', {}).get('max_spread', 0.05)  # 期权允许更大的价差
                
                if spread > max_spread:
                    self.logger.warning(f"期权买卖价差过大: {spread:.2%} > {max_spread:.2%}")
                    return False
                
                # 提交市价单
                submit_order_resp = self.trade_ctx.submit_order(
                    symbol=symbol,
                    order_type=OrderType.MO,  # 市价单
                    side=OrderSide.Buy,       # 买入
                    submitted_quantity=quantity,
                    time_in_force=TimeInForceType.Day,  # 当日有效
                    remark="DoomsdayOption"  # 订单备注
                )
                
                # 检查订单提交结果
                if submit_order_resp and hasattr(submit_order_resp, 'order_id'):
                    order_id = submit_order_resp.order_id
                    self.logger.info(f"市价单提交成功: {order_id}")
                    
                    # 等待订单成交
                    max_wait = self.config.get('order_config', {}).get('max_wait_seconds', 5)  # 市价单等待时间可以短一些
                    for _ in range(max_wait):
                        # 查询订单状态
                        order_detail = self.trade_ctx.order_detail(order_id)
                        if order_detail:
                            status = order_detail.status
                            if status == "Filled":  # 完全成交
                                executed_price = float(order_detail.executed_price)
                                executed_quantity = int(order_detail.executed_quantity)
                                
                                # 记录持仓信息
                                self.positions[symbol] = {
                                    'entry_time': datetime.now(),
                                    'entry_price': executed_price,
                                    'quantity': executed_quantity,
                                    'current_price': executed_price,
                                    'high_price': executed_price,
                                    'stop_price': executed_price * (1 - self.trailing_stop['trailing_distance']),
                                    'status': 'active',
                                    'order_id': order_id
                                }
                                
                                self.logger.info(f"期权开仓成功: {symbol}, 数量: {executed_quantity}张, 成交价: ${executed_price:.3f}")
                                return True
                                
                            elif status in ["Failed", "Rejected", "Cancelled"]:
                                self.logger.error(f"期权订单失败: {status}")
                                return False
                                
                        await asyncio.sleep(1)
                    
                    # 超时处理
                    self.logger.warning(f"期权订单等待超时: {order_id}")
                    # 市价单通常不需要撤单，但为了安全起见还是撤一下
                    self.trade_ctx.cancel_order(order_id)
                    return False
                    
                else:
                    self.logger.error("期权订单提交失败")
                    return False
                    
            except Exception as e:
                self.logger.error(f"执行期权开仓操作失败: {str(e)}")
                return False
                
        except Exception as e:
            self.logger.error(f"期权开仓失败: {str(e)}")
            return False

    def close_position(self, symbol: str, current_price: float) -> Dict[str, Any]:
        """
        平仓
        
        Args:
            symbol: 交易标的代码
            current_price: 当前价格
            
        Returns:
            Dict: 平仓信息
        """
        try:
            if symbol not in self.positions:
                return {'success': False, 'error': 'Position not found'}
            
            position = self.positions[symbol]
            exit_price = Decimal(str(current_price))
            
            # 计算盈亏
            pnl = (exit_price - position['entry_price']) * Decimal(str(position['quantity']))
            self.daily_pnl += pnl
            
            # 更新统计数据
            if pnl > 0:
                self.stats['winning_trades'] += 1
            else:
                self.stats['losing_trades'] += 1
            
            # 更新最大回撤
            self.stats['current_value'] += pnl
            if self.stats['current_value'] > self.stats['peak_value']:
                self.stats['peak_value'] = self.stats['current_value']
            drawdown = (self.stats['peak_value'] - self.stats['current_value']) / self.stats['peak_value']
            if drawdown > self.stats['max_drawdown']:
                self.stats['max_drawdown'] = drawdown
            
            # 删除持仓
            del self.positions[symbol]
            
            return {
                'success': True,
                'symbol': symbol,
                'quantity': position['quantity'],
                'entry_price': position['entry_price'],
                'exit_price': exit_price,
                'pnl': pnl,
                'holding_time': datetime.now(self.tz) - position['entry_time']
            }
            
        except Exception as e:
            self.logger.error(f"平仓失败: {str(e)}")
            return {'success': False, 'error': str(e)}

    def should_close_position(self, symbol: str, current_price: float) -> bool:
        """检查是否应该平仓"""
        try:
            if symbol not in self.positions:
                return False
            
            position = self.positions[symbol]
            entry_price = position['entry_price']
            
            # 计算盈亏比例
            pnl_ratio = (current_price - entry_price) / entry_price
            
            # 获取期权特定的止损止盈设置
            option_risk = self.risk_limits.get('option', {})
            stop_loss = option_risk.get('stop_loss', {})
            
            # 期权特定的止损设置 - 更严格的止损比例
            initial_stop = stop_loss.get('initial', 0.10)     # 最大止损10%
            trailing_stop = stop_loss.get('trailing', 0.07)   # 移动止损7%
            time_based_stop = stop_loss.get('time_based', 0.05)  # 基于时间的止损5%
            max_holding_time = option_risk.get('max_holding_time', 60)  # 最大持仓时间60分钟
            
            # 计算持仓时间（分钟）
            holding_time = (datetime.now() - position['entry_time']).total_seconds() / 60
            
            # 1. 固定止损检查 - 最高优先级
            if pnl_ratio < -initial_stop:
                self.logger.info(f"{symbol} 触发固定止损: {pnl_ratio:.2%} (止损线: -{initial_stop:.1%})")
                return True
            
            # 2. 移动止盈检查
            if pnl_ratio > trailing_stop:
                # 更新移动止损价格
                position['stop_price'] = current_price * (1 - trailing_stop)
                position['high_price'] = max(current_price, position.get('high_price', current_price))
                
                # 计算从最高点回撤的比例
                drawdown = (position['high_price'] - current_price) / position['high_price']
                if drawdown > trailing_stop:
                    self.logger.info(f"{symbol} 触发移动止盈: 从最高点回撤 {drawdown:.2%}")
                    return True
            
            # 3. 时间止损检查
            if holding_time > max_holding_time:
                self.logger.info(f"{symbol} 触发时间止损: 持仓时间 {holding_time:.1f}分钟")
                return True
            
            # 4. 基于时间的动态止损
            time_ratio = holding_time / max_holding_time
            dynamic_stop = min(time_based_stop * (1 + time_ratio), 0.10)  # 确保不超过10%
            if pnl_ratio < -dynamic_stop:
                self.logger.info(f"{symbol} 触发时间动态止损: {pnl_ratio:.2%} (阈值: {-dynamic_stop:.2%})")
                return True
            
            # 5. 盈利目标检查
            take_profit = option_risk.get('take_profit', 0.30)  # 降低盈利目标到30%
            if pnl_ratio > take_profit:
                self.logger.info(f"{symbol} 达到盈利目标: {pnl_ratio:.2%}")
                return True
            
            # 记录当前持仓状态
            if not hasattr(self, '_last_position_log') or \
               (datetime.now() - self._last_position_log).seconds >= 60:
                self.logger.info(f"\n=== 持仓状态 [{symbol}] ===")
                status = {
                    "持仓时间": f"{holding_time:.1f}分钟",
                    "当前盈亏": f"{pnl_ratio:.2%}",
                    "止损线": f"{-initial_stop:.1%}",
                    "动态止损": f"{-dynamic_stop:.1%}",
                    "移动止盈": f"{trailing_stop:.1%}"
                }
                table = tabulate(
                    [status],
                    headers="keys",
                    tablefmt="grid",
                    numalign="right",
                    stralign="left"
                )
                self.logger.info(f"\n{table}")
                self._last_position_log = datetime.now()
            
            return False
            
        except Exception as e:
            self.logger.error(f"检查平仓条件时发生错误: {str(e)}")
            return False

    def force_close_all(self) -> List[Dict[str, Any]]:
        """
        强制平掉所有持仓
        
        Returns:
            List[Dict]: 平仓结果列表
        """
        closed_positions = []
        for symbol in list(self.positions.keys()):
            try:
                # TODO: 获取实时价格
                current_price = self.positions[symbol]['entry_price']  # 临时使用入场价
                result = self.close_position(symbol, float(current_price))
                if result['success']:
                    closed_positions.append(result)
            except Exception as e:
                self.logger.error(f"强制平仓失败 {symbol}: {str(e)}")
        
        return closed_positions

    def get_all_positions(self) -> Dict[str, Dict[str, Any]]:
        """获取所有当前持仓"""
        positions_with_time = {}
        for symbol, pos in self.positions.items():
            pos_copy = pos.copy()
            # 确保有 holding_time 字段
            if 'entry_time' in pos_copy:
                pos_copy['holding_time'] = datetime.now() - pos_copy['entry_time']
            else:
                pos_copy['holding_time'] = timedelta(0)  # 默认持仓时间为0
            positions_with_time[symbol] = pos_copy
        return positions_with_time

    def get_position_stats(self) -> Dict[str, Any]:
        """
        获取持仓统计信息
        
        Returns:
            Dict: 统计信息字典
        """
        stats = {
            'total_trades': self.stats['total_trades'],
            'winning_trades': self.stats['winning_trades'],
            'losing_trades': self.stats['losing_trades'],
            'win_rate': self.stats['winning_trades'] / self.stats['total_trades'] if self.stats['total_trades'] > 0 else 0,
            'max_drawdown': float(self.stats['max_drawdown']),
            'daily_pnl': float(self.daily_pnl),
            'current_positions': len(self.positions)
        }
        
        # 添加移动止损相关统计
        stats['positions_with_trailing_stop'] = sum(
            1 for pos in self.positions.values()
            if (pos.get('high_price', pos['entry_price']) - pos['entry_price']) / pos['entry_price']
            >= self.trailing_stop['activation_return']
        )
        
        return stats

    async def get_real_positions(self):
        """获取实际的持仓数据"""
        try:
            positions_data = {
                "active": [],
                "closed": []
            }
            
            try:
                # 确保交易上下文已初始化
                if self._trade_ctx is None:
                    self._trade_ctx = TradeContext(self.longport_config)
                    self._trade_ctx.account_balance()
                    self.logger.debug("交易上下文初始化成功")
                
                # 获取当日持仓信息
                positions_resp = self._trade_ctx.stock_positions()
                
                if positions_resp and hasattr(positions_resp, 'channels'):
                    for channel in positions_resp.channels:
                        if hasattr(channel, 'positions'):
                            # 打印当日持仓表格
                            self.logger.info("\n=== 当日交易持仓明细 ===")
                            position_data = []
                            
                            # 获取所有持仓数据，用于计算列宽
                            max_symbol_len = max((len(pos.symbol_name) for pos in channel.positions), default=10)
                            
                            for pos in channel.positions:
                                try:
                                    # 获取基本信息
                                    symbol = pos.symbol
                                    symbol_name = pos.symbol_name
                                    quantity = abs(int(pos.quantity))
                                    cost_price = float(pos.cost_price)
                                    currency = pos.currency
                                    market = pos.market
                                    
                                    # 判断是否是期权
                                    is_option = 'C' in symbol or 'P' in symbol
                                    
                                    # 获取当前价格和日内变化
                                    try:
                                        # 确保行情上下文可用
                                        quote = self.quote_ctx.quote([symbol])
                                        if quote and len(quote) > 0:
                                            current_price = float(quote[0].last_done)
                                            prev_close = float(quote[0].prev_close)
                                            day_change = current_price - prev_close
                                            day_change_pct = (day_change / prev_close * 100) if prev_close != 0 else 0
                                        else:
                                            self.logger.debug(f"未获取到行情数据: {symbol}")
                                            current_price = cost_price
                                            day_change = 0
                                            day_change_pct = 0
                                    except Exception as e:
                                        self.logger.debug(f"获取行情失败: {str(e)}")
                                        current_price = cost_price
                                        day_change = 0
                                        day_change_pct = 0
                                    
                                    # 计算持仓数据
                                    multiplier = 100 if is_option else 1
                                    total_cost = quantity * cost_price * multiplier
                                    current_value = quantity * current_price * multiplier
                                    pnl = current_value - total_cost
                                    pnl_pct = (pnl / total_cost * 100) if total_cost != 0 else 0
                                    
                                    # 计算当日盈亏
                                    day_pnl = quantity * day_change * multiplier
                                    day_pnl_pct = day_change_pct
                                    
                                    # 格式化显示数据
                                    position_row = {
                                        "标的": symbol_name,
                                        "代码": symbol,
                                        "类型": "期权" if is_option else "股票",
                                        "数量": f"{quantity}{'张' if is_option else '股'}",
                                        "成本": f"${cost_price:.3f}",
                                        "现价": f"${current_price:.3f}",
                                        "当日涨跌": f"${day_change:+.3f}",
                                        "当日涨跌幅": f"{day_change_pct:+.1f}%",
                                        "当日盈亏": f"${day_pnl:,.0f}",
                                        "总成本": f"${total_cost:,.0f}",
                                        "市值": f"${current_value:,.0f}",
                                        "总盈亏": f"${pnl:,.0f}",
                                        "总收益率": f"{pnl_pct:+.1f}%",
                                        "币种": currency,
                                        "状态": "正常" if quantity > 0 else "锁定"
                                    }
                                    position_data.append(position_row)
                                    
                                    # 保存到返回数据
                                    positions_data["active"].append({
                                        "symbol": symbol,
                                        "type": "option" if is_option else "stock",
                                        "volume": quantity,
                                        "cost_price": cost_price,
                                        "current_price": current_price,
                                        "total_cost": total_cost,
                                        "value": current_value,
                                        "pnl": pnl,
                                        "pnl_pct": pnl_pct,
                                        "status": "active" if quantity > 0 else "locked"
                                    })
                                    
                                except Exception as e:
                                    self.logger.error(f"处理持仓数据时出错: {str(e)}, 持仓数据: {pos}")
                                    continue
                            
                            if position_data:
                                # 自定义表格样式
                                headers = {
                                    "标的": "标的",
                                    "代码": "代码",
                                    "类型": "类型",
                                    "数量": "数量",
                                    "成本": "成本",
                                    "现价": "现价",
                                    "当日涨跌": "当日涨跌",
                                    "当日涨跌幅": "当日涨跌幅",
                                    "当日盈亏": "当日盈亏",
                                    "总成本": "总成本",
                                    "市值": "市值",
                                    "总盈亏": "总盈亏",
                                    "总收益率": "总收益率",
                                    "币种": "币种",
                                    "状态": "状态"
                                }

                                # 设置表格格式
                                table_format = {
                                    "tablefmt": "grid",
                                    "numalign": "decimal",
                                    "stralign": "left",
                                    "floatfmt": ".2f",
                                    "colalign": (
                                        "left",    # 标的
                                        "left",    # 代码
                                        "center",  # 类型
                                        "right",   # 数量
                                        "decimal", # 成本
                                        "decimal", # 现价
                                        "decimal", # 当日涨跌
                                        "decimal", # 当日涨跌幅
                                        "decimal", # 当日盈亏
                                        "decimal", # 总成本
                                        "decimal", # 市值
                                        "decimal", # 总盈亏
                                        "decimal", # 总收益率
                                        "center",  # 币种
                                        "center"   # 状态
                                    )
                                }

                                # 显示持仓表格
                                position_table = tabulate(
                                    position_data,
                                    headers=headers,  # 使用之前定义的 headers 字典
                                    maxcolwidths=[
                                        22,  # 标的
                                        16,  # 代码
                                        8,   # 类型
                                        10,  # 数量
                                        12,  # 成本
                                        12,  # 现价
                                        12,  # 当日涨跌
                                        12,  # 当日涨跌幅
                                        14,  # 当日盈亏
                                        14,  # 总成本
                                        14,  # 市值
                                        14,  # 总盈亏
                                        12,  # 总收益率
                                        8,   # 币种
                                        8    # 状态
                                    ],
                                    **table_format
                                )

                                # 添加表格标题和分隔线
                                title = "\n" + "=" * 180 + "\n" + "当日交易持仓明细".center(180) + "\n" + "=" * 180 + "\n"
                                self.logger.info(f"{title}{position_table}")
                                
                                # 计算并显示持仓统计
                                total_cost = sum(float(pos["总成本"].replace("$", "").replace(",", "")) for pos in position_data)
                                total_value = sum(float(pos["市值"].replace("$", "").replace(",", "")) for pos in position_data)
                                total_pnl = total_value - total_cost
                                total_pnl_pct = (total_pnl / total_cost * 100) if total_cost != 0 else 0
                                total_day_pnl = sum(float(pos["当日盈亏"].replace("$", "").replace(",", "")) for pos in position_data)
                                
                                # 分类统计
                                stock_positions = [pos for pos in position_data if pos["类型"] == "股票"]
                                option_positions = [pos for pos in position_data if pos["类型"] == "期权"]
                                
                                stock_value = sum(float(pos["市值"].replace("$", "").replace(",", "")) for pos in stock_positions)
                                option_value = sum(float(pos["市值"].replace("$", "").replace(",", "")) for pos in option_positions)
                                
                                summary = [{
                                    "持仓总数": f"{len(position_data)}个",
                                    "股票": f"{len(stock_positions)}个 (${stock_value:,.0f})",
                                    "期权": f"{len(option_positions)}个 (${option_value:,.0f})",
                                    "当日盈亏": f"${total_day_pnl:,.0f}",
                                    "总成本": f"${total_cost:,.0f}",
                                    "总市值": f"${total_value:,.0f}",
                                    "总盈亏": f"${total_pnl:,.0f}",
                                    "总收益率": f"{total_pnl_pct:+.1f}%"
                                }]
                                
                                # 设置汇总表格格式
                                summary_format = {
                                    "tablefmt": "grid",
                                    "numalign": "decimal",
                                    "stralign": "center",
                                    "colalign": ("center",) * len(summary[0]),
                                    "floatfmt": ".2f"
                                }
                                
                                summary_table = tabulate(
                                    summary,
                                    headers="keys",
                                    **summary_format
                                )
                                
                                # 添加汇总标题
                                summary_title = "\n" + "=" * 80 + "\n" + "持仓汇总统计".center(80) + "\n" + "=" * 80 + "\n"
                                self.logger.info(f"{summary_title}{summary_table}")
                                
                                # 显示持仓明细
                                detail_title = "\n" + "=" * 120 + "\n" + "持仓标的明细".center(120) + "\n" + "=" * 120
                                self.logger.info(detail_title)
                                
                                # 格式化持仓明细显示
                                for pos_type, positions in [("股票", stock_positions), ("期权", option_positions)]:
                                    if positions:
                                        self.logger.info(f"\n{pos_type}持仓:")
                                        # 创建明细表头
                                        detail_header = (
                                            "| {:<20} | {:<18} | {:<8} | {:<10} | {:<10} | {:<12} | {:<12} | {:<12} |"
                                            .format("标的", "代码", "数量", "成本", "现价", "当日涨跌", "市值", "总盈亏")
                                        )
                                        detail_separator = "-" * len(detail_header)
                                        self.logger.info(detail_separator)
                                        self.logger.info(detail_header)
                                        self.logger.info(detail_separator)
                                        
                                        for pos in positions:
                                            detail_line = (
                                                "| {:<20} | {:<18} | {:<8} | {:<10} | {:<10} | {:<12} | {:<12} | {:<12} |"
                                                .format(
                                                    pos['标的'][:20],
                                                    pos['代码'][:18],
                                                    pos['数量'],
                                                    pos['成本'],
                                                    pos['现价'],
                                                    f"{pos['当日涨跌']} ({pos['当日涨跌幅']})",
                                                    pos['市值'],
                                                    f"{pos['总盈亏']} ({pos['总收益率']})"
                                                )
                                            )
                                            self.logger.info(detail_line)
                                        self.logger.info(detail_separator)

                return positions_data

            except Exception as e:
                self.logger.error(f"获取持仓数据时出错: {str(e)}")
                self.logger.exception("详细错误信息:")
                self._trade_ctx = None
                return None

        except Exception as e:
            self.logger.error(f"获取实际持仓数据失败: {str(e)}")
            self.logger.exception("详细错误信息:")
            return None

    async def _print_positions_table(self, positions_data: Dict[str, List[dict]]):
        """打印持仓表格"""
        try:
            if not positions_data or not positions_data.get("active"):
                self.logger.info("\n暂无持仓")
                return
            
            # 表头
            self.logger.info("\n" + "=" * 124)
            self.logger.info(" " * 60 + "持仓汇总")
            self.logger.info("=" * 124)
            
            header = (
                "| {:<20} | {:<10} | {:<12} | {:<26} | {:<20} | {:<20} |"
                .format("代码", "数量", "市值", "Last Price (Chg%)", "当日盈亏 (率)", "持仓盈亏 (率)")
            )
            separator = "|" + "-" * 22 + "|" + "-" * 12 + "|" + "-" * 14 + "|" + "-" * 28 + "|" + "-" * 22 + "|" + "-" * 22 + "|"
            
            self.logger.info(header)
            self.logger.info(separator)
            
            total_value = 0
            total_day_pnl = 0
            total_position_pnl = 0
            
            # 显示当前持仓
            sorted_positions = sorted(positions_data["active"], key=lambda x: x["symbol"])
            for pos in sorted_positions:
                try:
                    # 获取当前价格和涨跌幅
                    quotes = self.quote_ctx.quote([pos["symbol"]])
                    if quotes and len(quotes) > 0:
                        quote = quotes[0]
                        current_price = quote.last_done
                        prev_close = quote.prev_close
                        price_change_pct = (current_price - prev_close) / prev_close * 100 if prev_close else 0
                    else:
                        current_price = pos["cost_price"]
                        price_change_pct = 0
                    
                    # 计算持仓信息
                    position_value = current_price * pos["volume"]
                    day_pnl = (current_price - prev_close) * pos["volume"] if prev_close else 0
                    day_pnl_pct = day_pnl / (prev_close * pos["volume"]) * 100 if prev_close and pos["volume"] else 0
                    position_pnl = (current_price - pos["cost_price"]) * pos["volume"]
                    position_pnl_pct = position_pnl / (pos["cost_price"] * pos["volume"]) * 100 if pos["cost_price"] and pos["volume"] else 0
                    
                    # 确定持仓类型（股票/期权）
                    unit = "股" if ".US" in pos["symbol"] else "张"
                    
                    # 格式化行数据
                    line = (
                        "| {:<20} | {:>8}{} | ${:>10,.0f} | ${:>7.2f} → ${:<7.2f} ({:+.1f}%) | ${:>+8,.0f} ({:+.1f}%) | ${:>+8,.0f} ({:+.1f}%) |"
                        .format(
                            pos["symbol"],
                            pos["volume"], unit,
                            position_value,
                            prev_close, current_price, price_change_pct,
                            day_pnl, day_pnl_pct,
                            position_pnl, position_pnl_pct
                        )
                    )
                    self.logger.info(line)
                    
                    total_value += position_value
                    total_day_pnl += day_pnl
                    total_position_pnl += position_pnl
                    
                except Exception as e:
                    self.logger.error(f"处理持仓显示时出错: {str(e)}")
            
            # 显示合计行
            self.logger.info(separator)
            total_day_pnl_pct = total_day_pnl / total_value * 100 if total_value else 0
            total_position_pnl_pct = total_position_pnl / total_value * 100 if total_value else 0
            
            total_line = (
                "| {:<20} | {:>10} | ${:>10,.0f} | {:<26} | ${:>+8,.0f} ({:+.1f}%) | ${:>+8,.0f} ({:+.1f}%) |"
                .format(
                    f"总计 ({len(sorted_positions)}个持仓)",
                    "",
                    total_value,
                    "",
                    total_day_pnl, total_day_pnl_pct,
                    total_position_pnl, total_position_pnl_pct
                )
            )
            self.logger.info(total_line)
            self.logger.info("=" * 124)
            
        except Exception as e:
            self.logger.error(f"打印持仓表格时出错: {str(e)}")

    async def check_force_close(self, current_time: datetime) -> bool:
        """
        检查是否需要强制平仓
        
        Args:
            current_time: 当前时间
        
        Returns:
            bool: 是否需要强制平仓
        """
        try:
            # 获取美东时间
            ny_time = current_time.astimezone(self.tz)
            
            # 设置强制平仓时间（美东时间15:45）
            force_close_time = ny_time.replace(
                hour=15,
                minute=45,
                second=0,
                microsecond=0
            )
            
            # 如果已过强制平仓时间，检查是否还有持仓
            if ny_time.time() >= force_close_time.time():
                positions = await self.get_real_positions()
                if positions and positions.get("active"):
                    self.logger.warning(
                        f"已过强制平仓时间 ({force_close_time.strftime('%H:%M:%S')}), "
                        f"当前持仓数: {len(positions['active'])}"
                    )
                    return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"检查强制平仓时出错: {str(e)}")
            self.logger.exception("详细错误信息:")
            return False

    async def check_stop_loss(self, position: Dict[str, Any]) -> bool:
        """
        检查是否触发止损
        
        Args:
            position: 持仓信息
        
        Returns:
            bool: 是否需要止损
        """
        try:
            # 获取止损设置
            stop_loss_pct = self.risk_limits.get('stop_loss', {}).get('position', -30)  # 默认-30%
            trailing_stop = self.trailing_stop
            
            # 计算当前收益率
            pnl_pct = position.get('pnl_pct', 0)
            
            # 基础止损检查
            if pnl_pct <= stop_loss_pct:
                self.logger.warning(f"触发止损: 当前收益率 {pnl_pct:.1f}% <= {stop_loss_pct}%")
                return True
            
            # 移动止损检查
            if pnl_pct >= trailing_stop['activation_return']:
                # 计算移动止损价位
                peak_value = position.get('peak_value', position['value'])
                current_value = position['value']
                drawdown = (current_value - peak_value) / peak_value * 100
                
                if drawdown <= -trailing_stop['trailing_distance']:
                    self.logger.warning(
                        f"触发移动止损: 回撤 {drawdown:.1f}% >= {trailing_stop['trailing_distance']}%, "
                        f"最高点 ${peak_value:,.2f}, 当前 ${current_value:,.2f}"
                    )
                    return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"检查止损条件时出错: {str(e)}")
            return False