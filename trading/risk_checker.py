"""
风险检查模块
负责检查持仓风险和市场风险，包括止盈止损管理
"""
import asyncio
import json
import logging
import pytz
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional
import numpy as np

from config.config import DATA_DIR


class RiskChecker:
    # 默认风险限制配置
    DEFAULT_RISK_LIMITS = {
        'stock': {
            'risk_limits': {
                'position': {
                    'max_value': 100000,      # 单个持仓最大市值
                    'max_ratio': 0.2          # 单个持仓占总资产比例
                },
                'loss': {
                    'max_per_trade': 1000,    # 单笔最大亏损
                    'max_daily': 3000,        # 每日最大亏损
                    'max_drawdown': 0.1       # 最大回撤比例
                },
                'leverage': {
                    'max_ratio': 1.5          # 最大杠杆倍数
                },
                'volatility': {
                    'threshold': 0.3          # 波动率阈值
                }
            },
            # 止损止盈配置
            'stop_loss': -0.03,              # 股票止损点（3%）
            'take_profit': 0.05,             # 股票止盈点（5%）
            'trailing_stop': {
                'enabled': True,
                'activation': 0.05,           # 触发追踪止损的收益率（5%）
                'distance': 0.02,             # 追踪止损距离（2%）
                'step': 0.01,                 # 止损位上移步长（1%）
                'min_profit': 0.02            # 最小锁定收益（2%）
            }
        },
        'option': {
            'risk_limits': {
                'position': {
                    'max_value': 50000,       # 期权最大持仓
                    'max_ratio': 0.1          # 期权最大仓位比例
                },
                'loss': {
                    'max_per_trade': 2000,    # 单笔最大亏损
                    'max_daily': 5000,        # 每日最大亏损
                    'max_drawdown': 0.15      # 最大回撤比例
                },
                'leverage': {
                    'max_ratio': 3.0          # 最大杠杆倍数
                },
                'volatility': {
                    'threshold': 0.5          # 波动率阈值
                }
            },
            # 止损止盈配置
            'stop_loss': -0.30,              # 期权止损点（30%）
            'take_profit': 0.50,             # 期权止盈点（50%）
            'trailing_stop': {
                'enabled': True,
                'activation': 0.3,            # 触发追踪止损的收益率（30%）
                'distance': 0.15,             # 追踪止损距离（15%）
                'step': 0.05,                 # 止损位上移步长（5%）
                'min_profit': 0.1             # 最小锁定收益（10%）
            }
        }
    }

    def __init__(self, config: Dict[str, Any], option_strategy, time_checker):
        """初始化风险检查器"""
        self.logger = logging.getLogger(__name__)
        self.option_strategy = option_strategy
        self.time_checker = time_checker
        self.tz = pytz.timezone('America/New_York')
        
        # 加载风险限制配置
        self.risk_limits = self._load_risk_limits(config)
        
        # 初始化止损止盈跟踪器
        self.stop_loss_trackers = {}
        self.historical_risk_data = {}
        
        # 确保风险数据目录存在
        self.risk_data_dir = Path(DATA_DIR) / 'risk_data'
        self.risk_data_dir.mkdir(parents=True, exist_ok=True)
        
        # 风险数据文件路径
        self.risk_data_file = self.risk_data_dir / 'historical_risk.json'

    async def async_init(self):
        """异步初始化"""
        try:
            # 加载历史风险数据
            await self._load_historical_risk_data()
            self.logger.info("风险检查器初始化完成")
        except Exception as e:
            self.logger.error(f"风险检查器初始化失败: {str(e)}")
            raise

    def _load_risk_limits(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """加载风险限制配置"""
        try:
            risk_config = config.get('RISK_CONFIG', {})
            limits = self.DEFAULT_RISK_LIMITS.copy()
            
            # 更新配置
            if risk_config:
                for asset_type in ['stock', 'option']:
                    if asset_type in risk_config:
                        limits[asset_type].update(risk_config[asset_type])
            
            return limits
        except Exception as e:
            self.logger.error(f"加载风险限制配置失败: {str(e)}")
            return self.DEFAULT_RISK_LIMITS

    async def check_risk(self, symbol: str, signal: Dict[str, Any]) -> bool:
        """检查交易信号的风险"""
        try:
            asset_type = signal.get('asset_type', 'stock')
            risk_limits = self.risk_limits[asset_type]['risk_limits']
            
            # 1. 检查交易时间
            if not await self.time_checker.is_trading_time():
                self.logger.warning(f"{symbol} 当前不在交易时间")
                return False

            # 2. 检查止损止盈设置
            if not self._validate_stop_loss_take_profit(signal, asset_type):
                self.logger.warning(f"{symbol} 止损止盈设置无效")
                return False

            # 3. 检查波动率
            if not await self._check_volatility(symbol, asset_type):
                self.logger.warning(f"{symbol} 波动率超过限制")
                return False
            
            # 4. 检查持仓限制
            position_value = signal.get('position_value', 0)
            if position_value > risk_limits['position']['max_value']:
                self.logger.warning(f"{symbol} 持仓规模超过限制")
                return False
            
            # 5. 检查杠杆限制
            if signal.get('leverage', 1.0) > risk_limits['leverage']['max_ratio']:
                self.logger.warning(f"{symbol} 超过杠杆限制")
                return False

            # 6. 检查潜在亏损
            if signal.get('potential_loss', 0) > risk_limits['loss']['max_per_trade']:
                self.logger.warning(f"{symbol} 潜在亏损超过单笔限制")
                return False

            return True

        except Exception as e:
            self.logger.error(f"检查 {symbol} 风险时出错: {str(e)}")
            return False

    def _validate_stop_loss_take_profit(self, signal: Dict[str, Any], asset_type: str) -> bool:
        """验证止损止盈设置"""
        try:
            risk_limits = self.risk_limits[asset_type]
            stop_loss = signal.get('stop_loss_pct')
            take_profit = signal.get('take_profit_pct')
            
            # 检查是否存在止损止盈设置
            if stop_loss is None or take_profit is None:
                self.logger.warning(f"缺少止损止盈设置: stop_loss={stop_loss}, take_profit={take_profit}")
                return False
            
            # 确保止损是负数，止盈是正数
            stop_loss = float(stop_loss)
            take_profit = float(take_profit)
            
            # 检查止损设置（确保是负数且不超过限制）
            if stop_loss >= 0 or abs(stop_loss) > abs(risk_limits['stop_loss']):
                self.logger.warning(f"止损设置无效: {stop_loss}, 限制: {risk_limits['stop_loss']}")
                return False
                
            # 检查止盈设置（确保是正数且达到最小要求）
            if take_profit <= 0 or take_profit < risk_limits['take_profit']:
                self.logger.warning(f"止盈设置无效: {take_profit}, 限制: {risk_limits['take_profit']}")
                return False
                
            return True
            
        except Exception as e:
            self.logger.error(f"验证止损止盈设置时出错: {str(e)}")
            return False

    async def update_trailing_stop(self, symbol: str, current_price: float, position_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """更新追踪止损"""
        try:
            asset_type = position_info.get('asset_type', 'stock')
            trailing_config = self.risk_limits[asset_type]['trailing_stop']
            
            if not trailing_config['enabled']:
                return None
                
            entry_price = position_info.get('avg_cost', 0)
            current_profit = (current_price - entry_price) / entry_price
            
            # 如果未达到激活条件
            if current_profit < trailing_config['activation']:
                return None
                
            # 更新追踪止损价格
            if symbol not in self.stop_loss_trackers:
                self.stop_loss_trackers[symbol] = {
                    'highest_price': current_price,
                    'stop_price': current_price * (1 - trailing_config['distance'])
                }
            else:
                tracker = self.stop_loss_trackers[symbol]
                if current_price > tracker['highest_price']:
                    # 更新最高价和止损价
                    tracker['highest_price'] = current_price
                    tracker['stop_price'] = current_price * (1 - trailing_config['distance'])
                    
            return self.stop_loss_trackers[symbol]
            
        except Exception as e:
            self.logger.error(f"更新 {symbol} 追踪止损时出错: {str(e)}")
            return None

    async def check_trailing_stop_triggered(self, symbol: str, current_price: float) -> bool:
        """检查是否触发追踪止损"""
        try:
            if symbol not in self.stop_loss_trackers:
                return False
                
            tracker = self.stop_loss_trackers[symbol]
            return current_price <= tracker['stop_price']
            
        except Exception as e:
            self.logger.error(f"检查 {symbol} 追踪止损触发时出错: {str(e)}")
            return False

    async def _load_historical_risk_data(self) -> None:
        """加载历史风险数据"""
        try:
            if self.risk_data_file.exists():
                with open(self.risk_data_file, 'r', encoding='utf-8') as f:
                    self.historical_risk_data = json.load(f)
                self.logger.info("已加载历史风险数据")
            else:
                self.historical_risk_data = {
                    'daily_losses': {},
                    'max_drawdown': 0,
                    'total_exposure': 0,
                    'position_records': {},
                    'risk_events': []
                }
                await self._save_historical_risk_data()
                self.logger.info("已创建新的风险数据记录")
                
        except Exception as e:
            self.logger.error(f"加载历史风险数据时出错: {str(e)}")
            # 创建默认的风险数据结构
            self.historical_risk_data = {
                'daily_losses': {},
                'max_drawdown': 0,
                'total_exposure': 0,
                'position_records': {},
                'risk_events': []
            }

    async def _save_historical_risk_data(self) -> None:
        """保存历史风险数据"""
        try:
            with open(self.risk_data_file, 'w', encoding='utf-8') as f:
                json.dump(self.historical_risk_data, f, indent=4)
            self.logger.debug("已保存风险数据")
        except Exception as e:
            self.logger.error(f"保存风险数据时出错: {str(e)}")

    async def record_risk_event(self, symbol: str, event_type: str, details: Dict[str, Any]) -> None:
        """记录风险事件"""
        try:
            current_time = datetime.now(self.tz).strftime('%Y-%m-%d %H:%M:%S')
            event = {
                'timestamp': current_time,
                'symbol': symbol,
                'type': event_type,
                'details': details
            }
            self.historical_risk_data['risk_events'].append(event)
            await self._save_historical_risk_data()
            
        except Exception as e:
            self.logger.error(f"记录风险事件时出错: {str(e)}")

    async def update_daily_loss(self, loss_amount: float) -> None:
        """更新每日亏损记录"""
        try:
            current_date = datetime.now(self.tz).strftime('%Y-%m-%d')
            daily_losses = self.historical_risk_data['daily_losses']
            
            if current_date not in daily_losses:
                daily_losses[current_date] = 0
            
            daily_losses[current_date] += loss_amount
            await self._save_historical_risk_data()
            
        except Exception as e:
            self.logger.error(f"更新每日亏损记录时出错: {str(e)}")

    async def update_position_record(self, symbol: str, position_info: Dict[str, Any]) -> None:
        """更新持仓记录"""
        try:
            current_time = datetime.now(self.tz).strftime('%Y-%m-%d %H:%M:%S')
            if symbol not in self.historical_risk_data['position_records']:
                self.historical_risk_data['position_records'][symbol] = []
            
            position_record = {
                'timestamp': current_time,
                'quantity': position_info.get('quantity', 0),
                'avg_cost': position_info.get('avg_cost', 0),
                'market_value': position_info.get('market_value', 0),
                'unrealized_pnl': position_info.get('unrealized_pnl', 0)
            }
            
            self.historical_risk_data['position_records'][symbol].append(position_record)
            await self._save_historical_risk_data()
            
        except Exception as e:
            self.logger.error(f"更新持仓记录时出错: {str(e)}")

    async def cleanup(self):
        """清理资源"""
        try:
            # 保存最新的风险数据
            await self._save_historical_risk_data()
            self.logger.info("风险检查器清理完成")
        except Exception as e:
            self.logger.error(f"清理风险检查器时出错: {str(e)}")
