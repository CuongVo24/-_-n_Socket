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

        # --- THÊM PHẦN QUẢN LÝ CACHE ---
        self.frame_cache = {} # Lưu trữ frame: {frame_number: data_bytes}
        self.CACHE_LIMIT = 1000 # Giới hạn lưu 1000 frame gần nhất (tùy RAM máy bạn)

        self.userPaused = False # Đánh dấu xem người dùng có đang bấm Pause không

        # --- THÊM 3 DÒNG NÀY ĐỂ SỬA LỖI CRASH ---
        self.frameQueue = queue.Queue(maxsize=1000)
        self.BUFFER_START_THRESHOLD = 20   # Ngưỡng bắt đầu phát (cần 20 frame)
        self.BUFFER_REFILL_THRESHOLD = 40  # Ngưỡng nạp lại khi bị lag

        self.total_frames = 500 # Giá trị mặc định, sẽ cập nhật khi SETUP
        # Bien tam de lap rap cac manh (fragmentation) cua 1 frame HD
        self.currentFrameChunks = bytearray()

        self.discard_current_frame = False



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
        """Xử lý nút Pause: Đánh dấu userPaused để chặn tự động phát."""
        if self.state == self.PLAYING:
            # 1. Đánh dấu là người dùng chủ động Pause
            self.userPaused = True 
            
            # 2. Dừng hiển thị hình ảnh
            self.playEvent.clear()
            
            # 3. KHÔNG GỬI LỆNH PAUSE LÊN SERVER (để Buffer vẫn tiếp tục nạp ngầm)
            print("--> Paused: Dừng hình, Server vẫn đang nạp Buffer...")

    def playMovie(self):
        """Xử lý nút Play: Bỏ cờ userPaused để cho phép chạy lại."""
        # Đánh dấu là người dùng muốn xem tiếp
        self.userPaused = False 

        if self.state == self.READY and self.requestSent != self.PLAY:
            threading.Thread(target=self.listenRtp).start()
            self.playEvent.clear()
            self.sendRtspRequest(self.PLAY)
            self.master.after(100, self.update_image_loop)
        
        elif self.state == self.PLAYING:
            self.playEvent.set()
            print("--> Resume")

    def listenRtp(self):
        """Lắng nghe luồng RTP với cơ chế loại bỏ Frame lỗi (Smart Frame Drop)."""
        while True:
            try:
                # Nhận dữ liệu từ socket (Buffer 20480 bytes là đủ cho các mảnh gói tin)
                data = self.rtpSocket.recv(20480)
                if data:
                    rtpPacket = RtpPacket()
                    rtpPacket.decode(data)
                    
                    currSeq = rtpPacket.seqNum()
                    
                    # Lấy số Frame thực tế từ header (để vẽ thanh tiến độ chính xác)
                    # Yêu cầu: Bạn phải đã sửa RtpPacket.py như hướng dẫn trước đó
                    try:
                        server_frame_num = rtpPacket.timestamp()
                    except:
                        server_frame_num = self.frameNbr # Fallback nếu lỗi

                    # ==========================================================
                    # 1. KIỂM TRA MẤT GÓI TIN (PACKET LOSS DETECTION)
                    # ==========================================================
                    if self.expectedSeqNum != 0:
                        if currSeq > self.expectedSeqNum:
                            loss = currSeq - self.expectedSeqNum
                            self.packetLossCount += loss
                            
                            # QUAN TRỌNG: NẾU MẤT GÓI -> ĐÁNH DẤU HỎNG FRAME NÀY NGAY
                            # Thà bỏ 1 khung hình còn hơn hiển thị ảnh rác làm lag app
                            self.discard_current_frame = True 
                            self.currentFrameChunks = bytearray() # Xóa dữ liệu đang ghép dở
                    
                    self.expectedSeqNum = currSeq + 1
                    self.totalPacketsReceived += 1

                    # ==========================================================
                    # 2. GHÉP MẢNH (FRAGMENT REASSEMBLY)
                    # ==========================================================
                    # Chỉ ghép dữ liệu nếu frame hiện tại được đánh giá là "Sạch" (không mất gói)
                    if not getattr(self, 'discard_current_frame', False):
                        payload = rtpPacket.getPayload()
                        self.currentFrameChunks += payload
                    
                    # ==========================================================
                    # 3. KẾT THÚC FRAME (MARKER BIT CHECK)
                    # ==========================================================
                    # Marker = 1 báo hiệu đây là gói tin cuối cùng của 1 bức ảnh
                    if rtpPacket.getMarker():
                        # Nếu frame này "Sạch", đóng gói và gửi vào hàng đợi
                        if not getattr(self, 'discard_current_frame', False):
                            if len(self.currentFrameChunks) > 0:
                                if not self.frameQueue.full():
                                    # Gửi 1 Tuple: (Số_Frame, Dữ_Liệu_Ảnh)
                                    self.frameQueue.put((server_frame_num, self.currentFrameChunks))
                        
                        # --- RESET TRẠNG THÁI CHO FRAME MỚI ---
                        self.currentFrameChunks = bytearray()
                        self.discard_current_frame = False # Frame tiếp theo mặc định là sạch
            
            # Xử lý khi socket bị timeout (không nhận được tin trong 0.5s)
            except socket.timeout:
                if self.teardownAcked == 1:
                    break
                continue # Vẫn tiếp tục lắng nghe, không thoát
            
            # Xử lý các lỗi khác (ví dụ socket bị đóng)
            except Exception as e:
                if self.teardownAcked == 1:
                    break
                # print(f"RTP Exception: {e}") # Có thể bật lên để debug nếu cần
                break

    def update_image_loop(self):
        if self.state == self.PLAYING:
            # 1. Vẽ thanh tiến độ (Giữ nguyên)
            try:
                total = getattr(self, 'total_frames', 500)
                width = self.progressbar.winfo_width()
                curr_pct = self.frameNbr / total
                buffer_pct = (self.frameNbr + self.frameQueue.qsize()) / total 
                if curr_pct > 1: curr_pct = 1 
                if buffer_pct > 1: buffer_pct = 1
                self.progressbar.coords(self.buffer_bar, 0, 0, width * buffer_pct, 15)
                self.progressbar.coords(self.played_bar, 0, 0, width * curr_pct, 15)
            except: pass

            # 2. LOGIC ĐIỀU KHIỂN PLAYBACK
            # Nếu hết buffer -> Dừng hình (Lag)
            if self.frameQueue.qsize() == 0 and self.playEvent.is_set():
                 self.statLabel.config(text="Buffering... (Network lag)")
                 self.playEvent.clear()
            
            # Logic hồi phục (Auto-Resume):
            # CHỈ CHẠY NẾU NGƯỜI DÙNG KHÔNG BẤM PAUSE
            if not self.playEvent.is_set() and not self.userPaused: 
                
                # Nếu đủ buffer thì tự động chạy lại
                if self.frameQueue.qsize() >= self.BUFFER_START_THRESHOLD:
                    self.playEvent.set()
                    self.statLabel.config(text=f"Playing... Buffer: {self.frameQueue.qsize()}")
                else:
                    self.statLabel.config(text=f"Buffering... {self.frameQueue.qsize()}/{self.BUFFER_START_THRESHOLD}")
                    self.master.after(40, self.update_image_loop)
                    return

            # HIỂN THỊ HÌNH ẢNH
            if self.playEvent.is_set():
                try:
                    # Lấy dữ liệu từ hàng đợi (Bây giờ nó là 1 bộ Tuple)
                    queue_item = self.frameQueue.get_nowait()
                    
                    # Tách số Frame và Dữ liệu ảnh
                    frame_num, frame_data = queue_item
                    
                    # Cập nhật số Frame hiện tại theo đúng Server
                    self.frameNbr = frame_num 
                    
                    # Vẽ ảnh
                    self.render_frame_memory(frame_data)
                    
                    # XÓA DÒNG TỰ CỘNG NÀY: self.frameNbr += 1
                except queue.Empty:
                    pass
        
        self.master.after(40, self.update_image_loop)

    def render_frame_memory(self, data):
        """Hiển thị ảnh và Lưu vào Cache."""
        try:
            # 1. Lưu vào Cache trước khi vẽ
            # Nếu cache đầy thì xóa bớt frame cũ nhất để tránh tràn RAM
            if len(self.frame_cache) > self.CACHE_LIMIT:
                # Xóa frame có số thứ tự nhỏ nhất (cũ nhất)
                oldest_frame = min(self.frame_cache.keys())
                del self.frame_cache[oldest_frame]
            
            # Lưu frame hiện tại vào kho
            self.frame_cache[self.frameNbr] = data
            
            # 2. Vẽ lên màn hình (Giữ nguyên code cũ)
            image_stream = io.BytesIO(data)
            image = Image.open(image_stream)
            photo = ImageTk.PhotoImage(image)
            self.label.configure(image=photo, height=288)
            self.label.image = photo
            
            # Hiển thị thông tin cache để bạn kiểm tra
            self.statLabel.config(text=f"Playing... Buffer: {self.frameQueue.qsize()} | Cache: {len(self.frame_cache)}")
            
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
        """Tua Siêu Thông Minh: Ưu tiên Cache -> Buffer -> Mới đến Server."""
        if self.state != self.PLAYING and self.state != self.READY:
            return
        
        self.userPaused = False 

        width = self.progressbar.winfo_width()
        percent = event.x / width
        total = getattr(self, 'total_frames', 500)
        target_frame = int(percent * total)

        # === CACHE HIT (XỬ LÝ VÙNG ĐỎ) ===
        # Nếu frame muốn xem ĐÃ CÓ trong kho lưu trữ
        if target_frame in self.frame_cache:
            print(f"--> Cache Hit: Lấy frame {target_frame} từ RAM (Siêu tốc)")
            
            # 1. Lấy dữ liệu từ cache
            frame_data = self.frame_cache[target_frame]
            
            # 2. Cập nhật vị trí hiện tại
            self.frameNbr = target_frame
            
            # 3. Hiển thị ngay lập tức (Không cần chờ update_image_loop)
            self.render_frame_memory(frame_data)
            
            # 4. Cập nhật thanh đỏ
            try:
                self.progressbar.coords(self.played_bar, 0, 0, width * percent, 15)
            except: pass
            
            # QUAN TRỌNG:
            # Khi tua về quá khứ, Buffer hiện tại (tương lai) trở nên vô nghĩa với vị trí mới.
            # Tuy nhiên, ta KHÔNG XÓA nó vội, vì lỡ người dùng lại tua tiếp về tương lai thì sao?
            # Nhưng để đơn giản và tránh lỗi logic hiển thị, ta có thể giữ nguyên Buffer 
            # và chỉ thay đổi số frame hiển thị.
            
            # Nếu đang bị Pause thì bật lại
            if not self.playEvent.is_set():
                self.playEvent.set()
                
            return # KẾT THÚC, KHÔNG GỌI SERVER

        # === BUFFER HIT (XỬ LÝ VÙNG XÁM) ===
        max_buffered_frame = self.frameNbr + self.frameQueue.qsize()
        if self.frameNbr < target_frame < max_buffered_frame:
            print(f"--> Buffer Hit: Nhảy cóc tới frame {target_frame}")
            frames_to_skip = target_frame - self.frameNbr
            for _ in range(frames_to_skip):
                try: self.frameQueue.get_nowait()
                except: break
            self.frameNbr = target_frame
            try:
                self.progressbar.coords(self.played_bar, 0, 0, width * percent, 15)
            except: pass
            return 

        # === SERVER HIT (VÙNG TRẮNG HOẶC VÙNG ĐỎ QUÁ XA) ===
        print(f"--> Miss: Phải tải lại từ Server frame {target_frame}...")
        
        # Chỉ khi nào bắt buộc phải tải mới thì ta mới dọn dẹp
        with self.frameQueue.mutex:
            self.frameQueue.queue.clear()
        
        self.frameNbr = target_frame 
        self.packetLossCount = 0
        self.currentFrameChunks = bytearray()
        self.expectedSeqNum = 0 
        
        self.playEvent.clear() 
        self.statLabel.config(text=f"Seeking... (Wait for buffer)")
        self.sendSeekRequest(target_frame)

    def sendSeekRequest(self, frameNum):
        self.rtspSeq += 1
        request = f"PLAY {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}\nFrame-Num: {frameNum}"
        self.rtspSocket.send(request.encode('utf-8'))
        
        self.requestSent = self.PLAY
        self.playEvent.clear()
        threading.Thread(target=self.listenRtp).start()
