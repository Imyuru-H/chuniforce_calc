# src/entry.py
from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from workers import WorkerEntrypoint
import urllib
import aiohttp
import asyncio
import unicodedata
import logging
import secrets
import json
from datetime import datetime
from .calc import parse_user_response, calc_force, EMPTY_SCORE
from .utils import generate_code_verifier, generate_code_challenge


app = FastAPI()
templates = Jinja2Templates(directory="templates")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


CLIENT_ID = "b6247554-a2e8-4461-b04b-743b08e44073"
REDIRECT_URI = "http://localhost:5000/callback"  # 部署后替换
AUTHORIZE_URL = "https://maimai.lxns.net/oauth/authorize"
TOKEN_URL = "https://maimai.lxns.net/api/v0/oauth/token"
LX_BASE_URL = "https://maimai.lxns.net"
PLAYER_API_URL = f"{LX_BASE_URL}/api/v0/user/chunithm/player"


# 全局 CONST_DICT（启动时加载）
import requests
response = requests.get("https://www.diving-fish.com/api/chunithmprober/music_data")
CONST_DICT = {item["id"]: item["ds"] for item in response.json()}

def build_chuniforce_html(force: float) -> str:
    # 你的原函数，完整复制过来
    def get_class_info(force: float):
        if force < 2.5:
            return [1, 1]
        adjusted = force - 2.5
        steps = adjusted / 0.5
        if force >= 14.0:
            extra_steps = max(0, (force - 14.0) / 0.25)
            steps = 13 + extra_steps
        index = int(steps)
        grade = index // 4 + 1
        sub = index % 4 + 1
        if grade > 10 or (grade == 10 and sub > 4):
            return [10, 4]
        return [grade, sub]

    class_info = get_class_info(force)
    class_map = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII", 9: "IX", 10: "X"}
    emblem_text = class_map.get(class_info[0], "X")
    stars = "★" * class_info[1] + "☆" * (4 - class_info[1])

    return f"""<div id='class-card' style="display: inline-block;">
        <div id='emblem'><span class='emblem-text c{class_info[0]}'>{emblem_text}</span><span class='emblem-stars'>{stars}</span></div>
        <div id='force-detail'><span class='chuniforce-text c{class_info[0]}'>CHUNIFORCE</span><span class='chuniforce-number c{class_info[0]}'>{force:.3f}</span></div>
    </div>"""

@app.get("/", response_class=HTMLResponse)
async def home():
    verifier = generate_code_verifier()
    challenge = generate_code_challenge(verifier)
    query = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "read_player",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": verifier
    }
    url = f"{AUTHORIZE_URL}?{urllib.parse.urlencode(query)}"
    return f'<a href="{url}">点击授权 maimai CHUNITHM 数据</a>'

@app.get("/callback")
async def callback(code: str = Query(None), state: str = Query(None)):
    if not code or not state:
        raise HTTPException(400, "授权失败")

    verifier = state
    async with aiohttp.ClientSession() as sess:
        token_resp = await sess.post(TOKEN_URL, data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier
        })
        token_json = await token_resp.json()
        access_token = token_json.get("data", {}).get("access_token")
        if not access_token:
            raise HTTPException(400, "获取 token 失败")

        headers = {"Authorization": f"Bearer {access_token}"}

        player_resp = await sess.get(PLAYER_API_URL, headers=headers)
        player = await player_resp.json()

        scores_resp = await sess.get(f"{LX_BASE_URL}/api/v0/user/chunithm/player/scores", headers=headers)
        unparsed = await scores_resp.json()

    player["data"]["name"] = unicodedata.normalize("NFKC", player["data"].get("name", ""))

    scores = await parse_user_response(unparsed)
    calced = await calc_force(scores)
    calced.sort(key=lambda x: x["force"], reverse=True)
    ajc_list = [x for x in calced if x["fc_status"] == "ajc"]
    ajc_list.sort(key=lambda x: x["force"], reverse=True)

    b50 = calced[:50]
    ajc50 = ajc_list[:50]
    ajc_count = len(ajc_list)

    total_f = sum(x["force"] for x in b50)
    avg_f = total_f / len(b50) if b50 else 0

    total_ajc = sum(x["ajc_force"] for x in ajc50)
    avg_ajc = total_ajc / len(ajc50) if ajc50 else 0

    bonus = ajc_count / 10000
    result_force = avg_f + avg_ajc + bonus

    while len(b50) < 50:
        b50.append(EMPTY_SCORE.copy())
    while len(ajc50) < 50:
        ajc50.append(EMPTY_SCORE.copy())

    token = secrets.token_urlsafe(16)

    # 这里用 query param 传递 token，实际生产建议用 Workers KV 存储数据
    return RedirectResponse(url=f"/table?token={token}&force={result_force:.4f}")  # 简化演示，实际数据建议 KV

    # 更完整版：你可以把 packed_data json 序列化后传，或用 KV
    # 但 Workers 内存有限，推荐 KV namespace

@app.get("/table")
async def table_gen(request: Request, token: str = Query(...)):
    # 演示：实际这里从 KV 取数据，此处简化用 mock 或直接计算结果
    # 你可以把整个 packed_data 逻辑移到 callback 里，然后 render
    # 为完整性，这里假设你已存好数据
    # ... (类似你的 table_gen 逻辑)
    # return templates.TemplateResponse("table_render.html", {"request": request, ...})

    # 临时返回首页提示
    return {"message": "部署成功！请替换 REDIRECT_URI 并绑定 KV 存储数据"}


class Default(WorkerEntrypoint):
    async def fetch(self, req):
        from asgi import fetch as asgi_fetch  # Cloudflare 提供的 ASGI 桥接
        return await asgi_fetch(app, req.js_object, self.env)