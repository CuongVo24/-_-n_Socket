from tkinter import *
from tkinter import messagebox as tkMessageBox
from PIL import Image, ImageTk
import socket, threading, sys, os, io
import queue
from RtpPacket import RtpPacket

class Client:
    INIT = 0
    READY = 1
    PLAYING = 2
    state = INIT

    SETUP = 0
    PLAY = 1
    PAUSE = 2
    TEARDOWN = 3

    # Cấu hình Buffer: Tăng số này lên nếu mạng lag để video mượt hơn
    BUFFER_THRESHOLD = 20 
    
    def __init__(self, master, serveraddr, serverport, rtpport, filename):
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
        self.frameNbr = 0
        
        # Hàng đợi chứa các frame đã lắp ráp xong (sẵn sàng hiển thị)
        self.frameQueue = queue.Queue(maxsize=200)
        self.playEvent = threading.Event()
        
        # Biến tạm để lắp ráp các mảnh (fragmentation) của 1 frame HD
        self.currentFrameChunks = bytearray()
    
    def createWidgets(self):
        self.setup = Button(self.master, width=20, padx=3, pady=3, text="Setup", command=self.setupMovie)
        self.setup.grid(row=1, column=0, padx=2, pady=2)

        self.start = Button(self.master, width=20, padx=3, pady=3, text="Play", command=self.playMovie)
        self.start.grid(row=1, column=1, padx=2, pady=2)

        self.pause = Button(self.master, width=20, padx=3, pady=3, text="Pause", command=self.pauseMovie)
        self.pause.grid(row=1, column=2, padx=2, pady=2)

        self.teardown = Button(self.master, width=20, padx=3, pady=3, text="Teardown", command=self.exitClient)
        self.teardown.grid(row=1, column=3, padx=2, pady=2)

        self.label = Label(self.master, height=19)
        self.label.grid(row=0, column=0, columnspan=4, sticky=W+E+N+S, padx=5, pady=5)
        self.statLabel = Label(self.master, text="Status: Ready", fg="blue")
        self.statLabel.grid(row=2, column=0, columnspan=4)

    def setupMovie(self):
        if self.state == self.INIT:
            self.sendRtspRequest(self.SETUP)

    def exitClient(self):
        self.sendRtspRequest(self.TEARDOWN)
        self.master.destroy()

    def pauseMovie(self):
        if self.state == self.PLAYING:
            self.sendRtspRequest(self.PAUSE)

    def playMovie(self):
        if self.state == self.READY:
            threading.Thread(target=self.listenRtp).start()
            self.playEvent.clear()
            self.sendRtspRequest(self.PLAY)
            # Bắt đầu vòng lặp cập nhật GUI (Consumer)
            self.master.after(100, self.update_image_loop)

    def listenRtp(self):
        """
        PRODUCER THREAD: 
        1. Nhận dữ liệu từ Server.
        2. Ghép các mảnh (chunks) lại thành 1 frame hoàn chỉnh.
        3. Đẩy frame vào hàng đợi (Queue).
        """
        while True:
            try:
                data = self.rtpSocket.recv(20480) # Tăng buffer socket để nhận gói to
                if data:
                    rtpPacket = RtpPacket()
                    rtpPacket.decode(data)
                    
                    # Logic ghép phân mảnh (Fragmentation Reassembly)
                    payload = rtpPacket.getPayload()
                    self.currentFrameChunks += payload
                    
                    # Kiểm tra Marker bit. Nếu = 1 nghĩa là đã hết frame này.
                    if rtpPacket.getMarker():
                        # Đẩy toàn bộ dữ liệu frame vừa ghép vào Queue
                        if not self.frameQueue.full():
                            self.frameQueue.put(self.currentFrameChunks)
                        
                        # Reset biến tạm để đón frame tiếp theo
                        self.currentFrameChunks = bytearray() 
            except:
                if self.playEvent.isSet(): break
                if self.teardownAcked == 1:
                    self.rtpSocket.shutdown(socket.SHUT_RDWR)
                    self.rtpSocket.close()
                    break

