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
from longport.openapi import TradeContext, QuoteContext, Config, SubType, OrderType, OrderSide, TimeInForceType, OrderStatus
from tabulate import tabulate
import asyncio
from trading.risk_checker import RiskChecker  # 添加导入
import re
import traceback
from trading.time_checker import TimeChecker  # 添加导入

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
        self.config = config
        self.test_mode = test_mode
        self.logger = logging.getLogger(__name__)
        self.tz = pytz.timezone('America/New_York')
        
        # 使用传入的上下文
        self.quote_ctx = config['api']['quote_context']
        self.trade_ctx = config['api']['trade_context']
        
        # 持仓限制
        self.position_limits = {
            'max_positions': 5,  # 最大持仓数量
            'max_position_value': 10000  # 单个持仓最大金额
        }

        # 初始化风险检查器
        self.risk_checker = RiskChecker(config)

        # 持仓历史记录
        self.position_history = {
            'trades': [],          # 所有交易记录
            'daily_summary': {},   # 每日交易汇总
            'positions': {}        # 持仓记录
        }
        
        # 订单执行配置
        self.order_config = {
            'max_retry': 3,        # 最大重试次数
            'retry_interval': 1,   # 重试间隔(秒)
            'timeout': 5,          # 订单超时时间(秒)
            'min_fill_ratio': 0.9  # 最小成交比例
        }

    async def __aenter__(self):
        """异步上下文管理器的进入方法"""
        try:
            self.logger.info("持仓管理器初始化完成")
            return self
        except Exception as e:
            self.logger.error(f"初始化失败: {str(e)}")
            raise

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器的退出方法"""
        try:
            self.logger.info("持仓管理器清理完成")
        except Exception as e:
            self.logger.error(f"清理资源时出错: {str(e)}")

    async def get_real_positions(self) -> Dict[str, List[Dict[str, Any]]]:
        """获取实际持仓数据"""
        try:
            if not self.trade_ctx:
                raise RuntimeError("交易上下文未初始化")
            
            positions = {"active": []}
            
            # 获取持仓列表
            stock_positions = await self.trade_ctx.positions()
            if not stock_positions:
                return positions
                
            # 获取实时行情更新持仓信息
            for pos in stock_positions:
                quote = await self.quote_ctx.quote([pos.symbol])
                if not quote:
                    continue
                    
                current_price = float(quote[0].last_done)
                cost_price = float(pos.cost_price)
                quantity = int(pos.quantity)
                
                # 计算持仓信息
                market_value = current_price * quantity
                day_pnl = (current_price - cost_price) * quantity
                day_pnl_pct = ((current_price - cost_price) / cost_price) * 100
                
                position_data = {
                    "symbol": pos.symbol,
                    "volume": quantity,
                    "cost_price": cost_price,
                    "current_price": current_price,
                    "market_value": market_value,
                    "day_pnl": day_pnl,
                    "day_pnl_pct": day_pnl_pct
                }
                
                positions["active"].append(position_data)
            
            return positions
            
        except Exception as e:
            self.logger.error(f"获取持仓数据时出错: {str(e)}")
            return {"active": []}

    async def close_position(self, symbol: str, volume: int, reason: str = "", ratio: float = 1.0) -> bool:
        """平仓指定持仓"""
        try:
            self.logger.info(f"准备平仓: {symbol}, 数量: {volume}, 比例: {ratio:.2%}, 原因: {reason}")
            
            if ratio <= 0 or ratio > 1:
                self.logger.error(f"无效的平仓比例: {ratio}")
                return False
            
            # 计算实际平仓数量
            close_volume = int(volume * ratio)
            if close_volume < 1:
                close_volume = 1
            
            if not self.trade_ctx:
                self.logger.error("交易上下文未初始化")
                return False
            
            # 提交市价单平仓
            order = await self.trade_ctx.submit_order(
                symbol=symbol,
                order_type=OrderType.Market,
                side=OrderSide.Sell,
                submitted_quantity=close_volume,
                time_in_force=TimeInForceType.Day,
                remark=f"Close position: {reason}"
            )
            
            if not order or not hasattr(order, 'order_id'):
                self.logger.error("平仓订单提交失败")
                return False
            
            # 等待订单成交
            for _ in range(5):
                await asyncio.sleep(1)
                order_status = await self.trade_ctx.get_order_detail(order.order_id)
                
                if order_status.status == OrderStatus.Filled:
                    self.logger.info(
                        f"平仓成功:\n"
                        f"  标的: {symbol}\n"
                        f"  数量: {order_status.executed_quantity}张\n"
                        f"  成交价: ${order_status.executed_price:.2f}"
                    )
                    
                    # 记录平仓历史
                    if symbol not in self.position_history['positions']:
                        self.position_history['positions'][symbol] = []
                    
                    self.position_history['positions'][symbol].append({
                        'action': 'close',
                        'time': datetime.now(self.tz),
                        'price': float(order_status.executed_price),
                        'volume': close_volume,
                        'ratio': ratio,
                        'reason': reason
                    })
                    
                    return True
                    
                elif order_status.status in [OrderStatus.Failed, OrderStatus.Rejected, OrderStatus.Cancelled]:
                    self.logger.error(f"平仓订单失败: {order_status.status}")
                    return False
            
            # 超时处理
            await self.trade_ctx.cancel_order(order.order_id)
            return False
            
        except Exception as e:
            self.logger.error(f"执行平仓操作失败: {str(e)}")
            return False

    async def close_all_positions(self, reason: str = "") -> bool:
        """平掉所有持仓"""
        try:
            positions = await self.get_real_positions()
            if not positions or not positions.get("active"):
                return True
                
            success = True
            for position in positions["active"]:
                if not await self.close_position(
                    position["symbol"],
                    position["volume"],
                    reason
                ):
                    success = False
            
            return success
            
        except Exception as e:
            self.logger.error(f"平掉所有持仓时出错: {str(e)}")
            return False

    async def can_open_position(self, symbol: str) -> bool:
        """检查是否可以开新仓位"""
        try:
            # 获取当前价格
            quote = await self.quote_ctx.quote([symbol])
            if not quote:
                self.logger.error(f"获取{symbol}价格失败")
                return False
            
            current_price = float(quote[0].last_done)
            
            # 检查风险限制
            risk_high, reason = self.risk_checker.check_new_position_risk(
                symbol=symbol,
                price=current_price,
                volume=1  # 默认交易1张
            )
            
            if risk_high:
                self.logger.warning(f"风险检查未通过: {reason}")
                return False
            
            return True
            
        except Exception as e:
            self.logger.error(f"检查开仓限制时出错: {str(e)}")
            return False

    async def print_trading_status(self):
        """打印交易状态"""
        try:
            positions = await self.get_real_positions()
            
            if not positions or not positions.get("active"):
                self.logger.info("当前无持仓")
                return
            
            position_data = []
            for pos in positions["active"]:
                position_data.append({
                    "标的": pos["symbol"],
                    "数量": pos["volume"],
                    "成本": f"${pos['cost_price']:.2f}",
                    "现价": f"${pos['current_price']:.2f}",
                    "市值": f"${pos['market_value']:.2f}",
                    "盈亏": f"{pos['day_pnl_pct']:+.1f}%"
                })
            
            self.logger.info("\n" + tabulate(
                position_data,
                headers="keys",
                tablefmt="grid",
                numalign="right"
            ))
            
        except Exception as e:
            self.logger.error(f"打印交易状态时出错: {str(e)}")

    async def open_position(self, symbol: str, volume: int, reason: str = "") -> bool:
        """开仓"""
        try:
            self.logger.info(f"准备开仓: {symbol}, 数量: {volume}, 原因: {reason}")
            
            if not self.trade_ctx:
                self.logger.error("交易上下文未初始化")
                return False
            
            # 检查是否可以开仓
            if not await self.can_open_position(symbol):
                return False
            
            # 转换为 Decimal
            volume = Decimal(str(volume))
            
            # 提交市价单开仓
            order = await self.trade_ctx.submit_order(
                symbol=symbol,
                order_type=OrderType.Market,
                side=OrderSide.Buy,
                submitted_quantity=volume,  # 使用 Decimal
                time_in_force=TimeInForceType.Day,
                remark=f"Open position: {reason}"
            )
            
            if not order or not hasattr(order, 'order_id'):
                self.logger.error("开仓订单提交失败")
                return False
            
            # 等待订单成交
            for _ in range(5):
                await asyncio.sleep(1)
                order_status = await self.trade_ctx.get_order_detail(order.order_id)
                
                if order_status.status == OrderStatus.Filled:
                    self.logger.info(
                        f"开仓成功:\n"
                        f"  标的: {symbol}\n"
                        f"  数量: {order_status.executed_quantity}张\n"
                        f"  成交价: ${order_status.executed_price:.2f}\n"
                        f"  原因: {reason}"
                    )
                    
                    # 记录开仓历史
                    if symbol not in self.position_history['positions']:
                        self.position_history['positions'][symbol] = []
                    
                    self.position_history['positions'][symbol].append({
                        'action': 'open',
                        'time': datetime.now(self.tz),
                        'price': float(order_status.executed_price),
                        'volume': volume,
                        'reason': reason
                    })
                    
                    return True
                    
                elif order_status.status in [OrderStatus.Failed, OrderStatus.Rejected, OrderStatus.Cancelled]:
                    self.logger.error(f"开仓订单失败: {order_status.status}")
                    return False
            
            # 超时处理
            await self.trade_ctx.cancel_order(order.order_id)
            return False
            
        except Exception as e:
            if "301600" in str(e):  # 无效请求
                self.logger.error(f"请求参数有误: {str(e)}")
            elif "301606" in str(e):  # 限流
                self.logger.error(f"请求频率过高: {str(e)}")
            elif "301602" in str(e):  # 服务端错误
                self.logger.error(f"服务端内部错误: {str(e)}")
            else:
                self.logger.error(f"未知错误: {str(e)}")
            return False

    async def init_trade_context(self):
        """初始化交易上下文"""
        try:
            if not self.trade_ctx:
                self.trade_ctx = self.config['api']['trade_context']
                await self.trade_ctx.set_on_order_changed(self._on_order_changed)
                self.logger.info("交易上下文初始化成功")
        except Exception as e:
            self.logger.error(f"初始化交易上下文失败: {str(e)}")

    def get_position_history(self, symbol: str = None, 
                           start_date: str = None, 
                           end_date: str = None) -> Dict[str, Any]:
        """获取持仓历史"""
        try:
            if symbol:
                return {
                    'trades': [t for t in self.position_history['trades'] 
                             if t['symbol'] == symbol],
                    'positions': self.position_history['positions'].get(symbol, [])
                }
            
            if start_date:
                start = datetime.strptime(start_date, '%Y-%m-%d')
                end = datetime.strptime(end_date, '%Y-%m-%d') if end_date else datetime.now(self.tz)
                
                return {
                    'trades': [t for t in self.position_history['trades'] 
                             if start <= t['time'] <= end],
                    'daily_summary': {
                        date: summary 
                        for date, summary in self.position_history['daily_summary'].items()
                        if start_date <= date <= (end_date or trade_date)
                    }
                }
            
            return self.position_history
            
        except Exception as e:
            self.logger.error(f"获取持仓历史时出错: {str(e)}")
            return {}

    async def print_trading_history(self, days: int = 7):
        """打印交易历史"""
        try:
            end_date = datetime.now(self.tz)
            start_date = end_date - timedelta(days=days)
            
            trades = [t for t in self.position_history['trades'] 
                     if start_date <= t['time'] <= end_date]
            
            if not trades:
                self.logger.info(f"近 {days} 天无交易记录")
                return
            
            # 格式化交易记录
            trade_data = []
            for trade in trades:
                trade_data.append({
                    "时间": trade['time'].strftime('%Y-%m-%d %H:%M'),
                    "标的": trade['symbol'],
                    "方向": "买入" if trade['side'] == OrderSide.Buy else "卖出",
                    "数量": trade['volume'],
                    "价格": f"${trade['price']:.2f}",
                    "金额": f"${trade['value']:.2f}",
                    "原因": trade['reason']
                })
            
            self.logger.info("\n" + tabulate(
                trade_data,
                headers="keys",
                tablefmt="grid",
                numalign="right"
            ))
            
        except Exception as e:
            self.logger.error(f"打印交易历史时出错: {str(e)}")

    async def execute_order(self, 
                          symbol: str, 
                          side: str,  # 'buy' or 'sell'
                          volume: int,
                          order_type: str = 'market',  # 'market' or 'limit'
                          price: float = None,
                          reason: str = "") -> bool:
        """统一的订单执行接口"""
        try:
            self.logger.info(
                f"准备执行订单:\n"
                f"  标的: {symbol}\n"
                f"  方向: {side}\n"
                f"  数量: {volume}\n"
                f"  类型: {order_type}\n"
                f"  价格: {price if price else '市价'}\n"
                f"  原因: {reason}"
            )
            
            # 转换为 LongPort 的订单类型
            order_side = OrderSide.Buy if side == 'buy' else OrderSide.Sell
            order_type = OrderType.Market if order_type == 'market' else OrderType.Limit
            
            # 重试机制
            for attempt in range(self.order_config['max_retry']):
                try:
                    # 提交订单
                    order = await self.trade_ctx.submit_order(
                        symbol=symbol,
                        order_type=order_type,
                        side=order_side,
                        submitted_quantity=volume,
                        price=price if order_type == OrderType.Limit else None,
                        time_in_force=TimeInForceType.Day,
                        remark=f"{reason} - Attempt {attempt + 1}"
                    )
                    
                    if not order or not hasattr(order, 'order_id'):
                        raise Exception("订单提交失败")
                    
                    # 等待订单成交
                    filled = await self._wait_order_fill(order.order_id)
                    if filled:
                        # 记录交易历史
                        await self._record_trade(order, reason)
                        return True
                        
                    # 如果未完全成交，尝试撤单
                    await self.trade_ctx.cancel_order(order.order_id)
                    
                except Exception as e:
                    self.logger.error(f"订单执行失败 (尝试 {attempt + 1}): {str(e)}")
                    if attempt < self.order_config['max_retry'] - 1:
                        await asyncio.sleep(self.order_config['retry_interval'])
                    
            return False
            
        except Exception as e:
            self.logger.error(f"订单执行出错: {str(e)}")
            return False

    async def _wait_order_fill(self, order_id: str) -> bool:
        """等待订单成交"""
        try:
            start_time = datetime.now()
            while (datetime.now() - start_time).seconds < self.order_config['timeout']:
                order_status = await self.trade_ctx.get_order_detail(order_id)
                
                if order_status.status == OrderStatus.Filled:
                    return True
                    
                elif order_status.status in [OrderStatus.Failed, OrderStatus.Rejected, OrderStatus.Cancelled]:
                    return False
                    
                await asyncio.sleep(0.5)
                
            return False
            
        except Exception as e:
            self.logger.error(f"等待订单成交时出错: {str(e)}")
            return False

    async def _record_trade(self, order: Any, reason: str):
        """记录交易历史"""
        try:
            # 获取订单详情
            order_detail = await self.trade_ctx.get_order_detail(order.order_id)
            trade_date = datetime.now(self.tz).strftime('%Y-%m-%d')
            
            # 记录交易
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
            
            # 添加到交易历史
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
            
            # 记录日志
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