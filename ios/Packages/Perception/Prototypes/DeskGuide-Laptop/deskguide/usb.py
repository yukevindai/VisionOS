import plistlib
import socket
import struct


def read_exact(sock,size):
    data=bytearray()
    while len(data)<size:
        part=sock.recv(size-len(data))
        if not part:
            raise ConnectionError('Apple USB service closed its connection')
        data.extend(part)
    return bytes(data)


def request(sock,message,tag=1):
    payload=plistlib.dumps(dict(ClientVersionString='VisionOS/1.0',
                                ProgName='VisionOS',kLibUSBMuxVersion=3,**message))
    sock.sendall(struct.pack('<IIII',len(payload)+16,1,8,tag)+payload)
    length,version,kind,response_tag=struct.unpack('<IIII',read_exact(sock,16))
    if not 16<length<=1024*1024 or version!=1 or kind!=8 or response_tag!=tag:
        raise ValueError('Unexpected Apple USB service response')
    try:
        result=plistlib.loads(read_exact(sock,length-16))
    except plistlib.InvalidFileException as exc:
        raise ValueError('Apple USB response was not a plist') from exc
    if not isinstance(result,dict):
        raise ValueError('Invalid Apple USB response type')
    return result


def service_socket(timeout=3):
    try:
        return socket.create_connection(('127.0.0.1',27015),timeout=timeout)
    except OSError as exc:
        raise ConnectionError('Apple USB service unavailable. Install/open Apple Devices for Windows, '
                              'connect a data-capable USB cable, unlock iPhone and tap Trust.') from exc


def devices():
    with service_socket() as sock:
        response=request(sock,dict(MessageType='ListDevices'))
    if response.get('Number',0)!=0:
        raise ConnectionError('Apple USB service refused device enumeration')
    result=[]
    for item in response.get('DeviceList',[]):
        properties=item.get('Properties',{})
        if properties.get('ConnectionType')=='USB':
            device_id=item.get('DeviceID',properties.get('DeviceID'))
            if isinstance(device_id,int):
                result.append(device_id)
    return result


def connect(port=8888):
    if not 1<=port<=65535:
        raise ValueError('Phone stream port must be between 1 and 65535')
    attached=devices()
    if not attached:
        raise ConnectionError('No USB iPhone detected. Connect/unlock it, tap Trust, and check Apple Devices.')
    if len(attached)>1:
        raise ConnectionError('Multiple USB Apple devices detected. Disconnect all except the streaming iPhone.')
    sock=service_socket()
    try:
        result=request(sock,dict(MessageType='Connect',DeviceID=attached[0],PortNumber=socket.htons(port)))
        if result.get('MessageType')!='Result' or result.get('Number')!=0:
            raise ConnectionError('iPhone USB connection refused (code '+str(result.get('Number'))+
                                  '). Start PathOn streaming, keep it open, and verify its port.')
        return sock
    except Exception:
        sock.close()
        raise
