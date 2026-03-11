from flask import Flask, request, session, redirect, url_for, render_template
import flask
from math import floor
import time
from datetime import datetime
import os
import string
import pickle
import requests
import urllib.parse
import secrets
import hashlib
import base64
import asyncio
import aiohttp
import logging
import dotenv


# Read .env
dotenv.load_dotenv()

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(16))
app.config['TEMPLATES_AUTO_RELOAD'] = True
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

# 其它常量
CHARSET = string.ascii_letters + string.digits


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


async def calc_force(data:list) -> list:
    async def calc_component(song:dict) -> dict[tuple[int, int], float]:
        async def score_mapping(score:int) -> float:
                if score < 900000:
                    return -5.0
                elif score <= 974999:
                    return floor((score - 900000) / 150) / 100 - 5.0
                elif score <= 999999:
                    return floor((score - 975000) / 250) / 100
                elif score <= 1004999:
                    return (score - 1000000) / 10000 + 1.0
                elif score <= 1007499:
                    return (score - 1005000) / 5000 + 1.5
                elif score <= 1010000:
                    return (score - 1007500) / 10000 + 2.0
        
        id = song["id"]
        level_index = song["level_index"]
        const = song["const"]
        score = song["score"]
        clr_sta = song["clear_status"]
        fc_sta = song["fc_status"]
        
        ramp_map = {
            "fail" : 0.0,
            "clr" : 1.5,
            "fc" : 2.0,
            "aj" : 3.0,
            "ajc" : 3.1
        }
        
        score_corr = await score_mapping(score)
        ramp_corr = ramp_map.get("fail" if not clr_sta else fc_sta)
        force = const + score_corr + (0.0 if ramp_corr == None else ramp_corr)
        
        return {(id, level_index):max(force, 0.0)}
    
    task = [calc_component(item) for item in data]
    force_list = await asyncio.gather(*task)
    result_list = []
    for i in data:
        i["force"] = round(next((item.get((i["id"], i["level_index"])) for item in force_list if (i["id"], i["level_index"]) in item), None), 4)
        result_list.append(i)
    
    return result_list
        

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
                "clear_status": item["clear"] not in ["failed", None],
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
    start_time = time.time()
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
    
    # 数据处理
    scores = asyncio.run(parse_user_response(unparsed_scores))
    calced_scores = asyncio.run(calc_force(scores))
    calced_scores.sort(key=lambda x:x["force"], reverse=True)
    ajc_scores = [x for x in calced_scores if x["fc_status"] == "ajc"]
    ajc_scores.sort(key=lambda x:x["force"], reverse=True)
    
    best50_list = calced_scores[:min(len(calced_scores),50)]
    ajc_best50_list = ajc_scores[:min(len(ajc_scores),50)]
    ajc_count = len(ajc_scores)
    
    duration = time.time() - start_time
    logger.info(f"Duration: {duration*1000:.2f} ms")
    
    token = ''.join(secrets.choice(CHARSET) for _ in "00000000")
    packed_data = [player["data"], best50_list, ajc_best50_list, ajc_count]
    session[f"table_data_{token}"] = packed_data
    data_size = len(pickle.dumps(packed_data))
    
    logger.info(f"Data size: {data_size} Bytes")

    return redirect(url_for('table_gen', token=token))

@app.route("/table_gen")
def table_gen():
    # 获取存储在session中的信息并将其清除以释放内存
    token = request.args.get('token')
    packed_data = session[f"table_data_{token}"]
    del session[f"table_data_{token}"], token
    
    # 解包信息
    player = packed_data[0]
    b50_lst = packed_data[1]
    ajc_lst = packed_data[2]
    ajc_cnt = packed_data[3]
    
    return flask.jsonify(packed_data)

@app.route("/test")
def test():
    player = {
        "character": {
            "id": 20440,
            "level": 10,
            "name": "ヴァルマシアゴースト"
        },
        "class_emblem": {
            "base": 0,
            "medal": 4
        },
        "currency": 701500,
        "friend_code": 101762085662533,
        "level": 63,
        "name": "†Ｉｍｙｕｒｕ†",
        "over_power": 26553.47,
        "over_power_progress": 24.57,
        "rating": 16.52,
        "rating_possession": "normal",
        "reborn_count": 0,
        "total_currency": 780000,
        "total_play_count": 426,
        "trophy": {
            "color": "platina",
            "id": 7097,
            "name": "君が笑う再会の夜空へ。"
        },
        "upload_time": "2026-03-06T15:11:02Z"
    }
    
    return render_template("table_render.html", time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), player=player)


if __name__ == "__main__":
    app.run()
