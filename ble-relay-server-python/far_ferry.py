# -*- coding: utf-8 -*-
"""
FarFerry Protobuf 定义 - Python版本
从原始 FarFerry.js 移植的数据结构定义

使用纯Python类模拟protobuf解码，避免需要.proto文件编译
"""

import struct
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class HR:
    """心率数据"""
    timeStamp: int = 0
    hr: int = 0


@dataclass
class RRI:
    """RR间期数据"""
    timeStamp: int = 0
    rri: int = 0


@dataclass
class HRV:
    """HRV心率变异性数据"""
    timeStamp: int = 0
    # 时域指标
    MAX: int = 0
    MIN: int = 0
    MEAN: float = 0.0
    SDNN: float = 0.0
    RMSSD: float = 0.0
    SDSD: float = 0.0
    NN50: int = 0
    PNN50: float = 0.0
    # 非线性指标（Poincaré图）
    SD1: float = 0.0
    SD2: float = 0.0
    # 熵指标
    IE: float = 0.0
    SE: float = 0.0
    BE: float = 0.0
    GE: float = 0.0
    # 频域指标 - HF高频段
    HF_Peek: float = 0.0
    HF_Power1: float = 0.0
    HF_Power2: float = 0.0
    HF_Power3: float = 0.0
    # 频域指标 - LF低频段
    LF_Peek: float = 0.0
    LF_Power1: float = 0.0
    LF_Power2: float = 0.0
    LF_Power3: float = 0.0
    # 频域指标 - VLF极低频段
    VLF_Peek: float = 0.0
    VLF_Power1: float = 0.0
    VLF_Power2: float = 0.0
    VLF_Power3: float = 0.0


@dataclass
class MOTION:
    """运动状态"""
    timeStamp: int = 0
    action: int = 0


@dataclass
class ECG_Data:
    """ECG心电图数据"""
    timeStamp: int = 0
    ecgArray: List[float] = field(default_factory=list)


@dataclass
class IMU:
    """单个IMU数据点"""
    imu: List[float] = field(default_factory=list)


@dataclass
class IMU_Data:
    """IMU运动传感器数据"""
    timeStamp: int = 0
    imuArray: List[IMU] = field(default_factory=list)


@dataclass
class FerryData:
    """实时传输数据 - 核心数据结构"""
    hrArray: List[HR] = field(default_factory=list)
    rriArray: List[RRI] = field(default_factory=list)
    hrv: Optional[HRV] = None
    motion: Optional[MOTION] = None
    ecgData: Optional[ECG_Data] = None
    imuData: Optional[IMU_Data] = None
    userId: str = ""
    battery: int = 0


class ProtobufDecoder:
    """
    简易Protobuf解码器
    根据FarFerry.proto的字段定义解码二进制数据
    """
    
    # Wire types
    VARINT = 0
    FIXED64 = 1
    LENGTH_DELIMITED = 2
    FIXED32 = 5
    
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
    
    def read_varint(self) -> int:
        """读取varint编码的整数"""
        result = 0
        shift = 0
        while True:
            if self.pos >= len(self.data):
                break
            byte = self.data[self.pos]
            self.pos += 1
            result |= (byte & 0x7F) << shift
            if (byte & 0x80) == 0:
                break
            shift += 7
        return result
    
    def read_signed_varint(self) -> int:
        """读取有符号varint（zigzag编码）"""
        n = self.read_varint()
        return (n >> 1) ^ -(n & 1)
    
    def read_fixed32(self) -> int:
        """读取固定32位整数"""
        value = struct.unpack('<I', self.data[self.pos:self.pos+4])[0]
        self.pos += 4
        return value
    
    def read_float(self) -> float:
        """读取32位浮点数"""
        value = struct.unpack('<f', self.data[self.pos:self.pos+4])[0]
        self.pos += 4
        return value
    
    def read_fixed64(self) -> int:
        """读取固定64位整数"""
        value = struct.unpack('<Q', self.data[self.pos:self.pos+8])[0]
        self.pos += 8
        return value
    
    def read_double(self) -> float:
        """读取64位浮点数"""
        value = struct.unpack('<d', self.data[self.pos:self.pos+8])[0]
        self.pos += 8
        return value
    
    def read_bytes(self, length: int) -> bytes:
        """读取指定长度的字节"""
        value = self.data[self.pos:self.pos+length]
        self.pos += length
        return value
    
    def read_string(self, length: int) -> str:
        """读取字符串"""
        return self.read_bytes(length).decode('utf-8', errors='ignore')
    
    def read_tag(self) -> tuple:
        """读取字段标签，返回(field_number, wire_type)"""
        if self.pos >= len(self.data):
            return None, None
        tag = self.read_varint()
        field_number = tag >> 3
        wire_type = tag & 0x07
        return field_number, wire_type
    
    def skip_field(self, wire_type: int):
        """跳过未知字段"""
        if wire_type == self.VARINT:
            self.read_varint()
        elif wire_type == self.FIXED64:
            self.pos += 8
        elif wire_type == self.LENGTH_DELIMITED:
            length = self.read_varint()
            self.pos += length
        elif wire_type == self.FIXED32:
            self.pos += 4


