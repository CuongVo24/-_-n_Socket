class VideoStream:
    def __init__(self, filename):
        self.filename = filename
        try:
            self.file = open(filename, 'rb')
        except:
            raise IOError
        self.frameNum = 0
        
        # --- MỚI: Tạo chỉ mục (Index) cho các frame để hỗ trợ Seek ---
        self.frame_offsets = [] # Lưu vị trí byte bắt đầu của mỗi frame
        self._build_index()
        
    def _build_index(self):
        """Scan the entire file to locate frames."""
        current_pos = 0
        self.file.seek(0)
        while True:
            try:
                # Đọc 5 byte header độ dài
                data = self.file.read(5)
                if not data: 
                    break
                framelength = int(data)
                self.frame_offsets.append(current_pos)
                # Nhảy đến frame tiếp theo
                current_pos += 5 + framelength
                self.file.seek(current_pos)
            except ValueError:
                break
        # Reset file về đầu
        self.file.seek(0)
        self.total_frames = len(self.frame_offsets)
        print(f"Video Info: Total Frames = {self.total_frames}")

    def nextFrame(self):
        """Get next frame."""
        # Nếu đã hết video
        if self.frameNum >= len(self.frame_offsets):
            return None
            
        # Nhảy đến đúng vị trí frame hiện tại (hỗ trợ seek)
        self.file.seek(self.frame_offsets[self.frameNum])
        
        data = self.file.read(5)
        if data: 
            framelength = int(data)
            data = self.file.read(framelength)
            self.frameNum += 1
        return data
        
    def frameNbr(self):
        return self.frameNum
    
    def totalFrames(self):
        return self.total_frames
        
    def seek(self, frameNumber):
        """Jump to the specified frame."""
        if 0 <= frameNumber < len(self.frame_offsets):
            self.frameNum = frameNumber
