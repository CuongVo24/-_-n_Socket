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
        # Sequence number cho RTP packet (tăng liên tục mỗi gói, KHÔNG reset khi seek)
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
                    print(f"Data received:\n{data.decode('utf-8').strip()}")
                    self.processRtspRequest(data.decode("utf-8"))
            except Exception as e:
                print(f"RTSP Recv Error: {e}")
                break

    def processRtspRequest(self, data):
        """Xử lý yêu cầu RTSP từ Client."""
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

                # Tạo Session ID ngẫu nhiên
                self.clientInfo['session'] = randint(100000, 999999)
                
                # --- CẢI TIẾN 1: Lấy và gửi tổng số frame cho Client ---
                # Giúp Client biết độ dài video để vẽ thanh Seek
                total_frames = self.clientInfo['videoStream'].totalFrames()
                extra_headers = f"Total-Frames: {total_frames}"
                
                self.replyRtsp(self.OK_200, seq, extra_headers)
                
                # Lấy port RTP của Client
                for line in request:
                    if "client_port" in line:
                        self.clientInfo['rtpPort'] = line.split('client_port=')[1].strip()

        elif requestType == self.PLAY:
            # --- CẢI TIẾN 2: Cho phép PLAY khi đang READY hoặc PLAYING ---
            if self.state == self.READY or self.state == self.PLAYING:
                
                # --- XỬ LÝ SEEK (TUA) ---
                # Kiểm tra xem Client có gửi vị trí muốn tua tới không
                for line in request:
                    if "Frame-Num" in line:
                        try:
                            start_frame = int(line.split('Frame-Num: ')[1].strip())
                            print(f"Server seeking to frame: {start_frame}")
                            self.clientInfo['videoStream'].seek(start_frame)
                            # QUAN TRỌNG: KHÔNG reset rtpSequenceNum ở đây
                            # RTP Sequence phải tăng liên tục để Client không báo mất gói
                        except:
                            pass
                # ------------------------

                self.state = self.PLAYING
                
                # Tạo socket RTP nếu chưa có
                if 'rtpSocket' not in self.clientInfo:
                    self.clientInfo["rtpSocket"] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                
                self.replyRtsp(self.OK_200, seq)
                
                # --- CẢI TIẾN 3: Quản lý Thread an toàn ---
                # Kiểm tra nếu thread gửi RTP chưa chạy thì mới khởi tạo
                if 'worker' not in self.clientInfo or not self.clientInfo['worker'].is_alive():
                    self.clientInfo['event'] = threading.Event()
                    self.clientInfo['worker'] = threading.Thread(target=self.sendRtp)
                    self.clientInfo['worker'].start()
                else:
                    # Nếu thread đang tồn tại nhưng bị PAUSE (event set), thì clear event để chạy tiếp
                    self.clientInfo['event'].clear()

        elif requestType == self.PAUSE:
            if self.state == self.PLAYING:
                self.state = self.READY
                self.clientInfo['event'].set() # Dừng vòng lặp gửi RTP
                self.replyRtsp(self.OK_200, seq)

        elif requestType == self.TEARDOWN:
            self.clientInfo['event'].set() # Dừng thread
            self.replyRtsp(self.OK_200, seq)
            # Dọn dẹp socket
            try:
                if 'rtpSocket' in self.clientInfo:
                    self.clientInfo['rtpSocket'].close()
                    del self.clientInfo['rtpSocket'] # Xóa khỏi dict để tránh lỗi sau này
            except:
                pass
            self.state = self.INIT

    # Bạn cũng cần cập nhật hàm replyRtsp để hỗ trợ tham số extra_headers
    def replyRtsp(self, code, seq, extra_headers=""):
        if code == self.OK_200:
            reply = f"RTSP/1.0 200 OK\nCSeq: {seq}\nSession: {self.clientInfo['session']}"
            if extra_headers:
                reply += f"\n{extra_headers}"
            reply += "\n\n"
            
            connSocket = self.clientInfo['rtspSocket'][0]
            connSocket.send(reply.encode())
            
        elif code == self.FILE_NOT_FOUND_404:
            print("404 NOT FOUND")
        elif code == self.CON_ERR_500:
            print("500 CONNECTION ERROR")

    def sendRtp(self):
        """Chế độ Turbo: Tăng tốc độ nạp Buffer lên mức tối đa."""
        MAX_RTP_PAYLOAD = 1400 
        packet_count = 0 

        while True:
            # Thời gian chờ vòng lặp cực nhỏ
            self.clientInfo['event'].wait(0.001) 

            if self.clientInfo['event'].isSet():
                break

            data = self.clientInfo['videoStream'].nextFrame()
            
            if data:
                frameNumber = self.clientInfo['videoStream'].frameNbr()
                try:
                    address = self.clientInfo['rtspSocket'][1][0]
                    port = int(self.clientInfo['rtpPort'])

                    data_len = len(data)
                    curr_pos = 0

                    while curr_pos < data_len:
                        chunk = data[curr_pos : curr_pos + MAX_RTP_PAYLOAD]
                        curr_pos += MAX_RTP_PAYLOAD
                        
                        if curr_pos >= data_len: marker = 1
                        else: marker = 0
                        
                        self.rtpSequenceNum += 1
                        packet = self.makeRtp(chunk, self.rtpSequenceNum, frameNumber, marker)
                        
                        self.clientInfo['rtpSocket'].sendto(packet, (address, port))
                        
                        # --- TURBO MODE: BURST 100 GÓI TIN ---
                        packet_count += 1
                        # Bắn 100 gói liên tục rồi mới nghỉ 1ms
                        # Điều này giúp tốc độ nạp nhanh gấp 5 lần so với trước
                        if packet_count % 100 == 0:
                             time.sleep(0.001) 
                        # -------------------------------------
                        
                except Exception as e:
                    print(f"Connection Error: {e}")
                    break
            else:
                # Hết video
                print("End of stream.")
                self.state = self.READY
                break

    def makeRtp(self, payload, seqNum, timestamp, marker=0):
            """Đóng gói RTP."""
            version = 2
            padding = 0
            extension = 0
            cc = 0
            pt = 26 # MJPEG
            ssrc = 123456 # Random ID
        
            rtpPacket = RtpPacket()
            rtpPacket.encode(version, padding, extension, cc, seqNum, marker, pt, ssrc, payload)
        
            # Lưu ý: Bài tập dùng frameNumber làm timestamp giả lập.
            # Nếu muốn timestamp thực tế (đồng hồ), cần sửa lại RtpPacket.encode
        
            return rtpPacket.getPacket()
