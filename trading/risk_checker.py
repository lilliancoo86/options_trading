"""风险检查模块"""
from typing import Dict, Any, Optional, Tuple
import logging
from datetime import datetime, time
from decimal import Decimal

class RiskChecker:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.risk_limits = {
            'option': {
                'stop_loss': {
                    'initial': 10.0,  # 固定止损比例
                    'trailing': 5.0   # 移动止损比例
                },
                'take_profit': 20.0   # 止盈比例
            }
        }
        # 记录每个持仓的最高价格
        self.max_prices = {}

    def check_position_risk(self, symbol: str, current_price: float, cost_price: float) -> Tuple[bool, str]:
        """检查持仓风险（改进的逻辑）"""
        try:
            # 计算当前盈亏比例
            pnl_pct = ((current_price - cost_price) / cost_price * 100) if cost_price else 0
            
            # 更新最高价格
            if symbol not in self.max_prices or current_price > self.max_prices[symbol]:
                self.max_prices[symbol] = current_price
            
            # 计算从最高点的回撤比例
            max_price = self.max_prices[symbol]
            drawdown_pct = ((max_price - current_price) / max_price * 100) if max_price else 0
            
            # 记录详细日志
            self.logger.debug(
                f"风险检查 - {symbol}:\n"
                f"  当前价格: ${current_price:.2f}\n"
                f"  成本价格: ${cost_price:.2f}\n"
                f"  最高价格: ${max_price:.2f}\n"
                f"  盈亏比例: {pnl_pct:+.2f}%\n"
                f"  回撤比例: {drawdown_pct:.2f}%"
            )
            
            # 1. 固定止损检查（亏损超过10%）
            if pnl_pct <= -self.risk_limits['option']['stop_loss']['initial']:
                return True, f"触发固定止损: 亏损 {abs(pnl_pct):.2f}% >= {self.risk_limits['option']['stop_loss']['initial']}%"
            
            # 2. 移动止损检查（从最高点回撤超过5%）
            if pnl_pct > 0 and drawdown_pct >= self.risk_limits['option']['stop_loss']['trailing']:
                return True, f"触发移动止损: 从最高点回撤 {drawdown_pct:.2f}% >= {self.risk_limits['option']['stop_loss']['trailing']}%"
            
            # 3. 止盈检查
            if pnl_pct >= self.risk_limits['option']['take_profit']:
                return True, f"触发止盈: 盈利 {pnl_pct:.2f}% >= {self.risk_limits['option']['take_profit']}%"
            
            return False, "未触发风险控制"
            
        except Exception as e:
            self.logger.error(f"检查持仓风险时出错: {str(e)}")
            self.logger.exception("详细错误信息:")
            return False, "风险检查出错"