def decode_hr(decoder: ProtobufDecoder, length: int) -> HR:
    """解码HR消息"""
    hr = HR()
    end_pos = decoder.pos + length
    
    while decoder.pos < end_pos:
        field_num, wire_type = decoder.read_tag()
        if field_num is None:
            break
        
        if field_num == 1:  # timeStamp
            hr.timeStamp = decoder.read_varint()
        elif field_num == 2:  # hr
            hr.hr = decoder.read_varint()
        else:
            decoder.skip_field(wire_type)
    
    return hr


def decode_rri(decoder: ProtobufDecoder, length: int) -> RRI:
    """解码RRI消息"""
    rri = RRI()
    end_pos = decoder.pos + length
    
    while decoder.pos < end_pos:
        field_num, wire_type = decoder.read_tag()
        if field_num is None:
            break
        
        if field_num == 1:  # timeStamp
            rri.timeStamp = decoder.read_varint()
        elif field_num == 2:  # rri
            rri.rri = decoder.read_varint()
        else:
            decoder.skip_field(wire_type)
    
    return rri


def decode_hrv(decoder: ProtobufDecoder, length: int) -> HRV:
    """解码HRV消息"""
    hrv = HRV()
    end_pos = decoder.pos + length
    
    # HRV字段映射（field_number -> 属性名）
    hrv_int_fields = {1: 'timeStamp', 40: 'MAX', 41: 'MIN', 47: 'NN50'}
    hrv_float_fields = {
        42: 'MEAN', 43: 'SDNN', 44: 'RMSSD', 45: 'SDSD', 48: 'PNN50',
        62: 'SD1', 63: 'SD2', 64: 'IE', 65: 'SE', 66: 'BE', 67: 'GE',
        49: 'VLF_Peek', 50: 'VLF_Power1', 51: 'VLF_Power2', 52: 'VLF_Power3',
        53: 'LF_Peek', 54: 'LF_Power1', 55: 'LF_Power2', 56: 'LF_Power3',
        57: 'HF_Peek', 58: 'HF_Power1', 59: 'HF_Power2', 60: 'HF_Power3',
    }
    
    while decoder.pos < end_pos:
        field_num, wire_type = decoder.read_tag()
        if field_num is None:
            break
        
        if field_num in hrv_int_fields:
            setattr(hrv, hrv_int_fields[field_num], decoder.read_varint())
        elif field_num in hrv_float_fields:
            if wire_type == ProtobufDecoder.FIXED32:
                setattr(hrv, hrv_float_fields[field_num], decoder.read_float())
            else:
                decoder.skip_field(wire_type)
        else:
            decoder.skip_field(wire_type)
    
    return hrv


def decode_motion(decoder: ProtobufDecoder, length: int) -> MOTION:
    """解码MOTION消息"""
    motion = MOTION()
    end_pos = decoder.pos + length
    
    while decoder.pos < end_pos:
        field_num, wire_type = decoder.read_tag()
        if field_num is None:
            break
        
        if field_num == 1:  # timeStamp
            motion.timeStamp = decoder.read_varint()
        elif field_num == 2:  # action
            motion.action = decoder.read_varint()
        else:
            decoder.skip_field(wire_type)
    
    return motion


