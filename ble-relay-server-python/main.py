# -*- coding: utf-8 -*-
"""
BLE心电带数据中继服务器 - Python版本

功能：
1. 扫描并连接心电带设备（通过名称包含进行选择）
2. 接收实时心率、HRV数据并打印到控制台
3. 通过TCP将数据转发给Unity客户端

使用方法：
    pip install -r requirements.txt
    python main.py
"""

import asyncio
import argparse
import json
import os
import signal
import sys
import time
from typing import List, Optional
from dataclasses import asdict

from bluetooth import BluetoothManager
from far_ferry import FerryData, HR, RRI, HRV


def _get_env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        print(f'警告: 环境变量 {name}={value!r} 不是有效整数，已回退到默认值 {default}')
        return default


def _get_env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        print(f'警告: 环境变量 {name}={value!r} 不是有效数字，已回退到默认值 {default}')
        return default


def _get_env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


# ===================== 配置 =====================
class Config:
    # 设备名称关键字
    DEVICE_NAME = os.getenv('BLE_DEVICE_NAME', 'Farbeat')
    # 是否启用 TCP 转发（默认关闭，先保证蓝牙链路稳定）
    ENABLE_TCP = _get_env_bool('BLE_ENABLE_TCP', False)
    # TCP服务器端口（Windows 上 65432 常落入 excluded port range，默认改为 65450）
    TCP_PORT = _get_env_int('BLE_TCP_PORT', 65450)
    # TCP服务器地址
    TCP_HOST = os.getenv('BLE_TCP_HOST', '127.0.0.1')
    # 扫描超时时间（秒）
    SCAN_TIMEOUT = _get_env_float('BLE_SCAN_TIMEOUT', 30.0)


# ===================== 全局变量 =====================
bluetooth_manager = BluetoothManager()
tcp_server: Optional[asyncio.Server] = None
connected_clients: List[asyncio.StreamWriter] = []


# ===================== 数据格式化 =====================

def format_hr_data(hr_array: List[HR]) -> List[dict]:
    """格式化心率数据"""
    return [{'timestamp': hr.timeStamp, 'hr': hr.hr} for hr in hr_array]


def format_rri_data(rri_array: List[RRI]) -> List[dict]:
    """格式化RRI数据"""
    return [{'timestamp': rri.timeStamp, 'rri': rri.rri} for rri in rri_array]


def format_hrv_data(hrv: HRV) -> dict:
    """格式化HRV数据"""
    return {
        'timestamp': hrv.timeStamp,
        # 时域指标
        'MAX': hrv.MAX,
        'MIN': hrv.MIN,
        'MEAN': hrv.MEAN,
        'SDNN': hrv.SDNN,
        'RMSSD': hrv.RMSSD,
        'SDSD': hrv.SDSD,
        'NN50': hrv.NN50,
        'PNN50': hrv.PNN50,
        # 非线性指标
        'SD1': hrv.SD1,
        'SD2': hrv.SD2,
        # 熵指标
        'IE': hrv.IE,
        'SE': hrv.SE,
        'BE': hrv.BE,
        'GE': hrv.GE,
        # 频域指标
        'HF_Peek': hrv.HF_Peek,
        'HF_Power1': hrv.HF_Power1,
        'HF_Power2': hrv.HF_Power2,
        'HF_Power3': hrv.HF_Power3,
        'LF_Peek': hrv.LF_Peek,
        'LF_Power1': hrv.LF_Power1,
        'LF_Power2': hrv.LF_Power2,
        'LF_Power3': hrv.LF_Power3,
        'VLF_Peek': hrv.VLF_Peek,
        'VLF_Power1': hrv.VLF_Power1,
        'VLF_Power2': hrv.VLF_Power2,
        'VLF_Power3': hrv.VLF_Power3,
    }


def format_ferry_data_for_unity(data: FerryData) -> dict:
    """格式化FerryData用于传输给Unity"""
    result = {
        'type': 'liveData',
        'timestamp': int(time.time() * 1000),
        'battery': data.battery,
        'userId': data.userId,
    }
    
    if data.hrArray:
        result['hrArray'] = format_hr_data(data.hrArray)
    
    if data.rriArray:
        result['rriArray'] = format_rri_data(data.rriArray)
    
    if data.hrv:
        result['hrv'] = format_hrv_data(data.hrv)
    
    if data.motion:
        result['motion'] = {
            'timestamp': data.motion.timeStamp,
            'action': data.motion.action,
        }
    
    if data.ecgData:
        result['ecgData'] = {
            'timestamp': data.ecgData.timeStamp,
            'samples': data.ecgData.ecgArray,
        }
    
    if data.imuData:
        result['imuData'] = {
            'timestamp': data.imuData.timeStamp,
            'samples': [{'imu': imu.imu} for imu in data.imuData.imuArray],
        }
    
    return result


# ===================== 控制台输出 =====================

