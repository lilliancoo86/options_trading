"""风险检查模块"""
from typing import Dict, Any, Optional
import logging
from datetime import datetime, time
from decimal import Decimal

class RiskChecker:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        # 风险限制
        self.risk_limits = config['risk_limits']
        self.position_limits = config['position_sizing']
        
        # 趋势配置
        self.trend_config = config.get('trend_config', {
            'fast_length': 1,
            'slow_length': 5,
            'trend_period': 5,
            'vwap_dev': 2.0
        })
        
        # 初始化统计数据
        self.stats = {
            'daily_pnl': Decimal('0'),
            'total_position_value': Decimal('0'),
            'position_count': 0,
            'max_drawdown': Decimal('0')
        }

    def check_market_condition(self, vix: float, current_time: str) -> bool:
        """
        检查市场条件
        
        Args:
            vix: VIX指数值
            current_time: 当前时间 (格式: "HH:MM:SS")
            
        Returns:
            bool: 市场条件是否适合交易
        """
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

    def check_portfolio_risk(self, positions: Dict[str, Dict[str, Any]]) -> bool:
        """
        检查组合风险
        
        Args:
            positions: 所有持仓信息
            
        Returns:
            bool: 组合风险是否可接受
        """
        try:
            # 计算组合指标
            total_value = sum(float(pos.get('market_value', 0)) for pos in positions.values())
            total_delta = sum(float(pos.get('delta', 0)) for pos in positions.values())
            total_theta = sum(float(pos.get('theta', 0)) for pos in positions.values())
            
            # 检查总持仓市值
            if total_value > self.position_limits['value_limit']['max']:
                self.logger.warning(f"组合市值过大: ${total_value:,.2f}")
                return False
            
            # 检查总Delta
            max_portfolio_delta = self.config.get('max_portfolio_delta', 2.0)
            if abs(total_delta) > max_portfolio_delta:
                self.logger.warning(f"组合Delta过大: {total_delta:.2f}")
                return False
            
            # 检查总Theta
            min_portfolio_theta = self.config.get('min_portfolio_theta', -0.3)
            if total_theta < min_portfolio_theta:
                self.logger.warning(f"组合Theta衰减过快: {total_theta:.2f}")
                return False
            
            return True
            
        except Exception as e:
            self.logger.error(f"检查组合风险失败: {str(e)}")
            return False

    async def check_position_risk(self, position: Dict[str, Any], trend_data: Dict[str, str]) -> bool:
        """
        检查持仓风险
        
        Args:
            position: 持仓信息
            trend_data: 趋势数据 {'price_trend': str, 'time_trend': str}
            
        Returns:
            bool: 是否需要平仓
        """
        try:
            current_price = float(position.get('current_price', 0))
            cost_price = float(position.get('cost_price', 0))
            
            if not current_price or not cost_price:
                return False
                
            # 计算收益率
            pnl_pct = (current_price - cost_price) / cost_price * 100
            
            # 1. 检查止损条件
            if await self._check_stop_loss(position, pnl_pct):
                return True
                
            # 2. 检查止盈条件
            if await self._check_take_profit(position, pnl_pct, trend_data):
                return True
                
            # 3. 检查移动止损
            if await self._check_trailing_stop(position, pnl_pct):
                return True
                
            # 4. 检查趋势反转
            if await self._check_trend_reversal(position, trend_data):
                return True
                
            return False
            
        except Exception as e:
            self.logger.error(f"检查持仓风险时出错: {str(e)}")
            return False

    async def _check_stop_loss(self, position: Dict[str, Any], pnl_pct: float) -> bool:
        """检查止损条件"""
        try:
            stop_loss = self.risk_limits['option']['stop_loss']
            
            # 固定止损
            if pnl_pct <= -stop_loss['initial'] * 100:
                self.logger.warning(f"触发固定止损: {pnl_pct:.1f}% <= -{stop_loss['initial']*100}%")
                return True
                
            return False
            
        except Exception as e:
            self.logger.error(f"检查止损条件时出错: {str(e)}")
            return False

    async def _check_take_profit(self, position: Dict[str, Any], pnl_pct: float, trend_data: Dict[str, str]) -> bool:
        """检查止盈条件"""
        try:
            base_take_profit = self.risk_limits['option']['take_profit'] * 100
            
            # 根据趋势调整止盈目标
            if trend_data['price_trend'] == 'super_strong' and trend_data['time_trend'] in ['strong_up', 'up']:
                if pnl_pct >= 500:  # 超过500%收益
                    take_profit = pnl_pct * 0.9  # 回撤10%止盈
                else:
                    take_profit = base_take_profit * 3.0  # 提高200%
            elif trend_data['price_trend'] == 'super_strong' and trend_data['time_trend'] in ['strong_down', 'down']:
                take_profit = pnl_pct * 0.85  # 回撤15%止盈
            elif trend_data['price_trend'] == 'strong':
                if trend_data['time_trend'] in ['strong_up', 'up']:
                    take_profit = base_take_profit * 2.0  # 提高100%
                else:
                    take_profit = pnl_pct * 0.8  # 回撤20%
            elif trend_data['price_trend'] == 'normal':
                if trend_data['time_trend'] in ['strong_up', 'up']:
                    take_profit = base_take_profit * 1.5  # 提高50%
                else:
                    take_profit = base_take_profit * 0.8  # 降低20%
            else:
                take_profit = base_take_profit
            
            if pnl_pct >= take_profit:
                self.logger.warning(
                    f"触发止盈: {pnl_pct:.1f}% >= {take_profit:.1f}% "
                    f"(趋势: 价格={trend_data['price_trend']}, 分时={trend_data['time_trend']})"
                )
                return True
                
            return False
            
        except Exception as e:
            self.logger.error(f"检查止盈条件时出错: {str(e)}")
            return False

    async def _check_trailing_stop(self, position: Dict[str, Any], pnl_pct: float) -> bool:
        """检查移动止损"""
        try:
            trailing_stop = self.risk_limits['option']['stop_loss']['trailing']
            
            # 获取最高收益率
            peak_pnl = position.get('peak_pnl', pnl_pct)
            
            # 更新最高收益率
            if pnl_pct > peak_pnl:
                position['peak_pnl'] = pnl_pct
                return False
            
            # 计算回撤百分比
            drawdown = (pnl_pct - peak_pnl) / peak_pnl * 100 if peak_pnl > 0 else 0
            
            # 根据收益率设置不同的回撤止盈比例
            if peak_pnl >= 500:
                max_drawdown = -10  # 允许10%回撤
            elif peak_pnl >= 200:
                max_drawdown = -15  # 允许15%回撤
            elif peak_pnl >= 100:
                max_drawdown = -20  # 允许20%回撤
            else:
                max_drawdown = -25  # 普通情况允许25%回撤
            
            if drawdown <= max_drawdown:
                self.logger.warning(
                    f"触发移动止损: 从最高点{peak_pnl:.1f}%回撤{-drawdown:.1f}% > {-max_drawdown}%"
                )
                return True
                
            return False
            
        except Exception as e:
            self.logger.error(f"检查移动止损时出错: {str(e)}")
            return False

    async def _check_trend_reversal(self, position: Dict[str, Any], trend_data: Dict[str, str]) -> bool:
        """检查趋势反转"""
        try:
            # 获取之前的趋势
            prev_trend = position.get('prev_trend', trend_data['time_trend'])
            
            # 检查趋势反转
            if prev_trend in ['strong_up', 'up'] and trend_data['time_trend'] in ['strong_down', 'down']:
                # 上涨转下跌
                current_price = float(position.get('current_price', 0))
                cost_price = float(position.get('cost_price', 0))
                pnl_pct = (current_price - cost_price) / cost_price * 100
                
                # 只在有盈利的情况下考虑趋势反转
                if pnl_pct > 20:  # 超过20%收益
                    self.logger.warning(
                        f"趋势反转: {prev_trend} -> {trend_data['time_trend']}, "
                        f"当前收益: {pnl_pct:.1f}%"
                    )
                    return True
            
            # 更新趋势记录
            position['prev_trend'] = trend_data['time_trend']
            return False
            
        except Exception as e:
            self.logger.error(f"检查趋势反转时出错: {str(e)}")
            return False