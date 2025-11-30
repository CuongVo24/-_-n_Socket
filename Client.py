from tkinter import *
from tkinter import messagebox as tkMessageBox
import tkinter.messagebox
from PIL import Image, ImageTk
import socket, threading, sys, traceback, os
import queue # [NEW] Thêm thư viện hàng đợi
import time  # [NEW] Thêm thư viện time để kiểm soát tốc độ khung hình

from RtpPacket import RtpPacket

CACHE_FILE_NAME = "cache-"
CACHE_FILE_EXT = ".jpg"

class Client:
    INIT = 0
    READY = 1
    PLAYING = 2
    state = INIT

    SETUP = 0
    PLAY = 1
    PAUSE = 2
    TEARDOWN = 3

    # [NEW] Cấu hình Caching
    BUFFER_THRESHOLD = 20  # Số lượng frame cần buffer trước khi phát (Pre-buffer N frames) 
    FRAME_INTERVAL = 0.05  # Thời gian nghỉ giữa các frame (50ms)

    # Initiation..
    def __init__(self, master, serveraddr, serverport, rtpport, filename):
        print(">>> ĐANG Ở TRONG Client.__init__")
        self.master = master
        self.master.protocol("WM_DELETE_WINDOW", self.handler)
        self.createWidgets()
        self.serverAddr = serveraddr
        self.serverPort = int(serverport)
        self.rtpPort = int(rtpport)
        self.fileName = filename
        self.rtspSeq = 0
        self.sessionId = 0
        self.requestSent = -1
        self.teardownAcked = 0
        self.connectToServer()
        print(">>> ĐÃ KẾT NỐI SERVER XONG, CHUẨN BỊ KẾT THÚC INIT")
        self.frameNbr = 0
        self.currentFrameBuffer = bytearray()
        
        # [NEW] Khởi tạo Buffer
        self.frameBuffer = queue.Queue() 
        self.isBuffering = True # Cờ trạng thái đang nạp buffer

    def createWidgets(self):
        """Build GUI."""
        # Create Setup button
        self.setup = Button(self.master, width=20, padx=3, pady=3)
        self.setup["text"] = "Setup"
        self.setup["command"] = self.setupMovie
        self.setup.grid(row=1, column=0, padx=2, pady=2)

        # Create Play button
        self.start = Button(self.master, width=20, padx=3, pady=3)
        self.start["text"] = "Play"
        self.start["command"] = self.playMovie
        self.start.grid(row=1, column=1, padx=2, pady=2)

        # Create Pause button
        self.pause = Button(self.master, width=20, padx=3, pady=3)
        self.pause["text"] = "Pause"
        self.pause["command"] = self.pauseMovie
        self.pause.grid(row=1, column=2, padx=2, pady=2)

        # Create Teardown button
        self.teardown = Button(self.master, width=20, padx=3, pady=3)
        self.teardown["text"] = "Teardown"
        self.teardown["command"] = self.exitClient
        self.teardown.grid(row=1, column=3, padx=2, pady=2)

        # Create a label to display the movie
        self.label = Label(self.master, height=19)
        self.label.grid(row=0, column=0, columnspan=4, sticky=W + E + N + S, padx=5, pady=5)

    def setupMovie(self):
        """Setup button handler."""
        if self.state == self.INIT:
            self.sendRtspRequest(self.SETUP)

    def exitClient(self):
        """Teardown button handler."""
        self.sendRtspRequest(self.TEARDOWN)
        self.master.destroy()  # Close the gui window
        # Clean up cache file
        try:
            os.remove(CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT)
        except OSError:
            pass

    def pauseMovie(self):
        """Pause button handler."""
        if self.state == self.PLAYING:
            self.sendRtspRequest(self.PAUSE)
        else:
            print(">>> State is NOT PLAYING. Ignoring PAUSE request.")

    def playMovie(self):
        """Play button handler."""
        if self.state == self.READY:
            # Create a new thread to listen for RTP packets
            threading.Thread(target=self.listenRtp).start()
            
            # [NEW] Khởi tạo luồng hiển thị (Consumer) riêng biệt
            threading.Thread(target=self.runBufferLoop).start()
            
            self.playEvent = threading.Event()
            self.playEvent.clear()
            self.sendRtspRequest(self.PLAY)

