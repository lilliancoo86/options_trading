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
        """获取实际持仓数据"""
        try:
            if not self.trade_ctx:
                raise RuntimeError("交易上下文未初始化")
                
            # 获取持仓信息
            try:
                # 使用 stock_positions 方法获取持仓（同步方法）
                self.logger.debug("正在获取持仓数据...")
                stock_positions = self.trade_ctx.stock_positions()
                self.logger.debug(f"原始持仓数据: {stock_positions}")
                
                positions_data = {"active": []}
                
                # 获取持仓列表
                if hasattr(stock_positions, 'channels'):
                    for channel in stock_positions.channels:
                        if hasattr(channel, 'positions'):
                            for pos in channel.positions:
                                self.logger.debug(f"处理持仓: {pos}")
                                
                                # 转换数量为整数
                                quantity = int(pos.quantity)
                                cost_price = float(pos.cost_price)
                                
                                # 转换持仓数据格式
                                position_data = {
                                    "symbol": pos.symbol,
                                    "volume": quantity,
                                    "cost_price": cost_price,
                                    "current_price": cost_price,  # 暂时使用成本价
                                    "market_value": cost_price * quantity,
                                    "day_pnl": 0.0,  # 需要通过行情更新
                                    "day_pnl_pct": 0.0,  # 需要通过行情更新
                                    "total_pnl": 0.0,  # 需要通过行情更新
                                    "total_pnl_pct": 0.0,  # 需要通过行情更新
                                    "type": "option" if self._is_option(pos.symbol) else "stock"
                                }
                                
                                # 获取最新行情更新价格和盈亏
                                try:
                                    quotes = self.quote_ctx.quote([pos.symbol])
                                    if quotes and len(quotes) > 0:
                                        current_price = float(quotes[0].last_done)
                                        market_value = current_price * float(quantity)
                                        unrealized_pnl = (current_price - cost_price) * float(quantity)
                                        unrealized_pnl_ratio = ((current_price - cost_price) / cost_price) * 100 if cost_price != 0 else 0
                                        
                                        position_data.update({
                                            "current_price": current_price,
                                            "market_value": market_value,
                                            "day_pnl": unrealized_pnl,
                                            "day_pnl_pct": unrealized_pnl_ratio,
                                            "total_pnl": unrealized_pnl,
                                            "total_pnl_pct": unrealized_pnl_ratio
                                        })
                                        self.logger.debug(f"获取到行情数据: {quotes[0]}")
                                except Exception as e:
                                    self.logger.warning(f"获取行情数据失败: {str(e)}")
                                
                                # 添加到活跃持仓列表
                                positions_data["active"].append(position_data)
                                
                                # 记录详细日志
                                self.logger.debug(
                                    f"持仓数据 - {pos.symbol}:\n"
                                    f"  数量: {quantity}\n"
                                    f"  成本价: ${cost_price:.4f}\n"
                                    f"  现价: ${position_data['current_price']:.4f}\n"
                                    f"  市值: ${position_data['market_value']:.2f}\n"
                                    f"  未实现盈亏: ${position_data['total_pnl']:+.2f}\n"
                                    f"  盈亏比例: {position_data['total_pnl_pct']:+.2f}%"
                                )
                
                self.logger.info(f"获取到 {len(positions_data['active'])} 个持仓")
                return positions_data
                
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
            # 检查是否为期权
            if not self._is_option(position['symbol']):
                return False
            
            # 检查是否为当日到期期权
            expiry_date = self._extract_expiry_date(position['symbol'])
            if not expiry_date:
                return False
            
            current_date = datetime.now(self.tz).date()
            if expiry_date.date() != current_date:
                return False
            
            current_time = datetime.now(self.tz).strftime('%H:%M')
            
            # 收盘前警告
            if current_time >= self.market_close['warning_time']:
                self.logger.warning(
                    f"接近收盘时间，准备平仓当日到期期权:\n"
                    f"  标的: {position['symbol']}\n"
                    f"  到期日: {expiry_date.strftime('%Y-%m-%d')}"
                )
            
            # 强制平仓检查
            if current_time >= self.market_close['force_close_time']:
                self.logger.warning(
                    f"收盘前强制平仓:\n"
                    f"  标的: {position['symbol']}\n"
                    f"  到期日: {expiry_date.strftime('%Y-%m-%d')}\n"
                    f"  当前时间: {current_time}\n"
                    f"  平仓类型: 当日到期期权平仓"
                )
                await self._execute_market_close(position)
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"检查收盘平仓时出错: {str(e)}")
            self.logger.exception("详细错误信息:")
            return False

    def _extract_expiry_date(self, symbol: str) -> Optional[datetime]:
        """从期权代码中提取到期日期"""
        try:
            # 期权代码格式: XXXYYMMDDCNNN.US 或 XXXYYMMDDPNNN.US
            match = re.search(r'(\d{6})[CP]', symbol)
            if match:
                date_str = match.group(1)
                # 转换为日期对象 (假设年份是20YY)
                return datetime.strptime(f"20{date_str}", "%Y%m%d")
            return None
        except Exception as e:
            self.logger.error(f"提取期权到期日期时出错: {str(e)}")
            return None

    def _calculate_leverage(self, option_price: float, stock_price: float, delta: float) -> float:
        """计算期权杠杆率"""
        try:
            # 杠杆率 = delta * (股票价格/期权价格)
            return abs(delta * (stock_price / option_price))
        except ZeroDivisionError:
            return float('inf')
        except Exception as e:
            self.logger.error(f"计算杠杆率时出错: {str(e)}")
            return 0

    async def select_option_strike(self, stock_symbol: str, target_leverage: float = 25) -> Optional[str]:
        """
        选择最合适的单个期权合约
        
        Args:
            stock_symbol: 正股代码
            target_leverage: 目标杠杆率 (默认25)
        """
        try:
            # 获取正股价格和成交量
            stock_quotes = self.quote_ctx.quote([stock_symbol])
            if not stock_quotes:
                return None
            
            stock_quote = stock_quotes[0]
            stock_price = float(stock_quote.last_done)
            stock_volume = float(stock_quote.volume)
            
            # 获取可用的期权合约
            options = await self._get_available_options(stock_symbol)
            if not options:
                return None
            
            # 筛选条件
            filtered_options = []
            for option in options:
                try:
                    price = float(option['price'])
                    volume = float(option.get('volume', 0))
                    open_interest = float(option.get('open_interest', 0))
                    
                    # 1. 价格筛选 (避免太贵或太便宜的期权)
                    if not (1.0 <= price <= 15.0):
                        continue
                    
                    # 2. 流动性筛选
                    if volume < 100 or open_interest < 500:
                        continue
                    
                    # 3. 计算杠杆率
                    leverage = self._calculate_leverage(
                        option_price=price,
                        stock_price=stock_price,
                        delta=float(option['delta'])
                    )
                    
                    # 4. 杠杆率筛选 (20-30)
                    if not (20 <= leverage <= 30):
                        continue
                    
                    # 5. 到期日筛选 (7-30天)
                    days_to_expiry = (option['expiry_date'].date() - datetime.now(self.tz).date()).days
                    if not (7 <= days_to_expiry <= 30):
                        continue
                    
                    # 记录筛选后的期权
                    filtered_options.append({
                        **option,
                        'leverage': leverage,
                        'days_to_expiry': days_to_expiry,
                        'score': self._calculate_option_score(
                            price=price,
                            leverage=leverage,
                            volume=volume,
                            open_interest=open_interest,
                            days_to_expiry=days_to_expiry,
                            target_leverage=target_leverage
                        )
                    })
                    
                except Exception as e:
                    self.logger.warning(f"处理期权时出错: {str(e)}")
                    continue
            
            if not filtered_options:
                self.logger.info("没有找到符合条件的期权")
                return None
            
            # 按综合得分排序，选择最佳期权
            best_option = max(filtered_options, key=lambda x: x['score'])
            
            self.logger.info(
                f"选择期权:\n"
                f"  代码: {best_option['symbol']}\n"
                f"  类型: {best_option['type']}\n"
                f"  行权价: ${best_option['strike_price']:.2f}\n"
                f"  当前价: ${best_option['price']:.2f}\n"
                f"  杠杆率: {best_option['leverage']:.1f}x\n"
                f"  到期天数: {best_option['days_to_expiry']}天\n"
                f"  得分: {best_option['score']:.2f}"
            )
            
            return best_option['symbol']
            
        except Exception as e:
            self.logger.error(f"选择期权合约时出错: {str(e)}")
            self.logger.exception("详细错误信息:")
            return None

    def _calculate_option_score(self, price: float, leverage: float, volume: float,
                              open_interest: float, days_to_expiry: int,
                              target_leverage: float) -> float:
        """计算期权的综合得分"""
        try:
            # 1. 价格得分 (优先选择价格适中的期权)
            price_score = 1.0 - abs(price - 5.0) / 10.0  # 以5美元为最佳价格
            
            # 2. 杠杆率得分 (越接近目标杠杆率越好)
            leverage_score = 1.0 - abs(leverage - target_leverage) / target_leverage
            
            # 3. 流动性得分
            volume_score = min(volume / 1000, 1.0)  # 成交量得分
            oi_score = min(open_interest / 5000, 1.0)  # 持仓量得分
            liquidity_score = (volume_score + oi_score) / 2
            
            # 4. 到期日得分 (优先选择14-21天到期)
            if 14 <= days_to_expiry <= 21:
                expiry_score = 1.0
            else:
                expiry_score = 1.0 - abs(days_to_expiry - 17.5) / 30.0
            
            # 计算加权总分
            weights = {
                'price': 0.25,
                'leverage': 0.30,
                'liquidity': 0.25,
                'expiry': 0.20
            }
            
            total_score = (
                weights['price'] * price_score +
                weights['leverage'] * leverage_score +
                weights['liquidity'] * liquidity_score +
                weights['expiry'] * expiry_score
            )
            
            return total_score
            
        except Exception as e:
            self.logger.error(f"计算期权得分时出错: {str(e)}")
            return 0.0

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
                side=OrderSide.Sell if position["volume"] > 0 else OrderSide.Buy,  # 使用 Sell/Buy
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
            self.logger.info(f"当前时间: {current_time.strftime('%Y-%m-%d %H:%M:%S EST')}")
            self.logger.info(f"交易模式: {'测试模式' if self.test_mode else '实盘模式'}")
            
            # 打印持仓信息
            if positions["active"]:
                # 计算最大字段长度以实现表格自适应
                max_symbol_len = max(len(pos["symbol"]) for pos in positions["active"])
                symbol_width = max(25, max_symbol_len + 2)  # 至少25个字符宽
                
                # 构建表格格式
                fmt = (
                    f"{{:<{symbol_width}}} {{:>8}} {{:>12}} {{:>30}} {{:>25}}"
                )
                
                # 表头
                header = fmt.format(
                    "Symbol",          # 1. 期权代码
                    "Volume",         # 2. 数量
                    "市值",           # 3. 市值
                    "last",          # 4. 价格变动
                    "当日盈亏/盈亏率"   # 5. 盈亏信息
                )
                
                # 分隔线
                separator = "-" * len(header)
                
                # 打印表头和分隔线
                self.logger.info("\n当前持仓状态:")
                self.logger.info(separator)
                self.logger.info(header)
                self.logger.info(separator)
                
                # 按代码排序显示所有持仓
                total_value = 0
                for pos in sorted(positions["active"], key=lambda x: x["symbol"]):
                    try:
                        # 获取行情数据
                        quotes = self.quote_ctx.quote([pos["symbol"]])
                        if quotes and len(quotes) > 0:
                            quote = quotes[0]
                            current_price = float(quote.last_done)
                            prev_close = float(quote.prev_close)
                            cost_price = float(pos["cost_price"])
                            
                            # 计算涨跌幅
                            price_change_pct = ((current_price - cost_price) / cost_price * 100) if cost_price else 0
                            day_change_pct = ((current_price - prev_close) / prev_close * 100) if prev_close else 0
                            
                            # 计算当日盈亏
                            day_pnl = (current_price - prev_close) * pos["volume"]
                            
                            # 构建价格变动字符串
                            last_str = f"{cost_price:.2f} -> {current_price:.2f} ({price_change_pct:+.2f}%)"
                            
                            # 构建行数据
                            line = fmt.format(
                                pos["symbol"],
                                f"{abs(pos['volume']):d}",
                                f"${pos['market_value']:.2f}",
                                last_str,
                                f"${day_pnl:+.2f}/{day_change_pct:+.2f}%"
                            )
                            self.logger.info(line)
                            total_value += pos['market_value']
                    
                    except Exception as e:
                        self.logger.error(f"处理持仓显示时出错: {str(e)}")
                
                # 显示总计
                self.logger.info(separator)
                summary = fmt.format(
                    "总计",
                    f"{len(positions['active'])}",
                    f"${total_value:.2f}",
                    "",
                    ""
                )
                self.logger.info(summary)
                self.logger.info(separator)
                self.logger.info("")  # 添加空行
                
            else:
                self.logger.info("\n当前无持仓")
            
            # 打印风险限制信息
            self.logger.info("\n风险控制参数:")
            risk_data = [
                {
                    "类型": "期权",
                    "止损线": f"{self.risk_limits['option']['stop_loss']}%",
                    "止盈线": "不设置"
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

    async def close_position(self, symbol: str, volume: int, reason: str = ""):
        """
        平仓指定持仓
        
        Args:
            symbol: 交易标的代码
            volume: 持仓数量
            reason: 平仓原因
        """
        try:
            self.logger.warning(f"准备平仓: {symbol}, 数量: {volume}, 原因: {reason}")
            
            # 确保交易上下文存在
            if not self.trade_ctx:
                self.logger.error("交易上下文未初始化")
                return False
            
            try:
                # 提交市价单平仓
                order_resp = self.trade_ctx.submit_order(
                    symbol=symbol,
                    order_type=OrderType.MO,  # 使用市价单
                    side=OrderSide.Sell,      # 使用 Sell 而不是 SELL
                    submitted_quantity=volume,
                    time_in_force=TimeInForceType.Day,
                    remark=f"Close position: {reason}"
                )
                
                if not order_resp or not hasattr(order_resp, 'order_id'):
                    self.logger.error("平仓订单提交失败")
                    return False
                
                order_id = order_resp.order_id
                self.logger.info(f"平仓订单已提交: {symbol}, 订单ID: {order_id}")
                
                # 等待并检查订单状态
                max_retries = 5
                for i in range(max_retries):
                    await asyncio.sleep(1)
                    order_status = self.trade_ctx.order_detail(order_id)
                    self.logger.info(f"平仓订单状态 ({i+1}/{max_retries}): {order_status.status}")
                    
                    if order_status.status == "Filled":  # 完全成交
                        executed_price = float(order_status.executed_price)
                        executed_quantity = int(order_status.executed_quantity)
                        
                        self.logger.info(
                            f"平仓成功:\n"
                            f"  标的: {symbol}\n"
                            f"  数量: {executed_quantity}张\n"
                            f"  成交价: ${executed_price:.2f}\n"
                            f"  原因: {reason}"
                        )
                        return True
                        
                    elif order_status.status in ["Failed", "Rejected", "Cancelled"]:
                        self.logger.error(f"平仓订单失败: {order_status.status}")
                        return False
                
                # 超时处理
                self.logger.warning(f"平仓订单等待超时: {order_id}")
                self.trade_ctx.cancel_order(order_id)
                return False
                
            except Exception as e:
                self.logger.error(f"提交平仓订单失败: {str(e)}")
                self.logger.exception("详细错误信息:")
                return False
                
        except Exception as e:
            self.logger.error(f"执行平仓操作失败 {symbol}: {str(e)}")
            self.logger.exception("详细错误信息:")
            return False

    async def _get_available_options(self, stock_symbol: str) -> List[Dict[str, Any]]:
        """
        获取可用的期权合约
        
        Args:
            stock_symbol: 正股代码 (例如: AAPL.US)
        
        Returns:
            List[Dict]: 期权合约列表，每个合约包含 symbol, price, delta 等信息
        """
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

    async def analyze_stock_trend(self, stock_symbol: str) -> Dict[str, Any]:
        """
        分析股票趋势，使用多个技术指标综合判断
        
        Args:
            stock_symbol: 股票代码
        
        Returns:
            Dict: 包含趋势信息的字典
        """
        try:
            # 获取K线数据
            klines = await self.quote_ctx.history_candlesticks(
                symbol=stock_symbol,
                period="5m",  # 5分钟K线
                count=100     # 获取100根K线
            )
            
            if not klines:
                return {"trend": "neutral", "signal": None}
            
            # 提取价格数据
            prices = {
                'close': [float(k.close) for k in klines],
                'open': [float(k.open) for k in klines],
                'high': [float(k.high) for k in klines],
                'low': [float(k.low) for k in klines],
                'volume': [float(k.volume) for k in klines]
            }
            
            # 1. 计算技术指标
            indicators = {
                'ma5': self._calculate_ma(prices['close'], 5),
                'ma10': self._calculate_ma(prices['close'], 10),
                'ma20': self._calculate_ma(prices['close'], 20),
                'rsi': self._calculate_rsi(prices['close'], 14),
                'macd': self._calculate_macd(prices['close']),
                'volume_ma': self._calculate_ma(prices['volume'], 20)
            }
            
            # 2. 获取开盘涨跌幅
            current_quote = await self.quote_ctx.quote([stock_symbol])
            if not current_quote:
                return {"trend": "neutral", "signal": None}
            
            quote = current_quote[0]
            open_change_pct = (float(quote.open) - float(quote.prev_close)) / float(quote.prev_close) * 100
            current_change_pct = (float(quote.last_done) - float(quote.prev_close)) / float(quote.prev_close) * 100
            
            # 3. 趋势判断
            trend_signals = {
                'ma_trend': self._check_ma_trend(indicators),
                'rsi_signal': self._check_rsi_signal(indicators['rsi'][-1]),
                'macd_signal': self._check_macd_signal(indicators['macd']),
                'volume_signal': self._check_volume_signal(prices['volume'][-1], indicators['volume_ma'][-1]),
                'gap_signal': self._check_gap_signal(open_change_pct, current_change_pct)
            }
            
            # 4. 综合判断
            trend_score = self._calculate_trend_score(trend_signals)
            
            # 5. 生成交易信号
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
            
            result = {
                "trend": trend,
                "signal": signal,
                "score": trend_score,
                "details": {
                    "open_change": f"{open_change_pct:.2f}%",
                    "current_change": f"{current_change_pct:.2f}%",
                    "ma_trend": trend_signals['ma_trend'],
                    "rsi": indicators['rsi'][-1],
                    "macd": indicators['macd']['histogram'][-1],
                    "volume_ratio": prices['volume'][-1] / indicators['volume_ma'][-1]
                }
            }
            
            self.logger.info(
                f"趋势分析结果 - {stock_symbol}:\n"
                f"  趋势: {result['trend']}\n"
                f"  信号: {result['signal']}\n"
                f"  得分: {result['score']:.2f}\n"
                f"  开盘涨跌: {result['details']['open_change']}\n"
                f"  当前涨跌: {result['details']['current_change']}\n"
                f"  RSI: {result['details']['rsi']:.2f}\n"
                f"  成交量比: {result['details']['volume_ratio']:.2f}"
            )
            
            return result
            
        except Exception as e:
            self.logger.error(f"分析股票趋势时出错: {str(e)}")
            self.logger.exception("详细错误信息:")
            return {"trend": "neutral", "signal": None}

    def _calculate_ma(self, data: List[float], period: int) -> List[float]:
        """计算移动平均线"""
        ma = []
        for i in range(len(data)):
            if i < period - 1:
                ma.append(None)
            else:
                ma.append(sum(data[i-period+1:i+1]) / period)
        return ma

    def _calculate_rsi(self, prices: List[float], period: int = 14) -> List[float]:
        """计算RSI"""
        deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]
        
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        
        rsi = []
        for i in range(len(prices)):
            if i < period:
                rsi.append(None)
                continue
            
            if avg_loss == 0:
                rsi.append(100)
            else:
                rs = avg_gain / avg_loss
                rsi.append(100 - (100 / (1 + rs)))
            
            if i < len(prices) - 1:
                avg_gain = (avg_gain * (period-1) + gains[i]) / period
                avg_loss = (avg_loss * (period-1) + losses[i]) / period
            
        return rsi

    def _calculate_macd(self, prices: List[float]) -> Dict[str, List[float]]:
        """计算MACD"""
        ema12 = self._calculate_ema(prices, 12)
        ema26 = self._calculate_ema(prices, 26)
        
        macd_line = [ema12[i] - ema26[i] if ema12[i] and ema26[i] else None 
                     for i in range(len(prices))]
        signal_line = self._calculate_ema(macd_line, 9)
        
        histogram = [macd_line[i] - signal_line[i] if macd_line[i] and signal_line[i] else None
                    for i in range(len(prices))]
        
        return {
            'macd': macd_line,
            'signal': signal_line,
            'histogram': histogram
        }

    def _check_ma_trend(self, indicators: Dict[str, List[float]]) -> str:
        """检查均线趋势"""
        ma5, ma10, ma20 = indicators['ma5'][-1], indicators['ma10'][-1], indicators['ma20'][-1]
        
        if ma5 > ma10 > ma20:
            return "strong_up"
        elif ma5 > ma10:
            return "up"
        elif ma5 < ma10 < ma20:
            return "strong_down"
        elif ma5 < ma10:
            return "down"
        else:
            return "neutral"

    def _check_rsi_signal(self, rsi: float) -> str:
        """检查RSI信号"""
        if rsi > 70:
            return "overbought"
        elif rsi < 30:
            return "oversold"
        elif rsi > 60:
            return "strong"
        elif rsi < 40:
            return "weak"
        else:
            return "neutral"

    def _check_macd_signal(self, macd: Dict[str, List[float]]) -> str:
        """检查MACD信号"""
        hist = macd['histogram'][-3:]  # 最近3个柱
        
        if all(h > 0 for h in hist) and hist[-1] > hist[-2]:
            return "strong_up"
        elif all(h < 0 for h in hist) and hist[-1] < hist[-2]:
            return "strong_down"
        elif hist[-1] > 0:
            return "up"
        elif hist[-1] < 0:
            return "down"
        else:
            return "neutral"

    def _calculate_trend_score(self, signals: Dict[str, str]) -> float:
        """计算趋势综合得分"""
        scores = {
            'ma_trend': {
                'strong_up': 1.0,
                'up': 0.5,
                'neutral': 0,
                'down': -0.5,
                'strong_down': -1.0
            },
            'rsi_signal': {
                'overbought': 0.5,
                'strong': 0.3,
                'neutral': 0,
                'weak': -0.3,
                'oversold': -0.5
            },
            'macd_signal': {
                'strong_up': 1.0,
                'up': 0.5,
                'neutral': 0,
                'down': -0.5,
                'strong_down': -1.0
            },
            'volume_signal': {
                'high': 0.3,
                'normal': 0,
                'low': -0.3
            },
            'gap_signal': {
                'up_gap': 0.5,
                'down_gap': -0.5,
                'no_gap': 0
            }
        }
        
        weights = {
            'ma_trend': 0.3,
            'rsi_signal': 0.2,
            'macd_signal': 0.25,
            'volume_signal': 0.15,
            'gap_signal': 0.1
        }
        
        total_score = 0
        for signal_type, signal in signals.items():
            score = scores[signal_type].get(signal, 0)
            total_score += score * weights[signal_type]
        
        return round(total_score, 2)