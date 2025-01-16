"""风险检查模块"""
from typing import Dict, Any, Optional, Tuple
import logging
from datetime import datetime, time
from decimal import Decimal

class RiskChecker:
    def __init__(self, risk_limits: dict):
        self.risk_limits = risk_limits
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # 初始化止盈止损设置
        self.option_limits = risk_limits['option']
        self.initial_stop_loss = self.option_limits['stop_loss']['initial']  # 初始止损
        self.trailing_stop = self.option_limits['stop_loss']['trailing']     # 移动止损
        self.take_profit = self.option_limits['take_profit']                 # 止盈目标
        
        # 记录最高收益率
        self.max_profit_pct = {}  # 用于跟踪每个持仓的最高收益率

    def check_position_risk(self, symbol: str, current_pnl_pct: float, 
                          price_trend: str, time_trend: str) -> Tuple[bool, str]:
        """检查持仓风险"""
        try:
            # 更新最高收益率
            if symbol not in self.max_profit_pct:
                self.max_profit_pct[symbol] = current_pnl_pct
            else:
                self.max_profit_pct[symbol] = max(self.max_profit_pct[symbol], current_pnl_pct)
            
            # 计算从最高点的回撤
            drawdown = current_pnl_pct - self.max_profit_pct[symbol]
            
            # 1. 检查初始止损
            if current_pnl_pct <= -self.initial_stop_loss:
                reason = f"触发初始止损: {current_pnl_pct:.1f}% <= -{self.initial_stop_loss}%"
                self.logger.warning(reason)
                return True, reason
            
            # 2. 检查移动止损
            if drawdown <= -self.trailing_stop:
                reason = (f"触发移动止损: 从最高点{self.max_profit_pct[symbol]:.1f}%回撤"
                         f"{drawdown:.1f}% <= -{self.trailing_stop}%")
                self.logger.warning(reason)
                return True, reason
            
            # 3. 根据趋势动态调整止盈水平
            trend_multiplier = self._get_trend_multiplier(price_trend, time_trend)
            adjusted_take_profit = self.take_profit * trend_multiplier
            
            if current_pnl_pct >= adjusted_take_profit:
                reason = (f"触发止盈: {current_pnl_pct:.1f}% >= {adjusted_take_profit:.1f}% "
                         f"(趋势: 价格={price_trend}, 分时={time_trend})")
                self.logger.warning(reason)
                return True, reason
            
            return False, ""
            
        except Exception as e:
            self.logger.error(f"检查持仓风险时出错: {str(e)}")
            return False, ""

    def _get_trend_multiplier(self, price_trend: str, time_trend: str) -> float:
        """根据趋势强度调整止盈倍数"""
        # 基础止盈倍数
        base_multiplier = 1.0
        
        # 价格趋势调整
        price_adjustments = {
            'super_strong': 2.0,
            'strong': 1.5,
            'normal': 1.0,
            'weak': 0.8,
            'super_weak': 0.5
        }
        
        # 分时趋势调整
        time_adjustments = {
            'strong_up': 1.2,
            'up': 1.1,
            'neutral': 1.0,
            'down': 0.9,
            'strong_down': 0.8
        }
        
        # 计算综合倍数
        price_mult = price_adjustments.get(price_trend, 1.0)
        time_mult = time_adjustments.get(time_trend, 1.0)
        
        return base_multiplier * price_mult * time_mult