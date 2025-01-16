"""
持仓管理模块 更新
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
from trading.risk_checker import RiskChecker  # 添加导入

class MarketInfoFilter(logging.Filter):
    """过滤掉市场权限信息的日志过滤器"""
    def filter(self, record):
        # 扩展过滤字符串列表
        filtered_strings = [
            "Nasdaq Basic",
            "ChinaConnect",
            "LV1 Real-time Quotes",
            "Market Quotes",
            "USOption",
            "Market Permission",
            "Market Status",
            "Market Data",
            "+----------+",
            "|----------+",
            "| US       |",
            "| CN       |",
            "| HK       |",
            "| USOption |"
        ]
        message = record.getMessage()
        return not any(s in message for s in filtered_strings)

class DoomsdayPositionManager:
    def __init__(self, config: Dict[str, Any], test_mode: bool = False):
        """
        初始化持仓管理器
        
        Args:
            config: 配置字典，包含交易和风险控制参数
            test_mode: 是否为测试模式
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
        
        # 初始化风险检查器
        self.risk_checker = RiskChecker(config)
        
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
        
        # 添加日志过滤器
        log_filter = MarketInfoFilter()
        for handler in logging.getLogger().handlers:
            handler.addFilter(log_filter)
        
        self.test_mode = test_mode
        
        # 测试模式下的风控参数
        if test_mode:
            self.risk_limits['option'] = {
                'stop_loss': {
                    'initial': 10.0,  # 10%固定止损
                    'trailing': 10.0  # 10%移动止损
                },
                'take_profit': 50.0,  # 基础止盈比例（会根据趋势动态调整）
            }
            
            # 趋势判断参数（可以根据需要调整）
            self.trend_config = {
                'fast_length': 1,      # 快线周期
                'slow_length': 5,      # 慢线周期
                'curve_length': 10,    # 曲线周期
                'trend_period': 5,     # 趋势判断周期
                'vwap_dev': 2.0       # VWAP通道宽度
            }
        
        # 缓存历史数据
        self.price_history = {}
        self.vwap_history = {}

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

    async def force_close_all(self):
        """强制平仓所有持仓"""
        try:
            positions = await self.get_real_positions()
            if not positions or not positions.get("active"):
                self.logger.info("没有需要平仓的持仓")
                return
            
            self.logger.warning("开始执行强制平仓...")
            
            for pos in positions["active"]:
                try:
                    # 提交市价单平仓
                    self.trade_ctx.submit_order(
                        symbol=pos["symbol"],
                        order_type=OrderType.Market,  # 使用市价单
                        side=OrderSide.Sell,
                        submitted_quantity=pos["volume"],
                        time_in_force=TimeInForceType.Day,
                        remark="Force close position"
                    )
                    self.logger.info(f"已提交市价平仓订单: {pos['symbol']}, 数量: {pos['volume']}")
                    
                except Exception as e:
                    self.logger.error(f"平仓 {pos['symbol']} 失败: {str(e)}")
                    continue
                
            self.logger.info("强制平仓执行完成")
            
        except Exception as e:
            self.logger.error(f"强制平仓失败: {str(e)}")

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
        """获取实际持仓数据"""
        try:
            if not self.trade_ctx:
                self.logger.error("交易上下文未初始化")
                return None

            # 获取持仓数据
            positions_data = {"active": []}
            
            try:
                # 使用 stock_positions 方法获取持仓
                stock_positions = self.trade_ctx.stock_positions()  # 同步方法，不需要 await
                
                if stock_positions:
                    for pos in stock_positions:
                        # 转换持仓数据格式
                        position_data = {
                            "symbol": pos.symbol,
                            "volume": pos.quantity,
                            "cost_price": float(pos.avg_price),
                            "current_price": float(pos.current_price),
                            "market_value": float(pos.market_value),
                            "day_pnl": float(pos.unrealized_pnl),
                            "day_pnl_pct": float(pos.unrealized_pnl_ratio) * 100,
                            "total_pnl": float(pos.unrealized_pnl),
                            "total_pnl_pct": float(pos.unrealized_pnl_ratio) * 100,
                            "type": "stock" if not self._is_option(pos.symbol) else "option"
                        }
                        
                        # 添加到活跃持仓列表
                        positions_data["active"].append(position_data)
                        
                        # 记录详细日志
                        self.logger.debug(
                            f"持仓数据 - {pos.symbol}:\n"
                            f"  数量: {pos.quantity}\n"
                            f"  成本价: ${float(pos.avg_price):.4f}\n"
                            f"  现价: ${float(pos.current_price):.4f}\n"
                            f"  市值: ${float(pos.market_value):.2f}\n"
                            f"  未实现盈亏: ${float(pos.unrealized_pnl):+.2f}\n"
                            f"  盈亏比例: {float(pos.unrealized_pnl_ratio)*100:+.2f}%"
                        )
                
                self.logger.info(f"获取到 {len(positions_data['active'])} 个持仓")
                return positions_data

            except AttributeError as e:
                self.logger.error(f"API 方法不存在: {str(e)}")
                self.logger.info("尝试使用备用方法获取持仓...")
                
                try:
                    # 尝试使用 today_orders 方法获取当日订单
                    today_orders = await self.trade_ctx.today_orders()
                    if today_orders:
                        # 处理订单信息，提取持仓
                        filled_orders = [order for order in today_orders if order.status == "filled"]
                        for order in filled_orders:
                            position_data = {
                                "symbol": order.symbol,
                                "volume": order.executed_quantity,
                                "cost_price": float(order.executed_price),
                                "current_price": float(order.last_done or order.executed_price),
                                "market_value": float(order.executed_quantity * order.executed_price),
                                "day_pnl": 0.0,  # 需要计算
                                "day_pnl_pct": 0.0,  # 需要计算
                                "total_pnl": 0.0,  # 需要计算
                                "total_pnl_pct": 0.0,  # 需要计算
                                "type": "stock" if not self._is_option(order.symbol) else "option"
                            }
                            positions_data["active"].append(position_data)
                    
                    return positions_data
                    
                except Exception as e2:
                    self.logger.error(f"备用方法也失败: {str(e2)}")
                    raise

        except Exception as e:
            self.logger.error(f"获取持仓数据时出错: {str(e)}")
            self.logger.exception("详细错误信息:")
            return None

    async def print_trading_status(self):
        """打印交易状态信息"""
        try:
            # 1. 美股市场状态
            ny_time = datetime.now(self.tz)
            is_market_open = self.is_market_open(ny_time)
            market_status = (
                f"\n{'='*80}\n"
                f"美股市场状态 | 时间: {ny_time.strftime('%Y-%m-%d %H:%M:%S')} EST | "
                f"状态: {'交易中' if is_market_open else '休市'}\n"
                f"{'='*80}"
            )
            self.logger.info(market_status)

            # 2. 交易系统状态
            system_status = (
                f"\n交易系统状态:\n"
                f"{'='*80}\n"
                f"连接状态: {'已连接':^10} | "
                f"行情延迟: {self.quote_delay:^8}ms | "
                f"VIX指数: {self.current_vix:.2f} (限制范围: {self.risk_limits['volatility']['min_vix']}-"
                f"{self.risk_limits['volatility']['max_vix']})\n"
                f"{'='*80}"
            )
            self.logger.info(system_status)

            # 3. 交易条件状态
            risk_limits = self.risk_limits['option']
            trading_conditions = (
                f"\n交易条件状态:\n"
                f"{'='*80}\n"
                f"止损设置: {risk_limits['stop_loss']['initial']}% | "
                f"移动止损: {risk_limits['stop_loss']['trailing']}% | "
                f"止盈目标: {risk_limits['take_profit']}%"
            )

            # 添加交易决策信息
            if hasattr(self, 'trading_decision'):
                decision = self.trading_decision
                trading_conditions += (
                    f"\n交易决策: {decision.get('action', '未知')} | "
                    f"价格趋势: {decision.get('price_trend', '-')} | "
                    f"分时趋势: {decision.get('time_trend', '-')} | "
                    f"当前收益: {decision.get('pnl_pct', 0):+.1f}%"
                )

                # 如果有决策原因
                if 'reason' in decision:
                    trading_conditions += f"\n决策原因: {decision['reason']}"

            trading_conditions += f"\n{'='*80}"
            self.logger.info(trading_conditions)

            # 4. 持仓状态
            positions = await self.get_real_positions()
            if positions and positions.get("active"):
                await self._print_positions_table(positions)
            else:
                self.logger.info("\n当前持仓状态:")
                self.logger.info("暂无持仓")

            # 5. 市场条件检查
            if self.risk_checker.check_market_condition(self.current_vix, ny_time.strftime('%H:%M:%S')):
                self.logger.info("市场条件满足交易要求")
            else:
                self.logger.info("市场条件不适合交易")

        except Exception as e:
            self.logger.error(f"打印交易状态时出错: {str(e)}")
            self.logger.exception("详细错误信息:")

    async def _print_positions_table(self, positions_data: Dict[str, List[dict]]):
        """打印持仓标的明细"""
        try:
            if not positions_data or not positions_data.get("active"):
                self.logger.info("\n暂无持仓")
                return

            positions = positions_data["active"]
            if not positions:
                return

            # 计算最大字段长度以实现表格自适应
            max_symbol_len = max(len(pos["symbol"]) for pos in positions)
            symbol_width = max(15, max_symbol_len + 2)

            # 构建表格格式
            fmt = (
                f"| {{:<{symbol_width}}} | {{:>12}} | {{:>15}} | {{:>12}} | {{:>25}} |"
            )
            
            # 表头
            header = fmt.format(
                "代码",            # 1. 代码
                "市值",            # 2. 市值
                "现价/成本",       # 3. 现价/成本
                "当日涨跌幅",      # 4. 当日涨跌幅
                "当日盈亏/盈亏率"  # 5. 当日盈亏/当日盈亏率
            )
            
            # 计算分隔线长度
            total_width = len(header)
            separator = "=" * total_width

            # 打印表头
            self.logger.info(f"\n持仓标的明细:\n{separator}")
            self.logger.info(header)
            self.logger.info(separator)

            # 按代码排序显示所有持仓
            total_value = 0
            total_day_pnl = 0

            for pos in sorted(positions, key=lambda x: x["symbol"]):
                try:
                    quotes = self.quote_ctx.quote([pos["symbol"]])
                    quote = quotes[0] if quotes else None
                    
                    if quote:
                        current_price = quote.last_done
                        prev_close = quote.prev_close
                        price_change_pct = (current_price - prev_close) / prev_close * 100 if prev_close else 0
                    else:
                        current_price = pos.get("cost_price", 0)
                        prev_close = current_price
                        price_change_pct = 0

                    quantity = pos.get("volume", 0)
                    cost_price = pos.get("cost_price", current_price)
                    multiplier = 100 if any(x in pos["symbol"] for x in ['C', 'P']) else 1
                    position_value = current_price * quantity * multiplier
                    
                    day_pnl = (current_price - prev_close) * quantity * multiplier
                    day_pnl_pct = day_pnl / (prev_close * quantity * multiplier) * 100 if prev_close and quantity else 0

                    line = fmt.format(
                        pos["symbol"],
                        f"${position_value:,.0f}",
                        f"${current_price:.2f}/${cost_price:.2f}",
                        f"{price_change_pct:+.2f}%",
                        f"${day_pnl:+,.0f}/{day_pnl_pct:+.1f}%"
                    )
                    self.logger.info(line)

                    total_value += position_value
                    total_day_pnl += day_pnl

                except Exception as e:
                    self.logger.error(f"处理持仓显示时出错: {str(e)}")

            # 显示合计行
            total_day_pnl_pct = total_day_pnl / total_value * 100 if total_value else 0
            
            self.logger.info(separator)
            summary = fmt.format(
                f"总计({len(positions)})",
                f"${total_value:,.0f}",
                "-",
                "-",
                f"${total_day_pnl:+,.0f}/{total_day_pnl_pct:+.1f}%"
            )
            self.logger.info(summary)
            self.logger.info(separator)

        except Exception as e:
            self.logger.error(f"打印持仓表格时出错: {str(e)}")

    def is_market_open(self, current_time: datetime) -> bool:
        """检查市场是否开放"""
        # 转换为美东时间
        ny_time = current_time.astimezone(self.tz)
        
        # 检查是否为工作日
        if ny_time.weekday() >= 5:  # 5=周六, 6=周日
            return False
        
        # 检查是否在交易时间内 (9:30 - 16:00)
        market_open = ny_time.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = ny_time.replace(hour=16, minute=0, second=0, microsecond=0)
        
        return market_open.time() <= ny_time.time() <= market_close.time()

    def get_memory_usage(self) -> float:
        """获取当前进程内存使用情况"""
        import psutil
        process = psutil.Process()
        return process.memory_info().rss / 1024 / 1024  # 转换为MB

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
            stop_loss_pct = float(self.risk_limits['option']['stop_loss']['initial'])  # 固定止损比例
            trailing_stop_pct = float(self.risk_limits['option']['stop_loss']['trailing'])  # 移动止损比例
            
            # 获取当前价格和成本价
            current_price = float(position.get('current_price', 0))
            cost_price = float(position.get('cost_price', 0))
            
            # 计算当前收益率
            pnl_pct = (current_price - cost_price) / cost_price * 100 if cost_price else 0
            
            # 1. 固定止损检查
            if pnl_pct <= -stop_loss_pct:
                self.logger.warning(f"触发固定止损: 当前亏损 {pnl_pct:.1f}% <= -{stop_loss_pct:.1f}%")
                return True
            
            # 2. 移动止损检查
            if 'peak_price' not in position:
                position['peak_price'] = current_price
            else:
                position['peak_price'] = max(position['peak_price'], current_price)
            
            peak_price = float(position['peak_price'])
            drawdown_pct = (current_price - peak_price) / peak_price * 100
            
            # 只有在盈利时才启用移动止损
            if pnl_pct > 0 and drawdown_pct <= -trailing_stop_pct:
                self.logger.warning(
                    f"触发移动止损: 从最高点回撤 {drawdown_pct:.1f}% <= -{trailing_stop_pct:.1f}%, "
                    f"最高价 ${peak_price:.2f}, 当前价 ${current_price:.2f}"
                )
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"检查止损条件时出错: {str(e)}")
            return False

    async def check_take_profit(self, position: Dict[str, Any]) -> bool:
        """检查是否触发止盈"""
        try:
            take_profit_pct = float(self.risk_limits['option']['take_profit'])
            current_price = float(position.get('current_price', 0))
            cost_price = float(position.get('cost_price', 0))
            
            # 计算收益率
            pnl_pct = (current_price - cost_price) / cost_price * 100 if cost_price else 0
            
            # 检查趋势
            trend = await self.check_trend(position['symbol'], current_price, cost_price)
            
            # 根据趋势和盈利情况动态调整止盈策略
            if trend['price_trend'] == 'super_strong' and trend['time_trend'] in ['strong_up', 'up']:
                # 超强势且分时走强
                if pnl_pct >= 500:
                    take_profit_pct = pnl_pct * 0.9  # 回撤10%止盈
                    self.logger.info(f"超强势上涨，当前收益{pnl_pct:.1f}%，设置回撤止盈: {take_profit_pct:.1f}%")
                else:
                    take_profit_pct *= 3.0  # 提高200%的止盈目标
                    self.logger.info(f"超强势上涨，提高止盈目标至: {take_profit_pct:.1f}%")
            elif trend['price_trend'] == 'super_strong' and trend['time_trend'] in ['strong_down', 'down']:
                # 超强势但分时转弱
                take_profit_pct = pnl_pct * 0.85  # 回撤15%止盈
                self.logger.info(f"超强势但分时转弱，设置回撤止盈: {take_profit_pct:.1f}%")
            elif trend['price_trend'] == 'strong':
                if trend['time_trend'] in ['strong_up', 'up']:
                    take_profit_pct *= 2.0  # 提高100%的止盈目标
                else:
                    take_profit_pct = pnl_pct * 0.8  # 回撤20%止盈
            elif trend['price_trend'] == 'normal':
                if trend['time_trend'] in ['strong_up', 'up']:
                    take_profit_pct *= 1.5  # 提高50%的止盈目标
                else:
                    take_profit_pct *= 0.8  # 降低20%的止盈目标
                
            self.logger.info(
                f"当前趋势: 价格={trend['price_trend']}, 分时={trend['time_trend']}, "
                f"止盈目标: {take_profit_pct:.1f}%"
            )
            
            # 添加移动止盈
            if 'peak_pnl' not in position:
                position['peak_pnl'] = pnl_pct
            else:
                position['peak_pnl'] = max(position['peak_pnl'], pnl_pct)
            
            # 从最高点回撤超过设定比例时触发止盈
            peak_pnl = position['peak_pnl']
            drawdown_pct = (pnl_pct - peak_pnl) / peak_pnl * 100 if peak_pnl else 0
            
            # 根据收益率设置不同的回撤止盈比例
            if peak_pnl >= 500:  # 超过500%收益
                max_drawdown = -10  # 允许10%回撤
            elif peak_pnl >= 200:  # 超过200%收益
                max_drawdown = -15  # 允许15%回撤
            elif peak_pnl >= 100:  # 超过100%收益
                max_drawdown = -20  # 允许20%回撤
            else:
                max_drawdown = -25  # 普通情况允许25%回撤
            
            if drawdown_pct <= max_drawdown:
                self.logger.warning(
                    f"触发回撤止盈: 从最高点{peak_pnl:.1f}%回撤{-drawdown_pct:.1f}% > {-max_drawdown}%"
                )
                return True
            
            # 检查是否达到止盈条件
            if pnl_pct >= take_profit_pct:
                self.logger.warning(
                    f"触发止盈: 当前收益 {pnl_pct:.1f}% >= {take_profit_pct:.1f}% "
                    f"(趋势: {trend['time_trend']}, 最高收益: {peak_pnl:.1f}%)"
                )
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"检查止盈条件时出错: {str(e)}")
            return False

    def _is_option(self, symbol: str) -> bool:
        """检查是否为期权"""
        return any(x in symbol for x in ['C', 'P'])

    async def check_position_risk(self, position: Dict[str, Any]) -> bool:
        """检查持仓风险，包括止盈止损"""
        try:
            # 只处理期权持仓
            if not self._is_option(position['symbol']):
                self.logger.warning(f"跳过非期权持仓: {position['symbol']}")
                return False
            
            # 测试模式下记录更多信息
            if self.test_mode:
                self.logger.info(f"\n当前检查持仓: {position['symbol']}")
                self.logger.info(f"止损设置: 固定{self.risk_limits['option']['stop_loss']['initial']}%, "
                               f"移动{self.risk_limits['option']['stop_loss']['trailing']}%")
                self.logger.info(f"止盈设置: {self.risk_limits['option']['take_profit']}%")
                
            # 检查止损条件
            if await self.check_stop_loss(position):
                return True
            
            # 检查止盈条件
            if await self.check_take_profit(position):
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"检查持仓风险时出错: {str(e)}")
            return False

    async def test_risk_management(self, position: Dict[str, Any], current_price: float, vix: float = None) -> bool:
        """
        测试风险管理逻辑
        
        Args:
            position: 持仓信息
            current_price: 当前价格
            vix: VIX指数值 (可选)
            
        Returns:
            bool: 是否需要平仓
        """
        try:
            # 更新当前价格
            position['current_price'] = current_price
            
            # 获取趋势数据
            trend = await self.check_trend(position['symbol'], current_price, position['cost_price'])
            
            # 检查市场条件（如果提供了VIX）
            if vix is not None:
                current_time = datetime.now(self.tz).strftime('%H:%M:%S')
                if not self.risk_checker.check_market_condition(vix, current_time):
                    self.logger.warning(f"市场条件不适合交易: VIX={vix}")
                    return True
            
            # 检查持仓风险
            if await self.risk_checker.check_position_risk(position, trend):
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"测试风险管理时出错: {str(e)}")
            return False

    async def check_trend(self, symbol: str, current_price: float, cost_price: float) -> Dict[str, str]:
        """
        检查分时趋势和价格趋势
        返回: {
            'price_trend': 'super_strong'|'strong'|'normal'|'weak',  # 基于涨幅的趋势
            'time_trend': 'strong_up'|'up'|'strong_down'|'down'|'neutral'  # 基于分时的趋势
        }
        """
        try:
            # 计算涨幅趋势
            pnl_pct = (current_price - cost_price) / cost_price * 100 if cost_price else 0
            
            # 基于涨幅判断价格趋势
            if pnl_pct >= 200:
                price_trend = 'super_strong'
            elif pnl_pct >= 100:
                price_trend = 'strong'
            elif pnl_pct >= 30:
                price_trend = 'normal'
            else:
                price_trend = 'weak'
            
            # 获取分时趋势数据
            if symbol not in self.price_history:
                self.price_history[symbol] = []
            
            # 更新历史数据
            self.price_history[symbol].append(current_price)
            if len(self.price_history[symbol]) > 100:
                self.price_history[symbol].pop(0)
            
            prices = self.price_history[symbol]
            if len(prices) < self.trend_config['trend_period']:
                return {'price_trend': price_trend, 'time_trend': 'neutral'}
            
            # 计算分时指标
            fast_ma = sum(prices[-self.trend_config['fast_length']:]) / self.trend_config['fast_length']
            slow_ma = sum(prices[-self.trend_config['slow_length']:]) / self.trend_config['slow_length']
            
            # 计算VWAP和通道
            vwap = sum(prices) / len(prices)
            std_dev = (sum((p - vwap) ** 2 for p in prices) / len(prices)) ** 0.5
            upper_band = vwap + std_dev * self.trend_config['vwap_dev']
            lower_band = vwap - std_dev * self.trend_config['vwap_dev']
            
            # 判断分时趋势
            is_up_trend = fast_ma > slow_ma and current_price > vwap
            is_strong_up = is_up_trend and current_price > upper_band
            is_down_trend = fast_ma < slow_ma and current_price < vwap
            is_strong_down = is_down_trend and current_price < lower_band
            
            # 确定分时趋势
            if is_strong_up:
                time_trend = 'strong_up'
            elif is_up_trend:
                time_trend = 'up'
            elif is_strong_down:
                time_trend = 'strong_down'
            elif is_down_trend:
                time_trend = 'down'
            else:
                time_trend = 'neutral'
            
            return {
                'price_trend': price_trend,
                'time_trend': time_trend
            }
            
        except Exception as e:
            self.logger.error(f"检查趋势时出错: {str(e)}")
            return {'price_trend': 'weak', 'time_trend': 'neutral'}

    async def close_all_positions_before_market_close(self):
        """收盘前强制平仓所有持仓"""
        try:
            positions = await self.get_real_positions()
            if not positions or not positions.get("active"):
                self.logger.info("收盘前检查: 没有需要平仓的持仓")
                return
            
            self.logger.warning("=== 收盘前强制平仓 ===")
            
            for pos in positions["active"]:
                try:
                    # 只处理期权持仓
                    if not self._is_option(pos["symbol"]):
                        continue
                        
                    # 提交市价单平仓
                    self.trade_ctx.submit_order(
                        symbol=pos["symbol"],
                        order_type=OrderType.Market,  # 使用市价单
                        side=OrderSide.Sell,
                        submitted_quantity=pos["volume"],
                        time_in_force=TimeInForceType.Day,
                        remark="Market close position"
                    )
                    self.logger.warning(f"收盘前平仓: {pos['symbol']}, 数量: {pos['volume']}张, 使用市价单")
                    
                except Exception as e:
                    self.logger.error(f"收盘前平仓失败 {pos['symbol']}: {str(e)}")
                    continue
                
            self.logger.info("收盘前平仓执行完成")
            
        except Exception as e:
            self.logger.error(f"收盘前平仓操作失败: {str(e)}")

    def update_scenario(self, scenario_data: dict):
        """更新当前场景信息"""
        self.current_scenario = scenario_data

    def update_trading_decision(self, decision_data: dict):
        """更新交易决策信息"""
        self.trading_decision = decision_data

    async def check_position_risks(self):
        """检查所有持仓的风险状态"""
        try:
            positions = await self.get_real_positions()
            if not positions or not positions.get("active"):
                self.logger.info("无持仓，跳过风险检查")
                return
            
            self.logger.info(f"开始检查持仓风险... 持仓数量: {len(positions['active'])}")
            
            for pos in positions["active"]:
                try:
                    # 获取最新行情数据
                    quotes = await self.quote_ctx.quote([pos["symbol"]])
                    if not quotes:
                        self.logger.warning(f"无法获取行情数据: {pos['symbol']}")
                        continue
                    
                    # 计算收益率
                    current_pnl_pct = pos["total_pnl_pct"]  # 使用持仓数据中的收益率
                    
                    # 获取趋势信息
                    price_trend = await self.get_price_trend(pos["symbol"])
                    time_trend = await self.get_time_trend(pos["symbol"])
                    
                    # 记录详细日志
                    self.logger.info(
                        f"持仓风险检查 - {pos['symbol']}:\n"
                        f"  数量: {pos['volume']} {'张' if self._is_option(pos['symbol']) else '股'}\n"
                        f"  成本价: ${pos['cost_price']:.4f}\n"
                        f"  现价: ${pos['current_price']:.4f}\n"
                        f"  市值: ${pos['market_value']:.2f}\n"
                        f"  当日盈亏: ${pos['day_pnl']:+.2f} ({pos['day_pnl_pct']:+.2f}%)\n"
                        f"  总盈亏: ${pos['total_pnl']:+.2f} ({current_pnl_pct:+.2f}%)\n"
                        f"  价格趋势: {price_trend}\n"
                        f"  时间趋势: {time_trend}\n"
                        f"  止损设置: 初始={self.risk_checker.initial_stop_loss}%, "
                        f"移动={self.risk_checker.trailing_stop}%, "
                        f"止盈={self.risk_checker.take_profit}%"
                    )
                    
                    # 检查风险
                    should_close, reason = self.risk_checker.check_position_risk(
                        pos["symbol"], 
                        current_pnl_pct,
                        price_trend,
                        time_trend
                    )
                    
                    if should_close:
                        self.logger.warning(f"触发风险管理: {reason}")
                        await self.close_position(
                            symbol=pos["symbol"],
                            volume=int(pos["volume"]),
                            reason=reason
                        )
                    else:
                        self.logger.info(f"持仓 {pos['symbol']} 未触发风险管理条件")
                        
                except Exception as e:
                    self.logger.error(f"检查单个持仓风险时出错 {pos['symbol']}: {str(e)}")
                    self.logger.exception("详细错误信息:")
                
        except Exception as e:
            self.logger.error(f"检查持仓风险时出错: {str(e)}")
            self.logger.exception("详细错误信息:")

    async def close_position(self, symbol: str, volume: int, reason: str):
        """执行平仓操作"""
        try:
            self.logger.warning(f"准备平仓: {symbol}, 数量: {volume}, 原因: {reason}")
            
            # 确保交易上下文存在
            if not self.trade_ctx:
                self.logger.error("交易上下文未初始化")
                return
            
            # 检查账户状态
            account = await self.trade_ctx.account()
            if not account or account.status != "normal":
                self.logger.error(f"账户状态异常: {account.status if account else 'unknown'}")
                return
            
            # 提交市价单平仓
            order_resp = await self.trade_ctx.submit_order(
                symbol=symbol,
                order_type=OrderType.Market,  # 使用市价单确保执行
                side=OrderSide.Sell,
                submitted_quantity=volume,
                time_in_force=TimeInForceType.Day,
                remark=f"Risk management: {reason}"
            )
            
            self.logger.info(f"平仓订单已提交: {symbol}, 订单ID: {order_resp.order_id}")
            
            # 等待并检查订单状态
            max_retries = 5
            for i in range(max_retries):
                await asyncio.sleep(1)
                order_status = await self.trade_ctx.get_order(order_resp.order_id)
                self.logger.info(f"平仓订单状态 ({i+1}/{max_retries}): {order_status.status}")
                
                if order_status.status in ["filled", "partially_filled"]:
                    self.logger.info(f"平仓订单执行成功: {symbol}")
                    break
                elif order_status.status in ["rejected", "failed"]:
                    self.logger.error(f"平仓订单被拒绝: {symbol}, 原因: {order_status.reject_reason}")
                    break
                
        except Exception as e:
            self.logger.error(f"执行平仓操作失败 {symbol}: {str(e)}")
            self.logger.exception("详细错误信息:")

    async def get_price_trend(self, symbol: str) -> str:
        """获取价格趋势"""
        try:
            # 获取K线数据
            klines = await self.quote_ctx.get_candlesticks(
                symbol=symbol,
                period="1d",  # 日K
                count=10      # 最近10天
            )
            
            if not klines:
                return "normal"
            
            # 计算趋势
            prices = [k.close for k in klines]
            change = (prices[-1] - prices[0]) / prices[0] * 100
            
            if change >= 20:
                return "super_strong"
            elif change >= 10:
                return "strong"
            elif change <= -20:
                return "super_weak"
            elif change <= -10:
                return "weak"
            else:
                return "normal"
            
        except Exception as e:
            self.logger.error(f"获取价格趋势时出错: {str(e)}")
            return "normal"

    async def get_time_trend(self, symbol: str) -> str:
        """获取分时趋势"""
        try:
            # 获取分时数据
            quotes = await self.quote_ctx.get_quote(symbol)
            if not quotes:
                return "neutral"
            
            quote = quotes[0]
            change = (quote.last_done - quote.open) / quote.open * 100
            
            if change >= 5:
                return "strong_up"
            elif change >= 2:
                return "up"
            elif change <= -5:
                return "strong_down"
            elif change <= -2:
                return "down"
            else:
                return "neutral"
            
        except Exception as e:
            self.logger.error(f"获取分时趋势时出错: {str(e)}")
            return "neutral"

    async def start_risk_monitoring(self):
        """启动风险监控"""
        self.logger.info("风险监控任务启动...")
        
        while True:
            try:
                # 检查市场状态
                ny_time = datetime.now(self.tz)
                if not self.is_market_open(ny_time):
                    self.logger.info("市场休市中，暂停风险监控")
                    await asyncio.sleep(60)  # 休市时降低检查频率
                    continue
                    
                # 检查交易上下文
                if not self.trade_ctx or not self.quote_ctx:
                    self.logger.error("交易或行情上下文未初始化，重试中...")
                    await asyncio.sleep(5)
                    continue
                    
                # 执行风险检查
                await self.check_position_risks()
                
            except Exception as e:
                self.logger.error(f"风险监控出错: {str(e)}")
                self.logger.exception("详细错误信息:")
            finally:
                await asyncio.sleep(5)  # 每5秒检查一次

    async def __aenter__(self):
        """异步上下文管理器入口"""
        try:
            # 初始化交易和行情上下文
            self.trade_ctx = await TradeContext(self.longport_config).__aenter__()
            self.quote_ctx = await QuoteContext(self.longport_config).__aenter__()
            
            # 启动风险监控任务并保存引用
            self.risk_monitor_task = asyncio.create_task(self.start_risk_monitoring())
            self.logger.info("风险监控任务已启动")
            
            return self
            
        except Exception as e:
            self.logger.error(f"初始化失败: {str(e)}")
            self.logger.exception("详细错误信息:")
            raise

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器退出"""
        try:
            # 取消风险监控任务
            if hasattr(self, 'risk_monitor_task'):
                self.risk_monitor_task.cancel()
                try:
                    await self.risk_monitor_task
                except asyncio.CancelledError:
                    pass
            
            # 关闭上下文
            if self.trade_ctx:
                await self.trade_ctx.__aexit__(exc_type, exc_val, exc_tb)
            if self.quote_ctx:
                await self.quote_ctx.__aexit__(exc_type, exc_val, exc_tb)
            
        except Exception as e:
            self.logger.error(f"退出时出错: {str(e)}")
            self.logger.exception("详细错误信息:")