def print_live_data(data: FerryData):
    """打印实时数据到控制台"""
    print('\n' + '=' * 60)
    print(f'📊 实时数据 [{time.strftime("%H:%M:%S")}]')
    print('=' * 60)
    
    # 电量
    if data.battery > 0:
        print(f'🔋 电量: {data.battery}%')
    
    # 心率
    if data.hrArray:
        latest_hr = data.hrArray[-1]
        print(f'💓 心率: {latest_hr.hr} BPM')
    
    # RRI
    if data.rriArray:
        rri_values = [r.rri for r in data.rriArray]
        print(f'📈 RR间期: {rri_values} ms')
    
    # HRV
    if data.hrv:
        print('📉 HRV指标:')
        if data.hrv.SDNN:
            print(f'   SDNN: {data.hrv.SDNN:.2f} ms')
        if data.hrv.RMSSD:
            print(f'   RMSSD: {data.hrv.RMSSD:.2f} ms')
        if data.hrv.PNN50:
            print(f'   PNN50: {data.hrv.PNN50:.2f}%')
        if data.hrv.SD1:
            print(f'   SD1: {data.hrv.SD1:.2f}')
        if data.hrv.SD2:
            print(f'   SD2: {data.hrv.SD2:.2f}')
        if data.hrv.LF_Power1:
            print(f'   LF Power: {data.hrv.LF_Power1:.2f}')
        if data.hrv.HF_Power1:
            print(f'   HF Power: {data.hrv.HF_Power1:.2f}')
    
    # ECG
    if data.ecgData and data.ecgData.ecgArray:
        print(f'📈 ECG采样点数: {len(data.ecgData.ecgArray)}')
    
    print('=' * 60)


# ===================== TCP服务器 =====================

async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """处理Unity客户端连接"""
    addr = writer.get_extra_info('peername')
    print(f'\n🎮 Unity客户端已连接: {addr}')
    connected_clients.append(writer)
    
    # 发送欢迎消息
    welcome = json.dumps({
        'type': 'connected',
        'message': 'BLE Relay Server Connected (Python)',
        'timestamp': int(time.time() * 1000),
    })
    writer.write((welcome + '\n').encode())
    await writer.drain()
    
    try:
        # 保持连接，等待客户端断开
        while True:
            data = await reader.read(1024)
            if not data:
                break
    except Exception as e:
        print(f'🎮 Unity客户端错误: {e}')
    finally:
        print(f'🎮 Unity客户端已断开: {addr}')
        connected_clients.remove(writer)
        writer.close()
        await writer.wait_closed()


async def start_tcp_server():
    """启动TCP服务器"""
    global tcp_server
    try:
        tcp_server = await asyncio.start_server(
            handle_client,
            Config.TCP_HOST,
            Config.TCP_PORT
        )
    except PermissionError as e:
        raise RuntimeError(
            f'TCP服务器启动失败: 无法绑定 {Config.TCP_HOST}:{Config.TCP_PORT}。\n'
            '该端口可能被 Windows excluded port range 保留。\n'
            '请改用其他端口，例如 --tcp-port 65450，或设置环境变量 BLE_TCP_PORT。'
        ) from e
    except OSError as e:
        if getattr(e, 'winerror', None) == 10048:
            raise RuntimeError(
                f'TCP服务器启动失败: {Config.TCP_HOST}:{Config.TCP_PORT} 已被其他程序占用。\n'
                '请关闭占用进程，或改用其他端口，例如 --tcp-port 65450。'
            ) from e
        raise
    
    addr = tcp_server.sockets[0].getsockname()
    print(f'\n🌐 TCP服务器已启动: {addr[0]}:{addr[1]}')
    print('   等待Unity客户端连接...')


async def broadcast_to_unity(data: dict):
    """向所有连接的Unity客户端发送数据"""
    if not connected_clients:
        return
    
    message = json.dumps(data, ensure_ascii=False) + '\n'
    message_bytes = message.encode('utf-8')
    
    for writer in connected_clients[:]:  # 复制列表避免修改时出错
        try:
            writer.write(message_bytes)
            await writer.drain()
        except Exception as e:
            print(f'发送数据到Unity失败: {e}')


# ===================== 蓝牙事件处理 =====================

def setup_bluetooth_callbacks():
    """设置蓝牙事件监听"""
    
    # 连接成功
    def on_connected(data):
        print(f'\n✅ 设备已连接: {data.get("device_name", "")}')
        asyncio.create_task(broadcast_to_unity({
            'type': 'deviceConnected',
            'deviceId': data.get('device_id', ''),
            'deviceName': data.get('device_name', ''),
            'timestamp': data.get('timestamp', 0),
        }))
    
    # 断开连接
    def on_disconnect(data):
        print('\n❌ 设备已断开')
        asyncio.create_task(broadcast_to_unity({
            'type': 'deviceDisconnected',
            'deviceId': data.get('device_id', ''),
            'timestamp': data.get('timestamp', 0),
        }))
    
    # 设备电量
    def on_device_power(data):
        power = data.get('power', 0)
        version = data.get('version')
        version_str = f", 版本: {version}" if version else ""
        print(f'\n🔋 设备电量: {power}%{version_str}')
        
        asyncio.create_task(broadcast_to_unity({
            'type': 'deviceInfo',
            'power': power,
            'version': version,
            'timestamp': int(time.time() * 1000),
        }))
    
    # 实时数据（核心）
    def on_live_data(data: FerryData):
        # 打印到控制台
        print_live_data(data)
        
        # 发送给Unity
        formatted = format_ferry_data_for_unity(data)
        asyncio.create_task(broadcast_to_unity(formatted))
    
    bluetooth_manager.register_connected(on_connected)
    bluetooth_manager.register_disconnect(on_disconnect)
    bluetooth_manager.register_device_power(on_device_power)
    bluetooth_manager.register_live_data(on_live_data)


