from flask import Flask, request
import requests
import urllib.parse
import secrets
import hashlib
import base64
import asyncio
import aiohttp
import logging


# Initialize Flask app
app = Flask(__name__)
# Set logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] - %(message)s')
logger = logging.getLogger(__name__)

# Initialize constant dictionary
response = requests.get(url="https://www.diving-fish.com/api/chunithmprober/music_data")
data = response.json()
CONST_DICT = {item["id"]:item["ds"] for item in data}

# 应用信息（公共客户端，无 secret）
CLIENT_ID = "b6247554-a2e8-4461-b04b-743b08e44073"
REDIRECT_URI = "http://localhost:5000/callback"

# OAuth 接口地址
AUTHORIZE_URL = "https://maimai.lxns.net/oauth/authorize"
TOKEN_URL = "https://maimai.lxns.net/api/v0/oauth/token"
LX_BASE_URL = "https://maimai.lxns.net"
PLAYER_API_URL = "https://maimai.lxns.net/api/v0/user/chunithm/player"


# 生成 code_verifier 和 code_challenge
def generate_code_verifier():
    return secrets.token_urlsafe(64)

def generate_code_challenge(verifier):
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode()


class AsyncRequester:
    def __init__(self, headers:dict={}):
        self.headers = headers
        self.session = None
    
    async def __aenter__(self):
        """Support async context manager"""
        connector = aiohttp.TCPConnector(
            limit=4,            # 总并发连接数限制为30
            limit_per_host=4,   # 同一主机的并发连接数限制为10
            ssl=False            # 根据实际情况设置
        )
        
        self.session = aiohttp.ClientSession(headers=self.headers, connector=connector)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Clean up session"""
        if hasattr(self, 'session') and not self.session.closed:
            await self.session.close()
    
    async def get_song_const(self, id:int, level_index:int):
        return CONST_DICT[id][level_index]


async def parse_user_response(response:dict) -> list:
    data:list = response.get("data")
    fc_status_mapping = {"alljusticecritical" : "ajc",
                         "alljustice" : "aj",
                         "fullcombo" : "fc",
                         None : None}
    
    async with AsyncRequester() as requester:
        # 并发获取所有 const 值
        const_values = await asyncio.gather(*[
            requester.get_song_const(item["id"], item["level_index"]) 
            for item in data
        ])
        
        # 重构数据
        data = [
            {
                "id": item["id"],
                "title": item["song_name"],
                "level_index": item["level_index"],
                "const": const_values[i],
                "score": item["score"],
                "clear_status": item["clear"] != "failed",
                "fc_status": fc_status_mapping.get(item["full_combo"])
            }
            for i, item in enumerate(data)
        ]
    
    return data


@app.route("/")
def home():
    scope = ["read_player"]

    # 生成随机 code_verifier
    code_verifier = generate_code_verifier()
    code_challenge = generate_code_challenge(code_verifier)

    query = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(scope),
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": code_verifier
    }
    url = f"{AUTHORIZE_URL}?{urllib.parse.urlencode(query)}"
    return f'<a href="{url}">点击授权</a>'

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "授权失败，未获取到授权码", 400

    # 用 code_verifier 换 token
    resp = requests.post(TOKEN_URL, data={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": request.args.get("state")
    })
    token_data = resp.json()
    access_token = token_data["data"]["access_token"]
    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    # 调用 API
    player = requests.get(PLAYER_API_URL, headers=headers).json()
    unparsed_scores = requests.get(f"{LX_BASE_URL}/api/v0/user/chunithm/player/scores", headers=headers).json()
    scores = asyncio.run(parse_user_response(unparsed_scores))
    succ_cnt, fail_cnt = 0, 0
    for i in scores:
        if i["const"] == 0:
            fail_cnt += 1
        else:
            succ_cnt += 1
    logger.info(f"Success rate: {round(succ_cnt/len(scores)*100, 2)}%")

    return scores

if __name__ == "__main__":
    app.run()