def decode_ecg_data(decoder: ProtobufDecoder, length: int) -> ECG_Data:
    """解码ECG_Data消息"""
    ecg = ECG_Data()
    end_pos = decoder.pos + length
    
    while decoder.pos < end_pos:
        field_num, wire_type = decoder.read_tag()
        if field_num is None:
            break
        
        if field_num == 1:  # timeStamp
            ecg.timeStamp = decoder.read_varint()
        elif field_num == 2:  # ecgArray (packed repeated float)
            if wire_type == ProtobufDecoder.LENGTH_DELIMITED:
                arr_length = decoder.read_varint()
                arr_end = decoder.pos + arr_length
                while decoder.pos < arr_end:
                    ecg.ecgArray.append(decoder.read_float())
            elif wire_type == ProtobufDecoder.FIXED32:
                ecg.ecgArray.append(decoder.read_float())
        else:
            decoder.skip_field(wire_type)
    
    return ecg


def decode_imu_data(decoder: ProtobufDecoder, length: int) -> IMU_Data:
    """解码IMU_Data消息"""
    imu_data = IMU_Data()
    end_pos = decoder.pos + length
    
    while decoder.pos < end_pos:
        field_num, wire_type = decoder.read_tag()
        if field_num is None:
            break
        
        if field_num == 1:  # timeStamp
            imu_data.timeStamp = decoder.read_varint()
        elif field_num == 2:  # imuArray
            if wire_type == ProtobufDecoder.LENGTH_DELIMITED:
                imu_length = decoder.read_varint()
                # 解码IMU消息
                imu = IMU()
                imu_end = decoder.pos + imu_length
                while decoder.pos < imu_end:
                    imu_field, imu_wire = decoder.read_tag()
                    if imu_field == 1:  # imu array
                        if imu_wire == ProtobufDecoder.LENGTH_DELIMITED:
                            arr_len = decoder.read_varint()
                            arr_end = decoder.pos + arr_len
                            while decoder.pos < arr_end:
                                imu.imu.append(decoder.read_float())
                        elif imu_wire == ProtobufDecoder.FIXED32:
                            imu.imu.append(decoder.read_float())
                    else:
                        decoder.skip_field(imu_wire)
                imu_data.imuArray.append(imu)
        else:
            decoder.skip_field(wire_type)
    
    return imu_data


def decode_ferry_data(data: bytes) -> FerryData:
    """
    解码FerryData消息
    
    FerryData结构:
        1: hrArray (repeated HR)
        2: rriArray (repeated RRI)
        3: hrv (HRV)
        4: motion (MOTION)
        5: ecgData (ECG_Data)
        6: imuData (IMU_Data)
        7: userId (string)
        8: battery (int32)
    """
    ferry = FerryData()
    decoder = ProtobufDecoder(data)
    
    while decoder.pos < len(data):
        field_num, wire_type = decoder.read_tag()
        if field_num is None:
            break
        
        try:
            if field_num == 1:  # hrArray
                length = decoder.read_varint()
                ferry.hrArray.append(decode_hr(decoder, length))
            
            elif field_num == 2:  # rriArray
                length = decoder.read_varint()
                ferry.rriArray.append(decode_rri(decoder, length))
            
            elif field_num == 3:  # hrv
                length = decoder.read_varint()
                ferry.hrv = decode_hrv(decoder, length)
            
            elif field_num == 4:  # motion
                length = decoder.read_varint()
                ferry.motion = decode_motion(decoder, length)
            
            elif field_num == 5:  # ecgData
                length = decoder.read_varint()
                ferry.ecgData = decode_ecg_data(decoder, length)
            
            elif field_num == 6:  # imuData
                length = decoder.read_varint()
                ferry.imuData = decode_imu_data(decoder, length)
            
            elif field_num == 7:  # userId
                length = decoder.read_varint()
                ferry.userId = decoder.read_string(length)
            
            elif field_num == 8:  # battery
                ferry.battery = decoder.read_varint()
            
            else:
                decoder.skip_field(wire_type)
        
        except Exception as e:
            # 解码错误时跳过当前字段继续
            print(f"[Protobuf] 解码字段 {field_num} 失败: {e}")
            break
    
    return ferry