# ===================== 主程序 =====================

def parse_args():
    parser = argparse.ArgumentParser(description='BLE心电带数据中继服务器')
    parser.add_argument('--device-name', help='扫描时匹配的设备名称关键字')
    parser.add_argument('--tcp-host', help='TCP监听地址')
    parser.add_argument('--tcp-port', type=int, help='TCP监听端口')
    parser.add_argument('--scan-timeout', type=float, help='蓝牙扫描超时时间（秒）')
    parser.add_argument('--enable-tcp', action='store_true', help='启用 TCP 转发到 Unity')
    parser.add_argument('--no-tcp', action='store_true', help='禁用 TCP 转发，仅运行蓝牙链路')
    return parser.parse_args()


def apply_runtime_config(args):
    if args.device_name:
        Config.DEVICE_NAME = args.device_name
    if args.enable_tcp:
        Config.ENABLE_TCP = True
    if args.no_tcp:
        Config.ENABLE_TCP = False
    if args.tcp_host:
        Config.TCP_HOST = args.tcp_host
    if args.tcp_port is not None:
        Config.TCP_PORT = args.tcp_port
    if args.scan_timeout is not None:
        Config.SCAN_TIMEOUT = args.scan_timeout


async def main():
    print('╔════════════════════════════════════════════════════════════╗')
    print('║       BLE心电带数据中继服务器 v1.0 (Python)                  ║')
    print('║       连接心电带 → 解析数据 → 转发给Unity                    ║')
    print('╚════════════════════════════════════════════════════════════╝')
    print('')
    tcp_status = f'{Config.TCP_HOST}:{Config.TCP_PORT}' if Config.ENABLE_TCP else 'disabled'
    print(f'配置: 设备关键字={Config.DEVICE_NAME}, TCP={tcp_status}, 扫描超时={Config.SCAN_TIMEOUT}s')
    
    # 1. 启动TCP服务器
    if Config.ENABLE_TCP:
        try:
            await start_tcp_server()
        except Exception as e:
            print(f'\n⚠️ TCP转发启动失败，已自动降级为仅蓝牙模式: {e}')
            Config.ENABLE_TCP = False
    else:
        print('📴 已禁用 TCP 转发，仅输出控制台数据')
    
    # 2. 设置蓝牙回调
    setup_bluetooth_callbacks()
    
    # 3. 扫描并连接设备
    print(f'\n🔍 正在扫描心电带设备 (名称包含 "{Config.DEVICE_NAME}")...')
    print('   请确保设备已开机并在附近...')
    
    try:
        result = await bluetooth_manager.connect_by_name(
            Config.DEVICE_NAME,
            timeout=Config.SCAN_TIMEOUT
        )
        
        print('\n✅ 连接成功!')
        print(f'   设备名称: {result.device_name}')
        print(f'   设备ID: {result.device_id}')
        print(f'   服务UUID: {result.service_uuid}')
        print(f'   特征值UUID: {result.characteristic_uuid}')
        print('\n📡 开始接收实时数据...\n')
        
        # 保持运行
        while bluetooth_manager.is_connected():
            await asyncio.sleep(1)
        
        print('\n设备已断开，程序退出')
        
    except TimeoutError as e:
        print(f'\n❌ 连接失败: {e}')
        print('\n可能的原因:')
        print('  1. 设备未开机或不在附近')
        print('  2. 蓝牙未开启')
        print('  3. 需要先在系统蓝牙设置中配对设备')
    
    except Exception as e:
        print(f'\n❌ 错误: {e}')
        import traceback
        traceback.print_exc()


async def shutdown():
    """优雅关闭"""
    print('\n\n正在关闭...')
    
    # 先发送结束测试命令，让设备停止录制
    try:
        if bluetooth_manager.is_connected():
            print('📤 发送结束测试命令...')
            await bluetooth_manager.end_test()
            await asyncio.sleep(0.3)  # 等待命令发送完成
    except Exception as e:
        print(f'发送结束命令失败: {e}')
    
    # 断开蓝牙连接
    try:
        await bluetooth_manager.disconnect()
    except:
        pass
    
    if tcp_server:
        tcp_server.close()
        await tcp_server.wait_closed()
    
    for writer in connected_clients:
        writer.close()
    
    print('再见! 👋')


def signal_handler(sig, frame):
    """处理Ctrl+C"""
    asyncio.create_task(shutdown())
    sys.exit(0)


if __name__ == '__main__':
    args = parse_args()
    apply_runtime_config(args)

    # 注册信号处理
    signal.signal(signal.SIGINT, signal_handler)
    
    # 运行主程序
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('\n程序已中断')
