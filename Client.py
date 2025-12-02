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

    # Cau hinh Buffer: Tang so nay len neu mang lag de video muot hon
    BUFFER_THRESHOLD = 10 
    BUFFER_LOW_WATERMARK = 10


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
        self.totalPacketsReceived = 0
        self.expectedSeqNum = 0
        self.packetLossCount = 0
        # Hang doi chua cac frame da lap rap xong (san sang hien thi)
        self.frameQueue = queue.Queue(maxsize=1000)
        self.playEvent = threading.Event()

        # --- THÊM 3 DÒNG NÀY ĐỂ SỬA LỖI CRASH ---
        self.frameQueue = queue.Queue(maxsize=1000)
        self.BUFFER_START_THRESHOLD = 20   # Ngưỡng bắt đầu phát (cần 20 frame)
        self.BUFFER_REFILL_THRESHOLD = 40  # Ngưỡng nạp lại khi bị lag

        self.total_frames = 500 # Giá trị mặc định, sẽ cập nhật khi SETUP
        # Bien tam de lap rap cac manh (fragmentation) cua 1 frame HD
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

        # --- MOI: Thanh tien do (Progress Bar) ---
        # Canvas mau xam dam (background)
        self.progressbar = Canvas(self.master, height=15, bg="#444444", highlightthickness=0)
        self.progressbar.grid(row=3, column=0, columnspan=4, sticky=W+E, padx=5, pady=5)
        
        # Thanh Buffer (Mau trang/xam nhat) - Lop duoi
        self.buffer_bar = self.progressbar.create_rectangle(0, 0, 0, 15, fill="#bbbbbb", width=0)
        
        # Thanh Da xem (Mau do) - Lop tren
        self.played_bar = self.progressbar.create_rectangle(0, 0, 0, 15, fill="#ff0000", width=0)
        
        # Su kien click chuot de tua
        self.progressbar.bind("<Button-1>", self.on_seek)
        # -----------------------------------------

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
            # Bat dau vong lap cap nhat GUI (Consumer)
            self.master.after(100, self.update_image_loop)

    def listenRtp(self):
            while True:
                try:
                    data = self.rtpSocket.recv(20480)
                    if data:
                    
                        rtpPacket = RtpPacket()
                        rtpPacket.decode(data)
                    
                        currSeq = rtpPacket.seqNum()
                    
                        # --- THONG KE PACKET LOSS ---
                        if self.expectedSeqNum != 0:
                            if currSeq > self.expectedSeqNum:
                                loss = currSeq - self.expectedSeqNum
                                self.packetLossCount += loss
                                print(f"Warning: Lost {loss} packets! Total lost: {self.packetLossCount}")
                    
                        self.expectedSeqNum = currSeq + 1
                        self.totalPacketsReceived += 1
                        # -----------------------------

                        payload = rtpPacket.getPayload()
                        self.currentFrameChunks += payload
                    
                        if rtpPacket.getMarker():
                            if not self.frameQueue.full():
                                self.frameQueue.put(self.currentFrameChunks)
                            self.currentFrameChunks = bytearray()
    # --- PHẦN SỬA ĐỔI QUAN TRỌNG ---
                except socket.timeout:
                    # Nếu hết 0.5s mà không có dữ liệu, KHÔNG ĐƯỢC break
                    # Kiểm tra nếu user đã bấm Teardown thì mới thoát
                    if self.teardownAcked == 1:
                        break
                    continue # Tiếp tục lắng nghe
                except:
                    if self.teardownAcked == 1:
                        break
                    # print("RTP Error") # Có thể bật lên để debug
                    break

    def update_image_loop(self):
        if self.state == self.PLAYING:
            # 1. Vẽ thanh tiến độ (Giữ nguyên code cũ)
            try:
                total = getattr(self, 'total_frames', 500)
                width = self.progressbar.winfo_width()
                curr_pct = self.frameNbr / total
                # Buffer % tính cả số lượng đang nằm trong hàng đợi
                buffer_pct = (self.frameNbr + self.frameQueue.qsize()) / total 
                
                if curr_pct > 1: curr_pct = 1 
                if buffer_pct > 1: buffer_pct = 1
                
                self.progressbar.coords(self.buffer_bar, 0, 0, width * buffer_pct, 15)
                self.progressbar.coords(self.played_bar, 0, 0, width * curr_pct, 15)
            except: pass

            # 2. LOGIC ĐIỀU KHIỂN PLAYBACK (YOUTUBE STYLE)
            
            # Nếu hàng đợi cạn sạch (Network lag quá nặng) -> Dừng hình, hiện Buffering
            if self.frameQueue.qsize() == 0 and self.playEvent.is_set():
                 self.statLabel.config(text="Buffering... (Network lag)")
                 self.playEvent.clear() # Đánh dấu tạm dừng nội bộ
            
            # Logic hồi phục:
            # Nếu đang tạm dừng (do mới bấm Play hoặc do Lag)
            if not self.playEvent.is_set():
                # Chỉ cần nạp đủ ngưỡng khởi động (5 frame) là chạy lại ngay
                if self.frameQueue.qsize() >= self.BUFFER_START_THRESHOLD:
                    self.playEvent.set() # Cho phép chạy tiếp
                    self.statLabel.config(text=f"Playing... Buffer: {self.frameQueue.qsize()}")
                else:
                    # Chưa đủ thì chờ tiếp, hiển thị số frame đang nạp
                    self.statLabel.config(text=f"Buffering... {self.frameQueue.qsize()}/{self.BUFFER_START_THRESHOLD}")
                    self.master.after(40, self.update_image_loop)
                    return

            # 3. HIỂN THỊ HÌNH ẢNH (Chỉ chạy khi playEvent được set)
            if self.playEvent.is_set():
                try:
                    # Lấy frame ra hiển thị
                    frameData = self.frameQueue.get_nowait()
                    self.render_frame_memory(frameData)
                    self.frameNbr += 1
                except queue.Empty:
                    pass
        
        # Quan trọng: Client luôn gọi hàm này mỗi 40ms (25 FPS) để giữ đúng tốc độ video
        # Bất kể Server gửi nhanh thế nào, Client chỉ lấy ra đúng 25 hình/giây
        self.master.after(40, self.update_image_loop)

    def render_frame_memory(self, data):
        """Load image directly from RAM."""
        try:
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
            """Phân tích phản hồi từ Server, bao gồm cả Header mở rộng."""
            lines = data.split('\n')
            seqNum = int(lines[1].split(' ')[1])
        
            # Kiểm tra đúng Sequence Number
            if seqNum == self.rtspSeq:
                session = int(lines[2].split(' ')[1])
                # Nếu là lần đầu nhận Session ID (từ SETUP)
                if self.sessionId == 0: 
                    self.sessionId = session
            
                if self.sessionId == session:
                    if int(lines[0].split(' ')[1]) == 200:
                    
                        # --- MỚI: Đọc tổng số frame (Total-Frames) nếu server gửi kèm ---
                        # Giúp thanh tiến độ hiển thị chính xác với mọi video
                        for line in lines:
                            if "Total-Frames" in line:
                                try:
                                    val = int(line.split('Total-Frames: ')[1].strip())
                                    if val > 0:
                                        self.total_frames = val
                                        print(f"Server Video Info: Total Frames = {self.total_frames}")
                                except:
                                    pass
                        # -------------------------------------------------------------

                        if self.requestSent == self.SETUP:
                            self.state = self.READY
                            self.openRtpPort()
                        elif self.requestSent == self.PLAY:
                            self.state = self.PLAYING
                        elif self.requestSent == self.PAUSE:
                            self.state = self.READY
                            # Khi Pause, đảm bảo thread render không bị treo
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

    def on_seek(self, event):
            """Xử lý sự kiện click vào thanh tiến độ để tua."""
            # Chỉ cho phép tua khi đã SETUP xong (READY hoặc PLAYING)
            if self.state != self.PLAYING and self.state != self.READY:
                return

            # 1. Tính toán vị trí frame muốn tua đến
            width = self.progressbar.winfo_width()
            click_x = event.x
            percent = click_x / width
        
            total = getattr(self, 'total_frames', 500)
            target_frame = int(percent * total)
        
            print(f"Seeking to frame: {target_frame} ({int(percent*100)}%)")

            # 2. Dọn dẹp Buffer Client (QUAN TRỌNG)
            # Phải xóa sạch buffer cũ để tránh hiện lại các frame của đoạn trước khi tua
            with self.frameQueue.mutex:
                self.frameQueue.queue.clear()
        
            # Reset các biến đếm phía Client
            self.frameNbr = target_frame 
            self.packetLossCount = 0
            self.currentFrameChunks = bytearray() # Xóa mảnh frame đang lắp dở (nếu có)
            self.expectedSeqNum = 0 # Reset sequence check để không báo lỗi mất gói ảo

            # 3. Gửi lệnh PLAY kèm Frame-Num mới
            # Server mới đã hỗ trợ nhận PLAY khi đang PLAYING, nên không cần gửi PAUSE trước
            self.sendSeekRequest(target_frame)

    def sendSeekRequest(self, frameNum):
        self.rtspSeq += 1
        request = f"PLAY {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}\nFrame-Num: {frameNum}"
        self.rtspSocket.send(request.encode('utf-8'))
        
        self.requestSent = self.PLAY
        self.playEvent.clear()
        threading.Thread(target=self.listenRtp).start()
