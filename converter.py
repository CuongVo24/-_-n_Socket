import cv2
import sys
import os

def convert_mp4_to_mjpeg(input_file, output_file):
    # 1. Kiểm tra file đầu vào
    if not os.path.exists(input_file):
        print(f"LỖI: Không tìm thấy file '{input_file}'")
        print("Hãy chắc chắn file video nằm cùng thư mục và đúng tên.")
        return

    # 2. Mở video mp4
    cap = cv2.VideoCapture(input_file)
    if not cap.isOpened():
        print("LỖI: Không thể mở file video. File có thể bị hỏng.")
        return

    # Lấy thông số video gốc
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"-> Đang xử lý video: {input_file}")
    print(f"-> Thông số gốc: {fps} FPS, Tổng {total_frames} frames")
    print("-> Đang chuyển đổi... Vui lòng đợi.")

    with open(output_file, 'wb') as f:
        frame_count = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            # --- CẤU HÌNH KÍCH THƯỚC (QUAN TRỌNG) ---
            # Chuẩn 720p
            frame = cv2.resize(frame, (720, 1280)) 
            # -----------------------------------------

            # Mã hóa frame thành JPEG
            # quality=75 là đẹp và đủ nhẹ
            encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 50] 
            result, encimg = cv2.imencode('.jpg', frame, encode_param)

            if result:
                data = encimg.tobytes()
                size = len(data)

                # Giới hạn của đồ án: Header chỉ có 5 số => Max size = 99999 bytes (~97KB)
                if size > 99999:
                    # Nếu ảnh vẫn quá nặng, nén mạnh hơn xuống quality=50
                    encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 30]
                    _, encimg = cv2.imencode('.jpg', frame, encode_param)
                    data = encimg.tobytes()
                    size = len(data)

                # Tạo Header 5 byte theo đúng chuẩn đồ án
                size_str = str(size).zfill(5)
                
                # Ghi vào file
                f.write(size_str.encode()) # Header
                f.write(data)              # Ảnh JPEG
                
                frame_count += 1
                if frame_count % 100 == 0:
                    print(f"   Đã xong: {frame_count}/{total_frames} frames")

    cap.release()
    print("-" * 30)
    print(f"THÀNH CÔNG! File mới tên là: {output_file}")
    print(f"Tổng số frame: {frame_count}")
    print("-" * 30)

if __name__ == "__main__":
    # Tên file mặc định
    input_video = "video.mp4"       # Tên file gốc của bạn
    output_video = "movie_hd.Mjpeg" # Tên file kết quả
    
    # Cho phép nhập tên file từ dòng lệnh nếu muốn
    if len(sys.argv) > 1:
        input_video = sys.argv[1]
    
    convert_mp4_to_mjpeg(input_video, output_video)




