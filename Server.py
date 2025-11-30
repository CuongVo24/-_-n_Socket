import sys, socket
from ServerWorker import ServerWorker


# BỎ TRỐNG CLASS SERVER, VÌ HIỆN TẠI NÓ KHÔNG CÓ CHỨC NĂNG GÌ
class Server:
    pass


# ĐƯA HÀM MAIN RA NGOÀI THÀNH HÀM TOÀN CỤC (GLOBAL FUNCTION)
def main():
    try:
        # Dùng biến cục bộ, không dùng self
        SERVER_PORT = int(sys.argv[1])
    except (IndexError, ValueError):
        print("[Usage: Server.py Server_port]\n")
        # Dừng chương trình nếu có lỗi
        sys.exit(1)

    rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    rtspSocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    # Bind socket
    try:
        rtspSocket.bind(('', SERVER_PORT))
    except OSError as e:
        print(f"Error binding to port {SERVER_PORT}: {e}")
        sys.exit(1)

    rtspSocket.listen(5)
    print(f"RTSP Server listening on port {SERVER_PORT}...")
    print("Waiting for a client to connect...")

    while True:
        try:
            clientInfo = {}
            clientInfo['rtspSocket'] = rtspSocket.accept()
            print(f"Accepted connection from: {clientInfo['rtspSocket'][1]}")
            ServerWorker(clientInfo).run()
        except Exception as e:
            print(f"An error occurred in the main loop: {e}")
            break  # Thoát vòng lặp nếu có lỗi nghiêm trọng


# GỌI TRỰC TIẾP HÀM MAIN()
if __name__ == "__main__":
    main()