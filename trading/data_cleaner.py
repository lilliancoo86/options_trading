"""
数据清理模块
负责清理和维护数据文件
"""
from typing import Dict, List, Any, Optional
import logging
from datetime import datetime, timedelta
import pytz
from pathlib import Path
import shutil
import asyncio
import pandas as pd
import json
import os

class DataCleaner:
    def __init__(self, config: Dict[str, Any]) -> None:
        """初始化数据清理器"""
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.tz = pytz.timezone('America/New_York')
        
        # 数据目录配置
        self.data_dir = Path('/home/options_trading/data')
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
        
        # 上次清理时间记录
        self._last_cleanup = None
        self._last_backup = None
        
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
                if '+00:00' in df.index[0]:  # 检查是否为UTC时间
                    df.index = df.index.tz_localize('UTC').tz_convert(tz)
                else:
                    df.index = df.index.tz_localize(tz)
            else:
                # 对于旧格式的文件，假设时间戳是本地时间
                df.index = pd.to_datetime(df.index)
                df.index = df.index.tz_localize(self.tz, ambiguous='infer')
            
            # 处理数据...
            
            # 保存处理后的数据
            df.to_csv(file_path)
            self.logger.debug(f"成功处理文件: {file_path}")
            
        except Exception as e:
            self.logger.error(f"处理文件 {file_path} 时出错: {str(e)}")
            
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

    async def _backup_data(self) -> None:
        """备份数据"""
        try:
            timestamp = datetime.now(self.tz).strftime('%Y%m%d_%H%M%S')
            backup_dir = self.backup_dir / f"backup_{timestamp}"
            
            # 创建备份目录
            backup_dir.mkdir(parents=True, exist_ok=True)
            
            # 备份市场数据和期权数据
            for src_dir in [self.market_data_dir, self.options_data_dir]:
                dest_dir = backup_dir / src_dir.name
                if src_dir.exists():
                    shutil.copytree(src_dir, dest_dir)
                    
            self.logger.info(f"成功创建数据备份: {backup_dir}")
            
        except Exception as e:
            self.logger.error(f"备份数据时出错: {str(e)}")
