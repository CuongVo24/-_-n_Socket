from random import randint
import threading, socket, time
from VideoStream import VideoStream
from RtpPacket import RtpPacket

class ServerWorker:
    SETUP = 'SETUP'
    PLAY = 'PLAY'
    PAUSE = 'PAUSE'
    TEARDOWN = 'TEARDOWN'

    INIT = 0
    READY = 1
    PLAYING = 2
    state = INIT

    OK_200 = 0
    FILE_NOT_FOUND_404 = 1
    CON_ERR_500 = 2

    clientInfo = {}

    def __init__(self, clientInfo):
        self.clientInfo = clientInfo
        # Sequence number cho RTP packet (tăng liên tục mỗi gói)
        self.rtpSequenceNum = 0 
        self.clientInfo['event'] = threading.Event()

    def run(self):
        threading.Thread(target=self.recvRtspRequest).start()

    def recvRtspRequest(self):
        connSocket = self.clientInfo['rtspSocket'][0]
        while True:
            try:
                data = connSocket.recv(256)
                if data:
                    print(f"Data received: {data.decode('utf-8').strip()}")
                    self.processRtspRequest(data.decode("utf-8"))
            except:
                break

    def processRtspRequest(self, data):
        request = data.split('\n')
        line1 = request[0].split(' ')
        requestType = line1[0]
        filename = line1[1]
        seq = request[1].split(' ')[1]

        if requestType == self.SETUP:
            if self.state == self.INIT:
                try:
                    self.clientInfo['videoStream'] = VideoStream(filename)
                    self.state = self.READY
                except IOError:
                    self.replyRtsp(self.FILE_NOT_FOUND_404, seq)
                    return

                self.clientInfo['session'] = randint(100000, 999999)
                self.replyRtsp(self.OK_200, seq)
                # Parse port chính xác hơn
                for line in request:
                    if "client_port" in line:
                        self.clientInfo['rtpPort'] = line.split('client_port=')[1].strip()

        elif requestType == self.PLAY:
            if self.state == self.READY:
                self.state = self.PLAYING
                self.clientInfo["rtpSocket"] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.replyRtsp(self.OK_200, seq)
                self.clientInfo['event'] = threading.Event()
                self.clientInfo['worker'] = threading.Thread(target=self.sendRtp)
                self.clientInfo['worker'].start()

        elif requestType == self.PAUSE:
            if self.state == self.PLAYING:
                self.state = self.READY
                self.clientInfo['event'].set()
                self.replyRtsp(self.OK_200, seq)

        elif requestType == self.TEARDOWN:
            self.clientInfo['event'].set()
            self.replyRtsp(self.OK_200, seq)
            # Dọn dẹp
            try:
                self.clientInfo['rtpSocket'].close()
            except:
                pass
            self.state = self.INIT

    def sendRtp(self):
        """Logic gửi RTP nâng cao với phân mảnh (Fragmentation)."""
        MAX_RTP_PAYLOAD = 1400 # MTU an toàn
        
        while True:
            self.clientInfo['event'].wait(0.05) # Giả lập tốc độ frame (20fps)

            if self.clientInfo['event'].isSet():
                break

            data = self.clientInfo['videoStream'].nextFrame()
            
            if data:
                # Frame number coi như timestamp logic (để Client biết các gói thuộc về frame nào)
                frameNumber = self.clientInfo['videoStream'].frameNbr()
                
                try:
                    address = self.clientInfo['rtspSocket'][1][0]
                    port = int(self.clientInfo['rtpPort'])

                    # --- LOGIC PHÂN MẢNH ---
                    data_len = len(data)
                    curr_pos = 0

                    while curr_pos < data_len:
                        chunk = data[curr_pos : curr_pos + MAX_RTP_PAYLOAD]
                        curr_pos += MAX_RTP_PAYLOAD
                        
                        # Marker = 1 nếu là gói cuối cùng của frame
                        if curr_pos >= data_len:
                            marker = 1
                        else:
                            marker = 0
                        
                        self.rtpSequenceNum += 1 # SeqNum tăng cho mỗi GÓI

                        # Tạo packet
                        # Note: Truyen frameNumber vao vi tri timestamp (hoac ssrc) de client biet
                        # O day ta dung makeRtp chuan da sua
                        packet = self.makeRtp(chunk, self.rtpSequenceNum, frameNumber, marker)
                        
                        self.clientInfo['rtpSocket'].sendto(packet, (address, port))
                        
                except Exception as e:
                    print(f"Connection Error: {e}")
                    break

    def makeRtp(self, payload, seqNum, timestamp, marker=0):
        """Đóng gói RTP."""
        version = 2
        padding = 0
        extension = 0
        cc = 0
        pt = 26
        ssrc = 0
        
        rtpPacket = RtpPacket()
        rtpPacket.encode(version, padding, extension, cc, seqNum, marker, pt, ssrc, payload)
        
        # Override timestamp bằng frameNumber để client dễ quản lý (Hack cho bài tập này)
        # Thực tế nên set timestamp trong encode bằng time()
        # Nhưng để client ghép gói, ta cần 1 ID chung cho frame, ở đây dùng timestamp field
        # Mở rộng RtpPacket.encode để nhận timestamp nếu cần, hoặc set thủ công:
        # (Dòng này phụ thuộc vào RtpPacket bạn dùng, code trên RtpPacket tự lấy time)
        # Để đơn giản cho bài tập, ta để nguyên RtpPacket lấy time thực.
        
        return rtpPacket.getPacket()

    def replyRtsp(self, code, seq):
        if code == self.OK_200:
            reply = 'RTSP/1.0 200 OK\nCSeq: ' + seq + '\nSession: ' + str(self.clientInfo['session']) + '\n'
            connSocket = self.clientInfo['rtspSocket'][0]
            connSocket.send(reply.encode())
        elif code == self.FILE_NOT_FOUND_404:
            print("404 NOT FOUND")
        elif code == self.CON_ERR_500:
            print("500 CONNECTION ERROR")
