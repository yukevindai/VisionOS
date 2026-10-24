import plistlib
import socket
import struct
import unittest
from unittest.mock import patch
from deskguide import usb


class FakeSocket:
    def __init__(self,message):
        payload=plistlib.dumps(message)
        self.data=struct.pack('<IIII',16+len(payload),1,8,1)+payload
        self.sent=b''
        self.closed=False
    def sendall(self,data): self.sent+=data
    def recv(self,count):
        count=min(count,7)
        result,self.data=self.data[:count],self.data[count:]
        return result
    def close(self): self.closed=True
    def __enter__(self): return self
    def __exit__(self,*args): self.close()


class USBTests(unittest.TestCase):
    def test_device_enumeration_excludes_network(self):
        sock=FakeSocket(dict(DeviceList=[dict(DeviceID=4,Properties=dict(ConnectionType='USB')),
                                         dict(DeviceID=7,Properties=dict(ConnectionType='Network'))]))
        with patch('deskguide.usb.service_socket',return_value=sock):
            self.assertEqual(usb.devices(),[4])
        self.assertTrue(sock.closed)

    def test_connect_switches_to_raw_socket_and_encodes_port(self):
        sock=FakeSocket(dict(MessageType='Result',Number=0))
        with patch('deskguide.usb.devices',return_value=[4]),patch('deskguide.usb.service_socket',return_value=sock):
            self.assertIs(usb.connect(8888),sock)
        sent=plistlib.loads(sock.sent[16:])
        self.assertEqual(sent['DeviceID'],4)
        self.assertEqual(sent['PortNumber'],socket.htons(8888))
        self.assertFalse(sock.closed)

    def test_refused_connect_closes_socket(self):
        sock=FakeSocket(dict(MessageType='Result',Number=3))
        with patch('deskguide.usb.devices',return_value=[4]),patch('deskguide.usb.service_socket',return_value=sock):
            with self.assertRaisesRegex(ConnectionError,'Start PathOn'):
                usb.connect()
        self.assertTrue(sock.closed)

    def test_missing_device_and_multiple_devices(self):
        for attached in [[],[1,2]]:
            with patch('deskguide.usb.devices',return_value=attached):
                with self.assertRaises(ConnectionError): usb.connect()

    def test_missing_apple_service_has_helpful_error(self):
        with patch('deskguide.usb.socket.create_connection',side_effect=OSError('refused')):
            with self.assertRaisesRegex(ConnectionError,'Install/open Apple Devices'):
                usb.devices()

    def test_invalid_reply_rejected(self):
        sock=FakeSocket({})
        sock.data=struct.pack('<IIII',2000000,1,8,1)
        with self.assertRaises(ValueError): usb.request(sock,dict(MessageType='ListDevices'))


if __name__=='__main__': unittest.main()