def update_image_loop(self):
        if self.state == self.PLAYING:
            # --- LOGIC MỚI: PRE-BUFFERING ---
            # Nếu chưa đủ frame trong kho và chưa bắt đầu phát mượt mà
            # Ta có thể thêm 1 biến cờ: self.is_buffering = True ở __init__
            
            # Logic đơn giản hóa:
            if self.frameQueue.qsize() < self.BUFFER_THRESHOLD and not self.playEvent.is_set():
                 # Đang trong giai đoạn nạp ban đầu, chưa cho hiện
                 self.statLabel.config(text=f"Pre-buffering... {self.frameQueue.qsize()}/{self.BUFFER_THRESHOLD}")
                 self.master.after(40, self.update_image_loop)
                 return

            # Nếu queue cạn kiệt khi đang xem -> Buffering lại
            if self.frameQueue.qsize() == 0:
                self.statLabel.config(text="Buffering... (Network lag)")
            else:
                try:
                    frameData = self.frameQueue.get_nowait()
                    self.render_frame_memory(frameData)
                except queue.Empty:
                    pass
            
            self.master.after(40, self.update_image_loop)

    def render_frame_memory(self, data):
        """Load ảnh trực tiếp từ RAM (Không ghi đĩa -> Tối ưu tốc độ)."""
        try:
            # Biến byte array thành file-like object
            image_stream = io.BytesIO(data)
            image = Image.open(image_stream)
            photo = ImageTk.PhotoImage(image)
            self.label.configure(image=photo, height=288)
            self.label.image = photo
            self.statLabel.config(text=f"Playing... Buffer: {self.frameQueue.qsize()}")
        except Exception as e:
            print(f"Frame Error: {e}")

    def connectToServer(self):
        self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.rtspSocket.connect((self.serverAddr, self.serverPort))
        except:
            tkMessageBox.showwarning('Connection Failed', f'Connection to {self.serverAddr} failed.')

    def sendRtspRequest(self, requestCode):
        if requestCode == self.SETUP and self.state == self.INIT:
            threading.Thread(target=self.recvRtspReply).start()
            self.rtspSeq = 1
            request = f"SETUP {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nTransport: RTP/UDP; client_port={self.rtpPort}"
            self.requestSent = self.SETUP
        elif requestCode == self.PLAY and self.state == self.READY:
            self.rtspSeq += 1
            request = f"PLAY {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}"
            self.requestSent = self.PLAY
        elif requestCode == self.PAUSE and self.state == self.PLAYING:
            self.rtspSeq += 1
            request = f"PAUSE {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}"
            self.requestSent = self.PAUSE
        elif requestCode == self.TEARDOWN and not self.state == self.INIT:
            self.rtspSeq += 1
            request = f"TEARDOWN {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}"
            self.requestSent = self.TEARDOWN
        else: return
        
        self.rtspSocket.send(request.encode('utf-8'))

    def recvRtspReply(self):
        while True:
            try:
                reply = self.rtspSocket.recv(1024)
                if reply: self.parseRtspReply(reply.decode("utf-8"))
                if self.requestSent == self.TEARDOWN:
                    self.rtspSocket.shutdown(socket.SHUT_RDWR)
                    self.rtspSocket.close()
                    break
            except: break

    def parseRtspReply(self, data):
        lines = data.split('\n')
        seqNum = int(lines[1].split(' ')[1])
        if seqNum == self.rtspSeq:
            session = int(lines[2].split(' ')[1])
            if self.sessionId == 0: self.sessionId = session
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
        self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtpSocket.settimeout(0.5)
        try:
            self.rtpSocket.bind(('', self.rtpPort))
        except:
            tkMessageBox.showwarning('Unable to Bind', f'Unable to bind PORT={self.rtpPort}')

    def handler(self):
        self.pauseMovie()
        if tkMessageBox.askokcancel("Quit?", "Are you sure you want to quit?"):
            self.exitClient()
        else:
            self.playMovie()
