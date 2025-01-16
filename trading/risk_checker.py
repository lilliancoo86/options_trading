"""风险检查模块"""
from typing import Dict, Any, Optional, Tuple
import logging
from datetime import datetime, time
from decimal import Decimal

class RiskChecker:
    def __init__(self):
        # 加载风险控制参数
        self.risk_limits = {
            'option': {
                'stop_loss': {
                    'initial': 10.0,  # 固定止损比例
                    'trailing': 5.0   # 移动止损比例
                },
                'take_profit': 20.0   # 止盈比例
            },
            'volatility': {
                'min_vix': 15.0,
                'max_vix': 40.0
            }
        }

    def check_position_risk(self, symbol: str, pnl_pct: float, price_trend: str, time_trend: str) -> Tuple[bool, str]:
        """检查持仓风险"""
        try:
            # 固定止损检查（亏损超过10%）
            if pnl_pct <= -self.risk_limits['option']['stop_loss']['initial']:
                return True, f"触发固定止损: {pnl_pct:.2f}% <= -{self.risk_limits['option']['stop_loss']['initial']}%"

            # 移动止损检查
            if pnl_pct > self.risk_limits['option']['stop_loss']['trailing']:
                trailing_stop = pnl_pct - self.risk_limits['option']['stop_loss']['trailing']
                if price_trend == 'DOWN' and time_trend == 'DOWN':
                    return True, f"触发移动止损: 价格和时间趋势向下"

            # 止盈检查
            if pnl_pct >= self.risk_limits['option']['take_profit']:
                return True, f"触发止盈: {pnl_pct:.2f}% >= {self.risk_limits['option']['take_profit']}%"

            return False, "未触发风险控制"

        except Exception as e:
            logging.error(f"检查持仓风险时出错: {str(e)}")
            return False, "风险检查出错"