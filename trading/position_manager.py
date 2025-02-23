"""
持仓管理模块
负责管理交易持仓和资金管理
"""
import asyncio
import logging
import os
import time
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Any, Optional, Tuple

import pytz
from dotenv import load_dotenv
from longport.openapi import (
    TradeContext, Config, OrderType, OrderSide, TimeInForceType,
    OpenApiException
)

from trading.risk_checker import RiskChecker
from trading.time_checker import TimeChecker


class DoomsdayPositionManager:
    def __init__(self, config: Dict[str, Any], data_manager,option_strategy):
        """初始化持仓管理器"""
        if not isinstance(config, dict):
            raise ValueError("配置必须是字典类型")
        
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.data_manager = data_manager
        # 添加 option_strategy
        self.option_strategy = option_strategy
        self.tz = pytz.timezone('America/New_York')
        
        # 确保配置中包含必要的字段
        try:
            if hasattr(self.data_manager, 'symbols') and self.data_manager.symbols:
                self.symbols = self.data_manager.symbols.copy()  # 创建副本避免引用问题
                self.logger.info(f"使用数据管理器中的交易标的: {self.symbols}")
            elif 'TRADING_CONFIG' in config and 'symbols' in config['TRADING_CONFIG']:
                self.symbols = config['TRADING_CONFIG']['symbols'].copy()
                self.logger.info(f"从 TRADING_CONFIG 中获取交易标的: {self.symbols}")
            elif 'symbols' in config:
                self.symbols = config['symbols'].copy()
                self.logger.info(f"从配置中获取交易标的: {self.symbols}")
            else:
                raise ValueError("无法获取交易标的列表")
            
            # 验证交易标的
            if not isinstance(self.symbols, list):
                raise ValueError("交易标的必须是列表类型")
            if not self.symbols:
                raise ValueError("交易标的列表不能为空")
            for symbol in self.symbols:
                if not isinstance(symbol, str):
                    raise ValueError(f"交易标的必须是字符串类型: {symbol}")
                if not symbol.endswith('.US'):
                    raise ValueError(f"交易标的格式错误，必须以 .US 结尾: {symbol}")
        except Exception as e:
            self.logger.error(f"初始化交易标的时出错: {str(e)}")
            raise
        
        # 加载环境变量
        load_dotenv()
        
        # API配置
        self.longport_config = Config(
            app_key=os.getenv('LONGPORT_APP_KEY'),
            app_secret=os.getenv('LONGPORT_APP_SECRET'),
            access_token=os.getenv('LONGPORT_ACCESS_TOKEN')
        )
        
        # 初始化依赖组件
        self.time_checker = TimeChecker(config)
        self.risk_checker = RiskChecker(config, self, self.time_checker)
        
        # 交易连接管理
        self._trade_ctx_lock = asyncio.Lock()
        self._trade_ctx = None
        self._last_trade_time = 0
        self._trade_timeout = 60
        
        # 持仓管理
        self.positions = {}  # 当前持仓
        self.pending_orders = {}  # 待成交订单
        self.order_history = {}  # 订单历史
        
        # 资金管理
        self.account_info = {
            'cash': 0.0,
            'margin': 0.0,
            'buying_power': 0.0,
            'equity': 0.0
        }
        
        # 订单执行配置
        self.execution_config = config.get('execution', {
            'max_retry': 3,
            'retry_interval': 1.0,
            'price_tolerance': 0.01
        })

    async def async_init(self) -> None:
        """异步初始化"""
        try:
            # 初始化交易连接
            trade_ctx = await self._get_trade_ctx()
            if not trade_ctx:
                raise ConnectionError("初始化交易连接失败")
            
            # 更新账户信息
            await self._update_account_info()
            
            # 更新当前持仓
            await self._update_positions()
            
            self.logger.info("持仓管理器初始化完成")
            
        except Exception as e:
            self.logger.error(f"持仓管理器初始化失败: {str(e)}")
            raise

    async def open_position(self, symbol: str, quantity: int,price:int) -> bool:
        """开仓操作"""
        try:
            # 参数验证
            if not symbol or quantity <= 0:
                self.logger.error(f"开仓参数无效: 标的={symbol}, 数量={quantity}")
                return False
            
            # 1. 检查市场状态
            if not await self.time_checker.can_trade():
                self.logger.warning("当前不在交易时段")
                return False
            
            # 2. 获取策略信号
            strategy_signal = await self.option_strategy.generate_signal(symbol)
            if not strategy_signal or not strategy_signal.get('should_trade', False):
                self.logger.info(f"策略信号不满足开仓条件: {symbol}")
                return False
            
            # 3. 检查风险限制
            # 使用已有的 quote 数据 todo 有待确认完善
            quote = await self.data_manager.get_latest_quote(symbol)
            if not quote:
                self.logger.warning(f"无法获取报价数据: {symbol}")
                return False

            # 构建 market_data 字典
            market_data = {
                'symbol': symbol,
                'last_price': quote['last_price'],
                'volume': quote['volume'],
                'iv': quote.get('implied_volatility', 0)
            }
            risk_result, risk_msg, risk_level = await self.risk_checker.check_market_risk(symbol, market_data)
            if not risk_result:
                self.logger.warning(f"风险检查未通过: {risk_msg} level:{risk_level}")
                return False
            
            # 4. 选择期权合约
            contract_info = await self.option_strategy.select_option_contract(symbol)
            if not contract_info:
                self.logger.warning(f"未找到合适的期权合约: {symbol}")
                return False
            
            contract = contract_info['symbol']
            side = contract_info['side']
            
            # 5. 执行订单
            trade_ctx = await self._get_trade_ctx()
            if not trade_ctx:
                return False
            
            try:
                # 获取合约报价
                quote = await self.data_manager.get_latest_quote(contract)
                if not quote:
                    self.logger.error(f"无法获取合约报价: {contract}")
                    return False
                
                # 计算订单价格
                price = Decimal(str(quote['ask_price']))  # 买入时使用卖方报价

                # 提交平仓订单
                # 移除 await
                order_result = trade_ctx.submit_order(
                    symbol=symbol,
                    order_type=OrderType.LO,
                    side=side,
                    submitted_price=price,
                    submitted_quantity=Decimal(str(quantity)),
                    time_in_force=TimeInForceType.Day,
                    remark=f"Strategy Signal: {strategy_signal.get('signal_type', 'unknown')}"
                )
                # 更新持仓记录
                await self._update_position_record(contract, order_result)
                
                self.logger.info(f"成功提交开仓订单: {contract}, 数量: {quantity}, 价格: {price}")
                return True
                
            except OpenApiException as e:
                self.logger.error(f"提交订单失败: {str(e)}")
                return False
                
        except Exception as e:
            self.logger.error(f"开仓操作出错: {str(e)}")
            return False

    async def close_position(self, symbol: str, quantity: Optional[int] = None) -> bool:
        """平仓操作"""
        try:
            # 获取当前持仓
            position = self.positions.get(symbol)
            if not position:
                self.logger.warning(f"未找到持仓: {symbol}")
                return False
            
            # 确定平仓数量
            if quantity is None:
                quantity = position['quantity']
            elif quantity > position['quantity']:
                self.logger.warning(f"平仓数量超过持仓量: {quantity} > {position['quantity']}")
                return False
            
            # 检查市场状态
            if not await self.time_checker.can_trade():
                self.logger.warning("当前不在交易时段")
                return False
            
            # 获取交易连接
            trade_ctx = await self._get_trade_ctx()
            if not trade_ctx:
                return False
            
            try:
                # 获取报价
                quote = await self.data_manager.get_quote(symbol)
                if not quote:
                    self.logger.error(f"无法获取报价: {symbol}")
                    return False
                
                # 计算平仓价格
                price = Decimal(str(quote['bid_price']))  # 卖出时使用买方报价
                
                # 提交平仓订单
                # 移除 await
                order_result = trade_ctx.submit_order(
                    symbol=symbol,
                    order_type=OrderType.LO,
                    side=OrderSide.Sell if position['side'] == OrderSide.Buy else OrderSide.Buy,
                    submitted_price=price,
                    submitted_quantity=Decimal(str(quantity)),
                    time_in_force=TimeInForceType.Day,
                    remark="Position Close"
                )
                
                # 更新持仓记录
                await self._update_position_record(symbol, order_result, is_close=True)
                
                self.logger.info(f"成功提交平仓订单: {symbol}, 数量: {quantity}, 价格: {price}")
                return True
                
            except OpenApiException as e:
                self.logger.error(f"提交平仓订单失败: {str(e)}")
                return False
                
        except Exception as e:
            self.logger.error(f"平仓操作出错: {str(e)}")
            return False

    async def _get_trade_ctx(self) -> Optional[TradeContext]:
        """获取交易连接（带连接管理）"""
        try:
            async with self._trade_ctx_lock:
                current_time = time.time()
                
                # 检查是否需要重新连接
                if (self._trade_ctx is None or 
                    current_time - self._last_trade_time > self._trade_timeout):
                    
                    # 关闭旧连接
                    if self._trade_ctx:
                        try:
                            await self._trade_ctx.close()
                        except Exception as e:
                            self.logger.warning(f"关闭旧连接时出错: {str(e)}")
                    
                    try:
                        # 创建新连接
                        self._trade_ctx = TradeContext(self.longport_config)
                        self._last_trade_time = current_time
                        
                        # 验证连接
                        await self._validate_trade_ctx()
                        
                    except OpenApiException as e:
                        self.logger.error(f"创建交易连接失败: {str(e)}")
                        self._trade_ctx = None
                        raise
                    
                    except Exception as e:
                        self.logger.error(f"创建交易连接失败: {str(e)}")
                        self._trade_ctx = None
                        raise
                
                return self._trade_ctx
                
        except Exception as e:
            self.logger.error(f"获取交易连接时出错: {str(e)}")
            return None

    async def ensure_trade_ctx(self) -> Optional[TradeContext]:
        """确保交易连接可用"""
        try:
            if not self._trade_ctx:
                self._trade_ctx = TradeContext(self.longport_config)
                self.logger.info("交易连接已建立")
                
            # 验证连接是否可用
            try:
                # 尝试获取账户余额来验证连接
                balances = self._trade_ctx.account_balance()
                if not balances:
                    self.logger.error("交易连接验证失败：未能获取账户余额")
                    self._trade_ctx = None
                    return None
                self.logger.info("交易连接验证成功")
                self.logger.debug(f"账户余额详情: {balances}")
            except OpenApiException as e:
                self.logger.error(f"交易连接验证失败，API错误: {str(e)}")
                self._trade_ctx = None
                return None
                
            return self._trade_ctx
            
        except Exception as e:
            self.logger.error(f"确保交易连接时出错: {str(e)}")
            self._trade_ctx = None
            return None

    async def _update_account_info(self) -> bool:
        """更新账户信息"""
        try:
            trade_ctx = await self.ensure_trade_ctx()
            if not trade_ctx:
                return False
            
            # 使用 account_balance() 方法获取账户余额
            balances = trade_ctx.account_balance()
            if not balances:
                self.logger.error("获取账户余额失败")
                return False
            
            # 更新账户信息，使用正确的属性名
            balance = balances[0]  # 获取第一个账户的余额
            self.account_info = {
                'cash': float(balance.total_cash),
                'margin': float(balance.maintenance_margin),  # 使用 maintenance_margin 而不是 margin
                'buying_power': float(balance.buy_power),    # 使用 buy_power 而不是 max_power
                'equity': float(balance.net_assets)
            }
            
            self.logger.info(f"账户信息已更新: {self.account_info}")
            return True
            
        except Exception as e:
            self.logger.error(f"更新账户信息失败: {str(e)}")
            return False

    async def _update_positions(self) -> bool:
        """更新持仓信息"""
        try:
            trade_ctx = await self.ensure_trade_ctx()
            if not trade_ctx:
                return False
            
            try:
                # 获取所有持仓类型
                stock_positions_resp = trade_ctx.stock_positions()
                fund_positions_resp = trade_ctx.fund_positions()
                
                # 更新持仓信息
                self.positions = {}
                
                # 处理股票和期权持仓
                if hasattr(stock_positions_resp, 'channels'):
                    for channel in stock_positions_resp.channels:
                        if hasattr(channel, 'positions'):
                            for pos in channel.positions:
                                symbol_parts = pos.symbol.split('.')
                                symbol_name = pos.symbol_name if hasattr(pos, 'symbol_name') else symbol_parts[0]
                                
                                self.positions[pos.symbol] = {
                                    'symbol': pos.symbol,
                                    'name': symbol_name,
                                    'type': 'stock' if '250417' not in pos.symbol else 'option',
                                    'account': channel.account_channel,
                                    'quantity': float(pos.quantity),
                                    'cost_price': float(pos.cost_price),
                                    'current_price': float(pos.current_price) if hasattr(pos, 'current_price') else 0.0,
                                    'market_value': float(pos.market_value) if hasattr(pos, 'market_value') else 0.0,
                                    'currency': pos.currency if hasattr(pos, 'currency') else 'USD',
                                    'unrealized_pl': float(pos.unrealized_pl) if hasattr(pos, 'unrealized_pl') else 0.0
                                }
                
                # 以表格形式展示持仓
                if not self.positions:
                    self.logger.info("当前没有持仓")
                else:
                    # 计算每列的最大宽度
                    widths = {
                        'symbol': max(len(str(pos['symbol'])) for pos in self.positions.values()),
                        'name': max(len(str(pos['name'])) for pos in self.positions.values()),
                        'type': max(len(str(pos['type'])) for pos in self.positions.values()),
                        'account': max(len(str(pos['account'])) for pos in self.positions.values()),
                        'quantity': max(len(f"{pos['quantity']:,.0f}") for pos in self.positions.values()),
                        'cost_price': max(len(f"{pos['cost_price']:,.2f}") for pos in self.positions.values()),
                        'market_value': max(len(f"{pos['market_value']:,.2f}") for pos in self.positions.values())
                    }
                    
                    # 确保列标题的最小宽度
                    min_widths = {
                        'symbol': 12,
                        'name': 15,
                        'type': 8,
                        'account': 15,
                        'quantity': 10,
                        'cost_price': 12,
                        'market_value': 12
                    }
                    
                    # 使用最大宽度
                    for key in widths:
                        widths[key] = max(widths[key], min_widths[key])
                    
                    # 构建表头和分隔线
                    header = (
                        f"{'代码':<{widths['symbol']}} | "
                        f"{'名称':<{widths['name']}} | "
                        f"{'类型':<{widths['type']}} | "
                        f"{'账户':<{widths['account']}} | "
                        f"{'数量':>{widths['quantity']}} | "
                        f"{'成本价':>{widths['cost_price']}} | "
                        f"{'市值':>{widths['market_value']}} | "
                        f"{'币种':<6}"
                    )
                    
                    separator = '-' * len(header)
                    
                    # 输出表格
                    self.logger.info("\n当前持仓明细:")
                    self.logger.info(separator)
                    self.logger.info(header)
                    self.logger.info(separator)
                    
                    # 输出持仓数据
                    for pos in self.positions.values():
                        row = (
                            f"{pos['symbol']:<{widths['symbol']}} | "
                            f"{pos['name']:<{widths['name']}} | "
                            f"{pos['type']:<{widths['type']}} | "
                            f"{pos['account']:<{widths['account']}} | "
                            f"{pos['quantity']:>{widths['quantity']},.0f} | "
                            f"{pos['cost_price']:>{widths['cost_price']},.2f} | "
                            f"{pos['market_value']:>{widths['market_value']},.2f} | "
                            f"{pos['currency']:<6}"
                        )
                        self.logger.info(row)
                    
                    self.logger.info(separator)
                    
                    # 输出汇总信息
                    total_market_value = sum(pos['market_value'] for pos in self.positions.values())
                    total_unrealized_pl = sum(pos['unrealized_pl'] for pos in self.positions.values())
                    summary = (
                        f"总持仓: {len(self.positions)} 个标的  "
                        f"总市值: {total_market_value:,.2f} USD  "
                        f"总未实现盈亏: {total_unrealized_pl:,.2f} USD"
                    )
                    self.logger.info(summary)
                
                return True
                
            except AttributeError as e:
                self.logger.error(f"持仓数据结构错误: {str(e)}")
                return False
            
        except Exception as e:
            self.logger.error(f"更新持仓信息失败: {str(e)}")
            return False

    async def _check_position_limits(self, symbol: str, quantity: int) -> Tuple[bool, str]:
        """检查持仓限制"""
        try:
            # 获取当前持仓
            current_position = self.positions.get(symbol, {})
            current_quantity = current_position.get('quantity', 0)
            
            # 检查最大持仓数量
            if len(self.positions) >= self.risk_checker.risk_limits['market']['max_positions']:
                return False, "达到最大持仓数量限制"
            
            # 检查单个持仓金额限制
            quote = await self.data_manager.get_quote(symbol)
            if quote:
                position_value = float(quote.get('last_price', 0)) * (current_quantity + quantity)
                if position_value > self.risk_checker.risk_limits['market']['max_position_value']:
                    return False, "超过单个持仓金额限制"
            
            # 检查保证金率
            if self.account_info['margin'] / self.account_info['equity'] > self.risk_checker.risk_limits['market']['max_margin_ratio']:
                return False, "超过最大保证金率限制"
            
            return True, ""
            
        except Exception as e:
            self.logger.error(f"检查持仓限制时出错: {str(e)}")
            return False, f"检查出错: {str(e)}"

    async def _validate_trade_ctx(self) -> bool:
        """验证交易连接"""
        try:
            if not self._trade_ctx:
                return False
            
            try:
                # 尝试获取账户余额来验证连接
                balances = self._trade_ctx.account_balance()
                if not balances:
                    self.logger.error("验证交易连接失败：未能获取账户余额")
                    return False
                    
                self.logger.info("交易连接验证成功")
                return True
                    
            except OpenApiException as e:
                self.logger.error(f"验证交易连接失败，API错误: {str(e)}")
                return False
                    
        except Exception as e:
            self.logger.error(f"验证连接时出错: {str(e)}")
            return False

    async def log_position_status(self, position: Dict[str, Any]) -> None:
        """记录持仓状态"""
        try:
            if not position:
                return
            
            # 计算关键指标
            symbol = position.get('symbol', '')
            quantity = position.get('quantity', 0)
            cost_price = position.get('cost_price', 0)
            market_value = position.get('market_value', 0)
            unrealized_pl = position.get('unrealized_pl', 0)
            
            # 计算收益率
            if cost_price and cost_price > 0:
                pl_percentage = (unrealized_pl / (cost_price * quantity)) * 100
            else:
                pl_percentage = 0
            
            # 使用更醒目的日志格式
            status_info = (
                f"\n📊 持仓状态 - {symbol}:\n" +
                f"    数量: {quantity:,.0f}\n" +
                f"    成本价: ${cost_price:.2f}\n" +
                f"    市值: ${market_value:.2f}\n" +
                f"    未实现盈亏: ${unrealized_pl:.2f} ({pl_percentage:+.2f}%)\n" +
                f"    持仓时间: {self._get_position_duration(position)}"
            )
            
            # 添加风险警告
            if pl_percentage <= -10:
                status_info += f"\n    ⚠️ 警告: 亏损已超过 10%"
            elif pl_percentage >= 20:
                status_info += f"\n    🎉 提示: 盈利已超过 20%"
            
            self.logger.info(status_info)
            
        except Exception as e:
            self.logger.error(f"记录持仓状态时出错: {str(e)}")

    async def _update_position_record(self, symbol: str, order_result: Any, is_close: bool = False) -> None:
        """更新持仓记录"""
        try:
            if is_close:
                if symbol in self.positions:
                    position = self.positions[symbol]
                    position['quantity'] -= order_result.submitted_quantity
                    if position['quantity'] <= 0:
                        del self.positions[symbol]
            else:
                if symbol not in self.positions:
                    self.positions[symbol] = {
                        'symbol': symbol,
                        'quantity': order_result.submitted_quantity,
                        'cost_price': order_result.submitted_price,
                        'side': order_result.side,
                        'open_time': datetime.now(self.tz)
                    }
                else:
                    position = self.positions[symbol]
                    position['quantity'] += order_result.submitted_quantity
            
            # 记录持仓状态
            await self.log_position_status(self.positions.get(symbol))
            
        except Exception as e:
            self.logger.error(f"更新持仓记录时出错: {str(e)}")

    async def get_positions(self) -> List[Dict[str, Any]]:
        """获取当前持仓"""
        try:
            # 先更新持仓信息
            if not await self._update_positions():
                return []
            
            # 返回持仓列表
            return list(self.positions.values())
            
        except Exception as e:
            self.logger.error(f"获取持仓信息失败: {str(e)}")
            return []
