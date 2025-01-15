"""末日期权策略实现"""
from typing import Dict, Any, List, Tuple, Optional
from datetime import datetime, timedelta
import logging
import os
import numpy as np
from decimal import Decimal
from longport.openapi import (
    TradeContext, 
    QuoteContext, 
    Config, 
    SubType,
    Period, 
    OptionType,
    PushQuote
)
import asyncio
from tabulate import tabulate  # 添加这个导入用于表格展示

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
            "| HK       |"
        ]
        message = record.getMessage()
        return not any(s in message for s in filtered_strings)

class DoomsdayOptionStrategy:
    def __init__(self, trading_config, api_config):
        self.trading_config = trading_config
        self.api_config = api_config
        
        # 添加日志记录器
        self.logger = logging.getLogger(__name__)
        
        # 添加 VIX 到订阅列表
        self.symbols = trading_config.get('symbols', ["700.HK", "AAPL.US"])
        if "VIX.US" not in self.symbols:
            self.symbols.append("VIX.US")
        
        self.quote_ctx = None
        self.trade_ctx = None
        
        # 初始化持仓管理器
        from trading.position_manager import DoomsdayPositionManager
        self.position_manager = DoomsdayPositionManager(trading_config)
        
        # 保存订阅类型
        self.sub_types = []
        for type_name in self.api_config['quote_context']['sub_types']:
            try:
                sub_type = getattr(SubType, type_name)
                self.sub_types.append(sub_type)
            except AttributeError:
                self.logger.error(f"无效的订阅类型: {type_name}")

    def on_quote(self, symbol: str, event: PushQuote):
        """行情数据回调"""
        # 只记录 VIX 的变化和重要的价格变动
        if symbol == "VIX.US":
            self.logger.info(f"VIX指数: {event.last_done}")
        else:
            # 计算价格变动百分比
            if hasattr(self, 'last_prices') and symbol in self.last_prices:
                change_pct = (event.last_done - self.last_prices[symbol]) / self.last_prices[symbol]
                # 只有价格变动超过0.5%才记录
                if abs(change_pct) >= 0.005:
                    self.logger.info(f"{symbol} 价格变动: {event.last_done:.2f} ({change_pct:.2%})")
            
            # 更新最新价格
            if not hasattr(self, 'last_prices'):
                self.last_prices = {}
            self.last_prices[symbol] = event.last_done

    async def initialize(self):
        """初始化策略"""
        try:
            # 创建配置对象
            config = Config.from_env()
            
            # 添加日志过滤器
            log_filter = MarketInfoFilter()
            root_logger = logging.getLogger()
            for handler in root_logger.handlers:
                handler.addFilter(log_filter)
            
            # 初始化交易上下文
            try:
                self.trade_ctx = TradeContext(config)
                # 验证交易上下文
                self.trade_ctx.account_balance()
                self.logger.info("交易上下文初始化成功")
            except Exception as e:
                self.logger.error(f"交易上下文初始化失败: {str(e)}")
                raise
            
            # 初始化行情上下文
            try:
                self.quote_ctx = QuoteContext(config)
                # 设置行情回调
                self.quote_ctx.set_on_quote(self.on_quote)
                # 执行订阅
                self.quote_ctx.subscribe(
                    symbols=self.symbols,
                    sub_types=self.sub_types
                )
                self.logger.info("行情上下文初始化成功")
            except Exception as e:
                self.logger.error(f"行情上下文初始化失败: {str(e)}")
                raise
            
            # 获取并显示当前持仓
            await self.position_manager.get_real_positions()
            
            # 移除日志过滤器
            for handler in root_logger.handlers:
                handler.removeFilter(log_filter)
            
            self.logger.info("策略初始化完成")
            
        except Exception as e:
            self.logger.error(f"策略初始化失败: {str(e)}")
            raise
            
    async def get_market_data(self):
        """获取市场数据"""
        try:
            if not self.quote_ctx:
                raise ValueError("QuoteContext 未初始化")
            
            # 获取 VIX 指数数据
            vix_symbol = "VIX.US"
            try:
                vix_quote = self.quote_ctx.quote([vix_symbol])
                vix_value = float(vix_quote[0].last_done) if vix_quote else 20.0
            except Exception as e:
                logging.warning(f"获取VIX数据失败: {str(e)}, 使用默认值20.0")
                vix_value = 20.0
            
            # 获取其他股票行情
            quotes = self.quote_ctx.quote(self.symbols)
            
            # 每5分钟打印一次市场概况
            current_time = datetime.now()
            if not hasattr(self, '_last_market_log') or \
               (current_time - self._last_market_log).seconds >= 300:
                
                # 打印市场状态表头
                logging.info("\n=== 美股市场状态 ===")
                header = [
                    "市场",
                    "标的",
                    "最新价",
                    "涨跌幅",
                    "成交量",
                    "波动率",
                    "状态"
                ]
                market_data = []
                
                # 处理美股和期权数据
                for quote in quotes:
                    if not quote.symbol.endswith(('.US', '.USOption')):
                        continue
                        
                    # 计算日内波动率
                    daily_volatility = (quote.high - quote.low) / quote.open * 100
                    # 计算涨跌幅
                    price_change = (quote.last_done - quote.open) / quote.open * 100
                    
                    market_type = "期权" if quote.symbol.endswith('.USOption') else "股票"
                    
                    row = {
                        "市场": market_type,
                        "标的": quote.symbol.replace('.US', '').replace('.USOption', ''),
                        "最新价": f"${quote.last_done:.2f}",
                        "涨跌幅": f"{price_change:+.2f}%",
                        "成交量": f"{quote.volume:,}",
                        "波动率": f"{daily_volatility:.2f}%",
                        "状态": "正常" if quote.trade_status == "Normal" else "暂停"
                    }
                    market_data.append(row)
                
                if market_data:
                    table = tabulate(
                        market_data,
                        headers="keys",
                        tablefmt="grid",
                        numalign="right",
                        stralign="left"
                    )
                    logging.info(f"\nVIX指数: {vix_value:.2f}")
                    logging.info(f"\n{table}")
                
                self._last_market_log = current_time
            
            return {
                'quotes': quotes,
                'vix': vix_value,
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            logging.error(f"获取市场数据失败: {str(e)}")
            raise
            
    async def get_current_price(self, symbol):
        """获取当前价格"""
        try:
            if not self.quote_ctx:
                raise ValueError("QuoteContext 未初始化")
            # 使用同步方法获取报价
            quote = self.quote_ctx.quote([symbol])
            return quote[0].last_done if quote else None
        except Exception as e:
            logging.error(f"获取价格失败: {str(e)}")
            raise
            
    async def close(self):
        """关闭连接"""
        try:
            if self.quote_ctx and self.sub_types:
                try:
                    # 取消所有订阅 (同步调用)
                    self.quote_ctx.unsubscribe(
                        symbols=self.symbols,
                        sub_types=self.sub_types
                    )
                    logging.info("已取消所有订阅")
                except Exception as e:
                    logging.error(f"取消订阅失败: {str(e)}")
            
            # 关闭连接
            if hasattr(self, 'position_manager'):
                self.position_manager.close_contexts()
            
            self.quote_ctx = None
            self.trade_ctx = None
            logging.info("已关闭所有连接")
                
        except Exception as e:
            logging.error(f"关闭连接失败: {str(e)}")
            raise

    async def generate_signals(self, market_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """生成交易信号"""
        try:
            signals = []
            quotes = market_data['quotes']
            vix = market_data['vix']
            signal_data = []
            
            for quote in quotes:
                if quote.symbol == "VIX.US":
                    continue
                    
                # 计算技术指标
                daily_volatility = (quote.high - quote.low) / quote.open * 100
                price_change = (quote.last_done - quote.open) / quote.open * 100
                
                # 生成信号的条件
                signal = {
                    'symbol': quote.symbol,
                    'price': quote.last_done,
                    'volatility': daily_volatility,
                    'vix': vix,
                    'timestamp': market_data['timestamp'],
                    'signal_type': None,
                    'strength': 0,
                    'reason': []
                }
                
                # 信号生成逻辑
                reasons = []
                signal_strength = 0
                
                # VIX 分析
                if 15 <= vix <= 25:
                    signal_strength += 0.2
                    reasons.append("VIX在理想区间")
                elif 25 < vix <= 35:
                    signal_strength += 0.1
                    reasons.append("VIX略高")
                
                # 波动率分析
                if daily_volatility < 2:
                    signal_strength += 0.2
                    reasons.append("日内波动率正常")
                elif daily_volatility < 3:
                    signal_strength += 0.1
                    reasons.append("日内波动率较高")
                
                # 价格变动分析
                if abs(price_change) > 2:
                    signal_strength += 0.2
                    reasons.append(f"价格显著变动: {price_change:.1f}%")
                
                # 确定信号类型
                if signal_strength >= 0.3:
                    if price_change < -1:
                        # 检查是否可以开仓
                        can_open = await self.position_manager.can_open_position(quote.symbol, vix)
                        if can_open:
                            signal['signal_type'] = 'buy'
                            reasons.append("价格回调买入机会")
                    elif price_change > 1:
                        can_open = await self.position_manager.can_open_position(quote.symbol, vix)
                        if can_open:
                            signal['signal_type'] = 'sell'
                            reasons.append("价格上涨卖出机会")
                
                # 只添加有效信号
                if signal['signal_type']:
                    signal['strength'] = signal_strength
                    signal['reason'] = reasons
                    signals.append(signal)
                    
                    # 添加到表格数据
                    signal_info = {
                        "标的": signal['symbol'].replace('.US', ''),
                        "类型": "买入" if signal['signal_type'] == 'buy' else "卖出",
                        "现价": f"${signal['price']:.2f}",
                        "强度": f"{signal['strength']:.2f}",
                        "VIX": f"{signal['vix']:.1f}",
                        "波动率": f"{signal['volatility']:.1f}%",
                        "原因": ' | '.join(signal['reason'])
                    }
                    signal_data.append(signal_info)
            
            # 如果有信号，打印表格
            if signal_data:
                logging.info("\n=== 交易信号 ===")
                table = tabulate(
                    signal_data,
                    headers="keys",
                    tablefmt="grid",
                    numalign="right",
                    stralign="left",
                    maxcolwidths=[10, 6, 10, 6, 6, 8, 50]  # 设置每列最大宽度
                )
                logging.info(f"\n{table}")
            
            return signals
            
        except Exception as e:
            logging.error(f"生成交易信号失败: {str(e)}")
            return []

    async def execute_signals(self, signals: List[Dict[str, Any]]):
        """执行交易信号"""
        try:
            for signal in signals:
                symbol = signal['symbol']
                
                # 检查是否可以开仓
                can_open = await self.position_manager.can_open_position(symbol, signal['vix'])
                if not can_open:
                    continue
                    
                # 计算仓位大小
                position_size = self.calculate_position_size(signal)
                
                # 生成订单
                order = {
                    'symbol': symbol,
                    'quantity': position_size,
                    'price': signal['price'],
                    'volatility': signal.get('volatility', 0.2),
                    'delta': signal.get('delta', 0),
                    'theta': signal.get('theta', 0),
                    'vix': signal.get('vix', 20)
                }
                
                # 开仓
                success = await self.position_manager.open_position(order)
                if success:
                    self.logger.info(f"开仓成功: {symbol}, 数量: {position_size}")
                    
        except Exception as e:
            self.logger.error(f"执行交易信号失败: {str(e)}")

    def calculate_position_size(self, signal: Dict[str, Any]) -> int:
        """
        计算开仓数量
        
        Args:
            signal: 交易信号字典
            
        Returns:
            int: 建议开仓数量
        """
        try:
            # 获取仓位管理配置
            position_sizing = self.trading_config.get('position_sizing', {})
            method = position_sizing.get('method', 'fixed_ratio')
            size_limits = position_sizing.get('size_limit', {})
            value_limits = position_sizing.get('value_limit', {})
            risk_ratio = float(position_sizing.get('risk_ratio', 0.02))
            
            # 获取当前价格并转换为 float
            current_price = float(signal['price'])
            
            # 转换限制值为 float
            max_value = float(value_limits.get('max', 100000))
            min_value = float(value_limits.get('min', 1000))
            max_size = int(size_limits.get('max', 100))
            min_size = int(size_limits.get('min', 1))
            
            # 根据不同方法计算仓位
            if method == 'fixed_ratio':
                # 使用固定比例计算
                position_value = max_value * risk_ratio
                position_size = int(position_value / current_price)
                
            elif method == 'kelly':
                # 使用凯利公式计算
                win_rate = 0.6  # 假设胜率
                profit_ratio = 2.0  # 盈亏比
                kelly_ratio = win_rate - (1 - win_rate) / profit_ratio
                position_value = max_value * kelly_ratio * risk_ratio
                position_size = int(position_value / current_price)
                
            elif method == 'risk_parity':
                # 使用风险平价方法
                volatility = float(signal.get('volatility', 20)) / 100  # 波动率
                target_risk = max_value * risk_ratio
                position_value = target_risk / volatility
                position_size = int(position_value / current_price)
                
            else:
                # 默认使用固定数量
                position_size = max_size
            
            # 应用数量限制
            position_size = max(min_size, min(position_size, max_size))
            
            # 检查金额限制
            position_value = position_size * current_price
            if position_value < min_value:
                position_size = int(min_value / current_price)
            elif position_value > max_value:
                position_size = int(max_value / current_price)
            
            # 确保至少开仓1手
            position_size = max(1, position_size)
            
            # 记录计算过程
            logging.info("\n=== 仓位计算 ===")
            table_data = [{
                "方法": method,
                "目标金额": f"${position_value:,.2f}",
                "建议数量": str(position_size),
                "信号强度": f"{signal.get('strength', 0):.2f}",
                "每股价格": f"${current_price:.2f}"
            }]
            
            table = tabulate(
                table_data,
                headers="keys",
                tablefmt="grid",
                numalign="right",
                stralign="left"
            )
            logging.info(f"\n{table}")
            
            return position_size
            
        except Exception as e:
            logging.error(f"计算仓位大小失败: {str(e)}")
            return 1  # 发生错误时返回1，确保至少开仓1手