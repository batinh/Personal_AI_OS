import requests

# --- CẤU HÌNH TẠM THỜI ĐỂ LẤY TOKEN ---
# Bạn hãy điền Client ID và Secret từ Strava vào đây để chạy script này
CLIENT_ID = "200332"
CLIENT_SECRET = "24b7ad5bbcd7abadf02ecad1e11a048da15536ed"


def get_tokens():
    # Bước 1: Tạo link login
    url = f"https://www.strava.com/oauth/authorize?client_id={CLIENT_ID}&response_type=code&redirect_uri=http://localhost&approval_prompt=force&scope=activity:read_all,activity:write"

    print("1. Hãy mở link sau trên trình duyệt và bấm 'Authorize':")
    print(url)

    # Bước 2: Nhập code
    print("\n2. Sau khi authorize, trình duyệt sẽ báo lỗi (localhost).")
    print("Nhìn trên thanh địa chỉ, copy đoạn mã sau dấu '?code='")
    code = input("Dán code vào đây: ")

    # Bước 3: Đổi code lấy token
    payload = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
    }

    try:
        res = requests.post("https://www.strava.com/oauth/token", data=payload)
        res.raise_for_status()
        data = res.json()

        print("-" * 60)
        print("THÀNH CÔNG! HÃY COPY DÒNG DƯỚI VÀO FILE .env CỦA BẠN:")
        print(f"STRAVA_REFRESH_TOKEN={data['refresh_token']}")
        print("-" * 60)
    except Exception as e:
        print(f"Lỗi: {e}")


if __name__ == "__main__":
    get_tokens()
