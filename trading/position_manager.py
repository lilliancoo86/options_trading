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
    def __init__(self, config, test_mode=False):
        self.config = config
        self.test_mode = test_mode    
        self.logger = logging.getLogger(__name__)
        # 添加日志过滤器
        self.logger.addFilter(MarketInfoFilter())
        
        # 初始化为None，将在__aenter__中创建
        self.trade_ctx = None
        self.quote_ctx = None

        self.positions = {}
        
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

        self.tz = pytz.timezone('America/New_York')  # 添加时区
        
        # 添加收盘平仓时间设置
        self.market_close = {
            'force_close_time': '15:45',  # 收盘前15分钟强制平仓
            'warning_time': '15:40'       # 收盘前20分钟发出警告
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
            self.logger.exception("详细错误信息:")
            raise

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器的退出方法"""
        try:
            # 关闭连接
            if hasattr(self.trade_ctx, 'close'):
                await self.trade_ctx.close()
            if hasattr(self.quote_ctx, 'close'):
                await self.quote_ctx.close()
            
            self.logger.info("交易和行情连接已关闭")
            
        except Exception as e:
            self.logger.error(f"清理资源时出错: {str(e)}")
            self.logger.exception("详细错误信息:")
            raise

    async def get_real_positions(self):
        """获取实际持仓信息"""
        try:
            if not self.trade_ctx:
                raise RuntimeError("交易上下文未初始化")
                
            # 获取持仓信息
            try:
                # 获取股票持仓
                stock_positions = self.trade_ctx.stock_positions()
                self.logger.debug(f"股票持仓响应: {stock_positions}")
                
                all_positions = []
                
                # 处理持仓
                if stock_positions:
                    for pos in stock_positions:
                        try:
                            # 获取实时行情
                            quote = await self.quote_ctx.get_quote([pos.symbol])
                            current_price = float(quote[0].last_done if quote else pos.current_price)
                            
                            # 计算盈亏
                            cost_price = float(pos.cost_price)
                            volume = int(pos.quantity)
                            market_value = current_price * abs(volume)
                            cost_value = cost_price * abs(volume)
                            pnl = market_value - cost_value if volume > 0 else cost_value - market_value
                            
                            # 判断是期权还是股票
                            is_option = bool(re.search(r'\d{6}[CP]\d+', pos.symbol))
                            
                            position_info = {
                                "symbol": pos.symbol,
                                "volume": volume,
                                "cost_price": cost_price,
                                "current_price": current_price,
                                "market_value": market_value,
                                "pnl": pnl,
                                "pnl_ratio": (pnl / cost_value * 100) if cost_value != 0 else 0,
                                "type": "option" if is_option else "stock"
                            }
                            
                            self.logger.debug(
                                f"{position_info['type']}持仓详情:\n"
                                f"  标的: {position_info['symbol']}\n"
                                f"  数量: {position_info['volume']}\n"
                                f"  成本: ${position_info['cost_price']:.2f}\n"
                                f"  现价: ${position_info['current_price']:.2f}\n"
                                f"  市值: ${position_info['market_value']:.2f}\n"
                                f"  盈亏: ${position_info['pnl']:.2f} ({position_info['pnl_ratio']:.2f}%)"
                            )
                            
                            all_positions.append(position_info)
                        except Exception as e:
                            self.logger.warning(f"处理持仓信息时出错: {str(e)}")
                            continue
                
                # 获取账户余额信息
                try:
                    balance = self.trade_ctx.account_balance()
                    balance_info = balance[0] if balance else None
                except Exception as e:
                    self.logger.warning(f"获取账户余额信息失败: {str(e)}")
                    balance_info = None
                
                return {
                    "active": all_positions,
                    "balance": balance_info
                }
                
            except Exception as e:
                self.logger.warning(f"获取持仓列表时出错: {str(e)}")
                self.logger.exception("详细错误信息:")
                return {"active": [], "balance": None}
                
        except Exception as e:
            self.logger.error(f"获取持仓信息时出错: {str(e)}")
            self.logger.exception("详细错误信息:")
            return {"active": [], "balance": None}

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
                quantity=volume,
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
            
            # 获取当前价格和成本价
            current_price = float(position.get('current_price', 0))
            cost_price = float(position.get('cost_price', 0))
            
            # 计算收益率
            if cost_price == 0:
                return False
            pnl_pct = (current_price - cost_price) / cost_price * 100
            
            # 区分期权和股票
            is_option = self._is_option(position['symbol'])
            limits = self.risk_limits['option'] if is_option else self.risk_limits['stock']
            
            # 检查止损条件
            if limits['stop_loss'] is not None and pnl_pct <= limits['stop_loss']:
                self.logger.warning(f"触发固定止损: 当前亏损 {pnl_pct:.1f}% <= {limits['stop_loss']}%")
                await self._execute_stop_loss(position)
                return True
            
            # 检查止盈条件（仅股票）
            if not is_option and limits['take_profit'] is not None and pnl_pct >= limits['take_profit']:
                self.logger.warning(f"触发固定止盈: 当前收益 {pnl_pct:.1f}% >= {limits['take_profit']}%")
                await self._execute_take_profit(position)
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"检查持仓风险时出错: {str(e)}")
            return False

    def _is_option(self, symbol: str) -> bool:
        """检查是否为期权"""
        return any(x in symbol for x in ['C', 'P'])

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
                quantity=volume,
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
                quantity=volume,
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

    async def print_trading_status(self):
        """打印交易状态"""
        try:
            # 获取当前持仓
            positions = await self.get_real_positions()
            
            # 获取当前时间
            current_time = datetime.now(self.tz)
            
            # 打印基本信息
            self.logger.info("\n=== 交易状态报告 ===")
            self.logger.info(f"当前时间: {current_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            self.logger.info(f"交易模式: {'测试模式' if self.test_mode else '实盘模式'}")
            
            # 打印持仓信息
            if positions["active"]:
                position_data = []
                for pos in positions["active"]:
                    position_data.append({
                        "标的": pos["symbol"],
                        "数量": pos["volume"],
                        "成本价": f"${pos['cost_price']:.2f}",
                        "现价": f"${pos['current_price']:.2f}",
                        "盈亏": f"${pos['pnl']:.2f}"
                    })
                
                table = tabulate(
                    position_data,
                    headers="keys",
                    tablefmt="grid",
                    numalign="right"
                )
                self.logger.info("\n当前持仓:")
                self.logger.info(f"\n{table}")
            else:
                self.logger.info("\n当前无持仓")
            
            # 打印风险限制信息
            self.logger.info("\n风险控制参数:")
            risk_data = [
                {
                    "类型": "期权",
                    "止损线": f"{self.risk_limits['option']['stop_loss']}%",
                    "止盈线": "不设置" if self.risk_limits['option']['take_profit'] is None else f"{self.risk_limits['option']['take_profit']}%"
                },
                {
                    "类型": "股票",
                    "止损线": f"{self.risk_limits['stock']['stop_loss']}%",
                    "止盈线": f"{self.risk_limits['stock']['take_profit']}%"
                }
            ]
            table = tabulate(
                risk_data,
                headers="keys",
                tablefmt="grid",
                numalign="right"
            )
            self.logger.info(f"\n{table}")
            
            # 打印收盘时间设置
            self.logger.info("\n收盘设置:")
            self.logger.info(f"预警时间: {self.market_close['warning_time']}")
            self.logger.info(f"强制平仓时间: {self.market_close['force_close_time']}")
            
        except Exception as e:
            self.logger.error(f"打印交易状态时出错: {str(e)}")
            self.logger.exception("详细错误信息:")

    async def check_force_close(self, current_time: datetime) -> bool:
        """检查是否需要强制平仓"""
        try:
            # 转换为美东时间字符串
            current_time_str = current_time.strftime('%H:%M')
            force_close_time = self.market_close['force_close_time']
            warning_time = self.market_close['warning_time']
            
            # 检查是否到达预警时间
            if current_time_str >= warning_time and current_time_str < force_close_time:
                self.logger.warning("接近收盘时间，准备强制平仓")
                
            # 检查是否需要强制平仓
            if current_time_str >= force_close_time:
                positions = await self.get_real_positions()
                if positions and positions.get("active"):
                    self.logger.warning(
                        f"触发强制平仓:\n"
                        f"  当前时间: {current_time_str}\n"
                        f"  强制平仓时间: {force_close_time}"
                    )
                    return True
                    
            return False
            
        except Exception as e:
            self.logger.error(f"检查强制平仓时出错: {str(e)}")
            self.logger.exception("详细错误信息:")
            return False

    async def get_all_positions(self) -> Dict[str, Any]:
        """获取所有持仓"""
        try:
            if not self.trade_ctx:
                return {}
            
            positions = await self.get_real_positions()
            result = {}
            
            if positions and positions.get("active"):
                for pos in positions["active"]:
                    result[pos["symbol"]] = {
                        "quantity": pos["volume"],
                        "entry_price": pos["cost_price"],
                        "current_price": pos["current_price"],
                        "pnl": pos["pnl"],
                        "holding_time": datetime.now(self.tz) - datetime.fromtimestamp(0, self.tz)  # 临时占位
                    }
                    
            return result
            
        except Exception as e:
            self.logger.error(f"获取持仓信息时出错: {str(e)}")
            return {}

    async def check_position_risks(self):
        """检查所有持仓的风险状态"""
        try:
            positions = await self.get_real_positions()
            if not positions or not positions.get("active"):
                return
            
            for position in positions["active"]:
                # 检查持仓风险
                await self.check_position_risk(position)
                
                # 检查是否需要收盘平仓
                await self.check_market_close(position)
                
        except Exception as e:
            self.logger.error(f"检查持仓风险时出错: {str(e)}")
            self.logger.exception("详细错误信息:")