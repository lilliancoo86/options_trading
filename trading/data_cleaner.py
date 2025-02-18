"""
数据清理模块
负责清理和维护数据文件
"""
import json
import logging
import pandas as pd
import pytz
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any

from config.config import (
    DATA_DIR
)


class DataCleaner:
    def __init__(self, config: Dict[str, Any]) -> None:
        """初始化数据清理器"""
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.tz = pytz.timezone('America/New_York')
        
        # 数据目录配置
        self.data_dir =  DATA_DIR
        self.market_data_dir = self.data_dir / 'market_data'
        self.options_data_dir = self.data_dir / 'options_data'
        self.historical_dir = self.data_dir / 'historical'
        self.backup_dir = self.data_dir / 'backup'
        
        # 清理配置
        self.cleanup_config = {
            'max_file_age_days': 30,  # 文件最大保存天数
            'max_storage_gb': 10,     # 最大存储空间(GB)
            'backup_interval_hours': 24,  # 备份间隔(小时)
            'cleanup_interval_hours': 12,  # 清理间隔(小时)
        }
        
        # 上次清理和备份时间记录
        self._last_cleanup = None
        self._last_backup = None
        
        # 备份状态文件
        self.backup_status_file = self.data_dir / 'backup_status.json'
        self._load_backup_status()
        
    async def async_init(self) -> None:
        """异步初始化"""
        try:
            # 验证目录结构
            await self._verify_directories()
            self.logger.info("数据清理器初始化完成")
        except Exception as e:
            self.logger.error(f"数据清理器初始化失败: {str(e)}")
            raise
            
    async def _process_market_data_file(self, file_path: Path) -> None:
        """处理单个市场数据文件"""
        try:
            # 读取CSV文件
            df = pd.read_csv(file_path, index_col=0)
            
            # 检查是否存在时区信息列
            if 'original_timezone' in df.columns:
                # 使用文件中保存的时区信息
                timezone_info = df['original_timezone'].iloc[0]
                tz = pytz.timezone(timezone_info)
                
                # 解析ISO格式的时间戳
                df.index = pd.to_datetime(df.index)
                
                # 检查时间戳格式
                if isinstance(df.index[0], pd.Timestamp):
                    timestamp_str = str(df.index[0])
                    if '+00:00' in timestamp_str:  # 检查是否为UTC时间
                        df.index = pd.to_datetime(df.index, utc=True).tz_convert(tz)
                    else:
                        # 如果不是UTC时间，先本地化再转换
                        df.index = pd.to_datetime(df.index).tz_localize(tz, ambiguous='infer')
            else:
                # 对于旧格式的文件，假设时间戳是本地时间
                df.index = pd.to_datetime(df.index)
                df.index = df.index.tz_localize(self.tz, ambiguous='infer')
            
            # 确保所有时间戳都是时区感知的
            if df.index.tz is None:
                df.index = df.index.tz_localize(self.tz, ambiguous='infer')
            
            # 统一转换为UTC时间并格式化
            df.index = df.index.tz_convert('UTC')
            
            # 保存处理后的数据
            df_to_save = df.copy()
            df_to_save.index = df_to_save.index.strftime('%Y-%m-%d %H:%M:%S+00:00')
            df_to_save.to_csv(file_path)
            
            self.logger.debug(f"成功处理文件: {file_path}")
            
        except Exception as e:
            self.logger.error(f"处理文件 {file_path} 时出错: {str(e)}")
            raise  # 抛出异常以便上层处理
            
    async def cleanup(self) -> None:
        """执行数据清理"""
        try:
            current_time = datetime.now(self.tz)
            
            # 检查是否需要清理
            if (self._last_cleanup and 
                (current_time - self._last_cleanup).total_seconds() < 
                self.cleanup_config['cleanup_interval_hours'] * 3600):
                return
            
            # 创建备份
            backup_time = current_time.strftime('%Y%m%d_%H%M%S')
            backup_dir = self.backup_dir / f"backup_{backup_time}"
            backup_dir.mkdir(parents=True, exist_ok=True)
            
            # 处理市场数据文件
            for file_path in self.market_data_dir.glob('*.csv'):
                try:
                    await self._process_market_data_file(file_path)
                    # 创建备份
                    shutil.copy2(file_path, backup_dir / file_path.name)
                except Exception as e:
                    self.logger.error(f"处理文件 {file_path} 时出错: {str(e)}")
                    continue
            
            self.logger.info(f"成功创建数据备份: {backup_dir}")
            self._last_cleanup = current_time
            self.logger.info("数据清理完成")
            
        except Exception as e:
            self.logger.error(f"执行数据清理时出错: {str(e)}")

    async def _verify_directories(self) -> None:
        """验证目录结构"""
        try:
            for dir_path in [self.market_data_dir, self.options_data_dir,
                           self.historical_dir, self.backup_dir]:
                if not dir_path.exists():
                    dir_path.mkdir(parents=True)
                    self.logger.info(f"创建目录: {dir_path}")
        except Exception as e:
            self.logger.error(f"验证目录结构时出错: {str(e)}")
            raise

    async def _cleanup_expired_data(self) -> None:
        """清理过期数据"""
        try:
            cutoff_date = datetime.now(self.tz) - timedelta(
                days=self.cleanup_config['max_file_age_days']
            )
            
            # 清理市场数据
            await self._cleanup_directory(
                self.market_data_dir,
                cutoff_date,
                '*.csv'
            )
            
            # 清理期权数据
            await self._cleanup_directory(
                self.options_data_dir,
                cutoff_date,
                '*.json'
            )
            
        except Exception as e:
            self.logger.error(f"清理过期数据时出错: {str(e)}")

    async def _cleanup_directory(self, 
                               directory: Path, 
                               cutoff_date: datetime,
                               pattern: str) -> None:
        """清理指定目录"""
        try:
            for file_path in directory.rglob(pattern):
                try:
                    # 获取文件日期
                    file_date_str = file_path.stem.split('_')[-1]
                    file_date = datetime.strptime(file_date_str, '%Y%m%d')
                    
                    if file_date < cutoff_date:
                        # 移动到历史数据目录
                        dest_path = self.historical_dir / file_path.relative_to(directory)
                        dest_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(file_path), str(dest_path))
                        self.logger.info(f"移动过期文件到历史目录: {file_path.name}")
                        
                except Exception as e:
                    self.logger.error(f"处理文件 {file_path} 时出错: {str(e)}")
                    continue
                    
        except Exception as e:
            self.logger.error(f"清理目录 {directory} 时出错: {str(e)}")

    async def _check_storage_usage(self) -> None:
        """检查存储空间使用情况"""
        try:
            total_size = 0
            max_size_bytes = self.cleanup_config['max_storage_gb'] * 1024 * 1024 * 1024
            
            # 计算数据目录总大小
            for directory in [self.market_data_dir, self.options_data_dir]:
                total_size += sum(f.stat().st_size for f in directory.rglob('*') if f.is_file())
                
            # 如果超过限制，执行清理
            if total_size > max_size_bytes:
                self.logger.warning("存储空间超过限制，开始清理旧数据")
                await self._cleanup_by_size(max_size_bytes)
                
        except Exception as e:
            self.logger.error(f"检查存储空间时出错: {str(e)}")

    async def _cleanup_by_size(self, max_size_bytes: int) -> None:
        """根据大小清理数据"""
        try:
            # 获取所有数据文件及其大小和时间
            files_info = []
            for directory in [self.market_data_dir, self.options_data_dir]:
                for file_path in directory.rglob('*'):
                    if file_path.is_file():
                        files_info.append({
                            'path': file_path,
                            'size': file_path.stat().st_size,
                            'mtime': file_path.stat().st_mtime
                        })
            
            # 按修改时间排序
            files_info.sort(key=lambda x: x['mtime'])
            
            # 从最旧的文件开始移动到历史目录
            current_size = sum(f['size'] for f in files_info)
            for file_info in files_info:
                if current_size <= max_size_bytes:
                    break
                    
                file_path = file_info['path']
                dest_path = self.historical_dir / file_path.relative_to(self.data_dir)
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(file_path), str(dest_path))
                
                current_size -= file_info['size']
                self.logger.info(f"移动文件到历史目录以释放空间: {file_path.name}")
                
        except Exception as e:
            self.logger.error(f"根据大小清理数据时出错: {str(e)}")

    def _load_backup_status(self) -> None:
        """加载备份状态"""
        try:
            if self.backup_status_file.exists():
                with open(self.backup_status_file, 'r') as f:
                    status = json.load(f)
                    self._last_backup = datetime.fromisoformat(status.get('last_backup', ''))
            else:
                self._last_backup = None
        except Exception as e:
            self.logger.error(f"加载备份状态时出错: {str(e)}")
            self._last_backup = None

    def _save_backup_status(self) -> None:
        """保存备份状态"""
        try:
            with open(self.backup_status_file, 'w') as f:
                json.dump({
                    'last_backup': self._last_backup.isoformat() if self._last_backup else None
                }, f)
        except Exception as e:
            self.logger.error(f"保存备份状态时出错: {str(e)}")

    async def _should_backup(self) -> bool:
        """检查是否需要备份"""
        try:
            current_time = datetime.now(self.tz)
            
            # 如果没有上次备份记录，执行备份
            if not self._last_backup:
                return True
                
            # 计算距离上次备份的时间
            backup_interval = timedelta(hours=self.cleanup_config['backup_interval_hours'])
            time_since_last_backup = current_time - self._last_backup
            
            # 如果超过备份间隔，需要备份
            return time_since_last_backup >= backup_interval
            
        except Exception as e:
            self.logger.error(f"检查备份时间时出错: {str(e)}")
            return False

    async def _backup_data(self) -> None:
        """备份数据"""
        try:
            # 检查是否需要备份
            if not await self._should_backup():
                self.logger.debug("距离上次备份未超过24小时，跳过备份")
                return
                
            timestamp = datetime.now(self.tz).strftime('%Y%m%d_%H%M%S')
            backup_dir = self.backup_dir / f"backup_{timestamp}"
            
            # 创建备份目录
            backup_dir.mkdir(parents=True, exist_ok=True)
            
            # 备份市场数据和期权数据
            for src_dir in [self.market_data_dir, self.options_data_dir]:
                dest_dir = backup_dir / src_dir.name
                if src_dir.exists():
                    shutil.copytree(src_dir, dest_dir)
            
            # 更新备份时间
            self._last_backup = datetime.now(self.tz)
            self._save_backup_status()
            
            self.logger.info(f"成功创建数据备份: {backup_dir}")
            
        except Exception as e:
            self.logger.error(f"备份数据时出错: {str(e)}")
