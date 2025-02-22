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
from typing import Dict, Any, Tuple, List
import numpy as np

from config.config import (
    DATA_DIR
)


class RiskChecker:
    # 默认风险限制配置
    DEFAULT_RISK_LIMITS = {
        'option': {
            # 止损止盈配置
            'stop_loss': -0.3,         # 期权止损点
            'take_profit': 0.5,        # 期权止盈点
            'trailing_stop': {
                'enabled': True,
                'activation': 0.3,      # 触发追踪止损的收益率
                'distance': 0.15,       # 追踪止损距离
                'step': 0.05,          # 止损位上移步长
                'min_profit': 0.1      # 最小锁定收益
            },
            'profit_targets': [         # 分批止盈目标
                {'target': 0.5, 'ratio': 0.4},  # 盈利50%时平掉40%
                {'target': 0.8, 'ratio': 0.3}   # 盈利80%时平掉剩余
            ],
            
            # 期权交易策略配置
            'strategy': 'call_only',    # 只买入看涨期权
            'contract_selection': {
                'min_volume': 100,           # 最小成交量
                'min_open_interest': 500,    # 最小持仓量
                'delta_range': (0.3, 0.7),   # Delta范围
                'min_days': 3,               # 最短到期时间
                'max_days': 30,              # 最长到期时间
                'iv_percentile': 50          # IV百分位阈值
            },
            
            'risk_limits': {
                'loss': {
                    'max_per_trade': 500,     # 单笔最大亏损
                    'max_daily': 1000,        # 每日最大亏损
                    'max_drawdown': 0.1,      # 最大回撤
                },
                'leverage': {
                    'max_ratio': 2.0,         # 最大杠杆率
                    'max_margin': 0.5,        # 最大保证金率
                },
                'volatility': {
                    'threshold': 0.4,         # 波动率阈值
                    'lookback_days': 20,      # 波动率计算周期
                },
                'position': {
                    'max_count': 5,           # 最大持仓数量
                    'max_value': 10000,       # 单个标的最大持仓金额
                    'max_ratio': 0.2,         # 总持仓市值占比
                    'initial_size': 5000,     # 初始建仓金额
                    'min_size': 5000,         # 最小持仓规模
                    'increment': 2500         # 持仓规模递增单位
                },
                'greeks': {                   # 期权特有的希腊字母限制
                    'max_delta': 100,         # Delta上限
                    'max_gamma': 10,          # Gamma上限
                    'max_theta': -500,        # Theta下限
                    'max_vega': 1000          # Vega上限
                }
            }
        },
        'stock': {
            'stop_loss': -0.03,              # 股票固定3%止损
            'take_profit': 0.05,             # 股票固定5%止盈
            'risk_limits': {
                'loss': {
                    'max_per_trade': 300,     # 单笔最大亏损
                    'max_daily': 800,         # 每日最大亏损
                    'max_drawdown': 0.1,      # 最大回撤
                },
                'leverage': {
                    'max_ratio': 1.5,         # 最大杠杆率
                    'max_margin': 0.4,        # 最大保证金率
                },
                'volatility': {
                    'threshold': 0.3,         # 波动率阈值
                    'lookback_days': 20,      # 波动率计算周期
                },
                'position': {
                    'max_count': 10,          # 最大持仓数量
                    'max_value': 50000,       # 单个标的最大持仓金额
                    'max_ratio': 0.3,         # 总持仓市值占比
                    'initial_size': 10000,    # 初始建仓金额
                    'min_size': 5000,         # 最小持仓规模
                    'increment': 5000         # 持仓规模递增单位
                }
            }
        },
        'market': {
            'risk_limits': {
                'loss': {
                    'max_drawdown': 0.15,    # 市场最大回撤
                },
                'leverage': {
                    'max_ratio': 2.0,        # 市场最大杠杆率
                    'max_margin': 0.5,       # 市场最大保证金率
                },
                'volatility': {
                    'threshold': 0.4,        # 市场波动率阈值
                    'lookback_days': 20,     # 波动率计算周期
                },
                'position': {
                    'max_count': 10,         # 总持仓数量限制
                    'max_value': 100000,     # 单个标的最大持仓市值
                    'max_ratio': 0.8,        # 最大账户资金使用比例
                    'min_size': 5000,        # 最小持仓规模
                    'increment': 5000,       # 持仓规模递增单位
                }
            }
        },
        'portfolio': {
            'risk_limits': {
                'loss': {
                    'max_daily': 2000,       # 每日最大亏损
                    'max_drawdown': 0.2,     # 组合最大回撤
                },
                'leverage': {
                    'max_margin': 0.5,       # 最大保证金率
                },
                'position': {
                    'max_count': 5,          # 最大持仓数量
                    'max_ratio': 0.9,        # 最大账户资金使用比例
                    'max_concentration': 0.3, # 最大持仓集中度
                },
                'greeks': {                  # 希腊字母限制改名为greeks
                    'max_delta': 100,        # Delta上限
                    'max_gamma': 10,         # Gamma上限
                    'max_theta': -500,       # Theta下限
                    'max_vega': 1000         # Vega上限
                }
            }
        }
    }

    def __init__(self, config: Dict[str, Any], option_strategy, time_checker) -> None:
        """
        初始化风险检查器
        
        Args:
            config: 配置信息
            option_strategy: 期权策略实例，用于检查趋势和获取账户信息
            time_checker: 时间检查器实例，用于检查交易时间
        """
        self.logger = logging.getLogger(__name__)
        self.tz = pytz.timezone('America/New_York')
        
        # 保存配置
        self.config = config
        
        # 使用默认配置,不再从config中覆盖
        self.risk_limits = self.DEFAULT_RISK_LIMITS.copy()
        
        # 保存依赖的实例
        self.option_strategy = option_strategy
        self.time_checker = time_checker
        
        # 持仓记录
        self.position_records = {}
        
        # 风险状态记录
        self.risk_status = {
            'current_drawdown': 0.0,
            'daily_loss': 0.0,
            'position_values': {},
            'greek_exposures': {
                'delta': 0.0,
                'gamma': 0.0,
                'theta': 0.0,
                'vega': 0.0
            }
        }
        
        # 风险记录目录
        self.risk_dir = Path(DATA_DIR) / 'risk_records'
        self.risk_dir.mkdir(parents=True, exist_ok=True)

    def _merge_risk_limits(self, default_limits: Dict, custom_limits: Dict) -> Dict:
        """合并默认风险限制和自定义风险限制"""
        merged = default_limits.copy()
        for category in custom_limits:
            if category in merged:
                merged[category].update(custom_limits[category])
            else:
                merged[category] = custom_limits[category]
        return merged

    async def check_position_risk(self, position: Dict[str, Any], market_data: Dict[str, Any]) -> Tuple[bool, str, float]:
        """检查持仓风险"""
        try:
            symbol = position.get('symbol', '')
            if not symbol:
                return False, "持仓信息不完整", 0
                
            # 检查是否是期权
            is_option = '.US' in symbol and any(x in symbol for x in ['C', 'P'])
            if not is_option:
                return False, "不是期权持仓", 0
                
            # 获取价格信息
            cost_price = float(position.get('cost_price', 0))
            current_price = float(position.get('current_price', 0))
            if not (cost_price and current_price):
                return False, "价格信息不完整", 0
                
            # 计算盈亏率
            profit_rate = (current_price - cost_price) / cost_price
            
            # 检查希腊字母风险
            greeks_risk, greeks_msg = await self.check_greeks_risk(position)
            if greeks_risk:
                return True, greeks_msg, 1.0
            
            # 检查市场风险
            market_risk, market_msg, risk_level = await self.check_market_risk(symbol, market_data)
            if market_risk:
                return True, market_msg, risk_level
            
            # 检查持仓规模风险
            size_risk, size_msg, size_ratio = await self._check_position_size(position)
            if size_risk:
                return True, size_msg, size_ratio
            
            return False, "", 0
            
        except Exception as e:
            self.logger.error(f"检查持仓风险时出错: {str(e)}")
            return False, f"检查出错: {str(e)}", 0

    async def _check_trend(self, market_data: Dict[str, Any]) -> bool:
        """检查趋势是否良好"""
        try:
            # 获取K线数据
            df = await self.option_strategy._stock_klines(market_data['symbol'])
            if df is None:
                return False
                
            # 分析技术指标
            analysis = await self.option_strategy.analyze_stock_trend(df)
            if analysis is None:
                return False
                
            # 使用final_signal判断趋势
            # 只有上涨趋势才继续持有
            return analysis['signal'] > 0.3
            
        except Exception as e:
            self.logger.error(f"检查趋势时出错: {str(e)}")
            return False

    async def calculate_atr(self, symbol: str, klines: List[Dict]) -> float:
        """计算ATR"""
        try:
            current_time = datetime.now(self.tz)
            
            # 检查缓存是否有效（1分钟内）
            if (self._atr_cache['time'] and 
                (current_time - self._atr_cache['time']).total_seconds() < 60 and
                symbol in self._atr_cache['data']):
                return self._atr_cache['data'][symbol]
            
            if len(klines) < self.atr_config['min_periods']:
                return 0.0
                
            tr_list = []
            for i in range(1, len(klines)):
                high = float(klines[i]['high'])
                low = float(klines[i]['low'])
                prev_close = float(klines[i-1]['close'])
                
                tr = max(
                    high - low,
                    abs(high - prev_close),
                    abs(low - prev_close)
                )
                tr_list.append(tr)
            
            # 计算ATR
            atr = sum(tr_list[-self.atr_config['period']:]) / len(tr_list[-self.atr_config['period']:])
            
            # 更新缓存
            self._atr_cache['time'] = current_time
            self._atr_cache['data'][symbol] = atr
            
            return atr
            
        except Exception as e:
            self.logger.error(f"计算ATR时出错: {str(e)}")
            return 0.0

    async def check_intraday_position(self, position: Dict[str, Any], 
                                    market_data: Dict[str, Any]) -> Tuple[bool, str, float]:
        """检查日内持仓"""
        try:
            symbol = position['symbol']
            current_price = float(position['current_price'])
            
            # 获取1分钟K线数据
            klines = await self.quote_ctx.get_candlestick(
                symbol=symbol,
                period="1m",
                count=self.atr_config['period']
            )
            
            if not klines:
                return False, "", 0.0
                
            # 计算ATR
            atr = await self.calculate_atr(symbol, klines)
            if atr == 0:
                return False, "", 0.0
                
            # 获取当前分钟的高低点
            current_high = float(klines[-1]['high'])
            current_low = float(klines[-1]['low'])
            
            # 检查是否触及高点ATR
            if current_price >= current_high + (atr * self.atr_config['intraday']['high_threshold']):
                return True, "ATR高点止盈", 1.0  # 全部止盈
                
            # 检查是否触及低点ATR且有足够利润
            if (current_price <= current_low - (atr * self.atr_config['intraday']['low_threshold']) and
                self._has_sufficient_profit(position)):
                return True, "ATR低点建仓", 0.25  # 建仓1/4仓位
                
            return False, "", 0.0
            
        except Exception as e:
            self.logger.error(f"检查日内持仓时出错: {str(e)}")
            return False, "", 0.0

    def _has_sufficient_profit(self, position: Dict[str, Any]) -> bool:
        """检查是否有足够利润"""
        try:
            cost_price = float(position['cost_price'])
            current_price = float(position['current_price'])
            pnl_pct = (current_price - cost_price) / cost_price * 100
            
            return pnl_pct >= self.trailing_stop['min_profit']
            
        except Exception as e:
            self.logger.error(f"检查利润时出错: {str(e)}")
            return False

    def _check_stop_loss_take_profit(self, position: Dict[str, Any]) -> bool:
        """
        检查止损止盈
        
        Args:
            position: 持仓信息字典
            
        Returns:
            bool: 是否需要平仓
        """
        try:
            # 获取持仓信息
            symbol = position.get('symbol', '')
            cost_price = float(position.get('cost_price', 0))
            current_price = float(position.get('current_price', 0))
            position_type = position.get('type', '')
            
            if not (cost_price and current_price):
                return False
            
            # 计算收益率
            pnl_pct = (current_price - cost_price) / cost_price * 100
            
            # 获取风险限制
            risk_limits = self.config.get('risk_limits', {}).get(position_type, {})
            stop_loss = risk_limits.get('stop_loss')
            take_profit = risk_limits.get('take_profit')
            
            # 检查止损
            if stop_loss is not None and pnl_pct <= stop_loss:
                self.logger.warning(
                    f"触发止损: {symbol} "
                    f"收益率 {pnl_pct:.2f}% <= {stop_loss}%"
                )
                return True
            
            # 检查止盈
            if take_profit is not None and pnl_pct >= take_profit:
                self.logger.warning(
                    f"触发止盈: {symbol} "
                    f"收益率 {pnl_pct:.2f}% >= {take_profit}%"
                )
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"检查止损止盈时出错: {str(e)}")
            return False

    def _check_volatility_risk(self, market_data: Dict[str, Any]) -> bool:
        """
        检查波动率风险
        
        Args:
            market_data: 市场数据字典
            
        Returns:
            bool: 是否需要平仓
        """
        try:
            # 获取市场数据
            volatility = market_data.get('volatility', 0)
            vix = market_data.get('vix', 0)
            
            # 获取风险限制
            market_limits = self.config.get('risk_limits', {}).get('market', {})
            volatility_threshold = market_limits.get('volatility', {}).get('threshold', 0.4)
            
            # 检查波动率
            if volatility > volatility_threshold:
                self.logger.warning(
                    f"市场波动率过高: {volatility:.2f} > {volatility_threshold}"
                )
                return True
            
            # 检查VIX
            if vix > 35:  # VIX超过35表示市场恐慌
                self.logger.warning(f"VIX指数过高: {vix:.2f}")
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"检查波动率风险时出错: {str(e)}")
            return False

    def _check_position_size_risk(self, position: Dict[str, Any]) -> bool:
        """
        检查持仓规模风险
        
        Args:
            position: 持仓信息字典
            
        Returns:
            bool: 是否需要平仓
        """
        try:
            # 获取持仓信息
            market_value = float(position.get('market_value', 0))
            position_type = position.get('type', '')
            
            # 获取风险限制
            market_limits = self.config.get('risk_limits', {}).get('market', {})
            max_position_value = market_limits.get('position', {}).get('max_value', 100000)
            
            # 检查单个持仓规模
            if market_value > max_position_value:
                self.logger.warning(
                    f"持仓规模过大: {position.get('symbol')} "
                    f"市值 {market_value:.2f} > {max_position_value}"
                )
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"检查持仓规模风险时出错: {str(e)}")
            return False

    def _check_delta_risk(self, position: Dict[str, Any]) -> bool:
        """
        检查Delta风险
        
        Args:
            position: 持仓信息字典
            
        Returns:
            bool: 是否需要平仓
        """
        try:
            if position.get('type') != 'option':
                return False
                
            # 获取期权Delta
            delta = abs(float(position.get('delta', 0)))
            
            # 检查Delta是否过大
            if delta > 0.8:
                self.logger.warning(
                    f"期权Delta过大: {position.get('symbol')} "
                    f"Delta = {delta:.2f}"
                )
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"检查Delta风险时出错: {str(e)}")
            return False

    def _check_theta_risk(self, position: Dict[str, Any]) -> bool:
        """
        检查Theta风险
        
        Args:
            position: 持仓信息字典
            
        Returns:
            bool: 是否需要平仓
        """
        try:
            if position.get('type') != 'option':
                return False
                
            # 获取期权Theta
            theta = abs(float(position.get('theta', 0)))
            market_value = float(position.get('market_value', 0))
            
            # 计算Theta占比
            if market_value > 0:
                theta_ratio = (theta * 100) / market_value  # 转换为百分比
                
                # 如果每日时间衰减超过持仓价值的2%，考虑平仓
                if theta_ratio > 2:
                    self.logger.warning(
                        f"期权Theta过大: {position.get('symbol')} "
                        f"每日衰减 {theta_ratio:.2f}%"
                    )
                    return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"检查Theta风险时出错: {str(e)}")
            return False

    async def _record_risk_status(self, position: Dict[str, Any]) -> None:
        """记录风险状态"""
        try:
            # 确保 position 是字典类型
            if not isinstance(position, dict):
                self.logger.error(f"持仓数据类型错误: 期望 dict, 实际是 {type(position)}")
                return

            # 获取基础数据，使用 get 方法提供默认值
            market_value = float(position.get('market_value', 0))
            # 使用 volume 替代 quantity
            quantity = float(position.get('volume', 0))  # 修改这里，使用 volume 而不是 quantity
            cost_price = float(position.get('cost_price', 0))
            current_price = float(position.get('current_price', 0))
            
            # 计算保证金比例
            if market_value > 0:
                margin = position.get('margin', {})
                if isinstance(margin, dict):
                    initial_margin = float(margin.get('initial', 0))
                    self.risk_status['margin_ratio'] = initial_margin / market_value
            
            # 计算盈亏比例
            cost_basis = quantity * cost_price
            if cost_basis > 0:
                profit_loss = (market_value - cost_basis) / cost_basis
                self.risk_status['daily_loss'] = profit_loss
            
            # 记录检查时间
            self.risk_status['last_check'] = datetime.now(self.tz)
            
            # 记录波动率
            self.risk_status['volatility'] = float(position.get('volatility', 0.0))
            
        except Exception as e:
            self.logger.error(f"记录风险状态时出错: {str(e)}")

    def log_risk_status(self, position: Dict[str, Any]):
        """记录风险状态"""
        try:
            if not position:
                return
                
            # 使用 get 方法安全获取数据，提供默认值
            market_value = float(position.get('market_value', 0))
            volume = float(position.get('volume', 0))
            cost_price = float(position.get('cost_price', 0))
            
            # 计算保证金比例
            if market_value > 0:
                margin = position.get('margin', {})
                if isinstance(margin, dict):
                    initial_margin = float(margin.get('initial', 0))
                    self.risk_status['margin_ratio'] = initial_margin / market_value
            
            # 计算盈亏比例
            cost_basis = volume * cost_price
            if cost_basis > 0:
                profit_loss = (market_value - cost_basis) / cost_basis
                self.risk_status['daily_loss'] = profit_loss
            
            # 记录检查时间
            self.risk_status['last_check'] = datetime.now(self.tz)
            
        except KeyError as e:
            self.logger.error(f"记录风险状态时缺少必要字段: {str(e)}")
        except ValueError as e:
            self.logger.error(f"记录风险状态时数据格式错误: {str(e)}")
        except Exception as e:
            self.logger.error(f"记录风险状态时出错: {str(e)}")

    def _get_risk_level(self, unrealized_pl_rate: float) -> str:
        """获取风险等级"""
        if unrealized_pl_rate <= self.risk_limits['option']['stop_loss']:
            return "高风险 - 已触及止损线"
        elif unrealized_pl_rate <= -5:
            return "中等风险 - 接近止损线"
        elif unrealized_pl_rate >= 15:
            return "低风险 - 已有较好盈利"
        else:
            return "正常"

    def _is_option(self, symbol: str) -> bool:
        """检查是否为期权"""
        return any(x in symbol for x in ['C', 'P'])

    def check_new_position_risk(self, symbol: str, price: float, volume: int) -> Tuple[bool, str]:
        """检查新开仓位的风险"""
        try:
            # 计算持仓价值
            position_value = price * volume
            
            # 检查单个持仓限额
            if position_value > self.risk_limits['market']['position']['max_value']:
                self.logger.warning(
                    f"超过单个持仓限额:\n"
                    f"  标的: {symbol}\n"
                    f"  持仓价值: ${position_value:.2f}\n"
                    f"  限额: ${self.risk_limits['market']['position']['max_value']}"
                )
                return True, "超过持仓限额"
            
            # 检查总持仓限额
            total_value = self.risk_stats['total_exposure'] + position_value
            if total_value > self.risk_limits['market']['position']['max_value']:
                self.logger.warning(
                    f"超过总持仓限额:\n"
                    f"  当前总持仓: ${self.risk_stats['total_exposure']:.2f}\n"
                    f"  新增持仓: ${position_value:.2f}\n"
                    f"  限额: ${self.risk_limits['market']['position']['max_value']}"
                )
                return True, "超过总持仓限额"
            
            # 检查持仓数量限制
            if self.risk_stats['total_positions'] >= self.risk_limits['market']['position']['max_count']:
                self.logger.warning(f"超过最大持仓数量限制: {self.risk_stats['total_positions']}")
                return True, "超过持仓数量限制"
            
            return False, ""
            
        except Exception as e:
            self.logger.error(f"检查新开仓位风险时出错: {str(e)}")
            return False, ""

    async def async_init(self):
        """异步初始化"""
        try:
            # 不需要重新创建 time_checker，因为已经在 __init__ 中传入
            self.logger.info("风险检查器初始化完成")
            return self
        except Exception as e:
            self.logger.error(f"风险检查器初始化失败: {str(e)}")
            raise

    async def close(self):
        """关闭风险检查器"""
        try:
            self.logger.info("风险检查器已关闭")
        except Exception as e:
            self.logger.error(f"关闭风险检查器时出错: {str(e)}")

    async def _check_position_size(self, position: Dict[str, Any]) -> Tuple[bool, str, float]:
        """检查持仓规模"""
        try:
            if not isinstance(position, dict):
                raise ValueError(f"持仓数据类型错误: 期望 dict, 实际是 {type(position)}")
            
            market_value = float(position.get('market_value', 0))
            max_value = self.risk_limits['market']['position']['max_value']
            
            if market_value > max_value:
                return True, "持仓规模超过限制", 0.5
            
            return False, "", 0.0
            
        except Exception as e:
            self.logger.error(f"检查持仓规模时出错: {str(e)}")
            return False, "", 0.0

    async def _get_total_position_value(self) -> float:
        """获取当日所有持仓的总市值"""
        try:
            if not self.option_strategy:
                return 0.0
                
            positions = await self.option_strategy.get_positions()
            total_value = sum(
                float(pos.get('market_value', 0))
                for pos in positions
                if self._is_today_position(pos)
            )
            return total_value
        except Exception as e:
            self.logger.error(f"获取总持仓市值时出错: {str(e)}")
            return 0.0

    async def _get_account_value(self) -> float:
        """获取账户总资产"""
        try:
            if not self.option_strategy:
                return 0.0
                
            account_info = await self.option_strategy.get_account_info()
            return float(account_info.get('total_assets', 0))
        except Exception as e:
            self.logger.error(f"获取账户资产时出错: {str(e)}")
            return 0.0

    def _is_today_position(self, position: Dict[str, Any]) -> bool:
        """检查是否是当日持仓"""
        try:
            if 'open_time' not in position:
                return False
            
            open_time = datetime.fromtimestamp(position['open_time'], self.tz)
            now = datetime.now(self.tz)
            
            return open_time.date() == now.date()
        except Exception as e:
            self.logger.error(f"检查当日持仓时出错: {str(e)}")
            return False

    async def check_market_risk(self, symbol: str, market_data: Dict[str, Any]) -> Tuple[bool, str, float]:
        """检查市场风险"""
        try:
            market_limits = self.risk_limits['market']['risk_limits']
            
            # 1. 检查持仓数量限制
            positions = await self.option_strategy.get_positions()
            if len(positions) >= self.risk_limits['market']['position']['max_count']:
                return True, f"超过最大持仓数量限制 ({self.risk_limits['market']['position']['max_count']})", 1.0
            
            # 2. 检查保证金率
            account_info = await self.option_strategy.get_account_info()
            margin_ratio = float(account_info.get('margin_ratio', 0))
            if margin_ratio > market_limits['leverage']['max_ratio']:
                return True, f"超过最大杠杆率限制 ({market_limits['leverage']['max_ratio']*100:.0f}%)", 1.0
            
            # 3. 检查波动率
            if market_data and market_data.get('volatility', 0) > market_limits['volatility']['threshold']:
                return True, "市场波动率过高", 0.8
                
            return False, "", 0.0
            
        except Exception as e:
            self.logger.error(f"检查市场风险时出错: {str(e)}")
            return False, f"检查出错: {str(e)}", 0.0

    async def check_greeks_risk(self, position: Dict[str, Any]) -> Tuple[bool, str]:
        """检查期权希腊字母风险"""
        try:
            if 'greeks' not in position:
                return False, "无希腊字母数据"
                
            greeks = position['greeks']
            
            # 检查Delta中性
            if abs(greeks.get('delta', 0)) > 0.7:
                return True, f"Delta过高: {greeks['delta']:.2f}"
                
            # 检查Gamma风险
            if abs(greeks.get('gamma', 0)) > 0.1:
                return True, f"Gamma过高: {greeks['gamma']:.2f}"
                
            # 检查Theta衰减
            if greeks.get('theta', 0) < -50:
                return True, f"Theta衰减过快: {greeks['theta']:.2f}"
                
            # 检查Vega敏感度
            if abs(greeks.get('vega', 0)) > 50:
                return True, f"Vega敏感度过高: {greeks['vega']:.2f}"
                
            return False, ""
            
        except Exception as e:
            self.logger.error(f"检查希腊字母风险时出错: {str(e)}")
            return False, f"检查出错: {str(e)}"

    async def monitor_risk_status(self):
        """监控风险状态"""
        while True:
            try:
                # 获取当前持仓
                positions = await self.option_strategy.get_positions()
                
                # 检查整体风险
                total_risk = 0
                risk_messages = []
                
                for position in positions:
                    # 获取市场数据
                    market_data = await self.option_strategy.get_market_data(position['symbol'])
                    
                    # 检查持仓风险
                    has_risk, msg, risk_level = await self.check_position_risk(position, market_data)
                    if has_risk:
                        risk_messages.append(f"{position['symbol']}: {msg}")
                        total_risk += risk_level
                
                # 记录风险状态
                if risk_messages:
                    self.logger.warning(
                        "检测到风险:\n" + 
                        "\n".join(f"- {msg}" for msg in risk_messages)
                    )
                
                await asyncio.sleep(60)  # 每分钟检查一次
                
            except Exception as e:
                self.logger.error(f"风险监控出错: {str(e)}")
                await asyncio.sleep(60)

    async def check_all_risks(self, positions: List[Dict[str, Any]]) -> Tuple[bool, str]:
        """检查所有风险指标"""
        try:
            # 更新风险状态
            await self._update_risk_status(positions)
            
            # 检查市场风险
            market_safe, market_msg = await self._check_market_risks()
            if not market_safe:
                return False, f"市场风险: {market_msg}"
                
            # 检查期权风险
            option_safe, option_msg = await self._check_option_risks(positions)
            if not option_safe:
                return False, f"期权风险: {option_msg}"
                
            # 检查组合风险
            portfolio_safe, portfolio_msg = await self._check_portfolio_risks()
            if not portfolio_safe:
                return False, f"组合风险: {portfolio_msg}"
                
            return True, "风险检查通过"
            
        except Exception as e:
            self.logger.error(f"检查风险时出错: {str(e)}")
            return False, f"风险检查出错: {str(e)}"
            
    async def _update_risk_status(self, positions: List[Dict[str, Any]]) -> None:
        """更新风险状态"""
        try:
            # 更新持仓市值
            self.risk_status['position_values'] = {
                pos['symbol']: float(pos.get('market_value', 0))
                for pos in positions
            }
            
            # 更新希腊字母敞口
            total_greeks = {'delta': 0.0, 'gamma': 0.0, 'theta': 0.0, 'vega': 0.0}
            for pos in positions:
                for greek in total_greeks:
                    total_greeks[greek] += float(pos.get(greek, 0))
            self.risk_status['greek_exposures'] = total_greeks
            
            # 记录风险状态
            await self._save_risk_status()
            
        except Exception as e:
            self.logger.error(f"更新风险状态时出错: {str(e)}")
            
    async def _save_risk_status(self) -> None:
        """保存风险状态到文件"""
        try:
            current_date = datetime.now(self.tz).strftime('%Y%m%d')
            status_file = self.risk_dir / f"risk_status_{current_date}.json"
            
            with open(status_file, 'w') as f:
                json.dump(self.risk_status, f, indent=4)
                
        except Exception as e:
            self.logger.error(f"保存风险状态时出错: {str(e)}")
            
    async def _check_market_risks(self) -> Tuple[bool, str]:
        """检查市场风险"""
        try:
            limits = self.risk_limits['market']
            
            # 检查回撤
            if self.risk_status['current_drawdown'] > limits['loss']['max_drawdown']:
                return False, f"回撤超过限制: {self.risk_status['current_drawdown']:.2%}"
                
            # 检查波动率
            # 这里需要实现具体的波动率计算逻辑
            
            return True, ""
            
        except Exception as e:
            self.logger.error(f"检查市场风险时出错: {str(e)}")
            return False, str(e)
            
    async def _check_option_risks(self, positions: List[Dict[str, Any]]) -> Tuple[bool, str]:
        """检查期权风险"""
        try:
            limits = self.risk_limits['option']
            greeks = self.risk_status['greek_exposures']
            
            # 检查Theta
            if greeks['theta'] < limits['risk_limits']['max_theta']:
                return False, f"Theta风险过高: {greeks['theta']}"
                
            # 检查Gamma
            if abs(greeks['gamma']) > limits['risk_limits']['max_gamma']:
                return False, f"Gamma风险过高: {greeks['gamma']}"
                
            # 检查Vega
            if abs(greeks['vega']) > limits['risk_limits']['max_vega']:
                return False, f"Vega风险过高: {greeks['vega']}"
                
            return True, ""
            
        except Exception as e:
            self.logger.error(f"检查期权风险时出错: {str(e)}")
            return False, str(e)
            
    async def _check_portfolio_risks(self) -> Tuple[bool, str]:
        """检查组合风险"""
        try:
            limits = self.risk_limits['portfolio']
            
            # 检查保证金比例
            margin_ratio = await self._calculate_margin_ratio()
            if margin_ratio > limits['max_margin_ratio']:
                return False, f"保证金比例过高: {margin_ratio:.2%}"
                
            # 检查持仓集中度
            concentration = await self._calculate_concentration()
            if concentration > limits['max_concentration']:
                return False, f"持仓过于集中: {concentration:.2%}"
                
            # 检查日内亏损
            if self.risk_status['daily_loss'] > limits['max_daily_loss']:
                return False, f"日内亏损过大: {self.risk_status['daily_loss']}"
                
            return True, ""
            
        except Exception as e:
            self.logger.error(f"检查组合风险时出错: {str(e)}")
            return False, str(e)

    async def check_risk(self, symbol: str, signal: Dict[str, Any]) -> bool:
        """检查交易信号的风险"""
        try:
            asset_type = signal.get('asset_type', 'stock')
            risk_limits = self.risk_limits[asset_type]['risk_limits']
            
            # 检查单笔亏损限制
            if signal.get('potential_loss', 0) > risk_limits['loss']['max_per_trade']:
                self.logger.warning(f"{symbol} 潜在亏损超过单笔限制")
                return False
            
            # 检查持仓限制
            position_value = signal.get('position_value', 0)
            if position_value > risk_limits['position']['max_value']:
                self.logger.warning(f"{symbol} 持仓规模超过限制")
                return False
            
            # 检查杠杆限制
            if signal.get('leverage', 1.0) > risk_limits['leverage']['max_ratio']:
                self.logger.warning(f"{symbol} 超过杠杆限制")
                return False

            # 1. 检查交易时间
            if not await self.time_checker.is_trading_time():
                self.logger.warning(f"{symbol} 当前不在交易时间")
                return False

            # 2. 获取投资组合状态
            portfolio_status = await self.option_strategy.get_portfolio_status()
            
            # 3. 检查回撤限制
            if portfolio_status['total_unrealized_pnl'] < 0:
                drawdown = abs(portfolio_status['total_unrealized_pnl']) / portfolio_status['total_market_value']
                if drawdown > risk_limits['loss']['max_drawdown']:
                    self.logger.warning(f"{symbol} 超过最大回撤限制: {drawdown:.2%}")
                    return False

            # 4. 检查止损止盈设置
            if not self._validate_stop_loss_take_profit(signal, asset_type):
                self.logger.warning(f"{symbol} 止损止盈设置无效")
                return False

            # 5. 检查波动率限制
            if not await self._check_volatility(symbol, asset_type):
                self.logger.warning(f"{symbol} 波动率超出限制")
                return False

            return True

        except Exception as e:
            self.logger.error(f"检查 {symbol} 风险时出错: {str(e)}")
            return False

    def _validate_stop_loss_take_profit(self, signal: Dict[str, Any], asset_type: str) -> bool:
        """验证止损止盈设置"""
        try:
            risk_limits = self.risk_limits[asset_type]
            stop_loss = signal.get('stop_loss_pct', 0)
            take_profit = signal.get('take_profit_pct', 0)
            
            # 检查止损设置
            if stop_loss <= 0 or stop_loss > abs(risk_limits['stop_loss']):
                return False
                
            # 检查止盈设置
            if take_profit <= 0 or take_profit < risk_limits['take_profit']:
                return False
                
            return True
            
        except Exception as e:
            self.logger.error(f"验证止损止盈设置时出错: {str(e)}")
            return False

    async def _check_volatility(self, symbol: str, asset_type: str) -> bool:
        """检查波动率"""
        try:
            risk_limits = self.risk_limits[asset_type]['risk_limits']
            
            # 获取历史数据
            hist_data = await self.option_strategy.data_manager.get_historical_data(symbol)
            if hist_data is None or hist_data.empty:
                return False
            
            # 计算波动率
            returns = hist_data['close'].pct_change()
            volatility = returns.std() * np.sqrt(252)  # 年化波动率
            
            # 检查波动率是否在可接受范围内
            return volatility <= risk_limits['volatility']['threshold']
            
        except Exception as e:
            self.logger.error(f"检查 {symbol} 波动率时出错: {str(e)}")
            return False