# Thêm biến này vào __init__ của Client
    # self.currentFrameBuffer = bytearray() 

    def listenRtp(self):
        """Listen for RTP packets (PRODUCER)."""
        current_frame_buffer = bytearray() # Bộ đệm lắp ghép frame

        while True:
            try:
                data = self.rtpSocket.recv(20480)
                if data:
                    rtpPacket = RtpPacket()
                    rtpPacket.decode(data)
                    
                    # Lấy marker
                    is_last_packet = rtpPacket.getMarker() # Hàm mới thêm ở Bước 1
                    
                    # Ghép payload vào bộ đệm tạm
                    current_frame_buffer += rtpPacket.getPayload()

                    # Nếu là gói cuối cùng của frame (Marker = 1)
                    if is_last_packet == 1:
                        currFrameNbr = rtpPacket.seqNum()
                        
                        # Logic kiểm tra frame cũ (optional, tùy chỉnh)
                        # if currFrameNbr > self.frameNbr: ...
                        
                        self.frameNbr = currFrameNbr
                        
                        # Đẩy frame hoàn chỉnh vào hàng đợi Buffer (Cache)
                        # Chuyển bytearray về bytes để lưu file
                        self.frameBuffer.put(bytes(current_frame_buffer))
                        
                        # Reset bộ đệm lắp ghép cho frame tiếp theo
                        current_frame_buffer = bytearray()
                        
            except:
                if self.playEvent.isSet():
                    break
                if self.teardownAcked == 1:
                    self.rtpSocket.shutdown(socket.SHUT_RDWR)
                    self.rtpSocket.close()
                    break
    
    # [NEW] Hàm CONSUMER: Lấy frame từ buffer ra để hiển thị
    def runBufferLoop(self):
        print(">>> Bắt đầu luồng Buffer Consumer...")
        while not self.playEvent.isSet():
            # Nếu đang trong trạng thái Buffering (chưa đủ frame)
            if self.isBuffering:
                if self.frameBuffer.qsize() >= self.BUFFER_THRESHOLD:
                    self.isBuffering = False
                    print(f">>> Buffer đã đầy ({self.frameBuffer.qsize()} frames). Bắt đầu phát video!")
                else:
                    # Nếu chưa đủ frame, đợi một chút rồi check lại
                    # print(f"Buffering... ({self.frameBuffer.qsize()}/{self.BUFFER_THRESHOLD})")
                    time.sleep(0.1)
                    continue
            
            # Nếu Buffer cạn kiệt, quay lại trạng thái Buffering
            if self.frameBuffer.empty():
                print(">>> Buffer cạn! Đang tải thêm...")
                self.isBuffering = True
                continue

            # Lấy frame ra khỏi hàng đợi và hiển thị
            frame_data = self.frameBuffer.get()
            self.updateMovie(self.writeFrame(frame_data))
            
            # Giả lập tốc độ frame rate (để video chạy mượt, không quá nhanh)
            time.sleep(self.FRAME_INTERVAL)


    def writeFrame(self, data):
        """Write the received frame to a temp image file. Return the image file."""
        cachename = CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT
        file = open(cachename, "wb")
        file.write(data)
        file.close()

        return cachename

    def updateMovie(self, imageFile):
        """Update the image file as video frame in the GUI."""
        try:
            photo = ImageTk.PhotoImage(Image.open(imageFile))
            self.label.configure(image=photo, height=288)
            self.label.image = photo
        except Exception as e:
            print(f"Error updating movie frame: {e}")

    def connectToServer(self):
        """Connect to the Server. Start a new RTSP/TCP session."""
        print(">>> ĐANG CỐ KẾT NỐI TỚI SERVER...")
        self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.rtspSocket.connect((self.serverAddr, self.serverPort))
            print(">>> KẾT NỐI THÀNH CÔNG!")
        except:
            tkMessageBox.showwarning('Connection Failed', 'Connection to \'%s\' failed.' % self.serverAddr)

    def sendRtspRequest(self, requestCode):
        """Send RTSP request to the server."""
        # Setup request
        if requestCode == self.SETUP and self.state == self.INIT:
            threading.Thread(target=self.recvRtspReply).start()
            self.rtspSeq = 1
            request = f"SETUP {self.fileName} RTSP/1.0\n"
            request += f"CSeq: {self.rtspSeq}\n"
            request += f"Transport: RTP/UDP; client_port={self.rtpPort}\n"
            self.requestSent = self.SETUP

        # Play request
        elif requestCode == self.PLAY and self.state == self.READY:
            self.rtspSeq += 1
            request = f"PLAY {self.fileName} RTSP/1.0\n"
            request += f"CSeq: {self.rtspSeq}\n"
            request += f"Session: {self.sessionId}\n"
            self.requestSent = self.PLAY

        # Pause request
        elif requestCode == self.PAUSE and self.state == self.PLAYING:
            self.rtspSeq += 1
            request = f"PAUSE {self.fileName} RTSP/1.0\n"
            request += f"CSeq: {self.rtspSeq}\n"
            request += f"Session: {self.sessionId}\n"
            self.requestSent = self.PAUSE

        # Teardown request
        elif requestCode == self.TEARDOWN and not self.state == self.INIT:
            self.rtspSeq += 1
            request = f"TEARDOWN {self.fileName} RTSP/1.0\n"
            request += f"CSeq: {self.rtspSeq}\n"
            request += f"Session: {self.sessionId}\n"
            self.requestSent = self.TEARDOWN
        else:
            return

        if request:
            self.rtspSocket.send(request.encode('utf-8'))
            print('\nData sent:\n' + request)

    def recvRtspReply(self):
        """Receive RTSP reply from the server."""
        while True:
            reply = self.rtspSocket.recv(1024)

            if reply:
                self.parseRtspReply(reply.decode("utf-8"))

            if self.requestSent == self.TEARDOWN:
                self.rtspSocket.shutdown(socket.SHUT_RDWR)
                self.rtspSocket.close()
                break

    def parseRtspReply(self, data):
        """Parse the RTSP reply from the server."""
        lines = data.split('\n')
        seqNum = int(lines[1].split(' ')[1])

        if seqNum == self.rtspSeq:
            session = int(lines[2].split(' ')[1])
            if self.sessionId == 0:
                self.sessionId = session

            if self.sessionId == session:
                if int(lines[0].split(' ')[1]) == 200:
                    if self.requestSent == self.SETUP:
                        self.state = self.READY
                        self.openRtpPort()
                    elif self.requestSent == self.PLAY:
                        self.state = self.PLAYING
                    elif self.requestSent == self.PAUSE:
                        self.state = self.READY
                        self.playEvent.set()
                    elif self.requestSent == self.TEARDOWN:
                        self.state = self.INIT
                    
                    self.teardownAcked = 1

    def openRtpPort(self):
        """Open RTP socket binded to a specified port."""
        self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtpSocket.settimeout(0.5)
        try:
            self.rtpSocket.bind(('', self.rtpPort))
        except:
            tkMessageBox.showwarning('Unable to Bind', 'Unable to bind PORT=%d' % self.rtpPort)

    def handler(self):
        """Handler on explicitly closing the GUI window."""
        self.pauseMovie()
        if tkMessageBox.askokcancel("Quit?", "Are you sure you want to quit?"):
            self.exitClient()
        else:
            self.playMovie()
