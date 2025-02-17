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
    TradeContext, QuoteContext, Config, SubType, 
    OrderType, OrderSide, TimeInForceType,
    OrderStatus, Period, AdjustType, OpenApiException
)
import asyncio
import time
from trading.risk_checker import RiskChecker
from trading.time_checker import TimeChecker

class DoomsdayPositionManager:
    def __init__(self, config: Dict[str, Any], data_manager):
        """初始化持仓管理器"""
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.data_manager = data_manager
        self.tz = pytz.timezone('America/New_York')
        
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

    async def open_position(self, symbol: str, quantity: int) -> bool:
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
            strategy_signal = await self.option_strategy.get_trading_signal(symbol)
            if not strategy_signal or not strategy_signal.get('should_trade', False):
                self.logger.info(f"策略信号不满足开仓条件: {symbol}")
                return False
            
            # 3. 检查风险限制
            risk_result, risk_msg = await self.risk_checker.check_market_risk(symbol)
            if not risk_result:
                self.logger.warning(f"风险检查未通过: {risk_msg}")
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
                quote = await self.data_manager.get_quote(contract)
                if not quote:
                    self.logger.error(f"无法获取合约报价: {contract}")
                    return False
                
                # 计算订单价格
                price = Decimal(str(quote['ask_price']))  # 买入时使用卖方报价
                
                # 提交订单
                order_result = await trade_ctx.submit_order(
                    symbol=contract,
                    order_type=OrderType.LO,  # 限价单
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
                order_result = await trade_ctx.submit_order(
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
            
            # 使用 stock_positions() 方法获取持仓列表
            try:
                positions_response = trade_ctx.stock_positions()
                if positions_response is None:
                    self.logger.info("当前没有持仓")
                    self.positions = {}
                    return True
                
                # 更新持仓信息
                self.positions = {}
                
                # 直接遍历 positions_response
                for pos in positions_response:
                    if hasattr(pos, 'symbol') and pos.symbol:  # 确保持仓对象有 symbol 属性
                        self.positions[pos.symbol] = {
                            'symbol': pos.symbol,
                            'quantity': float(pos.quantity) if hasattr(pos, 'quantity') else 0.0,
                            'cost_price': float(pos.avg_price) if hasattr(pos, 'avg_price') else 0.0,
                            'current_price': float(pos.current_price) if hasattr(pos, 'current_price') else 0.0,
                            'market_value': float(pos.market_value) if hasattr(pos, 'market_value') else 0.0,
                            'unrealized_pl': float(pos.unrealized_pl) if hasattr(pos, 'unrealized_pl') else 0.0
                        }
                
                if self.positions:
                    self.logger.info(f"当前持仓数量: {len(self.positions)}")
                    for symbol, pos in self.positions.items():
                        self.logger.info(f"持仓详情 - {symbol}: 数量={pos['quantity']}, 成本价={pos['cost_price']:.2f}")
                else:
                    self.logger.info("当前没有持仓")
                
                return True
                
            except AttributeError as e:
                self.logger.error(f"持仓数据格式错误: {str(e)}")
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
            
            # 构建状态信息
            status_info = (
                f"持仓状态 - {position.get('symbol', 'Unknown')}:\n"
                f"  数量: {position.get('quantity', 0)}\n"
                f"  成本价: ${float(position.get('cost_price', 0)):.2f}\n"
                f"  市值: ${float(position.get('market_value', 0)):.2f}\n"
                f"  未实现盈亏: ${float(position.get('unrealized_pl', 0)):.2f}"
            )
            
            # 使用单行日志记录
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
