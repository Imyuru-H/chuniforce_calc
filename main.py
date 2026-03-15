from flask import Flask, request, session, redirect, url_for, render_template
import flask
from math import floor
import time
from datetime import datetime
import os
import ast
import string
import json
import unicodedata
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
REDIRECT_URI = "cn-hk-bgp-4.ofalias.net:35023/callback"

# OAuth 接口地址
AUTHORIZE_URL = "https://maimai.lxns.net/oauth/authorize"
TOKEN_URL = "https://maimai.lxns.net/api/v0/oauth/token"
LX_BASE_URL = "https://maimai.lxns.net"
PLAYER_API_URL = "https://maimai.lxns.net/api/v0/user/chunithm/player"

# 其它常量
CHARSET = string.ascii_letters + string.digits
EMPTY_SCORE = {
    "clear_status": False,
    "const": 0.0,
    "fc_status": "",
    "force": 0.0,
    "ajc_force": 0.0,
    "id": 0,
    "level_index": 5,
    "score": "0000000",
    "title": "暂无数据"
}


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
        i["ajc_force"] = round(((i["const"] / 15) ** 2 * 2) if i["fc_status"] == "ajc" else 0.0)
        result_list.append(i)
    
    return result_list
        

async def parse_user_response(response:dict) -> list:
    data:list = response.get("data")
    fc_status_mapping = {"alljusticecritical" : "ajc",
                         "alljustice" : "aj",
                         "fullcombo" : "fc",
                         None : ""}
    
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
    player:dict = requests.get(PLAYER_API_URL, headers=headers).json()
    unparsed_scores = requests.get(f"{LX_BASE_URL}/api/v0/user/chunithm/player/scores", headers=headers).json()
    
    # 处理全角字符
    player["data"]["name"] = unicodedata.normalize("NFKC", player["data"].get("name"))
    
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
    def build_chuniforce_html(force:float):
        def get_class_info(force: float) -> list[int]:
            if force < 2.5:
                return [1, 1]
            
            # 从 2.5 开始算偏移
            adjusted = force - 2.5
            steps = adjusted / 0.5                  # 大部分是 0.5 步长
            
            # 特殊处理 14.0~15.0 区间有 0.25 细分（4→5）
            if force >= 14.0:
                extra_steps = max(0, (force - 14.0) / 0.25)
                steps = 13 + extra_steps            # 14.0 对应 steps ≈ 23
            
            index = int(steps)                      # 向下取整
            
            grade = index // 4 + 1
            sub   = index % 4 + 1
            
            # 兜底
            if grade > 10 or (grade == 10 and sub > 4):
                return [10, 4]
            
            return [grade, sub]
        
        class_info = get_class_info(force)
        class_map = {
            1:  "I",
            2:  "II",
            3:  "III",
            4:  "IV",
            5:  "V",
            6:  "VI",
            7:  "VII",
            8:  "VIII",
            9:  "IX",
            10: "X"
        }
        emblem_text = class_map.get(class_info[0])
        stars = "★" * class_info[1] + "☆" * (4 - class_info[1])

        html = f"""<div id='class-card' style="display: inline-block;">
            <div id='emblem'><span class='emblem-text c{class_info[0]}'>{emblem_text}</span><span class='emblem-stars'>{stars}</span></div>
            <div id='force-detail'><span class='chuniforce-text c{class_info[0]}'>CHUNIFORCE</span><span class='chuniforce-number c{class_info[0]}'>{force:.3f}</span></div>
        </div>"""

        return html
    
    try:
        # 获取存储在session中的信息并将其清除以释放内存
        token = request.args.get('token')
        packed_data = session[f"table_data_{token}"]
        del session[f"table_data_{token}"], token
    except Exception as e:
        logger.error(e)
    
    # 解包信息    
    player:dict  = packed_data[0]
    b50_lst:list = packed_data[1]
    ajc_lst:list = packed_data[2]
    ajc_cnt:int  = packed_data[3]

    total_force, total_ajc_force = 0, 0
    for i in b50_lst:
        total_force += i["force"]
    avg_force = total_force / 50

    for i in ajc_lst:
        total_ajc_force += i["ajc_force"]
    avg_ajc_force = total_ajc_force / 50

    ajc_bonus = ajc_cnt / 10000
    force_result = avg_force + avg_ajc_force + ajc_bonus

    if len(b50_lst) < 50:
        for _ in range(50 - len(b50_lst)):
            b50_lst.append(EMPTY_SCORE)

    if len(ajc_lst) < 50:
        for _ in range(50 - len(ajc_lst)):
            ajc_lst.append(EMPTY_SCORE)
    
    return render_template("table_render.html",
                           time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                           player=player,
                           b50_lst=b50_lst,
                           ajc_lst=ajc_lst,
                           emblem=build_chuniforce_html(force_result),
                           force_result=force_result,
                           avg_force=round(avg_force, 4),
                           avg_ajc_force=avg_ajc_force,
                           ajc_bonus=ajc_bonus)

