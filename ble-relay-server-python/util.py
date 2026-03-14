# -*- coding: utf-8 -*-
"""
工具函数模块 - Python版本
从原有微信小程序代码移植
"""


def hex_to_bytes(hex_string: str) -> bytes:
    """
    十六进制字符串转bytes
    
    Args:
        hex_string: 十六进制字符串（可带空格）
    
    Returns:
        bytes对象
    """
    hex_string = hex_string.replace(' ', '')
    return bytes.fromhex(hex_string)


def bytes_to_hex(data: bytes) -> str:
    """
    bytes转十六进制字符串（空格分隔）
    
    Args:
        data: bytes对象
    
    Returns:
        十六进制字符串
    """
    return ' '.join(f'{b:02X}' for b in data)


def bytes_to_hex_compact(data: bytes) -> str:
    """
    bytes转十六进制字符串（无空格）
    
    Args:
        data: bytes对象
    
    Returns:
        十六进制字符串（无空格）
    """
    return data.hex().upper()


def int_to_hex(value: int, byte_length: int) -> str:
    """
    整数转固定长度十六进制字符串
    
    Args:
        value: 整数值
        byte_length: 字节长度
    
    Returns:
        十六进制字符串
    """
    hex_len = byte_length * 2
    return f'{value:0{hex_len}X}'


def string_to_hex(s: str) -> str:
    """
    字符串转十六进制
    
    Args:
        s: 输入字符串
    
    Returns:
        十六进制字符串
    """
    return s.encode('utf-8').hex().upper()


def hex_to_string(hex_str: str) -> str:
    """
    十六进制转字符串
    
    Args:
        hex_str: 十六进制字符串
    
    Returns:
        解码后的字符串
    """
    return bytes.fromhex(hex_str).decode('utf-8', errors='ignore')
