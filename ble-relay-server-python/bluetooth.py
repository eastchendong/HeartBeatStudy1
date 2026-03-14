# -*- coding: utf-8 -*-
"""
BLE心电带蓝牙管理器 - Python版本
基于 bleak 库实现蓝牙BLE通信
保留原有协议解析逻辑
"""

import asyncio
import time
from typing import Callable, Dict, List, Optional, Any
from dataclasses import dataclass

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.characteristic import BleakGATTCharacteristic

from crc import CrcUtil
from util import bytes_to_hex_compact, hex_to_bytes, int_to_hex, string_to_hex
from far_ferry import decode_ferry_data, FerryData


@dataclass
class ConnectionResult:
    """连接结果"""
    connected: bool
    device_id: str
    device_name: str
    service_uuid: str = ""
    characteristic_uuid: str = ""


class BluetoothManager:
    """
    BLE蓝牙管理器
    
    功能：
    - 扫描并连接心电带设备
    - 发送指令、接收数据
    - 解析Protobuf实时数据
    - 回调机制通知上层
    """
    
    # 帧头常量
    FRAME_HEADER = "475401"
    
    def __init__(self):
        self._is_connected = False
        self._device: Optional[BLEDevice] = None
        self._client: Optional[BleakClient] = None
        self._write_characteristic: Optional[BleakGATTCharacteristic] = None
        
        # 回调注册表
        self._callbacks: Dict[str, List[Callable]] = {}
        
        # 首次实时数据标记（跳过初始化数据）
        self._is_first_realtime_data = True
        
        # 超时配置
        self._default_timeout = 10.0
    
    def set_default_timeout(self, timeout: float):
        """设置默认超时时间（秒）"""
        self._default_timeout = timeout
    
    # ===================== 回调注册 =====================
    
    def register_callback(self, directive: str, callback: Callable) -> Callable:
        """
        注册指令回调
        
        Args:
            directive: 指令代码（如 '02', '08', 'connected', 'disconnect'）
            callback: 回调函数
        
        Returns:
            取消注册的函数
        """
        if directive not in self._callbacks:
            self._callbacks[directive] = []
        self._callbacks[directive].append(callback)
        
        def unregister():
            if directive in self._callbacks and callback in self._callbacks[directive]:
                self._callbacks[directive].remove(callback)
        
        return unregister
    
    def _trigger_callbacks(self, directive: str, data: Any):
        """触发指令回调"""
        if directive in self._callbacks:
            for callback in self._callbacks[directive]:
                try:
                    callback(data)
                except Exception as e:
                    print(f"[回调错误] {directive}: {e}")
    
    # 便捷注册方法
    def register_connected(self, callback: Callable):
        return self.register_callback('connected', callback)
    
    def register_disconnect(self, callback: Callable):
        return self.register_callback('disconnect', callback)
    
    def register_device_power(self, callback: Callable):
        return self.register_callback('02', callback)
    
    def register_live_data(self, callback: Callable):
        return self.register_callback('08', callback)
    
    # ===================== 蓝牙扫描与连接 =====================
    
    async def scan_devices(self, timeout: float = 10.0) -> List[BLEDevice]:
        """
        扫描附近的BLE设备
        
        Args:
            timeout: 扫描超时时间（秒）
        
        Returns:
            发现的设备列表
        """
        print(f"[扫描] 开始扫描BLE设备，超时: {timeout}秒...")
        devices = await BleakScanner.discover(timeout=timeout)
        print(f"[扫描] 发现 {len(devices)} 个设备")
        return devices
    
    async def connect_by_name(self, name_keyword: str, timeout: float = 30.0) -> ConnectionResult:
        """
        根据设备名称关键字扫描并连接设备
        
        Args:
            name_keyword: 设备名称关键字（如 'GLC'）
            timeout: 扫描超时时间（秒）
        
        Returns:
            ConnectionResult 连接结果
        """
        print(f"[扫描连接] 搜索名称包含 '{name_keyword}' 的设备...")
        
        target_device: Optional[BLEDevice] = None
        start_time = time.time()
        
        # 使用扫描回调实时检测设备
        def detection_callback(device: BLEDevice, advertisement_data):
            nonlocal target_device
            if target_device is not None:
                return
            
            name = device.name or ""
            if name_keyword.upper() in name.upper():
                target_device = device
                print(f"[扫描连接] 找到目标设备: {name} ({device.address})")
        
        scanner = BleakScanner(detection_callback)
        await scanner.start()
        
        # 等待找到设备或超时
        while target_device is None and (time.time() - start_time) < timeout:
            await asyncio.sleep(0.1)
        
        await scanner.stop()
        
        if target_device is None:
            raise TimeoutError(f"扫描超时，未找到名称包含 '{name_keyword}' 的设备")
        
        # 连接设备
        return await self.connect(target_device)
    
    async def connect(self, device: BLEDevice) -> ConnectionResult:
        """
        连接到指定的BLE设备
        
        Args:
            device: BLE设备对象
        
        Returns:
            ConnectionResult 连接结果
        """
        self._device = device
        print(f"[连接] 正在连接设备: {device.name} ({device.address})")
        
        self._client = BleakClient(
            device,
            disconnected_callback=self._on_disconnect
        )
        
        await self._client.connect()
        self._is_connected = True
        print("[连接] 设备连接成功")
        
        # 发现服务和特征值
        service_uuid = ""
        char_uuid = ""
        
        # 标准BLE服务UUID前缀（需要跳过）
        STANDARD_SERVICE_PREFIXES = [
            "00001800",  # Generic Access
            "00001801",  # Generic Attribute
            "0000180a",  # Device Information
            "0000180f",  # Battery Service
        ]
        
        for service in self._client.services:
            # 跳过标准BLE服务
            service_uuid_lower = service.uuid.lower()
            is_standard = any(service_uuid_lower.startswith(prefix) for prefix in STANDARD_SERVICE_PREFIXES)
            
            if is_standard:
                print(f"[连接] 跳过标准服务: {service.uuid}")
                continue
            
            print(f"[连接] 发现自定义服务: {service.uuid}")
            
            for char in service.characteristics:
                print(f"[连接]   特征值: {char.uuid} 属性: {char.properties}")
                
                # 找到可写入的特征值
                if "write" in char.properties or "write-without-response" in char.properties:
                    self._write_characteristic = char
                    service_uuid = service.uuid
                    char_uuid = char.uuid
                    print(f"[连接]   → 设为写入特征值")
                
                # 订阅notify特征值（用try-except处理可能的失败）
                if "notify" in char.properties or "indicate" in char.properties:
                    try:
                        await self._client.start_notify(char.uuid, self._on_notification)
                        print(f"[连接]   → 已订阅通知")
                    except Exception as e:
                        print(f"[连接]   → 订阅通知失败（忽略）: {e}")
        
        # 触发连接成功回调
        self._trigger_callbacks('connected', {
            'device_id': device.address,
            'device_name': device.name,
            'timestamp': int(time.time() * 1000)
        })
        
        # 初始化设备
        await self._initialize_device()
        
        return ConnectionResult(
            connected=True,
            device_id=device.address,
            device_name=device.name or "",
            service_uuid=service_uuid,
            characteristic_uuid=char_uuid
        )
    
    def _on_disconnect(self, client: BleakClient):
        """断开连接回调"""
        print("[连接] 设备已断开")
        self._is_connected = False
        self._trigger_callbacks('disconnect', {
            'device_id': self._device.address if self._device else "",
            'timestamp': int(time.time() * 1000)
        })
    
    async def disconnect(self):
        """断开连接"""
        if self._client and self._client.is_connected:
            await self._client.disconnect()
            print("[断开] 设备已断开连接")
        
        self._is_connected = False
        self._client = None
        self._device = None
        self._write_characteristic = None
        self._is_first_realtime_data = True
    
    def is_connected(self) -> bool:
        """检查是否已连接"""
        return self._is_connected and self._client is not None and self._client.is_connected
    
    # ===================== 数据收发 =====================
    
    async def write(self, data: bytes):
        """
        写入数据到设备
        
        Args:
            data: 要写入的bytes数据
        """
        if not self.is_connected() or not self._write_characteristic:
            raise RuntimeError("设备未连接或无可用的写入特征值")
        
        await self._client.write_gatt_char(
            self._write_characteristic.uuid,
            data,
            response=False  # write without response
        )
    
    def _on_notification(self, sender: BleakGATTCharacteristic, data: bytes):
        """
        接收到BLE通知数据的回调
        
        Args:
            sender: 发送数据的特征值
            data: 接收到的数据
        """
        self._on_ble_value_change(data)
    
    # ===================== 协议解析（核心逻辑，从原代码移植）=====================
    
    def _parse_device_info(self, value_hex: str) -> dict:
        """
        解析设备信息
        
        Args:
            value_hex: 十六进制字符串
        
        Returns:
            { 'power': int, 'version': str }
        """
        power = int(value_hex[16:18], 16)
        version_str = value_hex[162:170]
        
        middle = int(version_str[0:2]) if len(version_str) >= 2 else 0
        big = int(version_str[2:4]) if len(version_str) >= 4 else 0
        small = int(version_str[6:8]) if len(version_str) >= 8 else 0
        
        version = f"{big}.{middle}.{small}"
        return {'power': power, 'version': version}
    
    def _on_ble_value_change(self, data: bytes):
        """
        处理蓝牙数据变化（核心协议解析）
        
        Args:
            data: 接收到的原始bytes数据
        """
        value_hex = bytes_to_hex_compact(data)
        
        # 调试：打印接收到的原始数据
        print(f"[BLE数据] 收到 {len(data)} 字节: {value_hex[:60]}{'...' if len(value_hex) > 60 else ''}")
        
        # 检查帧头：必须以 475401 开始
        if not value_hex.startswith(self.FRAME_HEADER):
            print(f"[BLE数据] 帧头不匹配，跳过")
            return
        
        # 提取指令类型和子指令
        d = value_hex[12:14]  # 指令类型
        s = value_hex[16:18]  # 子指令
        print(f"[BLE数据] 指令类型: {d}, 子指令: {s}")
        
        # 根据指令类型分发处理
        if d == '08':
            # 实时数据传输（Protobuf）
            if self._is_first_realtime_data:
                self._is_first_realtime_data = False
                return
            
            try:
                # 提取Protobuf数据部分（去掉帧头和CRC）
                protobuf_hex = value_hex[16:-4]
                protobuf_bytes = hex_to_bytes(protobuf_hex)
                
                # 解码Protobuf
                ferry_data = decode_ferry_data(protobuf_bytes)
                
                # 如果有电量信息，单独触发电量回调
                if ferry_data.battery > 0:
                    self._trigger_callbacks('02', {
                        'power': ferry_data.battery,
                        'version': None
                    })
                
                # 触发实时数据回调
                self._trigger_callbacks('08', ferry_data)
                
            except Exception as e:
                print(f"[数据解析] Protobuf解码失败: {e}")
        
        elif d == '02':
            # 设备信息
            device_info = self._parse_device_info(value_hex)
            self._trigger_callbacks('02', device_info)
        
        elif d == '0D':
            # 运动风险预警
            self._trigger_callbacks('0D', s == '00')
    
    # ===================== 指令发送 =====================
    
    async def send_directive(self, field: str, **kwargs):
        """
        发送指令到设备
        
        Args:
            field: 指令类型
            **kwargs: 额外参数（fileName, version等）
        """
        if not self.is_connected():
            raise RuntimeError("设备未连接")
        
        file_name = kwargs.get('fileName', '')
        
        # 构建指令
        if field == 'search':
            cmd = '0003A00901'
        elif field == 'del':
            length = len(file_name)
            cmd = f'{int_to_hex(length + 3, 1)}A00A01{string_to_hex(file_name)}'
        elif field == 'down':
            length = len(file_name)
            cmd = f'{int_to_hex(length + 4, 1)}A00B0100{string_to_hex(file_name)}'
        elif field == 'success':
            cmd = '0004A00C0100'
        elif field == 'fail':
            cmd = '0004A00C0101'
        elif field == 'deviceInfo':
            cmd = '0004A0020101'
        elif field == 'time':
            timestamp = int(time.time())
            cmd = f'0009A00101{int_to_hex(timestamp, 4)}'
        elif field == 'private':
            cmd = '0007A0070101'
        elif field == 'testStart':
            cmd = '0004A00E0100'
        elif field == 'testEnd':
            cmd = '0004A00E0101'
        else:
            raise ValueError(f"未知指令: {field}")
        
        # 构建完整帧并发送
        frame = CrcUtil.build_frame(cmd)
        await self.write(frame)
    
    # ===================== 便捷方法 =====================
    
    async def _initialize_device(self):
        """初始化设备（连接成功后自动执行）"""
        try:
            print("[设备初始化] 开始初始化设备...")
            
            # 1. 写入时间戳
            await self.write_timestamp()
            await asyncio.sleep(0.1)
            
            # 2. 启用私有协议
            await self.use_private_contract()
            await asyncio.sleep(0.1)
            
            # 3. 获取硬件信息
            await self.get_hardware()
            await asyncio.sleep(0.2)
            
            # 4. 先结束之前可能存在的测试（容错：避免设备卡在录制状态）
            print("[设备初始化] 先发送结束测试指令（容错）...")
            await self.end_test()
            await asyncio.sleep(0.2)
            
            # 5. 开始测试（触发实时数据发送）
            print("[设备初始化] 开始测试模式...")
            await self.start_test()
            
            print("[设备初始化] 设备初始化完成")
        except Exception as e:
            print(f"[设备初始化] 初始化失败: {e}")
    
    async def write_timestamp(self):
        """同步时间戳"""
        await self.send_directive('time')
    
    async def use_private_contract(self):
        """启用私有协议"""
        await self.send_directive('private')
    
    async def get_hardware(self):
        """获取硬件信息"""
        await self.send_directive('deviceInfo')
    
    async def start_test(self):
        """开始测试"""
        await self.send_directive('testStart')
    
    async def end_test(self):
        """结束测试"""
        await self.send_directive('testEnd')
