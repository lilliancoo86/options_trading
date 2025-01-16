"""风险检查模块"""
from typing import Dict, Any, Optional, Tuple
import logging
from datetime import datetime, time
from decimal import Decimal

class RiskChecker:
    def __init__(self, risk_limits: dict):
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # 设置默认风险限制
        self.default_limits = {
            'option': {
                'stop_loss': {
                    'initial': 10.0,    # 初始止损比例
                    'trailing': 7.0     # 移动止损比例
                },
                'take_profit': 50.0     # 止盈目标比例
            },
            'volatility': {
                'min_vix': 15.0,        # 最小VIX
                'max_vix': 40.0         # 最大VIX
            },
            'market_hours': {
                'start': '09:30',       # 交易开始时间
                'end': '16:00'          # 交易结束时间
            }
        }

        # 使用传入的风险限制，如果没有则使用默认值
        self.risk_limits = risk_limits if risk_limits else self.default_limits
        
        # 确保必要的配置项存在
        if 'option' not in self.risk_limits:
            self.risk_limits['option'] = self.default_limits['option']
        
        # 初始化止盈止损设置
        self.option_limits = self.risk_limits['option']
        self.initial_stop_loss = self.option_limits.get('stop_loss', {}).get('initial', 10.0)
        self.trailing_stop = self.option_limits.get('stop_loss', {}).get('trailing', 7.0)
        self.take_profit = self.option_limits.get('take_profit', 50.0)
        
        # 记录最高收益率
        self.max_profit_pct = {}  # 用于跟踪每个持仓的最高收益率
        
        self.logger.info(f"风险检查器初始化完成: 初始止损={self.initial_stop_loss}%, "
                        f"移动止损={self.trailing_stop}%, 止盈目标={self.take_profit}%")

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

    def check_market_condition(self, current_vix: float, current_time: str) -> bool:
        """检查市场条件是否适合交易"""
        try:
            # 检查 VIX 范围
            vix_limits = self.risk_limits.get('volatility', self.default_limits['volatility'])
            if not (vix_limits['min_vix'] <= current_vix <= vix_limits['max_vix']):
                self.logger.warning(f"VIX指数 ({current_vix}) 超出交易范围 "
                                  f"({vix_limits['min_vix']}-{vix_limits['max_vix']})")
                return False

            # 检查交易时间
            market_hours = self.risk_limits.get('market_hours', self.default_limits['market_hours'])
            if not self._is_trading_hours(current_time, market_hours['start'], market_hours['end']):
                self.logger.warning(f"当前时间 ({current_time}) 不在交易时间内 "
                                  f"({market_hours['start']}-{market_hours['end']})")
                return False

            return True

        except Exception as e:
            self.logger.error(f"检查市场条件时出错: {str(e)}")
            return False

    def _is_trading_hours(self, current_time: str, start_time: str, end_time: str) -> bool:
        """检查是否在交易时间内"""
        try:
            current = datetime.strptime(current_time, '%H:%M:%S').time()
            start = datetime.strptime(start_time, '%H:%M').time()
            end = datetime.strptime(end_time, '%H:%M').time()
            return start <= current <= end
        except Exception as e:
            self.logger.error(f"检查交易时间时出错: {str(e)}")
            return False