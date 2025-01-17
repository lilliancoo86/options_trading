"""
风险检查模块
负责检查持仓风险和市场风险，包括止盈止损管理
"""
from typing import Dict, Any, Tuple, List
import logging
from datetime import datetime
import pytz
import re

class RiskChecker:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.tz = pytz.timezone('America/New_York')
        
        # 风险控制参数
        self.risk_limits = {
            'volatility': {
                'max_daily': 5.0,  # 最大日内波动率
                'warning': 3.0     # 波动率警告线
            },
            'vix': {
                'max_level': 35.0,
                'warning': 25.0
            },
            'option': {
                'stop_loss': -10.0,  # 期权固定10%止损
                'take_profit': None  # 期权不设固定止盈
            },
            'stock': {
                'stop_loss': -3.0,   # 股票固定3%止损
                'take_profit': 5.0    # 股票固定5%止盈
            },
            'market': {
                'max_position_value': 100000,  # 单个持仓限额
                'max_total_exposure': 500000,  # 总持仓限额
                'max_positions': 10  # 最大持仓数量限制
            }
        }
        
        # 添加收盘平仓设置
        self.market_close = {
            'force_close_time': '15:45',  # 收盘前15分钟强制平仓
            'warning_time': '15:40'       # 收盘前20分钟发出警告
        }

        # 添加移动止损配置
        self.trailing_stop = {
            'activation': 15.0,     # 盈利15%时激活移动止损
            'trail_percent': 5.0,   # 回撤5%时触发
            'min_profit': 10.0      # 确保至少保住10%的利润
        }

        # ATR 配置
        self.atr_config = {
            'period': 14,          # ATR周期
            'multiplier': 2.0,     # ATR倍数
            'min_periods': 1,      # 最小所需周期数
            'intraday': {
                'interval': '1m',  # 分时ATR间隔
                'high_threshold': 2.0,  # 高点ATR倍数
                'low_threshold': 0.5,   # 低点ATR倍数
            }
        }
        
        # 分批止盈配置
        self.take_profit_stages = {
            'stage1': {
                'threshold': 25.0,  # 25%时止盈
                'ratio': 0.33      # 卖出1/3仓位
            },
            'stage2': {
                'threshold': 40.0,  # 40%时止盈
                'ratio': 0.5       # 卖出一半剩余仓位
            }
        }
        
        # 缓存数据
        self._atr_cache = {
            'time': None,
            'data': {}
        }
        self._position_peaks = {}
        self._intraday_data = {}

        # 初始化时添加 TimeChecker 引用
        self.time_checker = config.get('time_checker')
        
        # 添加仓位阶段跟踪
        self._position_stages = {}  # 用于跟踪分批止盈阶段

        # 完整的风险控制体系
        self.risk_control = {
            # 1. 资金安全（最高优先级）
            'capital_protection': {
                'stop_loss': {
                    'option': -10.0,    # 期权固定止损10%
                    'stock': -3.0       # 股票固定止损3%
                },
                'max_loss_per_day': {
                    'option': 1000.0,   # 期权日亏损上限
                    'stock': 500.0      # 股票日亏损上限
                }
            },
            
            # 2. 利润保护（次高优先级）
            'profit_protection': {
                'trailing_stop': {
                    'activation': 15.0,     # 盈利15%时激活
                    'trail_percent': 5.0,   # 回撤5%触发
                    'min_profit': 10.0      # 保底利润10%
                },
                'take_profit_stages': {
                    'stage1': {'threshold': 25.0, 'ratio': 0.33},  # 盈利25%时卖出1/3
                    'stage2': {'threshold': 40.0, 'ratio': 0.5}    # 盈利40%时卖出一半
                }
            },
            
            # 3. 时间风险（强制执行）
            'time_risk': {
                'market_close': '15:45',    # 收盘前强平时间
                'warning_time': '15:40',    # 预警时间
                'option_expiry': 1          # 期权到期前1天平仓
            },
            
            # 4. 市场风险（动态监控）
            'market_risk': {
                'vix_limit': 35.0,          # VIX上限
                'volatility_limit': 5.0,    # 日内波动率上限
                'volume_threshold': 1000     # 最小成交量要求
            }
        }

        # 风险检查优先级（从高到低）
        self.risk_priority = {
            'stop_loss': 1,     # 固定止损（保护本金）
            'trailing': 2,      # 移动止损（保护利润）
            'time': 3,          # 收盘时间（避免隔夜）
            'expiry': 4,        # 期权到期（避免到期风险）
            'atr': 5,          # ATR动态调整（波动管理）
            'take_profit': 6    # 常规止盈（分批获利）
        }

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

    async def check_position_risk(self, position: Dict[str, Any], market_data: Dict[str, Any]) -> Tuple[bool, str, float]:
        """检查持仓风险（按优先级）"""
        try:
            # 1. 资金安全检查（不可忽略）
            result = await self._check_capital_safety(position)
            if result[0]:
                return result
                
            # 2. 利润保护检查（可选但重要）
            result = await self._check_profit_protection(position)
            if result[0]:
                return result
                
            # 3. 时间风险检查（强制执行）
            result = await self._check_time_risk(position)
            if result[0]:
                return result
                
            # 4. 波动管理检查（动态调整）
            result = await self._check_volatility(position, market_data)
            if result[0]:
                return result
                
            return False, "", 0.0
            
        except Exception as e:
            self.logger.error(f"检查持仓风险时出错: {str(e)}")
            return False, "", 0.0

    async def _check_capital_safety(self, position: Dict[str, Any]) -> Tuple[bool, str, float]:
        """资金安全检查"""
        try:
            symbol = position['symbol']
            current_price = float(position['current_price'])
            cost_price = float(position['cost_price'])
            pnl_pct = (current_price - cost_price) / cost_price * 100
            
            # 判断是否为期权
            is_option = bool(re.search(r'\d{6}[CP]\d+\.US$', symbol))
            stop_loss = self.risk_control['capital_protection']['stop_loss']
            
            # 1. 固定止损（最高优先级）
            if pnl_pct <= (stop_loss['option'] if is_option else stop_loss['stock']):
                self.logger.warning(
                    f"触发固定止损:\n"
                    f"  标的: {symbol}\n"
                    f"  亏损: {pnl_pct:.1f}%\n"
                    f"  止损线: {stop_loss['option'] if is_option else stop_loss['stock']}%"
                )
                return True, "固定止损", 1.0
            
            # 2. 日内亏损上限
            daily_limit = self.risk_control['capital_protection']['max_loss_per_day']
            daily_loss = self.calculate_daily_loss(position)
            if daily_loss >= (daily_limit['option'] if is_option else daily_limit['stock']):
                self.logger.warning(
                    f"触发日亏损上限:\n"
                    f"  标的: {symbol}\n"
                    f"  日内亏损: ${daily_loss:.2f}\n"
                    f"  上限: ${daily_limit['option'] if is_option else daily_limit['stock']}"
                )
                return True, "日亏损上限", 1.0
            
            return False, "", 0.0
            
        except Exception as e:
            self.logger.error(f"检查资金安全时出错: {str(e)}")
            return False, "", 0.0

    async def _check_profit_protection(self, position: Dict[str, Any]) -> Tuple[bool, str, float]:
        """利润保护检查"""
        try:
            symbol = position['symbol']
            current_price = float(position['current_price'])
            cost_price = float(position['cost_price'])
            pnl_pct = (current_price - cost_price) / cost_price * 100
            
            # 1. 移动止损
            trailing = self.risk_control['profit_protection']['trailing_stop']
            if pnl_pct >= trailing['activation']:
                if symbol not in self._position_peaks:
                    self._position_peaks[symbol] = pnl_pct
                else:
                    self._position_peaks[symbol] = max(self._position_peaks[symbol], pnl_pct)
                
                peak_pnl = self._position_peaks[symbol]
                drawdown = peak_pnl - pnl_pct
                
                if drawdown >= trailing['trail_percent'] and pnl_pct >= trailing['min_profit']:
                    self.logger.warning(
                        f"触发移动止损:\n"
                        f"  标的: {symbol}\n"
                        f"  最高收益: {peak_pnl:.1f}%\n"
                        f"  当前收益: {pnl_pct:.1f}%\n"
                        f"  回撤: {drawdown:.1f}%"
                    )
                    return True, "移动止损", 1.0
            
            # 2. 分批止盈
            take_profit = self.risk_control['profit_protection']['take_profit_stages']
            if symbol not in self._position_stages:
                self._position_stages[symbol] = set()
            
            # 检查第二阶段止盈
            if (pnl_pct >= take_profit['stage2']['threshold'] and 
                'stage2' not in self._position_stages[symbol]):
                self._position_stages[symbol].add('stage2')
                return True, "分批止盈-阶段2", take_profit['stage2']['ratio']
            
            # 检查第一阶段止盈
            if (pnl_pct >= take_profit['stage1']['threshold'] and 
                'stage1' not in self._position_stages[symbol]):
                self._position_stages[symbol].add('stage1')
                return True, "分批止盈-阶段1", take_profit['stage1']['ratio']
            
            return False, "", 0.0
            
        except Exception as e:
            self.logger.error(f"检查利润保护时出错: {str(e)}")
            return False, "", 0.0

    async def _check_time_risk(self, position: Dict[str, Any]) -> Tuple[bool, str, float]:
        """时间风险检查"""
        try:
            # 1. 检查是否需要收盘平仓
            need_close, reason = await self.check_market_close(position)
            if need_close:
                return True, reason, 1.0
            
            # 2. 检查期权是否即将到期
            need_close, reason = self.time_checker.check_expiry_close(position['symbol'])
            if need_close:
                return True, reason, 1.0
            
            return False, "", 0.0
            
        except Exception as e:
            self.logger.error(f"检查时间风险时出错: {str(e)}")
            return False, "", 0.0

    async def check_market_close(self, position: Dict[str, Any]) -> Tuple[bool, str]:
        """检查收盘平仓"""
        try:
            current_time = datetime.now(self.tz).strftime('%H:%M')
            
            # 收盘前警告
            if current_time >= self.risk_control['time_risk']['warning_time']:
                self.logger.warning(
                    f"接近收盘时间:\n"
                    f"  标的: {position['symbol']}\n"
                    f"  当前时间: {current_time}"
                )
            
            # 强制平仓检查
            if current_time >= self.risk_control['time_risk']['market_close']:
                self.logger.warning(
                    f"触发收盘平仓:\n"
                    f"  标的: {position['symbol']}\n"
                    f"  当前时间: {current_time}"
                )
                return True, "收盘平仓"
            
            return False, ""
            
        except Exception as e:
            self.logger.error(f"检查收盘平仓时出错: {str(e)}")
            return False, ""

    def calculate_pnl(self, position: Dict[str, Any]) -> float:
        """计算持仓盈亏"""
        try:
            current_price = float(position['current_price'])
            cost_price = float(position['cost_price'])
            volume = int(position['volume'])
            
            pnl = (current_price - cost_price) * volume
            return pnl
            
        except Exception as e:
            self.logger.error(f"计算持仓盈亏时出错: {str(e)}")
            return 0.0

    def calculate_daily_loss(self, position: Dict[str, Any]) -> float:
        """计算日内亏损"""
        try:
            pnl = self.calculate_pnl(position)
            return abs(min(0, pnl))
            
        except Exception as e:
            self.logger.error(f"计算日内亏损时出错: {str(e)}")
            return 0.0

    async def check_market_risk(self, vix_level: float, daily_volatility: float) -> Tuple[bool, str]:
        """检查市场风险"""
        try:
            market_risk = self.risk_control['market_risk']
            
            # 检查VIX
            if vix_level > market_risk['vix_limit']:
                return True, f"VIX过高: {vix_level:.1f} > {market_risk['vix_limit']}"
            
            # 检查波动率
            if daily_volatility > market_risk['volatility_limit']:
                return True, f"波动率过高: {daily_volatility:.1f}% > {market_risk['volatility_limit']}%"
            
            return False, ""
            
        except Exception as e:
            self.logger.error(f"检查市场风险时出错: {str(e)}")
            return False, ""

    def log_risk_status(self, position: Dict[str, Any]):
        """记录风险状态"""
        try:
            pnl = self.calculate_pnl(position)
            pnl_pct = pnl / (float(position['cost_price']) * int(position['volume'])) * 100
            
            self.logger.info(
                f"\n=== 风险状态 [{position['symbol']}] ===\n"
                f"当前价格: ${position['current_price']:.2f}\n"
                f"成本价格: ${position['cost_price']:.2f}\n"
                f"持仓数量: {position['volume']}张\n"
                f"盈亏金额: ${pnl:.2f}\n"
                f"盈亏比例: {pnl_pct:+.1f}%\n"
                f"{'='*40}"
            )
            
        except Exception as e:
            self.logger.error(f"记录风险状态时出错: {str(e)}")

    def _is_option(self, symbol: str) -> bool:
        """检查是否为期权"""
        return any(x in symbol for x in ['C', 'P'])

    def check_new_position_risk(self, symbol: str, price: float, volume: int) -> Tuple[bool, str]:
        """检查新开仓位的风险"""
        try:
            # 计算持仓价值
            position_value = price * volume
            
            # 检查单个持仓限额
            if position_value > self.risk_limits['market']['max_position_value']:
                self.logger.warning(
                    f"超过单个持仓限额:\n"
                    f"  标的: {symbol}\n"
                    f"  持仓价值: ${position_value:.2f}\n"
                    f"  限额: ${self.risk_limits['market']['max_position_value']}"
                )
                return True, "超过持仓限额"
            
            # 检查总持仓限额
            total_value = self.risk_stats['total_exposure'] + position_value
            if total_value > self.risk_limits['market']['max_total_exposure']:
                self.logger.warning(
                    f"超过总持仓限额:\n"
                    f"  当前总持仓: ${self.risk_stats['total_exposure']:.2f}\n"
                    f"  新增持仓: ${position_value:.2f}\n"
                    f"  限额: ${self.risk_limits['market']['max_total_exposure']}"
                )
                return True, "超过总持仓限额"
            
            # 检查持仓数量限制
            if self.risk_stats['total_positions'] >= self.risk_limits['market']['max_positions']:
                self.logger.warning(f"超过最大持仓数量限制: {self.risk_stats['total_positions']}")
                return True, "超过持仓数量限制"
            
            return False, ""
            
        except Exception as e:
            self.logger.error(f"检查新开仓位风险时出错: {str(e)}")
            return False, ""