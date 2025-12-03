import sys
from time import time

class RtpPacket:
    HEADER_SIZE = 12

    def __init__(self):
        self.header = bytearray(self.HEADER_SIZE)
        self.payload = bytearray()

    def encode(self, version, padding, extension, cc, seqnum, marker, pt, ssrc, payload, timestamp=0):
        """Encode the RTP packet with header fields and payload."""
        
        self.header = bytearray(self.HEADER_SIZE)
        
        # Byte 0: V(2) | P(1) | X(1) | CC(4)
        self.header[0] = (version << 6) | (padding << 5) | (extension << 4) | cc
        
        # Byte 1: M(1) | PT(7)
        self.header[1] = (marker << 7) | (pt & 0x7F)
        
        # Byte 2, 3: Sequence Number
        self.header[2] = (seqnum >> 8) & 0xFF
        self.header[3] = seqnum & 0xFF
        
        # Byte 4, 5, 6, 7: Timestamp
        self.header[4] = (timestamp >> 24) & 0xFF
        self.header[5] = (timestamp >> 16) & 0xFF
        self.header[6] = (timestamp >> 8) & 0xFF
        self.header[7] = timestamp & 0xFF
        
        # Byte 8, 9, 10, 11: SSRC
        self.header[8] = (ssrc >> 24) & 0xFF
        self.header[9] = (ssrc >> 16) & 0xFF
        self.header[10] = (ssrc >> 8) & 0xFF
        self.header[11] = ssrc & 0xFF
        
        self.payload = payload

    # Hàm lấy timestamp (để Client dùng)
    def timestamp(self):
        return int(self.header[4] << 24 | self.header[5] << 16 | self.header[6] << 8 | self.header[7])

    def decode(self, byteStream):
        """Decode the RTP packet."""
        self.header = bytearray(byteStream[:self.HEADER_SIZE])
        self.payload = byteStream[self.HEADER_SIZE:]

    def version(self):
        return int(self.header[0] >> 6)

    def seqNum(self):
        return int(self.header[2] << 8 | self.header[3])

    def timestamp(self):
        return int(self.header[4] << 24 | self.header[5] << 16 | self.header[6] << 8 | self.header[7])

    def payloadType(self):
        return int(self.header[1] & 0x7F)

    def getPayload(self):
        return self.payload

    def getPacket(self):
        return bytes(self.header) + self.payload
        
    def getMarker(self):
        return (self.header[1] >> 7) & 1
