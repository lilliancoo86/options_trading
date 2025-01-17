"""风险检查模块"""
from typing import Dict, Any, Optional, Tuple
import logging
from datetime import datetime, time
from decimal import Decimal

class RiskChecker:
    def __init__(self, config: Dict[str, Any]):
        """
        初始化风险检查器
        
        Args:
            config: 配置字典，包含风险控制参数
        """
        self.logger = logging.getLogger(__name__)
        
        # 从配置中加载风险控制参数
        self.risk_limits = config.get('risk_limits', {
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
        })
        
        # 记录加载的配置
        self.logger.info("风险控制参数加载完成:")
        self.logger.info(f"期权止损设置: 固定={self.risk_limits['option']['stop_loss']['initial']}%, "
                        f"移动={self.risk_limits['option']['stop_loss']['trailing']}%")
        self.logger.info(f"期权止盈设置: {self.risk_limits['option']['take_profit']}%")
        self.logger.info(f"波动率限制: VIX {self.risk_limits['volatility']['min_vix']}-"
                        f"{self.risk_limits['volatility']['max_vix']}")

    def check_position_risk(self, symbol: str, pnl_pct: float, price_trend: str, time_trend: str) -> Tuple[bool, str]:
        """
        检查持仓风险
        
        Args:
            symbol: 交易标的代码
            pnl_pct: 当前收益率
            price_trend: 价格趋势
            time_trend: 时间趋势
            
        Returns:
            Tuple[bool, str]: (是否需要平仓, 原因)
        """
        try:
            # 固定止损检查
            stop_loss = self.risk_limits['option']['stop_loss']['initial']
            if pnl_pct <= -stop_loss:
                return True, f"触发固定止损: {pnl_pct:.2f}% <= -{stop_loss}%"

            # 移动止损检查
            trailing_stop = self.risk_limits['option']['stop_loss']['trailing']
            if pnl_pct > trailing_stop:
                if price_trend == 'DOWN' and time_trend == 'DOWN':
                    return True, f"触发移动止损: 价格和时间趋势向下，当前收益={pnl_pct:.2f}%"

            # 止盈检查
            take_profit = self.risk_limits['option']['take_profit']
            if pnl_pct >= take_profit:
                return True, f"触发止盈: {pnl_pct:.2f}% >= {take_profit}%"

            return False, "未触发风险控制"

        except Exception as e:
            self.logger.error(f"检查持仓风险时出错: {str(e)}")
            return False, "风险检查出错"

    def check_market_condition(self, vix_level: float, current_time: str) -> bool:
        """
        检查市场条件是否适合交易
        
        Args:
            vix_level: VIX指数水平
            current_time: 当前时间 (格式: "HH:MM:SS")
            
        Returns:
            bool: 是否适合交易
        """
        try:
            # 检查VIX水平
            min_vix = self.risk_limits['volatility']['min_vix']
            max_vix = self.risk_limits['volatility']['max_vix']
            
            if not (min_vix <= vix_level <= max_vix):
                self.logger.warning(f"VIX水平不适合交易: {vix_level:.1f} (限制范围: {min_vix}-{max_vix})")
                return False
            
            # 检查交易时间（避开开盘和收盘前30分钟）
            try:
                current = datetime.strptime(current_time, "%H:%M:%S").time()
                market_open = time(9, 30, 0)
                market_close = time(16, 0, 0)
                avoid_open = time(10, 0, 0)
                avoid_close = time(15, 30, 0)
                
                if current < market_open or current > market_close:
                    self.logger.warning("非交易时段")
                    return False
                    
                if market_open <= current < avoid_open:
                    self.logger.warning("开盘初期，暂不交易")
                    return False
                    
                if avoid_close <= current <= market_close:
                    self.logger.warning("临近收盘，暂不交易")
                    return False
                    
            except ValueError:
                self.logger.error(f"时间格式错误: {current_time}")
                return False
            
            return True
            
        except Exception as e:
            self.logger.error(f"检查市场条件时出错: {str(e)}")
            return False