(function () {
  const DEFAULTS = {
    deviceNamePrefix: 'C5AB',
    serviceUuid: '0000ffe0-0000-1000-8000-00805f9b34fb',
    replayDelayMs: 5000,
    minReplaySpacingMs: 250,
    pulseUrl: '/api/pulse',
  };

  const HRV_INT_FIELDS = {
    1: 'timeStamp',
    40: 'MAX',
    41: 'MIN',
    47: 'NN50',
  };

  const HRV_FLOAT_FIELDS = {
    42: 'MEAN',
    43: 'SDNN',
    44: 'RMSSD',
    45: 'SDSD',
    48: 'PNN50',
    49: 'VLF_Peek',
    50: 'VLF_Power1',
    51: 'VLF_Power2',
    52: 'VLF_Power3',
    53: 'LF_Peek',
    54: 'LF_Power1',
    55: 'LF_Power2',
    56: 'LF_Power3',
    57: 'HF_Peek',
    58: 'HF_Power1',
    59: 'HF_Power2',
    60: 'HF_Power3',
    62: 'SD1',
    63: 'SD2',
    64: 'IE',
    65: 'SE',
    66: 'BE',
    67: 'GE',
  };

  const CRC16_CCITT1021 = [
    0x0000, 0x1021, 0x2042, 0x3063, 0x4084, 0x50a5, 0x60c6, 0x70e7,
    0x8108, 0x9129, 0xa14a, 0xb16b, 0xc18c, 0xd1ad, 0xe1ce, 0xf1ef,
    0x1231, 0x0210, 0x3273, 0x2252, 0x52b5, 0x4294, 0x72f7, 0x62d6,
    0x9339, 0x8318, 0xb37b, 0xa35a, 0xd3bd, 0xc39c, 0xf3ff, 0xe3de,
    0x2462, 0x3443, 0x0420, 0x1401, 0x64e6, 0x74c7, 0x44a4, 0x5485,
    0xa56a, 0xb54b, 0x8528, 0x9509, 0xe5ee, 0xf5cf, 0xc5ac, 0xd58d,
    0x3653, 0x2672, 0x1611, 0x0630, 0x76d7, 0x66f6, 0x5695, 0x46b4,
    0xb75b, 0xa77a, 0x9719, 0x8738, 0xf7df, 0xe7fe, 0xd79d, 0xc7bc,
    0x48c4, 0x58e5, 0x6886, 0x78a7, 0x0840, 0x1861, 0x2802, 0x3823,
    0xc9cc, 0xd9ed, 0xe98e, 0xf9af, 0x8948, 0x9969, 0xa90a, 0xb92b,
    0x5af5, 0x4ad4, 0x7ab7, 0x6a96, 0x1a71, 0x0a50, 0x3a33, 0x2a12,
    0xdbfd, 0xcbdc, 0xfbbf, 0xeb9e, 0x9b79, 0x8b58, 0xbb3b, 0xab1a,
    0x6ca6, 0x7c87, 0x4ce4, 0x5cc5, 0x2c22, 0x3c03, 0x0c60, 0x1c41,
    0xedae, 0xfd8f, 0xcdec, 0xddcd, 0xad2a, 0xbd0b, 0x8d68, 0x9d49,
    0x7e97, 0x6eb6, 0x5ed5, 0x4ef4, 0x3e13, 0x2e32, 0x1e51, 0x0e70,
    0xff9f, 0xefbe, 0xdfdd, 0xcffc, 0xbf1b, 0xaf3a, 0x9f59, 0x8f78,
    0x9188, 0x81a9, 0xb1ca, 0xa1eb, 0xd10c, 0xc12d, 0xf14e, 0xe16f,
    0x1080, 0x00a1, 0x30c2, 0x20e3, 0x5004, 0x4025, 0x7046, 0x6067,
    0x83b9, 0x9398, 0xa3fb, 0xb3da, 0xc33d, 0xd31c, 0xe37f, 0xf35e,
    0x02b1, 0x1290, 0x22f3, 0x32d2, 0x4235, 0x5214, 0x6277, 0x7256,
    0xb5ea, 0xa5cb, 0x95a8, 0x8589, 0xf56e, 0xe54f, 0xd52c, 0xc50d,
    0x34e2, 0x24c3, 0x14a0, 0x0481, 0x7466, 0x6447, 0x5424, 0x4405,
    0xa7db, 0xb7fa, 0x8799, 0x97b8, 0xe75f, 0xf77e, 0xc71d, 0xd73c,
    0x26d3, 0x36f2, 0x0691, 0x16b0, 0x6657, 0x7676, 0x4615, 0x5634,
    0xd94c, 0xc96d, 0xf90e, 0xe92f, 0x99c8, 0x89e9, 0xb98a, 0xa9ab,
    0x5844, 0x4865, 0x7806, 0x6827, 0x18c0, 0x08e1, 0x3882, 0x28a3,
    0xcb7d, 0xdb5c, 0xeb3f, 0xfb1e, 0x8bf9, 0x9bd8, 0xabbb, 0xbb9a,
    0x4a75, 0x5a54, 0x6a37, 0x7a16, 0x0af1, 0x1ad0, 0x2ab3, 0x3a92,
    0xfd2e, 0xed0f, 0xdd6c, 0xcd4d, 0xbdaa, 0xad8b, 0x9de8, 0x8dc9,
    0x7c26, 0x6c07, 0x5c64, 0x4c45, 0x3ca2, 0x2c83, 0x1ce0, 0x0cc1,
    0xef1f, 0xff3e, 0xcf5d, 0xdf7c, 0xaf9b, 0xbfba, 0x8fd9, 0x9ff8,
    0x6e17, 0x7e36, 0x4e55, 0x5e74, 0x2e93, 0x3eb2, 0x0ed1, 0x1ef0,
  ];

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function round2(value) {
    return Math.round(value * 100) / 100;
  }

  function bytesToHex(bytes) {
    return Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('').toUpperCase();
  }

  function hexToBytes(hex) {
    const clean = hex.replace(/\s+/g, '');
    const bytes = new Uint8Array(clean.length / 2);
    for (let index = 0; index < clean.length; index += 2) {
      bytes[index / 2] = parseInt(clean.slice(index, index + 2), 16);
    }
    return bytes;
  }

  function intToHex(value, byteLength) {
    return Math.max(0, Number(value) || 0).toString(16).padStart(byteLength * 2, '0').toUpperCase();
  }

  function concatBytes(...arrays) {
    const total = arrays.reduce((sum, array) => sum + array.length, 0);
    const out = new Uint8Array(total);
    let offset = 0;
    for (const array of arrays) {
      out.set(array, offset);
      offset += array.length;
    }
    return out;
  }

  function crc16Ccitt(bytes) {
    let accum = 0x0000;
    for (let index = 0; index < bytes.length; index += 1) {
      accum = ((accum << 8) ^ CRC16_CCITT1021[((accum >>> 8) ^ bytes[index]) & 0xff]) & 0xffff;
    }
    return new Uint8Array([(accum >> 8) & 0xff, accum & 0xff]);
  }

  class ProtoReader {
    constructor(bytes) {
      this.bytes = bytes;
      this.pos = 0;
      this.view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    }

    get len() {
      return this.bytes.length;
    }

    readVarint() {
      let result = 0;
      let shift = 0;
      while (this.pos < this.len) {
        const value = this.bytes[this.pos];
        this.pos += 1;
        result += (value & 0x7f) * (2 ** shift);
        if ((value & 0x80) === 0) {
          return result;
        }
        shift += 7;
      }
      return result;
    }

    readTag() {
      if (this.pos >= this.len) {
        return null;
      }
      const tag = this.readVarint();
      return {
        fieldNumber: tag >>> 3,
        wireType: tag & 0x07,
      };
    }

    readFloat() {
      const value = this.view.getFloat32(this.pos, true);
      this.pos += 4;
      return value;
    }

    readBytes(length) {
      const value = this.bytes.slice(this.pos, this.pos + length);
      this.pos += length;
      return value;
    }

    readString(length) {
      return new TextDecoder('utf-8').decode(this.readBytes(length));
    }

    skipField(wireType) {
      if (wireType === 0) {
        this.readVarint();
        return;
      }
      if (wireType === 1) {
        this.pos += 8;
        return;
      }
      if (wireType === 2) {
        const length = this.readVarint();
        this.pos += length;
        return;
      }
      if (wireType === 5) {
        this.pos += 4;
      }
    }
  }

  function decodeHr(reader, length) {
    const message = { timeStamp: 0, hr: 0 };
    const end = reader.pos + length;
    while (reader.pos < end) {
      const tag = reader.readTag();
      if (!tag) {
        break;
      }
      if (tag.fieldNumber === 1) {
        message.timeStamp = reader.readVarint();
      } else if (tag.fieldNumber === 2) {
        message.hr = reader.readVarint();
      } else {
        reader.skipField(tag.wireType);
      }
    }
    return message;
  }

  function decodeRri(reader, length) {
    const message = { timeStamp: 0, rri: 0 };
    const end = reader.pos + length;
    while (reader.pos < end) {
      const tag = reader.readTag();
      if (!tag) {
        break;
      }
      if (tag.fieldNumber === 1) {
        message.timeStamp = reader.readVarint();
      } else if (tag.fieldNumber === 2) {
        message.rri = reader.readVarint();
      } else {
        reader.skipField(tag.wireType);
      }
    }
    return message;
  }

  function decodeHrv(reader, length) {
    const message = { timeStamp: 0 };
    const end = reader.pos + length;
    while (reader.pos < end) {
      const tag = reader.readTag();
      if (!tag) {
        break;
      }
      if (Object.prototype.hasOwnProperty.call(HRV_INT_FIELDS, tag.fieldNumber)) {
        message[HRV_INT_FIELDS[tag.fieldNumber]] = reader.readVarint();
      } else if (Object.prototype.hasOwnProperty.call(HRV_FLOAT_FIELDS, tag.fieldNumber) && tag.wireType === 5) {
        message[HRV_FLOAT_FIELDS[tag.fieldNumber]] = reader.readFloat();
      } else {
        reader.skipField(tag.wireType);
      }
    }
    return message;
  }

  function decodeFerryData(bytes) {
    const reader = new ProtoReader(bytes);
    const message = {
      hrArray: [],
      rriArray: [],
      hrv: null,
      userId: '',
      battery: 0,
    };

    while (reader.pos < reader.len) {
      const tag = reader.readTag();
      if (!tag) {
        break;
      }
      if (tag.fieldNumber === 1) {
        message.hrArray.push(decodeHr(reader, reader.readVarint()));
      } else if (tag.fieldNumber === 2) {
        message.rriArray.push(decodeRri(reader, reader.readVarint()));
      } else if (tag.fieldNumber === 3) {
        message.hrv = decodeHrv(reader, reader.readVarint());
      } else if (tag.fieldNumber === 7) {
        message.userId = reader.readString(reader.readVarint());
      } else if (tag.fieldNumber === 8) {
        message.battery = reader.readVarint();
      } else {
        reader.skipField(tag.wireType);
      }
    }

    return message;
  }

  class BrowserBleHeartRelay {
    constructor(options = {}) {
      this.options = { ...DEFAULTS, ...options };
      this.callbacks = {};
      this.device = null;
      this.server = null;
      this.notifyCharacteristic = null;
      this.writeCharacteristic = null;
      this.pendingTimers = new Set();
      this.firstRealtimePacket = true;
      this.baseTargetAt = null;
      this.baseSourceTimestamp = null;
      this.lastTargetAt = 0;
      this.handleDisconnected = this.handleDisconnected.bind(this);
      this.handleNotification = this.handleNotification.bind(this);
    }

    isSupported() {
      return Boolean(window.isSecureContext && navigator.bluetooth);
    }

    setCallbacks(callbacks = {}) {
      this.callbacks = { ...callbacks };
    }

    isConnected() {
      return Boolean(this.device && this.device.gatt && this.device.gatt.connected);
    }

    emitStatus(state, extra = {}) {
      if (typeof this.callbacks.onStatus === 'function') {
        this.callbacks.onStatus({ state, ...extra });
      }
    }

    emitError(error) {
      if (typeof this.callbacks.onError === 'function') {
        this.callbacks.onError(error);
      }
    }

    clearReplayQueue() {
      for (const timerId of this.pendingTimers) {
        window.clearTimeout(timerId);
      }
      this.pendingTimers.clear();
      this.baseTargetAt = null;
      this.baseSourceTimestamp = null;
      this.lastTargetAt = 0;
    }

    async connect() {
      if (!this.isSupported()) {
        throw new Error('Web Bluetooth requires Chrome or Edge over HTTPS or localhost.');
      }

      const requestOptions = {
        optionalServices: [this.options.serviceUuid],
      };
      if (this.options.deviceNamePrefix) {
        requestOptions.filters = [{ namePrefix: this.options.deviceNamePrefix }];
      } else {
        requestOptions.acceptAllDevices = true;
      }

      this.emitStatus('requesting-device');
      this.device = await navigator.bluetooth.requestDevice(requestOptions);
      this.device.addEventListener('gattserverdisconnected', this.handleDisconnected);

      this.emitStatus('connecting', { deviceName: this.device.name || '' });
      this.server = await this.device.gatt.connect();

      const service = await this.server.getPrimaryService(this.options.serviceUuid);
      const characteristics = await service.getCharacteristics();
      for (const characteristic of characteristics) {
        const props = characteristic.properties || {};
        if (!this.notifyCharacteristic && (props.notify || props.indicate)) {
          this.notifyCharacteristic = characteristic;
        }
        if (!this.writeCharacteristic && (props.write || props.writeWithoutResponse)) {
          this.writeCharacteristic = characteristic;
        }
      }

      if (!this.notifyCharacteristic || !this.writeCharacteristic) {
        throw new Error('Required notify/write characteristics were not found on the BLE service.');
      }

      await this.notifyCharacteristic.startNotifications();
      this.notifyCharacteristic.addEventListener('characteristicvaluechanged', this.handleNotification);

      this.firstRealtimePacket = true;
      this.clearReplayQueue();
      await this.initializeDevice();

      return {
        deviceName: this.device.name || '',
        serviceUuid: this.options.serviceUuid,
        notifyCharacteristicUuid: this.notifyCharacteristic.uuid,
        writeCharacteristicUuid: this.writeCharacteristic.uuid,
      };
    }

    async disconnect(options = {}) {
      const sendStop = options.sendStop !== false;
      this.clearReplayQueue();
      if (sendStop && this.writeCharacteristic) {
        try {
          await this.endTest();
        } catch (error) {
          this.emitError(error);
        }
      }

      if (this.notifyCharacteristic) {
        try {
          this.notifyCharacteristic.removeEventListener('characteristicvaluechanged', this.handleNotification);
          await this.notifyCharacteristic.stopNotifications();
        } catch (error) {
          this.emitError(error);
        }
      }

      if (this.device && this.device.gatt && this.device.gatt.connected) {
        this.device.gatt.disconnect();
      } else {
        this.cleanupConnection();
      }
    }

    cleanupConnection() {
      this.clearReplayQueue();
      if (this.device) {
        this.device.removeEventListener('gattserverdisconnected', this.handleDisconnected);
      }
      this.server = null;
      this.notifyCharacteristic = null;
      this.writeCharacteristic = null;
      this.device = null;
      this.firstRealtimePacket = true;
    }

    handleDisconnected() {
      const deviceName = this.device && this.device.name ? this.device.name : '';
      this.cleanupConnection();
      this.emitStatus('disconnected', { deviceName });
      if (typeof this.callbacks.onDisconnected === 'function') {
        this.callbacks.onDisconnected({ deviceName });
      }
    }

    async initializeDevice() {
      this.emitStatus('initializing', { deviceName: this.device.name || '' });
      await this.writeTimestamp();
      await sleep(100);
      await this.usePrivateContract();
      await sleep(100);
      await this.getHardware();
      await sleep(150);
      try {
        await this.endTest();
        await sleep(200);
      } catch (error) {
        this.emitError(error);
      }
      await this.startTest();
      this.emitStatus('connected', { deviceName: this.device.name || '' });
    }

    async write(bytes) {
      if (!this.writeCharacteristic) {
        throw new Error('BLE write characteristic is not available.');
      }
      await this.writeCharacteristic.writeValueWithoutResponse(bytes);
    }

    buildFrame(commandHex) {
      const directive = hexToBytes(`475401${commandHex}`);
      return concatBytes(directive, crc16Ccitt(directive));
    }

    async sendDirective(field) {
      let commandHex = '';
      if (field === 'deviceInfo') {
        commandHex = '0004A0020101';
      } else if (field === 'time') {
        commandHex = `0009A00101${intToHex(Date.now(), 6)}`;
      } else if (field === 'private') {
        commandHex = '0007A0070101';
      } else if (field === 'testStart') {
        commandHex = '0004A00E0100';
      } else if (field === 'testEnd') {
        commandHex = '0004A00E0101';
      } else {
        throw new Error(`Unknown directive: ${field}`);
      }
      await this.write(this.buildFrame(commandHex));
    }

    async writeTimestamp() {
      await this.sendDirective('time');
    }

    async usePrivateContract() {
      await this.sendDirective('private');
    }

    async getHardware() {
      await this.sendDirective('deviceInfo');
    }

    async startTest() {
      await this.sendDirective('testStart');
    }

    async endTest() {
      await this.sendDirective('testEnd');
    }

    handleNotification(event) {
      const view = event.target.value;
      const bytes = new Uint8Array(view.buffer, view.byteOffset, view.byteLength);
      this.onBleValue(bytes);
    }

    onBleValue(bytes) {
      const hex = bytesToHex(bytes);
      if (!hex.startsWith('475401')) {
        return;
      }

      const directive = hex.slice(12, 14);
      if (directive === '08') {
        if (this.firstRealtimePacket) {
          this.firstRealtimePacket = false;
          return;
        }

        const payloadBytes = hexToBytes(hex.slice(16, -4));
        const liveData = decodeFerryData(payloadBytes);
        if (liveData.battery > 0 && typeof this.callbacks.onDeviceInfo === 'function') {
          this.callbacks.onDeviceInfo({ power: liveData.battery, version: null });
        }
        if (typeof this.callbacks.onLiveData === 'function') {
          this.callbacks.onLiveData(liveData);
        }
        this.enqueueReplay(liveData);
        return;
      }

      if (directive === '02' && typeof this.callbacks.onDeviceInfo === 'function') {
        const power = parseInt(hex.slice(16, 18), 16);
        const versionText = hex.slice(162, 170);
        const middle = parseInt(versionText.slice(0, 2) || '0', 10);
        const major = parseInt(versionText.slice(2, 4) || '0', 10);
        const patch = parseInt(versionText.slice(6, 8) || '0', 10);
        this.callbacks.onDeviceInfo({
          power,
          version: `${major}.${middle}.${patch}`,
        });
      }
    }

    scheduleTargetTime(sourceTimestamp) {
      if (!sourceTimestamp || sourceTimestamp <= 0) {
        const targetAt = Math.max(Date.now() + this.options.replayDelayMs, this.lastTargetAt + this.options.minReplaySpacingMs);
        this.lastTargetAt = targetAt;
        return targetAt;
      }

      if (this.baseTargetAt === null || this.baseSourceTimestamp === null) {
        this.baseTargetAt = Date.now() + this.options.replayDelayMs;
        this.baseSourceTimestamp = sourceTimestamp;
      }

      const rawDelta = Math.max(0, sourceTimestamp - this.baseSourceTimestamp);
      const sourceDeltaMs = rawDelta > 100 ? rawDelta : rawDelta * 1000;
      let targetAt = this.baseTargetAt + sourceDeltaMs;
      const minNext = this.lastTargetAt + this.options.minReplaySpacingMs;
      if (targetAt < minNext) {
        targetAt = minNext;
      }
      this.lastTargetAt = targetAt;
      return targetAt;
    }

    enqueueReplay(liveData) {
      if (!liveData || !Array.isArray(liveData.hrArray) || liveData.hrArray.length === 0) {
        return;
      }

      const rrIntervals = Array.isArray(liveData.rriArray)
        ? liveData.rriArray.map((item) => Number(item.rri)).filter((value) => value > 0)
        : [];
      const rmssd = liveData.hrv && Number.isFinite(Number(liveData.hrv.RMSSD))
        ? round2(Number(liveData.hrv.RMSSD))
        : null;
      const hrItems = [...liveData.hrArray].sort((left, right) => Number(left.timeStamp) - Number(right.timeStamp));

      hrItems.forEach((item, index) => {
        const bpm = Number(item.hr);
        if (!Number.isFinite(bpm) || bpm <= 0) {
          return;
        }

        const payload = { bpm };
        if (rmssd !== null) {
          payload.hrv_rmssd = rmssd;
        }
        if (index === 0 && rrIntervals.length > 0) {
          payload.rr_intervals = rrIntervals;
        }

        const targetAt = this.scheduleTargetTime(Number(item.timeStamp));
        const delay = Math.max(0, targetAt - Date.now());
        const timerId = window.setTimeout(async () => {
          this.pendingTimers.delete(timerId);
          await this.postPulse(payload);
        }, delay);
        this.pendingTimers.add(timerId);
      });
    }

    async postPulse(payload) {
      try {
        const response = await fetch(this.options.pulseUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        if (!response.ok) {
          throw new Error(`Pulse forward failed with HTTP ${response.status}`);
        }
        if (typeof this.callbacks.onPulsePosted === 'function') {
          this.callbacks.onPulsePosted(payload);
        }
      } catch (error) {
        this.emitError(error);
      }
    }
  }

  window.BrowserBleHeartRelay = BrowserBleHeartRelay;
})();