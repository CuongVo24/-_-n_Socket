class VideoStream:
    def __init__(self, filename):
        self.filename = filename
        try:
            self.file = open(filename, 'rb')
        except:
            raise IOError
        self.frameNum = 0
        
    def nextFrame(self):
        """Get next frame."""
        data = self.file.read(5) # Get the framelength from the first 5 bytes
        if data: 
            try:
                framelength = int(data)
                data = self.file.read(framelength)
                self.frameNum += 1
            except ValueError:
                return None # Xử lý nếu file lỗi
        return data
        
    def frameNbr(self):
        """Get frame number."""
        return self.frameNum