@app.route("/test")
def test():
    def build_chuniforce_html(force:float):
        def get_class_info(force: float) -> list[int]:
            if force < 2.5:
                return [1, 1]
            
            # 从 2.5 开始算偏移
            adjusted = force - 2.5
            steps = adjusted / 0.5                  # 大部分是 0.5 步长
            
            # 特殊处理 14.0~15.0 区间有 0.25 细分（4→5）
            if force >= 14.0:
                extra_steps = max(0, (force - 14.0) / 0.25)
                steps = 13 + extra_steps            # 14.0 对应 steps ≈ 23
            
            index = int(steps)                      # 向下取整
            
            grade = index // 4 + 1
            sub   = index % 4 + 1
            
            # 兜底
            if grade > 10 or (grade == 10 and sub > 4):
                return [10, 4]
            
            return [grade, sub]
        
        class_info = get_class_info(force)
        class_map = {
            1:  "I",
            2:  "II",
            3:  "III",
            4:  "IV",
            5:  "V",
            6:  "VI",
            7:  "VII",
            8:  "VIII",
            9:  "IX",
            10: "X"
        }
        emblem_text = class_map.get(class_info[0])
        stars = "★" * class_info[1] + "☆" * (4 - class_info[1])

        html = f"""<div id='class-card' style="display: inline-block;">
            <div id='emblem'><span class='emblem-text c{class_info[0]}'>{emblem_text}</span><span class='emblem-stars'>{stars}</span></div>
            <div id='force-detail'><span class='chuniforce-text c{class_info[0]}'>CHUNIFORCE</span><span class='chuniforce-number c{class_info[0]}'>{force:.3f}</span></div>
        </div>"""

        return html

    with open("data.json", "r", encoding="utf-8") as file:
        packed_data = json.load(file)
    player:dict  = packed_data[0]
    b50_lst:list = packed_data[1]
    ajc_lst:list = packed_data[2]
    ajc_cnt:int  = packed_data[3]

    total_force, total_ajc_force = 0, 0
    for i in b50_lst:
        total_force += i["force"]
    avg_force = total_force / 50

    for i in ajc_lst:
        total_ajc_force += i["ajc_force"]
    avg_ajc_force = total_ajc_force / 50

    ajc_bonus = ajc_cnt / 10000
    force_result = avg_force + avg_ajc_force + ajc_bonus

    if len(b50_lst) < 50:
        for _ in range(50 - len(b50_lst)):
            b50_lst.append(EMPTY_SCORE)

    if len(ajc_lst) < 50:
        for _ in range(50 - len(ajc_lst)):
            ajc_lst.append(EMPTY_SCORE)
    
    return render_template("table_render.html",
                           time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                           player=player,
                           b50_lst=b50_lst,
                           ajc_lst=ajc_lst,
                           emblem=build_chuniforce_html(force_result),
                           force_result=force_result,
                           avg_force=round(avg_force, 4),
                           avg_ajc_force=avg_ajc_force,
                           ajc_bonus=ajc_bonus)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=443)
