# src/entry.py
from typing import Any
from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from contextlib import asynccontextmanager
from workers import WorkerEntrypoint
import urllib
import aiohttp
import asyncio
import unicodedata
import logging
import secrets
import json
import time
import redis.asyncio as aioredis
from datetime import datetime
from .calc import parse_user_response, calc_force, EMPTY_SCORE
from .utils import generate_code_verifier, generate_code_challenge
from .ttl_dict import AsyncTTLDict


temp_data_store = AsyncTTLDict(default_ttl=600)

# lifespan 里加启动清理
@asynccontextmanager
async def lifespan(app: FastAPI):
    await temp_data_store.start_cleanup()
    try:
        yield
    finally:
        if temp_data_store._cleanup_task:
            temp_data_store._cleanup_task.cancel()

app = FastAPI(lifespan=lifespan)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


SESSION_KEY = "599c47bd18008f18e09ae67cd76668ba3f2a1e9c8d7b6e5a4f3c2d1e0b9a8f7"
CLIENT_ID = "b6247554-a2e8-4461-b04b-743b08e44073"
REDIRECT_URI = "http://localhost:5000/callback"  # 部署后替换
AUTHORIZE_URL = "https://maimai.lxns.net/oauth/authorize"
TOKEN_URL = "https://maimai.lxns.net/api/v0/oauth/token"
LX_BASE_URL = "https://maimai.lxns.net"
PLAYER_API_URL = f"{LX_BASE_URL}/api/v0/user/chunithm/player"


# 配置 FastAPI APP
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_KEY,
    session_cookie="session", # 建议显式指定名字，避免默认值冲突
    same_site="lax",          # 开发时建议加，防止浏览器不发 cookie
    https_only=False,         # 本地开发用 False，线上改 True
    max_age=3600,             # 可选，设置过期时间
)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


class AsyncRedisDict:
    def __init__(self, ttl: int = 60, redis_url: str = "redis://localhost:6379"):
        self.ttl = ttl
        self.redis = None
        self.redis_url = redis_url
        self.key_prefix = "my_dict:"


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
async def callback(request: Request, code: str = Query(None), state: str = Query(None)):
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

    b50_lst = calced[:50]
    ajc_lst = ajc_list[:50]
    ajc_count = len(ajc_list)

    total_f = sum(x["force"] for x in b50_lst)
    avg_f = total_f / len(b50_lst) if b50_lst else 0

    total_ajc = sum(x["ajc_force"] for x in ajc_lst)
    avg_ajc = total_ajc / len(ajc_lst) if ajc_lst else 0

    bonus = ajc_count / 10000
    result_force = avg_f + avg_ajc + bonus

    while len(b50_lst) < 50:
        b50_lst.append(EMPTY_SCORE.copy())
    while len(ajc_lst) < 50:
        ajc_lst.append(EMPTY_SCORE.copy())

    token = secrets.token_urlsafe(16)
    packed_data = [player["data"], b50_lst, ajc_lst, ajc_count, time.time()]
    
    await temp_data_store.set(key=f"table_data_{token}", value=packed_data)

    return RedirectResponse(url=f"/table?token={token}")

@app.get("/table")
async def table_gen(request: Request, token: str = Query(...)):
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
        packed_data = await temp_data_store.get(f"table_data_{token}")
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
            
    context = {
        "request"       : request,
        "time"          : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "player"        : player,
        "b50_lst"       : b50_lst,
        "ajc_lst"       : ajc_lst,
        "emblem"        : build_chuniforce_html(force_result),
        "force_result"  : force_result,
        "avg_force"     : round(avg_force, 4),
        "avg_ajc_force" : avg_ajc_force,
        "ajc_bonus"     : ajc_bonus
    }
            
    return templates.TemplateResponse(name="table_render.html",
                                      context=context,
                                      status_code=200)


class Default(WorkerEntrypoint):
    async def fetch(self, req):
        from asgi import fetch as asgi_fetch  # Cloudflare 提供的 ASGI 桥接
        return await asgi_fetch(app, req.js_object, self.env)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=5000, reload=True)