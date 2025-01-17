"""
风险检查模块
负责检查持仓风险和市场风险
"""
from typing import Dict, Any
import logging
from datetime import datetime
import pytz

class RiskChecker:
    def __init__(self, config: Dict[str, Any]):
        self.logger = logging.getLogger(__name__)
        self.config = config
        self.tz = pytz.timezone('America/New_York')
        
        # 简化风险控制参数
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
        
        # 添加收盘平仓设置
        self.market_close = {
            'force_close_time': '15:45',  # 收盘前15分钟强制平仓
            'warning_time': '15:40'       # 收盘前20分钟发出警告
        }

    async def check_risk(self, position: Dict[str, Any]) -> bool:
        """检查风险"""
        try:
            # 首先检查是否需要收盘平仓
            current_time = datetime.now(self.tz).strftime('%H:%M')
            
            # 收盘前警告
            if current_time >= self.market_close['warning_time']:
                self.logger.warning(f"接近收盘时间，准备平仓: {position['symbol']}")
            
            # 强制平仓检查
            if current_time >= self.market_close['force_close_time']:
                self.logger.warning(
                    f"收盘前强制平仓:\n"
                    f"  标的: {position['symbol']}\n"
                    f"  当前时间: {current_time}\n"
                    f"  平仓类型: 收盘平仓"
                )
                return True
            
            # 检查止盈止损
            current_price = float(position.get('current_price', 0))
            cost_price = float(position.get('cost_price', 0))
            if cost_price == 0:
                return False
                
            pnl_pct = (current_price - cost_price) / cost_price * 100
            
            # 区分期权和股票
            is_option = self._is_option(position['symbol'])
            limits = self.risk_limits['option'] if is_option else self.risk_limits['stock']
            
            # 检查止损
            if limits['stop_loss'] is not None and pnl_pct <= limits['stop_loss']:
                self.logger.warning(
                    f"触发止损:\n"
                    f"  标的: {position['symbol']}\n"
                    f"  当前亏损: {pnl_pct:.1f}%\n"
                    f"  止损线: {limits['stop_loss']}%"
                )
                return True
            
            # 检查止盈（仅股票）
            if not is_option and limits['take_profit'] is not None and pnl_pct >= limits['take_profit']:
                self.logger.warning(
                    f"触发止盈:\n"
                    f"  标的: {position['symbol']}\n"
                    f"  当前收益: {pnl_pct:.1f}%\n"
                    f"  止盈线: {limits['take_profit']}%"
                )
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"检查风险时出错: {str(e)}")
            self.logger.exception("详细错误信息:")
            return False

    def _is_option(self, symbol: str) -> bool:
        """检查是否为期权"""
        return any(x in symbol for x in ['C', 'P'])

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