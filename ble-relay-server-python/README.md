# BLE心电带数据中继服务器 (Python版本)

将心电带蓝牙设备的数据（心率、HRV等）通过TCP转发给Unity。

当前默认以仅蓝牙模式运行，不主动转发到 Unity；需要时再显式开启 TCP。

## 📦 安装依赖

```bash
cd ble-relay-server-python
pip install -r requirements.txt
```

需要的依赖：
- `bleak` - 跨平台BLE库（Windows/macOS/Linux都支持，无需额外驱动！）
- `protobuf` - 仅用于类型提示，实际使用纯Python解码

## 🚀 运行

```bash
python main.py
```

服务器会：
1. 默认跳过 TCP 转发，仅运行蓝牙链路
2. 扫描名称包含"GLC"的心电带设备
3. 连接设备并接收实时数据
4. 将数据打印到控制台

如果需要转发到 Unity，显式开启：

```bash
python main.py --enable-tcp --tcp-port 65450
```

如果需要兼容其他客户端端口，可以通过命令行或环境变量覆盖：

```bash
python main.py --enable-tcp --tcp-port 65432
```

```bash
set BLE_ENABLE_TCP=1
set BLE_TCP_PORT=65432
python main.py
```

## ✅ Windows优势

**Python + bleak 相比 Node.js + noble 的优势：**
- ✅ **无需安装特殊驱动**（不需要Zadig/WinUSB）
- ✅ 使用Windows原生蓝牙API
- ✅ 开箱即用，安装简单
- ✅ 跨平台兼容

## 📁 项目结构

```
ble-relay-server-python/
├── main.py          # 主入口（TCP服务器 + 控制台输出）
├── bluetooth.py     # 蓝牙管理器（基于bleak）
├── far_ferry.py     # Protobuf定义和解码器
├── crc.py           # CRC-16/CCITT校验
├── util.py          # 工具函数
├── requirements.txt # 依赖列表
└── README.md        # 本文件
```

## 📊 输出数据格式

### 实时数据 (liveData)
```json
{
  "type": "liveData",
  "timestamp": 1705234567890,
  "battery": 85,
  "hrArray": [
    { "timestamp": 1705234567000, "hr": 72 }
  ],
  "rriArray": [
    { "timestamp": 1705234567100, "rri": 833 }
  ],
  "hrv": {
    "SDNN": 45.5,
    "RMSSD": 38.2,
    "PNN50": 12.5,
    "SD1": 27.0,
    "SD2": 58.3
  }
}
```

### 设备信息 (deviceInfo)
```json
{
  "type": "deviceInfo",
  "power": 85,
  "version": "1.2.3",
  "timestamp": 1705234567890
}
```

## 🔧 配置

编辑 `main.py` 中的 `Config` 类：

```python
class Config:
    DEVICE_NAME = "GLC"      # 设备名称关键字
    ENABLE_TCP = False       # 默认不转发到 Unity
    TCP_PORT = 65450         # TCP端口
    TCP_HOST = "127.0.0.1"   # TCP地址
    SCAN_TIMEOUT = 30.0      # 扫描超时（秒）
```

也可以直接使用启动参数：

```bash
python main.py --device-name GLC --enable-tcp --tcp-host 127.0.0.1 --tcp-port 65450 --scan-timeout 30
```

## 🐍 Windows 下使用 venv

VS Code 工作区已经固定使用 .venv 里的解释器，不需要先执行激活脚本也可以直接运行：

```bash
.venv\Scripts\python.exe main.py
```

如果你希望在 PowerShell 里正常执行 Activate.ps1，需要允许当前用户脚本执行：

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

## 🎮 Unity端接收

Unity使用 `BleNetworkClient` 类接收数据：

```csharp
public class BleDataHandler : MonoBehaviour
{
    [Serializable]
    public class LiveData
    {
        public string type;
        public long timestamp;
        public int battery;
        public HRData[] hrArray;
        public RRIData[] rriArray;
        public HRVData hrv;
    }
    
    [Serializable]
    public class HRData
    {
        public long timestamp;
        public int hr;
    }
    
    void HandleData(byte[] data)
    {
        string json = System.Text.Encoding.UTF8.GetString(data);
        var liveData = JsonUtility.FromJson<LiveData>(json);
        
        if (liveData.hrArray != null && liveData.hrArray.Length > 0)
        {
            Debug.Log($"Heart Rate: {liveData.hrArray[0].hr} BPM");
        }
    }
}
```

## ❓ 常见问题

### 1. 找不到设备
- 确保心电带已开机
- 确保Windows蓝牙已开启
- 尝试先在Windows蓝牙设置中配对设备
- 增加 `SCAN_TIMEOUT` 值

### 2. 连接后没有数据
- 设备可能需要佩戴后才会发送数据
- 检查设备电量是否充足

### 3. 权限问题
- 以管理员身份运行可能有帮助
- 确保防火墙允许Python网络访问
- Windows 可能会保留一段 TCP 端口范围（excluded port range），此时即使本机地址是 `127.0.0.1` 也会出现 `PermissionError: [Errno 13]`
- 可以运行 `netsh int ipv4 show excludedportrange protocol=tcp` 检查端口是否被系统保留
- 如果默认端口不可用，改用 `--tcp-port 65450` 或其他未被占用、未被保留的端口

## 🔄 与原微信小程序代码的对应关系

| 原代码 | Python版本 |
|--------|-----------|
| `bluetooth.js` | `bluetooth.py` |
| `FarFerry.js` | `far_ferry.py` |
| `crc.js` | `crc.py` |
| `util.js` | `util.py` |
| 微信蓝牙API | bleak库 |
| protobufjs | 纯Python解码器 |
