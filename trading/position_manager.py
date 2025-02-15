"""
持仓管理模块
负责管理交易持仓和资金管理
"""
from typing import Dict, List, Any, Optional, Tuple
import logging
import os
from datetime import datetime, timedelta
import pytz
from decimal import Decimal
from dotenv import load_dotenv
from longport.openapi import (
    TradeContext, 
    QuoteContext, 
    Config, 
    SubType, 
    OrderType,
    OrderSide,
    TimeInForceType,
    OrderStatus,
    Period,
    AdjustType,
    OpenApiException
)
from tabulate import tabulate
import asyncio
import traceback
import time
import re
from trading.risk_checker import RiskChecker

class DoomsdayPositionManager:
    def __init__(self, config: Dict[str, Any], data_manager):
        """初始化持仓管理器"""
        if not config:
            raise ValueError("配置不能为空")
        if not data_manager:
            raise ValueError("数据管理器不能为空")
        
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.tz = pytz.timezone('America/New_York')
        self.data_manager = data_manager
        self.longport_config = data_manager.longport_config  # 使用 data_manager 的配置
        
        # 连接管理
        self._quote_ctx_lock = asyncio.Lock()
        self._quote_ctx = None
        self._trade_ctx_lock = asyncio.Lock()
        self._trade_ctx = None
        self._last_quote_time = 0
        self._last_trade_time = 0
        self._connection_timeout = 60  # 60秒超时
        
        # 使用 RiskChecker 进行风险控制
        self.risk_checker = RiskChecker(config, self, data_manager.time_checker)
        
        # 订单配置
        self.order_config = config.get('order_config', {
            'timeout': 10,
            'max_retry': 3,
            'retry_interval': 1
        })
        
        # 持仓历史记录
        self.position_history = {
            'trades': [],
            'positions': {},
            'daily_summary': {}
        }
        
        # 请求限制
        self.request_times = []
        self.request_limit = config.get('request_limit', {
            'max_requests': 120,  # 每分钟最大请求数
            'time_window': 60     # 时间窗口（秒）
        })
        
        # 数据缓存
        self.kline_cache = {}
        self.last_update = {}
        self.update_interval = 60  # 更新间隔(秒)
        
        # 订阅类型
        self.sub_types = [
            SubType.Quote,
            SubType.Trade,
            SubType.Depth
        ]
        
        # 添加当日开单记录
        self.daily_orders = {}  # 格式: {'symbol': timestamp}
        self._reset_daily_orders()  # 初始化时重置
        
        # 监控列表
        self.watch_list = config.get('symbols', [])
        
        # 当日交易记录
        self.daily_trades = {}
        self._reset_daily_trades()
        
        # 持仓状态记录
        self.position_status = {
            'total_value': 0.0,
            'total_pnl': 0.0,
            'daily_pnl': 0.0,      # 添加当日盈亏金额
            'daily_pnl_rate': 0.0,  # 添加当日盈亏率
            'last_update': None
        }
        
        # 记录最高/最低价格，用于追踪止盈止损
        self.price_records = {}  # {'symbol': {'high': price, 'low': price}}
        
        # 添加缓存初始化
        self._cache = {
            'positions': {},
            'orders': {},
            'quotes': {}
        }
        
        # 添加缓存配置
        self.cache_config = config.get('cache_config', {
            'positions': 60,  # 持仓数据缓存时间（秒）
            'orders': 30,     # 订单数据缓存时间（秒）
            'quotes': 5       # 报价数据缓存时间（秒）
        })
        
        self.logger.info("持仓管理器初始化完成")

    async def async_init(self):
        """异步初始化"""
        try:
            # 验证连接
            if await self._verify_connection():
                self.logger.info("持仓管理器初始化完成")
                return self
            else:
                raise ValueError("持仓管理器连接验证失败")
        except Exception as e:
            self.logger.error(f"持仓管理器初始化失败: {str(e)}")
            raise

    async def _verify_connection(self) -> bool:
        """验证连接状态"""
        try:
            # 验证账户状态
            try:
                trade_ctx = await self._get_trade_ctx()
                if not trade_ctx:
                    self.logger.error("无法获取交易连接")
                    return False
                    
                # 获取账户余额
                try:
                    balance = await trade_ctx.account_balance()
                    if not balance:
                        self.logger.error("账户余额为空")
                        return False
                    
                    # 构建余额信息
                    balance_info = (
                        f"账户余额 ({balance.currency}):\n"
                        f"  总资产: ${float(balance.net_assets):,.2f}\n"
                        f"  初始保证金: ${float(balance.init_margin):,.2f}\n"
                        f"  维持保证金: ${float(balance.maintenance_margin):,.2f}\n"
                        f"  风险等级: {balance.risk_level}"
                    )
                    
                    self.logger.info(balance_info)
                    
                    # 保存账户余额
                    self.account_balances = balance
                    return True
                    
                except OpenApiException as e:
                    self.logger.error(f"获取账户余额失败: {str(e)}")
                    return False
                
            except Exception as e:
                self.logger.error(f"验证账户状态时出错: {str(e)}")
                return False
            
        except Exception as e:
            self.logger.error(f"验证连接时出错: {str(e)}")
            return False

    async def close(self):
        """关闭持仓管理器"""
        try:
            # 关闭交易连接
            if self._trade_ctx:
                try:
                    # 手动清理资源
                    self._trade_ctx = None
                except Exception as e:
                    self.logger.warning(f"关闭交易连接时出错: {str(e)}")
            
            self.logger.info("持仓管理器已关闭")
        except Exception as e:
            self.logger.error(f"关闭持仓管理器时出错: {str(e)}")

    async def get_positions(self) -> Optional[List[Dict[str, Any]]]:
        """获取持仓数据"""
        try:
            # 检查缓存
            cache_data = self._cache.get('positions', {}).get('all')
            if cache_data and time.time() - cache_data['time'] < self.cache_config['positions']:
                return cache_data['data']

            trade_ctx = await self._get_trade_ctx()
            if not trade_ctx:
                raise ValueError("交易连接未就绪")

            # 获取持仓数据
            positions = await trade_ctx.stock_positions()
            
            # 更新缓存
            self._cache.setdefault('positions', {})['all'] = {
                'data': positions,
                'time': time.time()
            }
            
            return positions

        except Exception as e:
            self.logger.error(f"获取持仓数据时出错: {str(e)}")
            return None

    def calculate_total_value(self, positions: List[Dict[str, Any]]) -> float:
        """
        计算总持仓市值
        
        Args:
            positions: 持仓列表
        
        Returns:
            float: 总市值
        """
        try:
            total_value = 0.0
            for pos in positions:
                # 获取合约单位
                contract_size = pos.get('contract_size', 1)
                
                # 计算市值
                market_value = (
                    float(pos['quantity']) * 
                    float(pos['current_price']) * 
                    contract_size
                )
                total_value += market_value
            
            return total_value
            
        except Exception as e:
            self.logger.error(f"计算总市值时出错: {str(e)}")
            return 0.0

    def is_option(self, symbol: str) -> bool:
        """
        判断是否为期权符号
        
        Args:
            symbol: 标的代码
            
        Returns:
            bool: 是否为期权
        """
        # 期权格式: AAPL240216C00180000.US
        pattern = r'^[A-Z]+\d{6}[CP]\d+\.[A-Z]{2}$'
        return bool(re.match(pattern, symbol))

    async def open_position(self, symbol: str, quantity: int) -> bool:
        """开仓"""
        try:
            # 参数验证
            if not symbol or quantity <= 0:
                error_msg = f"开仓参数无效: 标的={symbol}, 数量={quantity}"
                self.logger.error(error_msg)
                return False
            
            # 检查是否为期权合约
            if self.is_option(symbol):
                self.logger.error(f"请使用标的代码而不是期权合约代码: {symbol}")
                return False
            
            # 1. 检查市场状态
            if not await self.time_checker.can_trade():
                self.logger.warning("当前不在交易时段")
                return False
                
            # 2. 检查风险限制
            risk_result, risk_msg, _ = await self.risk_checker.check_market_risk(symbol)
            if risk_result:
                self.logger.warning(f"开仓受限: {risk_msg}")
                return False
                
            # 3. 检查持仓限制
            position_check = await self._check_position_limits(symbol, quantity)
            if not position_check[0]:
                self.logger.warning(f"持仓限制: {position_check[1]}")
                return False
                
            # 4. 获取期权合约和交易方向 (根据策略信号)
            contract_info = await self.option_strategy.select_option_contract(
                symbol=symbol
            )
            
            if not contract_info:
                self.logger.warning(
                    f"未找到合适的期权合约:\n"
                    f"  标的: {symbol}\n"
                    f"  原因: 可能是信号不足或不符合策略条件"
                )
                return False
            
            contract = contract_info['symbol']
            side = contract_info['side']
            
            # 获取合约报价
            quote = await self.data_manager.get_quote(contract)
            if not quote:
                self.logger.error(f"无法获取合约 {contract} 的报价")
                return False
            
            # 智能选择订单类型和价格
            order_info = await self._get_smart_order_params(quote, side, quantity)
            if not order_info:
                return False
                
            # 构建开仓信息字符串
            order_msg = (
                f"准备开仓: {symbol}\n"
                f"  合约: {contract}\n"
                f"  方向: {side.upper()}\n"
                f"  数量: {quantity}\n"
                f"  类型: {order_info['type']}\n"
                f"  价格: {order_info['price'] if order_info['price'] else '市价'}\n"
                f"  策略: {order_info['strategy']}"
            )
            
            # 使用单行日志记录
            self.logger.info(order_msg)
            
            # 执行开仓订单
            order_config = self.risk_checker.DEFAULT_RISK_LIMITS['option']['order_execution']
            for attempt in range(order_config['max_retry']):
                try:
                    order_result = await self.execute_order(
                        contract,
                        side,
                        quantity,
                        order_info['type'],
                        price=order_info['price'],
                        reason=f"策略{side.upper()}开仓 (尝试 {attempt + 1}/{order_config['max_retry']})"
                    )
                    
                    if order_result:
                        return True
                        
                    # 订单失败，调整价格重试
                    if attempt < order_config['max_retry'] - 1:
                        order_info = await self._adjust_order_params(order_info, quote, side, attempt)
                        await asyncio.sleep(order_config['retry_interval'])
                        
                except Exception as e:
                    self.logger.error(f"订单执行出错 (尝试 {attempt + 1}): {str(e)}")
                    if attempt < order_config['max_retry'] - 1:
                        await asyncio.sleep(order_config['retry_interval'])
            
            return False
            
        except Exception as e:
            self.logger.error(f"开仓操作出错: {str(e)}")
            return False

    async def _get_smart_order_params(self, quote: Dict, side: str, quantity: int) -> Optional[Dict]:
        """智能选择订单参数"""
        try:
            bid = float(quote['bid'])
            ask = float(quote['ask'])
            last = float(quote['last_done'])
            volume = float(quote['volume'])
            spread = ask - bid
            spread_ratio = spread / bid if bid > 0 else float('inf')
            
            order_config = self.risk_checker.DEFAULT_RISK_LIMITS['option']['order_execution']
            
            # 1. 检查点差
            if spread_ratio > order_config['execution_rules']['max_spread_ratio']:
                # 点差过大，使用限价单
                if side.lower() == 'buy':
                    price = bid + spread * 0.3  # 略高于买一价
                    strategy = "大点差-限价单(买一上方)"
                else:
                    price = ask - spread * 0.3  # 略低于卖一价
                    strategy = "大点差-限价单(卖一下方)"
                return {
                    'type': 'limit',
                    'price': price,
                    'strategy': strategy
                }
            
            # 2. 检查流动性
            if volume < order_config['execution_rules']['min_liquidity']:
                # 流动性不足，使用限价单
                if side.lower() == 'buy':
                    price = ask  # 挂卖一价
                    strategy = "低流动性-限价单(卖一价)"
                else:
                    price = bid  # 挂买一价
                    strategy = "低流动性-限价单(买一价)"
                return {
                    'type': 'limit',
                    'price': price,
                    'strategy': strategy
                }
            
            # 3. 正常流动性情况
            if spread_ratio <= 0.01:  # 点差小于1%
                # 使用市价单
                return {
                    'type': 'market',
                    'price': None,
                    'strategy': "正常流动性-市价单"
                }
            else:
                # 使用限价单
                if side.lower() == 'buy':
                    price = min(ask, last * 1.002)  # 不超过最新价2‰
                    strategy = "正常流动性-限价单(买入)"
                else:
                    price = max(bid, last * 0.998)  # 不低于最新价2‰
                    strategy = "正常流动性-限价单(卖出)"
                return {
                    'type': 'limit',
                    'price': price,
                    'strategy': strategy
                }
                
        except Exception as e:
            self.logger.error(f"计算订单参数时出错: {str(e)}")
            return None

    async def _adjust_order_params(self, order_info: Dict, quote: Dict, side: str, attempt: int) -> Dict:
        """调整订单参数用于重试"""
        if order_info['type'] == 'limit':
            adjust_ratio = self.risk_checker.DEFAULT_RISK_LIMITS['option']['order_execution']['max_retry_price_adjust']
            if side.lower() == 'buy':
                # 买入订单，每次重试提高价格
                order_info['price'] *= (1 + adjust_ratio * (attempt + 1))
                order_info['strategy'] = f"重试调整-提高买入价{adjust_ratio * (attempt + 1):.1%}"
            else:
                # 卖出订单，每次重试降低价格
                order_info['price'] *= (1 - adjust_ratio * (attempt + 1))
                order_info['strategy'] = f"重试调整-降低卖出价{adjust_ratio * (attempt + 1):.1%}"
        else:
            # 市价单重试改为限价单
            bid = float(quote['bid'])
            ask = float(quote['ask'])
            if side.lower() == 'buy':
                order_info['type'] = 'limit'
                order_info['price'] = ask * 1.005  # 高于卖一价0.5%
                order_info['strategy'] = "市价改限价-买入"
            else:
                order_info['type'] = 'limit'
                order_info['price'] = bid * 0.995  # 低于买一价0.5%
                order_info['strategy'] = "市价改限价-卖出"
        return order_info

    async def execute_order(self, 
                          symbol: str, 
                          side: str,
                          volume: int,
                          order_type: str = 'market',
                          price: float = None,
                          reason: str = "") -> bool:
        """执行订单"""
        try:
            # 获取订单执行配置
            order_config = self.risk_checker.DEFAULT_RISK_LIMITS['option']['order_execution']
            
            self.logger.info(
                f"准备执行订单:\n"
                f"  标的: {symbol}\n"
                f"  方向: {side}\n"
                f"  数量: {volume}\n"
                f"  类型: {order_type}\n"
                f"  价格: {price if price else '市价'}\n"
                f"  原因: {reason}"
            )
            
            # 检查交易量限制
            if not order_config['min_volume'] <= volume <= order_config['max_volume']:
                self.logger.error(f"交易量超出限制: {volume}")
                return False
            
            # 转换为 LongPort 的订单类型
            order_side = OrderSide.Buy if side.lower() == 'buy' else OrderSide.Sell
            order_type_enum = OrderType.Market if order_type.lower() == 'market' else OrderType.Limit
            
            # 重试机制
            for attempt in range(order_config['max_retry']):
                try:
                    trade_ctx = await self._get_trade_ctx()
                    
                    # 检查点差
                    if order_config['execution_rules']['avoid_high_spread']:
                        quote = await self.data_manager.get_quote(symbol)
                        if quote:
                            spread_ratio = (quote['ask'] - quote['bid']) / quote['bid']
                            if spread_ratio > order_config['execution_rules']['max_spread_ratio']:
                                self.logger.warning(f"点差过大: {spread_ratio:.2%}")
                                return False
                    
                    # 提交订单
                    order = trade_ctx.submit_order(
                        symbol=symbol,
                        order_type=order_type_enum,
                        side=order_side,
                        submitted_quantity=volume,
                        price=price if order_type_enum == OrderType.Limit else None,
                        time_in_force=TimeInForceType.Day,
                        remark=f"{reason} - Attempt {attempt + 1}"
                    )
                    
                    if not order or not hasattr(order, 'order_id'):
                        raise ValueError("订单提交失败")
                    
                    # 等待订单成交
                    filled = await self._wait_order_fill(order.order_id, timeout=order_config['timeout'])
                    if filled:
                        await self._record_trade(order, reason)
                        return True
                    
                    # 如果未完全成交且不允许部分成交，尝试撤单
                    if not order_config['execution_rules']['allow_partial_fill']:
                        await trade_ctx.cancel_order(order.order_id)
                    
                except Exception as e:
                    self.logger.error(f"订单执行失败 (尝试 {attempt + 1}): {str(e)}")
                    if attempt < order_config['max_retry'] - 1:
                        await asyncio.sleep(order_config['retry_interval'])
            
            return False
            
        except Exception as e:
            self.logger.error(f"订单执行出错: {str(e)}")
            return False

    async def _wait_order_fill(self, order_id: str, timeout: int = 10) -> bool:
        """等待订单成交"""
        try:
            start_time = time.time()
            while (datetime.now() - start_time).seconds < timeout:
                order_status = await self.get_order_status(order_id)
                if not order_status:
                    return False
                
                if order_status['status'] == OrderStatus.Filled:
                    return True
                elif order_status['status'] in [OrderStatus.Failed, OrderStatus.Rejected, OrderStatus.Cancelled]:
                    return False
                    
                await asyncio.sleep(0.5)
                
            return False
            
        except Exception as e:
            self.logger.error(f"等待订单成交时出错: {str(e)}")
            return False

    async def _record_trade(self, order: Any, reason: str):
        """记录交易历史"""
        try:
            trade_ctx = await self._get_trade_ctx()
            order_detail = trade_ctx.order_detail(order.order_id)
            trade_date = datetime.now(self.tz).strftime('%Y-%m-%d')
            
            trade_record = {
                'time': datetime.now(self.tz),
                'symbol': order.symbol,
                'side': order.side,
                'volume': float(order_detail.executed_quantity),
                'price': float(order_detail.executed_price),
                'value': float(order_detail.executed_quantity) * float(order_detail.executed_price),
                'reason': reason,
                'order_id': order.order_id
            }
            
            # 更新交易历史
            self.position_history['trades'].append(trade_record)
            
            # 更新每日汇总
            if trade_date not in self.position_history['daily_summary']:
                self.position_history['daily_summary'][trade_date] = {
                    'trades': 0,
                    'volume': 0,
                    'value': 0.0,
                    'commission': 0.0,
                    'pnl': 0.0
                }
            
            daily = self.position_history['daily_summary'][trade_date]
            daily['trades'] += 1
            daily['volume'] += trade_record['volume']
            daily['value'] += trade_record['value']
            
            # 更新持仓记录
            symbol = order.symbol
            if symbol not in self.position_history['positions']:
                self.position_history['positions'][symbol] = []
            
            self.position_history['positions'][symbol].append(trade_record)
            
            self.logger.info(
                f"交易记录已更新:\n"
                f"  标的: {trade_record['symbol']}\n"
                f"  方向: {trade_record['side']}\n"
                f"  数量: {trade_record['volume']}\n"
                f"  价格: ${trade_record['price']:.2f}\n"
                f"  金额: ${trade_record['value']:.2f}\n"
                f"  原因: {trade_record['reason']}"
            )
            
        except Exception as e:
            self.logger.error(f"记录交易历史时出错: {str(e)}")

    def _reset_daily_orders(self):
        """重置当日开单记录"""
        self.daily_orders = {}
        
    def _reset_daily_trades(self):
        """重置当日交易记录"""
        self.daily_trades = {}
        
    def _can_trade_today(self, symbol: str) -> bool:
        """检查标的是否可以交易"""
        try:
            # 检查是否是期权
            is_option = '.US' in symbol and any(x in symbol for x in ['C', 'P'])
            if not is_option:
                self.logger.warning(f"{symbol} 不是期权，不进行交易")
                return False
            
            # 检查是否在监控列表中
            base_symbol = symbol.split('C')[0].split('P')[0] + '.US'  # 提取正股代码
            if base_symbol not in self.watch_list:
                self.logger.warning(f"{symbol} 的正股 {base_symbol} 不在监控列表中")
                return False
            
            # 检查当日是否已交易
            if symbol in self.daily_trades:
                last_trade_time = self.daily_trades[symbol]
                # 使用 time_checker 判断是否是新交易日
                if not self.risk_checker.time_checker.is_new_trading_day(last_trade_time):
                    self.logger.warning(f"{symbol} 当日已交易，不再交易")
                    return False
                else:
                    # 新的交易日，清除旧记录
                    del self.daily_trades[symbol]
                
            return True
            
        except Exception as e:
            self.logger.error(f"检查交易条件时出错: {str(e)}")
            return False

    async def _get_quote_ctx(self):
        """获取行情连接（带连接管理）"""
        async with self._quote_ctx_lock:
            current_time = time.time()
            
            # 检查是否需要重新连接
            if (self._quote_ctx is None or 
                current_time - self._last_quote_time > self._connection_timeout):
                
                # 关闭旧连接
                if self._quote_ctx:
                    try:
                        # 尝试异步关闭
                        if hasattr(self._quote_ctx, 'async_close'):
                            await self._quote_ctx.async_close()
                        # 如果没有异步方法，使用同步方法
                        elif hasattr(self._quote_ctx, 'close'):
                            self._quote_ctx.close()
                    except Exception as e:
                        self.logger.warning(f"关闭旧行情连接时出错: {str(e)}")
                
                try:
                    # 创建新连接前等待
                    await asyncio.sleep(1)
                    
                    # 使用 data_manager 的行情连接
                    self._quote_ctx = await self.data_manager.ensure_quote_ctx()
                    if not self._quote_ctx:
                        raise ValueError("无法创建行情连接")
                        
                    self._last_quote_time = current_time
                    
                except Exception as e:
                    self.logger.error(f"创建行情连接失败: {str(e)}")
                    self._quote_ctx = None
                    raise
            
            return self._quote_ctx

    async def _get_trade_ctx(self):
        """获取交易连接（带连接管理）"""
        async with self._trade_ctx_lock:
            current_time = time.time()
            
            # 检查是否需要重新连接
            if (self._trade_ctx is None or 
                current_time - self._last_trade_time > self._connection_timeout):
                
                # 关闭旧连接
                if self._trade_ctx:
                    try:
                        await self._trade_ctx.close()
                    except Exception as e:
                        self.logger.warning(f"关闭旧交易连接时出错: {str(e)}")
                
                self._trade_ctx = None
                
                try:
                    # 创建新连接前等待
                    await asyncio.sleep(1)
                    
                    # 创建新连接
                    self._trade_ctx = TradeContext(self.longport_config)
                    
                    # 等待连接建立并验证
                    await self._trade_ctx.connect()
                    await asyncio.sleep(2)
                    
                    # 验证连接
                    try:
                        await self._trade_ctx.account_balance()
                        self._last_trade_time = current_time
                        self.logger.info("交易连接验证成功")
                    except Exception as e:
                        self.logger.error(f"交易连接验证失败: {str(e)}")
                        self._trade_ctx = None
                        raise
                
                except Exception as e:
                    self.logger.error(f"创建交易连接失败: {str(e)}")
                    self._trade_ctx = None
                    raise
            
            return self._trade_ctx

    async def check_rate_limit(self):
        """检查请求限制"""
        try:
            current_time = time.time()
            # 清理过期的请求记录
            self.request_times = [t for t in self.request_times 
                                if current_time - t < self.request_limit['time_window']]
            
            # 检查是否超过限制
            if len(self.request_times) >= self.request_limit['max_requests']:
                wait_time = self.request_times[0] + self.request_limit['time_window'] - current_time
                if wait_time > 0:
                    self.logger.warning(f"达到请求限制，等待 {wait_time:.1f} 秒")
                    await asyncio.sleep(wait_time)
            
            # 记录新的请求时间
            self.request_times.append(current_time)
            
        except Exception as e:
            self.logger.error(f"检查请求限制时出错: {str(e)}")

    async def get_order_status(self, order_id: str) -> Dict[str, Any]:
        """获取订单状态"""
        try:
            trade_ctx = await self._get_trade_ctx()
            order_detail = trade_ctx.order_detail(order_id)
            
            status = {
                'order_id': order_id,
                'status': order_detail.status,
                'filled_quantity': float(order_detail.executed_quantity),
                'filled_price': float(order_detail.executed_price) if order_detail.executed_price else None,
                'filled_amount': float(order_detail.executed_amount) if order_detail.executed_amount else None,
                'create_time': order_detail.create_time,
                'update_time': order_detail.update_time
            }
            
            return status
            
        except Exception as e:
            self.logger.error(f"获取订单状态时出错: {str(e)}")
            return None

    async def get_real_positions(self):
        """获取实际持仓数据"""
        try:
            positions = await self.get_positions()
            
            # 将持仓数据分类
            result = {
                'active': [],    # 当前持仓
                'closed': [],    # 已平仓
                'pending': []    # 待成交
            }
            
            for pos in positions:
                try:
                    # 计算基础值
                    quantity = float(pos['quantity'])
                    cost_price = float(pos['cost_price'])
                    cost_basis = quantity * cost_price
                    
                    # 添加额外的持仓信息
                    if cost_basis != 0:
                        pos['unrealized_pl'] = float(pos['market_value']) - cost_basis
                        pos['unrealized_pl_rate'] = pos['unrealized_pl'] / cost_basis
                    else:
                        pos['unrealized_pl'] = 0
                        pos['unrealized_pl_rate'] = 0
                    
                    # 计算保证金要求
                    position_value = quantity * float(pos['current_price'])
                    pos['margin'] = {
                        'initial': position_value * 0.2,  # 初始保证金率20%
                        'maintenance': position_value * 0.15  # 维持保证金率15%
                    }
                    
                    # 根据状态分类
                    if quantity > 0:
                        result['active'].append(pos)
                    elif quantity < 0:
                        result['closed'].append(pos)
                    else:
                        result['pending'].append(pos)
                        
                except Exception as e:
                    self.logger.error(f"处理持仓数据时出错: {str(e)}")
                    continue
            
            # 记录持仓状态
            self.logger.info(
                f"当前持仓状态:\n"
                f"  活跃持仓: {len(result['active'])}\n"
                f"  已平仓: {len(result['closed'])}\n"
                f"  待成交: {len(result['pending'])}"
            )
            
            return result
            
        except Exception as e:
            self.logger.error(f"获取实际持仓数据时出错: {str(e)}")
            return {
                'active': [],
                'closed': [],
                'pending': []
            }

    async def get_position_summary(self):
        """获取持仓摘要"""
        try:
            positions = await self.get_real_positions()
            
            # 计算持仓统计
            summary = {
                'total_positions': len(positions['active']),
                'total_value': sum(float(p['market_value']) for p in positions['active']),
                'total_cost': sum(float(p['quantity']) * float(p['cost_price']) for p in positions['active']),
                'total_pl': sum(p.get('unrealized_pl', 0) for p in positions['active']),
                'margin_used': sum(p['margin']['initial'] for p in positions['active']),
                'positions_by_symbol': {}
            }
            
            # 按标的统计
            for pos in positions['active']:
                symbol = pos['symbol']
                if symbol not in summary['positions_by_symbol']:
                    summary['positions_by_symbol'][symbol] = {
                        'quantity': 0,
                        'market_value': 0,
                        'unrealized_pl': 0
                    }
                
                summary['positions_by_symbol'][symbol]['quantity'] += float(pos['quantity'])
                summary['positions_by_symbol'][symbol]['market_value'] += float(pos['market_value'])
                summary['positions_by_symbol'][symbol]['unrealized_pl'] += pos.get('unrealized_pl', 0)
            
            # 记录摘要信息
            self.logger.info(
                f"\n{'=' * 50}\n"
                f"持仓摘要:\n"
                f"  总持仓数: {summary['total_positions']}\n"
                f"  总市值: ${summary['total_value']:.2f}\n"
                f"  总成本: ${summary['total_cost']:,.2f}\n"
                f"  未实现盈亏: ${summary['total_pl']:,.2f}\n"
                f"  使用保证金: ${summary['margin_used']:,.2f}\n"
                f"{'=' * 50}"
            )
            
            return summary
            
        except Exception as e:
            self.logger.error(f"获取持仓摘要时出错: {str(e)}")
            return {
                'total_positions': 0,
                'total_value': 0,
                'total_cost': 0,
                'total_pl': 0,
                'margin_used': 0,
                'positions_by_symbol': {}
            }

    def handle_positions_response(self, response):
        if not hasattr(response, 'positions'):
            logging.error("持仓响应中没有 positions 属性")
            return []
        
        try:
            positions = response.positions
            # 处理持仓数据
            return positions
        except Exception as e:
            logging.error(f"处理持仓数据时出错: {e}")
            return []

    async def process_position(self, position: Dict[str, Any]) -> None:
        """
        处理单个持仓
        
        Args:
            position: 持仓信息字典
        """
        try:
            # 获取市场数据
            market_data = await self.data_manager.get_market_data()
            
            # 使用风险检查器进行检查
            need_close, reason, close_ratio = await self.risk_checker.check_position_risk(position, market_data)
            
            if need_close:
                self.logger.warning(f"持仓需要平仓: {position.get('symbol')}, 原因: {reason}, 平仓比例: {close_ratio:.0%}")
                # 执行平仓操作
                await self.close_position(position, close_ratio, reason)
            else:
                self.logger.debug(f"持仓检查通过: {position.get('symbol')}")
                
        except Exception as e:
            self.logger.error(f"处理持仓时出错 ({position.get('symbol')}): {str(e)}")

    async def close_position(self, position: Dict[str, Any], ratio: float = 1.0, reason: str = "") -> bool:
        """平仓"""
        try:
            symbol = position.get('symbol', '')
            quantity = int(float(position.get('quantity', 0)) * ratio)
            
            # 构建日志信息
            log_message = (
                f"准备平仓:\n"
                f"  标的: {symbol}\n"
                f"  数量: {quantity}\n"
                f"  比例: {ratio:.1%}\n"
                f"  原因: {reason}"
            )
            self.logger.info(log_message)
            
            if not symbol or not quantity:
                self.logger.error("持仓信息不完整")
                return False
                
            # 执行平仓订单
            side = 'sell' if position.get('position_side', '').lower() == 'long' else 'buy'
            order_result = await self.execute_order(
                symbol,
                side,
                quantity,
                'market',
                reason=reason
            )
            
            if order_result:
                success_message = (
                    f"平仓成功:\n"
                    f"  标的: {symbol}\n"
                    f"  数量: {quantity}\n"
                    f"  比例: {ratio:.1%}\n"
                    f"  原因: {reason}"
                )
                self.logger.info(success_message)
                return True
                
            return False
            
        except Exception as e:
            self.logger.error(f"平仓失败: {str(e)}")
            return False

    async def close_all_positions(self, reason: str = ""):
        """
        平掉所有持仓
        """
        try:
            # 1. 检查交易时间
            if not await self.data_manager.time_checker.can_trade():
                self.logger.warning("当前不在交易时间，无法平仓")
                return False
            
            # 2. 获取所有持仓
            positions = await self.get_positions()
            if not positions:
                self.logger.info("没有需要平仓的持仓")
                return True
            
            # 3. 逐个平仓
            success = True
            for position in positions:
                symbol = position['symbol']
                try:
                    result = await self.close_position(
                        position,
                        ratio=1.0,
                        reason=reason
                    )
                    if not result:
                        success = False
                        self.logger.error(f"平仓失败: {symbol}")
                    
                except Exception as e:
                    success = False
                    self.logger.error(f"平仓 {symbol} 时出错: {str(e)}")
                    continue
                
            return success
            
        except Exception as e:
            self.logger.error(f"平仓所有持仓时出错: {str(e)}")
            return False

    async def _wait_order_filled(self, order_id: str, timeout: int = 10) -> bool:
        """
        等待订单成交
        
        Args:
            order_id: 订单ID
            timeout: 超时时间(秒)
        """
        try:
            start_time = time.time()
            while time.time() - start_time < timeout:
                trade_ctx = await self._get_trade_ctx()
                if not trade_ctx:
                    raise ValueError("交易连接未就绪")
                
                order = trade_ctx.order_detail(order_id)
                if order.status in [OrderStatus.Filled, OrderStatus.PartiallyFilled]:
                    return True
                elif order.status in [OrderStatus.Failed, OrderStatus.Cancelled]:
                    return False
                
                await asyncio.sleep(0.5)
            
            return False
            
        except Exception as e:
            self.logger.error(f"等待订单成交时出错: {str(e)}")
            return False

    async def update_position_status(self):
        """更新持仓状态"""
        try:
            positions = await self.get_positions()
            if not positions:
                self.position_status.update({
                    'total_value': 0.0,
                    'total_pnl': 0.0,
                    'daily_pnl': 0.0,
                    'daily_pnl_rate': 0.0,
                    'last_update': datetime.now(self.tz)
                })
                return
            
            # 计算总市值和盈亏
            total_value = sum(pos['market_value'] for pos in positions)
            total_pnl = sum(pos['unrealized_pl'] for pos in positions)
            daily_pnl = sum(pos['daily_pnl'] for pos in positions)
            
            # 计算总的当日盈亏率
            daily_pnl_rate = (daily_pnl / total_value) * 100 if total_value > 0 else 0.0
            
            # 更新状态
            self.position_status.update({
                'total_value': total_value,
                'total_pnl': total_pnl,
                'daily_pnl': daily_pnl,
                'daily_pnl_rate': daily_pnl_rate,
                'last_update': datetime.now(self.tz)
            })
            
            # 打印每个持仓的详细信息
            position_info = []
            for pos in positions:
                position_info.append(
                    f"| {pos['symbol']:<20} | {pos['quantity']:>8} | USD {pos['current_price']:>9.2f} | "
                    f"{pos['cost_price']:>6.2f} -> {pos['current_price']:>6.2f} "
                    f"({pos['daily_pnl_rate']:>7.2f}%) | "
                    f"当日: $ {pos['daily_pnl']:>8.2f} ({pos['daily_pnl_rate']:>7.2f}%) |"
                )
            
            self.logger.info(
                f"\n{'=' * 80}\n"
                f"| {'标的':<20} | {'数量':>8} | {'币种 现价':>12} | {'成本 -> 现价 (变动率)':>28} | {'当日盈亏 (收益率)':>25} |\n"
                f"{'-' * 80}\n"
                f"{chr(10).join(position_info)}\n"
                f"{'=' * 80}\n"
                f"总市值: ${total_value:,.2f}\n"
                f"总盈亏: ${total_pnl:,.2f}\n"
                f"当日盈亏: ${daily_pnl:,.2f} ({daily_pnl_rate:.2f}%)"
            )
            
        except Exception as e:
            self.logger.error(f"更新持仓状态时出错: {str(e)}")

    def process_symbol(self, symbol: str) -> None:
        """
        处理交易标的
        
        Args:
            symbol: 标的代码
        """
        try:
            # 1. 检查标的是否在监控列表中
            if symbol in self.watch_list:
                self.logger.debug(f"处理正股: {symbol}")
                # ... 正股处理逻辑
                return
            
            # 2. 如果不在监控列表中，检查是否是期权
            if self.is_option(symbol):
                underlying = self.get_underlying_symbol(symbol)
                if not underlying:
                    self.logger.warning(f"无法从期权 {symbol} 解析出正股代码")
                    return
                
                if underlying in self.watch_list:
                    self.logger.debug(f"处理期权: {symbol} (正股: {underlying})")
                    # ... 期权处理逻辑
                    return
                else:
                    self.logger.warning(f"期权 {symbol} 的正股 {underlying} 不在监控列表中")
                    return
            
            # 3. 既不是监控的正股也不是期权
            self.logger.warning(f"标的 {symbol} 不在监控列表中")
            
        except Exception as e:
            self.logger.error(f"处理标的时出错 ({symbol}): {str(e)}")

    def get_underlying_symbol(self, symbol: str) -> Optional[str]:
        """
        从期权符号中提取正股代码
        
        Args:
            symbol: 期权代码 (例如: AAPL240216C00180000.US)
            
        Returns:
            Optional[str]: 正股代码 (例如: AAPL.US)，如果解析失败则返回 None
        """
        try:
            if not symbol or '.' not in symbol:
                self.logger.warning(f"无效的期权代码格式: {symbol}")
                return None
            
            # 分割代码和市场
            code, market = symbol.split('.')
            if not market:
                self.logger.warning(f"无效的市场代码: {symbol}")
                return None
            
            # 提取正股代码 - 使用更严格的正则表达式
            match = re.match(r'^([A-Z]{1,6})\d{6}[CP]\d+$', code)
            if not match:
                self.logger.warning(f"无法解析期权代码: {symbol}")
                return None
            
            underlying = match.group(1)
            if not underlying:
                self.logger.warning(f"无法提取正股代码: {symbol}")
                return None
            
            # 组合正股代码和市场
            result = f"{underlying}.{market}"
            
            # 验证正股代码是否在监控列表中
            if result not in self.watch_list:
                self.logger.debug(f"正股 {result} 不在监控列表中")
            
            return result
            
        except Exception as e:
            self.logger.error(f"解析正股代码时出错 ({symbol}): {str(e)}")
            return None

    def is_option(self, symbol: str) -> bool:
        """
        判断是否为期权代码
        
        Args:
            symbol: 标的代码 (例如: AAPL240216C00180000.US)
            
        Returns:
            bool: 是否为期权
        """
        try:
            if not symbol:
                return False
            
            # 更严格的期权代码格式验证
            pattern = r'^[A-Z]{1,6}\d{6}[CP]\d{8,9}\.[A-Z]{2}$'
            return bool(re.match(pattern, symbol))
        
        except Exception as e:
            self.logger.error(f"检查期权代码时出错 ({symbol}): {str(e)}")
            return False

    def get_contract_size(self, symbol: str) -> int:
        """
        获取合约单位
        
        Args:
            symbol: 标的代码
            
        Returns:
            int: 合约单位
        """
        try:
            # 检查是否是期权
            if self.is_option(symbol):
                # 美股期权标准合约单位为100
                return 100
            
            # 股票合约单位为1
            return 1
        
        except Exception as e:
            self.logger.error(f"获取合约单位时出错: {str(e)}")
            return 1

    async def get_account_balance(self) -> Dict[str, Any]:
        """获取账户余额信息"""
        try:
            trade_ctx = await self._get_trade_ctx()
            if not trade_ctx:
                raise ValueError("交易连接未就绪")
            
            # 获取账户余额
            balances = await trade_ctx.account_balance()
            if not balances:
                return {}
            
            # 转换为标准格式
            result = {}
            for balance in balances:
                try:
                    # 获取实际的货币类型
                    currency = balance.currency
                    result = {
                        'total_cash': float(balance.total_cash),
                        'available_cash': float(balance.available_cash),
                        'buying_power': float(balance.buying_power),
                        'currency': currency,
                        'net_assets': float(balance.net_assets),
                        'init_margin': float(balance.init_margin),
                        'maintenance_margin': float(balance.maintenance_margin),
                        'risk_level': balance.risk_level,
                        'margin_call': float(balance.margin_call)
                    }
                    break  # 只处理第一个账户
                except Exception as e:
                    self.logger.error(f"处理账户余额数据时出错: {str(e)}")
                    continue
            
            # 打印账户余额信息
            if result:
                self.logger.info(
                    f"\n{'=' * 50}\n"
                    f"账户余额信息 ({result['currency']}):\n"  # 使用实际货币单位
                    f"净资产: ${result['net_assets']:,.2f}\n"
                    f"总现金: ${result['total_cash']:,.2f}\n"
                    f"可用现金: ${result['available_cash']:,.2f}\n"
                    f"购买力: ${result['buying_power']:,.2f}\n"
                    f"初始保证金: ${result['init_margin']:,.2f}\n"
                    f"维持保证金: ${result['maintenance_margin']:,.2f}\n"
                    f"风险等级: {result['risk_level']}\n"
                    f"追保金额: ${result['margin_call']:,.2f}\n"
                    f"{'=' * 50}"
                )
            
            return result
            
        except Exception as e:
            self.logger.error(f"获取账户余额时出错: {str(e)}")
            return {}

    async def monitor_positions(self):
        """持仓监控主循环"""
        try:
            while True:
                positions = await self.get_positions()
                for position in positions:
                    try:
                        # 直接使用 risk_checker 检查风险
                        need_close, reason, ratio = await self.risk_checker.check_position_risk(position, {})
                        if need_close:
                            await self.close_position(
                                position,
                                ratio=ratio,
                                reason=reason
                            )
                            
                    except Exception as e:
                        self.logger.error(f"监控持仓出错 ({position['symbol']}): {str(e)}")
                    
                await asyncio.sleep(1)
                
        except Exception as e:
            self.logger.error(f"持仓监控主循环出错: {str(e)}")

    async def get_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        """获取单个持仓信息"""
        try:
            positions = await self.get_positions()
            for pos in positions:
                if pos['symbol'] == symbol:
                    return pos
            return None
        
        except Exception as e:
            self.logger.error(f"获取持仓信息时出错: {str(e)}")
            return None

    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.async_init()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.close()

    async def check_position(self, position: Dict[str, Any]) -> Tuple[bool, str, float]:
        """检查持仓是否需要平仓"""
        try:
            symbol = position.get('symbol', '')
            if not symbol:
                return False, "持仓信息不完整", 0
                
            # 获取市场数据
            market_data = await self.data_manager.get_market_data(symbol)
            if not market_data:
                self.logger.warning(f"无法获取 {symbol} 的市场数据")
                return False, "无市场数据", 0
                
            # 1. 检查时间风险
            time_result, time_msg, time_ratio = await self.time_checker.check_time_risk(position)
            if time_result:
                self.logger.info(f"持仓检查结果 - {symbol}: 需要平仓(时间风险), 原因: {time_msg}, 平仓比例: {time_ratio:.1%}")
                return True, time_msg, time_ratio
                
            # 2. 检查收盘前平仓保护
            close_result, close_msg, close_ratio = await self.time_checker.check_close_protection(position)
            if close_result:
                self.logger.info(f"持仓检查结果 - {symbol}: 需要平仓(收盘保护), 原因: {close_msg}, 平仓比例: {close_ratio:.1%}")
                return True, close_msg, close_ratio
                
            # 3. 检查持仓风险
            risk_result, risk_msg, risk_ratio = await self.risk_checker.check_position_risk(position, market_data)
            if risk_result:
                self.logger.info(f"持仓检查结果 - {symbol}: 需要平仓(风险控制), 原因: {risk_msg}, 平仓比例: {risk_ratio:.1%}")
                return True, risk_msg, risk_ratio
                
            # 无需平仓时也记录日志
            self.logger.debug(f"持仓检查结果 - {symbol}: 正常, 无需平仓")
            return False, "", 0
            
        except Exception as e:
            error_msg = f"检查持仓时出错: {str(e)}"
            self.logger.error(f"持仓检查结果 - {symbol}: 错误, {error_msg}")
            return False, error_msg, 0

    async def _check_position_limits(self, symbol: str, quantity: int) -> Tuple[bool, str]:
        """检查持仓限制"""
        try:
            # 1. 检查单个合约持仓限制
            max_contracts = self.risk_checker.DEFAULT_RISK_LIMITS['option']['max_contracts']
            if quantity > max_contracts:
                return False, f"超过单个合约持仓限制 ({quantity} > {max_contracts})"
            
            # 2. 检查单个标的持仓限制
            position_value = await self._calculate_position_value(symbol, quantity)
            max_value = self.risk_checker.DEFAULT_RISK_LIMITS['option']['max_position_value']
            
            if position_value > max_value:
                return False, f"超过单个标的持仓限制 (${position_value:.2f} > ${max_value:.2f})"
            
            # 3. 检查总持仓比例
            total_value = await self.risk_checker._get_total_position_value()
            account_value = await self.risk_checker._get_account_value()
            max_ratio = self.risk_checker.DEFAULT_RISK_LIMITS['option']['max_total_ratio']
            
            ratio_str = f"({(total_value + position_value) / account_value:.1%} > {max_ratio:.1%})"
            if (total_value + position_value) / account_value > max_ratio:
                return False, f"超过总持仓比例限制 {ratio_str}"
            
            return True, ""
            
        except Exception as e:
            self.logger.error(f"检查持仓限制时出错: {str(e)}")
            return False, f"检查出错: {str(e)}"

    async def process_order_status(self, order_id: str, status: OrderStatus) -> None:
        """处理订单状态更新"""
        try:
            # 获取订单详情
            order = await self.get_order(order_id)
            if not order:
                self.logger.warning(f"无法获取订单详情: {order_id}")
                return
            
            symbol = order.get('symbol', '')
            side = order.get('side', '')
            quantity = order.get('quantity', 0)
            price = order.get('price', 0)
            
            # 构建状态信息字符串
            status_info = (
                f"订单状态更新 - {symbol}:\n"
                f"  订单ID: {order_id}\n"
                f"  方向: {side}\n"
                f"  数量: {quantity}\n"
                f"  价格: ${price:.2f}\n"
                f"  状态: {status.name}"
            )
            
            # 使用单行日志记录
            self.logger.info(status_info)
            
            # 更新订单历史
            self._update_order_history(order_id, status)
            
        except Exception as e:
            self.logger.error(f"处理订单状态更新时出错: {str(e)}")

    async def _verify_connection(self) -> bool:
        """验证连接状态"""
        try:
            # 验证账户状态
            try:
                trade_ctx = await self._get_trade_ctx()
                if not trade_ctx:
                    self.logger.error("无法获取交易连接")
                    return False
                    
                # 获取账户余额
                try:
                    balance = await trade_ctx.account_balance()
                    if not balance:
                        self.logger.error("账户余额为空")
                        return False
                    
                    # 构建余额信息
                    balance_info = (
                        f"账户余额 ({balance.currency}):\n"
                        f"  总资产: ${float(balance.net_assets):,.2f}\n"
                        f"  初始保证金: ${float(balance.init_margin):,.2f}\n"
                        f"  维持保证金: ${float(balance.maintenance_margin):,.2f}\n"
                        f"  风险等级: {balance.risk_level}"
                    )
                    
                    self.logger.info(balance_info)
                    
                    # 保存账户余额
                    self.account_balances = balance
                    return True
                    
                except OpenApiException as e:
                    self.logger.error(f"获取账户余额失败: {str(e)}")
                    return False
                    
            except Exception as e:
                self.logger.error(f"验证账户状态时出错: {str(e)}")
                return False
            
        except Exception as e:
            self.logger.error(f"验证连接时出错: {str(e)}")
            return False

    async def log_position_status(self, position: Dict[str, Any]) -> None:
        """记录持仓状态"""
        try:
            if not position:
                return
            
            # 构建状态信息
            status_info = (
                f"持仓状态 - {position.get('symbol', 'Unknown')}:\n"
                f"  数量: {position.get('volume', 0)}\n"
                f"  成本价: ${float(position.get('cost_price', 0)):.2f}\n"
                f"  市值: ${float(position.get('market_value', 0)):.2f}\n"
                f"  未实现盈亏: ${float(position.get('unrealized_pl', 0)):.2f}"
            )
            
            # 使用单行日志记录
            self.logger.info(status_info)
            
        except Exception as e:
            self.logger.error(f"记录持仓状态时出错: {str(e)}")