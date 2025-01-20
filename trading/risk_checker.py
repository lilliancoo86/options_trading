"""
风险检查模块
负责检查持仓风险和市场风险
"""
from typing import Dict, Any, Tuple
import logging
from datetime import datetime
import pytz
import re

class RiskChecker:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.tz = pytz.timezone('America/New_York')
        
        # 风险控制参数
        self.risk_limits = {
            'volatility': {
                'max_daily': 5.0,  # 最大日内波动率
                'warning': 3.0     # 波动率警告线
            },
            'vix': {
                'max_level': 35.0,
                'warning': 25.0
            },
            'option': {
                'stop_loss': -10.0,  # 期权固定10%止损
                'take_profit': None  # 期权不设固定止盈
            },
            'stock': {
                'stop_loss': -3.0,   # 股票固定3%止损
                'take_profit': 5.0    # 股票固定5%止盈
            },
            'market': {
                'max_position_value': 100000,  # 单个持仓限额
                'max_total_exposure': 500000,  # 总持仓限额
                'max_positions': 10  # 最大持仓数量限制
            }
        }
        
        # 添加收盘平仓设置
        self.market_close = {
            'force_close_time': '15:45',  # 收盘前15分钟强制平仓
            'warning_time': '15:40'       # 收盘前20分钟发出警告
        }

    async def check_position_risk(self, position: Dict[str, Any]) -> Tuple[bool, str]:
        """
        检查持仓风险
        
        Returns:
            Tuple[bool, str]: (是否需要平仓, 平仓原因)
        """
        try:
            symbol = position['symbol']
            
            # 首先检查期权到期日（最高优先级）
            need_close, reason = self.time_checker.check_expiry_close(symbol)
            if need_close:
                return True, reason
            
            # 检查是否需要收盘平仓（次高优先级）
            need_close, reason = self.time_checker.check_force_close()
            if need_close:
                return True, reason
            
            current_price = float(position['current_price'])
            cost_price = float(position['cost_price'])
            volume = position['volume']
            
            # 计算盈亏百分比
            pnl_pct = (current_price - cost_price) / cost_price * 100
            
            # 判断是否为期权
            is_option = bool(re.search(r'\d{6}[CP]\d+\.US$', symbol))
            limits = self.risk_limits['option'] if is_option else self.risk_limits['stock']
            
            # 检查止损条件
            if limits['stop_loss'] is not None and pnl_pct <= limits['stop_loss']:
                self.logger.warning(
                    f"触发止损信号:\n"
                    f"  标的: {symbol}\n"
                    f"  类型: {'期权' if is_option else '股票'}\n"
                    f"  当前亏损: {pnl_pct:.1f}%\n"
                    f"  止损线: {limits['stop_loss']}%"
                )
                return True, "止损"
                
            # 检查止盈条件
            if limits['take_profit'] is not None and pnl_pct >= limits['take_profit']:
                self.logger.info(
                    f"触发止盈信号:\n"
                    f"  标的: {symbol}\n"
                    f"  类型: {'期权' if is_option else '股票'}\n"
                    f"  当前盈利: {pnl_pct:.1f}%\n"
                    f"  止盈线: {limits['take_profit']}%"
                )
                return True, "止盈"
            
            return False, ""
            
        except Exception as e:
            self.logger.error(f"检查持仓风险时出错: {str(e)}")
            self.logger.exception("详细错误信息:")
            return False, ""

    async def check_market_risk(self, vix_level: float, daily_volatility: float) -> Tuple[bool, str]:
        """
        检查市场风险
        
        Returns:
            Tuple[bool, str]: (是否风险过高, 原因)
        """
        try:
            vol_limits = self.risk_limits['volatility']
            
            # 检查日内波动率
            if daily_volatility > vol_limits['max_daily']:
                self.logger.warning(
                    f"日内波动率过高:\n"
                    f"  当前波动率: {daily_volatility:.1f}%\n"
                    f"  最大限制: {vol_limits['max_daily']}%"
                )
                return True, "波动率过高"
            
            return False, ""
            
        except Exception as e:
            self.logger.error(f"检查市场风险时出错: {str(e)}")
            self.logger.exception("详细错误信息:")
            return False, ""

    def check_market_status(self) -> bool:
        """检查市场状态"""
        try:
            current_time = datetime.now(self.tz)
            
            # 检查是否为工作日
            if current_time.weekday() > 4:  # 周六日不交易
                return False
            
            # 检查是否在交易时段 (9:30-16:00)
            time_str = current_time.strftime('%H:%M')
            if '09:30' <= time_str <= '16:00':
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"检查市场状态时出错: {str(e)}")
            return False

    def log_risk_status(self, position: Dict[str, Any]):
        """记录风险状态"""
        try:
            current_price = float(position.get('current_price', 0))
            cost_price = float(position.get('cost_price', 0))
            if cost_price == 0:
                return
            
            pnl_pct = (current_price - cost_price) / cost_price * 100
            is_option = self._is_option(position['symbol'])
            limits = self.risk_limits['option'] if is_option else self.risk_limits['stock']
            
            self.logger.info(
                f"\n=== 风险状态 [{position['symbol']}] ===\n"
                f"当前价格: ${current_price:.2f}\n"
                f"成本价格: ${cost_price:.2f}\n"
                f"收益率: {pnl_pct:+.1f}%\n"
                f"止损线: {limits['stop_loss']}%\n"
                f"止盈线: {limits['take_profit'] if not is_option else 'N/A'}\n"
                f"当前时间: {datetime.now(self.tz).strftime('%H:%M:%S')}\n"
                f"{'='*40}"
            )
            
        except Exception as e:
            self.logger.error(f"记录风险状态时出错: {str(e)}")

    def _is_option(self, symbol: str) -> bool:
        """检查是否为期权"""
        return any(x in symbol for x in ['C', 'P'])

    def check_new_position_risk(self, symbol: str, price: float, volume: int) -> Tuple[bool, str]:
        """检查新开仓位的风险"""
        try:
            # 计算持仓价值
            position_value = price * volume
            
            # 检查单个持仓限额
            if position_value > self.risk_limits['market']['max_position_value']:
                self.logger.warning(
                    f"超过单个持仓限额:\n"
                    f"  标的: {symbol}\n"
                    f"  持仓价值: ${position_value:.2f}\n"
                    f"  限额: ${self.risk_limits['market']['max_position_value']}"
                )
                return True, "超过持仓限额"
            
            # 检查总持仓限额
            total_value = self.risk_stats['total_exposure'] + position_value
            if total_value > self.risk_limits['market']['max_total_exposure']:
                self.logger.warning(
                    f"超过总持仓限额:\n"
                    f"  当前总持仓: ${self.risk_stats['total_exposure']:.2f}\n"
                    f"  新增持仓: ${position_value:.2f}\n"
                    f"  限额: ${self.risk_limits['market']['max_total_exposure']}"
                )
                return True, "超过总持仓限额"
            
            # 检查持仓数量限制
            if self.risk_stats['total_positions'] >= self.risk_limits['market']['max_positions']:
                self.logger.warning(f"超过最大持仓数量限制: {self.risk_stats['total_positions']}")
                return True, "超过持仓数量限制"
            
            return False, ""
            
        except Exception as e:
            self.logger.error(f"检查新开仓位风险时出错: {str(e)}")
            return False, ""