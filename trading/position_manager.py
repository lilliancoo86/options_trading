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
        
        # 初始化为None，将在__aenter__中创建
        self.trade_ctx = None
        self.quote_ctx = None
        
        # 持仓限制
        self.position_limits = {
            'max_positions': 5,  # 最大持仓数量
            'max_position_value': 10000  # 单个持仓最大金额
        }

    async def __aenter__(self):
        """异步上下文管理器的进入方法"""
        try:
            # 创建配置
            longport_config = Config.from_env()
            
            # 创建交易和行情上下文
            self.trade_ctx = TradeContext(longport_config)
            self.quote_ctx = QuoteContext(longport_config)
            
            self.logger.info("交易和行情连接已建立")
            return self
            
        except Exception as e:
            self.logger.error(f"初始化失败: {str(e)}")
            raise

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器的退出方法"""
        try:
            if hasattr(self.trade_ctx, 'close'):
                await self.trade_ctx.close()
            if hasattr(self.quote_ctx, 'close'):
                await self.quote_ctx.close()
            
            self.logger.info("交易和行情连接已关闭")
            
        except Exception as e:
            self.logger.error(f"清理资源时出错: {str(e)}")
            raise

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

    async def close_position(self, symbol: str, volume: int, reason: str = "") -> bool:
        """平仓指定持仓"""
        try:
            self.logger.info(f"准备平仓: {symbol}, 数量: {volume}, 原因: {reason}")
            
            if not self.trade_ctx:
                self.logger.error("交易上下文未初始化")
                return False
            
            # 提交市价单平仓
            order = await self.trade_ctx.submit_order(
                symbol=symbol,
                order_type=OrderType.Market,
                side=OrderSide.Sell,
                submitted_quantity=volume,
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
                
                if order_status.status == "Filled":
                    self.logger.info(
                        f"平仓成功:\n"
                        f"  标的: {symbol}\n"
                        f"  数量: {order_status.executed_quantity}张\n"
                        f"  成交价: ${order_status.executed_price:.2f}"
                    )
                    return True
                    
                elif order_status.status in ["Failed", "Rejected", "Cancelled"]:
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
            
            # 提交市价单开仓
            order = await self.trade_ctx.submit_order(
                symbol=symbol,
                order_type=OrderType.Market,
                side=OrderSide.Buy,
                submitted_quantity=volume,
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
                
                if order_status.status == "Filled":
                    self.logger.info(
                        f"开仓成功:\n"
                        f"  标的: {symbol}\n"
                        f"  数量: {order_status.executed_quantity}张\n"
                        f"  成交价: ${order_status.executed_price:.2f}\n"
                        f"  原因: {reason}"
                    )
                    return True
                    
                elif order_status.status in ["Failed", "Rejected", "Cancelled"]:
                    self.logger.error(f"开仓订单失败: {order_status.status}")
                    return False
            
            # 超时处理
            await self.trade_ctx.cancel_order(order.order_id)
            return False
            
        except Exception as e:
            self.logger.error(f"执行开仓操作失败: {str(e)}")
            return False