"""风险检查模块"""
from typing import Dict, Any
import logging
from datetime import datetime, time

class RiskChecker:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(__name__)

    def check_market_condition(self, vix: float, current_time: str) -> bool:
        """检查市场条件"""
        try:
            # VIX检查
            risk_limits = self.config.get('risk_limits', {})
            volatility_limits = risk_limits.get('volatility', {})
            
            max_vix = volatility_limits.get('max_vix', 40)
            min_vix = volatility_limits.get('min_vix', 15)
            
            if vix > max_vix:
                self.logger.warning(f"VIX过高: {vix} > {max_vix}")
                return False
                
            if vix < min_vix:
                self.logger.warning(f"VIX过低: {vix} < {min_vix}")
                return False
                
            # 时间检查
            force_close_time = self.config.get('force_close_time', '15:45:00')
            current_time_obj = datetime.strptime(current_time, '%H:%M:%S').time()
            force_close_time_obj = datetime.strptime(force_close_time, '%H:%M:%S').time()
            
            if current_time_obj > force_close_time_obj:
                self.logger.warning(f"超过强制平仓时间: {current_time} > {force_close_time}")
                return False
                
            return True
            
        except Exception as e:
            self.logger.error(f"检查市场条件失败: {str(e)}")
            return False

    def check_position_risk(self, position: Dict[str, Any]) -> bool:
        """检查持仓风险"""
        try:
            # 检查持仓时间
            hold_time = (datetime.now() - position['entry_time']).seconds / 60
            if hold_time > self.config['risk_limits']['max_holding_time']:
                self.logger.warning(f"持仓时间过长: {hold_time}分钟")
                return False

            # 检查盈亏比例
            pnl_ratio = (position['current_price'] - position['entry_price']) / position['entry_price']
            
            # 亏损超过止损线
            if pnl_ratio < -self.config['risk_limits']['stop_loss']['initial']:
                self.logger.warning(f"亏损超过止损线: {pnl_ratio:.2%}")
                return False
                
            # 检查Delta风险
            if abs(position.get('delta', 0)) > 0.5:
                self.logger.warning(f"Delta过大: {position.get('delta', 0)}")
                return False

            # 检查Theta衰减
            if position.get('theta', 0) < -0.1:
                self.logger.warning(f"Theta衰减过快: {position.get('theta', 0)}")
                return False

            return True
        except Exception as e:
            self.logger.error(f"检查持仓风险失败: {str(e)}")
            return False

    def check_portfolio_risk(self, positions: Dict[str, Dict[str, Any]]) -> bool:
        """检查组合风险"""
        try:
            total_delta = sum(pos.get('delta', 0) for pos in positions.values())
            total_theta = sum(pos.get('theta', 0) for pos in positions.values())
            
            # 检查总Delta
            if abs(total_delta) > 2.0:
                self.logger.warning(f"组合Delta过大: {total_delta}")
                return False
                
            # 检查总Theta
            if total_theta < -0.3:
                self.logger.warning(f"组合Theta衰减过快: {total_theta}")
                return False
                
            return True
        except Exception as e:
            self.logger.error(f"检查组合风险失败: {str(e)}")
            return False